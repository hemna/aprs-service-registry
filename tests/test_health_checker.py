"""Tests for health checker module."""

from datetime import datetime, timezone
from unittest.mock import patch

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


class TestScheduler:
    """Tests for health check scheduling."""

    def setup_method(self):
        """Clear stores before each test."""
        from aprs_service_registry.main import APRSServices

        APRSServices().data = {}

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
