import json
import logging
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import wrapt
from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from oslo_config import cfg
from pydantic import BaseModel

from aprs_service_registry import conf, objectstore, utils  # noqa
from aprs_service_registry.health_checker import (
    HealthCheckStore,
    setup_scheduler,
    start_scheduler,
    stop_scheduler,
)


LOG = logging.getLogger(__name__)
CONF = cfg.CONF

_WEB_DIR = Path(__file__).resolve().parent / "web"


def service_to_dict(service) -> dict:
    """Convert a service model to a dictionary.

    Handles both Pydantic v1 (.dict()) and v2 (.model_dump()) APIs.
    Also sets default status for legacy services.
    """
    try:
        data = service.model_dump()
    except AttributeError:
        data = service.dict()

    # Default status for legacy services
    if "status" not in data or data["status"] is None:
        data["status"] = "active"

    return data


def attach_last_health_check(service_dict: dict, callsign: str, store) -> None:
    """Attach last_health_check info to a service dictionary.

    Args:
        service_dict: The service dictionary to modify (in-place)
        callsign: The service callsign
        store: HealthCheckStore instance
    """
    last_result = store.get_last_result(callsign)
    if last_result:
        service_dict["last_health_check"] = {
            "timestamp": last_result.timestamp.isoformat(),
            "success": last_result.success,
            "response_time_ms": last_result.response_time_ms,
            "error": last_result.error,
        }
    else:
        service_dict["last_health_check"] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan context manager for startup/shutdown."""
    # Startup: Load services and health check results from disk
    APRSServices().load()
    HealthCheckStore().load()

    # Set up health check scheduler
    setup_scheduler()
    start_scheduler()

    yield

    # Shutdown: Stop scheduler and save data
    stop_scheduler()
    APRSServices().save()
    HealthCheckStore().save()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(_WEB_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(_WEB_DIR / "templates"))


class registryRequest(BaseModel):
    """Request to register a service with the registry."""

    callsign: str
    description: str
    service_website: str
    software: str
    callsign_owner: str | None = None
    status: Literal["active", "down", "deleted"] = "active"
    health_check_command: str | None = None


class APRSServices(objectstore.ObjectStoreMixin):
    _instance = None
    lock = threading.Lock()
    data: dict = {}

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init_store()
            cls._instance.data = {}
        return cls._instance

    @wrapt.synchronized(lock)
    def __getitem__(self, callsign):
        return self.data[callsign]

    @wrapt.synchronized(lock)
    def add(self, callsign, data: registryRequest):
        """Add a service to the registry.

        Note: This method does NOT persist to disk. Use add_and_persist
        if you need automatic persistence.
        """
        self.data[callsign] = data

    @wrapt.synchronized(lock)
    def add_and_persist(self, callsign, data: registryRequest):
        """Add a service to the registry and persist to disk.

        This is the preferred method for recording service changes
        as it ensures data is saved immediately.
        """
        self.data[callsign] = data
        # Call _save_unlocked to avoid deadlock (we already hold the lock)
        self._save_unlocked()

    @wrapt.synchronized(lock)
    def remove(self, callsign):
        if callsign in self.data:
            del self.data[callsign]


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def get(request: Request):
    services = APRSServices()
    all_services = services.get_all()
    store = HealthCheckStore()

    # Filter for website: show active and down, hide deleted
    # Also build health check info
    filtered_services = {}
    health_checks = {}

    for callsign, service in all_services.items():
        service_dict = service_to_dict(service)
        status = service_dict["status"]

        if status in ("active", "down"):
            filtered_services[callsign] = service
            # Get health check result
            health_checks[callsign] = store.get_last_result(callsign)

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "request": request,
            "services": filtered_services,
            "health_checks": health_checks,
        },
    )


@app.post("/api/v1/registry", response_class=JSONResponse)
async def registry(request: registryRequest):
    """Register a service with the registry and/or update."""
    LOG.info(f"registry: {request}")
    services = APRSServices()
    callsign_upper = request.callsign.upper()

    # Create a new model instance with uppercased callsign
    # Use dict() for Pydantic v1 compatibility, model_dump() for v2
    try:
        request_dict = request.model_dump()
    except AttributeError:
        request_dict = request.dict()
    request_dict["callsign"] = callsign_upper

    # Preserve existing health_check_command and status if not provided in request
    # This prevents re-registration from overwriting admin-set values
    if callsign_upper in services.data:
        existing = services[callsign_upper]
        try:
            existing_dict = existing.model_dump()
        except AttributeError:
            existing_dict = existing.dict()

        # Preserve health_check_command if not provided in new request
        if request_dict.get("health_check_command") is None:
            request_dict["health_check_command"] = existing_dict.get(
                "health_check_command"
            )

        # Preserve status if not explicitly changed (don't let re-registration reset deleted)
        if request_dict.get("status") is None or request_dict.get("status") == "active":
            existing_status = existing_dict.get("status")
            if existing_status == "deleted":
                # Don't allow re-registration to un-delete a service
                request_dict["status"] = "deleted"

    request_upper = registryRequest(**request_dict)
    services.add_and_persist(callsign_upper, request_upper)
    for service in services:
        LOG.info(
            f"{service}: {services[service].description} - {services[service].service_website}",
        )
    return json.dumps({"status": "ok"})


@app.get("/api/v1/registry", response_class=JSONResponse)
async def get_all_services(
    include_down: bool = False,
    include_deleted: bool = False,
    include_all: bool = False,
):
    """Get all registered services, filtered by status."""
    services = APRSServices()
    all_services = services.get_all()
    store = HealthCheckStore()

    # Determine which statuses to include
    allowed_statuses = {"active"}
    if include_down or include_all:
        allowed_statuses.add("down")
    if include_deleted or include_all:
        allowed_statuses.add("deleted")

    # Convert Pydantic models to dicts and filter by status
    services_list = []
    for callsign, service in all_services.items():
        service_dict = service_to_dict(service)

        if service_dict["status"] in allowed_statuses:
            attach_last_health_check(service_dict, callsign, store)
            services_list.append(service_dict)

    return {
        "count": len(services_list),
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "services": services_list,
    }


@app.get("/api/v1/registry/{callsign}", response_class=JSONResponse)
async def get_service(callsign: str):
    """Get a single service by callsign."""
    services = APRSServices()
    callsign_upper = callsign.upper()

    try:
        service = services[callsign_upper]
        service_dict = service_to_dict(service)
        attach_last_health_check(service_dict, callsign_upper, HealthCheckStore())
        return service_dict
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Service '{callsign_upper}' not found",
        )


@app.delete("/api/v1/registry/{callsign}", response_class=JSONResponse)
async def registry_delete(callsign: str):
    """Soft delete a service (set status to deleted)."""
    services = APRSServices()
    callsign_upper = callsign.upper()

    try:
        service = services[callsign_upper]
        service_dict = service_to_dict(service)
        service_dict["status"] = "deleted"
        updated_service = registryRequest(**service_dict)
        services.add_and_persist(callsign_upper, updated_service)

        LOG.info(f"Soft deleted {callsign_upper} from the registry.")
        return {
            "status": "ok",
            "message": f"Service '{callsign_upper}' marked as deleted",
        }
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Service '{callsign_upper}' not found",
        )


async def ws_process_balls(msg):
    time.sleep(2)
    return {"call": "balls", "data": msg["message"]}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    LOG.error("CONNECTING...")
    await websocket.accept()
    while True:
        try:
            msg = await websocket.receive_json()
            LOG.info(f"msg = {msg['message']}")
            resp = await ws_process_balls(msg)
            await websocket.send_json(resp)
        except Exception as e:
            print(e)
            break
    LOG.debug("CONNECTION DEAD...")
