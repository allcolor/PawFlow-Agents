"""Tests for the flow debugger — breakpoints, step execution, FlowFile inspection."""

import threading
import time
import pytest

from engine.debugger import FlowDebugger, Breakpoint, DebugAction, DebugSnapshot
from core import FlowFile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class MockExecutor:
    """Minimal mock of ContinuousFlowExecutor for attach/detach tests."""
    def __init__(self):
        self._debugger = None


def make_ff(content: str = "hello world", **attrs) -> FlowFile:
    """Create a FlowFile with content and optional attributes."""
    ff = FlowFile(content=content.encode("utf-8"))
    for k, v in attrs.items():
        ff.set_attribute(k, v)
    return ff


# ---------------------------------------------------------------------------
# Breakpoint management
# ---------------------------------------------------------------------------

class TestBreakpointManagement:

    def test_add_breakpoint(self):
        dbg = FlowDebugger()
        bp = dbg.add_breakpoint("task_1")
        assert isinstance(bp, Breakpoint)
        assert bp.task_id == "task_1"
        assert bp.enabled is True
        assert bp.hit_count == 0

    def test_add_breakpoint_with_condition(self):
        dbg = FlowDebugger()
        bp = dbg.add_breakpoint("t1", condition="attrs.get('x') == '1'")
        assert bp.condition == "attrs.get('x') == '1'"

    def test_remove_breakpoint(self):
        dbg = FlowDebugger()
        dbg.add_breakpoint("task_1")
        assert dbg.remove_breakpoint("task_1") is True
        assert dbg.remove_breakpoint("task_1") is False  # already removed

    def test_toggle_breakpoint(self):
        dbg = FlowDebugger()
        dbg.add_breakpoint("t1")
        # Initially enabled, toggle -> disabled
        result = dbg.toggle_breakpoint("t1")
        assert result is False
        # Toggle again -> enabled
        result = dbg.toggle_breakpoint("t1")
        assert result is True

    def test_toggle_nonexistent(self):
        dbg = FlowDebugger()
        assert dbg.toggle_breakpoint("nope") is False

    def test_clear_breakpoints(self):
        dbg = FlowDebugger()
        dbg.add_breakpoint("t1")
        dbg.add_breakpoint("t2")
        dbg.clear_breakpoints()
        assert len(dbg.get_breakpoints()) == 0

    def test_get_breakpoints(self):
        dbg = FlowDebugger()
        dbg.add_breakpoint("t1")
        dbg.add_breakpoint("t2")
        bps = dbg.get_breakpoints()
        assert "t1" in bps
        assert "t2" in bps
        assert len(bps) == 2


# ---------------------------------------------------------------------------
# should_pause logic
# ---------------------------------------------------------------------------

class TestShouldPause:

    def test_pause_with_breakpoint(self):
        dbg = FlowDebugger()
        dbg.add_breakpoint("t1")
        assert dbg.should_pause("t1") is True

    def test_no_pause_without_breakpoint(self):
        dbg = FlowDebugger()
        assert dbg.should_pause("t1") is False

    def test_no_pause_disabled_breakpoint(self):
        dbg = FlowDebugger()
        dbg.add_breakpoint("t1")
        dbg.toggle_breakpoint("t1")  # disable
        assert dbg.should_pause("t1") is False

    def test_conditional_breakpoint_true(self):
        dbg = FlowDebugger()
        dbg.add_breakpoint("t1", condition="attrs.get('status') == 'error'")
        ff = make_ff(status="error")
        assert dbg.should_pause("t1", ff) is True

    def test_conditional_breakpoint_false(self):
        dbg = FlowDebugger()
        dbg.add_breakpoint("t1", condition="attrs.get('status') == 'error'")
        ff = make_ff(status="ok")
        assert dbg.should_pause("t1", ff) is False

    def test_logpoint_does_not_pause(self):
        dbg = FlowDebugger()
        dbg.add_breakpoint("t1", log_message="just logging")
        assert dbg.should_pause("t1") is False

    def test_hit_count_increments(self):
        dbg = FlowDebugger()
        dbg.add_breakpoint("t1")
        dbg.should_pause("t1")
        dbg.should_pause("t1")
        bp = dbg.get_breakpoints()["t1"]
        assert bp.hit_count == 2

    def test_step_mode_pauses_everywhere(self):
        dbg = FlowDebugger()
        dbg._step_mode = True
        # Should pause even without a breakpoint
        assert dbg.should_pause("any_task") is True


# ---------------------------------------------------------------------------
# Pause / Resume
# ---------------------------------------------------------------------------

class TestPauseResume:

    def test_pause_at_blocks_and_continue_unblocks(self):
        dbg = FlowDebugger()
        ff = make_ff("test data")
        unblocked = threading.Event()

        def pauser():
            dbg.pause_at("t1", ff)
            unblocked.set()

        t = threading.Thread(target=pauser, daemon=True)
        t.start()

        # Give the thread time to block
        time.sleep(0.1)
        assert dbg.is_paused is True
        assert dbg.paused_at == "t1"

        # Continue should unblock
        dbg.continue_execution()
        assert unblocked.wait(timeout=2) is True
        assert dbg.is_paused is False

    def test_step_unblocks_and_sets_step_mode(self):
        dbg = FlowDebugger()
        unblocked = threading.Event()

        def pauser():
            dbg.pause_at("t1")
            unblocked.set()

        t = threading.Thread(target=pauser, daemon=True)
        t.start()

        time.sleep(0.1)
        assert dbg.is_paused is True

        dbg.step()
        assert unblocked.wait(timeout=2) is True
        assert dbg._step_mode is True
        assert dbg._action == DebugAction.STEP

    def test_stop_debugging_unblocks(self):
        dbg = FlowDebugger()
        unblocked = threading.Event()

        def pauser():
            dbg.pause_at("t1")
            unblocked.set()

        t = threading.Thread(target=pauser, daemon=True)
        t.start()

        time.sleep(0.1)
        dbg.stop_debugging()
        assert unblocked.wait(timeout=2) is True
        assert dbg._action == DebugAction.STOP
        assert len(dbg.get_breakpoints()) == 0


