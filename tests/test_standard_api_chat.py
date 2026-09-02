"""OpenAI Chat Completions contracts for published PawFlow agents."""

from __future__ import annotations

import json
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from core.a2a_store import A2AStore
from core.agent_runtime_api import (
    AgentFinalResult,
    AgentSubmission,
)
from core.standard_api_chat import (
    OpenAIChatAdmission,
    OpenAIChatError,
    OpenAIChatRun,
    OpenAIChatRunError,
    iter_openai_chat_sse,
    openai_chat_completion_payload,
    parse_openai_chat_request,
    prepare_openai_chat_run,
    wait_openai_chat_payload,
)


_CONFIG = {
    "standard_api_enabled": True,
    "api_model_id": "pawflow-agent",
    "api_permission_mode": "read_only",
    "api_session_ttl_seconds": 3600,
    "api_max_sessions_per_key": 20,
    "api_max_concurrent_runs_per_key": 2,
    "strict_fields": False,
    "api_request_overrides_json": {},
    "api_input_modalities_json": ["text"],
    "api_chat_completions_enabled": True,
    "api_responses_enabled": False,
    "api_anthropic_messages_enabled": False,
    "api_disconnect_policy": "cancel",
}

_TOOL = {
    "type": "function",
    "function": {
        "name": "lookup",
        "description": "Look up a value",
        "parameters": {
            "type": "object",
            "properties": {"q": {"type": "string"}},
            "required": ["q"],
        },
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
        standard_api_config.DIALECT_AVAILABILITY, "chat_completions", True)
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
        "messages": [{"role": "user", "content": "hello"}],
    }
    body.update(changes)
    return parse_openai_chat_request(
        publication, key, body,
        request_id="req_test",
        hash_secret=b"test-secret",
    )


def test_chat_parser_normalizes_transcript_tools_and_actionable_suffix():
    turn, include_usage = _parse(
        messages=[
            {"role": "system", "content": "Be concise"},
            {"role": "user", "content": "Weather?"},
            {"role": "assistant", "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "lookup",
                    "arguments": '{"q":"weather"}',
                },
            }]},
            {"role": "tool", "tool_call_id": "call_1", "content": "sunny"},
            {"role": "user", "content": [{"type": "text", "text": "Summarize"}]},
        ],
        tools=[_TOOL],
        stream=True,
        stream_options={"include_usage": True},
    )

    assert [item.kind for item in turn.visible_items] == [
        "client_instruction",
        "user_message",
        "client_tool_call_batch",
        "client_tool_result_batch",
        "user_message",
    ]
    assert turn.actionable_suffix_start == 3
    assert turn.client_tools[0]["name"] == "lookup"
    assert turn.stream is True
    assert include_usage is True


@pytest.mark.parametrize(("changes", "message"), [
    ({"model": "other"}, "model does not exist"),
    ({"n": 2}, "Only n=1"),
    ({"store": True}, "Stored Chat"),
    ({"parallel_tool_calls": False}, "cannot be enforced"),
    ({"tool_choice": "required"}, "Only tool_choice"),
    ({"tool_choice": {"type": "function"}}, "Only tool_choice"),
    ({"response_format": {"type": "json_object"}}, "Only text"),
    ({"messages": [{"role": "assistant", "content": "done"}]},
     "final message"),
    ({"messages": [
        {"role": "user", "content": "x"},
        {"role": "assistant", "tool_calls": [{
            "id": "call_1", "type": "function",
            "function": {"name": "lookup", "arguments": "{}"},
        }]},
        {"role": "tool", "tool_call_id": "other", "content": "x"},
    ]}, "exactly one result"),
])
def test_chat_parser_rejects_unsupported_semantics(changes, message):
    with pytest.raises(OpenAIChatError, match=message):
        _parse(**changes)


