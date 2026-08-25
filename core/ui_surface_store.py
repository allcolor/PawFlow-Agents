"""Durable registry for renderer-independent UI surfaces."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

import core.paths as _paths
from core.ui_surface import validate_ui_surface


class UiSurfaceConflict(RuntimeError):
    """A surface revision moved or was reused with different content."""


class UiSurfaceStore:
    """Persist the latest canonical revision of each scoped UI surface."""

    _instance: UiSurfaceStore | None = None
    _instance_lock = threading.Lock()

    @classmethod
    def instance(cls) -> UiSurfaceStore:
        path = _paths.RUNTIME_DIR / "ui_surfaces.sqlite3"
        with cls._instance_lock:
            if cls._instance is None or cls._instance.database_path != path:
                cls._instance = cls(path)
            return cls._instance

    @classmethod
    def reset(cls) -> None:
        with cls._instance_lock:
            cls._instance = None

    def __init__(self, database_path: Path | None = None) -> None:
        self.database_path = Path(
            database_path or (_paths.RUNTIME_DIR / "ui_surfaces.sqlite3"))
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS ui_surfaces (
                    surface_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    producer_kind TEXT NOT NULL,
                    producer_id TEXT NOT NULL,
                    document_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ui_surfaces_scope
                    ON ui_surfaces(user_id, conversation_id, updated_at);
                """)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            str(self.database_path), timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def upsert(
        self, surface: dict[str, Any], *, user_id: str,
        conversation_id: str,
    ) -> dict[str, Any]:
        """Store one validated revision without allowing scope or CAS drift."""
        clean = validate_ui_surface(surface)
        if clean["user_id"] != str(user_id or ""):
            raise ValueError("surface user_id does not match publisher context")
        if clean["conversation_id"] != str(conversation_id or ""):
            raise ValueError(
                "surface conversation_id does not match publisher context")
        encoded = json.dumps(
            clean, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        producer = clean["producer"]
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT revision, document_json FROM ui_surfaces "
                "WHERE surface_id = ?", (clean["surface_id"],),
            ).fetchone()
            if current is not None:
                if int(current["revision"]) > clean["revision"]:
                    raise UiSurfaceConflict("surface revision moved forward")
                if int(current["revision"]) == clean["revision"]:
                    if str(current["document_json"]) != encoded:
                        raise UiSurfaceConflict(
                            "surface revision was reused with different content")
                    connection.commit()
                    return clean
            connection.execute(
                """
                INSERT INTO ui_surfaces (
                    surface_id, user_id, conversation_id, revision, status,
                    producer_kind, producer_id, document_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(surface_id) DO UPDATE SET
                    user_id = excluded.user_id,
                    conversation_id = excluded.conversation_id,
                    revision = excluded.revision,
                    status = excluded.status,
                    producer_kind = excluded.producer_kind,
                    producer_id = excluded.producer_id,
                    document_json = excluded.document_json,
                    updated_at = excluded.updated_at
                """,
                (
                    clean["surface_id"], clean["user_id"],
                    clean["conversation_id"], clean["revision"],
                    clean["status"], producer["kind"], producer["id"],
                    encoded, str(clean.get("updated_at") or ""),
                ),
            )
            connection.commit()
        return clean

    def list(
        self, *, user_id: str, conversation_id: str, limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Return detached latest revisions for one authenticated scope."""
        user_id = str(user_id or "").strip()
        conversation_id = str(conversation_id or "").strip()
        if not user_id or not conversation_id:
            raise ValueError("user_id and conversation_id are required")
        bounded = max(1, min(int(limit), 500))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT document_json FROM ui_surfaces
                WHERE user_id = ? AND conversation_id = ?
                ORDER BY updated_at DESC, surface_id DESC LIMIT ?
                """,
                (user_id, conversation_id, bounded),
            ).fetchall()
        return [json.loads(row["document_json"]) for row in rows]


def publish_ui_surface(surface: dict[str, Any]) -> dict[str, Any]:
    """Commit a surface, then broadcast its canonical stored revision."""
    clean = validate_ui_surface(surface)
    stored = UiSurfaceStore.instance().upsert(
        clean, user_id=clean["user_id"],
        conversation_id=clean["conversation_id"])
    from core.conversation_event_bus import ConversationEventBus
    ConversationEventBus.instance().publish_event(
        stored["conversation_id"], "ui_surface_upserted", {"surface": stored})
    return stored


__all__ = [
    "UiSurfaceConflict", "UiSurfaceStore", "publish_ui_surface",
]
