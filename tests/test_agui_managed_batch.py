"""Managed frontend-execution batches + v8.2 tokens (plan, step P1-D).

Covers the B0 token scheme (derived, pinned key version, usage
separation, uniform failure), B1-X (T-freeze in the finish transaction,
idempotent generation-bound claim, two deadlines, begin/deposit state
matrix, races, T-complete) and B1-L (frontend-execution lease pinning
the thread against new admissions and rotation).
"""

import json

import pytest

from core.a2a_store import A2AStore
from core._a2a_turn_acquire import AguiParentMismatch, AguiTurnBusy
from core._a2a_turn_batch import (
    AguiBatchClaimed,
    AguiBatchIncomplete,
    AguiCatalogueRejected,
    AguiClaimExpired,
    AguiDepositRejected,
    AguiReceiptConflict,
    AguiTokenInvalid,
)
from core._a2a_turn_journal import AguiManagedSuccessWithoutBatch


@pytest.fixture()
def store(tmp_path):
    return A2AStore(database_path=tmp_path / "a2a.sqlite3")


@pytest.fixture()
def setup(store):
    publication = store.configure_publication(
        "owner", "conv-1", "helper", managed_mode=True)
    _, key = store.create_key(publication["publication_id"], "client")
    return publication, key["key_id"]


def _managed_run(store, setup, run_id="run-1", body="hash-1",
                 calls=("call-a", "call-b"), deadline=600.0,
                 finish_now=None, thread="t-1", **kwargs):
    publication, key_id = setup
    admission = store.acquire_agui_turn(publication, key_id, thread, 0,
                                        run_id, body, **kwargs)
    context_id = admission["context_id"]
    assert store.claim_agui_dispatch("dispatcher") is not None
    assert store.ack_agui_dispatch(context_id, run_id, "dispatcher")
    store.start_agui_turn(context_id, run_id, "w1")
    for call in calls:
        store.record_agui_call(context_id, run_id, call, "page_tool",
                               catalogue_id="host:page_tool",
                               catalogue_version="cv-1")
    store.finish_agui_turn(context_id, run_id, "w1", "success",
                           batch_deadline_seconds=deadline, now=finish_now)
    return context_id, run_id


def _claimed(store, context_id, run_id, claim_id="cl-1", **kwargs):
    token = store.batch_token_for(context_id, run_id)
    return store.claim_agui_batch(token, claim_id, **kwargs)


def _begin(store, receipt, **kwargs):
    """Begin presenting the helper's full live catalogue identity."""
    return store.begin_agui_call(receipt, catalogue_id="host:page_tool",
                                 catalogue_version="cv-1", **kwargs)


# ── T-freeze & frontend lease ────────────────────────────────────────

def test_managed_success_freezes_the_batch(store, setup):
    context_id, run_id = _managed_run(store, setup)
    batch = store.get_agui_batch(context_id, run_id)
    assert batch["state"] == "frozen"
    assert batch["handle"].startswith("bh")
    assert batch["absolute_deadline"] > 0
    assert store.has_frontend_execution_lease(context_id)


def test_batch_token_is_byte_identical_on_rederivation(store, setup):
    context_id, run_id = _managed_run(store, setup)
    assert store.batch_token_for(context_id, run_id) == \
        store.batch_token_for(context_id, run_id)


def test_classic_finish_never_freezes(store):
    publication = store.configure_publication("owner", "conv-1", "helper")
    assert publication["managed_mode"] is False
    _, key = store.create_key(publication["publication_id"], "client")
    admission = store.acquire_agui_turn(publication, key["key_id"], "t-1",
                                        0, "run-1", "hash-1")
    context_id = admission["context_id"]
    store.claim_agui_dispatch("d")
    store.ack_agui_dispatch(context_id, "run-1", "d")
    store.start_agui_turn(context_id, "run-1", "w1")
    store.record_agui_call(context_id, "run-1", "call-a")
    store.finish_agui_turn(context_id, "run-1", "w1", "success")
    assert store.get_agui_batch(context_id, "run-1") is None
    assert not store.has_frontend_execution_lease(context_id)


def test_managed_finish_without_calls_never_freezes(store, setup):
    context_id, run_id = _managed_run(store, setup, calls=())
    assert store.get_agui_batch(context_id, run_id) is None


def test_open_batch_blocks_new_admissions_and_rotation(store, setup):
    publication, key_id = setup
    context_id, run_id = _managed_run(store, setup)
    with pytest.raises(AguiBatchIncomplete):
        store.acquire_agui_turn(publication, key_id, "t-1", 0, "run-2",
                                "hash-2", parent_run_id=run_id)
    with pytest.raises(AguiTurnBusy):
        store.rotate_agui_thread(publication, key_id, "t-1",
                                 expected_generation=0,
                                 delete_conversation=lambda _c: None)


