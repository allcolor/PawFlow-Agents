"""Every path that ends a turn must finalize tool calls left in flight.

A tool call renders as "pending" (spinner + BG/kill buttons) until its result
arrives. When the turn ends first, the result never comes, so each terminal path
attaches a placeholder instead. The interrupt path used to be the one exception,
which left the call visually stuck with no result at all.
"""

import re
from pathlib import Path

CHAT_UI = Path("tasks/io/chat_ui")
SSE_HANDLERS_B = (CHAT_UI / "sse_handlers_b.js").read_text(encoding="utf-8")
SSE_STATE = (CHAT_UI / "sse_state.js").read_text(encoding="utf-8")
MESSAGES_TOOLS = (CHAT_UI / "messages_tools.js").read_text(encoding="utf-8")


def _listener_body(src: str, event: str) -> str:
    start = src.index("addEventListener('" + event + "'")
    depth = 0
    for i in range(src.index("{", start), len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError(f"unterminated listener for {event}")


def test_interrupt_finalizes_in_flight_tool_calls():
    """Regression: 'interrupting' only printed a system message. An interrupt
    aborts the running tool, and unlike stop/cancel the turn keeps going, so no
    other finalizer ever fired for that row."""
    body = _listener_body(SSE_HANDLERS_B, "interrupting")
    assert "_finalizeLiveToolCalls(" in body
    assert "[Interrupted]" in body


def test_every_terminal_path_finalizes_tool_calls():
    for event in ("active_released", "cancelled", "error_event", "interrupting"):
        assert "_finalizeLiveToolCalls(" in _listener_body(SSE_HANDLERS_B, event), event


def test_finalization_leaves_turn_view_cue_copies_alone():
    """A cue copy carries a snapshot of a call taken while it was running, so it
    still looks pending forever. Stamping it printed '[Stopped]' next to a call
    that had finished, in the copy the reader was watching."""
    start = SSE_STATE.index("function _finalizeLiveToolCalls")
    body = SSE_STATE[start:SSE_STATE.index("\n// Expose a reset hook", start)]
    assert "bullet.closest('.simple-turn-cue-copy')" in body


def test_placeholder_results_are_marked_as_such():
    """Placeholders must be distinguishable from real results so a late result
    can replace them."""
    assert "resultDiv.dataset.placeholder = '1'" in MESSAGES_TOOLS
    # Every synthetic result passes the flag; real results never do.
    for src, marker in ((SSE_STATE, "[Interrupted]"),
                        (SSE_HANDLERS_B, "[result not delivered]")):
        call = re.search(
            r"_attachToolResult\([^;]*" + re.escape(marker) + r"[^;]*\)", src)
        assert call and "placeholder: true" in call.group(0), marker


def test_real_result_replaces_a_placeholder_but_not_the_reverse():
    start = MESSAGES_TOOLS.index("function _attachToolResult(")
    body = MESSAGES_TOOLS[start:MESSAGES_TOOLS.index("\nfunction ", start + 1)]
    # Refuse when the incoming result is itself a placeholder, or when what is
    # already there is a real result; otherwise drop the placeholder and render.
    assert "if (_placeholder || _existingResult.dataset.placeholder !== '1') return;" in body
    assert "_existingResult.remove();" in body
