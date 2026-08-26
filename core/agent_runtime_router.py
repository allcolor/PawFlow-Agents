"""Incremental runtime router for workflow agents.

Legacy runtimes intentionally do not register adapters yet. They keep using
their characterized AgentLoop and external-runtime branches until separate
parity gates allow moving them behind this router.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from core.agent_feature_flags import WORKFLOW_AGENT_RUNTIME_KIND
from core.workflow_agent_contracts import PreparedAgentTurn


@dataclass(frozen=True)
class AgentRunKey:
    conversation_id: str
    agent_name: str


@runtime_checkable
class AgentRuntimeAdapter(Protocol):
    runtime_kind: str

    def submit(self, request: PreparedAgentTurn):
        ...

    def cancel(self, key: AgentRunKey, reason: str, force: bool) -> bool:
        ...


@runtime_checkable
class RecoverableAgentRuntimeAdapter(AgentRuntimeAdapter, Protocol):
    def recover(self, run_id: str):
        ...


@dataclass(frozen=True)
class ResolvedAgentRuntime:
    roster_conversation_id: str
    canonical_agent_name: str
    runtime_kind: str
    config: dict
    adapter: AgentRuntimeAdapter | None = None


class AgentRuntimeRoutingError(RuntimeError):
    def __init__(self, message: str, *, code: str):
        super().__init__(message)
        self.code = code


class AgentRuntimeRouter:
    """Route only workflow for now; legacy kinds deliberately fall through."""

    _instance: AgentRuntimeRouter | None = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._adapters: dict[str, AgentRuntimeAdapter] = {}
        self._adapters_lock = threading.RLock()

    @classmethod
    def instance(cls) -> AgentRuntimeRouter:
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def register(self, adapter: AgentRuntimeAdapter) -> None:
        kind = str(getattr(adapter, "runtime_kind", "") or "").strip()
        if kind != WORKFLOW_AGENT_RUNTIME_KIND:
            raise ValueError(
                "only the workflow adapter may register before legacy parity gates")
        with self._adapters_lock:
            self._adapters[kind] = adapter

    def unregister(self, runtime_kind: str) -> None:
        with self._adapters_lock:
            self._adapters.pop(str(runtime_kind or "").strip(), None)

    def resolve(self, conversation_id: str, agent_name: str) -> ResolvedAgentRuntime:
        from core.conv_agent_config import resolve_agent_config_entry

        roster_id, canonical, config = resolve_agent_config_entry(
            conversation_id, agent_name)
        kind = str(config.get("runtime_kind") or "llm")
        if kind != WORKFLOW_AGENT_RUNTIME_KIND:
            return ResolvedAgentRuntime(roster_id, canonical, kind, config)
        with self._adapters_lock:
            adapter = self._adapters.get(kind)
        if adapter is None:
            from core.workflow_agent_runtime import WorkflowAgentRuntime
            adapter = WorkflowAgentRuntime.instance()
            self.register(adapter)
        return ResolvedAgentRuntime(roster_id, canonical, kind, config, adapter)

    def dispatch(self, request: PreparedAgentTurn):
        resolved = self.resolve(request.conversation_id, request.agent_name)
        if resolved.runtime_kind != WORKFLOW_AGENT_RUNTIME_KIND:
            raise AgentRuntimeRoutingError(
                "Legacy runtime remains on its characterized direct path",
                code="legacy_runtime_direct",
            )
        return resolved.adapter.submit(request)

    def cancel(self, key: AgentRunKey, reason: str, force: bool) -> bool:
        resolved = self.resolve(key.conversation_id, key.agent_name)
        if resolved.runtime_kind != WORKFLOW_AGENT_RUNTIME_KIND:
            return False
        return bool(resolved.adapter.cancel(
            AgentRunKey(key.conversation_id, resolved.canonical_agent_name),
            reason, force))


__all__ = [
    "AgentRunKey",
    "AgentRuntimeAdapter",
    "AgentRuntimeRouter",
    "AgentRuntimeRoutingError",
    "RecoverableAgentRuntimeAdapter",
    "ResolvedAgentRuntime",
]
