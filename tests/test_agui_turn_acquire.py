"""AG-UI turn admission, outbox, lifecycle and fencing (plan v8.2, P1-C).

Covers B1-A (acquire branch order and one-writer), B1-I (idempotency key,
conflict, tombstone expiry, deterministic generation-aware turn id),
B1-O (outbox claim/ack crash matrix, running lease, heartbeat, fence,
orphaning with a journaled run_lost) and B1-P (abandonment + exact-set
exactly-once consumption of frontend calls).
"""

import pytest

from core.a2a_store import A2AStore
from core._a2a_turn_acquire import (
    AguiFenceLost,
    AguiIdempotencyConflict,
    AguiIdempotencyExpired,
    AguiParentMismatch,
    AguiTurnBusy,
)
from core._a2a_turn_journal import AguiRunTerminal
from core._a2a_turn_machine import AguiThreadRotated


@pytest.fixture()
def store(tmp_path):
    return A2AStore(database_path=tmp_path / "a2a.sqlite3")


@pytest.fixture()
def setup(store):
    publication = store.configure_publication("owner", "conv-1", "helper")
    _, key = store.create_key(publication["publication_id"], "client")
    return publication, key["key_id"]


def _acquire(store, setup, run_id="run-1", body="hash-1", **kwargs):
    publication, key_id = setup
    return store.acquire_agui_turn(publication, key_id, "t-1", 0, run_id,
                                   body, **kwargs)


def _start(store, admission, worker="w1"):
    context_id, run_id = admission["context_id"], admission["run_id"]
    assert store.claim_agui_dispatch("dispatcher") is not None
    assert store.ack_agui_dispatch(context_id, run_id, "dispatcher")
    store.start_agui_turn(context_id, run_id, worker)


def _run_to_terminal(store, admission, outcome="success", worker="w1"):
    context_id, run_id = admission["context_id"], admission["run_id"]
    _start(store, admission, worker)
    store.finish_agui_turn(context_id, run_id, worker, outcome)


# ── acquire ──────────────────────────────────────────────────────────

def test_acquire_reserves_with_deterministic_turn_id(store, setup):
    admission = _acquire(store, setup)
    assert admission["replay"] is False
    assert admission["state"] == "reserved"
    assert admission["turn_id"] == store.agui_turn_id(
        admission["context_id"], "run-1")
    assert admission["fence_token"] == 1
    # The journal run row was opened in the same transaction.
    assert store.get_agui_run(admission["context_id"], "run-1") is not None


def test_turn_id_is_generation_aware(store, setup):
    publication, key_id = setup
    admission = _acquire(store, setup)
    _run_to_terminal(store, admission)
    store.rotate_agui_thread(publication, key_id, "t-1",
                             expected_generation=0,
                             delete_conversation=lambda _c: None)
    fresh = store.acquire_agui_turn(publication, key_id, "t-1", 1, "run-1",
                                    "hash-1")
    assert fresh["turn_id"] != admission["turn_id"]


def test_replay_returns_same_admission_without_consuming(store, setup):
    first = _acquire(store, setup)
    replay = _acquire(store, setup)
    assert replay["replay"] is True
    assert replay["turn_id"] == first["turn_id"]
    with store._connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM agui_admissions").fetchone()[0]
    assert count == 1


def test_different_body_is_a_conflict(store, setup):
    _acquire(store, setup)
    with pytest.raises(AguiIdempotencyConflict):
        _acquire(store, setup, body="hash-OTHER")


def test_tombstoned_run_is_expired(store, setup):
    publication, key_id = setup
    admission = _acquire(store, setup)
    _run_to_terminal(store, admission)
    with store._connect() as connection:
        connection.execute(
            "DELETE FROM agui_admissions WHERE run_id='run-1'")
        connection.execute(
            "INSERT INTO agui_run_tombstones VALUES (?, ?, 't-1', 0, "
            "'run-1', 'hash-1', 0)",
            (publication["publication_id"], key_id))
    with pytest.raises(AguiIdempotencyExpired):
        _acquire(store, setup)


