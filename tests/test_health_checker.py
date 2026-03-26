"""Tests for health checker module."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch


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

    @patch("aprs_service_registry.health_checker.send_and_wait_for_response")
    def test_check_service_defaults_to_ping(self, mock_send):
        """Services without health_check_command default to 'ping'."""
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

        # Mock successful response
        mock_send.return_value = ("Pong!", 500)

        check_service("NOCOMMAND")

        # Verify 'ping' was used as the command
        mock_send.assert_called_once_with("NOCOMMAND", "ping", 60)

        # Verify result was stored
        store = HealthCheckStore()
        result = store.get_last_result("NOCOMMAND")
        assert result is not None
        assert result.success is True

    @patch("aprs_service_registry.health_checker.send_and_wait_for_response")
    def test_check_service_active_to_pending_on_failure(self, mock_send):
        """Active service transitions to pending on health check failure."""
        from aprs_service_registry.health_checker import check_service
        from aprs_service_registry.main import APRSServices, registryRequest

        services = APRSServices()
        services.add(
            "TESTCALL",
            registryRequest(
                callsign="TESTCALL",
                description="Test",
                service_website="https://test.com",
                software="test",
                status="active",
                health_check_command="ping",
            ),
        )

        # Mock timeout
        mock_send.return_value = (None, None)

        check_service("TESTCALL")

        # Verify status changed to pending
        service = services["TESTCALL"]
        service_dict = (
            service.model_dump() if hasattr(service, "model_dump") else service.dict()
        )
        assert service_dict["status"] == "pending"

    @patch("aprs_service_registry.health_checker.send_and_wait_for_response")
    def test_check_service_pending_to_active_on_success(self, mock_send):
        """Pending service transitions to active on health check success."""
        from aprs_service_registry.health_checker import check_service
        from aprs_service_registry.main import APRSServices, registryRequest

        services = APRSServices()
        services.add(
            "TESTCALL",
            registryRequest(
                callsign="TESTCALL",
                description="Test",
                service_website="https://test.com",
                software="test",
                status="pending",
                health_check_command="ping",
            ),
        )

        # Mock success
        mock_send.return_value = ("Pong!", 500)

        check_service("TESTCALL")

        # Verify status changed to active
        service = services["TESTCALL"]
        service_dict = (
            service.model_dump() if hasattr(service, "model_dump") else service.dict()
        )
        assert service_dict["status"] == "active"

    @patch("aprs_service_registry.health_checker.send_and_wait_for_response")
    def test_check_service_down_to_active_on_success(self, mock_send):
        """Down service transitions to active on health check success."""
        from aprs_service_registry.health_checker import check_service
        from aprs_service_registry.main import APRSServices, registryRequest

        services = APRSServices()
        services.add(
            "TESTCALL",
            registryRequest(
                callsign="TESTCALL",
                description="Test",
                service_website="https://test.com",
                software="test",
                status="down",
                health_check_command="ping",
            ),
        )

        # Mock success
        mock_send.return_value = ("Pong!", 500)

        check_service("TESTCALL")

        # Verify status changed to active
        service = services["TESTCALL"]
        service_dict = (
            service.model_dump() if hasattr(service, "model_dump") else service.dict()
        )
        assert service_dict["status"] == "active"

    @patch("aprs_service_registry.health_checker.send_and_wait_for_response")
    def test_check_service_pending_to_down_after_24h(self, mock_send):
        """Pending service transitions to down after 24h of failures."""
        from aprs_service_registry.health_checker import (
            HealthCheckResult,
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
                status="pending",
                health_check_command="ping",
            ),
        )

        # Add a failure result from 25 hours ago
        store = HealthCheckStore()
        old_failure = HealthCheckResult(
            timestamp=datetime.now(timezone.utc) - timedelta(hours=25),
            success=False,
            response_time_ms=None,
            response_text=None,
            error="Timeout",
        )
        store.add_result("TESTCALL", old_failure)

        # Mock another timeout
        mock_send.return_value = (None, None)

        check_service("TESTCALL")

        # Verify status changed to down
        service = services["TESTCALL"]
        service_dict = (
            service.model_dump() if hasattr(service, "model_dump") else service.dict()
        )
        assert service_dict["status"] == "down"

    @patch("aprs_service_registry.health_checker.send_and_wait_for_response")
    def test_check_service_pending_stays_pending_under_24h(self, mock_send):
        """Pending service stays pending if failures are under 24h."""
        from aprs_service_registry.health_checker import (
            HealthCheckResult,
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
                status="pending",
                health_check_command="ping",
            ),
        )

        # Add a failure result from 12 hours ago (under 24h threshold)
        store = HealthCheckStore()
        recent_failure = HealthCheckResult(
            timestamp=datetime.now(timezone.utc) - timedelta(hours=12),
            success=False,
            response_time_ms=None,
            response_text=None,
            error="Timeout",
        )
        store.add_result("TESTCALL", recent_failure)

        # Mock another timeout
        mock_send.return_value = (None, None)

        check_service("TESTCALL")

        # Verify status stays pending
        service = services["TESTCALL"]
        service_dict = (
            service.model_dump() if hasattr(service, "model_dump") else service.dict()
        )
        assert service_dict["status"] == "pending"


class TestScheduler:
    """Tests for health check scheduling."""

    def setup_method(self):
        """Clear stores before each test."""
        from aprs_service_registry.health_checker import HealthCheckStore
        from aprs_service_registry.main import APRSServices

        APRSServices().data = {}
        HealthCheckStore().data = {}

    def test_calculate_stagger_interval(self):
        """Stagger interval calculated correctly."""
        from aprs_service_registry.health_checker import (
            calculate_stagger_interval,
        )

        # 10 services = 360 second interval (6 minutes)
        assert calculate_stagger_interval(10) == 360

        # 15 services = 240 second interval (4 minutes)
        assert calculate_stagger_interval(15) == 240

        # 1 service = 3600 seconds (full hour)
        assert calculate_stagger_interval(1) == 3600

    def test_calculate_stagger_interval_zero_services(self):
        """Returns None if no checkable services."""
        from aprs_service_registry.health_checker import (
            calculate_stagger_interval,
        )

        assert calculate_stagger_interval(0) is None

    def test_get_checkable_services(self):
        """Returns all non-deleted services (they all get health checked)."""
        from aprs_service_registry.health_checker import get_checkable_services
        from aprs_service_registry.main import APRSServices, registryRequest

        services = APRSServices()
        services.data = {}

        # Checkable: has custom command, not deleted
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

        # Also checkable: no command (will default to 'ping')
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
        assert len(checkable) == 2
        assert "CHECKABLE" in checkable
        assert "NOCOMMAND" in checkable
        assert "DELETED" not in checkable


class TestSendAndWaitForResponse:
    """Tests for send_and_wait_for_response APRSD integration."""

    def test_returns_none_when_health_checks_disabled(self):
        """Returns (None, None) when health checks are disabled."""
        from unittest.mock import patch

        from aprs_service_registry.health_checker import (
            send_and_wait_for_response,
        )

        # Health checks are disabled by default in test config
        with patch(
            "aprs_service_registry.health_checker.CONF.registry.health_check_enabled",
            False,
        ):
            result = send_and_wait_for_response("TESTCALL", "ping", 10)
            assert result == (None, None)

    def test_returns_none_when_aprsd_init_fails(self):
        """Returns (None, None) when APRSD initialization fails."""
        from unittest.mock import patch

        # Reset the initialization flag
        import aprs_service_registry.health_checker as hc
        from aprs_service_registry.health_checker import (
            send_and_wait_for_response,
        )

        hc._aprsd_initialized = False

        with patch(
            "aprs_service_registry.health_checker.CONF.registry.health_check_enabled",
            True,
        ):
            with patch(
                "aprs_service_registry.health_checker.CONF.registry.aprsd_config_path",
                "/nonexistent/config.conf",
            ):
                result = send_and_wait_for_response("TESTCALL", "ping", 10)
                assert result == (None, None)

        # Reset for other tests
        hc._aprsd_initialized = False
