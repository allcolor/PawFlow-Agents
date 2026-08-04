"""Durable per-agent todo lists.

Each list is one atomic JSON document under
``data/runtime/todolists/<user>/<conversation>/<agent>.json``. Todo items are
mutable work state, deliberately separate from memories and orchestrated plans.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import core.paths as _paths


TODO_STATUSES = ("pending", "in_progress", "completed")
def _safe_component(value: str) -> str:
    """Encode one scope identifier as a collision-free path component."""
    return quote(value, safe="")


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

    @staticmethod
    def _require_scope(user_id: str, conversation_id: str,
                       agent_name: str) -> None:
        if not user_id:
            raise ValueError("user_id is required for todo storage")
        if not conversation_id:
            raise ValueError("conversation_id is required for todo storage")
        if not agent_name:
            raise ValueError("agent_name is required for todo storage")

    def _path(self, user_id: str, conversation_id: str,
              agent_name: str) -> Path:
        self._require_scope(user_id, conversation_id, agent_name)
        return (_paths.TODOLISTS_DIR / _safe_component(user_id)
                / _safe_component(conversation_id)
                / f"{_safe_component(agent_name)}.json")

    @staticmethod
    def _load(path: Path) -> List[Dict[str, Any]]:
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
            raise ValueError(f"invalid todo store document: {path}")
        return [dict(item) for item in data["tasks"] if isinstance(item, dict)]

    @staticmethod
    def _save(path: Path, tasks: List[Dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"version": 1, "tasks": tasks}, ensure_ascii=False,
            indent=2, sort_keys=True) + "\n"
        tmp = path.with_name(
            f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)

    @staticmethod
    def _find(tasks: List[Dict[str, Any]], task_id: str) -> Optional[Dict[str, Any]]:
        for task in tasks:
            if task.get("id") == task_id or task.get("external_id") == task_id:
                return task
        return None

    def create(self, user_id: str, conversation_id: str, agent_name: str,
               *, subject: str, description: str = "", active_form: str = "",
               owner: str = "", blocks: Optional[List[str]] = None,
               blocked_by: Optional[List[str]] = None,
               metadata: Optional[Dict[str, Any]] = None,
               external_id: str = "", source_call_id: str = "") -> Dict[str, Any]:
        subject = str(subject or "").strip()
        if not subject:
            raise ValueError("subject is required")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("metadata must be an object")
        for field, value in (("blocks", blocks), ("blocked_by", blocked_by)):
            if value is not None and not isinstance(value, list):
                raise ValueError(f"{field} must be an array")
        path = self._path(user_id, conversation_id, agent_name)
        now = time.time()
        values = {
            "subject": subject,
            "description": str(description or ""),
            "active_form": str(active_form or ""),
            "owner": str(owner or ""),
            "blocks": [str(item) for item in (blocks or [])],
            "blocked_by": [str(item) for item in (blocked_by or [])],
            "metadata": dict(metadata or {}),
            "external_id": str(external_id or ""),
            "source_call_id": str(source_call_id or ""),
        }
        with self._lock:
            tasks = self._load(path)
            if source_call_id:
                for task in tasks:
                    if task.get("source_call_id") == source_call_id:
                        task.update(values)
                        task["updated_at"] = now
                        self._save(path, tasks)
                        return dict(task)
            task = {
                "id": f"td_{uuid.uuid4().hex[:12]}",
                "status": "pending",
                "created_at": now,
                "updated_at": now,
                **values,
            }
            tasks.append(task)
            self._save(path, tasks)
            return dict(task)

    def update(self, user_id: str, conversation_id: str, agent_name: str,
               task_id: str, **changes: Any) -> Dict[str, Any]:
        task_id = str(task_id or "").strip()
        if not task_id:
            raise ValueError("task_id is required")
        allowed = {
            "subject", "description", "active_form", "status", "owner",
            "blocks", "blocked_by", "metadata", "external_id",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"unsupported todo fields: {', '.join(sorted(unknown))}")
        if not changes:
            raise ValueError("at least one todo field is required")
        if "status" in changes and changes["status"] not in TODO_STATUSES:
            raise ValueError(
                "status must be pending, in_progress, or completed")
        if "subject" in changes and not str(changes["subject"] or "").strip():
            raise ValueError("subject cannot be empty")
        if "metadata" in changes and not isinstance(changes["metadata"], dict):
            raise ValueError("metadata must be an object")
        for field in ("blocks", "blocked_by"):
            if field in changes and not isinstance(changes[field], list):
                raise ValueError(f"{field} must be an array")
        path = self._path(user_id, conversation_id, agent_name)
        with self._lock:
            tasks = self._load(path)
            task = self._find(tasks, task_id)
            if task is None:
                raise ValueError(f"todo task not found: {task_id}")
            for field, value in changes.items():
                if field in ("blocks", "blocked_by"):
                    task[field] = [str(item) for item in value]
                elif field == "metadata":
                    task[field] = dict(value)
                else:
                    task[field] = str(value).strip() if field == "subject" else str(value or "")
            task["updated_at"] = time.time()
            self._save(path, tasks)
            return dict(task)

    def get(self, user_id: str, conversation_id: str, agent_name: str,
            task_id: str) -> Optional[Dict[str, Any]]:
        path = self._path(user_id, conversation_id, agent_name)
        with self._lock:
            task = self._find(self._load(path), str(task_id or ""))
            return dict(task) if task is not None else None

    def list_tasks(self, user_id: str, conversation_id: str, agent_name: str,
                   *, status: str = "") -> List[Dict[str, Any]]:
        if status and status not in TODO_STATUSES:
            raise ValueError(
                "status must be pending, in_progress, or completed")
        path = self._path(user_id, conversation_id, agent_name)
        with self._lock:
            tasks = self._load(path)
        if status:
            tasks = [task for task in tasks if task.get("status") == status]
        tasks.sort(key=lambda task: (
            float(task.get("created_at") or 0), str(task.get("id") or "")))
        return tasks

    def context_text(self, user_id: str, conversation_id: str,
                     agent_name: str) -> str:
        tasks = self.list_tasks(user_id, conversation_id, agent_name)
        active = [task for task in tasks
                  if task.get("status") in ("pending", "in_progress")]
        completed = sorted(
            (task for task in tasks if task.get("status") == "completed"),
            key=lambda task: float(task.get("updated_at") or 0), reverse=True)[:5]
        if not active and not completed:
            return ""
        lines = [
            "This state is authoritative and survives compaction and provider restarts.",
            "Use `todolist` to inspect or update it.",
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
