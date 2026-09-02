"""Bounded ephemeral scratchpad storage for one conversation agent."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import core.paths as _paths
from core.sqlite_store_guard import SqliteStoreGuard, SqliteStoreUnavailableError


DEFAULT_TTL_HOURS = 168
MAX_TTL_HOURS = 720
MAX_NOTES_PER_SCOPE = 200
MAX_TOPIC_CHARS = 160
MAX_CONTENT_CHARS = 16_000
MAX_PAGE_SIZE = 100


class ScratchpadStore:
    """SQLite scratchpad scoped by ``(user, conversation, agent)``."""

    _instance: Optional["ScratchpadStore"] = None
    _instance_lock = threading.Lock()

    @classmethod
    def instance(cls) -> "ScratchpadStore":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._guard = SqliteStoreGuard("Scratchpad")
        try:
            self._guard.initialize(self._database_path, self._initialize)
        except SqliteStoreUnavailableError:
            pass

    @property
    def available(self) -> bool:
        """Return whether the store is safe to read or write."""
        return self._guard.available

    @property
    def _database_path(self) -> Path:
        return _paths.SCRATCHPADS_DIR / "scratchpads.sqlite3"

    def _connect(self) -> sqlite3.Connection:
        self._guard.require_available()
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS scratchpad_notes (
                    user_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    id TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tags TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (user_id, conversation_id, agent_name, id)
                );
                CREATE INDEX IF NOT EXISTS idx_scratchpad_scope_updated
                    ON scratchpad_notes (
                        user_id, conversation_id, agent_name,
                        updated_at DESC, id DESC);
                CREATE INDEX IF NOT EXISTS idx_scratchpad_expiry
                    ON scratchpad_notes (expires_at);
                """
            )

    @staticmethod
    def _require_scope(user_id: str, conversation_id: str,
                       agent_name: str) -> None:
        if not user_id or not conversation_id or not agent_name:
            raise ValueError(
                "user_id, conversation_id and agent_name are required")

    @staticmethod
    def _validate_text(topic: str, content: str) -> tuple[str, str]:
        topic = " ".join(str(topic or "").split())
        content = str(content or "").strip()
        if not topic:
            raise ValueError("topic is required")
        if not content:
            raise ValueError("content is required")
        if len(topic) > MAX_TOPIC_CHARS:
            raise ValueError(f"topic exceeds {MAX_TOPIC_CHARS} characters")
        if len(content) > MAX_CONTENT_CHARS:
            raise ValueError(f"content exceeds {MAX_CONTENT_CHARS} characters")
        return topic, content

    @staticmethod
    def _tags(value: Any) -> List[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("tags must be an array")
        tags = []
        for item in value:
            tag = " ".join(str(item or "").split())[:80]
            if tag and tag not in tags:
                tags.append(tag)
        return tags[:20]

    @staticmethod
    def _ttl(value: Any) -> int:
        ttl = int(value if value is not None else DEFAULT_TTL_HOURS)
        if ttl < 1 or ttl > MAX_TTL_HOURS:
            raise ValueError(
                f"ttl_hours must be between 1 and {MAX_TTL_HOURS}")
        return ttl

    @staticmethod
    def _row(row: sqlite3.Row) -> Dict[str, Any]:
        note = dict(row)
        note.pop("user_id", None)
        note.pop("conversation_id", None)
        note.pop("agent_name", None)
        try:
            note["tags"] = json.loads(note.get("tags") or "[]")
        except (TypeError, ValueError):
            note["tags"] = []
        return note

    @staticmethod
    def _search_term(query: str) -> str:
        query = str(query or "").strip()
        if not query:
            return "%"
        escaped = (query.replace("\\", "\\\\").replace("%", "\\%")
                   .replace("_", "\\_"))
        return f"%{escaped}%"

    @staticmethod
    def _prune(connection: sqlite3.Connection, now: float) -> int:
        cursor = connection.execute(
            "DELETE FROM scratchpad_notes WHERE expires_at <= ?", (now,))
        return int(cursor.rowcount)

    def create(self, user_id: str, conversation_id: str, agent_name: str,
               *, topic: str, content: str, tags: Any = None,
               ttl_hours: Any = None) -> Dict[str, Any]:
        self._require_scope(user_id, conversation_id, agent_name)
        topic, content = self._validate_text(topic, content)
        clean_tags = self._tags(tags)
        ttl = self._ttl(ttl_hours)
        now = time.time()
        with self._lock, self._connect() as connection:
            self._prune(connection, now)
            count = connection.execute(
                """SELECT COUNT(*) FROM scratchpad_notes
                   WHERE user_id = ? AND conversation_id = ? AND agent_name = ?""",
                (user_id, conversation_id, agent_name),
            ).fetchone()[0]
            if count >= MAX_NOTES_PER_SCOPE:
                raise ValueError(
                    f"scratchpad limit reached ({MAX_NOTES_PER_SCOPE} notes); "
                    "update or delete an existing note")
            note_id = f"sp_{uuid.uuid4().hex[:12]}"
            connection.execute(
                """INSERT INTO scratchpad_notes (
                       user_id, conversation_id, agent_name, id, topic, content,
                       tags, expires_at, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, conversation_id, agent_name, note_id, topic, content,
                 json.dumps(clean_tags, ensure_ascii=False, separators=(",", ":")),
                 now + ttl * 3600, now, now),
            )
            row = connection.execute(
                """SELECT * FROM scratchpad_notes
                   WHERE user_id = ? AND conversation_id = ?
                     AND agent_name = ? AND id = ?""",
                (user_id, conversation_id, agent_name, note_id),
            ).fetchone()
        return self._row(row)

    def update(self, user_id: str, conversation_id: str, agent_name: str,
               note_id: str, **changes: Any) -> Dict[str, Any]:
        self._require_scope(user_id, conversation_id, agent_name)
        note_id = str(note_id or "").strip()
        if not note_id:
            raise ValueError("note_id is required")
        allowed = {"topic", "content", "tags", "ttl_hours"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(
                f"unsupported scratchpad fields: {', '.join(sorted(unknown))}")
        if not changes:
            raise ValueError("at least one scratchpad field is required")
        now = time.time()
        with self._lock, self._connect() as connection:
            self._prune(connection, now)
            row = connection.execute(
                """SELECT * FROM scratchpad_notes
                   WHERE user_id = ? AND conversation_id = ?
                     AND agent_name = ? AND id = ?""",
                (user_id, conversation_id, agent_name, note_id),
            ).fetchone()
            if row is None:
                raise ValueError(f"scratchpad note not found: {note_id}")
            current = self._row(row)
            topic, content = self._validate_text(
                changes.get("topic", current["topic"]),
                changes.get("content", current["content"]))
            tags = self._tags(changes.get("tags", current["tags"]))
            expires_at = float(current["expires_at"])
            if "ttl_hours" in changes:
                expires_at = now + self._ttl(changes["ttl_hours"]) * 3600
            connection.execute(
                """UPDATE scratchpad_notes
                   SET topic = ?, content = ?, tags = ?, expires_at = ?,
                       updated_at = ?
                   WHERE user_id = ? AND conversation_id = ?
                     AND agent_name = ? AND id = ?""",
                (topic, content,
                 json.dumps(tags, ensure_ascii=False, separators=(",", ":")),
                 expires_at, now, user_id, conversation_id, agent_name, note_id),
            )
            row = connection.execute(
                """SELECT * FROM scratchpad_notes
                   WHERE user_id = ? AND conversation_id = ?
                     AND agent_name = ? AND id = ?""",
                (user_id, conversation_id, agent_name, note_id),
            ).fetchone()
        return self._row(row)

    def get(self, user_id: str, conversation_id: str, agent_name: str,
            note_id: str) -> Optional[Dict[str, Any]]:
        self._require_scope(user_id, conversation_id, agent_name)
        now = time.time()
        with self._lock, self._connect() as connection:
            self._prune(connection, now)
            row = connection.execute(
                """SELECT * FROM scratchpad_notes
                   WHERE user_id = ? AND conversation_id = ?
                     AND agent_name = ? AND id = ?""",
                (user_id, conversation_id, agent_name, str(note_id or "")),
            ).fetchone()
        return self._row(row) if row is not None else None

    def list_page(self, user_id: str, conversation_id: str, agent_name: str,
                  *, query: str = "", limit: int = 20,
                  offset: int = 0) -> Dict[str, Any]:
        self._require_scope(user_id, conversation_id, agent_name)
        limit, offset = int(limit), int(offset)
        if limit < 1 or limit > MAX_PAGE_SIZE:
            raise ValueError(f"limit must be between 1 and {MAX_PAGE_SIZE}")
        if offset < 0:
            raise ValueError("offset must be non-negative")
        term = self._search_term(query)
        now = time.time()
        params = (user_id, conversation_id, agent_name, term, term, term)
        with self._lock, self._connect() as connection:
            self._prune(connection, now)
            total = int(connection.execute(
                """SELECT COUNT(*) FROM scratchpad_notes
                   WHERE user_id = ? AND conversation_id = ? AND agent_name = ?
                     AND (topic LIKE ? ESCAPE char(92)
                          OR content LIKE ? ESCAPE char(92)
                          OR tags LIKE ? ESCAPE char(92))""",
                params,
            ).fetchone()[0])
            rows = connection.execute(
                """SELECT * FROM scratchpad_notes
                   WHERE user_id = ? AND conversation_id = ? AND agent_name = ?
                     AND (topic LIKE ? ESCAPE char(92)
                          OR content LIKE ? ESCAPE char(92)
                          OR tags LIKE ? ESCAPE char(92))
                   ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?""",
                (*params, limit, offset),
            ).fetchall()
        notes = [self._row(row) for row in rows]
        return {"notes": notes, "total": total,
                "has_more": offset + len(notes) < total,
                "limit": limit, "offset": offset}

    def delete(self, user_id: str, conversation_id: str, agent_name: str,
               note_id: str) -> bool:
        self._require_scope(user_id, conversation_id, agent_name)
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """DELETE FROM scratchpad_notes
                   WHERE user_id = ? AND conversation_id = ?
                     AND agent_name = ? AND id = ?""",
                (user_id, conversation_id, agent_name, str(note_id or "")),
            )
        return bool(cursor.rowcount)

    def clear(self, user_id: str, conversation_id: str,
              agent_name: str) -> int:
        self._require_scope(user_id, conversation_id, agent_name)
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """DELETE FROM scratchpad_notes
                   WHERE user_id = ? AND conversation_id = ? AND agent_name = ?""",
                (user_id, conversation_id, agent_name),
            )
        return int(cursor.rowcount)

    def context_hint(self, user_id: str, conversation_id: str,
                     agent_name: str) -> str:
        page = self.list_page(
            user_id, conversation_id, agent_name, limit=MAX_PAGE_SIZE)
        notes = page["notes"]
        if not notes:
            return ""
        topics = ", ".join(note["topic"] for note in notes[:5])
        oldest_expiry = min(float(note["expires_at"]) for note in notes)
        hours = max(0, int((oldest_expiry - time.time()) / 3600))
        return (
            f"Scratchpad contains {page['total']} active note(s); earliest expiry "
            f"in about {hours}h. Topics: {topics}.\n"
            "Contents are not injected automatically. Use scratchpad list/search/get "
            "after compaction or when resuming relevant work. Store only transient "
            "evidence, hypotheses, local decisions, and resume cues; update an existing "
            "note instead of duplicating it, and delete obsolete notes. Use todolist "
            "for authoritative work state and memory for durable facts/preferences."
        )


__all__ = ["ScratchpadStore"]
