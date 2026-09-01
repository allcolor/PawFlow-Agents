"""AG-UI protocol endpoint: RunAgentInput → PawFlow turn → AG-UI SSE stream."""

import json

import pytest

import core.agui_runtime as agui
from core.agui_runtime import (
    _TurnTranslator, _UNSET, agui_event, parse_run_input, run_agent_stream,
    sse_frame)


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

def test_parse_run_input_requires_thread_and_new_input():
    with pytest.raises(ValueError, match="threadId"):
        parse_run_input({"runId": "r", "messages": [
            {"id": "1", "role": "user", "content": "hi"}]})
    with pytest.raises(ValueError, match="messages"):
        parse_run_input({"threadId": "t", "runId": "r", "messages": []})
    with pytest.raises(ValueError, match="no new user input"):
        parse_run_input({"threadId": "t", "runId": "r", "messages": [
            {"id": "1", "role": "user", "content": "old"},
            {"id": "2", "role": "assistant", "content": "answered"}]})


def test_parse_run_input_takes_the_trailing_segment_only():
    spec = parse_run_input({
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
    assert (spec["thread_id"], spec["run_id"]) == ("t1", "r1")
    assert spec["user_texts"] == ["look at this\n"
                                  "[AG-UI image attachment: https://x/img.png]"]
    assert spec["tools"] == [{"name": "confirm",
                              "description": "ask the user",
                              "parameters": None,
                              "annotations": None,
                              "catalogue_id": "",
                              "catalogue_version": ""}]
    prompt = agui._assemble_prompt(spec, [], frontend_tools_live=True)
    assert "look at this" in prompt
    assert "older question" not in prompt   # server keeps its own history
    assert "page: /checkout" in prompt
    assert "confirm: ask the user" in prompt
    assert "available as real tools" in prompt


def test_parse_run_input_accepts_input_schema_and_annotations():
    # WebMCP registerTool declares `inputSchema` (preferred over
    # `parameters`) and unverified `annotations` hints.
    spec = parse_run_input({
        "threadId": "t1", "runId": "r1",
        "messages": [{"id": "1", "role": "user", "content": "hi"}],
        "tools": [{"name": "confirm", "description": "ask",
                   "inputSchema": {"type": "object",
                                   "properties": {"q": {"type": "string"}}},
                   "parameters": {"type": "object", "properties": {}},
                   "annotations": {"readOnlyHint": True}}],
    })
    tool = spec["tools"][0]
    assert tool["parameters"] == {"type": "object",
                                  "properties": {"q": {"type": "string"}}}
    assert tool["annotations"] == {"readOnlyHint": True}


def test_parse_run_input_collects_frontend_tool_results():
    spec = parse_run_input({
        "threadId": "t1",
        "messages": [
            {"id": "1", "role": "user", "content": "do it"},
            {"id": "2", "role": "assistant", "content": "",
             "toolCalls": [{"id": "tc9", "type": "function",
                            "function": {"name": "confirm",
                                         "arguments": "{}"}}]},
            {"id": "3", "role": "tool", "toolCallId": "tc9",
             "content": "user clicked yes"},
        ],
    })
    assert spec["user_texts"] == []
    assert spec["tool_results"] == [{"tool_call_id": "tc9",
                                     "content": "user clicked yes",
                                     "error": ""}]
    prompt = agui._assemble_prompt(spec, [], frontend_tools_live=True)
    assert "frontend tool result for call tc9" in prompt
    assert "user clicked yes" in prompt


def test_parse_run_input_inline_data_becomes_attachment():
    spec = parse_run_input({
        "threadId": "t1",
        "messages": [{"id": "1", "role": "user", "content": [
            {"type": "text", "text": "see image"},
            {"type": "image", "source": {"type": "data", "value": "aGk=",
                                          "mime_type": "image/png"}},
        ]}],
    })
    assert len(spec["attachments"]) == 1
    attachment = spec["attachments"][0]
    assert attachment["mime_type"] == "image/png"
    assert attachment["data"] == "aGk="
    assert attachment["filename"].endswith(".png")


def test_parse_run_input_resume_and_state():
    spec = parse_run_input({
        "threadId": "t1", "state": {"step": 2},
        "messages": [{"id": "1", "role": "assistant", "content": "waiting"}],
        "resume": [{"interruptId": "int_1", "status": "resolved",
                    "payload": {"approved": True}}],
    })
    assert spec["state"] == {"step": 2}
    assert spec["resume"] == [{"interrupt_id": "int_1", "status": "resolved",
                               "payload": {"approved": True}}]
    spec2 = parse_run_input({"threadId": "t", "messages": [
        {"id": "1", "role": "user", "content": "hi"}]})
    assert spec2["state"] is _UNSET


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


def test_translator_suppresses_frontend_tool_placeholder_results():
    tr = _TurnTranslator(frontend_tool_names={"confirm"})
    calls = tr.translate("tool_call", {"tool": "confirm", "tc_id": "tc1",
                                       "arguments": {"q": "sure?"}})
    assert [e["type"] for e in calls] == [
        "TOOL_CALL_START", "TOOL_CALL_ARGS", "TOOL_CALL_END"]
    assert tr.frontend_calls == 1
    # The server-side placeholder result is NOT streamed to the client...
    assert tr.translate("tool_result", {"tool": "confirm", "tc_id": "tc1",
                                        "result": "forwarded"}) == []
    # ...but a server tool's result still is.
    tr.translate("tool_call", {"tool": "grep", "tc_id": "tc2",
                               "arguments": {}})
    out = tr.translate("tool_result", {"tool": "grep", "tc_id": "tc2",
                                       "result": "found"})
    assert [e["type"] for e in out] == ["TOOL_CALL_RESULT"]


def test_translator_maps_state_events_and_collects_interrupts():
    tr = _TurnTranslator()
    out = tr.translate("agui_state_snapshot", {"state": {"a": 1}})
    assert [e["type"] for e in out] == ["STATE_SNAPSHOT"]
    assert out[0]["snapshot"] == {"a": 1}
    out = tr.translate("agui_state_delta",
                       {"delta": [{"op": "replace", "path": "/a",
                                   "value": 2}]})
    assert [e["type"] for e in out] == ["STATE_DELTA"]
    assert out[0]["delta"][0]["op"] == "replace"
    assert tr.translate("agui_interrupt",
                        {"interrupt": {"id": "int_1",
                                       "reason": "approval"}}) == []
    assert tr.interrupts == [{"id": "int_1", "reason": "approval"}]


# ── Full run stream ──────────────────────────────────────────────

_PUBLICATION = {
    "publication_id": "a2ap_test", "owner_user_id": "uid",
    "conversation_id": "conv0", "agent_name": "assistant",
    "label": "Test", "context_policy": "isolated", "enabled": True,
}
_KEY = {"key_id": "k1"}


class _Store:
    def ensure_named_context(self, publication, key_id, name):
        assert name == "agui_t1"
        return {"context_id": "ctx1", "internal_conversation_id": "conv1"}


def _run_input():
    return {"threadId": "t1", "runId": "r1", "state": None,
            "messages": [{"id": "1", "role": "user", "content": "hi"}],
            "tools": [], "context": [], "forwardedProps": None}


def _patch_runtime(monkeypatch, live_script, result, doc_state=None):
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
    monkeypatch.setattr(agui, "_prepare_agui_doc",
                        lambda conversation_id, spec: (doc_state, []))
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


def test_run_stream_opens_with_state_snapshot_when_state_exists(monkeypatch):
    from core.agent_runtime_api import AgentFinalResult
    result = AgentFinalResult(conversation_id="conv1", turn_id="x",
                              response="ok")
    _patch_runtime(monkeypatch, [], result, doc_state={"step": 1})
    events = _events(list(run_agent_stream(_PUBLICATION, _KEY, _run_input())))
    types = [e["type"] for e in events]
    assert types[:2] == ["RUN_STARTED", "STATE_SNAPSHOT"]
    assert events[1]["snapshot"] == {"step": 1}


def test_run_stream_interrupt_outcome(monkeypatch):
    from core.agent_runtime_api import AgentFinalResult
    result = AgentFinalResult(conversation_id="conv1", turn_id="x",
                              response="waiting for approval")
    interrupt = {"id": "int_1", "reason": "approval_required",
                 "message": "Deploy to prod?"}
    _patch_runtime(monkeypatch, [
        ("agui_interrupt", {"interrupt": interrupt}),
    ], result)
    events = _events(list(run_agent_stream(_PUBLICATION, _KEY, _run_input())))
    finished = events[-1]
    assert finished["type"] == "RUN_FINISHED"
    assert finished["outcome"] == {"type": "interrupt",
                                   "interrupts": [interrupt]}


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
    monkeypatch.setattr(agui, "_prepare_agui_doc",
                        lambda conversation_id, spec: (None, []))
    events = _events(list(run_agent_stream(_PUBLICATION, _KEY, _run_input())))
    assert [e["type"] for e in events] == ["RUN_STARTED", "RUN_ERROR"]
    assert events[1]["code"] == "submission_failed"


# ── Named contexts (AG-UI threadId → A2A context) ────────────────

def test_ensure_named_context_creates_then_reuses(tmp_path):
    from core.a2a_store import A2AStore
    store = A2AStore(database_path=tmp_path / "a2a.db")
    publication = store.configure_publication("uid", "conv0", "assistant")
    key1_id = store.create_key(publication["publication_id"], "k1")[1]["key_id"]
    key2_id = store.create_key(publication["publication_id"], "k2")[1]["key_id"]
    first = store.ensure_named_context(publication, key1_id, "agui_t1")
    again = store.ensure_named_context(publication, key1_id, "agui_t1")
    assert first["context_id"] == again["context_id"]
    assert first["internal_conversation_id"] == again["internal_conversation_id"]
    assert "::a2a::" in first["internal_conversation_id"]
    # Same thread name under a different key is a different context.
    other_key = store.ensure_named_context(publication, key2_id, "agui_t1")
    assert other_key["context_id"] != first["context_id"]
    # Shared policy keeps the publication conversation itself.
    shared_pub = store.configure_publication("uid", "conv9", "helper",
                                             context_policy="shared")
    shared_key = store.create_key(shared_pub["publication_id"], "k")[1]["key_id"]
    shared = store.ensure_named_context(shared_pub, shared_key, "agui_t1")
    assert shared["internal_conversation_id"] == "conv9"


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
                        ("POST", "/agui/{publication_id}"),
                        ("DELETE", "/agui/{publication_id}")]


def test_publication_configure_wires_managed_mode_and_ttl(tmp_path):
    from unittest.mock import MagicMock, patch
    from core import FlowFile
    from core.a2a_store import A2AStore
    from tasks.ai.actions._agentres_k7 import _handle_agentres_k7

    conv_store = MagicMock()
    conv_store.resolve_owner.return_value = "user"
    a2a = A2AStore(database_path=tmp_path / "a2a.db")
    patches = lambda: (
        patch("core.a2a_store.A2AStore.instance", return_value=a2a),
        patch("core.conv_agent_config.get_all_agent_configs",
              return_value={"helper": {"runtime_kind": "llm"}}),
        patch("core.conv_agent_config.get_agent_config",
              return_value={"runtime_kind": "llm"}),
        patch("services.a2a_server_endpoint.ensure_a2a_routes"),
        patch("services.agui_server_endpoint.ensure_agui_routes"),
    )

    flowfile = FlowFile()
    p1, p2, p3, p4, p5 = patches()
    with p1, p2, p3, p4, p5:
        _handle_agentres_k7(None, "a2a_publication_configure", {
            "conversation_id": "conv", "agent_name": "helper",
            "managed_mode": True, "thread_ttl_seconds": 3600,
        }, conv_store, "user", flowfile)
    publication = json.loads(flowfile.content)["publication"]
    assert publication["managed_mode"] is True
    assert publication["thread_ttl_seconds"] == 3600

    # Omitting both fields preserves the stored values (None = keep).
    flowfile = FlowFile()
    p1, p2, p3, p4, p5 = patches()
    with p1, p2, p3, p4, p5:
        _handle_agentres_k7(None, "a2a_publication_configure", {
            "conversation_id": "conv", "agent_name": "helper",
        }, conv_store, "user", flowfile)
    publication = json.loads(flowfile.content)["publication"]
    assert publication["managed_mode"] is True
    assert publication["thread_ttl_seconds"] == 3600

    # Managed mode is meaningless on a shared context: fail closed.
    flowfile = FlowFile()
    p1, p2, p3, p4, p5 = patches()
    with p1, p2, p3, p4, p5:
        _handle_agentres_k7(None, "a2a_publication_configure", {
            "conversation_id": "conv", "agent_name": "helper",
            "context_policy": "shared", "managed_mode": True,
        }, conv_store, "user", flowfile)
    assert flowfile.get_attribute("http.response.status") == "400"
    assert "isolated" in json.loads(flowfile.content)["error"]

    # JSON strings must not be coerced with bool("false") and silently
    # enable the publication-level execution mode.
    flowfile = FlowFile()
    p1, p2, p3, p4, p5 = patches()
    with p1, p2, p3, p4, p5:
        _handle_agentres_k7(None, "a2a_publication_configure", {
            "conversation_id": "conv", "agent_name": "helper",
            "managed_mode": "false",
        }, conv_store, "user", flowfile)
    assert flowfile.get_attribute("http.response.status") == "400"
    assert json.loads(flowfile.content) == {
        "error": "managed_mode must be a boolean"}


def test_publication_creation_installs_agui_routes():
    import inspect
    from tasks.ai.actions import _agentres_k7
    src = inspect.getsource(_agentres_k7)
    assert "ensure_agui_routes()" in src
    from services.http_listener_service import HTTPListenerService
    src = inspect.getsource(HTTPListenerService.__init__)
    assert "register_agui_routes(self)" in src
