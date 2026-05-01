"""Tests for the tool-relay kill-hook mechanism.

When FORCE STOP triggers `cancel_agent`, every in-flight tool call
should have its registered kill_hooks invoked so subprocesses /
sockets / external resources are torn down. Without this, the daemon
threads that own the tool's `_exec()` keep running because Python
threads can't be killed safely.
"""

import threading

import pytest

from services.tool_relay_service import (
    ToolRelayService,
    register_kill_hook,
    _set_current_kill_hooks,
    _set_current_cancel_event,
    current_cancel_event,
)


@pytest.fixture(autouse=True)
def _reset_inflight():
    ToolRelayService._inflight.clear()
    yield
    ToolRelayService._inflight.clear()


def _make_inflight(rid: str, conv: str = "c1", agent: str = "claude"):
    cancel = threading.Event()
    background = threading.Event()
    hooks: list = []
    ToolRelayService._inflight[rid] = {
        "conv": conv, "agent": agent,
        "cancel": cancel, "background": background, "kill_hooks": hooks,
        "tool_name": "bash",
    }
    return cancel, background, hooks


def test_cancel_agent_sets_cancel_event():
    cancel, _, _ = _make_inflight("r1")
    ToolRelayService.cancel_agent("c1", "claude")
    assert cancel.is_set()


def test_cancel_agent_invokes_kill_hooks():
    cancel, _, hooks = _make_inflight("r1")
    fired = []
    hooks.append(lambda: fired.append("a"))
    hooks.append(lambda: fired.append("b"))
    ToolRelayService.cancel_agent("c1", "claude")
    assert fired == ["a", "b"]


def test_cancel_request_invokes_kill_hooks():
    cancel, _, hooks = _make_inflight("r1")
    fired = []
    hooks.append(lambda: fired.append("targeted"))
    assert ToolRelayService.cancel_request("r1") is True
    assert cancel.is_set()
    assert fired == ["targeted"]


def test_cancel_agent_skips_other_conversations():
    cancel_a, _, hooks_a = _make_inflight("rA", conv="cA")
    cancel_b, _, hooks_b = _make_inflight("rB", conv="cB")
    fired = []
    hooks_a.append(lambda: fired.append("A"))
    hooks_b.append(lambda: fired.append("B"))
    ToolRelayService.cancel_agent("cA", "claude")
    assert fired == ["A"]
    assert cancel_a.is_set()
    assert not cancel_b.is_set()


def test_cancel_agent_skips_other_agents():
    cancel_a, _, hooks_a = _make_inflight("rA", agent="claude")
    cancel_b, _, hooks_b = _make_inflight("rB", agent="qwen")
    fired = []
    hooks_a.append(lambda: fired.append("claude"))
    hooks_b.append(lambda: fired.append("qwen"))
    ToolRelayService.cancel_agent("c1", "claude")
    assert fired == ["claude"]


def test_kill_hook_failure_does_not_block_others():
    _, _, hooks = _make_inflight("r1")
    fired = []
    def _bad():
        raise RuntimeError("boom")
    hooks.append(_bad)
    hooks.append(lambda: fired.append("after"))
    # Must not raise
    ToolRelayService.cancel_agent("c1", "claude")
    assert fired == ["after"]


def test_background_by_tc_id_matches_request_id_without_provider_tc_id():
    _, background, _ = _make_inflight("rid-no-provider")
    assert ToolRelayService.background_by_tc_id("rid-no-provider") is True
    assert background.is_set()


def test_register_kill_hook_appends_to_thread_local_list():
    hooks: list = []
    _set_current_kill_hooks(hooks)
    try:
        def _h():
            pass
        register_kill_hook(_h)
        assert hooks == [_h]
    finally:
        _set_current_kill_hooks(None)


def test_register_kill_hook_runs_immediately_when_already_cancelled():
    hooks: list = []
    cancel = threading.Event()
    cancel.set()
    fired = []
    _set_current_kill_hooks(hooks)
    _set_current_cancel_event(cancel)
    try:
        register_kill_hook(lambda: fired.append("late"))
        assert fired == ["late"]
    finally:
        _set_current_cancel_event(None)
        _set_current_kill_hooks(None)


def test_register_kill_hook_outside_dispatch_is_noop():
    _set_current_kill_hooks(None)
    # Must not raise even though no thread-local list is set
    register_kill_hook(lambda: None)


def test_current_cancel_event_returns_none_outside_dispatch():
    assert current_cancel_event() is None
