"""Managed MCP: the thin provider facade (WP4 isolated part).

The shared LLMClient registry is integrated by the assistant; these tests
drive ``LLMManagedMcpMixin`` on a stub client with a fake pool, a fake event
service and a fake coordinator, so the isolated contract is proven before the
registry lands: exactly one paste, one claim/release, one final answer, and no
MITM fallback for any of the three providers.
"""
import inspect
from types import SimpleNamespace

import pytest

from core.llm_client import LLMClientError, LLMMessage, LLMResponse
from core.llm_providers.codex_interactive import LLMCodexInteractiveMixin
from core.llm_providers.managed_mcp import LLMManagedMcpMixin


class _Client(LLMManagedMcpMixin):
    default_model = "default-model"

    def __init__(self):
        self._user_id = "u"
        self._conversation_id = "c"
        self._agent_name = "a"
        self._agent_service = "svc"
        self._abort = None
        self.cold_checks = []
        self.delta_checks = []

    def _cli_require_cold_context(self, provider):
        self.cold_checks.append(provider)

    def _cli_require_delta_context(self, provider):
        self.delta_checks.append(provider)

    def _cci_prompt(self, messages, tools, workdir, container_workdir,
                    user_id, conversation_id, initial_context=False,
                    agent_name="", state=None):
        return ("COLD " if initial_context else "DELTA ") + "prompt"


class _State:
    def __init__(self, provider="cc_mcp"):
        self.session_token = "sess"
        self.workdir = "/w"
        self.container_workdir = "/cc_sessions/c/a"
        self.initial_context_loaded = False
        self.emitted_tool_use_ids = set()
        self.emitted_tool_result_ids = set()
        self.submitted_msg_ids = set()
        self.name = "ctr"
        self.last_error = ""
        self.service_id = "svc"
        self.provider = provider
        self.created_at = 0.0


class _Pool:
    def __init__(self, state, send_ok=True):
        self.state = state
        self.send_ok = send_ok
        self.sent = []
        self.turns = []
        self.launch_callbacks = []
        self.stopped = []
        self.interrupts = []

    def ensure_started(self, client, model, user_id, conversation_id,
                       agent_name, before_launch=None):
        if before_launch is not None:
            self.launch_callbacks.append(before_launch)
            before_launch()
        return self.state

    def begin_turn(self, state):
        self.turns.append("begin")

    def end_turn(self, state):
        self.turns.append("end")

    def send_text(self, state, prompt):
        self.sent.append(prompt)
        return self.send_ok

    def send_interrupt(self, state, text):
        self.interrupts.append(text)
        return self.send_ok

    def touch(self, state):
        pass

    def session_is_live(self, name):
        return True

    def find_session(self, uid, cid, agent, service_id=""):
        return self.state

    def force_stop(self, state):
        self.stopped.append(state)
        return True

    def kill_session(self, *a, **k):
        pass

    def destroy_ephemeral(self, state):
        pass


class _Events:
    def __init__(self):
        self.claims = 0
        self.releases = []
        self.drained = 0

    def claim_consumer(self, token, kind="request"):
        self.claims += 1
        return 7

    def release_consumer(self, token, epoch=0):
        self.releases.append(epoch)

    def drain_session(self, token):
        self.drained += 1


@pytest.fixture()
def harness(monkeypatch):
    state = _State()
    pool = _Pool(state)
    events = _Events()
    built = []

    class _Coordinator:
        def __init__(self, event_service, session_token, *, provider,
                     callback=None, block_callback=None, turn_callback=None,
                     touch_callback=None, emitted_tool_use_ids=None,
                     emitted_tool_result_ids=None, consumer_epoch=0,
                     liveness_callback=None, started_at=0.0, **kw):
            built.append({"provider": provider, "epoch": consumer_epoch,
                          "started_at": started_at})
            self._cb = callback

        def run(self, abort=None):
            if self._cb:
                self._cb("final")
            return LLMResponse(content="final", tool_calls=[], model="",
                               raw={"provider": "cc_mcp"})
    monkeypatch.setattr(
        "core.llm_providers.managed_mcp._ManagedMcpTurnCoordinator", _Coordinator)
    monkeypatch.setattr(LLMManagedMcpMixin, "_managed_mcp_pool",
                        staticmethod(lambda spec: pool))
    monkeypatch.setattr(
        "services.cc_interactive_event_service.get_or_create_cc_interactive_event_service",
        lambda: ("wss://x", "t", events))
    return SimpleNamespace(state=state, pool=pool, events=events, built=built)


def _messages():
    return [LLMMessage(role="user", content="hello", conversation_id="c",
                       msg_id="m1")]


