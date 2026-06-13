"""Key vault — RAM-only, session-bound custody of per-resource DEKs.

Phase 1 of the encryption-at-rest design (docs/design/encryption-at-rest.md).
Builds on top of ``core/secrets.py`` and reuses its AEAD / scrypt profile, but
adds the concepts that file deliberately does not have:

  * **DEK** (data encryption key) -- 32 random bytes minted *per resource*
    (a conversation, or a conv-scoped relay workspace). The DEK is what
    actually encrypts content; it never touches disk in the clear.
  * **KEK** (key encryption key) -- derived from a passphrase via scrypt and
    used only to *wrap* (encrypt) the DEK. The wrapped DEK is the only form
    that lands on disk.
  * **Multi-wrap** -- the same DEK may be wrapped several independent ways so
    the resource can be unlocked by any of them: ``pass`` (passphrase, this
    phase), ``relay`` (asymmetric seal to a relay pubkey, phase 5) and
    ``escrow`` (optional recovery, phase 7). The on-disk container carries
    forward-compatible slots for the latter two now so the storage format does
    not have to change when they land.
  * **KeyVault** -- the in-RAM map ``resource_id -> DEK`` for *unlocked*
    resources. RAM-only by construction (never serialised), thread-safe,
    DEK bytes zeroised on drop, and indexed so a whole login session or a
    whole relay connection can be purged in one call (the eviction invariants
    in the RFC: relay-gone = relocked, logout/idle = relocked).

Forward-secrecy / threat model: see the RFC. This module defends **T1 (disk
at rest)** only -- the wraps on disk are useless without the passphrase (or a
live unlocked relay). It does not defend a live-root attacker (T2).

The per-wrap salt is **random and stored inside the wrap**, NOT the install
salt from secrets.py: two conversations protected by the same passphrase must
derive *different* KEKs, and an attacker must pay the scrypt cost per wrap.
The wrap is bound to its ``resource_id`` via AEAD associated-data so a wrap
blob cannot be transplanted from one conversation onto another.
"""

from __future__ import annotations

import base64
import logging
import os
import threading
import time
from typing import Dict, Iterable, Optional, Set, Tuple

from cryptography.hazmat.primitives.ciphers.aead import AESGCM, ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from core.secrets import SecretDecryptError

logger = logging.getLogger(__name__)

DEK_LEN = 32
_NONCE_LEN = 12
_SALT_LEN = 32
_WRAP_SCHEME = "pf-wrap-v1"
_DEFAULT_ALG = "aesgcm"
_AAD_PREFIX = b"pf-dek-wrap:v1:"

# scrypt profile -- same as core.secrets (cryptography's recommended PBKDF
# profile). Interactive unlock pays this once per attempt.
_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1

# Slots a multi-wrap container may carry. Only "pass" is produced in phase 1;
# the others are reserved so the on-disk format is stable across phases.
WRAP_SLOTS = ("pass", "relay", "escrow")


class KeyUnwrapError(SecretDecryptError):
    """A wrap failed to open: wrong passphrase (AEAD tag failure), tampered
    blob, or malformed envelope. A subclass of ``SecretDecryptError`` so the
    existing fail-loud handling applies, but distinct so the UI can show the
    'wrong passphrase' affordance without leaking *why* it failed."""


# --------------------------------------------------------------------------
# Low-level helpers
# --------------------------------------------------------------------------

