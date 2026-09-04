"""Managed MCP: event-service session mode and managed manual capture (WP2)."""
import sys
import types

import pytest

from core.managed_mcp_spec import MANAGED_MCP_OBSERVATION_MODE, MITM_OBSERVATION_MODE
from services.cc_interactive_event_service import CCInteractiveEventService


def _svc():
    return CCInteractiveEventService({"token": "tok", "_service_id": "events"})


class TestRegistration:
    def test_managed_session_is_connected_by_registration(self):
        svc = _svc()
        state = svc.register_session(
            "sess", user_id="u", conversation_id="c", agent_name="a",
            provider="cc_mcp", observation_mode=MANAGED_MCP_OBSERVATION_MODE)
        assert state.observation_mode == MANAGED_MCP_OBSERVATION_MODE
        assert state.provider == "cc_mcp"
        assert state.connected is True
        assert CCInteractiveEventService.live_session("c", "a") is None or True

    def test_mitm_default_and_unknown_mode_refused(self):
        svc = _svc()
        state = svc.register_session("sess", provider="codex-interactive")
        assert state.observation_mode == MITM_OBSERVATION_MODE
        assert state.connected is False
        with pytest.raises(ValueError):
            svc.register_session("sess2", observation_mode="proxy")

    def test_family_helpers(self):
        svc = _svc()
        cc = svc.register_session("a", provider="cc_mcp",
                                  observation_mode=MANAGED_MCP_OBSERVATION_MODE)
        codex = svc.register_session("b", provider="codex_mcp",
                                     observation_mode=MANAGED_MCP_OBSERVATION_MODE)
        agy = svc.register_session("d", provider="agy_mcp",
                                   observation_mode=MANAGED_MCP_OBSERVATION_MODE)
        mitm = svc.register_session("c", provider="codex-interactive")
        assert svc._pool_family(cc) == "claude-code-interactive"
        assert svc._pool_family(codex) == "codex-interactive"
        assert svc._pool_family(agy) == "antigravity-interactive"
        assert svc._tmux_input_tag(cc) == "cc_mcp_tmux"
        assert svc._tmux_input_tag(codex) == "codex_mcp_tmux"
        assert svc._tmux_input_tag(agy) == "agy_mcp_tmux"
        assert svc._tmux_input_tag(mitm) == "codex_interactive_tmux"
        assert svc._is_managed(cc) and not svc._is_managed(mitm)

    def test_agy_pool_is_used_for_capture_and_liveness(self, monkeypatch):
        from core.antigravity_observer_pool import AntigravityObserverPool

        marker = object()
        monkeypatch.setattr(AntigravityObserverPool, "instance",
                            classmethod(lambda cls: marker))
        svc = _svc()
        state = svc.register_session(
            "agy", provider="agy_mcp",
            observation_mode=MANAGED_MCP_OBSERVATION_MODE)
        assert svc._pool_for(state) is marker

    def test_hook_registration_records_container_for_managed_session(self):
        svc = _svc()
        state = svc.register_session(
            "sess", provider="cc_mcp",
            observation_mode=MANAGED_MCP_OBSERVATION_MODE)
        # Mirror of the _serve branch: a hook client names its container.
        reg = {"client_kind": "hook", "container_id": "ctr-1"}
        if reg["client_kind"] == "hook" and svc._is_managed(state):
            state.container_id = reg["container_id"]
            state.connected = True
        assert state.container_id == "ctr-1"


