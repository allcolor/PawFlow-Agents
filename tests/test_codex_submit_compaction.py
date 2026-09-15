"""Native Codex compaction must preempt even before submission is acknowledged."""

import pytest

from core.codex_interactive_pool import CodexInteractivePool
from core.llm_client import CCCompactDetected, LLMClient
from services.cc_interactive_event_service import CCInteractiveEventService
from tests.test_codex_interactive_provider import _state


PROMPT = "Continue the pending work."


def _events(provider="codex-interactive"):
    service = CCInteractiveEventService({"token": "tok", "_service_id": "events"})
    service.register_session("sess", provider=provider)
    service.remember_injected_prompt("sess", PROMPT)
    return service


@pytest.mark.parametrize("hook", ["PreCompact", "PostCompact"])
def test_compaction_wakes_submit_wait_without_consuming_event(monkeypatch, hook):
    service = _events()
    state = service.session_state("sess")
    marker = service.submission_marker("sess")
    event = {"type": "hook", "hook_event_name": hook}
    waits = []

    def publish_while_waiting(_timeout):
        waits.append(True)
        assert len(waits) == 1, "compaction did not wake the submission waiter"
        service.publish_event("sess", event)

    monkeypatch.setattr(state.stream_condition, "wait", publish_while_waiting)
    assert service.wait_for_prompt_submission(
        "sess", PROMPT, after_submit=marker[0], after_request=marker[1],
        timeout=60) == hook
    assert waits == [True]
    assert service.wait_event("sess", timeout=0) == event


def test_compaction_survives_drain_and_takes_priority_over_provider_receipt():
    service = _events()
    service.publish_event("sess", {
        "type": "hook", "hook_event_name": "PreCompact",
    })
    service.drain_session("sess")
    marker = service.submission_marker("sess")
    service.publish_event("sess", {
        "type": "request_start", "request_id": "r1",
        "path": "/backend-api/codex/responses",
    })

    assert service.wait_for_prompt_submission(
        "sess", PROMPT, after_submit=marker[0], after_request=marker[1],
        timeout=0) == "PreCompact"
    service.register_session("replacement", provider="codex-interactive")
    assert service.wait_for_prompt_submission(
        "replacement", PROMPT, after_submit=0, after_request=0, timeout=0) == ""


def test_claude_submission_wait_keeps_its_existing_compaction_contract():
    service = _events("claude-code-interactive")
    service.publish_event("sess", {
        "type": "hook", "hook_event_name": "PreCompact",
    })
    assert service.wait_for_prompt_submission(
        "sess", PROMPT, after_submit=0, after_request=0, timeout=0) == ""


