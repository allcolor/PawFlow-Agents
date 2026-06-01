"""Temporary OAuth invitation tokens for controlled external account onboarding."""

import hashlib
import hmac
import json
import secrets
import threading
import time
from typing import Any, Dict, List, Optional

import core.paths as _paths


_LOCK = threading.Lock()
_TOKEN_PREFIX = "pfo_"


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _read_store() -> Dict[str, Any]:
    path = _paths.OAUTH_INVITE_TOKENS_FILE
    if not path.exists():
        return {"tokens": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"tokens": []}
        tokens = data.get("tokens")
        if not isinstance(tokens, list):
            data["tokens"] = []
        return data
    except Exception:
        return {"tokens": []}


def _write_store(data: Dict[str, Any]) -> None:
    path = _paths.OAUTH_INVITE_TOKENS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _public_record(record: Dict[str, Any], now: Optional[float] = None) -> Dict[str, Any]:
    now = time.time() if now is None else now
    expires_at = float(record.get("expires_at") or 0)
    return {
        "id": record.get("id", ""),
        "prefix": record.get("prefix", ""),
        "role": record.get("role", ""),
        "link_username": record.get("link_username", ""),
        "created_by": record.get("created_by", ""),
        "created_at": record.get("created_at", 0),
        "expires_at": expires_at,
        "expired": bool(expires_at and expires_at <= now),
    }


def create_token(*, role: str = "viewer", link_username: str = "",
                 ttl_seconds: int = 3600, created_by: str = "") -> Dict[str, Any]:
    """Create a one-time OAuth onboarding token.

    The raw token is returned once and only its SHA-256 hash is persisted.
    """
    ttl_seconds = max(60, int(ttl_seconds or 3600))
    raw = _TOKEN_PREFIX + secrets.token_urlsafe(24)
    now = time.time()
    record = {
        "id": secrets.token_urlsafe(12),
        "token_hash": _hash_token(raw),
        "prefix": raw[:12],
        "role": str(role or "viewer"),
        "link_username": str(link_username or ""),
        "created_by": str(created_by or ""),
        "created_at": now,
        "expires_at": now + ttl_seconds,
    }
    with _LOCK:
        data = _read_store()
        data.setdefault("tokens", []).append(record)
        _write_store(data)
    public = _public_record(record, now)
    public["token"] = raw
    return public


def list_tokens() -> List[Dict[str, Any]]:
    now = time.time()
    with _LOCK:
        data = _read_store()
        active = [
            r for r in data.get("tokens", [])
            if float(r.get("expires_at") or 0) > now
        ]
        if len(active) != len(data.get("tokens", [])):
            data["tokens"] = active
            _write_store(data)
        return [_public_record(r, now) for r in active]


def revoke_token(token_id: str) -> bool:
    token_id = str(token_id or "").strip()
    if not token_id:
        return False
    with _LOCK:
        data = _read_store()
        before = len(data.get("tokens", []))
        data["tokens"] = [r for r in data.get("tokens", []) if r.get("id") != token_id]
        changed = len(data["tokens"]) != before
        if changed or len(data.get("tokens", [])) != before:
            _write_store(data)
        return changed


def consume_token(raw_token: str, *, used_by: str = "") -> Optional[Dict[str, Any]]:
    raw_token = str(raw_token or "").strip()
    if not raw_token:
        return None
    digest = _hash_token(raw_token)
    now = time.time()
    with _LOCK:
        data = _read_store()
        tokens = data.get("tokens", [])
        active = [r for r in tokens if float(r.get("expires_at") or 0) > now]
        changed = len(active) != len(tokens)
        for idx, record in enumerate(list(active)):
            if not hmac.compare_digest(str(record.get("token_hash") or ""), digest):
                continue
            del active[idx]
            data["tokens"] = active
            _write_store(data)
            return dict(record)
        if changed:
            data["tokens"] = active
            _write_store(data)
    return None
