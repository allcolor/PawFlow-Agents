"""Durable storage for A2A publications, tasks, contexts, and named targets.

Inbound A2A publications are deliberately separate from MCP publications:
one conversation may expose several agents and A2A clients do not hold an
exclusive CLI lease. Raw bearer keys are returned once and only hashes are
persisted.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import core.paths as _paths


_CONTEXT_POLICIES = frozenset({"isolated", "shared"})
_TARGET_KINDS = frozenset({"local", "remote"})


class A2AStore:
    """Thread-safe SQLite state for the PawFlow A2A transport."""

    _instance: Optional["A2AStore"] = None
    _instance_lock = threading.Lock()

    @classmethod
    def instance(cls) -> "A2AStore":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self, database_path: Optional[Path] = None) -> None:
        self._database_path_override = Path(database_path) if database_path else None
        self._lock = threading.RLock()
        self._initialize()

    @property
    def database_path(self) -> Path:
        return self._database_path_override or (_paths.SYSTEM_DIR / "a2a.sqlite3")

    def _connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS a2a_publications (
                    publication_id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    label TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    context_policy TEXT NOT NULL DEFAULT 'isolated',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(conversation_id, agent_name)
                );
                CREATE INDEX IF NOT EXISTS idx_a2a_pub_owner_conv
                    ON a2a_publications(owner_user_id, conversation_id);

                CREATE TABLE IF NOT EXISTS a2a_api_keys (
                    key_id TEXT PRIMARY KEY,
                    publication_id TEXT NOT NULL REFERENCES a2a_publications(publication_id)
                        ON DELETE CASCADE,
                    label TEXT NOT NULL,
                    prefix TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    created_at REAL NOT NULL,
                    last_used_at REAL NOT NULL DEFAULT 0,
                    revoked_at REAL NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_a2a_keys_publication
                    ON a2a_api_keys(publication_id, revoked_at);

                CREATE TABLE IF NOT EXISTS a2a_contexts (
                    context_id TEXT PRIMARY KEY,
                    publication_id TEXT NOT NULL REFERENCES a2a_publications(publication_id)
                        ON DELETE CASCADE,
                    key_id TEXT NOT NULL REFERENCES a2a_api_keys(key_id) ON DELETE CASCADE,
                    internal_conversation_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    last_seen_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_a2a_context_pub
                    ON a2a_contexts(publication_id, key_id);

                CREATE TABLE IF NOT EXISTS a2a_tasks (
                    task_id TEXT PRIMARY KEY,
                    publication_id TEXT NOT NULL REFERENCES a2a_publications(publication_id)
                        ON DELETE CASCADE,
                    context_id TEXT NOT NULL,
                    key_id TEXT NOT NULL,
                    internal_conversation_id TEXT NOT NULL,
                    turn_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    response_json TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_a2a_tasks_pub_context
                    ON a2a_tasks(publication_id, context_id, updated_at);

                CREATE TABLE IF NOT EXISTS a2a_targets (
                    target_id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL,
                    source_conversation_id TEXT NOT NULL,
                    alias TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    target_conversation_id TEXT NOT NULL DEFAULT '',
                    target_agent TEXT NOT NULL DEFAULT '',
                    agent_card_url TEXT NOT NULL DEFAULT '',
                    auth_secret TEXT NOT NULL DEFAULT '',
                    allow_private INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(source_conversation_id, alias)
                );
                CREATE INDEX IF NOT EXISTS idx_a2a_targets_owner_conv
                    ON a2a_targets(owner_user_id, source_conversation_id);
                """
            )

    @staticmethod
    def _publication_row(row: sqlite3.Row) -> Dict[str, Any]:
        data = dict(row)
        data["enabled"] = bool(data.get("enabled"))
        return data

    @staticmethod
    def _key_row(row: sqlite3.Row) -> Dict[str, Any]:
        data = dict(row)
        data.pop("token_hash", None)
        data["revoked"] = bool(data.get("revoked_at"))
        return data

    @staticmethod
    def _target_row(row: sqlite3.Row) -> Dict[str, Any]:
        data = dict(row)
        data["allow_private"] = bool(data.get("allow_private"))
        return data

    @staticmethod
    def _task_row(row: sqlite3.Row) -> Dict[str, Any]:
        data = dict(row)
        for key in ("request_json", "response_json"):
            raw = data.pop(key, "")
            data[key[:-5]] = json.loads(raw) if raw else None
        return data

    def configure_publication(
        self, owner_user_id: str, conversation_id: str, agent_name: str, *,
        label: str = "", description: str = "", context_policy: str = "isolated",
        enabled: bool = True,
    ) -> Dict[str, Any]:
        if not owner_user_id or not conversation_id or not agent_name:
            raise ValueError("owner_user_id, conversation_id and agent_name are required")
        context_policy = str(context_policy or "").strip().lower()
        if context_policy not in _CONTEXT_POLICIES:
            raise ValueError("context_policy must be 'isolated' or 'shared'")
        now = time.time()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT publication_id, owner_user_id FROM a2a_publications "
                "WHERE conversation_id = ? AND lower(agent_name) = lower(?)",
                (conversation_id, agent_name),
            ).fetchone()
            if row and row["owner_user_id"] != owner_user_id:
                raise PermissionError("A2A publication belongs to another owner")
            if row:
                publication_id = row["publication_id"]
                connection.execute(
                    "UPDATE a2a_publications SET agent_name=?, label=?, description=?, "
                    "context_policy=?, enabled=?, updated_at=? WHERE publication_id=?",
                    (agent_name, label or agent_name, description, context_policy,
                     int(bool(enabled)), now, publication_id),
                )
            else:
                publication_id = "a2ap_" + secrets.token_urlsafe(18).replace("-", "").replace("_", "")
                connection.execute(
                    "INSERT INTO a2a_publications VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (publication_id, owner_user_id, conversation_id, agent_name,
                     label or agent_name, description, context_policy,
                     int(bool(enabled)), now, now),
                )
        result = self.get_publication(publication_id)
        if result is None:
            raise RuntimeError("A2A publication was not persisted")
        return result

    def get_publication(self, publication_id: str) -> Optional[Dict[str, Any]]:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM a2a_publications WHERE publication_id=?",
                (publication_id,),
            ).fetchone()
        return self._publication_row(row) if row else None

    def list_publications(self, conversation_id: str = "") -> List[Dict[str, Any]]:
        query = "SELECT * FROM a2a_publications"
        params: Tuple[Any, ...] = ()
        if conversation_id:
            query += " WHERE conversation_id=?"
            params = (conversation_id,)
        query += " ORDER BY lower(label), created_at"
        with self._lock, self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._publication_row(row) for row in rows]

    def has_publications(self) -> bool:
        with self._lock, self._connect() as connection:
            return connection.execute(
                "SELECT 1 FROM a2a_publications LIMIT 1").fetchone() is not None

    def delete_publication(self, publication_id: str) -> bool:
        with self._lock, self._connect() as connection:
            result = connection.execute(
                "DELETE FROM a2a_publications WHERE publication_id=?",
                (publication_id,),
            )
        return bool(result.rowcount)

    def create_key(self, publication_id: str, label: str) -> Tuple[str, Dict[str, Any]]:
        if not self.get_publication(publication_id):
            raise ValueError("Unknown A2A publication")
        raw = "pfa2a_" + secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        key_id = "a2ak_" + secrets.token_urlsafe(12).replace("-", "").replace("_", "")
        now = time.time()
        prefix = raw[:13]
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO a2a_api_keys VALUES (?, ?, ?, ?, ?, ?, 0, 0)",
                (key_id, publication_id, label or "A2A client", prefix, token_hash, now),
            )
        key = self.get_key(key_id)
        if key is None:
            raise RuntimeError("A2A key was not persisted")
        return raw, key

    def get_key(self, key_id: str) -> Optional[Dict[str, Any]]:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM a2a_api_keys WHERE key_id=?", (key_id,)).fetchone()
        return self._key_row(row) if row else None

    def list_keys(self, publication_id: str) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM a2a_api_keys WHERE publication_id=? ORDER BY created_at",
                (publication_id,),
            ).fetchall()
        return [self._key_row(row) for row in rows]

    def validate_key(self, publication_id: str, raw: str) -> Optional[Dict[str, Any]]:
        if not raw:
            return None
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM a2a_api_keys WHERE publication_id=? AND revoked_at=0",
                (publication_id,),
            ).fetchall()
            row = next((item for item in rows if hmac.compare_digest(item["token_hash"], digest)), None)
            if row:
                connection.execute(
                    "UPDATE a2a_api_keys SET last_used_at=? WHERE key_id=?",
                    (time.time(), row["key_id"]),
                )
        return self._key_row(row) if row else None

    def revoke_key(self, publication_id: str, key_id: str) -> bool:
        with self._lock, self._connect() as connection:
            result = connection.execute(
                "UPDATE a2a_api_keys SET revoked_at=? WHERE publication_id=? "
                "AND key_id=? AND revoked_at=0", (time.time(), publication_id, key_id),
            )
        return bool(result.rowcount)

    def resolve_context(self, publication: Dict[str, Any], key_id: str,
                        requested_context_id: str = "") -> Dict[str, Any]:
        if requested_context_id:
            with self._lock, self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM a2a_contexts WHERE context_id=? AND publication_id=? "
                    "AND key_id=?", (requested_context_id, publication["publication_id"], key_id),
                ).fetchone()
                if row:
                    connection.execute(
                        "UPDATE a2a_contexts SET last_seen_at=? WHERE context_id=?",
                        (time.time(), requested_context_id),
                    )
            if not row:
                raise PermissionError("Unknown A2A context for this client")
            return dict(row)
        context_id = "ctx_" + secrets.token_urlsafe(18).replace("-", "").replace("_", "")
        internal = publication["conversation_id"]
        if publication["context_policy"] == "isolated":
            internal = f"{internal}::a2a::{context_id[4:]}"
        now = time.time()
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO a2a_contexts VALUES (?, ?, ?, ?, ?, ?)",
                (context_id, publication["publication_id"], key_id, internal, now, now),
            )
        return {
            "context_id": context_id, "publication_id": publication["publication_id"],
            "key_id": key_id, "internal_conversation_id": internal,
            "created_at": now, "last_seen_at": now,
        }

    def ensure_named_context(self, publication: Dict[str, Any], key_id: str,
                             name: str) -> Dict[str, Any]:
        """Get or create the context a client names itself (AG-UI threadId).

        A2A contexts are server-issued and an unknown requested id is a
        refusal (`resolve_context`); AG-UI thread ids are CLIENT-chosen, so
        the context must exist on first use. The stored ``context_id`` is a
        digest of (publication, key, name): deterministic per thread and
        collision-free across publications and keys even when two clients
        pick the same thread id.
        """
        if not name:
            raise ValueError("A named context requires a non-empty name")
        digest = hashlib.sha256(
            f"{publication['publication_id']}|{key_id}|{name}".encode("utf-8")
        ).hexdigest()[:24]
        context_id = "agui_" + digest
        now = time.time()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM a2a_contexts WHERE context_id=? AND "
                "publication_id=? AND key_id=?",
                (context_id, publication["publication_id"], key_id),
            ).fetchone()
            if row:
                connection.execute(
                    "UPDATE a2a_contexts SET last_seen_at=? WHERE context_id=?",
                    (now, context_id))
                return dict(row)
            internal = publication["conversation_id"]
            if publication["context_policy"] == "isolated":
                internal = f"{internal}::a2a::{digest}"
            connection.execute(
                "INSERT INTO a2a_contexts VALUES (?, ?, ?, ?, ?, ?)",
                (context_id, publication["publication_id"], key_id,
                 internal, now, now))
        return {
            "context_id": context_id,
            "publication_id": publication["publication_id"],
            "key_id": key_id, "internal_conversation_id": internal,
            "created_at": now, "last_seen_at": now,
        }

    def create_task(self, publication_id: str, context_id: str, key_id: str,
                    internal_conversation_id: str, turn_id: str,
                    request: Dict[str, Any]) -> Dict[str, Any]:
        task_id = "task_" + secrets.token_urlsafe(18).replace("-", "").replace("_", "")
        now = time.time()
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO a2a_tasks VALUES (?, ?, ?, ?, ?, ?, 'submitted', ?, '', '', ?, ?)",
                (task_id, publication_id, context_id, key_id,
                 internal_conversation_id, turn_id,
                 json.dumps(request, ensure_ascii=False), now, now),
            )
        result = self.get_task(publication_id, task_id, key_id)
        if result is None:
            raise RuntimeError("A2A task was not persisted")
        return result

    def update_task(self, task_id: str, state: str, *,
                    response: Optional[Dict[str, Any]] = None, error: str = "") -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE a2a_tasks SET state=?, response_json=?, error=?, updated_at=? "
                "WHERE task_id=?", (state, json.dumps(response, ensure_ascii=False)
                                     if response is not None else "", error,
                                     time.time(), task_id),
            )

    def get_task(self, publication_id: str, task_id: str,
                 key_id: str) -> Optional[Dict[str, Any]]:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM a2a_tasks WHERE publication_id=? AND task_id=? AND key_id=?",
                (publication_id, task_id, key_id),
            ).fetchone()
        return self._task_row(row) if row else None

    def list_tasks(self, publication_id: str, key_id: str,
                   context_id: str = "") -> List[Dict[str, Any]]:
        query = "SELECT * FROM a2a_tasks WHERE publication_id=? AND key_id=?"
        params: Tuple[Any, ...] = (publication_id, key_id)
        if context_id:
            query += " AND context_id=?"
            params += (context_id,)
        query += " ORDER BY updated_at DESC LIMIT 100"
        with self._lock, self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._task_row(row) for row in rows]

    def save_target(self, owner_user_id: str, source_conversation_id: str,
                    alias: str, kind: str, **values: Any) -> Dict[str, Any]:
        alias = str(alias or "").strip()
        kind = str(kind or "").strip().lower()
        if not owner_user_id or not source_conversation_id or not alias:
            raise ValueError("owner_user_id, source_conversation_id and alias are required")
        if kind not in _TARGET_KINDS:
            raise ValueError("kind must be 'local' or 'remote'")
        target_conversation_id = str(values.get("target_conversation_id") or "").strip()
        target_agent = str(values.get("target_agent") or "").strip()
        agent_card_url = str(values.get("agent_card_url") or "").strip()
        if kind == "local" and (not target_conversation_id or not target_agent):
            raise ValueError("Local targets require target_conversation_id and target_agent")
        if kind == "remote" and not agent_card_url:
            raise ValueError("Remote targets require an Agent Card URL")
        now = time.time()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT target_id, owner_user_id FROM a2a_targets "
                "WHERE source_conversation_id=? AND lower(alias)=lower(?)",
                (source_conversation_id, alias),
            ).fetchone()
            if row and row["owner_user_id"] != owner_user_id:
                raise PermissionError("A2A target belongs to another owner")
            target_id = row["target_id"] if row else (
                "a2at_" + secrets.token_urlsafe(14).replace("-", "").replace("_", ""))
            payload = (owner_user_id, source_conversation_id, alias, kind,
                       target_conversation_id, target_agent, agent_card_url,
                       str(values.get("auth_secret") or "").strip(),
                       int(bool(values.get("allow_private"))), now)
            if row:
                connection.execute(
                    "UPDATE a2a_targets SET alias=?, kind=?, target_conversation_id=?, "
                    "target_agent=?, agent_card_url=?, auth_secret=?, allow_private=?, "
                    "updated_at=? WHERE target_id=?", payload[2:] + (target_id,))
            else:
                connection.execute(
                    "INSERT INTO a2a_targets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (target_id,) + payload + (now,))
        result = self.get_target(source_conversation_id, alias)
        if result is None:
            raise RuntimeError("A2A target was not persisted")
        return result

    def get_target(self, source_conversation_id: str, alias: str) -> Optional[Dict[str, Any]]:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM a2a_targets WHERE source_conversation_id=? "
                "AND lower(alias)=lower(?)", (source_conversation_id, alias),
            ).fetchone()
        return self._target_row(row) if row else None

    def list_targets(self, source_conversation_id: str) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM a2a_targets WHERE source_conversation_id=? "
                "ORDER BY lower(alias)", (source_conversation_id,),
            ).fetchall()
        return [self._target_row(row) for row in rows]

    def delete_target(self, owner_user_id: str, source_conversation_id: str,
                      target_id: str) -> bool:
        with self._lock, self._connect() as connection:
            result = connection.execute(
                "DELETE FROM a2a_targets WHERE target_id=? AND owner_user_id=? "
                "AND source_conversation_id=?",
                (target_id, owner_user_id, source_conversation_id),
            )
        return bool(result.rowcount)
