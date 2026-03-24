"""Health check functionality for APRS services."""

import logging
import threading
import time
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

# Global flag to track if APRSD client is initialized
_aprsd_initialized = False
_aprsd_init_lock = threading.Lock()


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
        """Add a health check result for a service, keeping only last 3.

        Note: This method does NOT persist to disk. Use add_and_persist_result
        if you need automatic persistence.
        """
        callsign_upper = callsign.upper()
        if callsign_upper not in self.data:
            self.data[callsign_upper] = []

        # Prepend new result (most recent first)
        self.data[callsign_upper].insert(0, result)

        # Keep only last 3
        self.data[callsign_upper] = self.data[callsign_upper][:MAX_RESULTS_PER_SERVICE]

    @wrapt.synchronized(lock)
    def add_and_persist_result(self, callsign: str, result: HealthCheckResult):
        """Add a health check result and persist to disk.

        This is the preferred method for recording health check results
        as it ensures data is saved immediately.
        """
        callsign_upper = callsign.upper()
        if callsign_upper not in self.data:
            self.data[callsign_upper] = []

        # Prepend new result (most recent first)
        self.data[callsign_upper].insert(0, result)

        # Keep only last 3
        self.data[callsign_upper] = self.data[callsign_upper][:MAX_RESULTS_PER_SERVICE]

        # Persist to disk (use unlocked version since we hold the lock)
        self._save_unlocked()

    @wrapt.synchronized(lock)
    def get_results(self, callsign: str) -> list[HealthCheckResult]:
        """Get all health check results for a service."""
        return self.data.get(callsign.upper(), [])

    def get_last_result(self, callsign: str) -> HealthCheckResult | None:
        """Get the most recent health check result for a service."""
        results = self.get_results(callsign)
        return results[0] if results else None


def _initialize_aprsd() -> bool:
    """Initialize APRSD client with configuration.

    Returns:
        True if initialization successful, False otherwise.
    """
    global _aprsd_initialized

    with _aprsd_init_lock:
        if _aprsd_initialized:
            return True

        try:
            # Import APRSD modules
            from aprsd import conf as aprsd_conf
            from aprsd.client.client import APRSDClient

            # Load APRSD config from the path specified in our config
            aprsd_config_path = CONF.registry.aprsd_config_path
            LOG.info(f"Loading APRSD config from {aprsd_config_path}")

            # CRITICAL: Preserve our registry config values before re-parsing
            # because cfg.CONF() will reset all values to defaults from the
            # new config file, losing our registry settings like save_location
            preserved_registry_config = {
                "enable_save": CONF.registry.enable_save,
                "save_location": CONF.registry.save_location,
                "trace_enabled": CONF.registry.trace_enabled,
                "web_ip": str(CONF.registry.web_ip),
                "web_port": CONF.registry.web_port,
                "log_level": CONF.registry.log_level,
                "aprsd_config_path": CONF.registry.aprsd_config_path,
                "health_check_enabled": CONF.registry.health_check_enabled,
                "health_check_timeout": CONF.registry.health_check_timeout,
            }
            LOG.debug(f"Preserved registry config: {preserved_registry_config}")

            # Register APRSD's oslo.config options
            # These are needed for the APRS-IS client to work
            aprsd_conf.common.register_opts(cfg.CONF)
            aprsd_conf.client.register_opts(cfg.CONF)

            # Re-parse config to pick up APRSD options from aprsd.conf
            cfg.CONF(
                args=[],
                default_config_files=[aprsd_config_path],
            )

            # CRITICAL: Restore our registry config values after APRSD config
            # parsing overwrote them with defaults
            cfg.CONF.set_override(
                "enable_save",
                preserved_registry_config["enable_save"],
                group="registry",
            )
            cfg.CONF.set_override(
                "save_location",
                preserved_registry_config["save_location"],
                group="registry",
            )
            cfg.CONF.set_override(
                "trace_enabled",
                preserved_registry_config["trace_enabled"],
                group="registry",
            )
            cfg.CONF.set_override(
                "web_ip", preserved_registry_config["web_ip"], group="registry"
            )
            cfg.CONF.set_override(
                "web_port", preserved_registry_config["web_port"], group="registry"
            )
            cfg.CONF.set_override(
                "log_level", preserved_registry_config["log_level"], group="registry"
            )
            cfg.CONF.set_override(
                "aprsd_config_path",
                preserved_registry_config["aprsd_config_path"],
                group="registry",
            )
            cfg.CONF.set_override(
                "health_check_enabled",
                preserved_registry_config["health_check_enabled"],
                group="registry",
            )
            cfg.CONF.set_override(
                "health_check_timeout",
                preserved_registry_config["health_check_timeout"],
                group="registry",
            )
            LOG.debug(
                f"Restored registry config, save_location={CONF.registry.save_location}"
            )

            # Initialize the APRSD client (singleton)
            client = APRSDClient()
            if client.connected:
                LOG.info("APRSD client connected to APRS-IS")
                _aprsd_initialized = True
                return True
            else:
                LOG.error("APRSD client failed to connect to APRS-IS")
                return False

        except FileNotFoundError:
            LOG.error(f"APRSD config file not found: {aprsd_config_path}")
            return False
        except ImportError as e:
            LOG.error(f"Failed to import APRSD modules: {e}")
            return False
        except Exception as e:
            LOG.error(f"Failed to initialize APRSD client: {e}")
            return False


