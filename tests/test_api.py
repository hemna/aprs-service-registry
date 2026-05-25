"""Tests for APRS Service Registry API endpoints."""

from datetime import datetime

from fastapi.testclient import TestClient

from aprs_service_registry.main import APRSServices, app, registryRequest


client = TestClient(app)


class TestGetAllServices:
    """Tests for GET /api/v1/registry endpoint."""

    def setup_method(self):
        """Clear services before each test."""
        services = APRSServices()
        services.data = {}

    def test_get_all_services_empty(self):
        """Returns empty list with count 0 when no services registered."""
        response = client.get("/api/v1/registry")

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 0
        assert data["services"] == []
        assert "timestamp" in data

    def test_get_all_services_with_data(self):
        """Returns all registered services with correct count."""
        # Register a test service
        services = APRSServices()
        services.add(
            "TEST1",
            registryRequest(
                callsign="TEST1",
                description="Test Service 1",
                service_website="https://test1.example.com",
                software="test-soft 1.0",
            ),
        )
        services.add(
            "TEST2",
            registryRequest(
                callsign="TEST2",
                description="Test Service 2",
                service_website="https://test2.example.com",
                software="test-soft 2.0",
            ),
        )

        response = client.get("/api/v1/registry")

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 2
        assert len(data["services"]) == 2
        assert "timestamp" in data

        # Verify service data
        callsigns = [s["callsign"] for s in data["services"]]
        assert "TEST1" in callsigns
        assert "TEST2" in callsigns


class TestGetSingleService:
    """Tests for GET /api/v1/registry/{callsign} endpoint."""

    def setup_method(self):
        """Clear services and add test data before each test."""
        services = APRSServices()
        services.data = {}
        services.add(
            "TESTCALL",
            registryRequest(
                callsign="TESTCALL",
                description="Test Service",
                service_website="https://test.example.com",
                software="test-soft 1.0",
                callsign_owner="N0CALL",
            ),
        )

    def test_get_service_found(self):
        """Returns service data when callsign exists."""
        response = client.get("/api/v1/registry/TESTCALL")

        assert response.status_code == 200
        data = response.json()
        assert data["callsign"] == "TESTCALL"
        assert data["description"] == "Test Service"
        assert data["service_website"] == "https://test.example.com"
        assert data["software"] == "test-soft 1.0"
        assert data["callsign_owner"] == "N0CALL"

    def test_get_service_case_insensitive(self):
        """Callsign lookup is case-insensitive."""
        response = client.get("/api/v1/registry/testcall")

        assert response.status_code == 200
        data = response.json()
        assert data["callsign"] == "TESTCALL"

    def test_get_service_not_found(self):
        """Returns 404 when callsign doesn't exist."""
        response = client.get("/api/v1/registry/NOTEXIST")

        assert response.status_code == 404
        data = response.json()
        assert "detail" in data
        assert "NOTEXIST" in data["detail"]


class TestServiceStatus:
    """Tests for service status field."""

    def setup_method(self):
        """Clear services before each test."""
        services = APRSServices()
        services.data = {}

    def test_register_service_default_status(self):
        """New services default to active status."""
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

        # Fetch and verify status
        get_response = client.get("/api/v1/registry/TEST")
        assert get_response.status_code == 200
        assert get_response.json()["status"] == "active"

    def test_register_service_with_status(self):
        """Can register a service with explicit status."""
        response = client.post(
            "/api/v1/registry",
            json={
                "callsign": "TEST",
                "description": "Test Service",
                "service_website": "https://test.com",
                "software": "test 1.0",
                "status": "down",
            },
        )
        assert response.status_code == 200

        get_response = client.get("/api/v1/registry/TEST")
        assert get_response.json()["status"] == "down"

    def test_register_service_invalid_status(self):
        """Invalid status returns 422 validation error."""
        response = client.post(
            "/api/v1/registry",
            json={
                "callsign": "TEST",
                "description": "Test Service",
                "service_website": "https://test.com",
                "software": "test 1.0",
                "status": "invalid",
            },
        )
        assert response.status_code == 422

    def test_get_single_service_returns_regardless_of_status(self):
        """GET /api/v1/registry/{callsign} returns service even if deleted."""
        # Register and delete a service
        client.post(
            "/api/v1/registry",
            json={
                "callsign": "DELETED",
                "description": "Deleted Service",
                "service_website": "https://deleted.com",
                "software": "test 1.0",
                "status": "deleted",
            },
        )

        # Should still be fetchable by callsign
        get_response = client.get("/api/v1/registry/DELETED")
        assert get_response.status_code == 200
        assert get_response.json()["callsign"] == "DELETED"
        assert get_response.json()["status"] == "deleted"


