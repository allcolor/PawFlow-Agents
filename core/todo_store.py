"""Durable per-agent todo lists backed by indexed SQLite storage.

Todo items are scoped by user, conversation and agent. Legacy per-agent JSON
documents are imported transactionally once, then removed.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import unquote

import core.paths as _paths


TODO_STATUSES = ("pending", "in_progress", "completed")
_PAGE_LIMIT_MAX = 100
_TASK_FIELDS = (
    "id", "status", "subject", "description", "active_form", "owner",
    "blocks", "blocked_by", "metadata", "external_id", "source_call_id",
    "created_at", "updated_at",
)


class TodoStore:
    """Thread-safe persistent todo CRUD scoped by user, conversation and agent."""

    _instance: Optional["TodoStore"] = None
    _instance_lock = threading.Lock()

    @classmethod
    def instance(cls) -> "TodoStore":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._initialize()

    @property
    def _database_path(self) -> Path:
        return _paths.TODOLISTS_DIR / "todos.sqlite3"

    def _connect(self) -> sqlite3.Connection:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS todos (
                    user_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN ('pending', 'in_progress', 'completed')),
                    subject TEXT NOT NULL,
                    description TEXT NOT NULL,
                    active_form TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    blocks TEXT NOT NULL,
                    blocked_by TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    external_id TEXT NOT NULL,
                    source_call_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (user_id, conversation_id, agent_name, id)
                );
                CREATE INDEX IF NOT EXISTS idx_todos_scope_status_updated
                    ON todos (
                        user_id, conversation_id, agent_name,
                        status, updated_at DESC, id DESC);
                CREATE INDEX IF NOT EXISTS idx_todos_scope_created
                    ON todos (
                        user_id, conversation_id, agent_name,
                        created_at DESC, id DESC);
                CREATE INDEX IF NOT EXISTS idx_todos_scope_external
                    ON todos (
                        user_id, conversation_id, agent_name, external_id);
                CREATE INDEX IF NOT EXISTS idx_todos_scope_source
                    ON todos (
                        user_id, conversation_id, agent_name, source_call_id);
                """
            )
            legacy_files = self._import_legacy_documents(connection)
        self._remove_imported_documents(legacy_files)

    @staticmethod
    def _require_scope(user_id: str, conversation_id: str,
                       agent_name: str) -> None:
        if not user_id:
            raise ValueError("user_id is required for todo storage")
        if not conversation_id:
            raise ValueError("conversation_id is required for todo storage")
        if not agent_name:
            raise ValueError("agent_name is required for todo storage")

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _decoded_json(value: str, fallback: Any) -> Any:
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return fallback
        return decoded if isinstance(decoded, type(fallback)) else fallback

    @classmethod
    def _row_to_task(cls, row: sqlite3.Row) -> Dict[str, Any]:
        task = {field: row[field] for field in _TASK_FIELDS}
        task["blocks"] = cls._decoded_json(task.get("blocks", ""), [])
        task["blocked_by"] = cls._decoded_json(
            task.get("blocked_by", ""), [])
        task["metadata"] = cls._decoded_json(task.get("metadata", ""), {})
        return task

    def _import_legacy_documents(
            self, connection: sqlite3.Connection) -> List[Path]:
        imported: List[Path] = []
        root = _paths.TODOLISTS_DIR
        for path in sorted(root.rglob("*.json")):
            relative = path.relative_to(root)
            if len(relative.parts) != 3:
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(
                    data.get("tasks"), list):
                raise ValueError(f"invalid todo store document: {path}")
            user_id = unquote(relative.parts[0])
            conversation_id = unquote(relative.parts[1])
            agent_name = unquote(Path(relative.parts[2]).stem)
            self._require_scope(user_id, conversation_id, agent_name)
            for item in data["tasks"]:
                if not isinstance(item, dict):
                    continue
                status = str(item.get("status") or "pending")
                if status not in TODO_STATUSES:
                    raise ValueError(
                        f"invalid todo status in legacy document: {status}")
                task_id = str(item.get("id") or "").strip()
                subject = str(item.get("subject") or "").strip()
                if not task_id or not subject:
                    raise ValueError(
                        f"invalid todo item in legacy document: {path}")
                created_at = float(item.get("created_at") or 0)
                updated_at = float(item.get("updated_at") or created_at)
                connection.execute(
                    """
                    INSERT OR IGNORE INTO todos (
                        user_id, conversation_id, agent_name, id, status,
                        subject, description, active_form, owner, blocks,
                        blocked_by, metadata, external_id, source_call_id,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id, conversation_id, agent_name, task_id, status,
                        subject, str(item.get("description") or ""),
                        str(item.get("active_form") or ""),
                        str(item.get("owner") or ""),
                        self._json(item.get("blocks") or []),
                        self._json(item.get("blocked_by") or []),
                        self._json(item.get("metadata") or {}),
                        str(item.get("external_id") or ""),
                        str(item.get("source_call_id") or ""),
                        created_at, updated_at,
                    ),
                )
            imported.append(path)
        return imported

    @staticmethod
    def _remove_imported_documents(paths: List[Path]) -> None:
        for path in paths:
            root = _paths.TODOLISTS_DIR
            path.unlink()
            parent = path.parent
            while parent != root:
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent

    @staticmethod
    def _validate_dependencies(
            blocks: Optional[List[str]],
            blocked_by: Optional[List[str]]) -> None:
        for field, value in (("blocks", blocks), ("blocked_by", blocked_by)):
            if value is not None and not isinstance(value, list):
                raise ValueError(f"{field} must be an array")

    def create(self, user_id: str, conversation_id: str, agent_name: str,
               *, subject: str, description: str = "", active_form: str = "",
               status: str = "pending",
               owner: str = "", blocks: Optional[List[str]] = None,
               blocked_by: Optional[List[str]] = None,
               metadata: Optional[Dict[str, Any]] = None,
               external_id: str = "", source_call_id: str = "") -> Dict[str, Any]:
        self._require_scope(user_id, conversation_id, agent_name)
        subject = str(subject or "").strip()
        if not subject:
            raise ValueError("subject is required")
        status = str(status or "").strip()
        if status not in TODO_STATUSES:
            raise ValueError(
                "status must be pending, in_progress, or completed")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("metadata must be an object")
        self._validate_dependencies(blocks, blocked_by)
        now = time.time()
        values = {
            "subject": subject,
            "description": str(description or ""),
            "active_form": str(active_form or ""),
            "owner": str(owner or ""),
            "blocks": self._json([str(item) for item in (blocks or [])]),
            "blocked_by": self._json(
                [str(item) for item in (blocked_by or [])]),
            "metadata": self._json(dict(metadata or {})),
            "external_id": str(external_id or ""),
            "source_call_id": str(source_call_id or ""),
        }
        with self._lock, self._connect() as connection:
            existing = None
            if source_call_id:
                existing = connection.execute(
                    """
                    SELECT * FROM todos
                    WHERE user_id = ? AND conversation_id = ?
                      AND agent_name = ? AND source_call_id = ?
                    LIMIT 1
                    """,
                    (user_id, conversation_id, agent_name, source_call_id),
                ).fetchone()
            if existing is not None:
                connection.execute(
                    """
                    UPDATE todos SET
                        subject = ?, description = ?, active_form = ?,
                        owner = ?, blocks = ?, blocked_by = ?, metadata = ?,
                        external_id = ?, source_call_id = ?, updated_at = ?
                    WHERE user_id = ? AND conversation_id = ?
                      AND agent_name = ? AND id = ?
                    """,
                    (
                        values["subject"], values["description"],
                        values["active_form"], values["owner"],
                        values["blocks"], values["blocked_by"],
                        values["metadata"], values["external_id"],
                        values["source_call_id"], now,
                        user_id, conversation_id, agent_name, existing["id"],
                    ),
                )
                task_id = existing["id"]
            else:
                task_id = f"td_{uuid.uuid4().hex[:12]}"
                connection.execute(
                    """
                    INSERT INTO todos (
                        user_id, conversation_id, agent_name, id, status,
                        subject, description, active_form, owner, blocks,
                        blocked_by, metadata, external_id, source_call_id,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id, conversation_id, agent_name, task_id, status,
                        values["subject"], values["description"],
                        values["active_form"], values["owner"],
                        values["blocks"], values["blocked_by"],
                        values["metadata"], values["external_id"],
                        values["source_call_id"], now, now,
                    ),
                )
            row = connection.execute(
                """
                SELECT * FROM todos
                WHERE user_id = ? AND conversation_id = ?
                  AND agent_name = ? AND id = ?
                """,
                (user_id, conversation_id, agent_name, task_id),
            ).fetchone()
            return self._row_to_task(row)

    def update(self, user_id: str, conversation_id: str, agent_name: str,
               task_id: str, **changes: Any) -> Dict[str, Any]:
        self._require_scope(user_id, conversation_id, agent_name)
        task_id = str(task_id or "").strip()
        if not task_id:
            raise ValueError("task_id is required")
        allowed = {
            "subject", "description", "active_form", "status", "owner",
            "blocks", "blocked_by", "metadata", "external_id",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(
                f"unsupported todo fields: {', '.join(sorted(unknown))}")
        if not changes:
            raise ValueError("at least one todo field is required")
        if "status" in changes and changes["status"] not in TODO_STATUSES:
            raise ValueError(
                "status must be pending, in_progress, or completed")
        if "subject" in changes and not str(changes["subject"] or "").strip():
            raise ValueError("subject cannot be empty")
        if "metadata" in changes and not isinstance(changes["metadata"], dict):
            raise ValueError("metadata must be an object")
        self._validate_dependencies(
            changes.get("blocks"), changes.get("blocked_by"))

        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM todos
                WHERE user_id = ? AND conversation_id = ? AND agent_name = ?
                  AND (id = ? OR external_id = ?)
                LIMIT 1
                """,
                (user_id, conversation_id, agent_name, task_id, task_id),
            ).fetchone()
            if row is None:
                raise ValueError(f"todo task not found: {task_id}")
            current = self._row_to_task(row)
            current.update(changes)
            connection.execute(
                """
                UPDATE todos SET
                    subject = ?, description = ?, active_form = ?, status = ?,
                    owner = ?, blocks = ?, blocked_by = ?, metadata = ?,
                    external_id = ?, updated_at = ?
                WHERE user_id = ? AND conversation_id = ? AND agent_name = ?
                  AND id = ?
                """,
                (
                    str(current["subject"]).strip(),
                    str(current["description"] or ""),
                    str(current["active_form"] or ""),
                    str(current["status"]),
                    str(current["owner"] or ""),
                    self._json([str(item) for item in current["blocks"]]),
                    self._json(
                        [str(item) for item in current["blocked_by"]]),
                    self._json(dict(current["metadata"])),
                    str(current["external_id"] or ""), time.time(),
                    user_id, conversation_id, agent_name, current["id"],
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM todos
                WHERE user_id = ? AND conversation_id = ? AND agent_name = ?
                  AND id = ?
                """,
                (user_id, conversation_id, agent_name, current["id"]),
            ).fetchone()
            return self._row_to_task(row)

    def get(self, user_id: str, conversation_id: str, agent_name: str,
            task_id: str) -> Optional[Dict[str, Any]]:
        self._require_scope(user_id, conversation_id, agent_name)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM todos
                WHERE user_id = ? AND conversation_id = ? AND agent_name = ?
                  AND (id = ? OR external_id = ?)
                LIMIT 1
                """,
                (user_id, conversation_id, agent_name,
                 str(task_id or ""), str(task_id or "")),
            ).fetchone()
        return self._row_to_task(row) if row is not None else None

    @staticmethod
    def _search_term(query: str) -> str:
        query = str(query or "").strip()
        if not query:
            return "%"
        escaped = (
            query.replace("\\", "\\\\").replace("%", "\\%")
            .replace("_", "\\_")
        )
        return f"%{escaped}%"

    def list_page(self, user_id: str, conversation_id: str, agent_name: str,
                  *, status: str = "", query: str = "", limit: int = 20,
                  offset: int = 0) -> Dict[str, Any]:
        self._require_scope(user_id, conversation_id, agent_name)
        if status and status not in TODO_STATUSES:
            raise ValueError(
                "status must be pending, in_progress, or completed")
        limit = int(limit)
        offset = int(offset)
        if limit < 1 or limit > _PAGE_LIMIT_MAX:
            raise ValueError(
                f"limit must be between 1 and {_PAGE_LIMIT_MAX}")
        if offset < 0:
            raise ValueError("offset must be non-negative")
        search_term = self._search_term(query)
        scope_params: List[Any] = [
            user_id, conversation_id, agent_name, *([search_term] * 5)]
        with self._lock, self._connect() as connection:
            count_rows = connection.execute(
                """
                SELECT status, COUNT(*) AS count FROM todos
                WHERE user_id = ? AND conversation_id = ? AND agent_name = ?
                  AND (
                    id LIKE ? ESCAPE char(92)
                    OR subject LIKE ? ESCAPE char(92)
                    OR description LIKE ? ESCAPE char(92)
                    OR active_form LIKE ? ESCAPE char(92)
                    OR owner LIKE ? ESCAPE char(92)
                  )
                GROUP BY status
                """,
                scope_params,
            ).fetchall()
            counts = {item: 0 for item in TODO_STATUSES}
            for row in count_rows:
                counts[row["status"]] = int(row["count"])
            page_params = scope_params + [status, status, limit, offset]
            rows = connection.execute(
                """
                SELECT * FROM todos
                WHERE user_id = ? AND conversation_id = ? AND agent_name = ?
                  AND (
                    id LIKE ? ESCAPE char(92)
                    OR subject LIKE ? ESCAPE char(92)
                    OR description LIKE ? ESCAPE char(92)
                    OR active_form LIKE ? ESCAPE char(92)
                    OR owner LIKE ? ESCAPE char(92)
                  )
                  AND (? = '' OR status = ?)
                ORDER BY updated_at DESC, created_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                page_params,
            ).fetchall()
        tasks = [self._row_to_task(row) for row in rows]
        total = counts[status] if status else sum(counts.values())
        return {
            "tasks": tasks,
            "total": total,
            "counts": counts,
            "has_more": offset + len(tasks) < total,
            "limit": limit,
            "offset": offset,
        }

    def list_tasks(self, user_id: str, conversation_id: str, agent_name: str,
                   *, status: str = "") -> List[Dict[str, Any]]:
        self._require_scope(user_id, conversation_id, agent_name)
        if status and status not in TODO_STATUSES:
            raise ValueError(
                "status must be pending, in_progress, or completed")
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM todos
                WHERE user_id = ? AND conversation_id = ? AND agent_name = ?
                  AND (? = '' OR status = ?)
                ORDER BY created_at, id
                """,
                (user_id, conversation_id, agent_name, status, status),
            ).fetchall()
        return [self._row_to_task(row) for row in rows]

    def context_text(self, user_id: str, conversation_id: str,
                     agent_name: str) -> str:
        pending = self.list_tasks(
            user_id, conversation_id, agent_name, status="pending")
        in_progress = self.list_tasks(
            user_id, conversation_id, agent_name, status="in_progress")
        completed = self.list_page(
            user_id, conversation_id, agent_name, status="completed",
            limit=5)["tasks"]
        active = pending + in_progress
        if not active and not completed:
            return ""
        lines = [
            "This state is authoritative and survives compaction and provider restarts.",
            "Use todolist to inspect or update it.",
            "",
        ]
        for task in active:
            lines.append(
                f"- [{task.get('status')}] {task.get('id')} — {task.get('subject')}")
        if completed:
            if active:
                lines.append("")
            lines.append("Recently completed:")
            for task in completed:
                lines.append(
                    f"- [completed] {task.get('id')} — {task.get('subject')}")
        return "\n".join(lines)
