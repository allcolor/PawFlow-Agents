"""Durable AG-UI run journal (plan v8.2, step P1-B).

Covers B1-J / B1-G: monotonic committed sequences, per-run quota with a
terminal reserve, terminal-only pruning with a replay watermark,
``replay_expired`` answered by a terminal snapshot, and rotation
dropping the old context's runs.
"""

import threading

import pytest

from core.a2a_store import A2AStore
from core._a2a_turn_journal import (
    AguiReplayExpired,
    AguiRunQuotaExceeded,
    AguiRunTerminal,
    AguiRunUnknown,
)


@pytest.fixture()
def store(tmp_path):
    return A2AStore(database_path=tmp_path / "a2a.sqlite3")


def _context(store, thread_id="t-run"):
    publication = store.configure_publication("owner", "conv-1", "helper")
    _, key = store.create_key(publication["publication_id"], "client")
    context = store.resolve_agui_thread(publication, key["key_id"],
                                        thread_id, 0)
    return publication, key["key_id"], context


@pytest.fixture()
def run(store):
    _, _, context = _context(store)
    store.open_agui_run(context["context_id"], "run-1")
    return (context["context_id"], "run-1")


# ── append & sequences ───────────────────────────────────────────────

def test_append_assigns_monotonic_sequences(store, run):
    context_id, run_id = run
    assert store.append_agui_event(context_id, run_id, '{"n":1}') == 1
    assert store.append_agui_event(context_id, run_id, '{"n":2}') == 2
    state = store.get_agui_run(context_id, run_id)
    assert state["committed_sequence"] == 2
    assert state["event_count"] == 2
    assert state["state"] == "active"


def test_open_is_idempotent(store, run):
    context_id, run_id = run
    store.append_agui_event(context_id, run_id, '{"n":1}')
    again = store.open_agui_run(context_id, run_id)
    assert again["committed_sequence"] == 1


def test_append_to_unknown_run_is_rejected(store):
    with pytest.raises(AguiRunUnknown):
        store.append_agui_event("agui_ctx1", "nope", "{}")


def test_terminal_event_requires_outcome_and_closes_the_run(store, run):
    context_id, run_id = run
    with pytest.raises(ValueError):
        store.append_agui_event(context_id, run_id, "{}", terminal=True)
    store.append_agui_event(context_id, run_id, '{"type":"RUN_FINISHED"}',
                            terminal=True, outcome="success")
    state = store.get_agui_run(context_id, run_id)
    assert state["state"] == "terminal"
    assert state["outcome"] == "success"
    with pytest.raises(AguiRunTerminal):
        store.append_agui_event(context_id, run_id, '{"n":2}')


def test_concurrent_appends_never_collide(store, run):
    context_id, run_id = run
    barrier = threading.Barrier(4)
    errors = []

    def writer(index):
        barrier.wait()
        try:
            for n in range(10):
                store.append_agui_event(context_id, run_id,
                                        f'{{"w":{index},"n":{n}}}')
        except Exception as exc:  # pragma: no cover - diagnostic
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    events = store.read_agui_events(context_id, run_id, limit=100)
    sequences = [event["seq"] for event in events]
    assert sequences == list(range(1, 41))


# ── quota & terminal reserve ─────────────────────────────────────────

def test_event_quota_terminates_with_journaled_error(store, run):
    context_id, run_id = run
    store.append_agui_event(context_id, run_id, '{"n":1}', max_events=2)
    store.append_agui_event(context_id, run_id, '{"n":2}', max_events=2)
    with pytest.raises(AguiRunQuotaExceeded):
        store.append_agui_event(context_id, run_id, '{"n":3}', max_events=2)
    state = store.get_agui_run(context_id, run_id)
    assert state["state"] == "terminal"
    assert state["outcome"] == "run_quota_exceeded"
    events = store.read_agui_events(context_id, run_id)
    assert "run_quota_exceeded" in events[-1]["event_json"]


def test_byte_quota_terminates_with_journaled_error(store, run):
    context_id, run_id = run
    store.append_agui_event(context_id, run_id, "x" * 50, max_bytes=60)
    with pytest.raises(AguiRunQuotaExceeded):
        store.append_agui_event(context_id, run_id, "y" * 50, max_bytes=60)
    state = store.get_agui_run(context_id, run_id)
    assert state["outcome"] == "run_quota_exceeded"


def test_terminal_event_is_exempt_from_quota(store, run):
    """The reserve: a run at quota can still record how it ended."""
    context_id, run_id = run
    store.append_agui_event(context_id, run_id, '{"n":1}', max_events=1)
    sequence = store.append_agui_event(
        context_id, run_id, '{"type":"RUN_ERROR"}', terminal=True,
        outcome="run_lost", max_events=1)
    assert sequence == 2
    assert store.get_agui_run(context_id, run_id)["outcome"] == "run_lost"


# ── read / replay ────────────────────────────────────────────────────

