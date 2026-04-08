import json
import secrets
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal

import wrapt
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from loguru import logger
from oslo_config import cfg
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from aprs_service_registry import conf, objectstore, utils  # noqa
from aprs_service_registry.health_checker import (
    HealthCheckStore,
    calculate_uptime,
    check_service,
    get_checkable_services,
    setup_scheduler,
    start_persistent_consumer,
    start_scheduler,
    stop_persistent_consumer,
    stop_scheduler,
)


LOG = logger
CONF = cfg.CONF

_WEB_DIR = Path(__file__).resolve().parent / "web"

# Rate limiter: 60 requests per minute per IP for API endpoints
limiter = Limiter(key_func=get_remote_address)


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
    PendingCommandStore().load()

    # Start the persistent APRS-IS consumer for receiving packets
    start_persistent_consumer()

    # Set up health check scheduler
    setup_scheduler()
    start_scheduler()

    yield

    # Shutdown: Stop scheduler, consumer, and save data
    stop_scheduler()
    stop_persistent_consumer()
    APRSServices().save()
    HealthCheckStore().save()
    PendingCommandStore().save()


app = FastAPI(lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.mount("/static", StaticFiles(directory=str(_WEB_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(_WEB_DIR / "templates"))

# HTTP Basic Auth for admin routes
security = HTTPBasic()


def verify_admin(credentials: Annotated[HTTPBasicCredentials, Depends(security)]):
    """Verify admin credentials using HTTP Basic Auth."""
    admin_username = CONF.registry.admin_username
    admin_password = CONF.registry.admin_password

    if not admin_password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin interface is disabled",
        )

    is_valid_username = secrets.compare_digest(
        credentials.username.encode("utf-8"),
        admin_username.encode("utf-8"),
    )
    is_valid_password = secrets.compare_digest(
        credentials.password.encode("utf-8"),
        admin_password.encode("utf-8"),
    )

    if not (is_valid_username and is_valid_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )


class ServiceCommand(BaseModel):
    """A command that a service accepts."""

    name: str
    description: str


class registryRequest(BaseModel):
    """Request to register a service with the registry."""

    callsign: str
    description: str
    service_website: str
    software: str
    callsign_owner: str | None = None
    status: Literal["active", "pending", "down", "deleted"] = "active"
    health_check_command: str | None = None
    commands: list[ServiceCommand] = []


class PendingCommand(BaseModel):
    """A command submission awaiting moderation."""

    id: str
    callsign: str
    command_name: str
    command_description: str
    submitted_at: datetime
    submitted_by: str | None = None  # Optional submitter info


class PendingCommandStore(objectstore.ObjectStoreMixin):
    """Singleton store for pending command submissions."""

    _instance = None
    lock = threading.Lock()
    data: dict = {}  # {id: PendingCommand}

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init_store()
            cls._instance.data = {}
        return cls._instance

    def _save_filename(self):
        """Override to use different filename."""
        save_location = CONF.registry.save_location
        return f"{save_location}/pending_commands.p"

    @wrapt.synchronized(lock)
    def add(self, pending: PendingCommand):
        """Add a pending command submission."""
        self.data[pending.id] = pending
        self._save_unlocked()

    @wrapt.synchronized(lock)
    def remove(self, id: str):
        """Remove a pending command by ID."""
        if id in self.data:
            del self.data[id]
            self._save_unlocked()

    @wrapt.synchronized(lock)
    def get(self, id: str) -> PendingCommand | None:
        """Get a pending command by ID."""
        return self.data.get(id)

    @wrapt.synchronized(lock)
    def get_all(self) -> dict:
        """Get all pending commands."""
        return dict(self.data)

    @wrapt.synchronized(lock)
    def get_by_callsign(self, callsign: str) -> list[PendingCommand]:
        """Get all pending commands for a specific service."""
        return [p for p in self.data.values() if p.callsign.upper() == callsign.upper()]


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
    """Render the card-based view of services."""
    services = APRSServices()
    all_services = services.get_all()
    store = HealthCheckStore()

    # Filter for website: show active, pending and down, hide deleted
    # Build health check info (all results for heatmap)
    filtered_services = {}
    health_results = {}

    for callsign, service in all_services.items():
        service_dict = service_to_dict(service)
        status = service_dict["status"]

        if status in ("active", "pending", "down"):
            filtered_services[callsign] = service
            # Get all health check results for heatmap
            health_results[callsign] = store.get_results(callsign)

    # Sort services alphabetically by callsign
    sorted_services = dict(
        sorted(filtered_services.items(), key=lambda x: x[0].upper())
    )

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "request": request,
            "services": sorted_services,
            "health_results": health_results,
            "calculate_uptime": calculate_uptime,
        },
    )


