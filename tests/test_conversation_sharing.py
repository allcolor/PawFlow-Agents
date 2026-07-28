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


# -- Message submission ------------------------------------------------

class TestMessageSubmissionGate:
    """The write-side twin of the SSE gate (phase 4).

    Nothing below the gate is partitioned by requester: the agent context,
    the agent config and the CLI session state all load from the owner's
    directory using the conversation_id alone.
    """

    def test_a_stranger_cannot_submit_into_someone_elses_conversation(
            self, store, conv):
        with pytest.raises(ca.ConversationAccessError):
            ca.authorize_message_submission(conv, STRANGER, store=store)

    def test_an_invited_but_not_yet_accepted_user_cannot_submit(self, store, conv):
        _invite(store, conv)
        with pytest.raises(ca.ConversationAccessError):
            ca.authorize_message_submission(conv, COLLAB, store=store)

    def test_a_read_collaborator_cannot_submit(self, store, conv):
        _invite(store, conv, role=ca.ROLE_READ)
        _accept(store, conv)
        with pytest.raises(ca.ConversationAccessError):
            ca.authorize_message_submission(conv, COLLAB, store=store)

    def test_a_write_collaborator_submits_against_the_owners_storage(
            self, store, conv):
        _invite(store, conv)
        _accept(store, conv)
        access = ca.authorize_message_submission(conv, COLLAB, store=store)
        assert access.role == ca.ROLE_WRITE
        assert access.storage_user_id == OWNER

    def test_the_owner_submits_unchanged(self, store, conv):
        access = ca.authorize_message_submission(conv, OWNER, store=store)
        assert access.is_owner and access.storage_user_id == OWNER

    def test_an_unknown_conversation_passes_through_to_be_created(self, store):
        # A client may mint its own id; the submission creates it and the
        # requester becomes the owner. Raising here would break creation.
        access = ca.authorize_message_submission(
            "never-existed", STRANGER, store=store)
        assert access.role == "" and access.owner_user_id == ""

    def test_an_internal_caller_without_a_principal_is_not_rejected(
            self, store, conv):
        # Flow tasks, the task poller and sub-agents carry no trusted
        # principal; they are authorized upstream by flow_runtime_access.
        assert ca.authorize_message_submission(conv, "", store=store).role == ""

    def test_a_kicked_collaborator_cannot_submit_again(self, store, conv):
        _invite(store, conv)
        _accept(store, conv)
        _call(store, "kick_collaborator",
              {"conversation_id": conv, "collaborator_id": COLLAB}, OWNER)
        with pytest.raises(ca.ConversationAccessError):
            ca.authorize_message_submission(conv, COLLAB, store=store)


# -- Streaming submit path ---------------------------------------------

