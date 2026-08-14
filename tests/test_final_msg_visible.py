"""The turn's final marker must name a row with visible content.

Incident: a thinking-only flush was the last to update `_last_turn_msg_id`;
the turn named it `final_msg_id`, the `turn_final` patch matched no
transcript row, and the webchat closed the turn with no final answer while
the real text sat unmarked (salvaged by the NEXT turn under its turn id).
"""

from pathlib import Path

CLOSURES2 = Path("tasks/ai/_alc_closures2.py").read_text(encoding="utf-8")
ITERATION = Path("tasks/ai/_alc_iteration.py").read_text(encoding="utf-8")
SETUP = Path("tasks/ai/_alc_setup.py").read_text(encoding="utf-8")


def test_visible_id_is_set_only_by_text_flushes():
    # Two text-persisting sites, each sets BOTH ids.
    assert CLOSURES2.count("_last_turn_visible_msg_id = getattr(msg") == 2
    # The thinking-only branches keep updating the generic id but must never
    # claim the visible one.
    for start in _thinking_branches(CLOSURES2):
        assert "_last_turn_visible_msg_id" not in start


def _thinking_branches(src):
    # The turn-flush thinking-only branch and the block-callback
    # thinking branch, bounded by their neighbouring markers.
    a = src.index("elif _text_thinking:")
    a_end = src.index("# Finalize streaming element", a)
    b = src.index('if event_type in ("thinking", "thinking_content"):')
    b_end = src.index('if event_type == "tool_use":', b)
    return [src[a:a_end], src[b:b_end]]


def test_final_marker_prefers_the_visible_id():
    assert "_last_turn_visible_msg_id" in ITERATION
    seg = ITERATION[ITERATION.index("_visible_mid = ("):]
    seg = seg[:seg.index("st._release_active_after_terminal_visible_answer")]
    # Preference order: visible id first, generic id as fallback, and both
    # the gauge patch and final_msg_id use the same resolved id.
    assert seg.index("_last_turn_visible_msg_id") < seg.index(
        "_last_turn_msg_id")
    assert "_schedule_cc_turn_gauge_patch(" in seg
    assert "st.final_msg_id = (" in seg


def test_ids_reset_at_turn_setup():
    # A turn with no visible text must not inherit the previous turn's ids.
    assert '_last_turn_msg_id = ""' in SETUP
    assert '_last_turn_visible_msg_id = ""' in SETUP
