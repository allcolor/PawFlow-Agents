"""Dynamic Tool Handler — tools created at runtime by LLMs.

The LLM defines a tool (name, description, parameters, Python code).
The code executes on the relay (or sandbox), same as execute_script.
Parameters are injected as local variables in the code.
"""

import json
import logging
from typing import Dict, Any

from core.tool_handler import ToolHandler

logger = logging.getLogger(__name__)


class DynamicToolHandler(ToolHandler):
    """A tool defined at runtime by Python code."""

    def __init__(self, tool_name: str, tool_description: str,
                 tool_parameters: Dict[str, Any], code: str):
        self._name = tool_name
        self._description = tool_description
        self._parameters = {
            "type": "object",
            "properties": tool_parameters,
            "required": [k for k, v in tool_parameters.items()
                         if v.get("required", False)],
        }
        self._code = code
        self._is_dynamic = True

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return self._parameters

    def execute(self, arguments: Dict[str, Any]) -> str:
        # Build code: inject parameters as local variables + user code
        lines = []
        for k, v in arguments.items():
            if k.startswith("_"):
                continue
            lines.append(f"{k} = {repr(v)}")
        lines.append("")
        lines.append(self._code)
        full_code = "\n".join(lines)

        # Execute via the live execute_script handler (already wired with relay)
        from core.tool_registry import ToolRegistry
        registry = ToolRegistry._live_registry
        runner = registry.get("execute_script") if registry else None
        if runner is None:
            # Fallback: create bare handler (no relay — sandbox only)
            from core.handlers.web_fetch import ExecuteScriptHandler
            runner = ExecuteScriptHandler()
        run_args = {"code": full_code}
        if arguments.get("_secret_env"):
            run_args["_secret_env"] = arguments["_secret_env"]
        return runner.execute(run_args)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for storage."""
        return {
            "name": self._name,
            "description": self._description,
            "parameters": self._parameters.get("properties", {}),
            "code": self._code,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DynamicToolHandler":
        """Deserialize from storage."""
        return cls(
            tool_name=data["name"],
            tool_description=data.get("description", ""),
            tool_parameters=data.get("parameters", {}),
            code=data.get("code", ""),
        )


class CreateToolHandler(ToolHandler):
    """Meta-tool: create a dynamic tool at runtime."""

    def __init__(self):
        self._user_id = ""
        self._conversation_id = ""

    def set_user_id(self, uid: str):
        self._user_id = uid

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid

    @property
    def name(self) -> str:
        return "create_tool"

    @property
    def description(self) -> str:
        return (
            "Create a new tool from Python code. The tool becomes immediately "
            "available for use. Parameters are injected as local variables in "
            "the code. The code has access to env vars ($VAR) and can use "
            "imports (os, json, urllib, etc). Use print() for output."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "tool_name": {
                    "type": "string",
                    "description": "Unique name for the tool (snake_case)",
                },
                "tool_description": {
                    "type": "string",
                    "description": "Description of what the tool does (shown to LLMs)",
                },
                "parameters": {
                    "type": "object",
                    "description": (
                        "Tool parameters as JSON Schema properties. "
                        "Example: {\"post_id\": {\"type\": \"string\", \"description\": \"Post ID\"}}"
                    ),
                },
                "code": {
                    "type": "string",
                    "description": (
                        "Python code to execute. Tool parameters are available as "
                        "local variables. Use print() to return output."
                    ),
                },
            },
            "required": ["tool_name", "tool_description", "code"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        tool_name = arguments.get("tool_name", "").strip()
        tool_description = arguments.get("tool_description", "")
        parameters = arguments.get("parameters", {})
        code = arguments.get("code", "")

        if not tool_name:
            return "Error: tool_name is required"
        if not code:
            return "Error: code is required"
        # Validate name
        if not tool_name.replace("_", "").isalnum():
            return "Error: tool_name must be alphanumeric with underscores"

        # Create and register
        handler = DynamicToolHandler(tool_name, tool_description,
                                     parameters or {}, code)
        from core.tool_registry import ToolRegistry
        registry = ToolRegistry._live_registry
        if registry is None:
            return "Error: no active tool registry"
        if registry.get(tool_name) and not getattr(registry.get(tool_name), '_is_dynamic', False):
            return f"Error: tool '{tool_name}' already exists (builtin — cannot override)"
        registry.register(handler)

        # Persist to conversation
        self._persist(tool_name, handler, arguments)

        param_names = list((parameters or {}).keys())
        return (f"Tool '{tool_name}' created and registered. "
                f"Parameters: {param_names or 'none'}. "
                f"Available immediately for use.")

    def _persist(self, tool_name: str, handler: DynamicToolHandler,
                 arguments: Dict[str, Any]):
        """Save to conversation extras for reload."""
        try:
            cid = getattr(self, '_conversation_id', '')
            uid = getattr(self, '_user_id', '')
            if not cid:
                return
            from core.conversation_store import ConversationStore
            store = ConversationStore.instance()
            tools = store.get_extra(cid, "dynamic_tools") or {}
            tools[tool_name] = handler.to_dict()
            store.set_extra(cid, "dynamic_tools", tools, user_id=uid)
        except Exception as e:
            logger.warning("Failed to persist dynamic tool '%s': %s", tool_name, e)


class DeleteToolHandler(ToolHandler):
    """Meta-tool: delete a dynamic tool."""

    def __init__(self):
        self._user_id = ""
        self._conversation_id = ""

    def set_user_id(self, uid: str):
        self._user_id = uid

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid

    @property
    def name(self) -> str:
        return "delete_tool"

    @property
    def description(self) -> str:
        return "Delete a previously created dynamic tool."

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "tool_name": {
                    "type": "string",
                    "description": "Name of the tool to delete",
                },
            },
            "required": ["tool_name"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        tool_name = arguments.get("tool_name", "").strip()
        if not tool_name:
            return "Error: tool_name is required"

        from core.tool_registry import ToolRegistry
        registry = ToolRegistry._live_registry
        if registry is None:
            return "Error: no active tool registry"

        handler = registry.get(tool_name)
        if not handler:
            return f"Error: tool '{tool_name}' not found"
        if not getattr(handler, '_is_dynamic', False):
            return f"Error: tool '{tool_name}' is a builtin — cannot delete"

        registry.unregister(tool_name)

        # Remove from conversation extras
        try:
            cid = getattr(self, '_conversation_id', '')
            uid = getattr(self, '_user_id', '')
            if cid:
                from core.conversation_store import ConversationStore
                store = ConversationStore.instance()
                tools = store.get_extra(cid, "dynamic_tools") or {}
                tools.pop(tool_name, None)
                store.set_extra(cid, "dynamic_tools", tools, user_id=uid)
        except Exception as e:
            logger.warning("Failed to remove dynamic tool '%s' from store: %s",
                           tool_name, e)

        return f"Tool '{tool_name}' deleted."


def load_dynamic_tools(cid: str, registry):
    """Load dynamic tools from conversation extras into the registry."""
    try:
        from core.conversation_store import ConversationStore
        store = ConversationStore.instance()
        tools = store.get_extra(cid, "dynamic_tools") or {}
        loaded = 0
        for name, data in tools.items():
            if registry.get(name) and not getattr(registry.get(name), '_is_dynamic', False):
                continue  # don't override builtins
            handler = DynamicToolHandler.from_dict(data)
            registry.register(handler)
            loaded += 1
        if loaded:
            logger.info("[dynamic_tools] Loaded %d tool(s) for conv %s", loaded, cid[:8])
    except Exception as e:
        logger.warning("[dynamic_tools] Failed to load for conv %s: %s", cid[:8], e)
