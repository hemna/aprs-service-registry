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
