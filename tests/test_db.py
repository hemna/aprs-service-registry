"""Tests for the SQLite-backed RegistryDB."""

import json
import tempfile
from pathlib import Path

import pytest

from aprs_service_registry.db import RegistryDB


@pytest.fixture
def db():
    """Create a fresh in-memory RegistryDB for each test."""
    return RegistryDB(":memory:")


@pytest.fixture
def db_file(tmp_path):
    """Create a file-backed RegistryDB for persistence tests."""
    return RegistryDB(str(tmp_path / "test.db"))


class TestServices:
    """Tests for service CRUD operations."""

    def test_upsert_creates_service(self, db):
        result = db.upsert_service("TEST1", {
            "description": "Test Service",
            "service_website": "https://test.com",
            "software": "test 1.0",
            "status": "active",
        })
        assert result["callsign"] == "TEST1"
        assert result["description"] == "Test Service"
        assert result["status"] == "active"

    def test_upsert_uppercases_callsign(self, db):
        db.upsert_service("test1", {"description": "Test"})
        svc = db.get_service("test1")
        assert svc["callsign"] == "TEST1"

    def test_upsert_updates_existing(self, db):
        db.upsert_service("TEST1", {"description": "Original"})
        db.upsert_service("TEST1", {"description": "Updated"})
        svc = db.get_service("TEST1")
        assert svc["description"] == "Updated"

    def test_get_service_not_found(self, db):
        assert db.get_service("NOPE") is None

    def test_get_all_services_empty(self, db):
        assert db.get_all_services() == []

    def test_get_all_services_with_filter(self, db):
        db.upsert_service("ACTIVE1", {"status": "active"})
        db.upsert_service("DOWN1", {"status": "down"})
        db.upsert_service("DEL1", {"status": "deleted"})

        active = db.get_all_services(status_filter={"active"})
        assert len(active) == 1
        assert active[0]["callsign"] == "ACTIVE1"

        visible = db.get_all_services(status_filter={"active", "down"})
        assert len(visible) == 2

    def test_delete_service_soft_deletes(self, db):
        db.upsert_service("TEST1", {"status": "active"})
        db.delete_service("TEST1")
        svc = db.get_service("TEST1")
        assert svc["status"] == "deleted"

    def test_update_service_status(self, db):
        db.upsert_service("TEST1", {"status": "active"})
        db.update_service_status("TEST1", "down")
        svc = db.get_service("TEST1")
        assert svc["status"] == "down"

    def test_featured_flag(self, db):
        db.upsert_service("TEST1", {"featured": True})
        svc = db.get_service("TEST1")
        assert svc["featured"] is True

    def test_set_featured(self, db):
        db.upsert_service("TEST1", {"featured": False})
        svc = db.get_service("TEST1")
        assert svc["featured"] is False

        db.set_featured("TEST1", True, actor=("admin", "WB4BOR"))
        svc = db.get_service("TEST1")
        assert svc["featured"] is True

        db.set_featured("TEST1", False, actor=("admin", "WB4BOR"))
        svc = db.get_service("TEST1")
        assert svc["featured"] is False

    def test_set_featured_nonexistent(self, db):
        # Should not raise on nonexistent callsign
        db.set_featured("NOPE", True)

    def test_toggle_featured(self, db):
        db.upsert_service("TEST1", {"featured": False})

        result = db.toggle_featured("TEST1", actor=("admin", "WB4BOR"))
        assert result is True
        svc = db.get_service("TEST1")
        assert svc["featured"] is True

        result = db.toggle_featured("TEST1", actor=("admin", "WB4BOR"))
        assert result is False
        svc = db.get_service("TEST1")
        assert svc["featured"] is False

    def test_toggle_featured_nonexistent(self, db):
        result = db.toggle_featured("NOPE")
        assert result is None

    def test_search_by_query(self, db):
        db.upsert_service("WXBOT", {"description": "Weather bot"})
        db.upsert_service("EMAIL-2", {"description": "Email gateway"})

        results = db.search_services(query="weather")
        assert len(results) == 1
        assert results[0]["callsign"] == "WXBOT"

    def test_search_by_status(self, db):
        db.upsert_service("A1", {"status": "active"})
        db.upsert_service("D1", {"status": "down"})

        results = db.search_services(status="down")
        assert len(results) == 1
        assert results[0]["callsign"] == "D1"

    def test_search_by_featured(self, db):
        db.upsert_service("F1", {"featured": True})
        db.upsert_service("N1", {"featured": False})

        results = db.search_services(featured=True)
        assert len(results) == 1
        assert results[0]["callsign"] == "F1"


class TestCommands:
    """Tests for service command operations."""

    def test_upsert_with_commands(self, db):
        db.upsert_service("TEST1", {
            "commands": [
                {"name": "ping", "description": "Health check"},
                {"name": "help", "description": "Show help"},
            ]
        })
        svc = db.get_service("TEST1")
        assert len(svc["commands"]) == 2
        names = [c["name"] for c in svc["commands"]]
        assert "help" in names
        assert "ping" in names

    def test_add_command(self, db):
        db.upsert_service("TEST1", {})
        db.add_command("TEST1", "ping", "Health check")
        cmds = db.get_commands("TEST1")
        assert len(cmds) == 1
        assert cmds[0]["name"] == "ping"

    def test_add_duplicate_command_ignored(self, db):
        db.upsert_service("TEST1", {})
        db.add_command("TEST1", "ping", "Health check")
        db.add_command("TEST1", "ping", "Different description")
        cmds = db.get_commands("TEST1")
        assert len(cmds) == 1

    def test_remove_command(self, db):
        db.upsert_service("TEST1", {
            "commands": [{"name": "ping", "description": "test"}]
        })
        db.remove_command("TEST1", "ping")
        assert db.get_commands("TEST1") == []


