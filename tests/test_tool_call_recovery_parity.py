"""Every tool-call path recovers identically (execution + display + unwrap).

After the unification (u1/u2) the display/persistence unwrap family routes
through core.tool_json.parse_tool_arguments, so a truncated arguments_json is
recovered the SAME way on every provider and on the execution path -- including
mid-string truncation (Python reports an unterminated string at its opening
quote, which the canonical parser now autocloses).
"""

import pathlib

from core.tool_json import parse_tool_arguments
from core.llm_client import unwrap_mcp_tool
from core.llm_providers.claude_code_interactive import _loads_tolerant
from tools.cc_interactive_filters import (
    normalize_observed_tool, _loads_tolerant_str)

ROOT = pathlib.Path(__file__).resolve().parents[1]

_TRUNCATED = [
    '{"command": "echo hi',          # unterminated string, far from EOF
    '{"path":"/x","old_string":"a',  # unterminated string at EOF
    '{"command":"ls -la',            # unterminated string
]


def test_unterminated_string_truncation_recovers():
    assert parse_tool_arguments('{"command": "echo hi', tool_name="bash") == {
        "command": "echo hi"}


def test_all_paths_recover_truncation_identically():
    for raw in _TRUNCATED:
        expected = parse_tool_arguments(raw, tool_name="x")
        assert isinstance(expected, dict) and expected, raw
        assert _loads_tolerant(raw) == expected, raw
        assert _loads_tolerant_str(raw) == expected, raw
        _, u_args = unwrap_mcp_tool(
            "use_tool", {"tool_name": "bash", "arguments_json": raw})
        assert u_args == expected, raw
        _, n_args = normalize_observed_tool(
            "use_tool", {"tool_name": "bash", "arguments_json": raw})
        assert n_args == expected, raw


def _unwrap_source():
    """Slice just the unwrap_mcp_tool function body from llm_client.py."""
    src = (ROOT / "core" / "llm_client.py").read_text()
    start = src.index("def unwrap_mcp_tool(")
    rest = src[start:]
    nxt = rest.index("\ndef ", 1)
    return rest[:nxt]


def test_unwrap_routes_through_shared_decoder():
    src = (ROOT / "core" / "llm_client.py").read_text()
    assert "_decode_str_arg" in src
    # the old per-branch inline json.loads inside unwrap_mcp_tool is gone --
    # every inner decode now routes through _decode_str_arg. (The strict
    # json.loads in has_complete_mcp_tool_call is a different, completeness
    # probe and intentionally stays strict, so scope the check to unwrap.)
    body = _unwrap_source()
    assert "json.loads" not in body
    assert "_decode_str_arg" in body
