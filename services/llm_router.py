"""Turn-affine routing across direct LLM connection services."""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from typing import Any, Dict, Iterable, Optional

from core import ServiceError, ServiceFactory
from core.base_service import BaseService
from core.llm_failure_classifier import classify_cli_error, is_control_flow
from core.llm_routing_policy import select_candidates
from core.llm_routing_store import LLMRoutingStore
from core.llm_routing_types import (
    AffinityKey,
    AffinityRecord,
    CandidateDefinition,
    CandidateKey,
    ResolvedCandidate,
    ResolvedServiceRef,
    RouteIdentity,
    RoutePlan,
    STRATEGIES,
)
from core.service_definition_revision import compute_service_definition_revision


logger = logging.getLogger(__name__)


def _candidate_definitions(value: Any) -> list[CandidateDefinition]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ServiceError("candidates must be a JSON array") from exc
    if not isinstance(value, list):
        raise ServiceError("candidates must be a JSON array")
    result = []
    for position, item in enumerate(value):
        if not isinstance(item, dict):
            raise ServiceError("candidate entries must be objects")
        try:
            result.append(CandidateDefinition(
                service_id=str(item.get("service_id", "") or "").strip(),
                priority=int(item.get("priority", (position + 1) * 10)),
                weight=float(item.get("weight", 1.0)),
                enabled=bool(item.get("enabled", True)),
            ))
        except (TypeError, ValueError) as exc:
            raise ServiceError(f"Invalid router candidate at index {position}") from exc
    ids = [item.service_id for item in result]
    if len(ids) != len(set(ids)):
        raise ServiceError("LLM router candidate references must be unique")
    return result


class LLMRouteHandoffRequired(Exception):
    """Ask AgentLoop to rebuild context on the next snapshotted child."""

    def __init__(self, *, plan: RoutePlan, failed_service_id: str,
                 next_attempt: int, cause: Exception,
                 failures: Optional[list[dict]] = None):
        self.plan = plan
        self.failed_service_id = failed_service_id
        self.next_attempt = int(next_attempt)
        self.next_service_id = plan.ordered_candidates[next_attempt].service_id
        self.cause = cause
        self.failures = list(failures or [])
        super().__init__(
            f"LLM route handoff required: {failed_service_id} -> "
            f"{self.next_service_id}")


class LLMRouteExhausted(ServiceError):
    """Raised when an immutable route plan has no remaining candidate."""

    def __init__(self, failures: Iterable[dict]):
        self.failures = list(failures)
        super().__init__(
            f"All {len(self.failures)} planned LLM route attempts failed. "
            "No candidate remains available.")


