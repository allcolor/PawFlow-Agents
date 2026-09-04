"""Container lifecycle for the ``antigravity-acp`` provider.

Google's official Antigravity ACP server (``agy_acp_server``) is baked into
the ``pawflow-claude-code`` image. One container per (user, conversation,
agent, service) sleeps until the provider execs the server inside it over
stdio (``docker exec -i``). The container mounts the provider session root at
``/cc_sessions_host`` so ``GEMINI_HOME`` (credentials, sessions, settings) and
the per-conversation working directory live on the host runtime tree and
survive container replacement.

Secrets never travel in argv: environment entries are forwarded by name
(``docker exec -e NAME``) and take their values from the docker CLI process
environment, which ``AcpProcessSession`` sets from the validated config.
"""

from __future__ import annotations

import logging
import os
import subprocess  # nosec B404 - Docker process control is this module's job.
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import core.paths as _paths
from core.antigravity_observer_pool import AntigravityObserverPool
from core.apparmor import apparmor_security_opts
from core.docker_utils import (
    docker_cmd,
    get_server_id,
    pawflow_container_labels,
    to_host_path,
    translate_path,
)

logger = logging.getLogger(__name__)

CONTAINER_KIND = "antigravity-acp"
CONTAINER_SESSIONS_ROOT = "/cc_sessions_host"
DEFAULT_SERVER_BINARY = "/opt/pawflow/antigravity-acp/agy_acp_server.par"
#: ``--uid`` is an absl flag of the server: "If root, switch to this user id
#: (or empty-string not to switch)", default ``nobody``. The registry entry
#: passes it empty; the server did not answer ``initialize`` without it.
SERVER_ARGS = ("--uid=",)
CONTAINER_PYTHON = "/usr/bin/python3"
CONTAINER_MCP_BRIDGE = "/opt/pawflow/mcp_bridge.py"


@dataclass
class AntigravityAcpContainer:
    """One sleeping container and the host directories it serves."""

    key: tuple[str, str, str, str]
    name: str
    workdir: str
    home: str
    created_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)


