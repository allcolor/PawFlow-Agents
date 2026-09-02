"""Anthropic Messages contracts for published PawFlow agents."""

from __future__ import annotations

import json
import threading
from collections import deque
from types import SimpleNamespace

import pytest

from core.a2a_store import A2AStore
from core.agent_runtime_api import AgentFinalResult, AgentSubmission
from core.standard_api_anthropic import (
    AnthropicMessagesError,
    anthropic_message_payload,
    iter_anthropic_message_sse,
    parse_anthropic_message_request,
    prepare_anthropic_message_run,
    wait_anthropic_message_payload,
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
    "api_responses_enabled": False,
    "api_anthropic_messages_enabled": True,
    "api_disconnect_policy": "cancel",
}

_TOOL = {
    "name": "lookup",
    "description": "Look up a value",
    "input_schema": {
        "type": "object",
        "properties": {"q": {"type": "string"}},
        "required": ["q"],
    },
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


@pytest.fixture
def configured(tmp_path, monkeypatch):
    from core import standard_api_config

    monkeypatch.setitem(
        standard_api_config.DIALECT_AVAILABILITY,
        "anthropic_messages",
        True,
    )
    store = A2AStore(tmp_path / "a2a.sqlite3")
    publication = store.configure_publication(
        "alice",
        "conv-1",
        "Agent",
        standard_api_config=_CONFIG,
    )
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
        "max_tokens": 128,
        "messages": [{"role": "user", "content": "hello"}],
    }
    body.update(changes)
    return parse_anthropic_message_request(
        publication,
        key,
        body,
        request_id="req_test",
        hash_secret=b"test-secret",
    )


def _events(frames):
    values = []
    for frame in frames:
        for line in frame.splitlines():
            if line.startswith(b"data: "):
                values.append(json.loads(line[6:]))
    return values


def test_anthropic_parser_normalizes_system_history_and_tools():
    parsed = _parse(
        system=[{"type": "text", "text": "Be concise"}],
        messages=[
            {"role": "user", "content": "First"},
            {"role": "assistant", "content": "Done"},
            {"role": "user", "content": [
                {"type": "text", "text": "Continue"},
            ]},
        ],
        tools=[_TOOL],
        tool_choice={"type": "auto"},
        stream=True,
        temperature=0.2,
        metadata={"user_id": "consumer-1"},
    )

    assert parsed.turn.namespace.dialect == "anthropic_messages"
    assert [item.kind for item in parsed.turn.visible_items] == [
        "client_instruction",
        "user_message",
        "assistant_message",
        "user_message",
    ]
    assert parsed.turn.actionable_suffix_start == 3
    assert parsed.turn.client_tools[0]["name"] == "lookup"
    assert parsed.public_tools == (_TOOL,)
    assert parsed.max_tokens == 128
    assert parsed.turn.provider_overrides == {}
    assert parsed.turn.stream is True


def test_anthropic_parser_merges_consecutive_compatible_roles():
    parsed = _parse(messages=[
        {"role": "user", "content": "one"},
        {"role": "user", "content": "two"},
        {"role": "assistant", "content": "old"},
        {"role": "user", "content": "three"},
    ])

    assert [item.kind for item in parsed.turn.visible_items] == [
        "user_message",
        "assistant_message",
        "user_message",
    ]
    assert parsed.turn.visible_items[0].data["content"] == "one\ntwo"


@pytest.mark.parametrize(("changes", "message"), [
    ({"model": "other"}, "model does not exist"),
    ({"max_tokens": None}, "max_tokens is required"),
    ({"max_tokens": 0}, "positive integer"),
    ({"max_tokens": True}, "positive integer"),
    ({"messages": []}, "non-empty array"),
    ({"messages": [{"role": "assistant", "content": "prefill"}]},
     "final message"),
    ({"tool_choice": {"type": "any"}}, "Only tool_choice"),
    ({"tools": [{"name": "x", "input_schema": []}]}, "input_schema"),
    ({"messages": [{"role": "user", "content": [{
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "x"},
    }]}]}, "Only text"),
])
def test_anthropic_parser_rejects_unsupported_semantics(changes, message):
    with pytest.raises(AnthropicMessagesError, match=message):
        _parse(**changes)


