"""Secrets management — AEAD encryption of sensitive config values.

Format
------
Strings: `enc:v2:<base64>` where the bytes decode to JSON
         `{"v":2,"alg":"aesgcm"|"chacha20poly1305","kid":<id>,
           "salt":<b64 or empty>,"nonce":<b64>,"ct":<b64>}`.
Bytes:   `b"PFSEC2\0" + json_payload_bytes` (sidecar files).

Key management
--------------
The master key is resolved in order:
  1. PAWFLOW_SECRET_KEY_B64       — 32 raw bytes, base64 (preferred).
  2. PAWFLOW_SECRET_KEY           — plaintext password, scrypt-KDF’d.
  3. data/config/secret.key       — 32 random bytes, written 0600 on
                                     first boot when neither env is set.

Key rotation: `add_key(kid, key)` registers an extra key and
`set_current(kid)` switches new writes to it. `decrypt()` uses the
payload's kid against the keyring. CLI: `python -m core.secrets`.

Failure mode
------------
A decrypt that fails AEAD authentication raises `SecretDecryptError`.
We never silently return the ciphertext / plaintext as a fallback.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import threading
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import (
    AESGCM, ChaCha20Poly1305,
)
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

logger = logging.getLogger(__name__)

_MAGIC = b"PFSEC2\0"
_DEFAULT_KID = "k1"
_DEFAULT_ALG = "aesgcm"

# scrypt parameters — same `n=2**14, r=8, p=1` profile recommended by
# the cryptography package for password-based KDF.
_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_SALT = b"pawflow-secrets-v2"


class SecretDecryptError(ValueError):
    """Raised when a payload fails AEAD verification or is malformed.
    Distinct subclass so callers can fail-loud on a tampered secret
    instead of falling back to the (potentially wrong) ciphertext."""


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _ub64(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


def _aead(alg: str, key: bytes):
    if alg == "aesgcm":
        return AESGCM(key)
    if alg == "chacha20poly1305":
        return ChaCha20Poly1305(key)
    raise SecretDecryptError(f"unsupported alg: {alg!r}")


class SecretsManager:
    """Thread-safe AEAD-backed secret manager with a kid keyring.

    The keyring is dict[kid -> 32-byte key]; `_current_kid` is the kid
    used for new writes. Reads pick the kid from the payload.
    """

    def __init__(self, key: Optional[str] = None):
        self._lock = threading.RLock()
        self._keyring: dict[str, bytes] = {}
        self._current_kid = _DEFAULT_KID
        self._init_default_key(key)

    # --- Key management -------------------------------------------------

    def _init_default_key(self, override: Optional[str]) -> None:
        """Resolve the boot key and seed the keyring with it."""
        key = self._resolve_boot_key(override)
        self._keyring[_DEFAULT_KID] = key

    @staticmethod
    def _resolve_boot_key(override: Optional[str]) -> bytes:
        """Return the 32-byte boot key."""
        # 1) Direct override (used by tests / CLI rotation).
        if override is not None:
            return SecretsManager._derive_from_password(override)
        # 2) PAWFLOW_SECRET_KEY_B64 — raw 32-byte key.
        b64_env = os.environ.get("PAWFLOW_SECRET_KEY_B64")
        if b64_env:
            try:
                raw = _ub64(b64_env.strip())
            except Exception as e:
                raise SecretDecryptError(
                    f"PAWFLOW_SECRET_KEY_B64 is not valid base64: {e}")
            if len(raw) != 32:
                raise SecretDecryptError(
                    f"PAWFLOW_SECRET_KEY_B64 must decode to 32 bytes, got {len(raw)}")
            return raw
        # 3) PAWFLOW_SECRET_KEY — plaintext password.
        pwd = os.environ.get("PAWFLOW_SECRET_KEY")
        if pwd:
            return SecretsManager._derive_from_password(pwd)
        # 4) Generated key file fallback.
        from core.paths import SECRET_KEY_FILE
        path = SECRET_KEY_FILE
        if path.exists():
            data = path.read_bytes()
            if len(data) >= 32:
                return data[:32]
        path.parent.mkdir(parents=True, exist_ok=True)
        generated = os.urandom(32)
        path.write_bytes(generated)
        try:
            os.chmod(path, 0o600)
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        logger.warning(
            "[secrets] generated new random master key at %s — set "
            "PAWFLOW_SECRET_KEY_B64 in production to avoid relying on "
            "this on-disk file.", path)
        return generated

    @staticmethod
    def _derive_from_password(password: str) -> bytes:
        kdf = Scrypt(
            salt=_SCRYPT_SALT, length=32,
            n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)
        return kdf.derive(password.encode("utf-8"))

    def add_key(self, kid: str, key: bytes) -> None:
        """Register an additional key under kid. Reads accept it; writes
        keep using `_current_kid` until `set_current()` flips."""
        if not kid:
            raise ValueError("kid required")
        if len(key) != 32:
            raise ValueError(f"key must be 32 bytes, got {len(key)}")
        with self._lock:
            self._keyring[kid] = key

    def derive_subkey(self, domain: bytes) -> bytes:
        """HMAC-SHA256-derive a domain-bound 32-byte subkey from the
        current master.

        Use case: signing/verification helpers (cookie HMACs, capability
        token integrity, etc.) want a key that's *bound to a domain
        string* so that a forged value from one subsystem can't be
        replayed in another. `domain` is a short ASCII tag, e.g.
        `b"private-gateway-cookie"`.

        Cheap (one HMAC) — callers can re-derive on every use and
        don't have to cache. Rotates automatically when `set_current()`
        flips the master.
        """
        if not isinstance(domain, (bytes, bytearray)) or not domain:
            raise ValueError("domain must be non-empty bytes")
        with self._lock:
            master = self._keyring[self._current_kid]
        return hmac.new(master, bytes(domain), hashlib.sha256).digest()

    def set_current(self, kid: str) -> None:
        """Switch new writes to kid. Reads still try every key in the
        keyring; this only changes which kid is stamped on new payloads."""
        with self._lock:
            if kid not in self._keyring:
                raise KeyError(f"unknown kid {kid!r}")
            self._current_kid = kid

    # --- Encrypt / decrypt API ------------------------------------------

    def encrypt(self, plaintext: str) -> str:
        """Encrypt a UTF-8 string. Returns `enc:v2:<base64>`.

        Idempotent: a value already starting with `enc:` is returned
        as-is so we never double-wrap.
        """
        if not plaintext:
            return plaintext
        if plaintext.startswith("enc:"):
            return plaintext
        return self._encrypt_v2(plaintext.encode("utf-8"))

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt a stored secret string back to its UTF-8 plaintext.

        Accepts:
          - bare strings (no `enc:` prefix) — returned as-is.
          - `enc:v2:<base64>` — AEAD path.

        Raises SecretDecryptError if a payload looks encrypted but
        cannot be authenticated against the keyring. Never falls back
        silently to ciphertext.
        """
        if not ciphertext or not ciphertext.startswith("enc:"):
            return ciphertext
        if ciphertext.startswith("enc:v2:"):
            return self._decrypt_v2(ciphertext[len("enc:v2:"):]).decode(
                "utf-8")
        raise SecretDecryptError("unsupported secret format")

    def encrypt_bytes(self, data: bytes) -> bytes:
        """Encrypt raw bytes (sidecar files). Output is
        `_MAGIC + json_payload_bytes` so future decryptors recognise it
        without parsing."""
        with self._lock:
            kid = self._current_kid
            key = self._keyring[kid]
        nonce = os.urandom(12)
        ct = _aead(_DEFAULT_ALG, key).encrypt(nonce, data, None)
        payload = json.dumps({
            "v": 2, "alg": _DEFAULT_ALG, "kid": kid,
            "nonce": _b64(nonce), "ct": _b64(ct),
        }, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return _MAGIC + payload

    def decrypt_bytes(self, data: bytes) -> bytes:
        """Decrypt sidecar bytes with the v2 magic header."""
        if data[:len(_MAGIC)] != _MAGIC:
            raise SecretDecryptError("unsupported sidecar secret format")
        try:
            payload = json.loads(data[len(_MAGIC):].decode("utf-8"))
        except Exception as e:
            raise SecretDecryptError(
                f"v2 sidecar envelope is corrupted: {e}")
        return self._decrypt_v2_payload(payload)

    @staticmethod
    def is_encrypted(value: str) -> bool:
        return bool(value) and value.startswith("enc:")

    # --- Internal: v2 ---------------------------------------------------

    def _encrypt_v2(self, data: bytes) -> str:
        with self._lock:
            kid = self._current_kid
            key = self._keyring[kid]
        nonce = os.urandom(12)
        ct = _aead(_DEFAULT_ALG, key).encrypt(nonce, data, None)
        payload = json.dumps({
            "v": 2, "alg": _DEFAULT_ALG, "kid": kid,
            "nonce": _b64(nonce), "ct": _b64(ct),
        }, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return "enc:v2:" + _b64(payload)

    def _decrypt_v2(self, b64_payload: str) -> bytes:
        try:
            payload = json.loads(_ub64(b64_payload).decode("utf-8"))
        except Exception as e:
            raise SecretDecryptError(f"malformed v2 envelope: {e}")
        return self._decrypt_v2_payload(payload)

    def _decrypt_v2_payload(self, payload: dict) -> bytes:
        if not isinstance(payload, dict) or payload.get("v") != 2:
            raise SecretDecryptError(
                f"unexpected v2 payload version: {payload!r}")
        alg = payload.get("alg", "")
        kid = payload.get("kid", "")
        try:
            nonce = _ub64(payload["nonce"])
            ct = _ub64(payload["ct"])
        except Exception as e:
            raise SecretDecryptError(f"v2 envelope missing fields: {e}")
        with self._lock:
            key = self._keyring.get(kid)
        if key is None:
            raise SecretDecryptError(f"unknown kid {kid!r} — rotate or load it")
        try:
            return _aead(alg, key).decrypt(nonce, ct, None)
        except Exception as e:
            raise SecretDecryptError(
                f"AEAD decrypt failed (kid={kid}): {e}")

# --- Singleton ----------------------------------------------------------

_instance: Optional[SecretsManager] = None
_instance_lock = threading.Lock()


def get_secrets_manager() -> SecretsManager:
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = SecretsManager()
    return _instance


def _reset_for_tests() -> None:
    """Drop the singleton. Tests use this between cases that need a
    fresh keyring. Production code MUST NOT call this."""
    global _instance
    with _instance_lock:
        _instance = None
