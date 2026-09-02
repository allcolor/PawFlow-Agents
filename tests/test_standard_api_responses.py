"""OpenAI Responses contracts for published PawFlow agents."""

from __future__ import annotations

import json
import threading
from collections import deque
from types import SimpleNamespace

import pytest

from core.a2a_store import A2AStore
from core.agent_runtime_api import AgentFinalResult, AgentSubmission
from core.standard_api_responses import (
    OpenAIResponsesError,
    iter_openai_response_sse,
    parse_openai_response_request,
    prepare_openai_response_run,
    wait_openai_response_payload,
)


_CONFIG = {
    "standard_api_enabled": True,
    "api_model_id": "pawflow-agent",
    "api_permission_mode": "read_only",
    "api_session_ttl_seconds": 3600,
    "api_max_sessions_per_key": 20,
    "api_max_concurrent_runs_per_key": 4,
    "strict_fields": False,
    "api_request_overrides_json": {},
    "api_input_modalities_json": ["text"],
    "api_chat_completions_enabled": False,
    "api_responses_enabled": True,
    "api_anthropic_messages_enabled": False,
    "api_disconnect_policy": "cancel",
}

_TOOL = {
    "type": "function",
    "name": "lookup",
    "description": "Look up a value",
    "parameters": {
        "type": "object",
        "properties": {"q": {"type": "string"}},
        "required": ["q"],
    },
    "strict": False,
}


class _ConversationStore:
    def __init__(self):
        self.saved = {}
        self.extras = {}
        self.deleted = []

    def save(self, cid, messages, ttl=0, user_id="", status=""):
        self.saved[cid] = {
            "messages": list(messages),
            "ttl": ttl,
            "user_id": user_id,
            "status": status,
        }

    def set_extra(self, cid, key, value):
        self.extras[(cid, key)] = value

    def delete(self, cid, user_id=""):
        self.deleted.append((cid, user_id))
        self.saved.pop(cid, None)
        return True


class _Runtime:
    def __init__(self, results, live_text=""):
        self.results = deque(results)
        self.pending = {}
        self.submissions = []
        self.live_text = live_text

    def submit_structured(self, request):
        self.submissions.append(request)
        self.pending[request.msg_id] = self.results.popleft()
        if self.live_text and request.live_callback:
            request.live_callback(request.conversation_id, "token", {
                "text": self.live_text,
                "msg_id": "msg-live",
                "turn_id": request.msg_id,
            })
        return AgentSubmission(
            status="accepted",
            conversation_id=request.conversation_id,
            turn_id=request.msg_id,
        )

    def wait_for_done(self, conversation_id, turn_id, timeout=None):
        return self.pending.pop(turn_id)


class _BlockingRuntime:
    def __init__(self, result):
        self.result = result
        self.release = threading.Event()
        self.submissions = []

    def submit_structured(self, request):
        self.submissions.append(request)
        return AgentSubmission(
            status="accepted",
            conversation_id=request.conversation_id,
            turn_id=request.msg_id,
        )

    def wait_for_done(self, conversation_id, turn_id, timeout=None):
        if not self.release.wait(timeout):
            return None
        return self.result


@pytest.fixture
def configured(tmp_path, monkeypatch):
    from core import standard_api_config

    monkeypatch.setitem(
        standard_api_config.DIALECT_AVAILABILITY, "responses", True)
    store = A2AStore(tmp_path / "a2a.sqlite3")
    publication = store.configure_publication(
        "alice", "conv-1", "Agent", standard_api_config=_CONFIG)
    _raw, key = store.create_key(publication["publication_id"], "client")
    return {
        "store": store,
        "publication": publication,
        "key": key,
        "conversations": _ConversationStore(),
    }


def _parse(publication=None, key=None, **changes):
    publication = publication or {
        "publication_id": "a2ap_test",
        "api_generation": 1,
        "api_model_id": "pawflow-agent",
        "strict_fields": False,
    }
    key = key or {"key_id": "key_test"}
    body = {
        "model": "pawflow-agent",
        "input": "hello",
    }
    body.update(changes)
    return parse_openai_response_request(
        publication,
        key,
        body,
        request_id="req_test",
        hash_secret=b"test-secret",
    )


