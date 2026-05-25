# SQLite Migration Design

## Summary

Replace pickle-based persistence and git-backed JSON backup with a single SQLite database as the only storage backend for the APRS Service Registry.

## Goals (ordered by priority)

1. **Inspection and auditing** - Human-readable, queryable data with standard tools.
2. **Feature growth** - Search, filter, history views, moderation audit trails.
3. **Reliability** - Eliminate multi-process state drift (memory vs. disk overwrite on shutdown).

## Current State

The app uses three singleton stores (`APRSServices`, `HealthCheckStore`, `PendingCommandStore`) that inherit from `ObjectStoreMixin` and `GitStoreMixin`:

- **Runtime**: In-memory Python dicts as the canonical state.
- **Persistence**: Pickle files written on every mutating operation and on shutdown.
- **Optional backup**: Git-backed JSON with commit history and optional remote push.
- **Startup**: Loads from git JSON first, falls back to pickle.
- **Shutdown**: Saves in-memory state to both pickle and git.

### Problems

- Separate processes (e.g., `seed` CLI) write pickle but cannot update the web process's in-memory state.
- On shutdown, the web process overwrites the pickle with its stale in-memory state.
- Pickle files are opaque - not inspectable, not queryable, not diffable.
- Health check history is truncated to 24 results in memory.
- Command moderation history is deleted on approve/reject - no audit trail.

## Design

### Storage Layer

- **One SQLite file** at the configured `save_location` (e.g., `/config/registry.db`).
- **No in-memory canonical state.** Every read queries the DB; every write is a DB transaction.
- **Remove** `ObjectStoreMixin`, `GitStoreMixin`, and all pickle/git code from the runtime path.
- **Use stdlib `sqlite3`** with short-lived connections per operation.

### Connection Settings

```python
connection.execute("PRAGMA journal_mode=WAL")
connection.execute("PRAGMA foreign_keys=ON")
connection.execute("PRAGMA busy_timeout=5000")
```

WAL enables concurrent readers with a single writer. `busy_timeout` prevents immediate failures if the scheduler thread and a web request write simultaneously.

### Schema

```sql
-- Schema version tracking
CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Core service registry
CREATE TABLE services (
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

CREATE INDEX idx_services_status ON services(status);
CREATE INDEX idx_services_owner ON services(callsign_owner);

-- Normalized commands per service
CREATE TABLE service_commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    callsign TEXT NOT NULL REFERENCES services(callsign) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(callsign, name)
);

CREATE INDEX idx_service_commands_callsign ON service_commands(callsign);

-- Full health check history with retention policy
CREATE TABLE health_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    callsign TEXT NOT NULL REFERENCES services(callsign) ON DELETE CASCADE,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    success INTEGER NOT NULL,
    response_time_ms REAL,
    response_text TEXT,
    error TEXT
);

CREATE INDEX idx_health_checks_callsign_ts ON health_checks(callsign, timestamp DESC);

-- Command submissions with full moderation history
CREATE TABLE command_submissions (
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

CREATE INDEX idx_command_submissions_status ON command_submissions(status);
CREATE INDEX idx_command_submissions_callsign ON command_submissions(callsign);

-- Audit trail for all mutations
CREATE TABLE audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    actor_type TEXT NOT NULL CHECK (actor_type IN ('api', 'admin', 'system', 'scheduler')),
    actor_id TEXT,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    action TEXT NOT NULL,
    details TEXT  -- JSON payload with before/after or context
);

CREATE INDEX idx_audit_events_entity ON audit_events(entity_type, entity_id);
CREATE INDEX idx_audit_events_ts ON audit_events(timestamp DESC);
```

### Repository Layer

A new module `aprs_service_registry/db.py` provides:

```python
class RegistryDB:
    """SQLite-backed repository for all registry data."""

    def __init__(self, db_path: str):
        ...

    # --- Services ---
    def get_service(self, callsign: str) -> dict | None
    def get_all_services(self, status_filter: set | None = None) -> list[dict]
    def upsert_service(self, callsign: str, data: dict, actor: tuple) -> dict
    def delete_service(self, callsign: str, actor: tuple) -> None
    def search_services(self, query: str = None, status: str = None,
                        owner: str = None, featured: bool = None) -> list[dict]

    # --- Commands ---
    def get_commands(self, callsign: str) -> list[dict]
    def add_command(self, callsign: str, name: str, description: str, actor: tuple) -> None
    def remove_command(self, callsign: str, name: str, actor: tuple) -> None

    # --- Health Checks ---
    def add_health_check(self, callsign: str, result: dict) -> None
    def get_health_checks(self, callsign: str, limit: int = 50) -> list[dict]
    def get_last_health_check(self, callsign: str) -> dict | None

    # --- Command Submissions ---
    def submit_command(self, submission: dict) -> str
    def approve_submission(self, id: str, actor: tuple) -> None
    def reject_submission(self, id: str, actor: tuple) -> None
    def get_pending_submissions(self) -> list[dict]

    # --- Audit ---
    def get_audit_log(self, entity_type: str = None, entity_id: str = None,
                      limit: int = 100) -> list[dict]

    # --- Maintenance ---
    def apply_retention(self, health_check_days: int = 90) -> int
    def backup(self, dest_path: str) -> None
    def export_json(self, dest_path: str) -> None
```