def test_anthropic_parser_rejects_unknown_or_incomplete_tool_results():
    with pytest.raises(AnthropicMessagesError, match="exactly one result"):
        _parse(messages=[
            {"role": "user", "content": "Look"},
            {"role": "assistant", "content": [{
                "type": "tool_use",
                "id": "toolu_1",
                "name": "lookup",
                "input": {"q": "x"},
            }]},
            {"role": "user", "content": [{
                "type": "tool_result",
                "tool_use_id": "other",
                "content": "bad",
            }]},
        ])


def test_anthropic_strict_mode_rejects_compatibility_noops():
    publication = {
        "publication_id": "a2ap_test",
        "api_generation": 1,
        "api_model_id": "pawflow-agent",
        "strict_fields": True,
    }
    with pytest.raises(AnthropicMessagesError, match="strict field mode"):
        _parse(publication=publication, temperature=0.2)


def test_anthropic_payload_uses_native_text_and_usage():
    result = AgentFinalResult(
        conversation_id="conv",
        turn_id="turn",
        response="hello",
        finish_reason="stop",
        tokens_in=7,
        tokens_out=2,
    )

    payload = anthropic_message_payload(
        result,
        message_id="msg_test",
        model="pawflow-agent",
    )

    assert payload == {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "pawflow-agent",
        "content": [{"type": "text", "text": "hello"}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 7, "output_tokens": 2},
    }


def test_anthropic_tool_round_trip_settles_parent_batch(configured):
    first_runtime = _Runtime([AgentFinalResult(
        conversation_id="ignored",
        turn_id="ignored",
        outcome="client_tool_pending",
        finish_reason="client_tool_pending",
        client_tool_calls=[{
            "id": "toolu_weather",
            "name": "lookup",
            "arguments": {"q": "weather"},
        }],
    )])
    first = prepare_anthropic_message_run(
        configured["store"],
        configured["publication"],
        configured["key"],
        {
            "model": "pawflow-agent",
            "max_tokens": 128,
            "messages": [{"role": "user", "content": "Weather?"}],
            "tools": [_TOOL],
        },
        request_id="req_anthropic_tool",
        conversation_store=configured["conversations"],
        runtime_api=first_runtime,
        hash_secret=b"test-secret",
    )
    first_payload = wait_anthropic_message_payload(first.run)
    call = first_payload["content"][0]

    assert call == {
        "type": "tool_use",
        "id": "toolu_weather",
        "name": "lookup",
        "input": {"q": "weather"},
    }
    assert first_payload["stop_reason"] == "tool_use"

    second_runtime = _Runtime([AgentFinalResult(
        conversation_id="ignored",
        turn_id="ignored",
        response="Sunny.",
        finish_reason="stop",
    )])
    second = prepare_anthropic_message_run(
        configured["store"],
        configured["publication"],
        configured["key"],
        {
            "model": "pawflow-agent",
            "max_tokens": 128,
            "messages": [
                {"role": "user", "content": "Weather?"},
                {"role": "assistant", "content": first_payload["content"]},
                {"role": "user", "content": [{
                    "type": "tool_result",
                    "tool_use_id": call["id"],
                    "content": "sunny",
                }]},
            ],
            "tools": [_TOOL],
        },
        request_id="req_anthropic_tool_result",
        conversation_store=configured["conversations"],
        runtime_api=second_runtime,
        hash_secret=b"test-secret",
    )
    payload = wait_anthropic_message_payload(second.run)

    assert payload["content"] == [{"type": "text", "text": "Sunny."}]
    pending = configured["store"].get_api_tool_batch_for_run(
        "req_anthropic_tool")
    assert pending["state"] == "settled"
    assert pending["settled_by_run_id"] == "req_anthropic_tool_result"
    assert second_runtime.submissions[0].ingress_messages[0].role == "tool"
    assert (
        first_runtime.submissions[0].conversation_id
        == second_runtime.submissions[0].conversation_id
    )


