"""Durable typed user interactions and durable flow wait/notify signals.

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
import hashlib
import json
import logging
import math
import re
import sqlite3
import threading
import time
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import core.paths as _paths

logger = logging.getLogger(__name__)

_KINDS = frozenset({
    "confirm", "choice", "multi", "text", "multiline", "integer",
    "decimal", "date", "datetime", "file", "form",
})
_SCALAR_KINDS = _KINDS - {"form"}
_FILE_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,512}$")
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


def parse_utc_deadline(value: Any) -> float:
    """Parse an absolute timezone-aware ISO-8601 timestamp as UTC epoch seconds."""
    text = str(value or "").strip()
    if not text:
        raise ValueError("until is required")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("until must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("until must include a timezone")
    return parsed.astimezone(timezone.utc).timestamp()


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


def normalize_response_schema(kind: str, schema: Any = None) -> Dict[str, Any]:
    """Return a bounded, JSON-safe response schema for one interaction kind."""
    if schema is None:
        schema = {}
    if not isinstance(schema, dict):
        raise ValueError("response_schema must be an object")
    try:
        encoded = json.dumps(schema, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("response_schema must be JSON-serializable") from exc
    if len(encoded.encode("utf-8")) > 64 * 1024:
        raise ValueError("response_schema exceeds 64 KiB")
    result = json.loads(encoded)
    if kind == "form":
        fields = result.get("fields")
        if not isinstance(fields, list) or not fields:
            raise ValueError("form response_schema.fields must be a non-empty list")
        names = set()
        for field in fields:
            if not isinstance(field, dict):
                raise ValueError("form fields must be objects")
            name = str(field.get("name") or "").strip()
            field_type = str(field.get("type") or "text").strip().lower()
            if not name or name in names:
                raise ValueError("form field names must be non-empty and unique")
            if field_type not in _SCALAR_KINDS:
                raise ValueError(f"unsupported form field type '{field_type}'")
            field["name"] = name
            field["type"] = field_type
            names.add(name)
    return result


def _bounded_number(value: float, schema: Dict[str, Any], label: str) -> None:
    if "minimum" in schema and value < float(schema["minimum"]):
        raise ValueError(f"{label} is below minimum {schema['minimum']}")
    if "maximum" in schema and value > float(schema["maximum"]):
        raise ValueError(f"{label} is above maximum {schema['maximum']}")


def validate_interaction_answer(
    kind: str, answer: Any, schema: Dict[str, Any],
    options: Any = None, *, label: str = "answer",
) -> Any:
    """Validate and normalize an answer before resolving its continuation."""
    opts = normalize_options(options)
    valid = {option["value"] for option in opts}
    if kind == "confirm":
        valid = valid or {"yes", "no"}
        value = str(answer[0] if isinstance(answer, list) and answer else answer)
        if value not in valid:
            raise ValueError(f"Invalid {label} value: {value!r}")
        return value
    if kind == "choice":
        value = str(answer[0] if isinstance(answer, list) and answer else answer)
        if value not in valid:
            raise ValueError(f"Invalid {label} value: {value!r}")
        return value
    if kind == "multi":
        values = [str(value) for value in (
            answer if isinstance(answer, list) else [answer])]
        bad = [value for value in values if value not in valid]
        if not values or bad:
            raise ValueError(f"Invalid {label} values: {bad or 'empty'}")
        return values
    if kind in {"text", "multiline"}:
        if not isinstance(answer, str):
            raise ValueError(f"{label} must be text")
        if kind == "text" and ("\n" in answer or "\r" in answer):
            raise ValueError(f"{label} must be single-line text")
        length = len(answer)
        minimum = int(schema.get("min_length", 0))
        maximum = int(schema.get("max_length", 100000))
        if length < minimum:
            raise ValueError(f"{label} is shorter than min_length {minimum}")
        if length > maximum:
            raise ValueError(f"{label} exceeds max_length {maximum}")
        return answer
    if kind == "integer":
        if isinstance(answer, bool):
            raise ValueError(f"{label} must be an integer")
        try:
            value = int(answer)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} must be an integer") from exc
        if isinstance(answer, float) and not answer.is_integer():
            raise ValueError(f"{label} must be an integer")
        if isinstance(answer, str) and str(value) != answer.strip():
            raise ValueError(f"{label} must be an integer")
        _bounded_number(float(value), schema, label)
        return value
    if kind == "decimal":
        if isinstance(answer, bool):
            raise ValueError(f"{label} must be a decimal number")
        try:
            value = float(answer)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} must be a decimal number") from exc
        if not math.isfinite(value):
            raise ValueError(f"{label} must be a finite decimal number")
        _bounded_number(value, schema, label)
        return value
    if kind == "date":
        try:
            return date.fromisoformat(str(answer)).isoformat()
        except ValueError as exc:
            raise ValueError(f"{label} must be an ISO-8601 date") from exc
    if kind == "datetime":
        text = str(answer)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            value = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"{label} must be an ISO-8601 datetime") from exc
        if value.tzinfo is None:
            raise ValueError(f"{label} datetime must include a timezone")
        return value.isoformat()
    if kind == "file":
        if schema.get("multiple"):
            if not isinstance(answer, list) or not answer:
                raise ValueError(f"{label} must be a non-empty list of file references")
            item_schema = {**schema, "multiple": False}
            return [
                validate_interaction_answer(
                    "file", item, item_schema, label=f"{label}[{index}]")
                for index, item in enumerate(answer)
            ]
        if isinstance(answer, str):
            file_id = answer.strip()
            result = {"file_id": file_id}
        elif isinstance(answer, dict):
            allowed = {"file_id", "name", "mime_type", "size"}
            unknown = set(answer) - allowed
            if unknown:
                raise ValueError(f"{label} contains unsupported file fields")
            result = dict(answer)
            file_id = str(result.get("file_id") or "").strip()
            result["file_id"] = file_id
        else:
            raise ValueError(f"{label} must be a file reference")
        if not _FILE_ID_RE.fullmatch(file_id):
            raise ValueError(f"{label} has an invalid file_id")
        return result
    if kind == "form":
        if not isinstance(answer, dict):
            raise ValueError(f"{label} must be an object")
        fields = {field["name"]: field for field in schema.get("fields", [])}
        unknown = set(answer) - set(fields)
        if unknown:
            raise ValueError(f"{label} contains unknown fields: {sorted(unknown)}")
        normalized = {}
        for name, field in fields.items():
            if name not in answer or answer[name] is None:
                if field.get("required"):
                    raise ValueError(f"{label}.{name} is required")
                continue
            normalized[name] = validate_interaction_answer(
                field["type"], answer[name], field,
                field.get("options"), label=f"{label}.{name}")
        return normalized
    raise ValueError(f"unsupported interaction kind '{kind}'")


class UserInteractionStore:
    """Thread-safe SQLite state for typed interactions and durable waits."""

    _instance: Optional["UserInteractionStore"] = None
    _instance_lock = threading.Lock()

    @classmethod
    def instance(cls) -> "UserInteractionStore":
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
                    resolution_json TEXT NOT NULL DEFAULT '',
                    kind TEXT NOT NULL DEFAULT 'signal',
                    import_metadata_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_wait_signal
                    ON durable_waits(signal_id, status);
                CREATE INDEX IF NOT EXISTS idx_wait_status
                    ON durable_waits(status, expires_at);
                CREATE INDEX IF NOT EXISTS idx_wait_instance
                    ON durable_waits(instance_id, status, created_at);

                CREATE TABLE IF NOT EXISTS durable_signal_values (
                    signal_id TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL DEFAULT '',
                    fired_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS confirmation_store_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            row_count_before = connection.execute(
                "SELECT COUNT(*) FROM confirmations").fetchone()[0]
            wait_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(durable_waits)")
            }
            if "kind" not in wait_columns:
                connection.execute(
                    "ALTER TABLE durable_waits ADD COLUMN "
                    "kind TEXT NOT NULL DEFAULT 'signal'")
            if "import_metadata_json" not in wait_columns:
                connection.execute(
                    "ALTER TABLE durable_waits ADD COLUMN import_metadata_json TEXT")
            interaction_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(confirmations)")
            }
            migrations = {
                "contract_version": "INTEGER NOT NULL DEFAULT 1",
                "kind": "TEXT NOT NULL DEFAULT ''",
                "response_schema_json": "TEXT NOT NULL DEFAULT '{}'",
                "signal_id": "TEXT NOT NULL DEFAULT ''",
                "continuation_json": "TEXT NOT NULL DEFAULT '{}'",
            }
            for column, definition in migrations.items():
                if column not in interaction_columns:
                    connection.execute(
                        f"ALTER TABLE confirmations ADD COLUMN {column} {definition}")
            connection.execute(
                "UPDATE confirmations SET kind=mode WHERE kind='' OR kind IS NULL")
            connection.execute(
                "UPDATE confirmations SET signal_id='confirmation:' || request_id "
                "WHERE signal_id='' OR signal_id IS NULL")
            row_count_after = connection.execute(
                "SELECT COUNT(*) FROM confirmations").fetchone()[0]
            if row_count_after != row_count_before:
                raise RuntimeError("user interaction migration changed row count")
            if connection.execute("PRAGMA foreign_key_check").fetchone():
                raise RuntimeError("user interaction migration failed foreign-key check")
            invalid_pending = connection.execute(
                "SELECT COUNT(*) FROM confirmations WHERE status='pending' "
                "AND (signal_id='' OR kind='')").fetchone()[0]
            if invalid_pending:
                raise RuntimeError(
                    "user interaction migration left invalid pending continuations")
            connection.execute(
                "INSERT INTO confirmation_store_metadata(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ("user_interaction_schema", "1"))

    # ── Confirmations ────────────────────────────────────────────────

    @staticmethod
    def _confirmation_row(row: sqlite3.Row) -> Dict[str, Any]:
        data = dict(row)
        data["options"] = json.loads(data.pop("options_json") or "[]")
        data["response_schema"] = json.loads(
            data.pop("response_schema_json", "{}") or "{}")
        data["continuation"] = json.loads(
            data.pop("continuation_json", "{}") or "{}")
        raw_answer = data.pop("answer_json") or ""
        data["answer"] = json.loads(raw_answer) if raw_answer else None
        data["kind"] = data.get("kind") or data.get("mode") or "confirm"
        data["mode"] = data["kind"]
        data["contract_version"] = int(data.get("contract_version") or 1)
        data["signal_id"] = data.get("signal_id") or (
            f"confirmation:{data['request_id']}")
        return data

    def create_interaction(
        self, *, conversation_id: str, user_id: str, requester_kind: str,
        requester: str, message: str, title: str = "", kind: str = "text",
        options: Any = None, response_schema: Any = None,
        expires_in_seconds: float = 0, continuation: Any = None,
        signal_prefix: str = "interaction", idempotency_key: str = "",
    ) -> Dict[str, Any]:
        if not conversation_id or not user_id:
            raise ValueError("conversation_id and user_id are required")
        if not (message or "").strip():
            raise ValueError("message is required")
        kind = str(kind or "").strip().lower()
        if kind not in _KINDS:
            raise ValueError("unsupported interaction kind")
        opts = normalize_options(options)
        if kind == "confirm" and not opts:
            opts = [{"value": "yes", "label": "Yes"},
                    {"value": "no", "label": "No"}]
        if kind in ("choice", "multi") and len(opts) < 2:
            raise ValueError(f"kind '{kind}' requires at least 2 options")
        schema = normalize_response_schema(kind, response_schema)
        continuation = continuation or {}
        if not isinstance(continuation, dict):
            raise ValueError("continuation must be an object")
        now = time.time()
        idempotency_key = str(idempotency_key or "").strip()
        if idempotency_key:
            identity = "\x00".join((
                conversation_id, user_id, requester_kind, idempotency_key,
            )).encode("utf-8")
            request_id = "req_" + hashlib.sha256(identity).hexdigest()[:16]
        else:
            request_id = "req_" + uuid.uuid4().hex[:16]
        signal_id = f"{signal_prefix}:{request_id}"
        record = {
            "request_id": request_id, "conversation_id": conversation_id,
            "user_id": user_id, "requester_kind": requester_kind,
            "requester": requester or "", "title": title or "",
            "message": message, "mode": kind, "kind": kind, "options": opts,
            "response_schema": schema, "contract_version": 1,
            "signal_id": signal_id, "continuation": continuation,
            "created_at": now,
            "expires_at": (now + expires_in_seconds) if expires_in_seconds else 0,
            "status": "pending", "answer": None,
            "answered_by": "", "answered_at": 0,
        }
        with self._lock, self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM confirmations WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if existing is not None:
                current = self._confirmation_row(existing)
                comparable = {
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                    "requester_kind": requester_kind,
                    "requester": requester or "",
                    "title": title or "",
                    "message": message,
                    "kind": kind,
                    "options": opts,
                    "response_schema": schema,
                    "signal_id": signal_id,
                    "continuation": continuation,
                }
                if any(current.get(key) != value
                       for key, value in comparable.items()):
                    raise ValueError(
                        "idempotency key already identifies a different interaction")
                return current
            connection.execute(
                "INSERT INTO confirmations "
                "(request_id, conversation_id, user_id, requester_kind, requester, "
                "title, message, mode, options_json, created_at, expires_at, status, "
                "answer_json, answered_by, answered_at, contract_version, kind, "
                "response_schema_json, signal_id, continuation_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (request_id, conversation_id, user_id, requester_kind,
                 requester or "", title or "", message, kind,
                 json.dumps(opts, ensure_ascii=False), now,
                 record["expires_at"], "pending", "", "", 0, 1, kind,
                 json.dumps(schema, ensure_ascii=False), signal_id,
                 json.dumps(continuation, ensure_ascii=False)))
        self.ensure_sweeper()
        self._publish(conversation_id, "interaction_request", record)
        if signal_prefix == "confirmation":
            self._publish(conversation_id, "confirmation_request", record)
        return record

    def create_confirmation(
        self, *, conversation_id: str, user_id: str, requester_kind: str,
        requester: str, message: str, title: str = "",
        mode: str = "confirm", options: Any = None,
        expires_in_seconds: float = 0, idempotency_key: str = "",
    ) -> Dict[str, Any]:
        mode = str(mode or "confirm").strip().lower()
        if mode not in {"confirm", "choice", "multi"}:
            raise ValueError("mode must be confirm, choice, or multi")
        return self.create_interaction(
            conversation_id=conversation_id, user_id=user_id,
            requester_kind=requester_kind, requester=requester, message=message,
            title=title, kind=mode, options=options,
            expires_in_seconds=expires_in_seconds,
            signal_prefix="confirmation",
            idempotency_key=idempotency_key,
        )

    def get_confirmation(self, request_id: str) -> Optional[Dict[str, Any]]:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM confirmations WHERE request_id=?",
                (request_id,)).fetchone()
        return self._confirmation_row(row) if row else None

    get_interaction = get_confirmation

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

    list_interactions = list_confirmations

    def respond(self, request_id: str, answer: Any,
                answered_by: str = "") -> Dict[str, Any]:
        """Answer a pending confirmation and resume its requester.

        ``answer``: for mode confirm/choice a single option value (string);
        for multi a list of option values. Validated against the options.
        """
        record = self.get_confirmation(request_id)
        if not record:
            raise KeyError("Unknown user interaction request")
        if record["status"] != "pending":
            raise ValueError(f"Interaction is already {record['status']}")
        if record["expires_at"] and time.time() > record["expires_at"]:
            self._set_status(request_id, "expired")
            raise ValueError("Interaction has expired")
        normalized = validate_interaction_answer(
            record["kind"], answer, record["response_schema"], record["options"])
        now = time.time()
        with self._lock, self._connect() as connection:
            updated = connection.execute(
                "UPDATE confirmations SET status='answered', answer_json=?, "
                "answered_by=?, answered_at=? WHERE request_id=? AND status='pending'",
                (json.dumps(normalized, ensure_ascii=False), answered_by or "",
                 now, request_id)).rowcount
        if not updated:
            raise ValueError("Interaction was answered concurrently")
        record.update(status="answered", answer=normalized,
                      answered_by=answered_by or "", answered_at=now)
        self._publish(record["conversation_id"], "interaction_answered", record)
        if record["signal_id"].startswith("confirmation:"):
            self._publish(record["conversation_id"], "confirmation_answered", record)
        self._resume_requester(record)
        return record

    respond_interaction = respond

    def cancel(self, request_id: str, cancelled_by: str = "") -> bool:
        record = self.get_confirmation(request_id)
        if not record or record["status"] != "pending":
            return False
        self._set_status(request_id, "cancelled")
        record["status"] = "cancelled"
        self._publish(record["conversation_id"], "interaction_answered", record)
        if record["signal_id"].startswith("confirmation:"):
            self._publish(record["conversation_id"], "confirmation_answered", record)
        # A cancelled request still resolves its signal so a durably waiting
        # flow branch is released (with status carried in the value).
        self.notify_signal(record["signal_id"],
                           {"status": "cancelled", "answer": None})
        return True

    cancel_interaction = cancel

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
            record["signal_id"],
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
                    + f"[interaction:{record['request_id']}] The user answered "
                    f"your interaction request \"{record['message'][:120]}\": "
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
            "process_id": str(flowfile.process_id),
            "created_at": flowfile.created_at.isoformat(),
        })

    @staticmethod
    def _restore_flowfile(payload: str) -> Any:
        from core import FlowFile
        data = json.loads(payload)
        created_at = data.get("created_at")
        flowfile = FlowFile(
            content=base64.b64decode(data.get("content_b64", "") or ""),
            process_id=str(data.get("process_id") or "") or None,
            created_at=datetime.fromisoformat(created_at) if created_at else None,
        )
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
            self._stamp_interaction_resolution(flowfile, signal_id, existing)
            return None
        now = time.time()
        process_id = str(getattr(flowfile, "process_id", "") or "")
        if not process_id:
            raise ValueError("flowfile process_id is required")
        identity = "\x00".join((
            signal_id, instance_id, task_id, process_id,
        )).encode("utf-8")
        wait_id = "wait_" + hashlib.sha256(identity).hexdigest()[:16]
        with self._lock, self._connect() as connection:
            existing_wait = connection.execute(
                "SELECT wait_id FROM durable_waits WHERE wait_id=?",
                (wait_id,),
            ).fetchone()
            if existing_wait is not None:
                return wait_id
            connection.execute(
                "INSERT INTO durable_waits "
                "(wait_id, signal_id, instance_id, task_id, flowfile_json, "
                "created_at, expires_at, status, resolution_json, kind) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (wait_id, signal_id, instance_id, task_id,
                 self._serialize_flowfile(flowfile), now,
                 (now + timeout_seconds) if timeout_seconds else 0,
                 "waiting", "", "signal"))
        self.ensure_sweeper()
        logger.info("[durable-wait] parked %s on signal %r (flow %s/%s, "
                    "timeout %s)", wait_id, signal_id, instance_id[:12],
                    task_id, timeout_seconds or "none")
        return wait_id

    def park_timer(self, *, instance_id: str, task_id: str, flowfile: Any,
                   deadline_at: float) -> Optional[str]:
        """Park a FlowFile until an absolute UTC epoch deadline."""
        if not instance_id or not task_id:
            raise ValueError("instance_id and task_id are required")
        deadline_at = float(deadline_at)
        now = time.time()
        if deadline_at <= now:
            flowfile.set_attribute("durable.timer.status", "elapsed")
            flowfile.set_attribute("durable.timer.elapsed_at", datetime.now(
                timezone.utc).isoformat())
            flowfile.set_attribute("route.relationship", "elapsed")
            return None
        wait_id = "timer_" + uuid.uuid4().hex[:16]
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO durable_waits "
                "(wait_id, signal_id, instance_id, task_id, flowfile_json, "
                "created_at, expires_at, status, resolution_json, kind) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (wait_id, wait_id, instance_id, task_id,
                 self._serialize_flowfile(flowfile), now, deadline_at,
                 "waiting", "", "timer"))
        self.ensure_sweeper()
        logger.info("[durable-timer] parked %s until %.3f (flow %s/%s)",
                    wait_id, deadline_at, instance_id[:12], task_id)
        return wait_id

    def import_timer(
        self, *, wait_id: str, instance_id: str, task_id: str, flowfile: Any,
        deadline_at: float, created_at: float, import_metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Import one deterministic timer continuation without delivering it."""

        wait_id = str(wait_id or "").strip()
        instance_id = str(instance_id or "").strip()
        task_id = str(task_id or "").strip()
        if not wait_id.startswith("timer_legacy_") or not instance_id or not task_id:
            raise ValueError(
                "wait_id, instance_id and task_id are required for imported timer")
        if not isinstance(import_metadata, dict) or not import_metadata:
            raise ValueError("import_metadata must be a non-empty object")
        if isinstance(created_at, bool) or not isinstance(created_at, (int, float)):
            raise ValueError("created_at must be numeric")
        if isinstance(deadline_at, bool) or not isinstance(deadline_at, (int, float)):
            raise ValueError("deadline_at must be numeric")
        deadline_at = float(deadline_at)
        created_at = float(created_at)
        if deadline_at < created_at:
            raise ValueError("deadline_at must not precede created_at")
        payload = self._serialize_flowfile(flowfile)
        metadata = json.dumps(
            import_metadata, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"))
        expected = {
            "wait_id": wait_id,
            "signal_id": wait_id,
            "instance_id": instance_id,
            "task_id": task_id,
            "flowfile_json": payload,
            "created_at": created_at,
            "expires_at": deadline_at,
            "status": "waiting",
            "resolution_json": "",
            "kind": "timer",
            "import_metadata_json": metadata,
        }
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM durable_waits WHERE wait_id=?", (wait_id,),
            ).fetchone()
            if row is not None:
                existing = dict(row)
                if all(existing.get(key) == value for key, value in expected.items()):
                    result = dict(expected)
                    result["import_metadata"] = import_metadata
                    result.pop("flowfile_json")
                    result.pop("import_metadata_json")
                    return result
                raise ValueError("different imported timer already exists")
            connection.execute(
                "INSERT INTO durable_waits "
                "(wait_id, signal_id, instance_id, task_id, flowfile_json, "
                "created_at, expires_at, status, resolution_json, kind, "
                "import_metadata_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                tuple(expected.values()),
            )
        self.ensure_sweeper()
        result = dict(expected)
        result["import_metadata"] = import_metadata
        result.pop("flowfile_json")
        result.pop("import_metadata_json")
        return result

    def delete_imported_wait(
        self, wait_id: str, *, import_metadata: Dict[str, Any],
    ) -> bool:
        """Delete only a still-waiting continuation with exact provenance."""

        metadata = json.dumps(
            import_metadata, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"))
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT status, import_metadata_json FROM durable_waits "
                "WHERE wait_id=?", (wait_id,),
            ).fetchone()
            if row is None:
                return False
            if row["import_metadata_json"] != metadata:
                raise ValueError("imported wait provenance does not match")
            if row["status"] != "waiting":
                raise ValueError("imported wait is no longer compensatable")
            connection.execute(
                "DELETE FROM durable_waits WHERE wait_id=?", (wait_id,))
        return True

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
                "expires_at, status, kind FROM durable_waits"
        params: List[Any] = []
        if status and status != "all":
            query += " WHERE status=?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, min(int(limit or 200), 1000)))
        with self._lock, self._connect() as connection:
            return [dict(r) for r in connection.execute(query, params).fetchall()]

    def list_waits_for_instances(
            self, instance_ids: Any, status: str = "waiting"
    ) -> list[dict[str, Any]]:
        """Return every wait for exact runtime instances without a global cap."""

        values = tuple(dict.fromkeys(
            str(value or "").strip() for value in (instance_ids or ())
            if str(value or "").strip()
        ))
        if not values:
            return []
        marks = ",".join("?" for _ in values)
        query = (
            "SELECT wait_id, signal_id, instance_id, task_id, created_at, "
            "expires_at, status, kind FROM durable_waits "
            f"WHERE instance_id IN ({marks})"  # nosec B608 - placeholders only
        )
        params: list[Any] = list(values)
        if status and status != "all":
            query += " AND status=?"
            params.append(str(status))
        query += " ORDER BY created_at DESC"
        with self._lock, self._connect() as connection:
            return [dict(row) for row in connection.execute(query, params).fetchall()]

    def has_pending_waits(self, instance_id: str) -> bool:
        """Return whether an instance owns an undelivered continuation."""
        if not instance_id:
            return False
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM durable_waits WHERE instance_id=? AND "
                "status!='delivered' LIMIT 1", (instance_id,)).fetchone()
        return row is not None

    def cancel_wait(self, wait_id: str) -> bool:
        """Cancel one pending continuation and deliver its cancelled relation."""
        with self._lock, self._connect() as connection:
            updated = connection.execute(
                "UPDATE durable_waits SET status='cancelled' WHERE wait_id=? "
                "AND status='waiting'", (wait_id,)).rowcount
        if updated:
            self.deliver_resolved()
        return bool(updated)

    def deliver_resolved(self) -> int:
        """Re-inject resolved/timed-out waits whose flow instance is running.

        Idempotent and restart-safe: an undeliverable wait (instance not
        running) stays resolved and is retried by the sweeper.
        """
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM durable_waits WHERE status IN "
                "('resolved','timeout','elapsed','cancelled')").fetchall()
        delivered = 0
        for row in rows:
            if self._deliver_wait(dict(row)):
                delivered += 1
        return delivered

    def _deliver_wait(self, wait: Dict[str, Any]) -> bool:
        instance_id = str(wait.get("instance_id") or "")
        workflow_run_id = (
            instance_id.split(":", 1)[1]
            if instance_id.startswith("workflow:") else "")
        executor = None
        if not workflow_run_id:
            try:
                from core.executor_registry import ExecutorRegistry
                executor = ExecutorRegistry.get_instance().get(instance_id)
            except Exception:
                executor = None
            if executor is None or not getattr(executor, "is_running", False):
                return False
        try:
            flowfile = self._restore_flowfile(wait["flowfile_json"])
            if wait.get("kind") == "timer":
                status = wait["status"]
                flowfile.set_attribute("durable.timer.status", status)
                flowfile.set_attribute(
                    "durable.timer.scheduled_at",
                    datetime.fromtimestamp(
                        wait["expires_at"], timezone.utc).isoformat())
                flowfile.set_attribute(
                    "durable.timer.elapsed_at", datetime.now(timezone.utc).isoformat())
                flowfile.set_attribute("route.relationship", status)
            else:
                status = "timeout" if wait["status"] == "timeout" else "signaled"
                if wait["status"] == "cancelled":
                    status = "cancelled"
                flowfile.set_attribute("durable.wait.status", status)
                flowfile.set_attribute("durable.wait.signal_id", wait["signal_id"])
                if wait.get("resolution_json"):
                    resolution = json.loads(wait["resolution_json"])
                    value = resolution.get("value")
                    flowfile.set_attribute(
                        "durable.wait.value",
                        json.dumps(value, ensure_ascii=False))
                    self._stamp_interaction_resolution(
                        flowfile, wait["signal_id"], value)
                elif wait["signal_id"].startswith(("interaction:", "confirmation:")):
                    flowfile.set_attribute("interaction.status", status)
            if workflow_run_id:
                from core.workflow_agent_runtime import WorkflowAgentRuntime
                accepted = WorkflowAgentRuntime.instance().resume_wait(
                    workflow_run_id, flowfile, str(wait["task_id"]))
            else:
                accepted = executor.inject(
                    flowfile, entry_task_id=wait["task_id"])
            if not accepted:
                return False   # CAS/backpressure: retried by the sweeper
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

    @staticmethod
    def _stamp_interaction_resolution(
        flowfile: Any, signal_id: str, value: Any,
    ) -> None:
        if not signal_id.startswith(("interaction:", "confirmation:")):
            return
        if isinstance(value, dict):
            status = str(value.get("status") or "signaled")
            answer = value.get("answer")
        else:
            status = "signaled"
            answer = value
        flowfile.set_attribute("interaction.status", status)
        if answer is not None:
            if isinstance(answer, str):
                rendered = answer
            else:
                rendered = json.dumps(answer, ensure_ascii=False)
            flowfile.set_attribute("interaction.answer", rendered)

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
                "kind='signal' AND status='waiting' AND expires_at>0 AND expires_at<?",
                (now,)).rowcount
            elapsed = connection.execute(
                "UPDATE durable_waits SET status='elapsed' WHERE "
                "kind='timer' AND status='waiting' AND expires_at<=?",
                (now,)).rowcount
        for row in expired:
            record = self.get_confirmation(row["request_id"])
            if record:
                self._publish(row["conversation_id"],
                              "interaction_answered", record)
                if record["signal_id"].startswith("confirmation:"):
                    self._publish(row["conversation_id"],
                                  "confirmation_answered", record)
            if record:
                self.notify_signal(record["signal_id"],
                                   {"status": "expired", "answer": None})
        if timed_out:
            logger.info("[durable-wait] %d wait(s) timed out", timed_out)
        if elapsed:
            logger.info("[durable-timer] %d timer(s) elapsed", elapsed)
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


# One writable store, two API names during the one-shot migration. Existing
# imports keep pointing at the canonical class and therefore share its singleton.
ConfirmationStore = UserInteractionStore
