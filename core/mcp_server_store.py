"""Durable published-MCP server, API-key and CLI-lease storage.

Published servers are inbound MCP endpoints bound to one ordinary PawFlow
conversation and one of its agent instances.  They are intentionally separate
from ResourceStore's ``mcp`` resources, which describe outbound MCP servers
consumed by PawFlow agents.
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


_CLIENT_LEASE_TTL_SECONDS = 120.0
_IMAGE_OUTPUTS = frozenset({"native", "describe"})


class MCPServerStore:
    """Thread-safe SQLite store for MCP publications and delegated keys."""

    _instance: Optional["MCPServerStore"] = None
    _instance_lock = threading.Lock()

    @classmethod
    def instance(cls) -> "MCPServerStore":
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
        return self._database_path_override or (_paths.SYSTEM_DIR / "mcp_servers.sqlite3")

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
                CREATE TABLE IF NOT EXISTS mcp_servers (
                    server_id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL UNIQUE,
                    agent_name TEXT NOT NULL,
                    label TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    active_client_id TEXT NOT NULL DEFAULT '',
                    active_client_name TEXT NOT NULL DEFAULT '',
                    active_relay_id TEXT NOT NULL DEFAULT '',
                    client_heartbeat_at REAL NOT NULL DEFAULT 0,
                    image_output TEXT NOT NULL DEFAULT 'native'
                );
                CREATE INDEX IF NOT EXISTS idx_mcp_servers_owner_conversation
                    ON mcp_servers(owner_user_id, conversation_id);

                CREATE TABLE IF NOT EXISTS mcp_api_keys (
                    key_id TEXT PRIMARY KEY,
                    server_id TEXT NOT NULL REFERENCES mcp_servers(server_id)
                        ON DELETE CASCADE,
                    label TEXT NOT NULL,
                    prefix TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    created_at REAL NOT NULL,
                    last_used_at REAL NOT NULL DEFAULT 0,
                    revoked_at REAL NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_mcp_keys_server
                    ON mcp_api_keys(server_id, revoked_at);
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(mcp_servers)")
            }
            if "image_output" not in columns:
                connection.execute(
                    "ALTER TABLE mcp_servers ADD COLUMN "
                    "image_output TEXT NOT NULL DEFAULT 'native'"
                )

    @staticmethod
    def _server_row(row: sqlite3.Row) -> Dict[str, Any]:
        data = dict(row)
        data["enabled"] = bool(data.get("enabled"))
        heartbeat = float(data.get("client_heartbeat_at") or 0)
        data["client_active"] = bool(
            data.get("active_client_id")
            and heartbeat > time.time() - _CLIENT_LEASE_TTL_SECONDS)
        return data

    @staticmethod
    def _key_row(row: sqlite3.Row) -> Dict[str, Any]:
        data = dict(row)
        data.pop("token_hash", None)
        data["revoked"] = bool(data.get("revoked_at"))
        return data

    def configure(self, owner_user_id: str, conversation_id: str,
                  agent_name: str, label: str = "", enabled: bool = True,
                  image_output: str = "native") -> Dict[str, Any]:
        if not owner_user_id or not conversation_id or not agent_name:
            raise ValueError("owner_user_id, conversation_id and agent_name are required")
        image_output = str(image_output or "").strip().lower()
        if image_output not in _IMAGE_OUTPUTS:
            raise ValueError("image_output must be 'native' or 'describe'")
        now = time.time()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT server_id, owner_user_id FROM mcp_servers WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            if row and row["owner_user_id"] != owner_user_id:
                raise PermissionError("MCP server belongs to another conversation owner")
            if row:
                server_id = row["server_id"]
                connection.execute(
                    """UPDATE mcp_servers
                       SET agent_name = ?, label = ?, enabled = ?, image_output = ?,
                           updated_at = ?
                       WHERE server_id = ?""",
                    (agent_name, label or agent_name, int(bool(enabled)),
                     image_output, now, server_id),
                )
            else:
                server_id = "srv_" + secrets.token_urlsafe(18).replace("-", "").replace("_", "")
                connection.execute(
                    """INSERT INTO mcp_servers (
                           server_id, owner_user_id, conversation_id, agent_name,
                           label, enabled, image_output, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (server_id, owner_user_id, conversation_id, agent_name,
                     label or agent_name, int(bool(enabled)), image_output, now, now),
                )
        result = self.get(server_id)
        if result is None:
            raise RuntimeError("MCP server configuration was not persisted")
        return result

    def get(self, server_id: str) -> Optional[Dict[str, Any]]:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM mcp_servers WHERE server_id = ?", (server_id,)
            ).fetchone()
        return self._server_row(row) if row else None

    def get_for_conversation(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM mcp_servers WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
        return self._server_row(row) if row else None

    def has_servers(self) -> bool:
        """Return whether at least one inbound MCP publication exists."""
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM mcp_servers LIMIT 1"
            ).fetchone()
        return row is not None

    def set_enabled(self, server_id: str, enabled: bool) -> bool:
        with self._lock, self._connect() as connection:
            cur = connection.execute(
                "UPDATE mcp_servers SET enabled = ?, updated_at = ? WHERE server_id = ?",
                (int(bool(enabled)), time.time(), server_id),
            )
        return bool(cur.rowcount)

    def delete(self, server_id: str) -> bool:
        with self._lock, self._connect() as connection:
            cur = connection.execute(
                "DELETE FROM mcp_servers WHERE server_id = ?", (server_id,)
            )
        return bool(cur.rowcount)

    def create_key(self, server_id: str, label: str = "") -> Tuple[str, Dict[str, Any]]:
        if not self.get(server_id):
            raise KeyError("MCP server not found")
        raw = "pfmcp_" + secrets.token_urlsafe(32)
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        key_id = "key_" + secrets.token_hex(8)
        now = time.time()
        prefix = raw[:14]
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO mcp_api_keys (
                       key_id, server_id, label, prefix, token_hash, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (key_id, server_id, label or "CLI key", prefix, digest, now),
            )
        return raw, {
            "key_id": key_id, "server_id": server_id,
            "label": label or "CLI key", "prefix": prefix,
            "created_at": now, "last_used_at": 0, "revoked_at": 0,
            "revoked": False,
        }

    def list_keys(self, server_id: str) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM mcp_api_keys WHERE server_id = ?
                   ORDER BY created_at DESC""",
                (server_id,),
            ).fetchall()
        return [self._key_row(row) for row in rows]

    def revoke_key(self, server_id: str, key_id: str) -> bool:
        with self._lock, self._connect() as connection:
            cur = connection.execute(
                """UPDATE mcp_api_keys SET revoked_at = ?
                   WHERE server_id = ? AND key_id = ? AND revoked_at = 0""",
                (time.time(), server_id, key_id),
            )
        return bool(cur.rowcount)

    def validate_key(self, server_id: str, raw_token: str) -> Optional[Dict[str, Any]]:
        if not raw_token:
            return None
        digest = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """SELECT k.*, s.enabled AS server_enabled
                   FROM mcp_api_keys k JOIN mcp_servers s ON s.server_id = k.server_id
                   WHERE k.server_id = ? AND k.token_hash = ? AND k.revoked_at = 0""",
                (server_id, digest),
            ).fetchone()
            if not row or not row["server_enabled"]:
                return None
            # compare_digest retains a constant-time final comparison even though
            # SQLite already selected a high-entropy SHA-256 value.
            if not hmac.compare_digest(str(row["token_hash"]), digest):
                return None
            connection.execute(
                "UPDATE mcp_api_keys SET last_used_at = ? WHERE key_id = ?",
                (time.time(), row["key_id"]),
            )
        return self._key_row(row)

    def claim_client(self, server_id: str, client_id: str, client_name: str,
                     relay_id: str) -> Dict[str, Any]:
        if not client_id:
            raise ValueError("client_id is required")
        now = time.time()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM mcp_servers WHERE server_id = ?", (server_id,)
            ).fetchone()
            if not row:
                raise KeyError("MCP server not found")
            current = str(row["active_client_id"] or "")
            fresh = float(row["client_heartbeat_at"] or 0) > now - _CLIENT_LEASE_TTL_SECONDS
            if current and current != client_id and fresh:
                raise RuntimeError("MCP server already has an active CLI instance")
            connection.execute(
                """UPDATE mcp_servers SET active_client_id = ?, active_client_name = ?,
                       active_relay_id = ?, client_heartbeat_at = ?, updated_at = ?
                   WHERE server_id = ?""",
                (client_id, client_name or "CLI", relay_id, now, now, server_id),
            )
        result = self.get(server_id)
        if result is None:
            raise RuntimeError("MCP client lease was not persisted")
        return result

    def heartbeat_client(self, server_id: str, client_id: str) -> bool:
        with self._lock, self._connect() as connection:
            cur = connection.execute(
                """UPDATE mcp_servers SET client_heartbeat_at = ?, updated_at = ?
                   WHERE server_id = ? AND active_client_id = ?""",
                (time.time(), time.time(), server_id, client_id),
            )
        return bool(cur.rowcount)

    def release_client(self, server_id: str, client_id: str) -> bool:
        with self._lock, self._connect() as connection:
            cur = connection.execute(
                """UPDATE mcp_servers SET active_client_id = '', active_client_name = '',
                       active_relay_id = '', client_heartbeat_at = 0, updated_at = ?
                   WHERE server_id = ? AND active_client_id = ?""",
                (time.time(), server_id, client_id),
            )
        return bool(cur.rowcount)

    def expire_stale_clients(self) -> List[Dict[str, Any]]:
        """Clear and return CLI leases whose heartbeat TTL has elapsed."""
        cutoff = time.time() - _CLIENT_LEASE_TTL_SECONDS
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """SELECT * FROM mcp_servers
                   WHERE active_client_id != '' AND client_heartbeat_at < ?""",
                (cutoff,),
            ).fetchall()
            if rows:
                connection.execute(
                    """UPDATE mcp_servers
                       SET active_client_id = '', active_client_name = '',
                           active_relay_id = '', client_heartbeat_at = 0,
                           updated_at = ?
                       WHERE active_client_id != '' AND client_heartbeat_at < ?""",
                    (time.time(), cutoff),
                )
        return [self._server_row(row) for row in rows]


__all__ = ["MCPServerStore"]
