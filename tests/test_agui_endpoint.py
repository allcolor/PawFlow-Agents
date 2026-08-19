"""AG-UI protocol endpoint: RunAgentInput → PawFlow turn → AG-UI SSE stream."""

import json

import pytest

import core.agui_runtime as agui
from core.agui_runtime import (
    _TurnTranslator, agui_event, extract_run, run_agent_stream, sse_frame)


def _events(frames):
    """Decode SSE byte frames back into event dicts (skipping comments)."""
    out = []
    for frame in frames:
        text = frame.decode("utf-8")
        if text.startswith(":"):
            continue
        assert text.startswith("data: ") and text.endswith("\n\n")
        out.append(json.loads(text[len("data: "):]))
    return out


# ── Input parsing ────────────────────────────────────────────────

def test_extract_run_requires_thread_and_user_message():
    with pytest.raises(ValueError, match="threadId"):
        extract_run({"runId": "r", "messages": [
            {"id": "1", "role": "user", "content": "hi"}]})
    with pytest.raises(ValueError, match="messages"):
        extract_run({"threadId": "t", "runId": "r", "messages": []})
    with pytest.raises(ValueError, match="user message"):
        extract_run({"threadId": "t", "runId": "r", "messages": [
            {"id": "1", "role": "assistant", "content": "hello"}]})


def test_extract_run_takes_the_last_user_message_and_flattens_parts():
    thread_id, run_id, prompt = extract_run({
        "threadId": "t1", "runId": "r1",
        "messages": [
            {"id": "1", "role": "user", "content": "older question"},
            {"id": "2", "role": "assistant", "content": "older answer"},
            {"id": "3", "role": "user", "content": [
                {"type": "text", "text": "look at this"},
                {"type": "image",
                 "source": {"type": "url", "value": "https://x/img.png"}},
            ]},
        ],
        "context": [{"description": "page", "value": "/checkout"}],
        "tools": [{"name": "confirm", "description": "ask the user"}],
    })
    assert (thread_id, run_id) == ("t1", "r1")
    assert "look at this" in prompt
    assert "https://x/img.png" in prompt
    assert "older question" not in prompt   # server keeps its own history
    assert "page: /checkout" in prompt
    assert "confirm: ask the user" in prompt


def test_agui_events_are_camel_case_without_nulls():
    event = agui_event("TEXT_MESSAGE_START", messageId="m1",
                       role="assistant", parentMessageId=None)
    assert event["type"] == "TEXT_MESSAGE_START"
    assert event["messageId"] == "m1"
    assert "parentMessageId" not in event
    assert isinstance(event["timestamp"], int)
    frame = sse_frame(event).decode("utf-8")
    assert frame.startswith("data: {") and frame.endswith("\n\n")


# ── Bus-event translation ────────────────────────────────────────

def test_translator_pairs_text_thinking_and_tool_blocks():
    tr = _TurnTranslator()
    out = []
    out += tr.translate("thinking_delta", {"text": "hmm"})
    out += tr.translate("token", {"text": "Hel", "msg_id": "m1"})
    out += tr.translate("token", {"text": "lo", "msg_id": "m1"})
    out += tr.translate("tool_call", {"tool": "grep", "tc_id": "tc1",
                                      "arguments": {"pattern": "x"},
                                      "msg_id": "m1"})
    out += tr.translate("tool_result", {"tool": "grep", "tc_id": "tc1",
                                        "result": "found", "msg_id": "m2"})
    out += tr.close_open_blocks()
    types = [e["type"] for e in out]
    assert types == [
        "THINKING_START", "THINKING_TEXT_MESSAGE_START",
        "THINKING_TEXT_MESSAGE_CONTENT",
        # token closes the thinking block before opening the message
        "THINKING_TEXT_MESSAGE_END", "THINKING_END",
        "TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_CONTENT",
        # tool_call closes the open message
        "TEXT_MESSAGE_END",
        "TOOL_CALL_START", "TOOL_CALL_ARGS", "TOOL_CALL_END",
        "TOOL_CALL_RESULT",
    ]
    start = next(e for e in out if e["type"] == "TOOL_CALL_START")
    assert start["toolCallId"] == "tc1"
    assert start["toolCallName"] == "grep"
    args = next(e for e in out if e["type"] == "TOOL_CALL_ARGS")
    assert json.loads(args["delta"]) == {"pattern": "x"}
    result = next(e for e in out if e["type"] == "TOOL_CALL_RESULT")
    assert result["content"] == "found" and result["role"] == "tool"


