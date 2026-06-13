"""Shared filters for Claude Code interactive transcript observations."""

from __future__ import annotations

import json


_USE_TOOL_WRAPPERS = {
    "mcp__pawflow__use_tool", "mcp__pawflow__.use_tool",
    "mcp_pawflow_use_tool", "mcp_pawflow.use_tool",
    "pawflow.use_tool", "pawflow/use_tool", "use_tool",
}
_SCHEMA_WRAPPERS = {
    "mcp__pawflow__get_tool_schema",
    "mcp__pawflow__.get_tool_schema",
    "mcp_pawflow_get_tool_schema",
    "mcp_pawflow.get_tool_schema",
    "pawflow.get_tool_schema",
    "get_tool_schema",
}


def _json_dict(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return {}
    return value if isinstance(value, dict) else {}


def normalize_observed_tool(name: str, args) -> tuple[str, dict]:
    """Return the PawFlow tool name/args users should see for CCI events."""
    raw_name = name or ""
    tool_args = _json_dict(args)
    if raw_name in _USE_TOOL_WRAPPERS:
        if "tool_name" not in tool_args and isinstance(tool_args.get("parameters"), dict):
            tool_args = tool_args["parameters"]
        inner_name = str(tool_args.get("tool_name") or raw_name)
        # Source order mirrors the MCP bridge reader: the advertised string
        # `arguments_json` first (CCI sends it; _json_dict decodes the string),
        # then a legacy `arguments`/`parameters` object. Without this, CCI args
        # (now carried in arguments_json) render as empty parens.
        inner_raw = tool_args.get("arguments_json")
        if inner_raw is None or inner_raw == "":
            inner_raw = tool_args.get("arguments", tool_args.get("parameters", {}))
        inner_args = _json_dict(inner_raw)
        return inner_name, inner_args
    if raw_name in _SCHEMA_WRAPPERS:
        return "get_tool_schema", tool_args
    return raw_name, tool_args


def is_hidden_native_tool(name: str, args: dict) -> bool:
    """Hide Claude Code bootstrap/discovery tools from PawFlow transcripts."""
    tool = (name or "").lower().replace("_", "")
    if tool in {"getschema", "toolsearch", "toolschema", "listtools"}:
        return True
    if tool == "read":
        path = str(args.get("file_path") or args.get("path") or "")
        normalized = path.replace("\\", "/")
        return normalized.endswith("/.pawflow_cci/initial_context.md")
    return False