@app.get("/services", response_class=HTMLResponse, include_in_schema=False)
async def services_page(request: Request):
    """Render the table view of services (moved from index)."""
    services = APRSServices()
    all_services = services.get_all()
    store = HealthCheckStore()

    # Filter for website: show active, pending and down, hide deleted
    filtered_services = {}
    health_checks = {}

    for callsign, service in all_services.items():
        service_dict = service_to_dict(service)
        status = service_dict["status"]

        if status in ("active", "pending", "down"):
            filtered_services[callsign] = service
            health_checks[callsign] = store.get_last_result(callsign)

    # Sort services alphabetically by callsign
    sorted_services = dict(
        sorted(filtered_services.items(), key=lambda x: x[0].upper())
    )

    return templates.TemplateResponse(
        request=request,
        name="services.html",
        context={
            "request": request,
            "services": sorted_services,
            "health_checks": health_checks,
        },
    )


# Documentation routes
@app.get("/about", response_class=HTMLResponse, include_in_schema=False)
async def about_page(request: Request):
    """Render the About documentation page."""
    return templates.TemplateResponse(
        request=request,
        name="about.html",
        context={"request": request, "active_page": "about"},
    )


@app.get("/guide", response_class=HTMLResponse, include_in_schema=False)
async def guide_page(request: Request):
    """Render the Guide documentation page."""
    return templates.TemplateResponse(
        request=request,
        name="guide.html",
        context={"request": request, "active_page": "guide"},
    )


@app.get("/developers", response_class=HTMLResponse, include_in_schema=False)
async def developers_page(request: Request):
    """Render the Developers documentation page."""
    return templates.TemplateResponse(
        request=request,
        name="developers.html",
        context={"request": request, "active_page": "developers"},
    )


@app.get("/service-types", response_class=HTMLResponse, include_in_schema=False)
async def service_types_page(request: Request):
    """Render the Service Types documentation page."""
    return templates.TemplateResponse(
        request=request,
        name="service_types.html",
        context={"request": request, "active_page": "service-types"},
    )


@app.get("/faq", response_class=HTMLResponse, include_in_schema=False)
async def faq_page(request: Request):
    """Render the FAQ documentation page."""
    return templates.TemplateResponse(
        request=request,
        name="faq.html",
        context={"request": request, "active_page": "faq"},
    )


@app.post("/api/v1/registry", response_class=JSONResponse)
@limiter.limit("60/minute")
async def registry(request: Request, data: registryRequest):
    """Register a service with the registry and/or update."""
    LOG.info(f"registry: {data}")
    services = APRSServices()
    callsign_upper = data.callsign.upper()

    # Create a new model instance with uppercased callsign
    # Use dict() for Pydantic v1 compatibility, model_dump() for v2
    try:
        request_dict = data.model_dump()
    except AttributeError:
        request_dict = data.dict()
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
@limiter.limit("60/minute")
async def get_all_services(
    request: Request,
    include_down: bool = False,
    include_deleted: bool = False,
    include_all: bool = False,
):
    """Get all registered services, filtered by status."""
    services = APRSServices()
    all_services = services.get_all()
    store = HealthCheckStore()

    # Determine which statuses to include
    # By default, show active, pending, and down (everything except deleted)
    allowed_statuses = {"active", "pending", "down"}
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
@limiter.limit("60/minute")
async def get_service(request: Request, callsign: str):
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
@limiter.limit("60/minute")
async def registry_delete(request: Request, callsign: str):
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


