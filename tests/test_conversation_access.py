"""Authorization layer for shared conversations (core/conversation_access.py).

Phase 1 of docs/CONVERSATION_SHARING_PLAN.md: the resolver, the collaborators
ACL stored in ``extra``, and the per-user reverse index. Nothing calls this
yet, so the invariant under test is the primitive's own contract:

- an unshared conversation resolves exactly as it does today (owner-equality),
- a pending invite grants nothing until the invitee accepts,
- a kicked collaborator loses access but keeps an audit row,
- "not yours" and "does not exist" are indistinguishable to the caller.
"""

import json
import threading
import uuid

import pytest

from core.conversation_store import ConversationStore
import core.conversation_access as ca


@pytest.fixture(autouse=True)
def reset_singleton():
    ConversationStore.reset()
    yield
    ConversationStore.reset()


@pytest.fixture
def store(tmp_path):
    """A temp-backed store installed as the singleton the module resolves."""
    s = ConversationStore(store_dir=str(tmp_path / "conversations"))
    ConversationStore._instance = s
    return s


@pytest.fixture
def conv(store):
    cid = store.generate_id()
    store.save(cid, [], user_id="alice")
    return cid


# -- Owner resolution -------------------------------------------------

class TestResolveOwner:

    def test_returns_owner_of_existing_conversation(self, store, conv):
        assert store.resolve_owner(conv) == "alice"

    def test_unknown_conversation_resolves_empty(self, store):
        assert store.resolve_owner("nope_" + uuid.uuid4().hex[:8]) == ""
        assert store.resolve_owner("") == ""

    def test_resolves_from_disk_without_a_warm_cache(self, store, conv):
        """A fresh process must resolve ownership by scanning, not guessing."""
        cold = ConversationStore(store_dir=str(store._store_dir))
        assert cold.resolve_owner(conv) == "alice"


# -- Unshared conversations: zero change ------------------------------

class TestUnsharedConversation:

    def test_owner_gets_owner_role_and_own_storage_id(self, conv):
        access = ca.resolve_conversation_access(conv, "alice")
        assert access.role == ca.ROLE_OWNER
        assert access.owner_user_id == "alice"
        assert access.storage_user_id == "alice"
        assert access.is_owner and access.can_read and access.can_write

    def test_stranger_gets_no_access(self, conv):
        access = ca.resolve_conversation_access(conv, "bob")
        assert access.role == ca.ROLE_NONE
        assert not access.can_read and not access.can_write

    def test_no_acl_is_written_when_nothing_is_shared(self, store, conv):
        ca.resolve_conversation_access(conv, "alice")
        assert store.get_extra(conv, ca.COLLABORATORS_KEY) is None

    def test_missing_conversation_and_forbidden_are_indistinguishable(self, conv):
        missing = ca.resolve_conversation_access("gone_" + uuid.uuid4().hex[:8], "bob")
        forbidden = ca.resolve_conversation_access(conv, "bob")
        assert missing.role == forbidden.role == ca.ROLE_NONE
        with pytest.raises(ca.ConversationAccessError) as missing_err:
            ca.require_read("gone_" + uuid.uuid4().hex[:8], "bob")
        with pytest.raises(ca.ConversationAccessError) as forbidden_err:
            ca.require_read(conv, "bob")
        assert str(missing_err.value) == str(forbidden_err.value)

    def test_empty_requester_never_resolves(self, conv):
        assert ca.resolve_conversation_access(conv, "").role == ca.ROLE_NONE


# -- Invite lifecycle -------------------------------------------------