def test_one_writer_per_thread(store, setup):
    _acquire(store, setup)
    with pytest.raises(AguiTurnBusy) as excinfo:
        _acquire(store, setup, run_id="run-2")
    assert excinfo.value.active_run_id == "run-1"


def test_thread_frees_after_terminal(store, setup):
    admission = _acquire(store, setup)
    _run_to_terminal(store, admission)
    second = _acquire(store, setup, run_id="run-2", body="hash-2")
    assert second["state"] == "reserved"
    # finish invalidated the first fence (bump), the new admission bumps
    # again: strictly monotonic.
    assert second["fence_token"] > admission["fence_token"]


def test_generation_check_precedes_idempotency(store, setup):
    publication, key_id = setup
    admission = _acquire(store, setup)
    _run_to_terminal(store, admission)
    store.rotate_agui_thread(publication, key_id, "t-1",
                             expected_generation=0,
                             delete_conversation=lambda _c: None)
    # Replaying the old run at the old generation is thread_rotated,
    # not conflict/expired: generation is branch 0.
    with pytest.raises(AguiThreadRotated):
        store.acquire_agui_turn(publication, key_id, "t-1", 0, "run-1",
                                "hash-1")


# ── parent linkage & call ledger ─────────────────────────────────────

def test_pending_calls_require_parent_run(store, setup):
    admission = _acquire(store, setup)
    context_id = admission["context_id"]
    _start(store, admission)
    store.record_agui_call(context_id, "run-1", "call-a", "do_thing")
    store.finish_agui_turn(context_id, "run-1", "w1", "success")  # keeps calls pending
    with pytest.raises(AguiParentMismatch):
        _acquire(store, setup, run_id="run-2", body="hash-2")
    follow = _acquire(store, setup, run_id="run-2", body="hash-2",
                      parent_run_id="run-1")
    assert follow["parent_run_id"] == "run-1"


def test_dangling_parent_run_is_rejected(store, setup):
    # Fail closed: a parentRunId that binds to nothing (no consumable
    # batch, no pending calls) is a stale or replayed follow-up.
    with pytest.raises(AguiParentMismatch, match="ghost"):
        _acquire(store, setup, parent_run_id="ghost")


def test_consume_requires_the_exact_pending_set(store, setup):
    admission = _acquire(store, setup)
    context_id = admission["context_id"]
    _start(store, admission)
    store.record_agui_call(context_id, "run-1", "call-a")
    store.record_agui_call(context_id, "run-1", "call-b")
    with pytest.raises(AguiParentMismatch, match="missing"):
        store.consume_agui_calls(context_id, "run-1", ["call-a"])
    with pytest.raises(AguiParentMismatch, match="extra"):
        store.consume_agui_calls(context_id, "run-1",
                                 ["call-a", "call-b", "call-x"])
    store.consume_agui_calls(context_id, "run-1", ["call-b", "call-a"])
    # Exactly-once: the set is no longer pending.
    assert store.pending_agui_calls(context_id, "run-1") == []
    with pytest.raises(AguiParentMismatch):
        store.consume_agui_calls(context_id, "run-1", ["call-a", "call-b"])


def test_non_success_terminal_abandons_pending_calls(store, setup):
    admission = _acquire(store, setup)
    context_id = admission["context_id"]
    _start(store, admission)
    store.record_agui_call(context_id, "run-1", "call-a")
    store.finish_agui_turn(context_id, "run-1", "w1", "error")
    assert store.pending_agui_calls(context_id, "run-1") == []
    # Abandoned calls no longer block the next acquire.
    follow = _acquire(store, setup, run_id="run-2", body="hash-2")
    assert follow["state"] == "reserved"
    # And a late result for them is a mismatch, never consumable.
    with pytest.raises(AguiParentMismatch):
        store.consume_agui_calls(context_id, "run-1", ["call-a"])


# ── outbox claim / ack crash matrix ──────────────────────────────────

def test_claim_ack_happy_path(store, setup):
    admission = _acquire(store, setup, payload_json='{"turn":1}')
    claimed = store.claim_agui_dispatch("d1")
    assert claimed["run_id"] == "run-1"
    assert claimed["payload_json"] == '{"turn":1}'
    assert store.ack_agui_dispatch(admission["context_id"], "run-1", "d1")
    after = store.get_agui_admission(admission["context_id"], "run-1")
    assert after["state"] == "accepted"
    assert after["payload_json"] == ""  # cleared on durable acceptance