def test_strict_field_mode_rejects_compatibility_noops():
    publication = {
        "publication_id": "a2ap_test",
        "api_generation": 1,
        "api_model_id": "pawflow-agent",
        "strict_fields": True,
    }
    with pytest.raises(OpenAIChatError, match="strict field mode"):
        _parse(publication=publication, temperature=0.2)


def test_chat_parser_accepts_sdk_null_assistant_content_as_empty_history():
    turn, _include_usage = _parse(messages=[
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": None},
        {"role": "user", "content": "next"},
    ])

    assert turn.visible_items[1].kind == "assistant_message"
    assert turn.visible_items[1].data["content"] == ""
    assert turn.actionable_suffix_start == 2


def test_chat_completion_payload_uses_published_model_and_native_tools():
    result = AgentFinalResult(
        conversation_id="conv",
        turn_id="turn",
        outcome="client_tool_pending",
        client_tool_calls=[{
            "id": "call_1",
            "name": "lookup",
            "arguments": {"q": "weather"},
        }],
        model="private-provider-model",
        provider="private-provider",
        tokens_in=12,
        tokens_out=3,
    )

    payload = openai_chat_completion_payload(
        result,
        completion_id="chatcmpl_test",
        created=123,
        model="pawflow-agent",
    )

    assert payload["model"] == "pawflow-agent"
    assert "private-provider" not in json.dumps(payload)
    assert payload["choices"][0]["finish_reason"] == "tool_calls"
    call = payload["choices"][0]["message"]["tool_calls"][0]
    assert call["function"] == {
        "name": "lookup",
        "arguments": '{"q":"weather"}',
    }
    assert payload["usage"] == {
        "prompt_tokens": 12,
        "completion_tokens": 3,
        "total_tokens": 15,
    }


def test_official_openai_sdk_parses_non_stream_and_stream_contract():
    openai = pytest.importorskip("openai")
    import httpx

    result = AgentFinalResult(
        conversation_id="conv",
        turn_id="turn",
        response="hello",
        finish_reason="stop",
        tokens_in=4,
        tokens_out=1,
    )
    payload = openai_chat_completion_payload(
        result,
        completion_id="chatcmpl_sdk",
        created=123,
        model="pawflow-agent",
    )
    events = [("token", {
        "text": "hello",
        "msg_id": "msg-sdk",
        "turn_id": "turn",
    })]
    run = SimpleNamespace(
        completion_id="chatcmpl_sdk_stream",
        created=124,
        publication={
            "api_model_id": "pawflow-agent",
            "api_disconnect_policy": "cancel",
        },
        result=result,
        error=None,
        _events=events,
    )
    run.event_slice = lambda offset, timeout: (
        events[offset:], len(events), True)
    run.cancel = lambda: None
    admission = SimpleNamespace(run=run, include_usage=True, owner=True)
    stream_body = b"".join(iter_openai_chat_sse(admission))
    requests = []

    def _handler(request):
        assert request.method == "POST"
        assert request.url.path == "/openai/publication/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer sk-test"
        body = json.loads(request.content)
        turn, include_usage = _parse(**body)
        assert turn.visible_items[-1].data["content"] == "hello"
        requests.append(body)
        if body.get("stream"):
            assert include_usage is True
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
        completion = client.chat.completions.create(
            model="pawflow-agent",
            messages=[{"role": "user", "content": "hello"}],
        )
        stream = client.chat.completions.create(
            model="pawflow-agent",
            messages=[{"role": "user", "content": "hello"}],
            stream=True,
            stream_options={"include_usage": True},
        )
        chunks = list(stream)

    assert completion.id == "chatcmpl_sdk"
    assert completion.choices[0].message.content == "hello"
    assert completion.usage.total_tokens == 5
    assert "".join(
        chunk.choices[0].delta.content or ""
        for chunk in chunks
        if chunk.choices
    ) == "hello"
    assert chunks[-1].choices == []
    assert chunks[-1].usage.total_tokens == 5
    assert [request.get("stream", False) for request in requests] == [False, True]


