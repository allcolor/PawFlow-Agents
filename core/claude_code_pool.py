"""Claude Code container pool manager.

Maintains a pool of warm Docker containers for Claude Code execution.
Instead of spawning a new container per LLM call (docker run --rm),
the pool keeps containers alive and runs claude via docker exec.

Benefits:
- No container startup latency (~3s saved per call)
- Shared base memory (Node.js runtime) across sessions
- Multiple credentials supported via per-process CLAUDE_CONFIG_DIR
- Auto-scaling: spawns containers on demand, reaps idle ones

Architecture:
  Pool Container (long-lived, entrypoint=sleep infinity)
    ├── Volume: data/claude_sessions → /cc_sessions
    ├── Exec session 1: CLAUDE_CONFIG_DIR=/cc_sessions/<user>/<conv>/<agent> claude ...
    ├── Exec session 2: CLAUDE_CONFIG_DIR=/cc_sessions/<user>/<conv>/<agent> claude ...
    └── (up to max_sessions_per_container concurrent sessions)
"""

import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

from core.docker_utils import docker_cmd, to_host_path, get_host_ip

logger = logging.getLogger(__name__)


@dataclass
class _ContainerInfo:
    """State of a pool container."""
    name: str
    active_sessions: int = 0
    max_sessions: int = 5
    created_at: float = field(default_factory=time.monotonic)
    last_used: float = field(default_factory=time.monotonic)

    @property
    def has_capacity(self) -> bool:
        return self.active_sessions < self.max_sessions


