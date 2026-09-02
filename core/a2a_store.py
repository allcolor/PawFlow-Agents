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
from core._a2a_turn_acquire import TurnAcquireMixin
from core._a2a_turn_attach import TurnAttachMixin
from core._a2a_turn_batch import TurnBatchMixin
from core._a2a_turn_journal import TurnJournalMixin
from core._a2a_turn_machine import TurnMachineMixin
from core._a2a_standard_api import StandardApiStoreMixin
from core.standard_api_config import (
    STANDARD_API_FIELDS,
    default_standard_api_config,
    normalize_standard_api_update,
    standard_api_material_changed,
)
from core.sqlite_store_guard import SqliteStoreGuard, SqliteStoreUnavailableError


_CONTEXT_POLICIES = frozenset({"isolated", "shared"})
_TARGET_KINDS = frozenset({"local", "remote"})


class A2AStore(StandardApiStoreMixin, TurnMachineMixin, TurnJournalMixin,
               TurnAcquireMixin, TurnBatchMixin, TurnAttachMixin):
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
        self._guard = SqliteStoreGuard("A2A")
        try:
            self._guard.initialize(self.database_path, self._initialize)
        except SqliteStoreUnavailableError:
            pass

    @property
    def available(self) -> bool:
        """Return whether the store is safe to read or write."""
        return self._guard.available

    @property
    def database_path(self) -> Path:
        return self._database_path_override or (_paths.SYSTEM_DIR / "a2a.sqlite3")

    def _connect(self) -> sqlite3.Connection:
        self._guard.require_available()
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
            self._initialize_standard_api_tables(connection)
            self._initialize_turn_tables(connection)
            self._initialize_journal_tables(connection)
            self._initialize_acquire_tables(connection)
            self._initialize_batch_tables(connection)
            self._initialize_attach_tables(connection)

    @staticmethod
    def _publication_row(row: sqlite3.Row) -> Dict[str, Any]:
        data = dict(row)
        for field in (
                "enabled", "managed_mode", "standard_api_enabled",
                "strict_fields", "api_chat_completions_enabled",
                "api_responses_enabled", "api_anthropic_messages_enabled"):
            data[field] = bool(data.get(field))
        for field, fallback in (
                ("api_request_overrides_json", {}),
                ("api_input_modalities_json", [])):
            raw = data.get(field)
            try:
                data[field] = json.loads(raw) if isinstance(raw, str) else raw
            except json.JSONDecodeError:
                data[field] = fallback
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
        enabled: bool = True, thread_ttl_seconds: Optional[int] = None,
        managed_mode: Optional[bool] = None,
        standard_api_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create or update a publication.

        ``thread_ttl_seconds=None`` / ``managed_mode=None`` preserve the
        stored values on update (defaults only apply on insert) — an
        update that omits a parameter never silently resets it.
        ``managed_mode`` is publication-fixed: requests can never select
        the execution mode.
        """
        if not owner_user_id or not conversation_id or not agent_name:
            raise ValueError("owner_user_id, conversation_id and agent_name are required")
        context_policy = str(context_policy or "").strip().lower()
        if context_policy not in _CONTEXT_POLICIES:
            raise ValueError("context_policy must be 'isolated' or 'shared'")
        if thread_ttl_seconds is not None:
            thread_ttl_seconds = max(0, int(thread_ttl_seconds))
        now = time.time()
        with self._lock, self._immediate() as connection:
            row = connection.execute(
                "SELECT * "
                "FROM a2a_publications "
                "WHERE conversation_id = ? AND lower(agent_name) = lower(?)",
                (conversation_id, agent_name),
            ).fetchone()
            if row and row["owner_user_id"] != owner_user_id:
                raise PermissionError("A2A publication belongs to another owner")
            if row and float(row["delete_requested_at"] or 0):
                raise ValueError("A2A publication is being deleted")
            effective_managed = (bool(managed_mode)
                                 if managed_mode is not None
                                 else bool(row["managed_mode"]) if row else False)
            if effective_managed and context_policy != "isolated":
                raise ValueError(
                    "managed_mode requires context_policy='isolated'")
            current_standard = (
                {field: self._publication_row(row).get(field)
                 for field in STANDARD_API_FIELDS}
                if row else default_standard_api_config())
            candidate_standard = normalize_standard_api_update(
                current_standard,
                standard_api_config or {},
                context_policy=context_policy,
            )
            current_generation = int(row["api_generation"] or 0) if row else 0
            material_change = standard_api_material_changed(
                current_standard, candidate_standard)
            if row:
                material_change = material_change or any((
                    bool(row["enabled"]) != bool(enabled),
                    row["context_policy"] != context_policy,
                    row["agent_name"] != agent_name,
                ))
            api_generation = current_generation
            if current_generation and material_change:
                api_generation += 1
            elif not current_generation and candidate_standard[
                    "standard_api_enabled"]:
                api_generation = 1

            encoded_standard = dict(candidate_standard)
            encoded_standard["api_request_overrides_json"] = json.dumps(
                candidate_standard["api_request_overrides_json"],
                ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            encoded_standard["api_input_modalities_json"] = json.dumps(
                candidate_standard["api_input_modalities_json"],
                ensure_ascii=False, separators=(",", ":"))
            if row:
                publication_id = row["publication_id"]
                sets = ["agent_name=?", "label=?", "description=?",
                        "context_policy=?", "enabled=?", "api_generation=?"]
                params: List[Any] = [agent_name, label or agent_name,
                                     description, context_policy,
                                     int(bool(enabled)), api_generation]
                for field in STANDARD_API_FIELDS:
                    sets.append(f"{field}=?")
                    value = encoded_standard[field]
                    if field in {
                            "standard_api_enabled", "strict_fields",
                            "api_chat_completions_enabled",
                            "api_responses_enabled",
                            "api_anthropic_messages_enabled"}:
                        value = int(bool(value))
                    params.append(value)
                if thread_ttl_seconds is not None:
                    sets.append("thread_ttl_seconds=?")
                    params.append(thread_ttl_seconds)
                if managed_mode is not None:
                    sets.append("managed_mode=?")
                    params.append(int(bool(managed_mode)))
                sets.append("updated_at=?")
                params.extend([now, publication_id])
                # Every column fragment is selected from the fixed literals
                # above; only values are caller-derived and remain parameterized.
                update_sql = (
                    "UPDATE a2a_publications SET " + ", ".join(sets) +  # nosec B608
                    " WHERE publication_id=?")
                connection.execute(update_sql, params)
                if api_generation != current_generation:
                    self._expire_old_api_generations(
                        connection, publication_id, api_generation, now)
            else:
                publication_id = "a2ap_" + secrets.token_urlsafe(18).replace("-", "").replace("_", "")
                connection.execute(
                    "INSERT INTO a2a_publications (publication_id, owner_user_id, "
                    "conversation_id, agent_name, label, description, context_policy, "
                    "enabled, created_at, updated_at, thread_ttl_seconds, "
                    "managed_mode, standard_api_enabled, api_model_id, "
                    "api_generation, api_permission_mode, api_session_ttl_seconds, "
                    "api_max_sessions_per_key, api_max_concurrent_runs_per_key, "
                    "strict_fields, api_request_overrides_json, "
                    "api_input_modalities_json, api_chat_completions_enabled, "
                    "api_responses_enabled, api_anthropic_messages_enabled, "
                    "api_disconnect_policy, delete_requested_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                    "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (publication_id, owner_user_id, conversation_id, agent_name,
                     label or agent_name, description, context_policy,
                     int(bool(enabled)), now, now, thread_ttl_seconds or 0,
                     int(bool(managed_mode)),
                     int(bool(candidate_standard["standard_api_enabled"])),
                     candidate_standard["api_model_id"], api_generation,
                     candidate_standard["api_permission_mode"],
                     candidate_standard["api_session_ttl_seconds"],
                     candidate_standard["api_max_sessions_per_key"],
                     candidate_standard["api_max_concurrent_runs_per_key"],
                     int(bool(candidate_standard["strict_fields"])),
                     encoded_standard["api_request_overrides_json"],
                     encoded_standard["api_input_modalities_json"],
                     int(bool(candidate_standard[
                         "api_chat_completions_enabled"])),
                     int(bool(candidate_standard["api_responses_enabled"])),
                     int(bool(candidate_standard[
                         "api_anthropic_messages_enabled"])),
                     candidate_standard["api_disconnect_policy"], 0),
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
