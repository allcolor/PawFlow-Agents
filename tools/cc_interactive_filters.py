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


def _loads_tolerant_str(raw: str) -> dict:
    """Best-effort parse of an EOF-truncated tool-input JSON string.

    A use_tool wrapper carries the real tool input doubly-encoded in the
    `arguments_json` STRING. When the observed CCI stream is cut at EOF, the
    provider recovers the OUTER wrapper, but this inner string can still be
    truncated — strict json.loads then drops the args to {} and the call
    renders with empty parens (worst for large inputs like a multi-line bash
    `command`). Mirror the provider's outer recovery here so the inner input
    is recovered too. Lazy import keeps the dependency-light runtime/proxy
    copies degrading gracefully to {} when core is not importable.
    """
    try:
        from core.tool_json import parse_tool_arguments, tool_argument_parse_error
    except Exception:
        return {}
    parsed = parse_tool_arguments(raw, tool_name="cci-display", provider="cci")
    if isinstance(parsed, dict) and not tool_argument_parse_error(parsed):
        return parsed
    return {}


def _json_dict(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return _loads_tolerant_str(value)
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


def observed_tool_origin(name: str) -> str:
    """Classify an observed CCI tool by origin for the UI badge.

    PawFlow tools reach Claude Code through the MCP bridge (the use_tool /
    get_tool_schema wrappers) -> "mcp". Everything else is one of Claude Code's
    own built-in tools -> "native". Mirrors Codex's native/mcp tagging so both
    providers render the same badges. Pass the RAW observed name (before
    normalize_observed_tool unwraps it).
    """
    raw_name = name or ""
    if raw_name in _USE_TOOL_WRAPPERS or raw_name in _SCHEMA_WRAPPERS:
        return "mcp"
    return "native"


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
