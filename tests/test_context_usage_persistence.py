"""Ordered, bounded gauge persistence under blocked and competing writers."""

import copy
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tasks.ai import context_usage as gauges
from tasks.ai.agent_emitter import StreamEmitter


def usage(value, *, revision=0, updated=None, **fields):
    result = {"used": value, "max": 1000, "updated_at": value if updated is None else updated,
              "cache_params": {"max": 1000}, **fields}
    if revision:
        result.update(context_source_measured=True,
                      context_measurement_revision=revision,
                      context_measurement_mode="session")
    return result


class Store:
    def __init__(self):
        self.values = {}
        self.writes = []
        self.lock = threading.Lock()
        self.entered = threading.Event()
        self.release = threading.Event()
        self.block = None
        self.failure = None

    def get_extra_snapshot(self, cid, key, default):
        with self.lock:
            return copy.deepcopy(self.values.get(cid, default))

    def set_extra(self, cid, key, value):
        if self.block and self.block(cid, value):
            self.entered.set()
            assert self.release.wait(3)
        if self.failure and self.failure(cid, value):
            raise OSError("fixture write failure")
        with self.lock:
            self.values[cid] = copy.deepcopy(value)
            self.writes.append((cid, copy.deepcopy(value)))


@pytest.fixture
def persistence(monkeypatch):
    monkeypatch.setattr(gauges, "_USAGE_CACHE", {})
    writer = gauges._ContextUsagePersistence()
    store = Store()
    yield writer, store
    store.release.set()
    writer.shutdown()
    assert writer._workers == 0
    assert not writer._pending
    assert not writer._active
    assert all(not thread.is_alive() for thread in writer._threads)


def wait_idle(writer):
    with writer._condition:
        assert writer._condition.wait_for(
            lambda: not writer._pending and not writer._active, timeout=3)
        assert not writer._pending
        assert not writer._active


def test_burst_coalesces_and_keeps_final_snapshot_across_agents(persistence):
    writer, store = persistence
    store.block = lambda cid, values: values.get("a", {}).get("used") == 1
    writer.submit("conv", "a", usage(1), store=store)
    assert store.entered.wait(2)
    try:
        for value in range(2, 101):
            writer.submit("conv", "a", usage(value), store=store)
        final = usage(200)
        writer.submit("conv", "b", final, store=store)
        final["cache_params"]["max"] = 0
        with writer._condition:
            assert writer._workers == 1
            assert len(writer._pending) == 2
        assert store.writes == []
    finally:
        store.release.set()
    wait_idle(writer)
    assert [v["a"]["used"] for _, v in store.writes] == [1, 100, 100]
    assert store.values["conv"]["b"]["used"] == 200
    assert store.values["conv"]["b"]["cache_params"]["max"] == 1000


def test_worker_count_is_bounded_while_distinct_conversations_queue(persistence):
    writer, store = persistence
    entered = threading.Barrier(3)
    release = threading.Event()
    original = store.set_extra

    # Ensure the test exercises independent lock stripes.
    cid1 = next("conv" + str(i) for i in range(1, 100)
                if hash("conv" + str(i)) % 64 != hash("conv0") % 64)
    def blocked(cid, key, value):
        if cid in {"conv0", cid1}:
            entered.wait(3)
            assert release.wait(3)
        return original(cid, key, value)
    store.set_extra = blocked
    writer.submit("conv0", "a", usage(1), store=store)
    writer.submit(cid1, "a", usage(1), store=store)
    try:
        entered.wait(3)
        for i in range(20):
            writer.submit("other" + str(i), "a", usage(i + 1), store=store)
        with writer._condition:
            assert writer._workers == 2
            assert len(writer._active) == 2
            assert len(writer._pending) == 20
    finally:
        release.set()
    wait_idle(writer)
    assert len(store.writes) == 22


def test_workers_are_reused_across_separate_gauge_updates(persistence, monkeypatch):
    writer, store = persistence
    created = []
    original = threading.Thread

    def thread(*args, **kwargs):
        result = original(*args, **kwargs)
        created.append(result)
        return result

    monkeypatch.setattr(gauges.threading, "Thread", thread)
    for value in range(1, 11):
        writer.submit("conv", "a", usage(value), store=store)
        wait_idle(writer)
    assert len(created) <= 2
    assert all(thread.daemon for thread in created)
    assert store.values["conv"]["a"]["used"] == 10


