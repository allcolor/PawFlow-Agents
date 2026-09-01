"""Run-scoped attach/cancel credentials (plan B1-J — step P1-F/5).

Store-level coverage of ``agui_run_tokens``: minting inside the
admission transaction, byte-identical re-derivation, uniform failure
discipline, idempotent journaled cancellation, and keyring protection.
"""

import json
import time

import pytest

from core.a2a_store import A2AStore
from core._a2a_turn_batch import AguiTokenInvalid
from core._a2a_turn_journal import AguiSubscriberTakenOver


@pytest.fixture()
def setup(tmp_path):
    store = A2AStore(database_path=tmp_path / "a2a.sqlite3")
    publication = store.configure_publication(
        "owner", "conv-1", "helper", context_policy="isolated",
        managed_mode=True)
    _, key = store.create_key(publication["publication_id"], "client")
    admission = store.acquire_agui_turn(publication, key["key_id"], "t-1",
                                        0, "run-1", "hash-1",
                                        payload_json='{"x":1}')
    return {"store": store, "publication": publication, "key": key,
            "context_id": admission["context_id"]}


def _scope(setup):
    return (setup["publication"]["publication_id"], setup["key"]["key_id"])


# ── minting & derivation ─────────────────────────────────────────────

def test_tokens_are_minted_with_the_admission_and_stable(setup):
    store, context_id = setup["store"], setup["context_id"]
    tokens = store.agui_run_tokens_for(context_id, "run-1")
    assert tokens["attach_token"] and tokens["cancel_token"]
    assert tokens["attach_token"] != tokens["cancel_token"]
    # Byte-identical re-derivation, and a replayed admission keeps the
    # SAME handle (same tokens).
    assert store.agui_run_tokens_for(context_id, "run-1") == tokens
    replay = store.acquire_agui_turn(setup["publication"],
                                     setup["key"]["key_id"], "t-1", 0,
                                     "run-1", "hash-1")
    assert replay["replay"] is True
    assert store.agui_run_tokens_for(context_id, "run-1") == tokens


def test_attach_resolves_and_fails_uniformly(setup):
    store, context_id = setup["store"], setup["context_id"]
    tokens = store.agui_run_tokens_for(context_id, "run-1")
    resolved = store.resolve_agui_attach(tokens["attach_token"],
                                         scope=_scope(setup))
    assert resolved == {"context_id": context_id, "run_id": "run-1"}
    # Cross-usage replay (cancel token on attach) is uniform invalid.
    with pytest.raises(AguiTokenInvalid):
        store.resolve_agui_attach(tokens["cancel_token"],
                                  scope=_scope(setup))
    # Tampered MAC and malformed tokens too.
    tampered = tokens["attach_token"][:-1] + (
        "0" if tokens["attach_token"][-1] != "0" else "1")
    with pytest.raises(AguiTokenInvalid):
        store.resolve_agui_attach(tampered, scope=_scope(setup))
    with pytest.raises(AguiTokenInvalid):
        store.resolve_agui_attach("garbage", scope=_scope(setup))


def test_attach_token_never_crosses_publications(setup):
    store, context_id = setup["store"], setup["context_id"]
    tokens = store.agui_run_tokens_for(context_id, "run-1")
    other = store.configure_publication(
        "owner", "conv-2", "helper", context_policy="isolated",
        managed_mode=True, label="other")
    _, other_key = store.create_key(other["publication_id"], "client-b")
    with pytest.raises(AguiTokenInvalid):
        store.resolve_agui_attach(
            tokens["attach_token"],
            scope=(other["publication_id"], other_key["key_id"]))


def test_subscriber_epoch_is_monotone_persistent_and_cursor_safe(setup):
    store, context_id = setup["store"], setup["context_id"]
    first = store.acquire_agui_subscriber(
        context_id, "run-1", after_seq=0)
    assert first == {"subscriber_epoch": 1, "committed_sequence": 0}
    assert store.is_agui_subscriber_current(context_id, "run-1", 1)

    # A future cursor is rejected before the epoch changes, so a malformed
    # attach cannot evict the legitimate subscriber.
    with pytest.raises(ValueError):
        store.acquire_agui_subscriber(context_id, "run-1", after_seq=1)
    assert store.is_agui_subscriber_current(context_id, "run-1", 1)

    second = store.acquire_agui_subscriber(
        context_id, "run-1", after_seq=0)
    assert second["subscriber_epoch"] == 2
    with pytest.raises(AguiSubscriberTakenOver):
        store.read_agui_events(
            context_id, "run-1", subscriber_epoch=1)

    # The counter is durable, not a process-local subscriber registry.
    reopened = A2AStore(store.database_path)
    third = reopened.acquire_agui_subscriber(
        context_id, "run-1", after_seq=0)
    assert third["subscriber_epoch"] == 3


# ── cancellation ─────────────────────────────────────────────────────

