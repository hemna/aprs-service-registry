# Service Health Check Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add automated health checking for registered APRS services with staggered scheduling, result storage, and website display.

**Architecture:** Add `health_check_command` field to service model. Create a separate `health_checker.py` module with APRSD integration. Use APScheduler for staggered background checks. Store results in pickle file and display on website.

**Tech Stack:** FastAPI, APScheduler, APRSD, Pydantic, Jinja2

**Spec:** `docs/superpowers/specs/2026-03-24-health-check-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `pyproject.toml` | Modify | Add aprsd, apscheduler dependencies |
| `aprs_service_registry/conf/common.py` | Modify | Add health check config options |
| `aprs_service_registry/main.py` | Modify | Add health_check_command to model, update lifespan |
| `aprs_service_registry/health_checker.py` | Create | Health check logic, APRSD client, result storage |
| `aprs_service_registry/web/templates/index.html` | Modify | Add "Last Check" column |
| `aprs_service_registry/web/static/main.css` | Modify | Add health check styles |
| `tests/test_api.py` | Modify | Add tests for health_check_command field |
| `tests/test_health_checker.py` | Create | Unit tests for health checker module |

---

## Chunk 1: Data Model and Configuration

### Task 1: Add health check configuration options

**Files:**
- Modify: `aprs_service_registry/conf/common.py:24-56`

- [ ] **Step 1: Add health check config options**

In `aprs_service_registry/conf/common.py`, add new options to `registry_opts` list (after line 55):

```python
    cfg.StrOpt(
        "aprsd_config_path",
        default="/config/aprsd.conf",
        help="Path to APRSD configuration file for health checks.",
    ),
    cfg.BoolOpt(
        "health_check_enabled",
        default=False,
        help="Enable background health checks for services.",
    ),
    cfg.IntOpt(
        "health_check_timeout",
        default=60,
        help="Seconds to wait for health check response.",
    ),
```

- [ ] **Step 2: Verify config loads**

Run: `python -c "from aprs_service_registry import conf; from oslo_config import cfg; print(cfg.CONF.registry.health_check_enabled)"`

Expected: `False` (default value)

- [ ] **Step 3: Commit**

```bash
git add aprs_service_registry/conf/common.py
git commit -m "feat: add health check configuration options"
```

---

### Task 2: Add health_check_command field to model

**Files:**
- Modify: `aprs_service_registry/main.py:30-38`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write failing test for health_check_command field**

Add to `tests/test_api.py`:

```python
class TestHealthCheckCommand:
    """Tests for health_check_command field."""

    def setup_method(self):
        """Clear services before each test."""
        services = APRSServices()
        services.data = {}

    def test_register_service_without_health_check_command(self):
        """Services default to no health_check_command."""
        response = client.post(
            "/api/v1/registry",
            json={
                "callsign": "TEST",
                "description": "Test Service",
                "service_website": "https://test.com",
                "software": "test 1.0",
            },
        )
        assert response.status_code == 200

        get_response = client.get("/api/v1/registry/TEST")
        assert get_response.status_code == 200
        assert get_response.json()["health_check_command"] is None

    def test_register_service_with_health_check_command(self):
        """Can register a service with health_check_command."""
        response = client.post(
            "/api/v1/registry",
            json={
                "callsign": "TEST",
                "description": "Test Service",
                "service_website": "https://test.com",
                "software": "test 1.0",
                "health_check_command": "ping",
            },
        )
        assert response.status_code == 200

        get_response = client.get("/api/v1/registry/TEST")
        assert get_response.json()["health_check_command"] == "ping"

    def test_health_check_command_in_list_response(self):
        """health_check_command appears in list API response."""
        services = APRSServices()
        services.add(
            "TEST",
            registryRequest(
                callsign="TEST",
                description="Test",
                service_website="https://test.com",
                software="test",
                health_check_command="help",
            ),
        )

        response = client.get("/api/v1/registry")
        assert response.status_code == 200
        service = response.json()["services"][0]
        assert service["health_check_command"] == "help"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_api.py::TestHealthCheckCommand -v`

Expected: FAIL (health_check_command field doesn't exist)

- [ ] **Step 3: Add health_check_command field to model**

In `aprs_service_registry/main.py`, update the `registryRequest` class:

```python
class registryRequest(BaseModel):
    """Request to register a service with the registry."""

    callsign: str
    description: str
    service_website: str
    software: str
    callsign_owner: str | None = None
    status: Literal["active", "down", "deleted"] = "active"
    health_check_command: str | None = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_api.py::TestHealthCheckCommand -v`

Expected: PASS (all 3 tests)

- [ ] **Step 5: Run full test suite**

Run: `python -m pytest tests/test_api.py -v`

Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add aprs_service_registry/main.py tests/test_api.py
git commit -m "feat: add health_check_command field to service model"
```