def test_direct_provider_write_and_pending_estimate_cannot_regress(persistence):
    writer, store = persistence
    gauges.persist_context_usage("conv", "a", usage(500, revision=4), store=store)
    writer.submit("conv", "a", usage(100, updated=600), store=store)
    writer.submit("conv", "a", usage(600, revision=3, updated=700), store=store)
    wait_idle(writer)
    assert store.values["conv"]["a"]["used"] == 500
    gauges.persist_context_usage("conv", "a", usage(50, revision=5, updated=800), store=store)
    assert store.values["conv"]["a"]["used"] == 50
    assert gauges._USAGE_CACHE[("conv", "a")]["context_measurement_revision"] == 5


def test_pending_native_revision_cannot_be_replaced_by_late_estimate(persistence):
    writer, store = persistence
    store.block = lambda cid, values: values.get("a", {}).get("used") == 1
    writer.submit("conv", "a", usage(1), store=store)
    assert store.entered.wait(2)
    try:
        writer.submit("conv", "a", usage(30, revision=3, updated=3), store=store)
        writer.submit("conv", "a", usage(90, revision=2, updated=4), store=store)
        writer.submit("conv", "a", usage(99, updated=5), store=store)
    finally:
        store.release.set()
    wait_idle(writer)
    assert store.values["conv"]["a"]["used"] == 30


@pytest.mark.parametrize("reset_fields", [
    {"cli_context_state": "cold"},
    {"source": "compact_post"},
])
def test_reset_and_next_session_survive_stale_old_session(persistence, reset_fields):
    writer, store = persistence
    gauges.persist_context_usage("conv", "a", usage(500, revision=10), store=store)
    gauges.persist_context_usage("conv", "a", usage(0, updated=600, **reset_fields), store=store)
    writer.submit("conv", "a", usage(550, revision=11), store=store)
    wait_idle(writer)
    assert store.values["conv"]["a"]["used"] == 0
    gauges.persist_context_usage("conv", "a", usage(50, revision=1, updated=700), store=store)
    assert store.values["conv"]["a"]["used"] == 50


def test_blocked_sync_write_orders_other_agent_without_holding_cache_lock(persistence):
    writer, store = persistence
    store.block = lambda cid, values: values.get("a", {}).get("used") == 1 and "b" not in values
    errors = []

    def direct():
        try:
            gauges.persist_context_usage("conv", "a", usage(1), store=store)
        except BaseException as exc:
            errors.append(exc)

    direct_thread = threading.Thread(target=direct)
    direct_thread.start()
    try:
        assert store.entered.wait(2)
        assert gauges._USAGE_CACHE_LOCK.acquire(timeout=1)
        gauges._USAGE_CACHE_LOCK.release()
        writer.submit("conv", "b", usage(2), store=store)
    finally:
        store.release.set()
        direct_thread.join(3)
    wait_idle(writer)
    assert not errors
    assert {name: entry["used"] for name, entry in store.values["conv"].items()} == {"a": 1, "b": 2}


def test_existing_stored_agent_survives_partially_populated_memory_cache(persistence):
    _, store = persistence
    store.values["conv"] = {"stored": usage(4)}
    gauges._USAGE_CACHE[("conv", "active")] = usage(5)
    gauges.persist_context_usage("conv", "active", usage(6), store=store)
    assert set(store.values["conv"]) == {"stored", "active"}


def test_coalescing_retains_reset_before_next_session_revision(persistence):
    writer, store = persistence
    store.block = lambda cid, values: values["a"]["used"] == 500
    writer.submit("conv", "a", usage(500, revision=10), store=store)
    assert store.entered.wait(2)
    try:
        writer.submit("conv", "a", usage(0, updated=600, cli_context_state="cold"), store=store)
        writer.submit("conv", "a", usage(20, revision=1, updated=700), store=store)
        writer.submit("conv", "a", usage(30, revision=2, updated=800), store=store)
    finally:
        store.release.set()
    wait_idle(writer)
    assert store.values["conv"]["a"]["used"] == 30
    assert store.values["conv"]["a"]["context_measurement_revision"] == 2


