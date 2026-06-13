"""Relay-side key service — in-RAM custody + serve loop for the key relay.

Phase 5b (RFC §6 "At-rest on the relay host ... loaded into the relay process
RAM only after the user unlocks it; the relay answers need-DEK only while
unlocked"). This is the relay-daemon state behind the WS handlers
``key_pubkey_get`` and ``key_unseal``: it holds the unlocked X25519 private key
in RAM and refuses to serve while locked.

Pure and unit-testable — the socket/WS layer just calls ``handle_request`` with
the decoded message and ships the dict back. The private key is loaded via
:mod:`core.relay_key_store` (passphrase-locked on disk) and zeroised on lock.
"""

from __future__ import annotations

import base64
import threading
from typing import Dict, Optional

from core import relay_key_store as _ks
from core.relay_keywrap import public_from_private, key_id_for, unseal_dek


class RelayKeyService:
    """Holds the relay's private key in RAM once unlocked and answers key
    requests. One instance per relay daemon."""

    def __init__(self, config_dir):
        self._config_dir = config_dir
        self._lock = threading.RLock()
        self._priv: Optional[bytearray] = None
        self._key_id: str = ""
        self._pub_b64: str = ""

    # -- local unlock/lock (driven by `pawflow-relay key unlock`/daemon) --

    def unlock(self, passphrase: str) -> str:
        """Load the private key into RAM. Returns the key_id. Raises
        KeyUnwrapError on a wrong passphrase."""
        priv = _ks.load_private(self._config_dir, passphrase)
        pub = public_from_private(priv)
        with self._lock:
            self._priv = bytearray(priv)
            self._key_id = key_id_for(pub)
            self._pub_b64 = base64.b64encode(pub).decode("ascii")
        return self._key_id

    def lock(self) -> None:
        """Drop and zeroise the in-RAM private key."""
        with self._lock:
            if self._priv is not None:
                for i in range(len(self._priv)):
                    self._priv[i] = 0
            self._priv = None

    def is_unlocked(self) -> bool:
        with self._lock:
            return self._priv is not None

    # -- request handlers (called by the WS serve loop) -------------------

    def pubkey_response(self) -> Dict[str, object]:
        """Answer ``key_pubkey_get``. Only while unlocked."""
        with self._lock:
            if self._priv is None:
                return {"ok": False, "error": "relay key locked"}
            return {"ok": True, "key_id": self._key_id, "pubkey": self._pub_b64}

    def unseal_batch(self, items) -> Dict[str, object]:
        """Answer ``key_unseal``: ``items`` is a list of ``{conversation_id,
        wrap}``; return ``{conversation_id: dek_b64}`` for every wrap this key
        can open. Only while unlocked. A wrap sealed to another key_id is
        skipped, not fatal."""
        with self._lock:
            priv = bytes(self._priv) if self._priv is not None else None
        if priv is None:
            return {"ok": False, "error": "relay key locked"}
        deks: Dict[str, str] = {}
        for item in items or []:
            cid = item.get("conversation_id", "")
            wrap = item.get("wrap")
            if not cid or not isinstance(wrap, dict):
                continue
            try:
                dek = unseal_dek(wrap, priv)
            except Exception:  # nosec B112 - skip wraps this key cannot open (foreign/tampered)
                continue
            deks[cid] = base64.b64encode(dek).decode("ascii")
        return {"ok": True, "deks": deks}

    def handle_request(self, method: str, params: dict) -> Dict[str, object]:
        """Single entry point for the WS serve loop."""
        if method == "key_pubkey_get":
            return self.pubkey_response()
        if method == "key_unseal":
            return self.unseal_batch((params or {}).get("items") or [])
        return {"ok": False, "error": f"unknown key method: {method}"}