def test_read_returns_the_tail_after_a_position(store, run):
    context_id, run_id = run
    for n in range(5):
        store.append_agui_event(context_id, run_id, f'{{"n":{n}}}')
    tail = store.read_agui_events(context_id, run_id, after_seq=3)
    assert [event["seq"] for event in tail] == [4, 5]


def test_replay_below_watermark_returns_terminal_snapshot(store, run):
    context_id, run_id = run
    store.append_agui_event(context_id, run_id, '{"n":1}')
    store.append_agui_event(context_id, run_id, '{"type":"RUN_FINISHED"}',
                            terminal=True, outcome="success")
    assert store.prune_agui_journals(retention_seconds=0.0) == 1
    with pytest.raises(AguiReplayExpired) as excinfo:
        store.read_agui_events(context_id, run_id, after_seq=0)
    snapshot = excinfo.value.snapshot
    assert snapshot["state"] == "terminal"
    assert snapshot["outcome"] == "success"
    assert snapshot["committed_sequence"] == 2
    # Reading exactly at the watermark is an empty, valid tail.
    assert store.read_agui_events(context_id, run_id, after_seq=2) == []


# ── pruning ──────────────────────────────────────────────────────────

def test_prune_never_touches_active_runs(store, run):
    context_id, run_id = run
    store.append_agui_event(context_id, run_id, '{"n":1}')
    assert store.prune_agui_journals(retention_seconds=0.0) == 0
    assert store.read_agui_events(context_id, run_id)  # replay intact


def test_prune_honors_the_retention_floor(store, run):
    context_id, run_id = run
    store.append_agui_event(context_id, run_id, '{"type":"RUN_FINISHED"}',
                            terminal=True, outcome="success")
    # Younger than the floor: kept.
    assert store.prune_agui_journals(retention_seconds=3600.0) == 0
    assert store.read_agui_events(context_id, run_id)


def test_prune_keeps_the_run_row_as_snapshot(store, run):
    context_id, run_id = run
    store.append_agui_event(context_id, run_id, '{"type":"RUN_FINISHED"}',
                            terminal=True, outcome="success")
    store.prune_agui_journals(retention_seconds=0.0)
    state = store.get_agui_run(context_id, run_id)
    assert state is not None
    assert state["replay_watermark"] == state["committed_sequence"]
    with store._connect() as connection:
        remaining = connection.execute(
            "SELECT COUNT(*) FROM agui_journal WHERE context_id=? AND "
            "run_id=?", (context_id, run_id)).fetchone()[0]
    assert remaining == 0


def test_prune_is_idempotent(store, run):
    context_id, run_id = run
    store.append_agui_event(context_id, run_id, '{"type":"RUN_FINISHED"}',
                            terminal=True, outcome="success")
    assert store.prune_agui_journals(retention_seconds=0.0) == 1
    assert store.prune_agui_journals(retention_seconds=0.0) == 0


# ── review-required adversarial cases ────────────────────────────────

def test_prune_from_second_store_then_read_is_replay_expired(store, run, tmp_path):
    """Prune-first ordering: the late reader gets the terminal snapshot."""
    context_id, run_id = run
    store.append_agui_event(context_id, run_id, '{"n":1}')
    store.append_agui_event(context_id, run_id, '{"type":"RUN_FINISHED"}',
                            terminal=True, outcome="success")
    other = A2AStore(database_path=tmp_path / "a2a.sqlite3")
    assert other.prune_agui_journals(retention_seconds=0.0) == 1
    with pytest.raises(AguiReplayExpired) as excinfo:
        store.read_agui_events(context_id, run_id, after_seq=0)
    assert excinfo.value.snapshot["outcome"] == "success"


