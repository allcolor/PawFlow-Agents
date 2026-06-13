"""Server-side key-relay delivery — push-at-connect + need-DEK pull.

Phase 5b (RFC §6 "Delivery — push at connect (primary), pull on demand
(fallback)"). When an unlocked trusted relay connects, the server asks it to
unseal every ``wrap_relay`` for that user's encrypted conversations sealed to
the relay's key_id, and populates the KeyVault with the returned DEKs — tagged
with the relay connection so they are purged when it drops (relay-gone =
relocked). A single conversation can be pulled on demand the same way.

This is written against a minimal ``channel`` so it is unit-testable without a
live socket. ``channel.request(method, params) -> dict`` performs one
request/response round-trip over the relay control tunnel and returns the
relay's decoded reply. ``channel.connection_id`` identifies the connection for
KeyVault source-tagging.
"""

from __future__ import annotations

import base64
import logging
from typing import List, Optional

from core.conversation_store import ConversationStore
from core.key_vault import get_key_vault
from core.relay_keywrap import key_id_for

logger = logging.getLogger(__name__)


def _b64decode(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


def _bound_conversations(store: ConversationStore, user_id: str,
                         key_id: str) -> List[tuple]:
    """Return ``(conversation_id, wrap_relay)`` for every encrypted conversation
    of ``user_id`` whose relay wrap is sealed to ``key_id``."""
    out = []
    try:
        convs = store.list_conversations(user_id=user_id)
    except Exception:
        logger.debug("[relay-key-sync] list_conversations failed", exc_info=True)
        return out
    for c in convs:
        cid = c.get("conversation_id", "")
        if not cid:
            continue
        wrap = store.relay_wrap_for(cid)
        if isinstance(wrap, dict) and wrap.get("key_id") == key_id:
            out.append((cid, wrap))
    return out


def push_at_connect(channel, user_id: str,
                    store: Optional[ConversationStore] = None) -> int:
    """On relay connect: enroll the relay's key_id, then batch-unseal every
    bound conversation's DEK and load it into the vault tagged with this
    connection. Returns the number of conversations unlocked.

    No-op (returns 0) if the relay is locked or has no key.
    """
    store = store or ConversationStore.instance()
    pk = channel.request("key_pubkey_get", {})
    if not pk or not pk.get("ok"):
        return 0
    key_id = pk.get("key_id") or key_id_for(_b64decode(pk["pubkey"]))

    bound = _bound_conversations(store, user_id, key_id)
    if not bound:
        return 0
    items = [{"conversation_id": cid, "wrap": wrap} for cid, wrap in bound]
    resp = channel.request("key_unseal", {"items": items})
    if not resp or not resp.get("ok"):
        return 0
    deks = resp.get("deks") or {}
    source = getattr(channel, "connection_id", "") or "relay"
    unlocked = 0
    for cid, dek_b64 in deks.items():
        try:
            store.unlock_encryption_with_dek(
                cid, _b64decode(dek_b64), source=source)
            unlocked += 1
        except Exception:
            logger.debug("[relay-key-sync] unlock %s failed", cid[:8], exc_info=True)
    if unlocked:
        logger.info("[relay-key-sync] push-at-connect unlocked %d conv(s) via %s",
                    unlocked, source)
    return unlocked


def need_dek(channel, conversation_id: str,
             store: Optional[ConversationStore] = None) -> bool:
    """Pull a single conversation's DEK on demand (e.g. bound after the relay
    connected). Returns True if it was unlocked."""
    store = store or ConversationStore.instance()
    wrap = store.relay_wrap_for(conversation_id)
    if not isinstance(wrap, dict):
        return False
    resp = channel.request("key_unseal", {
        "items": [{"conversation_id": conversation_id, "wrap": wrap}]})
    if not resp or not resp.get("ok"):
        return False
    dek_b64 = (resp.get("deks") or {}).get(conversation_id)
    if not dek_b64:
        return False
    source = getattr(channel, "connection_id", "") or "relay"
    store.unlock_encryption_with_dek(
        conversation_id, _b64decode(dek_b64), source=source)
    return True


def on_relay_disconnect(connection_id: str) -> int:
    """Relay dropped (or locked): purge every DEK it delivered — relay-gone =
    relocked. Returns count purged."""
    n = get_key_vault().purge_source(connection_id)
    if n:
        logger.info("[relay-key-sync] relay %s gone -> relocked %d conv(s)",
                    connection_id, n)
    return n