# ── claim / renew ────────────────────────────────────────────────────

def test_claim_is_idempotent_per_claim_id(store, setup):
    context_id, run_id = _managed_run(store, setup)
    first = _claimed(store, context_id, run_id)
    replay = _claimed(store, context_id, run_id)
    assert replay["owner_token"] == first["owner_token"]
    assert replay["receipts"] == first["receipts"]
    assert len(first["receipts"]) == 2
    assert first["claim_generation"] == 1


def test_second_claim_id_is_refused_while_reservation_lives(store, setup):
    context_id, run_id = _managed_run(store, setup)
    _claimed(store, context_id, run_id, "cl-1")
    with pytest.raises(AguiBatchClaimed):
        _claimed(store, context_id, run_id, "cl-2")


def test_expired_preeffect_claim_invalidates_the_generation(store, setup):
    context_id, run_id = _managed_run(store, setup)
    stale = _claimed(store, context_id, run_id, "cl-1",
                     lease_seconds=1, now=1000.0)
    fresh = _claimed(store, context_id, run_id, "cl-2", now=2000.0)
    # The observed expiry itself bumped the generation, the fresh claim
    # bumped it again.
    assert fresh["claim_generation"] == 3
    # Every credential of the expired claim answers claim_expired.
    with pytest.raises(AguiClaimExpired):
        store.begin_agui_call(stale["receipts"][0]["receipt"], now=2000.0)
    with pytest.raises(AguiClaimExpired):
        store.renew_agui_batch(stale["owner_token"], now=2000.0)


def test_renew_extends_preeffect_and_noops_after_commit(store, setup):
    context_id, run_id = _managed_run(store, setup, deadline=600.0)
    claim = _claimed(store, context_id, run_id, lease_seconds=60,
                     now=1000.0)
    assert store.renew_agui_batch(claim["owner_token"], lease_seconds=60,
                                  now=1030.0)
    batch = store.get_agui_batch(context_id, run_id)
    assert batch["claim_lease_deadline"] == pytest.approx(1090.0)
    # Never beyond the absolute deadline.
    assert store.renew_agui_batch(claim["owner_token"],
                                  lease_seconds=10**6, now=1030.0)
    batch = store.get_agui_batch(context_id, run_id)
    assert batch["claim_lease_deadline"] <= batch["absolute_deadline"]
    _begin(store, claim["receipts"][0]["receipt"], now=1040.0)
    # After execution_committed the lease is ignored: idempotent no-op.
    deadline_before = store.get_agui_batch(context_id,
                                           run_id)["claim_lease_deadline"]
    assert store.renew_agui_batch(claim["owner_token"], now=1050.0)
    assert store.get_agui_batch(
        context_id, run_id)["claim_lease_deadline"] == deadline_before


# ── token integrity ──────────────────────────────────────────────────

def test_malformed_and_tampered_tokens_are_uniformly_invalid(store, setup):
    context_id, run_id = _managed_run(store, setup)
    token = store.batch_token_for(context_id, run_id)
    with pytest.raises(AguiTokenInvalid):
        store.claim_agui_batch("garbage", "cl-1")
    version, handle, mac = token.split(".")
    with pytest.raises(AguiTokenInvalid):
        store.claim_agui_batch(f"v99.{handle}.{mac}", "cl-1")
    with pytest.raises(AguiTokenInvalid):
        store.claim_agui_batch(f"{version}.{handle}.{'0' * 32}", "cl-1")


def test_cross_usage_token_replay_is_rejected(store, setup):
    context_id, run_id = _managed_run(store, setup)
    batch_token = store.batch_token_for(context_id, run_id)
    # A batch token is not an owner token.
    with pytest.raises((AguiClaimExpired, AguiTokenInvalid)):
        store.renew_agui_batch(batch_token)


def test_key_rotation_keeps_reissued_tokens_byte_identical(store, setup):
    import time as time_module
    context_id, run_id = _managed_run(store, setup)
    before = store.batch_token_for(context_id, run_id)
    with store._connect() as connection:
        connection.execute(
            "INSERT INTO agui_token_keys (version, secret, created_at) "
            "VALUES (2, 'newsecret', ?)", (time_module.time(),))
    # Pinned key version: same bytes after rotation.
    assert store.batch_token_for(context_id, run_id) == before


def test_missing_pinned_key_fails_closed(store, setup):
    context_id, run_id = _managed_run(store, setup)
    with store._connect() as connection:
        connection.execute("DELETE FROM agui_token_keys")
    with pytest.raises(RuntimeError, match="failing closed"):
        store.batch_token_for(context_id, run_id)


# ── begin / deposit matrix ───────────────────────────────────────────