@app.post("/api/v1/health-check/{callsign}", response_class=JSONResponse)
@limiter.limit("60/minute")
async def trigger_health_check(request: Request, callsign: str):
    """Manually trigger a health check for a specific service.

    The health check runs in a background thread and returns immediately.
    Results will be available via the /api/v1/registry endpoint.
    """
    services = APRSServices()
    callsign_upper = callsign.upper()

    try:
        _service = services[callsign_upper]  # Just check it exists
        LOG.info(f"Manually triggering health check for {callsign_upper}")

        # Run in background thread so we don't block the web server
        thread = threading.Thread(target=check_service, args=(callsign_upper,))
        thread.daemon = True
        thread.start()

        return {
            "status": "ok",
            "callsign": callsign_upper,
            "message": "Health check started in background",
        }
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Service '{callsign_upper}' not found",
        )


@app.post("/api/v1/health-check", response_class=JSONResponse)
@limiter.limit("60/minute")
async def trigger_all_health_checks(request: Request):
    """Manually trigger health checks for all active services.

    Health checks run in background threads and this returns immediately.
    Results will be available via the /api/v1/registry endpoint.
    """
    checkable = get_checkable_services()

    LOG.info(f"Manually triggering health checks for {len(checkable)} services")

    # Start all health checks in background threads
    for callsign in checkable:
        thread = threading.Thread(target=check_service, args=(callsign,))
        thread.daemon = True
        thread.start()

    return {
        "status": "ok",
        "message": f"Health checks started for {len(checkable)} services in background",
        "services": checkable,
    }


# ---- Command Submission API ----


class CommandSubmission(BaseModel):
    """Request to submit a command suggestion."""

    command_name: str
    command_description: str
    submitted_by: str | None = None


@app.post("/api/v1/services/{callsign}/commands", response_class=JSONResponse)
@limiter.limit("60/minute")
async def submit_command(request: Request, callsign: str, data: CommandSubmission):
    """Submit a command suggestion for a service (goes to moderation queue)."""
    services = APRSServices()
    callsign_upper = callsign.upper()

    # Verify service exists
    try:
        _service = services[callsign_upper]
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Service '{callsign_upper}' not found",
        )

    # Create pending command
    pending = PendingCommand(
        id=str(uuid.uuid4()),
        callsign=callsign_upper,
        command_name=data.command_name.strip(),
        command_description=data.command_description.strip(),
        submitted_at=datetime.now(timezone.utc),
        submitted_by=data.submitted_by.strip() if data.submitted_by else None,
    )

    # Add to pending store
    store = PendingCommandStore()
    store.add(pending)

    LOG.info(
        f"Command suggestion submitted for {callsign_upper}: '{pending.command_name}'"
    )

    return {
        "status": "ok",
        "message": "Command suggestion submitted for review",
        "id": pending.id,
    }


@app.get("/api/v1/services/{callsign}/commands", response_class=JSONResponse)
@limiter.limit("60/minute")
async def get_service_commands(request: Request, callsign: str):
    """Get approved commands for a service."""
    services = APRSServices()
    callsign_upper = callsign.upper()

    try:
        service = services[callsign_upper]
        service_dict = service_to_dict(service)
        commands = service_dict.get("commands", [])
        return {"callsign": callsign_upper, "commands": commands}
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Service '{callsign_upper}' not found",
        )


# ---- Admin API for Command Moderation ----


@app.get("/api/v1/admin/pending-commands", response_class=JSONResponse)
@limiter.limit("60/minute")
async def get_pending_commands(request: Request):
    """Get all pending command submissions (admin only)."""
    store = PendingCommandStore()
    pending = store.get_all()

    # Convert to list of dicts for JSON response
    result = []
    for id, cmd in pending.items():
        result.append(
            {
                "id": cmd.id,
                "callsign": cmd.callsign,
                "command_name": cmd.command_name,
                "command_description": cmd.command_description,
                "submitted_at": cmd.submitted_at.isoformat(),
                "submitted_by": cmd.submitted_by,
            }
        )

    # Sort by submission time (oldest first)
    result.sort(key=lambda x: x["submitted_at"])

    return {"pending_commands": result, "count": len(result)}


