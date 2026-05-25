"""SQLite-backed repository for the APRS Service Registry.

Replaces pickle-based ObjectStoreMixin and git-backed GitStoreMixin with a
single SQLite database as the canonical data store.
"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from loguru import logger

LOG = logger

SCHEMA_VERSION = 1

SCHEMA_SQL = """
-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Core service registry
CREATE TABLE IF NOT EXISTS services (
    callsign TEXT PRIMARY KEY,
    description TEXT NOT NULL DEFAULT '',
    service_website TEXT NOT NULL DEFAULT '',
    software TEXT NOT NULL DEFAULT '',
    callsign_owner TEXT,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'pending', 'down', 'deleted')),
    health_check_command TEXT,
    featured INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_services_status ON services(status);
CREATE INDEX IF NOT EXISTS idx_services_owner ON services(callsign_owner);

-- Normalized commands per service
CREATE TABLE IF NOT EXISTS service_commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    callsign TEXT NOT NULL REFERENCES services(callsign) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(callsign, name)
);

CREATE INDEX IF NOT EXISTS idx_service_commands_callsign ON service_commands(callsign);

-- Full health check history
CREATE TABLE IF NOT EXISTS health_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    callsign TEXT NOT NULL REFERENCES services(callsign) ON DELETE CASCADE,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    success INTEGER NOT NULL,
    response_time_ms REAL,
    response_text TEXT,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_health_checks_callsign_ts ON health_checks(callsign, timestamp DESC);

