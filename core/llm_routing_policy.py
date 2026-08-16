"""Deterministic initial-candidate policies for immutable LLM route plans."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from core.llm_routing_types import (
    AffinityRecord,
    CandidateExclusion,
    HealthRecord,
    ResolvedCandidate,
    STRATEGIES,
)


@dataclass(frozen=True)
class SelectionResult:
    ordered: tuple[ResolvedCandidate, ...]
    excluded: tuple[CandidateExclusion, ...]
    reason_code: str


def _rotate(items: list[ResolvedCandidate], offset: int) -> list[ResolvedCandidate]:
    if not items:
        return items
    offset = int(offset) % len(items)
    return items[offset:] + items[:offset]


def _health_for(candidate: ResolvedCandidate,
                health: Mapping[tuple[str, str, str], HealthRecord]):
    return health.get(candidate.service.key)


def select_candidates(
    candidates: Sequence[ResolvedCandidate], *, strategy: str,
    health: Mapping[tuple[str, str, str], HealthRecord] | None = None,
    counter: int = 0, affinity: AffinityRecord | None = None,
    sticky_successful_turns: int = 1,
    last_selected: Mapping[tuple[str, str, str], float] | None = None,
    now: float = 0,
) -> SelectionResult:
    """Filter and order one immutable turn snapshot."""
    if strategy not in STRATEGIES:
        raise ValueError(f"Unknown routing strategy '{strategy}'")
    health = health or {}
    last_selected = last_selected or {}
    eligible: list[ResolvedCandidate] = []
    excluded: list[CandidateExclusion] = []
    for candidate in candidates:
        if not candidate.definition.enabled:
            excluded.append(CandidateExclusion(candidate.service, "disabled"))
            continue
        record = _health_for(candidate, health)
        if record and record.state == "locked":
            excluded.append(CandidateExclusion(candidate.service, "locked"))
            continue
        if record and record.state == "cooldown" and record.cooldown_until > now:
            excluded.append(CandidateExclusion(
                candidate.service, "cooldown", record.cooldown_until))
            continue
        eligible.append(candidate)

    base = sorted(
        eligible,
        key=lambda item: (item.definition.priority, item.position,
                          item.service.scope, item.service.scope_id,
                          item.service.service_id))
    reason = strategy
    if strategy == "round_robin":
        base = _rotate(base, counter)
    elif strategy == "sticky_round_robin":
        sticky_limit = max(1, int(sticky_successful_turns))
        sticky_index = -1
        if affinity and affinity.expires_at > now \
                and affinity.successful_turns < sticky_limit:
            for index, candidate in enumerate(base):
                if candidate.service == affinity.child:
                    sticky_index = index
                    break
        if sticky_index >= 0:
            base = base[sticky_index:] + base[:sticky_index]
            reason = "sticky_affinity"
        else:
            base = _rotate(base, counter)
            reason = "sticky_rotate"
    elif strategy == "least_recently_used":
        base = sorted(
            base,
            key=lambda item: (
                float(last_selected.get(item.service.key, 0)),
                item.definition.priority, item.position,
                item.service.scope, item.service.scope_id,
                item.service.service_id))
    return SelectionResult(tuple(base), tuple(excluded), reason)