def test_anthropic_stream_emits_named_text_lifecycle(configured):
    runtime = _Runtime([AgentFinalResult(
        conversation_id="ignored",
        turn_id="ignored",
        response="hello",
        finish_reason="stop",
        tokens_in=2,
        tokens_out=1,
    )], live_text="hel")
    admission = prepare_anthropic_message_run(
        configured["store"],
        configured["publication"],
        configured["key"],
        {
            "model": "pawflow-agent",
            "max_tokens": 128,
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": True,
        },
        request_id="req_anthropic_stream",
        conversation_store=configured["conversations"],
        runtime_api=runtime,
        hash_secret=b"test-secret",
    )

    frames = list(iter_anthropic_message_sse(admission))
    events = _events(frames)
    types = [event["type"] for event in events]

    assert types == [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
    ]
    assert all(
        frame.startswith(b"event: " + event["type"].encode() + b"\n")
        for frame, event in zip(frames, events)
    )
    assert "".join(
        event["delta"]["text"]
        for event in events
        if event["type"] == "content_block_delta"
    ) == "hello"
    assert events[-2]["delta"]["stop_reason"] == "end_turn"


def test_anthropic_stream_emits_tool_input_json_delta(configured):
    runtime = _Runtime([AgentFinalResult(
        conversation_id="ignored",
        turn_id="ignored",
        outcome="client_tool_pending",
        finish_reason="client_tool_pending",
        client_tool_calls=[{
            "id": "toolu_stream",
            "name": "lookup",
            "arguments": {"q": "weather"},
        }],
    )])
    admission = prepare_anthropic_message_run(
        configured["store"],
        configured["publication"],
        configured["key"],
        {
            "model": "pawflow-agent",
            "max_tokens": 128,
            "messages": [{"role": "user", "content": "Weather?"}],
            "tools": [_TOOL],
            "stream": True,
        },
        request_id="req_anthropic_tool_stream",
        conversation_store=configured["conversations"],
        runtime_api=runtime,
        hash_secret=b"test-secret",
    )

    events = _events(list(iter_anthropic_message_sse(admission)))
    delta = next(
        event for event in events
        if event.get("delta", {}).get("type") == "input_json_delta"
    )

    assert json.loads(delta["delta"]["partial_json"]) == {"q": "weather"}
    assert events[-2]["delta"]["stop_reason"] == "tool_use"
    assert events[-1]["type"] == "message_stop"


def test_anthropic_stream_serializes_post_header_failure(configured):
    runtime = _Runtime([AgentFinalResult(
        conversation_id="ignored",
        turn_id="ignored",
        error="failed",
    )])
    admission = prepare_anthropic_message_run(
        configured["store"],
        configured["publication"],
        configured["key"],
        {
            "model": "pawflow-agent",
            "max_tokens": 128,
            "messages": [{"role": "user", "content": "Fail"}],
            "stream": True,
        },
        request_id="req_anthropic_failure",
        conversation_store=configured["conversations"],
        runtime_api=runtime,
        hash_secret=b"test-secret",
    )

    events = _events(list(iter_anthropic_message_sse(admission)))

    assert [event["type"] for event in events] == ["message_start", "error"]
    assert events[-1]["error"] == {
        "type": "api_error",
        "message": "The published agent run failed.",
    }
    assert events[-1]["request_id"] == "req_anthropic_failure"


class _EndpointRequest:
    def __init__(self, *, body=None, headers=None):
        self.body = json.dumps(body or {}).encode("utf-8")
        self.path_params = {"publication_id": "a2ap_test"}
        self.headers = headers or {
            "Host": "pawflow.example",
            "x-api-key": "pfa2a_test",
            "anthropic-version": "2023-06-01",
        }
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


def _endpoint_publication(**changes):
    value = {
        "publication_id": "a2ap_test",
        "owner_user_id": "alice",
        "conversation_id": "conv-1",
        "agent_name": "Agent",
        "enabled": True,
        "context_policy": "isolated",
        "standard_api_enabled": True,
        "api_anthropic_messages_enabled": True,
        "api_model_id": "pawflow-agent",
        "created_at": 123,
    }
    value.update(changes)
    return value


def _access(publication=None, *, error=""):
    return SimpleNamespace(
        error=error,
        publication=publication,
        key={"key_id": "key_test"} if publication else None,
    )