# ---------------------------------------------------------------------------
# Snapshot capture
# ---------------------------------------------------------------------------

class TestSnapshots:

    def test_capture_input_snapshot(self):
        dbg = FlowDebugger()
        ff = make_ff("hello world", filename="test.txt")
        dbg._capture_snapshot("t1", ff, "input")

        snaps = dbg.get_snapshots()
        assert len(snaps) == 1
        assert snaps[0]["task_id"] == "t1"
        assert snaps[0]["direction"] == "input"
        assert snaps[0]["content_preview"] == "hello world"
        assert snaps[0]["content_size"] == 11
        assert snaps[0]["attributes"]["filename"] == "test.txt"

    def test_capture_output_list(self):
        dbg = FlowDebugger()
        ffs = [make_ff("out1"), make_ff("out2")]
        dbg.capture_output("t1", ffs)

        snaps = dbg.get_snapshots()
        assert len(snaps) == 2
        assert all(s["direction"] == "output" for s in snaps)

    def test_capture_output_single(self):
        dbg = FlowDebugger()
        dbg.capture_output("t1", make_ff("single"))
        assert len(dbg.get_snapshots()) == 1

    def test_content_preview_truncated(self):
        dbg = FlowDebugger()
        long_content = "x" * 2000
        ff = make_ff(long_content)
        dbg._capture_snapshot("t1", ff, "input")

        snaps = dbg.get_snapshots()
        assert len(snaps[0]["content_preview"]) == 1000

    def test_snapshot_limit(self):
        dbg = FlowDebugger()
        dbg._max_snapshots = 5
        for i in range(10):
            dbg._capture_snapshot("t1", make_ff(f"data_{i}"), "input")

        assert len(dbg._snapshots) == 5
        # Should keep the latest 5
        snaps = dbg.get_snapshots()
        assert snaps[0]["content_preview"] == "data_5"

    def test_filter_snapshots_by_task(self):
        dbg = FlowDebugger()
        dbg._capture_snapshot("t1", make_ff("a"), "input")
        dbg._capture_snapshot("t2", make_ff("b"), "input")
        dbg._capture_snapshot("t1", make_ff("c"), "output")

        snaps = dbg.get_snapshots(task_id="t1")
        assert len(snaps) == 2
        assert all(s["task_id"] == "t1" for s in snaps)


# ---------------------------------------------------------------------------
# Status reporting
# ---------------------------------------------------------------------------

class TestStatus:

    def test_status_default(self):
        dbg = FlowDebugger()
        status = dbg.get_status()
        assert status["attached"] is False
        assert status["paused"] is False
        assert status["paused_at"] is None
        assert status["breakpoints"] == {}
        assert status["snapshots_count"] == 0
        assert status["step_mode"] is False

    def test_status_with_breakpoints(self):
        dbg = FlowDebugger()
        dbg.add_breakpoint("t1", condition="attrs.get('x') == '1'")
        status = dbg.get_status()
        assert "t1" in status["breakpoints"]
        assert status["breakpoints"]["t1"]["condition"] == "attrs.get('x') == '1'"


# ---------------------------------------------------------------------------
# Attach / Detach
# ---------------------------------------------------------------------------

class TestAttachDetach:

    def test_attach(self):
        dbg = FlowDebugger()
        executor = MockExecutor()
        dbg.attach(executor)
        assert executor._debugger is dbg
        assert dbg._attached_executor is executor
        assert dbg.get_status()["attached"] is True

    def test_detach(self):
        dbg = FlowDebugger()
        executor = MockExecutor()
        dbg.attach(executor)
        dbg.add_breakpoint("t1")

        dbg.detach()
        assert executor._debugger is None
        assert dbg._attached_executor is None
        # stop_debugging clears breakpoints
        assert len(dbg.get_breakpoints()) == 0

    def test_detach_without_attach(self):
        dbg = FlowDebugger()
        # Should not raise
        dbg.detach()
        assert dbg._attached_executor is None


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

class TestCallbacks:

    def test_on_paused_callback(self):
        dbg = FlowDebugger()
        events = []
        dbg.on("paused", lambda **kw: events.append(("paused", kw)))

        def do_pause():
            dbg.pause_at("t1")

        t = threading.Thread(target=do_pause, daemon=True)
        t.start()
        time.sleep(0.1)

        assert len(events) == 1
        assert events[0][0] == "paused"
        assert events[0][1]["task_id"] == "t1"

        dbg.continue_execution()
        t.join(timeout=2)

    def test_on_snapshot_callback(self):
        dbg = FlowDebugger()
        captured = []
        dbg.on("snapshot", lambda **kw: captured.append(kw))

        dbg._capture_snapshot("t1", make_ff("data"), "input")
        assert len(captured) == 1
        assert isinstance(captured[0]["snapshot"], DebugSnapshot)