class TestStatusFiltering:
    """Tests for GET /api/v1/registry status filtering."""

    def setup_method(self):
        """Set up test services with different statuses."""
        services = APRSServices()
        services.data = {}

        # Add services with different statuses
        services.add(
            "ACTIVE1",
            registryRequest(
                callsign="ACTIVE1",
                description="Active service 1",
                service_website="https://active1.com",
                software="test",
                status="active",
            ),
        )
        services.add(
            "ACTIVE2",
            registryRequest(
                callsign="ACTIVE2",
                description="Active service 2",
                service_website="https://active2.com",
                software="test",
                status="active",
            ),
        )
        services.add(
            "DOWN1",
            registryRequest(
                callsign="DOWN1",
                description="Down service",
                service_website="https://down.com",
                software="test",
                status="down",
            ),
        )
        services.add(
            "PENDING1",
            registryRequest(
                callsign="PENDING1",
                description="Pending service",
                service_website="https://pending.com",
                software="test",
                status="pending",
            ),
        )
        services.add(
            "DELETED1",
            registryRequest(
                callsign="DELETED1",
                description="Deleted service",
                service_website="https://deleted.com",
                software="test",
                status="deleted",
            ),
        )

    def test_default_returns_active_pending_and_down(self):
        """Default GET returns active, pending, and down services (not deleted)."""
        response = client.get("/api/v1/registry")
        assert response.status_code == 200
        data = response.json()

        assert data["count"] == 4
        callsigns = [s["callsign"] for s in data["services"]]
        assert "ACTIVE1" in callsigns
        assert "ACTIVE2" in callsigns
        assert "PENDING1" in callsigns
        assert "DOWN1" in callsigns
        assert "DELETED1" not in callsigns

    def test_include_deleted(self):
        """include_deleted=true returns all services including deleted."""
        response = client.get("/api/v1/registry?include_deleted=true")
        assert response.status_code == 200
        data = response.json()

        assert data["count"] == 5
        callsigns = [s["callsign"] for s in data["services"]]
        assert "ACTIVE1" in callsigns
        assert "ACTIVE2" in callsigns
        assert "PENDING1" in callsigns
        assert "DOWN1" in callsigns
        assert "DELETED1" in callsigns

    def test_include_all(self):
        """include_all=true returns all services."""
        response = client.get("/api/v1/registry?include_all=true")
        assert response.status_code == 200
        data = response.json()

        assert data["count"] == 5
        callsigns = [s["callsign"] for s in data["services"]]
        assert "ACTIVE1" in callsigns
        assert "ACTIVE2" in callsigns
        assert "PENDING1" in callsigns
        assert "DOWN1" in callsigns
        assert "DELETED1" in callsigns


