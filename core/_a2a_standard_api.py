"""Standard API publication schema and lifecycle operations for A2AStore."""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from typing import Any, Callable, Dict, Mapping, Sequence

from core.standard_api_config import standard_api_runtime_summary
from core.standard_api_types import NormalizedVisibleItem, StandardApiNamespace


class ApiSessionQuotaExceeded(Exception):
    """The authenticated key has reached its retained-session limit."""


class ApiRunQuotaExceeded(Exception):
    """The authenticated key has reached its concurrent-run limit."""


class ApiLeaseLost(Exception):
    """A finalizer no longer owns the session lease it presents."""


_NAMESPACE_FIELDS = (
    "publication_id",
    "api_generation",
    "key_id",
    "dialect",
    "api_model_id",
    "canonicalization_version",
    "hash_secret_version",
)


def _namespace_dict(namespace: StandardApiNamespace | Mapping[str, Any]
                    ) -> Dict[str, Any]:
    data = namespace.as_dict() if isinstance(
        namespace, StandardApiNamespace) else dict(namespace)
    missing = [field for field in _NAMESPACE_FIELDS if field not in data]
    if missing:
        raise ValueError("Incomplete standard API namespace: " + ", ".join(missing))
    return {field: data[field] for field in _NAMESPACE_FIELDS}


