"""Background tool manager — cc_tc_id matching + placeholder text.

Covers:
  - enqueue_cc_tc + pop_cc_tc round-trip, FIFO on hash collision
  - Stale entry pruning (>60s)
  - background() routes to tool-relay when tc_id is a CC one
  - Placeholder/result/kill text shape (English, rule enforced)
"""

import threading
import time
import inspect
from pathlib import Path
from unittest.mock import patch

import core.background_tool as bg
from tests._agent_core_src import agent_core_src


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


def test_tool_relay_filesystem_listing_uses_agent_bindings(monkeypatch):
    from services.tool_relay_service import ToolRelayService
    import core.relay_bindings as relay_bindings
    from core.service_registry import SCOPE_USER

    calls = []

    def _get_linked(cid, agent=""):
        calls.append((cid, agent))
        return ["relay-agent"] if agent == "agentA" else ["relay-conv"]

    class _Definition:
        service_id = "relay-agent"
        service_type = "relay"
        scope = SCOPE_USER

    class _Registry:
        def resolve_definition(self, service_id, user_id="", conv_id=""):
            return _Definition() if service_id == "relay-agent" else None

        def resolve(self, service_id, user_id="", conv_id=""):
            return object() if service_id == "relay-agent" else None

    monkeypatch.setattr(relay_bindings, "get_linked", _get_linked)
    monkeypatch.setattr(
        "core.service_registry.ServiceRegistry.get_instance",
        staticmethod(lambda: _Registry()),
    )

    available = ToolRelayService._list_available_filesystem_services(
        "alice", "conv1", "agentA")

    assert calls == [("conv1", "agentA")]
    assert [item["id"] for item in available] == ["relay-agent"]


def test_tool_relay_default_filesystem_prefers_default_relay(monkeypatch):
    from services.tool_relay_service import ToolRelayService
    import core.relay_bindings as relay_bindings
    from core.service_registry import SCOPE_USER

    relay_other = object()
    relay_default = object()

    class _Definition:
        service_type = "relay"
        scope = SCOPE_USER

        def __init__(self, service_id):
            self.service_id = service_id

    class _Registry:
        def resolve_definition(self, service_id, user_id="", conv_id=""):
            return _Definition(service_id)

        def resolve(self, service_id, user_id="", conv_id=""):
            return {
                "relay-other": relay_other,
                "relay-default": relay_default,
            }.get(service_id)

    monkeypatch.setattr(relay_bindings, "get_linked", lambda cid, agent="": ["relay-other", "relay-default"])
    monkeypatch.setattr(relay_bindings, "get_default", lambda cid, agent="": "relay-default")
    monkeypatch.setattr(
        "core.service_registry.ServiceRegistry.get_instance",
        staticmethod(lambda: _Registry()),
    )

    assert ToolRelayService._find_filesystem_service("alice", "conv1", "assistant") is relay_default
    resolver = ToolRelayService._make_filesystem_resolver("alice", "conv1", "assistant")
    assert resolver("") is relay_default


def test_tool_relay_default_filesystem_does_not_fallback_to_other_linked_relay(monkeypatch):
    from services.tool_relay_service import ToolRelayService
    import core.relay_bindings as relay_bindings
    from core.service_registry import SCOPE_USER

    relay_other = object()

    class _Definition:
        service_type = "relay"
        scope = SCOPE_USER
        scope_id = "alice"

        def __init__(self, service_id):
            self.service_id = service_id

    class _Registry:
        def resolve_definition(self, service_id, user_id="", conv_id=""):
            return _Definition(service_id)

        def is_connected(self, scope, scope_id, service_id):
            return service_id == "relay-other"

        def get_live_instance_cached(self, scope, scope_id, service_id):
            return relay_other if service_id == "relay-other" else None

        def resolve(self, service_id, user_id="", conv_id=""):
            return relay_other if service_id == "relay-other" else None

    monkeypatch.setattr(relay_bindings, "get_linked", lambda cid, agent="": ["relay-other", "relay-default"])
    monkeypatch.setattr(relay_bindings, "get_default", lambda cid, agent="": "relay-default")
    monkeypatch.setattr(
        "core.service_registry.ServiceRegistry.get_instance",
        staticmethod(lambda: _Registry()),
    )

    available = ToolRelayService._list_available_filesystem_services(
        "alice", "conv1", "assistant")

    assert [item["id"] for item in available] == ["relay-default", "relay-other"]
    assert available[0]["default"] is True
    assert available[0]["connected"] is False
    assert ToolRelayService._find_filesystem_service("alice", "conv1", "assistant") is None

    resolver = ToolRelayService._make_filesystem_resolver("alice", "conv1", "assistant")
    assert resolver("") is None
    assert resolver("relay-other") is relay_other
    assert resolver("relay-unlinked") is None


def test_pop_cc_tc_fifo_on_hash_collision():
    _reset_state()
    h = bg._args_hash({"command": "ls"})
    bg.enqueue_cc_tc("conv1", "claude", "toolu_A", "bash", h)
    bg.enqueue_cc_tc("conv1", "claude", "toolu_B", "bash", h)
    # First pop returns the older entry
    assert bg.pop_cc_tc("conv1", "claude", "bash", h) == "toolu_A"
    assert bg.pop_cc_tc("conv1", "claude", "bash", h) == "toolu_B"


def test_pop_cc_tc_falls_back_to_scoped_fifo_wildcard():
    _reset_state()
    bg.enqueue_cc_tc(
        "conv1", "gemini", "gemini_tc", bg.ANY_TOOL, bg.ANY_ARGS_HASH)
    assert bg.pop_cc_tc(
        "conv1", "gemini", "read", bg._args_hash({"path": "/workspace/README.md"})) == "gemini_tc"
    assert bg.pop_cc_tc(
        "conv1", "gemini", "read", bg._args_hash({"path": "/workspace/README.md"})) == ""


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


