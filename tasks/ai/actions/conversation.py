"""AgentLoopTask actions — conversation (dispatcher facade).

The handlers were split into
_conv_core/_conv_ops/_conv_tags_export/_conv_import/_conv_sharing for the
<=800-line rule; this module dispatches by action across them.

It also carries the authorization table for the clusters that do not resolve
access themselves (phase 5 of docs/CONVERSATION_SHARING_PLAN.md). Those
handlers address git, the archive directory and the FileStore by
conversation_id alone -- none of it partitioned by requester -- so before
sharing existed they were reachable by any authenticated user who knew an id.
Keeping the roles in one table rather than in twenty handlers is the point:
the inventory the plan asks for is readable in one screen, and
``test_conversation_sharing`` fails if a handled action is missing from it.
"""
from tasks.ai.actions._conv_base import (
    _UNHANDLED,
    _authorize_conversation_action,
)
from tasks.ai.actions._conv_core import _handle_conv_core
from tasks.ai.actions._conv_ops import _handle_conv_ops
from tasks.ai.actions._conv_tags_export import _handle_conv_tags_export
from tasks.ai.actions._conv_import import _handle_conv_import
from tasks.ai.actions._conv_sharing import _handle_conv_sharing

# Actions of the three unguarded clusters, and the access each needs.
#
# read  -- shows the conversation's content or history
# write -- mutates it, or spends against it
# owner -- destroys history or reaches outside the conversation itself
#
# _conv_core and _conv_sharing are absent on purpose: they already resolve
# access per action and then address storage with the resolved owner id, so a
# second resolution here would only add a metadata read to the poll path.
_ACTION_ROLES = {
    # _conv_ops
    "export": "read",
    "clear": "write",
    "clear_store": "write",
    "loop_start": "write",
    "loop_list": "read",
    "conv_git_log": "read",
    # Reading the source would be enough for confidentiality -- a reader can
    # already export the whole archive -- but ConversationStore.fork commits
    # a "before fork" snapshot into the SOURCE repo, so it mutates what it
    # copies. Classified by what it does, not by what it is for.
    "conv_fork": "write",
    "conv_branch": "write",
    "conv_switch_branch": "write",
    "conv_list_branches": "read",
    "conv_delete_branch": "owner",
    "conv_rollback": "owner",     # discards history for every participant
    # _conv_tags_export
    "conv_tag": "write",
    "conv_list_tags": "read",
    "conv_delete_tag": "write",
    "conv_export_pawflow": "read",
    "conv_export_claude_code": "read",
    # _conv_import
    "conv_compare_branches": "read",
}

# Handled by the same clusters but not scoped to an existing conversation.
# Listed so the completeness test can tell "reviewed, needs no conversation
# check" from "forgotten".
_UNSCOPED_ACTIONS = frozenset({
    "loop_stop",            # keyed by loop; gated inside the handler
    "conv_import_analyze",  # reads an upload owned by the requester
    "conv_import_execute",  # creates a new conversation
    "conv_import_cleanup",  # removes the requester's own temp dir
})


def _authorize_action(action, body, store, user_id, flowfile):
    """Gate one action against the conversation it names.

    Returns a response to send back, or None to continue dispatching. An
    action with no conversation_id falls through: the handler owns that
    error, and answering 404 here would turn its 400 into a lie.
    """
    role = _ACTION_ROLES.get(action)
    if not role:
        return None
    conv_id = str(body.get("conversation_id", "") or "").strip()
    if not conv_id:
        return None
    return _authorize_conversation_action(role, conv_id, user_id, store,
                                          flowfile)


def _handle_conversation(self, action, body, store, user_id, flowfile):
    """Handle conversation actions. Returns [flowfile] or None."""
    _denied = _authorize_action(action, body, store, user_id, flowfile)
    if _denied is not None:
        return _denied
    for _handler in (_handle_conv_core, _handle_conv_ops,
                     _handle_conv_tags_export, _handle_conv_import,
                     _handle_conv_sharing):
        _res = _handler(self, action, body, store, user_id, flowfile)
        if _res is not _UNHANDLED:
            return _res
    return None
