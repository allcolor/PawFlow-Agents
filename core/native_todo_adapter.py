"""Mirror successful native CLI task tools into PawFlow's TodoStore."""

from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any


logger = logging.getLogger(__name__)


def native_task_id(value: Any) -> str:
    """Extract a Claude native task id from JSON or stable text forms."""
    if isinstance(value, dict):
        for key in ("id", "taskId", "task_id"):
            if value.get(key) is not None:
                return str(value[key]).strip()
        for key in ("task", "result", "data", "content"):
            found = native_task_id(value.get(key))
            if found:
                return found
        return ""
    if isinstance(value, list):
        for item in value:
            found = native_task_id(item)
            if found:
                return found
        return ""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        parsed = None
    if parsed is not None and parsed != text:
        found = native_task_id(parsed)
        if found:
            return found
    for pattern in (
        r"\btask\s+#([A-Za-z0-9_.:-]+)",
        r"\btask(?:\s+id)?\s*[:#]\s*([A-Za-z0-9_.:-]+)",
    ):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


class NativeTodoAdapter:
    """Correlate native TaskCreate/TaskUpdate calls with successful results."""

    def __init__(self, user_id: str, conversation_id: str, agent_name: str,
                 provider: str):
        if not user_id or not conversation_id or not agent_name:
            raise ValueError("native todo adapter requires complete session identity")
        self.user_id = user_id
        self.conversation_id = conversation_id
        self.agent_name = agent_name
        self.provider = provider
        self.pending: dict[str, dict] = {}
        self.mirrored: set[str] = set()
        self._lock = threading.Lock()

    def observe(self, event: dict) -> None:
        """Observe one normalized tool_use/tool_result event; never raise."""
        try:
            self._observe(event)
        except Exception:
            logger.error(
                "[%s-todolist] failed to mirror native task event",
                self.provider, exc_info=True)

    def _observe(self, event: dict) -> None:
        event_type = event.get("type")
        tool_id = str(event.get("tool_use_id") or "")
        if not tool_id:
            return
        if event_type == "tool_use":
            name = str(event.get("name") or "")
            if name not in {"TaskCreate", "TaskUpdate"}:
                return
            arguments = event.get("arguments") or {}
            if not isinstance(arguments, dict):
                return
            ids = {tool_id}
            ids.update(str(item) for item in (event.get("alias_ids") or []) if item)
            record = {
                "call_id": tool_id, "ids": ids, "name": name,
                "arguments": dict(arguments),
            }
            with self._lock:
                if ids & self.mirrored:
                    return
                for call_id in ids:
                    self.pending[call_id] = record
            return
        if event_type != "tool_result":
            return
        with self._lock:
            record = self.pending.get(tool_id)
            if record is None:
                return
            ids = set(record["ids"])
            for call_id in ids:
                self.pending.pop(call_id, None)
            if ids & self.mirrored:
                return
        if event.get("is_error"):
            with self._lock:
                self.mirrored.update(ids)
            return
        self._apply(record, event.get("content"))
        with self._lock:
            self.mirrored.update(ids)

    def _apply(self, record: dict, result: Any) -> None:
        from core.todo_store import TODO_STATUSES, TodoStore

        store = TodoStore.instance()
        args = record["arguments"]
        if record["name"] == "TaskCreate":
            external_id = native_task_id(result)
            store.create(
                self.user_id, self.conversation_id, self.agent_name,
                subject=args.get("subject", ""),
                description=args.get("description", ""),
                active_form=args.get("activeForm", args.get("active_form", "")),
                owner=args.get("owner", ""), blocks=args.get("blocks"),
                blocked_by=args.get("blockedBy", args.get("blocked_by")),
                metadata=args.get("metadata"), external_id=external_id,
                source_call_id=record["call_id"])
            if not external_id:
                logger.warning(
                    "[%s-todolist] TaskCreate call=%s returned no native task id",
                    self.provider, record["call_id"])
            return

        task_id = str(
            args.get("taskId") or args.get("task_id") or args.get("id") or "")
        if not task_id:
            raise ValueError("TaskUpdate did not include a task id")
        changes = {}
        for native, target in (
            ("subject", "subject"), ("description", "description"),
            ("activeForm", "active_form"), ("active_form", "active_form"),
            ("owner", "owner"), ("metadata", "metadata"),
            ("blocks", "blocks"), ("blockedBy", "blocked_by"),
            ("blocked_by", "blocked_by"),
        ):
            if native in args:
                changes[target] = args[native]
        status = args.get("status")
        if status in TODO_STATUSES:
            changes["status"] = status
        elif status:
            raise ValueError(f"unsupported native task status: {status}")
        existing = store.get(
            self.user_id, self.conversation_id, self.agent_name, task_id)
        if existing is None:
            raise ValueError(f"native todo task not found: {task_id}")
        for native, target in (("addBlocks", "blocks"),
                               ("addBlockedBy", "blocked_by")):
            if native in args:
                current = list(existing.get(target) or [])
                for item in args.get(native) or []:
                    value = str(item)
                    if value not in current:
                        current.append(value)
                changes[target] = current
        if changes:
            store.update(
                self.user_id, self.conversation_id, self.agent_name,
                task_id, **changes)
