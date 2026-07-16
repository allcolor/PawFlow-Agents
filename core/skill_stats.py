"""Per-skill usage statistics.

Records every ``load_skill`` so the skill loop can suggest scope
promotion and the skill curator can flag stale/never-loaded skills.

Storage: ``data/runtime/skill_stats.json`` — one JSON object keyed by
``"{user_id}||{skill_name}"``. Writes are atomic (tmp-then-replace) and
serialized by a module lock, matching the other runtime stores.
"""

import json
import logging
import threading
import time
from typing import Any, Dict

import core.paths as _paths

logger = logging.getLogger(__name__)

_STATS_FILE = _paths.RUNTIME_DIR / "skill_stats.json"
_MAX_TRACKED_CONVERSATIONS = 8
_MAX_TRACKED_AGENTS = 8

_lock = threading.Lock()
_cache: Dict[str, Dict[str, Any]] = {}
_loaded = False


def _key(user_id: str, skill_name: str) -> str:
    return f"{user_id}||{skill_name}"


def _ensure_loaded() -> None:
    global _loaded
    if _loaded:
        return
    try:
        if _STATS_FILE.exists():
            data = json.loads(_STATS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                _cache.update(data)
    except Exception:
        logger.debug("[skill-stats] load failed", exc_info=True)
    _loaded = True


def _save() -> None:
    try:
        _STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _STATS_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(_cache, indent=1), encoding="utf-8")
        tmp.replace(_STATS_FILE)
    except Exception:
        logger.debug("[skill-stats] save failed", exc_info=True)


def record_load(user_id: str, skill_name: str,
                conversation_id: str = "",
                agent_name: str = "") -> Dict[str, Any]:
    """Record one skill load. Returns the updated stats entry (a copy)."""
    if not user_id or not skill_name:
        return {}
    with _lock:
        _ensure_loaded()
        entry = _cache.setdefault(_key(user_id, skill_name), {
            "loads": 0,
            "first_used_at": time.time(),
            "last_used_at": 0.0,
            "conversations": [],
            "agents": [],
        })
        entry["loads"] = int(entry.get("loads", 0)) + 1
        entry["last_used_at"] = time.time()
        if conversation_id:
            convs = entry.setdefault("conversations", [])
            if conversation_id in convs:
                convs.remove(conversation_id)
            convs.append(conversation_id)
            del convs[:-_MAX_TRACKED_CONVERSATIONS]
        if agent_name:
            agents = entry.setdefault("agents", [])
            if agent_name in agents:
                agents.remove(agent_name)
            agents.append(agent_name)
            del agents[:-_MAX_TRACKED_AGENTS]
        _save()
        return dict(entry)


def get_stats(user_id: str, skill_name: str) -> Dict[str, Any]:
    """Stats entry for one skill (empty dict when never loaded)."""
    with _lock:
        _ensure_loaded()
        return dict(_cache.get(_key(user_id, skill_name), {}))


def stats_for_user(user_id: str) -> Dict[str, Dict[str, Any]]:
    """All stats entries for one user, keyed by skill name."""
    if not user_id:
        return {}
    prefix = f"{user_id}||"
    with _lock:
        _ensure_loaded()
        return {k[len(prefix):]: dict(v) for k, v in _cache.items()
                if k.startswith(prefix)}


def reset_for_tests() -> None:
    """Clear the in-memory cache (test isolation only)."""
    global _loaded
    with _lock:
        _cache.clear()
        _loaded = False
