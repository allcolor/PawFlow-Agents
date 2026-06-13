"""Relay-side encryption operations (phase 5b/6 live integration).

Two independent capabilities, both **opt-in** — invoked only when the server
sends one of the new command actions, so a relay that never receives them
behaves exactly as before:

* **Workspace CryFS** (phase 6): ``ws_mount_encrypted`` / ``ws_unmount`` mount a
  CryFS cipher-store (persisted on the bind-mounted host dir) as a plaintext
  view, using the workspace DEK the server delivers over the control channel.
  Self-contained (subprocess + stdlib) so it works inside the relay container
  without the server's ``core`` package.

* **Key relay** (phase 5): ``key_pubkey_get`` / ``key_unseal`` answer the
  server's push-at-connect / need-DEK using the relay's own X25519 private key,
  passed into this process via ``PAWFLOW_RELAY_PRIVKEY_B64`` (set by the relay
  daemon after the user unlocked it — never on argv). Imports ``core`` lazily;
  only the standalone relay client (full install) uses this path.

The password / DEK is always delivered to CryFS via **stdin**, never argv.
"""

from __future__ import annotations

import base64
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional

KEY_ACTIONS = frozenset({
    "key_pubkey_get", "key_unseal", "ws_mount_encrypted", "ws_unmount",
})


def is_key_action(action: str) -> bool:
    return action in KEY_ACTIONS


# --------------------------------------------------------------------------
# Workspace CryFS (phase 6)
# --------------------------------------------------------------------------

def _run_cryfs(argv: List[str], password: str, env_extra: Dict[str, str]) -> Dict[str, Any]:
    env = dict(os.environ)
    env.update(env_extra)
    try:
        proc = subprocess.run(  # nosec B603 - fixed argv, password via stdin
            argv, input=(password + "\n").encode("utf-8"),
            env=env, capture_output=True, timeout=120)
    except FileNotFoundError:
        return {"ok": False, "error": "cryfs not installed in relay image"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "cryfs mount timed out"}
    if proc.returncode != 0:
        return {"ok": False, "error": proc.stderr.decode("utf-8", "replace")[-500:]}
    return {"ok": True}


def mount_encrypted_workspace(msg: Dict[str, Any]) -> Dict[str, Any]:
    """``ws_mount_encrypted``: CryFS-mount ``cipher_dir`` at ``mount_dir`` using
    the base64 DEK in ``dek``. Idempotent-ish: if already mounted, returns ok."""
    cipher_dir = msg.get("cipher_dir", "")
    mount_dir = msg.get("mount_dir", "")
    dek_b64 = msg.get("dek", "")
    if not cipher_dir or not mount_dir or not dek_b64:
        return {"ok": False, "error": "cipher_dir, mount_dir and dek required"}
    if os.path.ismount(mount_dir):
        return {"ok": True, "already_mounted": True}
    os.makedirs(cipher_dir, exist_ok=True)
    os.makedirs(mount_dir, exist_ok=True)
    argv = ["cryfs", cipher_dir, mount_dir]
    env_extra = {"CRYFS_FRONTEND": "noninteractive", "CRYFS_NO_UPDATE_CHECK": "true"}
    return _run_cryfs(argv, dek_b64, env_extra)


def unmount_workspace(msg: Dict[str, Any]) -> Dict[str, Any]:
    """``ws_unmount``: unmount the CryFS view (cipher-store stays on disk)."""
    mount_dir = msg.get("mount_dir", "")
    if not mount_dir:
        return {"ok": False, "error": "mount_dir required"}
    if not os.path.ismount(mount_dir):
        return {"ok": True, "already_unmounted": True}
    for argv in (["cryfs-unmount", mount_dir], ["fusermount", "-u", mount_dir]):
        try:
            proc = subprocess.run(argv, capture_output=True, timeout=60)  # nosec B603
            if proc.returncode == 0:
                return {"ok": True}
        except FileNotFoundError:
            continue
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "unmount timed out"}
    return {"ok": False, "error": "no working unmount tool (cryfs-unmount/fusermount)"}


# --------------------------------------------------------------------------
# Key relay (phase 5) — standalone relay client only
# --------------------------------------------------------------------------

def _relay_private_key() -> Optional[bytes]:
    raw = os.environ.get("PAWFLOW_RELAY_PRIVKEY_B64", "")
    if not raw:
        return None
    try:
        return base64.b64decode(raw)
    except Exception:
        return None


def pubkey_get() -> Dict[str, Any]:
    priv = _relay_private_key()
    if priv is None:
        return {"ok": False, "error": "relay key locked"}
    from core.relay_keywrap import public_from_private, key_id_for
    pub = public_from_private(priv)
    return {"ok": True, "key_id": key_id_for(pub),
            "pubkey": base64.b64encode(pub).decode("ascii")}


def unseal(msg: Dict[str, Any]) -> Dict[str, Any]:
    priv = _relay_private_key()
    if priv is None:
        return {"ok": False, "error": "relay key locked"}
    from core.relay_keywrap import unseal_dek
    deks: Dict[str, str] = {}
    for item in msg.get("items") or []:
        cid = item.get("conversation_id", "")
        wrap = item.get("wrap")
        if not cid or not isinstance(wrap, dict):
            continue
        try:
            dek = unseal_dek(wrap, priv)
        except Exception:
            continue
        deks[cid] = base64.b64encode(dek).decode("ascii")
    return {"ok": True, "deks": deks}


def handle(action: str, msg: Dict[str, Any]) -> Dict[str, Any]:
    """Dispatch one encryption action. Called from the relay command loop only
    for actions in KEY_ACTIONS."""
    try:
        if action == "ws_mount_encrypted":
            return mount_encrypted_workspace(msg)
        if action == "ws_unmount":
            return unmount_workspace(msg)
        if action == "key_pubkey_get":
            return pubkey_get()
        if action == "key_unseal":
            return unseal(msg)
    except Exception as e:  # never crash the relay loop
        sys.stderr.write(f"[key_ops] {action} failed: {e}\n")
        return {"ok": False, "error": str(e)}
    return {"ok": False, "error": f"unknown key action: {action}"}
