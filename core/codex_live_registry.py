"""Codex live container registry.

Keeps a Codex app-server process and its Docker container alive across turns
of the same (user_id, conv_id, agent_name), so PawFlow keeps Codex's thread
state and avoids paying the spawn cost on every turn.

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
#   user_id      — credential boundary + per-conv mount
#   conv_id      — ConversationStore session_id resume
#   agent_name   — per-agent codex_session ConversationStore key
#   service_id   — different service = different credentials
#   svc_pool_idx — which credential slot within the service
CodexLiveKey = Tuple[str, str, str, str, int]


@dataclass
class CodexLiveSession:
    """A Codex app-server session kept alive across turns of the same
    (user, conv, agent, service, pool_idx) tuple.

    What we keep alive is the app-server process, its backing container, the
    Codex thread_id, and the internal-auth token used by the MCP bridge.
    """
    container_name: str
    workdir: str
    service_id: str
    svc_pool_idx: int = -1
    # Credential-pool coordinates captured at register time so teardown
    # (idle sweep / shutdown / evict) can copy any CLI-rotated OAuth token
    # from the workdir back to the right pool slot. Defense-in-depth for
    # codex (OpenAI does not invalidate the old refresh_token); the same
    # hole logs CC users out (Anthropic rotates single-use). See cc.
    user_id: str = ""
    conv_id: str = ""
    # Per-turn process state — reset on each spawn. Kept on the session
    # so the dispatch loop, watchdog, and reader daemon can share a single
    # source of truth.
    proc: object = None              # subprocess.Popen of `codex app-server`
    event_q: object = None           # queue.Queue (reader → dispatch)
    reader_thread: object = None     # threading.Thread (stdout drain)
    stop_event: object = None        # threading.Event (shutdown signal)
    pool_container: Optional[str] = None  # alias of container_name
    # Persistent across turns:
    session_id: str = ""             # codex thread_id (== rollout file id)
    mcp_internal_token: Optional[str] = None
    hb_state: Optional[Dict[str, object]] = None
    spawn_at: float = field(default_factory=time.monotonic)
    last_used: float = field(default_factory=time.monotonic)
    reuse_count: int = 0
    # Serializes concurrent app-server turns on the same Codex thread.
    turn_lock: object = field(default_factory=threading.RLock)
    active_turn: bool = False

    def is_process_alive(self) -> bool:
        """Return True when the long-lived app-server process can be reused."""
        try:
            return self.proc is not None and self.proc.poll() is None
        except Exception:
            return False

    def is_container_alive(self) -> bool:
        """Return True when the backing Docker container is still running."""
        if self.container_name:
            try:
                from core.codex_pool import CodexPool
                return CodexPool.instance()._is_container_alive(self.container_name)
            except Exception:
                logger.debug("[codex-live] container liveness check failed", exc_info=True)
        return False

    def is_alive(self) -> bool:
        return self.is_process_alive()

    def idle_seconds(self) -> float:
        return time.monotonic() - self.last_used


# Back-compat alias so older import sites (`CodexLiveContainer`) keep working
# until they're migrated to the richer name.
CodexLiveContainer = CodexLiveSession


class CodexLiveRegistry:
    """Thread-safe singleton for codex live containers."""

    _instance: Optional['CodexLiveRegistry'] = None
    _instance_lock = threading.Lock()

    DEFAULT_IDLE_TTL = 1800.0  # 30 min idle by default; service timeout may override

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
        self._idle_ttl = float(self.DEFAULT_IDLE_TTL)
        self._tick_seconds = 60
        # callable(workdir, service_id, pool_index, user_id, conv_id) set via
        # ensure_sweeper; invoked by kill_and_evict to rescue CLI-rotated
        # OAuth tokens before a live container is destroyed.
        self._recover = None

    def get(self, key: CodexLiveKey) -> Optional[CodexLiveContainer]:
        with self._lock:
            return self._containers.get(key)

    def get_compatible(self, user_id: str, conv_id: str, agent_name: str,
                       service_id: str) -> Optional[Tuple[CodexLiveKey, CodexLiveContainer]]:
        """Return the most recent live session for the base identity.

        The exact key includes svc_pool_idx. If that extra is missing after a
        restart or compact, an exact lookup with -1 misses even though the
        warm app-server is still usable. Reuse the registered entry and carry
        its pool index forward instead of spawning a replacement container.
        """
        agent_name = agent_name or "default"
        service_id = service_id or ""
        with self._lock:
            candidates = [
                (k, e) for k, e in self._containers.items()
                if k[0] == user_id
                and k[1] == conv_id
                and k[2] == agent_name
                and (k[3] or "") == service_id
            ]
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[1].last_used, reverse=True)
        return candidates[0]

    def register(self, key: CodexLiveKey,  # nosec B107
                 container_name: str, workdir: str,
                 service_id: str = "",
                 session_id: str = "",
                 proc=None,
                 event_q=None,
                 reader_thread=None,
                 stop_event=None,
                 mcp_internal_token: str = "",
                 hb_state=None,
                 active_turn: bool = False) -> CodexLiveContainer:
        previous_container = ""
        # Pull svc_pool_idx out of the 5-tuple key so the container struct
        # can surface it via /codex_live status without re-deriving from
        # the dict key.
        try:
            _svc_pool_idx = int(key[4]) if len(key) >= 5 else -1
        except (TypeError, ValueError, IndexError):
            _svc_pool_idx = -1
        with self._lock:
            existing = self._containers.get(key)
            if existing and existing.container_name == container_name:
                existing.last_used = time.monotonic()
                # Refresh session_id + live-state fields on re-register so
                # the next REUSE has the latest state (codex re-issues a
                # session_id on `thread.started` if the previous one was
                # invalidated).
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
            if existing is not None:
                previous_container = existing.container_name
                logger.warning(
                    "[codex-live] register %s replacing container=%s with %s",
                    _fmt_key(key), previous_container, container_name)
            entry = CodexLiveContainer(
                container_name=container_name, workdir=workdir,
                service_id=service_id, svc_pool_idx=_svc_pool_idx,
                user_id=(key[0] if len(key) > 0 else ""),
                conv_id=(key[1] if len(key) > 1 else ""),
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
            logger.info("[codex-live] register %s container=%s session_id=%s",
                        _fmt_key(key), container_name,
                        (session_id[:12] + "...") if session_id else "EMPTY")
        if previous_container:
            try:
                from core.codex_pool import CodexPool
                CodexPool.instance().release(previous_container)
            except Exception as e:
                logger.warning(
                    "[codex-live] release replaced container failed: %s", e)
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
        # Rescue any OAuth token the CLI rotated into the workdir before the
        # container dies (defense-in-depth; see CodexLiveSession docstring).
        if self._recover is not None and getattr(entry, "workdir", ""):
            try:
                self._recover(entry.workdir, entry.service_id,
                              entry.svc_pool_idx, entry.user_id, entry.conv_id)
            except Exception:
                logger.debug("[codex-live] token recover failed (%s)",
                             reason, exc_info=True)
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
        """Snapshot for /codex_live UI telemetry. Mirror of CC's status()."""
        now = time.monotonic()
        with self._lock:
            out = []
            for key, entry in self._containers.items():
                u, c, a, svc, idx = key
                out.append({
                    "user_id": u,
                    "conv_id": c,
                    "agent_name": a,
                    "service_id": svc,
                    "svc_pool_idx": idx,
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
        """Evict inactive containers idle longer than ttl. Returns count."""
        ttl = float(ttl if ttl is not None else self._idle_ttl)
        cutoff = time.monotonic() - ttl
        with self._lock:
            victims = [
                k for k, e in self._containers.items()
                if not e.active_turn
                and (e.last_used < cutoff or not e.is_container_alive())
            ]
        for k in victims:
            self.kill_and_evict(k, f"idle>{int(ttl)}s")
        return len(victims)

    def ensure_sweeper(self, tick_seconds: int = 60,
                       idle_ttl_seconds: Optional[int] = None,
                       killer=None, recover=None) -> None:
        """Keep the sweeper running and update its configured idle TTL."""
        if recover is not None:
            self._recover = recover
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
                    logger.debug("[codex-live] sweeper tick failed", exc_info=True)

        t = threading.Thread(target=_loop, daemon=True, name="codex-live-sweeper")
        t.start()
        # atexit hook to release everything on Python shutdown
        import atexit
        atexit.register(self.shutdown_all)


def _fmt_key(key: CodexLiveKey) -> str:
    u, c, a, s, idx = key
    return f"{u[:6] or '?'}/{c[:8] or '?'}/{a or 'default'}@{s or 'default'}"
