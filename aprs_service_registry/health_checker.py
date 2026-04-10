"""Health check functionality for APRS services."""

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import wrapt
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger
from oslo_config import cfg

from aprs_service_registry import gitstore, objectstore


LOG = logger
CONF = cfg.CONF

MAX_RESULTS_PER_SERVICE = 24
SECONDS_PER_HOUR = 3600
CONSECUTIVE_FAILURES_FOR_DOWN = 3  # Consecutive failures before marking as down


def calculate_uptime(results: list) -> str:
    """Calculate uptime percentage from health check results.

    Args:
        results: List of health check result dicts/objects with 'success' key/attr

    Returns:
        Uptime string like "96%" or "--" if no data
    """
    if not results:
        return "--"
    # Handle both dict and object results
    passed = 0
    for r in results:
        if isinstance(r, dict):
            success = r.get("success", False)
        else:
            success = getattr(r, "success", False)
        if success:
            passed += 1
    percentage = (passed / len(results)) * 100
    return f"{percentage:.0f}%"


# Global flag to track if APRSD client is initialized
_aprsd_initialized = False
_aprsd_init_lock = threading.Lock()

# Global persistent consumer thread
_consumer_thread: threading.Thread | None = None
_consumer_running = False
_consumer_lock = threading.Lock()

# Global tracker for received ACKs/responses from services
# Key: callsign (uppercase), Value: {"timestamp": time.time(), "response": "ACK" or message}
_received_responses: dict[str, dict] = {}
_received_responses_lock = threading.Lock()


def record_response(callsign: str, response_text: str) -> None:
    """Record a response (ACK or message) from a service."""
    with _received_responses_lock:
        _received_responses[callsign.upper()] = {
            "timestamp": time.time(),
            "response": response_text,
        }
        LOG.debug(f"Recorded response from {callsign}: {response_text}")


def get_recent_response(callsign: str, since_timestamp: float) -> dict | None:
    """Get a response from a service if received after the given timestamp.

    Args:
        callsign: The service callsign
        since_timestamp: Only return responses received after this time

    Returns:
        Response dict with "timestamp" and "response" keys, or None
    """
    with _received_responses_lock:
        resp = _received_responses.get(callsign.upper())
        if resp and resp["timestamp"] >= since_timestamp:
            return resp
        return None


def clear_old_responses(max_age_seconds: int = 300) -> None:
    """Clear responses older than max_age_seconds."""
    cutoff = time.time() - max_age_seconds
    with _received_responses_lock:
        to_delete = [
            k for k, v in _received_responses.items() if v["timestamp"] < cutoff
        ]
        for k in to_delete:
            del _received_responses[k]


def _global_rx_callback(packet):
    """Global callback for ALL received packets.

    This runs in the persistent consumer thread and records all incoming
    ACKs and messages to the global tracker.
    """
    # Extract fields - handle both dict (from aprslib) and object formats
    if isinstance(packet, dict):
        pkt_from = packet.get("from", "")
        pkt_message = packet.get("message_text", "")
        pkt_response = packet.get("response", "")  # 'ack' or 'rej'
    else:
        pkt_from = getattr(packet, "from_call", "")
        pkt_message = getattr(packet, "message_text", "")
        pkt_response = getattr(packet, "response", "")

    # Record ACKs
    if pkt_response == "ack":
        record_response(pkt_from, "ACK")

    # Record message responses
    elif pkt_message:
        record_response(pkt_from, pkt_message)


