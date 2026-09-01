"""Management tool for the current conversation agent's ScratchDir."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from core.scratchdir_models import MAX_TTL_HOURS, ScratchDirError
from core.tool_handler import ToolHandler


class ScratchDirHandler(ToolHandler):
    """Manage one relay-backed temporary filesystem root."""

    def __init__(self) -> None:
        self._user_id = ""
        self._conversation_id = ""
        self._agent_name = ""
        self._manager = None
        self._fs_service = None

    @property
    def name(self) -> str:
        return "scratchdir"

    @property
    def description(self) -> str:
        return (
            "Manage the temporary filesystem for the current "
            "user/conversation/agent. Use fs://scratchdir/ with filesystem "
            "tools for intermediate files that must survive tool calls, "
            "compaction, or provider restarts. Use FileStore for durable "
            "deliverables and the workspace for source changes. ScratchDir "
            "expires automatically and never falls back to /tmp or a hidden "
            "project directory. Python virtual environments must use "
            "`python -m venv --copies` because escaping symlinks are rejected. "
            "Actions: status, ensure, renew, clear.")

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["status", "ensure", "renew", "clear"],
                    "description": (
                        "status: inspect availability and usage; ensure: create "
                        "the scoped root; renew: extend its TTL; clear: remove "
                        "the exact scoped root")
                },
                "ttl_hours": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_TTL_HOURS,
                    "description": "Hours until expiry (default 168, max 720)",
                },
            },
            "required": ["action"],
        }

    def set_user_id(self, user_id: str) -> None:
        self._user_id = user_id

    def set_conversation_id(self, conversation_id: str) -> None:
        self._conversation_id = conversation_id

    def set_agent_name(self, agent_name: str) -> None:
        self._agent_name = agent_name

    def set_scratchdir_manager(self, manager) -> None:
        self._manager = manager

    def set_fs_service(self, service) -> None:
        self._fs_service = service
        self._manager = None

    def execute(self, arguments: dict[str, Any]) -> str:
        if not self._user_id or not self._conversation_id or not self._agent_name:
            return (
                "Error [scratchdir_context_missing]: user_id, conversation_id "
                "and agent_name are required")
        action = str(arguments.get("action") or "").strip()
        try:
            if self._manager is None:
                if self._fs_service is None:
                    return (
                        "Error [scratchdir_unavailable]: ScratchDir lifecycle "
                        "is not available for the current relay")
                from core.scratchdir_manager import ScratchDirManager
                self._manager = ScratchDirManager(self._fs_service)
            value = self._manager.execute(
                action=action,
                user_id=self._user_id,
                conversation_id=self._conversation_id,
                agent_name=self._agent_name,
                ttl_hours=arguments.get("ttl_hours"),
            )
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        except ScratchDirError as exc:
            return f"Error [{exc.code}]: {exc}"
        except sqlite3.DatabaseError as exc:
            return f"Error [scratchdir_unavailable]: {exc}"
        except (TypeError, ValueError) as exc:
            return f"Error [scratchdir_invalid_request]: {exc}"