def _events(frames):
    return [
        json.loads(frame.split(b"data: ", 1)[1])
        for frame in frames
        if b"data: " in frame
    ]


def test_responses_parser_normalizes_string_instructions_and_native_tools():
    parsed = _parse(
        input="hello",
        instructions="Be concise",
        tools=[_TOOL],
        tool_choice="auto",
        store=False,
        stream=True,
        metadata={"trace": "test"},
    )

    assert parsed.turn.namespace.dialect == "responses"
    assert [item.kind for item in parsed.turn.visible_items] == ["user_message"]
    assert parsed.turn.visible_items[0].data["content"] == "hello"
    assert parsed.instructions == "Be concise"
    assert parsed.store is False
    assert parsed.metadata == {"trace": "test"}
    assert parsed.turn.stream is True
    assert parsed.turn.client_tools[0]["name"] == "lookup"
    assert parsed.public_tools == (_TOOL,)


def test_responses_parser_normalizes_message_function_call_and_output_items():
    parsed = _parse(input=[
        {
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [{
                "type": "output_text",
                "text": "I will look.",
                "annotations": [],
            }],
        },
        {
            "type": "function_call",
            "id": "fc_history",
            "call_id": "call_history",
            "name": "lookup",
            "arguments": '{"q":"history"}',
            "status": "completed",
        },
        {
            "type": "function_call_output",
            "call_id": "call_history",
            "output": "found",
        },
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "Continue"}],
        },
    ])

    assert [item.kind for item in parsed.turn.visible_items] == [
        "response_output",
        "client_tool_result_batch",
        "user_message",
    ]
    assert parsed.turn.visible_items[0].data["output"][1] == {
        "type": "function_call",
        "id": "fc_history",
        "call_id": "call_history",
        "name": "lookup",
        "arguments": '{"q":"history"}',
        "status": "completed",
    }
    assert parsed.turn.visible_items[1].data["results"][0] == {
        "id": "call_history",
        "content": "found",
        "error": False,
    }
    assert parsed.turn.actionable_suffix_start == 2


@pytest.mark.parametrize(("changes", "param"), [
    ({"model": "other"}, "model"),
    ({"input": []}, "input"),
    ({"parallel_tool_calls": False}, "parallel_tool_calls"),
    ({"tool_choice": "required"}, "tool_choice"),
    ({"tools": [{"type": "web_search_preview"}]}, "tools.0"),
    ({
        "previous_response_id": "resp_parent",
        "input": [{"type": "message", "role": "assistant", "content": "no"}],
    }, "input.0.role"),
])
def test_responses_parser_rejects_unsupported_or_contradictory_semantics(
        changes, param):
    with pytest.raises(OpenAIResponsesError) as caught:
        _parse(**changes)
    assert caught.value.param == param


def test_response_run_stores_native_payload_without_inheriting_instructions(
        configured):
    runtime = _Runtime([AgentFinalResult(
        conversation_id="ignored",
        turn_id="ignored",
        response="hello",
        finish_reason="stop",
        tokens_in=4,
        tokens_out=2,
    )])
    admission = prepare_openai_response_run(
        configured["store"],
        configured["publication"],
        configured["key"],
        {
            "model": "pawflow-agent",
            "input": "Say hello",
            "instructions": "Be concise",
            "metadata": {"trace": "one"},
        },
        request_id="req_response_text",
        conversation_store=configured["conversations"],
        runtime_api=runtime,
        hash_secret=b"test-secret",
    )

    payload = wait_openai_response_payload(admission.run)
    stored = configured["store"].get_api_response(
        admission.run.turn.namespace, payload["id"])

    assert payload["object"] == "response"
    assert payload["status"] == "completed"
    assert payload["model"] == "pawflow-agent"
    assert payload["previous_response_id"] is None
    assert payload["instructions"] == "Be concise"
    assert payload["metadata"] == {"trace": "one"}
    assert payload["output"][0]["type"] == "message"
    assert payload["output"][0]["content"][0]["text"] == "hello"
    assert payload["usage"]["total_tokens"] == 6
    assert stored["envelope"] == payload
    assert [item.kind for item in stored["visible_items"]] == [
        "user_message", "response_output"]
    assert all(
        item.kind != "client_instruction"
        for item in stored["visible_items"]
    )
    submission = runtime.submissions[0]
    assert submission.ingress_messages[0].source["request_scoped"] is True
    assert "Be concise" in submission.ingress_messages[0].content


