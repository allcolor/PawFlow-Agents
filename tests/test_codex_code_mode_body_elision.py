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