def test_claim_is_exclusive_until_deadline(store, setup):
    _acquire(store, setup)
    assert store.claim_agui_dispatch("d1", deadline_seconds=60) is not None
    assert store.claim_agui_dispatch("d2") is None


def test_expired_claim_is_reclaimed_by_cas(store, setup):
    """Crash after claim, before ack: the deadline frees the row."""
    admission = _acquire(store, setup)
    assert store.claim_agui_dispatch("d1", deadline_seconds=1,
                                     now=1000.0) is not None
    reclaimed = store.claim_agui_dispatch("d2", now=2000.0)
    assert reclaimed is not None and reclaimed["claim_owner"] == "d2"
    # The dead owner's late ack is refused; the new owner's works.
    assert not store.ack_agui_dispatch(admission["context_id"], "run-1", "d1")
    assert store.ack_agui_dispatch(admission["context_id"], "run-1", "d2")


def test_ack_is_idempotent_dedupe_answer(store, setup):
    """Crash after ack: a re-claimer can complete the lost ack."""
    admission = _acquire(store, setup)
    store.claim_agui_dispatch("d1")
    assert store.ack_agui_dispatch(admission["context_id"], "run-1", "d1")
    # Any later ack (same or different owner) answers True, changes nothing.
    assert store.ack_agui_dispatch(admission["context_id"], "run-1", "d1")
    assert store.ack_agui_dispatch(admission["context_id"], "run-1", "d2")
    assert store.get_agui_admission(
        admission["context_id"], "run-1")["state"] == "accepted"


def test_accepted_rows_are_not_reclaimable(store, setup):
    admission = _acquire(store, setup)
    store.claim_agui_dispatch("d1")
    store.ack_agui_dispatch(admission["context_id"], "run-1", "d1")
    assert store.claim_agui_dispatch("d2", now=10**12) is None


# ── running lease / fencing / orphaning ──────────────────────────────

def test_start_requires_accepted_state(store, setup):
    admission = _acquire(store, setup)
    with pytest.raises(AguiFenceLost):
        store.start_agui_turn(admission["context_id"], "run-1", "w1")


def test_heartbeat_only_for_the_lease_owner(store, setup):
    admission = _acquire(store, setup)
    store.claim_agui_dispatch("d1")
    store.ack_agui_dispatch(admission["context_id"], "run-1", "d1")
    fence = store.start_agui_turn(admission["context_id"], "run-1", "w1")
    assert fence == admission["fence_token"]
    assert store.check_agui_fence(admission["context_id"], fence)
    assert store.heartbeat_agui_turn(admission["context_id"], "run-1", "w1")
    assert not store.heartbeat_agui_turn(admission["context_id"], "run-1",
                                         "zombie")


def test_finish_by_wrong_worker_is_fence_lost(store, setup):
    admission = _acquire(store, setup)
    store.claim_agui_dispatch("d1")
    store.ack_agui_dispatch(admission["context_id"], "run-1", "d1")
    store.start_agui_turn(admission["context_id"], "run-1", "w1")
    with pytest.raises(AguiFenceLost):
        store.finish_agui_turn(admission["context_id"], "run-1", "zombie",
                               "success")
    # The legitimate worker still owns the run.
    store.finish_agui_turn(admission["context_id"], "run-1", "w1", "success")


def test_new_fence_invalidates_the_old_token(store, setup):
    first = _acquire(store, setup)
    _run_to_terminal(store, first)
    second = _acquire(store, setup, run_id="run-2", body="hash-2")
    assert not store.check_agui_fence(first["context_id"],
                                      first["fence_token"])
    assert store.check_agui_fence(second["context_id"],
                                  second["fence_token"])


