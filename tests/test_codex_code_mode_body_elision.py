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
# Eliding the body only shrank what the harness SENT. What it got back quotes
# the very calls the relay reported one by one, so those bytes reached the next
# context twice: once as each call's own tool result, once more inside the
# script's output. Invisible while the session is warm -- Codex never sees the
# relay's rows -- and paid for real at every cold start, which is to say at
# every compaction restart.
#
# Only the quoted bytes. A script also DERIVES: it compares what it read,
# counts it, concludes. Replacing the whole output on the strength of "this
# script made a call" threw that away, and no row was holding it.

READ = "AAA the first file, all of it, quoted back by the script" * 4
GREP = "BBB every hit the pattern found, line by line, verbatim" * 4
DERIVED = "\nderived comparison: the two differ on 3 lines\n"


def _coordinator_with_rows(results):
    """A coordinator that has emitted one code-mode call, then a relay row per
    entry of `results` -- the shape `_displayable_result` decides on."""
    coord = _codex()
    coord._code_mode_calls = {"call_1": 0}
    coord._mcp_row_results = {
        f"req_{i}": text for i, text in enumerate(results)}
    return coord


def test_what_the_rows_already_say_is_dropped_from_the_script_output():
    out = _coordinator_with_rows([READ, GREP])._displayable_result(
        {"id": "call_1"}, READ + GREP)
    assert READ not in out and GREP not in out
    assert str(len(READ)) in out and str(len(GREP)) in out


def test_what_the_script_derived_is_kept():
    """The reviewer's repro: one call, then a conclusion drawn from it. That
    conclusion exists in no row, and replacing the whole output lost it at the
    next cold start."""
    out = _coordinator_with_rows([READ])._displayable_result(
        {"id": "call_1"}, READ + DERIVED)
    assert DERIVED in out
    assert READ not in out


def test_an_output_that_quotes_nothing_is_untouched():
    """A script that made calls and printed its own summary of them duplicates
    nothing: there is no copy to drop."""
    summary = "read 2 files, 3 differences, none in the public API"
    out = _coordinator_with_rows([READ, GREP])._displayable_result(
        {"id": "call_1"}, summary)
    assert out == summary


def test_a_short_result_is_not_worth_a_marker():
    """Below the threshold the marker costs more than the bytes it replaces,
    and a short string is the kind that collides with unrelated prose."""
    out = _coordinator_with_rows(["ok"])._displayable_result(
        {"id": "call_1"}, "ok, and here is what that means")
    assert out == "ok, and here is what that means"


def test_a_script_that_reached_no_relay_keeps_its_output():
    """Read with Codex's own runtime, or computed in the script itself: no row
    describes it, so its output is the only record there is."""
    out = _coordinator_with_rows([])._displayable_result(
        {"id": "call_1"}, READ)
    assert out == READ


def test_an_ordinary_call_keeps_its_result():
    coord = _coordinator_with_rows([READ, GREP])
    assert coord._displayable_result({"id": "other"}, READ) == READ


def test_a_relay_row_records_what_it_returned():
    """That recording is what lets the script beside it drop the same bytes,
    and it happens on the way through, result by result."""
    coord = _coordinator_with_rows([""])
    assert coord._displayable_result({"id": "req_0"}, READ) == READ
    assert coord._mcp_row_results["req_0"] == READ


def test_only_the_rows_this_script_produced_are_looked_at():
    """A turn is a sequence of scripts. A later one that drove nothing must not
    be elided on the strength of an earlier one's rows."""
    coord = _codex()
    coord._code_mode_calls = {"first": 0, "second": 2}
    coord._mcp_row_results = {"req_0": READ, "req_1": GREP}
    assert coord._displayable_result({"id": "first"}, READ + GREP) != READ + GREP
    assert coord._displayable_result({"id": "second"}, READ) == READ


def test_other_providers_keep_every_result():
    base = object.__new__(_CCITurnCoordinator)
    assert base._displayable_result({"id": "call_1"}, READ) is READ