class RouterLLMClient:
    """LLMClient-compatible proxy bound to one immutable logical-turn plan."""

    def __init__(self, service: "LLMRouterService"):
        self._service = service
        self._plan: Optional[RoutePlan] = None
        self._attempt = 0
        self._failures: list[dict] = []
        self._registry = None
        self._llm_resolver = None
        self._tool_registry = None
        self._clients: Dict[int, Any] = {}
        self._services: Dict[int, Any] = {}
        self._aborted = threading.Event()
        self._agent_ctx = None
        self._user_id = ""
        self._conversation_id = ""
        self._agent_name = ""
        self._event_cid = ""
        self._agent_service = str(service.config.get("_service_id", "") or "")
        self._finalized = False

    @property
    def route_plan(self) -> Optional[RoutePlan]:
        return self._plan

    @property
    def active_service_ref(self) -> Optional[ResolvedServiceRef]:
        if self._plan and 0 <= self._attempt < len(self._plan.ordered_candidates):
            return self._plan.ordered_candidates[self._attempt]
        return None

    @property
    def active_service_id(self) -> str:
        ref = self.active_service_ref
        return ref.service_id if ref else ""

    @property
    def route_attempt_id(self) -> str:
        if not self._plan:
            return ""
        return str(uuid.uuid5(uuid.UUID(self._plan.plan_id), str(self._attempt)))

    @property
    def route_attempt_index(self) -> int:
        return self._attempt if self._plan else -1

    def select_route(self, plan: RoutePlan, attempt: int,
                     failures: Optional[list[dict]] = None) -> None:
        if not isinstance(plan, RoutePlan):
            raise ServiceError("LLM route rebuild requires an immutable RoutePlan")
        attempt = int(attempt)
        if attempt < 0 or attempt >= len(plan.ordered_candidates):
            raise ServiceError(f"Invalid LLM route attempt {attempt}")
        self._plan = plan
        self._attempt = attempt
        self._failures = list(failures or [])

    def set_tool_registry(self, registry) -> None:
        self._tool_registry = registry
        for client in self._clients.values():
            if hasattr(client, "set_tool_registry"):
                client.set_tool_registry(registry)

    def set_llm_resolver(self, resolver) -> None:
        self._llm_resolver = resolver

    def clone_for_call(self) -> "RouterLLMClient":
        clone = self.__class__(self._service)
        clone._plan = self._plan
        clone._attempt = self._attempt
        clone._failures = list(self._failures)
        clone._registry = self._registry
        clone._llm_resolver = self._llm_resolver
        clone._tool_registry = self._tool_registry
        for name in ("_user_id", "_conversation_id", "_agent_name",
                     "_event_cid", "_agent_service"):
            setattr(clone, name, getattr(self, name, ""))
        return clone

    def _apply_identity(self, kwargs: Dict[str, Any]) -> None:
        for source, target in (
            ("call_user_id", "_user_id"),
            ("call_conversation_id", "_conversation_id"),
            ("call_agent_name", "_agent_name"),
            ("call_event_cid", "_event_cid"),
        ):
            if kwargs.get(source):
                setattr(self, target, kwargs[source])

    @property
    def _router_key(self) -> tuple[str, str, str]:
        return (
            str(self._service.config.get("_scope", "global") or "global"),
            str(self._service.config.get("_scope_id", "__global__") or "__global__"),
            str(self._service.config.get("_service_id", "") or ""),
        )

    def _identity(self) -> RouteIdentity:
        return RouteIdentity(
            user_id=self._user_id or "system",
            conversation_id=self._conversation_id or str(uuid.uuid4()),
            agent_name=self._agent_name or "direct",
            turn_id=self._event_cid or str(uuid.uuid4()),
            event_cid=self._event_cid,
        )

    def _resolved_candidate(self, definition: CandidateDefinition,
                            position: int) -> ResolvedCandidate:
        from core.service_registry import ServiceRegistry

        registry = self._registry or ServiceRegistry.get_instance()
        sdef = registry.resolve_definition(
            definition.service_id, user_id=self._user_id,
            conv_id=self._conversation_id)
        if sdef is None or not sdef.enabled:
            raise ServiceError(
                f"LLM service '{definition.service_id}' is not available")
        if sdef.service_type != "llmConnection":
            raise ServiceError(
                "LLM router references must target llmConnection; "
                f"'{definition.service_id}' is {sdef.service_type}")
        ref = ResolvedServiceRef(
            sdef.scope, sdef.scope_id, sdef.service_id,
            compute_service_definition_revision(sdef))
        return ResolvedCandidate(
            definition, ref, str((sdef.config or {}).get("model", "") or ""),
            position)

    def _ensure_plan(self) -> RoutePlan:
        if self._plan is not None:
            return self._plan
        resolved = []
        failures = []
        for position, definition in enumerate(self._service.candidates):
            try:
                resolved.append(self._resolved_candidate(definition, position))
            except Exception as exc:
                failures.append(self._failure(definition.service_id, exc))
        if not resolved:
            raise LLMRouteExhausted(failures)
        store = self._service.routing_store
        health = {}
        for candidate in resolved:
            key = self._candidate_key(candidate.service, candidate.model)
            health[candidate.service.key] = store.get_health(key)
        identity = self._identity()
        affinity_key = AffinityKey(*self._router_key, identity.user_id,
                                   identity.conversation_id, identity.agent_name)
        affinity = store.get_affinity(affinity_key)
        counter = (store.next_counter(self._router_key)
                   if self._service.strategy in {
                       "round_robin", "sticky_round_robin"} else 0)
        last_selected = (store.last_selected_map(self._router_key)
                         if self._service.strategy == "least_recently_used"
                         else {})
        selected = select_candidates(
            resolved, strategy=self._service.strategy, health=health,
            counter=counter, affinity=affinity,
            sticky_successful_turns=self._service.sticky_successful_turns,
            last_selected=last_selected, now=time.time())
        if not selected.ordered:
            raise LLMRouteExhausted(failures + [
                {"service_id": item.child.service_id,
                 "category": item.reason_code}
                for item in selected.excluded])
        router_ref = self._service.resolved_ref()
        self._plan = RoutePlan.create(
            router=router_ref, strategy=self._service.strategy,
            identity=identity,
            ordered_candidates=tuple(item.service for item in selected.ordered),
            excluded=selected.excluded)
        self._event("llm.route.selected", child=self.active_service_ref,
                    outcome="selected", reason_code=selected.reason_code)
        return self._plan

    def _candidate_key(self, ref: ResolvedServiceRef,
                       model: str = "") -> CandidateKey:
        return CandidateKey(*self._router_key, *ref.key, str(model or ""))

    def _event(self, event_type: str, *, child=None, outcome="",
               reason_code="", details=None) -> None:
        try:
            identity = self._plan.identity if self._plan else self._identity()
            self._service.routing_store.append_event(
                event_type=event_type, router_key=self._router_key,
                plan_id=self._plan.plan_id if self._plan else "",
                turn_id=identity.turn_id, user_id=identity.user_id,
                conversation_id=identity.conversation_id,
                agent_name=identity.agent_name, child=child,
                attempt_index=self._attempt, outcome=outcome,
                reason_code=reason_code, details=details or {})
        except Exception:
            logger.warning("Failed to persist safe LLM route event", exc_info=True)

    @staticmethod
    def _failure(service_id: str, exc: Exception) -> dict:
        typed = classify_cli_error("", exc)
        return {
            "service_id": service_id,
            "category": typed.category,
            "origin": typed.origin,
            "provider_status": typed.provider_status,
        }

    def _resolve_connection(self, ref: ResolvedServiceRef):
        from core.service_registry import ServiceRegistry

        registry = self._registry or ServiceRegistry.get_instance()
        definition = registry.get_definition(ref.scope, ref.scope_id, ref.service_id)
        if definition is None or not definition.enabled:
            raise ServiceError(f"Planned LLM service '{ref.service_id}' is unavailable")
        if compute_service_definition_revision(definition) != ref.definition_revision:
            raise ServiceError(
                f"Planned LLM service '{ref.service_id}' changed after route selection")
        service = registry.get_live_instance(ref.scope, ref.scope_id, ref.service_id)
        if service is None or not hasattr(service, "get_client"):
            raise ServiceError(f"Planned LLM service '{ref.service_id}' could not connect")
        pool_index = -1
        if self._conversation_id and hasattr(service, "get_pool_size") \
                and service.get_pool_size() > 0:
            try:
                from core.conversation_store import ConversationStore
                pool_index = int(ConversationStore.instance().get_extra(
                    self._conversation_id,
                    f"llm_api_key_idx:{ref.service_id}") or -1)
            except Exception:
                pool_index = -1
        client = service.get_client(pool_index=pool_index)
        client._agent_service = ref.service_id
        client._user_id = self._user_id
        client._conversation_id = self._conversation_id
        client._agent_name = self._agent_name
        client._event_cid = self._event_cid
        client._agent_ctx = self._agent_ctx
        client._pawflow_context_is_delta = bool(
            self.__dict__.get("_pawflow_context_is_delta", False))
        client._max_context_size = int(
            self.__dict__.get("_max_context_size", 0) or 0)
        if self._tool_registry is not None and hasattr(client, "set_tool_registry"):
            client.set_tool_registry(self._tool_registry)
        return client, service

    def _client_for_attempt(self, attempt: int):
        self._ensure_plan()
        if attempt not in self._clients:
            client, service = self._resolve_connection(
                self._plan.ordered_candidates[attempt])
            self._clients[attempt] = client
            self._services[attempt] = service
        return self._clients[attempt]

    def _current_client(self):
        self._ensure_plan()
        return self._client_for_attempt(self._attempt)

    @property
    def provider(self):
        return getattr(self._current_client(), "provider", "") or ""

    @property
    def default_model(self):
        return getattr(self._current_client(), "default_model", "") or ""

    @property
    def base_url(self):
        return getattr(self._current_client(), "base_url", "") or ""

    @property
    def supports_vision(self):
        return bool(getattr(self._current_client(), "supports_vision", False))

    def get_cost_config(self):
        self._current_client()
        return dict(getattr(self._services.get(self._attempt), "config", {}) or {})

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return getattr(self._current_client(), name)

    def _record_failure(self, ref, exc):
        typed = classify_cli_error(getattr(self._clients.get(self._attempt),
                                           "provider", "") or "", exc)
        failure = {
            "service_id": ref.service_id,
            "category": typed.category,
            "origin": typed.origin,
            "provider_status": typed.provider_status,
        }
        penalize = typed.origin not in {"local", "caller", "policy"} \
            and typed.category != "context_overflow"
        if penalize:
            model = getattr(self._clients.get(self._attempt), "default_model", "") or ""
            cooldown_until = (time.time() + typed.retry_after_seconds
                              if typed.retry_after_seconds else 0)
            self._service.routing_store.record_failure(
                self._candidate_key(ref, model), failure_kind=typed.category,
                provider_status=typed.provider_status,
                cooldown_until=cooldown_until,
                locked=typed.category in {
                    "auth_invalid", "billing_exhausted", "model_unavailable"})
        self._event("llm.route.handoff", child=ref, outcome="failed",
                    reason_code=typed.category)
        return failure, typed

    def _run(self, method, messages, args, kwargs):
        self._apply_identity(kwargs)
        plan = self._ensure_plan()
        failures = list(self._failures)
        while self._attempt < len(plan.ordered_candidates):
            if self._aborted.is_set():
                from tasks.ai.agent_exceptions import AgentCancelled
                raise AgentCancelled()
            ref = plan.ordered_candidates[self._attempt]
            try:
                client = self._client_for_attempt(self._attempt)
                response = getattr(client, method)(messages, *args, **kwargs)
                model = getattr(client, "default_model", "") or ""
                self._service.routing_store.record_success(
                    self._candidate_key(ref, model))
                self._event("llm.route.recovered", child=ref,
                            outcome="success", reason_code="call_succeeded")
                return response
            except Exception as exc:
                if is_control_flow(exc):
                    raise
                failure, typed = self._record_failure(ref, exc)
                failures.append(failure)
                self._failures = list(failures)
                if typed.origin in {"caller", "policy", "local"} \
                        or typed.category == "context_overflow":
                    raise exc
                next_attempt = self._attempt + 1
                if next_attempt >= len(plan.ordered_candidates):
                    self._event("llm.route.exhausted", child=ref,
                                outcome="exhausted", reason_code=typed.category)
                    raise LLMRouteExhausted(failures) from exc
                if self._agent_ctx is not None:
                    try:
                        self._clients[self._attempt].abort()
                    except Exception:
                        logger.debug("Failed to abort abandoned route child", exc_info=True)
                    raise LLMRouteHandoffRequired(
                        plan=plan, failed_service_id=ref.service_id,
                        next_attempt=next_attempt, cause=exc,
                        failures=failures) from exc
                self._attempt = next_attempt
        raise LLMRouteExhausted(failures)

    def complete(self, messages, *args, **kwargs):
        return self._run("complete", messages, args, kwargs)

    def complete_stream(self, messages, *args, **kwargs):
        return self._run("complete_stream", messages, args, kwargs)

    def finalize_route(self, success: bool) -> None:
        if self._finalized or not self._plan:
            return
        self._finalized = True
        if not success:
            return
        identity = self._plan.identity
        key = AffinityKey(*self._router_key, identity.user_id,
                          identity.conversation_id, identity.agent_name)
        previous = self._service.routing_store.get_affinity(key)
        child = self.active_service_ref
        count = (previous.successful_turns + 1
                 if previous and previous.child == child else 1)
        now = time.time()
        self._service.routing_store.set_affinity(AffinityRecord(
            key=key, child=child, successful_turns=count,
            last_selected_at=now,
            expires_at=now + self._service.affinity_ttl_seconds))

    def abort(self):
        self._aborted.set()
        client = self._clients.get(self._attempt)
        if client is not None and hasattr(client, "abort"):
            client.abort()

    def reset_abort(self):
        self._aborted.clear()
        for client in self._clients.values():
            if hasattr(client, "reset_abort"):
                client.reset_abort()


