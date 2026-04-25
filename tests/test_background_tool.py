"""Background tool manager — cc_tc_id matching + placeholder text.

Covers:
  - enqueue_cc_tc + pop_cc_tc round-trip, FIFO on hash collision
  - Stale entry pruning (>60s)
  - background() routes to tool-relay when tc_id is a CC one
  - Placeholder/result/kill text shape (English, rule enforced)
"""

import time
from unittest.mock import patch

import core.background_tool as bg


def _reset_state():
    # Ensure test isolation — the module uses process-global dicts.
    bg._backgrounded.clear()
    bg._completed.clear()
    bg._pending_bg.clear()
    bg._cc_pending_tcs.clear()


def test_enqueue_and_pop_cc_tc_matches_by_name_and_args():
    _reset_state()
    h = bg._args_hash({"command": "ls"})
    bg.enqueue_cc_tc("conv1", "claude", "toolu_A", "bash", h)
    assert bg.pop_cc_tc("conv1", "claude", "bash", h) == "toolu_A"
    # Consumed — second pop returns empty
    assert bg.pop_cc_tc("conv1", "claude", "bash", h) == ""


def test_pop_cc_tc_no_match_returns_empty():
    _reset_state()
    bg.enqueue_cc_tc("conv1", "claude", "toolu_A", "bash",
                     bg._args_hash({"command": "ls"}))
    assert bg.pop_cc_tc("conv1", "claude", "bash",
                        bg._args_hash({"command": "pwd"})) == ""


def test_pop_cc_tc_fifo_on_hash_collision():
    _reset_state()
    h = bg._args_hash({"command": "ls"})
    bg.enqueue_cc_tc("conv1", "claude", "toolu_A", "bash", h)
    bg.enqueue_cc_tc("conv1", "claude", "toolu_B", "bash", h)
    # First pop returns the older entry
    assert bg.pop_cc_tc("conv1", "claude", "bash", h) == "toolu_A"
    assert bg.pop_cc_tc("conv1", "claude", "bash", h) == "toolu_B"


def test_pop_cc_tc_isolated_by_conv_and_agent():
    _reset_state()
    h = bg._args_hash({"x": 1})
    bg.enqueue_cc_tc("conv1", "claude", "toolu_A", "tool", h)
    # Different conv → no match
    assert bg.pop_cc_tc("conv2", "claude", "tool", h) == ""
    # Different agent → no match
    assert bg.pop_cc_tc("conv1", "qwen", "tool", h) == ""
    # Correct scope still works
    assert bg.pop_cc_tc("conv1", "claude", "tool", h) == "toolu_A"


def test_stale_entries_pruned_on_enqueue():
    _reset_state()
    h = bg._args_hash({"x": 1})
    bg.enqueue_cc_tc("c", "a", "old", "tool", h)
    # Force-age the entry
    bg._cc_pending_tcs[("c", "a")][0]["ts"] = time.time() - 120
    bg.enqueue_cc_tc("c", "a", "new", "tool", h)
    # "old" must have been evicted by the prune
    tc = bg.pop_cc_tc("c", "a", "tool", h)
    assert tc == "new"


def test_background_flags_pending_and_tries_tool_relay():
    _reset_state()
    with patch("services.tool_relay_service.ToolRelayService.background_by_tc_id") as m:
        m.return_value = False
        assert bg.background("toolu_X") is True
        assert "toolu_X" in bg._pending_bg
        m.assert_called_once_with("toolu_X")


def test_args_hash_is_deterministic():
    _reset_state()
    assert bg._args_hash({"a": 1, "b": 2}) == bg._args_hash({"b": 2, "a": 1})
    assert bg._args_hash({"a": 1}) != bg._args_hash({"a": 2})


def test_snapshot_cc_pending_returns_empty_when_no_queue():
    _reset_state()
    assert bg.snapshot_cc_pending("c", "a") == []


def test_snapshot_cc_pending_dumps_queued_entries():
    _reset_state()
    bg.enqueue_cc_tc("c", "a", "toolu_AAAAAAAAAAAA", "bash",
                     bg._args_hash({"command": "ls"}))
    bg.enqueue_cc_tc("c", "a", "toolu_BBBBBBBBBBBB", "read",
                     bg._args_hash({"path": "/tmp/x"}))
    snap = bg.snapshot_cc_pending("c", "a")
    assert len(snap) == 2
    names = [e["tool_name"] for e in snap]
    assert names == ["bash", "read"]
    # tc_id is truncated to last 12 chars
    assert all(len(e["tc_id"]) == 12 for e in snap)
    assert all("args_hash" in e and "age_s" in e for e in snap)


def test_snapshot_does_not_consume_queue():
    _reset_state()
    h = bg._args_hash({"x": 1})
    bg.enqueue_cc_tc("c", "a", "toolu_X", "tool", h)
    bg.snapshot_cc_pending("c", "a")
    bg.snapshot_cc_pending("c", "a")
    # Pop still works after multiple snapshots
    assert bg.pop_cc_tc("c", "a", "tool", h) == "toolu_X"
