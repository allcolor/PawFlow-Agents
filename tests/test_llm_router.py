"""Integration tests for immutable turn-level LLM routing."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core import ServiceError, ServiceFactory
from core._service_defs import ServiceDef
from core.llm_client import LLMMessage, LLMResponse, LLMToolCall
from core.llm_routing_store import LLMRoutingStore
from services.llm_router import (
    LLMRouteExhausted,
    LLMRouteHandoffRequired,
    LLMRouterService,
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
        return self._answer(messages, kwargs)

    def abort(self):
        self.aborted = True

    def reset_abort(self):
        self.aborted = False


class FakeService:
    TYPE = "llmConnection"

    def __init__(self, client):
        self.client = client
        self.config = {
            "provider": client.provider,
            "model": client.default_model,
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
            service_id: FakeService(client)
            for service_id, client in clients.items()
        }
        self.definitions = {
            service_id: ServiceDef(
                service_id=service_id, service_type="llmConnection",
                scope="user", scope_id="alice", config=service.config,
                created_at=100 + index)
            for index, (service_id, service) in enumerate(self.services.items())
        }

    def resolve_definition(self, service_id, **kwargs):
        return self.definitions.get(service_id)

    def get_definition(self, scope, scope_id, service_id):
        definition = self.definitions.get(service_id)
        if definition and (definition.scope, definition.scope_id) == (scope, scope_id):
            return definition
        return None

    def get_live_instance(self, scope, scope_id, service_id):
        return self.services.get(service_id) if self.get_definition(
            scope, scope_id, service_id) else None


def response(text):
    return LLMResponse(
        content=text, model="test-model", tokens_in=1, tokens_out=1)


def user_message():
    return [LLMMessage(
        role="user", content="Continue", conversation_id="conv-1")]


def make_service(tmp_path, *, strategy="ordered"):
    service = LLMRouterService({
        "_service_id": "resilient",
        "_scope": "user",
        "_scope_id": "alice",
        "strategy": strategy,
        "candidates": [
            {"service_id": "main", "priority": 10, "enabled": True},
            {"service_id": "fallback-1", "priority": 20, "enabled": True},
            {"service_id": "fallback-2", "priority": 30, "enabled": True},
        ],
    })
    service._routing_store = LLMRoutingStore(str(tmp_path / "routing.db"))
    service.connect()
    return service


def test_registration_and_save_validation(tmp_path):
    assert ServiceFactory.get("llmRouter") is LLMRouterService
    schema = make_service(tmp_path).get_parameter_schema()
    assert schema["candidates"]["type"] == "service_ref_list"
    assert schema["candidates"]["service_type"] == "llmConnection"
    with pytest.raises(ServiceError, match="at least two enabled"):
        LLMRouterService({
            "candidates": [
                {"service_id": "only", "enabled": True},
                {"service_id": "off", "enabled": False},
            ]
        }).connect()


def test_direct_call_falls_back_in_immutable_plan_order(tmp_path):
    main = FakeClient("main", [RuntimeError("down")])
    fallback = FakeClient("fallback-1", [response("continued")])
    registry = FakeRegistry({
        "main": main, "fallback-1": fallback,
        "fallback-2": FakeClient("fallback-2", [])})
    client = make_service(tmp_path).get_client()

    with patch("core.service_registry.ServiceRegistry.get_instance",
               return_value=registry):
        result = client.complete(
            user_message(), call_user_id="alice",
            call_conversation_id="conv-1", call_agent_name="assistant")

    assert result.content == "continued"
    assert [len(main.calls), len(fallback.calls)] == [1, 1]
    assert [ref.service_id for ref in client.route_plan.ordered_candidates] == [
        "main", "fallback-1", "fallback-2"]
    assert client.active_service_id == "fallback-1"


def test_agent_handoff_reuses_snapshot_after_scope_resolution_changes(tmp_path):
    registry = FakeRegistry({
        "main": FakeClient("main", [RuntimeError("down")]),
        "fallback-1": FakeClient("fallback-1", [response("continued")]),
        "fallback-2": FakeClient("fallback-2", []),
    })
    service = make_service(tmp_path)
    first = service.get_client()
    first._user_id = "alice"
    first._conversation_id = "conv-1"
    first._agent_name = "assistant"
    first._agent_ctx = {"active_agent_name": "assistant"}

    with patch("core.service_registry.ServiceRegistry.get_instance",
               return_value=registry):
        with pytest.raises(LLMRouteHandoffRequired) as raised:
            first.complete_stream(user_message())
        plan = raised.value.plan
        registry.definitions["fallback-1"] = ServiceDef(
            service_id="fallback-1", service_type="llmConnection",
            scope="conv", scope_id="conv-1",
            config=registry.services["fallback-1"].config, created_at=999)
        rebuilt = service.get_client()
        rebuilt.select_route(plan, raised.value.next_attempt,
                             raised.value.failures)
        rebuilt._user_id = "alice"
        rebuilt._conversation_id = "conv-1"
        rebuilt._agent_name = "assistant"
        with pytest.raises(LLMRouteExhausted):
            rebuilt.complete_stream(user_message())

    assert plan is rebuilt.route_plan
    assert plan.ordered_candidates[1].scope == "user"


def test_cancellation_does_not_handoff_or_change_health(tmp_path):
    from tasks.ai.agent_exceptions import AgentCancelled

    fallback = FakeClient("fallback-1", [response("wrong")])
    registry = FakeRegistry({
        "main": FakeClient("main", [AgentCancelled()]),
        "fallback-1": fallback,
        "fallback-2": FakeClient("fallback-2", []),
    })
    service = make_service(tmp_path)
    client = service.get_client()
    with patch("core.service_registry.ServiceRegistry.get_instance",
               return_value=registry):
        with pytest.raises(AgentCancelled):
            client.complete(user_message(), call_user_id="alice",
                            call_conversation_id="conv-1",
                            call_agent_name="assistant")
    assert not fallback.calls
    assert service.routing_store.list_events(plan_id=client.route_plan.plan_id)


def test_affinity_counts_only_terminal_success(tmp_path):
    registry = FakeRegistry({
        "main": FakeClient("main", [response("ok")]),
        "fallback-1": FakeClient("fallback-1", []),
        "fallback-2": FakeClient("fallback-2", []),
    })
    service = make_service(tmp_path, strategy="sticky_round_robin")
    client = service.get_client()
    with patch("core.service_registry.ServiceRegistry.get_instance",
               return_value=registry):
        client.complete(user_message(), call_user_id="alice",
                        call_conversation_id="conv-1",
                        call_agent_name="assistant")
    client.finalize_route(False)
    assert service.routing_store.get_affinity(
        client.route_plan.identity and __import__(
            "core.llm_routing_types", fromlist=["AffinityKey"]
        ).AffinityKey("user", "alice", "resilient", "alice", "conv-1",
                      "assistant")) is None


def test_health_explain_and_reset_actions_are_sanitized(tmp_path):
    registry = FakeRegistry({
        "main": FakeClient("main", [response("ok")]),
        "fallback-1": FakeClient("fallback-1", []),
        "fallback-2": FakeClient("fallback-2", []),
    })
    service = make_service(tmp_path)
    client = service.get_client()
    with patch("core.service_registry.ServiceRegistry.get_instance",
               return_value=registry):
        client.complete(user_message(), call_user_id="alice",
                        call_conversation_id="conv-1",
                        call_agent_name="assistant")
        health = service.health_snapshot(
            user_id="alice", conversation_id="conv-1")
        assert health[0]["state"] == "healthy"
        assert set(health[0]).isdisjoint({"error", "api_key", "authorization"})
        explanation = service.explain_last_decision()
        assert explanation["plan_id"] == client.route_plan.plan_id
        assert explanation["selected"] == "main"
        assert service.reset_health(
            user_id="alice", conversation_id="conv-1",
            service_id="main") == 1
        assert service.health_snapshot(
            user_id="alice", conversation_id="conv-1")[0]["state"] == "unknown"


def test_least_recently_used_prefers_unselected_candidates(tmp_path):
    registry = FakeRegistry({
        "main": FakeClient("main", [response("first")]),
        "fallback-1": FakeClient("fallback-1", [response("second")]),
        "fallback-2": FakeClient("fallback-2", [response("third")]),
    })
    service = make_service(tmp_path, strategy="least_recently_used")
    with patch("core.service_registry.ServiceRegistry.get_instance",
               return_value=registry):
        first = service.get_client()
        first.complete(user_message(), call_user_id="alice",
                       call_conversation_id="conv-1",
                       call_agent_name="assistant")
        assert first.active_service_id == "main"
        second = service.get_client()
        second.complete(user_message(), call_user_id="alice",
                        call_conversation_id="conv-1",
                        call_agent_name="assistant")
    assert second.active_service_id == "fallback-1"
    assert [ref.service_id for ref in second.route_plan.ordered_candidates] == [
        "fallback-1", "fallback-2", "main"]


def test_explain_last_decision_is_scoped_to_this_router(tmp_path):
    registry = FakeRegistry({
        "main": FakeClient("main", [response("ok")]),
        "fallback-1": FakeClient("fallback-1", []),
        "fallback-2": FakeClient("fallback-2", []),
    })
    service = make_service(tmp_path)
    client = service.get_client()
    with patch("core.service_registry.ServiceRegistry.get_instance",
               return_value=registry):
        client.complete(user_message(), call_user_id="alice",
                        call_conversation_id="conv-1",
                        call_agent_name="assistant")
    import uuid as _uuid
    service.routing_store.append_event(
        event_type="llm.route.selected",
        router_key=("user", "alice", "other-router"),
        plan_id=str(_uuid.uuid4()), outcome="selected",
        reason_code="ordered")
    explanation = service.explain_last_decision()
    assert explanation["plan_id"] == client.route_plan.plan_id
    assert explanation["selected"] == "main"
    assert all(event["router_service_id"] == "resilient"
               for event in explanation["events"])


class TurnHarness(_ALCLlmTurnMixin):
    def __init__(self):
        self.prepare_calls = []
        self.rebound = None

    def _prepare_agent_context(self, flowfile, **kwargs):
        self.prepare_calls.append((flowfile, kwargs))
        return {
            "client": SimpleNamespace(), "registry": object(), "tool_defs": [],
            "messages": [
                LLMMessage(role="user", content="original",
                           conversation_id="conv-1"),
                LLMMessage(role="assistant", content="done",
                           tool_calls=[LLMToolCall(
                               id="pending", name="edit", arguments={})],
                           conversation_id="conv-1"),
            ],
            "model": "", "active_llm_service": "resilient",
            "_context_rebuild_args": {"flowfile": flowfile},
        }

    def _alc_rebind_context(self, st, new_ctx):
        self.rebound = new_ctx


def test_agentloop_flushes_then_cold_rebuilds_with_same_plan(tmp_path):
    registry = FakeRegistry({
        "main": FakeClient("main", []),
        "fallback-1": FakeClient("fallback-1", []),
        "fallback-2": FakeClient("fallback-2", []),
    })
    service = make_service(tmp_path)
    client = service.get_client()
    client._user_id = "alice"
    client._conversation_id = "conv-1"
    client._agent_name = "assistant"
    with patch("core.service_registry.ServiceRegistry.get_instance",
               return_value=registry):
        plan = client._ensure_plan()
    handoff = LLMRouteHandoffRequired(
        plan=plan, failed_service_id="main", next_attempt=1,
        cause=RuntimeError("down"), failures=[{"service_id": "main"}])
    state = SimpleNamespace(
        _budget_precheck_done=True, total_tokens_in=7, total_tokens_out=3,
        total_cache_read=0, total_cache_write=0, user_id="alice",
        conversation_id="conv-1",
        ctx={"active_agent_name": "assistant",
             "_context_rebuild_args": {"flowfile": "flowfile"},
             "_consumed_cancel_checkpoint": "checkpoint"},
        _llm_call=lambda _: (_ for _ in ()).throw(handoff),
        _call_context=user_message(),
        emitter=SimpleNamespace(check_interrupt=lambda: False,
                                check_cancelled=lambda: None))
    harness = TurnHarness()
    with patch(
            "core.conversation_writer.ConversationWriter.for_conversation",
            return_value=SimpleNamespace(flush=lambda timeout: True)):
        assert harness._alc_llm_turn(state) is _ALC_CONTINUE
    assert harness.prepare_calls[0][1]["route_plan"] is plan
    assert harness.prepare_calls[0][1]["route_attempt"] == 1
    assert "outcome is unknown" in harness.rebound["messages"][2].content