def test_concurrent_prune_never_yields_silent_empty_tail(store, run, tmp_path):
    """Deterministic overlap proof, scheduler-independent, zero sleep:
    while the reader's BEGIN IMMEDIATE transaction is provably open, a
    second connection with ``busy_timeout=0`` attempting BEGIN IMMEDIATE
    MUST fail with 'database is locked' — so a prune can never slip
    between the reader's lookup and its event SELECT. The reader then
    gets the pre-prune events (never []), the prune proceeds afterwards,
    and a late replay gets the terminal snapshot."""
    import sqlite3
    from contextlib import contextmanager

    context_id, run_id = run
    store.append_agui_event(context_id, run_id, '{"n":1}')
    store.append_agui_event(context_id, run_id, '{"type":"RUN_FINISHED"}',
                            terminal=True, outcome="success")
    other = A2AStore(database_path=tmp_path / "a2a.sqlite3")

    reader_in_tx = threading.Event()
    release_reader = threading.Event()
    original_immediate = store._immediate

    @contextmanager
    def instrumented_immediate():
        with original_immediate() as connection:
            class _GatedConnection:
                def execute(self, sql, *params):
                    cursor = connection.execute(sql, *params)
                    if "FROM agui_runs" in sql and not reader_in_tx.is_set():
                        # The reader now holds the write lock; keep the
                        # transaction open until the probe has run.
                        reader_in_tx.set()
                        assert release_reader.wait(timeout=5)
                    return cursor
            yield _GatedConnection()

    result = {}
    errors = []

    def reader():
        try:
            result["events"] = store.read_agui_events(
                context_id, run_id, after_seq=0)
        except Exception as exc:  # pragma: no cover - diagnostic
            errors.append(exc)

    store._immediate = instrumented_immediate
    try:
        reader_thread = threading.Thread(target=reader)
        reader_thread.start()
        assert reader_in_tx.wait(timeout=5)
        # PROOF of contention: while the reader transaction is open, an
        # immediate transaction from any other connection is refused.
        probe = sqlite3.connect(tmp_path / "a2a.sqlite3")
        try:
            probe.isolation_level = None
            probe.execute("PRAGMA busy_timeout = 0")
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                probe.execute("BEGIN IMMEDIATE")
        finally:
            probe.close()
        release_reader.set()
        reader_thread.join(timeout=10)
    finally:
        del store._immediate
    assert errors == []
    assert not reader_thread.is_alive()
    # The reader saw the full pre-prune journal — never [].
    assert [event["seq"] for event in result["events"]] == [1, 2]
    # With the reader done, the second instance's prune proceeds…
    assert other.prune_agui_journals(retention_seconds=0.0) == 1
    # …and a late replay gets the terminal snapshot.
    with pytest.raises(AguiReplayExpired):
        store.read_agui_events(context_id, run_id, after_seq=0)


def test_utf8_multibyte_quota_counts_bytes(store, run):
    context_id, run_id = run
    # "éé" is 2 characters but 4 UTF-8 bytes: over a 3-byte quota.
    with pytest.raises(AguiRunQuotaExceeded):
        store.append_agui_event(context_id, run_id, "éé", max_bytes=3)
    assert store.get_agui_run(context_id, run_id)["outcome"] == \
        "run_quota_exceeded"


def test_cursor_domain_is_validated(store, run):
    context_id, run_id = run
    store.append_agui_event(context_id, run_id, '{"n":1}')
    # Negative cursor on an ACTIVE run: ValueError, never replay_expired.
    with pytest.raises(ValueError):
        store.read_agui_events(context_id, run_id, after_seq=-1)
    with pytest.raises(ValueError):
        store.read_agui_events(context_id, run_id, after_seq=6)
    with pytest.raises(ValueError):
        store.read_agui_events(context_id, run_id, limit=0)


def test_delete_publication_cascades_runs_and_journal(store, run):
    context_id, run_id = run
    store.append_agui_event(context_id, run_id, '{"n":1}')
    publication_id = store.list_publications()[0]["publication_id"]
    assert store.delete_publication(publication_id)
    assert store.get_agui_run(context_id, run_id) is None
    with store._connect() as connection:
        remaining = connection.execute(
            "SELECT COUNT(*) FROM agui_journal").fetchone()[0]
    assert remaining == 0


def test_open_on_rotated_context_is_refused(store):
    publication, key_id, context = _context(store)
    store.rotate_agui_thread(publication, key_id, "t-run",
                             expected_generation=0,
                             delete_conversation=lambda _c: None)
    with pytest.raises(AguiRunUnknown):
        store.open_agui_run(context["context_id"], "run-x")


def test_open_concurrent_with_rotation_never_asserts(store):
    publication, key_id, context = _context(store)
    barrier = threading.Barrier(2)
    unexpected = []

    def opener():
        barrier.wait()
        try:
            store.open_agui_run(context["context_id"], "run-x")
        except AguiRunUnknown:
            pass
        except Exception as exc:  # pragma: no cover - diagnostic
            unexpected.append(exc)

    def rotator():
        barrier.wait()
        store.rotate_agui_thread(publication, key_id, "t-run",
                                 expected_generation=0,
                                 delete_conversation=lambda _c: None)

    threads = [threading.Thread(target=opener),
               threading.Thread(target=rotator)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert unexpected == []
    # Whatever the interleaving, the rotated context keeps no run.
    assert store.get_agui_run(context["context_id"], "run-x") is None


# ── rotation interplay ───────────────────────────────────────────────

def test_rotation_drops_the_old_context_runs(store):
    publication = store.configure_publication("owner", "conv-1", "helper")
    _, key = store.create_key(publication["publication_id"], "client")
    key_id = key["key_id"]
    context = store.resolve_agui_thread(publication, key_id, "t-1", 0)
    store.open_agui_run(context["context_id"], "run-1")
    store.append_agui_event(context["context_id"], "run-1", '{"n":1}')
    store.rotate_agui_thread(publication, key_id, "t-1",
                             expected_generation=0,
                             delete_conversation=lambda _c: None)
    assert store.get_agui_run(context["context_id"], "run-1") is None
    with store._connect() as connection:
        remaining = connection.execute(
            "SELECT COUNT(*) FROM agui_journal WHERE context_id=?",
            (context["context_id"],)).fetchone()[0]
    assert remaining == 0
