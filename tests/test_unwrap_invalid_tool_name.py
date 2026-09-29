"""unwrap_mcp_tool must not surface a malformed inner tool_name.

Regression: a DeepSeek agent leaked its DSML tool-call markup into
use_tool's tool_name, and the Active Agents panel displayed the whole
markup + JSON payload as the agent's last tool.
"""
from core.llm_client import unwrap_mcp_tool

_LEAKED = ('bash"> <\uff5cDSML\uff5cparameter name="arguments_json" '
           'string="false">{"relay":"Ultima7D","command":"ls"}')


def test_use_tool_with_leaked_markup_keeps_wrapper_name():
    name, _ = unwrap_mcp_tool("use_tool", {"tool_name": _LEAKED})
    assert name == "use_tool"


def test_mcp_use_tool_with_leaked_markup_keeps_wrapper_name():
    name, _ = unwrap_mcp_tool("mcp__pawflow__use_tool", {"tool_name": _LEAKED})
    assert name == "mcp__pawflow__use_tool"


def test_call_mcp_tool_with_leaked_markup_keeps_wrapper_name():
    name, _ = unwrap_mcp_tool("call_mcp_tool", {"ToolName": _LEAKED})
    assert name == "call_mcp_tool"


def test_non_string_tool_name_keeps_wrapper_name():
    name, _ = unwrap_mcp_tool("use_tool", {"tool_name": ["bash"]})
    assert name == "use_tool"


def test_valid_names_still_unwrap():
    assert unwrap_mcp_tool(
        "use_tool", {"tool_name": "bash", "arguments_json": '{"command": "ls"}'}
    ) == ("bash", {"command": "ls"})
    assert unwrap_mcp_tool(
        "use_tool", {"tool_name": "shell", "arguments": {}})[0] == "bash"
    assert unwrap_mcp_tool(
        "use_tool", {"tool_name": "mcp__github__get-issue", "arguments": {}}
    )[0] == "mcp__github__get-issue"