---

### Task 3: Add dependencies to pyproject.toml

**Files:**
- Modify: `pyproject.toml:25-35`

- [ ] **Step 1: Add aprsd and apscheduler dependencies**

In `pyproject.toml`, update the `dependencies` list:

```toml
dependencies = [
    "click",
    "fastapi",
    "fastapi-utils",
    "jinja2",
    "loguru",
    "oslo.config",
    "typing-inspect",
    "uvicorn",
    "wrapt",
    "aprsd",
    "apscheduler>=3.10.0",
]
```

- [ ] **Step 2: Install dependencies**

Run: `pip install -e .` or `uv pip install -e .`

Expected: Successfully installs aprsd and apscheduler

- [ ] **Step 3: Verify imports work**

Run: `python -c "from aprsd.client.client import APRSDClient; from apscheduler.schedulers.asyncio import AsyncIOScheduler; print('OK')"`

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "feat: add aprsd and apscheduler dependencies"
```

---

## Chunk 2: Health Checker Module

### Task 4: Create HealthCheckResult dataclass and storage

**Files:**
- Create: `aprs_service_registry/health_checker.py`
- Create: `tests/test_health_checker.py`

- [ ] **Step 1: Write failing test for HealthCheckResult**

Create `tests/test_health_checker.py`:

```python
"""Tests for health checker module."""

from datetime import datetime, timezone

import pytest


class TestHealthCheckResult:
    """Tests for HealthCheckResult dataclass."""

    def test_create_success_result(self):
        """Can create a successful health check result."""
        from aprs_service_registry.health_checker import HealthCheckResult

        result = HealthCheckResult(
            timestamp=datetime.now(timezone.utc),
            success=True,
            response_time_ms=1500,
            response_text="Pong!",
            error=None,
        )

        assert result.success is True
        assert result.response_time_ms == 1500
        assert result.response_text == "Pong!"
        assert result.error is None

    def test_create_failure_result(self):
        """Can create a failed health check result."""
        from aprs_service_registry.health_checker import HealthCheckResult

        result = HealthCheckResult(
            timestamp=datetime.now(timezone.utc),
            success=False,
            response_time_ms=None,
            response_text=None,
            error="Timeout",
        )

        assert result.success is False
        assert result.response_time_ms is None
        assert result.error == "Timeout"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_health_checker.py::TestHealthCheckResult -v`

Expected: FAIL (module doesn't exist)

- [ ] **Step 3: Create health_checker.py with HealthCheckResult**

Create `aprs_service_registry/health_checker.py`:

```python
"""Health check functionality for APRS services."""

import logging
from dataclasses import dataclass
from datetime import datetime


LOG = logging.getLogger(__name__)


