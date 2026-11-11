"""Codex interactive startup and question hooks reach the PawFlow client."""

import asyncio
import io
import json
import re
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core.codex_interactive_pool import _CodexInteractiveSpawnMixin
from core.native_hook_input import answer_codex_hook
from services.cc_interactive_event_service import CCInteractiveEventService
from tools import cc_interactive_hook as hook


def blocking_raw():
    return {"hook_event_name": "PreToolUse", "tool_name": "request_user_input",
            "tool_use_id": "call-1", "session_id": "thread-1",
            "tool_input": {"questions": [
                {"id": "color", "header": "Color", "question": "Which color?",
                 "options": [{"label": "Red", "description": "warm"},
                             {"label": "Blue", "description": "cold"}]}]}}


def async_raw():
    return {"hook_event_name": "PreToolUse", "tool_name": "request_user_input_async",
            "tool_use_id": "call-2", "session_id": "thread-1",
            "tool_input": {"questions": [
                {"title": "Ship now?", "options": ["Yes", "Later"]}]}}


class InteractionStore:
    def __init__(self, current):
        self.current = current
        self.requests = []

    def create_interaction(self, **kwargs):
        self.requests.append(kwargs)
        return {"request_id": "pawflow-interaction"}

    def get_confirmation(self, request_id):
        return self.current

    def cancel(self, request_id, **kwargs):
        self.current = {"status": "cancelled"}


@pytest.fixture
def store(monkeypatch):
    store = InteractionStore({"status": "pending"})
    monkeypatch.setattr("core.confirmation_store.UserInteractionStore.instance",
                        lambda: store)
    return store


def answer(raw, cancel=None):
    return answer_codex_hook(
        raw, provider="codex-interactive", user_id="user",
        conversation_id="conversation", agent_name="agent",
        cancel_event=cancel or threading.Event())


def test_hooks_json_registers_session_start_and_question_tools(tmp_path):
    _CodexInteractiveSpawnMixin._write_codex_hooks(str(tmp_path))
    hooks = json.loads((tmp_path / ".codex" / "hooks.json").read_text())["hooks"]
    assert "SessionStart" in hooks
    (entry,) = hooks["PreToolUse"]
    matcher = re.compile(entry["matcher"])
    for tool in ("request_user_input", "request_user_input_async"):
        assert matcher.search(tool)
    assert not matcher.search("shell")
    handler = entry["hooks"][0]
    assert handler["command"].endswith("--event PreToolUse")
    assert handler["timeout"] == 3600


def test_blocking_question_returns_answers_as_deny_reason(store):
    store.current = {"status": "answered", "answer": {"question_0": "Blue"}}
    specific = answer(blocking_raw())["hookSpecificOutput"]
    assert specific["hookEventName"] == "PreToolUse"
    assert specific["permissionDecision"] == "deny"
    reason = specific["permissionDecisionReason"]
    payload = json.loads(reason[reason.index("["):])
    assert payload == [{"id": "color", "question": "Which color?", "answer": "Blue"}]
    (request,) = store.requests
    assert request["requester_kind"] == "provider"
    assert request["response_schema"]["fields"][0]["allow_other"] is True


def test_cancelled_question_denies_without_answers(store):
    store.current = {"status": "cancelled"}
    reason = answer(blocking_raw())["hookSpecificOutput"]["permissionDecisionReason"]
    assert "cancelled" in reason


def test_cancel_event_denies_before_any_form(store):
    cancel = threading.Event()
    cancel.set()
    specific = answer(blocking_raw(), cancel)["hookSpecificOutput"]
    assert specific["permissionDecision"] == "deny"
    assert not store.requests


def test_async_question_publishes_form_and_lets_tool_run(store):
    # Codex rejects a bare "allow"; no decision is its documented pass-through.
    assert answer(async_raw()) == {"hookSpecificOutput": {"hookEventName": "PreToolUse"}}
    (request,) = store.requests
    assert request["continuation"] == {
        "provider": "codex-interactive", "response_mode": "message",
        "thread_id": "thread-1", "item_id": "call-2"}
    assert request["response_schema"]["fields"][0]["label"] == "Ship now?"


@pytest.mark.parametrize("raw", [
    {"hook_event_name": "Stop"},
    {**blocking_raw(), "tool_name": "shell"},
    {**blocking_raw(), "tool_use_id": ""},
    {**blocking_raw(), "tool_input": []},
])
def test_invalid_codex_hooks_are_rejected(store, raw):
    with pytest.raises(ValueError):
        answer(raw)
    assert not store.requests