def test_anthropic_handler_uses_x_api_key_and_requires_supported_headers(
        monkeypatch):
    from core import standard_api_config
    from services import published_agent_auth
    from services.standard_api_endpoint import handle_anthropic_messages

    monkeypatch.setitem(
        standard_api_config.DIALECT_AVAILABILITY,
        "anthropic_messages",
        True,
    )
    seen = []

    def resolve(_req, **kwargs):
        seen.append(kwargs)
        return _access(_endpoint_publication())

    monkeypatch.setattr(published_agent_auth, "resolve_published_agent", resolve)

    invalid = _EndpointRequest(headers={
        "Host": "pawflow.example",
        "x-api-key": "native-key",
    })
    handle_anthropic_messages(invalid)
    status, headers, payload = _decoded(invalid)
    assert status == 400
    assert headers["request-id"].startswith("req_")
    assert payload["error"]["type"] == "invalid_request_error"
    assert "anthropic-version" in payload["error"]["message"]

    unsupported = _EndpointRequest(headers={
        "Host": "pawflow.example",
        "x-api-key": "native-key",
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "future-beta",
    })
    handle_anthropic_messages(unsupported)
    assert _decoded(unsupported)[0] == 400
    assert "anthropic-beta" in _decoded(unsupported)[2]["error"]["message"]

    valid = _EndpointRequest(body={
        "model": "pawflow-agent",
        "max_tokens": 128,
        "messages": [{"role": "user", "content": "hello"}],
    }, headers={
        "Host": "pawflow.example",
        "X-Api-Key": "native-key",
        "Anthropic-Version": "2023-06-01",
    })
    from core import standard_api_anthropic
    admission = SimpleNamespace(
        run=SimpleNamespace(turn=SimpleNamespace(stream=False)))
    monkeypatch.setattr(
        standard_api_anthropic,
        "prepare_anthropic_message_run",
        lambda *_args, **_kwargs: admission,
    )
    monkeypatch.setattr(
        standard_api_anthropic,
        "wait_anthropic_message_payload",
        lambda _run: {"id": "msg_handler", "type": "message"},
    )

    handle_anthropic_messages(valid)
    assert valid.done.wait(2)
    assert _decoded(valid)[0] == 200
    assert seen[-1] == {"credential": "native-key"}


def test_anthropic_handler_maps_auth_policy_and_disabled_shapes(monkeypatch):
    from core import standard_api_config
    from services import published_agent_auth
    from services.standard_api_endpoint import handle_anthropic_messages

    monkeypatch.setitem(
        standard_api_config.DIALECT_AVAILABILITY,
        "anthropic_messages",
        True,
    )
    monkeypatch.setattr(
        published_agent_auth,
        "resolve_published_agent",
        lambda _req, **_kwargs: _access(error="unauthorized"),
    )
    unauthorized = _EndpointRequest()
    handle_anthropic_messages(unauthorized)
    status, _headers, payload = _decoded(unauthorized)
    assert status == 401
    assert payload["error"]["type"] == "authentication_error"

    monkeypatch.setattr(
        published_agent_auth,
        "resolve_published_agent",
        lambda _req, **_kwargs: _access(error="origin_forbidden"),
    )
    forbidden = _EndpointRequest()
    handle_anthropic_messages(forbidden)
    assert _decoded(forbidden)[0] == 403
    assert _decoded(forbidden)[2]["error"]["type"] == "permission_error"

    disabled = _endpoint_publication(standard_api_enabled=False)
    monkeypatch.setattr(
        published_agent_auth,
        "resolve_published_agent",
        lambda _req, **_kwargs: _access(disabled),
    )
    missing = _EndpointRequest()
    handle_anthropic_messages(missing)
    assert _decoded(missing)[0] == 404
    assert _decoded(missing)[2]["error"]["type"] == "not_found_error"


