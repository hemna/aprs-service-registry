"""Git-backed JSON storage for data persistence with version history.

This module provides a mixin class that stores data as JSON files in a git
repository, automatically committing changes for full version history.
Optionally pushes to a remote (e.g., GitHub) for offsite backup.
"""

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger
from oslo_config import cfg


CONF = cfg.CONF
LOG = logger


class GitStoreMixin:
    """Mixin for git-backed JSON storage with automatic versioning.

    Features:
    - Saves data as human-readable JSON (not pickle)
    - Commits every change with timestamp and description
    - Optional push to remote for offsite backup
    - Full version history with easy rollback

    Requirements for using class:
    - Must have self.data as a dictionary
    - Must have self.lock as a threading lock
    - Must define _git_filename() returning the JSON filename (without path)

    Configuration (in registry.conf):
    - git_backup_enabled: Enable git-backed storage
    - git_backup_path: Path to the git repository
    - git_backup_remote: Remote URL (e.g., GitHub) for push
    - git_backup_push_interval: Minutes between pushes (0 = every commit)
    """

    _last_push_time: datetime = None
    _pending_commits: int = 0

    def _git_filename(self) -> str:
        """Return the JSON filename for this store. Override in subclass."""
        return f"{self.__class__.__name__.lower()}.json"

    def _git_repo_path(self) -> Path:
        """Return the git repository path."""
        return Path(CONF.registry.git_backup_path)

    def _git_file_path(self) -> Path:
        """Return the full path to this store's JSON file."""
        return self._git_repo_path() / self._git_filename()

    def _run_git(self, *args, check: bool = True) -> subprocess.CompletedProcess:
        """Run a git command in the backup repository."""
        cmd = ["git", "-C", str(self._git_repo_path())] + list(args)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=check,
            )
            return result
        except subprocess.CalledProcessError as e:
            LOG.error(f"Git command failed: {' '.join(cmd)}")
            LOG.error(f"stderr: {e.stderr}")
            raise

    def _init_git_repo(self):
        """Initialize the git repository if it doesn't exist."""
        repo_path = self._git_repo_path()

        # Create directory if needed
        if not repo_path.exists():
            LOG.info(f"Creating git backup directory: {repo_path}")
            repo_path.mkdir(parents=True, exist_ok=True)

        # Initialize git repo if needed
        git_dir = repo_path / ".git"
        if not git_dir.exists():
            LOG.info(f"Initializing git repository: {repo_path}")
            self._run_git("init")
            self._run_git("config", "user.email", "aprs-registry@localhost")
            self._run_git("config", "user.name", "APRS Service Registry")

            # Add remote if configured
            remote_url = CONF.registry.git_backup_remote
            if remote_url:
                LOG.info(f"Adding git remote: {remote_url}")
                self._run_git("remote", "add", "origin", remote_url, check=False)

            # Create initial commit
            readme_path = repo_path / "README.md"
            readme_path.write_text(
                "# APRS Service Registry Backup\n\n"
                "This repository contains automated backups of the APRS Service Registry data.\n\n"
                "## Files\n\n"
                "- `services.json` - Registered APRS services\n"
                "- `healthchecks.json` - Health check history\n"
                "- `pending_commands.json` - Command suggestions awaiting moderation\n"
            )
            self._run_git("add", "README.md")
            self._run_git("commit", "-m", "Initial commit")

    def _git_commit(self, message: str):
        """Commit changes with the given message."""
        try:
            self._run_git("add", self._git_filename())
            # Check if there are changes to commit
            result = self._run_git("diff", "--cached", "--quiet", check=False)
            if result.returncode != 0:  # There are changes
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                full_message = f"[{timestamp}] {message}"
                self._run_git("commit", "-m", full_message)
                self._pending_commits += 1
                LOG.debug(f"Git commit: {message}")
                self._maybe_push()
        except Exception as e:
            LOG.error(f"Failed to commit: {e}")

    def _maybe_push(self):
        """Push to remote if configured and interval has elapsed."""
        remote_url = CONF.registry.git_backup_remote
        if not remote_url:
            return

        push_interval = CONF.registry.git_backup_push_interval
        now = datetime.now()

        # Push if: interval is 0 (every commit) or interval has elapsed
        should_push = False
        if push_interval == 0:
            should_push = True
        elif self._last_push_time is None:
            should_push = True
        else:
            elapsed = (now - self._last_push_time).total_seconds() / 60
            should_push = elapsed >= push_interval

        if should_push and self._pending_commits > 0:
            self._push_to_remote()

    def _push_to_remote(self):
        """Push commits to the remote repository."""
        try:
            LOG.info(f"Pushing {self._pending_commits} commits to remote...")
            self._run_git("push", "-u", "origin", "master", check=False)
            self._last_push_time = datetime.now()
            self._pending_commits = 0
            LOG.info("Push complete")
        except Exception as e:
            LOG.error(f"Failed to push to remote: {e}")

    def _serialize_for_json(self, obj: Any) -> Any:
        """Convert objects to JSON-serializable format. Override for custom types."""
        if hasattr(obj, "__dict__"):
            return obj.__dict__
        elif hasattr(obj, "to_dict"):
            return obj.to_dict()
        return str(obj)

    def _dump_json(self) -> str:
        """Dump data to JSON string."""
        with self.lock:
            return json.dumps(
                self.data,
                indent=2,
                sort_keys=True,
                default=self._serialize_for_json,
            )

    def _dump_json_unlocked(self) -> str:
        """Dump data to JSON string without acquiring lock."""
        return json.dumps(
            self.data,
            indent=2,
            sort_keys=True,
            default=self._serialize_for_json,
        )

    def git_save(self, commit_message: str = None):
        """Save data to JSON and commit to git."""
        if not CONF.registry.git_backup_enabled:
            return

        self._init_git_repo()

        # Write JSON file
        json_data = self._dump_json()
        self._git_file_path().write_text(json_data)

        # Commit
        message = commit_message or f"Update {self._git_filename()}"
        self._git_commit(message)

    def _git_save_unlocked(self, commit_message: str = None):
        """Save to git without acquiring lock. Caller must hold lock."""
        if not CONF.registry.git_backup_enabled:
            return

        self._init_git_repo()

        # Write JSON file
        json_data = self._dump_json_unlocked()
        self._git_file_path().write_text(json_data)

        # Commit
        message = commit_message or f"Update {self._git_filename()}"
        self._git_commit(message)

    def git_load(self) -> bool:
        """Load data from JSON file. Returns True if data was loaded."""
        if not CONF.registry.git_backup_enabled:
            return False

        json_path = self._git_file_path()
        if not json_path.exists():
            LOG.debug(f"No git backup file found: {json_path}")
            return False

        try:
            json_data = json_path.read_text()
            loaded = json.loads(json_data)
            if loaded:
                self.data = loaded
                LOG.info(
                    f"{self.__class__.__name__}: Loaded {len(self.data)} entries from git backup"
                )
                return True
        except json.JSONDecodeError as e:
            LOG.error(f"Failed to parse JSON from {json_path}: {e}")
        except Exception as e:
            LOG.error(f"Failed to load from {json_path}: {e}")

        return False

    def git_force_push(self):
        """Force an immediate push to remote, regardless of interval."""
        if CONF.registry.git_backup_enabled and CONF.registry.git_backup_remote:
            self._pending_commits = 1  # Ensure we push
            self._push_to_remote()

    def git_history(self, limit: int = 10) -> list[dict]:
        """Get recent commit history for this file."""
        if not CONF.registry.git_backup_enabled:
            return []

        try:
            result = self._run_git(
                "log",
                f"-{limit}",
                "--pretty=format:%H|%ai|%s",
                "--",
                self._git_filename(),
                check=False,
            )
            history = []
            for line in result.stdout.strip().split("\n"):
                if line:
                    parts = line.split("|", 2)
                    if len(parts) == 3:
                        history.append(
                            {
                                "commit": parts[0],
                                "date": parts[1],
                                "message": parts[2],
                            }
                        )
            return history
        except Exception as e:
            LOG.error(f"Failed to get git history: {e}")
            return []

    def git_restore(self, commit_hash: str) -> bool:
        """Restore data from a specific commit. Returns True on success."""
        if not CONF.registry.git_backup_enabled:
            return False

        try:
            # Get file content at specific commit
            result = self._run_git(
                "show",
                f"{commit_hash}:{self._git_filename()}",
            )
            loaded = json.loads(result.stdout)
            if loaded:
                with self.lock:
                    self.data = loaded
                # Save current state (creates new commit)
                self.git_save(f"Restored from commit {commit_hash[:8]}")
                LOG.info(
                    f"Restored {self._git_filename()} from commit {commit_hash[:8]}"
                )
                return True
        except Exception as e:
            LOG.error(f"Failed to restore from {commit_hash}: {e}")

        return False
