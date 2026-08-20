"""Durable user confirmations and durable flow wait/notify signals.

Two first-class, fully asynchronous primitives backed by one SQLite store:

- **ConfirmationRequest**: an agent (``request_confirmation`` tool) or a flow
  (``requestConfirmation`` task) asks the user something — yes/no, single
  choice, or multi choice from a list. The request is DURABLE: it survives
  reloads and restarts, shows in the conversation and in the pending panel,
  and the user answers whenever they want (hours or days later). On answer
  the requester resumes: an agent is woken through the PollScheduler with
  the answer as its wake reason; a flow resumes through the durable signal
  ``confirmation:<request_id>`` (see below). ``expires_at`` is optional.

- **Durable wait/notify**: a deployed flow parks a FlowFile
  (``durableWait`` task) on a named signal for as long as the configured
  timeout allows — seconds to years, or forever. ``notify_signal`` (from the
  ``durableNotify`` task, a confirmation answer, or any code) resolves the
  wait; the parked FlowFile is restored with the resolution attributes and
  re-injected at the wait task itself, which passes it through. Delivery
  survives restarts: a resolved-but-undelivered wait is retried by the
  sweeper until its flow instance is running again. Signals are also
  VALUES: the latest notify per signal id is remembered, so a wait that
  parks after the notify passes through immediately (no lost-notify race).

Everything is additive: the in-memory ``SignalRegistry`` wait/notify tasks
keep their fast intra-process semantics for short synchronizations.
"""

from __future__ import annotations

import base64
import json
import logging
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import core.paths as _paths

logger = logging.getLogger(__name__)

_MODES = frozenset({"confirm", "choice", "multi"})
_SWEEP_SECONDS = 15.0

# Human-friendly timeout suffixes for durable waits — days, months, years
# are first-class because that is the whole point of a DURABLE wait.
_TIMEOUT_UNITS = {
    "s": 1, "m": 60, "h": 3600, "d": 86400,
    "w": 7 * 86400, "mo": 30 * 86400, "y": 365 * 86400,
}


def parse_timeout_seconds(value: Any) -> float:
    """Parse ``3600``, ``"90s"``, ``"12h"``, ``"30d"``, ``"6mo"``, ``"2y"``.

    Returns 0 for empty/0 (= no timeout, wait forever). Raises ValueError
    on garbage so a misconfigured flow fails at deploy time, not silently.
    """
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        if value < 0:
            raise ValueError("timeout must be >= 0")
        return float(value)
    text = str(value).strip().lower()
    if not text:
        return 0.0
    for suffix in ("mo", "y", "w", "d", "h", "m", "s"):
        if text.endswith(suffix):
            number = text[: -len(suffix)].strip()
            return float(number) * _TIMEOUT_UNITS[suffix]
    return float(text)


def normalize_options(options: Any) -> List[Dict[str, str]]:
    """Accept ["a", "b"] or [{value,label}] and return [{value,label}]."""
    out: List[Dict[str, str]] = []
    for opt in options or []:
        if isinstance(opt, dict):
            value = str(opt.get("value", opt.get("label", "")) or "").strip()
            label = str(opt.get("label", value) or value)
        else:
            value = str(opt).strip()
            label = value
        if value:
            out.append({"value": value, "label": label})
    return out