@app.post("/api/v1/admin/pending-commands/{id}/approve", response_class=JSONResponse)
@limiter.limit("60/minute")
async def approve_command(request: Request, id: str):
    """Approve a pending command submission (admin only)."""
    pending_store = PendingCommandStore()
    services = APRSServices()

    # Get the pending command
    pending = pending_store.get(id)
    if not pending:
        raise HTTPException(status_code=404, detail=f"Pending command '{id}' not found")

    # Get the service
    try:
        service = services[pending.callsign]
    except KeyError:
        # Service was deleted, remove the pending command
        pending_store.remove(id)
        raise HTTPException(
            status_code=404,
            detail=f"Service '{pending.callsign}' no longer exists",
        )

    # Add command to service
    service_dict = service_to_dict(service)
    commands = service_dict.get("commands", [])

    # Check for duplicate command name
    existing_names = [c["name"].lower() for c in commands]
    if pending.command_name.lower() in existing_names:
        pending_store.remove(id)
        return {
            "status": "ok",
            "message": f"Command '{pending.command_name}' already exists, removed from queue",
        }

    # Add the new command
    commands.append(
        {
            "name": pending.command_name,
            "description": pending.command_description,
        }
    )
    service_dict["commands"] = commands

    # Save updated service
    updated_service = registryRequest(**service_dict)
    services.add_and_persist(pending.callsign, updated_service)

    # Remove from pending
    pending_store.remove(id)

    LOG.info(f"Approved command '{pending.command_name}' for {pending.callsign}")

    return {
        "status": "ok",
        "message": f"Command '{pending.command_name}' approved for {pending.callsign}",
    }


@app.delete("/api/v1/admin/pending-commands/{id}", response_class=JSONResponse)
@limiter.limit("60/minute")
async def reject_command(request: Request, id: str):
    """Reject (delete) a pending command submission (admin only)."""
    pending_store = PendingCommandStore()

    pending = pending_store.get(id)
    if not pending:
        raise HTTPException(status_code=404, detail=f"Pending command '{id}' not found")

    pending_store.remove(id)

    LOG.info(f"Rejected command '{pending.command_name}' for {pending.callsign}")

    return {
        "status": "ok",
        "message": f"Command suggestion rejected",
    }


@app.delete(
    "/api/v1/services/{callsign}/commands/{command_name}", response_class=JSONResponse
)
@limiter.limit("60/minute")
async def delete_command(request: Request, callsign: str, command_name: str):
    """Delete an approved command from a service (admin only)."""
    services = APRSServices()
    callsign_upper = callsign.upper()

    try:
        service = services[callsign_upper]
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Service '{callsign_upper}' not found",
        )

    service_dict = service_to_dict(service)
    commands = service_dict.get("commands", [])

    # Find and remove the command
    original_len = len(commands)
    commands = [c for c in commands if c["name"].lower() != command_name.lower()]

    if len(commands) == original_len:
        raise HTTPException(
            status_code=404,
            detail=f"Command '{command_name}' not found for service '{callsign_upper}'",
        )

    service_dict["commands"] = commands
    updated_service = registryRequest(**service_dict)
    services.add_and_persist(callsign_upper, updated_service)

    LOG.info(f"Deleted command '{command_name}' from {callsign_upper}")

    return {
        "status": "ok",
        "message": f"Command '{command_name}' deleted from {callsign_upper}",
    }


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


# ============================================================================
# Admin Web Interface Routes
# ============================================================================


