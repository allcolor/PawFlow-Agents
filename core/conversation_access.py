"""Single source of truth for "may this requester act on this conversation".

A conversation has exactly one **owner** (the user whose directory physically
holds it, `{CONVERSATIONS_DIR}/{owner}/{cid}/`) and, once shared, a list of
**collaborators** with a `write` or `read` role. Sharing is purely an
authorization + routing layer: the on-disk layout never changes, and a
conversation with no collaborators resolves exactly like it does today --
owner-equality, same storage path, same result.

Every call site that today special-cases owner-equality must go through
`resolve_conversation_access` / `require_read` / `require_write` rather than
re-implementing an ACL check, so there is one place to audit.

Two ids come out of a resolution and they are not interchangeable:

- ``storage_user_id`` -- always the OWNER's id. This is what
  ``ConversationStore`` needs to resolve the conversation directory.
- ``requester_user_id`` -- the human who is asking. Used for the ACL decision
  and for message attribution, never for path resolution.

The collaborators ACL is stored under the owner's conversation via the
existing generic ``extra`` mechanism (no schema migration):

    store.get_extra(cid, "collaborators") -> [
      {"user_id": "bob", "role": "write", "status": "pending",
       "invited_by": "alice", "invited_at": 1785..., "responded_at": 0.0},
    ]

``status`` makes joining two-sided: the owner can only ever create a
``pending`` row, and the invitee alone flips it to ``accepted``. ``kicked``
rows are kept, never deleted, so "who had access when" stays reconstructable
and a re-invite is a status transition rather than a duplicate row.
"""

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

COLLABORATORS_KEY = "collaborators"

ROLE_OWNER = "owner"
ROLE_WRITE = "write"
ROLE_READ = "read"
ROLE_NONE = ""

STATUS_PENDING = "pending"
STATUS_ACCEPTED = "accepted"
STATUS_KICKED = "kicked"

COLLABORATOR_ROLES = (ROLE_WRITE, ROLE_READ)
COLLABORATOR_STATUSES = (STATUS_PENDING, STATUS_ACCEPTED, STATUS_KICKED)

_WRITE_ROLES = (ROLE_OWNER, ROLE_WRITE)
_READ_ROLES = (ROLE_OWNER, ROLE_WRITE, ROLE_READ)

# A row in one of these states is listed in the invitee's reverse index:
# pending so the invite is discoverable, accepted so the conversation shows
# up in "shared with me". A kicked/declined row is dropped from the index.
_INDEXED_STATUSES = (STATUS_PENDING, STATUS_ACCEPTED)


class ConversationAccessError(PermissionError):
    """Raised when a requester may not act on a conversation.

    Callers must render this as the same not-found response an unknown
    conversation_id produces -- never a distinct "exists but forbidden", which
    would leak the existence of someone else's conversation.
    """


@dataclass(frozen=True)
class ConversationAccess:
    owner_user_id: str = ""
    role: str = ROLE_NONE
    storage_user_id: str = ""

    @property
    def is_owner(self) -> bool:
        return self.role == ROLE_OWNER

    @property
    def can_read(self) -> bool:
        return self.role in _READ_ROLES

    @property
    def can_write(self) -> bool:
        return self.role in _WRITE_ROLES


def _store(store=None):
    """The store to resolve against: an explicit one, else the singleton.

    Action handlers are handed a store by the dispatcher and must keep using
    that one -- resolving the singleton behind their back would authorize
    against a different set of conversations than the one they then read.
    """
    if store is not None:
        return store
    from core.conversation_store import ConversationStore
    return ConversationStore.instance()


def _clean(value: Any) -> str:
    return str(value or "").strip()


# -- Collaborators ACL ------------------------------------------------

def _normalize_row(raw: Any) -> Optional[Dict[str, Any]]:
    """Coerce one stored ACL entry, or None if it is unusable.

    Unusable means no user_id or an unknown role/status: such a row can never
    grant access, so dropping it is the safe reading. It is dropped from the
    *view*, not from disk -- rewriting the ACL is the caller's decision.
    """
    if not isinstance(raw, dict):
        return None
    user_id = _clean(raw.get("user_id"))
    if not user_id:
        return None
    role = _clean(raw.get("role")).lower()
    status = _clean(raw.get("status")).lower()
    if role not in COLLABORATOR_ROLES or status not in COLLABORATOR_STATUSES:
        return None
    return {
        "user_id": user_id,
        "role": role,
        "status": status,
        "invited_by": _clean(raw.get("invited_by")),
        "invited_at": float(raw.get("invited_at") or 0.0),
        "responded_at": float(raw.get("responded_at") or 0.0),
    }


