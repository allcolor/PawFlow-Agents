"""Ordered, fault-tolerant LLM controller service.

The composite starts each agent turn with its main connection and advances
through the configured fallbacks only after the active connection fails. Once a
turn selects a fallback, every later LLM call in that turn stays on it unless it
also fails. Agent turns use a typed handoff signal so the AgentLoop can rebuild
the canonical persisted context before the next connection continues. A new
turn retries the main connection.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Dict, List, Optional, Tuple

from core import ServiceError, ServiceFactory
from core.base_service import BaseService


logger = logging.getLogger(__name__)


def _service_ids(value: Any) -> List[str]:
    """Return an ordered list of non-empty service IDs."""
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ServiceError(
                "fallback_llm_services must be a JSON array of service IDs"
            ) from exc
    if not isinstance(value, list):
        raise ServiceError(
            "fallback_llm_services must be a JSON array of service IDs")
    result = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ServiceError(
                "fallback_llm_services entries must be non-empty service IDs")
        result.append(item.strip())
    return result


class LLMFailoverRequired(Exception):
    """Ask the AgentLoop to cold-start the next configured LLM connection."""

    def __init__(
        self,
        *,
        failed_service_id: str,
        next_service_id: str,
        next_attempt: int,
        cause: Exception,
        failures: Optional[List[Dict[str, str]]] = None,
    ):
        super().__init__(
            f"LLM handoff required: {failed_service_id} -> {next_service_id}")
        self.failed_service_id = failed_service_id
        self.next_service_id = next_service_id
        self.next_attempt = int(next_attempt)
        self.cause = cause
        self.failures = list(failures or [])


class LLMFailoverExhausted(ServiceError):
    """Raised after every configured LLM connection has failed."""

    def __init__(self, failures: List[Dict[str, str]]):
        self.failures = list(failures)
        count = len(self.failures)
        noun = "service" if count == 1 else "services"
        super().__init__(
            f"All {count} LLM {noun} failed. No fallback remains available.")


class FailoverLLMClient:
    """LLMClient-compatible proxy for one ordered, sticky failover turn."""

    def __init__(self, service: "LLMFailoverService", attempt: int = 0):
        self._service = service
        self._attempt = int(attempt)
        self._registry = None
        self._llm_resolver = None
        self._tool_registry = None
        self._clients: Dict[int, Any] = {}
        self._services: Dict[int, Any] = {}
        self._failures: List[Dict[str, str]] = []
        self._agent_ctx = None
        self._aborted = threading.Event()
        self._user_id = ""
        self._conversation_id = ""
        self._agent_name = ""
        self._event_cid = ""
        self._agent_service = str(
            service.config.get("_service_id", "") or "")

    @property
    def candidate_ids(self) -> List[str]:
        return self._service.candidate_service_ids

    @property
    def active_service_id(self) -> str:
        candidates = self.candidate_ids
        if not candidates:
            return ""
        if self._attempt < 0 or self._attempt >= len(candidates):
            return ""
        return candidates[self._attempt]

    def select_attempt(
        self,
        attempt: int,
        failures: Optional[List[Dict[str, str]]] = None,
    ) -> None:
        attempt = int(attempt)
        if attempt < 0 or attempt >= len(self.candidate_ids):
            raise ServiceError(
                f"Invalid LLM failover attempt {attempt}; "
                f"configured candidates: {len(self.candidate_ids)}")
        self._attempt = attempt
        self._failures = list(failures or [])

    def set_tool_registry(self, registry) -> None:
        self._tool_registry = registry
        for client in self._clients.values():
            if hasattr(client, "set_tool_registry"):
                client.set_tool_registry(registry)

    def set_llm_resolver(self, resolver) -> None:
        self._llm_resolver = resolver

    def clone_for_call(self) -> "FailoverLLMClient":
        clone = self.__class__(self._service, self._attempt)
        clone._registry = self._registry
        clone._llm_resolver = self._llm_resolver
        clone._tool_registry = self._tool_registry
        clone._failures = list(self._failures)
        for name in (
            "_user_id",
            "_conversation_id",
            "_agent_name",
            "_event_cid",
            "_agent_service",
        ):
            setattr(clone, name, getattr(self, name, ""))
        return clone

    def _apply_identity(self, kwargs: Dict[str, Any]) -> None:
        mappings = (
            ("call_user_id", "_user_id"),
            ("call_conversation_id", "_conversation_id"),
            ("call_agent_name", "_agent_name"),
            ("call_event_cid", "_event_cid"),
        )
        for source, target in mappings:
            value = kwargs.get(source)
            if value:
                setattr(self, target, value)

    @staticmethod
    def _failure(service_id: str, exc: Exception) -> Dict[str, str]:
        return {
            "service_id": service_id,
            "error_type": type(exc).__name__,
        }

    @staticmethod
    def _is_control_flow(exc: Exception) -> bool:
        from core._llm_types import (
            CCCompactDetected,
            ColdStartRequired,
            DeltaContextRequired,
        )
        from tasks.ai.agent_exceptions import AgentCancelled

        return isinstance(
            exc,
            (AgentCancelled, CCCompactDetected, ColdStartRequired,
             DeltaContextRequired),
        )

    def _resolve_connection(self, service_id: str) -> Tuple[Any, Any]:
        if self._llm_resolver is not None:
            client, service = self._llm_resolver(
                service_id, self._user_id)
            if client is None or service is None:
                raise ServiceError(
                    f"LLM service '{service_id}' could not be resolved")
            if getattr(service, "TYPE", "") != "llmConnection":
                raise ServiceError(
                    "LLM failover references must target llmConnection; "
                    f"'{service_id}' is "
                    f"{getattr(service, 'TYPE', 'unknown')}")
        else:
            from core.service_registry import ServiceRegistry

            registry = self._registry or ServiceRegistry.get_instance()
            definition = registry.resolve_definition(
                service_id,
                user_id=self._user_id,
                conv_id=self._conversation_id,
            )
            if definition is None or not definition.enabled:
                raise ServiceError(
                    f"LLM service '{service_id}' is not available")
            if definition.service_type != "llmConnection":
                raise ServiceError(
                    "LLM failover references must target llmConnection; "
                    f"'{service_id}' is {definition.service_type}")
            service = registry.resolve(
                service_id,
                user_id=self._user_id,
                conv_id=self._conversation_id,
            )
            if service is None or not hasattr(service, "get_client"):
                raise ServiceError(
                    f"LLM service '{service_id}' could not be resolved")

            pool_index = -1
            if (
                self._conversation_id
                and hasattr(service, "get_pool_size")
                and service.get_pool_size() > 0
            ):
                try:
                    from core.conversation_store import ConversationStore

                    pool_index = int(
                        ConversationStore.instance().get_extra(
                            self._conversation_id,
                            f"llm_api_key_idx:{service_id}",
                        )
                        or -1
                    )
                except Exception:
                    pool_index = -1
            client = service.get_client(pool_index=pool_index)

        client._agent_service = service_id
        client._user_id = self._user_id
        client._conversation_id = self._conversation_id
        client._agent_name = self._agent_name
        client._event_cid = self._event_cid
        client._agent_ctx = self._agent_ctx
        client._pawflow_context_is_delta = bool(
            self.__dict__.get("_pawflow_context_is_delta", False))
        client._max_context_size = int(
            self.__dict__.get("_max_context_size", 0) or 0)
        if self._tool_registry is not None and hasattr(
            client, "set_tool_registry"
        ):
            client.set_tool_registry(self._tool_registry)
        return client, service

    def _client_for_attempt(self, attempt: int):
        if attempt not in self._clients:
            service_id = self.candidate_ids[attempt]
            client, service = self._resolve_connection(service_id)
            self._clients[attempt] = client
            self._services[attempt] = service
        return self._clients[attempt]

    def _current_client(self):
        failures = list(self._failures)
        while self._attempt < len(self.candidate_ids):
            try:
                return self._client_for_attempt(self._attempt)
            except Exception as exc:
                if self._is_control_flow(exc):
                    raise
                service_id = self.active_service_id
                failure = self._failure(service_id, exc)
                failures.append(failure)
                self._failures = list(failures)
                logger.warning(
                    "LLM failover could not resolve candidate '%s': %s",
                    service_id,
                    exc,
                )
                self._attempt += 1
        raise LLMFailoverExhausted(failures)

    @property
    def provider(self) -> str:
        return getattr(self._current_client(), "provider", "") or ""

    @property
    def default_model(self) -> str:
        return getattr(self._current_client(), "default_model", "") or ""

    @property
    def base_url(self) -> str:
        return getattr(self._current_client(), "base_url", "") or ""

    @property
    def supports_vision(self) -> bool:
        return bool(
            getattr(self._current_client(), "supports_vision", False))

    def get_cost_config(self) -> Dict[str, Any]:
        self._current_client()
        service = self._services.get(self._attempt)
        return dict(getattr(service, "config", {}) or {})

    def __getattr__(self, name: str):
        if name.startswith("__"):
            raise AttributeError(name)
        return getattr(self._current_client(), name)

    def _run(self, method: str, messages, args, kwargs):
        self._apply_identity(kwargs)
        failures = list(self._failures)

        while self._attempt < len(self.candidate_ids):
            if self._aborted.is_set():
                from tasks.ai.agent_exceptions import AgentCancelled

                raise AgentCancelled()

            service_id = self.active_service_id
            try:
                client = self._client_for_attempt(self._attempt)
                return getattr(client, method)(messages, *args, **kwargs)
            except Exception as exc:
                if self._is_control_flow(exc):
                    raise
                failure = self._failure(service_id, exc)
                failures.append(failure)
                self._failures = list(failures)
                logger.warning(
                    "LLM candidate '%s' failed (%s); %s",
                    service_id,
                    type(exc).__name__,
                    "preparing fallback"
                    if self._attempt + 1 < len(self.candidate_ids)
                    else "no fallback remains",
                    exc_info=True,
                )
                next_attempt = self._attempt + 1
                if next_attempt >= len(self.candidate_ids):
                    raise LLMFailoverExhausted(failures) from exc

                if self._agent_ctx is not None:
                    try:
                        client.abort()
                    except Exception:
                        logger.debug(
                            "Failed to stop abandoned LLM candidate '%s'",
                            service_id,
                            exc_info=True,
                        )
                    raise LLMFailoverRequired(
                        failed_service_id=service_id,
                        next_service_id=self.candidate_ids[next_attempt],
                        next_attempt=next_attempt,
                        cause=exc,
                        failures=failures,
                    ) from exc

                self._attempt = next_attempt

        raise LLMFailoverExhausted(failures)

    def complete(self, messages, *args, **kwargs):
        return self._run("complete", messages, args, kwargs)

    def complete_stream(self, messages, *args, **kwargs):
        return self._run("complete_stream", messages, args, kwargs)

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


class LLMFailoverService(BaseService):
    """Controller service providing ordered LLM continuation on failure."""

    TYPE = "llmFailover"
    VERSION = "1.0.0"
    NAME = "Fault-Tolerant LLM Service"
    CATEGORY = "ai"
    DESCRIPTION = (
        "Tries a main LLM connection first, then ordered fallback connections "
        "while preserving AgentLoop work through cold-start handoff"
    )

    @property
    def main_service_id(self) -> str:
        return str(
            self.config.get("main_llm_service", "") or "").strip()

    @property
    def fallback_service_ids(self) -> List[str]:
        return _service_ids(
            self.config.get("fallback_llm_services", []))

    @property
    def candidate_service_ids(self) -> List[str]:
        main = self.main_service_id
        return ([main] if main else []) + self.fallback_service_ids

    def _create_connection(self):
        main = self.main_service_id
        fallbacks = self.fallback_service_ids
        own_id = str(self.config.get("_service_id", "") or "").strip()
        if not main:
            raise ServiceError("main_llm_service is required")
        if not fallbacks:
            raise ServiceError(
                "fallback_llm_services requires at least one service")
        candidates = [main, *fallbacks]
        if len(set(candidates)) != len(candidates):
            raise ServiceError(
                "LLM failover service references must be unique")
        if own_id and own_id in candidates:
            raise ServiceError(
                "An LLM failover service cannot reference itself")
        return {"ready": True}

    def _close_connection(self):
        pass

    def get_client(self, pool_index: int = -1) -> FailoverLLMClient:
        return FailoverLLMClient(self)

    def complete(self, messages, **kwargs):
        self.ensure_connected()
        return self.get_client().complete(messages, **kwargs)

    def complete_stream(self, messages, **kwargs):
        self.ensure_connected()
        return self.get_client().complete_stream(messages, **kwargs)

    def try_acquire(self) -> bool:
        return True

    def release(self):
        return None

    def has_capacity(self) -> bool:
        return True

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "main_llm_service": {
                "type": "service_ref",
                "service_type": "llmConnection",
                "required": True,
                "default": "",
                "description": (
                    "Primary LLM connection tried first at the start of each "
                    "agent turn or standalone service call"
                ),
            },
            "fallback_llm_services": {
                "type": "json",
                "required": True,
                "default": [],
                "description": (
                    "Ordered JSON array of fallback llmConnection service IDs"
                ),
            },
        }


ServiceFactory.register(LLMFailoverService)
