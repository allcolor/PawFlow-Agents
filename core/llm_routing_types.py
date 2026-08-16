"""Immutable domain types shared by LLM routing policy and persistence."""

from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Tuple


HEALTH_STATES = frozenset({
    "healthy", "degraded", "cooldown", "locked", "unknown"})
STRATEGIES = frozenset({
    "ordered", "round_robin", "sticky_round_robin", "least_recently_used"})


def new_event_id() -> str:
    return str(uuid.uuid4())


@dataclass(frozen=True)
class CandidateDefinition:
    service_id: str
    priority: int
    weight: float = 1.0
    enabled: bool = True

    def __post_init__(self):
        if not str(self.service_id or "").strip():
            raise ValueError("Candidate service_id is required")
        if not math.isfinite(float(self.weight)) or float(self.weight) <= 0:
            raise ValueError("Candidate weight must be finite and positive")


@dataclass(frozen=True)
class RouteIdentity:
    user_id: str
    conversation_id: str
    agent_name: str
    turn_id: str
    event_cid: str = ""

    def __post_init__(self):
        for field_name in ("user_id", "conversation_id", "agent_name", "turn_id"):
            if not str(getattr(self, field_name) or ""):
                raise ValueError(f"Route identity requires {field_name}")


@dataclass(frozen=True)
class ResolvedServiceRef:
    scope: str
    scope_id: str
    service_id: str
    definition_revision: str

    def __post_init__(self):
        if self.scope not in {"global", "user", "conv"}:
            raise ValueError("Resolved service scope is invalid")
        if not self.scope_id or not self.service_id or len(self.definition_revision) != 64:
            raise ValueError("Resolved service reference is incomplete")

    @property
    def key(self) -> tuple[str, str, str]:
        return self.scope, self.scope_id, self.service_id


@dataclass(frozen=True)
class ResolvedCandidate:
    definition: CandidateDefinition
    service: ResolvedServiceRef
    model: str = ""
    position: int = 0


@dataclass(frozen=True)
class CandidateExclusion:
    child: ResolvedServiceRef
    reason_code: str
    retry_at: float = 0


@dataclass(frozen=True)
class CandidateSummary:
    child: ResolvedServiceRef
    eligible: bool
    reason_code: str
    factors: tuple[tuple[str, float], ...] = ()


@dataclass(frozen=True)
class RoutePlan:
    plan_id: str
    created_at: float
    router: ResolvedServiceRef
    strategy: str
    identity: RouteIdentity
    ordered_candidates: Tuple[ResolvedServiceRef, ...]
    excluded: Tuple[CandidateExclusion, ...] = ()

    def __post_init__(self):
        if not self.plan_id or self.created_at <= 0:
            raise ValueError("Route plan requires UUID and timestamp")
        uuid.UUID(self.plan_id)
        if self.strategy not in STRATEGIES:
            raise ValueError(f"Unknown routing strategy '{self.strategy}'")
        if not self.ordered_candidates:
            raise ValueError("Route plan requires at least one candidate")

    @classmethod
    def create(cls, *, router: ResolvedServiceRef, strategy: str,
               identity: RouteIdentity,
               ordered_candidates: tuple[ResolvedServiceRef, ...],
               excluded: tuple[CandidateExclusion, ...] = (), now=None):
        return cls(new_event_id(), float(now if now is not None else time.time()),
                   router, strategy, identity, ordered_candidates, excluded)


@dataclass
class RouteAttempt:
    attempt_id: str
    plan_id: str
    attempt_index: int
    child: ResolvedServiceRef
    started_at: float
    completed_at: float = 0
    outcome: str = "running"
    failure_kind: str = ""


@dataclass(frozen=True)
class CandidateKey:
    router_scope: str
    router_scope_id: str
    router_service_id: str
    child_scope: str
    child_scope_id: str
    child_service_id: str
    model: str
    credential_id: str = ""

    def as_tuple(self) -> tuple[str, ...]:
        return (
            self.router_scope, self.router_scope_id, self.router_service_id,
            self.child_scope, self.child_scope_id, self.child_service_id,
            self.model, self.credential_id)


@dataclass
class HealthRecord:
    key: CandidateKey
    state: str = "unknown"
    consecutive_failures: int = 0
    last_success_at: float = 0
    last_failure_at: float = 0
    cooldown_until: float = 0
    last_failure_kind: str = ""
    last_status: int = 0
    updated_at: float = 0
    revision: int = 0


@dataclass(frozen=True)
class AffinityKey:
    router_scope: str
    router_scope_id: str
    router_service_id: str
    user_id: str
    conversation_id: str
    agent_name: str

    def as_tuple(self) -> tuple[str, ...]:
        return (
            self.router_scope, self.router_scope_id, self.router_service_id,
            self.user_id, self.conversation_id, self.agent_name)


@dataclass(frozen=True)
class AffinityRecord:
    key: AffinityKey
    child: ResolvedServiceRef
    successful_turns: int
    last_selected_at: float
    expires_at: float
    revision: int = 0


@dataclass(frozen=True)
class FailureObservation:
    observation_id: str
    timestamp: float
    category: str
    origin: str
    scope: str
    retryable: bool
    provider_status: int
    retry_after_seconds: float
    service_id: str
    provider: str
    model: str
    safe_message: str


@dataclass(frozen=True)
class RouteDecision:
    decision_id: str
    plan_id: str
    timestamp: float
    selected: ResolvedServiceRef
    strategy: str
    reason_code: str
    candidate_summaries: Tuple[CandidateSummary, ...] = field(default_factory=tuple)
