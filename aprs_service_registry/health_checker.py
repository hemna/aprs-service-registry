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
    response_text: str | None  # First 100 chars of response
    error: str | None  # Error message if failed
