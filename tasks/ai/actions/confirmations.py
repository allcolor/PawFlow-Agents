"""Webchat actions for durable confirmation requests.

A confirmation is created by an agent (``request_confirmation`` tool) or a
flow (``requestConfirmation`` task) and answered here — whenever the user
wants. Answering resumes the requester (agent wake-up or durable flow
signal); see ``core/confirmation_store.py``.
"""

import json
import logging

logger = logging.getLogger(__name__)

_ACTIONS = {"list_confirmations", "respond_confirmation",
            "cancel_confirmation"}


def _can_touch(record, user_id: str) -> bool:
    """Owner always; collaborators with write access to the conversation."""
    if record["user_id"] == user_id:
        return True
    try:
        from core.conversation_access import resolve_conversation_access
        access = resolve_conversation_access(record["conversation_id"], user_id)
        return bool(access and access.can_write)
    except Exception:
        return False


def _handle_confirmations(self, action, body, store, user_id, flowfile):
    """Handle confirmation actions. Returns [flowfile] or None."""
    if action not in _ACTIONS:
        return None
    from core.confirmation_store import ConfirmationStore
    cstore = ConfirmationStore.instance()

    def _reply(payload, status=""):
        flowfile.set_content(json.dumps(payload, ensure_ascii=False).encode())
        if status:
            flowfile.set_attribute("http.response.status", status)
        return [flowfile]

    if action == "list_confirmations":
        rows = cstore.list_confirmations(
            user_id=user_id,
            conversation_id=body.get("conversation_id", ""),
            status=body.get("status", "pending"),
            limit=int(body.get("limit", 100) or 100))
        return _reply({"confirmations": rows})

    request_id = str(body.get("request_id", "") or "")
    if not request_id:
        return _reply({"error": "request_id is required"}, "400")
    record = cstore.get_confirmation(request_id)
    if not record or not _can_touch(record, user_id):
        # Same answer for unknown and unauthorized: no existence oracle.
        return _reply({"error": "Unknown confirmation request"}, "404")

    if action == "respond_confirmation":
        try:
            updated = cstore.respond(request_id, body.get("answer"),
                                     answered_by=user_id)
        except (KeyError, ValueError) as exc:
            return _reply({"error": str(exc)}, "400")
        return _reply({"ok": True, "confirmation": updated})

    if action == "cancel_confirmation":
        ok = cstore.cancel(request_id, cancelled_by=user_id)
        return _reply({"ok": bool(ok)} if ok else
                      {"error": "Confirmation is not pending"}, "" if ok else "400")

    return None
