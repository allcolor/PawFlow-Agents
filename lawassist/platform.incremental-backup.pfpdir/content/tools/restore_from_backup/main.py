"""restore_from_backup — symmetric counterpart to incremental_backup.

Reads a manifest (latest, or a specific timestamped one) from the same
destination/root a backup was written to, decrypts it with the same
`backup_passphrase`-derived key, and re-downloads+decrypts every referenced
file into a relay-local target root. Without the correct passphrase only
opaque encrypted blobs are recoverable — that is the intended guarantee
(see the package README/skill: losing the passphrase with no escrow
configured means the backup is unrecoverable by design, not a bug).

Same KNOWN GAP as incremental_backup regarding the read/write tools' text
orientation: binary payloads round-trip through base64 with a stripped
"cat -n"-style prefix, unverified against a real remote-service round trip.
"""

import base64
import hashlib
import json
import os
import re
from pathlib import Path

from pawflow import pfp

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:  # pragma: no cover - depends on the relay's environment
    AESGCM = None

_CATN_PREFIX_RE = re.compile(r"^\s*\d+\t")
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P, _DKLEN = 2 ** 14, 8, 1, 32
_NONCE_LEN = 12


def _require_aesgcm() -> None:
    if AESGCM is None:
        pfp.error(
            "the 'cryptography' package is not importable in this relay's "
            "Python environment; restore_from_backup requires it for "
            "AES-256-GCM (install it in the relay image before use)"
        )
        raise SystemExit(1)


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        passphrase.encode("utf-8"), salt=salt,
        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_DKLEN,
    )


def _decrypt(key: bytes, blob: bytes) -> bytes:
    _require_aesgcm()
    nonce, ciphertext = blob[:_NONCE_LEN], blob[_NONCE_LEN:]
    return AESGCM(key).decrypt(nonce, ciphertext, None)


def _b64_decode_read_result(raw) -> bytes:
    text = raw if isinstance(raw, str) else (raw or {}).get("content", "") if isinstance(raw, dict) else ""
    stripped = _CATN_PREFIX_RE.sub("", text, count=1).strip()
    return base64.b64decode(stripped) if stripped else b""


def _dest_read(service: str, path: str):
    try:
        raw = pfp.call_tool("read", path=path, source=service)
    except Exception:
        return None
    try:
        return _b64_decode_read_result(raw)
    except Exception:
        return None


def _local_mkdirs(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def main() -> None:
    args = pfp.payload.get("arguments", {}) if isinstance(pfp.payload, dict) else {}
    destination_service = args.get("destination_service")
    destination_root = str(args.get("destination_root") or "_backup").rstrip("/")
    manifest_timestamp = args.get("manifest_timestamp")
    target_path = args.get("target_path")

    if not destination_service or not target_path:
        pfp.error("'destination_service' and 'target_path' are required")
        raise SystemExit(1)

    passphrase = os.environ.get("BACKUP_PASSPHRASE", "")
    if not passphrase:
        pfp.error("BACKUP_PASSPHRASE secret is not bound (declare it at install/dev-load time)")
        raise SystemExit(1)

    salt = _dest_read(destination_service, f"{destination_root}/salt.bin")
    if not salt:
        pfp.error(f"no salt found at {destination_root}/salt.bin — is there a backup here at all?")
        raise SystemExit(1)
    key = _derive_key(passphrase, salt)

    if manifest_timestamp:
        manifest_path = f"{destination_root}/manifests/{manifest_timestamp}"
    else:
        manifest_path = f"{destination_root}/manifest.json.enc"

    enc_manifest = _dest_read(destination_service, manifest_path)
    if not enc_manifest:
        pfp.error(f"manifest not found or unreadable: {manifest_path}")
        raise SystemExit(1)

    try:
        manifest = json.loads(_decrypt(key, enc_manifest).decode("utf-8"))
    except Exception as exc:
        pfp.error(
            "failed to decrypt manifest — wrong passphrase, or the backup at "
            f"this destination/root was not written by this package ({exc})"
        )
        raise SystemExit(1)

    files = manifest.get("files", {}) if isinstance(manifest, dict) else {}
    target_root = Path(target_path)
    restored = 0
    failed = []
    for rel, meta in files.items():
        digest = (meta or {}).get("sha256")
        if not digest:
            failed.append(rel)
            continue
        blob = _dest_read(destination_service, f"{destination_root}/blobs/{digest}")
        if blob is None:
            failed.append(rel)
            continue
        try:
            plaintext = _decrypt(key, blob)
        except Exception:
            failed.append(rel)
            continue
        out_path = target_root / rel
        _local_mkdirs(out_path.parent)
        with open(out_path, "wb") as f:
            f.write(plaintext)
        restored += 1

    pfp.result({
        "manifest_counter": manifest.get("_counter"),
        "manifest_created_at": manifest.get("created_at"),
        "files_restored": restored,
        "files_failed": failed,
    })


if __name__ == "__main__":
    main()
