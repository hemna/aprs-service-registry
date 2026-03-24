# Service Health Check Feature Design

## Overview

Add automated health checking for registered APRS services. A background task runs hourly, sending APRS messages to each service and recording whether they respond. Results are displayed on the website and included in API responses.

## Goals

1. **Observability**: Track which services are responding on the APRS network
2. **History**: Keep the last 3 health check results per service
3. **Non-disruptive**: Log results only; do not auto-update service status (for now)
4. **Efficient**: Stagger checks throughout the hour to spread network load

## Non-Goals

- Automatic status updates (future enhancement after validating detection accuracy)
- Alerting/notifications
- Custom check intervals per service

---

## Data Model Changes

### New Field on `registryRequest`

```python
class registryRequest(BaseModel):
    callsign: str
    description: str
    service_website: str
    software: str
    callsign_owner: str | None = None
    status: Literal["active", "down", "deleted"] = "active"
    health_check_command: str | None = None  # NEW: e.g., "ping", "help", "version"
```

Services without `health_check_command` will be skipped during health checks.

### New Health Check Storage

A new data structure stored in the same pickle file (`aprsservices.p`):

```python
from dataclasses import dataclass
from datetime import datetime

@dataclass
class HealthCheckResult:
    timestamp: datetime           # When the check ran
    success: bool                 # True if response received within timeout
    response_time_ms: int | None  # Milliseconds to receive response (None if timeout)
    response_text: str | None     # The response message received (truncated to 100 chars)
    error: str | None             # Error message if failed (e.g., "Timeout", "Connection error")

# Storage structure (in APRSServices or separate):
# health_checks: dict[str, list[HealthCheckResult]] = {}
# Key: callsign (uppercase)
# Value: List of last 3 results, most recent first
```

---

## Background Task Architecture

### Dependencies

Add to `pyproject.toml`:
```
aprsd           # APRS-IS messaging
apscheduler     # Background task scheduling
```

### Scheduler Setup

Using APScheduler's `AsyncIOScheduler` integrated with FastAPI's lifespan:

```python
from contextlib import asynccontextmanager
from apscheduler.schedulers.asyncio import AsyncIOScheduler

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    if CONF.registry.health_check_enabled:
        scheduler = AsyncIOScheduler()
        schedule_health_checks(scheduler)
        scheduler.start()
    yield
    # Shutdown
    if scheduler:
        scheduler.shutdown()
```

### Staggered Scheduling

Checks are distributed evenly across each hour:

```
interval = 3600 / number_of_checkable_services

Example with 15 services:
- Interval = 240 seconds (4 minutes)
- Service 1 at :00, Service 2 at :04, Service 3 at :08, etc.
```

Services are "checkable" if:
- `status != "deleted"`
- `health_check_command` is not None/empty

### Health Check Flow

```
1. Scheduler triggers check for SERVICE_X
2. Load service from registry
3. Skip if status == "deleted" OR health_check_command is None
4. Connect to APRS-IS (if not already connected)
5. Send MessagePacket with health_check_command to callsign
6. Wait up to 60 seconds for ANY response from that callsign
7. Record HealthCheckResult:
   - success: True if response received
   - response_time_ms: Time from send to receive
   - response_text: First 100 chars of response
   - error: None if success, else "Timeout" or error message
8. Prepend to health_checks[callsign], keep only last 3
9. Save to pickle file
```

---

## APRSD Integration

### Configuration

APRSD uses oslo.config with INI format. The health checker needs its own APRSD config file.

**Registry config** (`registry.conf`):
```ini
[registry]
aprsd_config_path = /config/aprsd.conf
health_check_enabled = true
health_check_timeout = 60
```

**APRSD config** (`/config/aprsd.conf`):
```ini
[DEFAULT]
callsign = WB4BOR-15
owner_callsign = WB4BOR

[aprs_network]
enabled = true
password = YOUR_PASSCODE
host = noam.aprs2.net
port = 14580
```

### APRSD Client Usage

```python
from oslo_config import cfg
from aprsd import conf  # Registers config options
from aprsd.client.client import APRSDClient
from aprsd.packets import core as packets
from aprsd.threads import tx

# Load APRSD config (separate from registry config)
aprsd_conf = cfg.ConfigOpts()
aprsd_conf([], project='aprsd', default_config_files=[aprsd_config_path])

# Create client singleton
client = APRSDClient(auto_connect=True)

# Send health check message
msg = packets.MessagePacket(
    from_call=aprsd_conf.callsign,
    to_call=service_callsign,
    message_text=service.health_check_command,
)
tx.send(msg, direct=True)

# Listen for response via client.consumer() with timeout
```