def test_previous_response_continues_exact_parent_and_can_fork(configured):
    first_runtime = _Runtime([AgentFinalResult(
        conversation_id="ignored",
        turn_id="ignored",
        response="first",
        finish_reason="stop",
    )])
    first = prepare_openai_response_run(
        configured["store"],
        configured["publication"],
        configured["key"],
        {"model": "pawflow-agent", "input": "Start"},
        request_id="req_parent",
        conversation_store=configured["conversations"],
        runtime_api=first_runtime,
        hash_secret=b"test-secret",
    )
    parent = wait_openai_response_payload(first.run)

    child_runtime = _Runtime([AgentFinalResult(
        conversation_id="ignored",
        turn_id="ignored",
        response="child",
        finish_reason="stop",
    )])
    child = prepare_openai_response_run(
        configured["store"],
        configured["publication"],
        configured["key"],
        {
            "model": "pawflow-agent",
            "previous_response_id": parent["id"],
            "input": "Continue",
        },
        request_id="req_child",
        conversation_store=configured["conversations"],
        runtime_api=child_runtime,
        hash_secret=b"test-secret",
    )
    child_payload = wait_openai_response_payload(child.run)

    assert child_payload["previous_response_id"] == parent["id"]
    stored = configured["store"].get_api_response(
        child.run.turn.namespace, child_payload["id"])
    assert [item.kind for item in stored["visible_items"]] == [
        "user_message", "response_output", "user_message", "response_output"]
    assert (
        first_runtime.submissions[0].conversation_id
        == child_runtime.submissions[0].conversation_id
    )

    fork_runtime = _Runtime([AgentFinalResult(
        conversation_id="ignored",
        turn_id="ignored",
        response="fork",
        finish_reason="stop",
    )])
    fork = prepare_openai_response_run(
        configured["store"],
        configured["publication"],
        configured["key"],
        {
            "model": "pawflow-agent",
            "previous_response_id": parent["id"],
            "input": "Fork instead",
        },
        request_id="req_fork",
        conversation_store=configured["conversations"],
        runtime_api=fork_runtime,
        hash_secret=b"test-secret",
    )
    fork_payload = wait_openai_response_payload(fork.run)
    assert fork_payload["previous_response_id"] == parent["id"]
    assert (
        fork_runtime.submissions[0].conversation_id
        != child_runtime.submissions[0].conversation_id
    )
    fork_messages = configured["conversations"].saved[
        fork_runtime.submissions[0].conversation_id]["messages"]
    assert any(
        message["role"] == "assistant" and message["content"] == "first"
        for message in fork_messages
    )


def test_response_output_replay_without_parent_id_reuses_text_head(configured):
    first_runtime = _Runtime([AgentFinalResult(
        conversation_id="ignored",
        turn_id="ignored",
        response="first",
        finish_reason="stop",
    )])
    first = prepare_openai_response_run(
        configured["store"],
        configured["publication"],
        configured["key"],
        {"model": "pawflow-agent", "input": "Start"},
        request_id="req_replay_parent",
        conversation_store=configured["conversations"],
        runtime_api=first_runtime,
        hash_secret=b"test-secret",
    )
    parent = wait_openai_response_payload(first.run)

    second_runtime = _Runtime([AgentFinalResult(
        conversation_id="ignored",
        turn_id="ignored",
        response="continued",
        finish_reason="stop",
    )])
    second = prepare_openai_response_run(
        configured["store"],
        configured["publication"],
        configured["key"],
        {
            "model": "pawflow-agent",
            "input": [{
                "type": "message",
                "role": "user",
                "content": "Start",
            }] + parent["output"] + [{
                "type": "message",
                "role": "user",
                "content": "Continue",
            }],
        },
        request_id="req_replay_child",
        conversation_store=configured["conversations"],
        runtime_api=second_runtime,
        hash_secret=b"test-secret",
    )
    wait_openai_response_payload(second.run)

    assert (
        first_runtime.submissions[0].conversation_id
        == second_runtime.submissions[0].conversation_id
    )


