import json
import secrets
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal

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

from aprs_service_registry import conf, utils  # noqa
from aprs_service_registry.db import RegistryDB
from aprs_service_registry.health_checker import (
    calculate_uptime,
    check_service,
    get_aprs_connection_status,
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


def attach_last_health_check(service_dict: dict, callsign: str, db: RegistryDB) -> None:
    """Attach last_health_check info to a service dictionary."""
    last = db.get_last_health_check(callsign)
    if last:
        service_dict["last_health_check"] = last
    else:
        service_dict["last_health_check"] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan context manager for startup/shutdown."""
    # Startup: Create RegistryDB instance
    db_path = getattr(CONF.registry, "db_path", None)
    if not db_path:
        db_path = f"{CONF.registry.save_location}/registry.db"
    app.state.db = RegistryDB(db_path)

    # Set module-level _db on health_checker so it can access the database
    from aprs_service_registry import health_checker
    health_checker._db = app.state.db

    # Start the persistent APRS-IS consumer for receiving packets
    start_persistent_consumer()

    # Set up health check scheduler
    setup_scheduler()
    start_scheduler()

    yield

    # Shutdown: Stop scheduler and consumer, close DB
    stop_scheduler()
    stop_persistent_consumer()
    app.state.db.close()


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
    featured: bool = False


class PendingCommand(BaseModel):
    """A command submission awaiting moderation."""

    id: str
    callsign: str
    command_name: str
    command_description: str
    submitted_at: datetime
    submitted_by: str | None = None  # Optional submitter info


@app.get("/api/v1/health", response_class=JSONResponse)
async def health_check_endpoint():
    """System health check endpoint.

    Returns 200 if the service is healthy (web server up AND APRS-IS connected).
    Returns 503 if APRS-IS connection is down.

    Used by Docker HEALTHCHECK to detect when the service needs a restart.
    """
    aprs_status = get_aprs_connection_status()

    healthy = True
    checks = {
        "web_server": "ok",
        "aprs_is": "ok" if aprs_status["connected"] else "disconnected",
        "consumer_thread": "running" if aprs_status["consumer_running"] else "stopped",
    }

    # If health checks are enabled but APRS-IS is disconnected, report unhealthy
    if aprs_status["health_checks_enabled"] and not aprs_status["connected"]:
        healthy = False

    status_code = 200 if healthy else 503
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "healthy" if healthy else "unhealthy",
            "checks": checks,
        },
    )


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def get(request: Request):
    """Render the card-based view of services."""
    db: RegistryDB = request.app.state.db

    # Filter for website: show active, pending and down, hide deleted
    all_services = db.get_all_services(status_filter={"active", "pending", "down"})

    # Build health check info (all results for heatmap)
    filtered_services = {}
    health_results = {}

    for service in all_services:
        callsign = service["callsign"]
        filtered_services[callsign] = service
        # Get all health check results for heatmap
        health_results[callsign] = db.get_health_checks(callsign)

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
    db: RegistryDB = request.app.state.db

    # Filter for website: show active, pending and down, hide deleted
    all_services = db.get_all_services(status_filter={"active", "pending", "down"})

    filtered_services = {}
    health_checks = {}

    for service in all_services:
        callsign = service["callsign"]
        filtered_services[callsign] = service
        health_checks[callsign] = db.get_last_health_check(callsign)

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
    db: RegistryDB = request.app.state.db
    callsign_upper = data.callsign.upper()

    # Build the service data dict from the request
    try:
        request_dict = data.model_dump()
    except AttributeError:
        request_dict = data.dict()
    request_dict["callsign"] = callsign_upper

    # Convert commands from Pydantic models to dicts
    request_dict["commands"] = [
        {"name": c["name"], "description": c["description"]}
        for c in request_dict.get("commands", [])
    ]

    # Preserve existing admin-managed fields on re-registration
    existing = db.get_service(callsign_upper)
    if existing:
        # Preserve health_check_command if not provided in new request
        if request_dict.get("health_check_command") is None:
            request_dict["health_check_command"] = existing.get("health_check_command")

        # Preserve status if not explicitly changed (don't let re-registration reset deleted)
        if request_dict.get("status") is None or request_dict.get("status") == "active":
            existing_status = existing.get("status")
            if existing_status == "deleted":
                # Don't allow re-registration to un-delete a service
                request_dict["status"] = "deleted"

        # ALWAYS preserve commands - they are admin-managed and services don't know about them
        request_dict["commands"] = existing.get("commands", [])

        # ALWAYS preserve featured flag - it's admin-managed
        request_dict["featured"] = existing.get("featured", False)

    db.upsert_service(callsign_upper, request_dict, actor=("api", None))

    LOG.info(f"Registered/updated service: {callsign_upper}")
    return {"status": "ok"}


@app.get("/api/v1/registry", response_class=JSONResponse)
@limiter.limit("60/minute")
async def get_all_services(
    request: Request,
    include_down: bool = False,
    include_deleted: bool = False,
    include_all: bool = False,
):
    """Get all registered services, filtered by status."""
    db: RegistryDB = request.app.state.db

    # Determine which statuses to include
    # By default, show active, pending, and down (everything except deleted)
    allowed_statuses = {"active", "pending", "down"}
    if include_deleted or include_all:
        allowed_statuses.add("deleted")

    all_services = db.get_all_services(status_filter=allowed_statuses)

    # Attach health check info
    services_list = []
    for service_dict in all_services:
        attach_last_health_check(service_dict, service_dict["callsign"], db)
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
    db: RegistryDB = request.app.state.db
    callsign_upper = callsign.upper()

    service_dict = db.get_service(callsign_upper)
    if service_dict is None:
        raise HTTPException(
            status_code=404,
            detail=f"Service '{callsign_upper}' not found",
        )

    attach_last_health_check(service_dict, callsign_upper, db)
    return service_dict


@app.delete("/api/v1/registry/{callsign}", response_class=JSONResponse)
@limiter.limit("60/minute")
async def registry_delete(request: Request, callsign: str):
    """Soft delete a service (set status to deleted)."""
    db: RegistryDB = request.app.state.db
    callsign_upper = callsign.upper()

    service = db.get_service(callsign_upper)
    if service is None:
        raise HTTPException(
            status_code=404,
            detail=f"Service '{callsign_upper}' not found",
        )

    db.delete_service(callsign_upper, actor=("api", None))

    LOG.info(f"Soft deleted {callsign_upper} from the registry.")
    return {
        "status": "ok",
        "message": f"Service '{callsign_upper}' marked as deleted",
    }


@app.post("/api/v1/health-check/{callsign}", response_class=JSONResponse)
@limiter.limit("60/minute")
async def trigger_health_check(request: Request, callsign: str):
    """Manually trigger a health check for a specific service.

    The health check runs in a background thread and returns immediately.
    Results will be available via the /api/v1/registry endpoint.
    """
    db: RegistryDB = request.app.state.db
    callsign_upper = callsign.upper()

    service = db.get_service(callsign_upper)
    if service is None:
        raise HTTPException(
            status_code=404,
            detail=f"Service '{callsign_upper}' not found",
        )

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


def calculate_aprs_passcode(callsign: str) -> int:
    """Calculate APRS passcode for a callsign.

    The APRS passcode algorithm:
    1. Take the callsign without SSID (strip -N suffix)
    2. Convert to uppercase
    3. XOR pairs of characters with 0x73e2 as seed
    4. Mask to 15 bits (& 0x7fff)

    Args:
        callsign: Ham radio callsign (with or without SSID)

    Returns:
        Calculated APRS passcode (0-32767)
    """
    # Remove SSID if present (e.g., WB4BOR-14 -> WB4BOR)
    call = callsign.upper().split("-")[0]

    # Initial hash value
    hash_val = 0x73E2

    # Process pairs of characters
    i = 0
    while i < len(call):
        hash_val ^= ord(call[i]) << 8
        i += 1
        if i < len(call):
            hash_val ^= ord(call[i])
            i += 1

    # Mask to 15 bits
    return hash_val & 0x7FFF


def verify_aprs_passcode(callsign: str, passcode: int) -> bool:
    """Verify an APRS passcode for a callsign.

    Args:
        callsign: Ham radio callsign
        passcode: Claimed APRS passcode

    Returns:
        True if passcode is valid, False otherwise
    """
    expected = calculate_aprs_passcode(callsign)
    return passcode == expected


class CommandSubmission(BaseModel):
    """Request to submit a command suggestion."""

    command_name: str
    command_description: str
    submitter_callsign: str
    passcode: int


@app.post("/api/v1/services/{callsign}/commands", response_class=JSONResponse)
@limiter.limit("60/minute")
async def submit_command(request: Request, callsign: str, data: CommandSubmission):
    """Submit a command suggestion for a service (goes to moderation queue)."""
    db: RegistryDB = request.app.state.db
    callsign_upper = callsign.upper()
    submitter_callsign = data.submitter_callsign.strip().upper()

    # Validate submitter callsign format (basic check)
    if not submitter_callsign or len(submitter_callsign) < 3:
        raise HTTPException(
            status_code=400,
            detail="Please enter a valid callsign",
        )

    # Verify APRS passcode
    if not verify_aprs_passcode(submitter_callsign, data.passcode):
        raise HTTPException(
            status_code=401,
            detail="Invalid APRS passcode for the provided callsign",
        )

    # Verify service exists
    service = db.get_service(callsign_upper)
    if service is None:
        raise HTTPException(
            status_code=404,
            detail=f"Service '{callsign_upper}' not found",
        )

    # Normalize and validate command name/description
    command_name = data.command_name.strip()
    command_description = data.command_description.strip()

    if not command_name:
        raise HTTPException(
            status_code=400,
            detail="Command name is required",
        )
    if not command_description:
        raise HTTPException(
            status_code=400,
            detail="Command description is required",
        )

    # Reject if the service already has a command with this name
    existing_names = {
        c["name"].strip().lower()
        for c in (service.get("commands") or [])
        if c.get("name")
    }
    if command_name.lower() in existing_names:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Command '{command_name}' already exists for "
                f"{callsign_upper}"
            ),
        )

    # Reject if an identical command is already pending moderation
    pending_submissions = db.get_pending_submissions()
    pending_for_service = [
        p for p in pending_submissions if p.get("callsign", "").upper() == callsign_upper
    ]
    pending_names = {
        p["command_name"].strip().lower()
        for p in pending_for_service
        if p.get("command_name")
    }
    if command_name.lower() in pending_names:
        raise HTTPException(
            status_code=409,
            detail=(
                f"A suggestion for command '{command_name}' is already "
                f"pending review for {callsign_upper}"
            ),
        )

    # Create submission
    submission_id = str(uuid.uuid4())
    db.submit_command({
        "id": submission_id,
        "callsign": callsign_upper,
        "command_name": command_name,
        "command_description": command_description,
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "submitted_by": submitter_callsign,
    })

    LOG.info(
        f"Command suggestion submitted for {callsign_upper}: '{command_name}' by {submitter_callsign}"
    )

    return {
        "status": "ok",
        "message": "Command suggestion submitted for review",
        "id": submission_id,
    }


@app.get("/api/v1/services/{callsign}/commands", response_class=JSONResponse)
@limiter.limit("60/minute")
async def get_service_commands(request: Request, callsign: str):
    """Get approved commands for a service."""
    db: RegistryDB = request.app.state.db
    callsign_upper = callsign.upper()

    service = db.get_service(callsign_upper)
    if service is None:
        raise HTTPException(
            status_code=404,
            detail=f"Service '{callsign_upper}' not found",
        )

    commands = service.get("commands", [])
    return {"callsign": callsign_upper, "commands": commands}


# ---- Admin API for Command Moderation ----


@app.get("/api/v1/admin/pending-commands", response_class=JSONResponse)
@limiter.limit("60/minute")
async def get_pending_commands(request: Request):
    """Get all pending command submissions (admin only)."""
    db: RegistryDB = request.app.state.db
    pending = db.get_pending_submissions()

    # Sort by submission time (oldest first)
    pending.sort(key=lambda x: x.get("submitted_at", ""))

    return {"pending_commands": pending, "count": len(pending)}


@app.post("/api/v1/admin/pending-commands/{id}/approve", response_class=JSONResponse)
@limiter.limit("60/minute")
async def approve_command(request: Request, id: str):
    """Approve a pending command submission (admin only)."""
    db: RegistryDB = request.app.state.db

    # Get the pending command
    pending = db.get_submission(id)
    if not pending:
        raise HTTPException(status_code=404, detail=f"Pending command '{id}' not found")

    # Get the service
    service = db.get_service(pending["callsign"])
    if service is None:
        # Service was deleted, reject the pending command
        db.reject_submission(id, actor=("admin", None))
        raise HTTPException(
            status_code=404,
            detail=f"Service '{pending['callsign']}' no longer exists",
        )

    # Check for duplicate command name
    commands = service.get("commands", [])
    existing_names = [c["name"].lower() for c in commands]
    if pending["command_name"].lower() in existing_names:
        db.reject_submission(id, actor=("admin", None))
        return {
            "status": "ok",
            "message": f"Command '{pending['command_name']}' already exists, removed from queue",
        }

    # Approve - this adds the command to the service automatically
    db.approve_submission(id, actor=("admin", None))

    LOG.info(f"Approved command '{pending['command_name']}' for {pending['callsign']}")

    return {
        "status": "ok",
        "message": f"Command '{pending['command_name']}' approved for {pending['callsign']}",
    }


@app.delete("/api/v1/admin/pending-commands/{id}", response_class=JSONResponse)
@limiter.limit("60/minute")
async def reject_command(request: Request, id: str):
    """Reject (delete) a pending command submission (admin only)."""
    db: RegistryDB = request.app.state.db

    pending = db.get_submission(id)
    if not pending:
        raise HTTPException(status_code=404, detail=f"Pending command '{id}' not found")

    db.reject_submission(id, actor=("admin", None))

    LOG.info(f"Rejected command '{pending['command_name']}' for {pending['callsign']}")

    return {
        "status": "ok",
        "message": "Command suggestion rejected",
    }


@app.delete(
    "/api/v1/services/{callsign}/commands/{command_name}", response_class=JSONResponse
)
@limiter.limit("60/minute")
async def delete_command(request: Request, callsign: str, command_name: str):
    """Delete an approved command from a service (admin only)."""
    db: RegistryDB = request.app.state.db
    callsign_upper = callsign.upper()

    service = db.get_service(callsign_upper)
    if service is None:
        raise HTTPException(
            status_code=404,
            detail=f"Service '{callsign_upper}' not found",
        )

    # Check if command exists
    commands = service.get("commands", [])
    existing_names = [(c.get("name") or "").lower() for c in commands]
    if command_name.lower() not in existing_names:
        raise HTTPException(
            status_code=404,
            detail=f"Command '{command_name}' not found for service '{callsign_upper}'",
        )

    db.remove_command(callsign_upper, command_name, actor=("admin", None))

    LOG.info(f"Deleted command '{command_name}' from {callsign_upper}")

    return {
        "status": "ok",
        "message": f"Command '{command_name}' deleted from {callsign_upper}",
    }


class AdminCommandInput(BaseModel):
    """Input for adding a command via admin API."""

    name: str
    description: str


@app.put("/api/v1/admin/services/{callsign}/commands", response_class=JSONResponse)
@limiter.limit("60/minute")
async def admin_set_commands(
    request: Request,
    callsign: str,
    commands: list[AdminCommandInput],
    credentials: Annotated[HTTPBasicCredentials, Depends(security)],
):
    """Set/replace all commands for a service (admin only)."""
    verify_admin(credentials)

    db: RegistryDB = request.app.state.db
    callsign_upper = callsign.upper()

    service = db.get_service(callsign_upper)
    if service is None:
        raise HTTPException(
            status_code=404,
            detail=f"Service '{callsign_upper}' not found",
        )

    # Update service with new commands list
    service["commands"] = [
        {"name": c.name, "description": c.description} for c in commands
    ]
    db.upsert_service(callsign_upper, service, actor=("admin", credentials.username))

    LOG.info(f"Admin set {len(commands)} commands for {callsign_upper}")

    return {
        "status": "ok",
        "message": f"Set {len(commands)} commands for {callsign_upper}",
        "commands": service["commands"],
    }


@app.post("/api/v1/admin/services/{callsign}/commands", response_class=JSONResponse)
@limiter.limit("60/minute")
async def admin_add_command(
    request: Request,
    callsign: str,
    command: AdminCommandInput,
    credentials: Annotated[HTTPBasicCredentials, Depends(security)],
):
    """Add a single command to a service (admin only)."""
    verify_admin(credentials)

    db: RegistryDB = request.app.state.db
    callsign_upper = callsign.upper()

    service = db.get_service(callsign_upper)
    if service is None:
        raise HTTPException(
            status_code=404,
            detail=f"Service '{callsign_upper}' not found",
        )

    # Check for duplicate
    commands = service.get("commands", [])
    existing_names = [c["name"].lower() for c in commands]
    if command.name.lower() in existing_names:
        raise HTTPException(
            status_code=409,
            detail=f"Command '{command.name}' already exists for {callsign_upper}",
        )

    db.add_command(callsign_upper, command.name, command.description,
                   actor=("admin", credentials.username))

    LOG.info(f"Admin added command '{command.name}' to {callsign_upper}")

    return {
        "status": "ok",
        "message": f"Added command '{command.name}' to {callsign_upper}",
    }


@app.post("/api/v1/admin/beacon", response_class=JSONResponse)
@limiter.limit("10/minute")
async def admin_send_beacon(
    request: Request,
    credentials: Annotated[HTTPBasicCredentials, Depends(security)],
):
    """Send an APRS position beacon (admin only)."""
    verify_admin(credentials)

    from aprs_service_registry.health_checker import send_beacon

    success = send_beacon()
    if success:
        return {"status": "ok", "message": "Beacon sent successfully"}
    else:
        raise HTTPException(
            status_code=500,
            detail="Failed to send beacon - check logs for details",
        )


@app.post("/api/v1/admin/bulletins", response_class=JSONResponse)
@limiter.limit("10/minute")
async def admin_send_bulletins(
    request: Request,
    credentials: Annotated[HTTPBasicCredentials, Depends(security)],
):
    """Send APRS bulletin announcements (admin only)."""
    verify_admin(credentials)

    from aprs_service_registry.health_checker import send_bulletins

    send_bulletins()
    return {"status": "ok", "message": "Bulletins sent"}


async def ws_process_balls(msg):
    import time
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

    db: RegistryDB = request.app.state.db

    # Gather stats
    counts = db.service_count()
    total_services = counts.get("total", 0)
    active_services = counts.get("active", 0)
    pending_services = counts.get("pending", 0)
    down_services = counts.get("down", 0)
    pending_commands = len(db.get_pending_submissions())

    return templates.TemplateResponse(
        request=request,
        name="admin/dashboard.html",
        context={
            "total_services": total_services,
            "active_services": active_services,
            "pending_services": pending_services,
            "down_services": down_services,
            "pending_commands": pending_commands,
            "pending_commands_count": pending_commands,
        },
    )


@app.get("/admin/commands", response_class=HTMLResponse)
async def admin_commands(
    request: Request,
    credentials: Annotated[HTTPBasicCredentials, Depends(security)],
):
    """Admin page for moderating command suggestions."""
    verify_admin(credentials)

    db: RegistryDB = request.app.state.db
    pending_list = db.get_pending_submissions()

    # Sort by submission time (newest first)
    try:
        pending_list.sort(
            key=lambda x: x.get("submitted_at", ""),
            reverse=True,
        )
    except (TypeError, AttributeError):
        pass

    # Annotate each pending entry with a duplicate status so the admin
    # UI can warn before approval. Statuses:
    #   "ok"        - safe to approve
    #   "exists"    - service already has a command with this name
    #   "duplicate" - another pending entry for the same service has the
    #                 same name (only the first one shows ok)
    seen_pending_names: dict[tuple[str, str], str] = {}
    annotated = []
    for cmd in pending_list:
        callsign = (cmd.get("callsign") or "").upper()
        name_norm = (cmd.get("command_name") or "").strip().lower()
        dup_status = "ok"

        # Check existing approved commands on the service
        service = db.get_service(callsign)
        if service is None:
            # Service is gone - mark as exists so admin knows to reject
            dup_status = "exists"
        else:
            existing_names = {
                (c.get("name") or "").strip().lower()
                for c in (service.get("commands") or [])
                if c.get("name")
            }
            if name_norm and name_norm in existing_names:
                dup_status = "exists"

        # Check for duplicates inside the pending queue itself
        if dup_status == "ok" and name_norm:
            key = (callsign, name_norm)
            if key in seen_pending_names:
                dup_status = "duplicate"
            else:
                seen_pending_names[key] = cmd.get("id", "")

        annotated.append({"cmd": cmd, "dup_status": dup_status})

    return templates.TemplateResponse(
        request=request,
        name="admin/commands.html",
        context={
            "pending_commands": annotated,
            "pending_commands_count": len(annotated),
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

    db: RegistryDB = request.app.state.db

    pending = db.get_submission(id)
    if not pending:
        raise HTTPException(status_code=404, detail=f"Pending command '{id}' not found")

    # Get existing service
    service = db.get_service(pending["callsign"])
    if service is None:
        # Service is gone - just reject the pending entry
        db.reject_submission(id, actor=("admin", credentials.username))
        LOG.info(
            f"Admin approve: service '{pending['callsign']}' missing, "
            f"discarded pending command '{pending['command_name']}'"
        )
        return RedirectResponse(url="/admin/commands", status_code=303)

    commands = service.get("commands", []) or []

    # Skip duplicates - reject the pending entry but don't add another copy
    pending_name = (pending.get("command_name") or "").strip().lower()
    existing_names = {
        (c.get("name") or "").strip().lower() for c in commands if c.get("name")
    }
    if pending_name and pending_name in existing_names:
        db.reject_submission(id, actor=("admin", credentials.username))
        LOG.info(
            f"Admin approve: '{pending['command_name']}' already exists on "
            f"{pending['callsign']}; discarded duplicate suggestion"
        )
        return RedirectResponse(url="/admin/commands", status_code=303)

    # Approve - this adds the command to the service automatically
    db.approve_submission(id, actor=("admin", credentials.username))

    LOG.info(f"Admin approved command '{pending['command_name']}' for {pending['callsign']}")

    return RedirectResponse(url="/admin/commands", status_code=303)


@app.post("/admin/commands/{id}/reject", response_class=HTMLResponse)
async def admin_reject_command(
    request: Request,
    id: str,
    credentials: Annotated[HTTPBasicCredentials, Depends(security)],
):
    """Reject a pending command (admin web interface)."""
    verify_admin(credentials)

    db: RegistryDB = request.app.state.db

    pending = db.get_submission(id)
    if not pending:
        raise HTTPException(status_code=404, detail=f"Pending command '{id}' not found")

    db.reject_submission(id, actor=("admin", credentials.username))

    LOG.info(f"Admin rejected command '{pending['command_name']}' for {pending['callsign']}")

    return RedirectResponse(url="/admin/commands", status_code=303)


@app.get("/admin/services", response_class=HTMLResponse)
async def admin_services(
    request: Request,
    credentials: Annotated[HTTPBasicCredentials, Depends(security)],
):
    """Admin page for managing services."""
    verify_admin(credentials)

    db: RegistryDB = request.app.state.db

    # Build service list with health info
    all_services = db.get_all_services()
    service_list = []
    for service_dict in all_services:
        callsign = service_dict["callsign"]

        # Add health info
        last_result = db.get_last_health_check(callsign)
        if last_result:
            service_dict["last_health_check"] = last_result
            results = db.get_health_checks(callsign)
            service_dict["uptime"] = calculate_uptime(results)
        else:
            service_dict["last_health_check"] = None
            service_dict["uptime"] = None

        service_list.append(service_dict)

    # Sort by callsign
    service_list.sort(key=lambda x: x["callsign"])

    # Get pending commands count for sidebar badge
    pending_commands_count = len(db.get_pending_submissions())

    return templates.TemplateResponse(
        request=request,
        name="admin/services.html",
        context={
            "services": service_list,
            "pending_commands_count": pending_commands_count,
        },
    )


@app.get("/admin/services/new", response_class=HTMLResponse)
async def admin_new_service_form(
    request: Request,
    credentials: Annotated[HTTPBasicCredentials, Depends(security)],
):
    """Admin page for adding a new service."""
    verify_admin(credentials)

    db: RegistryDB = request.app.state.db
    pending_commands_count = len(db.get_pending_submissions())

    return templates.TemplateResponse(
        request=request,
        name="admin/service_new.html",
        context={
            "pending_commands_count": pending_commands_count,
            "error": None,
        },
    )


@app.post("/admin/services/new", response_class=HTMLResponse)
async def admin_create_service(
    request: Request,
    credentials: Annotated[HTTPBasicCredentials, Depends(security)],
):
    """Create a new service (admin web interface)."""
    verify_admin(credentials)

    db: RegistryDB = request.app.state.db
    form = await request.form()

    callsign = (form.get("callsign") or "").strip().upper()
    if not callsign:
        pending_commands_count = len(db.get_pending_submissions())
        return templates.TemplateResponse(
            request=request,
            name="admin/service_new.html",
            context={
                "pending_commands_count": pending_commands_count,
                "error": "Callsign is required.",
            },
        )

    # Check if service already exists
    existing = db.get_service(callsign)
    if existing is not None:
        pending_commands_count = len(db.get_pending_submissions())
        return templates.TemplateResponse(
            request=request,
            name="admin/service_new.html",
            context={
                "pending_commands_count": pending_commands_count,
                "error": f"Service '{callsign}' already exists.",
            },
        )

    # Build service from form data
    service_dict = {
        "callsign": callsign,
        "description": form.get("description", "").strip(),
        "service_website": form.get("service_website", "").strip(),
        "software": form.get("software", "").strip(),
        "callsign_owner": form.get("callsign_owner", "").strip() or None,
        "status": form.get("status", "active"),
        "health_check_command": form.get("health_check_command", "").strip() or None,
        "featured": form.get("featured") == "true",
        "commands": [],
    }

    db.upsert_service(callsign, service_dict, actor=("admin", credentials.username))

    LOG.info(f"Admin created new service {callsign}")

    return RedirectResponse(url=f"/admin/services/{callsign}", status_code=303)


@app.get("/admin/services/{callsign}", response_class=HTMLResponse)
async def admin_service_detail(
    request: Request,
    callsign: str,
    credentials: Annotated[HTTPBasicCredentials, Depends(security)],
):
    """Admin page for viewing/editing a single service."""
    verify_admin(credentials)

    db: RegistryDB = request.app.state.db
    callsign_upper = callsign.upper()

    service_dict = db.get_service(callsign_upper)
    if service_dict is None:
        raise HTTPException(
            status_code=404, detail=f"Service '{callsign_upper}' not found"
        )

    # Get health check history
    health_results = db.get_health_checks(callsign_upper, limit=50)
    uptime = calculate_uptime(health_results)

    # Get pending commands count for sidebar badge
    pending_commands_count = len(db.get_pending_submissions())

    return templates.TemplateResponse(
        request=request,
        name="admin/service_detail.html",
        context={
            "service": service_dict,
            "health_results": health_results,
            "uptime": uptime,
            "pending_commands_count": pending_commands_count,
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

    db: RegistryDB = request.app.state.db
    callsign_upper = callsign.upper()

    service_dict = db.get_service(callsign_upper)
    if service_dict is None:
        raise HTTPException(
            status_code=404, detail=f"Service '{callsign_upper}' not found"
        )

    # Get form data
    form = await request.form()

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
    # Preserve commands - they are not editable via this form
    # (commands are managed via the command moderation system)
    service_dict["commands"] = service_dict.get("commands", [])
    # Featured flag - checkbox sends "true" when checked, absent when unchecked
    service_dict["featured"] = form.get("featured") == "true"

    db.upsert_service(callsign_upper, service_dict, actor=("admin", credentials.username))

    LOG.info(f"Admin updated service {callsign_upper}")

    return RedirectResponse(url=f"/admin/services/{callsign_upper}", status_code=303)


@app.post("/admin/services/{callsign}/toggle-featured", response_class=HTMLResponse)
async def admin_toggle_featured(
    request: Request,
    callsign: str,
    credentials: Annotated[HTTPBasicCredentials, Depends(security)],
):
    """Toggle the featured flag on a service (admin web interface)."""
    verify_admin(credentials)

    db: RegistryDB = request.app.state.db
    callsign_upper = callsign.upper()

    new_featured = db.toggle_featured(callsign_upper, actor=("admin", credentials.username))
    if new_featured is None:
        raise HTTPException(
            status_code=404, detail=f"Service '{callsign_upper}' not found"
        )

    LOG.info(f"Admin {'featured' if new_featured else 'unfeatured'} service {callsign_upper}")

    # Redirect back to referring page (detail or list)
    referer = request.headers.get("referer", f"/admin/services/{callsign_upper}")
    return RedirectResponse(url=referer, status_code=303)


@app.post("/admin/services/{callsign}/delete", response_class=HTMLResponse)
async def admin_delete_service(
    request: Request,
    callsign: str,
    credentials: Annotated[HTTPBasicCredentials, Depends(security)],
):
    """Soft delete a service - mark status as 'deleted' (admin web interface)."""
    verify_admin(credentials)

    db: RegistryDB = request.app.state.db
    callsign_upper = callsign.upper()

    service = db.get_service(callsign_upper)
    if service is None:
        raise HTTPException(
            status_code=404, detail=f"Service '{callsign_upper}' not found"
        )

    db.delete_service(callsign_upper, actor=("admin", credentials.username))

    LOG.info(f"Admin soft-deleted service {callsign_upper}")

    return RedirectResponse(url="/admin/services", status_code=303)


@app.post("/admin/services/{callsign}/undelete", response_class=HTMLResponse)
async def admin_undelete_service(
    request: Request,
    callsign: str,
    credentials: Annotated[HTTPBasicCredentials, Depends(security)],
):
    """Restore a soft-deleted service, setting status back to 'active'."""
    verify_admin(credentials)

    db: RegistryDB = request.app.state.db
    callsign_upper = callsign.upper()

    service = db.get_service(callsign_upper)
    if service is None:
        raise HTTPException(
            status_code=404, detail=f"Service '{callsign_upper}' not found"
        )
    if service.get("status") != "deleted":
        raise HTTPException(
            status_code=400, detail=f"Service '{callsign_upper}' is not deleted"
        )

    db.update_service_status(
        callsign_upper, "active", actor=("admin", credentials.username)
    )

    LOG.info(f"Admin restored (undeleted) service {callsign_upper}")

    return RedirectResponse(url=f"/admin/services/{callsign_upper}", status_code=303)


@app.post("/admin/services/{callsign}/health-check", response_class=HTMLResponse)
async def admin_trigger_health_check(
    request: Request,
    callsign: str,
    credentials: Annotated[HTTPBasicCredentials, Depends(security)],
):
    """Trigger a health check for a service (admin web interface)."""
    verify_admin(credentials)

    db: RegistryDB = request.app.state.db
    callsign_upper = callsign.upper()

    service = db.get_service(callsign_upper)
    if service is None:
        raise HTTPException(
            status_code=404, detail=f"Service '{callsign_upper}' not found"
        )

    # Trigger health check in background thread (check_service is sync)
    thread = threading.Thread(target=check_service, args=(callsign_upper,))
    thread.daemon = True
    thread.start()

    LOG.info(f"Admin triggered health check for {callsign_upper}")

    return RedirectResponse(url=f"/admin/services/{callsign_upper}", status_code=303)


@app.post(
    "/admin/services/{callsign}/commands/{command_name}/delete",
    response_class=HTMLResponse,
)
async def admin_delete_command(
    request: Request,
    callsign: str,
    command_name: str,
    credentials: Annotated[HTTPBasicCredentials, Depends(security)],
):
    """Delete a single command from a service (admin web interface)."""
    verify_admin(credentials)

    db: RegistryDB = request.app.state.db
    callsign_upper = callsign.upper()

    service = db.get_service(callsign_upper)
    if service is None:
        raise HTTPException(
            status_code=404, detail=f"Service '{callsign_upper}' not found"
        )

    commands = service.get("commands", []) or []

    # Check command exists
    target = command_name.lower()
    found = any((c.get("name") or "").lower() == target for c in commands)

    if not found:
        raise HTTPException(
            status_code=404,
            detail=f"Command '{command_name}' not found for service '{callsign_upper}'",
        )

    db.remove_command(callsign_upper, command_name, actor=("admin", credentials.username))

    LOG.info(f"Admin deleted command '{command_name}' from {callsign_upper}")

    return RedirectResponse(url=f"/admin/services/{callsign_upper}", status_code=303)