def test_write_failure_does_not_strand_pending_final_state(persistence):
    writer, store = persistence
    store.block = lambda cid, values: values["a"]["used"] == 1
    store.failure = lambda cid, values: values["a"]["used"] == 1
    writer.submit("conv", "a", usage(1), store=store)
    assert store.entered.wait(2)
    writer.submit("conv", "a", usage(2), store=store)
    store.release.set()
    wait_idle(writer)
    assert store.values["conv"]["a"]["used"] == 2


def test_emitter_publishes_and_stops_while_gauge_write_is_blocked(persistence, monkeypatch):
    writer, store = persistence
    monkeypatch.setattr(gauges, "_CONTEXT_USAGE_PERSISTENCE", writer)
    monkeypatch.setattr("core.conversation_store.ConversationStore.instance", lambda: store)
    ctx = {"active_agent_name": "a", "client": SimpleNamespace(provider="test")}
    bus = Mock()
    emitter = StreamEmitter("conv", bus, ctx, Mock(), "generation", 1)
    payload = {"context_used": 1, "context_max": 1000, "context_cache": usage(1)}
    monkeypatch.setattr(emitter, "_context_usage_payload", lambda reason: payload)
    store.block = lambda cid, values: values["a"]["used"] == 1
    emitter._publish_context_usage("append")
    assert store.entered.wait(2)
    try:
        payload = {"context_used": 2, "context_max": 1000, "context_cache": usage(2)}
        emitter._publish_context_usage("append")
        emitter._stop_all_heartbeats()
        assert bus.publish_event.call_count == 2
        assert store.writes == []
    finally:
        store.release.set()
    wait_idle(writer)
    assert store.values["conv"]["a"]["used"] == 2


@pytest.mark.parametrize("cold_cache", [False, True])
def test_real_store_patch_shares_full_gauge_write_lock(
        persistence, tmp_path, monkeypatch, cold_cache):
    from core.conversation_store import ConversationStore

    _, _store = persistence
    store = ConversationStore(store_dir=str(tmp_path / "conversations"))
    cid = "fixture-gauge-race"
    store.save(cid, [], user_id="fixture-user")
    if cold_cache:
        with store._cache_lock:
            store._cache.pop(cid, None)
    entered, release, patch_started = (threading.Event() for _ in range(3))
    original_read = store._read_extras
    paused = False
    errors = []

    def read_extras(conv):
        nonlocal paused
        result = original_read(conv)
        if threading.current_thread() is gauge_thread and not paused:
            paused = True
            entered.set()
            assert release.wait(3)
        return result

    def gauge():
        try:
            gauges.persist_context_usage(cid, "a", usage(500, revision=4), store=store)
        except BaseException as exc:
            errors.append(exc)

    def patch():
        patch_started.set()
        try:
            store._maybe_persist_context_usage_from_patch(cid, {
                "ts": 700, "source": {
                    "name": "a", "context_used": 700, "context_max": 1000}})
        except BaseException as exc:
            errors.append(exc)

    monkeypatch.setattr(store, "_read_extras", read_extras)
    def no_conv_lock(cid):
        raise AssertionError("Gauge persistence must not acquire a conversation lock")
    monkeypatch.setattr(store, "_get_conv_lock", no_conv_lock)
    gauge_thread = threading.Thread(target=gauge)
    patch_thread = threading.Thread(target=patch)
    gauge_thread.start()
    try:
        assert entered.wait(2)
        patch_thread.start()
        assert patch_started.wait(2)
        extras_lock = store._get_extras_lock(cid)
        acquired = extras_lock.acquire(blocking=False)
        if acquired:
            extras_lock.release()
        assert not acquired, "Gauge merge released the extras lock after reading"
        assert gauges._USAGE_CACHE_LOCK.acquire(timeout=1)
        gauges._USAGE_CACHE_LOCK.release()
    finally:
        release.set()
        gauge_thread.join(3)
        if patch_thread.ident:
            patch_thread.join(3)
    assert not gauge_thread.is_alive()
    assert not patch_thread.is_alive()
    assert not errors
    assert original_read(cid)["context_usage"]["a"]["used"] == 700
    # A stale gauge must also respect a patch absent from the hot cache.
    gauges.persist_context_usage(cid, "a", usage(600, revision=5), store=store)
    assert original_read(cid)["context_usage"]["a"]["used"] == 700