def test_translator_dedupes_the_persisted_row_of_a_streamed_message():
    tr = _TurnTranslator()
    tr.translate("token", {"text": "Hello", "msg_id": "m1"})
    out = tr.translate("new_message", {"role": "assistant",
                                       "content": "Hello", "msg_id": "m1"})
    assert [e["type"] for e in out] == ["TEXT_MESSAGE_END"]
    # And it never replays a message id it already emitted in full.
    assert tr.translate("new_message", {"role": "assistant",
                                        "content": "Hello",
                                        "msg_id": "m1"}) == []


def test_translator_ignores_heartbeat_thinking_without_text():
    tr = _TurnTranslator()
    assert tr.translate("thinking", {"waiting_seconds": 4}) == []


def test_unstreamed_assistant_message_is_emitted_in_full():
    tr = _TurnTranslator()
    out = tr.translate("new_message", {"role": "assistant",
                                       "content": "whole answer",
                                       "msg_id": "m9"})
    assert [e["type"] for e in out] == [
        "TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END"]


# ── Full run stream ──────────────────────────────────────────────

_PUBLICATION = {
    "publication_id": "a2ap_test", "owner_user_id": "uid",
    "conversation_id": "conv0", "agent_name": "assistant",
    "label": "Test", "context_policy": "isolated", "enabled": True,
}
_KEY = {"key_id": "k1"}


class _Store:
    def resolve_context(self, publication, key_id, requested):
        assert requested == "agui_t1"
        return {"context_id": "ctx1", "internal_conversation_id": "conv1"}


def _run_input():
    return {"threadId": "t1", "runId": "r1", "state": None,
            "messages": [{"id": "1", "role": "user", "content": "hi"}],
            "tools": [], "context": [], "forwardedProps": None}


def _patch_runtime(monkeypatch, live_script, result):
    """Fake AgentRuntimeAPI: submit captures the live callback; the waiter
    replays `live_script` through it and returns `result`."""
    from core import agent_runtime_api as runtime
    from core.a2a_store import A2AStore
    captured = {}

    def submit(request):
        captured["request"] = request
        return runtime.AgentSubmission(status="accepted",
                                       conversation_id="conv1",
                                       turn_id=request.msg_id)

    def wait(conversation_id, turn_id, timeout=None):
        for event_type, data in live_script:
            captured["request"].live_callback(conversation_id, event_type, data)
        return result

    monkeypatch.setattr(runtime.AgentRuntimeAPI, "submit_message",
                        staticmethod(submit))
    monkeypatch.setattr(runtime.AgentRuntimeAPI, "wait_for_done",
                        staticmethod(wait))
    monkeypatch.setattr(A2AStore, "instance", classmethod(lambda cls: _Store()))
    monkeypatch.setattr(agui, "_ensure_isolated_conversation",
                        lambda publication, context: None)
    return captured