def _run_persistent_consumer():
    """Run the persistent consumer loop.

    This function runs in a dedicated thread and continuously reads
    from APRS-IS, calling _global_rx_callback for each packet.
    """
    global _consumer_running

    try:
        from aprsd.client.client import APRSDClient

        client = APRSDClient()
        LOG.info("Starting persistent APRS-IS consumer thread")

        while _consumer_running:
            try:
                # consumer() blocks and calls callback for each packet
                # We use a short internal timeout to check _consumer_running periodically
                client.consumer(_global_rx_callback, raw=False)
            except StopIteration:
                # Normal exit from consumer
                pass
            except Exception as e:
                if _consumer_running:
                    LOG.warning(f"Consumer error (will retry): {e}")
                    time.sleep(1)  # Brief pause before retry

    except Exception as e:
        LOG.error(f"Persistent consumer thread failed: {e}")
    finally:
        LOG.info("Persistent APRS-IS consumer thread stopped")


def start_persistent_consumer():
    """Start the persistent consumer thread if not already running."""
    global _consumer_thread, _consumer_running

    with _consumer_lock:
        if _consumer_thread is not None and _consumer_thread.is_alive():
            LOG.debug("Persistent consumer already running")
            return True

        if not _initialize_aprsd():
            LOG.error("Cannot start consumer: APRSD not initialized")
            return False

        _consumer_running = True
        _consumer_thread = threading.Thread(
            target=_run_persistent_consumer,
            name="aprs-consumer",
            daemon=True,
        )
        _consumer_thread.start()
        LOG.info("Started persistent APRS-IS consumer")
        return True


def stop_persistent_consumer():
    """Stop the persistent consumer thread."""
    global _consumer_thread, _consumer_running

    with _consumer_lock:
        if _consumer_thread is None:
            return

        LOG.info("Stopping persistent APRS-IS consumer...")
        _consumer_running = False

        # Give it a moment to stop
        if _consumer_thread.is_alive():
            _consumer_thread.join(timeout=5)

        _consumer_thread = None
        LOG.info("Persistent APRS-IS consumer stopped")


@dataclass
class HealthCheckResult:
    """Result of a single health check for a service."""

    timestamp: datetime
    success: bool
    response_time_ms: int | None  # None if timeout
    response_text: str | None  # First 100 chars of response
    error: str | None  # Error message if failed


