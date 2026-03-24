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