def test_first_begin_commits_the_claim(store, setup):
    context_id, run_id = _managed_run(store, setup)
    claim = _claimed(store, context_id, run_id)
    receipt = claim["receipts"][0]["receipt"]
    assert _begin(store, receipt)
    assert store.get_agui_batch(context_id,
                                run_id)["state"] == "execution_committed"
    assert _begin(store, receipt)  # idempotent


def test_deposit_matrix_is_closed_per_state(store, setup):
    context_id, run_id = _managed_run(store, setup)
    claim = _claimed(store, context_id, run_id)
    receipt_a, receipt_b = [item["receipt"] for item in claim["receipts"]]
    # Not begun: an effect-claiming outcome is rejected…
    with pytest.raises(AguiDepositRejected):
        store.deposit_agui_call(receipt_a, "result", '{"ok":1}')
    # …a no-effect outcome is accepted.
    store.deposit_agui_call(receipt_a, "denied")
    # Begun: no-effect outcomes are rejected, effects accepted.
    _begin(store, receipt_b)
    with pytest.raises(AguiDepositRejected):
        store.deposit_agui_call(receipt_b, "denied")
    outcome = store.deposit_agui_call(receipt_b, "result", '{"ok":2}')
    assert outcome["batch_state"] == "complete"


def test_duplicate_deposit_replays_and_conflict_is_rejected(store, setup):
    context_id, run_id = _managed_run(store, setup, calls=("call-a",
                                                           "call-b"))
    claim = _claimed(store, context_id, run_id)
    receipt_a = claim["receipts"][0]["receipt"]
    _begin(store, receipt_a)
    store.deposit_agui_call(receipt_a, "result", '{"ok":1}')
    replay = store.deposit_agui_call(receipt_a, "result", '{"ok":1}')
    assert replay["replay"] is True
    with pytest.raises(AguiReceiptConflict):
        store.deposit_agui_call(receipt_a, "result", '{"ok":OTHER}')


def test_last_deposit_completes_and_releases_the_lease(store, setup):
    publication, key_id = setup
    context_id, run_id = _managed_run(store, setup)
    claim = _claimed(store, context_id, run_id)
    for item in claim["receipts"]:
        _begin(store, item["receipt"])
        store.deposit_agui_call(item["receipt"], "result", '{"ok":true}')
    assert store.get_agui_batch(context_id, run_id)["state"] == "complete"
    assert not store.has_frontend_execution_lease(context_id)


def test_deposit_after_complete_replays_terminal_without_mutation(store, setup):
    context_id, run_id = _managed_run(store, setup, calls=("call-a",))
    claim = _claimed(store, context_id, run_id)
    receipt = claim["receipts"][0]["receipt"]
    _begin(store, receipt)
    store.deposit_agui_call(receipt, "result", '{"ok":1}')
    late = store.deposit_agui_call(receipt, "error", '{"boom":1}')
    assert late["replay"] is True
    assert late["kind"] == "result"
    assert late["payload_json"] == '{"ok":1}'
    results = store.batch_results(context_id, run_id)
    assert results[0]["result_kind"] == "result"


# ── deadlines ────────────────────────────────────────────────────────

def test_absolute_deadline_terminalizes_and_completes(store, setup):
    context_id, run_id = _managed_run(store, setup, deadline=100.0,
                                      finish_now=1.0)
    claim = _claimed(store, context_id, run_id, now=1.0,
                     lease_seconds=10**6)
    receipt_a, receipt_b = [item["receipt"] for item in claim["receipts"]]
    _begin(store, receipt_a, now=2.0)
    # call-a executing, call-b never begun; the absolute deadline fires.
    assert store.expire_agui_batches(now=10**6) == 1
    batch = store.get_agui_batch(context_id, run_id)
    assert batch["state"] == "complete"
    results = {item["tool_call_id"]: item["result_kind"]
               for item in store.batch_results(context_id, run_id)}
    assert results == {"call-a": "indeterminate", "call-b": "abandoned"}
    assert not store.has_frontend_execution_lease(context_id)


def test_begin_after_deadline_completion_is_refused(store, setup):
    context_id, run_id = _managed_run(store, setup, deadline=100.0,
                                      finish_now=1.0)
    claim = _claimed(store, context_id, run_id, now=1.0)
    store.expire_agui_batches(now=10**6)
    with pytest.raises(AguiClaimExpired):
        store.begin_agui_call(claim["receipts"][0]["receipt"], now=10**6)


# ── follow-up consumption (B1-A managed branch) ──────────────────────

