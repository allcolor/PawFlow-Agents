"""Regression guard: the permission gate authorizes the UNWRAPPED inner tool.

ToolSearch providers call `use_tool`/`get_tool_schema` wrappers. The approval
gate must decide on the inner tool (fetch/bash/...) with its real arguments, not
on the literal wrapper name — otherwise every lazy-provider call is gated on
`use_tool`, denied with no human to approve, and content-aware checks (dangerous
bash, protected paths) look at the wrapper's empty args and miss the real
command. See tasks/ai/agent_tool_exec.py.
"""

from pathlib import Path


def test_gate_unwraps_use_tool_and_always_allows_schema_plumbing():
    src = Path("tasks/ai/agent_tool_exec.py").read_text(encoding="utf-8")

    # Unwrap helper + schema-wrapper set are imported into the gate.
    assert "unwrap_mcp_tool as _unwrap_perm" in src
    assert "_MCP_SCHEMA_WRAPPERS as _SCHEMA_WRAPPERS" in src
    assert "_always_allow_plumbing" in src

    # Decisions run on the unwrapped inner tool + args.
    assert '_tperms.get(_eff_name' in src
    assert 'arguments=_eff_args' in src

    # The old conflation (deciding on the wrapper name/args) must be gone.
    assert '_tperms.get(tc.name' not in src
    assert 'arguments=tc.arguments,\n                    agent_name=_agent_key' not in src


def test_unwrap_mcp_tool_resolves_inner_tool():
    from core.llm_client import unwrap_mcp_tool, _MCP_SCHEMA_WRAPPERS

    name, args = unwrap_mcp_tool(
        "use_tool", {"tool_name": "fetch", "arguments": {"url": "http://x"}})
    assert name == "fetch"
    assert args == {"url": "http://x"}

    # get_tool_schema is recognised as introspection plumbing.
    assert "get_tool_schema" in _MCP_SCHEMA_WRAPPERS
    assert "mcp__pawflow__get_tool_schema" in _MCP_SCHEMA_WRAPPERS
