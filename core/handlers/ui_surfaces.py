"""Context-bound tool for publishing portable interactive UI surfaces."""

from __future__ import annotations

import json
from typing import Any

from core.tool_handler import ToolHandler


class PresentUiSurfaceHandler(ToolHandler):
    """Publish semantic UI state for the current user and conversation."""

    def __init__(self) -> None:
        self._conversation_id = ""
        self._user_id = ""
        self._agent_name = ""

    def set_conversation_id(self, value: str) -> None:
        self._conversation_id = str(value or "")

    def set_user_id(self, value: str) -> None:
        self._user_id = str(value or "")

    def set_agent_name(self, value: str) -> None:
        self._agent_name = str(value or "")

    @property
    def name(self) -> str:
        return "present_ui_surface"

    @property
    def description(self) -> str:
        return (
            "Publish or update a durable semantic UI surface in the current "
            "conversation. Any signed PFP component is optional presentation; "
            "all actions still dispatch through normal server handlers.")

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "producer_kind": {
                    "type": "string",
                    "enum": ["agent", "workflow_agent", "task", "workflow_task"],
                },
                "producer_id": {"type": "string"},
                "semantic": {"type": "object"},
                "surface_id": {"type": "string"},
                "revision": {"type": "integer", "minimum": 1},
                "status": {
                    "type": "string",
                    "enum": [
                        "open", "waiting_for_compatible_client",
                        "resolved", "cancelled",
                    ],
                },
                "required_capabilities": {
                    "type": "array", "items": {"type": "string"},
                },
                "presentation": {"type": "object"},
                "fallback": {"type": "object"},
            },
            "required": ["producer_kind", "producer_id", "semantic"],
        }

    def execute(self, arguments: dict[str, Any]) -> str:
        try:
            if not self._user_id or not self._conversation_id:
                return "Error: present_ui_surface requires user and conversation context"
            from core.ui_surface import make_ui_surface
            from core.ui_surface_store import publish_ui_surface
            surface = make_ui_surface(
                user_id=self._user_id,
                conversation_id=self._conversation_id,
                producer_kind=str(arguments.get("producer_kind") or ""),
                producer_id=str(arguments.get("producer_id") or ""),
                semantic=arguments.get("semantic"),
                surface_id=str(arguments.get("surface_id") or ""),
                revision=int(arguments.get("revision") or 1),
                status=str(arguments.get("status") or "open"),
                required_capabilities=arguments.get("required_capabilities"),
                presentation=arguments.get("presentation"),
                fallback=arguments.get("fallback"),
            )
            stored = publish_ui_surface(surface)
            return json.dumps({"surface": stored}, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001 - tool boundary serializes failures
            return f"Error: {exc}"


__all__ = ["PresentUiSurfaceHandler"]