def test_followup_consumes_the_completed_batch(store, setup):
    publication, key_id = setup
    context_id, run_id = _managed_run(store, setup)
    claim = _claimed(store, context_id, run_id)
    for item in claim["receipts"]:
        _begin(store, item["receipt"])
        store.deposit_agui_call(item["receipt"], "result", '{"ok":1}')
    # Wrong/missing parent is a mismatch.
    with pytest.raises(AguiParentMismatch):
        store.acquire_agui_turn(publication, key_id, "t-1", 0, "run-2",
                                "hash-2")
    follow = store.acquire_agui_turn(publication, key_id, "t-1", 0,
                                     "run-2", "hash-2",
                                     parent_run_id=run_id)
    assert follow["state"] == "reserved"
    assert store.get_agui_batch(context_id, run_id)["state"] == "consumed"
    # Consumed exactly once: a second follow-up needs no parent and the
    # calls are no longer pending anywhere.
    with store._connect() as connection:
        emitted = connection.execute(
            "SELECT COUNT(*) FROM agui_calls WHERE state='emitted'"
        ).fetchone()[0]
    assert emitted == 0


# ── publication mode ─────────────────────────────────────────────────

def test_managed_mode_is_persisted_and_preserved_on_update(store, setup):
    publication, _ = setup
    assert publication["managed_mode"] is True
    updated = store.configure_publication("owner", "conv-1", "helper",
                                          label="renamed")
    assert updated["managed_mode"] is True
    reset = store.configure_publication("owner", "conv-1", "helper",
                                        managed_mode=False)
    assert reset["managed_mode"] is False


# ── review v1 probes: mode pinning & journal integrity ───────────────

def test_finish_reads_the_mode_from_the_publication(store, setup):
    publication, key_id = setup
    admission = store.acquire_agui_turn(publication, key_id, "t-1", 0,
                                        "run-1", "hash-1")
    context_id = admission["context_id"]
    store.claim_agui_dispatch("d")
    store.ack_agui_dispatch(context_id, "run-1", "d")
    store.start_agui_turn(context_id, "run-1", "w1")
    store.record_agui_call(context_id, "run-1", "call-a",
                           catalogue_id="host:page_tool",
                           catalogue_version="cv-1")
    # The request surface has NO mode switch any more.
    with pytest.raises(TypeError):
        store.finish_agui_turn(context_id, "run-1", "w1", "success",
                               managed=False)
    # Demoting the PUBLICATION (the only mode authority) disables the
    # freeze for the next finish.
    store.configure_publication("owner", "conv-1", "helper",
                                managed_mode=False)
    store.finish_agui_turn(context_id, "run-1", "w1", "success")
    assert store.get_agui_batch(context_id, "run-1") is None
    assert not store.has_frontend_execution_lease(context_id)


def test_run_finished_event_carries_the_batch_token(store, setup):
    context_id, run_id = _managed_run(store, setup)
    events = store.read_agui_events(context_id, run_id)
    terminal = json.loads(events[-1]["event_json"])
    assert terminal["type"] == "RUN_FINISHED"
    assert terminal["batch_token"] == store.batch_token_for(context_id,
                                                            run_id)


def test_journal_success_without_batch_is_refused_for_managed(store, setup):
    publication, key_id = setup
    admission = store.acquire_agui_turn(publication, key_id, "t-1", 0,
                                        "run-1", "hash-1")
    context_id = admission["context_id"]
    store.claim_agui_dispatch("d")
    store.ack_agui_dispatch(context_id, "run-1", "d")
    store.start_agui_turn(context_id, "run-1", "w1")
    store.record_agui_call(context_id, "run-1", "call-a",
                           catalogue_id="host:page_tool",
                           catalogue_version="cv-1")
    # T-freeze is the only path to a managed success with pending calls.
    with pytest.raises(AguiManagedSuccessWithoutBatch):
        store.append_agui_event(context_id, "run-1",
                                '{"type": "RUN_FINISHED"}',
                                terminal=True, outcome="success")
    run = store.get_agui_run(context_id, "run-1")
    assert run["state"] == "active"
    # The legitimate path still freezes.
    store.finish_agui_turn(context_id, "run-1", "w1", "success")
    assert store.get_agui_batch(context_id, "run-1")["state"] == "frozen"


def test_journal_success_without_batch_stays_allowed_for_classic(store):
    publication = store.configure_publication("owner", "conv-1", "helper")
    _, key = store.create_key(publication["publication_id"], "client")
    admission = store.acquire_agui_turn(publication, key["key_id"], "t-1",
                                        0, "run-1", "hash-1")
    context_id = admission["context_id"]
    store.claim_agui_dispatch("d")
    store.ack_agui_dispatch(context_id, "run-1", "d")
    store.start_agui_turn(context_id, "run-1", "w1")
    store.record_agui_call(context_id, "run-1", "call-a")
    store.append_agui_event(context_id, "run-1", '{"type": "RUN_FINISHED"}',
                            terminal=True, outcome="success")
    assert store.get_agui_run(context_id, "run-1")["state"] == "terminal"


