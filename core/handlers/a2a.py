"""Agent tool for interoperating with configured remote A2A agents."""

import json
from typing import Any, Dict

from core.tool_handler import ToolHandler


class A2AHandler(ToolHandler):
    def __init__(self):
        self._user_id = ""
        self._conversation_id = ""
        self._agent_name = ""

    def set_user_id(self, value: str) -> None:
        self._user_id = value or ""

    def set_conversation_id(self, value: str) -> None:
        self._conversation_id = value or ""

    def set_agent_name(self, value: str) -> None:
        self._agent_name = value or ""

    @property
    def name(self) -> str:
        return "a2a"

    @property
    def display_name(self) -> str:
        return "A2A"

    @property
    def description(self) -> str:
        from core.a2a_store import A2AStore
        targets = (A2AStore.instance().list_targets(self._conversation_id)
                   if self._conversation_id else [])
        remote = [row["alias"] for row in targets if row.get("kind") == "remote"]
        suffix = (" Configured remote targets: " + ", ".join(remote)
                  if remote else " No remote targets are configured yet.")
        return (
            "Send a task to any configured A2A-compatible remote agent, then "
            "get or cancel its task. send returns immediately; retain its task "
            "and context IDs for follow-up calls." + suffix)

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["send", "get", "cancel"]},
                "target": {"type": "string", "description": "Configured target alias"},
                "message": {"type": "string", "description": "Task text for send"},
                "task_id": {"type": "string", "description": "Remote task ID for get/cancel"},
                "context_id": {"type": "string", "description": "Optional remote context to continue"},
            },
            "required": ["action", "target"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        if not self._conversation_id or not self._user_id:
            return "Error: a2a requires a conversation and authenticated user"
        alias = str(arguments.get("target") or "").strip()
        from core.a2a_store import A2AStore
        target = A2AStore.instance().get_target(self._conversation_id, alias)
        if not target:
            return f"Error: unknown A2A target alias '{alias}'"
        try:
            from core.a2a_client import call_target
            result = call_target(
                target, str(arguments.get("action") or ""),
                message=str(arguments.get("message") or ""),
                task_id=str(arguments.get("task_id") or ""),
                context_id=str(arguments.get("context_id") or ""),
                user_id=self._user_id, conversation_id=self._conversation_id,
                agent_name=self._agent_name,
            )
        except Exception as exc:
            return f"Error: A2A call failed: {exc}"
        return json.dumps(result, ensure_ascii=False)
