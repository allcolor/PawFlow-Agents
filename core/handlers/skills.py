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
        return block + self._skill_loop_suffix(name)

    def _skill_loop_suffix(self, name: str) -> str:
        """Usage tracking + improvement footer + promotion suggestion.

        Best-effort: the skill prompt is returned unchanged on any error.
        """
        try:
            from core.skill_loop import SKILL_IMPROVE_FOOTER
            from core.skill_stats import record_load
            suffix = SKILL_IMPROVE_FOOTER
            stats = record_load(self._user_id, name,
                                conversation_id=self._conversation_id,
                                agent_name=self._agent_name)
            if int(stats.get("loads", 0)) >= 3 and self._conversation_id:
                from core.resource_store import ResourceStore
                skill = ResourceStore.instance().get_any(
                    "skill", name, self._user_id,
                    conversation_id=self._conversation_id) or {}
                if skill.get("_scope") == "conversation":
                    suffix += (
                        f"\nThis conversation-scoped skill has been loaded "
                        f"{int(stats['loads'])} times — consider asking the "
                        f"user whether to promote it to user scope via "
                        f"`manage_resource` so other conversations can use it."
                    )
            return suffix
        except Exception:
            return ""