-- Command submissions with full moderation history
CREATE TABLE IF NOT EXISTS command_submissions (
    id TEXT PRIMARY KEY,
    callsign TEXT NOT NULL,
    command_name TEXT NOT NULL,
    command_description TEXT NOT NULL DEFAULT '',
    submitted_at TEXT NOT NULL DEFAULT (datetime('now')),
    submitted_by TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected')),
    reviewed_at TEXT,
    FOREIGN KEY (callsign) REFERENCES services(callsign) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_command_submissions_status ON command_submissions(status);
CREATE INDEX IF NOT EXISTS idx_command_submissions_callsign ON command_submissions(callsign);

-- Audit trail for all mutations
CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    actor_type TEXT NOT NULL CHECK (actor_type IN ('api', 'admin', 'system', 'scheduler')),
    actor_id TEXT,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    action TEXT NOT NULL,
    details TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_events_entity ON audit_events(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_audit_events_ts ON audit_events(timestamp DESC);
"""


class RegistryDB:
    """SQLite-backed repository for all registry data.

    Uses a persistent connection with WAL mode for concurrent reads and
    serialized writes. Thread-safe via SQLite's internal locking plus
    busy_timeout.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._conn = self._create_connection()
        self._ensure_schema()

    def _create_connection(self) -> sqlite3.Connection:
        """Create and configure a SQLite connection."""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    @contextmanager
    def _connect(self):
        """Provide the connection within a transaction."""
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def _ensure_schema(self):
        """Create tables if they don't exist and apply migrations."""
        with self._connect() as conn:
            conn.executescript(SCHEMA_SQL)
            conn.commit()
            # Record schema version
            existing = conn.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()[0]
            if existing is None or existing < SCHEMA_VERSION:
                conn.execute(
                    "INSERT OR REPLACE INTO schema_migrations (version) VALUES (?)",
                    (SCHEMA_VERSION,),
                )
                conn.commit()
        LOG.debug(f"RegistryDB: schema at version {SCHEMA_VERSION}")

    def close(self):
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def _now(self) -> str:
        """Current UTC timestamp as ISO string."""
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _audit(self, conn, actor_type: str, actor_id: str | None,
               entity_type: str, entity_id: str, action: str,
               details: dict | None = None):
        """Record an audit event within the current transaction."""
        conn.execute(
            """INSERT INTO audit_events
               (timestamp, actor_type, actor_id, entity_type, entity_id, action, details)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                self._now(),
                actor_type,
                actor_id,
                entity_type,
                entity_id,
                action,
                json.dumps(details) if details else None,
            ),
        )

    # -------------------------------------------------------------------------
    # Services
    # -------------------------------------------------------------------------

    def get_service(self, callsign: str) -> dict | None:
        """Get a single service by callsign."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM services WHERE callsign = ?", (callsign.upper(),)
            ).fetchone()
            if row is None:
                return None
            service = dict(row)
            service["featured"] = bool(service["featured"])
            service["commands"] = self._get_commands_unlocked(conn, callsign.upper())
            self._parse_service_timestamps(service)
            return service

    def _parse_service_timestamps(self, service: dict):
        """Parse created_at and updated_at strings to datetime objects."""
        for field in ("created_at", "updated_at"):
            val = service.get(field)
            if val and isinstance(val, str):
                try:
                    service[field] = datetime.fromisoformat(
                        val.replace("Z", "+00:00")
                    )
                except (ValueError, TypeError):
                    pass

    def get_all_services(self, status_filter: set | None = None) -> list[dict]:
        """Get all services, optionally filtered by status."""
        with self._connect() as conn:
            if status_filter:
                placeholders = ",".join("?" for _ in status_filter)
                rows = conn.execute(
                    f"SELECT * FROM services WHERE status IN ({placeholders}) ORDER BY callsign",
                    tuple(status_filter),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM services ORDER BY callsign"
                ).fetchall()

            services = []
            for row in rows:
                service = dict(row)
                service["featured"] = bool(service["featured"])
                service["commands"] = self._get_commands_unlocked(
                    conn, service["callsign"]
                )
                self._parse_service_timestamps(service)
                services.append(service)
            return services

    def upsert_service(self, callsign: str, data: dict,
                       actor: tuple[str, str | None] = ("system", None)) -> dict:
        """Create or update a service. Returns the service dict."""
        callsign_upper = callsign.upper()
        now = self._now()

        with self._connect() as conn:
            existing = conn.execute(
                "SELECT * FROM services WHERE callsign = ?", (callsign_upper,)
            ).fetchone()

            if existing:
                # Update
                conn.execute(
                    """UPDATE services SET
                       description = ?, service_website = ?, software = ?,
                       callsign_owner = ?, status = ?, health_check_command = ?,
                       featured = ?, updated_at = ?
                       WHERE callsign = ?""",
                    (
                        data.get("description", ""),
                        data.get("service_website", ""),
                        data.get("software", ""),
                        data.get("callsign_owner"),
                        data.get("status", "active"),
                        data.get("health_check_command"),
                        int(data.get("featured", False)),
                        now,
                        callsign_upper,
                    ),
                )
                self._audit(
                    conn, actor[0], actor[1], "service", callsign_upper,
                    "update", {"fields": list(data.keys())}
                )
            else:
                # Insert
                conn.execute(
                    """INSERT INTO services
                       (callsign, description, service_website, software,
                        callsign_owner, status, health_check_command, featured,
                        created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        callsign_upper,
                        data.get("description", ""),
                        data.get("service_website", ""),
                        data.get("software", ""),
                        data.get("callsign_owner"),
                        data.get("status", "active"),
                        data.get("health_check_command"),
                        int(data.get("featured", False)),
                        now,
                        now,
                    ),
                )
                self._audit(
                    conn, actor[0], actor[1], "service", callsign_upper, "create"
                )

            # Sync commands if provided
            commands = data.get("commands")
            if commands is not None:
                self._sync_commands_unlocked(conn, callsign_upper, commands)

        return self.get_service(callsign_upper)

    def update_service_status(self, callsign: str, new_status: str,
                              actor: tuple[str, str | None] = ("system", None)):
        """Update only the status of a service."""
        callsign_upper = callsign.upper()
        with self._connect() as conn:
            old = conn.execute(
                "SELECT status FROM services WHERE callsign = ?", (callsign_upper,)
            ).fetchone()
            if old is None:
                return
            conn.execute(
                "UPDATE services SET status = ?, updated_at = ? WHERE callsign = ?",
                (new_status, self._now(), callsign_upper),
            )
            self._audit(
                conn, actor[0], actor[1], "service", callsign_upper,
                "status_change", {"old": old["status"], "new": new_status}
            )

    def delete_service(self, callsign: str,
                       actor: tuple[str, str | None] = ("admin", None)):
        """Soft delete a service (set status to 'deleted')."""
        self.update_service_status(callsign, "deleted", actor)

    def search_services(self, query: str = None, status: str = None,
                        owner: str = None, featured: bool = None) -> list[dict]:
        """Search services with optional filters."""
        conditions = []
        params = []

        if query:
            conditions.append(
                "(callsign LIKE ? OR description LIKE ? OR software LIKE ?)"
            )
            like = f"%{query}%"
            params.extend([like, like, like])
        if status:
            conditions.append("status = ?")
            params.append(status)
        if owner:
            conditions.append("callsign_owner = ?")
            params.append(owner.upper())
        if featured is not None:
            conditions.append("featured = ?")
            params.append(int(featured))

        where = " AND ".join(conditions) if conditions else "1=1"

        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM services WHERE {where} ORDER BY callsign", params
            ).fetchall()
            services = []
            for row in rows:
                service = dict(row)
                service["featured"] = bool(service["featured"])
                service["commands"] = self._get_commands_unlocked(
                    conn, service["callsign"]
                )
                self._parse_service_timestamps(service)
                services.append(service)
            return services

    # -------------------------------------------------------------------------
    # Commands
    # -------------------------------------------------------------------------

    def _get_commands_unlocked(self, conn, callsign: str) -> list[dict]:
        """Get commands for a service (caller manages connection)."""
        rows = conn.execute(
            "SELECT name, description FROM service_commands WHERE callsign = ? ORDER BY name",
            (callsign.upper(),),
        ).fetchall()
        return [dict(r) for r in rows]

    def _sync_commands_unlocked(self, conn, callsign: str, commands: list[dict]):
        """Replace all commands for a service."""
        conn.execute(
            "DELETE FROM service_commands WHERE callsign = ?", (callsign,)
        )
        for cmd in commands:
            if cmd.get("name"):
                conn.execute(
                    """INSERT OR IGNORE INTO service_commands (callsign, name, description)
                       VALUES (?, ?, ?)""",
                    (callsign, cmd["name"], cmd.get("description", "")),
                )

    def get_commands(self, callsign: str) -> list[dict]:
        """Get all commands for a service."""
        with self._connect() as conn:
            return self._get_commands_unlocked(conn, callsign.upper())

    def add_command(self, callsign: str, name: str, description: str,
                    actor: tuple[str, str | None] = ("admin", None)):
        """Add a single command to a service."""
        callsign_upper = callsign.upper()
        with self._connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO service_commands (callsign, name, description)
                   VALUES (?, ?, ?)""",
                (callsign_upper, name, description),
            )
            self._audit(
                conn, actor[0], actor[1], "command", f"{callsign_upper}/{name}", "add"
            )

    def remove_command(self, callsign: str, name: str,
                       actor: tuple[str, str | None] = ("admin", None)):
        """Remove a command from a service."""
        callsign_upper = callsign.upper()
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM service_commands WHERE callsign = ? AND LOWER(name) = LOWER(?)",
                (callsign_upper, name),
            )
            self._audit(
                conn, actor[0], actor[1], "command", f"{callsign_upper}/{name}", "remove"
            )

    # -------------------------------------------------------------------------
    # Health Checks
    # -------------------------------------------------------------------------

    def add_health_check(self, callsign: str, result: dict):
        """Record a health check result."""
        callsign_upper = callsign.upper()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO health_checks
                   (callsign, timestamp, success, response_time_ms, response_text, error)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    callsign_upper,
                    result.get("timestamp", self._now()),
                    int(result.get("success", False)),
                    result.get("response_time_ms"),
                    result.get("response_text"),
                    result.get("error"),
                ),
            )

    def get_health_checks(self, callsign: str, limit: int = 50) -> list[dict]:
        """Get health check history for a service (most recent first)."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM health_checks
                   WHERE callsign = ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (callsign.upper(), limit),
            ).fetchall()
            results = []
            for row in rows:
                r = dict(row)
                r["success"] = bool(r["success"])
                # Parse timestamp to datetime for template compatibility
                if r.get("timestamp") and isinstance(r["timestamp"], str):
                    try:
                        r["timestamp"] = datetime.fromisoformat(
                            r["timestamp"].replace("Z", "+00:00")
                        )
                    except (ValueError, TypeError):
                        pass
                results.append(r)
            return results

    def get_last_health_check(self, callsign: str) -> dict | None:
        """Get the most recent health check for a service."""
        results = self.get_health_checks(callsign, limit=1)
        return results[0] if results else None

    # -------------------------------------------------------------------------
    # Command Submissions
    # -------------------------------------------------------------------------

    def submit_command(self, submission: dict) -> str:
        """Add a command submission for moderation. Returns the submission ID."""
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO command_submissions
                   (id, callsign, command_name, command_description,
                    submitted_at, submitted_by, status)
                   VALUES (?, ?, ?, ?, ?, ?, 'pending')""",
                (
                    submission["id"],
                    submission["callsign"].upper(),
                    submission["command_name"],
                    submission.get("command_description", ""),
                    submission.get("submitted_at", self._now()),
                    submission.get("submitted_by"),
                ),
            )
            self._audit(
                conn, "api", submission.get("submitted_by"),
                "submission", submission["id"], "submit"
            )
        return submission["id"]

    def get_pending_submissions(self) -> list[dict]:
        """Get all pending command submissions."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM command_submissions
                   WHERE status = 'pending'
                   ORDER BY submitted_at DESC"""
            ).fetchall()
            return [dict(r) for r in rows]

    def get_submission(self, id: str) -> dict | None:
        """Get a single submission by ID."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM command_submissions WHERE id = ?", (id,)
            ).fetchone()
            return dict(row) if row else None

    def approve_submission(self, id: str,
                           actor: tuple[str, str | None] = ("admin", None)):
        """Approve a submission and add the command to the service."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM command_submissions WHERE id = ?", (id,)
            ).fetchone()
            if row is None:
                return

            submission = dict(row)
            conn.execute(
                """UPDATE command_submissions
                   SET status = 'approved', reviewed_at = ?
                   WHERE id = ?""",
                (self._now(), id),
            )
            # Add command to service
            conn.execute(
                """INSERT OR IGNORE INTO service_commands (callsign, name, description)
                   VALUES (?, ?, ?)""",
                (
                    submission["callsign"],
                    submission["command_name"],
                    submission["command_description"],
                ),
            )
            self._audit(
                conn, actor[0], actor[1], "submission", id, "approve"
            )

    def reject_submission(self, id: str,
                          actor: tuple[str, str | None] = ("admin", None)):
        """Reject a command submission."""
        with self._connect() as conn:
            conn.execute(
                """UPDATE command_submissions
                   SET status = 'rejected', reviewed_at = ?
                   WHERE id = ?""",
                (self._now(), id),
            )
            self._audit(
                conn, actor[0], actor[1], "submission", id, "reject"
            )

    # -------------------------------------------------------------------------
    # Audit
    # -------------------------------------------------------------------------

    def get_audit_log(self, entity_type: str = None, entity_id: str = None,
                      limit: int = 100) -> list[dict]:
        """Get audit events, optionally filtered."""
        conditions = []
        params = []
        if entity_type:
            conditions.append("entity_type = ?")
            params.append(entity_type)
        if entity_id:
            conditions.append("entity_id = ?")
            params.append(entity_id)

        where = " AND ".join(conditions) if conditions else "1=1"
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(
                f"""SELECT * FROM audit_events
                    WHERE {where}
                    ORDER BY timestamp DESC LIMIT ?""",
                params,
            ).fetchall()
            results = []
            for row in rows:
                r = dict(row)
                if r.get("details"):
                    try:
                        r["details"] = json.loads(r["details"])
                    except (json.JSONDecodeError, TypeError):
                        pass
                results.append(r)
            return results

    # -------------------------------------------------------------------------
    # Maintenance
    # -------------------------------------------------------------------------

    def apply_retention(self, health_check_days: int = 90) -> int:
        """Delete health check records older than the specified retention period."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=health_check_days)
        cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM health_checks WHERE timestamp < ?", (cutoff_str,)
            )
            deleted = cursor.rowcount
            if deleted > 0:
                LOG.info(
                    f"Retention: deleted {deleted} health check records older than {health_check_days} days"
                )
            return deleted

    def backup(self, dest_path: str):
        """Create a backup copy of the database."""
        import shutil
        # Use SQLite backup API via a connection
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            backup_conn = sqlite3.connect(str(dest))
            conn.backup(backup_conn)
            backup_conn.close()
        LOG.info(f"Database backed up to {dest_path}")

    def export_json(self, dest_path: str):
        """Export all data as JSON for inspection."""
        data = {
            "exported_at": self._now(),
            "services": self.get_all_services(),
            "pending_submissions": self.get_pending_submissions(),
        }
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(data, indent=2, default=str))
        LOG.info(f"Data exported to {dest_path}")

    def service_count(self) -> dict:
        """Get counts by status."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM services GROUP BY status"
            ).fetchall()
            counts = {row["status"]: row["cnt"] for row in rows}
            counts["total"] = sum(counts.values())
            return counts