def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _ub64(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


def _aead(alg: str, key: bytes):
    if alg == "aesgcm":
        return AESGCM(key)
    if alg == "chacha20poly1305":
        return ChaCha20Poly1305(key)
    raise KeyUnwrapError(f"unsupported alg: {alg!r}")


def _resource_aad(resource_id: str) -> bytes:
    """AEAD associated-data binding a wrap to its resource so a wrap blob
    cannot be moved from one conversation onto another."""
    if not resource_id:
        raise ValueError("resource_id required")
    return _AAD_PREFIX + resource_id.encode("utf-8")


def _derive_kek(passphrase: str, salt: bytes) -> bytes:
    if not passphrase:
        raise ValueError("passphrase required")
    kdf = Scrypt(salt=salt, length=DEK_LEN,
                 n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)
    return kdf.derive(passphrase.encode("utf-8"))


# --------------------------------------------------------------------------
# DEK + passphrase wrap / unwrap
# --------------------------------------------------------------------------

def new_dek() -> bytes:
    """Mint a fresh 32-byte data encryption key."""
    return os.urandom(DEK_LEN)


def wrap_dek_passphrase(dek: bytes, passphrase: str, resource_id: str) -> dict:
    """Wrap ``dek`` under a passphrase-derived KEK. Returns a JSON-serialisable
    envelope safe to store on disk. The random per-wrap salt and the
    resource-bound AAD are baked in."""
    if len(dek) != DEK_LEN:
        raise ValueError(f"dek must be {DEK_LEN} bytes, got {len(dek)}")
    salt = os.urandom(_SALT_LEN)
    kek = _derive_kek(passphrase, salt)
    nonce = os.urandom(_NONCE_LEN)
    ct = _aead(_DEFAULT_ALG, kek).encrypt(nonce, dek, _resource_aad(resource_id))
    return {
        "scheme": _WRAP_SCHEME,
        "kdf": {"algo": "scrypt", "n": _SCRYPT_N, "r": _SCRYPT_R,
                "p": _SCRYPT_P, "salt": _b64(salt)},
        "aead": {"alg": _DEFAULT_ALG, "nonce": _b64(nonce), "ct": _b64(ct)},
    }


def unwrap_dek_passphrase(wrap: dict, passphrase: str, resource_id: str) -> bytes:
    """Recover the DEK from a passphrase wrap. Raises :class:`KeyUnwrapError`
    on a wrong passphrase, tampered blob, or malformed envelope -- never
    returns garbage."""
    if not isinstance(wrap, dict) or wrap.get("scheme") != _WRAP_SCHEME:
        raise KeyUnwrapError("not a pf-wrap-v1 envelope")
    try:
        kdf = wrap["kdf"]
        aead = wrap["aead"]
        salt = _ub64(kdf["salt"])
        nonce = _ub64(aead["nonce"])
        ct = _ub64(aead["ct"])
        alg = aead.get("alg", _DEFAULT_ALG)
        n, r, p = int(kdf["n"]), int(kdf["r"]), int(kdf["p"])
    except (KeyError, ValueError, TypeError) as e:
        raise KeyUnwrapError(f"malformed wrap envelope: {e}")
    kdf_obj = Scrypt(salt=salt, length=DEK_LEN, n=n, r=r, p=p)
    kek = kdf_obj.derive(passphrase.encode("utf-8"))
    try:
        dek = _aead(alg, kek).decrypt(nonce, ct, _resource_aad(resource_id))
    except Exception:
        # AEAD tag failure == wrong passphrase or tampered blob. Do not leak
        # which: the UI only learns "wrong passphrase".
        raise KeyUnwrapError("passphrase did not unwrap this key")
    if len(dek) != DEK_LEN:
        raise KeyUnwrapError("unwrapped key has wrong length")
    return dek


# --------------------------------------------------------------------------
# Multi-wrap container (on disk, beside resource metadata)
# --------------------------------------------------------------------------

def new_wrap_container(resource_id: str) -> dict:
    """An empty multi-wrap container with forward-compatible slots."""
    return {
        "v": 1,
        "resource_id": resource_id,
        "wraps": {slot: None for slot in WRAP_SLOTS},
    }


def create_passphrase_protected(resource_id: str,
                                passphrase: str) -> Tuple[bytes, dict]:
    """Mint a DEK and a container holding its passphrase wrap. Returns
    ``(dek, container)``; the caller stores the container and hands the DEK to
    the vault."""
    dek = new_dek()
    container = new_wrap_container(resource_id)
    container["wraps"]["pass"] = wrap_dek_passphrase(dek, passphrase, resource_id)
    return dek, container


def set_passphrase_wrap(container: dict, dek: bytes, passphrase: str) -> dict:
    """Re-wrap ``dek`` under a (new) passphrase, replacing the ``pass`` slot.
    Used by enable and by passphrase change. Mutates and returns ``container``."""
    resource_id = container["resource_id"]
    container["wraps"]["pass"] = wrap_dek_passphrase(dek, passphrase, resource_id)
    return container


def remove_wrap(container: dict, slot: str) -> dict:
    if slot not in WRAP_SLOTS:
        raise ValueError(f"unknown wrap slot: {slot!r}")
    container["wraps"][slot] = None
    return container


def unwrap_with_passphrase(container: dict, passphrase: str) -> bytes:
    """Open a container's ``pass`` wrap. Convenience over
    :func:`unwrap_dek_passphrase` that reads ``resource_id`` from the
    container."""
    wrap = (container.get("wraps") or {}).get("pass")
    if not wrap:
        raise KeyUnwrapError("no passphrase wrap on this resource")
    return unwrap_dek_passphrase(wrap, passphrase, container["resource_id"])


def set_relay_wrap(container: dict, dek: bytes, relay_pub_raw: bytes) -> dict:
    """Seal ``dek`` to a relay public key and store it in the ``relay`` slot
    (phase 5 enrollment). The server holds no key that can open this wrap.
    Mutates and returns ``container``."""
    from core.relay_keywrap import seal_dek
    container["wraps"]["relay"] = seal_dek(dek, relay_pub_raw)
    return container


def unwrap_with_relay(container: dict, relay_priv_raw: bytes) -> bytes:
    """Recover the DEK from a container's ``relay`` slot using the relay private
    key (runs relay-side). Raises if there is no relay wrap."""
    from core.relay_keywrap import unseal_dek
    wrap = (container.get("wraps") or {}).get("relay")
    if not wrap:
        raise KeyUnwrapError("no relay wrap on this resource")
    return unseal_dek(wrap, relay_priv_raw)


# --------------------------------------------------------------------------
# Best-effort memory locking
# --------------------------------------------------------------------------

_mlock_attempted = False


def _best_effort_mlockall() -> None:
    """Try to pin the process's pages so DEKs are not swapped to disk. Best
    effort: needs CAP_IPC_LOCK / RLIMIT_MEMLOCK and a libc with mlockall, so
    it quietly no-ops where unavailable. Attempted once, lazily, the first
    time a DEK enters the vault."""
    global _mlock_attempted
    if _mlock_attempted:
        return
    _mlock_attempted = True
    try:
        import ctypes
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        MCL_CURRENT, MCL_FUTURE = 1, 2
        if libc.mlockall(MCL_CURRENT | MCL_FUTURE) == 0:
            logger.debug("[key_vault] mlockall succeeded")
        else:
            logger.debug("[key_vault] mlockall unavailable (errno=%s) -- "
                         "DEKs may be swappable", ctypes.get_errno())
    except Exception:
        logger.debug("[key_vault] mlockall not attempted", exc_info=True)


# --------------------------------------------------------------------------
# KeyVault -- RAM-only, session/source-indexed DEK custody
# --------------------------------------------------------------------------

class KeyVault:
    """In-RAM custody of unlocked DEKs.

    Never serialised. Thread-safe. DEK bytes are held in ``bytearray`` and
    zeroised when dropped. Each DEK may be tagged with a *session_id* (the
    login session that unlocked it) and/or a *source* (e.g. the relay
    connection that delivered it), so the eviction invariants can be enforced
    in one call:

      * logout / session invalidation -> :meth:`purge_session`
      * relay disconnect / local relock -> :meth:`purge_source`
      * idle-lock / explicit lock -> :meth:`drop`
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._deks: Dict[str, bytearray] = {}
        self._by_session: Dict[str, Set[str]] = {}
        self._by_source: Dict[str, Set[str]] = {}
        self._touched: Dict[str, float] = {}  # resource_id -> last access (monotonic)

    def put(self, resource_id: str, dek: bytes, *,
            session_id: Optional[str] = None,
            source: Optional[str] = None) -> None:
        """Store (or replace) the DEK for ``resource_id`` and tag it."""
        if not resource_id:
            raise ValueError("resource_id required")
        if len(dek) != DEK_LEN:
            raise ValueError(f"dek must be {DEK_LEN} bytes, got {len(dek)}")
        _best_effort_mlockall()
        with self._lock:
            self._zeroise_locked(resource_id)
            self._deks[resource_id] = bytearray(dek)
            self._touched[resource_id] = time.monotonic()
            if session_id:
                self._by_session.setdefault(session_id, set()).add(resource_id)
            if source:
                self._by_source.setdefault(source, set()).add(resource_id)
        ensure_idle_lock_sweeper()

    def put_all(self, items: Iterable[Tuple[str, bytes]], *,
                session_id: Optional[str] = None,
                source: Optional[str] = None) -> None:
        """Bulk insert -- the relay push-at-connect path hands the vault every
        DEK a relay delivered in one batch, all tagged with that connection."""
        for resource_id, dek in items:
            self.put(resource_id, dek, session_id=session_id, source=source)

    def get(self, resource_id: str) -> Optional[bytes]:
        """Return a *copy* of the DEK, or None if the resource is locked.
        Counts as activity for idle-lock (refreshes the last-access stamp)."""
        with self._lock:
            buf = self._deks.get(resource_id)
            if buf is None:
                return None
            self._touched[resource_id] = time.monotonic()
            return bytes(buf)

    def is_unlocked(self, resource_id: str) -> bool:
        with self._lock:
            return resource_id in self._deks

    def drop(self, resource_id: str) -> bool:
        """Forget one resource's DEK (idle-lock / explicit lock). Returns True
        if it was present."""
        with self._lock:
            present = resource_id in self._deks
            self._zeroise_locked(resource_id)
            for index in (self._by_session, self._by_source):
                for owner in list(index.keys()):
                    index[owner].discard(resource_id)
                    if not index[owner]:
                        del index[owner]
            return present

    def purge_session(self, session_id: str) -> int:
        """Drop every DEK unlocked under ``session_id`` (logout / invalidation).
        Returns the count purged."""
        with self._lock:
            rids = list(self._by_session.get(session_id, ()))
            for rid in rids:
                self.drop(rid)
            return len(rids)

    def purge_source(self, source: str) -> int:
        """Drop every DEK delivered by ``source`` (relay disconnect / relock).
        Enforces the relay-gone = relocked invariant. Returns count purged."""
        with self._lock:
            rids = list(self._by_source.get(source, ()))
            for rid in rids:
                self.drop(rid)
            return len(rids)

    def clear(self) -> None:
        """Drop everything (server shutdown / full relock)."""
        with self._lock:
            for rid in list(self._deks.keys()):
                self._zeroise_locked(rid)
            self._by_session.clear()
            self._by_source.clear()

    def unlocked_ids(self) -> Set[str]:
        with self._lock:
            return set(self._deks.keys())

    def purge_idle(self, max_idle_seconds: float) -> int:
        """Drop every DEK not accessed within ``max_idle_seconds`` (the idle-lock
        sweep — RFC open-decision #2, default 15 min). Returns count purged.
        A periodic caller invokes this; the eviction itself is here so the
        zeroise + index cleanup stays in one place."""
        cutoff = time.monotonic() - max(0.0, max_idle_seconds)
        with self._lock:
            stale = [rid for rid, t in self._touched.items() if t < cutoff]
            for rid in stale:
                self.drop(rid)
            return len(stale)

    def _zeroise_locked(self, resource_id: str) -> None:
        """Overwrite and drop one DEK buffer. Caller holds the lock."""
        self._touched.pop(resource_id, None)
        buf = self._deks.pop(resource_id, None)
        if buf is not None:
            for i in range(len(buf)):
                buf[i] = 0


# --------------------------------------------------------------------------
# Singleton
# --------------------------------------------------------------------------

_instance: Optional[KeyVault] = None
_instance_lock = threading.Lock()

# Idle-lock sweeper (RFC open-decision #2): purge DEKs untouched for longer than
# IDLE_LOCK_SECONDS, every IDLE_LOCK_SWEEP_SECONDS. Lazily started on the first
# DEK put so it only runs once encryption is actually in use.
IDLE_LOCK_SECONDS = float(os.getenv("PAWFLOW_ENC_IDLE_LOCK_SECONDS", "900") or "900")
_SWEEP_SECONDS = float(os.getenv("PAWFLOW_ENC_IDLE_SWEEP_SECONDS", "60") or "60")
_sweeper_started = False
_sweeper_lock = threading.Lock()


def get_key_vault() -> KeyVault:
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = KeyVault()
    return _instance


def ensure_idle_lock_sweeper() -> None:
    """Start the background idle-lock sweep once. No-op under pytest (tests drive
    purge_idle directly) and when the idle budget is disabled (<= 0)."""
    global _sweeper_started
    if IDLE_LOCK_SECONDS <= 0 or "PYTEST_CURRENT_TEST" in os.environ:
        return
    with _sweeper_lock:
        if _sweeper_started:
            return
        _sweeper_started = True

    def _loop() -> None:
        while True:
            time.sleep(_SWEEP_SECONDS)
            try:
                n = get_key_vault().purge_idle(IDLE_LOCK_SECONDS)
                if n:
                    logger.info("[key_vault] idle-lock purged %d DEK(s)", n)
            except Exception:
                logger.debug("[key_vault] idle sweep failed", exc_info=True)

    threading.Thread(target=_loop, daemon=True, name="enc-idle-lock").start()


def _reset_for_tests() -> None:
    """Drop the singleton between test cases. Production code MUST NOT call
    this."""
    global _instance, _mlock_attempted
    with _instance_lock:
        if _instance is not None:
            _instance.clear()
        _instance = None
    _mlock_attempted = False