def test_chat_run_finalizes_text_and_streams_sdk_shaped_chunks(configured):
    runtime = _Runtime([
        AgentFinalResult(
            conversation_id="ignored",
            turn_id="ignored",
            response="hello",
            finish_reason="stop",
            tokens_in=4,
            tokens_out=2,
        )
    ], live_text="hel")
    admission = prepare_openai_chat_run(
        configured["store"],
        configured["publication"],
        configured["key"],
        {
            "model": "pawflow-agent",
            "messages": [{"role": "user", "content": "say hello"}],
            "stream": True,
            "stream_options": {"include_usage": True},
        },
        request_id="req_stream",
        conversation_store=configured["conversations"],
        runtime_api=runtime,
        hash_secret=b"test-secret",
    )

    frames = list(iter_openai_chat_sse(admission))
    decoded = [
        json.loads(frame[len(b"data: "):].decode("utf-8"))
        for frame in frames
        if frame.startswith(b"data: {")
    ]

    assert frames[-1] == b"data: [DONE]\n\n"
    assert decoded[0]["choices"][0]["delta"]["role"] == "assistant"
    assert "".join(
        chunk["choices"][0]["delta"].get("content", "")
        for chunk in decoded
        if chunk["choices"]
    ) == "hello"
    assert decoded[-1]["choices"] == []
    assert decoded[-1]["usage"]["total_tokens"] == 6
    payload = wait_openai_chat_payload(admission.run)
    assert payload["choices"][0]["message"]["content"] == "hello"
    assert configured["store"].get_api_run("req_stream")["status"] == "completed"


def test_chat_tool_round_trip_reuses_session_and_settles_exact_batch(configured):
    first_runtime = _Runtime([
        AgentFinalResult(
            conversation_id="ignored",
            turn_id="ignored",
            outcome="client_tool_pending",
            finish_reason="client_tool_pending",
            client_tool_calls=[{
                "id": "call_weather",
                "name": "lookup",
                "arguments": {"q": "weather"},
            }],
            tokens_in=7,
            tokens_out=1,
        )
    ])
    first = prepare_openai_chat_run(
        configured["store"],
        configured["publication"],
        configured["key"],
        {
            "model": "pawflow-agent",
            "messages": [{"role": "user", "content": "Weather?"}],
            "tools": [_TOOL],
        },
        request_id="req_tool_call",
        conversation_store=configured["conversations"],
        runtime_api=first_runtime,
        hash_secret=b"test-secret",
    )
    first_payload = wait_openai_chat_payload(first.run)
    assert first_payload["choices"][0]["finish_reason"] == "tool_calls"
    pending = configured["store"].get_api_tool_batch_for_run("req_tool_call")
    assert pending["state"] == "pending"

    second_runtime = _Runtime([
        AgentFinalResult(
            conversation_id="ignored",
            turn_id="ignored",
            response="It is sunny.",
            finish_reason="stop",
            tokens_in=9,
            tokens_out=4,
        )
    ])
    second = prepare_openai_chat_run(
        configured["store"],
        configured["publication"],
        configured["key"],
        {
            "model": "pawflow-agent",
            "messages": [
                {"role": "user", "content": "Weather?"},
                {"role": "assistant", "tool_calls": [{
                    "id": "call_weather",
                    "type": "function",
                    "function": {
                        "name": "lookup",
                        "arguments": '{"q":"weather"}',
                    },
                }]},
                {
                    "role": "tool",
                    "tool_call_id": "call_weather",
                    "content": "sunny",
                },
            ],
            "tools": [_TOOL],
        },
        request_id="req_tool_result",
        conversation_store=configured["conversations"],
        runtime_api=second_runtime,
        hash_secret=b"test-secret",
    )
    second_payload = wait_openai_chat_payload(second.run)

    assert second_payload["choices"][0]["message"]["content"] == "It is sunny."
    settled = configured["store"].get_api_tool_batch_for_run("req_tool_call")
    assert settled["state"] == "settled"
    assert settled["settled_by_run_id"] == "req_tool_result"
    assert (
        first_runtime.submissions[0].conversation_id
        == second_runtime.submissions[0].conversation_id
    )
    assert second_runtime.submissions[0].ingress_messages[0].role == "tool"


