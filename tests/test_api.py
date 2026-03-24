"""Tests for APRS Service Registry API endpoints."""

from fastapi.testclient import TestClient

from aprs_service_registry.main import app, APRSServices, registryRequest


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