def test_response_tool_round_trip_settles_parent_batch(configured):
    first_runtime = _Runtime([AgentFinalResult(
        conversation_id="ignored",
        turn_id="ignored",
        outcome="client_tool_pending",
        finish_reason="client_tool_pending",
        client_tool_calls=[{
            "id": "call_weather",
            "name": "lookup",
            "arguments": {"q": "weather"},
        }],
    )])
    first = prepare_openai_response_run(
        configured["store"],
        configured["publication"],
        configured["key"],
        {
            "model": "pawflow-agent",
            "input": "Weather?",
            "tools": [_TOOL],
        },
        request_id="req_response_tool",
        conversation_store=configured["conversations"],
        runtime_api=first_runtime,
        hash_secret=b"test-secret",
    )
    first_payload = wait_openai_response_payload(first.run)
    call = first_payload["output"][0]

    assert call["type"] == "function_call"
    assert call["call_id"] == "call_weather"
    assert json.loads(call["arguments"]) == {"q": "weather"}

    second_runtime = _Runtime([AgentFinalResult(
        conversation_id="ignored",
        turn_id="ignored",
        response="Sunny.",
        finish_reason="stop",
    )])
    second = prepare_openai_response_run(
        configured["store"],
        configured["publication"],
        configured["key"],
        {
            "model": "pawflow-agent",
            "previous_response_id": first_payload["id"],
            "input": [{
                "type": "function_call_output",
                "call_id": call["call_id"],
                "output": "sunny",
            }],
            "tools": [_TOOL],
        },
        request_id="req_response_tool_result",
        conversation_store=configured["conversations"],
        runtime_api=second_runtime,
        hash_secret=b"test-secret",
    )
    payload = wait_openai_response_payload(second.run)

    assert payload["output"][0]["content"][0]["text"] == "Sunny."
    pending = configured["store"].get_api_tool_batch_for_run(
        "req_response_tool")
    assert pending["state"] == "settled"
    assert pending["settled_by_run_id"] == "req_response_tool_result"
    assert second_runtime.submissions[0].ingress_messages[0].role == "tool"


def test_response_tool_output_replay_without_parent_id_settles_batch(configured):
    first_runtime = _Runtime([AgentFinalResult(
        conversation_id="ignored",
        turn_id="ignored",
        outcome="client_tool_pending",
        finish_reason="client_tool_pending",
        client_tool_calls=[{
            "id": "call_replay",
            "name": "lookup",
            "arguments": {"q": "replay"},
        }],
    )])
    first = prepare_openai_response_run(
        configured["store"],
        configured["publication"],
        configured["key"],
        {
            "model": "pawflow-agent",
            "input": "Look up",
            "tools": [_TOOL],
        },
        request_id="req_tool_replay_parent",
        conversation_store=configured["conversations"],
        runtime_api=first_runtime,
        hash_secret=b"test-secret",
    )
    parent = wait_openai_response_payload(first.run)

    second_runtime = _Runtime([AgentFinalResult(
        conversation_id="ignored",
        turn_id="ignored",
        response="done",
        finish_reason="stop",
    )])
    second = prepare_openai_response_run(
        configured["store"],
        configured["publication"],
        configured["key"],
        {
            "model": "pawflow-agent",
            "input": [{
                "type": "message",
                "role": "user",
                "content": "Look up",
            }] + parent["output"] + [{
                "type": "function_call_output",
                "call_id": "call_replay",
                "output": "found",
            }],
            "tools": [_TOOL],
        },
        request_id="req_tool_replay_child",
        conversation_store=configured["conversations"],
        runtime_api=second_runtime,
        hash_secret=b"test-secret",
    )
    wait_openai_response_payload(second.run)

    batch = configured["store"].get_api_tool_batch_for_run(
        "req_tool_replay_parent")
    assert batch["state"] == "settled"
    assert (
        first_runtime.submissions[0].conversation_id
        == second_runtime.submissions[0].conversation_id
    )