def test_stale_entries_pruned_on_pop_and_snapshot():
    _reset_state()
    h = bg._args_hash({"x": 1})
    bg.enqueue_cc_tc("c", "a", "old", "tool", h)
    bg._cc_pending_tcs[("c", "a")][0]["ts"] = time.time() - 120

    assert bg.snapshot_cc_pending("c", "a") == []
    assert bg.pop_cc_tc("c", "a", "tool", h) == ""
    assert ("c", "a") not in bg._cc_pending_tcs


def test_enqueue_late_binds_existing_tool_relay_inflight():
    _reset_state()
    from services.tool_relay_service import ToolRelayService

    with ToolRelayService._inflight_lock:
        ToolRelayService._inflight.clear()

    h = bg._args_hash({"command": "pwd"})
    evt = threading.Event()
    with ToolRelayService._inflight_lock:
        ToolRelayService._inflight["rid1"] = {
            "conv": "conv1",
            "agent": "assistant",
            "tool_name": "bash",
            "args_hash": h,
            "cc_tc_id": "",
            "bg_tc_id": "rid1",
            "background": evt,
        }

    try:
        bg.enqueue_cc_tc("conv1", "assistant", "tc1", "bash", h)
        with ToolRelayService._inflight_lock:
            info = ToolRelayService._inflight["rid1"]
            assert info["cc_tc_id"] == "tc1"
            assert info["bg_tc_id"] == "tc1"
        assert bg.snapshot_cc_pending("conv1", "assistant") == []
    finally:
        with ToolRelayService._inflight_lock:
            ToolRelayService._inflight.clear()


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


def test_tool_relay_auto_backgrounds_without_provider_tc_id(monkeypatch):
    """Explicit auto-background works even without a provider tool id."""
    _reset_state()
    from services.tool_relay_service import ToolRelayService

    svc = ToolRelayService({})
    monkeypatch.setattr(svc, "_auto_bg_after_seconds", 0.05)

    release = threading.Event()
    injected = []

    def _slow_execute(request_id, tool_name, arguments, user_id, conversation_id, agent_name):
        release.wait(timeout=2)
        return {"type": "result", "request_id": request_id, "data": "done"}

    def _capture_inject(tc_id, result_text, is_cancel=False):
        injected.append((tc_id, result_text, is_cancel))

    monkeypatch.setattr(svc, "_do_execute", _slow_execute)
    monkeypatch.setattr(bg, "_inject_result", _capture_inject)

    started = time.time()
    result = svc._handle_execute(
        "rid-no-provider-tc", "bash", {"command": "slow"},
        "alice", "conv1", "agent1")
    elapsed = time.time() - started

    assert elapsed < 0.8
    assert result["type"] == "result"
    assert "[Running in background (tc_id=rid-no-provider-tc)]" in result["data"]

    release.set()
    deadline = time.time() + 2
    while time.time() < deadline and not injected:
        time.sleep(0.01)

    assert injected == [("rid-no-provider-tc", "done", False)]


def test_tool_relay_auto_background_disabled_by_default():
    from services.tool_relay_service import ToolRelayService

    svc = ToolRelayService({})
    schema = svc.get_parameter_schema()

    assert svc._auto_bg_after_seconds == 0.0
    assert schema["auto_background_after_seconds"]["default"] == 0


def test_background_wait_has_no_default_timeout():
    sig = inspect.signature(bg.wait_pending)

    assert sig.parameters["timeout"].default is None


def test_agent_core_waits_for_background_without_default_timeout():
    src = agent_core_src()

    assert "wait_pending(conversation_id, timeout=120" not in src
    assert "wait_pending(\n                    conversation_id,\n                    cancel_check=" in src  # reindented by split


def test_tool_relay_has_no_implicit_execution_timeout():
    from services.tool_relay_service import ToolRelayService

    src = inspect.getsource(ToolRelayService._handle_execute)

    assert "wait(timeout=" not in src
    assert "evt.wait(timeout=300)" not in src
    assert "time.monotonic() + 0.5" not in src
    assert "8 * 3600" not in src
    assert "timed out" not in src


def test_continuous_run_batch_has_no_implicit_timeout():
    from engine.continuous_executor import ContinuousFlowExecutor

    sig = inspect.signature(ContinuousFlowExecutor.run_batch)
    assert sig.parameters["timeout"].default is None


def test_tool_relay_late_binds_without_bounded_retry(monkeypatch):
    _reset_state()
    from services.tool_relay_service import ToolRelayService

    svc = ToolRelayService({})
    calls = {"count": 0}

    def _pop(conv_id, agent_name, tool_name, args_hash):
        calls["count"] += 1
        return "tc-late" if calls["count"] == 2 else ""

    def _execute(request_id, tool_name, arguments, user_id, conversation_id, agent_name):
        with ToolRelayService._inflight_lock:
            info = ToolRelayService._inflight[request_id]
            assert info["cc_tc_id"] == "tc-late"
            assert info["bg_tc_id"] == "tc-late"
        return {"type": "result", "request_id": request_id, "data": "ok"}

    monkeypatch.setattr(bg, "pop_cc_tc", _pop)
    monkeypatch.setattr(svc, "_do_execute", _execute)

    result = svc._handle_execute(
        "rid-late", "bash", {"command": "true"}, "alice", "conv1", "agent1")

    assert result["data"] == "ok"
    assert calls == {"count": 2}
