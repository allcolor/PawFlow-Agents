"""incremental_backup — encrypted incremental backup to a remote filesystem
service already configured on this PawFlow instance (googleDrive,
rcloneFilesystem, or any other service the generic read/write/list_dir/
mkdir/exists/delete tools can target via source=/destination=<service_id>).

Encryption is never optional (see the plan's guardrail 16.2bis): every blob
and the manifest itself are AES-256-GCM encrypted before leaving the relay.
Each self-describing envelope carries its own random scrypt salt and nonce,
so concurrent initial backups never race on mutable global encryption
metadata. Plaintext hashes still provide stable incremental comparison.

Content hashing (sha256) is computed on the PLAINTEXT before encryption, for
the same reason: comparing ciphertext hashes across two runs of a
non-deterministic AEAD (fresh random nonce every call) would never match
even for an unchanged file.

Blobs are stored at `<root>/blobs/<sha256>` (opaque name — never the
original path/filename) and the manifest mapping real path -> hash/size/
mtime is itself encrypted, so neither a file's name nor its content is ever
visible in clear text to the destination cloud provider.

KNOWN GAP, see the package README before trusting this in production: the
generic `read`/`write` tools are documented as text-oriented (line-numbered
output, pagination) rather than a guaranteed raw-bytes channel. This tool
works around that by base64-encoding every binary payload before `write` and
stripping a possible "cat -n"-style leading line-number prefix before
decoding on `read` -- an assumption that has not been validated against a
real read/write round trip through a remote filesystem service. Test with a
small file before relying on this for real backups.
"""

import base64
import hashlib
import json
import os
import re
import secrets
import time
from pathlib import Path

from pawflow import pfp

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:  # pragma: no cover - depends on the relay's environment
    AESGCM = None

_DEFAULT_EXCLUDE = {".git", "node_modules", "__pycache__", ".venv"}
_CATN_PREFIX_RE = re.compile(r"^\s*\d+\t")
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P, _DKLEN = 2 ** 14, 8, 1, 32
_NONCE_LEN = 12
_SALT_LEN = 16
_ENVELOPE_MAGIC = b"PFBK1"


def _require_aesgcm() -> None:
    if AESGCM is None:
        pfp.error(
            "the 'cryptography' package is not importable in this relay's "
            "Python environment; incremental_backup requires it for "
            "AES-256-GCM (install it in the relay image before use)"
        )
        raise SystemExit(1)


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        passphrase.encode("utf-8"), salt=salt,
        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_DKLEN,
    )


def _encrypt(passphrase: str, plaintext: bytes) -> bytes:
    _require_aesgcm()
    salt = secrets.token_bytes(_SALT_LEN)
    key = _derive_key(passphrase, salt)
    nonce = secrets.token_bytes(_NONCE_LEN)
    return (
        _ENVELOPE_MAGIC + salt + nonce
        + AESGCM(key).encrypt(nonce, plaintext, _ENVELOPE_MAGIC)
    )


def _decrypt(passphrase: str, blob: bytes) -> bytes:
    _require_aesgcm()
    header_len = len(_ENVELOPE_MAGIC) + _SALT_LEN + _NONCE_LEN
    if len(blob) <= header_len or not blob.startswith(_ENVELOPE_MAGIC):
        raise ValueError("unsupported encrypted-backup envelope")
    offset = len(_ENVELOPE_MAGIC)
    salt = blob[offset:offset + _SALT_LEN]
    offset += _SALT_LEN
    nonce = blob[offset:offset + _NONCE_LEN]
    key = _derive_key(passphrase, salt)
    return AESGCM(key).decrypt(
        nonce, blob[offset + _NONCE_LEN:], _ENVELOPE_MAGIC)


