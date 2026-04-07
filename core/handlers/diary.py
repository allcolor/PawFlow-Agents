"""Diary handlers — agent reads/writes its personal journal."""

import json
import logging
from typing import Any, Dict

from core.tool_handler import ToolHandler

logger = logging.getLogger(__name__)


class DiaryWriteHandler(ToolHandler):
    """Write an entry to the agent's personal diary."""

    def __init__(self):
        self._user_id = ""
        self._agent_name = ""
        self._conversation_id = ""

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid

    @property
    def name(self) -> str:
        return "diary_write"

    @property
    def description(self) -> str:
        return (
            "Write an entry to your personal diary. Entries persist across "
            "conversations and help you maintain domain expertise."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "entry": {"type": "string", "description": "The diary entry text"},
                "type": {
                    "type": "string",
                    "enum": ["observation", "decision", "learning", "reflection"],
                    "description": "Entry type (default: observation)",
                },
                "tags": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Tags for categorization",
                },
            },
            "required": ["entry"],
        }

    def set_user_id(self, uid: str):
        self._user_id = uid

    def set_agent_name(self, name: str):
        self._agent_name = name

    def execute(self, arguments: Dict[str, Any]) -> str:
        entry = arguments.get("entry", "").strip()
        if not entry:
            return "Error: entry is required"
        if not self._user_id or not self._agent_name:
            return "Error: user_id and agent_name required"
        try:
            from core.agent_diary import AgentDiary
            record = AgentDiary.instance().write(
                self._user_id, self._agent_name, entry,
                entry_type=arguments.get("type", "observation"),
                tags=arguments.get("tags"),
            )
            return f"Diary entry saved ({record['id']}): [{record['type']}] {entry[:80]}"
        except Exception as e:
            return f"Error: {e}"


class DiaryReadHandler(ToolHandler):
    """Read recent entries from the agent's personal diary."""

    def __init__(self):
        self._user_id = ""
        self._agent_name = ""
        self._conversation_id = ""

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid

    @property
    def name(self) -> str:
        return "diary_read"

    @property
    def description(self) -> str:
        return "Read your recent diary entries. Shows your past observations and decisions."

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max entries (default: 10)"},
                "type": {
                    "type": "string",
                    "enum": ["observation", "decision", "learning", "reflection"],
                    "description": "Filter by entry type",
                },
            },
        }

    def set_user_id(self, uid: str):
        self._user_id = uid

    def set_agent_name(self, name: str):
        self._agent_name = name

    def execute(self, arguments: Dict[str, Any]) -> str:
        if not self._user_id or not self._agent_name:
            return "Error: user_id and agent_name required"
        try:
            from core.agent_diary import AgentDiary
            entries = AgentDiary.instance().read(
                self._user_id, self._agent_name,
                limit=int(arguments.get("limit", 10) or 10),
                entry_type=arguments.get("type", ""),
            )
            if not entries:
                return "No diary entries yet."
            lines = [f"Diary ({len(entries)} entries, newest first):"]
            for e in entries:
                import time
                _ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(e.get("ts", 0)))
                lines.append(f"  [{e.get('type', '?')}] {_ts}: {e.get('text', '')[:120]}")
            return "\n".join(lines)
        except Exception as e:
            return f"Error: {e}"
