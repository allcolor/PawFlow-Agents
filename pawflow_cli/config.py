"""Configuration and session persistence for PawCode."""

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

CONFIG_DIR = Path.home() / ".pawflow"
SESSION_FILE = CONFIG_DIR / "session.json"
CONFIG_FILE = CONFIG_DIR / "config.json"
HISTORY_FILE = CONFIG_DIR / "history"


def ensure_config_dir():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def load_session(include_expired: bool = False) -> Dict[str, Any]:
    """Load cached session. Returns {} if missing (or expired, unless include_expired).

    Token is stored encrypted via OS credential protection (DPAPI on Windows).
    """
    if not SESSION_FILE.exists():
        return {}
    try:
        data = json.loads(SESSION_FILE.read_text())
        if not include_expired and data.get("expires_at", 0) < time.time():
            return {}  # expired
        # Decrypt token
        encrypted_token = data.get("token", "")
        if encrypted_token:
            try:
                from pawflow_cli.secure_store import unprotect
                data["token"] = unprotect(encrypted_token)
            except Exception:
                # Migration: token might be plain text from old version
                data["token"] = encrypted_token
        return data
    except Exception:
        return {}


def save_session(token: str, username: str, server_url: str, expires_at: float):
    """Save session with encrypted token."""
    ensure_config_dir()
    # Encrypt the token
    try:
        from pawflow_cli.secure_store import protect
        encrypted_token = protect(token)
    except Exception:
        # Fallback: store as-is (shouldn't happen on supported OS)
        encrypted_token = token
        sys.stderr.write("[PawCode] Warning: could not encrypt session token\n")
    SESSION_FILE.write_text(json.dumps({
        "token": encrypted_token,
        "username": username,
        "server_url": server_url,
        "expires_at": expires_at,
    }, indent=2))


def clear_session():
    if SESSION_FILE.exists():
        SESSION_FILE.unlink()


def load_config() -> Dict[str, Any]:
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text())
    except Exception:
        return {}


def save_config(data: Dict[str, Any]):
    ensure_config_dir()
    # Merge with existing
    existing = load_config()
    existing.update(data)
    CONFIG_FILE.write_text(json.dumps(existing, indent=2))