def test_store_false_returns_payload_but_cannot_be_retrieved(configured):
    runtime = _Runtime([AgentFinalResult(
        conversation_id="ignored",
        turn_id="ignored",
        response="ephemeral",
        finish_reason="stop",
    )])
    admission = prepare_openai_response_run(
        configured["store"],
        configured["publication"],
        configured["key"],
        {
            "model": "pawflow-agent",
            "input": "Do not store",
            "store": False,
        },
        request_id="req_ephemeral_response",
        conversation_store=configured["conversations"],
        runtime_api=runtime,
        hash_secret=b"test-secret",
    )
    payload = wait_openai_response_payload(admission.run)

    assert payload["store"] is False
    assert configured["store"].get_api_response(
        admission.run.turn.namespace, payload["id"]) is None


def test_responses_stream_emits_semantic_lifecycle_without_done_sentinel(
        configured):
    runtime = _Runtime([AgentFinalResult(
        conversation_id="ignored",
        turn_id="ignored",
        response="hello",
        finish_reason="stop",
        tokens_in=2,
        tokens_out=1,
    )], live_text="hel")
    admission = prepare_openai_response_run(
        configured["store"],
        configured["publication"],
        configured["key"],
        {
            "model": "pawflow-agent",
            "input": "Hello",
            "stream": True,
        },
        request_id="req_response_stream",
        conversation_store=configured["conversations"],
        runtime_api=runtime,
        hash_secret=b"test-secret",
    )

    frames = list(iter_openai_response_sse(admission))
    events = _events(frames)
    event_types = [event["type"] for event in events]

    assert event_types[:2] == ["response.created", "response.in_progress"]
    assert "response.output_item.added" in event_types
    assert "response.content_part.added" in event_types
    assert "response.output_text.delta" in event_types
    assert event_types[-1] == "response.completed"
    assert [event["sequence_number"] for event in events] == list(
        range(len(events)))
    assert b"".join(frames).find(b"[DONE]") == -1
    assert "".join(
        event["delta"]
        for event in events
        if event["type"] == "response.output_text.delta"
    ) == "hello"
    assert events[-1]["response"]["status"] == "completed"


def test_responses_tool_stream_uses_function_argument_events(configured):
    runtime = _Runtime([AgentFinalResult(
        conversation_id="ignored",
        turn_id="ignored",
        outcome="client_tool_pending",
        finish_reason="client_tool_pending",
        client_tool_calls=[{
            "id": "call_stream",
            "name": "lookup",
            "arguments": {"q": "stream"},
        }],
    )])
    admission = prepare_openai_response_run(
        configured["store"],
        configured["publication"],
        configured["key"],
        {
            "model": "pawflow-agent",
            "input": "Look up",
            "tools": [_TOOL],
            "stream": True,
        },
        request_id="req_response_tool_stream",
        conversation_store=configured["conversations"],
        runtime_api=runtime,
        hash_secret=b"test-secret",
    )

    events = _events(list(iter_openai_response_sse(admission)))
    types = [event["type"] for event in events]

    assert "response.function_call_arguments.delta" in types
    assert "response.function_call_arguments.done" in types
    assert events[-1]["type"] == "response.completed"
    assert events[-1]["response"]["output"][0]["call_id"] == "call_stream"


