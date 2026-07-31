"""Tests for CCI observed-tool normalization, incl. tolerant inner decode.

Regression: use_tool-wrapped calls carry the real tool input doubly-encoded in
the `arguments_json` STRING. When the observed stream is cut at EOF the inner
string can be truncated; strict parsing dropped it to {} so the call rendered
with empty parens (e.g. a bare `Bash()` for a large multi-line command). The
inner decode now recovers truncated JSON the same way the provider recovers the
outer wrapper.
"""

from tools.cc_interactive_filters import (
    code_mode_body, normalize_observed_tool, observed_tool_origin)


# A code-mode harness reaches PawFlow from inside the JavaScript it runs in
# its freeform `exec` tool, so the call is an MCP `read` wearing a native name.
_CODE_MODE_READ = (
    'const r=await tools.mcp__pawflow__use_tool({tool_name:"read",'
    'arguments_json:"{\\"path\\":\\"/workspace/a.py\\",\\"offset\\":241,'
    '\\"limit\\":2000}"});'
    'for(const c of r.content||[])text(c.text)')


def test_a_body_that_calls_tools_is_recognised_as_code_mode():
    assert code_mode_body({"input": _CODE_MODE_READ}) is True
    assert code_mode_body(
        {"input": 'await tools.exec_command({cmd:"ls"})'}) is True


def test_the_shapes_a_source_parser_choked_on_are_still_code_mode():
    # Property shorthand, an array of calls driven by a loop, a filter over a
    # tool list: ordinary code-mode, and none of it a JS literal at the call
    # site. Recognising the body is all that is asked of the source now — what
    # it ran is reported by the relay that executed it.
    for body in (
            "tools.mcp__pawflow__get_tool_schema({tool_name})",
            'const calls=[["read",{path:"a"}]];'
            "for(const [t,a] of calls) await tools.mcp__pawflow__use_tool("
            "{tool_name:t,arguments_json:JSON.stringify(a)});",
            "await Promise.all(names.map(n=>tools.mcp__pawflow__use_tool(n)))",
    ):
        assert code_mode_body({"input": body}) is True


def test_a_body_with_no_tool_call_is_not_code_mode():
    code = 'text("nothing to call here")'

    assert code_mode_body({"input": code}) is False
    # ... and it keeps its own name and code, like any other native call.
    name, args = normalize_observed_tool("exec", {"input": code})
    assert name == "exec"
    assert args == {"input": code}


def test_an_ordinary_call_is_never_code_mode():
    assert code_mode_body({"command": "ls -la"}) is False
    assert code_mode_body(None) is False


def test_origin_is_read_from_the_name_alone():
    assert observed_tool_origin("mcp__pawflow__use_tool") == "mcp"
    assert observed_tool_origin("exec") == "native"


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