class TestSoftDelete:
    """Tests for soft delete behavior."""

    def setup_method(self):
        """Clear services and add test data."""
        services = APRSServices()
        services.data = {}
        services.add(
            "TODELETE",
            registryRequest(
                callsign="TODELETE",
                description="Service to delete",
                service_website="https://delete.com",
                software="test",
                status="active",
            ),
        )

    def test_delete_sets_status_deleted(self):
        """DELETE sets status to deleted instead of removing."""
        # Verify service exists and is active
        get_response = client.get("/api/v1/registry/TODELETE")
        assert get_response.status_code == 200
        assert get_response.json()["status"] == "active"

        # Delete the service
        delete_response = client.delete("/api/v1/registry/TODELETE")
        assert delete_response.status_code == 200
        data = delete_response.json()
        assert data["status"] == "ok"
        assert "deleted" in data["message"].lower()

        # Service should still exist but with deleted status
        get_response = client.get("/api/v1/registry/TODELETE")
        assert get_response.status_code == 200
        assert get_response.json()["status"] == "deleted"

    def test_deleted_service_excluded_from_list(self):
        """Deleted services are excluded from default list."""
        # Delete the service
        client.delete("/api/v1/registry/TODELETE")

        # Should not appear in default list
        list_response = client.get("/api/v1/registry")
        callsigns = [s["callsign"] for s in list_response.json()["services"]]
        assert "TODELETE" not in callsigns

        # Should appear with include_deleted
        list_response = client.get("/api/v1/registry?include_deleted=true")
        callsigns = [s["callsign"] for s in list_response.json()["services"]]
        assert "TODELETE" in callsigns


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

    def test_list_services_includes_null_health_check_when_no_results(self):
        """GET all services includes last_health_check=None when no health checks exist."""
        services = APRSServices()
        services.add(
            "TESTNOHC",
            registryRequest(
                callsign="TESTNOHC",
                description="Service with no health checks",
                service_website="https://test.com",
                software="test",
            ),
        )

        response = client.get("/api/v1/registry")
        assert response.status_code == 200
        data = response.json()

        # Find our service in the list
        service = next(s for s in data["services"] if s.get("callsign") == "TESTNOHC")

        assert "last_health_check" in service
        assert service["last_health_check"] is None


class TestAdminCreateService:
    """Tests for the admin create service endpoint (POST /admin/services/new)."""

    def setup_method(self):
        """Clear services and enable admin before each test."""
        services = APRSServices()
        services.data = {}
        from oslo_config import cfg

        cfg.CONF.set_override("admin_password", "testpass", group="registry")

    def teardown_method(self):
        """Reset admin password after each test."""
        from oslo_config import cfg

        cfg.CONF.set_override("admin_password", "", group="registry")

    def _auth(self):
        return ("admin", "testpass")

    def test_create_service_success(self):
        """Admin can create a new service via the form."""
        response = client.post(
            "/admin/services/new",
            data={
                "callsign": "FIND",
                "description": "APRS station lookup service",
                "service_website": "https://aprs.wiki/find/",
                "software": "aprsd",
                "callsign_owner": "",
                "status": "active",
                "health_check_command": "",
                "featured": "",
            },
            auth=self._auth(),
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "/admin/services/FIND" in response.headers["location"]

        # Verify service was persisted
        services = APRSServices()
        assert "FIND" in services.data
        svc = services["FIND"]
        assert svc.description == "APRS station lookup service"
        assert svc.service_website == "https://aprs.wiki/find/"

    def test_create_service_duplicate(self):
        """Creating a duplicate callsign returns an error."""
        services = APRSServices()
        services.add(
            "DUPE",
            registryRequest(
                callsign="DUPE",
                description="Existing",
                service_website="https://example.com",
                software="test",
            ),
        )

        response = client.post(
            "/admin/services/new",
            data={
                "callsign": "DUPE",
                "description": "Another one",
                "service_website": "https://example.com",
                "software": "test",
            },
            auth=self._auth(),
            follow_redirects=False,
        )
        # Should return 200 with error message, not redirect
        assert response.status_code == 200
        assert "already exists" in response.text

    def test_create_service_missing_callsign(self):
        """Creating a service without callsign returns an error."""
        response = client.post(
            "/admin/services/new",
            data={
                "callsign": "",
                "description": "No callsign",
                "service_website": "https://example.com",
                "software": "test",
            },
            auth=self._auth(),
            follow_redirects=False,
        )
        assert response.status_code == 200
        assert "required" in response.text.lower()

    def test_create_service_unauthenticated(self):
        """Unauthenticated requests are rejected."""
        response = client.post(
            "/admin/services/new",
            data={"callsign": "TEST"},
            follow_redirects=False,
        )
        assert response.status_code == 401

    def test_get_new_service_form(self):
        """Admin can access the new service form."""
        response = client.get("/admin/services/new", auth=self._auth())
        assert response.status_code == 200
        assert "Add New Service" in response.text
