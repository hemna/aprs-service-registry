from pathlib import Path

from fastapi import FastAPI, WebSocket, Request, Response, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi_utils.tasks import repeat_every
from datetime import datetime, timezone
import json
import logging
from oslo_config import cfg
from pydantic import BaseModel
import time
import threading
import wrapt

from aprs_service_registry import conf, utils  # noqa
from aprs_service_registry import objectstore


LOG = logging.getLogger(__name__)
CONF = cfg.CONF

_WEB_DIR = Path(__file__).resolve().parent / "web"
app = FastAPI()
app.mount("/static", StaticFiles(directory=str(_WEB_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(_WEB_DIR / "templates"))


class registryRequest(BaseModel):
    """Request to register a service with the registry."""

    callsign: str
    description: str
    service_website: str
    software: str
    callsign_owner: str | None = None


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
        self.data[callsign] = data

    @wrapt.synchronized(lock)
    def remove(self, callsign):
        if callsign in self.data:
            del self.data[callsign]


@app.on_event("startup")
@repeat_every(seconds=60)
def save_services(*args, **kwargs):
    APRSServices().save()
    print(time.time())


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def get(request: Request):
    services = APRSServices()
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"request": request, "services": services},
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
    request_upper = registryRequest(**request_dict)
    services.add(callsign_upper, request_upper)
    for service in services:
        LOG.info(
            f"{service}: {services[service].description} - {services[service].service_website}"
        )
    return json.dumps({"status": "ok"})


@app.get("/api/v1/registry", response_class=JSONResponse)
async def get_all_services():
    """Get all registered services."""
    services = APRSServices()
    all_services = services.get_all()

    # Convert Pydantic models to dicts
    services_list = []
    for callsign, service in all_services.items():
        try:
            services_list.append(service.model_dump())
        except AttributeError:
            services_list.append(service.dict())

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
        try:
            return service.model_dump()
        except AttributeError:
            return service.dict()
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"Service '{callsign_upper}' not found"
        )


@app.delete("/api/v1/registry/{callsign}", response_class=JSONResponse)
async def registry_delete(callsign: str):
    """Remove a service from the registry."""
    services = APRSServices()
    LOG.info(f"Removing {callsign} from the registry.")
    services.remove(callsign.upper())
    return json.dumps({"status": "ok"})


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
