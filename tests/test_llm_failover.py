"""Tests for ordered LLM failover and AgentLoop cold-start handoff."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core import ServiceError, ServiceFactory
from core.llm_client import LLMMessage, LLMResponse, LLMToolCall
from services.llm_failover import (
    LLMFailoverExhausted,
    LLMFailoverRequired,
    LLMFailoverService,
)
from tasks.ai._alc_base import _ALC_CONTINUE
from tasks.ai._alc_llm_turn import _ALCLlmTurnMixin


class FakeClient:
    def __init__(self, service_id, responses, provider="openai"):
        self.service_id = service_id
        self.responses = list(responses)
        self.provider = provider
        self.default_model = f"{service_id}-model"
        self.base_url = f"https://{service_id}.example.test"
        self.supports_vision = True
        self.calls = []
        self.aborted = False

    def clone_for_call(self):
        return self

    def _answer(self, messages, kwargs):
        self.calls.append((list(messages), dict(kwargs)))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        callback = kwargs.get("callback")
        if callback and response.content:
            callback(response.content)
        return response

    def complete(self, messages, *args, **kwargs):
        return self._answer(messages, kwargs)

    def complete_stream(self, messages, *args, **kwargs):
        if len(args) >= 5 and "callback" not in kwargs:
            kwargs["callback"] = args[4]
        return self._answer(messages, kwargs)

    def abort(self):
        self.aborted = True

    def reset_abort(self):
        self.aborted = False


class FakeLLMService:
    TYPE = "llmConnection"

    def __init__(self, client, config=None):
        self.client = client
        self.config = config or {
            "cost_per_1m_input": 1,
            "cost_per_1m_output": 2,
        }

    def get_client(self, pool_index=-1):
        return self.client

    def get_pool_size(self):
        return 0


class FakeRegistry:
    def __init__(self, clients):
        self.services = {
            service_id: FakeLLMService(client)
            for service_id, client in clients.items()
        }

    def resolve_definition(self, service_id, **kwargs):
        service = self.services.get(service_id)
        if service is None:
            return None
        return SimpleNamespace(
            service_id=service_id,
            service_type=service.TYPE,
            enabled=True,
        )

    def resolve(self, service_id, **kwargs):
        return self.services.get(service_id)


def response(text):
    return LLMResponse(
        content=text,
        model="test-model",
        tokens_in=1,
        tokens_out=1,
    )


def make_service():
    service = LLMFailoverService({
        "_service_id": "resilient",
        "main_llm_service": "main",
        "fallback_llm_services": ["fallback-1", "fallback-2"],
    })
    service.connect()
    return service


def user_message():
    return [LLMMessage(
        role="user",
        content="Continue the work",
        conversation_id="conv-1",
    )]


def test_service_registration_and_configuration_validation():
    assert ServiceFactory.get("llmFailover") is LLMFailoverService
    schema = make_service().get_parameter_schema()
    assert schema["main_llm_service"]["service_type"] == "llmConnection"
    assert schema["fallback_llm_services"]["type"] == "json"

    invalid_configs = [
        {"main_llm_service": "", "fallback_llm_services": ["fallback"]},
        {"main_llm_service": "main", "fallback_llm_services": []},
        {"main_llm_service": "main", "fallback_llm_services": ["main"]},
        {"_service_id": "self", "main_llm_service": "self",
         "fallback_llm_services": ["fallback"]},
        {"main_llm_service": "main",
         "fallback_llm_services": ["fallback", "fallback"]},
    ]
    for config in invalid_configs:
        with pytest.raises(ServiceError):
            LLMFailoverService(config).connect()


def test_main_success_never_resolves_or_calls_a_fallback():
    main = FakeClient("main", [response("main answer")])
    registry = FakeRegistry({"main": main})
    client = make_service().get_client()

    with patch("core.service_registry.ServiceRegistry.get_instance",
               return_value=registry):
        result = client.complete(user_message())

    assert result.content == "main answer"
    assert len(main.calls) == 1


def test_direct_calls_try_candidates_in_definition_order():
    main = FakeClient("main", [RuntimeError("429")])
    fallback_1 = FakeClient("fallback-1", [RuntimeError("provider down")])
    fallback_2 = FakeClient("fallback-2", [response("continued")])
    registry = FakeRegistry({
        "main": main,
        "fallback-1": fallback_1,
        "fallback-2": fallback_2,
    })
    client = make_service().get_client()

    with patch("core.service_registry.ServiceRegistry.get_instance",
               return_value=registry):
        result = client.complete(user_message())

    assert result.content == "continued"
    assert [len(client.calls) for client in
            (main, fallback_1, fallback_2)] == [1, 1, 1]


def test_each_new_logical_call_starts_with_main_again():
    main = FakeClient("main", [RuntimeError("429"), response("main recovered")])
    fallback_1 = FakeClient("fallback-1", [response("temporary continuation")])
    registry = FakeRegistry({
        "main": main,
        "fallback-1": fallback_1,
        "fallback-2": FakeClient("fallback-2", []),
    })
    service = make_service()

    with patch("core.service_registry.ServiceRegistry.get_instance",
               return_value=registry):
        assert service.get_client().complete(user_message()).content == (
            "temporary continuation")
        assert service.get_client().complete(user_message()).content == (
            "main recovered")

    assert len(main.calls) == 2
    assert len(fallback_1.calls) == 1


def test_agent_call_raises_handoff_instead_of_replaying_stale_messages():
    main = FakeClient("main", [RuntimeError("429")])
    fallback_1 = FakeClient("fallback-1", [response("must not run yet")])
    registry = FakeRegistry({
        "main": main,
        "fallback-1": fallback_1,
        "fallback-2": FakeClient("fallback-2", []),
    })
    client = make_service().get_client()
    client._agent_ctx = {"active_agent_name": "assistant"}

    with patch("core.service_registry.ServiceRegistry.get_instance",
               return_value=registry):
        with pytest.raises(LLMFailoverRequired) as raised:
            client.complete_stream(user_message())

    assert raised.value.failed_service_id == "main"
    assert raised.value.next_service_id == "fallback-1"
    assert raised.value.next_attempt == 1
    assert raised.value.failures == [{
        "service_id": "main", "error_type": "RuntimeError"}]
    assert len(fallback_1.calls) == 0


def test_selected_candidate_survives_clone_and_drives_identity_and_cost():
    main = FakeClient("main", [])
    fallback_1 = FakeClient("fallback-1", [], provider="anthropic")
    registry = FakeRegistry({
        "main": main,
        "fallback-1": fallback_1,
        "fallback-2": FakeClient("fallback-2", []),
    })
    client = make_service().get_client()
    client.select_attempt(1)

    with patch("core.service_registry.ServiceRegistry.get_instance",
               return_value=registry):
        clone = client.clone_for_call()
        assert clone.active_service_id == "fallback-1"
        assert clone.provider == "anthropic"
        assert clone.default_model == "fallback-1-model"
        assert clone.get_cost_config()["cost_per_1m_output"] == 2


def test_all_candidates_exhausted_returns_one_safe_error():
    clients = {
        "main": FakeClient("main", [RuntimeError("secret body")]),
        "fallback-1": FakeClient("fallback-1", [RuntimeError("private URL")]),
        "fallback-2": FakeClient("fallback-2", [RuntimeError("api key")]),
    }
    client = make_service().get_client()

    with patch("core.service_registry.ServiceRegistry.get_instance",
               return_value=FakeRegistry(clients)):
        with pytest.raises(LLMFailoverExhausted) as raised:
            client.complete(user_message())

    message = str(raised.value)
    assert "3 LLM services" in message
    assert "secret body" not in message
    assert "private URL" not in message
    assert "api key" not in message
    assert [item["service_id"] for item in raised.value.failures] == [
        "main", "fallback-1", "fallback-2"]


def test_failure_history_survives_agent_cold_start_clients():
    fallback_1 = FakeClient("fallback-1", [RuntimeError("down")])
    fallback_2 = FakeClient("fallback-2", [RuntimeError("also down")])
    registry = FakeRegistry({
        "main": FakeClient("main", []),
        "fallback-1": fallback_1,
        "fallback-2": fallback_2,
    })
    prior = [{"service_id": "main", "error_type": "RuntimeError"}]

    first = make_service().get_client()
    first.select_attempt(1, failures=prior)
    first._agent_ctx = {"active_agent_name": "assistant"}
    with patch("core.service_registry.ServiceRegistry.get_instance",
               return_value=registry):
        with pytest.raises(LLMFailoverRequired) as raised:
            first.complete_stream(user_message())

        second = make_service().get_client()
        second.select_attempt(2, failures=raised.value.failures)
        second._agent_ctx = {"active_agent_name": "assistant"}
        with pytest.raises(LLMFailoverExhausted) as exhausted:
            second.complete_stream(user_message())

    assert [item["service_id"] for item in exhausted.value.failures] == [
        "main", "fallback-1", "fallback-2"]


def test_user_cancellation_never_advances_to_fallback():
    from tasks.ai.agent_exceptions import AgentCancelled

    main = FakeClient("main", [AgentCancelled()])
    fallback = FakeClient("fallback-1", [response("wrong")])
    registry = FakeRegistry({
        "main": main,
        "fallback-1": fallback,
        "fallback-2": FakeClient("fallback-2", []),
    })
    client = make_service().get_client()

    with patch("core.service_registry.ServiceRegistry.get_instance",
               return_value=registry):
        with pytest.raises(AgentCancelled):
            client.complete(user_message())

    assert len(fallback.calls) == 0


class TurnHarness(_ALCLlmTurnMixin):
    def __init__(self):
        self.prepare_calls = []
        self.rebound = None

    def _prepare_agent_context(self, flowfile, **kwargs):
        self.prepare_calls.append((flowfile, kwargs))
        return {
            "client": SimpleNamespace(),
            "registry": object(),
            "tool_defs": [],
            "messages": [
                LLMMessage(
                    role="user",
                    content="original request",
                    conversation_id="conv-1",
                ),
                LLMMessage(
                    role="assistant",
                    content="work already completed",
                    tool_calls=[
                        LLMToolCall(
                            id="pending-tool",
                            name="edit",
                            arguments={"path": "a.py"},
                        )
                    ],
                    conversation_id="conv-1",
                ),
            ],
            "model": "",
            "active_llm_service": "resilient",
            "_context_rebuild_args": {"flowfile": flowfile},
        }

    def _alc_rebind_context(self, st, new_ctx):
        self.rebound = new_ctx


def test_agent_handoff_flushes_and_rebuilds_from_current_persisted_context():
    handoff = LLMFailoverRequired(
        failed_service_id="main",
        next_service_id="fallback-1",
        next_attempt=1,
        cause=RuntimeError("429"),
        failures=[{"service_id": "main", "error_type": "RuntimeError"}],
    )
    state = SimpleNamespace(
        _budget_precheck_done=True,
        total_tokens_in=7,
        total_tokens_out=3,
        total_cache_read=0,
        total_cache_write=0,
        user_id="alice",
        conversation_id="conv-1",
        ctx={
            "active_agent_name": "assistant",
            "active_llm_service": "resilient",
            "_context_rebuild_args": {"flowfile": "flowfile"},
            "_consumed_cancel_checkpoint": "checkpoint",
        },
        _llm_call=lambda _messages: (_ for _ in ()).throw(handoff),
        _call_context=user_message(),
        emitter=SimpleNamespace(
            check_interrupt=lambda: False,
            check_cancelled=lambda: None,
        ),
    )
    harness = TurnHarness()
    writer = SimpleNamespace(flush=lambda timeout: True)

    with patch(
        "core.conversation_writer.ConversationWriter.for_conversation",
        return_value=writer,
    ):
        result = harness._alc_llm_turn(state)

    assert result is _ALC_CONTINUE
    assert harness.prepare_calls == [(
        "flowfile",
        {
            "force_cold": True,
            "failover_attempt": 1,
            "failover_failures": [
                {"service_id": "main", "error_type": "RuntimeError"}],
            "resume_checkpoint": "checkpoint",
        },
    )]
    assert harness.rebound is not None
    messages = harness.rebound["messages"]
    pending_results = [
        message for message in messages
        if message.role == "tool" and message.tool_call_id == "pending-tool"
    ]
    assert len(pending_results) == 1
    assert "outcome is unknown" in pending_results[0].content
    assert messages[-1].role == "system"
    assert "Continue from the current persisted state" in messages[-1].content
    assert state.total_tokens_in == 7
    assert state.total_tokens_out == 3


def test_agent_handoff_stops_when_persisted_writes_cannot_be_confirmed():
    handoff = LLMFailoverRequired(
        failed_service_id="main",
        next_service_id="fallback-1",
        next_attempt=1,
        cause=RuntimeError("429"),
        failures=[{"service_id": "main", "error_type": "RuntimeError"}],
    )
    state = SimpleNamespace(
        _budget_precheck_done=True,
        total_tokens_in=0,
        total_tokens_out=0,
        total_cache_read=0,
        total_cache_write=0,
        user_id="alice",
        conversation_id="conv-1",
        ctx={
            "active_agent_name": "assistant",
            "_context_rebuild_args": {"flowfile": "flowfile"},
        },
        _llm_call=lambda _messages: (_ for _ in ()).throw(handoff),
        _call_context=user_message(),
        emitter=SimpleNamespace(
            check_interrupt=lambda: False,
            check_cancelled=lambda: None,
        ),
    )
    harness = TurnHarness()

    with patch(
        "core.conversation_writer.ConversationWriter.for_conversation",
        return_value=SimpleNamespace(flush=lambda timeout: False),
    ):
        with pytest.raises(RuntimeError, match="writes could not be confirmed"):
            harness._alc_llm_turn(state)

    assert harness.prepare_calls == []
    assert harness.rebound is None
