"""AgentLoopTask actions - conversation sharing (collaborators).

Phase 3 of docs/CONVERSATION_SHARING_PLAN.md: the write-path actions that
make sharing reachable. Every one of them resolves authorization through
``core.conversation_access`` -- the ACL is never re-implemented here.

Two rules shape the error handling below:

- **Never leak existence.** Any request whose requester cannot see the
  conversation answers with the same 404 an unknown ``conversation_id``
  produces. That includes an invitee guessing at an invite they were never
  sent: no pending row means 404, not "exists but not for you".
- **The invite is two-sided.** ``share_conversation`` only ever creates a
  ``pending`` row; access starts when the invitee accepts, never before.
"""

import json
import logging

import core.conversation_access as ca
from tasks.ai.actions._conv_base import _UNHANDLED

logger = logging.getLogger(__name__)

_SHARING_ACTIONS = (
    "share_conversation",
    "list_collaborators",
    "respond_to_share_invite",
    "update_collaborator_role",
    "kick_collaborator",
    "leave_conversation",
    "list_shared_conversations",
)


def _json(flowfile, payload, status="200"):
    flowfile.set_content(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    if status != "200":
        flowfile.set_attribute("http.response.status", status)
    return [flowfile]


def _not_found(flowfile):
    """The single rejection shape: unknown and forbidden are the same answer."""
    return _json(flowfile, {"error": "Conversation not found"}, "404")


def _user_exists(user_id: str) -> bool:
    from core.conversation_access import user_exists
    return user_exists(user_id)


def _row_view(row, owner_user_id):
    return {
        "user_id": row["user_id"],
        "role": row["role"],
        "status": row["status"],
        "invited_by": row["invited_by"] or owner_user_id,
        "invited_at": row["invited_at"],
        "responded_at": row["responded_at"],
    }


def _handle_conv_sharing(self, action, body, store, user_id, flowfile):
    """Conversation actions cluster: sharing. Returns result or _UNHANDLED.

    ``user_id`` is the authenticated requester (``http.auth.principal``),
    never a verified owner -- that is exactly what gets resolved here.
    """
    if action not in _SHARING_ACTIONS:
        return _UNHANDLED

    if action == "list_shared_conversations":
        return _list_shared(store, user_id, flowfile)

    conv_id = str(body.get("conversation_id", "") or "").strip()
    if not conv_id:
        return _json(flowfile, {"error": "Missing conversation_id"}, "400")

    if action == "share_conversation":
        return _share(store, conv_id, user_id, body, flowfile)
    if action == "list_collaborators":
        return _list_collaborators(store, conv_id, user_id, flowfile)
    if action == "respond_to_share_invite":
        return _respond(store, conv_id, user_id, body, flowfile)
    if action == "update_collaborator_role":
        return _update_role(store, conv_id, user_id, body, flowfile)
    if action == "kick_collaborator":
        return _kick(store, conv_id, user_id, body, flowfile)
    return _leave(store, conv_id, user_id, flowfile)


def _share(store, conv_id, user_id, body, flowfile):
    """Owner-only invite. Re-inviting an accepted collaborator only sets the
    role: flipping them back to ``pending`` would silently revoke access."""
    try:
        access = ca.require_owner(conv_id, user_id, store=store)
    except ca.ConversationAccessError:
        return _not_found(flowfile)

    target = str(body.get("collaborator_id", "") or "").strip()
    if not target:
        return _json(flowfile, {"error": "Missing collaborator_id"}, "400")
    if target == access.owner_user_id:
        return _json(flowfile, {
            "error": "The owner already has access to this conversation",
        }, "400")
    if not _user_exists(target):
        return _json(flowfile, {"error": f"Unknown user: {target}"}, "400")

    role = str(body.get("role", "") or ca.ROLE_WRITE).strip().lower()
    if role not in ca.COLLABORATOR_ROLES:
        return _json(flowfile, {"error": f"Invalid role: {role}"}, "400")

    existing = ca.get_collaborator(conv_id, target, store=store)
    status = "" if (existing and existing["status"] == ca.STATUS_ACCEPTED) \
        else ca.STATUS_PENDING
    row = ca.set_collaborator(conv_id, target, role=role, status=status,
                              invited_by=user_id, store=store)
    logger.info("[action] share_conversation %s -> %s (role=%s, status=%s)",
                conv_id[:8], target, row["role"], row["status"])
    return _json(flowfile, {"ok": True, "conversation_id": conv_id,
                            "collaborator": _row_view(row, access.owner_user_id)})


def _list_collaborators(store, conv_id, user_id, flowfile):
    """The owner sees the whole ACL; a collaborator sees only their own row."""
    access = ca.resolve_conversation_access(conv_id, user_id, store=store)
    rows = []
    if access.is_owner:
        rows = ca.get_collaborators(conv_id, store=store)
    elif access.can_read:
        own = ca.get_collaborator(conv_id, user_id, store=store)
        rows = [own] if own else []
    else:
        # Includes the pending invitee: they may respond to the invite
        # without being able to enumerate who else was invited.
        pending = ca.get_collaborator(conv_id, user_id, store=store)
        if pending is None or pending["status"] != ca.STATUS_PENDING:
            return _not_found(flowfile)
        rows = [pending]
    return _json(flowfile, {
        "conversation_id": conv_id,
        "owner": access.owner_user_id,
        "role": access.role,
        "collaborators": [_row_view(r, access.owner_user_id) for r in rows],
    })


def _respond(store, conv_id, user_id, body, flowfile):
    """Invitee-only: accept turns the pending row into access, decline erases it."""
    response = str(body.get("response", "") or "").strip().lower()
    if response not in ("accept", "decline"):
        return _json(flowfile, {
            "error": "response must be 'accept' or 'decline'",
        }, "400")
    row = ca.get_collaborator(conv_id, user_id, store=store)
    if row is None or row["status"] != ca.STATUS_PENDING:
        return _not_found(flowfile)
    if response == "decline":
        ca.remove_collaborator(conv_id, user_id, store=store)
        return _json(flowfile, {"ok": True, "conversation_id": conv_id,
                                "status": "declined"})
    accepted = ca.set_collaborator(conv_id, user_id,
                                   status=ca.STATUS_ACCEPTED, store=store)
    logger.info("[action] respond_to_share_invite %s accepted by %s (role=%s)",
                conv_id[:8], user_id, accepted["role"])
    return _json(flowfile, {"ok": True, "conversation_id": conv_id,
                            "status": accepted["status"],
                            "role": accepted["role"]})


def _update_role(store, conv_id, user_id, body, flowfile):
    try:
        access = ca.require_owner(conv_id, user_id, store=store)
    except ca.ConversationAccessError:
        return _not_found(flowfile)
    target = str(body.get("collaborator_id", "") or "").strip()
    role = str(body.get("role", "") or "").strip().lower()
    if not target:
        return _json(flowfile, {"error": "Missing collaborator_id"}, "400")
    if role not in ca.COLLABORATOR_ROLES:
        return _json(flowfile, {"error": f"Invalid role: {role}"}, "400")
    row = ca.get_collaborator(conv_id, target, store=store)
    if row is None or row["status"] == ca.STATUS_KICKED:
        return _json(flowfile, {"error": "Collaborator not found"}, "404")
    updated = ca.set_collaborator(conv_id, target, role=role, store=store)
    return _json(flowfile, {"ok": True, "conversation_id": conv_id,
                            "collaborator": _row_view(updated,
                                                      access.owner_user_id)})


def _kick(store, conv_id, user_id, body, flowfile):
    """Owner-only revoke. The row survives as ``kicked`` for the audit trail."""
    try:
        access = ca.require_owner(conv_id, user_id, store=store)
    except ca.ConversationAccessError:
        return _not_found(flowfile)
    target = str(body.get("collaborator_id", "") or "").strip()
    if not target:
        return _json(flowfile, {"error": "Missing collaborator_id"}, "400")
    row = ca.get_collaborator(conv_id, target, store=store)
    if row is None or row["status"] == ca.STATUS_KICKED:
        return _json(flowfile, {"error": "Collaborator not found"}, "404")
    kicked = ca.set_collaborator(conv_id, target, status=ca.STATUS_KICKED,
                                 store=store)
    logger.info("[action] kick_collaborator %s -> %s", conv_id[:8], target)
    return _json(flowfile, {"ok": True, "conversation_id": conv_id,
                            "collaborator": _row_view(kicked,
                                                      access.owner_user_id)})


def _leave(store, conv_id, user_id, flowfile):
    """Self-service kick. Same terminal status, so the audit trail reads the
    same way whether access ended by the owner's hand or the collaborator's."""
    access = ca.resolve_conversation_access(conv_id, user_id, store=store)
    if access.is_owner:
        return _json(flowfile, {
            "error": "The owner cannot leave their own conversation",
        }, "400")
    if not access.can_read:
        return _not_found(flowfile)
    ca.set_collaborator(conv_id, user_id, status=ca.STATUS_KICKED, store=store)
    logger.info("[action] leave_conversation %s by %s", conv_id[:8], user_id)
    return _json(flowfile, {"ok": True, "conversation_id": conv_id})


def _list_shared(store, user_id, flowfile):
    """Conversations shared *with* the requester, pending invites included.

    The reverse index is a candidate list, not an authorization: every cid it
    returns is re-checked against the ACL, so a stale entry drops out here
    instead of granting anything.
    """
    rows = []
    for cid in ca.shared_conversation_ids(user_id, store=store):
        row = ca.get_collaborator(cid, user_id, store=store)
        if row is None or row["status"] not in (ca.STATUS_PENDING,
                                                ca.STATUS_ACCEPTED):
            continue
        meta = store.get_metadata(cid)
        if meta is None:
            continue  # conversation deleted since the invite was sent
        rows.append({
            "conversation_id": cid,
            "owner": meta.get("user_id", ""),
            "role": row["role"],
            "status": row["status"],
            "invited_by": row["invited_by"] or meta.get("user_id", ""),
            "invited_at": row["invited_at"],
            "title": store.get_extra(cid, "title", "") or "",
            "message_count": meta.get("message_count", 0),
            "updated_at": meta.get("updated_at", 0),
        })
    rows.sort(key=lambda r: r["updated_at"], reverse=True)
    return _json(flowfile, {"conversations": rows})