# ── review v1 probes: claim resurrection ─────────────────────────────

def test_renew_cannot_resurrect_an_expired_lease(store, setup):
    context_id, run_id = _managed_run(store, setup, deadline=600.0,
                                      finish_now=1.0)
    claim = _claimed(store, context_id, run_id, "cl-1", lease_seconds=10,
                     now=1.0)
    with pytest.raises(AguiClaimExpired):
        store.renew_agui_batch(claim["owner_token"], lease_seconds=60,
                               now=100.0)
    # The old owner cannot begin either — the claim is dead.
    with pytest.raises(AguiClaimExpired):
        store.begin_agui_call(claim["receipts"][0]["receipt"], now=100.0)
    # A fresh claim id takes over with a new generation.
    fresh = _claimed(store, context_id, run_id, "cl-2", now=100.0)
    assert fresh["claim_generation"] == 3


def test_expired_claim_id_is_terminal_forever(store, setup):
    context_id, run_id = _managed_run(store, setup, deadline=600.0,
                                      finish_now=1.0)
    _claimed(store, context_id, run_id, "cl-1", lease_seconds=10, now=1.0)
    # cl-2 records cl-1's expiry durably while taking over.
    _claimed(store, context_id, run_id, "cl-2", lease_seconds=10, now=50.0)
    # cl-2 expires too; cl-1 must NOT be resurrectable.
    with pytest.raises(AguiClaimExpired):
        _claimed(store, context_id, run_id, "cl-1", now=100.0)
    fresh = _claimed(store, context_id, run_id, "cl-3", now=100.0)
    assert fresh["claim_generation"] == 5


def test_tampered_generation_tokens_are_uniformly_invalid(store, setup):
    context_id, run_id = _managed_run(store, setup)
    claim = _claimed(store, context_id, run_id)
    version, handle, _mac = claim["owner_token"].split(".")
    with pytest.raises(AguiTokenInvalid):
        store.renew_agui_batch(f"{version}.{handle}.{'0' * 32}")
    receipt = claim["receipts"][0]["receipt"]
    r_version, r_handle, _r_mac = receipt.split(".")
    with pytest.raises(AguiTokenInvalid):
        store.begin_agui_call(f"{r_version}.{r_handle}.{'0' * 32}")
    # Cross-usage on a KNOWN handle is invalid, never claim_expired.
    batch_token = store.batch_token_for(context_id, run_id)
    with pytest.raises(AguiTokenInvalid):
        store.renew_agui_batch(batch_token)


# ── review v1 probes: keyring referential safety ─────────────────────

def test_key_version_is_never_reused_after_keyring_wipe(store, setup):
    context_id, run_id = _managed_run(store, setup)
    assert store.get_agui_batch(context_id, run_id)["key_version"] == 1
    # Hostile wipe of batches AND keys: the durable high-water still
    # forbids re-minting version 1 with a different secret.
    with store._connect() as connection:
        connection.execute("DELETE FROM agui_batches")
        connection.execute("DELETE FROM agui_token_keys")
    context_id2, run_id2 = _managed_run(store, setup, run_id="run-2",
                                        body="hash-2", thread="t-2")
    assert store.get_agui_batch(context_id2, run_id2)["key_version"] == 2


def test_startup_audit_fails_closed_on_missing_referenced_key(
        store, setup, tmp_path):
    _managed_run(store, setup)
    with store._connect() as connection:
        connection.execute("DELETE FROM agui_token_keys")
    with pytest.raises(RuntimeError, match="failing closed"):
        A2AStore(database_path=tmp_path / "a2a.sqlite3")


def test_prune_never_deletes_referenced_or_current_keys(store, setup):
    context_id, run_id = _managed_run(store, setup)  # pins version 1
    with store._connect() as connection:
        connection.execute(
            "INSERT INTO agui_token_keys (version, secret, created_at) "
            "VALUES (2, 's2', 0), (3, 's3', 0)")
    # v1 referenced, v3 current → only v2 is prunable.
    assert store.prune_agui_token_keys() == 1
    with store._connect() as connection:
        versions = [row[0] for row in connection.execute(
            "SELECT version FROM agui_token_keys ORDER BY version")]
    assert versions == [1, 3]
    assert store.batch_token_for(context_id, run_id).startswith("v1.")


# ── review v1 probes: absolute deadline races ────────────────────────

def test_claim_never_opens_past_the_absolute_deadline(store, setup):
    context_id, run_id = _managed_run(store, setup, deadline=100.0,
                                      finish_now=1.0)
    with pytest.raises(AguiClaimExpired):
        _claimed(store, context_id, run_id, now=200.0)
    # The sweep still terminalizes the batch afterwards.
    assert store.expire_agui_batches(now=200.0) == 1
    assert store.get_agui_batch(context_id, run_id)["state"] == "complete"


