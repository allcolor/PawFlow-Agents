"""Agent-facing management of plaintext PawFlow variables."""

from __future__ import annotations

import json
import re
from typing import Any

from core.tool_handler import ToolHandler

_VARIABLE_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")


def _stored_value(value: Any) -> str:
    """Return the stable string representation used by expression resolution."""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class ManageVariableHandler(ToolHandler):
    """Read and mutate plaintext user or conversation variables."""

    def __init__(self):
        self._user_id = ""
        self._conversation_id = ""

    @property
    def name(self) -> str:
        return "manage_variable"

    @property
    def description(self) -> str:
        return (
            "Get, list, set, or delete plaintext PawFlow variables in the "
            "current user or conversation scope. Use store_secret for "
            "credentials and other sensitive values."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["get", "list", "set", "delete"],
                    "description": "Variable operation to perform.",
                },
                "scope": {
                    "type": "string",
                    "enum": ["user", "conversation"],
                    "default": "user",
                    "description": "Persistence scope. Defaults to user.",
                },
                "name": {
                    "type": "string",
                    "description": (
                        "Variable name. Required except for list; supports "
                        "namespaces such as comfyui.default_relay."
                    ),
                },
                "value": {
                    "description": (
                        "Value for set. Strings are stored verbatim; other JSON "
                        "values are stored as compact JSON."
                    ),
                },
            },
            "required": ["action"],
        }

    def set_user_id(self, uid: str) -> None:
        self._user_id = str(uid or "")

    def set_conversation_id(self, cid: str) -> None:
        self._conversation_id = str(cid or "")

    @staticmethod
    def _validate_name(name: str, action: str) -> str:
        name = str(name or "").strip()
        if action == "list" and not name:
            return ""
        if not _VARIABLE_NAME_RE.fullmatch(name):
            raise ValueError(
                "name must start with a letter and contain only letters, "
                "digits, '.', '_' or '-' (maximum 128 characters)"
            )
        return name

    def _load(self, scope: str) -> dict[str, Any]:
        if scope == "user":
            if not self._user_id:
                raise ValueError("user scope requires the current user context")
            from core.config_store import ConfigStore
            from core.paths import user_params_path
            return ConfigStore.load_params(user_params_path(self._user_id))

        if not self._conversation_id:
            raise ValueError(
                "conversation scope requires the current conversation context")
        from core.conversation_store import ConversationStore
        values = ConversationStore.instance().get_extra(
            self._conversation_id, "conv_parameters") or {}
        if not isinstance(values, dict):
            raise TypeError("conversation parameters are not a JSON object")
        return values

    def _save(self, scope: str, values: dict[str, Any]) -> None:
        if scope == "user":
            from core.config_store import ConfigStore
            from core.paths import user_params_path
            ConfigStore.save_params(user_params_path(self._user_id), values)
            return

        from core.conversation_store import ConversationStore
        if not ConversationStore.instance().set_extra(
                self._conversation_id, "conv_parameters", values):
            raise ValueError("current conversation does not exist")

    def execute(self, arguments: dict[str, Any]) -> str:
        action = str(arguments.get("action") or "").strip().lower()
        if action not in {"get", "list", "set", "delete"}:
            raise ValueError("action must be get, list, set, or delete")
        scope = str(arguments.get("scope") or "user").strip().lower()
        if scope not in {"user", "conversation"}:
            raise ValueError("scope must be user or conversation")
        name = self._validate_name(arguments.get("name", ""), action)
        values = self._load(scope)

        if action == "list":
            payload = {
                key: str(value)
                for key, value in sorted(values.items())
            }
            return json.dumps({
                "action": action, "scope": scope, "variables": payload,
            }, ensure_ascii=False, sort_keys=True)

        if action == "get":
            found = name in values
            return json.dumps({
                "action": action,
                "scope": scope,
                "name": name,
                "found": found,
                "value": str(values[name]) if found else None,
            }, ensure_ascii=False, sort_keys=True)

        if action == "set":
            if "value" not in arguments:
                raise ValueError("value is required for set")
            value = _stored_value(arguments["value"])
            if scope == "user":
                from core.config_value import ConfigValue
                values[name] = ConfigValue(value=value)
            else:
                values[name] = value
            self._save(scope, values)
            return json.dumps({
                "action": action, "scope": scope, "name": name,
                "value": value,
            }, ensure_ascii=False, sort_keys=True)

        deleted = name in values
        values.pop(name, None)
        if deleted:
            self._save(scope, values)
        return json.dumps({
            "action": action, "scope": scope, "name": name,
            "deleted": deleted,
        }, ensure_ascii=False, sort_keys=True)
