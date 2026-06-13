"""Conversation field-level cipher -- encrypt content, keep metadata clear.

Phase 2 of the encryption-at-rest design (docs/design/encryption-at-rest.md,
open-decision #3 [Decided]: "encrypt content fields, metadata clear").

A conversation transcript is line-delimited JSON. Each row carries structural
metadata that the storage layer must be able to read **without** the DEK --
the five-field invariant ``(msg_id, ts, seq, conversation_id, user_id)`` plus
``role`` / ``tool_call_id`` / ``parent_message_id`` / ``tool_name`` -- because
ordering, restart-from (``truncate_after_msg_id``), patch and delete all key on
that metadata. Only the *content-bearing* fields are encrypted:

    content    -- user / assistant / thinking text, and tool *results*
    arguments  -- tool-call input (may be a dict / list, not just a string)

This keeps the file git-compatible: it stays valid JSONL with clear metadata,
so per-conversation git history, diffs, retention and the segment index work
unchanged; only the opaque content strings differ between revisions.

The codec is bound to one conversation's **DEK** (from the KeyVault). Encrypt
is idempotent (an already-wrapped value is left alone) and decrypt is a
pass-through for any value that is not one of our envelopes -- so a partially
migrated log, or a plaintext (non-encrypted) conversation, reads back correctly
through the same code path.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any, Dict

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from core.secrets import SecretDecryptError

# Fields whose values hold conversation *content*. Everything else on a row is
# structural metadata and stays clear. Versioned via the envelope, so this set
# can grow without breaking already-stored rows.
CONTENT_FIELDS = ("content", "arguments")

_PREFIX = "enc:cv1:"
_ALG = "aesgcm"
_NONCE_LEN = 12
_DEK_LEN = 32
_AAD_DOMAIN = b"pf-conv-field:v1:"


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _ub64(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


def _field_aad(field: str) -> bytes:
    """Bind a ciphertext to the field it came from so a ``content`` blob can
    never be reinterpreted as ``arguments`` (or vice-versa) on the same row."""
    return _AAD_DOMAIN + field.encode("utf-8")


def is_encrypted_value(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(_PREFIX)


class RowCodec:
    """Encrypt/decrypt the content fields of conversation rows under one DEK.

    Duck-typed against what :class:`SegmentedJsonl` expects: ``encode(row)``
    on write, ``decode(row)`` on read. Stateless beyond the key, so one codec
    can be shared across a conversation's transcript / shared / agent logs.
    """

    __slots__ = ("_dek",)

    def __init__(self, dek: bytes):
        if len(dek) != _DEK_LEN:
            raise ValueError(f"dek must be {_DEK_LEN} bytes, got {len(dek)}")
        self._dek = bytes(dek)

    # -- single value --------------------------------------------------

    def _encrypt_value(self, value: Any, field: str) -> str:
        if value is None or value == "":
            return value  # nothing to hide; keep empty/None clear
        if is_encrypted_value(value):
            return value  # idempotent -- already wrapped
        if isinstance(value, str):
            tag, raw = "s", value.encode("utf-8")
        else:
            tag = "j"
            raw = json.dumps(value, ensure_ascii=False,
                             sort_keys=True).encode("utf-8")
        nonce = os.urandom(_NONCE_LEN)
        ct = AESGCM(self._dek).encrypt(nonce, raw, _field_aad(field))
        payload = json.dumps(
            {"v": 1, "t": tag, "alg": _ALG,
             "nonce": _b64(nonce), "ct": _b64(ct)},
            separators=(",", ":"), sort_keys=True).encode("utf-8")
        return _PREFIX + _b64(payload)

    def _decrypt_value(self, value: Any, field: str) -> Any:
        if not is_encrypted_value(value):
            return value  # plaintext / non-encrypted / partially migrated
        try:
            payload = json.loads(_ub64(value[len(_PREFIX):]).decode("utf-8"))
            tag = payload["t"]
            nonce = _ub64(payload["nonce"])
            ct = _ub64(payload["ct"])
        except Exception as e:
            raise SecretDecryptError(f"malformed conv-field envelope: {e}")
        try:
            raw = AESGCM(self._dek).decrypt(nonce, ct, _field_aad(field))
        except Exception as e:
            raise SecretDecryptError(
                f"conv-field decrypt failed (field={field}): {e}")
        text = raw.decode("utf-8")
        return text if tag == "s" else json.loads(text)

    # -- whole row -----------------------------------------------------

    def encode(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """Return a copy of ``row`` with content fields encrypted. Metadata is
        untouched. Idempotent on already-encrypted rows."""
        out = dict(row)
        for field in CONTENT_FIELDS:
            if field in out:
                out[field] = self._encrypt_value(out[field], field)
        return out

    def decode(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """Return a copy of ``row`` with content fields decrypted. Plaintext
        values pass through, so mixed logs decode correctly."""
        out = dict(row)
        for field in CONTENT_FIELDS:
            if field in out:
                out[field] = self._decrypt_value(out[field], field)
        return out


# --------------------------------------------------------------------------
# Migration primitives (the "migration job" of RFC phase 2)
# --------------------------------------------------------------------------

def encrypt_log(path, dek: bytes) -> int:
    """Rewrite a SegmentedJsonl stream in place, encrypting content fields.

    Reads rows raw (so plaintext and any already-encrypted rows both pass
    through) and writes them back through the codec, whose ``encode`` is
    idempotent -- so this is safe to re-run on a partially migrated log
    (resumable migration). Returns the row count rewritten.
    """
    from core.segmented_jsonl import SegmentedJsonl
    src = SegmentedJsonl(path)
    if not src.exists():
        return 0
    rows = list(src.iter_rows())
    SegmentedJsonl(path, codec=RowCodec(dek)).replace_dicts(rows)
    return len(rows)


def decrypt_log(path, dek: bytes) -> int:
    """Rewrite a SegmentedJsonl stream in place, decrypting content fields
    back to clear (used by 'disable encryption'). Returns the row count."""
    from core.segmented_jsonl import SegmentedJsonl
    src = SegmentedJsonl(path, codec=RowCodec(dek))
    if not src.exists():
        return 0
    rows = list(src.iter_rows())
    SegmentedJsonl(path).replace_dicts(rows)
    return len(rows)
