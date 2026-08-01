import json
import uuid

import pytest

import core.conversation_access as ca
from core import FlowFile
from core.conversation_store import ConversationStore
from core.flow_runtime_access import (
    ROLE_OWNER,
    ROLE_READ,
    FlowRuntimeAccessError,
    authorize_conversation_target,
    authorize_filestore_target,
    make_runtime_context,
)
from tasks.io.read_conversation import ReadConversationTask


def _cid(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _save_conv(cid, user_id, text="hello"):
    ConversationStore.instance().save(
        cid, [{"role": "user", "content": text}], user_id=user_id)


def _json_content(flowfile):
    return json.loads(flowfile.get_content().decode("utf-8"))


def test_user_scope_can_target_own_conversation_only():
    own = _cid("own")
    other = _cid("other")
    _save_conv(own, "alice")
    _save_conv(other, "bob")

    ctx = make_runtime_context(scope="user", user_id="alice")

    assert authorize_conversation_target(ctx, own) == own
    try:
        authorize_conversation_target(ctx, other)
    except FlowRuntimeAccessError as exc:
        assert str(exc) == "Permission denied"
    else:
        raise AssertionError("cross-user conversation access should be denied")


def test_conversation_scope_cannot_target_another_conversation_same_user():
    first = _cid("first")
    second = _cid("second")
    _save_conv(first, "alice")
    _save_conv(second, "alice")

    ctx = make_runtime_context(
        scope="conversation", user_id="alice", conversation_id=first)

    assert authorize_conversation_target(ctx, first) == first
    try:
        authorize_conversation_target(ctx, second)
    except FlowRuntimeAccessError as exc:
        assert str(exc) == "Permission denied"
    else:
        raise AssertionError("cross-conversation access should be denied")


def test_global_scope_is_bounded_by_trusted_requester_for_builtin_runtimes():
    own = _cid("own")
    other = _cid("other")
    _save_conv(own, "alice", "owned")
    _save_conv(other, "bob", "foreign")
    ctx = make_runtime_context(scope="global")

    assert authorize_conversation_target(
        ctx, own, requester_user_id="alice") == own
    try:
        authorize_conversation_target(ctx, other, requester_user_id="alice")
    except FlowRuntimeAccessError as exc:
        assert str(exc) == "Permission denied"
    else:
        raise AssertionError("global requester access must stay within requester owner")


def test_global_scope_admin_opt_in_can_target_any_conversation():
    target = _cid("admin")
    _save_conv(target, "bob")

    assert authorize_conversation_target(
        make_runtime_context(scope="global"), target,
        allow_global_admin=True) == target


def test_global_scope_admin_opt_in_can_target_all_filestore_when_unbounded():
    assert authorize_filestore_target(
        make_runtime_context(scope="global"), allow_global_admin=True) == ("", "")


def test_read_conversation_enforces_user_scope():
    own = _cid("own")
    other = _cid("other")
    _save_conv(own, "alice", "owned")
    _save_conv(other, "bob", "foreign")

    task = ReadConversationTask({"conversation_id": other})
    task.set_runtime_context(scope="user", user_id="alice")

    result = task.execute(FlowFile(content=b""))[0]

    assert _json_content(result) == {"error": "Permission denied"}


def test_read_conversation_allows_global_builtin_requester_for_own_conv():
    own = _cid("own")
    _save_conv(own, "alice", "owned")

    task = ReadConversationTask({"conversation_id": own})
    task.set_runtime_context(scope="global")
    ff = FlowFile(content=b"", attributes={"http.auth.principal": "alice"})

    result = _json_content(task.execute(ff)[0])

    assert result["conversation_id"] == own
    assert result["messages"][0]["content"] == "owned"


def test_read_conversation_denies_global_builtin_requester_for_other_user():
    other = _cid("other")
    _save_conv(other, "bob", "foreign")

    task = ReadConversationTask({"conversation_id": other})
    task.set_runtime_context(scope="global")
    ff = FlowFile(content=b"", attributes={"http.auth.principal": "alice"})

    result = task.execute(ff)[0]

    assert _json_content(result) == {"error": "Permission denied"}


# -- Collaborators (phase 4) -------------------------------------------
#
# The scope bound and the ACL are two different questions. These pin that
# a collaborator satisfies the ACL half without loosening the scope half,
# and that the role a call site asks for is the role it gets.

def _shared(cid, owner, collaborator, role=ca.ROLE_WRITE):
    _save_conv(cid, owner, "shared")
    ca.set_collaborator(cid, collaborator, role=role,
                        status=ca.STATUS_ACCEPTED, invited_by=owner)
    return cid


def test_user_scope_reaches_a_conversation_shared_with_that_user():
    cid = _shared(_cid("shared"), "alice", "bob")
    ctx = make_runtime_context(scope="user", user_id="bob")

    assert authorize_conversation_target(ctx, cid) == cid


def test_global_scope_requester_reaches_a_conversation_shared_with_them():
    cid = _shared(_cid("shared"), "alice", "bob")

    assert authorize_conversation_target(
        make_runtime_context(scope="global"), cid,
        requester_user_id="bob") == cid


def test_a_read_collaborator_is_denied_a_write_target_and_allowed_a_read_one():
    cid = _shared(_cid("shared"), "alice", "bob", role=ca.ROLE_READ)
    ctx = make_runtime_context(scope="user", user_id="bob")

    # ROLE_WRITE is the default precisely so an unreviewed call site denies.
    with pytest.raises(FlowRuntimeAccessError):
        authorize_conversation_target(ctx, cid)
    assert authorize_conversation_target(
        ctx, cid, required_role=ROLE_READ) == cid


def test_a_write_collaborator_is_still_denied_owner_only_operations():
    cid = _shared(_cid("shared"), "alice", "bob")
    ctx = make_runtime_context(scope="user", user_id="bob")

    with pytest.raises(FlowRuntimeAccessError):
        authorize_conversation_target(ctx, cid, required_role=ROLE_OWNER)


def test_a_pending_invite_grants_nothing_to_the_flow_runtime():
    cid = _cid("pending")
    _save_conv(cid, "alice")
    ca.set_collaborator(cid, "bob", role=ca.ROLE_WRITE,
                        status=ca.STATUS_PENDING, invited_by="alice")
    ctx = make_runtime_context(scope="user", user_id="bob")

    with pytest.raises(FlowRuntimeAccessError):
        authorize_conversation_target(ctx, cid)


def test_conversation_scope_stays_a_scope_bound_for_a_collaborator_too():
    # Sharing widens who may reach a conversation, never which conversation a
    # deployed flow may reach.
    mine = _shared(_cid("mine"), "alice", "bob")
    other = _shared(_cid("other"), "alice", "bob")
    ctx = make_runtime_context(
        scope="conversation", user_id="bob", conversation_id=mine)

    assert authorize_conversation_target(ctx, mine) == mine
    with pytest.raises(FlowRuntimeAccessError):
        authorize_conversation_target(ctx, other)


def test_read_conversation_serves_a_collaborator_from_the_owners_storage():
    cid = _shared(_cid("shared"), "alice", "bob", role=ca.ROLE_READ)

    task = ReadConversationTask({"conversation_id": cid})
    task.set_runtime_context(scope="global")
    ff = FlowFile(content=b"", attributes={"http.auth.principal": "bob"})

    result = _json_content(task.execute(ff)[0])

    assert result["conversation_id"] == cid
    assert result["messages"][0]["content"] == "shared"