@dataclass
class HealthCheckResult:
    """Result of a single health check for a service."""

    timestamp: datetime
    success: bool
    response_time_ms: int | None  # None if timeout
    response_text: str | None     # First 100 chars of response
    error: str | None             # Error message if failed
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_health_checker.py::TestHealthCheckResult -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add aprs_service_registry/health_checker.py tests/test_health_checker.py
git commit -m "feat: add HealthCheckResult dataclass"
```

---

### Task 5: Add health check results storage

**Files:**
- Modify: `aprs_service_registry/health_checker.py`
- Test: `tests/test_health_checker.py`

- [ ] **Step 1: Write failing test for HealthCheckStore**

Add to `tests/test_health_checker.py`:

```python
class TestHealthCheckStore:
    """Tests for HealthCheckStore."""

    def setup_method(self):
        """Clear store before each test."""
        from aprs_service_registry.health_checker import HealthCheckStore
        store = HealthCheckStore()
        store.data = {}

    def test_add_result(self):
        """Can add a health check result for a service."""
        from aprs_service_registry.health_checker import (
            HealthCheckResult,
            HealthCheckStore,
        )

        store = HealthCheckStore()
        result = HealthCheckResult(
            timestamp=datetime.now(timezone.utc),
            success=True,
            response_time_ms=1000,
            response_text="OK",
            error=None,
        )

        store.add_result("TESTCALL", result)

        results = store.get_results("TESTCALL")
        assert len(results) == 1
        assert results[0].success is True

    def test_keeps_only_last_3_results(self):
        """Store keeps only the last 3 results per service."""
        from aprs_service_registry.health_checker import (
            HealthCheckResult,
            HealthCheckStore,
        )

        store = HealthCheckStore()

        # Add 5 results
        for i in range(5):
            result = HealthCheckResult(
                timestamp=datetime.now(timezone.utc),
                success=True,
                response_time_ms=i * 100,
                response_text=f"Response {i}",
                error=None,
            )
            store.add_result("TESTCALL", result)

        results = store.get_results("TESTCALL")
        assert len(results) == 3
        # Most recent should be first
        assert results[0].response_text == "Response 4"
        assert results[2].response_text == "Response 2"

    def test_get_last_result(self):
        """Can get the most recent result for a service."""
        from aprs_service_registry.health_checker import (
            HealthCheckResult,
            HealthCheckStore,
        )

        store = HealthCheckStore()
        result = HealthCheckResult(
            timestamp=datetime.now(timezone.utc),
            success=True,
            response_time_ms=500,
            response_text="Latest",
            error=None,
        )
        store.add_result("TESTCALL", result)

        last = store.get_last_result("TESTCALL")
        assert last is not None
        assert last.response_text == "Latest"

    def test_get_last_result_none_if_no_results(self):
        """get_last_result returns None if no results exist."""
        from aprs_service_registry.health_checker import HealthCheckStore

        store = HealthCheckStore()
        assert store.get_last_result("NONEXISTENT") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_health_checker.py::TestHealthCheckStore -v`

Expected: FAIL (HealthCheckStore doesn't exist)

- [ ] **Step 3: Implement HealthCheckStore**

Update `aprs_service_registry/health_checker.py`:

```python
"""Health check functionality for APRS services."""

import logging
import threading
from dataclasses import dataclass
from datetime import datetime

import wrapt
from oslo_config import cfg

from aprs_service_registry import objectstore


LOG = logging.getLogger(__name__)
CONF = cfg.CONF

MAX_RESULTS_PER_SERVICE = 3


@dataclass
class HealthCheckResult:
    """Result of a single health check for a service."""

    timestamp: datetime
    success: bool
    response_time_ms: int | None  # None if timeout
    response_text: str | None     # First 100 chars of response
    error: str | None             # Error message if failed


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_health_checker.py::TestHealthCheckStore -v`

Expected: PASS (all 4 tests)

- [ ] **Step 5: Commit**

```bash
git add aprs_service_registry/health_checker.py tests/test_health_checker.py
git commit -m "feat: add HealthCheckStore for result storage"
```

---

### Task 6: Add health check execution function

**Files:**
- Modify: `aprs_service_registry/health_checker.py`
- Test: `tests/test_health_checker.py`

- [ ] **Step 1: Write test for check_service function**

Add to `tests/test_health_checker.py`:

```python
from unittest.mock import MagicMock, patch


