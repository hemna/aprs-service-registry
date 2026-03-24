"""Tests for APRS Service Registry API endpoints."""

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
