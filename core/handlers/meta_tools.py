"""Meta-tools for lazy tool loading — get_tool_schema + use_tool.

API providers (non-claude-code) receive only these 2 tools instead of the
full tool catalog.  The LLM discovers real tool schemas on demand via
get_tool_schema, then executes them via use_tool.
"""

import json
import logging
from typing import Dict, Any

from core.tool_handler import ToolHandler

logger = logging.getLogger(__name__)


class GetToolSchemaHandler(ToolHandler):
    """Return the full JSON schema of a tool so the LLM can call it via use_tool."""

    def __init__(self, registry: "ToolRegistry"):  # noqa: F821
        self._registry = registry

    @property
    def name(self) -> str:
        return "get_tool_schema"

    @property
    def description(self) -> str:
        return "Get the JSON schema for a tool by name. Call this before using a tool to learn its parameters."

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "tool_name": {"type": "string", "description": "Name of the tool to inspect"},
            },
            "required": ["tool_name"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        name = arguments.get("tool_name", "")
        handler = self._registry.get(name)
        if not handler:
            available = [h.name for h in self._registry.list_tools()
                         if h.name not in ("get_tool_schema", "use_tool")]
            return json.dumps({"error": f"Unknown tool '{name}'",
                               "available": available})
        return json.dumps({
            "name": handler.name,
            "display_name": handler.display_name,
            "description": handler.description,
            "parameters": handler.parameters_schema,
        }, indent=2)


class UseToolHandler(ToolHandler):
    """Execute any tool by name. The LLM should call get_tool_schema first."""

    def __init__(self, registry: "ToolRegistry"):  # noqa: F821
        self._registry = registry

    @property
    def name(self) -> str:
        return "use_tool"

    @property
    def description(self) -> str:
        return "Execute a tool by name with the given arguments. Call get_tool_schema first to know the parameters."

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "tool_name": {"type": "string", "description": "Name of the tool to execute"},
                "arguments": {"type": "object", "description": "Arguments to pass to the tool"},
            },
            "required": ["tool_name", "arguments"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        tool_name = arguments.get("tool_name", "")
        tool_args = arguments.get("arguments", {})
        # LLM sometimes sends arguments as JSON string instead of dict
        # (can be double-encoded -- keep parsing until we get a dict)
        for _ in range(3):  # max 3 levels of JSON encoding
            if isinstance(tool_args, str):
                try:
                    tool_args = json.loads(tool_args)
                except (json.JSONDecodeError, TypeError):
                    return f"Error: invalid arguments format for '{tool_name}' -- expected JSON object, got string: {tool_args[:200]}"
            else:
                break
        if not isinstance(tool_args, dict):
            return f"Error: arguments for '{tool_name}' must be a JSON object, got {type(tool_args).__name__}"
        if tool_name in ("get_tool_schema", "use_tool"):
            return (f"Error: '{tool_name}' is a meta-tool -- call it directly "
                    f"as a top-level tool call, not via use_tool.")
        # Validate arguments against tool schema
        handler = self._registry.get(tool_name)
        if handler:
            schema = handler.parameters_schema or {}
            props = schema.get("properties", {})
            if props and isinstance(tool_args, dict):
                unknown = [k for k in tool_args if k not in props]
                if unknown:
                    valid = list(props.keys())
                    return (f"Error: unknown argument(s) {unknown} for tool '{tool_name}'. "
                            f"Valid arguments: {valid}. "
                            f"Use get_tool_schema(tool_name='{tool_name}') to see full schema.")
        return self._registry.execute(tool_name, tool_args)
