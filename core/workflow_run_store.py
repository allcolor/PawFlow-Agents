"""Authoritative SQLite state and terminal outbox for workflow-agent runs."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import core.paths as _paths
from core._workflow_run_store_llm import (
    WorkflowBudgetExceeded,
    WorkflowRunStoreLLMMixin,
)
from core.workflow_agent_contracts import (
    WORKFLOW_TERMINAL_STATUSES,
    WorkflowRunError,
    WorkflowRunRecord,
    validate_workflow_run_transition,
)

_RECOVERABLE = (
    "accepted", "running", "waiting", "retryable_failed", "cancelling",
    "committing",
)


def _utc(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def _required(value: Any, name: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{name} is required")
    return result


def _agent(value: Any) -> str:
    return _required(value, "agent_name").casefold()


def _json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class WorkflowRunStore(WorkflowRunStoreLLMMixin):
    """Transactional run state, active generations, step cache, and outbox."""

    _instance: WorkflowRunStore | None = None
    _instance_lock = threading.Lock()

    @classmethod
    def instance(cls) -> WorkflowRunStore:
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        with cls._instance_lock:
            cls._instance = None

    def __init__(self, database_path: Path | None = None) -> None:
        self._database_path_override = (
            Path(database_path) if database_path is not None else None)
        self._lock = threading.RLock()
        self._initialize()

    @property
    def database_path(self) -> Path:
        return self._database_path_override or (
            _paths.RUNTIME_DIR / "workflow_runs.sqlite3")

    def _connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS workflow_generations (
                    conversation_id TEXT NOT NULL,
                    agent_key TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(conversation_id, agent_key)
                );
                CREATE TABLE IF NOT EXISTS workflow_runs (
                    run_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    agent_key TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    permission_mode TEXT NOT NULL,
                    root_turn_id TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    ingress_source_json TEXT NOT NULL DEFAULT '{}',
                    run_generation INTEGER NOT NULL,
                    flow_ref_json TEXT NOT NULL,
                    invocation_mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reason TEXT,
                    deadline_at TEXT NOT NULL,
                    parameters_json TEXT NOT NULL,
                    binding_json TEXT NOT NULL DEFAULT '{}',
                    service_snapshot_json TEXT NOT NULL,
                    limits_json TEXT NOT NULL DEFAULT '{}',
                    authorization_ref_json TEXT NOT NULL,
                    parent_invocation_json TEXT,
                    publish_to_conversation INTEGER NOT NULL DEFAULT 0,
                    claimed_ids_json TEXT NOT NULL DEFAULT '[]',
                    usage_json TEXT NOT NULL DEFAULT '{}',
                    staged_result_json TEXT,
                    assistant_payload_json TEXT,
                    answered_turn_ids_json TEXT NOT NULL DEFAULT '[]',
                    assistant_msg_id TEXT,
                    terminal_event_id TEXT,
                    terminal_event_json TEXT,
                    terminal_status TEXT NOT NULL DEFAULT 'completed',
                    message_committed INTEGER NOT NULL DEFAULT 0,
                    inbox_acknowledged INTEGER NOT NULL DEFAULT 0,
                    outbox_enqueued INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    terminal_at REAL,
                    recovery_count INTEGER NOT NULL DEFAULT 0,
                    last_event_sequence INTEGER NOT NULL DEFAULT 0,
                    waiting_since REAL,
                    resume_task_id TEXT NOT NULL DEFAULT '',
                    resume_flowfile_json TEXT,
                    error_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_workflow_runs_recovery
                    ON workflow_runs(status, updated_at);
                CREATE INDEX IF NOT EXISTS idx_workflow_runs_key
                    ON workflow_runs(conversation_id, agent_key, created_at);
                CREATE TABLE IF NOT EXISTS workflow_active_runs (
                    conversation_id TEXT NOT NULL,
                    agent_key TEXT NOT NULL,
                    run_id TEXT NOT NULL REFERENCES workflow_runs(run_id)
                        ON DELETE CASCADE,
                    generation INTEGER NOT NULL,
                    lease_expires_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(conversation_id, agent_key)
                );
                CREATE TABLE IF NOT EXISTS workflow_step_results (
                    run_id TEXT NOT NULL REFERENCES workflow_runs(run_id)
                        ON DELETE CASCADE,
                    task_id TEXT NOT NULL,
                    input_hash TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    usage_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(run_id, task_id, input_hash)
                );
                CREATE TABLE IF NOT EXISTS workflow_llm_reservations (
                    run_id TEXT NOT NULL REFERENCES workflow_runs(run_id)
                        ON DELETE CASCADE,
                    task_id TEXT NOT NULL,
                    input_hash TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(run_id, task_id, input_hash)
                );
                CREATE TABLE IF NOT EXISTS workflow_terminal_outbox (
                    event_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL UNIQUE REFERENCES workflow_runs(run_id)
                        ON DELETE CASCADE,
                    event_json TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state IN ('pending','delivered')),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_attempt_at REAL,
                    delivered_at REAL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS workflow_run_events (
                    run_id TEXT NOT NULL REFERENCES workflow_runs(run_id)
                        ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    event_id TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(run_id, sequence)
                );
                """
            )
            columns = {
                str(row["name"]) for row in connection.execute(
                    "PRAGMA table_info(workflow_runs)").fetchall()}
            if "limits_json" not in columns:
                connection.execute(
                    "ALTER TABLE workflow_runs ADD COLUMN "
                    "limits_json TEXT NOT NULL DEFAULT '{}'")
            if "last_event_sequence" not in columns:
                connection.execute(
                    "ALTER TABLE workflow_runs ADD COLUMN "
                    "last_event_sequence INTEGER NOT NULL DEFAULT 0")
            if "binding_json" not in columns:
                connection.execute(
                    "ALTER TABLE workflow_runs ADD COLUMN "
                    "binding_json TEXT NOT NULL DEFAULT '{}'")
            if "permission_mode" not in columns:
                connection.execute(
                    "ALTER TABLE workflow_runs ADD COLUMN "
                    "permission_mode TEXT NOT NULL DEFAULT 'read_only'")
            if "parent_invocation_json" not in columns:
                connection.execute(
                    "ALTER TABLE workflow_runs ADD COLUMN "
                    "parent_invocation_json TEXT")
            if "publish_to_conversation" not in columns:
                connection.execute(
                    "ALTER TABLE workflow_runs ADD COLUMN "
                    "publish_to_conversation INTEGER NOT NULL DEFAULT 0")
            if "terminal_status" not in columns:
                connection.execute(
                    "ALTER TABLE workflow_runs ADD COLUMN "
                    "terminal_status TEXT NOT NULL DEFAULT 'completed'")
            if "waiting_since" not in columns:
                connection.execute(
                    "ALTER TABLE workflow_runs ADD COLUMN waiting_since REAL")
            if "resume_task_id" not in columns:
                connection.execute(
                    "ALTER TABLE workflow_runs ADD COLUMN "
                    "resume_task_id TEXT NOT NULL DEFAULT ''")
            if "resume_flowfile_json" not in columns:
                connection.execute(
                    "ALTER TABLE workflow_runs ADD COLUMN resume_flowfile_json TEXT")
            if "error_json" not in columns:
                connection.execute(
                    "ALTER TABLE workflow_runs ADD COLUMN error_json TEXT")
            if "ingress_source_json" not in columns:
                connection.execute(
                    "ALTER TABLE workflow_runs ADD COLUMN "
                    "ingress_source_json TEXT NOT NULL DEFAULT '{}'")

    def reserve_generation(self, conversation_id: str,
                           agent_name: str) -> int:
        conversation_id = _required(conversation_id, "conversation_id")
        agent_key = _agent(agent_name)
        now = time.time()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT generation FROM workflow_generations
                   WHERE conversation_id=? AND agent_key=?""",
                (conversation_id, agent_key)).fetchone()
            generation = int(row["generation"]) + 1 if row else 1
            connection.execute(
                """INSERT INTO workflow_generations VALUES (?, ?, ?, ?)
                   ON CONFLICT(conversation_id, agent_key) DO UPDATE SET
                       generation=excluded.generation,
                       updated_at=excluded.updated_at""",
                (conversation_id, agent_key, generation, now))
        return generation

    def create_run(self, *, context, request, parameters: dict[str, Any],
                   lease_seconds: float,
                   binding: dict[str, Any] | None = None,
                   parent_invocation: dict[str, Any] | None = None,
                   publish_to_conversation: bool = False,
                   ingress_source: dict[str, Any] | None = None) -> dict[str, Any]:
        now = time.time()
        run_id = _required(context.run_id, "run_id")
        conversation_id = _required(
            context.conversation_id, "conversation_id")
        agent_name = _required(context.agent_name, "agent_name")
        agent_key = _agent(agent_name)
        values = (
            run_id, conversation_id, agent_key, agent_name,
            _required(context.user_id, "user_id"),
            _required(context.channel, "channel"),
            _required(context.permission_mode, "permission_mode"),
            _required(context.root_turn_id, "root_turn_id"),
            _json(request.to_dict()), _json(ingress_source or {}),
            int(context.run_generation),
            _json(context.flow_ref.to_dict()), context.invocation_mode,
            "accepted", context.deadline_at or "", _json(parameters),
            _json(binding or {}),
            _json(context.service_snapshot),
            _json(context.limits.to_dict()),
            _json(context.authorization_ref.to_dict()),
            (_json(parent_invocation) if parent_invocation is not None else None),
            int(bool(publish_to_conversation)), now, now,
        )
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if existing is not None:
                if (
                    existing["conversation_id"] != conversation_id
                    or existing["agent_key"] != agent_key
                    or existing["permission_mode"] != context.permission_mode
                    or existing["root_turn_id"] != context.root_turn_id
                    or existing["ingress_source_json"] != _json(
                        ingress_source or {})
                    or existing["run_generation"] != context.run_generation
                    or existing["flow_ref_json"] != _json(
                        context.flow_ref.to_dict())
                    or existing["parent_invocation_json"] != (
                        _json(parent_invocation)
                        if parent_invocation is not None else None)
                    or bool(existing["publish_to_conversation"])
                    != bool(publish_to_conversation)
                ):
                    raise ValueError(
                        "run_id already identifies a different workflow run")
                return self._row(existing)
            active = connection.execute(
                """SELECT run_id FROM workflow_active_runs
                   WHERE conversation_id=? AND agent_key=?""",
                (conversation_id, agent_key)).fetchone()
            if active is not None and active["run_id"] != run_id:
                raise RuntimeError("another workflow run owns the active key")
            connection.execute(
                """INSERT INTO workflow_runs(
                       run_id, conversation_id, agent_key, agent_name, user_id,
                       channel, permission_mode, root_turn_id, request_json,
                       ingress_source_json, run_generation,
                       flow_ref_json, invocation_mode, status, deadline_at,
                       parameters_json, binding_json, service_snapshot_json,
                       limits_json, authorization_ref_json,
                       parent_invocation_json, publish_to_conversation,
                       created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                             ?, ?, ?, ?, ?, ?, ?, ?)""",
                values)
            connection.execute(
                """INSERT INTO workflow_active_runs VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(conversation_id, agent_key) DO UPDATE SET
                       run_id=excluded.run_id,
                       generation=excluded.generation,
                       lease_expires_at=excluded.lease_expires_at,
                       updated_at=excluded.updated_at""",
                (conversation_id, agent_key, run_id,
                 int(context.run_generation),
                 now + max(1.0, float(lease_seconds)), now))
            row = connection.execute(
                "SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)
            ).fetchone()
        return self._row(row)

    def transition(self, run_id: str, expected: str, current: str,
                   reason: str | None = None,
                   *, now: float | None = None) -> bool:
        validate_workflow_run_transition(expected, current)
        timestamp = time.time() if now is None else float(now)
        terminal_at = (
            timestamp if current in WORKFLOW_TERMINAL_STATUSES else None)
        with self._lock, self._connect() as connection:
            waiting_since = timestamp if current == "waiting" else None
            cursor = connection.execute(
                """UPDATE workflow_runs
                   SET status=?, reason=?, updated_at=?, terminal_at=?,
                       waiting_since=?
                   WHERE run_id=? AND status=?""",
                (current, reason, timestamp, terminal_at, waiting_since,
                 run_id, expected))
            if cursor.rowcount and current in WORKFLOW_TERMINAL_STATUSES:
                connection.execute(
                    "DELETE FROM workflow_active_runs WHERE run_id=?", (run_id,))
            if cursor.rowcount and (
                    current == "waiting" or current in WORKFLOW_TERMINAL_STATUSES):
                connection.execute(
                    "UPDATE workflow_runs SET resume_task_id='', "
                    "resume_flowfile_json=NULL WHERE run_id=?", (run_id,))
            return cursor.rowcount == 1

    def resume_wait(self, run_id: str, *, task_id: str, flowfile: Any,
                    lease_seconds: float) -> bool:
        """Atomically persist and resume one signalled Workflow Agent wait.

        The continuation is copied into the run row before the interaction wait
        is acknowledged as delivered. A crash after this method returns can
        therefore recover from the exact task without replaying the root turn.
        Time spent waiting for the user is added back to the wall-clock deadline.
        """
        run_id = _required(run_id, "run_id")
        task_id = _required(task_id, "task_id")
        from core.confirmation_store import ConfirmationStore
        serialized = ConfirmationStore._serialize_flowfile(flowfile)
        now = time.time()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if row is None or row["status"] != "waiting":
                return False
            active = connection.execute(
                """SELECT run_id, generation FROM workflow_active_runs
                   WHERE conversation_id=? AND agent_key=?""",
                (row["conversation_id"], row["agent_key"]),
            ).fetchone()
            if (active is None or active["run_id"] != run_id
                    or int(active["generation"]) != int(row["run_generation"])):
                return False
            waiting_since = float(row["waiting_since"] or now)
            deadline_text = str(row["deadline_at"] or "")
            if deadline_text:
                deadline = datetime.fromisoformat(deadline_text)
                deadline_text = (deadline + timedelta(
                    seconds=max(0.0, now - waiting_since))).isoformat()
            cursor = connection.execute(
                """UPDATE workflow_runs
                   SET status='running', reason=NULL, updated_at=?,
                       deadline_at=?, waiting_since=NULL, resume_task_id=?,
                       resume_flowfile_json=?
                   WHERE run_id=? AND status='waiting'""",
                (now, deadline_text, task_id, serialized, run_id),
            )
            if cursor.rowcount != 1:
                return False
            connection.execute(
                """UPDATE workflow_active_runs
                   SET lease_expires_at=?, updated_at=? WHERE run_id=?""",
                (now + max(1.0, float(lease_seconds)), now, run_id),
            )
        return True

    def clear_resume(self, run_id: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE workflow_runs SET resume_task_id='', "
                "resume_flowfile_json=NULL WHERE run_id=?", (run_id,))

    def pause_retryable_failure(
            self, run_id: str, *, error: WorkflowRunError,
            task_id: str, flowfile: Any, lease_seconds: float) -> bool:
        """Persist an exact retry checkpoint and pause the current generation."""
        if error.run_id != run_id or not error.retryable:
            raise ValueError("retryable workflow error does not match the run")
        task_id = _required(task_id, "task_id")
        from core.confirmation_store import ConfirmationStore
        serialized = ConfirmationStore._serialize_flowfile(flowfile)
        now = time.time()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """UPDATE workflow_runs
                   SET status='retryable_failed', reason=?, error_json=?,
                       updated_at=?, waiting_since=?, resume_task_id=?,
                       resume_flowfile_json=?
                   WHERE run_id=? AND status='running'
                     AND EXISTS (
                       SELECT 1 FROM workflow_active_runs active
                       WHERE active.run_id=workflow_runs.run_id
                         AND active.generation=workflow_runs.run_generation
                     )""",
                (error.message, _json(error.to_dict()), now, now, task_id,
                 serialized, run_id),
            )
            if cursor.rowcount != 1:
                return False
            connection.execute(
                """UPDATE workflow_active_runs
                   SET lease_expires_at=?, updated_at=? WHERE run_id=?""",
                (now + max(1.0, float(lease_seconds)), now, run_id),
            )
        return True

    def retry_failure(self, run_id: str, *, lease_seconds: float) -> bool:
        """CAS one paused retryable error back to running on the same run."""
        now = time.time()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if (row is None or row["status"] != "retryable_failed"
                    or not row["resume_task_id"] or not row["resume_flowfile_json"]):
                return False
            active = connection.execute(
                "SELECT run_id, generation FROM workflow_active_runs "
                "WHERE conversation_id=? AND agent_key=?",
                (row["conversation_id"], row["agent_key"]),
            ).fetchone()
            if (active is None or active["run_id"] != run_id
                    or int(active["generation"]) != int(row["run_generation"])):
                return False
            paused_since = float(row["waiting_since"] or now)
            deadline_text = str(row["deadline_at"] or "")
            if deadline_text:
                deadline = datetime.fromisoformat(deadline_text)
                deadline_text = (deadline + timedelta(
                    seconds=max(0.0, now - paused_since))).isoformat()
            cursor = connection.execute(
                """UPDATE workflow_runs
                   SET status='running', reason=NULL, updated_at=?,
                       deadline_at=?, waiting_since=NULL,
                       recovery_count=recovery_count+1
                   WHERE run_id=? AND status='retryable_failed'""",
                (now, deadline_text, run_id),
            )
            if cursor.rowcount != 1:
                return False
            connection.execute(
                """UPDATE workflow_active_runs
                   SET lease_expires_at=?, updated_at=? WHERE run_id=?""",
                (now + max(1.0, float(lease_seconds)), now, run_id),
            )
        return True

    def record_error(self, run_id: str, error: WorkflowRunError) -> bool:
        if error.run_id != run_id:
            raise ValueError("workflow error does not match the run")
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE workflow_runs SET error_json=? WHERE run_id=?",
                (_json(error.to_dict()), run_id),
            )
            return cursor.rowcount == 1

    def fail(self, run_id: str, reason: str) -> bool:
        run = self.get_run(run_id)
        if run is None or run["status"] in WORKFLOW_TERMINAL_STATUSES:
            return False
        target = "recovery_failed" if run["status"] == "committing" else "failed"
        return self.transition(run_id, run["status"], target, reason)

    def supersede(self, run_id: str, reason: str = "restart") -> bool:
        run = self.get_run(run_id)
        if run is None or run["status"] in WORKFLOW_TERMINAL_STATUSES:
            return False
        if run["status"] not in {
                "accepted", "running", "waiting", "retryable_failed", "committing"}:
            return False
        return self.transition(run_id, run["status"], "superseded", reason)

    def force_stop(self, run_id: str, reason: str = "force_stop") -> bool:
        run = self.get_run(run_id)
        if run is None or run["status"] in WORKFLOW_TERMINAL_STATUSES:
            return False
        if run["status"] == "accepted":
            return self.transition(run_id, "accepted", "cancelled", reason)
        if run["status"] in {
                "running", "waiting", "retryable_failed", "cancelling"}:
            return self.transition(
                run_id, run["status"], "force_stopped", reason)
        return False

    def stage_terminal(self, run_id: str, *, result,
                       assistant_payload: dict[str, Any],
                       terminal_event: dict[str, Any]) -> dict[str, Any]:
        """CAS running to committing while storing all stable saga identities."""
        assistant_msg_id = _required(
            assistant_payload.get("msg_id"), "assistant_msg_id")
        event_id = _required(terminal_event.get("event_id"), "event_id")
        result_status = str(
            terminal_event.get("status") or getattr(
                result, "status", "completed"))
        terminal_status = (
            "completed" if result_status == "no_change" else result_status
        )
        if terminal_status not in WORKFLOW_TERMINAL_STATUSES:
            raise ValueError("terminal status is invalid")
        answered = tuple(result.answered_turn_ids)
        now = time.time()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError("workflow run does not exist")
            if row["status"] == "committing":
                if (row["assistant_msg_id"] != assistant_msg_id
                        or row["terminal_event_id"] != event_id
                        or row["staged_result_json"] != _json(result.to_dict())
                        or row["assistant_payload_json"] != _json(
                            assistant_payload)
                        or row["terminal_event_json"] != _json(
                            terminal_event)
                        or row["terminal_status"] != terminal_status):
                    raise RuntimeError(
                        "committing run has different terminal identities")
                return self._row(row)
            if row["status"] != "running":
                raise RuntimeError(
                    f"workflow run cannot stage terminal from {row['status']}")
            cursor = connection.execute(
                """UPDATE workflow_runs SET status='committing',
                       staged_result_json=?, assistant_payload_json=?,
                       answered_turn_ids_json=?, assistant_msg_id=?,
                       terminal_event_id=?, terminal_event_json=?,
                       terminal_status=?, outbox_enqueued=1, updated_at=?
                   WHERE run_id=? AND status='running'""",
                (_json(result.to_dict()), _json(assistant_payload),
                 _json(answered), assistant_msg_id, event_id,
                 _json(terminal_event), terminal_status, now, run_id))
            if cursor.rowcount != 1:
                raise RuntimeError("terminal staging CAS lost")
            connection.execute(
                """INSERT INTO workflow_terminal_outbox(
                       event_id, run_id, event_json, state, created_at
                   ) VALUES (?, ?, ?, 'pending', ?)
                   ON CONFLICT(event_id) DO NOTHING""",
                (event_id, run_id, _json(terminal_event), now))
            row = connection.execute(
                "SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)
            ).fetchone()
        return self._row(row)

    def mark_message_committed(self, run_id: str) -> bool:
        return self._mark_committing(run_id, "message_committed")

    def mark_inbox_acknowledged(self, run_id: str) -> bool:
        return self._mark_committing(run_id, "inbox_acknowledged")

    def set_service_snapshot(
            self, run_id: str, snapshot: dict[str, Any]) -> dict[str, Any]:
        """Set the immutable service snapshot once for a live run."""
        encoded = _json(snapshot)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status, service_snapshot_json FROM workflow_runs "
                "WHERE run_id=?", (_required(run_id, "run_id"),)).fetchone()
            if row is None:
                raise KeyError("workflow run does not exist")
            current = json.loads(row["service_snapshot_json"])
            if current:
                if row["service_snapshot_json"] != encoded:
                    raise RuntimeError("workflow service snapshot is immutable")
            elif row["status"] not in {"accepted", "running"}:
                raise RuntimeError(
                    "workflow service snapshot cannot be set after execution")
            else:
                connection.execute(
                    "UPDATE workflow_runs SET service_snapshot_json=?, "
                    "updated_at=? WHERE run_id=?",
                    (encoded, time.time(), run_id))
            updated = connection.execute(
                "SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)
            ).fetchone()
        return self._row(updated)

    def set_authorization_ref(self, run_id: str,
                              authorization_ref: dict[str, Any]) -> dict[str, Any]:
        """Advance the exact authority snapshot without changing its lineage."""
        context_id = _required(
            authorization_ref.get("context_id"), "authorization context_id")
        root_turn_id = _required(
            authorization_ref.get("root_turn_id"), "authorization root_turn_id")
        revision = int(authorization_ref.get("revision") or 0)
        if revision < 1:
            raise ValueError("authorization revision must be positive")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT authorization_ref_json, root_turn_id FROM workflow_runs "
                "WHERE run_id=?", (_required(run_id, "run_id"),)).fetchone()
            if row is None:
                raise KeyError("workflow run does not exist")
            current = json.loads(row["authorization_ref_json"])
            if (current.get("context_id") != context_id
                    or row["root_turn_id"] != root_turn_id):
                raise ValueError("authorization lineage differs from workflow run")
            current_revision = int(current.get("revision") or 0)
            if revision < current_revision:
                raise ValueError("authorization revision cannot move backward")
            if revision > current_revision:
                connection.execute(
                    "UPDATE workflow_runs SET authorization_ref_json=?, "
                    "updated_at=? WHERE run_id=?",
                    (_json(authorization_ref), time.time(), run_id))
            updated = connection.execute(
                "SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)
            ).fetchone()
        return self._row(updated)

    def record_claimed_ids(self, run_id: str,
                           msg_ids: Iterable[str]) -> tuple[str, ...]:
        incoming = tuple(dict.fromkeys(
            str(value) for value in msg_ids if str(value)))
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT claimed_ids_json FROM workflow_runs WHERE run_id=?",
                (_required(run_id, "run_id"),)).fetchone()
            if row is None:
                raise KeyError("workflow run does not exist")
            merged = tuple(dict.fromkeys(
                (*json.loads(row["claimed_ids_json"]), *incoming)))
            connection.execute(
                """UPDATE workflow_runs SET claimed_ids_json=?, updated_at=?
                   WHERE run_id=?""", (_json(merged), time.time(), run_id))
        return merged

    def _mark_committing(self, run_id: str, field: str) -> bool:
        if field not in {"message_committed", "inbox_acknowledged"}:
            raise ValueError("unsupported commit marker")
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE workflow_runs SET {field}=1, updated_at=? "  # nosec B608 - field whitelist
                "WHERE run_id=? AND status='committing'",
                (time.time(), run_id))
            return cursor.rowcount == 1

    def pending_outbox(self, run_id: str | None = None
                       ) -> tuple[dict[str, Any], ...]:
        query = "SELECT * FROM workflow_terminal_outbox WHERE state='pending'"
        params: tuple[Any, ...] = ()
        if run_id is not None:
            query += " AND run_id=?"
            params = (run_id,)
        query += " ORDER BY created_at"
        with self._lock, self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return tuple(self._outbox_row(row) for row in rows)

    def record_outbox_attempt(self, event_id: str, delivered: bool) -> bool:
        now = time.time()
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """UPDATE workflow_terminal_outbox
                   SET attempts=attempts+1, last_attempt_at=?,
                       state=CASE WHEN ? THEN 'delivered' ELSE state END,
                       delivered_at=CASE WHEN ? THEN ? ELSE delivered_at END
                   WHERE event_id=?""",
                (now, int(delivered), int(delivered), now, event_id))
            return cursor.rowcount == 1

    def complete(self, run_id: str) -> bool:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT message_committed, inbox_acknowledged,
                          assistant_msg_id, terminal_event_id, terminal_status
                   FROM workflow_runs WHERE run_id=? AND status='committing'""",
                (run_id,)).fetchone()
            if row is None:
                existing = connection.execute(
                    "SELECT status FROM workflow_runs WHERE run_id=?", (run_id,)
                ).fetchone()
                return bool(
                    existing
                    and existing["status"] in WORKFLOW_TERMINAL_STATUSES)
            outbox = connection.execute(
                """SELECT state FROM workflow_terminal_outbox
                   WHERE run_id=?""", (run_id,)).fetchone()
            if (not row["message_committed"] or not row["inbox_acknowledged"]
                    or not row["assistant_msg_id"]
                    or not row["terminal_event_id"]
                    or outbox is None or outbox["state"] != "delivered"):
                return False
            now = time.time()
            terminal_status = str(row["terminal_status"] or "completed")
            if terminal_status not in WORKFLOW_TERMINAL_STATUSES:
                return False
            cursor = connection.execute(
                """UPDATE workflow_runs SET status=?,
                       terminal_at=?, updated_at=?
                   WHERE run_id=? AND status='committing'""",
                (terminal_status, now, now, run_id))
            connection.execute(
                "DELETE FROM workflow_active_runs WHERE run_id=?", (run_id,))
            return cursor.rowcount == 1

    def is_current_generation(self, run_id: str) -> bool:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """SELECT 1 FROM workflow_runs AS r
                   JOIN workflow_active_runs AS a
                     ON a.conversation_id=r.conversation_id
                    AND a.agent_key=r.agent_key
                    AND a.run_id=r.run_id
                    AND a.generation=r.run_generation
                   WHERE r.run_id=?""", (run_id,)).fetchone()
        return row is not None

    def reacquire(self, run_id: str, lease_seconds: float) -> bool:
        now = time.time()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT * FROM workflow_runs WHERE run_id=?
                   AND status IN (
                     'accepted','running','waiting','retryable_failed','cancelling'
                   )""",
                (run_id,)).fetchone()
            if row is None:
                return False
            active = connection.execute(
                """SELECT run_id, generation FROM workflow_active_runs
                   WHERE conversation_id=? AND agent_key=?""",
                (row["conversation_id"], row["agent_key"])).fetchone()
            if (active is not None and active["run_id"] != run_id
                    and int(active["generation"]) >= row["run_generation"]):
                return False
            connection.execute(
                "DELETE FROM workflow_llm_reservations WHERE run_id=?",
                (run_id,))
            connection.execute(
                """INSERT INTO workflow_active_runs VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(conversation_id, agent_key) DO UPDATE SET
                       run_id=excluded.run_id,
                       generation=excluded.generation,
                       lease_expires_at=excluded.lease_expires_at,
                       updated_at=excluded.updated_at""",
                (row["conversation_id"], row["agent_key"], run_id,
                 row["run_generation"],
                 now + max(1.0, float(lease_seconds)), now))
            connection.execute(
                """UPDATE workflow_runs SET recovery_count=recovery_count+1,
                       updated_at=? WHERE run_id=?""", (now, run_id))
            return True

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)
            ).fetchone()
        return self._row(row) if row else None

    def get_record(self, run_id: str) -> WorkflowRunRecord | None:
        row = self.get_run(run_id)
        if row is None:
            return None
        return WorkflowRunRecord(
            run_id=row["run_id"],
            conversation_id=row["conversation_id"],
            agent_name=row["agent_name"],
            root_turn_id=row["root_turn_id"],
            run_generation=row["run_generation"],
            flow_ref=row["flow_ref"],
            status=row["status"],
            reason=row["reason"],
            answered_turn_ids=tuple(row["answered_turn_ids"]),
            assistant_msg_id=row["assistant_msg_id"],
            terminal_event_id=row["terminal_event_id"],
            created_at=_utc(row["created_at"]),
            updated_at=_utc(row["updated_at"]),
            terminal_at=(
                _utc(row["terminal_at"])
                if row["terminal_at"] is not None else None),
            recovery_count=row["recovery_count"],
        )

    def list_recoverable(self, statuses: Iterable[str] = _RECOVERABLE
                         ) -> tuple[dict[str, Any], ...]:
        values = tuple(dict.fromkeys(str(value) for value in statuses))
        if not values:
            return ()
        marks = ",".join("?" for _ in values)
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM workflow_runs "  # nosec B608 - placeholders only
                f"WHERE status IN ({marks}) "
                "ORDER BY CASE status WHEN 'committing' THEN 0 ELSE 1 END, "
                "created_at", values).fetchall()
        return tuple(self._row(row) for row in rows)

    def list_runs(self, conversation_id: str, agent_name: str = "",
                  limit: int = 50, offset: int = 0
                  ) -> tuple[dict[str, Any], ...]:
        """List newest durable runs for one authorized conversation."""
        conversation_id = _required(conversation_id, "conversation_id")
        count = int(limit)
        position = int(offset)
        if count < 1:
            raise ValueError("limit must be positive")
        if position < 0:
            raise ValueError("offset must be non-negative")
        values: list[Any] = [conversation_id]
        where = "conversation_id=?"
        if str(agent_name or "").strip():
            where += " AND agent_key=?"
            values.append(_agent(agent_name))
        values.extend((count, position))
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM workflow_runs WHERE {where} "  # nosec B608 - fixed clauses only
                "ORDER BY created_at DESC LIMIT ? OFFSET ?", values).fetchall()
        return tuple(self._row(row) for row in rows)

    def append_event(self, run_id: str, event_type: str,
                     data: dict[str, Any]) -> dict[str, Any]:
        run_id = _required(run_id, "run_id")
        event_type = _required(event_type, "event_type")
        now = time.time()
        event_id = str(uuid.uuid4())
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT last_event_sequence FROM workflow_runs
                   WHERE run_id=?""", (run_id,)).fetchone()
            if row is None:
                raise KeyError("workflow run does not exist")
            sequence = int(row["last_event_sequence"]) + 1
            event = {
                "event_id": event_id, "run_id": run_id,
                "sequence": sequence, "event_type": event_type,
                "timestamp": _utc(now), "data": dict(data),
            }
            connection.execute(
                """INSERT INTO workflow_run_events
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (run_id, sequence, event_id, event_type, _json(event), now))
            connection.execute(
                """UPDATE workflow_runs SET last_event_sequence=?, updated_at=?
                   WHERE run_id=?""", (sequence, now, run_id))
        return event

    def append_event_once(
            self, run_id: str, event_type: str, data: dict[str, Any],
            *, idempotency_key: str) -> tuple[dict[str, Any], bool]:
        """Append one idempotent run event without creating a board-side store.

        The existing immutable event journal remains the source of truth.  The
        transaction serializes lookup and insertion, so concurrent retries of
        the same UI command observe the original event.
        """

        run_id = _required(run_id, "run_id")
        event_type = _required(event_type, "event_type")
        key = _required(idempotency_key, "idempotency_key")
        payload = dict(data)
        payload["idempotency_key"] = key
        now = time.time()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT last_event_sequence FROM workflow_runs WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise KeyError("workflow run does not exist")
            existing_rows = connection.execute(
                "SELECT event_json FROM workflow_run_events "
                "WHERE run_id=? AND event_type=? ORDER BY sequence",
                (run_id, event_type),
            ).fetchall()
            for existing_row in existing_rows:
                existing = json.loads(existing_row["event_json"])
                existing_data = existing.get("data")
                if (isinstance(existing_data, dict)
                        and existing_data.get("idempotency_key") == key):
                    if existing_data != payload:
                        raise ValueError(
                            "idempotency key already identifies a different event")
                    return existing, False
            sequence = int(row["last_event_sequence"]) + 1
            event = {
                "event_id": str(uuid.uuid4()), "run_id": run_id,
                "sequence": sequence, "event_type": event_type,
                "timestamp": _utc(now), "data": payload,
            }
            connection.execute(
                """INSERT INTO workflow_run_events
                   (run_id, sequence, event_id, event_type, event_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (run_id, sequence, event["event_id"], event_type,
                 _json(event), now),
            )
            connection.execute(
                "UPDATE workflow_runs SET last_event_sequence=?, updated_at=? "
                "WHERE run_id=?", (sequence, now, run_id),
            )
        return event, True

    def list_events(self, run_id: str) -> tuple[dict[str, Any], ...]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """SELECT event_json FROM workflow_run_events
                   WHERE run_id=? ORDER BY sequence""", (run_id,)).fetchall()
        return tuple(json.loads(row["event_json"]) for row in rows)

    def delete_conversation(self, conversation_id: str) -> int:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM workflow_runs WHERE conversation_id=?",
                (_required(conversation_id, "conversation_id"),))
            connection.execute(
                "DELETE FROM workflow_generations WHERE conversation_id=?",
                (conversation_id,))
            return int(cursor.rowcount)

    def delete_terminal(self, run_id: str, conversation_id: str) -> bool:
        values = tuple(sorted(WORKFLOW_TERMINAL_STATUSES))
        marks = ",".join("?" for _ in values)
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                f"DELETE FROM workflow_runs "  # nosec B608 - placeholders only
                f"WHERE run_id=? AND conversation_id=? "
                f"AND status IN ({marks})",
                (_required(run_id, "run_id"),
                 _required(conversation_id, "conversation_id"), *values))
            return cursor.rowcount == 1

    def prune_terminal(self, retention_seconds: float,
                       *, now: float | None = None) -> int:
        timestamp = time.time() if now is None else float(now)
        cutoff = timestamp - max(0.0, float(retention_seconds))
        values = tuple(sorted(WORKFLOW_TERMINAL_STATUSES))
        marks = ",".join("?" for _ in values)
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                f"DELETE FROM workflow_runs "  # nosec B608 - placeholders only
                f"WHERE status IN ({marks}) AND updated_at < ?",
                (*values, cutoff))
            return int(cursor.rowcount)

    @staticmethod
    def _outbox_row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["event"] = json.loads(result.pop("event_json"))
        return result

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["deadline_at"] = str(result.get("deadline_at") or "") or None
        for key in (
            "request_json", "ingress_source_json", "flow_ref_json",
            "parameters_json", "binding_json",
            "service_snapshot_json", "limits_json", "authorization_ref_json",
            "claimed_ids_json", "usage_json", "staged_result_json",
            "assistant_payload_json", "answered_turn_ids_json",
            "terminal_event_json", "parent_invocation_json",
            "error_json",
        ):
            raw = result.pop(key)
            result[key[:-5]] = json.loads(raw) if raw is not None else None
        for key in (
            "message_committed", "inbox_acknowledged", "outbox_enqueued",
            "publish_to_conversation"):
            result[key] = bool(result[key])
        return result


def new_terminal_identities(run_id: str) -> tuple[str, str]:
    """Allocate the stable message and event identities exactly once."""
    return f"workflow:{_required(run_id, 'run_id')}", str(uuid.uuid4())


__all__ = [
    "WorkflowBudgetExceeded", "WorkflowRunStore", "new_terminal_identities",
]
