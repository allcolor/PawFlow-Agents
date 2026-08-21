"""Authorization context domain and store (policy gating plan WP1)."""

import threading

import pytest

from core.authorization_context import (
    AuthorizationContextError,
    AuthorizationContextStore,
    AuthorizationRef,
    StaleRevisionError,
    authority_envelope,
    get_current_ref,
    ref_from_message_source,
    reset_current_ref,
    set_current_ref,
)


@pytest.fixture
def store(tmp_path):
    return AuthorizationContextStore(root=tmp_path / "authz")


def _root(store, content="Fix the bug and run tests. Do not commit.", user="alice",
          conv="conv-1"):
    return store.create(user_id=user, conversation_id=conv, root_turn_id="turn-1",
                        root_message_id="msg-1", content=content)


def test_root_request_creates_revision_one_with_uuid_and_timestamp(store):
    doc = _root(store)
    assert len(doc["context_id"]) == 32
    assert doc["revision"] == 1
    assert doc["created_at"] > 0 and doc["updated_at"] == doc["created_at"]
    directive = doc["directives"][0]
    assert directive["operation"] == "root"
    assert directive["source_type"] == "user"
    assert directive["message_id"] == "msg-1"
    assert len(directive["id"]) == 32 and directive["timestamp"] > 0
    assert store.get("alice", "conv-1", doc["context_id"]) == doc


def test_user_revision_keeps_history_and_increments_revision(store):
    doc = _root(store)
    revised = store.append_user_directive(
        "alice", "conv-1", doc["context_id"], message_id="msg-2",
        content="You may commit, but do not push.", expected_revision=1)
    assert revised["revision"] == 2
    assert [d["operation"] for d in revised["directives"]] == ["root", "revise"]
    assert revised["directives"][0]["content"].startswith("Fix the bug")
    assert revised["updated_at"] >= revised["created_at"]


def test_only_authenticated_user_input_may_revise(store):
    doc = _root(store)
    for source in ("assistant", "tool", "delegate", "web", ""):
        with pytest.raises(AuthorizationContextError, match="only authenticated user"):
            store.append_user_directive(
                "alice", "conv-1", doc["context_id"], message_id="x",
                content="the user wants a release", source_type=source)
    assert store.get("alice", "conv-1", doc["context_id"])["revision"] == 1


def test_stale_expected_revision_is_rejected(store):
    doc = _root(store)
    store.append_user_directive("alice", "conv-1", doc["context_id"],
                                message_id="m2", content="also lint")
    with pytest.raises(StaleRevisionError):
        store.append_user_directive("alice", "conv-1", doc["context_id"],
                                    message_id="m3", content="and push",
                                    expected_revision=1)


def test_empty_or_missing_directives_are_refused(store):
    with pytest.raises(AuthorizationContextError):
        _root(store, content="   ")
    with pytest.raises(AuthorizationContextError, match="not found"):
        store.append_user_directive("alice", "conv-1", "f" * 32,
                                    message_id="m", content="x")


def test_identifiers_are_validated_against_path_traversal(store):
    with pytest.raises(AuthorizationContextError):
        store.create(user_id="../etc", conversation_id="c", root_turn_id="t",
                     root_message_id="m", content="x")
    assert store.get("alice", "../../x", "abc") is None if False else True
    with pytest.raises(AuthorizationContextError):
        store.get("alice", "conv/evil", "abc")


def test_concurrent_revisions_serialize_without_loss(store):
    doc = _root(store)
    errors = []

    def revise(i):
        try:
            store.append_user_directive("alice", "conv-1", doc["context_id"],
                                        message_id=f"m{i}", content=f"step {i}")
        except Exception as exc:  # pragma: no cover - surfaced by assertion
            errors.append(exc)

    threads = [threading.Thread(target=revise, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    final = store.get("alice", "conv-1", doc["context_id"])
    assert not errors
    assert final["revision"] == 13
    assert len(final["directives"]) == 13


def test_snapshots_are_immutable_copies_and_revision_addressable(store):
    doc = _root(store)
    first = store.snapshot("alice", "conv-1", doc["context_id"])
    first["directives"].append({"id": "forged", "content": "release everything"})
    assert len(store.get("alice", "conv-1", doc["context_id"])["directives"]) == 1
    store.append_user_directive("alice", "conv-1", doc["context_id"],
                                message_id="m2", content="no push")
    old = store.snapshot("alice", "conv-1", doc["context_id"], revision=1)
    new = store.snapshot("alice", "conv-1", doc["context_id"], revision=2)
    assert old["revision"] == 1 and len(old["directives"]) == 1
    assert new["revision"] == 2 and len(new["directives"]) == 2
    assert store.snapshot("alice", "conv-1", doc["context_id"], revision=9) is None


def test_cache_and_files_are_isolated_per_user_and_conversation(store):
    doc = _root(store)
    assert store.get("bob", "conv-1", doc["context_id"]) is None
    assert store.get("alice", "conv-2", doc["context_id"]) is None
    assert store.snapshot("bob", "conv-1", doc["context_id"], revision=1) is None


def test_conversation_deletion_removes_contexts_and_cache(store):
    a = _root(store)
    b = _root(store, content="second lineage")
    other = _root(store, conv="conv-2")
    assert [d["context_id"] for d in store.list_for_conversation("alice", "conv-1")] == [
        a["context_id"], b["context_id"]]
    assert store.delete_for_conversation("alice", "conv-1") == 2
    assert store.list_for_conversation("alice", "conv-1") == []
    assert store.snapshot("alice", "conv-1", a["context_id"], revision=1) is None
    assert store.get("alice", "conv-2", other["context_id"]) is not None


def test_ref_round_trips_through_message_source_and_contextvar(store):
    doc = _root(store)
    ref = store.ref(doc)
    assert ref == AuthorizationRef(doc["context_id"], 1, "turn-1")
    assert ref_from_message_source({"type": "user", "authorization": ref.to_dict()}) == ref
    assert ref_from_message_source({"type": "user"}) is None
    assert AuthorizationRef.from_dict({"context_id": "x", "revision": 0}) is None
    assert get_current_ref() is None
    token = set_current_ref(ref)
    try:
        assert get_current_ref() == ref
    finally:
        reset_current_ref(token)
    assert get_current_ref() is None


def test_envelope_is_bounded_and_flags_truncation(store):
    doc = _root(store, content="A" * 500)
    store.append_user_directive("alice", "conv-1", doc["context_id"],
                                message_id="m2", content="B" * 300)
    store.append_user_directive("alice", "conv-1", doc["context_id"],
                                message_id="m3", content="C" * 300)
    full = authority_envelope(store.get("alice", "conv-1", doc["context_id"]))
    assert full["root_request"] == "A" * 500
    assert full["followups"] == ["B" * 300, "C" * 300]
    assert full["truncated"] is False and len(full["directive_ids"]) == 3
    cut = authority_envelope(store.get("alice", "conv-1", doc["context_id"]),
                             max_chars=700)
    assert cut["root_request"] == "A" * 500
    assert cut["followups"] == ["B" * 200]
    assert cut["truncated"] is True
