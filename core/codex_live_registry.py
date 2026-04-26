"""Codex live container registry.

Keeps a Docker container alive across turns of the same
(user_id, conv_id, agent_name) so we don't pay the spawn cost on every
turn. Unlike CC's LiveSessionRegistry, codex `exec --json` is one-shot
(the process exits at end-of-turn), so what we reuse here is the
CONTAINER — the codex binary still re-execs each turn, but inside the
same warm Docker namespace with cached node_modules, npm warmups, mounts
resolved, etc.

Lifecycle:
    get(key)       — returns the live container_name if any (else None)
    register(...)  — stash after a clean turn so the next turn reuses it
    evict(key, r)  — remove from registry (does NOT kill the container)
    kill_and_evict — evict + ask CodexPool.release(container)
    sweep_idle(ttl)— idle eviction tick (called by background daemon)

Deliberately separate from gemini's registry — see memory "Separate
pools per CLI".
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Composite key: every dimension that would invalidate reuse if it drifts.
#   user_id     — credential boundary + per-conv mount
#   conv_id     — ConversationStore session_id resume
#   agent_name  — per-agent codex_session ConversationStore key
#   service_id  — different service = different credentials
CodexLiveKey = Tuple[str, str, str, str]


@dataclass
class CodexLiveContainer:
    container_name: str
    workdir: str
    service_id: str
    spawn_at: float = field(default_factory=time.monotonic)
    last_used: float = field(default_factory=time.monotonic)
    reuse_count: int = 0
    turn_lock: object = field(default_factory=threading.RLock)

    def idle_seconds(self) -> float:
        return time.monotonic() - self.last_used


class CodexLiveRegistry:
    """Thread-safe singleton for codex live containers."""

    _instance: Optional['CodexLiveRegistry'] = None
    _instance_lock = threading.Lock()

    DEFAULT_IDLE_TTL = 600.0  # 10 min idle → reaper kills the container

    @classmethod
    def instance(cls) -> 'CodexLiveRegistry':
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
                    cls._instance._start_sweeper()
        return cls._instance

    def __init__(self):
        self._containers: Dict[CodexLiveKey, CodexLiveContainer] = {}
        self._lock = threading.Lock()
        self._sweeper_started = False
        self._sweeper_stop = threading.Event()

    def get(self, key: CodexLiveKey) -> Optional[CodexLiveContainer]:
        with self._lock:
            return self._containers.get(key)

    def register(self, key: CodexLiveKey,
                 container_name: str, workdir: str,
                 service_id: str = "") -> CodexLiveContainer:
        with self._lock:
            existing = self._containers.get(key)
            if existing and existing.container_name == container_name:
                existing.last_used = time.monotonic()
                return existing
            entry = CodexLiveContainer(
                container_name=container_name, workdir=workdir,
                service_id=service_id)
            self._containers[key] = entry
            logger.info("[codex-live] register %s container=%s",
                        _fmt_key(key), container_name)
            return entry

    def touch(self, key: CodexLiveKey, bump_reuse: bool = True):
        with self._lock:
            entry = self._containers.get(key)
            if entry is not None:
                entry.last_used = time.monotonic()
                if bump_reuse:
                    entry.reuse_count += 1

    def evict(self, key: CodexLiveKey, reason: str) -> Optional[CodexLiveContainer]:
        with self._lock:
            entry = self._containers.pop(key, None)
        if entry is not None:
            logger.info("[codex-live] evict %s (%s) container=%s",
                        _fmt_key(key), reason, entry.container_name)
        return entry

    def kill_and_evict(self, key: CodexLiveKey, reason: str) -> None:
        entry = self.evict(key, reason)
        if entry is None:
            return
        try:
            from core.codex_pool import CodexPool
            CodexPool.instance().release(entry.container_name)
        except Exception as e:
            logger.warning("[codex-live] release after evict failed: %s", e)

    def kill_and_evict_by_conv(self, conv_id: str, reason: str) -> int:
        with self._lock:
            victims = [k for k in self._containers if k[1] == conv_id]
        for k in victims:
            self.kill_and_evict(k, reason)
        return len(victims)

    def shutdown_all(self):
        with self._lock:
            keys = list(self._containers.keys())
        for k in keys:
            self.kill_and_evict(k, "shutdown")

    def sweep_idle(self, ttl: float = DEFAULT_IDLE_TTL) -> int:
        """Evict every container idle longer than ttl. Returns count."""
        cutoff = time.monotonic() - ttl
        with self._lock:
            victims = [k for k, e in self._containers.items() if e.last_used < cutoff]
        for k in victims:
            self.kill_and_evict(k, f"idle>{int(ttl)}s")
        return len(victims)

    def _start_sweeper(self):
        if self._sweeper_started:
            return
        self._sweeper_started = True

        def _loop():
            while not self._sweeper_stop.wait(60):
                try:
                    self.sweep_idle()
                except Exception:
                    logger.debug("[codex-live] sweeper tick failed", exc_info=True)

        t = threading.Thread(target=_loop, daemon=True, name="codex-live-sweeper")
        t.start()
        # atexit hook to release everything on Python shutdown
        import atexit
        atexit.register(self.shutdown_all)


def _fmt_key(key: CodexLiveKey) -> str:
    u, c, a, s = key
    return f"{u[:6] or '?'}/{c[:8] or '?'}/{a or 'default'}@{s or 'default'}"