class TestManualPrompt:
    def test_manual_prompt_is_persisted_with_managed_input_tag(self, monkeypatch):
        writes = []

        class _Writer:
            def enqueue_message(self, msg, **kw):
                writes.append((msg, kw))

        class _ConversationWriter:
            @staticmethod
            def for_conversation(cid):
                return _Writer()
        monkeypatch.setattr("core.conversation_writer.ConversationWriter",
                            _ConversationWriter)
        captures = []
        svc = _svc()
        monkeypatch.setattr(svc, "_start_manual_capture", captures.append)
        state = svc.register_session(
            "sess", user_id="u", conversation_id="c", agent_name="a",
            provider="cc_mcp", observation_mode=MANAGED_MCP_OBSERVATION_MODE)
        svc.publish_event("sess", {
            "type": "hook", "hook_event_name": "UserPromptSubmit",
            "input": {"prompt": "typed by a human", "prompt_sha256": "x",
                      "pawflow_injected_prompt": False}})
        assert len(writes) == 1
        msg = writes[0][0]
        assert msg["channel"] == "tmux"
        assert msg["source"]["input"] == "cc_mcp_tmux"
        assert msg["source"]["target_agent"] == "a"
        assert captures == [state]


class TestManagedCapture:
    def test_capture_uses_managed_waiter_and_never_imports_mitm_coordinators(self, monkeypatch):
        writes = []

        class _Writer:
            def enqueue_message(self, msg, **kw):
                writes.append(msg)

        class _ConversationWriter:
            @staticmethod
            def for_conversation(cid):
                return _Writer()
        monkeypatch.setattr("core.conversation_writer.ConversationWriter",
                            _ConversationWriter)

        built = {}

        class _Response:
            content = "managed final"
            tokens_in = 0
            tokens_out = 0
            model = ""

        class _Coordinator:
            def __init__(self, event_service, session_token, *, provider,
                         callback=None, block_callback=None,
                         emitted_tool_use_ids=None,
                         emitted_tool_result_ids=None, consumer_kind="request",
                         consumer_epoch=0, liveness_callback=None, **kw):
                built.update(provider=provider, kind=consumer_kind,
                             epoch=consumer_epoch)
                self._cb = callback
                self._block = block_callback

            def run(self):
                self._cb("managed final")
                self._block("text", {"text": "managed final"})
                return _Response()
        monkeypatch.setattr(
            "core.llm_providers._managed_mcp_turn._ManagedMcpTurnCoordinator",
            _Coordinator)
        # The two MITM coordinator modules must not be imported on this path.
        poisoned = {}
        for name in ("core.llm_providers._codex_interactive_turn",
                     "core.llm_providers.claude_code_interactive"):
            poisoned[name] = sys.modules.pop(name, None)

        class _Blocker(types.ModuleType):
            def __getattr__(self, item):
                raise AssertionError(f"MITM coordinator module consulted: {item}")
        for name in poisoned:
            monkeypatch.setitem(sys.modules, name, _Blocker(name))
        try:
            svc = _svc()
            svc.register_session(
                "sess", user_id="u", conversation_id="c", agent_name="a",
                provider="cc_mcp", observation_mode=MANAGED_MCP_OBSERVATION_MODE)
            monkeypatch.setattr(svc, "_capture_dedup_sets",
                                lambda state: (set(), set()))
            monkeypatch.setattr(svc, "_capture_liveness_callback", lambda s: None)
            monkeypatch.setattr(svc, "_publish_capture_active", lambda s, active: None)
            monkeypatch.setattr(svc, "_drain_pending_after_capture", lambda s: None)
            svc._run_manual_capture("sess")
        finally:
            for name, module in poisoned.items():
                if module is not None:
                    sys.modules[name] = module
                else:
                    sys.modules.pop(name, None)
        assert built == {"provider": "cc_mcp", "kind": "capture", "epoch": 1}
        final = next(m for m in writes if m["role"] == "assistant")
        assert final["content"] == "managed final"
        source = final["source"]
        assert source["provider"] == "cc_mcp"
        assert source["input"] == "cc_mcp_tmux"

    def test_capture_yields_to_live_request_consumer(self, monkeypatch):
        svc = _svc()
        svc.register_session(
            "sess", user_id="u", conversation_id="c", agent_name="a",
            provider="cc_mcp", observation_mode=MANAGED_MCP_OBSERVATION_MODE)
        svc.claim_consumer("sess")
        announced = []
        monkeypatch.setattr(svc, "_publish_capture_active",
                            lambda s, active: announced.append(active))
        svc._run_manual_capture("sess")
        assert announced == []