@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
    credentials: Annotated[HTTPBasicCredentials, Depends(security)],
):
    """Admin dashboard - overview page."""
    verify_admin(credentials)

    services = APRSServices()
    health_store = HealthCheckStore()
    pending_store = PendingCommandStore()

    # Gather stats
    total_services = len(services.data)
    active_services = sum(
        1 for s in services.data.values() if getattr(s, "status", "active") == "active"
    )
    pending_services = sum(
        1 for s in services.data.values() if getattr(s, "status", None) == "pending"
    )
    down_services = sum(
        1 for s in services.data.values() if getattr(s, "status", None) == "down"
    )
    pending_commands = len(pending_store.data)

    return templates.TemplateResponse(
        request=request,
        name="admin/dashboard.html",
        context={
            "total_services": total_services,
            "active_services": active_services,
            "pending_services": pending_services,
            "down_services": down_services,
            "pending_commands": pending_commands,
        },
    )


@app.get("/admin/commands", response_class=HTMLResponse)
async def admin_commands(
    request: Request,
    credentials: Annotated[HTTPBasicCredentials, Depends(security)],
):
    """Admin page for moderating command suggestions."""
    verify_admin(credentials)

    pending_store = PendingCommandStore()
    pending_list = list(pending_store.data.values())

    # Sort by submission time (newest first), handling potential data issues
    try:
        pending_list.sort(
            key=lambda x: (
                x.submitted_at if isinstance(x.submitted_at, datetime) else datetime.min
            ),
            reverse=True,
        )
    except (TypeError, AttributeError):
        # If sorting fails, just use unsorted list
        pass

    return templates.TemplateResponse(
        request=request,
        name="admin/commands.html",
        context={
            "pending_commands": pending_list,
        },
    )


@app.post("/admin/commands/{id}/approve", response_class=HTMLResponse)
async def admin_approve_command(
    request: Request,
    id: str,
    credentials: Annotated[HTTPBasicCredentials, Depends(security)],
):
    """Approve a pending command (admin web interface)."""
    verify_admin(credentials)

    pending_store = PendingCommandStore()
    services = APRSServices()

    pending = pending_store.get(id)
    if not pending:
        raise HTTPException(status_code=404, detail=f"Pending command '{id}' not found")

    # Get existing service
    try:
        service = services[pending.callsign]
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"Service '{pending.callsign}' not found"
        )

    # Add command to service
    service_dict = service_to_dict(service)
    commands = service_dict.get("commands", [])
    commands.append(
        {"name": pending.command_name, "description": pending.command_description}
    )
    service_dict["commands"] = commands

    updated_service = registryRequest(**service_dict)
    services.add_and_persist(pending.callsign, updated_service)

    # Remove from pending
    pending_store.remove(id)

    LOG.info(f"Admin approved command '{pending.command_name}' for {pending.callsign}")

    return RedirectResponse(url="/admin/commands", status_code=303)


@app.post("/admin/commands/{id}/reject", response_class=HTMLResponse)
async def admin_reject_command(
    request: Request,
    id: str,
    credentials: Annotated[HTTPBasicCredentials, Depends(security)],
):
    """Reject a pending command (admin web interface)."""
    verify_admin(credentials)

    pending_store = PendingCommandStore()

    pending = pending_store.get(id)
    if not pending:
        raise HTTPException(status_code=404, detail=f"Pending command '{id}' not found")

    pending_store.remove(id)

    LOG.info(f"Admin rejected command '{pending.command_name}' for {pending.callsign}")

    return RedirectResponse(url="/admin/commands", status_code=303)


@app.get("/admin/services", response_class=HTMLResponse)
async def admin_services(
    request: Request,
    credentials: Annotated[HTTPBasicCredentials, Depends(security)],
):
    """Admin page for managing services."""
    verify_admin(credentials)

    services = APRSServices()
    health_store = HealthCheckStore()

    # Build service list with health info
    service_list = []
    for callsign, service in services.data.items():
        service_dict = service_to_dict(service)
        service_dict["callsign"] = callsign

        # Add health info
        last_result = health_store.get_last_result(callsign)
        if last_result:
            service_dict["last_health_check"] = last_result
            results = health_store.get_results(callsign)
            service_dict["uptime"] = calculate_uptime(results)
        else:
            service_dict["last_health_check"] = None
            service_dict["uptime"] = None

        service_list.append(service_dict)

    # Sort by callsign
    service_list.sort(key=lambda x: x["callsign"])

    return templates.TemplateResponse(
        request=request,
        name="admin/services.html",
        context={
            "services": service_list,
        },
    )