def test_orphaning_journals_run_lost_and_abandons_calls(store, setup):
    admission = _acquire(store, setup)
    context_id = admission["context_id"]
    store.claim_agui_dispatch("d1")
    store.ack_agui_dispatch(context_id, "run-1", "d1")
    store.start_agui_turn(context_id, "run-1", "w1", now=1000.0)
    store.record_agui_call(context_id, "run-1", "call-a")
    # Heartbeat stale beyond the timeout → orphaned, never relaunched.
    orphaned = store.orphan_expired_agui_turns(
        heartbeat_timeout_seconds=120.0, now=2000.0)
    assert orphaned == 1
    admission_after = store.get_agui_admission(context_id, "run-1")
    assert admission_after["state"] == "orphaned"
    assert admission_after["outcome"] == "run_lost"
    run = store.get_agui_run(context_id, "run-1")
    assert run["state"] == "terminal" and run["outcome"] == "run_lost"
    events = store.read_agui_events(context_id, "run-1")
    assert "run_lost" in events[-1]["event_json"]
    assert store.pending_agui_calls(context_id, "run-1") == []
    # The journal refuses further appends on the lost run.
    with pytest.raises(AguiRunTerminal):
        store.append_agui_event(context_id, "run-1", '{"n":1}')
    # And the thread is free again.
    follow = _acquire(store, setup, run_id="run-2", body="hash-2")
    assert follow["state"] == "reserved"


def test_fresh_heartbeat_is_not_orphaned(store, setup):
    admission = _acquire(store, setup)
    store.claim_agui_dispatch("d1")
    store.ack_agui_dispatch(admission["context_id"], "run-1", "d1")
    store.start_agui_turn(admission["context_id"], "run-1", "w1", now=1000.0)
    store.heartbeat_agui_turn(admission["context_id"], "run-1", "w1",
                              now=1990.0)
    assert store.orphan_expired_agui_turns(
        heartbeat_timeout_seconds=120.0, now=2000.0) == 0


# ── rotation interplay ───────────────────────────────────────────────

def test_rotation_drops_admissions_and_fences(store, setup):
    publication, key_id = setup
    admission = _acquire(store, setup)
    _run_to_terminal(store, admission)
    store.rotate_agui_thread(publication, key_id, "t-1",
                             expected_generation=0,
                             delete_conversation=lambda _c: None)
    assert store.get_agui_admission(admission["context_id"], "run-1") is None
    with store._connect() as connection:
        fences = connection.execute(
            "SELECT COUNT(*) FROM agui_fences WHERE context_id=?",
            (admission["context_id"],)).fetchone()[0]
    assert fences == 0


# ── review-required adversarial cases (P1-C round 2) ─────────────────

def test_rotation_racing_acquire_reads_as_thread_rotated(store, setup):
    """A rotation landing between resolve and the admission transaction
    must surface as thread_rotated — never an FK IntegrityError."""
    publication, key_id = setup
    original = store.resolve_agui_thread

    def racy_resolve(pub, key, thread, generation, **kwargs):
        context = original(pub, key, thread, generation, **kwargs)
        store.rotate_agui_thread(pub, key, thread,
                                 expected_generation=generation,
                                 delete_conversation=lambda _c: None)
        return context

    store.resolve_agui_thread = racy_resolve
    try:
        with pytest.raises(AguiThreadRotated) as excinfo:
            _acquire(store, setup)
        assert excinfo.value.current_generation == 1
    finally:
        del store.resolve_agui_thread


def test_finish_synchronizes_journal_and_counters(store, setup):
    admission = _acquire(store, setup)
    context_id = admission["context_id"]
    store.claim_agui_dispatch("d")
    store.ack_agui_dispatch(context_id, "run-1", "d")
    store.start_agui_turn(context_id, "run-1", "w1")
    store.append_agui_event(context_id, "run-1", '{"n":1}')
    store.finish_agui_turn(context_id, "run-1", "w1", "success")
    run = store.get_agui_run(context_id, "run-1")
    assert run["state"] == "terminal" and run["outcome"] == "success"
    events = store.read_agui_events(context_id, "run-1")
    assert "RUN_FINISHED" in events[-1]["event_json"]
    assert run["event_count"] == len(events) == 2
    assert run["byte_count"] == sum(
        len(event["event_json"].encode("utf-8")) for event in events)