def send_and_wait_for_response(
    callsign: str,
    message: str,
    timeout: int,
) -> tuple[str | None, int | None]:
    """Send APRS message and wait for response.

    Args:
        callsign: Target callsign to send message to
        message: Message text to send (e.g., "ping", "help")
        timeout: Seconds to wait for response

    Returns:
        Tuple of (response_text, response_time_ms) or (None, None) on timeout.
    """
    # Check if health checks are enabled
    if not CONF.registry.health_check_enabled:
        LOG.debug("Health checks disabled, skipping APRS message")
        return (None, None)

    # Initialize APRSD if needed
    if not _initialize_aprsd():
        LOG.error("Cannot send health check: APRSD not initialized")
        return (None, None)

    try:
        from aprsd.client.client import APRSDClient
        from aprsd.packets import MessagePacket
        from aprsd.packets import collector as packet_collector
        from aprsd.threads import tx

        # Get our callsign from APRSD config
        from_call = cfg.CONF.callsign
        if not from_call:
            LOG.error("No callsign configured in APRSD config")
            return (None, None)

        # Result container for thread communication
        result = {"response_text": None, "response_time_ms": None}
        stop_event = threading.Event()
        start_time = time.time()

        def rx_callback(packet):
            """Callback for received packets."""
            nonlocal result

            # Register with packet collector
            packet_collector.PacketCollector().rx(packet)

            # Check if this is a message from our target
            if hasattr(packet, "from_call") and hasattr(packet, "message_text"):
                if packet.from_call.upper() == callsign.upper():
                    elapsed_ms = int((time.time() - start_time) * 1000)
                    result["response_text"] = packet.message_text
                    result["response_time_ms"] = elapsed_ms
                    LOG.debug(
                        f"Received response from {callsign}: "
                        f"'{packet.message_text}' in {elapsed_ms}ms",
                    )

                    # Send ACK for the received message
                    try:
                        from aprsd.packets import AckPacket

                        if hasattr(packet, "msgNo") and packet.msgNo:
                            tx.send(
                                AckPacket(
                                    from_call=from_call,
                                    to_call=packet.from_call,
                                    msgNo=packet.msgNo,
                                ),
                                direct=True,
                            )
                    except Exception as e:
                        LOG.warning(f"Failed to send ACK: {e}")

                    stop_event.set()
                    raise StopIteration

        # Create and send the message
        LOG.info(f"Sending health check to {callsign}: '{message}'")
        packet = MessagePacket(
            from_call=from_call,
            to_call=callsign.upper(),
            message_text=message,
        )
        tx.send(packet, direct=True)

        # Start consumer in a thread to receive response
        client = APRSDClient()

        def consume():
            try:
                client.consumer(rx_callback, raw=False)
            except StopIteration:
                pass
            except Exception as e:
                LOG.debug(f"Consumer stopped: {e}")

        consumer_thread = threading.Thread(target=consume, daemon=True)
        consumer_thread.start()

        # Wait for response or timeout
        stop_event.wait(timeout=timeout)

        if result["response_text"] is not None:
            return (result["response_text"], result["response_time_ms"])
        else:
            LOG.warning(f"Health check timeout for {callsign} after {timeout}s")
            return (None, None)

    except ImportError as e:
        LOG.error(f"APRSD import error: {e}")
        return (None, None)
    except Exception as e:
        LOG.error(f"Error sending health check to {callsign}: {e}")
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
    service_dict = _service_to_dict(service)

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

    store.add_and_persist_result(callsign, result)


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


def _service_to_dict(service) -> dict:
    """Convert a service model to a dictionary.

    Handles both Pydantic v1 (.dict()) and v2 (.model_dump()) APIs.
    """
    try:
        return service.model_dump()
    except AttributeError:
        return service.dict()


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
        service_dict = _service_to_dict(service)

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
        f"{interval}s interval",
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
            next_run_time=datetime.now(timezone.utc) + timedelta(seconds=initial_delay),
        )
        LOG.debug(
            f"Scheduled health check for {callsign} (initial delay: {initial_delay}s)",
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
