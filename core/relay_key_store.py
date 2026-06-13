"""Relay-side key custody — the relay keypair lives only on the user's machine.

Phase 5 (RFC §6 "Relay-side key custody", open-decision #9). The relay's X25519
private key is stored **passphrase-locked** in a file under the relay config
dir; the public key is enrolled server-side. The private key is loaded into RAM
only after the user unlocks it (relay daemon), and the file at rest is useless
without the passphrase (reusing the phase-1 scrypt+AEAD wrap, so the relay and
server share one audited wrapping construction).

This module is the on-disk custody + the ``pawflow-relay key`` CLI's file ops
(init / status / export-pubkey / rotate). The in-RAM unlock/serve loop belongs
to the running relay daemon (WS transport).

File format (JSON, mode 0600)::

    {"v":1, "key_id": <fp>, "pub_b64": <relay public key>,
     "priv_wrap": <pf-wrap-v1 of the 32-byte private key>}
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Optional, Tuple

from core.key_vault import (
    KeyUnwrapError, unwrap_dek_passphrase, wrap_dek_passphrase,
)
from core.relay_keywrap import (
    generate_relay_keypair, key_id_for, public_from_private,
)

_WRAP_RESOURCE = "relay-key"  # AAD domain for the private-key wrap
_FILE_NAME = "relay_key.json"


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def key_path(config_dir) -> Path:
    return Path(config_dir) / _FILE_NAME


def exists(config_dir) -> bool:
    return key_path(config_dir).is_file()


def _write(config_dir, doc: dict) -> None:
    p = key_path(config_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(p)


def _read(config_dir) -> dict:
    p = key_path(config_dir)
    if not p.is_file():
        raise FileNotFoundError(f"no relay key at {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def init_relay_key(config_dir, passphrase: str, *,
                   overwrite: bool = False) -> Tuple[str, str]:
    """Generate the relay keypair, store the private key passphrase-locked, and
    return ``(key_id, pub_b64)`` to enroll server-side. Refuses to clobber an
    existing key unless ``overwrite`` (use :func:`rotate`)."""
    if not passphrase:
        raise ValueError("passphrase required")
    if exists(config_dir) and not overwrite:
        raise FileExistsError(
            f"relay key already exists at {key_path(config_dir)} — use rotate")
    priv, pub = generate_relay_keypair()
    kid = key_id_for(pub)
    priv_wrap = wrap_dek_passphrase(priv, passphrase, _WRAP_RESOURCE)
    _write(config_dir, {"v": 1, "key_id": kid, "pub_b64": _b64(pub),
                        "priv_wrap": priv_wrap})
    return kid, _b64(pub)


def load_private(config_dir, passphrase: str) -> bytes:
    """Unwrap the private key into RAM (relay daemon unlock). Raises
    :class:`KeyUnwrapError` on a wrong passphrase."""
    doc = _read(config_dir)
    return unwrap_dek_passphrase(doc["priv_wrap"], passphrase, _WRAP_RESOURCE)


def status(config_dir) -> dict:
    """Non-secret status: whether a key exists, its id and public key."""
    if not exists(config_dir):
        return {"exists": False, "key_id": "", "pub_b64": ""}
    doc = _read(config_dir)
    return {"exists": True, "key_id": doc.get("key_id", ""),
            "pub_b64": doc.get("pub_b64", "")}


def export_pubkey(config_dir) -> Tuple[str, str]:
    """Return ``(key_id, pub_b64)`` for (re-)enrollment with a server."""
    doc = _read(config_dir)
    return doc.get("key_id", ""), doc.get("pub_b64", "")


def rotate(config_dir, passphrase: str) -> Tuple[str, str]:
    """Generate a fresh keypair, invalidating the old key_id. The server must
    re-enroll (re-seal wrap_relay to the new pubkey) on next unlock."""
    return init_relay_key(config_dir, passphrase, overwrite=True)


def verify_passphrase(config_dir, passphrase: str) -> bool:
    """True if ``passphrase`` unwraps the stored private key."""
    try:
        load_private(config_dir, passphrase)
        return True
    except KeyUnwrapError:
        return False