class TestStreamingSubmitGate:
    """The gate has to sit above the pre-persist, not below the thread.

    ``_execute_streaming`` writes the user message to the transcript and
    publishes it to every SSE subscriber before handing the turn to a
    background thread. A check placed in ``_prepare_agent_context`` -- which
    runs *on that thread* -- fires after the write it was meant to prevent,
    and raises where no HTTP response is left to answer 404 with.
    """

    @pytest.fixture
    def talking(self, store):
        cid = store.generate_id()
        store.save(cid, [{"role": "user", "content": "ship the release"}],
                   user_id=OWNER)
        return cid

    def _task_and_flowfile(self, cid, requester):
        from unittest.mock import MagicMock
        from tasks.ai.agent_loop import AgentLoopTask

        task = AgentLoopTask({
            "api_key": "test-key",
            "streaming": True,
            "conversation_store": True,
        })
        task._streaming_agent_loop = MagicMock()
        task._prepare_agent_context = MagicMock()
        ff = FlowFile(content=json.dumps({
            "message": "give me the transcript",
            "conversation_id": cid,
            "target_agent": "assistant",
        }).encode(), attributes={"http.auth.principal": requester})
        return task, ff

    def _run(self, store, cid, requester):
        from unittest.mock import MagicMock, patch

        task, ff = self._task_and_flowfile(cid, requester)
        with patch("core.conversation_store.ConversationStore.instance",
                   return_value=store), \
                patch("core.conversation_writer.ConversationWriter."
                      "for_conversation") as writer_for_conv:
            writer_for_conv.return_value.enqueue_message = MagicMock()
            results = task._execute_streaming(ff)
            enqueued = writer_for_conv.return_value.enqueue_message
        return results[0], enqueued, task

    def test_a_stranger_is_refused_before_anything_is_written(
            self, store, talking):
        out, enqueued, task = self._run(store, talking, STRANGER)

        assert out.get_attribute("http.response.status") == "404"
        assert json.loads(out.get_content().decode())["error"] == \
            "Conversation not found"
        # The message never reached the transcript, and no turn was started.
        enqueued.assert_not_called()
        task._streaming_agent_loop.assert_not_called()
        task._prepare_agent_context.assert_not_called()

    def test_a_read_collaborator_is_refused_too(self, store, talking):
        _invite(store, talking, role=ca.ROLE_READ)
        _accept(store, talking)

        out, enqueued, _task = self._run(store, talking, COLLAB)

        assert out.get_attribute("http.response.status") == "404"
        enqueued.assert_not_called()

    def test_the_owner_is_not_refused(self, store, talking):
        out, _enqueued, _task = self._run(store, talking, OWNER)

        assert out.get_attribute("http.response.status") != "404"

    def test_a_write_collaborator_is_not_refused(self, store, talking):
        _invite(store, talking)
        _accept(store, talking)

        out, _enqueued, _task = self._run(store, talking, COLLAB)

        assert out.get_attribute("http.response.status") != "404"


# -- Dispatcher table (phase 5) ----------------------------------------

class TestActionRoleTable:
    """The clusters that address git, archives and the FileStore by id.

    None of them is partitioned by requester, so before the table any
    authenticated user knowing an id could roll back, delete branches or
    export someone else's conversation.
    """

    @pytest.fixture
    def talking(self, store):
        """A conversation with one real message, owned by alice."""
        cid = store.generate_id()
        store.save(cid, [{"role": "user", "content": "ship the release"}],
                   user_id=OWNER)
        return cid

    def test_every_handled_action_is_classified(self):
        # The point of the table is that it is an inventory. An action added
        # to one of these clusters without a row here is ungated, and this
        # test is what makes that visible instead of silent.
        import re
        from pathlib import Path
        from tasks.ai.actions import conversation as dispatcher

        known = set(dispatcher._ACTION_ROLES) | set(dispatcher._UNSCOPED_ACTIONS)
        root = Path(dispatcher.__file__).parent
        handled = set()
        for module in ("_conv_ops", "_conv_tags_export", "_conv_import"):
            source = (root / f"{module}.py").read_text(encoding="utf-8")
            handled |= set(re.findall(r'action == "([a-z_]+)"', source))

        assert handled, "action scan found nothing -- the regex went stale"
        assert handled - known == set()

    def test_a_stranger_cannot_read_the_git_log(self, store, conv):
        assert _call(store, "conv_git_log",
                     {"conversation_id": conv}, STRANGER)[1] == "404"

    def test_a_stranger_cannot_export_a_conversation(self, store, talking):
        assert _call(store, "export",
                     {"conversation_id": talking}, STRANGER)[1] == "404"

    def test_a_read_collaborator_can_export_but_not_tag(self, store, talking):
        _invite(store, talking, role=ca.ROLE_READ)
        _accept(store, talking)
        assert _call(store, "export",
                     {"conversation_id": talking}, COLLAB)[1] == "200"
        assert _call(store, "conv_tag",
                     {"conversation_id": talking, "tag_name": "v1"},
                     COLLAB)[1] == "404"

    def test_a_write_collaborator_cannot_roll_back_history(self, store, conv):
        # Owner-only: a rollback discards history for every participant.
        _invite(store, conv)
        _accept(store, conv)
        assert _call(store, "conv_rollback",
                     {"conversation_id": conv, "commit_hash": "deadbee"},
                     COLLAB)[1] == "404"
        assert _call(store, "conv_delete_branch",
                     {"conversation_id": conv, "branch_name": "main"},
                     COLLAB)[1] == "404"

    def test_an_action_without_a_conversation_id_keeps_its_own_error(self, store):
        # The gate must not turn a handler's 400 into a 404: the request is
        # malformed, not unauthorized.
        _payload, status = _call(store, "conv_git_log", {}, STRANGER)
        assert status != "404"

    def test_loop_list_refuses_to_enumerate_every_users_loops(self, store):
        _payload, status = _call(store, "loop_list", {}, STRANGER)
        assert status == "400"


