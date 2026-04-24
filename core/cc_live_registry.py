"""Live Claude Code session registry.

Tracks CC subprocesses that are kept alive across turns so the next
turn reuses the warm stream-json stdin/stdout instead of paying the
full spawn + resume cost again. A live session is pinned by
(user_id, conv_id, agent_name, service_id, svc_pool_idx) — any drift
on these dimensions means the session is unusable and must be evicted.

Lifecycle:
    register(key, session)    — stash after a clean turn
    get(key)                   — fetch for reuse (None if absent/dead)
    touch(key)                 — bump last_used + reuse_count
    evict(key, reason)         — remove from registry (does NOT kill)
    kill_and_evict(key, r)     — kill proc + release pool slot + evict
    shutdown_all()             — kill every tracked session (atexit)
    sweep_idle(ttl)            — idle sweeper tick (called by thread)

The registry does not own the spawn/dispatch loop; it is a passive
store that the caller populates on successful turn completion and
queries before spawning a fresh CC process.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Composite key: every dimension that would invalidate reuse if it drifts.
#   user_id       — credential boundary
#   conv_id       — per-conv session file
#   agent_name    — per-agent subdir + session id
#   service_id    — different LLM service = different credentials
#   svc_pool_idx  — which credential slot within the service
LiveKey = Tuple[str, str, str, str, int]


@dataclass
class CCLiveSession:
    """A Claude Code subprocess kept alive between turns.

    Fields captured at spawn time never change; counters update on reuse.
    """
    proc: object                  # subprocess.Popen
    event_q: object               # queue.Queue (reader → dispatch)
    reader_thread: object         # threading.Thread (stdout drain)
    stop_event: object            # threading.Event (shutdown signal for reader)
    pool_container: Optional[str] # container name to release on kill
    workdir: str
    service_id: str
    svc_pool_idx: int
    # MCP internal-auth token minted at spawn time. CC keeps using it
    # across turns, so its lifetime is tied to the live session, not the
    # turn. Revoked in teardown to avoid token accumulation.
    mcp_internal_token: Optional[str] = None
    # Shared heartbeat state updated by the reader daemon and consumed
    # by the per-turn stall watchdog. Persistent across turns so the
    # reader (captured once at spawn) can keep writing into it.
    hb_state: Optional[Dict[str, object]] = None
    spawn_at: float = field(default_factory=time.monotonic)
    last_used: float = field(default_factory=time.monotonic)
    reuse_count: int = 0

    def is_alive(self) -> bool:
        """True iff the underlying CC process is still running."""
        try:
            return self.proc.poll() is None
        except Exception:
            return False

    def idle_seconds(self) -> float:
        return time.monotonic() - self.last_used


class LiveSessionRegistry:
    """Thread-safe singleton tracking every live CC session."""

    _instance: Optional['LiveSessionRegistry'] = None
    _instance_lock = threading.Lock()

    @classmethod
    def instance(cls) -> 'LiveSessionRegistry':
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._sessions: Dict[LiveKey, CCLiveSession] = {}
        self._lock = threading.Lock()
        self._sweeper_started = False
        self._sweeper_stop = threading.Event()

    # ── Lookup / register ──────────────────────────────────────

    def register(self, key: LiveKey, session: CCLiveSession) -> None:
        """Stash a live session. Replaces any prior entry for the same key
        (which is a caller bug — log but don't crash)."""
        with self._lock:
            prior = self._sessions.get(key)
            if prior is not None and prior is not session:
                logger.warning(
                    "[cc-live] register %s overwrote prior entry "
                    "(prior spawn=%.1fs ago) — leaking?",
                    _fmt_key(key), time.monotonic() - prior.spawn_at)
            self._sessions[key] = session
            logger.info("[cc-live] register %s (spawn=%.1fs)",
                        _fmt_key(key),
                        time.monotonic() - session.spawn_at)

    def get(self, key: LiveKey) -> Optional[CCLiveSession]:
        """Return the live session if present AND alive, else None.

        A dead proc is auto-evicted as a side effect — callers never see
        a zombie entry.
        """
        with self._lock:
            session = self._sessions.get(key)
            if session is None:
                return None
            if not session.is_alive():
                logger.info("[cc-live] get %s — proc dead, auto-evict",
                            _fmt_key(key))
                self._sessions.pop(key, None)
                return None
            return session

    def touch(self, key: LiveKey) -> None:
        """Mark a session as reused this turn."""
        with self._lock:
            session = self._sessions.get(key)
            if session is not None:
                session.last_used = time.monotonic()
                session.reuse_count += 1

    def evict(self, key: LiveKey, reason: str) -> Optional[CCLiveSession]:
        """Remove from registry without killing. Returns the evicted entry."""
        with self._lock:
            session = self._sessions.pop(key, None)
        if session is not None:
            logger.info("[cc-live] evict %s (%s)", _fmt_key(key), reason)
        return session

    def kill_and_evict(self, key: LiveKey, reason: str,
                       killer=None) -> None:
        """Evict AND tear down the process + pool slot.

        `killer` is an optional callable(proc) — the caller (CC mixin)
        passes its own `_kill_cc_hard` so pgid semantics stay consistent.
        If None, we fall back to a plain terminate/wait.
        """
        session = self.evict(key, reason)
        if session is None:
            return
        _teardown_session(session, reason, killer)

    # ── Idle sweeper ────────────────────────────────────────

    def ensure_sweeper(self, tick_seconds: int = 60,
                       idle_ttl_seconds: int = 3600,
                       killer=None) -> None:
        """Start the idle sweeper thread (idempotent).

        The sweeper kills + evicts any session idle beyond `idle_ttl_seconds`
        and any session whose proc has died silently.
        """
        if self._sweeper_started:
            return
        self._sweeper_started = True
        self._sweeper_stop.clear()

        def _loop():
            while not self._sweeper_stop.is_set():
                try:
                    self.sweep_idle(idle_ttl_seconds, killer=killer)
                except Exception:
                    logger.warning(
                        "[cc-live] sweeper tick failed", exc_info=True)
                # Interruptible sleep so shutdown returns quickly.
                self._sweeper_stop.wait(tick_seconds)

        t = threading.Thread(
            target=_loop, name="cc-live-sweeper", daemon=True)
        t.start()
        logger.info(
            "[cc-live] sweeper started (tick=%ds, idle_ttl=%ds)",
            tick_seconds, idle_ttl_seconds)

    def sweep_idle(self, idle_ttl_seconds: int, killer=None) -> int:
        """Evict idle or dead sessions. Returns the count killed."""
        now = time.monotonic()
        to_kill = []
        with self._lock:
            for key, session in list(self._sessions.items()):
                if not session.is_alive():
                    to_kill.append((key, "proc_dead", session))
                elif (now - session.last_used) > idle_ttl_seconds:
                    to_kill.append((key, "idle_ttl", session))
            for key, _reason, _session in to_kill:
                self._sessions.pop(key, None)
        for key, reason, session in to_kill:
            logger.info("[cc-live] sweeper evict %s (%s, idle=%.0fs, reuse=%d)",
                        _fmt_key(key), reason,
                        now - session.last_used, session.reuse_count)
            _teardown_session(session, reason, killer)
        return len(to_kill)

    # ── Shutdown ─────────────────────────────────────────────

    def shutdown_all(self, killer=None) -> None:
        """Kill every tracked session. Called on server exit."""
        self._sweeper_stop.set()
        with self._lock:
            entries = list(self._sessions.items())
            self._sessions.clear()
        for key, session in entries:
            logger.info("[cc-live] shutdown kill %s (reuse=%d, lived=%.1fs)",
                        _fmt_key(key), session.reuse_count,
                        time.monotonic() - session.spawn_at)
            _teardown_session(session, "shutdown", killer)

    # ── Diagnostics ──────────────────────────────────────────

    def status(self) -> list:
        """Snapshot for /api/agents/status and UI telemetry."""
        now = time.monotonic()
        with self._lock:
            out = []
            for key, session in self._sessions.items():
                u, c, a, svc, idx = key
                out.append({
                    "user_id": u,
                    "conv_id": c,
                    "agent_name": a,
                    "service_id": svc,
                    "svc_pool_idx": idx,
                    "live": session.is_alive(),
                    "idle_seconds": int(now - session.last_used),
                    "reuse_count": session.reuse_count,
                    "spawn_at": session.spawn_at,
                    "lived_seconds": int(now - session.spawn_at),
                })
        return out

    def __len__(self) -> int:
        with self._lock:
            return len(self._sessions)


def _fmt_key(key: LiveKey) -> str:
    u, c, a, svc, idx = key
    return f"{u[:6]}/{c[:8]}/{a}@{svc}#{idx}"


def _teardown_session(session: CCLiveSession, reason: str, killer) -> None:
    """Best-effort kill proc + stop reader + release pool slot.

    Every step is isolated — one failure must not skip the next.
    """
    # 1. Signal reader thread to stop.
    try:
        session.stop_event.set()
    except Exception:
        logger.debug("stop_event.set failed", exc_info=True)
    # 2. Kill the process. Prefer the caller's killer (knows pgid).
    try:
        if killer is not None:
            killer(session.proc)
        else:
            try:
                session.proc.terminate()
            except Exception:
                logger.debug("proc.terminate failed", exc_info=True)
            try:
                session.proc.wait(timeout=2)
            except Exception:
                try:
                    session.proc.kill()
                except Exception:
                    logger.debug("proc.kill failed", exc_info=True)
    except Exception:
        logger.warning(
            "[cc-live] killer failed for reason=%s", reason, exc_info=True)
    # 3. Release pool container slot (if any).
    if session.pool_container:
        try:
            from core.claude_code_pool import ClaudeCodePool
            ClaudeCodePool.instance().release(session.pool_container)
        except Exception:
            logger.debug("pool release failed", exc_info=True)
    # 4. Revoke the MCP internal-auth token. Its lifetime was tied to
    #    the live session, so teardown is the right moment — not turn
    #    end. Skipping this leaks valid replayable tokens in
    #    core.internal_auth._tokens until server restart.
    if session.mcp_internal_token:
        try:
            from core.internal_auth import revoke_token
            revoke_token(session.mcp_internal_token)
        except Exception:
            logger.debug("internal-auth revoke failed", exc_info=True)
