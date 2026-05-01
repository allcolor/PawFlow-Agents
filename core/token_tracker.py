"""TokenTracker — Track LLM token usage per user.

Simple JSON-file-based tracking of input/output tokens per user.
No rate limiting by default — just counting for visibility.
"""

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

import core.paths as _paths


class TokenTracker:
    """Singleton tracker for LLM token usage."""

    _instance: Optional["TokenTracker"] = None
    _lock = threading.Lock()

    def __init__(self, path: str = ""):
        self._path = Path(path or str(_paths.TOKEN_USAGE_FILE))
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._data: Dict[str, Dict[str, Any]] = {}
        self._store_lock = threading.Lock()
        self._loaded = False
        self._dirty = False

    @classmethod
    def instance(cls) -> "TokenTracker":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls):
        with cls._lock:
            cls._instance = None

    def track(self, user_id: str, tokens_in: int, tokens_out: int,
              model: str = "", agent_name: str = "", llm_service: str = "",
              cache_read: int = 0, cache_write: int = 0):
        """Record token usage for a user, optionally per agent/service."""
        with self._store_lock:
            self._ensure_loaded()
            entry = self._data.setdefault(user_id, {
                "total_in": 0, "total_out": 0,
                "daily": {}, "models": {}, "agents": {},
            })
            entry["total_in"] += tokens_in
            entry["total_out"] += tokens_out
            entry["total_cache_read"] = entry.get("total_cache_read", 0) + cache_read
            entry["total_cache_write"] = entry.get("total_cache_write", 0) + cache_write

            # Daily tracking
            today = time.strftime("%Y-%m-%d")
            day = entry["daily"].setdefault(today, {
                "in": 0, "out": 0, "cache_read": 0, "cache_write": 0})
            day["in"] += tokens_in
            day["out"] += tokens_out
            day["cache_read"] = day.get("cache_read", 0) + cache_read
            day["cache_write"] = day.get("cache_write", 0) + cache_write

            # Per-model tracking
            if model:
                m = entry["models"].setdefault(model, {
                    "in": 0, "out": 0, "cache_read": 0, "cache_write": 0})
                m["in"] += tokens_in
                m["out"] += tokens_out
                m["cache_read"] = m.get("cache_read", 0) + cache_read
                m["cache_write"] = m.get("cache_write", 0) + cache_write

            # Per-agent tracking (agent_name::llm_service)
            if not agent_name or not llm_service:
                raise ValueError(f"BUG: agent_name={agent_name!r}, llm_service={llm_service!r} required for token tracking")
            agent_key = agent_name + "::" + llm_service
            a = entry.setdefault("agents", {}).setdefault(agent_key, {
                "agent": agent_name,
                "llm_service": llm_service,
                "in": 0, "out": 0, "cache_read": 0,
                "cache_write": 0, "calls": 0,
            })
            a["in"] += tokens_in
            a["out"] += tokens_out
            a["cache_read"] = a.get("cache_read", 0) + cache_read
            a["cache_write"] = a.get("cache_write", 0) + cache_write
            a["calls"] = a.get("calls", 0) + 1

            self._dirty = True

    def get_usage(self, user_id: str) -> Dict[str, Any]:
        """Get usage stats for a user."""
        with self._store_lock:
            self._ensure_loaded()
            entry = self._data.get(user_id)
            if not entry:
                return {"total_in": 0, "total_out": 0,
                        "total_cache_read": 0, "total_cache_write": 0,
                        "daily": {}, "models": {}, "agents": {}}
            return dict(entry)

    def get_agent_usage(self, user_id: str, agent_name: str = "") -> list:
        """Get per-agent usage stats. If agent_name given, filter to that agent."""
        with self._store_lock:
            self._ensure_loaded()
            entry = self._data.get(user_id, {})
            agents = entry.get("agents", {})
            result = []
            for key, stats in agents.items():
                if agent_name and stats.get("agent", "").lower() != agent_name.lower():
                    continue
                result.append(dict(stats))
            return result

    def get_all_usage(self) -> Dict[str, Dict[str, Any]]:
        """Get usage for all users (admin)."""
        with self._store_lock:
            self._ensure_loaded()
            return dict(self._data)

    def flush(self):
        """Write to disk if dirty."""
        with self._store_lock:
            if not self._dirty:
                return
            self._save()
            self._dirty = False

    def _ensure_loaded(self):
        if self._loaded:
            return
        self._loaded = True
        if self._path.exists():
            try:
                self._data = json.loads(
                    self._path.read_text(encoding="utf-8")
                )
            except Exception as e:
                logger.warning(f"Failed to load token usage: {e}")

    def _save(self):
        try:
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(self._data, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp.replace(self._path)
        except Exception as e:
            logger.error(f"Failed to save token usage: {e}")
