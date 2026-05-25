"""Tests for APRS Service Registry API endpoints."""

from datetime import datetime

from fastapi.testclient import TestClient

from aprs_service_registry.db import RegistryDB
from aprs_service_registry.main import app


client = TestClient(app)


def _reset_db():
    """Replace the app's DB with a fresh in-memory instance."""
    app.state.db = RegistryDB(":memory:")


def _db():
    """Get the current app DB."""
    return app.state.db


class TestGetAllServices:
    """Tests for GET /api/v1/registry endpoint."""

    def setup_method(self):
        _reset_db()

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
        db = _db()
        db.upsert_service("TEST1", {
            "description": "Test Service 1",
            "service_website": "https://test1.example.com",
            "software": "test-soft 1.0",
        })
        db.upsert_service("TEST2", {
            "description": "Test Service 2",
            "service_website": "https://test2.example.com",
            "software": "test-soft 2.0",
        })

        response = client.get("/api/v1/registry")

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 2
        assert len(data["services"]) == 2
        assert "timestamp" in data

        callsigns = [s["callsign"] for s in data["services"]]
        assert "TEST1" in callsigns
        assert "TEST2" in callsigns


class TestGetSingleService:
    """Tests for GET /api/v1/registry/{callsign} endpoint."""

    def setup_method(self):
        _reset_db()
        db = _db()
        db.upsert_service("TESTCALL", {
            "description": "Test Service",
            "service_website": "https://test.example.com",
            "software": "test-soft 1.0",
            "callsign_owner": "N0CALL",
            "status": "active",
            "commands": [
                {"name": "ping", "description": "Health check"},
                {"name": "help", "description": "Show help"},
            ],
        })

    def test_get_existing_service(self):
        """Returns service data when callsign exists."""
        response = client.get("/api/v1/registry/TESTCALL")

        assert response.status_code == 200
        data = response.json()
        assert data["callsign"] == "TESTCALL"
        assert data["description"] == "Test Service"
        assert data["service_website"] == "https://test.example.com"
        assert data["software"] == "test-soft 1.0"
        assert data["callsign_owner"] == "N0CALL"
        assert data["status"] == "active"

    def test_get_service_case_insensitive(self):
        """Callsign lookup is case-insensitive."""
        response = client.get("/api/v1/registry/testcall")

        assert response.status_code == 200
        data = response.json()
        assert data["callsign"] == "TESTCALL"

    def test_get_nonexistent_service(self):
        """Returns 404 when callsign doesn't exist."""
        response = client.get("/api/v1/registry/NOSUCH")

        assert response.status_code == 404

    def test_get_service_includes_commands(self):
        """Response includes commands list."""
        response = client.get("/api/v1/registry/TESTCALL")

        assert response.status_code == 200
        data = response.json()
        assert "commands" in data
        assert len(data["commands"]) == 2
        names = [c["name"] for c in data["commands"]]
        assert "ping" in names
        assert "help" in names

    def test_get_service_includes_health_check(self):
        """Response includes last_health_check field."""
        response = client.get("/api/v1/registry/TESTCALL")

        assert response.status_code == 200
        data = response.json()
        assert "last_health_check" in data


class TestRegisterService:
    """Tests for POST /api/v1/registry endpoint."""

    def setup_method(self):
        _reset_db()

    def test_register_new_service(self):
        """Successfully register a new service."""
        response = client.post(
            "/api/v1/registry",
            json={
                "callsign": "NEWTEST",
                "description": "Brand New Service",
                "service_website": "https://new.example.com",
                "software": "test 2.0",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

        # Verify it was stored
        svc = _db().get_service("NEWTEST")
        assert svc is not None
        assert svc["description"] == "Brand New Service"

    def test_register_uppercases_callsign(self):
        """Callsign is uppercased on registration."""
        response = client.post(
            "/api/v1/registry",
            json={
                "callsign": "lowercase",
                "description": "Test",
                "service_website": "https://test.com",
                "software": "test",
            },
        )

        assert response.status_code == 200
        svc = _db().get_service("LOWERCASE")
        assert svc is not None

    def test_re_register_preserves_commands(self):
        """Re-registering a service preserves existing commands."""
        db = _db()
        db.upsert_service("TEST1", {
            "description": "Original",
            "commands": [{"name": "ping", "description": "Health check"}],
        })

        response = client.post(
            "/api/v1/registry",
            json={
                "callsign": "TEST1",
                "description": "Updated",
                "service_website": "https://test.com",
                "software": "test",
            },
        )

        assert response.status_code == 200
        svc = db.get_service("TEST1")
        assert svc["description"] == "Updated"
        # Commands should be preserved
        assert len(svc["commands"]) == 1
        assert svc["commands"][0]["name"] == "ping"

    def test_re_register_preserves_featured(self):
        """Re-registering a service preserves featured flag."""
        db = _db()
        db.upsert_service("TEST1", {"featured": True})

        response = client.post(
            "/api/v1/registry",
            json={
                "callsign": "TEST1",
                "description": "Updated",
                "service_website": "https://test.com",
                "software": "test",
            },
        )

        assert response.status_code == 200
        svc = db.get_service("TEST1")
        assert svc["featured"] is True


class TestDeleteService:
    """Tests for DELETE /api/v1/registry/{callsign} endpoint."""

    def setup_method(self):
        _reset_db()
        _db().upsert_service("DELME", {
            "description": "To delete",
            "status": "active",
        })

    def test_delete_service(self):
        """Soft deletes a service."""
        response = client.delete("/api/v1/registry/DELME")

        assert response.status_code == 200
        svc = _db().get_service("DELME")
        assert svc["status"] == "deleted"

    def test_delete_nonexistent(self):
        """Returns 404 for nonexistent service."""
        response = client.delete("/api/v1/registry/NOSUCH")
        assert response.status_code == 404


class TestHealthCheckField:
    """Tests for health check data in service responses."""

    def setup_method(self):
        _reset_db()

    def test_service_with_health_checks(self):
        """Service response includes health check data."""
        db = _db()
        db.upsert_service("TESTHC", {
            "description": "Health check test",
        })
        db.add_health_check("TESTHC", {
            "success": True,
            "response_time_ms": 150,
            "timestamp": "2025-01-01T12:00:00Z",
        })

        response = client.get("/api/v1/registry/TESTHC")
        assert response.status_code == 200
        data = response.json()
        assert data["last_health_check"] is not None
        assert data["last_health_check"]["success"] is True

    def test_service_without_health_checks(self):
        """Service with no health checks has null last_health_check."""
        db = _db()
        db.upsert_service("TESTNOHC", {
            "description": "No health checks",
        })

        response = client.get("/api/v1/registry/TESTNOHC")
        assert response.status_code == 200
        data = response.json()
        assert data["last_health_check"] is None


class TestAdminCreateService:
    """Tests for the admin create service endpoint (POST /admin/services/new)."""

    def setup_method(self):
        _reset_db()
        from oslo_config import cfg
        cfg.CONF.set_override("admin_password", "testpass", group="registry")

    def teardown_method(self):
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

        svc = _db().get_service("FIND")
        assert svc is not None
        assert svc["description"] == "APRS station lookup service"

    def test_create_service_duplicate(self):
        """Creating a duplicate callsign returns an error."""
        _db().upsert_service("DUPE", {"description": "Existing"})

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
