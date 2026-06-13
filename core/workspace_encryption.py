"""Server-relay workspace encryption (CryFS cipher-store).

Phase 6 of the encryption-at-rest design (RFC §6/§7.2, decisions #1 CryFS, #8
conv-scoped relays only). A conv-scoped server relay's workspace is stored as a
CryFS cipher-store; the relay mounts it with a DEK delivered over the WS control
channel (never via --env/argv). This module owns the server-side DEK lifecycle
(mirroring conversation encryption, keyed ``ws:<conv_id>``) + the conv-scope
constraint + the CryFS command construction. The actual mount/unmount runs on
the relay (relay runtime).

The workspace DEK is wrapped in a multi-wrap container stored in the
conversation's extras under ``workspace_encryption`` — passphrase now, with the
same forward-compatible relay/escrow slots as conversation encryption, so a
trusted key-relay can auto-unlock the workspace exactly like a conversation.
"""

from __future__ import annotations

import base64
from typing import Any, Dict, List, Optional, Tuple

from core.conversation_store import ConversationLockedError
from core.key_vault import (
    KeyUnwrapError, create_passphrase_protected, get_key_vault,
    unwrap_with_passphrase,
)

_EXTRA_KEY = "workspace_encryption"

# Container layout for an encrypted workspace: the bind-mounted host dir holds
# the CryFS cipher-store (encrypted at rest); the relay mounts a plaintext view
# at the normal workspace path so tools are unchanged.
CIPHER_DIR_IN_CONTAINER = "/workspace_cipher"
MOUNT_DIR_IN_CONTAINER = "/workspace"


def _resource(conv_id: str) -> str:
    return f"ws:{conv_id}"


def is_conv_scoped(relay_meta: Optional[dict]) -> bool:
    """Workspace encryption is allowed only for conv-scoped relays (RFC #8):
    user/global relays are shared across conversations and have no single
    workspace key. Server-spawned relays are conv-scoped by construction."""
    if relay_meta is None:
        return False
    scope = (relay_meta.get("scope") or "conv").lower()
    return scope in ("", "conv", "conversation")


def _descriptor(store, conv_id: str) -> dict:
    try:
        return store.get_extra(conv_id, _EXTRA_KEY, {}) or {}
    except Exception:
        return {}


def _set_descriptor(store, conv_id: str, desc: dict) -> None:
    store.set_extra(conv_id, _EXTRA_KEY, desc)


def status(store, conv_id: str) -> Dict[str, Any]:
    desc = _descriptor(store, conv_id)
    enabled = bool(desc.get("enabled"))
    unlocked = enabled and get_key_vault().is_unlocked(_resource(conv_id))
    return {
        "enabled": enabled,
        "unlocked": unlocked,
        "state": "off" if not enabled else ("unlocked" if unlocked else "locked"),
    }


def enable(store, conv_id: str, passphrase: str, *, relay_meta: Optional[dict] = None,
           session_id: str = "") -> Dict[str, Any]:
    """Turn on workspace encryption: mint a workspace DEK, store its passphrase
    wrap, and unlock it in the vault. Refuses non-conv-scoped relays. The CryFS
    cipher-store init/migration runs relay-side with the delivered DEK."""
    if relay_meta is not None and not is_conv_scoped(relay_meta):
        raise ValueError("workspace encryption is only available for conv-scoped relays")
    if not passphrase:
        raise ValueError("passphrase required")
    if _descriptor(store, conv_id).get("enabled"):
        return status(store, conv_id)
    dek, container = create_passphrase_protected(_resource(conv_id), passphrase)
    get_key_vault().put(_resource(conv_id), dek, session_id=session_id)
    _set_descriptor(store, conv_id, {"enabled": True, "v": 1, "container": container})
    return status(store, conv_id)


def unlock(store, conv_id: str, passphrase: str, *, session_id: str = "") -> bool:
    desc = _descriptor(store, conv_id)
    if not desc.get("enabled"):
        raise ValueError("workspace is not encrypted")
    dek = unwrap_with_passphrase(desc["container"], passphrase)
    get_key_vault().put(_resource(conv_id), dek, session_id=session_id)
    return True


def lock(store, conv_id: str) -> None:
    get_key_vault().drop(_resource(conv_id))


def disable(store, conv_id: str) -> Dict[str, Any]:
    desc = _descriptor(store, conv_id)
    if not desc.get("enabled"):
        return status(store, conv_id)
    if get_key_vault().get(_resource(conv_id)) is None:
        raise ConversationLockedError("unlock the workspace before disabling encryption")
    _set_descriptor(store, conv_id, {"enabled": False})
    get_key_vault().drop(_resource(conv_id))
    return status(store, conv_id)


def workspace_dek_b64(conv_id: str) -> Optional[str]:
    """The unlocked workspace DEK (base64) to deliver to the relay for mounting,
    or None if locked. Delivered over the TLS WS control channel — never argv."""
    dek = get_key_vault().get(_resource(conv_id))
    return base64.b64encode(dek).decode("ascii") if dek is not None else None


def cryfs_password_from_dek(dek_b64: str) -> str:
    """Derive the CryFS store password from the workspace DEK. CryFS takes a
    passphrase; we feed it the base64 DEK so the same KeyVault-managed key gates
    the cipher-store."""
    return dek_b64


def build_cryfs_mount_command(cipher_dir: str, mount_dir: str,
                              dek_b64: str) -> Tuple[List[str], Dict[str, str], str]:
    """Build the relay-side CryFS mount invocation. Returns
    ``(argv, env, stdin_password)``.

    The password is delivered via **stdin** (and CRYFS_FRONTEND=noninteractive),
    never on argv — argv is world-readable through /proc, the same rule as the
    workspace DEK transport. ``CRYFS_NO_UPDATE_CHECK`` keeps the mount offline.
    """
    argv = ["cryfs", cipher_dir, mount_dir, "-f"]
    env = {
        "CRYFS_FRONTEND": "noninteractive",
        "CRYFS_NO_UPDATE_CHECK": "true",
    }
    password = cryfs_password_from_dek(dek_b64)
    return argv, env, password


def build_cryfs_unmount_command(mount_dir: str) -> List[str]:
    return ["cryfs-unmount", mount_dir]