def test_finish_reconciles_an_already_terminal_journal(store, setup):
    admission = _acquire(store, setup)
    context_id = admission["context_id"]
    store.claim_agui_dispatch("d")
    store.ack_agui_dispatch(context_id, "run-1", "d")
    store.start_agui_turn(context_id, "run-1", "w1")
    store.append_agui_event(context_id, "run-1", '{"type":"RUN_FINISHED"}',
                            terminal=True, outcome="success")
    store.finish_agui_turn(context_id, "run-1", "w1", "success")
    run = store.get_agui_run(context_id, "run-1")
    assert run["event_count"] == 1  # no duplicate terminal event
    assert store.get_agui_admission(
        context_id, "run-1")["state"] == "terminal"


def test_orphan_reconciles_a_completed_journal(store, setup):
    """Worker dies AFTER the journal recorded success: the admission
    adopts the journal outcome — never orphaned/run_lost over it."""
    admission = _acquire(store, setup)
    context_id = admission["context_id"]
    store.claim_agui_dispatch("d")
    store.ack_agui_dispatch(context_id, "run-1", "d")
    store.start_agui_turn(context_id, "run-1", "w1", now=1000.0)
    store.record_agui_call(context_id, "run-1", "call-a")
    store.append_agui_event(context_id, "run-1", '{"type":"RUN_FINISHED"}',
                            terminal=True, outcome="success")
    assert store.orphan_expired_agui_turns(
        heartbeat_timeout_seconds=120.0, now=2000.0) == 1
    after = store.get_agui_admission(context_id, "run-1")
    assert after["state"] == "terminal" and after["outcome"] == "success"
    events = store.read_agui_events(context_id, "run-1")
    assert all("run_lost" not in event["event_json"] for event in events)
    # A successful outcome keeps the emitted calls pending.
    assert store.pending_agui_calls(context_id, "run-1") == ["call-a"]


def test_fence_invalidated_on_finish_and_orphan(store, setup):
    admission = _acquire(store, setup)
    context_id = admission["context_id"]
    _run_to_terminal(store, admission)
    assert not store.check_agui_fence(context_id, admission["fence_token"])
    second = _acquire(store, setup, run_id="run-2", body="hash-2")
    store.claim_agui_dispatch("d")
    store.ack_agui_dispatch(context_id, "run-2", "d")
    store.start_agui_turn(context_id, "run-2", "w1", now=1000.0)
    assert store.check_agui_fence(context_id, second["fence_token"])
    store.orphan_expired_agui_turns(heartbeat_timeout_seconds=120.0,
                                    now=2000.0)
    assert not store.check_agui_fence(context_id, second["fence_token"])


def test_delete_publication_drops_fences(store, setup):
    publication, _ = setup
    admission = _acquire(store, setup)
    assert store.delete_publication(publication["publication_id"])
    with store._connect() as connection:
        fences = connection.execute(
            "SELECT COUNT(*) FROM agui_fences").fetchone()[0]
    assert fences == 0
    assert not store.check_agui_fence(admission["context_id"],
                                      admission["fence_token"])


def test_rotate_refused_under_active_admission(store, setup):
    publication, key_id = setup
    admission = _acquire(store, setup)
    with pytest.raises(AguiTurnBusy):
        store.rotate_agui_thread(publication, key_id, "t-1",
                                 expected_generation=0,
                                 delete_conversation=lambda _c: None)
    _run_to_terminal(store, admission)
    handle = store.rotate_agui_thread(publication, key_id, "t-1",
                                      expected_generation=0,
                                      delete_conversation=lambda _c: None)
    assert handle["generation"] == 1


def test_run_lost_event_counts_bytes(store, setup):
    admission = _acquire(store, setup)
    context_id = admission["context_id"]
    store.claim_agui_dispatch("d")
    store.ack_agui_dispatch(context_id, "run-1", "d")
    store.start_agui_turn(context_id, "run-1", "w1", now=1000.0)
    store.orphan_expired_agui_turns(heartbeat_timeout_seconds=120.0,
                                    now=2000.0)
    run = store.get_agui_run(context_id, "run-1")
    assert run["event_count"] == 1
    assert run["byte_count"] > 0
    events = store.read_agui_events(context_id, "run-1")
    assert run["byte_count"] == len(
        events[0]["event_json"].encode("utf-8"))