### Response Detection

The health checker will:
1. Send message with unique msgNo
2. Start a timer
3. Listen for ANY message from the target callsign (not just ACK)
4. First message received = success, record response time
5. If 60 seconds pass with no response = timeout failure

---

## API Changes

### POST /api/v1/registry

Accept new `health_check_command` field:
```json
{
  "callsign": "ANSRVR",
  "description": "Answer Server",
  "service_website": "https://ansrvr.com",
  "software": "ansrvr 1.0",
  "health_check_command": "help"
}
```

Validation: If provided, must be a non-empty string, max 50 characters.

### GET /api/v1/registry

Include `health_check_command` and `last_health_check` in response:
```json
{
  "count": 15,
  "timestamp": "2026-03-24T16:00:00Z",
  "services": [
    {
      "callsign": "ANSRVR",
      "status": "active",
      "health_check_command": "help",
      "last_health_check": {
        "timestamp": "2026-03-24T15:56:00Z",
        "success": true,
        "response_time_ms": 2340
      }
    },
    {
      "callsign": "REPEAT",
      "status": "active",
      "health_check_command": null,
      "last_health_check": null
    }
  ]
}
```

### GET /api/v1/registry/{callsign}

Same structure as above for single service.

---

## Website Changes

### New "Last Check" Column

Add column to the services table between "Status" and "Owner":

| Callsign | Status | Last Check | Owner | Description | URL | Software |
|----------|--------|------------|-------|-------------|-----|----------|
| ANSRVR | Active | ✓ 5m ago | ... | ... | ... | ... |
| HEMNA | DOWN | ✗ 12m ago | ... | ... | ... | ... |
| REPEAT | Active | — | ... | ... | ... | ... |

### Display Logic

| Condition | Display | Style |
|-----------|---------|-------|
| Last check success | ✓ {relative_time} ago | Green text |
| Last check failed | ✗ {relative_time} ago | Red text |
| No health_check_command | — | Gray text |
| Never checked | — | Gray text |

### CSS

```css
.health-check-success {
    color: #198754;  /* Bootstrap success green */
}

.health-check-failure {
    color: #dc3545;  /* Bootstrap danger red */
}

.health-check-none {
    color: #6c757d;  /* Bootstrap secondary gray */
}
```

---

## Configuration Options

New oslo.config options in `registry` group:

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `aprsd_config_path` | string | `/config/aprsd.conf` | Path to APRSD config file |
| `health_check_enabled` | bool | `false` | Enable background health checks |
| `health_check_timeout` | int | `60` | Seconds to wait for response |

---

## File Changes Summary

| File | Change |
|------|--------|
| `pyproject.toml` | Add `aprsd`, `apscheduler` dependencies |
| `aprs_service_registry/main.py` | Add `health_check_command` to model, update lifespan, add scheduler |
| `aprs_service_registry/health_checker.py` | NEW: Health check logic and APRSD integration |
| `aprs_service_registry/web/templates/index.html` | Add "Last Check" column |
| `aprs_service_registry/web/static/main.css` | Add health check styles |
| `tests/test_api.py` | Add tests for health_check_command field |
| `tests/test_health_checker.py` | NEW: Unit tests for health checker |

---

## Testing Strategy

### Unit Tests

1. **Model tests**: `health_check_command` field validation
2. **Health check result storage**: Add/retrieve/limit to 3 results
3. **Scheduler calculation**: Correct stagger intervals
4. **API response format**: Health check data included correctly

### Integration Tests (Manual)

1. Start server with health checks enabled
2. Register a service with `health_check_command`
3. Verify scheduler runs and records results
4. Check website displays results correctly

### Mock Testing

APRSD client will be mocked in unit tests to avoid actual APRS-IS connections.

---

## Deployment Considerations

### Docker

The production container will need:
1. APRSD config file mounted at `/config/aprsd.conf`
2. Valid APRS-IS credentials in the config
3. `health_check_enabled = true` in registry.conf

### Existing Services

Existing services will have `health_check_command = None` and won't be checked until updated via POST with the field populated.

---

## Future Enhancements (Out of Scope)

1. Auto-update status after N consecutive failures
2. Configurable check interval per service
3. Health check history API endpoint
4. Webhook/notification on status change
5. Manual "check now" button on website
