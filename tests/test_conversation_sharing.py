"""Sharing actions + the authorization they put on the conversation handlers.

Phase 3 of docs/CONVERSATION_SHARING_PLAN.md. Two things are under test:

- the lifecycle the actions expose (invite -> pending -> accept -> write ->
  role change -> kick -> revoked; decline; leave; shared list),
- the gate the same phase adds to ``_conv_core``: a stranger gets the exact
  404 an unknown conversation_id gets, on every action that names one.

The handlers are driven directly, with the store the dispatcher would hand
them -- that store is what authorization resolves against, so a test store is
never silently authorized against the production singleton.
"""

import json

import pytest

import core.paths as _paths
from core import FlowFile
from core.conversation_store import ConversationStore
from core.security import Role, SecurityManager, User
import core.conversation_access as ca
from tasks.ai.actions.conversation import _handle_conversation

OWNER = "alice"
COLLAB = "bob"
STRANGER = "carol"


@pytest.fixture(autouse=True)
def users(tmp_path, monkeypatch):
    """A real SecurityManager over a temp users file.

    ``share_conversation`` refuses to invite a user who does not exist, so the
    invitee has to be a real account rather than a stubbed lookup.
    """
    monkeypatch.setattr(_paths, "SECURITY_FILE", tmp_path / "security.json")
    monkeypatch.setattr(_paths, "USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(_paths, "SESSIONS_FILE", tmp_path / "sessions.json")
    SecurityManager._instance = None
    sm = SecurityManager.get_instance()
    # Registered directly rather than through create_user: nothing here
    # authenticates, and a 600k-iteration PBKDF2 per user per test is pure
    # wall-clock.
    for name in (OWNER, COLLAB, STRANGER):
        sm._users[name] = User(username=name, role=Role.USER)
    yield sm
    SecurityManager._instance = None


@pytest.fixture(autouse=True)
def reset_store():
    ConversationStore.reset()
    yield
    ConversationStore.reset()


@pytest.fixture
def store(tmp_path):
    return ConversationStore(store_dir=str(tmp_path / "conversations"))


@pytest.fixture
def conv(store):
    cid = store.generate_id()
    store.save(cid, [], user_id=OWNER)
    return cid


class _Task:
    """The AgentLoopTask surface the exercised handlers actually touch."""

    def _classify_messages_for_display(self, messages):
        return list(messages)

    def _ensure_active_agent(self, conv_id, active_res, uid):
        return active_res or {"agent": "assistant"}


def _call(store, action, body, requester):
    ff = FlowFile(content=b"")
    ff.set_attribute("http.auth.principal", requester)
    out = _handle_conversation(_Task(), action, body, store, requester, ff)
    assert out == [ff]
    status = ff.get_attribute("http.response.status") or "200"
    return json.loads(ff.get_content().decode("utf-8")), status


def _invite(store, cid, target=COLLAB, role=ca.ROLE_WRITE, by=OWNER):
    return _call(store, "share_conversation", {
        "conversation_id": cid, "collaborator_id": target, "role": role,
    }, by)


def _accept(store, cid, who=COLLAB):
    return _call(store, "respond_to_share_invite", {
        "conversation_id": cid, "response": "accept",
    }, who)


# -- Invite ------------------------------------------------------------

class TestShareConversation:

    def test_owner_invite_creates_a_pending_row_granting_nothing(self, store, conv):
        payload, status = _invite(store, conv)
        assert status == "200" and payload["ok"]
        assert payload["collaborator"]["status"] == ca.STATUS_PENDING
        assert payload["collaborator"]["invited_by"] == OWNER
        assert ca.resolve_conversation_access(conv, COLLAB, store=store).role == ""

    def test_a_collaborator_cannot_invite_anyone(self, store, conv):
        _invite(store, conv)
        _accept(store, conv)
        payload, status = _invite(store, conv, target=STRANGER, by=COLLAB)
        assert status == "404" and payload == {"error": "Conversation not found"}
        assert ca.get_collaborator(conv, STRANGER, store=store) is None

    def test_a_stranger_inviting_gets_the_not_found_answer(self, store, conv):
        payload, status = _invite(store, conv, target=STRANGER, by=STRANGER)
        assert status == "404" and payload == {"error": "Conversation not found"}

    def test_unknown_conversation_and_forbidden_are_the_same_answer(self, store, conv):
        forbidden, s1 = _invite(store, conv, by=STRANGER)
        missing, s2 = _call(store, "share_conversation", {
            "conversation_id": "deadbeefdeadbeef", "collaborator_id": COLLAB,
        }, STRANGER)
        assert (forbidden, s1) == (missing, s2)

    def test_inviting_an_unknown_user_is_refused(self, store, conv):
        payload, status = _invite(store, conv, target="nobody")
        assert status == "400" and "nobody" in payload["error"]
        assert ca.get_collaborators(conv, store=store) == []

    def test_inviting_the_owner_is_refused(self, store, conv):
        payload, status = _invite(store, conv, target=OWNER)
        assert status == "400"
        assert ca.get_collaborators(conv, store=store) == []

    def test_invalid_role_is_refused(self, store, conv):
        payload, status = _invite(store, conv, role="admin")
        assert status == "400" and "admin" in payload["error"]

    def test_re_inviting_an_accepted_collaborator_never_revokes_access(self, store, conv):
        _invite(store, conv)
        _accept(store, conv)
        payload, _ = _invite(store, conv, role=ca.ROLE_READ)
        assert payload["collaborator"]["status"] == ca.STATUS_ACCEPTED
        assert ca.resolve_conversation_access(conv, COLLAB, store=store).role == ca.ROLE_READ

    def test_re_inviting_a_kicked_user_re_opens_a_pending_invite(self, store, conv):
        _invite(store, conv)
        _accept(store, conv)
        _call(store, "kick_collaborator",
              {"conversation_id": conv, "collaborator_id": COLLAB}, OWNER)
        payload, _ = _invite(store, conv)
        assert payload["collaborator"]["status"] == ca.STATUS_PENDING
        assert ca.resolve_conversation_access(conv, COLLAB, store=store).role == ""


# -- Responding to an invite -------------------------------------------

class TestRespondToInvite:

    def test_accept_grants_the_invited_role(self, store, conv):
        _invite(store, conv, role=ca.ROLE_READ)
        payload, status = _accept(store, conv)
        assert status == "200"
        assert payload["status"] == ca.STATUS_ACCEPTED
        assert payload["role"] == ca.ROLE_READ
        assert ca.require_read(conv, COLLAB, store=store).role == ca.ROLE_READ

    def test_decline_erases_the_row_and_the_index_entry(self, store, conv):
        _invite(store, conv)
        payload, status = _call(store, "respond_to_share_invite", {
            "conversation_id": conv, "response": "decline",
        }, COLLAB)
        assert status == "200" and payload["status"] == "declined"
        assert ca.get_collaborators(conv, store=store) == []
        assert ca.shared_conversation_ids(COLLAB, store=store) == []

    def test_responding_without_an_invite_cannot_probe_existence(self, store, conv):
        payload, status = _accept(store, conv, who=STRANGER)
        assert status == "404" and payload == {"error": "Conversation not found"}

    def test_accepting_twice_is_not_a_way_back_in_after_a_kick(self, store, conv):
        _invite(store, conv)
        _accept(store, conv)
        _call(store, "kick_collaborator",
              {"conversation_id": conv, "collaborator_id": COLLAB}, OWNER)
        payload, status = _accept(store, conv)
        assert status == "404"
        assert ca.resolve_conversation_access(conv, COLLAB, store=store).role == ""

    def test_an_unknown_response_verb_is_rejected(self, store, conv):
        _invite(store, conv)
        payload, status = _call(store, "respond_to_share_invite", {
            "conversation_id": conv, "response": "maybe",
        }, COLLAB)
        assert status == "400"


# -- Role changes, kick, leave -----------------------------------------

class TestRoleChangeKickLeave:

    def test_owner_downgrades_a_writer_to_reader(self, store, conv):
        _invite(store, conv)
        _accept(store, conv)
        payload, status = _call(store, "update_collaborator_role", {
            "conversation_id": conv, "collaborator_id": COLLAB,
            "role": ca.ROLE_READ,
        }, OWNER)
        assert status == "200"
        assert payload["collaborator"]["role"] == ca.ROLE_READ
        assert payload["collaborator"]["status"] == ca.STATUS_ACCEPTED
        with pytest.raises(ca.ConversationAccessError):
            ca.require_write(conv, COLLAB, store=store)

    def test_a_collaborator_cannot_promote_themselves(self, store, conv):
        _invite(store, conv, role=ca.ROLE_READ)
        _accept(store, conv)
        payload, status = _call(store, "update_collaborator_role", {
            "conversation_id": conv, "collaborator_id": COLLAB,
            "role": ca.ROLE_WRITE,
        }, COLLAB)
        assert status == "404"
        assert ca.resolve_conversation_access(conv, COLLAB, store=store).role == ca.ROLE_READ

    def test_kick_revokes_access_and_keeps_the_audit_row(self, store, conv):
        _invite(store, conv)
        _accept(store, conv)
        payload, status = _call(store, "kick_collaborator", {
            "conversation_id": conv, "collaborator_id": COLLAB,
        }, OWNER)
        assert status == "200"
        assert payload["collaborator"]["status"] == ca.STATUS_KICKED
        assert ca.resolve_conversation_access(conv, COLLAB, store=store).role == ""
        row = ca.get_collaborator(conv, COLLAB, store=store)
        assert row is not None and row["invited_by"] == OWNER
        assert ca.shared_conversation_ids(COLLAB, store=store) == []

    def test_kicking_someone_who_was_never_invited_is_a_404(self, store, conv):
        payload, status = _call(store, "kick_collaborator", {
            "conversation_id": conv, "collaborator_id": STRANGER,
        }, OWNER)
        assert status == "404" and payload["error"] == "Collaborator not found"

    def test_a_collaborator_can_leave(self, store, conv):
        _invite(store, conv)
        _accept(store, conv)
        payload, status = _call(store, "leave_conversation",
                                {"conversation_id": conv}, COLLAB)
        assert status == "200" and payload["ok"]
        assert ca.resolve_conversation_access(conv, COLLAB, store=store).role == ""
        assert ca.shared_conversation_ids(COLLAB, store=store) == []

    def test_the_owner_cannot_leave_their_own_conversation(self, store, conv):
        payload, status = _call(store, "leave_conversation",
                                {"conversation_id": conv}, OWNER)
        assert status == "400"
        assert ca.require_owner(conv, OWNER, store=store).is_owner

    def test_a_stranger_leaving_learns_nothing(self, store, conv):
        payload, status = _call(store, "leave_conversation",
                                {"conversation_id": conv}, STRANGER)
        assert status == "404" and payload == {"error": "Conversation not found"}


# -- Listing -----------------------------------------------------------

class TestListing:

    def test_owner_sees_the_whole_acl(self, store, conv):
        _invite(store, conv)
        _invite(store, conv, target=STRANGER, role=ca.ROLE_READ)
        payload, status = _call(store, "list_collaborators",
                                {"conversation_id": conv}, OWNER)
        assert status == "200" and payload["role"] == ca.ROLE_OWNER
        assert payload["owner"] == OWNER
        assert sorted(r["user_id"] for r in payload["collaborators"]) == \
            sorted([COLLAB, STRANGER])

    def test_a_collaborator_sees_only_their_own_row(self, store, conv):
        _invite(store, conv)
        _invite(store, conv, target=STRANGER)
        _accept(store, conv)
        payload, _ = _call(store, "list_collaborators",
                           {"conversation_id": conv}, COLLAB)
        assert [r["user_id"] for r in payload["collaborators"]] == [COLLAB]
        assert payload["owner"] == OWNER

    def test_a_pending_invitee_sees_their_invite_but_not_the_others(self, store, conv):
        _invite(store, conv)
        _invite(store, conv, target=STRANGER)
        payload, status = _call(store, "list_collaborators",
                                {"conversation_id": conv}, COLLAB)
        assert status == "200"
        assert [r["user_id"] for r in payload["collaborators"]] == [COLLAB]
        assert payload["role"] == ""

    def test_an_outsider_listing_gets_the_not_found_answer(self, store, conv):
        payload, status = _call(store, "list_collaborators",
                                {"conversation_id": conv}, STRANGER)
        assert status == "404" and payload == {"error": "Conversation not found"}

    def test_shared_list_carries_pending_and_accepted_rows(self, store, conv):
        store.set_extra(conv, "title", "Release plan", user_id=OWNER)
        _invite(store, conv)
        payload, status = _call(store, "list_shared_conversations", {}, COLLAB)
        assert status == "200"
        row = payload["conversations"][0]
        assert row["conversation_id"] == conv
        assert row["owner"] == OWNER and row["status"] == ca.STATUS_PENDING
        assert row["title"] == "Release plan"
        _accept(store, conv)
        payload, _ = _call(store, "list_shared_conversations", {}, COLLAB)
        assert payload["conversations"][0]["status"] == ca.STATUS_ACCEPTED

    def test_shared_list_never_shows_a_users_own_conversations(self, store, conv):
        payload, _ = _call(store, "list_shared_conversations", {}, OWNER)
        assert payload["conversations"] == []

    def test_a_stale_index_entry_is_dropped_not_trusted(self, store, conv):
        """The index is a candidate list; the ACL decides what survives."""
        path = store.shared_index_path(STRANGER)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([conv]), encoding="utf-8")
        payload, _ = _call(store, "list_shared_conversations", {}, STRANGER)
        assert payload["conversations"] == []

    def test_a_deleted_conversation_drops_out_of_the_shared_list(self, store, conv):
        _invite(store, conv)
        _accept(store, conv)
        store.delete(conv, user_id=OWNER)
        payload, _ = _call(store, "list_shared_conversations", {}, COLLAB)
        assert payload["conversations"] == []