class HealthCheckStore(objectstore.ObjectStoreMixin, gitstore.GitStoreMixin):
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

    def _git_filename(self) -> str:
        return "healthchecks.json"

    def _serialize_for_json(self, obj):
        """Convert health check results to JSON-serializable format."""
        if isinstance(obj, HealthCheckResult):
            return {
                "timestamp": obj.timestamp.isoformat() if obj.timestamp else None,
                "success": obj.success,
                "response_time_ms": obj.response_time_ms,
                "response_text": obj.response_text,
                "error": obj.error,
            }
        elif isinstance(obj, datetime):
            return obj.isoformat()
        elif hasattr(obj, "__dict__"):
            return obj.__dict__
        return str(obj)

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
                "admin_username": CONF.registry.admin_username,
                "admin_password": CONF.registry.admin_password,
                "git_backup_enabled": CONF.registry.git_backup_enabled,
                "git_backup_path": CONF.registry.git_backup_path,
                "git_backup_remote": CONF.registry.git_backup_remote,
                "git_backup_push_interval": CONF.registry.git_backup_push_interval,
                "bulletin_enabled": CONF.registry.bulletin_enabled,
                "bulletin_interval": CONF.registry.bulletin_interval,
                "bulletin_messages": CONF.registry.bulletin_messages,
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
            cfg.CONF.set_override(
                "admin_username",
                preserved_registry_config["admin_username"],
                group="registry",
            )
            cfg.CONF.set_override(
                "admin_password",
                preserved_registry_config["admin_password"],
                group="registry",
            )
            cfg.CONF.set_override(
                "git_backup_enabled",
                preserved_registry_config["git_backup_enabled"],
                group="registry",
            )
            cfg.CONF.set_override(
                "git_backup_path",
                preserved_registry_config["git_backup_path"],
                group="registry",
            )
            cfg.CONF.set_override(
                "git_backup_remote",
                preserved_registry_config["git_backup_remote"],
                group="registry",
            )
            cfg.CONF.set_override(
                "git_backup_push_interval",
                preserved_registry_config["git_backup_push_interval"],
                group="registry",
            )
            cfg.CONF.set_override(
                "bulletin_enabled",
                preserved_registry_config["bulletin_enabled"],
                group="registry",
            )
            cfg.CONF.set_override(
                "bulletin_interval",
                preserved_registry_config["bulletin_interval"],
                group="registry",
            )
            cfg.CONF.set_override(
                "bulletin_messages",
                preserved_registry_config["bulletin_messages"],
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

    Uses a persistent consumer thread to receive packets in real-time.
    Polls the global response tracker for incoming ACKs/messages.

    Args:
        callsign: Target callsign to send message to
        message: Message text to send (e.g., "ping", "help")
        timeout: Seconds to wait for response (not currently used, we use retry_interval)

    Returns:
        Tuple of (response_text, response_time_ms) or (None, None) on timeout.
    """
    # Check if health checks are enabled
    if not CONF.registry.health_check_enabled:
        LOG.debug("Health checks disabled, skipping APRS message")
        return (None, None)

    # Initialize APRSD and start persistent consumer if needed
    if not _initialize_aprsd():
        LOG.error("Cannot send health check: APRSD not initialized")
        return (None, None)

    # Ensure persistent consumer is running
    if not start_persistent_consumer():
        LOG.error("Cannot send health check: persistent consumer not running")
        return (None, None)

    try:
        from aprsd.packets import MessagePacket
        from aprsd.threads import tx

        # Get our callsign from APRSD config
        from_call = cfg.CONF.callsign
        if not from_call:
            LOG.error("No callsign configured in APRSD config")
            return (None, None)

        start_time = time.time()
        callsign_upper = callsign.upper()

        # Clear any old response from this callsign before we start
        with _received_responses_lock:
            if callsign_upper in _received_responses:
                del _received_responses[callsign_upper]

        # Send message with retry logic - retry every 30s if no response
        max_retries = 3
        retry_interval = 30  # seconds
        poll_interval = 0.5  # Check for response every 500ms

        for attempt in range(1, max_retries + 1):
            # Create and send the message
            packet = MessagePacket(
                from_call=from_call,
                to_call=callsign_upper,
                message_text=message,
            )
            LOG.info(
                f"Sending health check to {callsign}: '{message}' (attempt {attempt}/{max_retries})"
            )
            tx.send(packet, direct=True)

            # Poll for response
            attempt_start = time.time()
            while time.time() - attempt_start < retry_interval:
                # Check if we got a response
                response = get_recent_response(callsign_upper, start_time)
                if response:
                    elapsed_ms = int((response["timestamp"] - start_time) * 1000)
                    LOG.debug(
                        f"Received response from {callsign}: '{response['response']}' in {elapsed_ms}ms"
                    )
                    return (response["response"], elapsed_ms)

                # Wait before polling again
                time.sleep(poll_interval)

            if attempt < max_retries:
                LOG.debug(
                    f"No response from {callsign} after {retry_interval}s, retrying..."
                )

        LOG.warning(f"Health check timeout for {callsign} after {max_retries} attempts")
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

    Status transitions:
    - On SUCCESS: pending/down -> active
    - On FAILURE: active -> pending
    - On FAILURE (3 consecutive): pending -> down
    """
    from aprs_service_registry.main import APRSServices, registryRequest

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
    current_status = service_dict.get("status", "active")
    if current_status == "deleted":
        LOG.debug(f"Skipping health check for deleted service {callsign}")
        return

    # Skip services without health_check_command
    # Get health check command, defaulting to 'ping'
    health_check_command = service_dict.get("health_check_command") or "ping"

    LOG.info(f"Running health check for {callsign}: sending '{health_check_command}'")

    # Record start time for checking late responses
    check_start_time = time.time()

    # Send message and wait for response
    timeout = CONF.registry.health_check_timeout
    response_text, response_time_ms = send_and_wait_for_response(
        callsign,
        health_check_command,
        timeout,
    )

    # If we didn't get a direct response, check the global tracker for late ACKs
    if response_text is None:
        late_response = get_recent_response(callsign, check_start_time)
        if late_response:
            response_text = late_response["response"]
            # Calculate approximate response time (from start of check to when recorded)
            response_time_ms = int(
                (late_response["timestamp"] - check_start_time) * 1000
            )
            LOG.info(
                f"Found late response from {callsign}: '{response_text}' "
                f"(arrived after {response_time_ms}ms)"
            )

    # Determine new status based on result
    new_status = current_status  # Default: no change

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

        # SUCCESS: If pending or down, transition to active
        if current_status in ("pending", "down"):
            new_status = "active"
            LOG.info(
                f"Service {callsign}: {current_status} -> active (health check passed)"
            )
    else:
        result = HealthCheckResult(
            timestamp=datetime.now(timezone.utc),
            success=False,
            response_time_ms=None,
            response_text=None,
            error="Timeout",
        )
        LOG.warning(f"Health check for {callsign}: TIMEOUT")

        # FAILURE: Determine status transition
        if current_status == "active":
            # active -> pending on first failure
            new_status = "pending"
            LOG.warning(f"Service {callsign}: active -> pending (health check failed)")
        elif current_status == "pending":
            # Check if we've had 3+ consecutive failures (including this one)
            # Look at historical results to count consecutive failures
            results = store.get_results(callsign.upper())
            consecutive_failures = 1  # Count current failure
            if results:
                for r in results:  # Most recent first
                    if r.success:
                        break  # Stop counting on first success
                    consecutive_failures += 1

            if consecutive_failures >= CONSECUTIVE_FAILURES_FOR_DOWN:
                new_status = "down"
                LOG.warning(
                    f"Service {callsign}: pending -> down "
                    f"({consecutive_failures} consecutive failures)"
                )

    store.add_and_persist_result(callsign, result)

    # Update service status if changed
    if new_status != current_status:
        service_dict["status"] = new_status
        updated_service = registryRequest(**service_dict)
        services.add_and_persist(callsign.upper(), updated_service)


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

    Returns services that are not deleted (status != "deleted").
    All active/down services are checked using their health_check_command
    or 'ping' as the default.
    """
    from aprs_service_registry.main import APRSServices

    services = APRSServices()
    checkable = []

    for callsign in services:
        service = services[callsign]
        service_dict = _service_to_dict(service)

        status = service_dict.get("status", "active")

        if status != "deleted":
            checkable.append(callsign)

    return checkable


def send_bulletins() -> None:
    """Send APRS bulletin packets to announce the service registry."""
    if not CONF.registry.bulletin_enabled:
        return

    if not _aprsd_initialized:
        if not _initialize_aprsd():
            LOG.error("Cannot send bulletins: APRSD not initialized")
            return

    if not start_persistent_consumer():
        LOG.error("Cannot send bulletins: persistent consumer not running")
        return

    try:
        from aprsd.packets import BulletinPacket
        from aprsd.threads import tx

        from aprs_service_registry.main import APRSServices

        from_call = cfg.CONF.callsign
        if not from_call:
            LOG.error("No callsign configured for bulletin send")
            return

        # Get service count for {count} placeholder
        services = APRSServices()
        service_count = len(services)

        messages = CONF.registry.bulletin_messages
        for i, message_template in enumerate(messages):
            bid = str(i + 1)

            # Substitute placeholders
            message_text = message_template.format(count=service_count)

            # APRS bulletin messages are max 67 chars
            if len(message_text) > 67:
                LOG.warning(
                    f"Bulletin BLN{bid} text exceeds 67 chars, truncating: {message_text}"
                )
                message_text = message_text[:67]

            packet = BulletinPacket(
                from_call=from_call,
                to_call=f"BLN{bid}",
                bid=bid,
                message_text=message_text,
            )
            # Fix APRSD bug: BulletinPacket._build_payload() incorrectly pads
            # the bid to 9 chars instead of padding the whole addressee (BLN+bid)
            # to 9 chars. Correct format: :BLN1     :message (9-char addressee)
            # APRSD generates: :BLN1        :message (12-char addressee)
            addressee = f"BLN{bid}"
            packet.payload = f":{addressee:<9}:{message_text}"
            tx.send(packet, direct=True)
            LOG.info(f"Sent bulletin BLN{bid}: {message_text}")

    except Exception as e:
        LOG.error(f"Failed to send bulletins: {e}")


def send_beacon() -> bool:
    """Send an APRS position beacon for the registry station.

    Returns:
        True if beacon was sent successfully, False otherwise.
    """
    if not _aprsd_initialized:
        if not _initialize_aprsd():
            LOG.error("Cannot send beacon: APRSD not initialized")
            return False

    if not start_persistent_consumer():
        LOG.error("Cannot send beacon: persistent consumer not running")
        return False

    try:
        from aprsd.packets import BeaconPacket
        from aprsd.threads import tx

        from_call = cfg.CONF.callsign
        if not from_call:
            LOG.error("No callsign configured for beacon send")
            return False

        # Get location from APRSD config
        lat = cfg.CONF.latitude
        lon = cfg.CONF.longitude

        if lat == 0.0 and lon == 0.0:
            LOG.error("No latitude/longitude configured for beacon")
            return False

        beacon = BeaconPacket(
            from_call=from_call,
            latitude=lat,
            longitude=lon,
            symbol="r",  # Repeater symbol
            symbol_table="/",
            comment="APRS Service Registry - aprs.hemna.com",
        )
        tx.send(beacon, direct=True)
        LOG.info(f"Sent position beacon: {beacon.raw}")
        return True

    except Exception as e:
        LOG.error(f"Failed to send beacon: {e}")
        return False


# Global scheduler instance
_scheduler: AsyncIOScheduler | None = None


def setup_scheduler() -> AsyncIOScheduler | None:
    """Set up the health check and bulletin scheduler.

    Returns:
        The scheduler instance, or None if nothing to schedule.
    """
    global _scheduler

    has_health_checks = CONF.registry.health_check_enabled
    has_bulletins = CONF.registry.bulletin_enabled

    if not has_health_checks and not has_bulletins:
        LOG.info("Health checks and bulletins both disabled")
        return None

    _scheduler = AsyncIOScheduler()

    # Schedule health checks if enabled
    if has_health_checks:
        checkable = get_checkable_services()
        if checkable:
            interval = calculate_stagger_interval(len(checkable))
            LOG.info(
                f"Setting up health check scheduler: {len(checkable)} services, "
                f"{interval}s interval",
            )
            for i, callsign in enumerate(checkable):
                initial_delay = i * interval
                _scheduler.add_job(
                    check_service,
                    "interval",
                    seconds=SECONDS_PER_HOUR,
                    args=[callsign],
                    id=f"health_check_{callsign}",
                    name=f"Health check for {callsign}",
                    next_run_time=datetime.now(timezone.utc)
                    + timedelta(seconds=initial_delay),
                )
                LOG.debug(
                    f"Scheduled health check for {callsign} "
                    f"(initial delay: {initial_delay}s)",
                )
        else:
            LOG.info("No checkable services found for health checks")

    # Schedule bulletin announcements if enabled
    if has_bulletins:
        bulletin_interval = CONF.registry.bulletin_interval
        _scheduler.add_job(
            send_bulletins,
            "interval",
            seconds=bulletin_interval,
            id="bulletin_sender",
            name="APRS bulletin announcements",
            next_run_time=datetime.now(timezone.utc) + timedelta(seconds=30),
        )
        LOG.info(f"Scheduled bulletin announcements every {bulletin_interval}s")

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
