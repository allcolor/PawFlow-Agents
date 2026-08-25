"""Durable parent continuations for Workflow Agent invocations from flows."""

from __future__ import annotations

import base64
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import core.paths as _paths
from core import FlowFile
from core.resource_identity import ResourceRef

MAX_FLOW_INVOCATION_DEPTH = 8
_TERMINAL_STATES = frozenset({"delivered", "cancelled"})


def _required(value: Any, name: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{name} is required")
    return result


def _json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _serialize_flowfile(flowfile: FlowFile) -> str:
    return _json({
        "content_b64": base64.b64encode(
            flowfile.get_content() or b"").decode("ascii"),
        "attributes": flowfile.get_attributes(),
        "process_id": flowfile.process_id,
        "created_at": flowfile.created_at.isoformat(),
    })


def _restore_flowfile(payload: str) -> FlowFile:
    from datetime import datetime

    value = json.loads(payload)
    return FlowFile(
        content=base64.b64decode(value.get("content_b64") or ""),
        attributes=dict(value.get("attributes") or {}),
        process_id=_required(value.get("process_id"), "flowfile process_id"),
        created_at=datetime.fromisoformat(
            _required(value.get("created_at"), "flowfile created_at")),
    )


def validate_flow_invocation_ancestry(
    *,
    agent_ref: ResourceRef,
    flow_ref: ResourceRef,
    invocation_depth: int,
    ancestor_agent_refs: tuple[ResourceRef, ...],
    ancestor_flow_refs: tuple[ResourceRef, ...],
) -> None:
    """Reject recursive exact-resource ancestry and excessive nesting."""

    depth = int(invocation_depth)
    if depth < 1 or depth > MAX_FLOW_INVOCATION_DEPTH:
        raise ValueError(
            f"Workflow Agent invocation depth exceeds {MAX_FLOW_INVOCATION_DEPTH}")
    if agent_ref in ancestor_agent_refs:
        raise ValueError("Workflow Agent repeats an ancestor agent ref")
    if flow_ref in ancestor_flow_refs:
        raise ValueError("Workflow Agent repeats an ancestor flow ref")


class WorkflowParentInvocationStore:
    """One durable parent FlowFile continuation per logical child invocation."""

    _instance: WorkflowParentInvocationStore | None = None
    _instance_lock = threading.Lock()

    @classmethod
    def instance(cls) -> WorkflowParentInvocationStore:
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
            _paths.RUNTIME_DIR / "workflow_parent_invocations.sqlite3")

    def _connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS workflow_parent_invocations (
                    invocation_id TEXT PRIMARY KEY,
                    instance_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    flowfile_process_id TEXT NOT NULL,
                    flowfile_json TEXT NOT NULL,
                    parent_json TEXT NOT NULL,
                    child_run_id TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL CHECK (
                        state IN ('created','submitted','resolved','delivered',
                                  'cancelled')),
                    terminal_event_id TEXT,
                    terminal_event_json TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    delivered_at REAL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS
                    idx_workflow_parent_child
                    ON workflow_parent_invocations(child_run_id)
                    WHERE child_run_id != '';
                CREATE UNIQUE INDEX IF NOT EXISTS
                    idx_workflow_parent_terminal
                    ON workflow_parent_invocations(terminal_event_id)
                    WHERE terminal_event_id IS NOT NULL;
                CREATE INDEX IF NOT EXISTS
                    idx_workflow_parent_instance_state
                    ON workflow_parent_invocations(instance_id, state);
                """
            )

    def create(self, *, parent: dict[str, Any],
               flowfile: FlowFile) -> dict[str, Any]:
        invocation_id = _required(parent.get("invocation_id"), "invocation_id")
        instance_id = _required(parent.get("instance_id"), "instance_id")
        task_id = _required(parent.get("task_id"), "task_id")
        process_id = _required(
            parent.get("flowfile_process_id"), "flowfile_process_id")
        if process_id != flowfile.process_id:
            raise ValueError("parent FlowFile process identity differs")
        encoded_parent = _json(parent)
        encoded_flowfile = _serialize_flowfile(flowfile)
        now = time.time()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM workflow_parent_invocations "
                "WHERE invocation_id=?", (invocation_id,)).fetchone()
            if row is not None:
                if (
                    row["instance_id"] != instance_id
                    or row["task_id"] != task_id
                    or row["flowfile_process_id"] != process_id
                    or row["parent_json"] != encoded_parent
                    or row["flowfile_json"] != encoded_flowfile
                ):
                    raise ValueError(
                        "invocation_id already identifies a different parent")
                return self._row(row)
            connection.execute(
                """INSERT INTO workflow_parent_invocations(
                       invocation_id, instance_id, task_id,
                       flowfile_process_id, flowfile_json, parent_json,
                       state, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, 'created', ?, ?)""",
                (invocation_id, instance_id, task_id, process_id,
                 encoded_flowfile, encoded_parent, now, now),
            )
            row = connection.execute(
                "SELECT * FROM workflow_parent_invocations "
                "WHERE invocation_id=?", (invocation_id,)).fetchone()
        return self._row(row)

    def bind_child(self, invocation_id: str, run_id: str) -> dict[str, Any]:
        invocation_id = _required(invocation_id, "invocation_id")
        run_id = _required(run_id, "child run_id")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM workflow_parent_invocations "
                "WHERE invocation_id=?", (invocation_id,)).fetchone()
            if row is None:
                raise KeyError("parent invocation does not exist")
            if row["child_run_id"] and row["child_run_id"] != run_id:
                raise ValueError("parent invocation is bound to another child run")
            if not row["child_run_id"]:
                connection.execute(
                    """UPDATE workflow_parent_invocations
                       SET child_run_id=?, state='submitted', updated_at=?
                       WHERE invocation_id=? AND state='created'""",
                    (run_id, time.time(), invocation_id),
                )
            row = connection.execute(
                "SELECT * FROM workflow_parent_invocations "
                "WHERE invocation_id=?", (invocation_id,)).fetchone()
        return self._row(row)

    def get(self, invocation_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM workflow_parent_invocations "
                "WHERE invocation_id=?", (_required(
                    invocation_id, "invocation_id"),)).fetchone()
        return self._row(row) if row is not None else None

    def has_pending(self, instance_id: str) -> bool:
        if not instance_id:
            return False
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """SELECT 1 FROM workflow_parent_invocations
                   WHERE instance_id=? AND state NOT IN ('delivered','cancelled')
                   LIMIT 1""", (instance_id,)).fetchone()
        return row is not None

    def detach(self, invocation_id: str) -> bool:
        """Close a fire-and-forget parent after its stable child is bound."""

        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """UPDATE workflow_parent_invocations
                   SET state='delivered', delivered_at=?, updated_at=?
                   WHERE invocation_id=? AND state='submitted'""",
                (time.time(), time.time(),
                 _required(invocation_id, "invocation_id")),
            )
        return cursor.rowcount == 1

    def deliver_terminal(self, invocation_id: str,
                         event: dict[str, Any]) -> bool:
        invocation_id = _required(invocation_id, "invocation_id")
        event_id = _required(event.get("event_id"), "terminal event_id")
        encoded = _json(event)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM workflow_parent_invocations "
                "WHERE invocation_id=?", (invocation_id,)).fetchone()
            if row is None:
                raise KeyError("parent invocation does not exist")
            if row["state"] == "delivered":
                if (row["terminal_event_id"] != event_id
                        or row["terminal_event_json"] != encoded):
                    raise ValueError(
                        "delivered invocation has a different terminal event")
                return True
            if row["state"] == "cancelled":
                return True
            if row["child_run_id"] and row["child_run_id"] != str(
                    event.get("run_id") or row["child_run_id"]):
                raise ValueError("terminal event belongs to another child run")
            if row["terminal_event_id"] not in {None, event_id}:
                raise ValueError("parent invocation already has another terminal")
            if row["terminal_event_json"] not in {None, encoded}:
                raise ValueError("terminal event payload changed")
            connection.execute(
                """UPDATE workflow_parent_invocations
                   SET state='resolved', terminal_event_id=?,
                       terminal_event_json=?, updated_at=?
                   WHERE invocation_id=?""",
                (event_id, encoded, time.time(), invocation_id),
            )
            row = connection.execute(
                "SELECT * FROM workflow_parent_invocations "
                "WHERE invocation_id=?", (invocation_id,)).fetchone()

        try:
            from core.executor_registry import ExecutorRegistry
            executor = ExecutorRegistry.get_instance().get(row["instance_id"])
        except Exception:
            executor = None
        if executor is None or not getattr(executor, "is_running", False):
            return False

        flowfile = _restore_flowfile(row["flowfile_json"])
        status = str(event.get("status") or "failure")
        known = {
            "completed", "no_change", "failed", "cancelled", "timed_out",
            "superseded", "budget_exceeded", "force_stopped",
        }
        relationship = status if status in known else "failure"
        flowfile.set_attribute("workflow.agent.status", status)
        flowfile.set_attribute("workflow.agent.run_id", str(
            event.get("run_id") or row["child_run_id"]))
        flowfile.set_attribute(
            "workflow.agent.response", str(event.get("response") or ""))
        flowfile.set_attribute(
            "workflow.agent.artifacts", _json(event.get("artifacts") or []))
        flowfile.set_attribute(
            "workflow.agent.metrics", _json(event.get("metrics") or {}))
        flowfile.set_attribute(
            "workflow.agent.answered_turn_ids",
            _json(event.get("answered_turn_ids") or []),
        )
        flowfile.set_attribute("route.relationship", relationship)
        if not executor.inject(flowfile, entry_task_id=row["task_id"]):
            return False

        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """UPDATE workflow_parent_invocations
                   SET state='delivered', delivered_at=?, updated_at=?
                   WHERE invocation_id=? AND state='resolved'
                     AND terminal_event_id=?""",
                (time.time(), time.time(), invocation_id, event_id),
            )
        return cursor.rowcount == 1

    def cancel_instance(self, instance_id: str) -> tuple[str, ...]:
        """Mark pending parents cancelled and return propagate-policy child IDs."""

        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """SELECT invocation_id, child_run_id, parent_json
                   FROM workflow_parent_invocations
                   WHERE instance_id=? AND state NOT IN ('delivered','cancelled')""",
                (_required(instance_id, "instance_id"),),
            ).fetchall()
            now = time.time()
            for row in rows:
                connection.execute(
                    """UPDATE workflow_parent_invocations
                       SET state='cancelled', updated_at=?
                       WHERE invocation_id=?""",
                    (now, row["invocation_id"]),
                )
        return tuple(
            row["child_run_id"] for row in rows
            if row["child_run_id"]
            and json.loads(row["parent_json"]).get(
                "cancellation_policy") == "propagate"
        )

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["parent"] = json.loads(result.pop("parent_json"))
        result["flowfile"] = _restore_flowfile(result.pop("flowfile_json"))
        raw_event = result.pop("terminal_event_json")
        result["terminal_event"] = (
            json.loads(raw_event) if raw_event is not None else None)
        return result


__all__ = [
    "MAX_FLOW_INVOCATION_DEPTH",
    "WorkflowParentInvocationStore",
    "validate_flow_invocation_ancestry",
]