def test_cancel_running_run_is_journaled_and_cuts_everything(setup):
    store, context_id = setup["store"], setup["context_id"]
    fence = store.adopt_agui_run(context_id, "run-1", "w1")
    store.record_agui_call(context_id, "run-1", "call-a", "page_tool",
                           catalogue_id="host:t", catalogue_version="v1")
    tokens = store.agui_run_tokens_for(context_id, "run-1")
    result = store.cancel_agui_run(tokens["cancel_token"],
                                   scope=_scope(setup))
    assert (result["outcome"], result["already"]) == ("cancelled", False)
    admission = store.get_agui_admission(context_id, "run-1")
    assert (admission["state"], admission["outcome"]) == ("terminal",
                                                          "cancelled")
    assert admission["lease_owner"] == ""
    # Journaled terminal RUN_ERROR {cancelled}.
    events = store.read_agui_events(context_id, "run-1")
    last = json.loads(events[-1]["event_json"])
    assert (last["type"], last["code"]) == ("RUN_ERROR", "cancelled")
    run = store.get_agui_run(context_id, "run-1")
    assert (run["state"], run["outcome"]) == ("terminal", "cancelled")
    # Pending calls abandoned, fence invalidated, worker fenced out.
    assert store.pending_agui_calls(context_id, "run-1") == []
    assert store.check_agui_fence(context_id, fence) is False
    assert store.heartbeat_agui_turn(context_id, "run-1", "w1") is False


def test_cancel_is_idempotent_and_replays_the_terminal(setup):
    store, context_id = setup["store"], setup["context_id"]
    tokens = store.agui_run_tokens_for(context_id, "run-1")
    first = store.cancel_agui_run(tokens["cancel_token"],
                                  scope=_scope(setup))
    assert (first["outcome"], first["already"]) == ("cancelled", False)
    sequence = store.get_agui_run(context_id, "run-1")["committed_sequence"]
    second = store.cancel_agui_run(tokens["cancel_token"],
                                   scope=_scope(setup))
    assert (second["outcome"], second["already"]) == ("cancelled", True)
    # Replay mutates nothing — no second journal event.
    assert store.get_agui_run(context_id,
                              "run-1")["committed_sequence"] == sequence


def test_cancel_of_a_reserved_run_purges_the_recovery_payload(setup):
    store, context_id = setup["store"], setup["context_id"]
    tokens = store.agui_run_tokens_for(context_id, "run-1")
    assert store.get_agui_admission(context_id,
                                    "run-1")["payload_json"] == '{"x":1}'
    store.cancel_agui_run(tokens["cancel_token"], scope=_scope(setup))
    admission = store.get_agui_admission(context_id, "run-1")
    # A cancelled reserved run can never be (re)submitted: terminal, no
    # payload left for any recovery path.
    assert admission["state"] == "terminal"
    assert admission["payload_json"] == ""


def test_cancel_after_success_replays_success_without_mutation(setup):
    store, context_id = setup["store"], setup["context_id"]
    store.adopt_agui_run(context_id, "run-1", "w1")
    store.finish_agui_turn(context_id, "run-1", "w1", "success")
    tokens = store.agui_run_tokens_for(context_id, "run-1")
    result = store.cancel_agui_run(tokens["cancel_token"],
                                   scope=_scope(setup))
    assert (result["outcome"], result["already"]) == ("success", True)
    run = store.get_agui_run(context_id, "run-1")
    assert (run["state"], run["outcome"]) == ("terminal", "success")


def test_cancel_token_scope_is_enforced(setup):
    store, context_id = setup["store"], setup["context_id"]
    tokens = store.agui_run_tokens_for(context_id, "run-1")
    other = store.configure_publication(
        "owner", "conv-2", "helper", context_policy="isolated",
        managed_mode=True, label="other")
    _, other_key = store.create_key(other["publication_id"], "client-b")
    with pytest.raises(AguiTokenInvalid):
        store.cancel_agui_run(
            tokens["cancel_token"],
            scope=(other["publication_id"], other_key["key_id"]))
    # The run is untouched by the refused foreign cancel.
    assert store.get_agui_admission(context_id,
                                    "run-1")["state"] == "reserved"


# ── keyring protection ───────────────────────────────────────────────

def test_keyring_prune_never_drops_a_run_token_key(setup):
    store, context_id = setup["store"], setup["context_id"]
    tokens = store.agui_run_tokens_for(context_id, "run-1")
    # Force a NEWER key version, making run-1's pinned key non-current.
    with store._lock, store._immediate() as connection:
        high = connection.execute(
            "SELECT MAX(version) FROM agui_token_keys").fetchone()[0]
        connection.execute(
            "INSERT INTO agui_token_keys (version, secret, created_at) "
            "VALUES (?, 'fresh-secret', ?)", (int(high) + 1, time.time()))
    store.prune_agui_token_keys()
    # The pinned key survived: tokens still re-derive byte-identically
    # and still resolve.
    assert store.agui_run_tokens_for(context_id, "run-1") == tokens
    assert store.resolve_agui_attach(tokens["attach_token"],
                                     scope=_scope(setup))["run_id"] == "run-1"
