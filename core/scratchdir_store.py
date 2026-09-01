"""Durable metadata for relay-backed ScratchDir roots."""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
import uuid

import core.paths as _paths
from core.scratchdir_models import (
    ScratchDirError,
    ScratchDirRecord,
    ScratchDirState,
    require_scope,
    validate_quotas,
    validate_ttl,
)

_CORRUPTION_MARKERS = (
    "unsupported file format",
    "file is not a database",
    "database disk image is malformed",
    "malformed database schema",
    "quick_check failed",
)

logger = logging.getLogger(__name__)


class ScratchDirStore:
    """SQLite state scoped by user, conversation, agent, and relay."""

    _instance: ScratchDirStore | None = None
    _instance_lock = threading.Lock()

    @classmethod
    def instance(cls) -> ScratchDirStore:
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        self._lock = threading.RLock()
        try:
            self._initialize()
        except sqlite3.DatabaseError as exc:
            if not self._is_corruption(exc):
                raise
            quarantined = self._quarantine_corrupt_database()
            logger.critical(
                "quarantined corrupt ephemeral ScratchDir metadata at %s; "
                "a fresh store will be created. reason=%s: %s",
                quarantined, type(exc).__name__, exc,
            )
            self._initialize()

    @property
    def _database_path(self):
        return _paths.SCRATCHDIRS_DIR / "scratchdirs.sqlite3"

    def _connect(self) -> sqlite3.Connection:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("PRAGMA cell_size_check = ON")
        return connection

    @staticmethod
    def _is_corruption(exc: sqlite3.DatabaseError) -> bool:
        lowered = str(exc).lower()
        return any(marker in lowered for marker in _CORRUPTION_MARKERS)

    def _quarantine_corrupt_database(self):
        database = self._database_path
        suffix = f".corrupt-{time.time_ns()}"
        quarantined = database.with_name(database.name + suffix)
        for artifact in (
            database.with_name(database.name + "-wal"),
            database.with_name(database.name + "-shm"),
            database,
        ):
            if not artifact.exists():
                continue
            target = artifact.with_name(artifact.name + suffix)
            artifact.rename(target)
            if artifact == database:
                quarantined = target
        return quarantined

    def _preflight(self) -> None:
        database = self._database_path
        if not database.exists():
            return
        connection = sqlite3.connect(
            database.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
        try:
            self._quick_check(connection)
        finally:
            connection.close()

    @staticmethod
    def _quick_check(connection: sqlite3.Connection) -> None:
        results = [
            str(row[0])
            for row in connection.execute("PRAGMA quick_check").fetchall()
        ]
        if results != ["ok"]:
            raise sqlite3.DatabaseError(
                "ScratchDir store quick_check failed: "
                + "; ".join(results[:10]))

    def _initialize(self) -> None:
        self._preflight()
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS scratchdirs (
                    user_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    relay_id TEXT NOT NULL,
                    id TEXT NOT NULL UNIQUE,
                    locator TEXT NOT NULL,
                    state TEXT NOT NULL,
                    epoch INTEGER NOT NULL,
                    revision INTEGER NOT NULL,
                    quota_bytes INTEGER NOT NULL,
                    quota_files INTEGER NOT NULL,
                    observed_bytes INTEGER NOT NULL,
                    observed_files INTEGER NOT NULL,
                    operation_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    cleared_at REAL NOT NULL,
                    reconciled_at REAL NOT NULL,
                    PRIMARY KEY (
                        user_id, conversation_id, agent_name, relay_id)
                );
                CREATE INDEX IF NOT EXISTS idx_scratchdirs_expiry
                    ON scratchdirs (state, expires_at);
                CREATE INDEX IF NOT EXISTS idx_scratchdirs_id
                    ON scratchdirs (id);
                """
            )
            self._quick_check(connection)

    @staticmethod
    def _record(row: sqlite3.Row) -> ScratchDirRecord:
        return ScratchDirRecord(**dict(row))

    @staticmethod
    def _expire(connection: sqlite3.Connection, now: float) -> None:
        connection.execute(
            """UPDATE scratchdirs
               SET state = ?, revision = revision + 1, updated_at = ?
               WHERE state = ? AND expires_at <= ?""",
            (ScratchDirState.EXPIRED.value, now,
             ScratchDirState.ACTIVE.value, now),
        )

    def get(self, user_id: str, conversation_id: str, agent_name: str,
            relay_id: str, *, now: float | None = None
            ) -> ScratchDirRecord | None:
        scope = require_scope(user_id, conversation_id, agent_name, relay_id)
        current = time.time() if now is None else float(now)
        with self._lock, self._connect() as connection:
            self._expire(connection, current)
            row = connection.execute(
                """SELECT * FROM scratchdirs
                   WHERE user_id = ? AND conversation_id = ?
                     AND agent_name = ? AND relay_id = ?""",
                scope,
            ).fetchone()
        return self._record(row) if row is not None else None

    def activate(self, user_id: str, conversation_id: str, agent_name: str,
                 relay_id: str, *, locator: str, ttl_hours=None,
                 quota_bytes=None, quota_files=None, operation_id: str,
                 scratch_id: str = "",
                 now: float | None = None) -> ScratchDirRecord:
        scope = require_scope(user_id, conversation_id, agent_name, relay_id)
        locator = str(locator or "").strip()
        operation_id = str(operation_id or "").strip()
        if not locator:
            raise ScratchDirError(
                "scratchdir_locator_missing",
                "relay returned an empty ScratchDir locator")
        if not operation_id:
            raise ScratchDirError(
                "scratchdir_operation_missing", "operation_id is required")
        ttl = validate_ttl(ttl_hours)
        byte_limit, file_limit = validate_quotas(quota_bytes, quota_files)
        current = time.time() if now is None else float(now)
        with self._lock, self._connect() as connection:
            self._expire(connection, current)
            row = connection.execute(
                """SELECT * FROM scratchdirs
                   WHERE user_id = ? AND conversation_id = ?
                     AND agent_name = ? AND relay_id = ?""",
                scope,
            ).fetchone()
            if row is not None:
                existing = self._record(row)
                if (existing.state == ScratchDirState.ACTIVE.value
                        and existing.operation_id == operation_id):
                    return existing
                if existing.state == ScratchDirState.ACTIVE.value:
                    return existing
                scratch_id = existing.id
                epoch = existing.epoch + 1
                revision = existing.revision + 1
                created_at = existing.created_at
            else:
                scratch_id = str(scratch_id or f"sd_{uuid.uuid4().hex}")
                epoch = 1
                revision = 1
                created_at = current
            connection.execute(
                """INSERT INTO scratchdirs (
                       user_id, conversation_id, agent_name, relay_id, id,
                       locator, state, epoch, revision, quota_bytes, quota_files,
                       observed_bytes, observed_files, operation_id, created_at,
                       updated_at, expires_at, cleared_at, reconciled_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?, 0, 0)
                   ON CONFLICT(user_id, conversation_id, agent_name, relay_id)
                   DO UPDATE SET
                       locator = excluded.locator,
                       state = excluded.state,
                       epoch = excluded.epoch,
                       revision = excluded.revision,
                       quota_bytes = excluded.quota_bytes,
                       quota_files = excluded.quota_files,
                       observed_bytes = 0,
                       observed_files = 0,
                       operation_id = excluded.operation_id,
                       updated_at = excluded.updated_at,
                       expires_at = excluded.expires_at,
                       cleared_at = 0,
                       reconciled_at = 0""",
                (*scope, scratch_id, locator, ScratchDirState.ACTIVE.value,
                 epoch, revision, byte_limit, file_limit, operation_id,
                 created_at, current, current + ttl * 3600),
            )
            row = connection.execute(
                "SELECT * FROM scratchdirs WHERE id = ?", (scratch_id,)
            ).fetchone()
        return self._record(row)

    def renew(self, user_id: str, conversation_id: str, agent_name: str,
              relay_id: str, *, ttl_hours=None,
              now: float | None = None) -> ScratchDirRecord:
        scope = require_scope(user_id, conversation_id, agent_name, relay_id)
        ttl = validate_ttl(ttl_hours)
        current = time.time() if now is None else float(now)
        with self._lock, self._connect() as connection:
            self._expire(connection, current)
            cursor = connection.execute(
                """UPDATE scratchdirs
                   SET expires_at = ?, updated_at = ?, revision = revision + 1
                   WHERE user_id = ? AND conversation_id = ?
                     AND agent_name = ? AND relay_id = ? AND state = ?""",
                (current + ttl * 3600, current, *scope,
                 ScratchDirState.ACTIVE.value),
            )
            if cursor.rowcount != 1:
                raise ScratchDirError(
                    "scratchdir_not_active",
                    "ScratchDir is not active; call ensure first")
            row = connection.execute(
                """SELECT * FROM scratchdirs
                   WHERE user_id = ? AND conversation_id = ?
                     AND agent_name = ? AND relay_id = ?""",
                scope,
            ).fetchone()
        return self._record(row)

    def update_usage(self, user_id: str, conversation_id: str, agent_name: str,
                     relay_id: str, *, observed_bytes: int,
                     observed_files: int, reconciled_at: float | None = None
                     ) -> ScratchDirRecord:
        scope = require_scope(user_id, conversation_id, agent_name, relay_id)
        byte_count, file_count = int(observed_bytes), int(observed_files)
        if byte_count < 0 or file_count < 0:
            raise ScratchDirError(
                "scratchdir_usage_invalid", "ScratchDir usage cannot be negative")
        current = time.time() if reconciled_at is None else float(reconciled_at)
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """UPDATE scratchdirs
                   SET observed_bytes = ?, observed_files = ?,
                       reconciled_at = ?, updated_at = ?,
                       revision = revision + 1
                   WHERE user_id = ? AND conversation_id = ?
                     AND agent_name = ? AND relay_id = ?""",
                (byte_count, file_count, current, current, *scope),
            )
            if cursor.rowcount != 1:
                raise ScratchDirError(
                    "scratchdir_not_found", "ScratchDir is not registered")
            row = connection.execute(
                """SELECT * FROM scratchdirs
                   WHERE user_id = ? AND conversation_id = ?
                     AND agent_name = ? AND relay_id = ?""",
                scope,
            ).fetchone()
        return self._record(row)

    def begin_clear(self, user_id: str, conversation_id: str, agent_name: str,
                    relay_id: str, *, operation_id: str,
                    now: float | None = None) -> ScratchDirRecord:
        scope = require_scope(user_id, conversation_id, agent_name, relay_id)
        operation_id = str(operation_id or "").strip()
        if not operation_id:
            raise ScratchDirError(
                "scratchdir_operation_missing", "operation_id is required")
        current = time.time() if now is None else float(now)
        with self._lock, self._connect() as connection:
            self._expire(connection, current)
            row = connection.execute(
                """SELECT * FROM scratchdirs
                   WHERE user_id = ? AND conversation_id = ?
                     AND agent_name = ? AND relay_id = ?""",
                scope,
            ).fetchone()
            if row is None:
                raise ScratchDirError(
                    "scratchdir_not_found", "ScratchDir is not registered")
            existing = self._record(row)
            if existing.state == ScratchDirState.CLEARED.value:
                return existing
            if (existing.state == ScratchDirState.CLEARING.value
                    and existing.operation_id == operation_id):
                return existing
            connection.execute(
                """UPDATE scratchdirs
                   SET state = ?, operation_id = ?, epoch = epoch + 1,
                       revision = revision + 1, updated_at = ?
                   WHERE user_id = ? AND conversation_id = ?
                     AND agent_name = ? AND relay_id = ?""",
                (ScratchDirState.CLEARING.value, operation_id, current, *scope),
            )
            row = connection.execute(
                """SELECT * FROM scratchdirs
                   WHERE user_id = ? AND conversation_id = ?
                     AND agent_name = ? AND relay_id = ?""",
                scope,
            ).fetchone()
        return self._record(row)

    def finish_clear(self, user_id: str, conversation_id: str, agent_name: str,
                     relay_id: str, *, operation_id: str,
                     now: float | None = None) -> ScratchDirRecord:
        scope = require_scope(user_id, conversation_id, agent_name, relay_id)
        operation_id = str(operation_id or "").strip()
        current = time.time() if now is None else float(now)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM scratchdirs
                   WHERE user_id = ? AND conversation_id = ?
                     AND agent_name = ? AND relay_id = ?""",
                scope,
            ).fetchone()
            if row is None:
                raise ScratchDirError(
                    "scratchdir_not_found", "ScratchDir is not registered")
            existing = self._record(row)
            if (existing.state == ScratchDirState.CLEARED.value
                    and existing.operation_id == operation_id):
                return existing
            if (existing.state != ScratchDirState.CLEARING.value
                    or existing.operation_id != operation_id):
                raise ScratchDirError(
                    "scratchdir_state_conflict",
                    "ScratchDir clear operation no longer owns this state")
            connection.execute(
                """UPDATE scratchdirs
                   SET locator = '', state = ?, observed_bytes = 0,
                       observed_files = 0, revision = revision + 1,
                       updated_at = ?, cleared_at = ?
                   WHERE user_id = ? AND conversation_id = ?
                     AND agent_name = ? AND relay_id = ?""",
                (ScratchDirState.CLEARED.value, current, current, *scope),
            )
            row = connection.execute(
                """SELECT * FROM scratchdirs
                   WHERE user_id = ? AND conversation_id = ?
                     AND agent_name = ? AND relay_id = ?""",
                scope,
            ).fetchone()
        return self._record(row)

    def mark_orphaned(self, user_id: str, conversation_id: str, agent_name: str,
                      relay_id: str, *, operation_id: str,
                      now: float | None = None) -> ScratchDirRecord:
        scope = require_scope(user_id, conversation_id, agent_name, relay_id)
        current = time.time() if now is None else float(now)
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """UPDATE scratchdirs
                   SET state = ?, operation_id = ?, revision = revision + 1,
                       updated_at = ?, reconciled_at = ?
                   WHERE user_id = ? AND conversation_id = ?
                     AND agent_name = ? AND relay_id = ?""",
                (ScratchDirState.ORPHANED.value, str(operation_id or ""),
                 current, current, *scope),
            )
            if cursor.rowcount != 1:
                raise ScratchDirError(
                    "scratchdir_not_found", "ScratchDir is not registered")
            row = connection.execute(
                """SELECT * FROM scratchdirs
                   WHERE user_id = ? AND conversation_id = ?
                     AND agent_name = ? AND relay_id = ?""",
                scope,
            ).fetchone()
        return self._record(row)

    def context_hint(self, user_id: str, conversation_id: str,
                     agent_name: str, relay_id: str) -> str:
        record = self.get(user_id, conversation_id, agent_name, relay_id)
        if record is None or record.state == ScratchDirState.CLEARED.value:
            return ""
        return (
            f"ScratchDir: {record.state} on relay {record.relay_id}; "
            f"{record.observed_files} files / {record.observed_bytes} bytes; "
            "use fs://scratchdir/ for resumable temporary files.")
