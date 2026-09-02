"""SQLite authority for single-server LLM routing operational state."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import core.paths as _paths
from core.llm_routing_types import (
    AffinityKey,
    AffinityRecord,
    CandidateKey,
    HealthRecord,
    ResolvedServiceRef,
)
from core.sqlite_store_guard import (
    SqliteStoreGuard,
    is_corruption_error,
)


logger = logging.getLogger(__name__)
# Backoff applied when a candidate crosses the transient-failure threshold
# without a provider Retry-After: doubles per extra failure, capped.
_BASE_COOLDOWN_SECONDS = 30.0
_MAX_COOLDOWN_SECONDS = 1800.0

_SENSITIVE_PARTS = (
    "authorization", "api_key", "apikey", "access_token", "refresh_token",
    "password", "secret", "cookie")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS router_health (
    router_scope TEXT NOT NULL,
    router_scope_id TEXT NOT NULL,
    router_service_id TEXT NOT NULL,
    child_scope TEXT NOT NULL,
    child_scope_id TEXT NOT NULL,
    child_service_id TEXT NOT NULL,
    model TEXT NOT NULL,
    credential_id TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL,
    consecutive_failures INTEGER NOT NULL,
    last_success_at REAL NOT NULL,
    last_failure_at REAL NOT NULL,
    cooldown_until REAL NOT NULL,
    last_failure_kind TEXT NOT NULL,
    last_status INTEGER NOT NULL,
    revision INTEGER NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (
        router_scope, router_scope_id, router_service_id,
        child_scope, child_scope_id, child_service_id, model, credential_id)
);
CREATE TABLE IF NOT EXISTS router_affinity (
    router_scope TEXT NOT NULL,
    router_scope_id TEXT NOT NULL,
    router_service_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    child_scope TEXT NOT NULL,
    child_scope_id TEXT NOT NULL,
    child_service_id TEXT NOT NULL,
    child_definition_revision TEXT NOT NULL,
    successful_turns INTEGER NOT NULL,
    last_selected_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    revision INTEGER NOT NULL,
    PRIMARY KEY (
        router_scope, router_scope_id, router_service_id,
        user_id, conversation_id, agent_name)
);
CREATE TABLE IF NOT EXISTS router_counters (
    router_scope TEXT NOT NULL,
    router_scope_id TEXT NOT NULL,
    router_service_id TEXT NOT NULL,
    value INTEGER NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (router_scope, router_scope_id, router_service_id)
);
CREATE TABLE IF NOT EXISTS router_probe_leases (
    health_key TEXT PRIMARY KEY,
    lease_id TEXT NOT NULL,
    expires_at REAL NOT NULL)
;
CREATE TABLE IF NOT EXISTS router_events (
    id TEXT PRIMARY KEY,
    ts REAL NOT NULL,
    event_type TEXT NOT NULL,
    router_scope TEXT NOT NULL,
    router_scope_id TEXT NOT NULL,
    router_service_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    child_scope TEXT NOT NULL,
    child_scope_id TEXT NOT NULL,
    child_service_id TEXT NOT NULL,
    attempt_index INTEGER NOT NULL,
    outcome TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    duration_ms INTEGER NOT NULL,
    details_json TEXT NOT NULL)
;
CREATE INDEX IF NOT EXISTS idx_router_events_ts ON router_events (ts);
CREATE INDEX IF NOT EXISTS idx_router_events_plan ON router_events (plan_id, ts);
CREATE INDEX IF NOT EXISTS idx_router_events_conv ON router_events (conversation_id, ts);
CREATE INDEX IF NOT EXISTS idx_router_events_child
    ON router_events (child_scope, child_scope_id, child_service_id, ts);
"""


def _contains_sensitive(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower().replace("-", "_")
            if any(part in lowered for part in _SENSITIVE_PARTS):
                return True
            if _contains_sensitive(item):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_sensitive(item) for item in value)
    return False


