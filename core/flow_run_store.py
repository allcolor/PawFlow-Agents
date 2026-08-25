"""Durable lifecycle and terminal outbox for declarative one-shot flow runs."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import core.paths as _paths
from core.resource_identity import ResourceRef

FLOW_RUN_TERMINALS = frozenset({
    "completed", "failed", "cancelled", "timed_out", "force_stopped",
})
FLOW_RUN_LIVE = frozenset({
    "created", "starting", "running", "waiting", "cancelling", "committing",
})
_TRANSITIONS = {
    "created": frozenset({"starting", "cancelling", "failed", "timed_out", "force_stopped"}),
    "starting": frozenset({"running", "cancelling", "failed", "timed_out", "force_stopped"}),
    "running": frozenset({"waiting", "committing", "cancelling", "failed", "timed_out", "force_stopped"}),
    "waiting": frozenset({"running", "committing", "cancelling", "failed", "timed_out", "force_stopped"}),
    "cancelling": frozenset({"cancelled", "force_stopped"}),
    "committing": frozenset({"completed"}),
}


def _required(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} is required")
    return result


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class FlowRunStore:
    """Authoritative metadata store for durable one-shot execution."""

    _instance: FlowRunStore | None = None
    _instance_lock = threading.Lock()

    @classmethod
    def instance(cls) -> FlowRunStore:
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        with cls._instance_lock:
            cls._instance = None

    def __init__(
        self,
        database_path: Path | None = None,
        *,
        before_live_write: Callable[[], Any] | None = None,
    ) -> None:
        self._database_path = Path(database_path) if database_path else None
        if before_live_write is None:
            from core.plan_migration_runtime import mark_active_plan_migration_write

            before_live_write = mark_active_plan_migration_write
        if not callable(before_live_write):
            raise TypeError("before_live_write must be callable")
        self._before_live_write = before_live_write
        self._lock = threading.RLock()
        self._initialize()

    @property
    def database_path(self) -> Path:
        return self._database_path or (_paths.RUNTIME_DIR / "flow_runs.sqlite3")

    def _connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS flow_runs (
                    run_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    flow_ref_json TEXT NOT NULL,
                    deployment_instance_id TEXT NOT NULL UNIQUE,
                    proposal_id TEXT,
                    parent_invocation_json TEXT,
                    authorization_ref_json TEXT NOT NULL,
                    input_json TEXT NOT NULL,
                    parameters_json TEXT NOT NULL,
                    replay_of TEXT,
                    status TEXT NOT NULL,
                    terminal_json TEXT,
                    terminal_event_id TEXT,
                    error TEXT,
                    recovery_count INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    terminal_at REAL,
                    import_metadata_json TEXT,
                    checkpoint_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_flow_runs_recovery
                    ON flow_runs(status, updated_at);
                CREATE INDEX IF NOT EXISTS idx_flow_runs_conversation
                    ON flow_runs(conversation_id, created_at);
                CREATE TABLE IF NOT EXISTS flow_run_outbox (
                    event_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL UNIQUE REFERENCES flow_runs(run_id),
                    event_json TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('pending','delivered')),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    delivered_at REAL
                );
            """)
            columns = {
                str(row["name"]) for row in connection.execute(
                    "PRAGMA table_info(flow_runs)").fetchall()}
            if "import_metadata_json" not in columns:
                connection.execute(
                    "ALTER TABLE flow_runs ADD COLUMN import_metadata_json TEXT")
            if "checkpoint_json" not in columns:
                connection.execute(
                    "ALTER TABLE flow_runs ADD COLUMN checkpoint_json TEXT")

    @staticmethod
    def _decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        for column, target in (
            ("flow_ref_json", "flow_ref"),
            ("parent_invocation_json", "parent_invocation"),
            ("authorization_ref_json", "authorization_ref"),
            ("input_json", "input"),
            ("parameters_json", "parameters"),
            ("terminal_json", "terminal"),
            ("import_metadata_json", "import_metadata"),
            ("checkpoint_json", "checkpoint"),
        ):
            raw = result.pop(column, None)
            result[target] = json.loads(raw) if raw else None
        return result

    def create(
        self, *, user_id: str, conversation_id: str, flow_ref: dict[str, Any],
        authorization_ref: dict[str, Any], input_snapshot: dict[str, Any],
        parameters: dict[str, Any], proposal_id: str = "",
        parent_invocation: dict[str, Any] | None = None,
        replay_of: str = "", run_id: str = "", instance_id: str = "",
    ) -> dict[str, Any]:
        ref = ResourceRef.from_dict(flow_ref)
        if ref.resource_type != "flow":
            raise ValueError("flow_ref must identify an exact flow")
        if not isinstance(authorization_ref, dict) or not authorization_ref:
            raise ValueError("authorization_ref is required")
        if not isinstance(input_snapshot, dict) or not isinstance(parameters, dict):
            raise TypeError("input_snapshot and parameters must be objects")
        user_id = _required(user_id, "user_id")
        conversation_id = _required(conversation_id, "conversation_id")
        run_id = run_id or f"fr_{uuid.uuid4().hex}"
        instance_id = instance_id or f"flowrun__{uuid.uuid4().hex}"
        now = time.time()
        self._before_live_write()
        with self._lock, self._connect() as connection:
            generation = int(connection.execute(
                "SELECT COALESCE(MAX(generation), 0) FROM flow_runs "
                "WHERE conversation_id = ? AND flow_ref_json = ?",
                (conversation_id, _json(ref.to_dict())),
            ).fetchone()[0]) + 1
            connection.execute(
                """
                INSERT INTO flow_runs (
                    run_id, user_id, conversation_id, generation,
                    flow_ref_json, deployment_instance_id, proposal_id,
                    parent_invocation_json, authorization_ref_json, input_json,
                    parameters_json, replay_of, status, terminal_json,
                    terminal_event_id, error, recovery_count, created_at,
                    updated_at, terminal_at, import_metadata_json,
                    checkpoint_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (run_id, user_id, conversation_id, generation,
                 _json(ref.to_dict()), instance_id, proposal_id or None,
                 _json(parent_invocation) if parent_invocation else None,
                 _json(authorization_ref), _json(input_snapshot), _json(parameters),
                 replay_of or None, "created", None, None, None, 0, now, now,
                 None, None, None),
            )
        return self.get(run_id)

    def import_terminal(
        self, *, run_id: str, user_id: str, conversation_id: str,
        flow_ref: dict[str, Any], proposal_id: str, status: str,
        terminal: dict[str, Any], import_metadata: dict[str, Any],
        created_at: float, terminal_at: float,
    ) -> dict[str, Any]:
        """Import terminal history without emitting a live terminal event."""

        run_id = _required(run_id, "run_id")
        user_id = _required(user_id, "user_id")
        conversation_id = _required(conversation_id, "conversation_id")
        proposal_id = _required(proposal_id, "proposal_id")
        if status not in FLOW_RUN_TERMINALS:
            raise ValueError("imported flow run status must be terminal")
        if not isinstance(terminal, dict) or not isinstance(import_metadata, dict):
            raise TypeError("terminal and import_metadata must be objects")
        if (
            isinstance(created_at, bool) or not isinstance(created_at, (int, float))
            or isinstance(terminal_at, bool)
            or not isinstance(terminal_at, (int, float))
            or float(terminal_at) < float(created_at)
        ):
            raise ValueError("import timestamps are invalid")
        ref = ResourceRef.from_dict(flow_ref)
        if ref.resource_type != "flow":
            raise ValueError("flow_ref must identify an exact flow")
        expected = {
            "run_id": run_id,
            "user_id": user_id,
            "conversation_id": conversation_id,
            "flow_ref": ref.to_dict(),
            "proposal_id": proposal_id,
            "status": status,
            "terminal": terminal,
            "import_metadata": import_metadata,
            "created_at": float(created_at),
            "terminal_at": float(terminal_at),
        }
        with self._lock, self._connect() as connection:
            existing_row = connection.execute(
                "SELECT * FROM flow_runs WHERE run_id = ?", (run_id,)).fetchone()
            if existing_row is not None:
                existing = self._decode(existing_row)
                if all(existing.get(key) == value for key, value in expected.items()):
                    return existing
                raise ValueError("different imported run already exists")
            generation = int(connection.execute(
                "SELECT COALESCE(MAX(generation), 0) FROM flow_runs "
                "WHERE conversation_id = ? AND flow_ref_json = ?",
                (conversation_id, _json(ref.to_dict())),
            ).fetchone()[0]) + 1
            connection.execute(
                """
                INSERT INTO flow_runs (
                    run_id, user_id, conversation_id, generation,
                    flow_ref_json, deployment_instance_id, proposal_id,
                    parent_invocation_json, authorization_ref_json, input_json,
                    parameters_json, replay_of, status, terminal_json,
                    terminal_event_id, error, recovery_count, created_at,
                    updated_at, terminal_at, import_metadata_json,
                    checkpoint_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    run_id, user_id, conversation_id, generation,
                    _json(ref.to_dict()), f"flowrun__legacy__{run_id}",
                    proposal_id, None,
                    _json({"kind": "legacy_plan_import"}),
                    _json({}), _json({}), None, status, _json(terminal),
                    None, str(terminal.get("error") or "") or None, 0,
                    float(created_at), float(terminal_at), float(terminal_at),
                    _json(import_metadata), None,
                ),
            )
        return self.get(run_id)

    def import_active(
        self, *, run_id: str, user_id: str, conversation_id: str,
        flow_ref: dict[str, Any], proposal_id: str, status: str,
        checkpoint: dict[str, Any], import_metadata: dict[str, Any],
        authorization_ref: dict[str, Any], input_snapshot: dict[str, Any],
        parameters: dict[str, Any], created_at: float,
    ) -> dict[str, Any]:
        """Import a provable waiting checkpoint without starting execution."""

        run_id = _required(run_id, "run_id")
        user_id = _required(user_id, "user_id")
        conversation_id = _required(conversation_id, "conversation_id")
        proposal_id = _required(proposal_id, "proposal_id")
        if status != "waiting":
            raise ValueError("active checkpoint import requires waiting status")
        for value, name in (
            (checkpoint, "checkpoint"),
            (import_metadata, "import_metadata"),
            (authorization_ref, "authorization_ref"),
            (input_snapshot, "input_snapshot"),
            (parameters, "parameters"),
        ):
            if not isinstance(value, dict) or (
                    name in {"checkpoint", "import_metadata", "authorization_ref"}
                    and not value):
                raise ValueError(f"{name} must be an object")
        if isinstance(created_at, bool) or not isinstance(created_at, (int, float)):
            raise TypeError("created_at must be numeric")
        ref = ResourceRef.from_dict(flow_ref)
        if ref.resource_type != "flow":
            raise ValueError("flow_ref must identify an exact flow")
        created_at = float(created_at)
        expected = {
            "run_id": run_id,
            "user_id": user_id,
            "conversation_id": conversation_id,
            "flow_ref": ref.to_dict(),
            "proposal_id": proposal_id,
            "authorization_ref": authorization_ref,
            "input": input_snapshot,
            "parameters": parameters,
            "status": status,
            "import_metadata": import_metadata,
            "checkpoint": checkpoint,
            "created_at": created_at,
        }
        with self._lock, self._connect() as connection:
            existing_row = connection.execute(
                "SELECT * FROM flow_runs WHERE run_id = ?", (run_id,),
            ).fetchone()
            if existing_row is not None:
                existing = self._decode(existing_row)
                if all(existing.get(key) == value for key, value in expected.items()):
                    return existing
                raise ValueError("different imported run already exists")
            generation = int(connection.execute(
                "SELECT COALESCE(MAX(generation), 0) FROM flow_runs "
                "WHERE conversation_id = ? AND flow_ref_json = ?",
                (conversation_id, _json(ref.to_dict())),
            ).fetchone()[0]) + 1
            connection.execute(
                """
                INSERT INTO flow_runs (
                    run_id, user_id, conversation_id, generation,
                    flow_ref_json, deployment_instance_id, proposal_id,
                    parent_invocation_json, authorization_ref_json, input_json,
                    parameters_json, replay_of, status, terminal_json,
                    terminal_event_id, error, recovery_count, created_at,
                    updated_at, terminal_at, import_metadata_json,
                    checkpoint_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    run_id, user_id, conversation_id, generation,
                    _json(ref.to_dict()), f"flowrun__legacy__{run_id}",
                    proposal_id, None, _json(authorization_ref),
                    _json(input_snapshot), _json(parameters), None, status,
                    None, None, None, 0, created_at, created_at, None,
                    _json(import_metadata), _json(checkpoint),
                ),
            )
        return self.get(run_id)

    def import_pending(
        self, *, run_id: str, user_id: str, conversation_id: str,
        flow_ref: dict[str, Any], proposal_id: str,
        import_metadata: dict[str, Any],
        authorization_ref: dict[str, Any], input_snapshot: dict[str, Any],
        parameters: dict[str, Any], created_at: float,
    ) -> dict[str, Any]:
        """Import an approved legacy run without starting its executor."""

        run_id = _required(run_id, "run_id")
        user_id = _required(user_id, "user_id")
        conversation_id = _required(conversation_id, "conversation_id")
        proposal_id = _required(proposal_id, "proposal_id")
        for value, name in (
            (import_metadata, "import_metadata"),
            (authorization_ref, "authorization_ref"),
            (input_snapshot, "input_snapshot"),
            (parameters, "parameters"),
        ):
            if not isinstance(value, dict) or (
                    name in {"import_metadata", "authorization_ref"} and not value):
                raise ValueError(f"{name} must be an object")
        if isinstance(created_at, bool) or not isinstance(created_at, (int, float)):
            raise TypeError("created_at must be numeric")
        ref = ResourceRef.from_dict(flow_ref)
        if ref.resource_type != "flow":
            raise ValueError("flow_ref must identify an exact flow")
        created_at = float(created_at)
        expected = {
            "run_id": run_id,
            "user_id": user_id,
            "conversation_id": conversation_id,
            "flow_ref": ref.to_dict(),
            "proposal_id": proposal_id,
            "authorization_ref": authorization_ref,
            "input": input_snapshot,
            "parameters": parameters,
            "status": "created",
            "import_metadata": import_metadata,
            "checkpoint": None,
            "created_at": created_at,
        }
        with self._lock, self._connect() as connection:
            existing_row = connection.execute(
                "SELECT * FROM flow_runs WHERE run_id = ?", (run_id,),
            ).fetchone()
            if existing_row is not None:
                existing = self._decode(existing_row)
                if all(existing.get(key) == value for key, value in expected.items()):
                    return existing
                raise ValueError("different imported run already exists")
            generation = int(connection.execute(
                "SELECT COALESCE(MAX(generation), 0) FROM flow_runs "
                "WHERE conversation_id = ? AND flow_ref_json = ?",
                (conversation_id, _json(ref.to_dict())),
            ).fetchone()[0]) + 1
            connection.execute(
                """
                INSERT INTO flow_runs (
                    run_id, user_id, conversation_id, generation,
                    flow_ref_json, deployment_instance_id, proposal_id,
                    parent_invocation_json, authorization_ref_json, input_json,
                    parameters_json, replay_of, status, terminal_json,
                    terminal_event_id, error, recovery_count, created_at,
                    updated_at, terminal_at, import_metadata_json,
                    checkpoint_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    run_id, user_id, conversation_id, generation,
                    _json(ref.to_dict()), f"flowrun__legacy__{run_id}",
                    proposal_id, None, _json(authorization_ref),
                    _json(input_snapshot), _json(parameters), None, "created",
                    None, None, None, 0, created_at, created_at, None,
                    _json(import_metadata), None,
                ),
            )
        return self.get(run_id)

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM flow_runs WHERE run_id = ?", (run_id,)).fetchone()
        return self._decode(row)

    def delete_imported(
        self, run_id: str, *, import_metadata: dict[str, Any],
    ) -> bool:
        """Delete imported history only when exact provenance still matches."""

        run_id = _required(run_id, "run_id")
        if not isinstance(import_metadata, dict) or not import_metadata:
            raise ValueError("import_metadata must be a non-empty object")
        expected = _json(import_metadata)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT import_metadata_json FROM flow_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                return False
            if row["import_metadata_json"] != expected:
                raise ValueError("imported run provenance does not match")
            if connection.execute(
                "SELECT 1 FROM flow_run_outbox WHERE run_id = ?", (run_id,),
            ).fetchone():
                raise ValueError("imported run has a live outbox event")
            connection.execute("DELETE FROM flow_runs WHERE run_id = ?", (run_id,))
        return True

    def list(self, conversation_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM flow_runs WHERE conversation_id = ? "
                "ORDER BY created_at DESC LIMIT ?", (conversation_id, limit),
            ).fetchall()
        return [self._decode(row) for row in rows]

    def transition(self, run_id: str, target: str, error: str = "") -> dict[str, Any]:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT status, terminal_json, terminal_event_id FROM flow_runs "
                "WHERE run_id = ?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(run_id)
            current = str(row["status"])
            if current == target:
                return self.get(run_id)
            if current in FLOW_RUN_TERMINALS or target not in _TRANSITIONS.get(current, ()):
                raise ValueError(f"invalid flow run transition {current} -> {target}")
            self._before_live_write()
            now = time.time()
            connection.execute(
                "UPDATE flow_runs SET status = ?, error = ?, updated_at = ?, "
                "terminal_at = CASE WHEN ? THEN ? ELSE terminal_at END WHERE run_id = ?",
                (target, error or None, now, int(target in FLOW_RUN_TERMINALS), now, run_id),
            )
            if target in FLOW_RUN_TERMINALS and not row["terminal_event_id"]:
                event_id = f"fre_{uuid.uuid4().hex}"
                terminal = (
                    json.loads(row["terminal_json"])
                    if row["terminal_json"] else None)
                event = {
                    "event_id": event_id, "run_id": run_id,
                    "type": "flow_run_terminal", "status": target,
                    "terminal": terminal, "error": error or "",
                    "created_at": now,
                }
                connection.execute(
                    "UPDATE flow_runs SET terminal_event_id = ? WHERE run_id = ?",
                    (event_id, run_id))
                connection.execute(
                    "INSERT INTO flow_run_outbox VALUES "
                    "(?, ?, ?, 'pending', 0, ?, NULL)",
                    (event_id, run_id, _json(event), now))
        return self.get(run_id)

    def stage_terminal(self, run_id: str, terminal: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(terminal, dict):
            raise TypeError("terminal must be an object")
        payload = _json(terminal)
        if len(payload.encode()) > 1024 * 1024:
            raise ValueError("terminal exceeds 1 MiB")
        artifacts = terminal.get("artifacts", [])
        if not isinstance(artifacts, list) or len(artifacts) > 100:
            raise ValueError("terminal artifacts must be an array of at most 100 items")
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT status, terminal_json, terminal_event_id FROM flow_runs "
                "WHERE run_id = ?", (run_id,),
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            if row["terminal_json"]:
                if row["terminal_json"] != payload:
                    raise ValueError("flow run already has a different terminal")
                return self.get(run_id)
            if row["status"] not in {"running", "waiting"}:
                raise ValueError("flow run terminal requires running or waiting status")
            self._before_live_write()
            event_id = f"fre_{uuid.uuid4().hex}"
            now = time.time()
            event = {
                "event_id": event_id, "run_id": run_id,
                "type": "flow_run_terminal", "status": "completed",
                "terminal": terminal, "created_at": now,
            }
            connection.execute(
                "UPDATE flow_runs SET status = 'committing', terminal_json = ?, "
                "terminal_event_id = ?, updated_at = ? WHERE run_id = ?",
                (payload, event_id, now, run_id),
            )
            connection.execute(
                "INSERT INTO flow_run_outbox VALUES (?, ?, ?, 'pending', 0, ?, NULL)",
                (event_id, run_id, _json(event), now),
            )
        return self.get(run_id)

    def commit(self, run_id: str) -> dict[str, Any]:
        run = self.get(run_id)
        if run is None:
            raise KeyError(run_id)
        if run["status"] == "completed":
            return run
        return self.transition(run_id, "completed")

    def has_pending_instance(self, instance_id: str) -> bool:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM flow_runs WHERE deployment_instance_id = ?",
                (instance_id,),
            ).fetchone()
        return bool(row and row["status"] in FLOW_RUN_LIVE)

    def list_recoverable(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM flow_runs WHERE status IN ('starting','running','waiting','committing') "
                "ORDER BY created_at",
            ).fetchall()
        return [self._decode(row) for row in rows]

    def mark_recovered(self, run_id: str) -> dict[str, Any]:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM flow_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            if row["status"] not in {"starting", "running", "waiting"}:
                raise ValueError("flow run is not recoverable")
            self._before_live_write()
            connection.execute(
                "UPDATE flow_runs SET status = 'starting', "
                "recovery_count = recovery_count + 1, updated_at = ? "
                "WHERE run_id = ?", (time.time(), run_id))
        return self.get(run_id)

    def pending_events(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT event_json FROM flow_run_outbox WHERE state = 'pending' "
                "ORDER BY created_at",
            ).fetchall()
        return [json.loads(row["event_json"]) for row in rows]

    def acknowledge_event(self, event_id: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE flow_run_outbox SET state = 'delivered', attempts = attempts + 1, "
                "delivered_at = ? WHERE event_id = ?", (time.time(), event_id))


__all__ = ["FLOW_RUN_LIVE", "FLOW_RUN_TERMINALS", "FlowRunStore"]