def test_begin_never_wins_past_the_absolute_deadline(store, setup):
    context_id, run_id = _managed_run(store, setup, deadline=100.0,
                                      finish_now=1.0)
    claim = _claimed(store, context_id, run_id, now=1.0,
                     lease_seconds=10**6)
    with pytest.raises(AguiClaimExpired):
        store.begin_agui_call(claim["receipts"][0]["receipt"], now=200.0)
    results = {item["tool_call_id"]: item["result_kind"]
               for item in store.batch_results(context_id, run_id)}
    assert results["call-a"] == ""  # never executed


def test_sweep_isolates_batches_one_transaction_each(store, setup,
                                                     monkeypatch):
    _managed_run(store, setup, run_id="run-1", body="hash-1",
                 thread="t-1", deadline=100.0, finish_now=1.0)
    _managed_run(store, setup, run_id="run-2", body="hash-2",
                 thread="t-2", deadline=200.0, finish_now=1.0)
    original = A2AStore._expire_batch_in_tx
    seen = []

    def flaky(self, connection, context_id, run_id, now):
        if seen:
            raise RuntimeError("sweep crash on the second batch")
        seen.append(run_id)
        original(self, connection, context_id, run_id, now)

    monkeypatch.setattr(A2AStore, "_expire_batch_in_tx", flaky)
    with pytest.raises(RuntimeError):
        store.expire_agui_batches(now=10**6)
    monkeypatch.setattr(A2AStore, "_expire_batch_in_tx", original)
    # The first batch COMMITTED despite the crash on the second.
    states = set()
    with store._connect() as connection:
        for row in connection.execute("SELECT run_id, state FROM "
                                      "agui_batches"):
            states.add((row["run_id"], row["state"]))
    assert ("run-1", "complete") in states
    assert ("run-2", "frozen") in states
    assert store.expire_agui_batches(now=10**6) == 1


# ── review v1 probes: catalogue identity ─────────────────────────────

def _managed_run_with_catalogue(store, setup):
    publication, key_id = setup
    admission = store.acquire_agui_turn(publication, key_id, "t-1", 0,
                                        "run-1", "hash-1")
    context_id = admission["context_id"]
    store.claim_agui_dispatch("d")
    store.ack_agui_dispatch(context_id, "run-1", "d")
    store.start_agui_turn(context_id, "run-1", "w1")
    store.record_agui_call(context_id, "run-1", "call-a", "page_tool",
                           catalogue_id="host:tool-a",
                           catalogue_version="cv-7")
    store.finish_agui_turn(context_id, "run-1", "w1", "success",
                           batch_deadline_seconds=600.0)
    return context_id, "run-1"


def test_catalogue_identity_is_checked_before_begin(store, setup):
    context_id, run_id = _managed_run_with_catalogue(store, setup)
    claim = _claimed(store, context_id, run_id)
    receipt = claim["receipts"][0]["receipt"]
    with pytest.raises(AguiCatalogueRejected) as excinfo:
        store.begin_agui_call(receipt, catalogue_id="host:tool-a",
                              catalogue_version="cv-8")
    assert excinfo.value.outcome == "catalogue_changed"
    # Terminalized durably, never executed; the batch completed.
    results = store.batch_results(context_id, run_id)
    assert results[0]["result_kind"] == "catalogue_changed"
    assert store.get_agui_batch(context_id, run_id)["state"] == "complete"


def test_missing_live_catalogue_is_unverifiable(store, setup):
    context_id, run_id = _managed_run_with_catalogue(store, setup)
    claim = _claimed(store, context_id, run_id)
    receipt = claim["receipts"][0]["receipt"]
    with pytest.raises(AguiCatalogueRejected) as excinfo:
        store.begin_agui_call(receipt)
    assert excinfo.value.outcome == "catalogue_unverifiable"
    # A no-effect terminalization never commits the claim.
    results = store.batch_results(context_id, run_id)
    assert results[0]["result_kind"] == "catalogue_unverifiable"


def test_matching_catalogue_identity_allows_begin_and_deposit(store, setup):
    context_id, run_id = _managed_run_with_catalogue(store, setup)
    claim = _claimed(store, context_id, run_id)
    receipt = claim["receipts"][0]["receipt"]
    assert store.begin_agui_call(receipt, catalogue_id="host:tool-a",
                                 catalogue_version="cv-7")
    # Deposit needs NO live catalogue (pre-reload result stays
    # depositable).
    result = store.deposit_agui_call(receipt, "result", '{"ok":1}')
    assert result["batch_state"] == "complete"


# ── review v1 probes: deposit responses ──────────────────────────────

