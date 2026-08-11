"""Tool handler for the current conversation agent's scratchpad."""

from __future__ import annotations

import json
from typing import Any, Dict

from core.scratchpad_store import MAX_TTL_HOURS, ScratchpadStore
from core.tool_handler import ToolHandler


class ScratchpadHandler(ToolHandler):
    """Create, search, update and remove temporary working notes."""

    def __init__(self) -> None:
        self._user_id = ""
        self._conversation_id = ""
        self._agent_name = ""

    @property
    def name(self) -> str:
        return "scratchpad"

    @property
    def description(self) -> str:
        return (
            "Manage temporary working notes that survive compaction and provider "
            "restarts for the current user/conversation/agent only. Contents are "
            "never injected automatically; only a compact topic/count hint appears in "
            "context. Use list/get when that hint reports relevant notes, and create or "
            "update notes for transient evidence, hypotheses, local decisions, and resume "
            "cues. Use todolist for authoritative tasks, memory for durable facts, and "
            "diary for durable first-person lessons. Notes expire automatically. "
            "Actions: create, update, get, list, delete, clear.")

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": [
                    "create", "update", "get", "list", "delete", "clear"],
                    "description": (
                        "create: add a note; update: mutate by note_id; get: read one; "
                        "list: paginated text search; delete: remove one; clear: remove "
                        "all notes in the current scope")},
                "note_id": {"type": "string",
                            "description": "Required for update/get/delete"},
                "topic": {"type": "string",
                          "description": "Short non-sensitive label shown in context hints"},
                "content": {"type": "string",
                            "description": "Temporary note body; never auto-injected"},
                "tags": {"type": "array", "items": {"type": "string"},
                         "description": "Searchable labels"},
                "ttl_hours": {"type": "integer", "minimum": 1,
                              "maximum": MAX_TTL_HOURS,
                              "description": "Hours until expiry (default 168, max 720)"},
                "query": {"type": "string",
                          "description": "Search topic, content, and tags for list"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100,
                          "description": "Page size for list (default 20)"},
                "offset": {"type": "integer", "minimum": 0,
                           "description": "Pagination offset for list"},
            },
            "required": ["action"],
        }

    def set_user_id(self, user_id: str) -> None:
        self._user_id = user_id

    def set_conversation_id(self, conversation_id: str) -> None:
        self._conversation_id = conversation_id

    def set_agent_name(self, agent_name: str) -> None:
        self._agent_name = agent_name

    def execute(self, arguments: Dict[str, Any]) -> str:
        if not self._user_id or not self._conversation_id or not self._agent_name:
            return "Error: user_id, conversation_id and agent_name are required"
        action = str(arguments.get("action") or "").strip()
        store = ScratchpadStore.instance()
        scope = (self._user_id, self._conversation_id, self._agent_name)
        try:
            if action == "create":
                value = store.create(
                    *scope, topic=arguments.get("topic", ""),
                    content=arguments.get("content", ""),
                    tags=arguments.get("tags"),
                    ttl_hours=arguments.get("ttl_hours"))
            elif action == "update":
                changes = {key: arguments[key] for key in (
                    "topic", "content", "tags", "ttl_hours") if key in arguments}
                value = store.update(
                    *scope, str(arguments.get("note_id") or ""), **changes)
            elif action == "get":
                note_id = str(arguments.get("note_id") or "")
                value = store.get(*scope, note_id)
                if value is None:
                    raise ValueError(f"scratchpad note not found: {note_id}")
            elif action == "list":
                value = store.list_page(
                    *scope, query=str(arguments.get("query") or ""),
                    limit=int(arguments.get("limit") or 20),
                    offset=int(arguments.get("offset") or 0))
            elif action == "delete":
                note_id = str(arguments.get("note_id") or "")
                if not note_id:
                    raise ValueError("note_id is required")
                value = {"deleted": store.delete(*scope, note_id),
                         "note_id": note_id}
            elif action == "clear":
                value = {"deleted": store.clear(*scope)}
            else:
                raise ValueError(
                    "action must be create, update, get, list, delete, or clear")
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError) as exc:
            return f"Error: {exc}"
