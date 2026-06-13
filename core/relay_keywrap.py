"""Asymmetric key-relay wrap (``wrap_relay``) — seal a DEK to a relay's pubkey.

Phase 5 of the encryption-at-rest design (RFC §6, open-decision #5 [Decided]:
the key-relay wrap is asymmetric so the *server never holds a key that opens it*
— only the relay's private key, which lives solely on the user's relay machine,
can recover the DEK; and #7 [Proposed]: X25519 sealed-box).

Construction (libsodium ``crypto_box_seal`` shape, built on ``cryptography``):

  enroll (server, has relay pubkey ``R``):
    e_sk, e_pk = X25519 ephemeral keypair
    shared     = X25519(e_sk, R)
    key        = HKDF-SHA256(shared, info = e_pk || R)        # 32 bytes
    ct         = AES-GCM(key).encrypt(nonce, DEK, aad=key_id)
    wrap_relay = { e_pk, nonce, ct, key_id }                   # safe on disk

  recover (relay, has private key ``r``):
    shared = X25519(r, e_pk);  key = HKDF(... e_pk || derive_pub(r))
    DEK    = AES-GCM(key).decrypt(nonce, ct, aad=key_id)

Forward secrecy of the wrap is not the goal (the blob persists); the point is
that a stolen ``wrap_relay`` (threat T1) is useless without the live relay's
private key. ``key_id`` is a stable fingerprint of the relay pubkey, recorded
so the server knows which relay/keypair a wrap is sealed to (revocation/rotation).
"""

from __future__ import annotations

import base64
import hashlib
import os
from typing import Tuple

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey, X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from core.secrets import SecretDecryptError

_SCHEME = "pf-relaywrap-x25519-v1"
_NONCE_LEN = 12
_DEK_LEN = 32
_HKDF_INFO = b"pf-relay-wrap:v1"


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _ub64(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


def generate_relay_keypair() -> Tuple[bytes, bytes]:
    """Return ``(private_raw, public_raw)`` — 32 raw bytes each. The private key
    never leaves the relay machine; the public key is enrolled server-side."""
    sk = X25519PrivateKey.generate()
    priv = sk.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption())
    pub = sk.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw)
    return priv, pub


def public_from_private(priv_raw: bytes) -> bytes:
    sk = X25519PrivateKey.from_private_bytes(priv_raw)
    return sk.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw)


def key_id_for(pub_raw: bytes) -> str:
    """Stable short fingerprint of a relay public key."""
    return hashlib.sha256(pub_raw).hexdigest()[:32]


def _derive(shared: bytes, e_pk: bytes, r_pk: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=_DEK_LEN, salt=None,
               info=_HKDF_INFO + e_pk + r_pk).derive(shared)


def seal_dek(dek: bytes, relay_pub_raw: bytes) -> dict:
    """Seal ``dek`` to a relay public key. Returns a JSON-serialisable
    ``wrap_relay`` envelope (carries its own ``key_id``)."""
    if len(dek) != _DEK_LEN:
        raise ValueError(f"dek must be {_DEK_LEN} bytes, got {len(dek)}")
    R = X25519PublicKey.from_public_bytes(relay_pub_raw)
    e_sk = X25519PrivateKey.generate()
    e_pk = e_sk.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw)
    shared = e_sk.exchange(R)
    key = _derive(shared, e_pk, relay_pub_raw)
    kid = key_id_for(relay_pub_raw)
    nonce = os.urandom(_NONCE_LEN)
    ct = AESGCM(key).encrypt(nonce, dek, kid.encode("ascii"))
    return {
        "scheme": _SCHEME,
        "key_id": kid,
        "epk": _b64(e_pk),
        "nonce": _b64(nonce),
        "ct": _b64(ct),
    }


def unseal_dek(wrap: dict, relay_priv_raw: bytes) -> bytes:
    """Recover the DEK from a ``wrap_relay`` envelope using the relay private
    key. Raises :class:`SecretDecryptError` on any mismatch."""
    if not isinstance(wrap, dict) or wrap.get("scheme") != _SCHEME:
        raise SecretDecryptError("not a pf-relaywrap-x25519-v1 envelope")
    try:
        e_pk = _ub64(wrap["epk"])
        nonce = _ub64(wrap["nonce"])
        ct = _ub64(wrap["ct"])
        kid = str(wrap["key_id"])
    except (KeyError, ValueError, TypeError) as e:
        raise SecretDecryptError(f"malformed relay wrap: {e}")
    sk = X25519PrivateKey.from_private_bytes(relay_priv_raw)
    r_pk = public_from_private(relay_priv_raw)
    if kid != key_id_for(r_pk):
        raise SecretDecryptError("relay wrap sealed to a different key_id")
    shared = sk.exchange(X25519PublicKey.from_public_bytes(e_pk))
    key = _derive(shared, e_pk, r_pk)
    try:
        dek = AESGCM(key).decrypt(nonce, ct, kid.encode("ascii"))
    except Exception as e:
        raise SecretDecryptError(f"relay wrap decrypt failed: {e}")
    if len(dek) != _DEK_LEN:
        raise SecretDecryptError("unsealed key has wrong length")
    return dek