def test_partial_no_effect_deposit_reports_the_real_batch_state(store,
                                                                setup):
    context_id, run_id = _managed_run(store, setup)
    claim = _claimed(store, context_id, run_id)
    receipt_a, receipt_b = [item["receipt"] for item in claim["receipts"]]
    partial = store.deposit_agui_call(receipt_a, "denied")
    # No begin happened: the claim is NOT committed — the answer must
    # say so instead of pretending execution_committed.
    assert partial["batch_state"] == "reserved_pre_effect"
    assert store.get_agui_batch(
        context_id, run_id)["state"] == "reserved_pre_effect"
    _begin(store, receipt_b)
    final = store.deposit_agui_call(receipt_b, "result", '{"ok":1}')
    assert final["batch_state"] == "complete"


def test_late_divergent_deposit_after_consumption_replays_terminal(
        store, setup):
    publication, key_id = setup
    context_id, run_id = _managed_run(store, setup, calls=("call-a",))
    claim = _claimed(store, context_id, run_id)
    receipt = claim["receipts"][0]["receipt"]
    _begin(store, receipt)
    store.deposit_agui_call(receipt, "result", '{"ok":1}')
    store.acquire_agui_turn(publication, key_id, "t-1", 0, "run-2",
                            "hash-2", parent_run_id=run_id)
    assert store.get_agui_batch(context_id, run_id)["state"] == "consumed"
    late = store.deposit_agui_call(receipt, "error", '{"boom":1}')
    assert late["replay"] is True
    assert late["kind"] == "result"
    assert late["batch_state"] == "consumed"
    assert store.get_agui_batch(context_id, run_id)["state"] == "consumed"


# ── review v2 probes ─────────────────────────────────────────────────

def _running_managed(store, setup, catalogue=True):
    publication, key_id = setup
    admission = store.acquire_agui_turn(publication, key_id, "t-1", 0,
                                        "run-1", "hash-1")
    context_id = admission["context_id"]
    store.claim_agui_dispatch("d")
    store.ack_agui_dispatch(context_id, "run-1", "d")
    store.start_agui_turn(context_id, "run-1", "w1")
    if catalogue:
        store.record_agui_call(context_id, "run-1", "call-a", "page_tool",
                               catalogue_id="host:page_tool",
                               catalogue_version="cv-1")
    return context_id


def test_record_call_requires_full_catalogue_identity_when_managed(
        store, setup):
    context_id = _running_managed(store, setup, catalogue=False)
    with pytest.raises(ValueError, match="catalogue"):
        store.record_agui_call(context_id, "run-1", "call-a")
    with pytest.raises(ValueError, match="catalogue"):
        store.record_agui_call(context_id, "run-1", "call-a",
                               catalogue_id="host:x")
    with pytest.raises(ValueError, match="catalogue"):
        store.record_agui_call(context_id, "run-1", "call-a",
                               catalogue_version="cv-1")
    store.record_agui_call(context_id, "run-1", "call-a",
                           catalogue_id="host:x", catalogue_version="cv-1")
    assert store.pending_agui_calls(context_id, "run-1") == ["call-a"]


def test_begin_rejects_a_changed_stable_id_with_the_same_version(store,
                                                                 setup):
    context_id, run_id = _managed_run_with_catalogue(store, setup)
    claim = _claimed(store, context_id, run_id)
    receipt = claim["receipts"][0]["receipt"]
    with pytest.raises(AguiCatalogueRejected) as excinfo:
        store.begin_agui_call(receipt, catalogue_id="host:tool-B",
                              catalogue_version="cv-7")
    assert excinfo.value.outcome == "catalogue_changed"
    results = store.batch_results(context_id, run_id)
    assert results[0]["result_kind"] == "catalogue_changed"


def test_terminal_snapshot_carries_the_batch_token(store, setup):
    from core._a2a_turn_journal import AguiReplayExpired
    context_id, run_id = _managed_run(store, setup)
    token = store.batch_token_for(context_id, run_id)
    snapshot = store.agui_terminal_snapshot(
        store.get_agui_run(context_id, run_id))
    assert snapshot["batch_token"] == token
    # Reconnect after pruning: replay_expired still hands out the token
    # of the still-open batch.
    assert store.prune_agui_journals(retention_seconds=0.0) == 1
    with pytest.raises(AguiReplayExpired) as excinfo:
        store.read_agui_events(context_id, run_id, after_seq=0)
    assert excinfo.value.snapshot["batch_token"] == token
    # A closed batch no longer advertises a claimable token.
    assert store.expire_agui_batches(now=10**12) == 1
    snapshot = store.agui_terminal_snapshot(
        store.get_agui_run(context_id, run_id))
    assert "batch_token" not in snapshot