def test_concurrent_exact_retry_attaches_to_one_chat_run(configured):
    runtime = _BlockingRuntime(AgentFinalResult(
        conversation_id="ignored",
        turn_id="ignored",
        response="shared",
        finish_reason="stop",
    ))
    barrier = threading.Barrier(2)

    def _prepare(index):
        barrier.wait()
        return prepare_openai_chat_run(
            configured["store"],
            configured["publication"],
            configured["key"],
            {
                "model": "pawflow-agent",
                "messages": [{"role": "user", "content": "same request"}],
            },
            request_id=f"req_retry_{index}",
            conversation_store=configured["conversations"],
            runtime_api=runtime,
            hash_secret=b"test-secret",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        admissions = list(executor.map(_prepare, range(2)))

    assert admissions[0].run is admissions[1].run
    assert sorted(admission.owner for admission in admissions) == [False, True]
    assert len(runtime.submissions) == 1
    runtime.release.set()
    assert wait_openai_chat_payload(
        admissions[0].run)["choices"][0]["message"]["content"] == "shared"


@pytest.mark.parametrize(("policy", "should_cancel"), [
    ("cancel", True),
    ("finish_detached", False),
])
def test_chat_stream_close_obeys_disconnect_policy(
        monkeypatch, policy, should_cancel):
    from tasks.ai.agent_loop import AgentLoopTask

    stopped = []
    failed = []
    monkeypatch.setattr(
        AgentLoopTask,
        "force_stop_agent",
        staticmethod(lambda conversation_id, agent_name:
                     stopped.append((conversation_id, agent_name))),
    )
    store = SimpleNamespace(
        fail_api_run=lambda run_id, lease_id, **kwargs:
        failed.append((run_id, lease_id, kwargs)) or True,
    )
    resolution = SimpleNamespace(
        session={"internal_conversation_id": "conv-disconnect"},
        run={"run_id": "run-disconnect"},
        lease_id="lease-disconnect",
    )
    run = OpenAIChatRun(
        store=store,
        publication={
            "api_model_id": "pawflow-agent",
            "api_disconnect_policy": policy,
            "agent_name": "Agent",
        },
        key={},
        turn=SimpleNamespace(),
        resolution=resolution,
        runtime_api=None,
        hash_secret=b"test-secret",
        completion_id="chatcmpl_disconnect",
        created=123,
    )
    stream = iter_openai_chat_sse(OpenAIChatAdmission(
        run=run, include_usage=False, owner=True))

    assert next(stream).startswith(b"data: {")
    stream.close()

    assert bool(failed) is should_cancel
    assert bool(stopped) is should_cancel
    assert run.done is should_cancel
    if should_cancel:
        assert failed[0][2]["canceled"] is True
        assert run.error.code == "client_disconnected"


def test_chat_stream_serializes_post_header_error_then_done():
    run = OpenAIChatRun(
        store=SimpleNamespace(),
        publication={
            "api_model_id": "pawflow-agent",
            "api_disconnect_policy": "cancel",
            "agent_name": "Agent",
        },
        key={},
        turn=SimpleNamespace(),
        resolution=SimpleNamespace(),
        runtime_api=None,
        hash_secret=b"test-secret",
        completion_id="chatcmpl_error",
        created=123,
    )
    run.fail(OpenAIChatRunError("failed after headers", code="agent_error"))

    frames = list(iter_openai_chat_sse(OpenAIChatAdmission(
        run=run, include_usage=False, owner=True)))

    error = json.loads(frames[-2][len(b"data: "):])
    assert error["error"] == {
        "message": "failed after headers",
        "type": "server_error",
        "param": None,
        "code": "agent_error",
    }
    assert frames[-1] == b"data: [DONE]\n\n"


class _EndpointRequest:
    def __init__(self, *, body=None, model_id=""):
        self.path_params = {
            "publication_id": "a2ap_test",
            "model_id": model_id,
        }
        self.headers = {"Authorization": "Bearer key"}
        self.body = (
            json.dumps(body).encode("utf-8") if body is not None else b"")
        self.response = None
        self.stream = None
        self.event = threading.Event()

    def complete(self, status, headers, body):
        self.response = (status, headers, body)
        self.event.set()

    def complete_stream(self, status, headers, stream):
        self.response = (status, headers, b"")
        self.stream = stream
        self.event.set()


def _access(publication=None, *, error=""):
    return SimpleNamespace(
        error=error,
        publication=publication,
        key={"key_id": "key_test"} if publication else None,
    )


def _endpoint_publication(**changes):
    value = {
        "publication_id": "a2ap_test",
        "owner_user_id": "alice",
        "conversation_id": "conv-1",
        "agent_name": "Agent",
        "enabled": True,
        "context_policy": "isolated",
        "standard_api_enabled": True,
        "api_chat_completions_enabled": True,
        "api_responses_enabled": False,
        "api_model_id": "pawflow-agent",
        "created_at": 123,
    }
    value.update(changes)
    return value


def test_openai_model_handlers_authenticate_and_expose_only_published_model(
        monkeypatch):
    from core import standard_api_config
    from services import published_agent_auth
    from services.standard_api_endpoint import (
        handle_openai_model,
        handle_openai_models,
    )

    monkeypatch.setitem(
        standard_api_config.DIALECT_AVAILABILITY, "chat_completions", True)
    publication = _endpoint_publication()
    monkeypatch.setattr(
        published_agent_auth,
        "resolve_published_agent",
        lambda req: _access(publication),
    )
    listing = _EndpointRequest()
    handle_openai_models(listing)
    payload = json.loads(listing.response[2])
    assert listing.response[0] == 200
    assert payload == {
        "object": "list",
        "data": [{
            "id": "pawflow-agent",
            "object": "model",
            "created": 123,
            "owned_by": "pawflow",
        }],
    }

    missing = _EndpointRequest(model_id="private-model")
    handle_openai_model(missing)
    error = json.loads(missing.response[2])["error"]
    assert missing.response[0] == 404
    assert error["code"] == "model_not_found"


def test_openai_handlers_use_native_auth_and_disabled_not_found_shapes(
        monkeypatch):
    from core import standard_api_config
    from services import published_agent_auth
    from services.standard_api_endpoint import handle_openai_chat_completions

    monkeypatch.setitem(
        standard_api_config.DIALECT_AVAILABILITY, "chat_completions", True)
    monkeypatch.setattr(
        published_agent_auth,
        "resolve_published_agent",
        lambda req: _access(error="unauthorized"),
    )
    unauthorized = _EndpointRequest()
    handle_openai_chat_completions(unauthorized)
    assert unauthorized.response[0] == 401
    assert unauthorized.response[1]["WWW-Authenticate"] == "Bearer"
    assert json.loads(unauthorized.response[2])["error"]["code"] == "invalid_api_key"

    disabled = _endpoint_publication(standard_api_enabled=False)
    monkeypatch.setattr(
        published_agent_auth,
        "resolve_published_agent",
        lambda req: _access(disabled),
    )
    unavailable = _EndpointRequest()
    handle_openai_chat_completions(unavailable)
    assert unavailable.response[0] == 404
    assert json.loads(unavailable.response[2])["error"]["code"] == "not_found"


def test_openai_chat_handler_rejects_origin_and_invalid_wire_before_runtime(
        monkeypatch):
    from core import standard_api_config
    from services import published_agent_auth
    from services.standard_api_endpoint import handle_openai_chat_completions

    monkeypatch.setitem(
        standard_api_config.DIALECT_AVAILABILITY, "chat_completions", True)
    monkeypatch.setattr(
        published_agent_auth,
        "resolve_published_agent",
        lambda req: _access(error="origin_forbidden"),
    )
    forbidden = _EndpointRequest()
    handle_openai_chat_completions(forbidden)
    assert forbidden.response[0] == 403
    assert json.loads(forbidden.response[2])["error"]["code"] == "forbidden"
    assert forbidden.response[1]["x-request-id"].startswith("req_")

    publication = _endpoint_publication()
    monkeypatch.setattr(
        published_agent_auth,
        "resolve_published_agent",
        lambda req: _access(publication),
    )
    invalid = _EndpointRequest()
    invalid.body = b"{not-json"
    handle_openai_chat_completions(invalid)
    assert invalid.response[0] == 400
    assert json.loads(invalid.response[2])["error"]["code"] == "invalid_json"
    assert invalid.response[1]["x-request-id"].startswith("req_")

    oversized = _EndpointRequest()
    oversized.body = b"x" * 2_000_001
    handle_openai_chat_completions(oversized)
    assert oversized.response[0] == 413
    assert json.loads(
        oversized.response[2])["error"]["code"] == "request_too_large"


def test_openai_chat_handler_opens_stream_only_after_admission(monkeypatch):
    from core import standard_api_config
    from services import published_agent_auth
    from services.standard_api_endpoint import handle_openai_chat_completions
    from core import standard_api_chat

    monkeypatch.setitem(
        standard_api_config.DIALECT_AVAILABILITY, "chat_completions", True)
    publication = _endpoint_publication()
    monkeypatch.setattr(
        published_agent_auth,
        "resolve_published_agent",
        lambda req: _access(publication),
    )
    run = SimpleNamespace(turn=SimpleNamespace(stream=True))
    admission = SimpleNamespace(run=run)
    monkeypatch.setattr(
        standard_api_chat, "prepare_openai_chat_run",
        lambda *args, **kwargs: admission)
    monkeypatch.setattr(
        standard_api_chat, "iter_openai_chat_sse",
        lambda value: iter((b"data: [DONE]\n\n",)))

    request = _EndpointRequest(body={
        "model": "pawflow-agent",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": True,
    })
    handle_openai_chat_completions(request)

    assert request.response[0] == 200
    assert request.response[1]["Content-Type"] == "text/event-stream"
    assert request.response[1]["x-request-id"].startswith("req_")
    assert list(request.stream) == [b"data: [DONE]\n\n"]


def test_openai_chat_handler_completes_non_stream_asynchronously(monkeypatch):
    from core import standard_api_config
    from services import published_agent_auth
    from services.standard_api_endpoint import handle_openai_chat_completions
    from core import standard_api_chat

    monkeypatch.setitem(
        standard_api_config.DIALECT_AVAILABILITY, "chat_completions", True)
    publication = _endpoint_publication()
    monkeypatch.setattr(
        published_agent_auth,
        "resolve_published_agent",
        lambda req: _access(publication),
    )
    run = SimpleNamespace(turn=SimpleNamespace(stream=False))
    admission = SimpleNamespace(run=run)
    payload = {"id": "chatcmpl_test", "object": "chat.completion"}
    monkeypatch.setattr(
        standard_api_chat, "prepare_openai_chat_run",
        lambda *args, **kwargs: admission)
    monkeypatch.setattr(
        standard_api_chat, "wait_openai_chat_payload",
        lambda value: payload)

    request = _EndpointRequest(body={
        "model": "pawflow-agent",
        "messages": [{"role": "user", "content": "hello"}],
    })
    handle_openai_chat_completions(request)

    assert request.event.wait(1)
    assert request.response[0] == 200
    assert json.loads(request.response[2]) == payload