class TestStream:
    def test_cold_turn_pastes_once_and_returns_the_hook_final(self, harness):
        client = _Client()
        texts = []
        response = client._stream_cc_mcp(_messages(), "opus", callback=texts.append)
        assert response.content == "final"
        assert response.model == "opus"
        assert response.tool_calls == []
        assert harness.pool.sent == ["COLD prompt"]
        assert client.cold_checks == ["cc_mcp"] and client.delta_checks == []
        assert harness.state.initial_context_loaded is True
        assert harness.state.submitted_msg_ids == {"m1"}
        assert harness.events.claims == 1 and harness.events.drained == 1
        assert harness.events.releases == [7]
        assert harness.pool.turns == ["begin", "end"]
        assert harness.built[0]["provider"] == "cc_mcp"
        assert harness.built[0]["epoch"] == 7
        assert harness.built[0]["started_at"] > 0
        assert texts == ["final"]
        assert client._cci_active_agent_name == "a"

    def test_reused_session_is_a_delta_turn(self, harness):
        harness.state.initial_context_loaded = True
        client = _Client()
        client._stream_codex_mcp(_messages(), "gpt-5")
        assert harness.pool.sent == ["DELTA prompt"]
        assert client.delta_checks == ["codex_mcp"]
        assert client._codex_interactive_active_agent_name == "a"

    def test_failed_paste_releases_claim_and_never_replays(self, harness):
        harness.pool.send_ok = False
        harness.state.last_error = "tmux paste-buffer failed"
        client = _Client()
        with pytest.raises(LLMClientError, match="tmux paste-buffer failed"):
            client._stream_cc_mcp(_messages(), "opus")
        assert harness.pool.sent == ["COLD prompt"]
        assert harness.events.releases == [7]
        assert harness.pool.turns == ["begin", "end"]
        assert harness.built == []

    def test_agy_uses_the_same_managed_turn_contract(self, harness):
        harness.state.provider = "agy_mcp"
        client = _Client()
        response = client._stream_agy_mcp(_messages(), "gemini")
        assert response.content == "final"
        assert harness.pool.sent == ["COLD prompt"]
        assert harness.pool.turns == ["begin", "end"]
        assert harness.built[0]["provider"] == "agy_mcp"

    def test_identity_is_required(self, harness):
        client = _Client()
        client._agent_name = ""
        with pytest.raises(LLMClientError, match="agent_name"):
            client._stream_cc_mcp(_messages(), "opus")


class TestInterruptCancelPreempt:
    def test_interrupt_without_session_is_a_noop_not_an_error(self, harness, monkeypatch):
        client = _Client()
        monkeypatch.setattr(harness.pool, "find_session", lambda *a, **k: None)
        response = client.interrupt_cc_mcp("STOP", user_id="u", conversation_id="c",
                                           agent_name="a")
        assert response.content == "" and response.model == "default-model"

    def test_interrupt_with_session_sends_and_waits_for_final(self, harness):
        client = _Client()
        response = client.interrupt_cc_mcp("STOP", user_id="u", conversation_id="c",
                                           agent_name="a", model="opus")
        assert harness.pool.interrupts == ["STOP"]
        assert response.content == "final"
        assert harness.events.releases == [7]

    def test_session_of_the_mitm_twin_is_never_used(self, harness):
        harness.state.provider = "claude-code-interactive"
        client = _Client()
        response = client.interrupt_cc_mcp("STOP", user_id="u", conversation_id="c",
                                           agent_name="a")
        assert response.content == ""
        assert harness.pool.interrupts == []

    def test_force_stop_only(self, harness):
        client = _Client()
        client._stream_cc_mcp(_messages(), "opus")
        assert client.cancel_cc_mcp(force=False) is False
        assert client.cancel_cc_mcp(force=True) is True
        assert harness.pool.stopped == [harness.state]
        assert client.cancel_agy_mcp(force=True) is False

    def test_live_preempt_is_not_advertised(self, harness):
        client = _Client()
        assert client._cc_mcp_send_user_message("x", user_id="u") is False
        assert client._codex_mcp_send_user_message("x") is False
        assert client._agy_mcp_send_user_message("x") is False


class TestDispatchParity:
    def test_stream_signatures_match_the_interactive_twins(self):
        """The driver passes one kwargs set to every CLI provider."""
        reference = inspect.signature(
            LLMCodexInteractiveMixin._stream_codex_interactive)
        for name in ("_stream_cc_mcp", "_stream_codex_mcp", "_stream_agy_mcp"):
            candidate = inspect.signature(getattr(LLMManagedMcpMixin, name))
            assert list(candidate.parameters) == list(reference.parameters), name
            for pname, param in reference.parameters.items():
                assert candidate.parameters[pname].kind == param.kind, (name, pname)

    def test_capability_matrix_is_exposed_on_the_client(self):
        matrix = _Client().managed_mcp_capabilities()
        assert matrix["cc_mcp"]["available"] is True
        assert matrix["agy_mcp"]["available"] is True
