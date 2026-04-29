"""Gemini live container registry.

Mirror of CodexLiveRegistry but for Gemini ACP — keeps the Docker
container, ACP process, stdout drain, and session id alive across turns of
the same (user, conv, agent). Independent file from codex's by design — see
memory "Separate pools per CLI".
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

GeminiLiveKey = Tuple[str, str, str, str]


@dataclass
class GeminiLiveContainer:
    container_name: str
    workdir: str
    service_id: str
    svc_pool_idx: int = -1
    proc: object = None
    event_q: object = None
    reader_thread: object = None
    stop_event: object = None
    pool_container: Optional[str] = None
    session_id: str = ""
    mcp_internal_token: Optional[str] = None
    hb_state: Optional[Dict[str, object]] = None
    spawn_at: float = field(default_factory=time.monotonic)
    last_used: float = field(default_factory=time.monotonic)
    reuse_count: int = 0
    turn_lock: object = field(default_factory=threading.RLock)
    active_turn: bool = False

    def is_alive(self) -> bool:
        try:
            return self.proc is None or self.proc.poll() is None
        except Exception:
            return False

    def idle_seconds(self) -> float:
        return time.monotonic() - self.last_used


# Alias matching CC's CCLiveSession / codex's CodexLiveSession naming —
# the gemini stream code references the richer name.
GeminiLiveSession = GeminiLiveContainer


class GeminiLiveRegistry:
    _instance: Optional['GeminiLiveRegistry'] = None
    _instance_lock = threading.Lock()

    DEFAULT_IDLE_TTL = 1800.0

    @classmethod
    def instance(cls) -> 'GeminiLiveRegistry':
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
                    cls._instance._start_sweeper()
        return cls._instance

    def __init__(self):
        self._containers: Dict[GeminiLiveKey, GeminiLiveContainer] = {}
        self._lock = threading.Lock()
        self._sweeper_started = False
        self._sweeper_stop = threading.Event()
        self._idle_ttl = float(self.DEFAULT_IDLE_TTL)
        self._tick_seconds = 60

    def get(self, key: GeminiLiveKey) -> Optional[GeminiLiveContainer]:
        with self._lock:
            return self._containers.get(key)

    def register(self, key: GeminiLiveKey,
                 container_name: str, workdir: str,
                 service_id: str = "",
                 session_id: str = "",
                 proc=None,
                 event_q=None,
                 reader_thread=None,
                 stop_event=None,
                 mcp_internal_token: str = "",
                 hb_state=None,
                 active_turn: bool = False) -> GeminiLiveContainer:
        with self._lock:
            existing = self._containers.get(key)
            if existing and existing.container_name == container_name:
                existing.last_used = time.monotonic()
                if session_id:
                    existing.session_id = session_id
                if proc is not None:
                    existing.proc = proc
                if event_q is not None:
                    existing.event_q = event_q
                if reader_thread is not None:
                    existing.reader_thread = reader_thread
                if stop_event is not None:
                    existing.stop_event = stop_event
                if mcp_internal_token:
                    existing.mcp_internal_token = mcp_internal_token
                if hb_state is not None:
                    existing.hb_state = hb_state
                existing.active_turn = bool(active_turn)
                return existing
            entry = GeminiLiveContainer(
                container_name=container_name, workdir=workdir,
                service_id=service_id,
                session_id=session_id,
                proc=proc,
                event_q=event_q,
                reader_thread=reader_thread,
                stop_event=stop_event,
                mcp_internal_token=mcp_internal_token,
                hb_state=hb_state,
                active_turn=bool(active_turn),
            )
            self._containers[key] = entry
            logger.info("[gemini-live] register %s container=%s session_id=%s",
                        _fmt_key(key), container_name,
                        (session_id[:12] + "...") if session_id else "EMPTY")
            return entry

    def touch(self, key: GeminiLiveKey, bump_reuse: bool = True):
        with self._lock:
            entry = self._containers.get(key)
            if entry is not None:
                entry.last_used = time.monotonic()
                if bump_reuse:
                    entry.reuse_count += 1

    def evict(self, key: GeminiLiveKey, reason: str) -> Optional[GeminiLiveContainer]:
        with self._lock:
            entry = self._containers.pop(key, None)
        if entry is not None:
            logger.info("[gemini-live] evict %s (%s) container=%s",
                        _fmt_key(key), reason, entry.container_name)
        return entry

    def kill_and_evict(self, key: GeminiLiveKey, reason: str) -> None:
        entry = self.evict(key, reason)
        if entry is None:
            return
        try:
            from core.gemini_pool import GeminiPool
            GeminiPool.instance().release(entry.container_name)
        except Exception as e:
            logger.warning("[gemini-live] release after evict failed: %s", e)

    def kill_and_evict_by_conv(self, conv_id: str, reason: str) -> int:
        with self._lock:
            victims = [k for k in self._containers if k[1] == conv_id]
        for k in victims:
            self.kill_and_evict(k, reason)
        return len(victims)

    def kill_and_evict_by_conv_agent(self, conv_id: str, agent_name: str,
                                       reason: str) -> int:
        """Kill live containers for a specific (conv, agent) pair.

        Used by the edit-message flow to drop only the affected agent's
        warm container while leaving siblings in the same conv alive.
        """
        with self._lock:
            victims = [k for k in self._containers
                       if k[1] == conv_id and k[2] == agent_name]
        for k in victims:
            self.kill_and_evict(k, reason)
        return len(victims)

    def status(self) -> list:
        """Snapshot for /gemini_live UI telemetry. Mirror of CC's status()."""
        now = time.monotonic()
        with self._lock:
            out = []
            for key, entry in self._containers.items():
                u, c, a, svc = key
                out.append({
                    "user_id": u,
                    "conv_id": c,
                    "agent_name": a,
                    "service_id": svc,
                    "container": entry.container_name,
                    "live": True,
                    "active_turn": bool(entry.active_turn),
                    "idle_seconds": int(now - entry.last_used),
                    "reuse_count": entry.reuse_count,
                    "spawn_at": entry.spawn_at,
                    "lived_seconds": int(now - entry.spawn_at),
                })
        return out

    def __len__(self) -> int:
        with self._lock:
            return len(self._containers)

    def shutdown_all(self):
        with self._lock:
            keys = list(self._containers.keys())
        for k in keys:
            self.kill_and_evict(k, "shutdown")

    def sweep_idle(self, ttl: Optional[float] = None) -> int:
        ttl = float(ttl if ttl is not None else self._idle_ttl)
        cutoff = time.monotonic() - ttl
        with self._lock:
            victims = [
                k for k, e in self._containers.items()
                if (not e.active_turn or not e.is_alive()) and e.last_used < cutoff
            ]
        for k in victims:
            self.kill_and_evict(k, f"idle>{int(ttl)}s")
        return len(victims)

    def ensure_sweeper(self, tick_seconds: int = 60,
                       idle_ttl_seconds: Optional[int] = None,
                       killer=None) -> None:
        if idle_ttl_seconds and idle_ttl_seconds > 0:
            self._idle_ttl = float(idle_ttl_seconds)
        if tick_seconds and tick_seconds > 0:
            self._tick_seconds = int(tick_seconds)
        self._start_sweeper()

    def _start_sweeper(self):
        if self._sweeper_started:
            return
        self._sweeper_started = True

        def _loop():
            while not self._sweeper_stop.wait(self._tick_seconds):
                try:
                    self.sweep_idle()
                except Exception:
                    logger.debug("[gemini-live] sweeper tick failed", exc_info=True)

        t = threading.Thread(target=_loop, daemon=True, name="gemini-live-sweeper")
        t.start()
        import atexit
        atexit.register(self.shutdown_all)


def _fmt_key(key: GeminiLiveKey) -> str:
    u, c, a, s = key
    return f"{u[:6] or '?'}/{c[:8] or '?'}/{a or 'default'}@{s or 'default'}"
