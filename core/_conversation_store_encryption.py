"""ConversationStore encryption enable/unlock/relay/escrow."""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.segmented_jsonl import SegmentedJsonl

logger = logging.getLogger(__name__)
# Split out of conversation_store.py for the <=800-line rule; composed back into
# ConversationStore (invariant 2: MRO/shared state on the host).

from core._conversation_store_base import (  # noqa: F401,E402
    _CTX_CACHE_MAX_MESSAGES, _CTX_CACHE_MAX_CHARS, _CTX_CACHE_MAX_CONVS, _CONV_LOCK_DIAG_MS, _GIT_RETENTION_DAYS, _GIT_RETENTION_COMMITS, _GIT_RETENTION_INTERVAL_SEC, _HOT_METADATA_FLUSH_INTERVAL_SEC, _HOT_METADATA_FLUSH_MSG_DELTA, _HOT_METADATA_KEYS, _HOT_METADATA_EXECUTOR, _GIT_RETENTION_EXECUTOR, _GIT_RETENTION_RUNNING, _GIT_RETENTION_RUNNING_LOCK, ConversationLockedError, _ConversationTimedRLock)


class _CsEncryptionMixin:
    """encryption enable/unlock/relay/escrow."""

    def _is_encryption_enabled(self, cid: str) -> bool:
        """Authoritative (disk-backed) 'is this conversation encrypted?', cached
        per process. Must never guess False for an encrypted conv, or the hot
        write path would persist plaintext — so the first lookup reads the
        descriptor from extras and the result is cached; enable/disable update
        the cache in lock-step."""
        val = self._enc_enabled.get(cid)
        if val is None:
            val = bool((self._encryption_descriptor(cid) or {}).get("enabled"))
            self._enc_enabled[cid] = val
        return val

    def _codec_for(self, cid: str):
        """Return a RowCodec when ``cid`` is encrypted AND its DEK is unlocked in
        the KeyVault; else None (plaintext passthrough). When enabled but locked
        this is None by design: reads yield ciphertext (no plaintext leak) and
        the write gate in append_message refuses to persist."""
        if not self._is_encryption_enabled(cid):
            return None
        from core.key_vault import get_key_vault
        from core.conversation_cipher import RowCodec
        dek = get_key_vault().get(f"conv:{cid}")
        return RowCodec(dek) if dek is not None else None

    def _encryption_descriptor(self, cid: str) -> dict:
        try:
            return self.get_extra(cid, "encryption", {}) or {}
        except Exception:
            return {}

    def _set_encryption_descriptor(self, cid: str, desc: dict) -> None:
        self.set_extra(cid, "encryption", desc)
        self._enc_enabled[cid] = bool(desc.get("enabled"))

    def _content_log_paths(self, cid: str) -> List[Path]:
        """Every content-bearing log under a conversation: transcript, shared
        context, and each agent's context — the set the migration job rewrites."""
        paths = [self._transcript_path(cid), self._shared_ctx_path(cid)]
        conv_dir = self._conv_dir(cid)
        if conv_dir.is_dir():
            for entry in sorted(conv_dir.iterdir()):
                if entry.is_dir() and self._jsonl_exists(entry / "context.jsonl"):
                    paths.append(entry / "context.jsonl")
        return paths

    def _invalidate_enc_caches(self, cid: str) -> None:
        """Drop cached rows after a lock/unlock/enable/disable so the next read
        re-resolves the codec instead of serving stale plain/cipher rows."""
        with self._cache_lock:
            self._cache.pop(cid, None)
        try:
            self._invalidate_ctx_cache(cid)
        except Exception:
            logger.debug("ctx cache invalidation failed", exc_info=True)

    def encryption_status(self, cid: str) -> Dict[str, Any]:
        """Report encryption state for the UI (conv_encrypt_status)."""
        desc = self._encryption_descriptor(cid)
        enabled = bool(desc.get("enabled"))
        wraps = ((desc.get("container") or {}).get("wraps")) or {}
        unlocked = False
        if enabled:
            from core.key_vault import get_key_vault
            unlocked = get_key_vault().is_unlocked(f"conv:{cid}")
        return {
            "enabled": enabled,
            "unlocked": unlocked,
            "state": "off" if not enabled else ("unlocked" if unlocked else "locked"),
            "has_pass_wrap": bool(wraps.get("pass")),
            "has_relay_wrap": bool(wraps.get("relay")),
            "has_escrow": bool(wraps.get("escrow")),
            "relay_key_id": desc.get("relay_key_id", ""),
        }

    def enable_encryption(self, cid: str, passphrase: str,
                          session_id: str = "") -> Dict[str, Any]:
        """Turn on encryption: mint a DEK, store its passphrase wrap, unlock it
        in the vault, and migrate every content log to ciphertext. Idempotent —
        a no-op if already enabled."""
        if not self.exists(cid):
            raise ValueError(f"conversation {cid[:16]} not found")
        if not passphrase:
            raise ValueError("passphrase required")
        from core.key_vault import create_passphrase_protected, get_key_vault
        from core.conversation_cipher import encrypt_log
        lock = self._get_conv_lock(cid)
        with lock:
            if self._encryption_descriptor(cid).get("enabled"):
                return self.encryption_status(cid)
            dek, container = create_passphrase_protected(f"conv:{cid}", passphrase)
            # Descriptor + vault FIRST, then migrate. A crash mid-migration
            # leaves enabled+locked with a mix of cipher/plaintext rows, which
            # decode tolerates (plaintext passes through) and encrypt_log can
            # resume idempotently — never a corrupt, unreadable conversation.
            get_key_vault().put(f"conv:{cid}", dek, session_id=session_id)
            self._set_encryption_descriptor(
                cid, {"enabled": True, "v": 1, "container": container,
                      "migrated": False})
            for path in self._content_log_paths(cid):
                encrypt_log(path, dek)
            desc = self._encryption_descriptor(cid)
            desc["migrated"] = True
            self._set_encryption_descriptor(cid, desc)
            self._invalidate_enc_caches(cid)
        return self.encryption_status(cid)

    def unlock_encryption(self, cid: str, passphrase: str,
                          session_id: str = "") -> bool:
        """Unwrap the DEK with ``passphrase`` into the vault. Raises KeyUnwrapError
        on a wrong passphrase. Finishes any migration left pending by a crash."""
        desc = self._encryption_descriptor(cid)
        if not desc.get("enabled"):
            raise ValueError("conversation is not encrypted")
        from core.key_vault import get_key_vault, unwrap_with_passphrase
        from core.conversation_cipher import encrypt_log
        dek = unwrap_with_passphrase(desc["container"], passphrase)
        get_key_vault().put(f"conv:{cid}", dek, session_id=session_id)
        self._enc_enabled[cid] = True
        if not desc.get("migrated", True):
            with self._get_conv_lock(cid):
                for path in self._content_log_paths(cid):
                    encrypt_log(path, dek)
                desc = self._encryption_descriptor(cid)
                desc["migrated"] = True
                self._set_encryption_descriptor(cid, desc)
        self._invalidate_enc_caches(cid)
        return True

    def lock_encryption(self, cid: str) -> None:
        """Drop the DEK from RAM now (idle-lock / explicit lock / re-lock)."""
        from core.key_vault import get_key_vault
        get_key_vault().drop(f"conv:{cid}")
        self._invalidate_enc_caches(cid)

    def disable_encryption(self, cid: str, session_id: str = "") -> Dict[str, Any]:
        """Decrypt every content log back to clear and remove the wraps. Requires
        the conversation to be unlocked."""
        desc = self._encryption_descriptor(cid)
        if not desc.get("enabled"):
            return self.encryption_status(cid)
        from core.key_vault import get_key_vault
        from core.conversation_cipher import decrypt_log
        kv = get_key_vault()
        dek = kv.get(f"conv:{cid}")
        if dek is None:
            raise ConversationLockedError("unlock the conversation before disabling encryption")
        lock = self._get_conv_lock(cid)
        with lock:
            for path in self._content_log_paths(cid):
                decrypt_log(path, dek)
            self._set_encryption_descriptor(cid, {"enabled": False})
            kv.drop(f"conv:{cid}")
            self._invalidate_enc_caches(cid)
        return self.encryption_status(cid)

    def change_encryption_passphrase(self, cid: str, old_passphrase: str,
                                     new_passphrase: str) -> bool:
        """Re-wrap the DEK under a new passphrase. The DEK and content are
        unchanged — only the passphrase wrap is replaced."""
        desc = self._encryption_descriptor(cid)
        if not desc.get("enabled"):
            raise ValueError("conversation is not encrypted")
        if not new_passphrase:
            raise ValueError("new passphrase required")
        from core.key_vault import set_passphrase_wrap, unwrap_with_passphrase
        dek = unwrap_with_passphrase(desc["container"], old_passphrase)
        set_passphrase_wrap(desc["container"], dek, new_passphrase)
        self._set_encryption_descriptor(cid, desc)
        return True

    def set_conv_relay(self, cid: str, relay_pub_b64: str) -> Dict[str, Any]:
        """Enroll a trusted key-relay: seal the conversation DEK to the relay's
        public key and store it in the container's ``relay`` slot (phase 5,
        ``conv_encrypt_set_relay``). Requires the conversation to be unlocked
        (the server must hold the DEK to seal it). The server keeps no key that
        can open this wrap."""
        import base64
        desc = self._encryption_descriptor(cid)
        if not desc.get("enabled"):
            raise ValueError("conversation is not encrypted")
        from core.key_vault import get_key_vault, set_relay_wrap
        from core.relay_keywrap import key_id_for
        dek = get_key_vault().get(f"conv:{cid}")
        if dek is None:
            raise ConversationLockedError("unlock the conversation before binding a relay")
        try:
            pub = base64.b64decode(relay_pub_b64)
        except Exception as e:
            raise ValueError(f"invalid relay public key: {e}")
        set_relay_wrap(desc["container"], dek, pub)
        desc["relay_key_id"] = key_id_for(pub)
        self._set_encryption_descriptor(cid, desc)
        return self.encryption_status(cid)

    def remove_conv_relay(self, cid: str) -> Dict[str, Any]:
        """Unbind the trusted key-relay: drop the ``relay`` wrap so the relay can
        no longer supply the DEK."""
        from core.key_vault import remove_wrap
        desc = self._encryption_descriptor(cid)
        if not desc.get("enabled"):
            return self.encryption_status(cid)
        remove_wrap(desc["container"], "relay")
        desc.pop("relay_key_id", None)
        self._set_encryption_descriptor(cid, desc)
        return self.encryption_status(cid)

    def set_conv_escrow(self, cid: str, recovery_passphrase: str) -> Dict[str, Any]:
        """Add an optional recovery (escrow) wrap under a separate recovery
        passphrase (phase 7). Requires the conversation to be unlocked."""
        desc = self._encryption_descriptor(cid)
        if not desc.get("enabled"):
            raise ValueError("conversation is not encrypted")
        if not recovery_passphrase:
            raise ValueError("recovery passphrase required")
        from core.key_vault import get_key_vault, set_escrow_wrap
        dek = get_key_vault().get(f"conv:{cid}")
        if dek is None:
            raise ConversationLockedError("unlock the conversation before adding recovery")
        set_escrow_wrap(desc["container"], dek, recovery_passphrase)
        self._set_encryption_descriptor(cid, desc)
        return self.encryption_status(cid)

    def remove_conv_escrow(self, cid: str) -> Dict[str, Any]:
        """Drop the recovery (escrow) wrap."""
        from core.key_vault import remove_wrap
        desc = self._encryption_descriptor(cid)
        if not desc.get("enabled"):
            return self.encryption_status(cid)
        remove_wrap(desc["container"], "escrow")
        self._set_encryption_descriptor(cid, desc)
        return self.encryption_status(cid)

    def unlock_encryption_with_recovery(self, cid: str, recovery_passphrase: str,
                                        session_id: str = "") -> bool:
        """Unlock via the escrow recovery passphrase (when the primary is lost).
        Raises KeyUnwrapError on a wrong recovery passphrase."""
        desc = self._encryption_descriptor(cid)
        if not desc.get("enabled"):
            raise ValueError("conversation is not encrypted")
        from core.key_vault import get_key_vault, unwrap_with_escrow
        dek = unwrap_with_escrow(desc["container"], recovery_passphrase)
        get_key_vault().put(f"conv:{cid}", dek, session_id=session_id)
        self._enc_enabled[cid] = True
        self._invalidate_enc_caches(cid)
        return True

    def unlock_encryption_with_dek(self, cid: str, dek: bytes, *,
                                   session_id: str = "",
                                   source: str = "") -> bool:
        """Unlock using a DEK already recovered out-of-band (the key-relay
        delivered it over the WS control channel). ``source`` tags the
        delivering relay connection so KeyVault.purge_source can re-lock when it
        drops (relay-gone = relocked)."""
        desc = self._encryption_descriptor(cid)
        if not desc.get("enabled"):
            raise ValueError("conversation is not encrypted")
        from core.key_vault import get_key_vault
        get_key_vault().put(f"conv:{cid}", dek, session_id=session_id, source=source)
        self._enc_enabled[cid] = True
        self._invalidate_enc_caches(cid)
        return True

    def relay_wrap_for(self, cid: str) -> Optional[dict]:
        """The ``wrap_relay`` blob to hand a relay for unsealing (None if unbound).
        Used by the push-at-connect / need-DEK delivery path."""
        desc = self._encryption_descriptor(cid)
        return ((desc.get("container") or {}).get("wraps") or {}).get("relay")

    def flush_append_handles(self, cid: str) -> None:
        SegmentedJsonl.flush_append_handles(self._conv_dir(cid))
