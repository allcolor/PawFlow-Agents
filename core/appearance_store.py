"""Server-backed per-user chat appearance preferences."""

import hashlib
import json
import re
import threading
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlparse

import core.paths as _paths


PREFS_VERSION = 1
DEFAULTS = {
    "version": PREFS_VERSION,
    "scale": 100,
    "source": "none",
    "kind": "image",
    "url": "",
    "file_id": "",
    "name": "",
    "dim": 38,
    "blur": 0,
    "saturation": 100,
    "panel": 88,
    "motion": False,
}
_LOCK = threading.RLock()


def _safe_user_id(user_id: str) -> str:
    if not user_id:
        raise ValueError("Authenticated user is required")
    cleaned = re.sub(r"[^A-Za-z0-9_.@-]", "_", user_id)[:120]
    if cleaned in {"", ".", ".."}:
        cleaned = "user-" + hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:20]
    return cleaned


def _path(user_id: str) -> Path:
    return _paths.USER_CONFIG_DIR / _safe_user_id(user_id) / "appearance.json"


def _clamp(value: Any, minimum: int, maximum: int, fallback: int) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    if number != number:
        return fallback
    return round(min(maximum, max(minimum, number)))


def normalize_preferences(raw: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("prefs must be an object")
    prefs = dict(DEFAULTS)
    prefs.update(raw)
    prefs["version"] = PREFS_VERSION
    prefs["scale"] = _clamp(prefs.get("scale"), 75, 150, 100)
    prefs["dim"] = _clamp(prefs.get("dim"), 0, 80, 38)
    prefs["blur"] = _clamp(prefs.get("blur"), 0, 24, 0)
    prefs["saturation"] = _clamp(prefs.get("saturation"), 50, 150, 100)
    prefs["panel"] = _clamp(prefs.get("panel"), 55, 100, 88)
    prefs["kind"] = "video" if prefs.get("kind") == "video" else "image"
    prefs["motion"] = bool(prefs.get("motion"))
    prefs["name"] = str(prefs.get("name") or "")[:255]
    prefs["url"] = str(prefs.get("url") or "")[:4096]
    prefs["file_id"] = str(prefs.get("file_id") or "")[:64]
    source = str(prefs.get("source") or "none")
    if source not in {"none", "upload", "url"}:
        raise ValueError("Invalid appearance source")
    prefs["source"] = source
    if source == "none":
        prefs.update({"url": "", "file_id": "", "name": ""})
    elif source == "url":
        parsed = urlparse(prefs["url"])
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("Remote appearance URLs must use HTTPS")
        prefs["file_id"] = ""
    elif not prefs["file_id"]:
        raise ValueError("Uploaded appearance requires file_id")
    return prefs


def _empty_record() -> Dict[str, Any]:
    return {"version": PREFS_VERSION, "global": None, "conversations": {}}


def _read(user_id: str) -> Dict[str, Any]:
    path = _path(user_id)
    if not path.exists():
        return _empty_record()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_record()
    if not isinstance(data, dict):
        return _empty_record()
    conversations = data.get("conversations")
    return {
        "version": PREFS_VERSION,
        "global": data.get("global") if isinstance(data.get("global"), dict) else None,
        "conversations": conversations if isinstance(conversations, dict) else {},
    }


def _write(user_id: str, data: Dict[str, Any]) -> None:
    path = _path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temp.replace(path)


def resolve_preferences(user_id: str, conversation_id: str = "") -> Dict[str, Any]:
    with _LOCK:
        data = _read(user_id)
    global_prefs = data["global"]
    conversation_prefs = (
        data["conversations"].get(conversation_id) if conversation_id else None
    )
    resolved = conversation_prefs or global_prefs or dict(DEFAULTS)
    return {
        "global": global_prefs,
        "conversation": conversation_prefs,
        "resolved": dict(resolved),
        "scope": "conversation" if conversation_prefs is not None else "global",
        "exists": global_prefs is not None or conversation_prefs is not None,
    }


def save_preferences(user_id: str, scope: str, prefs: Dict[str, Any],
                     conversation_id: str = "") -> Dict[str, Any]:
    normalized = normalize_preferences(prefs)
    if scope not in {"global", "conversation"}:
        raise ValueError("scope must be global or conversation")
    if scope == "conversation" and not conversation_id:
        raise ValueError("conversation_id is required for conversation appearance")
    with _LOCK:
        data = _read(user_id)
        if scope == "global":
            data["global"] = normalized
        else:
            data["conversations"][conversation_id] = normalized
        _write(user_id, data)
    return resolve_preferences(user_id, conversation_id)


def clear_conversation_preferences(user_id: str,
                                   conversation_id: str) -> Dict[str, Any]:
    if not conversation_id:
        raise ValueError("conversation_id is required")
    with _LOCK:
        data = _read(user_id)
        data["conversations"].pop(conversation_id, None)
        _write(user_id, data)
    return resolve_preferences(user_id, conversation_id)


def referenced_file_ids(user_id: str) -> set:
    with _LOCK:
        data = _read(user_id)
    prefs = [data.get("global")] + list(data["conversations"].values())
    return {
        str(item.get("file_id"))
        for item in prefs
        if isinstance(item, dict) and item.get("source") == "upload"
        and item.get("file_id")
    }