def test_record_call_refused_after_terminal(store, setup):
    admission = _acquire(store, setup)
    context_id = admission["context_id"]
    store.claim_agui_dispatch("d")
    store.ack_agui_dispatch(context_id, "run-1", "d")
    store.start_agui_turn(context_id, "run-1", "w1")
    store.finish_agui_turn(context_id, "run-1", "w1", "error")
    from core._a2a_turn_journal import AguiRunTerminal
    with pytest.raises(AguiRunTerminal):
        store.record_agui_call(context_id, "run-1", "late-call")
    # The abandonment stays final: nothing pending, thread free.
    assert store.pending_agui_calls(context_id, "run-1") == []
    assert _acquire(store, setup, run_id="run-2",
                    body="hash-2")["state"] == "reserved"


def test_ack_on_orphaned_is_idempotent_true(store, setup):
    admission = _acquire(store, setup)
    context_id = admission["context_id"]
    store.claim_agui_dispatch("d")
    store.ack_agui_dispatch(context_id, "run-1", "d")
    store.start_agui_turn(context_id, "run-1", "w1", now=1000.0)
    store.orphan_expired_agui_turns(heartbeat_timeout_seconds=120.0,
                                    now=2000.0)
    assert store.ack_agui_dispatch(context_id, "run-1", "d")
    assert store.ack_agui_dispatch(context_id, "run-1", "other")


# ── review-required adversarial cases (P1-C round 3) ─────────────────

def test_finish_adopts_journal_outcome_on_mismatch(store, setup):
    """Journal already terminal with a DIFFERENT outcome: the admission
    and the abandonment follow the JOURNAL, not the worker's claim."""
    admission = _acquire(store, setup)
    context_id = admission["context_id"]
    _start(store, admission)
    store.record_agui_call(context_id, "run-1", "call-a")
    store.append_agui_event(context_id, "run-1", '{"type":"RUN_ERROR"}',
                            terminal=True, outcome="run_quota_exceeded")
    store.finish_agui_turn(context_id, "run-1", "w1", "success")
    after = store.get_agui_admission(context_id, "run-1")
    assert after["outcome"] == "run_quota_exceeded"
    # Non-success effective outcome → the pending call was abandoned.
    assert store.pending_agui_calls(context_id, "run-1") == []


def test_finish_fails_closed_without_journal_row(store, setup):
    from core._a2a_turn_journal import AguiRunUnknown
    admission = _acquire(store, setup)
    context_id = admission["context_id"]
    _start(store, admission)
    with store._connect() as connection:
        connection.execute(
            "DELETE FROM agui_runs WHERE context_id=? AND run_id='run-1'",
            (context_id,))
    with pytest.raises(AguiRunUnknown):
        store.finish_agui_turn(context_id, "run-1", "w1", "success")


def test_fence_migration_preserves_tokens(store, setup, tmp_path):
    """v1→v2 migration keeps the high-water: no fence ABA after upgrade."""
    import sqlite3
    admission = _acquire(store, setup)
    context_id = admission["context_id"]
    _run_to_terminal(store, admission)  # token now 2 (acquire + finish)
    with store._connect() as connection:
        token_before = connection.execute(
            "SELECT token FROM agui_fences WHERE context_id=?",
            (context_id,)).fetchone()[0]
    # Downgrade to the FK-less v1 shape, keeping the token value.
    raw = sqlite3.connect(tmp_path / "a2a.sqlite3")
    raw.execute("DROP TABLE agui_fences")
    raw.execute("CREATE TABLE agui_fences (context_id TEXT PRIMARY KEY, "
                "token INTEGER NOT NULL DEFAULT 0)")
    raw.execute("INSERT INTO agui_fences VALUES (?, ?)",
                (context_id, token_before))
    raw.commit()
    raw.close()
    migrated = A2AStore(database_path=tmp_path / "a2a.sqlite3")
    with migrated._connect() as connection:
        assert connection.execute(
            "PRAGMA foreign_key_list(agui_fences)").fetchall()
        token_after = connection.execute(
            "SELECT token FROM agui_fences WHERE context_id=?",
            (context_id,)).fetchone()[0]
    assert token_after == token_before
    # A new admission continues the monotonic sequence — the old token
    # can never become valid again.
    publication, key_id = setup
    fresh = migrated.acquire_agui_turn(publication, key_id, "t-1", 0,
                                       "run-2", "hash-2")
    assert fresh["fence_token"] == token_before + 1
    assert not migrated.check_agui_fence(context_id,
                                         admission["fence_token"])