# -- Conversation-scoped services (phase 5, second pass) ---------------

class TestConvScopedServiceGate:
    """``scope="conv"`` addresses the service registry by conversation id.

    ``_service_scope_id`` drops the requester for that scope, so every
    service_flow handler that reads ``scope`` from the body acted on whatever
    conversation the request named -- reading its service config, or
    mutating it.
    """

    def _call(self, store, action, body, requester):
        from tasks.ai.actions.service_flow import _handle_service_flow
        ff = FlowFile(content=b"")
        ff.set_attribute("http.auth.principal", requester)
        out = _handle_service_flow(_Task(), action, body, store, requester, ff)
        if out is None:
            return None, "unhandled"
        status = ff.get_attribute("http.response.status") or "200"
        return json.loads(ff.get_content().decode("utf-8")), status

    def test_a_stranger_cannot_read_a_conversation_scoped_service(
            self, store, conv):
        _payload, status = self._call(store, "get_service_detail", {
            "service_id": "anthropic", "scope": "conv",
            "conversation_id": conv,
        }, STRANGER)
        assert status == "404"

    def test_a_stranger_cannot_mutate_a_conversation_scoped_service(
            self, store, conv):
        for action in ("update_service", "delete_service", "toggle_service",
                       "service_enable", "service_disable"):
            _payload, status = self._call(store, action, {
                "service_id": "anthropic", "scope": "conv",
                "conversation_id": conv,
            }, STRANGER)
            assert status == "404", action

    def test_a_read_collaborator_does_not_reach_service_configuration(
            self, store, conv):
        # Service config carries credentials; read access to the transcript
        # is not read access to the conversation's plumbing.
        _invite(store, conv, role=ca.ROLE_READ)
        _accept(store, conv)
        _payload, status = self._call(store, "get_service_detail", {
            "service_id": "anthropic", "scope": "conv",
            "conversation_id": conv,
        }, COLLAB)
        assert status == "404"

    def test_the_owner_is_unaffected(self, store, conv):
        _payload, status = self._call(store, "get_service_detail", {
            "service_id": "nope", "scope": "conv", "conversation_id": conv,
        }, OWNER)
        # Reaches the handler, which answers its own not-found.
        assert status == "200"

    def test_a_write_collaborator_is_unaffected(self, store, conv):
        _invite(store, conv)
        _accept(store, conv)
        _payload, status = self._call(store, "get_service_detail", {
            "service_id": "nope", "scope": "conv", "conversation_id": conv,
        }, COLLAB)
        assert status == "200"

    def test_global_scope_requests_are_untouched(self, store, conv):
        # The default scope is global; the gate must not fire on it, or every
        # ordinary service call would need a conversation.
        _payload, status = self._call(store, "get_service_detail", {
            "service_id": "nope", "conversation_id": conv,
        }, STRANGER)
        assert status != "404"


# -- Owner reassignment (phase 6) --------------------------------------

