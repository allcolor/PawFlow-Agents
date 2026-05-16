"""Shared filters for Claude Code interactive transcript observations."""

from __future__ import annotations


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
