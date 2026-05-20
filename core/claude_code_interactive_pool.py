"""Persistent Docker sessions for claude-code-interactive.

Unlike ``ClaudeCodePool`` where one ``claude -p`` exec owns one throwaway
container, this pool keeps one interactive Claude Code tmux session alive per
``(user, conversation, agent, service)``. Output is not read from tmux or the
Claude transcript; the provider consumes MITM-observed SSE events.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional
try:
    import fcntl  # POSIX-only; the flock below is best-effort on platforms that have it
except ImportError:  # Windows: server still boots, the marker file is written without an advisory lock
    fcntl = None  # type: ignore[assignment]
import hashlib
import json
import os
import shlex
import socket
import subprocess  # nosec B404
import threading
import time
import uuid
import logging

from core.cc_interactive_certs import generate_leaf, ca_private_key_is_host_only
from core.docker_utils import docker_cmd, get_host_ip, get_server_id, to_host_path, translate_path
from core.llm_providers.claude_code_session import ClaudeCodeSessionMixin
import core.paths as _paths


logger = logging.getLogger(__name__)


@dataclass
class InteractiveContainer:
    key: tuple[str, str, str, str]
    name: str
    workdir: str
    container_workdir: str
    session_token: str
    event_service_id: str
    internal_token: str
    created_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)
    initial_context_loaded: bool = False
    proxy_started: bool = False
    claude_started: bool = False
    last_error: str = ""


class InteractiveClaudeCodePool:
    _instance: Optional["InteractiveClaudeCodePool"] = None
    _instance_lock = threading.Lock()

    @classmethod
    def instance(cls) -> "InteractiveClaudeCodePool":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
                cls._instance._register_death_handlers()
            return cls._instance

    def __init__(self):
        self._lock = threading.RLock()
        self._sessions: Dict[tuple[str, str, str, str], InteractiveContainer] = {}
        self._disallowed_builtin_tools = ClaudeCodeSessionMixin._DISALLOWED_BUILTIN_TOOLS
        self._sweeper_started = False
        self._sweeper_stop = threading.Event()
        self._tick_seconds = 60
        self._idle_ttl = float(os.environ.get("PAWFLOW_CCI_IDLE_TTL_SECONDS", "1800"))
        self._shutdown_once = False

    def _register_death_handlers(self):
        """Kill tracked interactive containers when the Python process exits."""
        import atexit
        import signal
        import sys

        def _kill_all(*_args, **_kwargs):
            if getattr(self, "_shutdown_once", False):
                return
            self._shutdown_once = True
            try:
                self.shutdown_all()
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

        atexit.register(_kill_all)
        try:
            if threading.current_thread() is threading.main_thread():
                for _sig in (signal.SIGINT, signal.SIGTERM):
                    try:
                        _prev = signal.getsignal(_sig)

                        def _handler(signum, frame, prev=_prev):
                            _kill_all()
                            if callable(prev):
                                prev(signum, frame)
                            elif prev == signal.SIG_DFL:
                                sys.exit(128 + signum)

                        signal.signal(_sig, _handler)
                    except Exception:
                        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

    @staticmethod
    def _safe(value: str) -> str:
        return (value or "").replace(":", "_").replace("/", "_").replace("\\", "_")

    @staticmethod
    def _container_workdir(user_id: str, conversation_id: str, agent_name: str) -> str:
        return "/cc_sessions/{}/{}".format(
            (conversation_id or "").replace(":", "_"),
            agent_name,
        )

    @staticmethod
    def _physical_container_workdir(user_id: str, conversation_id: str, agent_name: str) -> str:
        return "/cc_sessions/{}/{}/{}".format(
            InteractiveClaudeCodePool._safe(user_id),
            (conversation_id or "").replace(":", "_"),
            agent_name,
        )

    def ensure_started(self, client, model: str, user_id: str,
                       conversation_id: str, agent_name: str) -> InteractiveContainer:
        idle_ttl = getattr(client, "timeout", None)
        self.ensure_sweeper(idle_ttl_seconds=int(idle_ttl) if idle_ttl else None)
        service_id = getattr(client, "_agent_service", "") or ""
        key = (user_id, conversation_id, agent_name, service_id)
        with self._lock:
            existing = self._sessions.get(key)
            if existing and self._is_alive(existing.name):
                existing.last_used = time.time()
                return existing
            if existing:
                self._sessions.pop(key, None)

        state = self._start_new(client, model, user_id, conversation_id, agent_name, key)
        with self._lock:
            self._sessions[key] = state
        return state

    def touch(self, state: InteractiveContainer) -> None:
        with self._lock:
            state.last_used = time.time()

    def find_session(self, user_id: str, conversation_id: str,
                     agent_name: str, service_id: str = "") -> Optional[InteractiveContainer]:
        """Return the newest live interactive session for an agent."""
        with self._lock:
            candidates = [
                state for key, state in self._sessions.items()
                if key[0] == user_id
                and key[1] == conversation_id
                and key[2] == agent_name
                and (not service_id or key[3] == service_id)
            ]
            candidates.sort(key=lambda state: state.last_used, reverse=True)
            for state in candidates:
                if self._is_alive(state.name):
                    state.last_used = time.time()
                    return state
                self._sessions.pop(state.key, None)
        return None

    def list_sessions(self, user_id: str, conversation_id: str,
                      service_id: str = "") -> list[dict]:
        """Return live interactive sessions for a conversation."""
        sessions: list[dict] = []
        with self._lock:
            candidates = [
                (key, state) for key, state in self._sessions.items()
                if key[0] == user_id
                and key[1] == conversation_id
                and (not service_id or key[3] == service_id)
            ]
            candidates.sort(key=lambda row: row[1].last_used, reverse=True)
            for key, state in candidates:
                if not self._is_alive(state.name):
                    self._sessions.pop(key, None)
                    continue
                sessions.append({
                    "agent_name": key[2],
                    "service_id": key[3],
                    "container_name": state.name,
                    "last_used": state.last_used,
                    "live": True,
                    "reuse_count": 1 if state.initial_context_loaded else 0,
                    "created_at": state.created_at,
                    "idle_seconds": max(0.0, time.time() - state.last_used),
                    "lived_seconds": max(0.0, time.time() - state.created_at),
                    "provider": "claude-code-interactive",
                })
        return sessions

    def send_text(self, state: InteractiveContainer, text: str) -> bool:
        state.last_error = ""
        if not self._is_alive(state.name):
            state.last_error = f"Container {state.name} is not running"
            return False
        self._remember_injected_prompt(state, text)
        self._remember_injected_prompt_for_event_service(state, text)
        if not self._load_buffer(state, text):
            return False
        if not self._paste_buffer(state):
            return False
        return self.send_keys(state, ["Enter"])

    def _load_buffer(self, state: InteractiveContainer, text: str) -> bool:
        cmd = docker_cmd() + [
            "exec", "-i", "--user", "1000:1000", state.name,
            "tmux", "load-buffer", "-",
        ]
        r = subprocess.run(cmd, input=text.encode("utf-8"), capture_output=True, timeout=15)  # nosec B603
        if r.returncode != 0:
            state.last_error = self._command_error("tmux load-buffer", r)
            return False
        return True

    def _paste_buffer(self, state: InteractiveContainer) -> bool:
        r = subprocess.run(  # nosec B603
            docker_cmd() + ["exec", "--user", "1000:1000", state.name,
                            "tmux", "paste-buffer", "-t", "pawflow"],
            capture_output=True, timeout=10)
        if r.returncode != 0:
            state.last_error = self._command_error("tmux paste-buffer", r)
            return False
        return True

    @staticmethod
    def _remember_injected_prompt(state: InteractiveContainer, text: str) -> None:
        """Record PawFlow-injected tmux prompts so hooks can ignore them."""
        try:
            marker_dir = Path(state.workdir) / ".pawflow_cci"
            marker_dir.mkdir(parents=True, exist_ok=True)
            marker = marker_dir / "injected_prompts.jsonl"
            payload = {
                "sha256": hashlib.sha256((text or "").encode("utf-8")).hexdigest(),
                "length": len(text or ""),
                "ts": time.time(),
            }
            with open(marker, "a", encoding="utf-8") as fh:
                if fcntl is not None:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                fh.write(json.dumps(payload, separators=(",", ":")) + "\n")
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

    @staticmethod
    def _remember_injected_prompt_for_event_service(state: InteractiveContainer,
                                                    text: str) -> None:
        try:
            from services.cc_interactive_event_service import get_or_create_cc_interactive_event_service
            _, _, event_service = get_or_create_cc_interactive_event_service()
            event_service.remember_injected_prompt(state.session_token, text or "")
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

    def send_interrupt(self, state: InteractiveContainer, text: str) -> bool:
        state.last_error = ""
        if not self._is_alive(state.name):
            state.last_error = f"Container {state.name} is not running"
            return False
        self._remember_injected_prompt(state, text)
        self._remember_injected_prompt_for_event_service(state, text)
        return (self._load_buffer(state, text)
                and self._paste_buffer(state)
                and self.send_keys(state, ["Escape"])
                and self.send_keys(state, ["Enter"]))

    def force_stop(self, state: InteractiveContainer) -> bool:
        return self.send_keys(
            state, ["Space", "Space", "Escape", "Escape", "BSpace", "BSpace"])

    def send_keys(self, state: InteractiveContainer, keys: list[str]) -> bool:
        state.last_error = ""
        if not self._is_alive(state.name):
            state.last_error = f"Container {state.name} is not running"
            return False
        r = subprocess.run(  # nosec B603
            docker_cmd() + ["exec", "--user", "1000:1000", state.name,
                            "tmux", "send-keys", "-t", "pawflow", *keys],
            capture_output=True, timeout=10)
        if r.returncode != 0:
            state.last_error = self._command_error("tmux send-keys", r)
            return False
        return True

    @staticmethod
    def _command_error(label: str, result) -> str:
        stderr = getattr(result, "stderr", b"") or b""
        stdout = getattr(result, "stdout", b"") or b""
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        detail = (stderr or stdout or "").strip()
        if detail:
            return f"{label} failed: {detail[:500]}"
        return f"{label} failed with exit code {getattr(result, 'returncode', '?')}"

    def kill_session(self, user_id: str, conversation_id: str,
                     agent_name: str, service_id: str = "") -> bool:
        key = (user_id, conversation_id, agent_name, service_id or "")
        with self._lock:
            state = self._sessions.pop(key, None)
        if not state:
            return False
        self._kill_container(state.name)
        return True

    def kill_and_evict_by_conv(self, conv_id: str, reason: str) -> int:
        """Kill live containers for every interactive session in a conversation."""
        with self._lock:
            victims = [(key, state) for key, state in self._sessions.items()
                       if key[1] == conv_id]
            for key, _state in victims:
                self._sessions.pop(key, None)
        for key, state in victims:
            logger.info("[cci-live] kill_by_conv %s (%s)",
                        self._fmt_key(key), reason)
            self._kill_container(state.name)
        return len(victims)

    def kill_and_evict_by_conv_agent(self, conv_id: str, agent_name: str,
                                      reason: str) -> int:
        """Kill live containers for one interactive (conversation, agent) pair."""
        with self._lock:
            victims = [(key, state) for key, state in self._sessions.items()
                       if key[1] == conv_id and key[2] == agent_name]
            for key, _state in victims:
                self._sessions.pop(key, None)
        for key, state in victims:
            logger.info("[cci-live] kill_by_conv_agent %s (%s)",
                        self._fmt_key(key), reason)
            self._kill_container(state.name)
        return len(victims)

    def ensure_sweeper(self, tick_seconds: int = 60,
                       idle_ttl_seconds: Optional[int] = None) -> None:
        if idle_ttl_seconds and idle_ttl_seconds > 0:
            self._idle_ttl = max(self._idle_ttl, float(idle_ttl_seconds))
        self._tick_seconds = max(1, int(tick_seconds or 60))
        if self._sweeper_started:
            return
        self._sweeper_started = True

        def _loop():
            while not self._sweeper_stop.wait(self._tick_seconds):
                try:
                    self.sweep_idle()
                except Exception:
                    logger.debug("[cci-live] sweeper tick failed", exc_info=True)

        threading.Thread(target=_loop, daemon=True, name="cci-live-sweeper").start()

    def sweep_idle(self, idle_ttl_seconds: Optional[float] = None) -> int:
        ttl = float(idle_ttl_seconds if idle_ttl_seconds is not None else self._idle_ttl)
        cutoff = time.time() - ttl
        to_kill: list[tuple[str, str]] = []
        with self._lock:
            snapshot = list(self._sessions.items())
        dead: Dict[tuple[str, str, str, str], bool] = {}
        for key, state in snapshot:
            dead[key] = not self._is_alive(state.name)
        with self._lock:
            for key, state in snapshot:
                current = self._sessions.get(key)
                if current is not state:
                    continue
                reason = ""
                if dead.get(key):
                    reason = "dead_container"
                elif current.last_used < cutoff:
                    reason = f"idle>{int(ttl)}s"
                if reason:
                    self._sessions.pop(key, None)
                    to_kill.append((current.name, reason))
        for name, reason in to_kill:
            logger.info("[cci-live] evict %s (%s)", name, reason)
            self._kill_container(name)
        return len(to_kill)

    def shutdown_all(self) -> None:
        self._sweeper_stop.set()
        with self._lock:
            names = [state.name for state in self._sessions.values()]
            self._sessions.clear()
        for name in names:
            self._kill_container(name)

    @staticmethod
    def _kill_container(name: str) -> None:
        subprocess.run(docker_cmd() + ["rm", "-f", name], capture_output=True, timeout=15)  # nosec B603

    @staticmethod
    def _fmt_key(key: tuple[str, str, str, str]) -> str:
        user_id, conv_id, agent_name, service_id = key
        return (
            f"{user_id[:6] or '?'}/{conv_id[:8] or '?'}/"
            f"{agent_name or 'default'}@{service_id or 'default'}"
        )

    def _start_new(self, client, model: str, user_id: str, conversation_id: str,
                   agent_name: str, key: tuple[str, str, str, str]) -> InteractiveContainer:
        from services.cc_interactive_event_service import get_or_create_cc_interactive_event_service

        workdir = client._get_session_workdir(conversation_id, agent_name, user_id)
        client._setup_credentials(workdir)
        mcp_path, internal_token = client._setup_mcp_config(workdir, user_id, conversation_id, agent_name)
        cert_dir = Path(workdir) / ".pawflow_cci" / "certs"
        certs = generate_leaf(cert_dir)

        event_url, event_token, event_service = get_or_create_cc_interactive_event_service()
        host_ip = get_host_ip()
        event_url = event_url.replace("localhost", host_ip).replace("127.0.0.1", host_ip)
        session_token = uuid.uuid4().hex
        event_service.register_session(
            session_token,
            user_id=user_id,
            conversation_id=conversation_id,
            agent_name=agent_name,
        )

        name = self._spawn_container(
            user_id=user_id, conversation_id=conversation_id,
            agent_name=agent_name)
        physical_container_workdir = self._physical_container_workdir(
            user_id, conversation_id, agent_name)
        container_workdir = self._container_workdir(user_id, conversation_id, agent_name)
        state = InteractiveContainer(
            key=key,
            name=name,
            workdir=workdir,
            container_workdir=container_workdir,
            session_token=session_token,
            event_service_id=getattr(event_service, "service_id", ""),
            internal_token=internal_token,
        )

        try:
            self._write_hook_settings(workdir)
            self._install_ca(name, physical_container_workdir)
            self._start_proxy(
                name=name,
                container_workdir=physical_container_workdir,
                session_token=session_token,
                event_url=event_url,
                event_token=event_token,
                internal_token=internal_token,
            )
            state.proxy_started = True
            self._start_claude_tmux(
                name=name,
                container_workdir=physical_container_workdir,
                mcp_path=f"{container_workdir}/.mcp.json",
                model=model,
                effort=client._cfg("effort", "") if hasattr(client, "_cfg") else "",
                ca_path=f"{container_workdir}/.pawflow_cci/certs/pawflow-ca.crt",
                session_token=session_token,
                event_url=event_url,
                event_token=event_token,
                internal_token=internal_token,
            )
            state.claude_started = True
        except Exception:
            subprocess.run(docker_cmd() + ["rm", "-f", name], capture_output=True, timeout=15)  # nosec B603
            raise
        return state

    def _spawn_container(self, *, user_id: str = "", conversation_id: str = "",
                         agent_name: str = "") -> str:
        _paths.CLAUDE_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        project_root = Path(__file__).resolve().parents[1]
        sessions_host = translate_path(to_host_path(str(_paths.CLAUDE_SESSIONS_DIR.resolve())))
        mounts = ["-v", f"{sessions_host}:/cc_sessions"]
        files = [
            (project_root / "tools" / "mcp_bridge.py", "/opt/pawflow/mcp_bridge.py"),
            (project_root / "tools" / "cc_interactive_filters.py", "/opt/pawflow/cc_interactive_filters.py"),
            (project_root / "tools" / "cc_interactive_proxy.py", "/opt/pawflow/cc_interactive_proxy.py"),
            (project_root / "tools" / "cc_interactive_hook.py", "/opt/pawflow/cc_interactive_hook.py"),
            (project_root / "docker" / "pawflow_sdk" / "pawflow.py", "/opt/pawflow/pawflow.py"),
        ]
        for src, dst in files:
            if src.exists():
                mounts += ["-v", f"{translate_path(to_host_path(str(src)))}:{dst}:ro"]
        pkg_dir = project_root / "pawflow_relay"
        if pkg_dir.is_dir():
            mounts += ["-v", f"{translate_path(to_host_path(str(pkg_dir)))}:/opt/pawflow/pawflow_relay:ro"]
        # Bind-mount the skill repository scope dirs read-only so SKILL.md
        # asset references (${CLAUDE_SKILL_DIR}/...) resolve inside the
        # persistent interactive container, like the batch claude-code pool.
        try:
            from core.cli_workspace_mounts import build_skill_mount_args
            mounts += build_skill_mount_args(
                conversation_id, agent_name, user_id=user_id)
        except Exception:
            logger.debug("[cci-live] skill mount args failed", exc_info=True)
        if not ca_private_key_is_host_only([m.split(":", 1)[0] for m in mounts if isinstance(m, str)]):
            raise RuntimeError("Refusing to mount CC interactive CA private key")

        owner = get_server_id()
        name = f"pf-{owner[:12]}-cci-{uuid.uuid4().hex[:8]}"
        image = os.environ.get("PAWFLOW_CLAUDE_CODE_IMAGE", "pawflow-claude-code:latest")
        run_args = [
            "-d", "--rm", "--name", name,
            *mounts,
            "--add-host", "api.anthropic.com:127.0.0.1",
            "--add-host", "host.docker.internal:host-gateway",
            "--cap-add", "SYS_ADMIN",
            "--shm-size", "512m",
            "--tmpfs", "/tmp:rw,nosuid,size=512m",  # nosec B108 - Docker tmpfs mount target inside ephemeral container.
            "--user", "root",
            "--entrypoint", "/usr/bin/sleep",
            image,
            "infinity",
        ]
        result = subprocess.run(docker_cmd() + ["run"] + run_args,  # nosec B603
                                capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to spawn CC interactive container: {result.stderr[:500]}")
        subprocess.run(docker_cmd() + ["exec", "--user", "root", name, "chronyd"],  # nosec B603
                       capture_output=True, timeout=5)
        return name

    def _install_ca(self, name: str, container_workdir: str) -> None:
        ca_path = f"{container_workdir}/.pawflow_cci/certs/pawflow-ca.crt"
        cmd = (
            f"cp {shlex.quote(ca_path)} /usr/local/share/ca-certificates/pawflow-cci.crt && "
            "update-ca-certificates"
        )
        r = subprocess.run(docker_cmd() + ["exec", "--user", "root", name, "bash", "-lc", cmd],  # nosec B603
                           capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            raise RuntimeError(f"Failed to install CC interactive CA: {r.stderr[:300]}")

    def _start_proxy(self, *, name: str, container_workdir: str,
                     session_token: str, event_url: str, event_token: str,
                     internal_token: str) -> None:
        ips = self._resolve_upstream_ips()
        env = [
            "-e", f"PAWFLOW_CCI_SESSION_TOKEN={session_token}",
            "-e", f"PAWFLOW_CCI_EVENT_URL={event_url}",
            "-e", f"PAWFLOW_CCI_EVENT_TOKEN={event_token}",
            "-e", f"PAWFLOW_INTERNAL_TOKEN={internal_token}",
            "-e", f"PAWFLOW_ANTHROPIC_UPSTREAM_IPS={','.join(ips)}",
            "-e", f"PAWFLOW_CCI_LEAF_CERT={container_workdir}/.pawflow_cci/certs/api-anthropic.crt",
            "-e", f"PAWFLOW_CCI_LEAF_KEY={container_workdir}/.pawflow_cci/certs/api-anthropic.key",
        ]
        for key in (
            "PAWFLOW_CCI_PROXY_WIRE_LOG",
            "PAWFLOW_CCI_PROXY_WIRE_LOG_ALL",
            "PAWFLOW_CCI_PROXY_WIRE_LOG_PATHS",
        ):
            value = os.environ.get(key)
            if value:
                env += ["-e", f"{key}={value}"]
        r = subprocess.run(  # nosec B603
            docker_cmd() + ["exec", "-d", "--user", "root", *env, name,
                            "python3", "/opt/pawflow/cc_interactive_proxy.py"],
            capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            raise RuntimeError(f"Failed to start CC interactive proxy: {r.stderr[:300]}")

    def _write_hook_settings(self, workdir: str) -> None:
        hooks = {}
        handler = {
            "type": "command",
            "command": "python3",
            "args": ["/opt/pawflow/cc_interactive_hook.py"],
            "timeout": 5,
        }
        for event_name in ("UserPromptSubmit", "Stop", "StopFailure", "PreCompact", "PostCompact", "SessionEnd"):
            hooks[event_name] = [{"hooks": [handler]}]
        settings_path = Path(workdir) / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings = self._read_json(settings_path)
        self._deny_builtin_tools(settings)
        env = settings.get("env")
        if not isinstance(env, dict):
            env = {}
        # Claude Code recognizes these env toggles in their documented string forms.
        env.update({
            "CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION": "false",
            "CLAUDE_CODE_DISABLE_TERMINAL_TITLE": "1",
        })
        settings.update({
            "hooks": hooks,
            "enableAllProjectMcpServers": True,
            "enabledMcpjsonServers": ["pawflow"],
            "env": env,
        })
        settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")

        root_settings_path = Path(workdir) / "settings.json"
        root_settings = self._read_json(root_settings_path)
        root_settings.update({
            "theme": root_settings.get("theme") or "dark",
            "skipDangerousModePermissionPrompt": True,
        })
        root_settings_path.write_text(json.dumps(root_settings, indent=2) + "\n", encoding="utf-8")

        claude_json_path = Path(workdir) / ".claude.json"
        claude_json = self._read_json(claude_json_path)
        claude_json.update({
            "theme": claude_json.get("theme") or "dark",
            "hasCompletedOnboarding": True,
        })
        project_key = self._container_workdir("", "", "")
        try:
            rel = Path(workdir).relative_to(_paths.CLAUDE_SESSIONS_DIR)
            rel_parts = rel.as_posix().split("/")
            if len(rel_parts) >= 3:
                project_key = "/cc_sessions/" + "/".join(rel_parts[1:])
        except Exception:
            project_key = ""
        if project_key:
            claude_json.setdefault("projects", {}).setdefault(
                project_key, {})["hasTrustDialogAccepted"] = True
        claude_json_path.write_text(json.dumps(claude_json, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def _read_json(path: Path) -> dict:
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        return {}

    def _deny_builtin_tools(self, settings: dict) -> None:
        permissions = settings.get("permissions")
        if not isinstance(permissions, dict):
            permissions = {}
        disallowed = [
            item.strip() for item in self._disallowed_builtin_tools.split(",")
            if item.strip()
        ]
        disallowed_set = set(disallowed)
        allow = permissions.get("allow")
        if isinstance(allow, list):
            permissions["allow"] = [item for item in allow if item not in disallowed_set]
        deny = permissions.get("deny")
        if not isinstance(deny, list):
            deny = []
        for tool_name in disallowed:
            if tool_name not in deny:
                deny.append(tool_name)
        permissions["deny"] = deny
        settings["permissions"] = permissions

    def _start_claude_tmux(self, *, name: str, container_workdir: str,
                           mcp_path: str, model: str, effort: str = "",
                           ca_path: str,
                           session_token: str, event_url: str,
                           event_token: str, internal_token: str) -> None:
        parts = container_workdir.lstrip("/").split("/")
        if len(parts) < 3 or parts[0] != "cc_sessions":
            raise ValueError(
                f"container_workdir must look like /cc_sessions/<user>/<conv>/...; "
                f"got {container_workdir!r}")
        user_slot = "/cc_sessions/" + parts[1]
        ns_workdir = "/" + "/".join(parts[:1] + parts[2:])
        args = [
            "claude",
            # Interactive sessions are append-only while the tmux/container is
            # live. A cold start must always create a fresh Claude Code session
            # and receive PawFlow's initial context file; never pass --resume.
            "--dangerously-skip-permissions",
            "--verbose",
            "--thinking-display", "summarized",
            "--strict-mcp-config",
            "--mcp-config", mcp_path,
            "--max-turns", "1000",
        ]
        if model:
            args.extend(["--model", model])
        if effort:
            args.extend(["--effort", effort])
        args.extend(["--disallowedTools", self._disallowed_builtin_tools])
        quoted = " ".join(shlex.quote(a) for a in args)
        drop_privs = "setpriv --reuid=1000 --regid=1000 --clear-groups --"
        shell = (
            f"mount --bind {shlex.quote(user_slot)} /cc_sessions && "
            f"cd {shlex.quote(ns_workdir)} && ("
            f"{drop_privs} tmux kill-session -t pawflow 2>/dev/null || true; "
            f"{drop_privs} tmux new-session -d -s pawflow "
            f"'env HOME={shlex.quote(ns_workdir)} USER=pawflow "
            f"CLAUDE_CONFIG_DIR={shlex.quote(ns_workdir)} "
            f"NODE_EXTRA_CA_CERTS={shlex.quote(ca_path.replace(container_workdir, ns_workdir, 1))} "
            f"PAWFLOW_CCI_SESSION_TOKEN={shlex.quote(session_token)} "
            f"PAWFLOW_CCI_EVENT_URL={shlex.quote(event_url)} "
            f"PAWFLOW_CCI_EVENT_TOKEN={shlex.quote(event_token)} "
            f"PAWFLOW_INTERNAL_TOKEN={shlex.quote(internal_token)} "
            f"PAWFLOW_CCI_INJECTED_PROMPTS={shlex.quote(ns_workdir + '/.pawflow_cci/injected_prompts.jsonl')} "
            "CLAUDE_CODE_CERT_STORE=system TERM=xterm-256color "
            f"{quoted}')"
        )
        r = subprocess.run(  # nosec B603
            docker_cmd() + ["exec", "--user", "root", name,
                            "setsid", "--wait", "unshare", "-m", "--",
                            "bash", "-lc", shell],
            capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            raise RuntimeError(f"Failed to start Claude tmux: {r.stderr[:500]}")
        probe = subprocess.run(  # nosec B603
            docker_cmd() + ["exec", "--user", "1000:1000", name,
                            "tmux", "has-session", "-t", "pawflow"],
            capture_output=True, text=True, timeout=10)
        if probe.returncode != 0:
            raise RuntimeError(
                "Claude tmux session exited during startup: "
                f"{(probe.stderr or probe.stdout or '').strip()[:500]}")

    @staticmethod
    def _resolve_upstream_ips() -> list[str]:
        infos = socket.getaddrinfo("api.anthropic.com", 443, type=socket.SOCK_STREAM)
        seen = []
        for info in infos:
            ip = info[4][0]
            if ip not in seen and ip != "127.0.0.1":
                seen.append(ip)
        return seen

    @staticmethod
    def _is_alive(name: str) -> bool:
        try:
            result = subprocess.run(  # nosec B603
                docker_cmd() + ["inspect", "-f", "{{.State.Running}}", name],
                capture_output=True, text=True, timeout=5)
            return result.stdout.strip() == "true"
        except Exception:
            return False