class AntigravityAcpPool:
    """Sleeping ``pawflow-claude-code`` containers keyed by provider identity."""

    _instance: Optional["AntigravityAcpPool"] = None
    _instance_lock = threading.Lock()

    @classmethod
    def instance(cls) -> "AntigravityAcpPool":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._containers: dict[tuple[str, str, str, str], AntigravityAcpContainer] = {}
        # Same uid/gid contract as the other provider pools: the in-container
        # process runs as the host launcher so the mounted session tree stays
        # writable without chown games.
        self.run_uid = self._numeric_env("PAWFLOW_RUN_UID", "1000")
        self.run_gid = self._numeric_env("PAWFLOW_RUN_GID", "1000")

    @staticmethod
    def _numeric_env(name: str, default: str) -> str:
        value = os.environ.get(name, default).strip()
        return value if value.isdigit() else default

    def _user_spec(self) -> str:
        return f"{self.run_uid}:{self.run_gid}"

    @staticmethod
    def _safe(value: str) -> str:
        return (value or "").replace(":", "_").replace("/", "_").replace("\\", "_")

    @staticmethod
    def base_dir() -> Path:
        return _paths.RUNTIME_DIR / "sessions" / CONTAINER_KIND

    @classmethod
    def home_dir(cls, user_id: str, service_id: str) -> Path:
        """Host directory holding ``GEMINI_HOME`` for one (user, service).

        Shared across conversations on purpose: a login is done once per
        service, and the server keeps its own session store under it.
        """
        if not user_id:
            raise ValueError("user_id is required for antigravity-acp")
        if not service_id:
            raise ValueError("service_id is required for antigravity-acp")
        path = cls.base_dir() / cls._safe(user_id) / "homes" / cls._safe(service_id)
        (path / ".gemini").mkdir(parents=True, exist_ok=True)
        return path

    @classmethod
    def workdir(cls, user_id: str, conversation_id: str, agent_name: str) -> Path:
        if not user_id:
            raise ValueError("user_id is required for antigravity-acp")
        if not conversation_id:
            raise ValueError("conversation_id is required for antigravity-acp")
        if not agent_name:
            raise ValueError("agent_name is required for antigravity-acp")
        path = (cls.base_dir() / cls._safe(user_id) / cls._safe(conversation_id)
                / cls._safe(agent_name))
        (path / "logs").mkdir(parents=True, exist_ok=True)
        return path

    @classmethod
    def container_path(cls, host_path: str | Path) -> str:
        """Map a path under :meth:`base_dir` to its in-container location."""
        rel = Path(host_path).resolve().relative_to(cls.base_dir().resolve())
        return f"{CONTAINER_SESSIONS_ROOT}/{rel.as_posix()}"

    @staticmethod
    def server_binary() -> str:
        return os.environ.get("PAWFLOW_ANTIGRAVITY_ACP_BIN") or DEFAULT_SERVER_BINARY

    @staticmethod
    def image() -> str:
        return (
            os.environ.get("PAWFLOW_ANTIGRAVITY_ACP_IMAGE")
            or os.environ.get("PAWFLOW_ANTIGRAVITY_IMAGE")
            or os.environ.get("PAWFLOW_GEMINI_IMAGE")
            or "pawflow-claude-code:latest"
        )

    # -- lifecycle -----------------------------------------------------------

    def ensure(self, *, user_id: str, conversation_id: str, agent_name: str,
               service_id: str) -> AntigravityAcpContainer:
        """Return the live container for this identity, spawning one if needed."""
        key = (user_id, conversation_id, agent_name, service_id)
        workdir = self.workdir(user_id, conversation_id, agent_name)
        home = self.home_dir(user_id, service_id)
        stale = None
        with self._lock:
            existing = self._containers.get(key)
            if existing is not None and self._is_alive(existing.name):
                existing.last_used = time.time()
                return existing
            if existing is not None:
                self._containers.pop(key, None)
                stale = existing
        if stale is not None:
            logger.info("[antigravity-acp] replacing dead container %s", stale.name)
            self.kill(stale)
        name = self._spawn()
        state = AntigravityAcpContainer(
            key=key, name=name, workdir=str(workdir), home=str(home))
        with self._lock:
            self._containers[key] = state
        return state

    def find(self, *, user_id: str, conversation_id: str, agent_name: str,
             service_id: str) -> Optional[AntigravityAcpContainer]:
        key = (user_id, conversation_id, agent_name, service_id)
        with self._lock:
            state = self._containers.get(key)
        if state is not None and self._is_alive(state.name):
            return state
        return None

    def exec_argv(self, container: AntigravityAcpContainer, *,
                  env_names: list[str] | tuple[str, ...] = ()) -> list[str]:
        """argv that runs the ACP server inside ``container`` over stdio.

        ``env_names`` are forwarded by name only; their values come from the
        docker CLI process environment.
        """
        home = self.container_path(container.home)
        cwd = self.container_path(container.workdir)
        argv = docker_cmd() + [
            "exec", "-i",
            "--user", self._user_spec(),
            "-w", cwd,
            "-e", f"HOME={home}",
            "-e", f"GEMINI_HOME={home}/.gemini",
            "-e", "USER=pawflow",
        ]
        for name in env_names:
            if "=" in name or not name:
                raise ValueError(f"invalid environment name: {name!r}")
            argv += ["-e", name]
        argv += [container.name, self.server_binary(), *SERVER_ARGS]
        return argv

    def session_cwd(self, container: AntigravityAcpContainer) -> str:
        return self.container_path(container.workdir)

    def stderr_path(self, container: AntigravityAcpContainer) -> str:
        return str(Path(container.workdir) / "logs" / "acp-server.stderr.log")

    def kill_session(self, *, user_id: str, conversation_id: str,
                     agent_name: str, service_id: str) -> bool:
        key = (user_id, conversation_id, agent_name, service_id)
        with self._lock:
            state = self._containers.pop(key, None)
        if state is None:
            return False
        self.kill(state)
        return True

    def kill_all(self) -> int:
        with self._lock:
            states = list(self._containers.values())
            self._containers.clear()
        for state in states:
            self.kill(state)
        return len(states)

    def kill(self, state: AntigravityAcpContainer) -> None:
        subprocess.run(  # nosec B603
            docker_cmd() + ["kill", "--signal=KILL", state.name],
            capture_output=True, timeout=10)
        subprocess.run(  # nosec B603
            docker_cmd() + ["rm", "-f", state.name],
            capture_output=True, timeout=15)
        with self._lock:
            if self._containers.get(state.key) is state:
                self._containers.pop(state.key, None)

    # -- docker ----------------------------------------------------------------

    @staticmethod
    def _is_alive(name: str) -> bool:
        try:
            result = subprocess.run(  # nosec B603
                docker_cmd() + ["inspect", "-f", "{{.State.Running}}", name],
                capture_output=True, text=True, timeout=5)
            return result.stdout.strip() == "true"
        except (OSError, subprocess.SubprocessError):
            return False

    def _spawn(self) -> str:
        base = self.base_dir()
        base.mkdir(parents=True, exist_ok=True)
        sessions_host = translate_path(to_host_path(str(base.resolve())))
        project_root = Path(__file__).resolve().parents[1]
        runtime_files = [
            (project_root / "tools" / "mcp_bridge.py", CONTAINER_MCP_BRIDGE),
            (project_root / "core" / "tool_json.py", "/opt/pawflow/tool_json.py"),
            (project_root / "docker" / "pawflow_sdk" / "pawflow.py", "/opt/pawflow/pawflow.py"),
        ]
        owner = get_server_id()
        name = f"pf-{owner[:12]}-agyacp-{uuid.uuid4().hex[:8]}"
        image = self.image()
        run_args = [
            "-d", "--rm", "--name", name, "--init",
            *pawflow_container_labels(CONTAINER_KIND),
            "-v", f"{sessions_host}:{CONTAINER_SESSIONS_ROOT}",
            "--add-host", "host.docker.internal:host-gateway",
            *apparmor_security_opts(image),
            "--shm-size", "512m",
            "--tmpfs", "/tmp:rw,nosuid,size=512m",  # nosec B108 - Docker tmpfs mount target inside the container.
            "--user", "root",
            "--entrypoint", "/usr/bin/sleep",
            image,
            "infinity",
        ]
        result = subprocess.run(  # nosec B603
            docker_cmd() + ["run"] + run_args,
            capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to spawn antigravity-acp container: {result.stderr[:500]}")
        try:
            AntigravityObserverPool._copy_runtime_files(
                name, runtime_files, project_root / "pawflow_relay")
        except Exception:
            subprocess.run(  # nosec B603
                docker_cmd() + ["rm", "-f", name], capture_output=True, timeout=15)
            raise
        return name


__all__ = [
    "AntigravityAcpContainer",
    "AntigravityAcpPool",
    "CONTAINER_KIND",
    "CONTAINER_MCP_BRIDGE",
    "CONTAINER_PYTHON",
    "DEFAULT_SERVER_BINARY",
    "SERVER_ARGS",
]