class TestInviteLifecycle:

    def test_pending_invite_grants_nothing(self, conv):
        ca.set_collaborator(conv, "bob", role=ca.ROLE_WRITE, invited_by="alice")
        access = ca.resolve_conversation_access(conv, "bob")
        assert access.role == ca.ROLE_NONE
        assert not access.can_read

    def test_accepted_write_collaborator_reads_and_writes_owner_storage(self, conv):
        ca.set_collaborator(conv, "bob", role=ca.ROLE_WRITE, invited_by="alice")
        ca.set_collaborator(conv, "bob", status=ca.STATUS_ACCEPTED)
        access = ca.require_write(conv, "bob")
        assert access.role == ca.ROLE_WRITE
        assert access.storage_user_id == "alice"  # never the collaborator's id
        assert not access.is_owner

    def test_read_collaborator_may_read_but_not_write(self, conv):
        ca.set_collaborator(conv, "bob", role=ca.ROLE_READ,
                            status=ca.STATUS_ACCEPTED)
        assert ca.require_read(conv, "bob").role == ca.ROLE_READ
        with pytest.raises(ca.ConversationAccessError):
            ca.require_write(conv, "bob")

    def test_role_change_keeps_accepted_status(self, conv):
        ca.set_collaborator(conv, "bob", role=ca.ROLE_WRITE,
                            status=ca.STATUS_ACCEPTED)
        row = ca.set_collaborator(conv, "bob", role=ca.ROLE_READ)
        assert row["status"] == ca.STATUS_ACCEPTED
        assert ca.resolve_conversation_access(conv, "bob").role == ca.ROLE_READ

    def test_accepting_stamps_responded_at_only_once(self, conv):
        ca.set_collaborator(conv, "bob", role=ca.ROLE_WRITE, now=100.0)
        accepted = ca.set_collaborator(conv, "bob", status=ca.STATUS_ACCEPTED,
                                       now=200.0)
        assert accepted["invited_at"] == 100.0
        assert accepted["responded_at"] == 200.0
        again = ca.set_collaborator(conv, "bob", status=ca.STATUS_ACCEPTED,
                                    now=300.0)
        assert again["responded_at"] == 200.0

    def test_kick_revokes_access_but_keeps_the_audit_row(self, conv):
        ca.set_collaborator(conv, "bob", role=ca.ROLE_WRITE,
                            status=ca.STATUS_ACCEPTED)
        ca.set_collaborator(conv, "bob", status=ca.STATUS_KICKED)
        assert ca.resolve_conversation_access(conv, "bob").role == ca.ROLE_NONE
        row = ca.get_collaborator(conv, "bob")
        assert row is not None and row["status"] == ca.STATUS_KICKED

    def test_re_invite_after_kick_is_a_status_transition(self, conv):
        ca.set_collaborator(conv, "bob", role=ca.ROLE_WRITE,
                            status=ca.STATUS_KICKED)
        ca.set_collaborator(conv, "bob", status=ca.STATUS_PENDING)
        assert len(ca.get_collaborators(conv)) == 1
        ca.set_collaborator(conv, "bob", status=ca.STATUS_ACCEPTED)
        assert ca.require_read(conv, "bob").role == ca.ROLE_WRITE

    def test_decline_erases_the_row(self, conv):
        ca.set_collaborator(conv, "bob", role=ca.ROLE_READ)
        assert ca.remove_collaborator(conv, "bob") is True
        assert ca.get_collaborators(conv) == []
        assert ca.remove_collaborator(conv, "bob") is False

    def test_owner_row_in_the_acl_cannot_downgrade_the_owner(self, conv):
        """There is exactly one owner: an ACL row about them is inert."""
        ca.set_collaborator(conv, "alice", role=ca.ROLE_READ,
                            status=ca.STATUS_KICKED)
        assert ca.require_write(conv, "alice").role == ca.ROLE_OWNER

    def test_require_owner_rejects_a_write_collaborator(self, conv):
        ca.set_collaborator(conv, "bob", role=ca.ROLE_WRITE,
                            status=ca.STATUS_ACCEPTED)
        assert ca.require_owner(conv, "alice").is_owner
        with pytest.raises(ca.ConversationAccessError):
            ca.require_owner(conv, "bob")


# -- ACL storage hygiene ----------------------------------------------

class TestAclStorage:

    def test_rejects_unknown_role_or_status(self, conv):
        with pytest.raises(ValueError):
            ca.set_collaborator(conv, "bob", role="admin")
        with pytest.raises(ValueError):
            ca.set_collaborator(conv, "bob", role=ca.ROLE_WRITE, status="maybe")
        with pytest.raises(ValueError):
            ca.set_collaborator(conv, "bob", status=ca.STATUS_ACCEPTED)  # no role
        with pytest.raises(ValueError):
            ca.set_collaborator(conv, "", role=ca.ROLE_WRITE)

    def test_corrupt_rows_are_ignored_not_trusted(self, store, conv):
        store.set_extra(conv, ca.COLLABORATORS_KEY, [
            {"user_id": "bob", "role": "root", "status": "accepted"},
            {"role": "write", "status": "accepted"},
            "not-a-row",
            {"user_id": "carol", "role": "read", "status": "accepted"},
        ])
        assert [r["user_id"] for r in ca.get_collaborators(conv)] == ["carol"]
        assert ca.resolve_conversation_access(conv, "bob").role == ca.ROLE_NONE
        assert ca.resolve_conversation_access(conv, "carol").role == ca.ROLE_READ

    def test_acl_is_stored_under_the_owner_conversation(self, store, conv):
        ca.set_collaborator(conv, "bob", role=ca.ROLE_WRITE)
        stored = store.get_extra(conv, ca.COLLABORATORS_KEY)
        assert isinstance(stored, list) and stored[0]["user_id"] == "bob"
        # Same directory as before sharing -- no storage-schema change.
        assert store._conv_dir(conv).parent.name == "alice"

    def test_concurrent_invites_do_not_clobber_each_other(self, conv):
        """Read-modify-write on one shared extras key, from many threads."""
        names = [f"user{i}" for i in range(12)]
        barrier = threading.Barrier(len(names))

        def invite(name):
            barrier.wait(timeout=5.0)
            ca.set_collaborator(conv, name, role=ca.ROLE_WRITE,
                                invited_by="alice")

        threads = [threading.Thread(target=invite, args=(n,)) for n in names]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)
        assert sorted(r["user_id"] for r in ca.get_collaborators(conv)) == sorted(names)