# -- The gate the same phase puts on the conversation handlers ---------

class TestConvCoreAuthorization:

    @pytest.fixture
    def talking(self, store):
        """A conversation with one real message, owned by alice."""
        cid = store.generate_id()
        store.save(cid, [{"role": "user", "content": "ship the release"}],
                   user_id=OWNER)
        return cid

    def test_owner_reads_history_exactly_as_before(self, store, talking):
        payload, status = _call(store, "load_history",
                                {"conversation_id": talking}, OWNER)
        assert status == "200" and payload["message_count"] == 1

    def test_accepted_collaborator_reads_the_owners_history(self, store, talking):
        _invite(store, talking, role=ca.ROLE_READ)
        _accept(store, talking)
        payload, status = _call(store, "load_history",
                                {"conversation_id": talking}, COLLAB)
        assert status == "200" and payload["message_count"] == 1

    def test_stranger_gets_404_on_history_search_and_poll(self, store, talking):
        for action, body in (
            ("load_history", {"conversation_id": talking}),
            ("search_messages", {"conversation_id": talking, "query": "ship"}),
            ("poll", {"conversation_id": talking, "last_count": 0}),
        ):
            payload, status = _call(store, action, body, STRANGER)
            assert status == "404", action
            assert payload == {"error": "Conversation not found"}, action

    def test_pending_invitee_cannot_read_yet(self, store, talking):
        _invite(store, talking)
        payload, status = _call(store, "load_history",
                                {"conversation_id": talking}, COLLAB)
        assert status == "404"

    def test_a_stranger_can_no_longer_rename_someone_elses_conversation(self, store, conv):
        payload, status = _call(store, "set_conv_title", {
            "conversation_id": conv, "title": "hijacked",
        }, STRANGER)
        assert status == "404"
        assert store.get_extra(conv, "title") != "hijacked"

    def test_a_write_collaborator_can_rename(self, store, conv):
        _invite(store, conv)
        _accept(store, conv)
        payload, status = _call(store, "set_conv_title", {
            "conversation_id": conv, "title": "Release plan",
        }, COLLAB)
        assert status == "200" and payload["title"] == "Release plan"
        assert store.get_extra(conv, "title") == "Release plan"

    def test_a_read_collaborator_cannot_rename(self, store, conv):
        _invite(store, conv, role=ca.ROLE_READ)
        _accept(store, conv)
        payload, status = _call(store, "set_conv_title", {
            "conversation_id": conv, "title": "nope",
        }, COLLAB)
        assert status == "404"
        assert store.get_extra(conv, "title") != "nope"

    def test_encryption_management_stays_owner_only(self, store, conv):
        _invite(store, conv)
        _accept(store, conv)
        payload, status = _call(store, "conv_encrypt_status",
                                {"conversation_id": conv}, COLLAB)
        assert status == "404"
        payload, status = _call(store, "conv_encrypt_status",
                                {"conversation_id": conv}, OWNER)
        assert status == "200" and payload["state"] == "off"

    def test_a_collaborator_cannot_delete_the_conversation(self, store, conv):
        _invite(store, conv)
        _accept(store, conv)
        payload, status = _call(store, "delete_conversation",
                                {"conversation_id": conv}, COLLAB)
        assert status == "404"
        assert store.exists(conv)

    def test_a_kicked_collaborator_loses_read_on_the_next_request(self, store, talking):
        _invite(store, talking)
        _accept(store, talking)
        assert _call(store, "load_history",
                     {"conversation_id": talking}, COLLAB)[1] == "200"
        _call(store, "kick_collaborator",
              {"conversation_id": talking, "collaborator_id": COLLAB}, OWNER)
        assert _call(store, "load_history",
                     {"conversation_id": talking}, COLLAB)[1] == "404"