def test_run_stream_happy_path(monkeypatch):
    from core.agent_runtime_api import AgentFinalResult
    result = AgentFinalResult(conversation_id="conv1", turn_id="x",
                              response="Hello!")
    captured = _patch_runtime(monkeypatch, [
        ("token", {"text": "Hello!", "msg_id": "m1"}),
        ("tool_call", {"tool": "read", "tc_id": "tc1", "arguments": {}}),
        ("tool_result", {"tool": "read", "tc_id": "tc1", "result": "ok"}),
    ], result)
    events = _events(list(run_agent_stream(_PUBLICATION, _KEY, _run_input())))
    types = [e["type"] for e in events]
    assert types[0] == "RUN_STARTED"
    assert types[-1] == "RUN_FINISHED"
    assert events[0]["threadId"] == "t1" and events[0]["runId"] == "r1"
    finished = events[-1]
    assert finished["result"] == "Hello!"
    assert finished["outcome"] == {"type": "success"}
    assert "TOOL_CALL_RESULT" in types
    # Every TEXT_MESSAGE_START has its END before the run finishes.
    assert types.count("TEXT_MESSAGE_START") == types.count("TEXT_MESSAGE_END")
    request = captured["request"]
    assert request.channel == "agui"
    assert request.target_agent == "assistant"
    assert request.conversation_id == "conv1"


def test_run_stream_agent_error_becomes_run_error(monkeypatch):
    from core.agent_runtime_api import AgentFinalResult
    result = AgentFinalResult(conversation_id="conv1", turn_id="x",
                              error="boom", event_type="error_event")
    _patch_runtime(monkeypatch, [], result)
    events = _events(list(run_agent_stream(_PUBLICATION, _KEY, _run_input())))
    assert [e["type"] for e in events] == ["RUN_STARTED", "RUN_ERROR"]
    assert events[1]["message"] == "boom"


def test_run_stream_invalid_input_is_a_single_run_error():
    events = _events(list(run_agent_stream(
        _PUBLICATION, _KEY, {"runId": "r", "messages": []})))
    assert [e["type"] for e in events] == ["RUN_ERROR"]
    assert events[0]["code"] == "invalid_input"


def test_run_stream_disabled_publication_refuses():
    publication = dict(_PUBLICATION, enabled=False)
    events = _events(list(run_agent_stream(publication, _KEY, _run_input())))
    assert [e["type"] for e in events] == ["RUN_ERROR"]


def test_run_stream_submission_failure(monkeypatch):
    from core import agent_runtime_api as runtime
    from core.a2a_store import A2AStore

    def submit(request):
        raise RuntimeError("no live AgentLoopTask")

    monkeypatch.setattr(runtime.AgentRuntimeAPI, "submit_message",
                        staticmethod(submit))
    monkeypatch.setattr(A2AStore, "instance", classmethod(lambda cls: _Store()))
    monkeypatch.setattr(agui, "_ensure_isolated_conversation",
                        lambda publication, context: None)
    events = _events(list(run_agent_stream(_PUBLICATION, _KEY, _run_input())))
    assert [e["type"] for e in events] == ["RUN_STARTED", "RUN_ERROR"]
    assert events[1]["code"] == "submission_failed"


# ── Route registration ───────────────────────────────────────────

class _Listener:
    def __init__(self):
        self.routes = []

    def get_routes(self):
        return [{"method": m, "pattern": p} for m, p, *_ in self.routes]

    def register_route(self, method, pattern, owner, callback=None,
                       public=False):
        assert public is True
        self.routes.append((method, pattern, owner, callback))


def test_agui_routes_register_idempotently():
    from services.agui_server_endpoint import register_agui_routes
    listener = _Listener()
    register_agui_routes(listener)
    register_agui_routes(listener)
    patterns = [(m, p) for m, p, *_ in listener.routes]
    assert patterns == [("GET", "/agui/{publication_id}"),
                        ("POST", "/agui/{publication_id}")]


def test_publication_creation_installs_agui_routes():
    import inspect
    from tasks.ai.actions import _agentres_k7
    src = inspect.getsource(_agentres_k7)
    assert "ensure_agui_routes()" in src
    from services.http_listener_service import HTTPListenerService
    src = inspect.getsource(HTTPListenerService.__init__)
    assert "register_agui_routes(self)" in src