@pytest.mark.parametrize("specific,accepted", [
    ({"permissionDecision": "deny", "permissionDecisionReason": "answers"}, True),
    ({}, True),
    ({"permissionDecision": "allow"}, False),
    ({"permissionDecision": "deny", "permissionDecisionReason": "  "}, False),
    ({"permissionDecision": "ask", "permissionDecisionReason": "x"}, False),
])
def test_hook_script_forwards_only_valid_codex_outputs(monkeypatch, specific, accepted):
    output = {"hookSpecificOutput": {"hookEventName": "PreToolUse", **specific}}
    monkeypatch.setattr(hook, "_connect", lambda *args: Mock())
    monkeypatch.setattr(hook, "_recv_json", lambda *args: {
        "type": "native_input_result", "output": output})
    result = hook._interaction_output(blocking_raw(), "ws://test", "token", "session",
                                      client="codex")
    if accepted:
        assert result == output
    else:
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert result["hookSpecificOutput"]["permissionDecisionReason"] != "answers"


@pytest.mark.parametrize("raw", [blocking_raw(), async_raw()])
def test_hook_script_routes_codex_questions_to_pawflow(monkeypatch, capsys, raw):
    calls = []
    monkeypatch.setenv("PAWFLOW_CCI_HOOK_CLIENT", "codex")
    monkeypatch.setattr(hook.sys, "argv", ["hook", "--event", "PreToolUse"])
    monkeypatch.setattr(hook.sys, "stdin", io.StringIO(json.dumps(raw)))
    monkeypatch.setattr(hook, "_interaction_output", lambda raw, *args, client: (
        calls.append((raw["tool_name"], client)) or {"routed": True}))
    assert hook.main() == 0
    assert json.loads(capsys.readouterr().out) == {"routed": True}
    assert calls == [(raw["tool_name"], "codex")]


def test_codex_hook_script_denies_malformed_question_stdin(monkeypatch, capsys):
    monkeypatch.setenv("PAWFLOW_CCI_HOOK_CLIENT", "codex")
    monkeypatch.setattr(hook.sys, "argv", ["hook", "--event", "PreToolUse"])
    monkeypatch.setattr(hook.sys, "stdin", io.StringIO("not json"))
    assert hook.main() == 0
    specific = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert specific["permissionDecision"] == "deny"
    assert specific["permissionDecisionReason"].strip()


@pytest.mark.parametrize("provider,routed", [
    ("codex-interactive", True), ("codex_mcp", True), ("gemini", False)])
def test_server_routes_native_input_by_provider(monkeypatch, provider, routed):
    from services import _cci_native_input

    sent = []

    async def send(writer, payload, opcode=1):
        sent.append(json.loads(payload))

    monkeypatch.setattr("services.filesystem_service._ws_send_frame", send)
    monkeypatch.setattr(_cci_native_input, "answer_codex_hook",
                        lambda raw, **kwargs: {"codex": kwargs["provider"]})
    state = SimpleNamespace(provider=provider, user_id="user",
                            conversation_id="conversation", agent_name="agent")
    asyncio.run(_cci_native_input.answer_native_input(
        state, blocking_raw(), threading.Event(), Mock()))
    if routed:
        assert sent == [{"type": "native_input_result", "output": {"codex": provider}}]
    else:
        assert sent[0]["type"] == "error"


@pytest.fixture
def writer(monkeypatch):
    writer = Mock()
    monkeypatch.setattr("core.conversation_writer.ConversationWriter.for_conversation",
                        lambda conversation_id: writer)
    return writer


def session(provider, conversation_id="conversation"):
    svc = CCInteractiveEventService({"token": "token", "_service_id": "events"})
    state = svc.register_session("session", user_id="user", conversation_id=conversation_id,
                                 agent_name="agent", provider=provider)
    return svc, state


def start_event():
    return {"type": "hook", "hook_event_name": "SessionStart",
            "input": {"hook_event_name": "SessionStart", "source": "startup"}}


def test_codex_session_start_is_published_once_outside_turn_queue(writer):
    svc, state = session("codex-interactive")
    svc._publish_codex_session_start(state, start_event())
    svc._publish_codex_session_start(state, start_event())
    writer.enqueue_message.assert_called_once()
    message = writer.enqueue_message.call_args.args[0]
    kwargs = writer.enqueue_message.call_args.kwargs
    assert message["role"] == "system"
    assert message["source"]["event"] == "session_start"
    assert message["msg_id"] and message["ts"]
    assert kwargs["agent_name"] == "agent" and kwargs["user_id"] == "user"
    (sse,) = kwargs["sse_events"]
    assert sse["type"] == "new_message" and sse["data"]["agent_name"] == "agent"
    assert state.session_start_published


@pytest.mark.parametrize("provider,conversation_id,event", [
    ("claude-code-interactive", "conversation", start_event()),
    ("codex-interactive", "conversation__ephemeral_x", start_event()),
    ("codex-interactive", "conversation", {"type": "hook", "hook_event_name": "Stop"}),
])
def test_session_start_publish_is_codex_startup_only(writer, provider, conversation_id, event):
    svc, state = session(provider, conversation_id)
    svc._publish_codex_session_start(state, event)
    writer.enqueue_message.assert_not_called()