class ConfirmationStore:
    """Thread-safe SQLite state for confirmations and durable waits."""

    _instance: Optional["ConfirmationStore"] = None
    _instance_lock = threading.Lock()

    @classmethod
    def instance(cls) -> "ConfirmationStore":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self, database_path: Optional[Path] = None) -> None:
        self._database_path = Path(
            database_path or (_paths.DATA_DIR / "confirmations.db"))
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._sweeper_started = False
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS confirmations (
                    request_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    requester_kind TEXT NOT NULL,
                    requester TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    message TEXT NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'confirm',
                    options_json TEXT NOT NULL DEFAULT '[]',
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'pending',
                    answer_json TEXT NOT NULL DEFAULT '',
                    answered_by TEXT NOT NULL DEFAULT '',
                    answered_at REAL NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_confirm_user_status
                    ON confirmations(user_id, status, created_at);
                CREATE INDEX IF NOT EXISTS idx_confirm_conv
                    ON confirmations(conversation_id, status);

                CREATE TABLE IF NOT EXISTS durable_waits (
                    wait_id TEXT PRIMARY KEY,
                    signal_id TEXT NOT NULL,
                    instance_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    flowfile_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'waiting',
                    resolution_json TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_wait_signal
                    ON durable_waits(signal_id, status);
                CREATE INDEX IF NOT EXISTS idx_wait_status
                    ON durable_waits(status, expires_at);

                CREATE TABLE IF NOT EXISTS durable_signal_values (
                    signal_id TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL DEFAULT '',
                    fired_at REAL NOT NULL
                );
                """
            )

    # ── Confirmations ────────────────────────────────────────────────

    @staticmethod
    def _confirmation_row(row: sqlite3.Row) -> Dict[str, Any]:
        data = dict(row)
        data["options"] = json.loads(data.pop("options_json") or "[]")
        raw_answer = data.pop("answer_json") or ""
        data["answer"] = json.loads(raw_answer) if raw_answer else None
        return data

    def create_confirmation(
        self, *, conversation_id: str, user_id: str, requester_kind: str,
        requester: str, message: str, title: str = "",
        mode: str = "confirm", options: Any = None,
        expires_in_seconds: float = 0,
    ) -> Dict[str, Any]:
        if not conversation_id or not user_id:
            raise ValueError("conversation_id and user_id are required")
        if not (message or "").strip():
            raise ValueError("message is required")
        mode = (mode or "confirm").strip().lower()
        if mode not in _MODES:
            raise ValueError("mode must be confirm, choice, or multi")
        opts = normalize_options(options)
        if mode == "confirm" and not opts:
            opts = [{"value": "yes", "label": "Yes"},
                    {"value": "no", "label": "No"}]
        if mode in ("choice", "multi") and len(opts) < 2:
            raise ValueError(f"mode '{mode}' requires at least 2 options")
        now = time.time()
        request_id = "req_" + uuid.uuid4().hex[:16]
        record = {
            "request_id": request_id, "conversation_id": conversation_id,
            "user_id": user_id, "requester_kind": requester_kind,
            "requester": requester or "", "title": title or "",
            "message": message, "mode": mode, "options": opts,
            "created_at": now,
            "expires_at": (now + expires_in_seconds) if expires_in_seconds else 0,
            "status": "pending", "answer": None,
            "answered_by": "", "answered_at": 0,
        }
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO confirmations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (request_id, conversation_id, user_id, requester_kind,
                 requester or "", title or "", message, mode,
                 json.dumps(opts, ensure_ascii=False), now,
                 record["expires_at"], "pending", "", "", 0))
        self.ensure_sweeper()
        self._publish(conversation_id, "confirmation_request", record)
        return record

    def get_confirmation(self, request_id: str) -> Optional[Dict[str, Any]]:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM confirmations WHERE request_id=?",
                (request_id,)).fetchone()
        return self._confirmation_row(row) if row else None

    def list_confirmations(self, *, user_id: str, conversation_id: str = "",
                           status: str = "pending",
                           limit: int = 100) -> List[Dict[str, Any]]:
        query = "SELECT * FROM confirmations WHERE user_id=?"
        params: List[Any] = [user_id]
        if conversation_id:
            query += " AND conversation_id=?"
            params.append(conversation_id)
        if status and status != "all":
            query += " AND status=?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, min(int(limit or 100), 500)))
        with self._lock, self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._confirmation_row(r) for r in rows]

    def respond(self, request_id: str, answer: Any,
                answered_by: str = "") -> Dict[str, Any]:
        """Answer a pending confirmation and resume its requester.

        ``answer``: for mode confirm/choice a single option value (string);
        for multi a list of option values. Validated against the options.
        """
        record = self.get_confirmation(request_id)
        if not record:
            raise KeyError("Unknown confirmation request")
        if record["status"] != "pending":
            raise ValueError(f"Confirmation is already {record['status']}")
        if record["expires_at"] and time.time() > record["expires_at"]:
            self._set_status(request_id, "expired")
            raise ValueError("Confirmation has expired")
        valid = {o["value"] for o in record["options"]}
        if record["mode"] == "multi":
            values = [str(v) for v in (answer if isinstance(answer, list) else [answer])]
            bad = [v for v in values if v not in valid]
            if not values or bad:
                raise ValueError(f"Invalid answer values: {bad or 'empty'}")
            normalized: Any = values
        else:
            value = str(answer[0] if isinstance(answer, list) and answer else answer)
            if value not in valid:
                raise ValueError(f"Invalid answer value: {value!r}")
            normalized = value
        now = time.time()
        with self._lock, self._connect() as connection:
            updated = connection.execute(
                "UPDATE confirmations SET status='answered', answer_json=?, "
                "answered_by=?, answered_at=? WHERE request_id=? AND status='pending'",
                (json.dumps(normalized, ensure_ascii=False), answered_by or "",
                 now, request_id)).rowcount
        if not updated:
            raise ValueError("Confirmation was answered concurrently")
        record.update(status="answered", answer=normalized,
                      answered_by=answered_by or "", answered_at=now)
        self._publish(record["conversation_id"], "confirmation_answered", record)
        self._resume_requester(record)
        return record

    def cancel(self, request_id: str, cancelled_by: str = "") -> bool:
        record = self.get_confirmation(request_id)
        if not record or record["status"] != "pending":
            return False
        self._set_status(request_id, "cancelled")
        record["status"] = "cancelled"
        self._publish(record["conversation_id"], "confirmation_answered", record)
        # A cancelled request still resolves its signal so a durably waiting
        # flow branch is released (with status carried in the value).
        self.notify_signal(f"confirmation:{request_id}",
                           {"status": "cancelled", "answer": None})
        return True

    def _set_status(self, request_id: str, status: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE confirmations SET status=? WHERE request_id=?",
                (status, request_id))

    def _resume_requester(self, record: Dict[str, Any]) -> None:
        """Route the answer back to whoever asked."""
        answer_json = json.dumps(record["answer"], ensure_ascii=False)
        # Flows (and anything else) can durably wait on the request signal.
        self.notify_signal(
            f"confirmation:{record['request_id']}",
            {"status": "answered", "answer": record["answer"],
             "answered_by": record["answered_by"]})
        if record["requester_kind"] == "agent":
            try:
                from core.poll_scheduler import PollScheduler
                from tasks.ai.agent_loop import AgentLoopTask
                agent = record.get("requester", "")
                # "[scheduled:<agent>]" is the reason prefix the poller's
                # _extract_agent_from_reasons recognizes to target the agent.
                reason = (
                    (f"[scheduled:{agent}] " if agent else "")
                    + f"[confirmation:{record['request_id']}] The user answered "
                    f"your confirmation request \"{record['message'][:120]}\": "
                    f"{answer_json}. Continue from where you left off.")
                PollScheduler.instance().schedule_delay(
                    record["conversation_id"], 1.0,
                    key=f"confirmation::{record['request_id']}",
                    reason=reason, user_id=record["user_id"])
                AgentLoopTask.wake_poller()
            except Exception:
                logger.exception("confirmation: agent wake failed")

    # ── Durable wait/notify ──────────────────────────────────────────

    @staticmethod
    def _serialize_flowfile(flowfile: Any) -> str:
        content = flowfile.get_content() or b""
        return json.dumps({
            "content_b64": base64.b64encode(content).decode("ascii"),
            "attributes": flowfile.get_attributes(),
        })

    @staticmethod
    def _restore_flowfile(payload: str) -> Any:
        from core import FlowFile
        data = json.loads(payload)
        flowfile = FlowFile(
            content=base64.b64decode(data.get("content_b64", "") or ""))
        for key, value in (data.get("attributes") or {}).items():
            flowfile.set_attribute(key, value)
        return flowfile

    def park_wait(self, *, signal_id: str, instance_id: str, task_id: str,
                  flowfile: Any, timeout_seconds: float = 0) -> Optional[str]:
        """Park a FlowFile on ``signal_id``.

        If the signal already has a stored value the wait resolves
        IMMEDIATELY: returns None and the caller passes the FlowFile through
        (the value is consumed). Otherwise returns the wait id.
        """
        if not signal_id:
            raise ValueError("signal_id is required")
        existing = self.consume_signal_value(signal_id)
        if existing is not None:
            flowfile.set_attribute("durable.wait.status", "signaled")
            flowfile.set_attribute("durable.wait.value",
                                   json.dumps(existing, ensure_ascii=False))
            return None
        now = time.time()
        wait_id = "wait_" + uuid.uuid4().hex[:16]
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO durable_waits VALUES (?,?,?,?,?,?,?,?,?)",
                (wait_id, signal_id, instance_id, task_id,
                 self._serialize_flowfile(flowfile), now,
                 (now + timeout_seconds) if timeout_seconds else 0,
                 "waiting", ""))
        self.ensure_sweeper()
        logger.info("[durable-wait] parked %s on signal %r (flow %s/%s, "
                    "timeout %s)", wait_id, signal_id, instance_id[:12],
                    task_id, timeout_seconds or "none")
        return wait_id

    def notify_signal(self, signal_id: str, value: Any = None) -> int:
        """Fire a durable signal: resolve parked waits, remember the value.

        Returns the number of waits resolved. When no wait was parked the
        value is stored so the NEXT wait on this signal passes through
        immediately (latest value wins).
        """
        if not signal_id:
            raise ValueError("signal_id is required")
        resolution = json.dumps(
            {"value": value, "fired_at": time.time()}, ensure_ascii=False)
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT wait_id FROM durable_waits WHERE signal_id=? AND "
                "status='waiting'", (signal_id,)).fetchall()
            for row in rows:
                connection.execute(
                    "UPDATE durable_waits SET status='resolved', "
                    "resolution_json=? WHERE wait_id=?",
                    (resolution, row["wait_id"]))
            if not rows:
                connection.execute(
                    "INSERT INTO durable_signal_values VALUES (?,?,?) "
                    "ON CONFLICT(signal_id) DO UPDATE SET value_json=excluded.value_json, "
                    "fired_at=excluded.fired_at",
                    (signal_id, json.dumps(value, ensure_ascii=False),
                     time.time()))
        self.ensure_sweeper()
        if rows:
            self.deliver_resolved()
        return len(rows)

    def consume_signal_value(self, signal_id: str) -> Optional[Any]:
        """Pop the stored value of a signal (None when there is none)."""
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT value_json FROM durable_signal_values WHERE signal_id=?",
                (signal_id,)).fetchone()
            if not row:
                return None
            connection.execute(
                "DELETE FROM durable_signal_values WHERE signal_id=?",
                (signal_id,))
        try:
            return json.loads(row["value_json"]) if row["value_json"] else None
        except ValueError:
            return None

    def list_waits(self, status: str = "waiting",
                   limit: int = 200) -> List[Dict[str, Any]]:
        query = "SELECT wait_id, signal_id, instance_id, task_id, created_at, " \
                "expires_at, status FROM durable_waits"
        params: List[Any] = []
        if status and status != "all":
            query += " WHERE status=?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, min(int(limit or 200), 1000)))
        with self._lock, self._connect() as connection:
            return [dict(r) for r in connection.execute(query, params).fetchall()]

    def deliver_resolved(self) -> int:
        """Re-inject resolved/timed-out waits whose flow instance is running.

        Idempotent and restart-safe: an undeliverable wait (instance not
        running) stays resolved and is retried by the sweeper.
        """
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM durable_waits WHERE status IN "
                "('resolved','timeout')").fetchall()
        delivered = 0
        for row in rows:
            if self._deliver_wait(dict(row)):
                delivered += 1
        return delivered

    def _deliver_wait(self, wait: Dict[str, Any]) -> bool:
        try:
            from core.executor_registry import ExecutorRegistry
            executor = ExecutorRegistry.get_instance().get(wait["instance_id"])
        except Exception:
            executor = None
        if executor is None or not getattr(executor, "is_running", False):
            return False
        try:
            flowfile = self._restore_flowfile(wait["flowfile_json"])
            status = "timeout" if wait["status"] == "timeout" else "signaled"
            flowfile.set_attribute("durable.wait.status", status)
            flowfile.set_attribute("durable.wait.signal_id", wait["signal_id"])
            if wait.get("resolution_json"):
                resolution = json.loads(wait["resolution_json"])
                flowfile.set_attribute(
                    "durable.wait.value",
                    json.dumps(resolution.get("value"), ensure_ascii=False))
            if not executor.inject(flowfile, entry_task_id=wait["task_id"]):
                return False   # backpressure: retried by the sweeper
        except Exception:
            logger.exception("[durable-wait] delivery failed for %s",
                             wait["wait_id"])
            return False
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE durable_waits SET status='delivered' WHERE wait_id=?",
                (wait["wait_id"],))
        logger.info("[durable-wait] delivered %s (%s) to %s/%s",
                    wait["wait_id"], wait["status"],
                    wait["instance_id"][:12], wait["task_id"])
        return True

    # ── Sweeper ──────────────────────────────────────────────────────

    def ensure_sweeper(self) -> None:
        """Start the background sweeper once (lazy, daemon)."""
        with self._lock:
            if self._sweeper_started:
                return
            self._sweeper_started = True
        thread = threading.Thread(
            target=self._sweep_loop, daemon=True, name="confirmation-sweeper")
        thread.start()

    def _sweep_loop(self) -> None:
        while True:
            try:
                self.sweep_once()
            except Exception:
                logger.exception("confirmation sweeper failed")
            time.sleep(_SWEEP_SECONDS)

    def sweep_once(self) -> None:
        now = time.time()
        # Expire pending confirmations past their deadline.
        with self._lock, self._connect() as connection:
            expired = connection.execute(
                "SELECT request_id, conversation_id FROM confirmations WHERE "
                "status='pending' AND expires_at>0 AND expires_at<?",
                (now,)).fetchall()
            for row in expired:
                connection.execute(
                    "UPDATE confirmations SET status='expired' WHERE request_id=?",
                    (row["request_id"],))
            timed_out = connection.execute(
                "UPDATE durable_waits SET status='timeout' WHERE "
                "status='waiting' AND expires_at>0 AND expires_at<?",
                (now,)).rowcount
        for row in expired:
            record = self.get_confirmation(row["request_id"])
            if record:
                self._publish(row["conversation_id"],
                              "confirmation_answered", record)
            self.notify_signal(f"confirmation:{row['request_id']}",
                               {"status": "expired", "answer": None})
        if timed_out:
            logger.info("[durable-wait] %d wait(s) timed out", timed_out)
        self.deliver_resolved()

    # ── Plumbing ─────────────────────────────────────────────────────

    @staticmethod
    def _publish(conversation_id: str, event_type: str,
                 record: Dict[str, Any]) -> None:
        try:
            from core.conversation_event_bus import ConversationEventBus
            ConversationEventBus.instance().publish_event(
                conversation_id, event_type, dict(record))
        except Exception:
            logger.debug("confirmation publish failed", exc_info=True)


def find_own_flow_ids(task: Any) -> Optional[Dict[str, str]]:
    """Locate the running executor and task id that own ``task``.

    Tasks do not carry their instance/task ids; the registry does. Identity
    match, bounded by the number of running flows.
    """
    try:
        from core.executor_registry import ExecutorRegistry
        registry = ExecutorRegistry.get_instance()
    except Exception:
        return None
    for instance_id, executor in list(getattr(registry, "_executors", {}).items()):
        for task_id, candidate in getattr(executor, "_tasks", {}).items():
            if candidate is task:
                return {"instance_id": instance_id, "task_id": task_id}
    return None
