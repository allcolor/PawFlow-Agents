"""Configuration and session persistence for PawCode."""
import logging

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
        # Decrypt token — wrapped in a background thread with a 3s hard
        # timeout: Windows DPAPI (CryptUnprotectData) can hang forever
        # when lsass is unhealthy (e.g. after system OOM pressure). If
        # decrypt can't answer in 3s, drop the session file so the user
        # just /login again.
        encrypted_token = data.get("token", "")
        if encrypted_token:
            import threading as _th
            _result = {}
            def _do_unprotect():
                try:
                    from pawflow_cli.secure_store import unprotect
                    _result["plain"] = unprotect(encrypted_token)
                except Exception as _ue:
                    _result["err"] = _ue
            _t = _th.Thread(target=_do_unprotect, daemon=True)
            _t.start()
            _t.join(timeout=3.0)
            if _t.is_alive():
                try:
                    SESSION_FILE.unlink()
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                return {}
            if "plain" in _result:
                data["token"] = _result["plain"]
            else:
                # Migration: token might be plain text from old version
                data["token"] = encrypted_token
        return data
    except Exception:
        return {}


def save_session(token: str, username: str, server_url: str, expires_at: float):
    """Save session with encrypted token."""
    ensure_config_dir()
    # Encrypt the token — same DPAPI hang issue as unprotect: wrap in a
    # thread with a 3s hard timeout and fall back to plain storage if
    # the OS credential layer doesn't answer. Better a plain-text token
    # on disk than a CLI frozen forever after a successful /login.
    import threading as _th
    _result = {}
    def _do_protect():
        try:
            from pawflow_cli.secure_store import protect
            _result["enc"] = protect(token)
        except Exception as _pe:
            _result["err"] = _pe
    _t = _th.Thread(target=_do_protect, daemon=True)
    _t.start()
    _t.join(timeout=3.0)
    if _t.is_alive():
        encrypted_token = token
        sys.stderr.write("[PawCode] Warning: OS credential protection timed out; "
                         "storing token unencrypted\n")
    elif "enc" in _result:
        encrypted_token = _result["enc"]
    else:
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
