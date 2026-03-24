"""Health check functionality for APRS services."""

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import wrapt
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from oslo_config import cfg

from aprs_service_registry import objectstore


LOG = logging.getLogger(__name__)
CONF = cfg.CONF

MAX_RESULTS_PER_SERVICE = 3
SECONDS_PER_HOUR = 3600


@dataclass
class HealthCheckResult:
    """Result of a single health check for a service."""

    timestamp: datetime
    success: bool
    response_time_ms: int | None  # None if timeout
    response_text: str | None  # First 100 chars of response
    error: str | None  # Error message if failed


class HealthCheckStore(objectstore.ObjectStoreMixin):
    """Singleton store for health check results."""

    _instance = None
    lock = threading.Lock()
    data: dict = {}  # {callsign: [HealthCheckResult, ...]}

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init_store()
            cls._instance.data = {}
        return cls._instance

    def _save_filename(self):
        """Override to use different filename than services."""
        save_location = CONF.registry.save_location
        return f"{save_location}/healthchecks.p"

    @wrapt.synchronized(lock)
    def add_result(self, callsign: str, result: HealthCheckResult):
        """Add a health check result for a service, keeping only last 3."""
        callsign_upper = callsign.upper()
        if callsign_upper not in self.data:
            self.data[callsign_upper] = []

        # Prepend new result (most recent first)
        self.data[callsign_upper].insert(0, result)

        # Keep only last 3
        self.data[callsign_upper] = self.data[callsign_upper][:MAX_RESULTS_PER_SERVICE]

    @wrapt.synchronized(lock)
    def get_results(self, callsign: str) -> list[HealthCheckResult]:
        """Get all health check results for a service."""
        return self.data.get(callsign.upper(), [])

    def get_last_result(self, callsign: str) -> HealthCheckResult | None:
        """Get the most recent health check result for a service."""
        results = self.get_results(callsign)
        return results[0] if results else None


def send_and_wait_for_response(
    callsign: str,
    message: str,
    timeout: int,
) -> tuple[str | None, int | None]:
    """Send APRS message and wait for response.

    Returns:
        Tuple of (response_text, response_time_ms) or (None, None) on timeout.

    Note: This is a placeholder that will be implemented with actual APRSD
    integration. For now, it always returns timeout for testing purposes.
    """
    # PLACEHOLDER: APRSD integration will be implemented in a separate task
    # This stub allows the rest of the health check system to be tested
    LOG.warning(
        f"APRSD integration not yet implemented. Would send '{message}' to {callsign}"
    )
    return (None, None)


def check_service(callsign: str) -> None:
    """Run a health check for a single service.

    Skips services that are:
    - Deleted (status == "deleted")
    - Missing health_check_command
    """
    from aprs_service_registry.main import APRSServices

    services = APRSServices()
    store = HealthCheckStore()

    try:
        service = services[callsign.upper()]
    except KeyError:
        LOG.warning(f"Service {callsign} not found, skipping health check")
        return

    # Get service dict for status check
    try:
        service_dict = service.model_dump()
    except AttributeError:
        service_dict = service.dict()

    # Skip deleted services
    status = service_dict.get("status", "active")
    if status == "deleted":
        LOG.debug(f"Skipping health check for deleted service {callsign}")
        return

    # Skip services without health_check_command
    health_check_command = service_dict.get("health_check_command")
    if not health_check_command:
        LOG.debug(f"Skipping health check for {callsign}: no health_check_command")
        return

    LOG.info(f"Running health check for {callsign}: sending '{health_check_command}'")

    # Send message and wait for response
    timeout = CONF.registry.health_check_timeout
    response_text, response_time_ms = send_and_wait_for_response(
        callsign,
        health_check_command,
        timeout,
    )

    # Record result
    if response_text is not None:
        result = HealthCheckResult(
            timestamp=datetime.now(timezone.utc),
            success=True,
            response_time_ms=response_time_ms,
            response_text=response_text[:100] if response_text else None,
            error=None,
        )
        LOG.info(f"Health check for {callsign}: SUCCESS ({response_time_ms}ms)")
    else:
        result = HealthCheckResult(
            timestamp=datetime.now(timezone.utc),
            success=False,
            response_time_ms=None,
            response_text=None,
            error="Timeout",
        )
        LOG.warning(f"Health check for {callsign}: TIMEOUT")

    store.add_result(callsign, result)
    store.save()


def calculate_stagger_interval(num_services: int) -> int | None:
    """Calculate interval between health checks to spread them over an hour.

    Args:
        num_services: Number of services to check

    Returns:
        Interval in seconds, or None if no services to check
    """
    if num_services <= 0:
        return None
    return SECONDS_PER_HOUR // num_services


def get_checkable_services() -> list[str]:
    """Get list of service callsigns that should be health checked.

    Returns services that:
    - Have a health_check_command set
    - Are not deleted (status != "deleted")
    """
    from aprs_service_registry.main import APRSServices

    services = APRSServices()
    checkable = []

    for callsign in services:
        service = services[callsign]
        try:
            service_dict = service.model_dump()
        except AttributeError:
            service_dict = service.dict()

        status = service_dict.get("status", "active")
        health_check_command = service_dict.get("health_check_command")

        if status != "deleted" and health_check_command:
            checkable.append(callsign)

    return checkable


# Global scheduler instance
_scheduler: AsyncIOScheduler | None = None


def setup_scheduler() -> AsyncIOScheduler | None:
    """Set up the health check scheduler.

    Returns:
        The scheduler instance, or None if health checks are disabled.
    """
    global _scheduler

    if not CONF.registry.health_check_enabled:
        LOG.info("Health checks disabled in config")
        return None

    checkable = get_checkable_services()
    if not checkable:
        LOG.info("No checkable services found, skipping scheduler setup")
        return None

    interval = calculate_stagger_interval(len(checkable))
    LOG.info(
        f"Setting up health check scheduler: {len(checkable)} services, "
        f"{interval}s interval"
    )

    _scheduler = AsyncIOScheduler()

    # Schedule each service with a staggered start time
    for i, callsign in enumerate(checkable):
        # Initial delay to stagger the first run
        initial_delay = i * interval

        _scheduler.add_job(
            check_service,
            "interval",
            seconds=SECONDS_PER_HOUR,  # Run hourly
            args=[callsign],
            id=f"health_check_{callsign}",
            name=f"Health check for {callsign}",
            next_run_time=datetime.now() + timedelta(seconds=initial_delay),
        )
        LOG.debug(
            f"Scheduled health check for {callsign} (initial delay: {initial_delay}s)"
        )

    return _scheduler


def start_scheduler() -> None:
    """Start the health check scheduler."""
    global _scheduler
    if _scheduler:
        _scheduler.start()
        LOG.info("Health check scheduler started")


def stop_scheduler() -> None:
    """Stop the health check scheduler."""
    global _scheduler
    if _scheduler:
        _scheduler.shutdown()
        _scheduler = None
        LOG.info("Health check scheduler stopped")