class TestOwnerReassignment:
    """A conversation lives in its owner's directory.

    Delete that account and the remaining participants would lose a
    conversation they are still entitled to write to, so the first write by
    an accepted write collaborator moves it to them.
    """

    def _delete_owner(self, users):
        users._users.pop(OWNER, None)

    def test_the_first_write_moves_the_conversation_to_the_collaborator(
            self, store, conv, users):
        _invite(store, conv)
        _accept(store, conv)
        self._delete_owner(users)

        access = ca.require_write(conv, COLLAB, store=store)

        assert access.is_owner
        assert access.owner_user_id == COLLAB
        assert access.storage_user_id == COLLAB
        assert store.resolve_owner(conv) == COLLAB

    def test_the_conversation_is_still_readable_after_the_move(
            self, store, users):
        cid = store.generate_id()
        store.save(cid, [{"role": "user", "content": "still here"}],
                   user_id=OWNER)
        _invite(store, cid)
        _accept(store, cid)
        self._delete_owner(users)

        ca.require_write(cid, COLLAB, store=store)

        messages = store.load(cid, user_id=COLLAB)
        assert messages and messages[0]["content"] == "still here"

    def test_a_live_owner_is_never_reassigned(self, store, conv, users):
        _invite(store, conv)
        _accept(store, conv)

        access = ca.require_write(conv, COLLAB, store=store)

        assert access.role == ca.ROLE_WRITE
        assert access.storage_user_id == OWNER
        assert store.resolve_owner(conv) == OWNER

    def test_a_read_collaborator_is_not_a_successor(self, store, conv, users):
        _invite(store, conv, role=ca.ROLE_READ)
        _accept(store, conv)
        self._delete_owner(users)

        # A reader cannot write, so it never reaches reassignment at all.
        with pytest.raises(ca.ConversationAccessError):
            ca.require_write(conv, COLLAB, store=store)
        assert store.resolve_owner(conv) == OWNER

    def test_the_second_writer_finds_the_move_already_done(
            self, store, conv, users):
        # Two collaborators writing right after the owner deletion: the
        # check-then-rename is under the conversation lock, so exactly one
        # move happens and the other resolves against the new owner.
        _invite(store, conv)
        _invite(store, conv, target=STRANGER)
        _accept(store, conv)
        _accept(store, conv, who=STRANGER)
        self._delete_owner(users)

        first = ca.require_write(conv, COLLAB, store=store)
        second = ca.require_write(conv, STRANGER, store=store)

        assert first.owner_user_id == COLLAB
        assert second.owner_user_id == COLLAB
        assert second.role == ca.ROLE_WRITE
        assert second.storage_user_id == COLLAB

    def test_a_failed_lookup_does_not_hand_over_the_conversation(
            self, store, conv, users, monkeypatch):
        # An unavailable SecurityManager must not read as "the owner was
        # deleted" -- that would reassign every shared conversation at once.
        _invite(store, conv)
        _accept(store, conv)

        def _boom():
            raise RuntimeError("security manager unavailable")

        monkeypatch.setattr("core.security.SecurityManager.get_instance", _boom)

        access = ca.require_write(conv, COLLAB, store=store)

        assert access.role == ca.ROLE_WRITE
        assert store.resolve_owner(conv) == OWNER

    def test_the_message_path_triggers_the_recovery_too(
            self, store, conv, users):
        # Sending a message is the most common write; if it did not trigger
        # the recovery, a conversation would sit in a deleted owner's
        # directory until some unrelated action happened to call
        # require_write.
        _invite(store, conv)
        _accept(store, conv)
        users._users.pop(OWNER, None)

        access = ca.authorize_message_submission(conv, COLLAB, store=store)

        assert access.is_owner and access.storage_user_id == COLLAB
        assert store.resolve_owner(conv) == COLLAB

    def test_an_empty_user_registry_is_not_evidence_of_deletion(
            self, store, conv, users):
        # A single-user or unauthenticated deployment has no registered users
        # at all. Reading that as "every owner is gone" would reassign every
        # shared conversation on the instance on the next write.
        _invite(store, conv)
        _accept(store, conv)
        users._users.clear()

        access = ca.require_write(conv, COLLAB, store=store)

        assert access.role == ca.ROLE_WRITE
        assert store.resolve_owner(conv) == OWNER