class ClaudeCodePool:
    """Singleton pool of warm Docker containers for Claude Code.

    Usage:
        pool = ClaudeCodePool.instance()
        container = pool.acquire()       # get a container with a free slot
        proc = pool.exec_claude(container, session_dir, claude_args)
        # ... stream proc.stdout ...
        pool.release(container)          # free the slot
    """

    _instance: Optional['ClaudeCodePool'] = None
    _instance_lock = threading.Lock()

    @classmethod
    def instance(cls) -> 'ClaudeCodePool':
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
                    cls._instance._register_death_handlers()
        return cls._instance

    def _register_death_handlers(self):
        """Kill every spawned container when the Python process dies.

        Pool containers are `docker run -d --rm sleep infinity`. Without
        --rm they leak on crash; with --rm they auto-remove on EXIT, but
        they NEVER exit on their own because of `sleep infinity`. The
        server/CLI shutting down doesn't signal them.

        Register atexit + SIGINT/SIGTERM handlers that issue
        `docker rm -f` on every tracked container. Combined with the
        boot-time _cleanup_orphans reaper, no container can survive the
        parent process.
        """
        import atexit, signal, sys
        def _kill_all(*_args, **_kwargs):
            try:
                self.shutdown()
            except Exception:
                pass
        atexit.register(_kill_all)
        # Only install signal handlers in the main thread — subprocesses /
        # worker threads can't register them and will raise ValueError.
        try:
            import threading as _th
            if _th.current_thread() is _th.main_thread():
                for _sig in (signal.SIGINT, signal.SIGTERM):
                    try:
                        _prev = signal.getsignal(_sig)
                        def _chain(signum, frame, _p=_prev):
                            _kill_all()
                            # Chain to previous handler if it was a callable
                            if callable(_p) and _p not in (signal.SIG_DFL, signal.SIG_IGN):
                                _p(signum, frame)
                            else:
                                # Default: re-raise as exit
                                sys.exit(128 + signum)
                        signal.signal(_sig, _chain)
                    except (ValueError, OSError):
                        pass
        except Exception:
            pass

    def __init__(self):
        self._containers: Dict[str, _ContainerInfo] = {}
        self._lock = threading.Lock()
        self._reaper_started = False

        # Config (can be overridden via env vars)
        self.image = os.environ.get(
            "PAWFLOW_CC_IMAGE", "pawflow-claude-code:latest")
        self.max_sessions_per_container = int(os.environ.get(
            "PAWFLOW_CC_POOL_SESSIONS", "5"))
        self.max_containers = int(os.environ.get(
            "PAWFLOW_CC_POOL_MAX", "10"))
        self.idle_timeout = int(os.environ.get(
            "PAWFLOW_CC_POOL_IDLE", "300"))  # seconds
        self.cpu_limit = os.environ.get("PAWFLOW_CC_CPU", "2")
        self.memory_limit = os.environ.get("PAWFLOW_CC_MEM", "4g")

        # Kill orphan pool containers from previous PawFlow runs
        self._cleanup_orphans()

        # Sessions volume: host path for CC sessions (must be absolute for Docker -v)
        import core.paths as _paths
        _raw_path = str(_paths.CLAUDE_SESSIONS_DIR.resolve())
        # translate_path converts Windows paths (C:\...) to WSL mount paths (/mnt/c/...)
        from pawflow_relay.utils import translate_path
        self._sessions_host_path = translate_path(to_host_path(_raw_path))

    # ── Public API ─────────────────────────────────────────────────

    def acquire(self) -> str:
        """Acquire a container with a free slot. Returns container name.

        Spawns a new container if all existing ones are full.
        Raises RuntimeError if pool is exhausted (max_containers reached).
        """
        with self._lock:
            self._ensure_reaper()

            # Find container with available capacity
            for info in self._containers.values():
                if info.has_capacity:
                    info.active_sessions += 1
                    info.last_used = time.monotonic()
                    logger.info("Pool acquire: %s (sessions=%d/%d)",
                                info.name, info.active_sessions,
                                info.max_sessions)
                    return info.name

            # No capacity — spawn new container
            if len(self._containers) >= self.max_containers:
                raise RuntimeError(
                    f"Claude Code pool exhausted: {len(self._containers)}"
                    f"/{self.max_containers} containers, all full")

            name = self._spawn_container()
            self._containers[name].active_sessions = 1
            logger.info("Pool acquire (new container): %s", name)
            return name

    def release(self, container_name: str):
        """Release a session slot in a container."""
        with self._lock:
            info = self._containers.get(container_name)
            if info:
                info.active_sessions = max(0, info.active_sessions - 1)
                info.last_used = time.monotonic()
                logger.info("Pool release: %s (sessions=%d/%d)",
                            info.name, info.active_sessions,
                            info.max_sessions)

    def exec_claude(self, container_name: str, session_dir: str,
                    claude_args: list, extra_env: dict = None,
                    **popen_kwargs) -> subprocess.Popen:
        """Start a claude process inside a pool container.

        Args:
            container_name: container from acquire()
            session_dir: path INSIDE the container for CLAUDE_CONFIG_DIR
                         (e.g. /cc_sessions/<user>/<conv>/<agent>)
            claude_args: args for the claude CLI (after 'claude')
            extra_env: additional env vars (e.g. ANTHROPIC_API_KEY)
            **popen_kwargs: extra args for subprocess.Popen

        Returns:
            subprocess.Popen with stdin/stdout/stderr
        """
        host_ip = get_host_ip()

        # Per-exec mount namespace via `unshare -m`: each docker exec gets
        # its own private view of the filesystem, so the bind of
        # session_dir → /workspace is visible ONLY to this CC subprocess.
        # Concurrent agents in the same pool container each see their own
        # /workspace pointing at their own session_dir — no collision on
        # session.jsonl, .mcp.json, or .credentials.json.
        #
        # The wrapper runs as root (needed for mount + unshare -m with
        # CAP_SYS_ADMIN) then drops privileges to uid 1000 via setpriv
        # before exec'ing claude — Claude Code refuses to run as root.
        exec_args = [
            "-i",
            "--user", "root",
            "-e", "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "-e", "HOME=/workspace",
            "-e", "USER=pawflow",
            "-e", "CLAUDE_CONFIG_DIR=/workspace",
            "-e", "NODE_OPTIONS=--max-old-space-size=768",
            "-e", f"PAWFLOW_HOST={host_ip}",
            "-e", "GIT_CONFIG_COUNT=1",
            "-e", "GIT_CONFIG_KEY_0=safe.directory",
            "-e", "GIT_CONFIG_VALUE_0=/workspace",
        ]
        # Pass extra env vars (e.g. ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL)
        for k, v in (extra_env or {}).items():
            exec_args.extend(["-e", f"{k}={v}"])
        # Build the in-namespace command:
        #   mkdir -p /workspace
        #   mount --bind <session_dir> /workspace      (private to this ns)
        #   cd /workspace
        #   exec setpriv --reuid=1000 --regid=1000 \
        #        --clear-groups -- claude <args>
        import shlex
        _claude_quoted = " ".join(shlex.quote(str(a)) for a in claude_args)
        _shell_script = (
            f"mkdir -p /workspace && "
            f"mount --bind {shlex.quote(session_dir)} /workspace && "
            f"cd /workspace && "
            f"exec setpriv --reuid=1000 --regid=1000 --clear-groups "
            f"-- claude {_claude_quoted}"
        )
        exec_args.extend([
            container_name,
            "unshare", "-m", "--",
            "bash", "-c", _shell_script,
        ])

        cmd = docker_cmd() + ["exec"] + exec_args
        logger.info("Pool exec: %s → %s", container_name,
                    " ".join(cmd[:10]) + "...")

        return subprocess.Popen(cmd, **popen_kwargs)

    def status(self) -> dict:
        """Return pool status for diagnostics."""
        with self._lock:
            containers = []
            for info in self._containers.values():
                containers.append({
                    "name": info.name,
                    "sessions": info.active_sessions,
                    "max_sessions": info.max_sessions,
                    "idle_seconds": int(time.monotonic() - info.last_used),
                })
            return {
                "containers": containers,
                "total": len(self._containers),
                "max": self.max_containers,
                "image": self.image,
            }

    def shutdown(self):
        """Kill all pool containers (called on server shutdown)."""
        with self._lock:
            for name in list(self._containers.keys()):
                self._kill_container(name)
            self._containers.clear()
            logger.info("Pool shutdown: all containers killed")

    # ── Internal ───────────────────────────────────────────────────

    def _spawn_container(self) -> str:
        """Spawn a new warm container. Must be called under self._lock."""
        import uuid
        name = f"pf-cc-pool-{uuid.uuid4().hex[:8]}"

        # Ensure sessions dir exists on host
        os.makedirs(
            os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "data", "claude_sessions",
            ),
            exist_ok=True,
        )

        run_args = [
            "-d",  # detached
            "--rm",  # auto-remove on exit — prevents dead-container pileup
            "--name", name,
            "--cpus", self.cpu_limit,
            "--memory", self.memory_limit,
            # Mount sessions volume (all sessions, shared across all execs)
            "-v", f"{self._sessions_host_path}:/cc_sessions",
            # Network: allow MCP bridge to reach host tool relay
            "--add-host", "host.docker.internal:host-gateway",
            # Run as non-root (Claude Code requirement)
            "--user", "1000:1000",
            # Force clean HOME/USER — PATH is set in docker exec, not here
            # (setting PATH here breaks the entrypoint command resolution)
            "-e", "HOME=/home/pawflow",
            "-e", "USER=pawflow",
            # Security
            "--shm-size", "512m",
            "--tmpfs", "/tmp:rw,nosuid,size=512m",
            "--cap-add", "SYS_ADMIN",  # needed for mount --bind in exec_claude
            # Override entrypoint: keep alive (full path — PATH may be dirty)
            "--entrypoint", "/usr/bin/sleep",
            self.image,
            "infinity",
        ]

        cmd = docker_cmd() + ["run"] + run_args
        logger.info("Pool spawn: %s (image=%s)", name, self.image)

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to spawn pool container {name}: "
                f"{result.stderr.strip()[:300]}")

        # Start chronyd for time sync (avoid drift in long-running containers)
        subprocess.run(
            docker_cmd() + ["exec", "--user", "root", name, "chronyd"],
            capture_output=True, timeout=5)

        info = _ContainerInfo(
            name=name,
            max_sessions=self.max_sessions_per_container,
        )
        self._containers[name] = info
        logger.info("Pool container spawned: %s", name)
        return name

    def _kill_container(self, name: str):
        """Kill and remove a container. Must be called under self._lock."""
        try:
            subprocess.run(
                docker_cmd() + ["rm", "-f", name],
                capture_output=True, timeout=10)
            logger.info("Pool container killed: %s", name)
        except Exception as e:
            logger.warning("Failed to kill pool container %s: %s", name, e)

    def _is_container_alive(self, name: str) -> bool:
        """Check if a container is still running."""
        try:
            result = subprocess.run(
                docker_cmd() + ["inspect", "-f", "{{.State.Running}}", name],
                capture_output=True, text=True, timeout=5)
            return result.stdout.strip() == "true"
        except Exception:
            return False

    # ── Idle reaper ────────────────────────────────────────────────

    def _ensure_reaper(self):
        """Start the idle reaper thread if not already running."""
        if self._reaper_started:
            return
        self._reaper_started = True
        t = threading.Thread(target=self._reaper_loop, daemon=True)
        t.start()

    def _reaper_loop(self):
        """Periodically kill idle containers."""
        while True:
            time.sleep(60)  # check every minute
            try:
                self._reap_idle()
            except Exception as e:
                logger.warning("Pool reaper error: %s", e)

    def _reap_idle(self):
        """Kill containers that have been idle for > idle_timeout."""
        now = time.monotonic()
        to_kill = []

        with self._lock:
            for name, info in list(self._containers.items()):
                idle = now - info.last_used
                if info.active_sessions == 0 and idle > self.idle_timeout:
                    to_kill.append(name)
                # Also remove dead containers
                elif not self._is_container_alive(name):
                    if info.active_sessions == 0:
                        to_kill.append(name)
                    else:
                        logger.warning(
                            "Pool container %s is dead but has %d active "
                            "sessions — removing from pool",
                            name, info.active_sessions)
                        to_kill.append(name)

            for name in to_kill:
                self._kill_container(name)
                del self._containers[name]

        if to_kill:
            logger.info("Pool reaper: killed %d idle container(s): %s",
                        len(to_kill), to_kill)

    def _cleanup_orphans(self):
        """Kill + remove orphan pf-cc-pool containers from previous runs.

        Without -a the previous version only saw RUNNING containers,
        so Exited pool containers piled up across restarts (seen with
        hundreds after a few days). ps -a includes every state
        (running, exited, created, dead) and rm -f stops+removes.
        """
        try:
            result = subprocess.run(
                docker_cmd() + ["ps", "-a",
                                "--filter", "name=pf-cc-pool-",
                                "--format", "{{.Names}}"],
                capture_output=True, text=True, timeout=10)
            if result.returncode == 0 and result.stdout.strip():
                orphans = [n.strip() for n in result.stdout.strip().split("\n") if n.strip()]
                for name in orphans:
                    try:
                        subprocess.run(
                            docker_cmd() + ["rm", "-f", name],
                            capture_output=True, timeout=10)
                        logger.info("Pool: removed orphan container %s", name)
                    except Exception:
                        pass
                if orphans:
                    logger.info("Pool: cleaned up %d orphan container(s)", len(orphans))
        except Exception as e:
            logger.warning("Pool: orphan cleanup failed: %s", e)