def test_fence_migration_recovers_from_interruption(store, setup, tmp_path):
    """Crash after the intermediate CREATE (residual table on disk):
    the next startup cleans it and completes the migration — no
    'table already exists', token preserved, no ABA."""
    import sqlite3
    admission = _acquire(store, setup)
    context_id = admission["context_id"]
    _run_to_terminal(store, admission)
    with store._connect() as connection:
        token_before = connection.execute(
            "SELECT token FROM agui_fences WHERE context_id=?",
            (context_id,)).fetchone()[0]
    raw = sqlite3.connect(tmp_path / "a2a.sqlite3")
    raw.execute("DROP TABLE agui_fences")
    raw.execute("CREATE TABLE agui_fences (context_id TEXT PRIMARY KEY, "
                "token INTEGER NOT NULL DEFAULT 0)")
    raw.execute("INSERT INTO agui_fences VALUES (?, ?)",
                (context_id, token_before))
    # Simulate the interrupted migration: the intermediate table was
    # created (and autocommitted by the pre-fix code) before the crash.
    raw.execute("CREATE TABLE agui_fences_migrated ("
                "context_id TEXT PRIMARY KEY, token INTEGER NOT NULL)")
    raw.commit()
    raw.close()
    recovered = A2AStore(database_path=tmp_path / "a2a.sqlite3")
    with recovered._connect() as connection:
        assert connection.execute(
            "PRAGMA foreign_key_list(agui_fences)").fetchall()
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='agui_fences_migrated'"
        ).fetchone() is None
        token_after = connection.execute(
            "SELECT token FROM agui_fences WHERE context_id=?",
            (context_id,)).fetchone()[0]
    assert token_after == token_before
    assert not recovered.check_agui_fence(context_id,
                                          admission["fence_token"])


def test_rotation_fails_closed_on_broken_admission_table(store, setup):
    import sqlite3
    publication, key_id = setup
    store.resolve_agui_thread(publication, key_id, "t-1", 0)
    with store._connect() as connection:
        connection.execute(
            "ALTER TABLE agui_admissions RENAME TO agui_admissions_broken")
    try:
        with pytest.raises(sqlite3.OperationalError):
            store.rotate_agui_thread(publication, key_id, "t-1",
                                     expected_generation=0,
                                     delete_conversation=lambda _c: None)
    finally:
        with store._connect() as connection:
            connection.execute(
                "ALTER TABLE agui_admissions_broken RENAME TO agui_admissions")
    # The rotation did NOT proceed blind: generation unchanged.
    assert store.ensure_agui_thread(publication, key_id,
                                    "t-1")["generation"] == 0


def test_record_call_requires_running_state(store, setup):
    from core._a2a_turn_journal import AguiRunTerminal
    admission = _acquire(store, setup)
    context_id = admission["context_id"]
    # reserved (before dispatch/start): refused.
    with pytest.raises(AguiRunTerminal):
        store.record_agui_call(context_id, "run-1", "call-before-dispatch")
    assert store.pending_agui_calls(context_id, "run-1") == []
    _start(store, admission)
    store.record_agui_call(context_id, "run-1", "call-a")
    assert store.pending_agui_calls(context_id, "run-1") == ["call-a"]


def test_body_hash_helper_is_canonical(store):
    assert store.agui_body_hash({"b": 1, "a": [2, 3]}) == \
        store.agui_body_hash({"a": [2, 3], "b": 1})
    assert store.agui_body_hash({"a": 1}) != store.agui_body_hash({"a": 2})