def test_anthropic_model_and_message_handlers_use_native_shapes(monkeypatch):
    from core import standard_api_config
    from core import standard_api_anthropic
    from services import published_agent_auth
    from services.standard_api_endpoint import (
        handle_anthropic_messages,
        handle_anthropic_models,
    )

    monkeypatch.setitem(
        standard_api_config.DIALECT_AVAILABILITY,
        "anthropic_messages",
        True,
    )
    publication = _endpoint_publication()
    monkeypatch.setattr(
        published_agent_auth,
        "resolve_published_agent",
        lambda _req, **_kwargs: _access(publication),
    )

    listing = _EndpointRequest()
    handle_anthropic_models(listing)
    status, headers, payload = _decoded(listing)
    assert status == 200
    assert headers["request-id"].startswith("req_")
    assert payload["data"][0]["id"] == "pawflow-agent"
    assert payload["data"][0]["type"] == "model"
    assert payload["has_more"] is False

    stream_admission = SimpleNamespace(
        run=SimpleNamespace(turn=SimpleNamespace(stream=True)))
    monkeypatch.setattr(
        standard_api_anthropic,
        "prepare_anthropic_message_run",
        lambda *_args, **_kwargs: stream_admission,
    )
    monkeypatch.setattr(
        standard_api_anthropic,
        "iter_anthropic_message_sse",
        lambda _admission: iter((b"event: message_stop\n\n",)),
    )
    streaming = _EndpointRequest(body={
        "model": "pawflow-agent",
        "max_tokens": 128,
        "messages": [{"role": "user", "content": "hello"}],
        "stream": True,
    })
    handle_anthropic_messages(streaming)
    assert streaming.streamed[0] == 200
    assert streaming.streamed[1]["Content-Type"] == "text/event-stream"
    assert streaming.streamed[1]["request-id"].startswith("req_")
    assert list(streaming.streamed[2]) == [b"event: message_stop\n\n"]


def test_official_anthropic_sdk_parses_message_and_stream_contract(configured):
    anthropic = pytest.importorskip("anthropic")
    httpx2 = pytest.importorskip("httpx2")

    payload_runtime = _Runtime([AgentFinalResult(
        conversation_id="ignored",
        turn_id="ignored",
        response="hello",
        finish_reason="stop",
        tokens_in=2,
        tokens_out=1,
    )])
    payload_run = prepare_anthropic_message_run(
        configured["store"],
        configured["publication"],
        configured["key"],
        {
            "model": "pawflow-agent",
            "max_tokens": 128,
            "messages": [{"role": "user", "content": "hello"}],
        },
        request_id="req_anthropic_sdk_payload",
        conversation_store=configured["conversations"],
        runtime_api=payload_runtime,
        hash_secret=b"test-secret",
    )
    payload = wait_anthropic_message_payload(payload_run.run)

    stream_runtime = _Runtime([AgentFinalResult(
        conversation_id="ignored",
        turn_id="ignored",
        response="hello",
        finish_reason="stop",
        tokens_in=2,
        tokens_out=1,
    )], live_text="hello")
    stream_run = prepare_anthropic_message_run(
        configured["store"],
        configured["publication"],
        configured["key"],
        {
            "model": "pawflow-agent",
            "max_tokens": 128,
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
        },
        request_id="req_anthropic_sdk_stream",
        conversation_store=configured["conversations"],
        runtime_api=stream_runtime,
        hash_secret=b"test-secret",
    )
    stream_body = b"".join(iter_anthropic_message_sse(stream_run))

    def handler(request):
        body = json.loads(request.content)
        if body.get("stream"):
            return httpx2.Response(
                200,
                content=stream_body,
                headers={"content-type": "text/event-stream"},
            )
        return httpx2.Response(
            200,
            json=payload,
            headers={"request-id": "req-sdk"},
        )

    with httpx2.Client(
            transport=httpx2.MockTransport(handler)) as http_client:
        client = anthropic.Anthropic(
            api_key="test-key",
            base_url="https://pawflow.test/anthropic/publication",
            http_client=http_client,
            max_retries=0,
        )
        message = client.messages.create(
            model="pawflow-agent",
            max_tokens=128,
            messages=[{"role": "user", "content": "hello"}],
        )
        stream = client.messages.create(
            model="pawflow-agent",
            max_tokens=128,
            messages=[{"role": "user", "content": "hello"}],
            stream=True,
        )
        events = list(stream)

    assert message.id == payload["id"]
    assert message.content[0].text == "hello"
    assert events[0].type == "message_start"
    assert events[-1].type == "message_stop"