class LLMRouterService(BaseService):
    TYPE = "llmRouter"
    VERSION = "1.0.0"
    NAME = "Adaptive LLM Router"
    CATEGORY = "ai"
    DESCRIPTION = "Selects one direct LLM connection per turn with safe handoff."

    @property
    def candidates(self):
        return _candidate_definitions(self.config.get("candidates", []))

    @property
    def strategy(self):
        return str(self.config.get("strategy", "ordered") or "ordered")

    @property
    def sticky_successful_turns(self):
        return max(1, int(self.config.get("sticky_successful_turns", 1) or 1))

    @property
    def affinity_ttl_seconds(self):
        return max(60, int(self.config.get("affinity_ttl_seconds", 86400) or 86400))

    @property
    def router_key(self) -> tuple[str, str, str]:
        return (
            str(self.config.get("_scope", "global") or "global"),
            str(self.config.get("_scope_id", "__global__") or "__global__"),
            str(self.config.get("_service_id", "") or ""),
        )

    @property
    def routing_store(self):
        if not hasattr(self, "_routing_store"):
            self._routing_store = LLMRoutingStore()
        return self._routing_store

    @classmethod
    def validate_config(cls, config, *, service_id=""):
        candidates = _candidate_definitions((config or {}).get("candidates", []))
        if len([item for item in candidates if item.enabled]) < 2:
            raise ServiceError("LLM router requires at least two enabled candidates")
        strategy = str((config or {}).get("strategy", "ordered") or "ordered")
        if strategy not in STRATEGIES:
            raise ServiceError(f"Unknown LLM routing strategy '{strategy}'")
        if service_id and service_id in {item.service_id for item in candidates}:
            raise ServiceError("An LLM router cannot reference itself")
        return candidates

    def resolved_ref(self):
        from core._service_defs import ServiceDef
        from core.service_registry import ServiceRegistry

        scope = str(self.config.get("_scope", "global") or "global")
        scope_id = str(self.config.get("_scope_id", "__global__") or "__global__")
        service_id = str(self.config.get("_service_id", "") or "")
        definition = ServiceRegistry.get_instance().get_definition(
            scope, scope_id, service_id) if service_id else None
        if definition is None:
            definition = ServiceDef(
                service_id=service_id or "router", service_type=self.TYPE,
                scope=scope, scope_id=scope_id, config=dict(self.config),
                created_at=float(self.config.get("_created_at", 1) or 1))
        return ResolvedServiceRef(
            scope, scope_id, definition.service_id,
            compute_service_definition_revision(definition))

    def _create_connection(self):
        own_id = str(self.config.get("_service_id", "") or "")
        self.validate_config(self.config, service_id=own_id)
        self.routing_store
        return {"ready": True}

    def _close_connection(self):
        if hasattr(self, "_routing_store"):
            self._routing_store.close()
            del self._routing_store

    def get_client(self, pool_index: int = -1):
        return RouterLLMClient(self)

    def complete(self, messages, **kwargs):
        self.ensure_connected()
        return self.get_client().complete(messages, **kwargs)

    def complete_stream(self, messages, **kwargs):
        self.ensure_connected()
        return self.get_client().complete_stream(messages, **kwargs)

    def try_acquire(self):
        return True

    def release(self):
        return None

    def has_capacity(self):
        return True

    def get_parameter_schema(self):
        return {
            "candidates": {
                "type": "service_ref_list", "service_type": "llmConnection",
                "required": True, "default": [],
                "description": "Ordered direct LLM connection candidates",
            },
            "strategy": {
                "type": "select", "required": True, "default": "ordered",
                "options": sorted(STRATEGIES),
            },
            "sticky_successful_turns": {
                "type": "number", "required": False, "default": 1,
            },
            "affinity_ttl_seconds": {
                "type": "number", "required": False, "default": 86400,
            },
        }

    def get_service_actions(self):
        return [
            {"id": "llm_router_health", "label": "Health",
             "description": "Show safe health state for every router candidate"},
            {"id": "llm_router_explain", "label": "Explain last decision",
             "description": "Show the latest sanitized route selection"},
            {"id": "llm_router_health_reset", "label": "Reset health",
             "description": "Clear persisted health for one or all candidates"},
        ]

    def health_snapshot(self, *, user_id: str, conversation_id: str = ""):
        client = self.get_client()
        client._user_id = user_id or "system"
        client._conversation_id = conversation_id
        client._agent_name = "operator"
        rows = []
        for position, definition in enumerate(self.candidates):
            try:
                candidate = client._resolved_candidate(definition, position)
                record = self.routing_store.get_health(client._candidate_key(
                    candidate.service, candidate.model))
                rows.append({
                    "service_id": candidate.service.service_id,
                    "scope": candidate.service.scope,
                    "scope_id": candidate.service.scope_id,
                    "model": candidate.model,
                    "state": record.state,
                    "consecutive_failures": record.consecutive_failures,
                    "last_success_at": record.last_success_at,
                    "last_failure_at": record.last_failure_at,
                    "cooldown_until": record.cooldown_until,
                    "last_failure_kind": record.last_failure_kind,
                    "last_status": record.last_status,
                })
            except Exception:
                rows.append({
                    "service_id": definition.service_id,
                    "state": "unavailable", "reason": "resolution_failed"})
        return rows

    def explain_last_decision(self):
        events = self.routing_store.list_events(
            router_key=self.router_key, limit=100)
        selected = next((event for event in events
                         if event["event_type"] == "llm.route.selected"), None)
        if selected is None:
            return {}
        plan_events = [event for event in events
                       if event["plan_id"] == selected["plan_id"]]
        return {
            "plan_id": selected["plan_id"],
            "strategy": self.strategy,
            "selected": selected["child_service_id"],
            "reason": selected["reason_code"],
            "events": plan_events,
        }

    def reset_health(self, *, user_id: str, conversation_id: str = "",
                     service_id: str = "") -> int:
        client = self.get_client()
        client._user_id = user_id or "system"
        client._conversation_id = conversation_id
        client._agent_name = "operator"
        cleared = 0
        for position, definition in enumerate(self.candidates):
            if service_id and definition.service_id != service_id:
                continue
            candidate = client._resolved_candidate(definition, position)
            self.routing_store.clear_health(client._candidate_key(
                candidate.service, candidate.model))
            cleared += 1
        return cleared


ServiceFactory.register(LLMRouterService)
