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
#   user_id      — credential boundary + per-conv mount
#   conv_id      — ConversationStore session_id resume
#   agent_name   — per-agent codex_session ConversationStore key
#   service_id   — different service = different credentials
#   svc_pool_idx — which credential slot within the service
CodexLiveKey = Tuple[str, str, str, str, int]


@dataclass
class CodexLiveSession:
    """A codex CLI session kept alive across turns of the same
    (user, conv, agent, service, pool_idx) tuple.

    Codex `exec --json` is one-shot — the process exits at end-of-turn.
    What we keep alive is the CONTAINER (warm Docker namespace) plus the
    session_id (so `codex exec resume <sid>` reattaches to the same
    rollout file) plus the internal-auth token (so the MCP bridge keeps
    using the same scoped credential across turns). proc / event_q /
    reader_thread / stop_event are bound to a turn and reset on each
    spawn — they live on the session for symmetry with CC's
    CCLiveSession (where proc is long-lived) but are recycled per call.
    """
    container_name: str
    workdir: str
    service_id: str
    svc_pool_idx: int = -1
    # Per-turn process state — reset on each spawn. Kept on the session
    # so the dispatch loop, watchdog, and reader daemon can share a single
    # source of truth.
    proc: object = None              # subprocess.Popen of `codex exec`
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
    # Serializes concurrent _stream_codex calls that would otherwise
    # spawn parallel codex execs writing to the same session_id.
    turn_lock: object = field(default_factory=threading.RLock)

    def is_alive(self) -> bool:
        try:
            return self.proc is None or self.proc.poll() is None
        except Exception:
            return False

    def idle_seconds(self) -> float:
        return time.monotonic() - self.last_used


# Back-compat alias so older import sites (`CodexLiveContainer`) keep working
# until they're migrated to the richer name.
CodexLiveContainer = CodexLiveSession


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
                 service_id: str = "",
                 session_id: str = "",
                 proc=None,
                 event_q=None,
                 reader_thread=None,
                 stop_event=None,
                 mcp_internal_token: str = "",
                 hb_state=None) -> CodexLiveContainer:
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
                return existing
            entry = CodexLiveContainer(
                container_name=container_name, workdir=workdir,
                service_id=service_id, svc_pool_idx=_svc_pool_idx,
                session_id=session_id,
                proc=proc,
                event_q=event_q,
                reader_thread=reader_thread,
                stop_event=stop_event,
                mcp_internal_token=mcp_internal_token,
                hb_state=hb_state,
            )
            self._containers[key] = entry
            logger.info("[codex-live] register %s container=%s session_id=%s",
                        _fmt_key(key), container_name,
                        (session_id[:12] + "…") if session_id else "EMPTY")
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

    def sweep_idle(self, ttl: float = DEFAULT_IDLE_TTL) -> int:
        """Evict every container idle longer than ttl. Returns count."""
        cutoff = time.monotonic() - ttl
        with self._lock:
            victims = [k for k, e in self._containers.items() if e.last_used < cutoff]
        for k in victims:
            self.kill_and_evict(k, f"idle>{int(ttl)}s")
        return len(victims)

    def ensure_sweeper(self, killer=None) -> None:
        """Mirror of CC's `LiveSessionRegistry.ensure_sweeper(killer=)`.

        Codex's sweeper is auto-started by `instance()` (it's a singleton
        that hard-couples the sweeper lifecycle to the registry's first
        access), so this is mostly a no-op kept for API parity with the
        cloned `_stream_codex` body. The `killer` callback is ignored:
        codex's sweeper releases via the pool, which already does the
        equivalent kill (docker rm -f under the 1:1 model).
        """
        # No-op: _start_sweeper has already run via instance().
        return

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
    u, c, a, s, idx = key
    return f"{u[:6] or '?'}/{c[:8] or '?'}/{a or 'default'}@{s or 'default'}"