def get_collaborators(cid: str, *, store=None) -> List[Dict[str, Any]]:
    """The conversation's ACL rows (all statuses), normalized, never None."""
    cid = _clean(cid)
    if not cid:
        return []
    try:
        raw = _store(store).get_extra(cid, COLLABORATORS_KEY, default=None)
    except Exception:
        logger.debug("collaborators read failed for %s", cid[:8], exc_info=True)
        return []
    if not isinstance(raw, list):
        return []
    rows = []
    for entry in raw:
        row = _normalize_row(entry)
        if row is not None:
            rows.append(row)
    return rows


def get_collaborator(cid: str, user_id: str, *,
                     store=None) -> Optional[Dict[str, Any]]:
    user_id = _clean(user_id)
    if not user_id:
        return None
    for row in get_collaborators(cid, store=store):
        if row["user_id"] == user_id:
            return row
    return None


def set_collaborators(cid: str, rows: List[Dict[str, Any]], *,
                      store=None) -> bool:
    """Replace the ACL wholesale and resync every affected reverse index."""
    cid = _clean(cid)
    if not cid:
        return False
    clean: List[Dict[str, Any]] = []
    for entry in rows or []:
        row = _normalize_row(entry)
        if row is None:
            raise ValueError(f"Invalid collaborator row: {entry!r}")
        clean.append(row)
    before = {row["user_id"] for row in get_collaborators(cid, store=store)}
    if not _store(store).set_extra(cid, COLLABORATORS_KEY, clean):
        return False
    for user_id in before | {row["user_id"] for row in clean}:
        _sync_shared_index(cid, user_id, clean, store=store)
    return True


def set_collaborator(cid: str, user_id: str, *, role: str = "",
                     status: str = "", invited_by: str = "",
                     now: float = 0.0, store=None) -> Dict[str, Any]:
    """Insert or update one ACL row and return it.

    Read-modify-write under the conversation's extras lock: concurrent
    invites to the same conversation must not clobber each other. ``role`` and
    ``status`` are optional on an update -- omitting one keeps the stored
    value, which is what a role change (status untouched) and an invite
    response (role untouched) each need.
    """
    cid = _clean(cid)
    user_id = _clean(user_id)
    if not cid:
        raise ValueError("set_collaborator requires a conversation_id")
    if not user_id:
        raise ValueError("set_collaborator requires a user_id")
    role = _clean(role).lower()
    status = _clean(status).lower()
    if role and role not in COLLABORATOR_ROLES:
        raise ValueError(f"Invalid collaborator role: {role!r}")
    if status and status not in COLLABORATOR_STATUSES:
        raise ValueError(f"Invalid collaborator status: {status!r}")

    conv_store = _store(store)
    now = float(now or time.time())
    with conv_store._get_extras_lock(cid):
        rows = get_collaborators(cid, store=conv_store)
        existing = next((r for r in rows if r["user_id"] == user_id), None)
        if existing is None:
            if not role:
                raise ValueError("A new collaborator row requires a role")
            row = {
                "user_id": user_id,
                "role": role,
                "status": status or STATUS_PENDING,
                "invited_by": _clean(invited_by),
                "invited_at": now,
                "responded_at": 0.0,
            }
            rows.append(row)
        else:
            row = existing
            if role:
                row["role"] = role
            if status and status != row["status"]:
                row["status"] = status
                row["responded_at"] = now
            if invited_by:
                row["invited_by"] = _clean(invited_by)
        conv_store.set_extra(cid, COLLABORATORS_KEY, rows)
    _sync_shared_index(cid, user_id, rows, store=conv_store)
    return dict(row)


def remove_collaborator(cid: str, user_id: str, *, store=None) -> bool:
    """Drop a row entirely -- declining an invite, not being kicked.

    A kick keeps the row (``status="kicked"``) for the audit trail; only a row
    that never became access in the first place is erased.
    """
    cid = _clean(cid)
    user_id = _clean(user_id)
    if not cid or not user_id:
        return False
    conv_store = _store(store)
    with conv_store._get_extras_lock(cid):
        rows = get_collaborators(cid, store=conv_store)
        kept = [r for r in rows if r["user_id"] != user_id]
        if len(kept) == len(rows):
            return False
        conv_store.set_extra(cid, COLLABORATORS_KEY, kept)
    _sync_shared_index(cid, user_id, kept, store=conv_store)
    return True


# -- Ephemeral UI bus channels ----------------------------------------

# The chat UI opens a second SSE stream per browser tab on a channel id of
# the form `__ui__:<tab id>` (`_uiActionConversationId` in rxbus.js). Every
# action$() call routes its `command_result` there, deliberately decoupled
# from the conversation stream, which resumeConv closes and reopens around
# history rendering.
#
# It is NOT a conversation: nothing on disk, no owner, no ACL. Resolving it
# through resolve_conversation_access can only ever deny -- resolve_owner
# finds nothing -- so gating this endpoint on read access took the whole UI
# command bus down with it: no action ever returned a result.
UI_BUS_PREFIX = "__ui__:"