def _finite_json(value: Any, label: str, *, max_bytes: int = 1048576) -> str:
    try:
        encoded = json.dumps(
            value, ensure_ascii=False, allow_nan=False,
            sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be finite JSON") from exc
    if len(encoded.encode("utf-8")) > max_bytes:
        raise ValueError(f"{label} exceeds its storage limit")
    return encoded


def _json_fingerprint(value: Any, label: str) -> tuple[str, str]:
    encoded = _finite_json(value, label)
    return encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _response_record_values(
        response_id: str,
        record: Mapping[str, Any] | None,
        item_count: int,
) -> Dict[str, str] | None:
    if record is None:
        return None
    if not isinstance(record, Mapping):
        raise ValueError("Response record must be an object")
    normalized_id = str(response_id or "").strip()
    if not normalized_id or len(normalized_id) > 256:
        raise ValueError("Stored response_id is required and must be bounded")
    previous_value = record.get("previous_response_id")
    if previous_value is not None and not isinstance(previous_value, str):
        raise ValueError("Stored previous_response_id must be a string")
    previous_response_id = str(previous_value or "").strip()
    if len(previous_response_id) > 256:
        raise ValueError("Stored previous_response_id exceeds its limit")
    visible_items = record.get("visible_items")
    if (not isinstance(visible_items, Sequence)
            or isinstance(visible_items, (str, bytes))
            or any(
                not isinstance(item, NormalizedVisibleItem)
                for item in visible_items)):
        raise ValueError(
            "Stored response visible_items must be normalized visible items")
    if (isinstance(item_count, bool) or not isinstance(item_count, int)
            or len(visible_items) != item_count):
        raise ValueError(
            "Stored response visible_items must match the finalized item_count")
    if "output" not in record or "envelope" not in record:
        raise ValueError("Stored response output and envelope are required")
    envelope = record["envelope"]
    if (not isinstance(envelope, Mapping)
            or str(envelope.get("id") or "") != normalized_id):
        raise ValueError("Stored response envelope id does not match response_id")
    return {
        "response_id": normalized_id,
        "previous_response_id": previous_response_id,
        "visible_items_json": _finite_json(
            [
                {"kind": item.kind, "data": dict(item.data)}
                for item in visible_items
            ],
            "Stored response visible items",
        ),
        "output_json": _finite_json(record["output"], "Stored response output"),
        "envelope_json": _finite_json(
            dict(envelope), "Stored response envelope"),
    }


def _client_tool_schema_map(
        definitions: Sequence[Mapping[str, Any]]) -> Dict[str, str]:
    from core.client_tools import validate_client_tool_definitions
    from core.identifier import identifier_key

    schemas = {}
    for definition in validate_client_tool_definitions(definitions):
        payload = {
            "name": identifier_key(definition["name"]),
            "parameters": definition["parameters"],
        }
        schemas[identifier_key(definition["name"])] = _json_fingerprint(
            payload, "Client tool schema")[1]
    return schemas


def _pending_tool_rows(
        calls: Sequence[Mapping[str, Any]],
        definitions: Sequence[Mapping[str, Any]],
) -> tuple[Dict[str, Any], ...]:
    from core.identifier import identifier_key

    if not calls:
        return ()
    if len(calls) > 128:
        raise ValueError("A client tool batch cannot exceed 128 calls")
    schemas = _client_tool_schema_map(definitions)
    rows = []
    seen = set()
    for position, call in enumerate(calls):
        if not isinstance(call, Mapping):
            raise ValueError("Client tool calls must be objects")
        call_id = str(call.get("id") or "").strip()
        name = str(call.get("name") or "").strip()
        if not call_id or len(call_id) > 256 or not name:
            raise ValueError("Client tool call id and name are required")
        if call_id in seen:
            raise ValueError(f"Duplicate client tool call id: {call_id}")
        seen.add(call_id)
        schema_fingerprint = schemas.get(identifier_key(name))
        if not schema_fingerprint:
            raise ValueError(
                f"Client tool call '{name}' has no matching definition")
        arguments_json = _finite_json(
            call.get("arguments", {}), "Client tool arguments")
        rows.append({
            "call_id": call_id,
            "position": position,
            "tool_name": name,
            "arguments_json": arguments_json,
            "schema_fingerprint": schema_fingerprint,
        })
    return tuple(rows)


class StandardApiStoreMixin:
    """Publication-level state shared by all standard API dialects."""

    _lock: Any
    _connect: Callable[[], Any]
    get_publication: Callable[[str], Dict[str, Any] | None]

    @staticmethod
    def _expire_old_api_generations(
            connection, publication_id: str, api_generation: int,
            now: float) -> None:
        """Schedule idle state from older generations for immediate cleanup."""

        connection.execute(
            "UPDATE api_export_tool_calls SET state='canceled' "
            "WHERE state='pending' AND batch_id IN ("
            "SELECT b.batch_id FROM api_export_tool_batches b "
            "JOIN api_export_sessions s ON s.session_id=b.session_id "
            "WHERE s.publication_id=? AND s.api_generation!=?)",
            (publication_id, api_generation),
        )
        connection.execute(
            "UPDATE api_export_tool_batches SET state='canceled', settled_at=? "
            "WHERE state='pending' AND session_id IN ("
            "SELECT session_id FROM api_export_sessions "
            "WHERE publication_id=? AND api_generation!=?)",
            (now, publication_id, api_generation),
        )
        connection.execute(
            "UPDATE api_export_sessions SET state='quarantined', "
            "expires_at=MIN(expires_at, ?) "
            "WHERE publication_id=? AND api_generation!=? "
            "AND state='waiting_tool'",
            (now, publication_id, api_generation),
        )
        connection.execute(
            "UPDATE api_export_sessions SET expires_at=MIN(expires_at, ?) "
            "WHERE publication_id=? AND api_generation!=? "
            "AND state IN ('idle', 'quarantined')",
            (now, publication_id, api_generation),
        )

    @staticmethod
    def _initialize_standard_api_tables(connection) -> None:
        """Apply the one-shot publication migration with fixed DDL literals."""

        columns = {
            row[1] for row in connection.execute(
                "PRAGMA table_info(a2a_publications)").fetchall()
        }
        migrations = (
            ("standard_api_enabled",
             "ALTER TABLE a2a_publications ADD COLUMN "
             "standard_api_enabled INTEGER NOT NULL DEFAULT 0"),
            ("api_model_id",
             "ALTER TABLE a2a_publications ADD COLUMN "
             "api_model_id TEXT NOT NULL DEFAULT ''"),
            ("api_generation",
             "ALTER TABLE a2a_publications ADD COLUMN "
             "api_generation INTEGER NOT NULL DEFAULT 0"),
            ("api_permission_mode",
             "ALTER TABLE a2a_publications ADD COLUMN "
             "api_permission_mode TEXT NOT NULL DEFAULT ''"),
            ("api_session_ttl_seconds",
             "ALTER TABLE a2a_publications ADD COLUMN "
             "api_session_ttl_seconds INTEGER NOT NULL DEFAULT 0"),
            ("api_max_sessions_per_key",
             "ALTER TABLE a2a_publications ADD COLUMN "
             "api_max_sessions_per_key INTEGER NOT NULL DEFAULT 0"),
            ("api_max_concurrent_runs_per_key",
             "ALTER TABLE a2a_publications ADD COLUMN "
             "api_max_concurrent_runs_per_key INTEGER NOT NULL DEFAULT 0"),
            ("strict_fields",
             "ALTER TABLE a2a_publications ADD COLUMN "
             "strict_fields INTEGER NOT NULL DEFAULT 0"),
            ("api_request_overrides_json",
             "ALTER TABLE a2a_publications ADD COLUMN "
             "api_request_overrides_json TEXT NOT NULL DEFAULT '{}'"),
            ("api_input_modalities_json",
             "ALTER TABLE a2a_publications ADD COLUMN "
             "api_input_modalities_json TEXT NOT NULL DEFAULT '[]'"),
            ("api_chat_completions_enabled",
             "ALTER TABLE a2a_publications ADD COLUMN "
             "api_chat_completions_enabled INTEGER NOT NULL DEFAULT 0"),
            ("api_responses_enabled",
             "ALTER TABLE a2a_publications ADD COLUMN "
             "api_responses_enabled INTEGER NOT NULL DEFAULT 0"),
            ("api_anthropic_messages_enabled",
             "ALTER TABLE a2a_publications ADD COLUMN "
             "api_anthropic_messages_enabled INTEGER NOT NULL DEFAULT 0"),
            ("api_disconnect_policy",
             "ALTER TABLE a2a_publications ADD COLUMN "
             "api_disconnect_policy TEXT NOT NULL DEFAULT ''"),
            ("delete_requested_at",
             "ALTER TABLE a2a_publications ADD COLUMN "
             "delete_requested_at REAL NOT NULL DEFAULT 0"),
        )
        for name, statement in migrations:
            if name not in columns:
                connection.execute(statement)
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS api_export_sessions (
                session_id TEXT PRIMARY KEY,
                publication_id TEXT NOT NULL
                    REFERENCES a2a_publications(publication_id) ON DELETE CASCADE,
                key_id TEXT NOT NULL
                    REFERENCES a2a_api_keys(key_id) ON DELETE CASCADE,
                api_generation INTEGER NOT NULL,
                dialect TEXT NOT NULL,
                api_model_id TEXT NOT NULL,
                canonicalization_version INTEGER NOT NULL,
                hash_secret_version INTEGER NOT NULL,
                internal_conversation_id TEXT NOT NULL UNIQUE,
                visible_head_hash TEXT NOT NULL DEFAULT '',
                item_count INTEGER NOT NULL DEFAULT 0,
                head_checkpoint_id TEXT NOT NULL DEFAULT '',
                state TEXT NOT NULL,
                lease_id TEXT NOT NULL DEFAULT '',
                lease_deadline REAL NOT NULL DEFAULT 0,
                heartbeat_at REAL NOT NULL DEFAULT 0,
                ttl_seconds INTEGER NOT NULL,
                created_at REAL NOT NULL,
                last_seen_at REAL NOT NULL,
                expires_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_api_sessions_namespace
                ON api_export_sessions(
                    publication_id, key_id, api_generation, dialect,
                    api_model_id, canonicalization_version,
                    hash_secret_version, state, expires_at);
            CREATE INDEX IF NOT EXISTS idx_api_sessions_lease
                ON api_export_sessions(state, lease_deadline);

            CREATE TABLE IF NOT EXISTS api_export_prefixes (
                prefix_id TEXT PRIMARY KEY,
                publication_id TEXT NOT NULL,
                key_id TEXT NOT NULL,
                api_generation INTEGER NOT NULL,
                dialect TEXT NOT NULL,
                api_model_id TEXT NOT NULL,
                canonicalization_version INTEGER NOT NULL,
                hash_secret_version INTEGER NOT NULL,
                prefix_hash TEXT NOT NULL,
                item_count INTEGER NOT NULL,
                session_id TEXT NOT NULL
                    REFERENCES api_export_sessions(session_id) ON DELETE CASCADE,
                checkpoint_id TEXT NOT NULL DEFAULT '',
                boundary_kind TEXT NOT NULL,
                created_at REAL NOT NULL,
                last_seen_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_api_prefix_lookup
                ON api_export_prefixes(
                    publication_id, key_id, api_generation, dialect,
                    api_model_id, canonicalization_version,
                    hash_secret_version, prefix_hash, item_count);
            CREATE INDEX IF NOT EXISTS idx_api_prefix_session
                ON api_export_prefixes(session_id, item_count);

            CREATE TABLE IF NOT EXISTS api_export_runs (
                run_id TEXT PRIMARY KEY,
                request_id TEXT NOT NULL,
                session_id TEXT NOT NULL
                    REFERENCES api_export_sessions(session_id) ON DELETE CASCADE,
                lease_id TEXT NOT NULL,
                parent_head_hash TEXT NOT NULL,
                parent_item_count INTEGER NOT NULL,
                body_fingerprint TEXT NOT NULL,
                status TEXT NOT NULL,
                response_id TEXT NOT NULL DEFAULT '',
                terminal_error_code TEXT NOT NULL DEFAULT '',
                started_at REAL NOT NULL,
                heartbeat_at REAL NOT NULL,
                replay_until REAL NOT NULL,
                finished_at REAL NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_api_runs_active
                ON api_export_runs(session_id, status, replay_until);
            CREATE INDEX IF NOT EXISTS idx_api_runs_lease
                ON api_export_runs(lease_id, status);

            CREATE TABLE IF NOT EXISTS api_export_responses (
                response_id TEXT PRIMARY KEY,
                publication_id TEXT NOT NULL,
                key_id TEXT NOT NULL,
                api_generation INTEGER NOT NULL,
                dialect TEXT NOT NULL,
                api_model_id TEXT NOT NULL,
                canonicalization_version INTEGER NOT NULL,
                hash_secret_version INTEGER NOT NULL,
                session_id TEXT NOT NULL
                    REFERENCES api_export_sessions(session_id) ON DELETE CASCADE,
                run_id TEXT NOT NULL UNIQUE
                    REFERENCES api_export_runs(run_id) ON DELETE CASCADE,
                previous_response_id TEXT NOT NULL DEFAULT '',
                checkpoint_id TEXT NOT NULL DEFAULT '',
                visible_head_hash TEXT NOT NULL,
                item_count INTEGER NOT NULL,
                visible_items_json TEXT NOT NULL,
                output_json TEXT NOT NULL,
                envelope_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                deleted_at REAL NOT NULL DEFAULT 0,
                expires_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_api_responses_namespace
                ON api_export_responses(
                    publication_id, key_id, api_generation, dialect,
                    api_model_id, canonicalization_version,
                    hash_secret_version, response_id, deleted_at, expires_at);
            CREATE INDEX IF NOT EXISTS idx_api_responses_expiry
                ON api_export_responses(deleted_at, expires_at);

            CREATE TABLE IF NOT EXISTS api_export_run_events (
                run_id TEXT NOT NULL
                    REFERENCES api_export_runs(run_id) ON DELETE CASCADE,
                seq INTEGER NOT NULL,
                event_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY (run_id, seq)
            );

            CREATE TABLE IF NOT EXISTS api_export_active_admissions (
                publication_id TEXT NOT NULL,
                key_id TEXT NOT NULL,
                api_generation INTEGER NOT NULL,
                dialect TEXT NOT NULL,
                api_model_id TEXT NOT NULL,
                canonicalization_version INTEGER NOT NULL,
                hash_secret_version INTEGER NOT NULL,
                parent_head_hash TEXT NOT NULL,
                parent_item_count INTEGER NOT NULL,
                body_fingerprint TEXT NOT NULL,
                session_id TEXT NOT NULL
                    REFERENCES api_export_sessions(session_id) ON DELETE CASCADE,
                run_id TEXT NOT NULL UNIQUE,
                created_at REAL NOT NULL,
                replay_until REAL NOT NULL,
                PRIMARY KEY (
                    publication_id, key_id, api_generation, dialect,
                    api_model_id, canonicalization_version,
                    hash_secret_version, parent_head_hash,
                    parent_item_count, body_fingerprint)
            );
            CREATE INDEX IF NOT EXISTS idx_api_active_admission_expiry
                ON api_export_active_admissions(replay_until);

            CREATE TABLE IF NOT EXISTS api_export_tool_batches (
                batch_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL UNIQUE
                    REFERENCES api_export_runs(run_id) ON DELETE CASCADE,
                session_id TEXT NOT NULL
                    REFERENCES api_export_sessions(session_id) ON DELETE CASCADE,
                visible_head_hash TEXT NOT NULL,
                item_count INTEGER NOT NULL,
                state TEXT NOT NULL,
                settled_by_run_id TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                settled_at REAL NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_api_tool_batches_session_state
                ON api_export_tool_batches(
                    session_id, visible_head_hash, item_count, state);

            CREATE TABLE IF NOT EXISTS api_export_tool_calls (
                batch_id TEXT NOT NULL
                    REFERENCES api_export_tool_batches(batch_id) ON DELETE CASCADE,
                call_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                tool_name TEXT NOT NULL,
                arguments_json TEXT NOT NULL,
                schema_fingerprint TEXT NOT NULL,
                state TEXT NOT NULL,
                result_fingerprint TEXT NOT NULL DEFAULT '',
                settled_at REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (batch_id, call_id),
                UNIQUE (batch_id, position)
            );
            CREATE INDEX IF NOT EXISTS idx_api_tool_calls_state
                ON api_export_tool_calls(batch_id, state);
            """
        )

    def reset_api_sessions(
            self, publication_id: str, *,
            now: float | None = None) -> Dict[str, Any]:
        """Move future API admissions to a fresh generation."""

        timestamp = time.time() if now is None else float(now)
        with self._lock, self._immediate() as connection:
            row = connection.execute(
                "SELECT api_generation FROM a2a_publications "
                "WHERE publication_id=?",
                (publication_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Unknown A2A publication")
            generation = int(row["api_generation"] or 0)
            if generation < 1:
                raise ValueError(
                    "The publication has no standard API sessions to reset")
            connection.execute(
                "UPDATE a2a_publications SET api_generation=?, updated_at=? "
                "WHERE publication_id=?",
                (generation + 1, timestamp, publication_id),
            )
            self._expire_old_api_generations(
                connection, publication_id, generation + 1, timestamp)
        publication = self.get_publication(publication_id)
        if publication is None:
            raise RuntimeError("A2A publication disappeared during reset")
        return publication

    def request_publication_delete(
            self, publication_id: str) -> Dict[str, Any]:
        """Durably disable admissions and mark a publication for cleanup."""

        now = time.time()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT delete_requested_at, api_generation "
                "FROM a2a_publications WHERE publication_id=?",
                (publication_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Unknown A2A publication")
            if not float(row["delete_requested_at"] or 0):
                generation = int(row["api_generation"] or 0)
                next_generation = generation + 1 if generation else 0
                connection.execute(
                    "UPDATE a2a_publications SET enabled=0, "
                    "delete_requested_at=?, api_generation=?, updated_at=? "
                    "WHERE publication_id=?",
                    (now, next_generation, now, publication_id),
                )
        publication = self.get_publication(publication_id)
        if publication is None:
            raise RuntimeError("A2A publication disappeared during deletion request")
        return publication

    def get_standard_api_runtime_summary(
            self, publication_id: str) -> Dict[str, Any]:
        """Return the content-free owner status for one publication."""

        publication = self.get_publication(publication_id)
        if publication is None:
            raise ValueError("Unknown A2A publication")
        with self._lock, self._connect() as connection:
            live_keys = connection.execute(
                "SELECT COUNT(*) FROM a2a_api_keys "
                "WHERE publication_id=? AND revoked_at=0",
                (publication_id,),
            ).fetchone()[0]
            session_count = 0
            active_run_count = 0
            tables = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
            if "api_export_sessions" in tables:
                session_count = connection.execute(
                    "SELECT COUNT(*) FROM api_export_sessions "
                    "WHERE publication_id=? AND state != 'expired'",
                    (publication_id,),
                ).fetchone()[0]
            if "api_export_runs" in tables:
                active_run_count = connection.execute(
                    "SELECT COUNT(*) FROM api_export_runs r "
                    "JOIN api_export_sessions s ON s.session_id=r.session_id "
                    "WHERE s.publication_id=? "
                    "AND r.status IN ('admitted', 'running')",
                    (publication_id,),
                ).fetchone()[0]
        return standard_api_runtime_summary(
            publication,
            live_key_count=int(live_keys),
            session_count=int(session_count),
            active_run_count=int(active_run_count),
        )

    @staticmethod
    def _api_session_row(row) -> Dict[str, Any]:
        return dict(row)

    @staticmethod
    def _api_run_row(row) -> Dict[str, Any]:
        return dict(row)

    @staticmethod
    def _api_response_row(row) -> Dict[str, Any]:
        response = dict(row)
        visible_items = json.loads(response.pop("visible_items_json"))
        response["visible_items"] = tuple(
            NormalizedVisibleItem(kind=item["kind"], data=item["data"])
            for item in visible_items
        )
        response["output"] = json.loads(response.pop("output_json"))
        response["envelope"] = json.loads(response.pop("envelope_json"))
        return response

    @staticmethod
    def _api_tool_batch_row(connection, row) -> Dict[str, Any]:
        batch = dict(row)
        batch["calls"] = [
            dict(call) for call in connection.execute(
                "SELECT * FROM api_export_tool_calls WHERE batch_id=? "
                "ORDER BY position",
                (row["batch_id"],),
            ).fetchall()
        ]
        return batch

    def get_api_tool_batch_for_run(
            self, run_id: str) -> Dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM api_export_tool_batches WHERE run_id=?",
                (run_id,),
            ).fetchone()
            return self._api_tool_batch_row(connection, row) if row else None

    def get_pending_api_tool_batch(
            self, session_id: str) -> Dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM api_export_tool_batches "
                "WHERE session_id=? AND state='pending' "
                "ORDER BY created_at DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            return self._api_tool_batch_row(connection, row) if row else None

    @staticmethod
    def _validate_api_namespace(connection, namespace: Dict[str, Any],
                                now: float):
        row = connection.execute(
            "SELECT p.*, k.revoked_at AS api_key_revoked_at "
            "FROM a2a_publications p "
            "JOIN a2a_api_keys k ON k.publication_id=p.publication_id "
            "WHERE p.publication_id=? AND k.key_id=?",
            (namespace["publication_id"], namespace["key_id"]),
        ).fetchone()
        if row is None or float(row["api_key_revoked_at"] or 0):
            raise ValueError("Unknown or revoked publication API key")
        if (not bool(row["enabled"])
                or not bool(row["standard_api_enabled"])
                or float(row["delete_requested_at"] or 0)
                or row["context_policy"] != "isolated"):
            raise ValueError("Standard API publication is unavailable")
        if int(row["api_generation"] or 0) != namespace["api_generation"]:
            raise ValueError("Standard API generation is stale")
        if row["api_model_id"] != namespace["api_model_id"]:
            raise ValueError("Standard API model does not match the publication")
        dialect_field = {
            "chat_completions": "api_chat_completions_enabled",
            "responses": "api_responses_enabled",
            "anthropic_messages": "api_anthropic_messages_enabled",
        }.get(namespace["dialect"])
        if not dialect_field or not bool(row[dialect_field]):
            raise ValueError("Standard API dialect is unavailable")
        from core.standard_api_config import DIALECT_AVAILABILITY
        if not DIALECT_AVAILABILITY.get(namespace["dialect"], False):
            raise ValueError("Standard API dialect is unavailable in this build")
        return row

    def create_api_session(
            self,
            namespace: StandardApiNamespace | Mapping[str, Any],
            internal_conversation_id: str,
            *,
            visible_head_hash: str = "",
            item_count: int = 0,
            now: float | None = None,
    ) -> Dict[str, Any]:
        """Create one independent isolated session under the key quota."""

        values = _namespace_dict(namespace)
        if not internal_conversation_id:
            raise ValueError("internal_conversation_id is required")
        if isinstance(item_count, bool) or not isinstance(item_count, int) \
                or item_count < 0:
            raise ValueError("item_count must be a non-negative integer")
        timestamp = time.time() if now is None else float(now)
        with self._lock, self._immediate() as connection:
            publication = self._validate_api_namespace(
                connection, values, timestamp)
            quota = int(publication["api_max_sessions_per_key"] or 0)
            retained = connection.execute(
                "SELECT COUNT(*) FROM api_export_sessions "
                "WHERE publication_id=? AND key_id=? AND state!='expired' "
                "AND (state='running' OR expires_at>?)",
                (values["publication_id"], values["key_id"], timestamp),
            ).fetchone()[0]
            if quota < 1 or int(retained) >= quota:
                raise ApiSessionQuotaExceeded(
                    "The API session quota for this key has been reached")
            ttl_seconds = int(publication["api_session_ttl_seconds"] or 0)
            session_id = (
                "apis_" + secrets.token_urlsafe(18).replace("-", "").replace("_", ""))
            connection.execute(
                "INSERT INTO api_export_sessions ("
                "session_id, publication_id, key_id, api_generation, dialect, "
                "api_model_id, canonicalization_version, hash_secret_version, "
                "internal_conversation_id, visible_head_hash, item_count, "
                "head_checkpoint_id, state, lease_id, lease_deadline, "
                "heartbeat_at, ttl_seconds, created_at, last_seen_at, expires_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', 'idle', '', "
                "0, 0, ?, ?, ?, ?)",
                (
                    session_id,
                    values["publication_id"],
                    values["key_id"],
                    values["api_generation"],
                    values["dialect"],
                    values["api_model_id"],
                    values["canonicalization_version"],
                    values["hash_secret_version"],
                    internal_conversation_id,
                    visible_head_hash,
                    item_count,
                    ttl_seconds,
                    timestamp,
                    timestamp,
                    timestamp + ttl_seconds,
                ),
            )
            row = connection.execute(
                "SELECT * FROM api_export_sessions WHERE session_id=?",
                (session_id,),
            ).fetchone()
        return self._api_session_row(row)

    def get_api_session(self, session_id: str) -> Dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM api_export_sessions WHERE session_id=?",
                (session_id,),
            ).fetchone()
        return self._api_session_row(row) if row else None

    def discard_empty_api_session(self, session_id: str) -> bool:
        """Remove an unstarted session after seeding failed or lost a race."""

        with self._lock, self._immediate() as connection:
            result = connection.execute(
                "DELETE FROM api_export_sessions WHERE session_id=? "
                "AND state='idle' "
                "AND NOT EXISTS (SELECT 1 FROM api_export_runs "
                "WHERE session_id=?)",
                (session_id, session_id),
            )
            return bool(result.rowcount)

    def quarantine_unstarted_api_session(
            self, session_id: str, *,
            now: float | None = None) -> bool:
        """Make a failed reconstruction eligible for retrying child cleanup."""

        timestamp = time.time() if now is None else float(now)
        with self._lock, self._immediate() as connection:
            result = connection.execute(
                "UPDATE api_export_sessions SET state='quarantined', "
                "expires_at=MIN(expires_at, ?) WHERE session_id=? "
                "AND state='idle' "
                "AND NOT EXISTS (SELECT 1 FROM api_export_runs "
                "WHERE session_id=?)",
                (timestamp, session_id, session_id),
            )
            return bool(result.rowcount)

    def get_api_run(self, run_id: str) -> Dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM api_export_runs WHERE run_id=?",
                (run_id,),
            ).fetchone()
        return self._api_run_row(row) if row else None

    def get_api_response(
            self,
            namespace: StandardApiNamespace | Mapping[str, Any],
            response_id: str,
            *,
            now: float | None = None,
    ) -> Dict[str, Any] | None:
        """Return one live stored response without crossing its namespace."""

        values = _namespace_dict(namespace)
        normalized_id = str(response_id or "").strip()
        if not normalized_id:
            raise ValueError("response_id is required")
        timestamp = time.time() if now is None else float(now)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM api_export_responses WHERE response_id=? "
                "AND publication_id=? AND key_id=? AND api_generation=? "
                "AND dialect=? AND api_model_id=? "
                "AND canonicalization_version=? AND hash_secret_version=? "
                "AND deleted_at=0 AND expires_at>?",
                (
                    normalized_id,
                    values["publication_id"],
                    values["key_id"],
                    values["api_generation"],
                    values["dialect"],
                    values["api_model_id"],
                    values["canonicalization_version"],
                    values["hash_secret_version"],
                    timestamp,
                ),
            ).fetchone()
        return self._api_response_row(row) if row else None

    def delete_api_response(
            self,
            namespace: StandardApiNamespace | Mapping[str, Any],
            response_id: str,
            *,
            now: float | None = None,
    ) -> Dict[str, Any] | None:
        """Tombstone one live response in its exact namespace."""

        values = _namespace_dict(namespace)
        normalized_id = str(response_id or "").strip()
        if not normalized_id:
            raise ValueError("response_id is required")
        timestamp = time.time() if now is None else float(now)
        with self._lock, self._immediate() as connection:
            result = connection.execute(
                "UPDATE api_export_responses SET deleted_at=? "
                "WHERE response_id=? AND publication_id=? AND key_id=? "
                "AND api_generation=? AND dialect=? AND api_model_id=? "
                "AND canonicalization_version=? AND hash_secret_version=? "
                "AND deleted_at=0 AND expires_at>?",
                (
                    timestamp,
                    normalized_id,
                    values["publication_id"],
                    values["key_id"],
                    values["api_generation"],
                    values["dialect"],
                    values["api_model_id"],
                    values["canonicalization_version"],
                    values["hash_secret_version"],
                    timestamp,
                ),
            )
            if result.rowcount != 1:
                return None
        return {
            "response_id": normalized_id,
            "deleted_at": timestamp,
        }

    def lookup_api_prefix(
            self,
            namespace: StandardApiNamespace | Mapping[str, Any],
            candidates: Sequence[Mapping[str, Any]],
            *,
            now: float | None = None,
    ) -> Dict[str, Any]:
        """Find the longest eligible prefix without hiding ambiguity."""

        values = _namespace_dict(namespace)
        if not candidates:
            return {"status": "miss", "candidate_count": 0}
        if len(candidates) > 4096:
            raise ValueError("Too many API prefix candidates")
        ordered = []
        for candidate in candidates:
            prefix_hash = str(candidate.get("prefix_hash") or "")
            item_count = candidate.get("item_count")
            if (not prefix_hash or isinstance(item_count, bool)
                    or not isinstance(item_count, int) or item_count < 1):
                raise ValueError("Invalid API prefix candidate")
            ordered.append((prefix_hash, item_count))
        timestamp = time.time() if now is None else float(now)
        placeholders = ", ".join("(?, ?)" for _ in ordered)
        candidate_params = [
            value for pair in ordered for value in pair
        ]
        query = (
            "SELECT p.*, s.state AS session_state, "
            "s.expires_at AS session_expires_at "
            "FROM api_export_prefixes p "
            "JOIN api_export_sessions s ON s.session_id=p.session_id "
            "WHERE p.publication_id=? AND p.key_id=? "
            "AND p.api_generation=? AND p.dialect=? AND p.api_model_id=? "
            "AND p.canonicalization_version=? AND p.hash_secret_version=? "
            f"AND (p.prefix_hash, p.item_count) IN ({placeholders})"  # nosec B608
        )
        params = [
            values["publication_id"], values["key_id"],
            values["api_generation"], values["dialect"],
            values["api_model_id"], values["canonicalization_version"],
            values["hash_secret_version"], *candidate_params,
        ]
        with self._lock, self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
            by_pair: Dict[tuple[str, int], list[Any]] = {}
            for row in rows:
                if row["session_state"] not in {
                        "idle", "running", "waiting_tool"}:
                    continue
                if (row["session_state"] == "idle"
                        and float(row["session_expires_at"]) <= timestamp):
                    continue
                by_pair.setdefault(
                    (row["prefix_hash"], int(row["item_count"])), []).append(row)
            for pair in reversed(ordered):
                matched = by_pair.get(pair, [])
                if not matched:
                    continue
                by_session = {}
                for row in matched:
                    by_session.setdefault(row["session_id"], row)
                if len(by_session) > 1:
                    return {
                        "status": "ambiguous",
                        "candidate_count": len(by_session),
                        "item_count": pair[1],
                    }
                prefix = next(iter(by_session.values()))
                connection.execute(
                    "UPDATE api_export_prefixes SET last_seen_at=? "
                    "WHERE prefix_id=?",
                    (timestamp, prefix["prefix_id"]),
                )
                connection.execute(
                    "UPDATE api_export_sessions SET last_seen_at=? "
                    "WHERE session_id=?",
                    (timestamp, prefix["session_id"]),
                )
                session = connection.execute(
                    "SELECT * FROM api_export_sessions WHERE session_id=?",
                    (prefix["session_id"],),
                ).fetchone()
                return {
                    "status": "unique",
                    "candidate_count": 1,
                    "prefix": dict(prefix),
                    "session": self._api_session_row(session),
                    "item_count": pair[1],
                }
        return {"status": "miss", "candidate_count": 0}

    def find_active_api_run(
            self,
            namespace: StandardApiNamespace | Mapping[str, Any],
            *,
            parent_head_hash: str,
            parent_item_count: int,
            body_fingerprint: str,
            now: float | None = None,
    ) -> Dict[str, Any] | None:
        """Find an exact active admission even when no prefix is published."""

        values = _namespace_dict(namespace)
        timestamp = time.time() if now is None else float(now)
        with self._lock, self._immediate() as connection:
            connection.execute(
                "DELETE FROM api_export_active_admissions "
                "WHERE replay_until<?",
                (timestamp,),
            )
            row = connection.execute(
                "SELECT a.session_id, a.run_id "
                "FROM api_export_active_admissions a "
                "JOIN api_export_runs r ON r.run_id=a.run_id "
                "JOIN api_export_sessions s ON s.session_id=a.session_id "
                "WHERE a.publication_id=? AND a.key_id=? "
                "AND a.api_generation=? AND a.dialect=? "
                "AND a.api_model_id=? AND a.canonicalization_version=? "
                "AND a.hash_secret_version=? AND a.parent_head_hash=? "
                "AND a.parent_item_count=? AND a.body_fingerprint=? "
                "AND a.replay_until>=? AND r.status='running' "
                "AND s.state='running' AND s.lease_deadline>?",
                (
                    values["publication_id"], values["key_id"],
                    values["api_generation"], values["dialect"],
                    values["api_model_id"],
                    values["canonicalization_version"],
                    values["hash_secret_version"], parent_head_hash,
                    parent_item_count, body_fingerprint,
                    timestamp, timestamp,
                ),
            ).fetchone()
            if row is None:
                return None
            session = connection.execute(
                "SELECT * FROM api_export_sessions WHERE session_id=?",
                (row["session_id"],),
            ).fetchone()
            run = connection.execute(
                "SELECT * FROM api_export_runs WHERE run_id=?",
                (row["run_id"],),
            ).fetchone()
            return {
                "status": "attached",
                "session": self._api_session_row(session),
                "run": self._api_run_row(run),
                "lease_id": run["lease_id"],
            }

    def acquire_api_session(
            self,
            session_id: str,
            *,
            expected_head_hash: str,
            expected_item_count: int,
            run_id: str,
            request_id: str,
            body_fingerprint: str,
            lease_seconds: float = 120.0,
            replay_window_seconds: float = 15.0,
            now: float | None = None,
    ) -> Dict[str, Any]:
        """Acquire an idle head or attach an exact retry to its active run."""

        if not all((session_id, run_id, request_id, body_fingerprint)):
            raise ValueError("Session, run, request, and body identities are required")
        timestamp = time.time() if now is None else float(now)
        if lease_seconds <= 0 or replay_window_seconds < 0:
            raise ValueError("Lease and replay durations are invalid")
        with self._lock, self._immediate() as connection:
            session = connection.execute(
                "SELECT * FROM api_export_sessions WHERE session_id=?",
                (session_id,),
            ).fetchone()
            if session is None:
                return {"status": "not_found"}
            if session["state"] == "running":
                if float(session["lease_deadline"] or 0) <= timestamp:
                    connection.execute(
                        "UPDATE api_export_runs SET status='abandoned', "
                        "terminal_error_code='lease_expired', finished_at=? "
                        "WHERE session_id=? AND status='running'",
                        (timestamp, session_id),
                    )
                    connection.execute(
                        "UPDATE api_export_sessions SET state='quarantined', "
                        "lease_id='', lease_deadline=0, heartbeat_at=0 "
                        "WHERE session_id=?",
                        (session_id,),
                    )
                    connection.execute(
                        "DELETE FROM api_export_active_admissions "
                        "WHERE session_id=?",
                        (session_id,),
                    )
                    return {"status": "unavailable"}
                active = connection.execute(
                    "SELECT * FROM api_export_runs WHERE session_id=? "
                    "AND status='running' AND parent_head_hash=? "
                    "AND parent_item_count=? AND body_fingerprint=? "
                    "AND replay_until>=? ORDER BY started_at LIMIT 1",
                    (
                        session_id, expected_head_hash, expected_item_count,
                        body_fingerprint, timestamp,
                    ),
                ).fetchone()
                if active:
                    return {
                        "status": "attached",
                        "session": self._api_session_row(session),
                        "run": self._api_run_row(active),
                        "lease_id": active["lease_id"],
                    }
                return {
                    "status": "busy",
                    "session": self._api_session_row(session),
                }
            if session["state"] not in {"idle", "waiting_tool"}:
                return {"status": "unavailable"}
            if (session["state"] == "idle"
                    and float(session["expires_at"]) <= timestamp):
                connection.execute(
                    "UPDATE api_export_sessions SET state='expired' "
                    "WHERE session_id=? AND state='idle'",
                    (session_id,),
                )
                return {"status": "expired"}
            if (session["visible_head_hash"] != expected_head_hash
                    or int(session["item_count"]) != expected_item_count):
                return {
                    "status": "stale",
                    "session": self._api_session_row(session),
                }
            if session["state"] == "waiting_tool":
                pending = connection.execute(
                    "SELECT 1 FROM api_export_tool_batches "
                    "WHERE session_id=? AND visible_head_hash=? "
                    "AND item_count=? AND state='pending'",
                    (session_id, expected_head_hash, expected_item_count),
                ).fetchone()
                if pending is None:
                    return {"status": "unavailable"}
            namespace = {
                field: session[field] for field in _NAMESPACE_FIELDS
            }
            publication = self._validate_api_namespace(
                connection, namespace, timestamp)
            connection.execute(
                "DELETE FROM api_export_active_admissions "
                "WHERE replay_until<?",
                (timestamp,),
            )
            active = connection.execute(
                "SELECT a.session_id, a.run_id "
                "FROM api_export_active_admissions a "
                "JOIN api_export_runs r ON r.run_id=a.run_id "
                "JOIN api_export_sessions s ON s.session_id=a.session_id "
                "WHERE a.publication_id=? AND a.key_id=? "
                "AND a.api_generation=? AND a.dialect=? "
                "AND a.api_model_id=? AND a.canonicalization_version=? "
                "AND a.hash_secret_version=? AND a.parent_head_hash=? "
                "AND a.parent_item_count=? AND a.body_fingerprint=? "
                "AND a.replay_until>=? AND r.status='running' "
                "AND s.state='running' AND s.lease_deadline>?",
                (
                    namespace["publication_id"], namespace["key_id"],
                    namespace["api_generation"], namespace["dialect"],
                    namespace["api_model_id"],
                    namespace["canonicalization_version"],
                    namespace["hash_secret_version"], expected_head_hash,
                    expected_item_count, body_fingerprint,
                    timestamp, timestamp,
                ),
            ).fetchone()
            if active:
                active_session = connection.execute(
                    "SELECT * FROM api_export_sessions WHERE session_id=?",
                    (active["session_id"],),
                ).fetchone()
                active_run = connection.execute(
                    "SELECT * FROM api_export_runs WHERE run_id=?",
                    (active["run_id"],),
                ).fetchone()
                return {
                    "status": "attached",
                    "session": self._api_session_row(active_session),
                    "run": self._api_run_row(active_run),
                    "lease_id": active_run["lease_id"],
                }
            existing = connection.execute(
                "SELECT * FROM api_export_runs WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if existing is not None:
                if (existing["session_id"] == session_id
                        and existing["body_fingerprint"] == body_fingerprint
                        and existing["status"] == "running"):
                    return {
                        "status": "attached",
                        "session": self._api_session_row(session),
                        "run": self._api_run_row(existing),
                        "lease_id": existing["lease_id"],
                    }
                raise ValueError("run_id is already used by another API request")
            run_quota = int(
                publication["api_max_concurrent_runs_per_key"] or 0)
            active_count = connection.execute(
                "SELECT COUNT(*) FROM api_export_sessions "
                "WHERE publication_id=? AND key_id=? AND state='running' "
                "AND lease_deadline>?",
                (
                    session["publication_id"], session["key_id"], timestamp,
                ),
            ).fetchone()[0]
            if run_quota < 1 or int(active_count) >= run_quota:
                raise ApiRunQuotaExceeded(
                    "The concurrent API run quota for this key has been reached")
            reservation_values = (
                namespace["publication_id"], namespace["key_id"],
                namespace["api_generation"], namespace["dialect"],
                namespace["api_model_id"],
                namespace["canonicalization_version"],
                namespace["hash_secret_version"], expected_head_hash,
                expected_item_count, body_fingerprint, session_id, run_id,
                timestamp, timestamp + replay_window_seconds,
            )
            reserved = connection.execute(
                "INSERT OR IGNORE INTO api_export_active_admissions "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                reservation_values,
            )
            if reserved.rowcount != 1:
                active = connection.execute(
                    "SELECT a.session_id, a.run_id "
                    "FROM api_export_active_admissions a "
                    "JOIN api_export_runs r ON r.run_id=a.run_id "
                    "WHERE a.publication_id=? AND a.key_id=? "
                    "AND a.api_generation=? AND a.dialect=? "
                    "AND a.api_model_id=? AND a.canonicalization_version=? "
                    "AND a.hash_secret_version=? AND a.parent_head_hash=? "
                    "AND a.parent_item_count=? AND a.body_fingerprint=? "
                    "AND r.status='running' AND a.replay_until>=?",
                    (
                        *reservation_values[:10],
                        timestamp,
                    ),
                ).fetchone()
                if active:
                    active_session = connection.execute(
                        "SELECT * FROM api_export_sessions WHERE session_id=?",
                        (active["session_id"],),
                    ).fetchone()
                    active_run = connection.execute(
                        "SELECT * FROM api_export_runs WHERE run_id=?",
                        (active["run_id"],),
                    ).fetchone()
                    return {
                        "status": "attached",
                        "session": self._api_session_row(active_session),
                        "run": self._api_run_row(active_run),
                        "lease_id": active_run["lease_id"],
                    }
                raise RuntimeError("Conflicting active API admission reservation")
            lease_id = (
                "apil_" + secrets.token_urlsafe(18).replace("-", "").replace("_", ""))
            updated = connection.execute(
                "UPDATE api_export_sessions SET state='running', lease_id=?, "
                "lease_deadline=?, heartbeat_at=?, last_seen_at=? "
                "WHERE session_id=? AND state IN ('idle', 'waiting_tool') "
                "AND visible_head_hash=? AND item_count=?",
                (
                    lease_id, timestamp + lease_seconds, timestamp, timestamp,
                    session_id, expected_head_hash, expected_item_count,
                ),
            )
            if updated.rowcount != 1:
                connection.execute(
                    "DELETE FROM api_export_active_admissions WHERE run_id=?",
                    (run_id,),
                )
                return {"status": "conflict"}
            connection.execute(
                "INSERT INTO api_export_runs ("
                "run_id, request_id, session_id, lease_id, parent_head_hash, "
                "parent_item_count, body_fingerprint, status, started_at, "
                "heartbeat_at, replay_until) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?, ?, ?)",
                (
                    run_id, request_id, session_id, lease_id,
                    expected_head_hash, expected_item_count, body_fingerprint,
                    timestamp, timestamp, timestamp + replay_window_seconds,
                ),
            )
            refreshed = connection.execute(
                "SELECT * FROM api_export_sessions WHERE session_id=?",
                (session_id,),
            ).fetchone()
            run = connection.execute(
                "SELECT * FROM api_export_runs WHERE run_id=?",
                (run_id,),
            ).fetchone()
            return {
                "status": "acquired",
                "session": self._api_session_row(refreshed),
                "run": self._api_run_row(run),
                "lease_id": lease_id,
            }

    def finalize_api_run(
            self,
            run_id: str,
            lease_id: str,
            *,
            visible_head_hash: str,
            item_count: int,
            prefixes: Sequence[Mapping[str, Any]],
            checkpoint_id: str = "",
            response_id: str = "",
            response_record: Mapping[str, Any] | None = None,
            pending_client_tool_calls: Sequence[Mapping[str, Any]] = (),
            client_tool_definitions: Sequence[Mapping[str, Any]] = (),
            now: float | None = None,
    ) -> Dict[str, Any]:
        """Publish a completed visible head and release its lease atomically."""

        if not run_id or not lease_id or not visible_head_hash:
            raise ValueError("Run, lease, and visible head identities are required")
        pending_rows = _pending_tool_rows(
            pending_client_tool_calls, client_tool_definitions)
        response_values = _response_record_values(
            response_id, response_record, item_count)
        timestamp = time.time() if now is None else float(now)
        with self._lock, self._immediate() as connection:
            run = connection.execute(
                "SELECT * FROM api_export_runs WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if run is None:
                raise ValueError("Unknown standard API run")
            session = connection.execute(
                "SELECT * FROM api_export_sessions WHERE session_id=?",
                (run["session_id"],),
            ).fetchone()
            if response_values is not None and (
                    session is None or session["dialect"] != "responses"):
                raise ValueError(
                    "Stored response records require the Responses dialect")
            if run["status"] == "completed":
                if (session is None
                        or session["visible_head_hash"] != visible_head_hash
                        or int(session["item_count"]) != int(item_count)):
                    raise ApiLeaseLost(
                        "Completed API finalization does not match its head")
                tool_batch = connection.execute(
                    "SELECT * FROM api_export_tool_batches WHERE run_id=?",
                    (run_id,),
                ).fetchone()
                if pending_rows:
                    if tool_batch is None:
                        raise ApiLeaseLost(
                            "Completed API finalization has no matching tool batch")
                    existing = connection.execute(
                        "SELECT call_id, position, tool_name, arguments_json, "
                        "schema_fingerprint FROM api_export_tool_calls "
                        "WHERE batch_id=? ORDER BY position",
                        (tool_batch["batch_id"],),
                    ).fetchall()
                    expected = [
                        (
                            row["call_id"], row["position"], row["tool_name"],
                            row["arguments_json"], row["schema_fingerprint"],
                        )
                        for row in pending_rows
                    ]
                    actual = [
                        (
                            row["call_id"], int(row["position"]),
                            row["tool_name"], row["arguments_json"],
                            row["schema_fingerprint"],
                        )
                        for row in existing
                    ]
                    if actual != expected:
                        raise ApiLeaseLost(
                            "Completed API finalization tool batch differs")
                stored_response = connection.execute(
                    "SELECT * FROM api_export_responses WHERE run_id=?",
                    (run_id,),
                ).fetchone()
                if response_id and run["response_id"] != response_id:
                    raise ApiLeaseLost(
                        "Completed API finalization response id differs")
                if response_values is not None and (
                        stored_response is None
                        or stored_response["response_id"]
                        != response_values["response_id"]
                        or stored_response["previous_response_id"]
                        != response_values["previous_response_id"]
                        or stored_response["checkpoint_id"] != checkpoint_id
                        or stored_response["visible_head_hash"]
                        != visible_head_hash
                        or int(stored_response["item_count"]) != item_count
                        or stored_response["visible_items_json"]
                        != response_values["visible_items_json"]
                        or stored_response["output_json"]
                        != response_values["output_json"]
                        or stored_response["envelope_json"]
                        != response_values["envelope_json"]):
                    raise ApiLeaseLost(
                        "Completed API finalization response record differs")
                return {
                    "idempotent": True,
                    "session": self._api_session_row(session),
                    "run": self._api_run_row(run),
                    "response": (
                        self._api_response_row(stored_response)
                        if stored_response else None),
                    "tool_batch": (
                        self._api_tool_batch_row(connection, tool_batch)
                        if tool_batch else None),
                }
            if (run["status"] != "running" or session is None
                    or session["state"] != "running"
                    or run["lease_id"] != lease_id
                    or session["lease_id"] != lease_id):
                raise ApiLeaseLost("The standard API run lease is no longer owned")
            if (isinstance(item_count, bool) or not isinstance(item_count, int)
                    or item_count < int(run["parent_item_count"])):
                raise ValueError("Final item_count cannot precede the parent head")
            namespace = {
                field: session[field] for field in _NAMESPACE_FIELDS
            }
            publication_generation = int(connection.execute(
                "SELECT api_generation FROM a2a_publications "
                "WHERE publication_id=?",
                (session["publication_id"],),
            ).fetchone()[0])
            expires_at = (
                timestamp + int(session["ttl_seconds"])
                if publication_generation == int(session["api_generation"])
                else timestamp
            )
            if pending_rows and connection.execute(
                    "SELECT 1 FROM api_export_tool_batches "
                    "WHERE session_id=? AND state='pending'",
                    (session["session_id"],),
            ).fetchone():
                raise ValueError(
                    "A session cannot publish a second pending client tool batch")
            for prefix in prefixes:
                prefix_hash = str(prefix.get("prefix_hash") or "")
                prefix_count = prefix.get("item_count")
                boundary_kind = str(prefix.get("boundary_kind") or "")
                if (not prefix_hash or isinstance(prefix_count, bool)
                        or not isinstance(prefix_count, int)
                        or prefix_count < 1 or prefix_count > item_count
                        or not boundary_kind):
                    raise ValueError("Invalid finalized API prefix")
                prefix_id = (
                    "apip_" + secrets.token_urlsafe(18).replace("-", "").replace("_", ""))
                connection.execute(
                    "INSERT INTO api_export_prefixes ("
                    "prefix_id, publication_id, key_id, api_generation, dialect, "
                    "api_model_id, canonicalization_version, hash_secret_version, "
                    "prefix_hash, item_count, session_id, checkpoint_id, "
                    "boundary_kind, created_at, last_seen_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        prefix_id,
                        namespace["publication_id"],
                        namespace["key_id"],
                        namespace["api_generation"],
                        namespace["dialect"],
                        namespace["api_model_id"],
                        namespace["canonicalization_version"],
                        namespace["hash_secret_version"],
                        prefix_hash,
                        prefix_count,
                        session["session_id"],
                        str(prefix.get("checkpoint_id") or checkpoint_id),
                        boundary_kind,
                        timestamp,
                        timestamp,
                    ),
                )
            tool_batch = None
            if pending_rows:
                batch_id = (
                    "apitb_" + secrets.token_urlsafe(18).replace(
                        "-", "").replace("_", ""))
                connection.execute(
                    "INSERT INTO api_export_tool_batches ("
                    "batch_id, run_id, session_id, visible_head_hash, item_count, "
                    "state, created_at) VALUES (?, ?, ?, ?, ?, 'pending', ?)",
                    (
                        batch_id, run_id, session["session_id"],
                        visible_head_hash, item_count, timestamp,
                    ),
                )
                for row in pending_rows:
                    connection.execute(
                        "INSERT INTO api_export_tool_calls ("
                        "batch_id, call_id, position, tool_name, arguments_json, "
                        "schema_fingerprint, state) "
                        "VALUES (?, ?, ?, ?, ?, ?, 'pending')",
                        (
                            batch_id, row["call_id"], row["position"],
                            row["tool_name"], row["arguments_json"],
                            row["schema_fingerprint"],
                        ),
                    )
                tool_batch = connection.execute(
                    "SELECT * FROM api_export_tool_batches WHERE batch_id=?",
                    (batch_id,),
                ).fetchone()
            stored_response = None
            if response_values is not None:
                connection.execute(
                    "INSERT INTO api_export_responses ("
                    "response_id, publication_id, key_id, api_generation, "
                    "dialect, api_model_id, canonicalization_version, "
                    "hash_secret_version, session_id, run_id, "
                    "previous_response_id, checkpoint_id, visible_head_hash, "
                    "item_count, visible_items_json, output_json, "
                    "envelope_json, created_at, expires_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                    "?, ?, ?)",
                    (
                        response_values["response_id"],
                        namespace["publication_id"],
                        namespace["key_id"],
                        namespace["api_generation"],
                        namespace["dialect"],
                        namespace["api_model_id"],
                        namespace["canonicalization_version"],
                        namespace["hash_secret_version"],
                        session["session_id"],
                        run_id,
                        response_values["previous_response_id"],
                        checkpoint_id,
                        visible_head_hash,
                        item_count,
                        response_values["visible_items_json"],
                        response_values["output_json"],
                        response_values["envelope_json"],
                        timestamp,
                        expires_at,
                    ),
                )
                stored_response = connection.execute(
                    "SELECT * FROM api_export_responses WHERE response_id=?",
                    (response_values["response_id"],),
                ).fetchone()
            next_state = "waiting_tool" if pending_rows else "idle"
            connection.execute(
                "UPDATE api_export_sessions SET visible_head_hash=?, "
                "item_count=?, head_checkpoint_id=?, state=?, lease_id='', "
                "lease_deadline=0, heartbeat_at=0, last_seen_at=?, expires_at=? "
                "WHERE session_id=? AND lease_id=?",
                (
                    visible_head_hash, item_count, checkpoint_id, next_state,
                    timestamp, expires_at,
                    session["session_id"], lease_id,
                ),
            )
            connection.execute(
                "UPDATE api_export_runs SET status='completed', response_id=?, "
                "heartbeat_at=?, finished_at=? WHERE run_id=? AND lease_id=?",
                (response_id, timestamp, timestamp, run_id, lease_id),
            )
            connection.execute(
                "DELETE FROM api_export_active_admissions WHERE run_id=?",
                (run_id,),
            )
            refreshed = connection.execute(
                "SELECT * FROM api_export_sessions WHERE session_id=?",
                (session["session_id"],),
            ).fetchone()
            finished = connection.execute(
                "SELECT * FROM api_export_runs WHERE run_id=?",
                (run_id,),
            ).fetchone()
            return {
                "idempotent": False,
                "session": self._api_session_row(refreshed),
                "run": self._api_run_row(finished),
                "response": (
                    self._api_response_row(stored_response)
                    if stored_response else None),
                "tool_batch": (
                    self._api_tool_batch_row(connection, tool_batch)
                    if tool_batch else None),
            }

    def settle_api_tool_batch(
            self,
            run_id: str,
            lease_id: str,
            *,
            results: Sequence[Mapping[str, Any]],
            client_tool_definitions: Sequence[Mapping[str, Any]],
            now: float | None = None,
    ) -> Dict[str, Any]:
        """Settle the complete pending parent batch under the successor lease."""

        if not run_id or not lease_id:
            raise ValueError("Run and lease identities are required")
        schemas = _client_tool_schema_map(client_tool_definitions)
        normalized_results = {}
        for result in results:
            if not isinstance(result, Mapping):
                raise ValueError("Client tool results must be objects")
            call_id = str(result.get("id") or "").strip()
            if not call_id:
                raise ValueError("Client tool result id is required")
            if call_id in normalized_results:
                raise ValueError(f"Duplicate client tool result id: {call_id}")
            normalized_results[call_id] = _json_fingerprint(
                dict(result), "Client tool result")[1]

        timestamp = time.time() if now is None else float(now)
        with self._lock, self._immediate() as connection:
            run = connection.execute(
                "SELECT * FROM api_export_runs WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if run is None:
                raise ValueError("Unknown standard API run")
            session = connection.execute(
                "SELECT * FROM api_export_sessions WHERE session_id=?",
                (run["session_id"],),
            ).fetchone()
            if (run["status"] != "running" or session is None
                    or session["state"] != "running"
                    or run["lease_id"] != lease_id
                    or session["lease_id"] != lease_id):
                raise ApiLeaseLost("The standard API run lease is no longer owned")
            batch = connection.execute(
                "SELECT * FROM api_export_tool_batches "
                "WHERE session_id=? AND visible_head_hash=? AND item_count=? "
                "ORDER BY created_at DESC LIMIT 1",
                (
                    session["session_id"], run["parent_head_hash"],
                    int(run["parent_item_count"]),
                ),
            ).fetchone()
            if batch is None:
                raise ValueError(
                    "Current tool results do not match a pending client tool batch")
            calls = connection.execute(
                "SELECT * FROM api_export_tool_calls WHERE batch_id=? "
                "ORDER BY position",
                (batch["batch_id"],),
            ).fetchall()
            expected_ids = {call["call_id"] for call in calls}
            result_ids = set(normalized_results)
            unknown = sorted(result_ids - expected_ids)
            if unknown:
                raise ValueError(
                    "Client tool result references an unknown pending call: "
                    + unknown[0])
            missing = sorted(expected_ids - result_ids)
            if missing:
                raise ValueError(
                    "The request must settle every pending client tool call")
            from core.identifier import identifier_key
            for call in calls:
                schema = schemas.get(identifier_key(call["tool_name"]))
                if schema != call["schema_fingerprint"]:
                    raise ValueError(
                        f"Client tool schema changed for '{call['tool_name']}'")

            if batch["state"] == "settled":
                if batch["settled_by_run_id"] != run_id:
                    raise ValueError("Client tool batch was already settled")
                if any(
                        call["result_fingerprint"]
                        != normalized_results[call["call_id"]]
                        for call in calls):
                    raise ValueError(
                        "Client tool batch was retried with different results")
                return {
                    "idempotent": True,
                    "batch": self._api_tool_batch_row(connection, batch),
                }
            if batch["state"] != "pending":
                raise ValueError("Client tool batch is canceled")

            for call in calls:
                connection.execute(
                    "UPDATE api_export_tool_calls SET state='settled', "
                    "result_fingerprint=?, settled_at=? "
                    "WHERE batch_id=? AND call_id=? AND state='pending'",
                    (
                        normalized_results[call["call_id"]], timestamp,
                        batch["batch_id"], call["call_id"],
                    ),
                )
            connection.execute(
                "UPDATE api_export_tool_batches SET state='settled', "
                "settled_by_run_id=?, settled_at=? "
                "WHERE batch_id=? AND state='pending'",
                (run_id, timestamp, batch["batch_id"]),
            )
            refreshed = connection.execute(
                "SELECT * FROM api_export_tool_batches WHERE batch_id=?",
                (batch["batch_id"],),
            ).fetchone()
            return {
                "idempotent": False,
                "batch": self._api_tool_batch_row(connection, refreshed),
            }

    def fail_api_run(
            self,
            run_id: str,
            lease_id: str,
            *,
            error_code: str,
            canceled: bool = False,
            now: float | None = None,
    ) -> bool:
        """Release a failed lease without publishing its partial visible head."""

        code = str(error_code or "").strip()
        if not run_id or not lease_id or not code or len(code) > 64:
            raise ValueError("Run, lease, and bounded error code are required")
        timestamp = time.time() if now is None else float(now)
        status = "canceled" if canceled else "failed"
        with self._lock, self._immediate() as connection:
            run = connection.execute(
                "SELECT * FROM api_export_runs WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if run is None:
                raise ValueError("Unknown standard API run")
            if run["status"] == status:
                return False
            session = connection.execute(
                "SELECT * FROM api_export_sessions WHERE session_id=?",
                (run["session_id"],),
            ).fetchone()
            if (run["status"] != "running" or session is None
                    or session["state"] != "running"
                    or run["lease_id"] != lease_id
                    or session["lease_id"] != lease_id):
                raise ApiLeaseLost("The standard API run lease is no longer owned")
            connection.execute(
                "UPDATE api_export_tool_calls SET state='pending', "
                "result_fingerprint='', settled_at=0 "
                "WHERE state='settled' AND batch_id IN ("
                "SELECT batch_id FROM api_export_tool_batches "
                "WHERE state='settled' AND settled_by_run_id=?)",
                (run_id,),
            )
            connection.execute(
                "UPDATE api_export_tool_batches SET state='pending', "
                "settled_by_run_id='', settled_at=0 "
                "WHERE state='settled' AND settled_by_run_id=?",
                (run_id,),
            )
            connection.execute(
                "UPDATE api_export_runs SET status=?, terminal_error_code=?, "
                "heartbeat_at=?, finished_at=? WHERE run_id=? AND lease_id=?",
                (status, code, timestamp, timestamp, run_id, lease_id),
            )
            connection.execute(
                "UPDATE api_export_sessions SET state='quarantined', "
                "lease_id='', lease_deadline=0, heartbeat_at=0, "
                "last_seen_at=?, expires_at=MIN(expires_at, ?) "
                "WHERE session_id=? AND lease_id=?",
                (
                    timestamp, timestamp, session["session_id"], lease_id,
                ),
            )
            connection.execute(
                "DELETE FROM api_export_active_admissions WHERE run_id=?",
                (run_id,),
            )
            return True

    def heartbeat_api_run(
            self, run_id: str, lease_id: str, *,
            lease_seconds: float = 120.0, now: float | None = None) -> bool:
        timestamp = time.time() if now is None else float(now)
        with self._lock, self._immediate() as connection:
            run = connection.execute(
                "UPDATE api_export_runs SET heartbeat_at=? "
                "WHERE run_id=? AND lease_id=? AND status='running'",
                (timestamp, run_id, lease_id),
            )
            session = connection.execute(
                "UPDATE api_export_sessions SET heartbeat_at=?, lease_deadline=? "
                "WHERE lease_id=? AND state='running'",
                (timestamp, timestamp + lease_seconds, lease_id),
            )
            return bool(run.rowcount and session.rowcount)

    def append_api_run_event(
            self, run_id: str, event: Mapping[str, Any], *,
            max_events: int = 256, now: float | None = None) -> int:
        if max_events < 1:
            raise ValueError("max_events must be positive")
        timestamp = time.time() if now is None else float(now)
        try:
            payload = json.dumps(
                event, ensure_ascii=False, allow_nan=False,
                sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ValueError("API replay event must be finite JSON") from exc
        with self._lock, self._immediate() as connection:
            run = connection.execute(
                "SELECT status FROM api_export_runs WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if run is None or run["status"] != "running":
                raise ValueError("API replay events require a running run")
            seq = int(connection.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 "
                "FROM api_export_run_events WHERE run_id=?",
                (run_id,),
            ).fetchone()[0])
            connection.execute(
                "INSERT INTO api_export_run_events VALUES (?, ?, ?, ?)",
                (run_id, seq, payload, timestamp),
            )
            connection.execute(
                "DELETE FROM api_export_run_events WHERE run_id=? AND seq<=?",
                (run_id, max(0, seq - max_events)),
            )
            return seq

    def read_api_run_events(
            self, run_id: str, after_seq: int = 0) -> list[Dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT seq, event_json, created_at FROM api_export_run_events "
                "WHERE run_id=? AND seq>? ORDER BY seq",
                (run_id, int(after_seq)),
            ).fetchall()
        return [
            {
                "seq": int(row["seq"]),
                "event": json.loads(row["event_json"]),
                "created_at": float(row["created_at"]),
            }
            for row in rows
        ]

    def abandon_expired_api_runs(self, *,
                                 now: float | None = None) -> int:
        """Quarantine sessions whose durable running lease expired."""

        timestamp = time.time() if now is None else float(now)
        with self._lock, self._immediate() as connection:
            sessions = connection.execute(
                "SELECT session_id FROM api_export_sessions "
                "WHERE state='running' AND lease_deadline<=?",
                (timestamp,),
            ).fetchall()
            for session in sessions:
                connection.execute(
                    "UPDATE api_export_tool_calls SET state='pending', "
                    "result_fingerprint='', settled_at=0 "
                    "WHERE state='settled' AND batch_id IN ("
                    "SELECT batch_id FROM api_export_tool_batches "
                    "WHERE state='settled' AND settled_by_run_id IN ("
                    "SELECT run_id FROM api_export_runs "
                    "WHERE session_id=? AND status='running'))",
                    (session["session_id"],),
                )
                connection.execute(
                    "UPDATE api_export_tool_batches SET state='pending', "
                    "settled_by_run_id='', settled_at=0 "
                    "WHERE state='settled' AND settled_by_run_id IN ("
                    "SELECT run_id FROM api_export_runs "
                    "WHERE session_id=? AND status='running')",
                    (session["session_id"],),
                )
                connection.execute(
                    "UPDATE api_export_runs SET status='abandoned', "
                    "terminal_error_code='lease_expired', finished_at=? "
                    "WHERE session_id=? AND status='running'",
                    (timestamp, session["session_id"]),
                )
                connection.execute(
                    "UPDATE api_export_sessions SET state='quarantined', "
                    "lease_id='', lease_deadline=0, heartbeat_at=0 "
                    "WHERE session_id=? AND state='running'",
                    (session["session_id"],),
                )
                connection.execute(
                    "DELETE FROM api_export_active_admissions "
                    "WHERE session_id=?",
                    (session["session_id"],),
                )
            return len(sessions)

    def sweep_expired_api_sessions(
            self,
            delete_conversation: Callable[[str], bool],
            *,
            now: float | None = None,
            limit: int = 100,
    ) -> int:
        """Claim expired idle/quarantined sessions, then delete their children."""

        if limit < 1:
            raise ValueError("limit must be positive")
        timestamp = time.time() if now is None else float(now)
        with self._lock, self._immediate() as connection:
            rows = connection.execute(
                "SELECT session_id, internal_conversation_id "
                "FROM api_export_sessions "
                "WHERE state IN ('idle', 'quarantined') AND expires_at<=? "
                "ORDER BY expires_at LIMIT ?",
                (timestamp, limit),
            ).fetchall()
            for row in rows:
                connection.execute(
                    "UPDATE api_export_sessions SET state='expired' "
                    "WHERE session_id=? AND state IN ('idle', 'quarantined')",
                    (row["session_id"],),
                )
        deleted = 0
        for row in rows:
            try:
                removed = bool(delete_conversation(
                    row["internal_conversation_id"]))
            except Exception:
                removed = False
            with self._lock, self._immediate() as connection:
                if removed:
                    result = connection.execute(
                        "DELETE FROM api_export_sessions "
                        "WHERE session_id=? AND state='expired'",
                        (row["session_id"],),
                    )
                    deleted += int(bool(result.rowcount))
                else:
                    connection.execute(
                        "UPDATE api_export_sessions SET state='quarantined', "
                        "expires_at=? WHERE session_id=? AND state='expired'",
                        (timestamp + 60, row["session_id"]),
                    )
        return deleted