@pytest.mark.parametrize("interrupt", [False, True])
@pytest.mark.parametrize("hook", ["PreCompact", "PostCompact"])
@pytest.mark.parametrize("replacement", [False, True])
@pytest.mark.parametrize("arrival", [
    "preexisting", "submit", "readiness", "load", "paste", "escape",
    "enter_failure", "transport_exception", "not_alive", "coordinator",
])
def test_provider_preempts_native_compaction_during_real_send(
        monkeypatch, interrupt, hook, replacement, arrival):
    client = LLMClient("codex-interactive")
    state = _state(("user", "conv", "assistant", "svc"))
    state.prompt_ready = True
    pool = CodexInteractivePool()
    service = CCInteractiveEventService({"token": "tok", "_service_id": "events"})
    event_state = service.register_session(
        state.session_token, provider="codex-interactive")
    keys = []
    ended = []
    killed = []
    recovered = []
    pool._sessions[state.key] = state
    next_state = _state(state.key)
    next_state.name = "replacement-container"
    next_state.session_token = "replacement-token"
    next_event_state = service.register_session(
        next_state.session_token, provider="codex-interactive")

    def publish_compaction():
        # Deterministically replace the key after this turn captured its state.
        if replacement:
            with pool._lock:
                pool._sessions[state.key] = next_state
        service.publish_event(state.session_token, {
            "type": "hook", "hook_event_name": hook,
        })

    monkeypatch.setenv("PAWFLOW_CCI_SUBMIT_VERIFY_SECONDS", "0.01")
    monkeypatch.setattr(
        CodexInteractivePool, "instance", classmethod(lambda cls: pool))
    monkeypatch.setattr(pool, "ensure_started", lambda *_a, **_kw: state)
    monkeypatch.setattr(pool, "begin_turn", lambda _state: None)
    monkeypatch.setattr(pool, "end_turn", lambda item: ended.append(item))
    monkeypatch.setattr(pool, "_kill_container", lambda name: killed.append(name))
    monkeypatch.setattr(
        pool, "_recover_container_tokens", lambda item: recovered.append(item))
    monkeypatch.setattr(pool, "_is_alive", lambda _name: True)
    monkeypatch.setattr(pool, "_cancel_copy_mode", lambda _state: None)
    monkeypatch.setattr(pool, "_wait_for_prompt_ready", lambda _name: True)
    monkeypatch.setattr(pool, "_remember_injected_prompt", lambda *_a: None)

    def remember(_state, text):
        service.remember_injected_prompt(state.session_token, text)
        return service

    monkeypatch.setattr(pool, "_remember_injected_prompt_for_event_service", remember)
    monkeypatch.setattr(pool, "_load_buffer", lambda *_a: True)
    monkeypatch.setattr(pool, "_paste_buffer", lambda *_a: True)
    monkeypatch.setattr(pool, "_paste_landed", lambda *_a: True)
    monkeypatch.setattr(pool, "_paste_settle_seconds", lambda: 0)
    monkeypatch.setattr(pool, "_submit_delay_seconds", lambda: 0)

    def no_pane_after_compaction(_name):
        assert not killed
        assert not any(batch == ["Enter"] for batch in keys), (
            "submission inspected the pane instead of preempting compaction")
        return ""

    monkeypatch.setattr(pool, "_pane_text", no_pane_after_compaction)

    def send_keys(_state, batch):
        keys.append(batch)
        if batch == ["Enter"]:
            publish_compaction()
        return True

    monkeypatch.setattr(pool, "send_keys", send_keys)
    monkeypatch.setattr(client, "_cci_prompt", lambda *_a, **_kw: PROMPT)
    monkeypatch.setattr(client, "_codex_interactive_session_state", lambda **_kw: state)
    monkeypatch.setattr(
        "services.cc_interactive_event_service.get_or_create_cc_interactive_event_service",
        lambda: ("", "", service))

    def compact(*_args, **_kwargs):
        publish_compaction()
        if arrival == "transport_exception":
            raise RuntimeError("transport failed")
        return False

    if arrival == "coordinator":
        def submitted(*_args):
            publish_compaction()
            return True
        monkeypatch.setattr(pool, "send_interrupt" if interrupt else "send_text", submitted)
    elif arrival == "preexisting":
        compact()
    elif arrival == "readiness":
        state.prompt_ready = False
        monkeypatch.setattr(pool, "_wait_for_prompt_ready", compact)
    elif arrival in {"load", "transport_exception"}:
        monkeypatch.setattr(pool, "_load_buffer", compact)
    elif arrival == "paste":
        monkeypatch.setattr(pool, "_paste_buffer", compact)
    elif arrival == "not_alive":
        monkeypatch.setattr(pool, "_is_alive", compact)
    elif arrival in {"escape", "enter_failure"}:
        def fail_keys(_state, batch):
            keys.append(batch)
            if batch == (["Escape", "Escape"] if arrival == "escape" else ["Enter"]):
                return compact()
            return True
        monkeypatch.setattr(pool, "send_keys", fail_keys)

    with pytest.raises(CCCompactDetected, match=hook):
        if interrupt:
            client.interrupt_codex_interactive(
                PROMPT, user_id="user", conversation_id="conv",
                agent_name="assistant")
        else:
            client._stream_codex_interactive(
                [], "", call_user_id="user", call_conversation_id="conv",
                call_agent_name="assistant")

    assert killed == ([] if replacement else [state.name])
    assert recovered == ([] if replacement else [state])
    if replacement:
        assert pool._sessions[state.key] is next_state
    else:
        assert state.key not in pool._sessions
        assert service.session_state(state.session_token) is None
        assert event_state.closed is True
    assert service.session_state(next_state.session_token) is next_event_state
    assert next_event_state.closed is False
    assert ended == [state]
    assert event_state.active_request_consumer_epoch == 0
    assert state.initial_context_loaded is (arrival == "coordinator" and not interrupt)
    assert not state.submitted_msg_ids
    assert state.last_error == ""
    expected = [] if arrival in {"preexisting", "not_alive", "coordinator"} else [["Escape", "Escape"]]
    if arrival == "readiness" and not interrupt:
        expected = []
    if arrival == "submit":
        expected += [["Enter"], ["Enter"]]
    elif arrival == "enter_failure":
        expected += [["Enter"]]
    assert keys == expected


