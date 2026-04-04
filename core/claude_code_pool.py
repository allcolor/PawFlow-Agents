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
        return cls._instance

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

        # Sessions volume: host path for data/claude_sessions
        _raw_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "claude_sessions",
        )
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
                    claude_args: list, **popen_kwargs) -> subprocess.Popen:
        """Start a claude process inside a pool container.

        Args:
            container_name: container from acquire()
            session_dir: path INSIDE the container for CLAUDE_CONFIG_DIR
                         (e.g. /cc_sessions/<user>/<conv>/<agent>)
            claude_args: args for the claude CLI (after 'claude')
            **popen_kwargs: extra args for subprocess.Popen

        Returns:
            subprocess.Popen with stdin/stdout/stderr
        """
        host_ip = get_host_ip()

        # Create symlink /workspace → session_dir so CC sees the same
        # environment as the old per-container model (cwd=/workspace,
        # HOME=/workspace, CLAUDE_CONFIG_DIR=/workspace).
        _setup = subprocess.run(
            docker_cmd() + ["exec", "--user", "root", container_name, "bash", "-c",
                            f"rm -rf /workspace && ln -sfn {session_dir} /workspace && chown -h 1000:1000 /workspace"],
            capture_output=True, timeout=5)
        if _setup.returncode != 0:
            logger.warning("Pool: symlink setup failed: %s", _setup.stderr)

        exec_args = [
            "-i",
            "-e", "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "-e", "HOME=/workspace",
            "-e", "USER=pawflow",
            "-e", "CLAUDE_CONFIG_DIR=/workspace",
            "-e", "NODE_OPTIONS=--max-old-space-size=768",
            "-e", f"PAWFLOW_HOST={host_ip}",
            "-e", "GIT_CONFIG_COUNT=1",
            "-e", "GIT_CONFIG_KEY_0=safe.directory",
            "-e", "GIT_CONFIG_VALUE_0=/workspace",
            "-w", "/workspace",
            container_name,
            "claude",
        ] + claude_args

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
            "--name", name,
            "--cpus", self.cpu_limit,
            "--memory", self.memory_limit,
            # Mount sessions volume (all sessions, shared across all execs)
            "-v", f"{self._sessions_host_path}:/cc_sessions",
            # Network: allow MCP bridge to reach host tool relay
            "--add-host", "host.docker.internal:host-gateway",
            # Run as non-root (Claude Code requirement)
            "--user", "1000:1000",
            # Force clean env — Docker Desktop WSL2 injects host PATH/HOME
            "-e", "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "-e", "HOME=/home/pawflow",
            "-e", "USER=pawflow",
            # Security
            "--shm-size", "512m",
            "--tmpfs", "/tmp:rw,nosuid,size=512m",
            "--security-opt", "no-new-privileges",
            # Override entrypoint: keep alive (not claude)
            "--entrypoint", "sleep",
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
