"""Tests for health checker module."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch


class TestCalculateUptime:
    """Tests for calculate_uptime helper function."""

    def test_calculate_uptime_all_success(self):
        """Test uptime calculation with all successful checks."""
        from aprs_service_registry.health_checker import calculate_uptime

        results = [{"success": True} for _ in range(24)]
        assert calculate_uptime(results) == "100%"

    def test_calculate_uptime_mixed(self):
        """Test uptime calculation with mixed results."""
        from aprs_service_registry.health_checker import calculate_uptime

        results = [{"success": True}] * 23 + [{"success": False}]
        assert calculate_uptime(results) == "96%"

    def test_calculate_uptime_all_failures(self):
        """Test uptime calculation with all failures."""
        from aprs_service_registry.health_checker import calculate_uptime

        results = [{"success": False} for _ in range(24)]
        assert calculate_uptime(results) == "0%"

    def test_calculate_uptime_empty(self):
        """Test uptime calculation with no data."""
        from aprs_service_registry.health_checker import calculate_uptime

        assert calculate_uptime([]) == "--"

    def test_calculate_uptime_partial(self):
        """Test uptime calculation with partial data (new service)."""
        from aprs_service_registry.health_checker import calculate_uptime

        results = [{"success": True}] * 6
        assert calculate_uptime(results) == "100%"

    def test_calculate_uptime_with_objects(self):
        """Test uptime calculation with object results (HealthCheckResult)."""
        from aprs_service_registry.health_checker import (
            HealthCheckResult,
            calculate_uptime,
        )

        results = [
            HealthCheckResult(
                timestamp=datetime.now(timezone.utc),
                success=True,
                response_time_ms=100,
                response_text="OK",
                error=None,
            )
            for _ in range(10)
        ]
        # Add 2 failures
        results.append(
            HealthCheckResult(
                timestamp=datetime.now(timezone.utc),
                success=False,
                response_time_ms=None,
                response_text=None,
                error="Timeout",
            )
        )
        results.append(
            HealthCheckResult(
                timestamp=datetime.now(timezone.utc),
                success=False,
                response_time_ms=None,
                response_text=None,
                error="Timeout",
            )
        )
        # 10/12 = 83.33% -> 83%
        assert calculate_uptime(results) == "83%"


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


class TestCheckService:
    """Tests for check_service function."""

    def setup_method(self):
        """Set up fresh DB for health checker."""
        import aprs_service_registry.health_checker as hc
        from aprs_service_registry.db import RegistryDB

        self.db = RegistryDB(":memory:")
        hc._db = self.db

    @patch("aprs_service_registry.health_checker.send_and_wait_for_response")
    def test_check_service_success(self, mock_send):
        """Successful health check records success result."""
        from aprs_service_registry.health_checker import check_service

        self.db.upsert_service("TESTCALL", {
            "description": "Test",
            "service_website": "https://test.com",
            "software": "test",
            "health_check_command": "ping",
        })

        mock_send.return_value = ("Pong!", 1500)
        check_service("TESTCALL")

        result = self.db.get_last_health_check("TESTCALL")
        assert result is not None
        assert result["success"] is True
        assert result["response_time_ms"] == 1500

    @patch("aprs_service_registry.health_checker.send_and_wait_for_response")
    def test_check_service_timeout(self, mock_send):
        """Timeout records failure result."""
        from aprs_service_registry.health_checker import check_service

        self.db.upsert_service("TESTCALL", {
            "description": "Test",
            "service_website": "https://test.com",
            "software": "test",
            "health_check_command": "ping",
        })

        mock_send.return_value = (None, None)
        check_service("TESTCALL")

        result = self.db.get_last_health_check("TESTCALL")
        assert result is not None
        assert result["success"] is False
        assert result["error"] == "Timeout"

    def test_check_service_skips_deleted(self):
        """Deleted services are skipped."""
        from aprs_service_registry.health_checker import check_service

        self.db.upsert_service("DELETED", {
            "description": "Test",
            "status": "deleted",
            "health_check_command": "ping",
        })

        check_service("DELETED")

        assert self.db.get_last_health_check("DELETED") is None

    @patch("aprs_service_registry.health_checker.send_and_wait_for_response")
    def test_check_service_defaults_to_ping(self, mock_send):
        """Services without health_check_command default to 'ping'."""
        from aprs_service_registry.health_checker import check_service

        self.db.upsert_service("NOCOMMAND", {
            "description": "Test",
            "health_check_command": None,
        })

        mock_send.return_value = ("Pong!", 500)
        check_service("NOCOMMAND")

        mock_send.assert_called_once_with("NOCOMMAND", "ping", 60)

        result = self.db.get_last_health_check("NOCOMMAND")
        assert result is not None
        assert result["success"] is True

    @patch("aprs_service_registry.health_checker.send_and_wait_for_response")
    def test_check_service_active_to_pending_on_failure(self, mock_send):
        """Active service transitions to pending on health check failure."""
        from aprs_service_registry.health_checker import check_service

        self.db.upsert_service("TESTCALL", {
            "status": "active",
            "health_check_command": "ping",
        })

        mock_send.return_value = (None, None)
        check_service("TESTCALL")

        svc = self.db.get_service("TESTCALL")
        assert svc["status"] == "pending"

    @patch("aprs_service_registry.health_checker.send_and_wait_for_response")
    def test_check_service_pending_to_active_on_success(self, mock_send):
        """Pending service transitions to active on health check success."""
        from aprs_service_registry.health_checker import check_service

        self.db.upsert_service("TESTCALL", {
            "status": "pending",
            "health_check_command": "ping",
        })

        mock_send.return_value = ("Pong!", 500)
        check_service("TESTCALL")

        svc = self.db.get_service("TESTCALL")
        assert svc["status"] == "active"

    @patch("aprs_service_registry.health_checker.send_and_wait_for_response")
    def test_check_service_down_to_active_on_success(self, mock_send):
        """Down service transitions to active on health check success."""
        from aprs_service_registry.health_checker import check_service

        self.db.upsert_service("TESTCALL", {
            "status": "down",
            "health_check_command": "ping",
        })

        mock_send.return_value = ("Pong!", 500)
        check_service("TESTCALL")

        svc = self.db.get_service("TESTCALL")
        assert svc["status"] == "active"

    @patch("aprs_service_registry.health_checker.send_and_wait_for_response")
    def test_check_service_pending_to_down_after_24h(self, mock_send):
        """Pending service transitions to down after consecutive failures."""
        from aprs_service_registry.health_checker import check_service

        self.db.upsert_service("TESTCALL", {
            "status": "pending",
            "health_check_command": "ping",
        })

        # Add 2 prior failure results (total with current = 3 = CONSECUTIVE_FAILURES_FOR_DOWN)
        self.db.add_health_check("TESTCALL", {
            "success": False,
            "timestamp": "2025-01-01T00:00:00Z",
            "error": "Timeout",
        })
        self.db.add_health_check("TESTCALL", {
            "success": False,
            "timestamp": "2025-01-01T01:00:00Z",
            "error": "Timeout",
        })

        mock_send.return_value = (None, None)
        check_service("TESTCALL")

        svc = self.db.get_service("TESTCALL")
        assert svc["status"] == "down"

    @patch("aprs_service_registry.health_checker.send_and_wait_for_response")
    def test_check_service_pending_stays_pending_under_24h(self, mock_send):
        """Pending service stays pending if under failure threshold."""
        from aprs_service_registry.health_checker import check_service

        self.db.upsert_service("TESTCALL", {
            "status": "pending",
            "health_check_command": "ping",
        })

        # Add only 1 prior failure (total with current = 2, under threshold of 3)
        self.db.add_health_check("TESTCALL", {
            "success": False,
            "timestamp": "2025-01-01T00:00:00Z",
            "error": "Timeout",
        })

        mock_send.return_value = (None, None)
        check_service("TESTCALL")

        svc = self.db.get_service("TESTCALL")
        assert svc["status"] == "pending"


class TestScheduler:
    """Tests for health check scheduling."""

    def setup_method(self):
        """Set up fresh DB for scheduler tests."""
        import aprs_service_registry.health_checker as hc
        from aprs_service_registry.db import RegistryDB

        self.db = RegistryDB(":memory:")
        hc._db = self.db

    def test_calculate_stagger_interval(self):
        """Stagger interval calculated correctly."""
        from aprs_service_registry.health_checker import calculate_stagger_interval

        assert calculate_stagger_interval(10) == 360
        assert calculate_stagger_interval(15) == 240
        assert calculate_stagger_interval(1) == 3600

    def test_calculate_stagger_interval_zero_services(self):
        """Returns None if no checkable services."""
        from aprs_service_registry.health_checker import calculate_stagger_interval

        assert calculate_stagger_interval(0) is None

    def test_get_checkable_services(self):
        """Returns all non-deleted services."""
        from aprs_service_registry.health_checker import get_checkable_services

        self.db.upsert_service("CHECKABLE", {
            "status": "active",
            "health_check_command": "ping",
        })
        self.db.upsert_service("NOCOMMAND", {
            "status": "active",
        })
        self.db.upsert_service("DELETED", {
            "status": "deleted",
            "health_check_command": "ping",
        })

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
