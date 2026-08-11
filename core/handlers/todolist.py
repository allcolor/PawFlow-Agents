"""Universal durable todo-list tool handler."""

from __future__ import annotations

import json
from typing import Any, Dict

from core.todo_store import TODO_STATUSES, TodoStore
from core.tool_handler import ToolHandler


class TodoListHandler(ToolHandler):
    """Create, update and inspect the current agent's durable work list."""

    def __init__(self) -> None:
        self._user_id = ""
        self._conversation_id = ""
        self._agent_name = ""

    @property
    def name(self) -> str:
        return "todolist"

    @property
    def description(self) -> str:
        return (
            "Manage your durable working todo list. Use it to record concrete "
            "work that must survive long tasks, context compaction, provider "
            "restarts, and cold sessions. Create items before meaningful multi-step "
            "work, mark the active item in_progress, and complete it promptly. Use "
            "scratchpad for temporary notes, memory for durable facts, and create_plan "
            "when user approval or step orchestration is required. Actions: create, "
            "update, list, get.")

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["create", "update", "list", "get"],
                    "description": (
                        "create: new pending item; update: mutate an item by task_id; "
                        "get: fetch one item by task_id; list: paginated search/filter"),
                },
                "task_id": {"type": "string",
                            "description": "Required for update/get; returned by create"},
                "subject": {"type": "string",
                            "description": "Required for create; concise outcome"},
                "description": {"type": "string",
                                "description": "Success criteria and resume details"},
                "active_form": {"type": "string",
                                "description": "Present-tense activity shown while active"},
                "status": {"type": "string", "enum": list(TODO_STATUSES),
                           "description": "Filter for list or new status for update"},
                "query": {"type": "string",
                          "description": "Text filter for list"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100,
                          "description": "Page size for list (default 20)"},
                "offset": {"type": "integer", "minimum": 0,
                           "description": "Pagination offset for list"},
                "owner": {"type": "string",
                          "description": "Optional responsible agent/person label"},
                "blocks": {"type": "array", "items": {"type": "string"},
                           "description": "Task IDs blocked by this item"},
                "blocked_by": {"type": "array", "items": {"type": "string"},
                              "description": "Task IDs that block this item"},
                "metadata": {"type": "object",
                             "description": "Small structured resume metadata"},
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
        store = TodoStore.instance()
        try:
            if action == "create":
                task = store.create(
                    self._user_id, self._conversation_id, self._agent_name,
                    subject=arguments.get("subject", ""),
                    description=arguments.get("description", ""),
                    active_form=arguments.get("active_form", ""),
                    owner=arguments.get("owner", ""),
                    blocks=arguments.get("blocks"),
                    blocked_by=arguments.get("blocked_by"),
                    metadata=arguments.get("metadata"),
                )
                return json.dumps(task, ensure_ascii=False, sort_keys=True)
            if action == "update":
                changes = {
                    key: arguments[key] for key in (
                        "subject", "description", "active_form", "status",
                        "owner", "blocks", "blocked_by", "metadata")
                    if key in arguments
                }
                task = store.update(
                    self._user_id, self._conversation_id, self._agent_name,
                    arguments.get("task_id", ""), **changes)
                return json.dumps(task, ensure_ascii=False, sort_keys=True)
            if action == "get":
                task_id = str(arguments.get("task_id") or "").strip()
                if not task_id:
                    raise ValueError("task_id is required")
                task = store.get(
                    self._user_id, self._conversation_id,
                    self._agent_name, task_id)
                if task is None:
                    raise ValueError(f"todo task not found: {task_id}")
                return json.dumps(task, ensure_ascii=False, sort_keys=True)
            if action == "list":
                page = store.list_page(
                    self._user_id, self._conversation_id, self._agent_name,
                    status=str(arguments.get("status") or ""),
                    query=str(arguments.get("query") or ""),
                    limit=int(arguments.get("limit") or 20),
                    offset=int(arguments.get("offset") or 0))
                return json.dumps(
                    page, ensure_ascii=False, sort_keys=True)
            raise ValueError("action must be create, update, list, or get")
        except (TypeError, ValueError) as exc:
            return f"Error: {exc}"
