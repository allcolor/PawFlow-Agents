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
        return (
            "Retrieve the full JSON schema (name, description, parameters) for a\n"
            "tool by name. This is the first step in the lazy tool discovery pattern.\n\n"
            "Instead of receiving all tool schemas upfront (which can exceed context\n"
            "limits), you start with only get_tool_schema and use_tool. Use this\n"
            "tool to inspect any tool's parameters BEFORE calling it via use_tool.\n\n"
            "Parameters:\n"
            "  tool_name -- exact name of the tool to inspect.\n\n"
            "Returns the tool's name, display_name, description, and full parameter\n"
            "schema. If the tool does not exist, returns the list of all available\n"
            "tool names so you can pick the right one.\n\n"
            "Workflow: get_tool_schema(tool_name='X') -> read the schema ->\n"
            "use_tool(tool_name='X', arguments={...}) with correct arguments."
        )

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
        # LLMs sometimes prefix the MCP namespace by mistake (they call
        # use_tool(tool_name="mcp__pawflow__get_tool_schema") instead of
        # calling get_tool_schema directly). Strip the prefix and — if
        # the caller was trying to reach get_tool_schema or use_tool —
        # point them at the direct tool.
        _raw = name
        if name.startswith("mcp__pawflow__"):
            name = name[len("mcp__pawflow__"):]
        if name in ("get_tool_schema", "use_tool"):
            return json.dumps({
                "error": (f"'{_raw}' is an MCP dispatcher — call "
                          f"{name} directly (it's already exposed as "
                          f"its own tool), don't go through use_tool."),
            })
        handler = self._registry.get(name)
        if not handler:
            available = [h.name for h in self._registry.list_tools()
                         if h.name not in ("get_tool_schema", "use_tool")]
            return json.dumps({"error": f"Unknown tool '{_raw}'",
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
        return (
            "Execute any registered tool by name, passing arguments as a JSON object.\n"
            "This is the execution half of the lazy tool discovery pattern.\n\n"
            "IMPORTANT: Always call get_tool_schema first to learn the tool's\n"
            "parameter schema. Passing unknown or mistyped arguments will return an\n"
            "error listing the valid parameter names.\n\n"
            "Parameters:\n"
            "  tool_name  -- exact name of the tool to execute.\n"
            "  arguments  -- JSON object of arguments matching the tool's schema.\n\n"
            "The tool executes with the same permissions and context as a direct\n"
            "tool call. Results are returned as-is from the underlying handler.\n\n"
            "You cannot call get_tool_schema or use_tool recursively through this\n"
            "tool -- they must be called as top-level tool calls.\n\n"
            "Workflow: get_tool_schema(tool_name='X') -> read the schema ->\n"
            "use_tool(tool_name='X', arguments={...})."
        )

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
        # Strip the mcp__pawflow__ prefix if the LLM included it —
        # inside use_tool, `tool_name` is the BARE PawFlow name.
        if tool_name.startswith("mcp__pawflow__"):
            tool_name = tool_name[len("mcp__pawflow__"):]
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
