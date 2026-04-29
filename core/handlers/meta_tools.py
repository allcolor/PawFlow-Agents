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


_TOOL_ALIASES = {
    "read_file": "read",
}


def _canonical_tool_name(name: str) -> str:
    if not isinstance(name, str):
        return ""
    return _TOOL_ALIASES.get(name) or _TOOL_ALIASES.get(name.lower()) or name


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
        name = _canonical_tool_name(name)
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
        # LLM key aliasing: smaller models routinely invent variants of
        # the schema (name/params instead of tool_name/arguments,
        # nesting under an extra 'arguments' wrapper, etc.). Accept the
        # common ones so the call doesn't waste a turn on a cryptic
        # 'unknown tool ' error and force a re-discovery loop.
        if isinstance(arguments, dict):
            # Some LLMs nest the real payload one level too deep:
            # use_tool(arguments={"arguments": {...real...}})
            # Detect: outer dict has only an 'arguments' key whose value
            # is itself a dict containing the actual tool spec.
            if (set(arguments.keys()) == {"arguments"}
                    and isinstance(arguments["arguments"], dict)):
                arguments = arguments["arguments"]
        tool_name = (arguments.get("tool_name")
                     or arguments.get("name")
                     or arguments.get("tool")
                     or "")
        tool_args = arguments.get("arguments")
        if tool_args is None:
            tool_args = arguments.get("params")
        if tool_args is None:
            tool_args = arguments.get("input")
        if tool_args is None:
            tool_args = {}
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
        def _normalize_tool_name(name: Any) -> str:
            if isinstance(name, str) and name.startswith("mcp__pawflow__"):
                name = name[len("mcp__pawflow__"):]
            return _canonical_tool_name(name)

        tool_name = _normalize_tool_name(tool_name)

        # Recover the common nested meta-tool mistake:
        # use_tool(tool_name="use_tool", arguments={"tool_name": "read", ...})
        # This is mechanically unambiguous, so unwrap it instead of making
        # the model spend another turn correcting the envelope.
        unwrap_budget = 3
        while tool_name == "use_tool" and isinstance(tool_args, dict) and unwrap_budget > 0:
            nested_name = (tool_args.get("tool_name")
                           or tool_args.get("name")
                           or tool_args.get("tool")
                           or "")
            nested_args = tool_args.get("arguments")
            if nested_args is None:
                nested_args = tool_args.get("params")
            if nested_args is None:
                nested_args = tool_args.get("input")
            if nested_args is None:
                nested_args = {}

            for _ in range(3):
                if isinstance(nested_args, str):
                    try:
                        nested_args = json.loads(nested_args)
                    except (json.JSONDecodeError, TypeError):
                        return (f"Error: invalid arguments format for '{nested_name}' -- "
                                f"expected JSON object, got string: {nested_args[:200]}")
                else:
                    break
            if not isinstance(nested_args, dict):
                return (f"Error: arguments for '{nested_name}' must be a JSON object, "
                        f"got {type(nested_args).__name__}")
            if not nested_name:
                break
            tool_name = _normalize_tool_name(nested_name)
            tool_args = nested_args
            unwrap_budget -= 1

        if not tool_name:
            return (
                "Error: missing 'tool_name' in use_tool arguments. "
                "Expected: use_tool(tool_name='<name>', arguments={...}). "
                "Got keys: " + str(sorted(arguments.keys()))
            )
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