@app.get("/admin/services/{callsign}", response_class=HTMLResponse)
async def admin_service_detail(
    request: Request,
    callsign: str,
    credentials: Annotated[HTTPBasicCredentials, Depends(security)],
):
    """Admin page for viewing/editing a single service."""
    verify_admin(credentials)

    services = APRSServices()
    health_store = HealthCheckStore()
    callsign_upper = callsign.upper()

    try:
        service = services[callsign_upper]
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"Service '{callsign_upper}' not found"
        )

    service_dict = service_to_dict(service)
    service_dict["callsign"] = callsign_upper

    # Get health check history
    health_results = health_store.get_results(callsign_upper)[:50]
    uptime = calculate_uptime(health_results)

    return templates.TemplateResponse(
        request=request,
        name="admin/service_detail.html",
        context={
            "service": service_dict,
            "health_results": health_results,
            "uptime": uptime,
        },
    )


@app.post("/admin/services/{callsign}/edit", response_class=HTMLResponse)
async def admin_edit_service(
    request: Request,
    callsign: str,
    credentials: Annotated[HTTPBasicCredentials, Depends(security)],
):
    """Update a service (admin web interface)."""
    verify_admin(credentials)

    services = APRSServices()
    callsign_upper = callsign.upper()

    try:
        service = services[callsign_upper]
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"Service '{callsign_upper}' not found"
        )

    # Get form data
    form = await request.form()

    service_dict = service_to_dict(service)
    service_dict["description"] = form.get(
        "description", service_dict.get("description", "")
    )
    service_dict["service_website"] = form.get(
        "service_website", service_dict.get("service_website", "")
    )
    service_dict["software"] = form.get("software", service_dict.get("software", ""))
    service_dict["callsign_owner"] = form.get(
        "callsign_owner", service_dict.get("callsign_owner")
    )
    service_dict["status"] = form.get("status", service_dict.get("status", "active"))
    service_dict["health_check_command"] = form.get(
        "health_check_command", service_dict.get("health_check_command")
    )

    updated_service = registryRequest(**service_dict)
    services.add_and_persist(callsign_upper, updated_service)

    LOG.info(f"Admin updated service {callsign_upper}")

    return RedirectResponse(url=f"/admin/services/{callsign_upper}", status_code=303)


@app.post("/admin/services/{callsign}/delete", response_class=HTMLResponse)
async def admin_delete_service(
    request: Request,
    callsign: str,
    credentials: Annotated[HTTPBasicCredentials, Depends(security)],
):
    """Delete a service (admin web interface)."""
    verify_admin(credentials)

    services = APRSServices()
    callsign_upper = callsign.upper()

    try:
        del services[callsign_upper]
        services.save()
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"Service '{callsign_upper}' not found"
        )

    LOG.info(f"Admin deleted service {callsign_upper}")

    return RedirectResponse(url="/admin/services", status_code=303)


@app.post("/admin/services/{callsign}/health-check", response_class=HTMLResponse)
async def admin_trigger_health_check(
    request: Request,
    callsign: str,
    credentials: Annotated[HTTPBasicCredentials, Depends(security)],
):
    """Trigger a health check for a service (admin web interface)."""
    verify_admin(credentials)

    services = APRSServices()
    callsign_upper = callsign.upper()

    try:
        service = services[callsign_upper]
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"Service '{callsign_upper}' not found"
        )

    # Trigger health check in background
    import asyncio

    asyncio.create_task(check_service(callsign_upper, service))

    LOG.info(f"Admin triggered health check for {callsign_upper}")

    return RedirectResponse(url=f"/admin/services/{callsign_upper}", status_code=303)