class LLMRoutingStore:
    """Thread-safe operational store; one instance owns one SQLite connection."""

    def __init__(self, path: str = "", *, clock: Callable[[], float] = time.time,
                 event_retention: int = 10000):
        self.path = Path(path or str(_paths.LLM_ROUTING_DB_FILE))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.event_retention = max(100, int(event_retention))
        self._lock = threading.RLock()
        self._guard = SqliteStoreGuard("LLM routing")
        self._guard.preflight(self.path)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._guard.runtime(self.path), self._lock:
            self._conn.executescript(_SCHEMA)
            try:
                self._conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.DatabaseError as exc:
                if is_corruption_error(exc):
                    raise
                logger.debug("LLM routing WAL is unavailable", exc_info=True)
            self._conn.commit()
        self.cleanup()

    @property
    def available(self) -> bool:
        """Return whether the store is safe to read or write."""
        return self._guard.available

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _health_from_row(key: CandidateKey, row) -> HealthRecord:
        if row is None:
            return HealthRecord(key=key)
        return HealthRecord(
            key=key, state=row["state"],
            consecutive_failures=row["consecutive_failures"],
            last_success_at=row["last_success_at"],
            last_failure_at=row["last_failure_at"],
            cooldown_until=row["cooldown_until"],
            last_failure_kind=row["last_failure_kind"],
            last_status=row["last_status"], updated_at=row["updated_at"],
            revision=row["revision"])

    def get_health(self, key: CandidateKey) -> HealthRecord:
        with self._guard.runtime(self.path), self._lock:
            row = self._conn.execute(
                "SELECT * FROM router_health WHERE "
                "router_scope=? AND router_scope_id=? AND router_service_id=? "
                "AND child_scope=? AND child_scope_id=? AND child_service_id=? "
                "AND model=? AND credential_id=?", key.as_tuple()).fetchone()
        return self._health_from_row(key, row)

    def get_health_many(self, keys: Iterable[CandidateKey]) -> dict[CandidateKey, HealthRecord]:
        return {key: self.get_health(key) for key in keys}

    def record_success(self, key: CandidateKey) -> HealthRecord:
        now = float(self.clock())
        with self._guard.runtime(self.path), self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO router_health VALUES (?,?,?,?,?,?,?,?,'healthy',0,?,0,0,'',0,1,?) "
                "ON CONFLICT(router_scope,router_scope_id,router_service_id,"
                "child_scope,child_scope_id,child_service_id,model,credential_id) "
                "DO UPDATE SET state='healthy',consecutive_failures=0,"
                "last_success_at=excluded.last_success_at,cooldown_until=0,"
                "last_failure_kind='',last_status=0,revision=revision+1,"
                "updated_at=excluded.updated_at",
                key.as_tuple() + (now, now))
        return self.get_health(key)

    def record_failure(self, key: CandidateKey, *, failure_kind: str,
                       provider_status: int = 0, cooldown_until: float = 0,
                       locked: bool = False,
                       transient_threshold: int = 3) -> HealthRecord:
        now = float(self.clock())
        threshold = max(1, int(transient_threshold))
        with self._guard.runtime(self.path), self._lock, self._conn:
            row = self._conn.execute(
                "SELECT consecutive_failures FROM router_health WHERE "
                "router_scope=? AND router_scope_id=? AND router_service_id=? "
                "AND child_scope=? AND child_scope_id=? AND child_service_id=? "
                "AND model=? AND credential_id=?", key.as_tuple()).fetchone()
            failures = int(row[0] if row else 0) + 1
            cooldown_until = float(cooldown_until or 0)
            state = (
                "locked" if locked else
                "cooldown" if cooldown_until > now or failures >= threshold
                else "degraded")
            if state == "cooldown" and cooldown_until <= now:
                # Provider gave no Retry-After: without a deadline the policy
                # never excludes the candidate ("cooldown" with
                # cooldown_until=0 is inert). Exponential backoff from the
                # transient threshold, capped.
                cooldown_until = now + min(
                    _MAX_COOLDOWN_SECONDS,
                    _BASE_COOLDOWN_SECONDS * (2 ** max(0, failures - threshold)))
            self._conn.execute(
                "INSERT INTO router_health VALUES (?,?,?,?,?,?,?,?,?,?,0,?,?,?,?,1,?) "
                "ON CONFLICT(router_scope,router_scope_id,router_service_id,"
                "child_scope,child_scope_id,child_service_id,model,credential_id) "
                "DO UPDATE SET state=excluded.state,"
                "consecutive_failures=excluded.consecutive_failures,"
                "last_failure_at=excluded.last_failure_at,"
                "cooldown_until=excluded.cooldown_until,"
                "last_failure_kind=excluded.last_failure_kind,"
                "last_status=excluded.last_status,revision=revision+1,"
                "updated_at=excluded.updated_at",
                key.as_tuple() + (
                    state, failures, now, cooldown_until,
                    str(failure_kind or "unknown")[:64], int(provider_status or 0), now))
        return self.get_health(key)

    def clear_health(self, key: CandidateKey) -> None:
        with self._guard.runtime(self.path), self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM router_health WHERE "
                "router_scope=? AND router_scope_id=? AND router_service_id=? "
                "AND child_scope=? AND child_scope_id=? AND child_service_id=? "
                "AND model=? AND credential_id=?", key.as_tuple())

    def next_counter(self, router_key: tuple[str, str, str]) -> int:
        now = float(self.clock())
        with self._guard.runtime(self.path), self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO router_counters VALUES (?,?,?,1,?) "
                "ON CONFLICT(router_scope,router_scope_id,router_service_id) "
                "DO UPDATE SET value=value+1,updated_at=excluded.updated_at",
                tuple(router_key) + (now,))
            row = self._conn.execute(
                "SELECT value FROM router_counters WHERE "
                "router_scope=? AND router_scope_id=? AND router_service_id=?",
                tuple(router_key)).fetchone()
        return int(row[0]) - 1

    def get_affinity(self, key: AffinityKey) -> Optional[AffinityRecord]:
        with self._guard.runtime(self.path), self._lock:
            row = self._conn.execute(
                "SELECT * FROM router_affinity WHERE "
                "router_scope=? AND router_scope_id=? AND router_service_id=? "
                "AND user_id=? AND conversation_id=? AND agent_name=?",
                key.as_tuple()).fetchone()
        if not row:
            return None
        child = ResolvedServiceRef(
            row["child_scope"], row["child_scope_id"],
            row["child_service_id"], row["child_definition_revision"])
        return AffinityRecord(
            key, child, row["successful_turns"], row["last_selected_at"],
            row["expires_at"], row["revision"])

    def set_affinity(self, record: AffinityRecord,
                     *, expected_revision: int | None = None) -> bool:
        values = record.key.as_tuple() + (
            record.child.scope, record.child.scope_id, record.child.service_id,
            record.child.definition_revision, int(record.successful_turns),
            float(record.last_selected_at), float(record.expires_at))
        with self._guard.runtime(self.path), self._lock, self._conn:
            if expected_revision is None:
                self._conn.execute(
                    "INSERT INTO router_affinity VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1) "
                    "ON CONFLICT(router_scope,router_scope_id,router_service_id,"
                    "user_id,conversation_id,agent_name) DO UPDATE SET "
                    "child_scope=excluded.child_scope,"
                    "child_scope_id=excluded.child_scope_id,"
                    "child_service_id=excluded.child_service_id,"
                    "child_definition_revision=excluded.child_definition_revision,"
                    "successful_turns=excluded.successful_turns,"
                    "last_selected_at=excluded.last_selected_at,"
                    "expires_at=excluded.expires_at,revision=revision+1", values)
                return True
            cursor = self._conn.execute(
                "UPDATE router_affinity SET child_scope=?,child_scope_id=?,"
                "child_service_id=?,child_definition_revision=?,successful_turns=?,"
                "last_selected_at=?,expires_at=?,revision=revision+1 WHERE "
                "router_scope=? AND router_scope_id=? AND router_service_id=? "
                "AND user_id=? AND conversation_id=? AND agent_name=? AND revision=?",
                values[6:] + values[:6] + (int(expected_revision),))
            return cursor.rowcount == 1

    def clear_affinity(self, key: AffinityKey) -> None:
        with self._guard.runtime(self.path), self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM router_affinity WHERE router_scope=? "
                "AND router_scope_id=? AND router_service_id=? AND user_id=? "
                "AND conversation_id=? AND agent_name=?", key.as_tuple())

    @staticmethod
    def health_key_text(key: CandidateKey) -> str:
        return json.dumps(key.as_tuple(), separators=(",", ":"))

    def acquire_probe(self, key: CandidateKey, *, ttl_seconds: float = 30) -> str:
        now = float(self.clock())
        lease_id = str(uuid.uuid4())
        health_key = self.health_key_text(key)
        with self._guard.runtime(self.path), self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM router_probe_leases WHERE health_key=? AND expires_at<=?",
                (health_key, now))
            try:
                self._conn.execute(
                    "INSERT INTO router_probe_leases VALUES (?,?,?)",
                    (health_key, lease_id, now + max(1, float(ttl_seconds))))
            except sqlite3.IntegrityError:
                return ""
        return lease_id

    def release_probe(self, key: CandidateKey, lease_id: str) -> bool:
        with self._guard.runtime(self.path), self._lock, self._conn:
            cursor = self._conn.execute(
                "DELETE FROM router_probe_leases WHERE health_key=? AND lease_id=?",
                (self.health_key_text(key), str(lease_id or "")))
            return cursor.rowcount == 1

    def append_event(self, *, event_type: str, router_key: tuple[str, str, str],
                     plan_id: str = "", turn_id: str = "", user_id: str = "",
                     conversation_id: str = "", agent_name: str = "",
                     child: ResolvedServiceRef | None = None,
                     attempt_index: int = -1, outcome: str = "",
                     reason_code: str = "", duration_ms: int = 0,
                     details: dict | None = None, event_id: str = "",
                     ts: float | None = None) -> str:
        details = details or {}
        if not isinstance(details, dict) or _contains_sensitive(details):
            raise ValueError("Routing event details contain forbidden sensitive fields")
        encoded = json.dumps(
            details, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False)
        if len(encoded) > 4096:
            raise ValueError("Routing event details exceed 4096 bytes")
        event_id = event_id or str(uuid.uuid4())
        uuid.UUID(event_id)
        timestamp = float(ts if ts is not None else self.clock())
        child_values = child.key if child else ("", "", "")
        with self._guard.runtime(self.path), self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO router_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (event_id, timestamp, str(event_type), *router_key, plan_id,
                 turn_id, user_id, conversation_id, agent_name, *child_values,
                 int(attempt_index), str(outcome), str(reason_code),
                 max(0, int(duration_ms)), encoded))
            self._conn.execute(
                "DELETE FROM router_events WHERE id IN (SELECT id FROM "
                "router_events ORDER BY ts DESC, id DESC LIMIT -1 OFFSET ?)",
                (self.event_retention,))
        return event_id

    def last_selected_map(
            self, router_key: tuple[str, str, str]) -> dict[tuple[str, str, str], float]:
        """Latest selection timestamp per child, for one router only."""
        with self._guard.runtime(self.path), self._lock:
            rows = self._conn.execute(
                "SELECT child_scope, child_scope_id, child_service_id, "
                "MAX(ts) AS ts FROM router_events WHERE router_scope=? "
                "AND router_scope_id=? AND router_service_id=? "
                "AND event_type='llm.route.selected' "
                "GROUP BY child_scope, child_scope_id, child_service_id",
                tuple(router_key)).fetchall()
        return {
            (row["child_scope"], row["child_scope_id"],
             row["child_service_id"]): float(row["ts"])
            for row in rows}

    def list_events(self, *, plan_id: str = "",
                    router_key: tuple[str, str, str] | None = None,
                    limit: int = 100) -> list[dict]:
        limit = max(1, min(1000, int(limit)))
        query = "SELECT * FROM router_events"
        clauses = []
        args: tuple[Any, ...] = ()
        if plan_id:
            clauses.append("plan_id=?")
            args += (plan_id,)
        if router_key is not None:
            clauses.append(
                "router_scope=? AND router_scope_id=? AND router_service_id=?")
            args += tuple(router_key)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY ts DESC,id DESC LIMIT ?"
        with self._guard.runtime(self.path), self._lock:
            rows = self._conn.execute(query, args + (limit,)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["details"] = json.loads(item.pop("details_json"))
            result.append(item)
        return result

    def cleanup(self) -> None:
        now = float(self.clock())
        with self._guard.runtime(self.path), self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM router_affinity WHERE expires_at<=?", (now,))
            self._conn.execute(
                "DELETE FROM router_probe_leases WHERE expires_at<=?", (now,))