class TestHealthChecks:
    """Tests for health check operations."""

    def test_add_and_get_health_check(self, db):
        db.upsert_service("TEST1", {})
        db.add_health_check("TEST1", {
            "success": True,
            "response_time_ms": 150.5,
            "response_text": "pong",
        })
        results = db.get_health_checks("TEST1")
        assert len(results) == 1
        assert results[0]["success"] is True
        assert results[0]["response_time_ms"] == 150.5

    def test_get_last_health_check(self, db):
        db.upsert_service("TEST1", {})
        db.add_health_check("TEST1", {"success": True, "timestamp": "2025-01-01T00:00:00Z"})
        db.add_health_check("TEST1", {"success": False, "timestamp": "2025-01-02T00:00:00Z"})
        last = db.get_last_health_check("TEST1")
        assert last["success"] is False

    def test_get_last_health_check_none(self, db):
        db.upsert_service("TEST1", {})
        assert db.get_last_health_check("TEST1") is None

    def test_health_check_limit(self, db):
        db.upsert_service("TEST1", {})
        for i in range(100):
            db.add_health_check("TEST1", {
                "success": True,
                "timestamp": f"2025-01-{i+1:02d}T00:00:00Z",
            })
        results = db.get_health_checks("TEST1", limit=10)
        assert len(results) == 10


class TestCommandSubmissions:
    """Tests for command moderation workflow."""

    def test_submit_and_get_pending(self, db):
        db.upsert_service("TEST1", {})
        db.submit_command({
            "id": "sub-1",
            "callsign": "TEST1",
            "command_name": "weather",
            "command_description": "Get weather",
        })
        pending = db.get_pending_submissions()
        assert len(pending) == 1
        assert pending[0]["command_name"] == "weather"

    def test_approve_adds_command(self, db):
        db.upsert_service("TEST1", {})
        db.submit_command({
            "id": "sub-1",
            "callsign": "TEST1",
            "command_name": "weather",
            "command_description": "Get weather",
        })
        db.approve_submission("sub-1")

        # Command should now be on the service
        cmds = db.get_commands("TEST1")
        assert len(cmds) == 1
        assert cmds[0]["name"] == "weather"

        # Submission should no longer be pending
        pending = db.get_pending_submissions()
        assert len(pending) == 0

    def test_reject_removes_from_pending(self, db):
        db.upsert_service("TEST1", {})
        db.submit_command({
            "id": "sub-1",
            "callsign": "TEST1",
            "command_name": "spam",
            "command_description": "Spam command",
        })
        db.reject_submission("sub-1")
        pending = db.get_pending_submissions()
        assert len(pending) == 0

        # Submission record still exists with rejected status
        sub = db.get_submission("sub-1")
        assert sub["status"] == "rejected"

    def test_get_submission_not_found(self, db):
        assert db.get_submission("nonexistent") is None


class TestAudit:
    """Tests for audit trail."""

    def test_service_create_audited(self, db):
        db.upsert_service("TEST1", {}, actor=("admin", "WB4BOR"))
        log = db.get_audit_log(entity_type="service", entity_id="TEST1")
        assert len(log) == 1
        assert log[0]["action"] == "create"
        assert log[0]["actor_type"] == "admin"
        assert log[0]["actor_id"] == "WB4BOR"

    def test_service_update_audited(self, db):
        db.upsert_service("TEST1", {"description": "v1"})
        db.upsert_service("TEST1", {"description": "v2"}, actor=("admin", "K7TME"))
        log = db.get_audit_log(entity_type="service", entity_id="TEST1")
        assert len(log) == 2
        actions = {e["action"] for e in log}
        assert "create" in actions
        assert "update" in actions

    def test_status_change_audited(self, db):
        db.upsert_service("TEST1", {"status": "active"})
        db.update_service_status("TEST1", "down", actor=("scheduler", None))
        log = db.get_audit_log(entity_type="service", entity_id="TEST1")
        # create + status_change
        assert any(e["action"] == "status_change" for e in log)
        status_event = next(e for e in log if e["action"] == "status_change")
        assert status_event["details"]["old"] == "active"
        assert status_event["details"]["new"] == "down"


class TestMaintenance:
    """Tests for backup and retention."""

    def test_service_count(self, db):
        db.upsert_service("A1", {"status": "active"})
        db.upsert_service("A2", {"status": "active"})
        db.upsert_service("D1", {"status": "down"})

        counts = db.service_count()
        assert counts["active"] == 2
        assert counts["down"] == 1
        assert counts["total"] == 3

    def test_backup(self, db_file, tmp_path):
        db_file.upsert_service("TEST1", {"description": "Backup test"})
        backup_path = str(tmp_path / "backup.db")
        db_file.backup(backup_path)

        # Verify backup is readable
        backup_db = RegistryDB(backup_path)
        svc = backup_db.get_service("TEST1")
        assert svc["description"] == "Backup test"

    def test_export_json(self, db, tmp_path):
        db.upsert_service("TEST1", {"description": "Export test"})
        export_path = str(tmp_path / "export.json")
        db.export_json(export_path)

        data = json.loads(Path(export_path).read_text())
        assert len(data["services"]) == 1
        assert data["services"][0]["callsign"] == "TEST1"

    def test_retention(self, db):
        db.upsert_service("TEST1", {})
        # Add old health check
        db.add_health_check("TEST1", {
            "success": True,
            "timestamp": "2020-01-01T00:00:00Z",
        })
        # Add recent health check
        db.add_health_check("TEST1", {
            "success": True,
            "timestamp": "2099-01-01T00:00:00Z",
        })
        deleted = db.apply_retention(health_check_days=30)
        assert deleted == 1
        remaining = db.get_health_checks("TEST1")
        assert len(remaining) == 1