def test_responses_stream_serializes_post_header_failure(configured):
    runtime = _Runtime([AgentFinalResult(
        conversation_id="ignored",
        turn_id="ignored",
        error="failed",
    )])
    admission = prepare_openai_response_run(
        configured["store"],
        configured["publication"],
        configured["key"],
        {
            "model": "pawflow-agent",
            "input": "Fail",
            "stream": True,
        },
        request_id="req_response_failure",
        conversation_store=configured["conversations"],
        runtime_api=runtime,
        hash_secret=b"test-secret",
    )

    events = _events(list(iter_openai_response_sse(admission)))

    assert [event["type"] for event in events] == [
        "response.created", "response.in_progress", "response.failed"]
    assert events[-1]["response"]["status"] == "failed"
    assert events[-1]["response"]["error"]["code"] == "agent_error"


def test_missing_previous_response_is_constant_not_found(configured):
    with pytest.raises(OpenAIResponsesError) as caught:
        prepare_openai_response_run(
            configured["store"],
            configured["publication"],
            configured["key"],
            {
                "model": "pawflow-agent",
                "previous_response_id": "resp_missing",
                "input": "Continue",
            },
            request_id="req_missing_parent",
            conversation_store=configured["conversations"],
            runtime_api=_Runtime([]),
            hash_secret=b"test-secret",
        )
    assert caught.value.status == 404
    assert caught.value.code == "response_not_found"
    assert caught.value.param == "previous_response_id"