def is_ui_bus_channel(cid: str) -> bool:
    """True for a per-tab UI command bus id, which has no conversation ACL.

    Callers must still require an authenticated principal: the exemption is
    from the per-conversation check, not from authentication.
    """
    cid = _clean(cid)
    return cid.startswith(UI_BUS_PREFIX) and len(cid) > len(UI_BUS_PREFIX)


# -- Access resolution ------------------------------------------------

def resolve_conversation_access(cid: str, requester_user_id: str, *,
                                store=None) -> ConversationAccess:
    """Resolve what ``requester_user_id`` may do with ``cid``.

    An empty ``role`` means no access *and* must be treated as not-found by
    the caller -- it covers both "no such conversation" and "exists but not
    yours", deliberately indistinguishable.
    """
    cid = _clean(cid)
    requester = _clean(requester_user_id)
    if not cid or not requester:
        return ConversationAccess()
    owner = _clean(_store(store).resolve_owner(cid))
    if not owner:
        return ConversationAccess()
    if owner == requester:
        # Unshared / owner path: identical to the pre-sharing behavior.
        return ConversationAccess(owner_user_id=owner, role=ROLE_OWNER,
                                  storage_user_id=owner)
    row = get_collaborator(cid, requester, store=store)
    if row is None or row["status"] != STATUS_ACCEPTED:
        return ConversationAccess(owner_user_id=owner, role=ROLE_NONE,
                                  storage_user_id=owner)
    return ConversationAccess(owner_user_id=owner, role=row["role"],
                              storage_user_id=owner)


def require_read(cid: str, requester_user_id: str, *,
                 store=None) -> ConversationAccess:
    access = resolve_conversation_access(cid, requester_user_id, store=store)
    if not access.can_read:
        raise ConversationAccessError("Conversation not found")
    return access


def require_write(cid: str, requester_user_id: str, *,
                  store=None) -> ConversationAccess:
    access = resolve_conversation_access(cid, requester_user_id, store=store)
    if not access.can_write:
        raise ConversationAccessError("Conversation not found")
    return access


def require_owner(cid: str, requester_user_id: str, *,
                  store=None) -> ConversationAccess:
    """For owner-only operations: delete, invite, kick, role change."""
    access = resolve_conversation_access(cid, requester_user_id, store=store)
    if not access.is_owner:
        raise ConversationAccessError("Conversation not found")
    return access


# -- Reverse index: conversations shared WITH a user -------------------

def shared_conversation_ids(user_id: str, *, store=None) -> List[str]:
    """cids where ``user_id`` has a pending or accepted row.

    Read-mostly side index -- ``list_shared_conversations`` cannot afford a
    disk-wide ACL sweep per request. Entries are a *candidate* list only:
    callers still resolve access per cid, so a stale entry grants nothing.
    """
    user_id = _clean(user_id)
    if not user_id:
        return []
    path = _store(store).shared_index_path(user_id)
    try:
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.debug("shared index read failed for %s", user_id, exc_info=True)
        return []
    if not isinstance(data, list):
        return []
    return [c for c in (_clean(x) for x in data) if c]


def _write_shared_index(user_id: str, cids: List[str], store=None) -> None:
    path = _store(store).shared_index_path(user_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cids, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        # The index is a cache, not the ACL: a failed write costs a
        # conversation its place in the sidebar until the next rebuild, it
        # never grants or denies access.
        logger.warning("shared index write failed for %s", user_id, exc_info=True)


def _sync_shared_index(cid: str, user_id: str,
                       rows: List[Dict[str, Any]], store=None) -> None:
    """Add/remove ``cid`` from ``user_id``'s index to match ``rows``."""
    cid = _clean(cid)
    user_id = _clean(user_id)
    if not cid or not user_id:
        return
    row = next((r for r in rows or [] if r.get("user_id") == user_id), None)
    should_index = bool(row) and row.get("status") in _INDEXED_STATUSES
    current = shared_conversation_ids(user_id, store=store)
    if should_index == (cid in current):
        return
    if should_index:
        current.append(cid)
    else:
        current = [c for c in current if c != cid]
    _write_shared_index(user_id, current, store=store)


def rebuild_shared_index(user_id: str, *, store=None) -> List[str]:
    """Recompute one user's index from a full ACL sweep.

    The defensive counterpart to the incremental updates above: whenever the
    index is suspected inconsistent (a crashed write, a hand-edited ACL), this
    reconstructs it from the conversations themselves, which are the only
    source of truth.
    """
    user_id = _clean(user_id)
    if not user_id:
        return []
    found = []
    for conv in _store(store).list_conversations():
        cid = _clean(conv.get("conversation_id"))
        if not cid or _clean(conv.get("user_id")) == user_id:
            continue
        row = get_collaborator(cid, user_id, store=store)
        if row is not None and row["status"] in _INDEXED_STATUSES:
            found.append(cid)
    _write_shared_index(user_id, found, store=store)
    return found
