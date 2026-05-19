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


class PfpToolProxyHandler(ToolHandler):
    """Runtime proxy for a package tool executed through the relay PFP runner."""

    def __init__(self, tool_name: str, tool_description: str,
                 tool_parameters: Dict[str, Any], package_runtime: Dict[str, Any],
                 installed_from: Dict[str, Any] | None = None):
        self._name = tool_name
        self._description = tool_description
        self._parameters = _as_json_schema(tool_parameters)
        self._package_runtime = package_runtime or {}
        self._installed_from = installed_from or {}
        self._user_id = ""
        self._conversation_id = ""
        self._agent_name = ""
        self._is_dynamic = True
        self._is_pfp_tool = True

    def set_user_id(self, uid: str):
        self._user_id = uid or ""

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid or ""

    def set_agent_name(self, agent_name: str):
        self._agent_name = agent_name or ""

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
        from core import pfp_runtime
        try:
            return pfp_runtime.invoke_tool(
                self._package_runtime, self._installed_from, arguments, {
                    "user_id": self._user_id,
                    "conversation_id": self._conversation_id,
                    "agent_name": self._agent_name,
                    "scope": "conversation" if self._conversation_id else "user",
                })
        except pfp_runtime.PackageRuntimeError as exc:
            return f"Error: {exc}"


def _as_json_schema(parameters: Dict[str, Any]) -> Dict[str, Any]:
    params = parameters or {}
    if params.get("type") == "object" and isinstance(params.get("properties", {}), dict):
        return params
    return {"type": "object", "properties": params}


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
            "Create a new tool at runtime from Python code. The tool is registered\n"
            "immediately and becomes callable by the LLM in the same conversation.\n\n"
            "How it works:\n"
            "  1. You define a tool_name (snake_case), description, optional\n"
            "     parameters (JSON Schema), and the Python code body.\n"
            "  2. When the tool is called, each parameter value is injected as a\n"
            "     local variable (e.g. parameter 'post_id' becomes the variable\n"
            "     post_id in the code).\n"
            "  3. The code executes on the relay (same sandbox as execute_script).\n"
            "     Use print() to produce output returned to the LLM.\n"
            "  4. The tool is persisted to the conversation -- it will be reloaded\n"
            "     automatically if the conversation is resumed later.\n\n"
            "Parameters:\n"
            "  tool_name        -- unique snake_case identifier (alphanumeric + _).\n"
            "  tool_description -- shown to the LLM; describe what the tool does.\n"
            "  parameters       -- JSON Schema properties object (optional).\n"
            "  code             -- Python source. Has access to env vars ($VAR),\n"
            "                      standard imports (os, json, urllib, etc).\n\n"
            "Use this when a task requires a reusable operation that does not exist\n"
            "in the built-in tool set. Cannot override built-in tool names."
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
        parameters = arguments.get("parameters", {}) or {}
        code = arguments.get("code", "")

        if not tool_name:
            return "Error: tool_name is required"
        if not code:
            return "Error: code is required"
        if not tool_name.replace("_", "").isalnum():
            return "Error: tool_name must be alphanumeric with underscores"

        # Static + sandbox validation — same gate every create path uses.
        from core.tool_validation import validate_and_load
        try:
            validate_and_load(code)
        except ValueError as e:
            return f"Error: {e}"

        from core.tool_registry import ToolRegistry
        registry = ToolRegistry._live_registry
        if registry is None:
            return "Error: no active tool registry"
        existing = registry.get(tool_name)
        if existing and not getattr(existing, "_is_dynamic", False):
            return f"Error: tool '{tool_name}' already exists (builtin — cannot override)"

        # Persist to ResourceStore (conv-scoped — tools created at runtime
        # belong to their conversation by default).
        cid = getattr(self, "_conversation_id", "")
        uid = getattr(self, "_user_id", "")
        if not cid:
            return "Error: no conversation context for create_tool"
        try:
            from core.resource_store import ResourceStore
            data = {
                "source": code,
                "description": tool_description,
                "parameters": parameters,
            }
            try:
                ResourceStore.instance().create(
                    "tool", tool_name, uid, data, conversation_id=cid)
            except ValueError:
                ResourceStore.instance().update(
                    "tool", tool_name, uid, data, conversation_id=cid)
        except Exception as e:
            return f"Error: persist failed: {e}"

        handler = DynamicToolHandler(tool_name, tool_description,
                                       parameters, code)
        handler._origin = "dynamic"
        handler._origin_scope = "conversation"
        registry.register(handler)

        param_names = list(parameters.keys())
        return (f"Tool '{tool_name}' created and registered. "
                f"Parameters: {param_names or 'none'}. "
                f"Available immediately for use.")


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

        # Remove from ResourceStore — search conv first (most likely
        # location for runtime-created tools), then user. Global tools
        # are not deletable via this path (use manage_resource).
        cid = getattr(self, "_conversation_id", "")
        uid = getattr(self, "_user_id", "")
        try:
            from core.resource_store import ResourceStore
            rs = ResourceStore.instance()
            removed = False
            if cid and uid:
                try:
                    removed = rs.delete("tool", tool_name, uid,
                                         conversation_id=cid)
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            if not removed and uid:
                try:
                    rs.delete("tool", tool_name, uid)
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        except Exception as e:
            logger.warning("Failed to remove dynamic tool '%s' from store: %s",
                           tool_name, e)

        return f"Tool '{tool_name}' deleted."
