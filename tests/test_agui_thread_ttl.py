"""AG-UI thread handles, generations and TTL sweep (plan v8.2, step P1-A).

Covers the B1-T / B1-G / B1.4-6 contracts of
``docs/WEBMCP_INTEGRATION_PLAN.md``: generation bootstrap at 0,
``thread_rotated`` precedence, generation-aware context identity created
atomically with the generation check, CAS rotation with crash-ordered
cleanup, the ``closed_before_generation`` watermark, the
``thread_ttl_seconds`` publication option, and the structurally scoped
sweep (agui-only, TTL-bearing isolated publications, lease-aware,
atomically throttled).
"""

import threading

import pytest

from core.a2a_store import A2AStore
from core._a2a_turn_machine import AguiCleanupPending, AguiThreadRotated


@pytest.fixture()
def store(tmp_path):
    return A2AStore(database_path=tmp_path / "a2a.sqlite3")


def _publication(store, *, ttl=None, policy="isolated", agent="helper",
                 conversation="conv-1"):
    publication = store.configure_publication(
        "owner", conversation, agent, context_policy=policy,
        thread_ttl_seconds=ttl)
    _, key = store.create_key(publication["publication_id"], "client")
    return publication, key["key_id"]


def _pending_marker(store, thread_id="t-1"):
    with store._connect() as connection:
        return connection.execute(
            "SELECT pending_cleanup_conversation FROM agui_threads "
            "WHERE thread_id=?", (thread_id,)).fetchone()[0]


# ── bootstrap & generation checks ────────────────────────────────────

def test_thread_bootstrap_starts_at_generation_zero(store):
    publication, key_id = _publication(store)
    handle = store.ensure_agui_thread(publication, key_id, "t-1")
    assert handle["generation"] == 0
    assert handle["context_id"].startswith("agui_")
    # Idempotent: a second bootstrap returns the same handle.
    assert store.ensure_agui_thread(publication, key_id, "t-1") == handle


def test_empty_thread_id_is_rejected(store):
    publication, key_id = _publication(store)
    with pytest.raises(ValueError):
        store.ensure_agui_thread(publication, key_id, "")


def test_resolve_creates_generation_aware_context(store):
    publication, key_id = _publication(store)
    context = store.resolve_agui_thread(publication, key_id, "t-1", 0)
    expected = store.agui_context_id(
        publication["publication_id"], key_id, "t-1", 0)
    assert context["context_id"] == expected
    assert context["internal_conversation_id"].startswith(
        publication["conversation_id"] + "::a2a::")


def test_unseen_thread_with_nonzero_generation_is_rotated(store):
    publication, key_id = _publication(store)
    with pytest.raises(AguiThreadRotated) as excinfo:
        store.resolve_agui_thread(publication, key_id, "t-new", 3)
    assert excinfo.value.current_generation == 0


def test_shared_policy_context_has_no_isolated_suffix(store):
    publication, key_id = _publication(store, policy="shared")
    context = store.resolve_agui_thread(publication, key_id, "t-1", 0)
    assert context["internal_conversation_id"] == publication["conversation_id"]


# ── rotation ─────────────────────────────────────────────────────────

def test_rotation_bumps_generation_and_changes_context_identity(store):
    publication, key_id = _publication(store)
    before = store.resolve_agui_thread(publication, key_id, "t-1", 0)
    deleted = []
    handle = store.rotate_agui_thread(
        publication, key_id, "t-1", expected_generation=0,
        delete_conversation=deleted.append)
    assert handle["generation"] == 1
    assert handle["context_id"] != before["context_id"]
    assert deleted == [before["internal_conversation_id"]]
    with pytest.raises(AguiThreadRotated) as excinfo:
        store.resolve_agui_thread(publication, key_id, "t-1", 0)
    assert excinfo.value.current_generation == 1
    context = store.resolve_agui_thread(publication, key_id, "t-1", 1)
    assert context["context_id"] == handle["context_id"]


def test_rotation_deletes_old_context_row(store):
    publication, key_id = _publication(store)
    before = store.resolve_agui_thread(publication, key_id, "t-1", 0)
    store.rotate_agui_thread(publication, key_id, "t-1",
                             expected_generation=0,
                             delete_conversation=lambda _cid: None)
    with store._connect() as connection:
        row = connection.execute(
            "SELECT 1 FROM a2a_contexts WHERE context_id=?",
            (before["context_id"],)).fetchone()
    assert row is None


def test_rotate_cas_bumps_only_once(store):
    publication, key_id = _publication(store)
    store.resolve_agui_thread(publication, key_id, "t-1", 0)
    store.rotate_agui_thread(publication, key_id, "t-1",
                             expected_generation=0,
                             delete_conversation=lambda _c: None)
    # A second rotate/sweep holding the same generation snapshot loses.
    with pytest.raises(AguiThreadRotated) as excinfo:
        store.rotate_agui_thread(publication, key_id, "t-1",
                                 expected_generation=0,
                                 delete_conversation=lambda _c: None)
    assert excinfo.value.current_generation == 1
    assert store.ensure_agui_thread(publication, key_id, "t-1")["generation"] == 1


