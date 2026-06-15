"""Tests for CCI observed-tool normalization, incl. tolerant inner decode.

Regression: use_tool-wrapped calls carry the real tool input doubly-encoded in
the `arguments_json` STRING. When the observed stream is cut at EOF the inner
string can be truncated; strict parsing dropped it to {} so the call rendered
with empty parens (e.g. a bare `Bash()` for a large multi-line command). The
inner decode now recovers truncated JSON the same way the provider recovers the
outer wrapper.
"""

from tools.cc_interactive_filters import normalize_observed_tool


def test_use_tool_bash_complete_inner():
    name, args = normalize_observed_tool(
        "mcp__pawflow__use_tool",
        {"tool_name": "bash", "arguments_json": '{"command": "ls -la"}'})
    assert name == "bash"
    assert args == {"command": "ls -la"}


def test_use_tool_bash_truncated_inner_recovered():
    # arguments_json string cut at EOF (closing brace missing) — previously {}.
    name, args = normalize_observed_tool(
        "mcp__pawflow__use_tool",
        {"tool_name": "bash", "arguments_json": '{"command": "git status --short"'})
    assert name == "bash"
    assert args.get("command") == "git status --short"


def test_use_tool_unrecoverable_inner_falls_back_empty():
    name, args = normalize_observed_tool(
        "mcp__pawflow__use_tool",
        {"tool_name": "bash", "arguments_json": "not json at all"})
    assert name == "bash"
    assert args == {}


def test_native_tool_passthrough_unchanged():
    name, args = normalize_observed_tool("Bash", {"command": "echo hi"})
    assert name == "Bash"
    assert args == {"command": "echo hi"}


def test_legacy_arguments_object_still_supported():
    name, args = normalize_observed_tool(
        "mcp__pawflow__use_tool",
        {"tool_name": "edit", "arguments": {"path": "a.py"}})
    assert name == "edit"
    assert args == {"path": "a.py"}