def test_renew_materializes_the_observed_expiry(store, setup):
    context_id, run_id = _managed_run(store, setup, deadline=600.0,
                                      finish_now=1.0)
    claim = _claimed(store, context_id, run_id, "cl-1", lease_seconds=10,
                     now=1.0)
    with pytest.raises(AguiClaimExpired):
        store.renew_agui_batch(claim["owner_token"], now=100.0)
    # The expiry is durable at OBSERVATION — before any fresh claim.
    batch = store.get_agui_batch(context_id, run_id)
    assert batch["state"] == "frozen"
    assert batch["batch_claim_id"] == ""
    with store._connect() as connection:
        row = connection.execute(
            "SELECT 1 FROM agui_expired_claims WHERE batch_claim_id='cl-1'"
        ).fetchone()
    assert row is not None
    # The very FIRST re-claim with the dead id is already refused.
    with pytest.raises(AguiClaimExpired):
        _claimed(store, context_id, run_id, "cl-1", now=100.0)


def test_begin_materializes_the_observed_expiry(store, setup):
    context_id, run_id = _managed_run(store, setup, deadline=600.0,
                                      finish_now=1.0)
    claim = _claimed(store, context_id, run_id, "cl-1", lease_seconds=10,
                     now=1.0)
    with pytest.raises(AguiClaimExpired):
        _begin(store, claim["receipts"][0]["receipt"], now=100.0)
    batch = store.get_agui_batch(context_id, run_id)
    assert batch["state"] == "frozen"
    with store._connect() as connection:
        row = connection.execute(
            "SELECT 1 FROM agui_expired_claims WHERE batch_claim_id='cl-1'"
        ).fetchone()
    assert row is not None


def test_expired_receipts_are_revoked_for_deposits_after_renew(store,
                                                               setup):
    context_id, run_id = _managed_run(store, setup, deadline=600.0,
                                      finish_now=1.0)
    claim = _claimed(store, context_id, run_id, "cl-1", lease_seconds=10,
                     now=1.0)
    with pytest.raises(AguiClaimExpired):
        store.renew_agui_batch(claim["owner_token"], now=100.0)
    # EVERY credential of the expired claim is dead — deposits included:
    # an old receipt can neither deposit 'denied' nor close the batch.
    with pytest.raises(AguiClaimExpired):
        store.deposit_agui_call(claim["receipts"][0]["receipt"], "denied",
                                now=100.0)
    assert store.get_agui_batch(context_id, run_id)["state"] == "frozen"


def test_expired_receipts_are_revoked_for_deposits_after_begin(store,
                                                               setup):
    context_id, run_id = _managed_run(store, setup, deadline=600.0,
                                      finish_now=1.0)
    claim = _claimed(store, context_id, run_id, "cl-1", lease_seconds=10,
                     now=1.0)
    with pytest.raises(AguiClaimExpired):
        _begin(store, claim["receipts"][0]["receipt"], now=100.0)
    with pytest.raises(AguiClaimExpired):
        store.deposit_agui_call(claim["receipts"][1]["receipt"], "denied",
                                now=100.0)
    assert store.get_agui_batch(context_id, run_id)["state"] == "frozen"


@pytest.mark.parametrize("live_id,live_version",
                         [("", "cv-7"), ("host:tool-a", "")])
def test_empty_catalogue_identity_halves_are_unverifiable(
        store, setup, live_id, live_version):
    context_id, run_id = _managed_run_with_catalogue(store, setup)
    claim = _claimed(store, context_id, run_id)
    receipt = claim["receipts"][0]["receipt"]
    # An empty half is NOT comparable: unverifiable, never 'changed'.
    with pytest.raises(AguiCatalogueRejected) as excinfo:
        store.begin_agui_call(receipt, catalogue_id=live_id,
                              catalogue_version=live_version)
    assert excinfo.value.outcome == "catalogue_unverifiable"


def test_finish_enforces_the_terminal_event_type(store, setup):
    context_id = _running_managed(store, setup)
    with pytest.raises(ValueError, match="RUN_FINISHED"):
        store.finish_agui_turn(context_id, "run-1", "w1", "success",
                               terminal_event_json='{"type":"RUN_ERROR"}')
    with pytest.raises(ValueError, match="RUN_FINISHED"):
        store.finish_agui_turn(
            context_id, "run-1", "w1", "error",
            terminal_event_json='{"type":"RUN_FINISHED"}')
    # The run is untouched and the honest terminal still freezes with
    # the batch_token injected into the SERVER-validated RUN_FINISHED.
    store.finish_agui_turn(
        context_id, "run-1", "w1", "success",
        terminal_event_json='{"type":"RUN_FINISHED","note":"custom"}')
    terminal = json.loads(
        store.read_agui_events(context_id, "run-1")[-1]["event_json"])
    assert terminal["type"] == "RUN_FINISHED"
    assert terminal["note"] == "custom"
    assert terminal["batch_token"] == store.batch_token_for(context_id,
                                                            "run-1")