Every method opens a connection, executes within a transaction, and closes. No shared connection objects across threads.

### Integration with FastAPI

- A single `RegistryDB` instance is created at app startup and passed via `app.state.db`.
- Route handlers access it as `request.app.state.db`.
- The health check scheduler thread uses its own calls (safe due to WAL + per-operation connections).
- Remove the `lifespan` shutdown save logic entirely.

### What Gets Removed

| File/Code | Action |
|-----------|--------|
| `objectstore.py` | Delete entirely |
| `gitstore.py` | Delete entirely |
| `APRSServices` singleton class | Replace with `RegistryDB` methods |
| `HealthCheckStore` singleton class | Replace with `RegistryDB` methods |
| `PendingCommandStore` singleton class | Replace with `RegistryDB` methods |
| Pickle load/save in lifespan | Remove |
| Git save/push in lifespan | Remove |
| `_serialize_for_json` methods | Remove |
| `wrapt.synchronized` locking | Remove (SQLite handles concurrency) |
| Config: `git_backup_*` options | Remove |
| Config: `enable_save` | Replace with DB path config |

### What Gets Added

| Component | Purpose |
|-----------|---------|
| `db.py` | SQLite repository layer |
| `migrations/` | SQL schema files, versioned |
| CLI: `migrate-to-sqlite` | One-time import from pickle |
| CLI: `db-backup` | Create a backup copy of the DB |
| CLI: `db-export-json` | Export current state as JSON |
| Config: `db_path` | Path to SQLite file |

## Migration Strategy

### Phase 1: Add DB layer alongside existing stores

1. Create `db.py` with full schema and repository methods.
2. Add `migrate-to-sqlite` CLI command that reads existing pickle files and populates the SQLite DB.
3. Add config option `db_path`.

### Phase 2: Cut over reads and writes

4. Update all route handlers and the health check scheduler to use `RegistryDB` instead of singleton stores.
5. Remove in-memory singleton stores from `main.py`.
6. Remove lifespan save/load logic.

### Phase 3: Remove legacy code

7. Delete `objectstore.py` and `gitstore.py`.
8. Remove pickle/git config options.
9. Update tests to use SQLite (in-memory `:memory:` for fast tests).
10. Update Dockerfile and production config.

### Rollback

If issues arise after cutover:
- The pickle files are preserved (migration does not delete them).
- Revert to the previous Docker image.
- The migration command is idempotent and can be re-run.

## Deployment

1. Deploy new image with both old stores and new DB layer.
2. Run `migrate-to-sqlite` inside the container.
3. Restart with the new code that reads from SQLite.
4. Verify via API and admin UI.
5. On next deploy, remove legacy code entirely.

## Operational Model

- **Backups**: `aprs-service-registry db-backup --dest /backups/registry-$(date +%F).db`
- **Inspection**: `sqlite3 /config/registry.db ".mode column" "SELECT * FROM services"`
- **Retention**: Scheduled or manual `aprs-service-registry db-retention --days 90`
- **No git, no pickle, no hidden persistence side effects.**

## Testing

- All existing API tests continue to pass against an in-memory SQLite DB.
- Add migration tests that import known pickle fixtures and verify record counts.
- Add concurrency tests for simultaneous health-check writes and API reads.
- Add audit event verification in admin action tests.

## Decision Log

| Decision | Rationale |
|----------|-----------|
| SQLite over Postgres | No separate DB server needed; fits single-host Docker deployment |
| stdlib sqlite3 over SQLAlchemy | Less abstraction, full SQL control, smaller dependency surface |
| Remove git JSON entirely | SQLite is inspectable and exportable; git adds complexity without value |
| Remove pickle entirely | One-time migration, then gone |
| Full health check history | Retention policy is cleaner than in-memory truncation |
| Keep command submission history | Audit trail for moderation decisions |
| Per-operation connections | Thread-safe without shared state or connection pools |
