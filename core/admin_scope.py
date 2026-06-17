"""Admin cross-user scope helpers.

Two strictly-additive admin capabilities over the scoped repository surfaces
(services, flows, resource depots):

  * view-all  -- an admin may list resources for every user/conversation, with
                 each row labelled by owner. A non-admin (or a request without
                 the flag) is unaffected.
  * owner override -- an admin creating a user/conv-scoped resource may target a
                 different owner. Absent an explicit, differing target the owner
                 is the caller, exactly as before.

The gate is the role attribute on the FlowFile (``http.auth.roles``), identical
to the inline ``\"admin\" in ...`` checks already used across the action
handlers. Identity (display names, conversation ownership) is resolved against
SecurityManager / ConversationStore.

Raise contract:
  * PermissionError -> the caller maps it to HTTP 403 (non-admin override).
  * ValueError      -> the caller maps it to HTTP 400 (unknown user / conv-owner
                       mismatch).
"""

import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def is_admin(flowfile) -> bool:
    """True when the authenticated session carries the admin role."""
    try:
        roles = flowfile.get_attribute("http.auth.roles") or ""
    except Exception:
        return False
    return "admin" in roles


def wants_view_all(body, flowfile) -> bool:
    """True only when an admin explicitly requested the cross-user view.

    A non-admin asking for ``view=all`` is silently downgraded to self view --
    no error, no disclosure that other users exist.
    """
    if not isinstance(body, dict):
        return False
    if str(body.get("view", "") or "").strip().lower() != "all":
        return False
    return is_admin(flowfile)


def _user_exists(user_id: str) -> bool:
    try:
        from core.security import SecurityManager
        return SecurityManager.get_instance().get_user(user_id) is not None
    except Exception:
        logger.debug("admin_scope: user existence check failed", exc_info=True)
        return False


def _conv_owner(conv_id: str) -> str:
    """Return the user_id that owns a conversation, or "" if unknown."""
    if not conv_id:
        return ""
    try:
        from core.conversation_store import ConversationStore
        meta = ConversationStore.instance().get_metadata(conv_id)
        if isinstance(meta, dict):
            return meta.get("user_id", "") or ""
    except Exception:
        logger.debug("admin_scope: conv owner lookup failed", exc_info=True)
    return ""


def effective_owner(body, caller_user_id: str, caller_conv_id: str,
                    flowfile, scope: str) -> Tuple[str, str]:
    """Resolve the (owner_user_id, owner_conv_id) for a create/write.

    Default (no override, or a non-differing one) -> the caller, so the existing
    behaviour is bit-for-bit preserved. An admin may override via
    ``target_user_id`` / ``target_conversation_id`` in the request body.

    scope is one of: global, user, conv/conversation. Global has no per-user
    owner, so it always resolves to ("", "").
    """
    norm = "conv" if scope in ("conv", "conversation") else scope
    if norm == "global":
        return "", ""

    target_user = str((body or {}).get("target_user_id", "") or "").strip()
    target_conv = str(
        (body or {}).get("target_conversation_id", "") or "").strip()

    # No override requested -> caller, unchanged.
    if not target_user and not target_conv:
        return caller_user_id, caller_conv_id

    # An override that does not actually change the owner is a no-op too.
    if (target_user in ("", caller_user_id)
            and target_conv in ("", caller_conv_id)):
        return caller_user_id, caller_conv_id

    if not is_admin(flowfile):
        raise PermissionError(
            "Owner override requires admin role.")

    if norm == "user":
        owner_user = target_user or caller_user_id
        if not _user_exists(owner_user):
            raise ValueError(f"Unknown target user: {owner_user!r}")
        return owner_user, ""

    # conv scope: need a conversation, and it must belong to the owner.
    owner_conv = target_conv or caller_conv_id
    if not owner_conv:
        raise ValueError(
            "target_conversation_id is required for conversation scope.")
    conv_owner = _conv_owner(owner_conv)
    owner_user = target_user or conv_owner or caller_user_id
    if not _user_exists(owner_user):
        raise ValueError(f"Unknown target user: {owner_user!r}")
    if conv_owner and conv_owner != owner_user:
        raise ValueError(
            "Conversation does not belong to the target user.")
    logger.info("admin owner override: scope=%s owner_user=%s owner_conv=%s",
                norm, owner_user, owner_conv[:8] if owner_conv else "")
    return owner_user, owner_conv


_DISPLAY_CACHE: dict = {}


def display_name_for(user_id: str) -> str:
    """Human-readable name for a user_id; falls back to the id itself."""
    if not user_id:
        return ""
    cached = _DISPLAY_CACHE.get(user_id)
    if cached is not None:
        return cached
    name = user_id
    try:
        from core.security import SecurityManager
        user = SecurityManager.get_instance().get_user(user_id)
        if user is not None:
            name = getattr(user, "display_name", "") or user_id
    except Exception:
        logger.debug("admin_scope: display name lookup failed", exc_info=True)
    _DISPLAY_CACHE[user_id] = name
    return name


def invalidate_display_cache(user_id: Optional[str] = None) -> None:
    if user_id is None:
        _DISPLAY_CACHE.clear()
    else:
        _DISPLAY_CACHE.pop(user_id, None)


def conv_index() -> dict:
    """Map every conversation id to {"owner": user_id, "title": title}.

    Built from a single ConversationStore.list_conversations() sweep so the
    view-all listing handlers can both label rows and enumerate conv-scoped
    resources without per-conversation lookups. Best-effort: returns {} on
    failure.
    """
    out: dict = {}
    try:
        from core.conversation_store import ConversationStore
        for c in ConversationStore.instance().list_conversations():
            cid = c.get("conversation_id", "")
            if not cid:
                continue
            out[cid] = {
                "owner": c.get("user_id", "") or "",
                "title": c.get("title", "") or "",
            }
    except Exception:
        logger.debug("admin_scope: conv index build failed", exc_info=True)
    return out
