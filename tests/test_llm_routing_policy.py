from dataclasses import FrozenInstanceError

import pytest

from core.llm_routing_policy import select_candidates
from core.llm_routing_types import (
    AffinityKey,
    AffinityRecord,
    CandidateDefinition,
    CandidateKey,
    HealthRecord,
    ResolvedCandidate,
    ResolvedServiceRef,
    RouteIdentity,
    RoutePlan,
)


REVISION = "a" * 64


def _candidate(name, priority, position, *, enabled=True):
    ref = ResolvedServiceRef("global", "__global__", name, REVISION)
    return ResolvedCandidate(
        CandidateDefinition(name, priority, 1.0, enabled), ref,
        model="m", position=position)


def _health(candidate, state, *, until=0):
    key = CandidateKey(
        "global", "__global__", "router", *candidate.service.key, "m")
    return HealthRecord(key, state=state, cooldown_until=until)


def test_ordered_is_priority_then_position_and_deterministic():
    a, b, c = _candidate("a", 20, 0), _candidate("b", 10, 2), _candidate("c", 10, 1)
    selected = select_candidates([a, b, c], strategy="ordered")
    assert [item.service.service_id for item in selected.ordered] == ["c", "b", "a"]


def test_round_robin_advances_by_turn_counter_only():
    candidates = [_candidate(name, 10, index) for index, name in enumerate("abc")]
    assert [item.service.service_id for item in select_candidates(
        candidates, strategy="round_robin", counter=1).ordered] == ["b", "c", "a"]
    assert [item.service.service_id for item in select_candidates(
        candidates, strategy="round_robin", counter=4).ordered] == ["b", "c", "a"]


def test_sticky_reuses_eligible_target_then_rotates_at_success_limit():
    candidates = [_candidate(name, 10, index) for index, name in enumerate("abc")]
    key = AffinityKey("global", "__global__", "router", "u", "c", "agent")
    affinity = AffinityRecord(key, candidates[2].service, 1, 10, 100, 1)
    sticky = select_candidates(
        candidates, strategy="sticky_round_robin", counter=0,
        affinity=affinity, sticky_successful_turns=2, now=20)
    rotated = select_candidates(
        candidates, strategy="sticky_round_robin", counter=1,
        affinity=AffinityRecord(key, candidates[2].service, 2, 10, 100, 1),
        sticky_successful_turns=2, now=20)
    assert sticky.ordered[0].service.service_id == "c"
    assert sticky.reason_code == "sticky_affinity"
    assert rotated.ordered[0].service.service_id == "b"


def test_lru_uses_oldest_timestamp_with_deterministic_ties():
    a, b, c = [_candidate(name, 10, index) for index, name in enumerate("abc")]
    selected = select_candidates(
        [a, b, c], strategy="least_recently_used",
        last_selected={a.service.key: 20, b.service.key: 10, c.service.key: 10})
    assert [item.service.service_id for item in selected.ordered] == ["b", "c", "a"]


def test_disabled_locked_and_active_cooldown_are_excluded():
    disabled = _candidate("disabled", 1, 0, enabled=False)
    locked = _candidate("locked", 2, 1)
    cooling = _candidate("cooling", 3, 2)
    recovered = _candidate("recovered", 4, 3)
    health = {
        locked.service.key: _health(locked, "locked"),
        cooling.service.key: _health(cooling, "cooldown", until=50),
        recovered.service.key: _health(recovered, "cooldown", until=10),
    }
    selected = select_candidates(
        [disabled, locked, cooling, recovered], strategy="ordered",
        health=health, now=20)
    assert [item.service.service_id for item in selected.ordered] == ["recovered"]
    assert {item.reason_code for item in selected.excluded} == {
        "disabled", "locked", "cooldown"}


def test_all_ineligible_is_explicit_and_plan_is_immutable():
    candidate = _candidate("a", 1, 0, enabled=False)
    assert not select_candidates([candidate], strategy="ordered").ordered
    plan = RoutePlan.create(
        router=ResolvedServiceRef("global", "__global__", "router", REVISION),
        strategy="ordered",
        identity=RouteIdentity("u", "c", "a", "turn"),
        ordered_candidates=(candidate.service,))
    with pytest.raises(FrozenInstanceError):
        plan.strategy = "round_robin"