class TestCheckService:
    """Tests for check_service function."""

    def setup_method(self):
        """Clear stores before each test."""
        from aprs_service_registry.health_checker import HealthCheckStore
        from aprs_service_registry.main import APRSServices

        APRSServices().data = {}
        HealthCheckStore().data = {}

    @patch("aprs_service_registry.health_checker.send_and_wait_for_response")
    def test_check_service_success(self, mock_send):
        """Successful health check records success result."""
        from aprs_service_registry.health_checker import (
            HealthCheckStore,
            check_service,
        )
        from aprs_service_registry.main import APRSServices, registryRequest

        # Setup service
        services = APRSServices()
        services.add(
            "TESTCALL",
            registryRequest(
                callsign="TESTCALL",
                description="Test",
                service_website="https://test.com",
                software="test",
                health_check_command="ping",
            ),
        )

        # Mock APRSD response
        mock_send.return_value = ("Pong!", 1500)

        # Run check
        check_service("TESTCALL")

        # Verify result stored
        store = HealthCheckStore()
        result = store.get_last_result("TESTCALL")
        assert result is not None
        assert result.success is True
        assert result.response_time_ms == 1500
        assert result.response_text == "Pong!"

    @patch("aprs_service_registry.health_checker.send_and_wait_for_response")
    def test_check_service_timeout(self, mock_send):
        """Timeout records failure result."""
        from aprs_service_registry.health_checker import (
            HealthCheckStore,
            check_service,
        )
        from aprs_service_registry.main import APRSServices, registryRequest

        services = APRSServices()
        services.add(
            "TESTCALL",
            registryRequest(
                callsign="TESTCALL",
                description="Test",
                service_website="https://test.com",
                software="test",
                health_check_command="ping",
            ),
        )

        # Mock timeout
        mock_send.return_value = (None, None)

        check_service("TESTCALL")

        store = HealthCheckStore()
        result = store.get_last_result("TESTCALL")
        assert result is not None
        assert result.success is False
        assert result.error == "Timeout"

    def test_check_service_skips_deleted(self):
        """Deleted services are skipped."""
        from aprs_service_registry.health_checker import (
            HealthCheckStore,
            check_service,
        )
        from aprs_service_registry.main import APRSServices, registryRequest

        services = APRSServices()
        services.add(
            "DELETED",
            registryRequest(
                callsign="DELETED",
                description="Test",
                service_website="https://test.com",
                software="test",
                status="deleted",
                health_check_command="ping",
            ),
        )

        check_service("DELETED")

        # No result should be stored
        store = HealthCheckStore()
        assert store.get_last_result("DELETED") is None

    def test_check_service_skips_no_command(self):
        """Services without health_check_command are skipped."""
        from aprs_service_registry.health_checker import (
            HealthCheckStore,
            check_service,
        )
        from aprs_service_registry.main import APRSServices, registryRequest

        services = APRSServices()
        services.add(
            "NOCOMMAND",
            registryRequest(
                callsign="NOCOMMAND",
                description="Test",
                service_website="https://test.com",
                software="test",
                health_check_command=None,
            ),
        )

        check_service("NOCOMMAND")

        store = HealthCheckStore()
        assert store.get_last_result("NOCOMMAND") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_health_checker.py::TestCheckService -v`

Expected: FAIL (check_service doesn't exist)

- [ ] **Step 3: Implement check_service function**

Add to `aprs_service_registry/health_checker.py`:

```python
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
    LOG.warning(f"APRSD integration not yet implemented. Would send '{message}' to {callsign}")
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
    from datetime import timezone
    
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_health_checker.py::TestCheckService -v`

Expected: PASS (all 4 tests)

- [ ] **Step 5: Commit**

```bash
git add aprs_service_registry/health_checker.py tests/test_health_checker.py
git commit -m "feat: add check_service function for health checks"
```

---

## Chunk 3: Scheduler Integration

### Task 7: Add scheduler setup and staggered scheduling

**Files:**
- Modify: `aprs_service_registry/health_checker.py`
- Modify: `aprs_service_registry/main.py`
- Test: `tests/test_health_checker.py`

- [ ] **Step 1: Write test for schedule calculation**

Add to `tests/test_health_checker.py`:

```python
class TestScheduler:
    """Tests for health check scheduling."""

    def test_calculate_stagger_interval(self):
        """Stagger interval calculated correctly."""
        from aprs_service_registry.health_checker import calculate_stagger_interval

        # 10 services = 360 second interval (6 minutes)
        assert calculate_stagger_interval(10) == 360

        # 15 services = 240 second interval (4 minutes)
        assert calculate_stagger_interval(15) == 240

        # 1 service = 3600 seconds (full hour)
        assert calculate_stagger_interval(1) == 3600

    def test_calculate_stagger_interval_zero_services(self):
        """Returns None if no checkable services."""
        from aprs_service_registry.health_checker import calculate_stagger_interval

        assert calculate_stagger_interval(0) is None

    def test_get_checkable_services(self):
        """Only returns services with health_check_command and not deleted."""
        from aprs_service_registry.health_checker import get_checkable_services
        from aprs_service_registry.main import APRSServices, registryRequest

        services = APRSServices()
        services.data = {}

        # Checkable: has command, not deleted
        services.add(
            "CHECKABLE",
            registryRequest(
                callsign="CHECKABLE",
                description="Test",
                service_website="https://test.com",
                software="test",
                health_check_command="ping",
            ),
        )

        # Not checkable: no command
        services.add(
            "NOCOMMAND",
            registryRequest(
                callsign="NOCOMMAND",
                description="Test",
                service_website="https://test.com",
                software="test",
            ),
        )

        # Not checkable: deleted
        services.add(
            "DELETED",
            registryRequest(
                callsign="DELETED",
                description="Test",
                service_website="https://test.com",
                software="test",
                status="deleted",
                health_check_command="ping",
            ),
        )

        checkable = get_checkable_services()
        assert len(checkable) == 1
        assert checkable[0] == "CHECKABLE"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_health_checker.py::TestScheduler -v`

Expected: FAIL (functions don't exist)

- [ ] **Step 3: Implement scheduler helper functions**

Add to `aprs_service_registry/health_checker.py`:

```python
SECONDS_PER_HOUR = 3600


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_health_checker.py::TestScheduler -v`

Expected: PASS (all 3 tests)

- [ ] **Step 5: Commit**

```bash
git add aprs_service_registry/health_checker.py tests/test_health_checker.py
git commit -m "feat: add scheduler helper functions"
```

---

### Task 8: Integrate scheduler with FastAPI lifespan

**Files:**
- Modify: `aprs_service_registry/main.py`
- Modify: `aprs_service_registry/health_checker.py`

- [ ] **Step 1: Add setup_scheduler function to health_checker.py**

Add to `aprs_service_registry/health_checker.py`:

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler


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
        LOG.debug(f"Scheduled health check for {callsign} (initial delay: {initial_delay}s)")

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
```