def _b64_encode(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


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


def _dest_write(service: str, path: str, data: bytes) -> None:
    pfp.call_tool("write", path=path, content=_b64_encode(data), destination=service)


def _dest_mkdir(service: str, path: str) -> None:
    try:
        pfp.call_tool("mkdir", path=path, destination=service)
    except Exception:
        pass  # best-effort: write() creating missing parents is common enough


def _walk_source(root: Path, exclude: set):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in exclude]
        for fn in filenames:
            if fn in exclude:
                continue
            full = Path(dirpath) / fn
            yield full.relative_to(root).as_posix(), full


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _prune_old_manifests(service: str, root: str, keep: int) -> None:
    # Best-effort retention: keeps the N most recent timestamped manifests,
    # never touches _backup/blobs/* (content-addressed, safe to keep forever
    # since disk cost is small relative to the risk of deleting a blob a kept
    # manifest still references without doing real reference counting).
    try:
        listing = pfp.call_tool("list_dir", path=f"{root}/manifests", source=service)
    except Exception:
        return
    names = []
    if isinstance(listing, list):
        names = [str(n) for n in listing]
    elif isinstance(listing, dict):
        names = [str(e.get("name") or e) for e in (listing.get("entries") or listing.get("files") or [])]
    names = sorted(n for n in names if n.endswith(".json.enc"))
    for stale in names[:-keep] if keep > 0 else []:
        try:
            pfp.call_tool("delete", path=f"{root}/manifests/{stale}", destination=service)
        except Exception:
            continue


def main() -> None:
    args = pfp.payload.get("arguments", {}) if isinstance(pfp.payload, dict) else {}
    source_path = args.get("source_path")
    destination_service = args.get("destination_service")
    destination_root = str(args.get("destination_root") or "_backup").rstrip("/")
    exclude = set(args.get("exclude") or []) | _DEFAULT_EXCLUDE
    keep_manifests = int(args.get("keep_manifests") or 30)

    if not source_path or not destination_service:
        pfp.error("'source_path' and 'destination_service' are required")
        raise SystemExit(1)

    passphrase = os.environ.get("BACKUP_PASSPHRASE", "")
    if not passphrase:
        pfp.error("BACKUP_PASSPHRASE secret is not bound (declare it at install/dev-load time)")
        raise SystemExit(1)

    root = Path(source_path)
    if not root.is_dir():
        pfp.error(f"source_path does not exist or is not a directory: {source_path}")
        raise SystemExit(1)

    _dest_mkdir(destination_service, destination_root)

    manifest_pointer = f"{destination_root}/manifest.json.enc"
    last_manifest = {}
    enc_prior = _dest_read(destination_service, manifest_pointer)
    if enc_prior:
        try:
            last_manifest = json.loads(
                _decrypt(passphrase, enc_prior).decode("utf-8"))
        except Exception:
            last_manifest = {}
    last_files = last_manifest.get("files", {}) if isinstance(last_manifest, dict) else {}

    current_files = {}
    uploaded = 0
    unchanged = 0
    errors = []
    for rel, full in _walk_source(root, exclude):
        try:
            stat = full.stat()
            digest = _sha256_file(full)
        except OSError as exc:
            errors.append(f"{rel}: {exc}")
            continue
        current_files[rel] = {"size": stat.st_size, "mtime": stat.st_mtime, "sha256": digest}
        prior = last_files.get(rel)
        if prior and prior.get("sha256") == digest:
            unchanged += 1
            continue
        with open(full, "rb") as f:
            plaintext = f.read()
        blob_path = f"{destination_root}/blobs/{digest}"
        _dest_write(
            destination_service, blob_path, _encrypt(passphrase, plaintext))
        uploaded += 1

    counter = int(last_manifest.get("_counter", 0)) + 1
    manifest = {
        "_counter": counter,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "files": current_files,
    }
    enc_manifest = _encrypt(
        passphrase, json.dumps(manifest, ensure_ascii=False).encode("utf-8"))
    _dest_write(destination_service, manifest_pointer, enc_manifest)
    _dest_write(destination_service, f"{destination_root}/manifests/{counter:012d}.json.enc", enc_manifest)
    _prune_old_manifests(destination_service, destination_root, keep_manifests)

    pfp.result({
        "files_total": len(current_files),
        "files_uploaded": uploaded,
        "files_unchanged": unchanged,
        "manifest_counter": counter,
        "errors": errors,
    })


if __name__ == "__main__":
    main()
