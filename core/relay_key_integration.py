"""Wiring between the relay control channel and the key-relay protocol.

Phase 5b/6 live integration. Adapts a server-side ``FilesystemService`` relay
connection into the ``channel`` interface used by :mod:`core.relay_key_sync`,
and exposes connect/disconnect hooks the WS session calls.

Strictly opt-in and non-blocking: every hook is best-effort, runs off the WS
event loop, swallows all errors, and does nothing unless the owning user has
encryption actually enabled on a conversation/workspace bound to this relay. A
relay with no encrypted resources sees zero behavioural change.
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT = 30.0


class _Channel:
    """channel.request(method, params)->dict + connection_id, over a relay's
    FilesystemService synchronous request path."""

    def __init__(self, service, relay_id: str):
        self._service = service
        self.connection_id = relay_id

    def request(self, method: str, params=None) -> dict:
        try:
            res = self._service._request(
                method, _request_timeout=_REQUEST_TIMEOUT,
                _retry_on_disconnect=False, **(params or {}))
            return res if isinstance(res, dict) else {"ok": False}
        except Exception:
            logger.debug("[relay-key] request %s failed", method, exc_info=True)
            return {"ok": False}


def _owner_user_id(service) -> str:
    uid = getattr(service, "_user_id", "") or ""
    if uid:
        return uid
    try:
        return str(service.config.get("server_user_id") or "") if service.config else ""
    except Exception:
        return ""


def _conv_id_for_relay(relay_id: str):
    """Map a server workspace relay id back to its conversation, or None."""
    try:
        from core.server_relay_manager import ServerRelayManager
        for entry in ServerRelayManager.get_instance().list_all():
            if entry.get("relay_id") == relay_id:
                return entry.get("conv_id")
    except Exception:
        logger.debug("[relay-key] conv lookup failed", exc_info=True)
    return None


def _deliver(service, relay_id: str) -> None:
    from core import relay_key_sync
    channel = _Channel(service, relay_id)
    user_id = _owner_user_id(service)

    # (1) Conversation auto-unlock — push-at-connect. No-ops unless the relay
    # holds an unlocked private key and the user has conv wraps for its key_id.
    if user_id:
        try:
            relay_key_sync.push_at_connect(channel, user_id)
        except Exception:
            logger.debug("[relay-key] push_at_connect failed", exc_info=True)

    # (2) Workspace CryFS — if this relay's conversation has workspace
    # encryption unlocked server-side, tell the relay to mount the cipher-store.
    conv_id = _conv_id_for_relay(relay_id)
    if conv_id:
        try:
            from core import workspace_encryption as we
            from core.conversation_store import ConversationStore
            dek_b64 = we.workspace_dek_b64(conv_id)
            if dek_b64 and we.status(ConversationStore.instance(), conv_id)["state"] == "unlocked":
                channel.request("ws_mount_encrypted", {
                    "cipher_dir": we.CIPHER_DIR_IN_CONTAINER,
                    "mount_dir": we.MOUNT_DIR_IN_CONTAINER,
                    "dek": dek_b64,
                })
        except Exception:
            logger.debug("[relay-key] workspace mount push failed", exc_info=True)


def on_relay_connected(service, relay_id: str) -> None:
    """WS connect hook — deliver any keys this relay's owner has bound to it.
    Runs on a background thread so it never blocks the relay session, and never
    raises into the WS handler."""
    try:
        threading.Thread(
            target=_deliver, args=(service, relay_id),
            name=f"relay-key-deliver-{relay_id[:12]}", daemon=True).start()
    except Exception:
        logger.debug("[relay-key] connect hook failed", exc_info=True)


def on_relay_disconnected(relay_id: str) -> None:
    """WS disconnect hook — relay-gone = relocked: purge every DEK delivered by
    this relay connection."""
    try:
        from core import relay_key_sync
        relay_key_sync.on_relay_disconnect(relay_id)
    except Exception:
        logger.debug("[relay-key] disconnect hook failed", exc_info=True)