Also add at top of file:

```python
from datetime import datetime, timedelta
```

- [ ] **Step 2: Update main.py to use lifespan context manager**

In `aprs_service_registry/main.py`, add the lifespan context manager and update the app:

First, add import at top:

```python
from contextlib import asynccontextmanager
```

Then add the lifespan function before the `app = FastAPI()` line:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan context manager for startup/shutdown."""
    # Startup
    from aprs_service_registry.health_checker import (
        setup_scheduler,
        start_scheduler,
        stop_scheduler,
    )
    
    # Load services from disk
    APRSServices().load()
    
    # Set up health check scheduler
    setup_scheduler()
    start_scheduler()
    
    yield
    
    # Shutdown
    stop_scheduler()
    APRSServices().save()


app = FastAPI(lifespan=lifespan)
```

Also **remove** the old startup event handler (lines 67-71 in the original file):

```python
# DELETE THIS BLOCK:
@app.on_event("startup")
@repeat_every(seconds=60)
def save_services(*args, **kwargs):
    APRSServices().save()
    print(time.time())
```

The save functionality is now handled in the lifespan shutdown.

- [ ] **Step 3: Verify server starts**

Run: `python -m aprs_service_registry.cli server --help`

Expected: Help message shows (no import errors)

- [ ] **Step 4: Commit**

```bash
git add aprs_service_registry/main.py aprs_service_registry/health_checker.py
git commit -m "feat: integrate health check scheduler with FastAPI lifespan"
```

---

## Chunk 4: API Response Updates

### Task 9: Add health check info to API responses

**Files:**
- Modify: `aprs_service_registry/main.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write test for health check info in API responses**

Add to `tests/test_api.py`:

```python
class TestHealthCheckInResponse:
    """Tests for health check info in API responses."""

    def setup_method(self):
        """Clear services and health checks before each test."""
        from aprs_service_registry.health_checker import HealthCheckStore

        services = APRSServices()
        services.data = {}
        HealthCheckStore().data = {}

    def test_single_service_includes_last_health_check(self):
        """GET single service includes last_health_check."""
        from datetime import timezone

        from aprs_service_registry.health_checker import (
            HealthCheckResult,
            HealthCheckStore,
        )

        # Add service
        services = APRSServices()
        services.add(
            "TESTCALL",
            registryRequest(
                callsign="TESTCALL",
                description="Test",
                service_website="https://test.com",
                software="test",
                health_check_command="ping",
            ),
        )

        # Add health check result
        store = HealthCheckStore()
        store.add_result(
            "TESTCALL",
            HealthCheckResult(
                timestamp=datetime.now(timezone.utc),
                success=True,
                response_time_ms=1500,
                response_text="Pong!",
                error=None,
            ),
        )

        response = client.get("/api/v1/registry/TESTCALL")
        assert response.status_code == 200
        data = response.json()

        # Verify health_check_command is in response
        assert data["health_check_command"] == "ping"
        # Verify last_health_check is in response
        assert "last_health_check" in data
        assert data["last_health_check"]["success"] is True
        assert data["last_health_check"]["response_time_ms"] == 1500

    def test_single_service_no_health_check(self):
        """GET single service with no health checks returns null."""
        services = APRSServices()
        services.add(
            "TESTCALL",
            registryRequest(
                callsign="TESTCALL",
                description="Test",
                service_website="https://test.com",
                software="test",
            ),
        )

        response = client.get("/api/v1/registry/TESTCALL")
        assert response.status_code == 200
        data = response.json()

        assert "last_health_check" in data
        assert data["last_health_check"] is None

    def test_list_services_includes_last_health_check(self):
        """GET all services includes last_health_check for each."""
        from datetime import timezone

        from aprs_service_registry.health_checker import (
            HealthCheckResult,
            HealthCheckStore,
        )

        services = APRSServices()
        services.add(
            "TEST1",
            registryRequest(
                callsign="TEST1",
                description="Test 1",
                service_website="https://test1.com",
                software="test",
                health_check_command="ping",
            ),
        )

        store = HealthCheckStore()
        store.add_result(
            "TEST1",
            HealthCheckResult(
                timestamp=datetime.now(timezone.utc),
                success=False,
                response_time_ms=None,
                response_text=None,
                error="Timeout",
            ),
        )

        response = client.get("/api/v1/registry")
        assert response.status_code == 200
        data = response.json()

        service = data["services"][0]
        # Verify health_check_command is in response
        assert service["health_check_command"] == "ping"
        # Verify last_health_check is in response
        assert "last_health_check" in service
        assert service["last_health_check"]["success"] is False
        assert service["last_health_check"]["error"] == "Timeout"
```

Also add import at top of test file:

```python
from datetime import datetime
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_api.py::TestHealthCheckInResponse -v`

Expected: FAIL (last_health_check not in response)

- [ ] **Step 3: Update get_service endpoint**

In `aprs_service_registry/main.py`, update the `get_service` function:

```python
@app.get("/api/v1/registry/{callsign}", response_class=JSONResponse)
async def get_service(callsign: str):
    """Get a single service by callsign."""
    from aprs_service_registry.health_checker import HealthCheckStore

    services = APRSServices()
    callsign_upper = callsign.upper()

    try:
        service = services[callsign_upper]
        try:
            service_dict = service.model_dump()
        except AttributeError:
            service_dict = service.dict()

        # Add health check info
        store = HealthCheckStore()
        last_result = store.get_last_result(callsign_upper)
        if last_result:
            service_dict["last_health_check"] = {
                "timestamp": last_result.timestamp.isoformat(),
                "success": last_result.success,
                "response_time_ms": last_result.response_time_ms,
            }
        else:
            service_dict["last_health_check"] = None

        return service_dict
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Service '{callsign_upper}' not found",
        )
```

- [ ] **Step 4: Update get_all_services endpoint**

In `aprs_service_registry/main.py`, update the `get_all_services` function to include health check info:

```python
@app.get("/api/v1/registry", response_class=JSONResponse)
async def get_all_services(
    include_down: bool = False,
    include_deleted: bool = False,
    include_all: bool = False,
):
    """Get all registered services, filtered by status."""
    from aprs_service_registry.health_checker import HealthCheckStore

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
        # Handle legacy services without status field
        try:
            service_dict = service.model_dump()
        except AttributeError:
            service_dict = service.dict()

        # Default status for legacy services
        if "status" not in service_dict or service_dict["status"] is None:
            service_dict["status"] = "active"

        if service_dict["status"] in allowed_statuses:
            # Add health check info
            last_result = store.get_last_result(callsign)
            if last_result:
                service_dict["last_health_check"] = {
                    "timestamp": last_result.timestamp.isoformat(),
                    "success": last_result.success,
                    "response_time_ms": last_result.response_time_ms,
                }
            else:
                service_dict["last_health_check"] = None

            services_list.append(service_dict)

    return {
        "count": len(services_list),
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "services": services_list,
    }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_api.py::TestHealthCheckInResponse -v`

Expected: PASS (all 3 tests)

- [ ] **Step 6: Run full test suite**

Run: `python -m pytest tests/ -v`

Expected: All tests PASS

- [ ] **Step 7: Commit**

```bash
git add aprs_service_registry/main.py tests/test_api.py
git commit -m "feat: add health check info to API responses"
```

---

## Chunk 5: Website UI Updates

### Task 10: Add "Last Check" column to website

**Files:**
- Modify: `aprs_service_registry/main.py` (website route)
- Modify: `aprs_service_registry/web/templates/index.html`
- Modify: `aprs_service_registry/web/static/main.css`

- [ ] **Step 1: Update website route to pass health check data**

In `aprs_service_registry/main.py`, update the `get` function (website route):

```python
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def get(request: Request):
    from aprs_service_registry.health_checker import HealthCheckStore

    services = APRSServices()
    all_services = services.get_all()
    store = HealthCheckStore()

    # Filter for website: show active and down, hide deleted
    # Also build health check info
    filtered_services = {}
    health_checks = {}

    for callsign, service in all_services.items():
        try:
            status = service.status if hasattr(service, "status") else "active"
        except AttributeError:
            status = "active"

        if status in ("active", "down"):
            filtered_services[callsign] = service
            # Get health check result
            last_result = store.get_last_result(callsign)
            health_checks[callsign] = last_result

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "request": request,
            "services": filtered_services,
            "health_checks": health_checks,
        },
    )
```

- [ ] **Step 2: Update template to show "Last Check" column**

In `aprs_service_registry/web/templates/index.html`, update the table:

Update `<thead>` (around line 46-55):

```html
<thead>
  <tr>
    <th>Callsign</th>
    <th>Status</th>
    <th>Last Check</th>
    <th>Owner</th>
    <th>Description</th>
    <th>URL</th>
    <th>Software</th>
  </tr>
</thead>
```

Update `<tbody>` (around line 56-77):

```html
<tbody>
  {% for service in services %}
  <tr{% if services[service].status == 'down' %} class="service-down"{% endif %}>
    <td><span class="callsign">{{ service }}</span></td>
    <td>
      {% if services[service].status == 'down' %}
        <span class="badge bg-warning text-dark">DOWN</span>
      {% else %}
        <span class="badge bg-success">Active</span>
      {% endif %}
    </td>
    <td>
      {% if health_checks[service] %}
        {% if health_checks[service].success %}
          <span class="health-check-success" title="{{ health_checks[service].timestamp }}">
            ✓ {{ health_checks[service].response_time_ms }}ms
          </span>
        {% else %}
          <span class="health-check-failure" title="{{ health_checks[service].timestamp }}">
            ✗ {{ health_checks[service].error }}
          </span>
        {% endif %}
      {% else %}
        <span class="health-check-none">—</span>
      {% endif %}
    </td>
    <td>{{ services[service].callsign_owner or '-' }}</td>
    <td>{{ services[service].description }}</td>
    <td>
      <a href="{{ services[service].service_website }}" target="_blank" rel="noopener noreferrer" class="link-external">
        {{ services[service].service_website }}
      </a>
    </td>
    <td><code class="software">{{ services[service].software }}</code></td>
  </tr>
  {% endfor %}
</tbody>
```

- [ ] **Step 3: Add CSS styles for health check display**

In `aprs_service_registry/web/static/main.css`, add at the end:

```css
/* Health check status styling */
.health-check-success {
    color: #198754;
    font-weight: 500;
}

.health-check-failure {
    color: #dc3545;
    font-weight: 500;
}

.health-check-none {
    color: #6c757d;
}
```

- [ ] **Step 4: Test manually**

Start server: `make server` or `aprs-service-registry server`

1. Visit http://localhost:8001/
2. Verify the "Last Check" column appears
3. Services without health checks show "—"

- [ ] **Step 5: Commit**

```bash
git add aprs_service_registry/main.py aprs_service_registry/web/templates/index.html aprs_service_registry/web/static/main.css
git commit -m "feat: add Last Check column to website"
```

---

## Chunk 6: Final Verification

### Task 11: Run full test suite and pre-commit

- [ ] **Step 1: Run complete test suite**

Run: `python -m pytest tests/ -v`

Expected: All tests PASS

- [ ] **Step 2: Run pre-commit hooks**

Run: `pre-commit run --all-files`

Expected: All hooks PASS (or fix any issues)

- [ ] **Step 3: Final commit if any fixes needed**

```bash
git add -A
git commit -m "style: fix linting issues"
```

---

### Task 12: Update README documentation

**Files:**
- Modify: `README.rst`

- [ ] **Step 1: Add health check documentation to README**

Add to `README.rst` after the Service Status section:

```rst
Health Checks
-------------

The registry can automatically check if services are responding on the APRS network.

**Enabling health checks:**

1. Create an APRSD configuration file with your APRS-IS credentials:

.. code-block:: ini

   # /config/aprsd.conf
   [DEFAULT]
   callsign = YOUR-CALL
   owner_callsign = YOUR-CALL

   [aprs_network]
   enabled = true
   password = YOUR_PASSCODE
   host = noam.aprs2.net
   port = 14580

2. Enable health checks in your registry configuration:

.. code-block:: ini

   # registry.conf
   [registry]
   aprsd_config_path = /config/aprsd.conf
   health_check_enabled = true
   health_check_timeout = 60

**Registering a service with health check:**

Include the ``health_check_command`` field when registering:

.. code-block:: bash

   curl -X POST https://aprs.hemna.com/api/v1/registry \
     -H "Content-Type: application/json" \
     -d '{"callsign": "MYSERVICE", "description": "...", "service_website": "...", "software": "...", "health_check_command": "ping"}'

The ``health_check_command`` is the message sent to the service. Common values:

- ``ping`` — For services that respond to ping
- ``help`` — For services that respond to help requests
- ``?`` — For services that respond to queries

**Health check results:**

Results appear in the API responses and on the website:

- ✓ with response time for successful checks
- ✗ with error message for failed checks
- — for services without health checks configured
```

- [ ] **Step 2: Commit**

```bash
git add README.rst
git commit -m "docs: add health check documentation to README"
```

---

## Summary

This plan implements:

1. **Data Model** (Tasks 1-3): Config options, health_check_command field, dependencies
2. **Health Checker Module** (Tasks 4-6): HealthCheckResult, HealthCheckStore, check_service function
3. **Scheduler** (Tasks 7-8): Stagger calculation, APScheduler integration with FastAPI lifespan
4. **API Updates** (Task 9): last_health_check in GET responses
5. **Website UI** (Task 10): "Last Check" column with status indicators
6. **Verification** (Tasks 11-12): Tests, linting, documentation

**Note:** The APRSD integration (`send_and_wait_for_response`) is left as a placeholder. A future task will implement the actual APRS-IS messaging once the infrastructure is in place and tested.
