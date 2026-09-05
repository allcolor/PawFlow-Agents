"""Shared identity and storage contract for native CLI login and execution."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

PROVIDERS = {
    "cursor-acp": ("cursor", "Cursor", "PAWFLOW_CURSOR", "cursor-agent"),
    "grok-build-acp": ("grok", "Grok Build", "PAWFLOW_GROK_BUILD", "grok"),
    "opencode": ("opencode", "OpenCode", "PAWFLOW_OPENCODE", "opencode"),
}


def native_cli_home(provider: str, user_id: str, service_id: str) -> Path:
    if provider not in PROVIDERS or not user_id or not service_id:
        raise ValueError("Native CLI auth requires provider, user_id and service_id")
    if provider == "opencode":
        from core.opencode_pool import OpenCodePool
        return OpenCodePool.home_dir(user_id, service_id)
    from core.paths import RUNTIME_DIR
    digest = hashlib.sha256(json.dumps(
        [provider, user_id, service_id], separators=(",", ":")).encode()).hexdigest()
    home = RUNTIME_DIR / "sessions" / "native-cli" / "homes" / digest
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    return home


def native_cli_image(provider: str) -> str:
    if provider == "opencode":
        from core.opencode_pool import OpenCodePool
        return OpenCodePool.image()
    return os.environ.get(PROVIDERS[provider][2] + "_IMAGE") or "pawflow-claude-code:latest"


def native_cli_binary(provider: str) -> str:
    if provider == "opencode":
        from core.opencode_pool import OpenCodePool
        return OpenCodePool.binary()
    return os.environ.get(PROVIDERS[provider][2] + "_BIN") or PROVIDERS[provider][3]


def native_cli_user_spec() -> str:
    uid = os.environ.get("PAWFLOW_RUN_UID", "1000")
    gid = os.environ.get("PAWFLOW_RUN_GID", "1000")
    if not uid.isdigit() or not gid.isdigit():
        raise ValueError("PAWFLOW_RUN_UID/GID must be numeric")
    return f"{uid}:{gid}"


def native_cli_auth_status(provider: str, user_id: str, service_id: str) -> dict:
    """Report stored material only; never claim that offline tokens are valid."""
    home = native_cli_home(provider, user_id, service_id)
    if provider == "cursor-acp":
        # Cursor's credential layout is not a public API. Only a successful
        # native login creates this marker; config-file presence proves nothing.
        stored = (home / ".pawflow-login-complete").is_file()
        return {"stored": stored, "verified": False}
    relative = ".grok/auth.json" if provider == "grok-build-acp" else ".local/share/opencode/auth.json"
    try:
        data = json.loads((home / relative).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return {"stored": isinstance(data, dict) and bool(data), "verified": False}


def merge_native_auth(home: Path, relative: str, incoming: dict) -> None:
    """Merge provider credentials in place, preserving other profiles and binds."""
    if not isinstance(incoming, dict) or not incoming:
        raise ValueError("Login wrote no credentials")
    target = home / relative
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        existing = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        existing = {}
    if not isinstance(existing, dict):
        raise ValueError("Existing credential file must contain an object")
    existing.update(incoming)
    # Keep the inode: running OpenCode sessions bind/symlink this auth file.
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        os.fchmod(handle.fileno(), 0o600)
        json.dump(existing, handle)
