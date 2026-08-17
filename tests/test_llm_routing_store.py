import concurrent.futures
import sqlite3

import pytest

from core.llm_routing_store import LLMRoutingStore
from core.llm_routing_types import (
    AffinityKey,
    AffinityRecord,
    CandidateKey,
    ResolvedServiceRef,
)


REVISION = "b" * 64


@pytest.fixture
def clock():
    value = [100.0]
    return value, lambda: value[0]


@pytest.fixture
def store(tmp_path, clock):
    instance = LLMRoutingStore(
        str(tmp_path / "routing.db"), clock=clock[1], event_retention=100)
    yield instance
    instance.close()


def _candidate_key():
    return CandidateKey(
        "user", "u", "router", "global", "__global__", "child", "m")


def _affinity_key():
    return AffinityKey("user", "u", "router", "u", "c", "agent")


def _child():
    return ResolvedServiceRef("global", "__global__", "child", REVISION)


def test_schema_health_and_persistence_across_reopen(tmp_path, clock):
    path = str(tmp_path / "routing.db")
    first = LLMRoutingStore(path, clock=clock[1])
    record = first.record_failure(
        _candidate_key(), failure_kind="rate_limited", provider_status=429,
        cooldown_until=150)
    assert record.state == "cooldown"
    assert record.consecutive_failures == 1
    first.close()
    second = LLMRoutingStore(path, clock=clock[1])
    try:
        assert second.get_health(_candidate_key()).last_status == 429
        success = second.record_success(_candidate_key())
        assert success.state == "healthy"
        assert success.consecutive_failures == 0
    finally:
        second.close()


def test_threshold_cooldown_without_retry_after_gets_a_deadline(store, clock):
    """Regression: state='cooldown' with cooldown_until=0 was inert."""
    key = _candidate_key()
    for _ in range(2):
        record = store.record_failure(key, failure_kind="provider_error")
    assert record.state == "degraded"
    assert record.cooldown_until == 0
    record = store.record_failure(key, failure_kind="provider_error")
    assert record.state == "cooldown"
    assert record.cooldown_until > clock[1]()
    # Backoff doubles per extra failure and a provider Retry-After still wins.
    deeper = store.record_failure(key, failure_kind="provider_error")
    assert deeper.cooldown_until > record.cooldown_until
    explicit = store.record_failure(
        key, failure_kind="rate_limited", cooldown_until=clock[1]() + 5)
    assert explicit.cooldown_until == clock[1]() + 5
    assert store.record_success(key).cooldown_until == 0


def test_counter_increment_is_atomic_under_threads(store):
    key = ("global", "__global__", "router")
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(lambda _index: store.next_counter(key), range(40)))
    assert sorted(values) == list(range(40))


def test_affinity_compare_and_swap_and_expiry(store, clock):
    key = _affinity_key()
    initial = AffinityRecord(key, _child(), 0, 100, 200)
    assert store.set_affinity(initial)
    loaded = store.get_affinity(key)
    assert loaded.child == _child()
    updated = AffinityRecord(key, _child(), 1, 110, 200)
    assert store.set_affinity(updated, expected_revision=loaded.revision)
    assert not store.set_affinity(updated, expected_revision=loaded.revision)
    clock[0][0] = 201
    store.cleanup()
    assert store.get_affinity(key) is None


def test_probe_lease_is_exclusive_and_expires(store, clock):
    first = store.acquire_probe(_candidate_key(), ttl_seconds=10)
    assert first
    assert store.acquire_probe(_candidate_key(), ttl_seconds=10) == ""
    assert not store.release_probe(_candidate_key(), "wrong")
    clock[0][0] = 111
    second = store.acquire_probe(_candidate_key(), ttl_seconds=10)
    assert second and second != first


def test_events_have_uuid_timestamp_retention_and_no_secret_fields(store):
    child = _child()
    event_id = store.append_event(
        event_type="plan_created", router_key=("user", "u", "router"),
        plan_id="plan", turn_id="turn", user_id="u",
        conversation_id="c", agent_name="agent", child=child,
        details={"candidate_count": 2})
    assert store.list_events(plan_id="plan")[0]["id"] == event_id
    with pytest.raises(ValueError, match="sensitive"):
        store.append_event(
            event_type="bad", router_key=("user", "u", "router"),
            details={"api_key": "secret"})
    with pytest.raises(ValueError, match="4096"):
        store.append_event(
            event_type="large", router_key=("user", "u", "router"),
            details={"safe": "x" * 5000})


def test_last_selected_map_and_router_scoped_events(store):
    child_a = ResolvedServiceRef("global", "__global__", "child-a", REVISION)
    child_b = ResolvedServiceRef("global", "__global__", "child-b", REVISION)
    mine = ("user", "u", "router")
    other = ("user", "u", "other")
    store.append_event(event_type="llm.route.selected", router_key=mine,
                       child=child_a, ts=100)
    store.append_event(event_type="llm.route.selected", router_key=mine,
                       child=child_b, ts=110)
    store.append_event(event_type="llm.route.selected", router_key=mine,
                       child=child_a, ts=120)
    store.append_event(event_type="llm.route.recovered", router_key=mine,
                       child=child_b, ts=130)
    store.append_event(event_type="llm.route.selected", router_key=other,
                       child=child_b, ts=140)
    assert store.last_selected_map(mine) == {
        ("global", "__global__", "child-a"): 120.0,
        ("global", "__global__", "child-b"): 110.0}
    assert {event["router_service_id"]
            for event in store.list_events(router_key=mine)} == {"router"}
    assert len(store.list_events(router_key=other)) == 1


def test_database_contains_no_secret_columns(store):
    rows = store._conn.execute("PRAGMA table_info(router_health)").fetchall()
    columns = {row[1] for row in rows}
    assert not columns.intersection({
        "api_key", "access_token", "refresh_token", "authorization", "cookie"})
    assert store._conn.execute("PRAGMA journal_mode").fetchone()[0] in {
        "wal", "memory", "delete"}