def test_concurrent_resolve_and_rotate_never_resurrect_stale_context(store):
    """Whatever the interleaving, the old generation's context row is gone."""
    publication, key_id = _publication(store)
    store.resolve_agui_thread(publication, key_id, "t-1", 0)
    old_context_id = store.agui_context_id(
        publication["publication_id"], key_id, "t-1", 0)
    barrier = threading.Barrier(2)
    errors = []

    def resolver():
        barrier.wait()
        try:
            store.resolve_agui_thread(publication, key_id, "t-1", 0)
        except AguiThreadRotated:
            pass
        except Exception as exc:  # pragma: no cover - diagnostic
            errors.append(exc)

    def rotator():
        barrier.wait()
        try:
            store.rotate_agui_thread(publication, key_id, "t-1",
                                     expected_generation=0,
                                     delete_conversation=lambda _c: None)
        except Exception as exc:  # pragma: no cover - diagnostic
            errors.append(exc)

    threads = [threading.Thread(target=resolver),
               threading.Thread(target=rotator)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    with store._connect() as connection:
        row = connection.execute(
            "SELECT 1 FROM a2a_contexts WHERE context_id=?",
            (old_context_id,)).fetchone()
    assert row is None
    assert store.ensure_agui_thread(publication, key_id, "t-1")["generation"] == 1


# ── cleanup recovery ─────────────────────────────────────────────────

def test_interrupted_rotation_cleanup_is_finished_by_resolve(store):
    publication, key_id = _publication(store)
    before = store.resolve_agui_thread(publication, key_id, "t-1", 0)
    # Simulate the crash: no delete_conversation callback runs.
    store.rotate_agui_thread(publication, key_id, "t-1", expected_generation=0)
    assert _pending_marker(store) == before["internal_conversation_id"]
    deleted = []
    store.resolve_agui_thread(publication, key_id, "t-1", 1,
                              delete_conversation=deleted.append)
    assert deleted == [before["internal_conversation_id"]]
    assert _pending_marker(store) == ""


def test_pending_cleanup_without_callback_is_refused(store):
    publication, key_id = _publication(store)
    store.resolve_agui_thread(publication, key_id, "t-1", 0)
    store.rotate_agui_thread(publication, key_id, "t-1", expected_generation=0)
    marker = _pending_marker(store)
    assert marker
    with pytest.raises(AguiCleanupPending):
        store.resolve_agui_thread(publication, key_id, "t-1", 1)
    assert _pending_marker(store) == marker


def test_cleanup_callback_exception_keeps_marker(store):
    publication, key_id = _publication(store)
    store.resolve_agui_thread(publication, key_id, "t-1", 0)
    store.rotate_agui_thread(publication, key_id, "t-1", expected_generation=0)
    marker = _pending_marker(store)

    def failing(_conversation_id):
        raise OSError("conversation store unavailable")

    with pytest.raises(OSError):
        store.resolve_agui_thread(publication, key_id, "t-1", 1,
                                  delete_conversation=failing)
    assert _pending_marker(store) == marker


def test_second_rotation_refused_while_cleanup_pending(store):
    publication, key_id = _publication(store)
    store.resolve_agui_thread(publication, key_id, "t-1", 0)
    store.rotate_agui_thread(publication, key_id, "t-1", expected_generation=0)
    with pytest.raises(AguiCleanupPending):
        store.rotate_agui_thread(publication, key_id, "t-1",
                                 expected_generation=1,
                                 delete_conversation=lambda _c: None)
    # Once resolve finishes the cleanup, rotation works again.
    store.resolve_agui_thread(publication, key_id, "t-1", 1,
                              delete_conversation=lambda _c: None)
    handle = store.rotate_agui_thread(publication, key_id, "t-1",
                                      expected_generation=1,
                                      delete_conversation=lambda _c: None)
    assert handle["generation"] == 2


def test_rotate_cleanup_callback_runs_without_store_lock(store):
    """A blocked deletion callback must not freeze unrelated store work."""
    publication, key_id = _publication(store)
    store.resolve_agui_thread(publication, key_id, "t-a", 0)
    started = threading.Event()
    release = threading.Event()

    def blocking_delete(_conversation_id):
        started.set()
        assert release.wait(timeout=5)

    rotator = threading.Thread(target=store.rotate_agui_thread, args=(
        publication, key_id, "t-a"), kwargs={
            "expected_generation": 0,
            "delete_conversation": blocking_delete})
    rotator.start()
    assert started.wait(timeout=5)
    done = threading.Event()

    def other_operations():
        store.resolve_agui_thread(publication, key_id, "t-b", 0)
        store.get_publication(publication["publication_id"])
        done.set()

    worker = threading.Thread(target=other_operations)
    worker.start()
    assert done.wait(timeout=5), \
        "store operations blocked while the cleanup callback was running"
    release.set()
    rotator.join(timeout=5)
    worker.join(timeout=5)
    assert not rotator.is_alive()


def test_resolve_recovery_callback_runs_without_store_lock(store):
    publication, key_id = _publication(store)
    store.resolve_agui_thread(publication, key_id, "t-a", 0)
    # Interrupted rotation: pending marker, no callback ran.
    store.rotate_agui_thread(publication, key_id, "t-a", expected_generation=0)
    started = threading.Event()
    release = threading.Event()

    def blocking_delete(_conversation_id):
        started.set()
        assert release.wait(timeout=5)

    resolver = threading.Thread(target=store.resolve_agui_thread, args=(
        publication, key_id, "t-a", 1), kwargs={
            "delete_conversation": blocking_delete})
    resolver.start()
    assert started.wait(timeout=5)
    done = threading.Event()

    def other_operations():
        store.resolve_agui_thread(publication, key_id, "t-b", 0)
        store.get_publication(publication["publication_id"])
        done.set()

    worker = threading.Thread(target=other_operations)
    worker.start()
    assert done.wait(timeout=5), \
        "store operations blocked while the recovery callback was running"
    release.set()
    resolver.join(timeout=5)
    worker.join(timeout=5)
    assert not resolver.is_alive()
    assert _pending_marker(store, "t-a") == ""


def test_shared_rotation_never_deletes_the_parent_conversation(store):
    publication, key_id = _publication(store, policy="shared")
    before = store.resolve_agui_thread(publication, key_id, "t-1", 0)
    deleted = []
    store.rotate_agui_thread(publication, key_id, "t-1",
                             expected_generation=0,
                             delete_conversation=deleted.append)
    assert deleted == []
    assert _pending_marker(store) == ""
    with store._connect() as connection:
        row = connection.execute(
            "SELECT 1 FROM a2a_contexts WHERE context_id=?",
            (before["context_id"],)).fetchone()
    assert row is None


def test_close_generation_advances_watermark(store):
    publication, key_id = _publication(store)
    store.resolve_agui_thread(publication, key_id, "t-1", 0)
    handle = store.rotate_agui_thread(
        publication, key_id, "t-1", expected_generation=0,
        delete_conversation=lambda _c: None, close_generation=True)
    assert handle["generation"] == 1
    with store._connect() as connection:
        row = connection.execute(
            "SELECT closed_before_generation FROM agui_threads "
            "WHERE thread_id='t-1'").fetchone()
    assert row[0] == 1
    with pytest.raises(AguiThreadRotated):
        store.resolve_agui_thread(publication, key_id, "t-1", 0)


def test_rotation_clears_old_generation_tombstones(store):
    publication, key_id = _publication(store)
    store.resolve_agui_thread(publication, key_id, "t-1", 0)
    with store._connect() as connection:
        connection.execute(
            "INSERT INTO agui_run_tombstones VALUES (?, ?, 't-1', 0, 'run-1', "
            "'hash', 0)", (publication["publication_id"], key_id))
    store.rotate_agui_thread(publication, key_id, "t-1",
                             expected_generation=0,
                             delete_conversation=lambda _c: None)
    with store._connect() as connection:
        row = connection.execute(
            "SELECT 1 FROM agui_run_tombstones WHERE thread_id='t-1'").fetchone()
    assert row is None


# ── publication TTL option & schema ──────────────────────────────────

def test_thread_ttl_defaults_to_zero_and_persists(store):
    publication, _ = _publication(store)
    assert publication["thread_ttl_seconds"] == 0
    updated = store.configure_publication(
        "owner", "conv-1", "helper", thread_ttl_seconds=120)
    assert updated["thread_ttl_seconds"] == 120


def test_update_without_ttl_preserves_existing_ttl(store):
    store.configure_publication("owner", "conv-1", "helper",
                                thread_ttl_seconds=120)
    updated = store.configure_publication(
        "owner", "conv-1", "helper", label="renamed")
    assert updated["thread_ttl_seconds"] == 120
    # Explicit 0 still resets it.
    reset = store.configure_publication(
        "owner", "conv-1", "helper", thread_ttl_seconds=0)
    assert reset["thread_ttl_seconds"] == 0


def test_negative_ttl_is_clamped_to_zero(store):
    publication = store.configure_publication(
        "owner", "conv-1", "helper", thread_ttl_seconds=-5)
    assert publication["thread_ttl_seconds"] == 0


def test_delete_publication_cascades_thread_rows(store):
    publication, key_id = _publication(store)
    store.resolve_agui_thread(publication, key_id, "t-1", 0)
    with store._connect() as connection:
        connection.execute(
            "INSERT INTO agui_run_tombstones VALUES (?, ?, 't-1', 0, 'run-1', "
            "'hash', 0)", (publication["publication_id"], key_id))
    assert store.delete_publication(publication["publication_id"])
    with store._connect() as connection:
        threads = connection.execute(
            "SELECT COUNT(*) FROM agui_threads").fetchone()[0]
        tombstones = connection.execute(
            "SELECT COUNT(*) FROM agui_run_tombstones").fetchone()[0]
    assert threads == 0
    assert tombstones == 0


# ── sweep ────────────────────────────────────────────────────────────

def _expired(expired_ids):
    return lambda conversation_id: conversation_id in expired_ids


def test_sweep_rotates_only_expired_ttl_threads(store):
    publication, key_id = _publication(store, ttl=60)
    expired = store.resolve_agui_thread(publication, key_id, "t-old", 0)
    fresh = store.resolve_agui_thread(publication, key_id, "t-new", 0)
    deleted = []
    swept = store.sweep_agui_threads(
        is_conversation_expired=_expired({expired["internal_conversation_id"]}),
        delete_conversation=deleted.append,
        min_interval_seconds=0.0)
    assert swept == 1
    assert deleted == [expired["internal_conversation_id"]]
    with pytest.raises(AguiThreadRotated):
        store.resolve_agui_thread(publication, key_id, "t-old", 0)
    assert store.resolve_agui_thread(
        publication, key_id, "t-new", 0)["context_id"] == fresh["context_id"]


def test_sweep_ignores_ttl_zero_publications(store):
    publication, key_id = _publication(store, ttl=0)
    context = store.resolve_agui_thread(publication, key_id, "t-1", 0)
    swept = store.sweep_agui_threads(
        is_conversation_expired=_expired({context["internal_conversation_id"]}),
        delete_conversation=lambda _c: None,
        min_interval_seconds=0.0)
    assert swept == 0


def test_sweep_ignores_shared_policy_publications(store):
    publication, key_id = _publication(store, ttl=60, policy="shared")
    store.resolve_agui_thread(publication, key_id, "t-1", 0)
    swept = store.sweep_agui_threads(
        is_conversation_expired=lambda _c: True,
        delete_conversation=lambda _c: None,
        min_interval_seconds=0.0)
    assert swept == 0


def test_sweep_never_touches_server_issued_a2a_contexts(store):
    publication, key_id = _publication(store, ttl=60)
    a2a_context = store.resolve_context(publication, key_id)
    assert a2a_context["context_id"].startswith("ctx_")
    swept = store.sweep_agui_threads(
        is_conversation_expired=lambda _c: True,
        delete_conversation=lambda _c: None,
        min_interval_seconds=0.0)
    assert swept == 0
    with store._connect() as connection:
        row = connection.execute(
            "SELECT 1 FROM a2a_contexts WHERE context_id=?",
            (a2a_context["context_id"],)).fetchone()
    assert row is not None


def test_sweep_skips_threads_with_an_active_lease(store):
    publication, key_id = _publication(store, ttl=60)
    context = store.resolve_agui_thread(publication, key_id, "t-1", 0)
    swept = store.sweep_agui_threads(
        is_conversation_expired=lambda _c: True,
        delete_conversation=lambda _c: None,
        has_active_lease=lambda context_id: context_id == context["context_id"],
        min_interval_seconds=0.0)
    assert swept == 0


def test_sweep_is_throttled_by_min_interval(store):
    publication, key_id = _publication(store, ttl=60)
    store.resolve_agui_thread(publication, key_id, "t-1", 0)
    first = store.sweep_agui_threads(
        is_conversation_expired=lambda _c: True,
        delete_conversation=lambda _c: None,
        min_interval_seconds=300.0)
    assert first == 1
    second = store.sweep_agui_threads(
        is_conversation_expired=lambda _c: True,
        delete_conversation=lambda _c: None,
        min_interval_seconds=300.0)
    assert second == 0


def test_sweep_throttle_is_shared_across_store_instances(store, tmp_path):
    """Two workers on the same database pass the throttle at most once."""
    publication, key_id = _publication(store, ttl=60)
    store.resolve_agui_thread(publication, key_id, "t-1", 0)
    other = A2AStore(database_path=tmp_path / "a2a.sqlite3")
    first = store.sweep_agui_threads(
        is_conversation_expired=lambda _c: True,
        delete_conversation=lambda _c: None,
        min_interval_seconds=300.0)
    assert first == 1
    second = other.sweep_agui_threads(
        is_conversation_expired=lambda _c: True,
        delete_conversation=lambda _c: None,
        min_interval_seconds=300.0)
    assert second == 0
