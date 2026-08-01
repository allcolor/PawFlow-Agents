"""A code-mode script is plumbing: keep its row, drop its body.

The GPT-5.x "sol" harness calls nothing directly. It runs one freeform ``exec``
item and drives every tool from inside the JavaScript, so a group of MCP calls
is always fronted by a row reading ``exec(const r=await
tools.mcp__pawflow__use_tool...)``. Two costs came with quoting that body:

* on screen it is the aggregator the eye lands on, in front of the rows that
  actually name what ran;
* in the record, a call's arguments are persisted and replayed into the next
  context, so kilobytes of generated JavaScript came back at every bootstrap.

The row stays -- it is the only evidence the turn ran a script -- but it
reports the body's size instead of quoting it. Every other call is untouched:
the elision keys on the code-mode recogniser, not on the tool name.
"""

from core.llm_providers._cci_turn import _CCITurnCoordinator
from core.llm_providers._codex_interactive_turn import (
    _CodexInteractiveTurnCoordinator)

SCRIPT = (
    "const rs=await Promise.all([\n"
    "  tools.mcp__pawflow__use_tool({tool_name:'read',arguments_json:'{}'}),\n"
    "  tools.mcp__pawflow__use_tool({tool_name:'grep',arguments_json:'{}'}),\n"
    "]);\n"
)


def _codex():
    # _displayable_args reads no instance state; the coordinator's __init__
    # wants a live proxy session this test has no business standing up.
    return object.__new__(_CodexInteractiveTurnCoordinator)


def test_code_mode_body_is_replaced_by_its_size():
    out = _codex()._displayable_args("exec", {"input": SCRIPT})
    assert out["input"] == f"<code-mode script, {len(SCRIPT)} chars>"


def test_the_row_survives_the_elision():
    """Elided, not suppressed: a script can reach Codex's own runtime, and
    those calls are executed by no relay and reported by nobody else."""
    out = _codex()._displayable_args("exec", {"input": SCRIPT})
    assert out and "input" in out


def test_sibling_arguments_are_kept():
    out = _codex()._displayable_args(
        "exec", {"input": SCRIPT, "timeout": 30})
    assert out["timeout"] == 30


def test_the_caller_s_dict_is_not_mutated():
    args = {"input": SCRIPT}
    _codex()._displayable_args("exec", args)
    assert args["input"] == SCRIPT


def test_a_real_tool_call_is_untouched():
    args = {"path": "/workspace/core/base_task.py", "offset": 1}
    assert _codex()._displayable_args("read", args) == args


def test_a_script_that_drives_no_tool_is_untouched():
    """Not every freeform body is code-mode. Only a body that reaches the tool
    table is an aggregator standing in for calls rendered elsewhere."""
    args = {"input": "console.log('hello');"}
    assert _codex()._displayable_args("exec", args) == args


def test_other_providers_keep_every_argument():
    """Claude Code and Antigravity call tools directly: there is no aggregator
    row and nothing to elide."""
    base = object.__new__(_CCITurnCoordinator)
    args = {"input": SCRIPT}
    assert base._displayable_args("exec", args) is args


# The other half of the same duplication.
#
# Eliding the body only shrank what the harness SENT. What it got back is the
# aggregate of the very calls the relay reported one by one, so the same bytes
# reached the next context twice: once as each call's own tool result, once
# more inside the script's output. Invisible while the session is warm -- Codex
# never sees the relay's rows -- and paid for real at every cold start, which
# is to say at every compaction restart.

OUTPUT = "x" * 15591


def _coordinator_with_rows(mcp_rows):
    """A coordinator that has emitted one code-mode call, then `mcp_rows`
    relay rows for it -- the shape `_displayable_result` decides on."""
    coord = _codex()
    coord._code_mode_calls = {"call_1": 0}
    coord._mcp_rows_seen = mcp_rows
    return coord


def test_a_script_output_yields_to_the_rows_that_name_it():
    out = _coordinator_with_rows(4)._displayable_result(
        {"id": "call_1"}, OUTPUT)
    assert str(len(OUTPUT)) in out
    assert "4 call(s)" in out
    assert "x" * 100 not in out


def test_a_script_that_reached_no_relay_keeps_its_output():
    """Read with Codex's own runtime, or computed in the script itself: no row
    describes it, so its output is the only record there is."""
    out = _coordinator_with_rows(0)._displayable_result(
        {"id": "call_1"}, OUTPUT)
    assert out == OUTPUT


def test_an_ordinary_call_keeps_its_result():
    coord = _coordinator_with_rows(4)
    assert coord._displayable_result({"id": "other"}, OUTPUT) == OUTPUT


def test_only_the_rows_this_script_produced_are_counted():
    """A turn is a sequence of scripts. A later one that drove nothing must not
    be elided on the strength of an earlier one's rows."""
    coord = _codex()
    coord._code_mode_calls = {"first": 0, "second": 4}
    coord._mcp_rows_seen = 4
    assert "call(s)" in coord._displayable_result({"id": "first"}, OUTPUT)
    assert coord._displayable_result({"id": "second"}, OUTPUT) == OUTPUT


def test_other_providers_keep_every_result():
    base = object.__new__(_CCITurnCoordinator)
    assert base._displayable_result({"id": "call_1"}, OUTPUT) is OUTPUT
