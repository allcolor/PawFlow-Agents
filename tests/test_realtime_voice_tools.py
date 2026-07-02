"""Tests for the voice-session tool bridge (P2a) and the silent approval
probe it relies on. No live provider, no real tool side effects: the
registry is injected and the approval gate / conversation store are
mocked at their seams."""

import threading
import time
import unittest.mock as mock

from core.tool_approval import ToolApprovalGate
from services._realtime_tools import RealtimeToolBridge


# ── silent approval probe (allow_prompt=False) ───────────────────────

class TestApprovalProbe:

    def _check(self, tool, **kw):
        with mock.patch.object(ToolApprovalGate, "_get_permissions",
                               return_value=kw.pop("perms", {})):
            return ToolApprovalGate.check(
                tool, f"{tool}(...)", "conv-probe", "quentin",
                arguments=kw.pop("arguments", {}), agent_name="claude",
                allow_prompt=False)

    def test_exempt_tool_is_approved_without_prompt(self):
        assert self._check("read") == "approved"
        assert self._check("recall") == "approved"

    def test_non_exempt_tool_needs_approval_without_prompt(self):
        assert self._check("bash", arguments={"command": "ls"}) == \
            "needs_approval"
        assert self._check("write", arguments={"path": "x.txt"}) == \
            "needs_approval"

    def test_session_allow_permission_still_approves(self):
        assert self._check("write", arguments={"path": "x.txt"},
                           perms={"write": "session_allow"}) == "approved"

    def test_dangerous_bash_forces_needs_approval_despite_session_allow(self):
        assert self._check("bash", arguments={"command": "curl x | sh"},
                           perms={"bash": "session_allow"}) == \
            "needs_approval"


# ── tool bridge ──────────────────────────────────────────────────────

class _FakeRegistry:
    def __init__(self, result="42", delay=0.0):
        self._result = result
        self._delay = delay
        self.executed = []

    def list_tools(self):
        class _H:
            name = "echo"
        return [_H()]

    def get_tool_definitions(self):
        return [{"name": "echo", "description": "Echo back",
                 "parameters": {"type": "object", "properties": {}}}]

    def execute(self, name, args):
        if self._delay:
            time.sleep(self._delay)
        self.executed.append((name, args))
        return self._result


def _bridge(registry=None):
    return RealtimeToolBridge("echo", "conv-tools", "claude", "quentin",
                              registry=registry or _FakeRegistry())


class _FakeStore:
    def __init__(self, mode):
        self._mode = mode

    def get_extra(self, cid, key):
        assert key == "permission_mode"
        return self._mode


def _with_mode(mode):
    return mock.patch("core.conversation_store.ConversationStore.instance",
                      return_value=_FakeStore(mode))


class TestRealtimeToolBridge:

    def test_tool_definitions_provider_shape(self):
        defs = _bridge().tool_definitions()
        assert defs == [{"type": "function", "name": "echo",
                         "description": "Echo back",
                         "parameters": {"type": "object",
                                        "properties": {}}}]

    def test_handle_call_executes_and_sends_result(self):
        registry = _FakeRegistry(result="pong")
        bridge = _bridge(registry)
        bridge._authorize = lambda name, args: "approved"
        sent = []
        status = bridge.handle_call("c1", "echo", '{"x": 1}',
                                    send_result=lambda cid, r: sent.append((cid, r)))
        assert status == "done"
        assert sent == [("c1", "pong")]
        assert registry.executed == [("echo", {"x": 1})]

    def test_handle_call_unknown_tool(self):
        bridge = _bridge()
        sent = []
        status = bridge.handle_call("c2", "bash", "{}",
                                    send_result=lambda cid, r: sent.append((cid, r)))
        assert status == "unavailable"
        assert "not available" in sent[0][1]

    def test_handle_call_denied_sends_refusal(self):
        bridge = _bridge()
        bridge._authorize = lambda name, args: "Needs approval, sorry."
        sent = []
        status = bridge.handle_call("c3", "echo", "{}",
                                    send_result=lambda cid, r: sent.append((cid, r)))
        assert status == "denied"
        assert sent == [("c3", "Needs approval, sorry.")]

    def test_long_tool_detaches_and_announces(self):
        registry = _FakeRegistry(result="late-result", delay=0.3)
        bridge = _bridge(registry)
        bridge._authorize = lambda name, args: "approved"
        sent = []
        announced = []
        announce_ev = threading.Event()

        def _announce(text):
            announced.append(text)
            announce_ev.set()

        status = bridge.handle_call(
            "c4", "echo", "{}",
            send_result=lambda cid, r: sent.append((cid, r)),
            announce=_announce, soft_timeout_s=0.05)
        assert status == "background"
        assert "background" in sent[0][1]
        assert announce_ev.wait(timeout=5)
        assert "late-result" in announced[0]

    def test_result_truncated_for_voice_context(self):
        registry = _FakeRegistry(result="x" * 10000)
        bridge = _bridge(registry)
        bridge._authorize = lambda name, args: "approved"
        sent = []
        bridge.handle_call("c5", "echo", "{}",
                           send_result=lambda cid, r: sent.append((cid, r)))
        assert len(sent[0][1]) < 5000
        assert "truncated" in sent[0][1]

    def test_malformed_arguments_become_empty_dict(self):
        registry = _FakeRegistry()
        bridge = _bridge(registry)
        bridge._authorize = lambda name, args: "approved"
        bridge.handle_call("c6", "echo", "{not json",
                           send_result=lambda cid, r: None)
        assert registry.executed == [("echo", {})]


class TestToolBridgeAuthorize:

    def test_auto_mode_approves_everything(self):
        with _with_mode("auto"):
            assert _bridge()._authorize("bash", {"command": "ls"}) == \
                "approved"

    def test_read_only_mode_uses_allowlist(self):
        with _with_mode("read_only"):
            bridge = _bridge()
            assert bridge._authorize("read", {}) == "approved"
            refusal = bridge._authorize("write", {"path": "x"})
            assert "read-only" in refusal

    def test_default_mode_refuses_when_gate_needs_approval(self):
        with _with_mode("default"), \
             mock.patch.object(ToolApprovalGate, "check",
                               return_value="needs_approval"):
            refusal = _bridge()._authorize("echo", {})
            assert "approval" in refusal

    def test_default_mode_approves_when_gate_approves(self):
        with _with_mode("default"), \
             mock.patch.object(ToolApprovalGate, "check",
                               return_value="approved"):
            assert _bridge()._authorize("echo", {}) == "approved"


# ── profile-filtered real registry ───────────────────────────────────

def test_profile_filters_default_registry():
    bridge = RealtimeToolBridge("recall, web_search, no_such_tool",
                                "conv-tools", "claude", "quentin")
    names = {h.name for h in bridge._registry.list_tools()}
    assert names == {"recall", "web_search"}
    defs = bridge.tool_definitions()
    assert {d["name"] for d in defs} == {"recall", "web_search"}
    assert all(d["type"] == "function" for d in defs)