# -- Reverse index ----------------------------------------------------

class TestSharedIndex:

    def test_invite_indexes_and_decline_unindexes(self, conv):
        ca.set_collaborator(conv, "bob", role=ca.ROLE_WRITE)
        assert ca.shared_conversation_ids("bob") == [conv]
        ca.remove_collaborator(conv, "bob")
        assert ca.shared_conversation_ids("bob") == []

    def test_accepting_keeps_the_entry(self, conv):
        ca.set_collaborator(conv, "bob", role=ca.ROLE_WRITE)
        ca.set_collaborator(conv, "bob", status=ca.STATUS_ACCEPTED)
        assert ca.shared_conversation_ids("bob") == [conv]

    def test_kick_unindexes(self, conv):
        ca.set_collaborator(conv, "bob", role=ca.ROLE_WRITE,
                            status=ca.STATUS_ACCEPTED)
        ca.set_collaborator(conv, "bob", status=ca.STATUS_KICKED)
        assert ca.shared_conversation_ids("bob") == []

    def test_no_entry_for_a_user_with_no_invite(self, conv):
        ca.set_collaborator(conv, "bob", role=ca.ROLE_WRITE)
        assert ca.shared_conversation_ids("carol") == []
        assert ca.shared_conversation_ids("") == []

    def test_entry_is_not_duplicated_by_repeated_updates(self, conv):
        for _ in range(3):
            ca.set_collaborator(conv, "bob", role=ca.ROLE_WRITE)
        assert ca.shared_conversation_ids("bob") == [conv]

    def test_index_lives_beside_user_dirs_not_inside_one(self, store, conv):
        ca.set_collaborator(conv, "bob", role=ca.ROLE_WRITE)
        path = store.shared_index_path("bob")
        assert path.parent == store._store_dir / "_shared_index"
        assert json.loads(path.read_text(encoding="utf-8")) == [conv]

    def test_a_stale_index_entry_grants_nothing(self, store, conv):
        """The index is a candidate list; the ACL is what decides."""
        store.shared_index_path("bob").parent.mkdir(parents=True, exist_ok=True)
        store.shared_index_path("bob").write_text(json.dumps([conv]),
                                                  encoding="utf-8")
        assert ca.shared_conversation_ids("bob") == [conv]
        assert ca.resolve_conversation_access(conv, "bob").role == ca.ROLE_NONE

    def test_rebuild_repairs_a_corrupt_index(self, store, conv):
        other = store.generate_id()
        store.save(other, [], user_id="alice")
        ca.set_collaborator(conv, "bob", role=ca.ROLE_WRITE,
                            status=ca.STATUS_ACCEPTED)
        store.shared_index_path("bob").write_text("{ not json", encoding="utf-8")
        assert ca.shared_conversation_ids("bob") == []
        assert ca.rebuild_shared_index("bob") == [conv]
        assert ca.shared_conversation_ids("bob") == [conv]

    def test_rebuild_never_lists_a_users_own_conversations(self, store, conv):
        assert ca.rebuild_shared_index("alice") == []


# -- Wholesale ACL replacement ----------------------------------------

class TestSetCollaborators:

    def test_replaces_rows_and_resyncs_every_index(self, conv):
        ca.set_collaborator(conv, "bob", role=ca.ROLE_WRITE,
                            status=ca.STATUS_ACCEPTED)
        ca.set_collaborators(conv, [
            {"user_id": "carol", "role": "read", "status": "accepted"},
        ])
        assert ca.shared_conversation_ids("bob") == []
        assert ca.shared_conversation_ids("carol") == [conv]
        assert ca.resolve_conversation_access(conv, "bob").role == ca.ROLE_NONE
        assert ca.resolve_conversation_access(conv, "carol").role == ca.ROLE_READ

    def test_rejects_an_invalid_row_without_writing_anything(self, conv):
        ca.set_collaborator(conv, "bob", role=ca.ROLE_WRITE,
                            status=ca.STATUS_ACCEPTED)
        with pytest.raises(ValueError):
            ca.set_collaborators(conv, [{"user_id": "carol", "role": "root",
                                         "status": "accepted"}])
        assert [r["user_id"] for r in ca.get_collaborators(conv)] == ["bob"]
