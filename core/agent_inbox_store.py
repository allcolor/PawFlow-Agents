"""Durable unread inbox, workflow leases and ingress receipt ledger."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import core.paths as _paths
from core.sqlite_store_guard import SqliteStoreGuard, SqliteStoreUnavailableError
from core.workflow_agent_contracts import AgentInboxClaim, AgentInboxItem


class _InboxDeliveryItem(AgentInboxItem):
    """Inbox delivery audit, extending the unchanged phase-1 workflow schema."""

    state: Literal["pending", "notified", "read", "claimed", "acknowledged", "discarded"]
    notified_at: str | None = None
    read_at: str | None = None
    read_call_id: str = ""


_ITEMS_SCHEMA = """
    CREATE TABLE IF NOT EXISTS agent_inbox_items (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id TEXT NOT NULL,
        agent_key TEXT NOT NULL,
        msg_id TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        source TEXT NOT NULL,
        state TEXT NOT NULL CHECK (
            state IN ('pending','notified','read','claimed','acknowledged','discarded')
        ),
        owner_run_id TEXT,
        owner_task_id TEXT,
        lease_expires_at REAL,
        created_at REAL NOT NULL,
        enqueued_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        notified_at REAL,
        read_at REAL,
        read_call_id TEXT NOT NULL DEFAULT '',
        UNIQUE(conversation_id, agent_key, msg_id)
    );
