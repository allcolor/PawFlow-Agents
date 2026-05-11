"""Skill tools for lazy skill loading."""

import json
from typing import Any, Dict

from core.tool_handler import ToolHandler


class LoadSkillHandler(ToolHandler):
    """Return the full prompt for an assigned skill on demand."""

    def __init__(self):
        self._user_id = ""
        self._conversation_id = ""
        self._agent_name = ""

    @property
    def name(self) -> str:
        return "load_skill"

    @property
    def description(self) -> str:
        return (
            "Load the full prompt for a skill assigned to the current agent. "
            "Use this only after an available-skill manifest says the skill is relevant."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name of the assigned skill to load.",
                },
            },
            "required": ["name"],
        }

    def set_user_id(self, uid: str):
        self._user_id = uid or ""

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid or ""

    def set_agent_name(self, name: str):
        self._agent_name = name or ""

    def execute(self, arguments: Dict[str, Any]) -> str:
        name = (arguments.get("name") or "").strip()
        if not name:
            return "Error: 'name' is required"
        if not self._agent_name:
            return "Error: load_skill requires an active agent context"
        from core.skill_resolver import resolve_assigned_skill_prompt
        block = resolve_assigned_skill_prompt(
            name, self._user_id, self._conversation_id, self._agent_name)
        if not block:
            return json.dumps({
                "error": f"Skill '{name}' is not assigned to agent '{self._agent_name}'",
            })
        return block
