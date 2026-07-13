"""Tests for the multi-LLM aggregator controller service."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core import ServiceError, ServiceFactory
from core.llm_client import LLMMessage, LLMResponse, LLMToolCall
from core.tool_registry import ToolHandler, ToolRegistry
from services.llm_aggregator import LLMAggregatorService
from tasks.ai._alc_base import _svc_rates, _usage_cost_usd


class ReadHandler(ToolHandler):
    @property
    def name(self):
        return "read"

    @property
    def description(self):
        return "Read test data"

    @property
    def parameters_schema(self):
        return {"type": "object", "properties": {}}

    def execute(self, arguments):
        return "project facts"


class FakeClient:
    def __init__(self, responses, provider="openai"):
        self.responses = list(responses)
        self.provider = provider
        self.default_model = "test-model"
        self.base_url = "https://example.test"
        self.supports_vision = True
        self.calls = []
        self.aborted = False

    def clone_for_call(self):
        return self

    def complete_stream(self, messages, *args, **kwargs):
        self.calls.append({"messages": list(messages), "kwargs": kwargs,
                           "tools": args[3] if len(args) > 3 else kwargs.get("tools")})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        callback = kwargs.get("callback")
        if callback and response.content:
            callback(response.content)
        return response

    def complete(self, messages, *args, **kwargs):
        self.calls.append({"messages": list(messages), "kwargs": kwargs,
                           "tools": kwargs.get("tools")})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def abort(self):
        self.aborted = True

    def reset_abort(self):
        self.aborted = False


class FakeLLMService:
    TYPE = "llmConnection"

    def __init__(self, client, config=None):
        self.client = client
        self.config = config or {}

    def get_client(self, pool_index=-1):
        return self.client

    def get_pool_size(self):
        return 0


class FakeServiceRegistry:
    def __init__(self, clients, configs=None):
        configs = configs or {}
        self.services = {
            key: FakeLLMService(value, configs.get(key))
            for key, value in clients.items()
        }

    def resolve_definition(self, service_id, **kwargs):
        if service_id not in self.services:
            return None
        return SimpleNamespace(
            service_id=service_id, service_type="llmConnection", enabled=True)

    def resolve(self, service_id, **kwargs):
        return self.services.get(service_id)


def _response(content, *, tokens_in=1, tokens_out=1, tool_calls=None):
    return LLMResponse(
        content=content, model="test-model", tokens_in=tokens_in,
        tokens_out=tokens_out, tool_calls=tool_calls or [])


def _service(failure_policy="best_effort"):
    service = LLMAggregatorService({
        "_service_id": "council",
        "aggregator_llm_service": "final",
        "advisor_llm_services": ["advisor_a", "advisor_b"],
        "max_parallel_advisors": 2,
        "advisor_max_iterations": 3,
        "failure_policy": failure_policy,
    })
    service.connect()
    return service


def test_service_is_registered_and_validates_references():
    assert ServiceFactory.get("llmAggregator") is LLMAggregatorService
    schema = _service().get_parameter_schema()
    assert schema["aggregator_llm_service"]["service_type"] == "llmConnection"
    assert schema["advisor_llm_services"]["type"] == "json"
    assert schema["enforce_read_only"]["default"] is True

    with pytest.raises(ServiceError, match="cannot also be an advisor"):
        LLMAggregatorService({
            "aggregator_llm_service": "same",
            "advisor_llm_services": ["same"],
        }).connect()
    strict = LLMAggregatorService({
        "aggregator_llm_service": "final",
        "advisor_llm_services": ["advisor"],
        "enforce_read_only": True,
    })
    strict.connect()
    assert strict.enforce_read_only is True


def test_composite_cost_uses_final_rates_plus_advisor_delta():
    client = SimpleNamespace(get_cost_config=lambda: {
        "cost_per_1m_input": 2,
        "cost_per_1m_output": 4,
        "cost_per_1m_cache_read": 0.2,
        "cost_per_1m_cache_write": 2.5,
    })
    ctx = {"client": client, "_additional_usage_cost_usd": 0.25}

    assert _svc_rates(ctx) == (2.0, 4.0, 0.2, 2.5)
    assert _usage_cost_usd(
        ctx, 1_000_000, 500_000, 0, 0) == pytest.approx(4.25)


def test_advisors_use_tools_once_and_reports_are_cached_for_tool_loop():
    advisor_a = FakeClient([
        _response("", tool_calls=[
            LLMToolCall(id="read-1", name="read", arguments={})]),
        _response("Plan A", tokens_in=11, tokens_out=5),
    ])
    advisor_b = FakeClient([_response("Plan B", tokens_in=13, tokens_out=7)])
    final = FakeClient([
        _response("Need a final tool", tokens_in=20, tokens_out=4,
                  tool_calls=[LLMToolCall(id="final-1", name="read", arguments={})]),
        _response("Final answer", tokens_in=22, tokens_out=8),
    ], provider="codex-app-server")
    fake_registry = FakeServiceRegistry({
        "advisor_a": advisor_a, "advisor_b": advisor_b, "final": final},
        configs={
            "advisor_a": {
                "cost_per_1m_input": "2", "cost_per_1m_output": "4"},
            "advisor_b": {
                "cost_per_1m_input": "3", "cost_per_1m_output": "5"},
        })
    tool_registry = ToolRegistry()
    tool_registry.register(ReadHandler())
    client = _service().get_client()
    client.set_tool_registry(tool_registry)
    messages = [LLMMessage(
        role="user", content="Implement the feature",
        conversation_id="conv-1")]

    with patch("core.service_registry.ServiceRegistry.get_instance",
               return_value=fake_registry):
        assert client.provider == "codex-app-server"
        first = client.complete_stream(
            messages, tools=[], call_user_id="alice",
            call_conversation_id="conv-1", call_agent_name="assistant",
            call_is_initial_user_turn=True)
        second = client.complete_stream(
            messages, tools=[], call_user_id="alice",
            call_conversation_id="conv-1", call_agent_name="assistant",
            call_is_initial_user_turn=False)

    assert len(advisor_a.calls) == 2
    assert len(advisor_b.calls) == 1
    assert advisor_a.calls[0]["tools"]
    assert advisor_b.calls[0]["tools"]
    assert first.tokens_in == 20
    assert first.tokens_out == 4
    assert second.tokens_in == 22
    assert second.tokens_out == 8
    assert second.content == "Final answer"
    assert len(first.raw["_pawflow_aggregation"]["advisors"]) == 2
    assert first.raw["_pawflow_aggregation"][
        "advisor_cost_usd_delta"] == pytest.approx(0.000122)
    assert second.raw["_pawflow_aggregation"][
        "advisor_cost_usd_delta"] == 0

    # Reports are appended to the last user message (never a trailing
    # system message — providers may drop or last-wins those) and the
    # original message object stays untouched.
    report_message = final.calls[0]["messages"][-1]
    assert report_message.role == "user"
    assert report_message.content.startswith("Implement the feature")
    assert "Plan A" in report_message.content
    assert "Plan B" in report_message.content
    assert "untrusted analysis" in report_message.content
    assert messages[0].content == "Implement the feature"


def test_best_effort_passes_advisor_failure_to_final_llm():
    failed = FakeClient([RuntimeError("advisor offline")])
    healthy = FakeClient([_response("Healthy plan")])
    final = FakeClient([_response("Recovered")])
    registry = FakeServiceRegistry({
        "advisor_a": failed, "advisor_b": healthy, "final": final})
    client = _service("best_effort").get_client()
    client.set_tool_registry(ToolRegistry())

    with patch("core.service_registry.ServiceRegistry.get_instance",
               return_value=registry):
        response = client.complete_stream(
            [LLMMessage("user", "Work", conversation_id="conv-1")],
            call_user_id="alice", call_conversation_id="conv-1",
            call_is_initial_user_turn=True)

    assert response.content == "Recovered"
    assert "Advisor unavailable" in final.calls[0]["messages"][-1].content


def test_fail_fast_stops_before_final_llm():
    failed = FakeClient([RuntimeError("advisor offline")])
    healthy = FakeClient([_response("Healthy plan")])
    final = FakeClient([_response("must not run")])
    registry = FakeServiceRegistry({
        "advisor_a": failed, "advisor_b": healthy, "final": final})
    client = _service("fail_fast").get_client()
    client.set_tool_registry(ToolRegistry())

    with patch("core.service_registry.ServiceRegistry.get_instance",
               return_value=registry), pytest.raises(
                   ServiceError, match="Advisor execution failed"):
        client.complete_stream(
            [LLMMessage("user", "Work", conversation_id="conv-1")],
            call_user_id="alice", call_conversation_id="conv-1",
            call_is_initial_user_turn=True)

    assert not final.calls