"""


def _utc(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def _required(value: Any, name: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{name} is required")
    return result


def _agent(value: Any) -> str:
    return _required(value, "agent_key").lower()


def _created_at(payload: dict[str, Any]) -> float:
    value = payload.get("ts")
    if value is None:
        value = payload.get("timestamp")
    try:
        if value is None or isinstance(value, bool):
            raise ValueError
        try:
            result = float(value)
        except ValueError:
            stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                raise ValueError
            result = stamp.timestamp()
        if not math.isfinite(result):
            raise ValueError
        return result
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("inbox payload requires a valid creation timestamp (ts)") from exc


class AgentInboxStore:
    """Thread-safe work ledger with non-destructive leased delivery."""

    _instance: AgentInboxStore | None = None
    _instance_lock = threading.Lock()

    @classmethod
    def instance(cls) -> AgentInboxStore:
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
        self._guard = SqliteStoreGuard("Agent inbox")
        try:
            self._guard.initialize(self.database_path, self._initialize)
        except SqliteStoreUnavailableError:
            # The guard preserved and described the database. Keep the
            # singleton alive so every later call fails fast instead of
            # reopening the damaged file with a traceback per request.
            pass

    @property
    def available(self) -> bool:
        """Return whether the inbox is safe to read or write."""
        return self._guard.available

    @property
    def database_path(self) -> Path:
        return self._database_path_override or (
            _paths.RUNTIME_DIR / "agent_inbox.sqlite3")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self._guard.require_available()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=10)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 10000")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript(
                _ITEMS_SCHEMA +
                """
                CREATE INDEX IF NOT EXISTS idx_agent_inbox_ready
                    ON agent_inbox_items(
                        conversation_id, agent_key, state, sequence
                    );
                CREATE INDEX IF NOT EXISTS idx_agent_inbox_lease
                    ON agent_inbox_items(state, lease_expires_at);
                CREATE TABLE IF NOT EXISTS agent_inbox_claims (
                    claim_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    agent_key TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    item_ids_json TEXT NOT NULL,
                    lease_expires_at REAL NOT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE(conversation_id, agent_key, run_id, task_id)
                );
                CREATE TABLE IF NOT EXISTS agent_ingress_receipts (
                    conversation_id TEXT NOT NULL,
                    agent_key TEXT NOT NULL,
                    msg_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    source TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (
                        state IN ('prepared','transcript_persisted','queued')
                    ),
                    prepared_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(conversation_id, agent_key, msg_id)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_receipt_state
                    ON agent_ingress_receipts(state, updated_at);
                CREATE TABLE IF NOT EXISTS agent_inbox_migrations (
                    conversation_id TEXT NOT NULL,
                    agent_key TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL,
                    item_count INTEGER NOT NULL,
                    completed_at REAL NOT NULL,
                    PRIMARY KEY(conversation_id, agent_key)
                );
                CREATE TABLE IF NOT EXISTS agent_inbox_notices (
                    conversation_id TEXT NOT NULL,
                    agent_key TEXT NOT NULL,
                    notified_sequence INTEGER NOT NULL DEFAULT 0,
                    unread_turns INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(conversation_id, agent_key)
                );
                CREATE TABLE IF NOT EXISTS agent_inbox_read_offsets (
                    conversation_id TEXT NOT NULL,
                    agent_key TEXT NOT NULL,
                    msg_id TEXT NOT NULL,
                    read_offset INTEGER NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(conversation_id, agent_key, msg_id)
                );
                CREATE TABLE IF NOT EXISTS agent_inbox_fences (
                    conversation_id TEXT NOT NULL,
                    agent_key TEXT NOT NULL,
                    cutoff REAL NOT NULL,
                    PRIMARY KEY(conversation_id, agent_key)
                );
                """
            )
            self._upgrade_items(connection)
            connection.execute("""CREATE TABLE IF NOT EXISTS agent_inbox_context_receipts (
                conversation_id TEXT NOT NULL,
                agent_key TEXT NOT NULL,
                msg_id TEXT NOT NULL,
                PRIMARY KEY(conversation_id, agent_key, msg_id)
            )""")

    @staticmethod
    def _upgrade_items(connection: sqlite3.Connection) -> None:
        """Rebuild the old CHECK constraint once, in one rollback-safe transaction."""
        connection.execute("BEGIN IMMEDIATE")
        columns = {row["name"] for row in connection.execute(
            "PRAGMA table_info(agent_inbox_items)")}
        schema = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='agent_inbox_items'").fetchone()[0]
        if ({"created_at", "notified_at", "read_at", "read_call_id"} <= columns
                and "'notified'" in schema and "'read'" in schema):
            return
        high_water = connection.execute(
            "SELECT seq FROM sqlite_sequence WHERE name='agent_inbox_items'").fetchone()
        connection.execute(_ITEMS_SCHEMA.replace(
            "agent_inbox_items", "agent_inbox_items_upgrade"))
        for row in connection.execute("SELECT * FROM agent_inbox_items ORDER BY sequence"):
            values = dict(row)
            if "created_at" not in values:
                payload = json.loads(row["payload_json"])
                # Phase-1 workflow payloads predate message creation stamps;
                # their work item was created at enqueue, as in the old ledger.
                values["created_at"] = (
                    row["enqueued_at"] if not row["source"].startswith("inbox:")
                    and payload.get("ts") is None and payload.get("timestamp") is None
                    else _created_at(payload))
            values.setdefault("notified_at", None)
            values.setdefault("read_at", None)
            values.setdefault("read_call_id", "")
            if row["source"].startswith("inbox:"):
                if row["state"] == "acknowledged":
                    values.update(state="read", read_at=row["updated_at"])
                elif row["state"] == "claimed":
                    values.update(state="pending", owner_run_id=None,
                                  owner_task_id=None, lease_expires_at=None)
                if values["state"] == "pending":
                    notice = connection.execute(
                        """SELECT updated_at FROM agent_inbox_notices
                           WHERE conversation_id=? AND agent_key=? AND notified_sequence >= ?""",
                        (row["conversation_id"], row["agent_key"], row["sequence"])).fetchone()
                    if notice is not None:
                        values.update(state="notified", notified_at=notice[0],
                                      updated_at=max(values["updated_at"], notice[0]))
            connection.execute(
                """INSERT INTO agent_inbox_items_upgrade(
                       sequence, conversation_id, agent_key, msg_id, payload_json,
                       source, state, owner_run_id, owner_task_id, lease_expires_at,
                       created_at, enqueued_at, updated_at, notified_at, read_at, read_call_id
                   ) VALUES (:sequence, :conversation_id, :agent_key, :msg_id, :payload_json,
                             :source, :state, :owner_run_id, :owner_task_id, :lease_expires_at,
                             :created_at, :enqueued_at, :updated_at, :notified_at, :read_at, :read_call_id)""",
                values)
        connection.execute("DROP TABLE agent_inbox_items")
        connection.execute("ALTER TABLE agent_inbox_items_upgrade RENAME TO agent_inbox_items")
        if high_water is not None:
            current = connection.execute(
                "SELECT seq FROM sqlite_sequence WHERE name='agent_inbox_items'").fetchone()
            connection.execute(
                "DELETE FROM sqlite_sequence WHERE name='agent_inbox_items'")
            connection.execute(
                "INSERT INTO sqlite_sequence(name, seq) VALUES ('agent_inbox_items', ?)",
                (max(high_water[0], current[0] if current is not None else 0),))
        connection.execute(
            """CREATE INDEX idx_agent_inbox_ready ON agent_inbox_items(
                   conversation_id, agent_key, state, sequence)""")
        connection.execute(
            "CREATE INDEX idx_agent_inbox_lease ON agent_inbox_items(state, lease_expires_at)")

    @staticmethod
    def _payload_json(payload: dict[str, Any]) -> str:
        if not isinstance(payload, dict):
            raise TypeError("inbox payload must be an object")
        return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"))

    @staticmethod
    def _meaningful(payload_json: str) -> dict[str, Any]:
        """Payload without the ``_pending_*`` queue bookkeeping fields."""
        return {key: value for key, value in json.loads(payload_json).items()
                if not str(key).startswith("_pending_")}

    @staticmethod
    def _item(row: sqlite3.Row) -> AgentInboxItem:
        fields = {
            "conversation_id": row["conversation_id"],
            "agent_key": row["agent_key"],
            "msg_id": row["msg_id"],
            "sequence": row["sequence"],
            "payload": json.loads(row["payload_json"]),
            "source": row["source"],
            "state": row["state"],
            "owner_run_id": row["owner_run_id"],
            "lease_expires_at": (
                _utc(row["lease_expires_at"])
                if row["lease_expires_at"] is not None else None),
            "enqueued_at": _utc(row["enqueued_at"]),
            "updated_at": _utc(row["updated_at"]),
        }
        if row["source"].startswith("inbox:"):
            return _InboxDeliveryItem(
                **fields,
                notified_at=(_utc(row["notified_at"])
                             if row["notified_at"] is not None else None),
                read_at=_utc(row["read_at"]) if row["read_at"] is not None else None,
                read_call_id=row["read_call_id"],
            )
        return AgentInboxItem(**fields)

    def enqueue(self, conversation_id: str, agent_key: str,
                payload: dict[str, Any], source: str = "agent_msg",
                *, now: float | None = None) -> AgentInboxItem:
        conversation_id = _required(conversation_id, "conversation_id")
        agent_key = _agent(agent_key)
        timestamp = time.time() if now is None else float(now)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._enqueue_item(
                connection, conversation_id, agent_key, payload, source, timestamp)
        return self._item(row)

    def _enqueue_item(self, connection, conversation_id, agent_key,
                      payload, source, timestamp, *, enqueued_at=None) -> sqlite3.Row:
        """Insert under the caller's write transaction, including the durable fence."""
        encoded = self._payload_json(payload)
        msg_id = _required(payload.get("msg_id"), "msg_id")
        source = _required(source, "source")
        created_at = (
            timestamp if not source.startswith("inbox:")
            and payload.get("ts") is None and payload.get("timestamp") is None
            else _created_at(payload))
        fence = connection.execute(
            "SELECT cutoff FROM agent_inbox_fences WHERE conversation_id=? AND agent_key=?",
            (conversation_id, agent_key)).fetchone()
        state = "discarded" if fence is not None and created_at <= fence[0] else "pending"
        connection.execute(
            """INSERT INTO agent_inbox_items(
                   conversation_id, agent_key, msg_id, payload_json, source,
                   state, created_at, enqueued_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(conversation_id, agent_key, msg_id) DO NOTHING""",
            (conversation_id, agent_key, msg_id, encoded, source,
             state, created_at, timestamp if enqueued_at is None else enqueued_at, timestamp))
        row = connection.execute(
            """SELECT * FROM agent_inbox_items
               WHERE conversation_id=? AND agent_key=? AND msg_id=?""",
            (conversation_id, agent_key, msg_id)).fetchone()
        if row is None:
            raise RuntimeError("inbox item was not persisted")
        if (row["payload_json"] != encoded
                and self._meaningful(row["payload_json"])
                != self._meaningful(encoded)):
            raise ValueError("msg_id already has a different inbox payload")
        return row

    def enqueue_context_history(self, conversation_id: str, agent_key: str,
                                payload: dict[str, Any]) -> bool:
        """Transfer a legacy context once, even after read-row retention expires."""
        conversation_id = _required(conversation_id, "conversation_id")
        agent_key = _agent(agent_key)
        msg_id = _required(payload.get("msg_id"), "msg_id")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                    "SELECT 1 FROM agent_inbox_context_receipts "
                    "WHERE conversation_id=? AND agent_key=? AND msg_id=?",
                    (conversation_id, agent_key, msg_id)).fetchone():
                return False
            row = self._enqueue_item(connection, conversation_id, agent_key,
                                     payload, "inbox:catch_up", time.time())
            connection.execute(
                "INSERT INTO agent_inbox_context_receipts VALUES (?, ?, ?)",
                (conversation_id, agent_key, msg_id))
            return row["state"] in ("pending", "notified")

    def prepare_receipt(self, conversation_id: str, agent_key: str,
                        payload: dict[str, Any], source: str,
                        *, now: float | None = None) -> str:
        conversation_id = _required(conversation_id, "conversation_id")
        agent_key = _agent(agent_key)
        msg_id = _required(payload.get("msg_id"), "msg_id")
        source = _required(source, "source")
        timestamp = time.time() if now is None else float(now)
        encoded = self._payload_json(payload)
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO agent_ingress_receipts(
                       conversation_id, agent_key, msg_id, payload_json, source,
                       state, prepared_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, 'prepared', ?, ?)
                   ON CONFLICT(conversation_id, agent_key, msg_id) DO NOTHING""",
                (conversation_id, agent_key, msg_id, encoded, source,
                 timestamp, timestamp))
            row = connection.execute(
                """SELECT state, payload_json FROM agent_ingress_receipts
                   WHERE conversation_id=? AND agent_key=? AND msg_id=?""",
                (conversation_id, agent_key, msg_id)).fetchone()
        if row is None:
            raise RuntimeError("ingress receipt was not persisted")
        if row["payload_json"] != encoded:
            raise ValueError("msg_id already has a different ingress payload")
        return str(row["state"])

    def mark_transcript_persisted(
        self, conversation_id: str, agent_key: str, msg_id: str,
        *, now: float | None = None,
    ) -> AgentInboxItem:
        conversation_id = _required(conversation_id, "conversation_id")
        agent_key = _agent(agent_key)
        msg_id = _required(msg_id, "msg_id")
        timestamp = time.time() if now is None else float(now)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            receipt = connection.execute(
                """SELECT * FROM agent_ingress_receipts
                   WHERE conversation_id=? AND agent_key=? AND msg_id=?""",
                (conversation_id, agent_key, msg_id)).fetchone()
            if receipt is None:
                raise KeyError("ingress receipt does not exist")
            connection.execute(
                """UPDATE agent_ingress_receipts
                   SET state='transcript_persisted', updated_at=?
                   WHERE conversation_id=? AND agent_key=? AND msg_id=?
                     AND state='prepared'""",
                (timestamp, conversation_id, agent_key, msg_id))
            row = self._enqueue_item(
                connection, conversation_id, agent_key,
                json.loads(receipt["payload_json"]), receipt["source"],
                timestamp, enqueued_at=receipt["prepared_at"])
            connection.execute(
                """UPDATE agent_ingress_receipts SET state='queued', updated_at=?
                   WHERE conversation_id=? AND agent_key=? AND msg_id=?""",
                (timestamp, conversation_id, agent_key, msg_id))
        if row is None:
            raise RuntimeError("receipt promotion did not create an inbox item")
        return self._item(row)

    @staticmethod
    def _rows_for_ids(connection, conversation_id, agent_key, ids,
                      run_id=None):
        if not ids:
            return []
        marks = ",".join("?" for _ in ids)
        query = (
            f"SELECT * FROM agent_inbox_items WHERE conversation_id=? "  # nosec B608 - placeholders only
            f"AND agent_key=? AND msg_id IN ({marks})")
        params: list[Any] = [conversation_id, agent_key, *ids]
        if run_id is not None:
            query += " AND owner_run_id=? AND substr(source, 1, 6)!='inbox:'"
            params.append(run_id)
        rows = connection.execute(query, params).fetchall()
        by_id = {row["msg_id"]: row for row in rows}
        return [by_id[item_id] for item_id in ids if item_id in by_id]

    @staticmethod
    def _recover_expired(connection: sqlite3.Connection, now: float) -> int:
        cursor = connection.execute(
            """UPDATE agent_inbox_items
               SET state='pending', owner_run_id=NULL, owner_task_id=NULL,
                   lease_expires_at=NULL, updated_at=?
               WHERE state='claimed' AND lease_expires_at <= ?""",
            (now, now))
        connection.execute(
            "DELETE FROM agent_inbox_claims WHERE lease_expires_at <= ?", (now,))
        return int(cursor.rowcount)

    def claim(self, conversation_id: str, agent_key: str, run_id: str,
              task_id: str, max_messages: int = 0,
              lease_seconds: float = 60.0,
              sources: Iterable[str] | None = None,
              *, now: float | None = None,
              include_msg_ids: Iterable[str] | None = None,
              max_sequence: int | None = None,
              ) -> tuple[AgentInboxClaim | None, tuple[AgentInboxItem, ...]]:
        conversation_id = _required(conversation_id, "conversation_id")
        agent_key = _agent(agent_key)
        run_id = _required(run_id, "run_id")
        task_id = _required(task_id, "task_id")
        message_limit = int(max_messages)
        if message_limit < 0:
            raise ValueError("max_messages must be non-negative")
        timestamp = time.time() if now is None else float(now)
        expires = timestamp + max(1.0, float(lease_seconds))
        source_values = tuple(dict.fromkeys(
            str(v) for v in (sources or ()) if str(v)))
        wanted = tuple(dict.fromkeys(
            str(v) for v in (include_msg_ids or ()) if str(v)))
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._recover_expired(connection, timestamp)
            previous = connection.execute(
                """SELECT * FROM agent_inbox_claims
                   WHERE conversation_id=? AND agent_key=? AND run_id=?
                     AND task_id=?""",
                (conversation_id, agent_key, run_id, task_id)).fetchone()
            if previous:
                ids = tuple(json.loads(previous["item_ids_json"]))
                rows = self._rows_for_ids(
                    connection, conversation_id, agent_key, ids, run_id)
                claim = AgentInboxClaim(
                    claim_id=previous["claim_id"], run_id=run_id,
                    task_id=task_id, item_ids=ids,
                    lease_expires_at=_utc(previous["lease_expires_at"]))
                return claim, tuple(self._item(row) for row in rows)
            clauses = [
                "conversation_id=?", "agent_key=?", "state='pending'",
                "substr(source, 1, 6)!='inbox:'"]
            params: list[Any] = [conversation_id, agent_key]
            if source_values:
                clauses.append(
                    "source IN (" + ",".join("?" for _ in source_values) + ")")
                params.extend(source_values)
            if wanted:
                clauses.append(
                    "msg_id IN (" + ",".join("?" for _ in wanted) + ")")
                params.extend(wanted)
            if max_sequence is not None:
                clauses.append("sequence <= ?")
                params.append(max(0, int(max_sequence)))
            limit_clause = ""
            if message_limit > 0:
                limit_clause = " LIMIT ?"
                params.append(message_limit)
            rows = connection.execute(
                "SELECT msg_id FROM agent_inbox_items WHERE "  # nosec B608 - fixed clauses only
                + " AND ".join(clauses) + " ORDER BY sequence" + limit_clause,
                params).fetchall()
            ids = tuple(row["msg_id"] for row in rows)
            if not ids:
                return None, ()
            claim_id = str(uuid.uuid4())
            marks = ",".join("?" for _ in ids)
            connection.execute(
                f"UPDATE agent_inbox_items "  # nosec B608 - placeholders only
                "SET state='claimed', owner_run_id=?, owner_task_id=?, "
                "lease_expires_at=?, updated_at=? "
                "WHERE conversation_id=? AND agent_key=? AND state='pending' "
                f"AND msg_id IN ({marks})",
                (run_id, task_id, expires, timestamp,
                 conversation_id, agent_key, *ids))
            connection.execute(
                "INSERT INTO agent_inbox_claims VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (claim_id, conversation_id, agent_key, run_id, task_id,
                 json.dumps(ids), expires, timestamp))
            claimed = self._rows_for_ids(
                connection, conversation_id, agent_key, ids, run_id)
        claim = AgentInboxClaim(
            claim_id=claim_id, run_id=run_id, task_id=task_id,
            item_ids=ids, lease_expires_at=_utc(expires))
        return claim, tuple(self._item(row) for row in claimed)

    def recover_expired_leases(self, *, now: float | None = None) -> int:
        timestamp = time.time() if now is None else float(now)
        with self._lock, self._connect() as connection:
            return self._recover_expired(connection, timestamp)

    def recover_orphaned_workflow_claims(
            self, recoverable_run_ids: Iterable[str],
            *, now: float | None = None) -> int:
        """Release workflow-owned claims whose durable run cannot recover."""
        run_ids = tuple(dict.fromkeys(
            _required(value, "recoverable_run_id")
            for value in recoverable_run_ids))
        timestamp = time.time() if now is None else float(now)
        clause = ""
        params: list[Any] = [timestamp]
        if run_ids:
            clause = " AND owner_run_id NOT IN (" + ",".join(
                "?" for _ in run_ids) + ")"
            params.extend(run_ids)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE agent_inbox_items "  # nosec B608 - fixed optional clause
                "SET state='pending', owner_run_id=NULL, owner_task_id=NULL, "
                "lease_expires_at=NULL, updated_at=? WHERE state='claimed' "
                "AND substr(owner_run_id, 1, 3)='wr_'" + clause,
                params)
            claim_clause = ""
            claim_params: list[Any] = []
            if run_ids:
                claim_clause = " AND run_id NOT IN (" + ",".join(
                    "?" for _ in run_ids) + ")"
                claim_params.extend(run_ids)
            connection.execute(
                "DELETE FROM agent_inbox_claims "  # nosec B608 - fixed optional clause
                "WHERE substr(run_id, 1, 3)='wr_'" + claim_clause,
                claim_params)
            return int(cursor.rowcount)

    @staticmethod
    def _drop_claims(connection, conversation_id, agent_key, run_id):
        connection.execute(
            """DELETE FROM agent_inbox_claims
               WHERE conversation_id=? AND agent_key=? AND run_id=?""",
            (conversation_id, agent_key, run_id))

    def acknowledge(self, conversation_id: str, agent_key: str, run_id: str,
                    msg_ids: Iterable[str], *,
                    now: float | None = None) -> int:
        conversation_id = _required(conversation_id, "conversation_id")
        agent_key = _agent(agent_key)
        run_id = _required(run_id, "run_id")
        ids = tuple(dict.fromkeys(str(v) for v in msg_ids if str(v)))
        if not ids:
            return 0
        timestamp = time.time() if now is None else float(now)
        marks = ",".join("?" for _ in ids)
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE agent_inbox_items "  # nosec B608 - placeholders only
                "SET state='acknowledged', owner_run_id=NULL, "
                "owner_task_id=NULL, lease_expires_at=NULL, updated_at=? "
                "WHERE conversation_id=? AND agent_key=? AND state='claimed' "
                f"AND owner_run_id=? AND msg_id IN ({marks})",
                (timestamp, conversation_id, agent_key, run_id, *ids))
            self._drop_claims(
                connection, conversation_id, agent_key, run_id)
            return int(cursor.rowcount)

    def release(self, conversation_id: str, agent_key: str, run_id: str,
                msg_ids: Iterable[str] | None = None,
                *, now: float | None = None) -> int:
        conversation_id = _required(conversation_id, "conversation_id")
        agent_key = _agent(agent_key)
        run_id = _required(run_id, "run_id")
        timestamp = time.time() if now is None else float(now)
        ids = tuple(dict.fromkeys(
            str(v) for v in (msg_ids or ()) if str(v)))
        query = (
            """UPDATE agent_inbox_items
               SET state='pending', owner_run_id=NULL, owner_task_id=NULL,
                   lease_expires_at=NULL, updated_at=?
               WHERE conversation_id=? AND agent_key=? AND state='claimed'
                 AND owner_run_id=?""")
        params: list[Any] = [timestamp, conversation_id, agent_key, run_id]
        if ids:
            query += " AND msg_id IN (" + ",".join("?" for _ in ids) + ")"
            params.extend(ids)
        with self._lock, self._connect() as connection:
            cursor = connection.execute(query, params)
            self._drop_claims(
                connection, conversation_id, agent_key, run_id)
            return int(cursor.rowcount)

    def transfer(self, conversation_id: str, agent_key: str,
                 old_run_id: str, new_run_id: str,
                 lease_seconds: float = 60.0,
                 *, now: float | None = None) -> int:
        conversation_id = _required(conversation_id, "conversation_id")
        agent_key = _agent(agent_key)
        old_run_id = _required(old_run_id, "old_run_id")
        new_run_id = _required(new_run_id, "new_run_id")
        timestamp = time.time() if now is None else float(now)
        expires = timestamp + max(1.0, float(lease_seconds))
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """UPDATE agent_inbox_items SET owner_run_id=?,
                       lease_expires_at=?, updated_at=?
                   WHERE conversation_id=? AND agent_key=? AND state='claimed'
                     AND owner_run_id=?""",
                (new_run_id, expires, timestamp, conversation_id,
                 agent_key, old_run_id))
            connection.execute(
                """UPDATE agent_inbox_claims
                   SET run_id=?, task_id='restart:' || ? || ':' || task_id,
                       lease_expires_at=?
                   WHERE conversation_id=? AND agent_key=? AND run_id=?""",
                (new_run_id, old_run_id, expires,
                 conversation_id, agent_key, old_run_id))
            return int(cursor.rowcount)

    def discard_through(self, conversation_id: str, agent_key: str,
                        cutoff: float, *, now: float | None = None) -> int:
        """Fence creation time and discard older unread/leased work atomically."""
        conversation_id = _required(conversation_id, "conversation_id")
        agent_key = _agent(agent_key)
        timestamp = time.time() if now is None else float(now)
        cutoff = float(cutoff)
        if not math.isfinite(cutoff):
            raise ValueError("cutoff must be finite")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO agent_inbox_fences VALUES (?, ?, ?)
                   ON CONFLICT(conversation_id, agent_key) DO UPDATE SET
                       cutoff=max(cutoff, excluded.cutoff)""",
                (conversation_id, agent_key, cutoff))
            cursor = connection.execute(
                """UPDATE agent_inbox_items SET state='discarded',
                       owner_run_id=NULL, owner_task_id=NULL,
                       lease_expires_at=NULL, updated_at=?
                   WHERE conversation_id=? AND agent_key=?
                     AND state IN ('pending','notified','claimed')
                     AND created_at <= (SELECT cutoff FROM agent_inbox_fences
                                        WHERE conversation_id=? AND agent_key=?)""",
                (timestamp, conversation_id, agent_key, conversation_id, agent_key))
            connection.execute(
                """DELETE FROM agent_inbox_claims
                   WHERE conversation_id=? AND agent_key=? AND NOT EXISTS (
                       SELECT 1 FROM agent_inbox_items AS i
                       WHERE i.conversation_id=agent_inbox_claims.conversation_id
                         AND i.agent_key=agent_inbox_claims.agent_key
                         AND i.owner_run_id=agent_inbox_claims.run_id
                         AND i.state='claimed')""",
                (conversation_id, agent_key))
            return int(cursor.rowcount)

    def discard_msg_ids(self, conversation_id: str, agent_key: str,
                        msg_ids: Iterable[str],
                        sources: Iterable[str] | None = None,
                        *, now: float | None = None) -> int:
        ids = tuple(dict.fromkeys(str(value) for value in msg_ids if str(value)))
        if not ids:
            return 0
        conversation_id = _required(conversation_id, "conversation_id")
        agent_key = _agent(agent_key)
        timestamp = time.time() if now is None else float(now)
        clauses = [
            "conversation_id=?", "agent_key=?",
            "state IN ('pending','notified','claimed')",
            "msg_id IN (" + ",".join("?" for _ in ids) + ")",
        ]
        params: list[Any] = [conversation_id, agent_key, *ids]
        source_values = tuple(dict.fromkeys(
            str(value) for value in (sources or ()) if str(value)))
        if source_values:
            clauses.append(
                "source IN (" + ",".join("?" for _ in source_values) + ")")
            params.extend(source_values)
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE agent_inbox_items SET state='discarded', "  # nosec B608 - fixed clauses only
                "owner_run_id=NULL, owner_task_id=NULL, "
                "lease_expires_at=NULL, updated_at=? WHERE "
                + " AND ".join(clauses), (timestamp, *params))
            return int(cursor.rowcount)

    def list_items(self, conversation_id: str, agent_key: str,
                   states: Iterable[str] | None = None,
                   limit: int = 1000) -> tuple[AgentInboxItem, ...]:
        clauses = ["conversation_id=?", "agent_key=?"]
        params: list[Any] = [
            _required(conversation_id, "conversation_id"), _agent(agent_key)]
        values = tuple(dict.fromkeys(
            str(v) for v in (states or ()) if str(v)))
        if values:
            clauses.append("state IN (" + ",".join("?" for _ in values) + ")")
            params.extend(values)
        params.append(max(1, int(limit)))
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM agent_inbox_items WHERE "  # nosec B608 - fixed clauses only
                + " AND ".join(clauses)
                + " ORDER BY sequence LIMIT ?", params).fetchall()
        return tuple(self._item(row) for row in rows)

    def list_pending(self, conversation_id: str, agent_key: str, *,
                     source_prefix: str | None = None,
                     exclude_source_prefix: str | None = None,
                     limit: int = 1000) -> tuple[AgentInboxItem, ...]:
        """Unread pending/notified items in order, optionally filtered by source.

        ``source_prefix`` keeps only sources starting with it;
        ``exclude_source_prefix`` drops them. The comparison uses ``substr``
        so prefix characters need no LIKE escaping.
        """
        clauses = ["conversation_id=?", "agent_key=?", "state IN ('pending','notified')"]
        params: list[Any] = [
            _required(conversation_id, "conversation_id"), _agent(agent_key)]
        if source_prefix:
            clauses.append("substr(source, 1, ?)=?")
            params.extend((len(source_prefix), source_prefix))
        if exclude_source_prefix:
            clauses.append("substr(source, 1, ?)!=?")
            params.extend((len(exclude_source_prefix), exclude_source_prefix))
        params.append(max(1, int(limit)))
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM agent_inbox_items WHERE "  # nosec B608 - fixed clauses only
                + " AND ".join(clauses)
                + " ORDER BY sequence LIMIT ?", params).fetchall()
        return tuple(self._item(row) for row in rows)

    def notice_state(self, conversation_id: str,
                     agent_key: str) -> tuple[int, int]:
        """Return ``(notified_sequence, unread_turns)``; ``(0, 0)`` if unset."""
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """SELECT notified_sequence, unread_turns
                   FROM agent_inbox_notices
                   WHERE conversation_id=? AND agent_key=?""",
                (_required(conversation_id, "conversation_id"),
                 _agent(agent_key))).fetchone()
        if row is None:
            return 0, 0
        return int(row["notified_sequence"]), int(row["unread_turns"])

    def set_notice_state(self, conversation_id: str, agent_key: str, *,
                         notified_sequence: int | None = None,
                         unread_turns: int | None = None) -> None:
        """Upsert the notice state; only the given fields change."""
        if notified_sequence is None and unread_turns is None:
            raise ValueError(
                "notified_sequence or unread_turns is required")
        values = {
            "conversation_id": _required(conversation_id, "conversation_id"),
            "agent_key": _agent(agent_key),
            "notified": (None if notified_sequence is None
                         else int(notified_sequence)),
            "turns": None if unread_turns is None else int(unread_turns),
            "now": time.time(),
        }
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO agent_inbox_notices(
                       conversation_id, agent_key, notified_sequence,
                       unread_turns, updated_at
                   ) VALUES (:conversation_id, :agent_key,
                             coalesce(:notified, 0), coalesce(:turns, 0), :now)
                   ON CONFLICT(conversation_id, agent_key) DO UPDATE SET
                       notified_sequence=coalesce(:notified, notified_sequence),
                       unread_turns=coalesce(:turns, unread_turns),
                       updated_at=:now""",
                values)

    def read_offset(self, conversation_id: str, agent_key: str,
                    msg_id: str) -> int:
        """Characters of ``msg_id`` already returned by a paged read."""
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """SELECT read_offset FROM agent_inbox_read_offsets
                   WHERE conversation_id=? AND agent_key=? AND msg_id=?""",
                (_required(conversation_id, "conversation_id"),
                 _agent(agent_key), _required(msg_id, "msg_id"))).fetchone()
        return int(row["read_offset"]) if row else 0

    def set_read_offset(self, conversation_id: str, agent_key: str,
                        msg_id: str, offset: int) -> None:
        offset = int(offset)
        if offset < 0:
            raise ValueError("offset must be non-negative")
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO agent_inbox_read_offsets(
                       conversation_id, agent_key, msg_id, read_offset,
                       updated_at
                   ) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(conversation_id, agent_key, msg_id) DO UPDATE SET
                       read_offset=excluded.read_offset,
                       updated_at=excluded.updated_at""",
                (_required(conversation_id, "conversation_id"),
                 _agent(agent_key), _required(msg_id, "msg_id"), offset,
                 time.time()))

    def mark_notified(self, conversation_id: str, agent_key: str,
                      through_sequence: int, *, now: float | None = None) -> int:
        """Record the first successful notice for pending inbox items through a cutoff."""
        timestamp = time.time() if now is None else float(now)
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """UPDATE agent_inbox_items
                   SET state='notified', notified_at=?, updated_at=?
                   WHERE conversation_id=? AND agent_key=? AND state='pending'
                     AND substr(source, 1, 6)='inbox:' AND sequence <= ?""",
                (timestamp, timestamp, _required(conversation_id, "conversation_id"),
                 _agent(agent_key), int(through_sequence)))
            return int(cursor.rowcount)

    def mark_read(self, conversation_id: str, agent_key: str,
                  msg_ids: Iterable[str], read_call_id: str = "", *,
                  now: float | None = None) -> int:
        """Mark fully returned inbox items read, preserving the first read audit."""
        conversation_id = _required(conversation_id, "conversation_id")
        agent_key = _agent(agent_key)
        ids = tuple(dict.fromkeys(str(v) for v in msg_ids if str(v)))
        if not ids:
            return 0
        timestamp = time.time() if now is None else float(now)
        marks = ",".join("?" for _ in ids)
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE agent_inbox_items "  # nosec B608 - placeholders only
                "SET state='read', owner_run_id=NULL, "
                "owner_task_id=NULL, lease_expires_at=NULL, updated_at=?, "
                "read_at=?, read_call_id=? "
                "WHERE conversation_id=? AND agent_key=? "
                "AND state IN ('pending','notified') AND substr(source, 1, 6)='inbox:' "
                f"AND msg_id IN ({marks})",
                (timestamp, timestamp, read_call_id, conversation_id, agent_key, *ids))
            connection.execute(
                "DELETE FROM agent_inbox_read_offsets "  # nosec B608 - placeholders only
                "WHERE conversation_id=? AND agent_key=? "
                f"AND msg_id IN ({marks})",
                (conversation_id, agent_key, *ids))
            return int(cursor.rowcount)

    def purge_acknowledged(self, older_than_seconds: float, *,
                           now: float | None = None) -> int:
        """Delete read/acknowledged/discarded rows older than the retention.

        Also drops read offsets whose item is no longer unread or claimed.
        Returns the number of deleted item rows.
        """
        retention = float(older_than_seconds)
        if retention < 0:
            raise ValueError("older_than_seconds must be non-negative")
        timestamp = time.time() if now is None else float(now)
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """DELETE FROM agent_inbox_items
                   WHERE state IN ('read','acknowledged','discarded')
                     AND updated_at < ?""",
                (timestamp - retention,))
            connection.execute(
                """DELETE FROM agent_inbox_read_offsets
                   WHERE NOT EXISTS (
                       SELECT 1 FROM agent_inbox_items AS i
                       WHERE i.conversation_id=agent_inbox_read_offsets.conversation_id
                         AND i.agent_key=agent_inbox_read_offsets.agent_key
                         AND i.msg_id=agent_inbox_read_offsets.msg_id
                         AND i.state IN ('pending','notified','claimed'))""")
            return int(cursor.rowcount)

    def pending_count(self, conversation_id: str, agent_key: str) -> int:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """SELECT count(*) AS n FROM agent_inbox_items
                   WHERE conversation_id=? AND agent_key=?
                     AND state IN ('pending','notified')""",
                (_required(conversation_id, "conversation_id"),
                 _agent(agent_key))).fetchone()
        return int(row["n"])

    def latest_sequence(self, conversation_id: str, agent_key: str) -> int:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """SELECT coalesce(max(sequence), 0) AS value
                   FROM agent_inbox_items
                   WHERE conversation_id=? AND agent_key=?""",
                (_required(conversation_id, "conversation_id"),
                 _agent(agent_key))).fetchone()
        return int(row["value"])

    def list_agent_keys(self, conversation_id: str) -> tuple[str, ...]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """SELECT DISTINCT agent_key FROM agent_inbox_items
                   WHERE conversation_id=? AND state IN ('pending','notified','claimed')
                   ORDER BY agent_key""",
                (_required(conversation_id, "conversation_id"),)).fetchall()
        return tuple(str(row["agent_key"]) for row in rows)

    def list_ready_keys(self) -> tuple[tuple[str, str], ...]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """SELECT DISTINCT conversation_id, agent_key
                   FROM agent_inbox_items WHERE state IN ('pending','notified')
                   ORDER BY conversation_id, agent_key""").fetchall()
        return tuple(
            (str(row["conversation_id"]), str(row["agent_key"]))
            for row in rows)

    def reconcile_receipts(
        self, transcript_contains: Callable[[str, str], bool],
        transcript_append: Callable[[str, str, dict[str, Any]], Any] | None = None,
    ) -> dict[str, int]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM agent_ingress_receipts
                   WHERE state != 'queued' ORDER BY prepared_at""").fetchall()
        repaired = 0
        awaiting = 0
        for row in rows:
            present = transcript_contains(
                row["conversation_id"], row["msg_id"])
            if not present and transcript_append is not None:
                transcript_append(
                    row["conversation_id"], row["agent_key"],
                    json.loads(row["payload_json"]))
                present = True
            if present:
                self.mark_transcript_persisted(
                    row["conversation_id"], row["agent_key"], row["msg_id"])
                repaired += 1
            else:
                awaiting += 1
        return {"repaired": repaired, "awaiting_transcript": awaiting}

    def migrate_pending_jsonl(self, conversation_id: str, agent_key: str,
                              source_path: Path) -> dict[str, Any]:
        conversation_id = _required(conversation_id, "conversation_id")
        agent_key = _agent(agent_key)
        path = Path(source_path)
        raw = path.read_bytes() if path.exists() else b""
        digest = hashlib.sha256(raw).hexdigest()
        rows = []
        for number, line in enumerate(raw.splitlines(), 1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"invalid pending JSONL line {number}: {exc}") from exc
            if not isinstance(payload, dict) or not payload.get("msg_id"):
                raise ValueError(
                    f"invalid pending JSONL line {number}: stamped object required")
            _created_at(payload)
            rows.append(payload)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute(
                """SELECT * FROM agent_inbox_migrations
                   WHERE conversation_id=? AND agent_key=?""",
                (conversation_id, agent_key)).fetchone()
            if previous:
                if (previous["source_sha256"] != digest
                        or previous["item_count"] != len(rows)):
                    raise ValueError("legacy pending queue changed after migration")
                return {"migrated": False, "count": len(rows), "sha256": digest}
            timestamp = time.time()
            for payload in rows:
                self._enqueue_item(
                    connection, conversation_id, agent_key, payload,
                    self._legacy_source(payload), timestamp)
            connection.execute(
                "INSERT INTO agent_inbox_migrations VALUES (?, ?, ?, ?, ?, ?)",
                (conversation_id, agent_key, str(path), digest, len(rows),
                 time.time()))
            if rows:
                ids = tuple(dict.fromkeys(str(row["msg_id"]) for row in rows))
                count = connection.execute(
                    "SELECT count(*) AS n FROM agent_inbox_items "  # nosec B608 - placeholders only
                    "WHERE conversation_id=? AND agent_key=? AND msg_id IN ("
                    + ",".join("?" for _ in ids) + ")",
                    (conversation_id, agent_key, *ids)).fetchone()["n"]
                if count != len(ids):
                    raise RuntimeError(
                        "legacy pending migration count validation failed")
        return {"migrated": True, "count": len(rows), "sha256": digest}

    @staticmethod
    def _legacy_source(payload: dict[str, Any]) -> str:
        """Classify legacy producer tags without inventing an unknown source."""
        source = _required(payload.get("_pending_source"), "_pending_source")
        if source == "preempt_rescue":
            origin = payload.get("source") or {}
            origin_type = origin.get("type")
            if origin_type == "user" and not origin.get("delegate"):
                return source
            if origin_type in {"agent_delegate", "a2a", "cross_conversation_delegate"}:
                source = ("delegate_reply" if origin.get("kind") == "reply"
                          else "delegate_message")
            elif origin.get("delegate"):
                source = "delegate_result"
            elif origin_type == "scheduled_wakeup":
                source = "scheduled_wakeup"
            elif origin_type == "system" and origin.get("name") == "background":
                source = "background_result"
            else:
                raise ValueError("legacy preempt_rescue requires a recognized origin")
        if source in {"http", "agent_msg", "resume_agent", "preempt_soft", "skill_run"}:
            return source
        if source.startswith("inbox:"):
            _required(source[6:], "inbox kind")
            return source
        if source == "delegate_reply":
            origin = payload.get("source")
            kind = origin.get("kind") if isinstance(origin, dict) else None
            source = {"reply": "delegate_reply", "request": "delegate_message"}.get(
                kind, "delegate_result")
        elif source == "bg_tool":
            source = "background_result"
        elif source == "scheduled_wakeup":
            if "continuation" in str(payload.get("content", "")).lower():
                source = "continuation"
        elif source.startswith("skill_"):
            source = "skill_event"
        elif source not in {"delegate_message", "delegate_result", "background_result",
                            "continuation", "task_checkin", "catch_up", "workflow_proposal"}:
            raise ValueError(f"unknown legacy pending source: {source!r}")
        return "inbox:" + source

    def migration_status(self, conversation_id: str,
                         agent_key: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM agent_inbox_migrations
                   WHERE conversation_id=? AND agent_key=?""",
                (_required(conversation_id, "conversation_id"),
                 _agent(agent_key))).fetchone()
        return dict(row) if row else None

    def delete_conversation(self, conversation_id: str) -> int:
        conversation_id = _required(conversation_id, "conversation_id")
        deleted = 0
        with self._lock, self._connect() as connection:
            for table in (
                "agent_inbox_claims", "agent_inbox_items",
                "agent_ingress_receipts", "agent_inbox_migrations",
                "agent_inbox_notices", "agent_inbox_read_offsets",
                "agent_inbox_fences", "agent_inbox_context_receipts",
            ):
                deleted += connection.execute(
                    f"DELETE FROM {table} WHERE conversation_id=?",  # nosec B608 - table whitelist
                    (conversation_id,)).rowcount
        return int(deleted)


__all__ = ["AgentInboxStore"]