def test_official_openai_sdk_parses_response_and_stream_contract(configured):
    openai = pytest.importorskip("openai")
    import httpx

    payload_runtime = _Runtime([AgentFinalResult(
        conversation_id="ignored",
        turn_id="ignored",
        response="hello",
        finish_reason="stop",
        tokens_in=2,
        tokens_out=1,
    )])
    payload_run = prepare_openai_response_run(
        configured["store"],
        configured["publication"],
        configured["key"],
        {"model": "pawflow-agent", "input": "hello"},
        request_id="req_response_sdk_payload",
        conversation_store=configured["conversations"],
        runtime_api=payload_runtime,
        hash_secret=b"test-secret",
    )
    payload = wait_openai_response_payload(payload_run.run)

    stream_runtime = _Runtime([AgentFinalResult(
        conversation_id="ignored",
        turn_id="ignored",
        response="hello",
        finish_reason="stop",
        tokens_in=2,
        tokens_out=1,
    )], live_text="hello")
    stream_run = prepare_openai_response_run(
        configured["store"],
        configured["publication"],
        configured["key"],
        {"model": "pawflow-agent", "input": "hello", "stream": True},
        request_id="req_response_sdk_stream",
        conversation_store=configured["conversations"],
        runtime_api=stream_runtime,
        hash_secret=b"test-secret",
    )
    stream_body = b"".join(iter_openai_response_sse(stream_run))

    def _handler(request):
        body = json.loads(request.content)
        if body.get("stream"):
            return httpx.Response(
                200,
                content=stream_body,
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(
            200,
            json=payload,
            headers={"x-request-id": "req-sdk"},
        )

    with httpx.Client(transport=httpx.MockTransport(_handler)) as http_client:
        client = openai.OpenAI(
            api_key="sk-test",
            base_url="https://pawflow.test/openai/publication/v1",
            http_client=http_client,
            max_retries=0,
        )
        response = client.responses.create(
            model="pawflow-agent", input="hello")
        events = list(client.responses.create(
            model="pawflow-agent", input="hello", stream=True))

    assert response.id == payload["id"]
    assert response.output_text == "hello"
    assert events[0].type == "response.created"
    assert events[-1].type == "response.completed"
    assert events[-1].response.output_text == "hello"


class _EndpointRequest:
    def __init__(self, *, body=None, response_id=""):
        self.body = json.dumps(body or {}).encode("utf-8")
        self.path_params = {
            "publication_id": "a2ap_test",
            "response_id": response_id,
        }
        self.headers = {"Host": "pawflow.example"}
        self.completed = None
        self.streamed = None
        self.done = threading.Event()

    def complete(self, status, headers, body):
        self.completed = (status, headers, body)
        self.done.set()

    def complete_stream(self, status, headers, stream):
        self.streamed = (status, headers, stream)
        self.done.set()


def _decoded(request):
    status, headers, body = request.completed
    return status, headers, json.loads(body.decode("utf-8"))


def _endpoint_access(configured):
    return SimpleNamespace(
        error="",
        publication=configured["publication"],
        key=configured["key"],
    )


def test_response_get_and_delete_handlers_use_native_shapes(
        configured, monkeypatch):
    from services import published_agent_auth
    from services.standard_api_endpoint import (
        handle_openai_response_delete,
        handle_openai_response_get,
    )

    runtime = _Runtime([AgentFinalResult(
        conversation_id="ignored",
        turn_id="ignored",
        response="stored",
        finish_reason="stop",
    )])
    admission = prepare_openai_response_run(
        configured["store"],
        configured["publication"],
        configured["key"],
        {"model": "pawflow-agent", "input": "Store"},
        request_id="req_endpoint_stored",
        conversation_store=configured["conversations"],
        runtime_api=runtime,
        hash_secret=b"test-secret",
    )
    payload = wait_openai_response_payload(admission.run)
    monkeypatch.setattr(
        published_agent_auth,
        "resolve_published_agent",
        lambda _req: _endpoint_access(configured),
    )
    monkeypatch.setattr(
        A2AStore, "instance",
        classmethod(lambda cls: configured["store"]),
    )

    retrieve = _EndpointRequest(response_id=payload["id"])
    handle_openai_response_get(retrieve)
    assert _decoded(retrieve)[0] == 200
    assert _decoded(retrieve)[2] == payload

    delete = _EndpointRequest(response_id=payload["id"])
    handle_openai_response_delete(delete)
    assert _decoded(delete)[2] == {
        "id": payload["id"],
        "object": "response.deleted",
        "deleted": True,
    }

    missing = _EndpointRequest(response_id=payload["id"])
    handle_openai_response_get(missing)
    status, _headers, error = _decoded(missing)
    assert status == 404
    assert error["error"]["code"] == "not_found"


def test_response_create_handler_admits_before_async_or_stream_completion(
        configured, monkeypatch):
    from services import published_agent_auth
    from services.standard_api_endpoint import handle_openai_responses
    from core import standard_api_responses

    monkeypatch.setattr(
        published_agent_auth,
        "resolve_published_agent",
        lambda _req: _endpoint_access(configured),
    )
    payload = {"id": "resp_handler", "object": "response"}
    non_stream_admission = SimpleNamespace(
        run=SimpleNamespace(turn=SimpleNamespace(stream=False)))
    monkeypatch.setattr(
        standard_api_responses,
        "prepare_openai_response_run",
        lambda *_args, **_kwargs: non_stream_admission,
    )
    monkeypatch.setattr(
        standard_api_responses,
        "wait_openai_response_payload",
        lambda _run: payload,
    )

    request = _EndpointRequest(body={
        "model": "pawflow-agent",
        "input": "hello",
    })
    handle_openai_responses(request)
    assert request.done.wait(2)
    assert _decoded(request)[0] == 200
    assert _decoded(request)[2] == payload

    stream_admission = SimpleNamespace(
        run=SimpleNamespace(turn=SimpleNamespace(stream=True)))
    monkeypatch.setattr(
        standard_api_responses,
        "prepare_openai_response_run",
        lambda *_args, **_kwargs: stream_admission,
    )
    monkeypatch.setattr(
        standard_api_responses,
        "iter_openai_response_sse",
        lambda _admission: iter((b"event: response.completed\n\n",)),
    )
    streaming = _EndpointRequest(body={
        "model": "pawflow-agent",
        "input": "hello",
        "stream": True,
    })
    handle_openai_responses(streaming)
    assert streaming.streamed[0] == 200
    assert streaming.streamed[1]["Content-Type"] == "text/event-stream"