@pytest.mark.parametrize("hook", ["PreCompact", "PostCompact"])
def test_readiness_preempts_compaction_before_another_probe(monkeypatch, hook):
    state = _state(("user", "conv", "assistant", "svc"))
    pool = CodexInteractivePool()
    pool._sessions[state.key] = state
    service = CCInteractiveEventService({"token": "tok", "_service_id": "events"})
    service.register_session(state.session_token, provider="codex-interactive")
    monkeypatch.setattr(
        "services.cc_interactive_event_service.get_or_create_cc_interactive_event_service",
        lambda: ("", "", service))

    def probe(_name):
        service.publish_event(state.session_token, {"type": "hook", "hook_event_name": hook})
        return None

    monkeypatch.setattr(pool, "_codex_readiness_state", probe)
    monkeypatch.setattr("core.codex_interactive_pool.time.sleep",
                        lambda _seconds: pytest.fail("waited after native compaction"))
    with pytest.raises(CCCompactDetected, match=hook):
        pool._wait_for_prompt_ready(state.name, timeout=60)


def test_live_message_keeps_rescue_queued_while_turn_owner_preempts(monkeypatch):
    from types import SimpleNamespace
    from core.llm_providers._codex_interactive_turn import (
        _CodexInteractiveTurnCoordinator)

    client = LLMClient("codex-interactive")
    state = _state(("user", "conv", "assistant", "svc"))
    service = _events()
    state.session_token = "sess"

    def compact_during_interrupt(_state, _prompt):
        service.publish_event("sess", {
            "type": "hook", "hook_event_name": "PreCompact",
        })
        raise CCCompactDetected("PreCompact")

    pool = SimpleNamespace(send_interrupt=compact_during_interrupt)
    monkeypatch.setattr(
        CodexInteractivePool, "instance", classmethod(lambda cls: pool))
    monkeypatch.setattr(client, "_codex_interactive_session_state", lambda **_kw: state)
    monkeypatch.setattr(client, "_cci_preempt_prompt", lambda *_a, **_kw: PROMPT)

    assert client.send_user_message(
        PROMPT, user_id="user", conversation_id="conv",
        agent_name="assistant") is False
    assert not getattr(client, "_had_preempts_this_turn", False)
    with pytest.raises(CCCompactDetected, match="PreCompact"):
        _CodexInteractiveTurnCoordinator(service, "sess").run()


def test_kill_session_unregisters_its_latched_event_state(monkeypatch):
    state = _state(("user", "conv", "assistant", "svc"))
    service = CCInteractiveEventService({"token": "tok", "_service_id": "events"})
    event_state = service.register_session(
        state.session_token, provider="codex-interactive")
    service.publish_event(state.session_token, {
        "type": "hook", "hook_event_name": "PreCompact",
    })
    pool = CodexInteractivePool()
    pool._sessions[state.key] = state
    killed = []
    monkeypatch.setattr(pool, "_recover_container_tokens", lambda _state: None)
    monkeypatch.setattr(pool, "_kill_container", lambda name: killed.append(name))
    monkeypatch.setattr(
        "services.cc_interactive_event_service.get_or_create_cc_interactive_event_service",
        lambda: ("", "", service))

    assert pool.kill_session("user", "conv", "assistant", "svc") is True
    assert killed == [state.name]
    assert state.key not in pool._sessions
    assert service.session_state(state.session_token) is None
    assert event_state.closed is True
