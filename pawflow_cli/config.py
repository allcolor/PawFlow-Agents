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
    def _trace(msg):
        try:
            with open(CONFIG_DIR / "pawcode_start.log", "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%H:%M:%S')} [load_session] {msg}\n")
        except Exception:
            pass
    _trace(f"entered, SESSION_FILE={SESSION_FILE}")
    if not SESSION_FILE.exists():
        _trace("session file does not exist — returning {}")
        return {}
    try:
        _trace("reading session file")
        data = json.loads(SESSION_FILE.read_text())
        _trace(f"parsed, expires_at={data.get('expires_at', 0)}")
        if not include_expired and data.get("expires_at", 0) < time.time():
            _trace("expired — returning {}")
            return {}  # expired
        # Decrypt token
        encrypted_token = data.get("token", "")
        if encrypted_token:
            try:
                _trace("calling unprotect (DPAPI/AES)")
                from pawflow_cli.secure_store import unprotect
                data["token"] = unprotect(encrypted_token)
                _trace("unprotect returned")
            except Exception as _ue:
                _trace(f"unprotect raised {type(_ue).__name__}: {_ue} — fallback")
                # Migration: token might be plain text from old version
                data["token"] = encrypted_token
        _trace("returning data")
        return data
    except Exception as _le:
        _trace(f"exception: {type(_le).__name__}: {_le}")
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
