"""Antigravity CLI interactive sessions.

This pool starts the real ``agy`` CLI in tmux with Gemini OAuth/MCP config and
a transparent observer proxy for ``daily-cloudcode-pa.googleapis.com``. The
same tmux/proxy foundation is used by both the diagnostics observer action and
the ``antigravity-interactive`` LLM provider.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, TYPE_CHECKING
import json
import logging
import os
import shlex
import socket
import subprocess  # nosec B404 - Docker/tmux process control is this module's job.
import threading
import time
import uuid

import core.paths as _paths
from core.cc_interactive_certs import ca_private_key_is_host_only, generate_leaf
from core.docker_utils import docker_cmd, get_server_id, to_host_path, translate_path
from core.apparmor import apparmor_security_opts

if TYPE_CHECKING:
    from core.llm_client import LLMClient


logger = logging.getLogger(__name__)
from core._antigravity_base import AntigravityObserverSession, ANTIGRAVITY_BACKEND_HOST  # noqa: F401,E402
from core._antigravity_manual import _AntigravityManualIngestMixin  # noqa: E402
from core._antigravity_input import _AntigravityInputMixin  # noqa: E402


class AntigravityObserverPool(_AntigravityManualIngestMixin, _AntigravityInputMixin):
    """Persistent observer containers keyed by user/conversation/agent/service."""

    _instance: Optional["AntigravityObserverPool"] = None
    _instance_lock = threading.Lock()
    _TMUX_TARGET = "pawflow-agy:0.0"
    _LITERAL_CHUNK_BYTES = 512
    _LITERAL_CHUNK_DELAY_SECONDS = 0.2
    _NO_DONE_IDLE_DRAIN_SECONDS = 8.0

    @classmethod
    def instance(cls) -> "AntigravityObserverPool":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def __init__(self):
        self._lock = threading.Lock()
        self._sessions: dict[tuple[str, str, str, str], AntigravityObserverSession] = {}
        # Run the in-container CLI as the host launcher's uid/gid
        # (PAWFLOW_RUN_UID/GID) — the uid that owns the cc_sessions slot — never
        # a hardcoded 1000, which lands in the wrong /tmp/tmux-<uid>/ and hits
        # EACCES on deployments launched under a different uid (e.g. 1001).
        # Same contract as every other provider pool (CCI/codex/gemini).
        self.run_uid = self._numeric_env("PAWFLOW_RUN_UID", "1000")
        self.run_gid = self._numeric_env("PAWFLOW_RUN_GID", "1000")

    @staticmethod
    def _numeric_env(name: str, default: str) -> str:
        value = os.environ.get(name, default).strip()
        return value if value.isdigit() else default

    def _user_spec(self) -> str:
        """`<uid>:<gid>` for `docker exec --user`, mapped to the host launcher."""
        return f"{self.run_uid}:{self.run_gid}"

    @staticmethod
    def _safe(value: str) -> str:
        return (value or "").replace(":", "_").replace("/", "_").replace("\\", "_")

    @staticmethod
    def _base_dir() -> Path:
        return _paths.RUNTIME_DIR / "sessions" / "antigravity-observer"

    @classmethod
    def _workdir(cls, user_id: str, conversation_id: str, agent_name: str) -> str:
        if not user_id:
            raise ValueError("user_id is required for Antigravity observer")
        if not conversation_id:
            raise ValueError("conversation_id is required for Antigravity observer")
        if not agent_name:
            raise ValueError("agent_name is required for Antigravity observer")
        path = cls._base_dir() / cls._safe(user_id) / cls._safe(conversation_id) / agent_name
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    @staticmethod
    def _physical_container_workdir(user_id: str, conversation_id: str, agent_name: str) -> str:
        return "/cc_sessions_host/{}/{}/{}".format(
            AntigravityObserverPool._safe(user_id),
            AntigravityObserverPool._safe(conversation_id),
            agent_name,
        )

    @staticmethod
    def _container_workdir(user_id: str, conversation_id: str, agent_name: str) -> str:
        return "/cc_sessions/{}/{}".format(
            AntigravityObserverPool._safe(conversation_id),
            agent_name,
        )

    def start(self, *, user_id: str, conversation_id: str, agent_name: str,
              service_id: str = "", model: str = "") -> AntigravityObserverSession:
        key = (user_id, conversation_id, agent_name, service_id or "")
        stale = None
        with self._lock:
            existing = self._sessions.get(key)
            if existing and self._is_usable(existing):
                existing.last_used = time.time()
                self._ensure_manual_ingest(existing)
                return existing
            if existing:
                self._sessions.pop(key, None)
                stale = existing
        if stale:
            logger.info("Restarting stale Antigravity observer session %s", stale.name)
            self.kill(stale)
        state = self._start_new(user_id, conversation_id, agent_name, service_id or "", model or "")
        self._ensure_manual_ingest(state)
        with self._lock:
            self._sessions[key] = state
        return state

    def ensure_started(self, client, model: str, user_id: str,
                       conversation_id: str, agent_name: str) -> AntigravityObserverSession:
        service_id = getattr(client, "_agent_service", "") or ""
        key = (user_id, conversation_id, agent_name, service_id)
        stale = None
        with self._lock:
            existing = self._sessions.get(key)
            if existing and self._is_usable(existing):
                existing.last_used = time.time()
                return existing
            if existing:
                self._sessions.pop(key, None)
                stale = existing
        if stale:
            logger.info("Restarting stale Antigravity interactive session %s", stale.name)
            self.kill(stale)
        state = self._start_new(
            user_id, conversation_id, agent_name, service_id, model or "",
            client=client)
        with self._lock:
            self._sessions[key] = state
        return state

    def find_session(self, user_id: str, conversation_id: str, agent_name: str,
                     service_id: str = "") -> Optional[AntigravityObserverSession]:
        key = (user_id, conversation_id, agent_name, service_id or "")
        with self._lock:
            state = self._sessions.get(key)
            if state and self._is_alive(state.name):
                state.last_used = time.time()
                return state
            if state:
                self._sessions.pop(key, None)
        return None

    def list_sessions(self, user_id: str, conversation_id: str, service_id: str = "") -> list[dict]:
        now = time.time()
        out = []
        with self._lock:
            for key, state in list(self._sessions.items()):
                uid, conv, agent, svc = key
                if uid != user_id or conv != conversation_id:
                    continue
                if service_id and svc != service_id:
                    continue
                alive = self._is_alive(state.name)
                if not alive:
                    self._sessions.pop(key, None)
                    continue
                out.append({
                    "user_id": uid,
                    "conv_id": conv,
                    "agent_name": agent,
                    "service_id": svc,
                    "container_name": state.name,
                    "log_path": state.log_path,
                    "idle_seconds": max(0.0, now - state.last_used),
                    "lived_seconds": max(0.0, now - state.created_at),
                    "provider": "antigravity-observer",
                })
        return out

    def kill_and_evict_by_conv(self, conv_id: str, reason: str) -> int:
        """Kill all live Antigravity sessions for one conversation."""
        with self._lock:
            victims = [(key, state) for key, state in self._sessions.items()
                       if key[1] == conv_id]
            for key, _state in victims:
                self._sessions.pop(key, None)
        for key, state in victims:
            logger.info("[ag-live] kill_by_conv %s/%s/%s (%s)",
                        key[1][:8], key[2], key[3], reason)
            self.kill(state)
        return len(victims)

    def kill_and_evict_by_conv_agent(self, conv_id: str, agent_name: str,
                                      reason: str) -> int:
        """Kill live Antigravity sessions for one (conversation, agent) pair."""
        with self._lock:
            victims = [(key, state) for key, state in self._sessions.items()
                       if key[1] == conv_id and key[2] == agent_name]
            for key, _state in victims:
                self._sessions.pop(key, None)
        for key, state in victims:
            logger.info("[ag-live] kill_by_conv_agent %s/%s/%s (%s)",
                        key[1][:8], key[2], key[3], reason)
            self.kill(state)
        return len(victims)

    def touch(self, state: AntigravityObserverSession) -> None:
        state.last_used = time.time()

    def _start_new(self, user_id: str, conversation_id: str, agent_name: str,
                   service_id: str, model: str, client=None) -> AntigravityObserverSession:
        workdir = self._workdir(user_id, conversation_id, agent_name)
        if client is None:
            from core.llm_client import LLMClient
            setup_client = LLMClient(provider="gemini", config={"provider": "gemini"})
        else:
            setup_client = client
        original_agent_service = getattr(setup_client, "_agent_service", "") or ""
        setup_client._agent_service = service_id or original_agent_service
        setup_client._user_id = user_id
        setup_client._agent_name = agent_name
        try:
            setup_client._gemini_setup_credentials(workdir)
            self._write_antigravity_config(setup_client, workdir, user_id, conversation_id, agent_name, model)
        finally:
            if client is not None:
                setup_client._agent_service = original_agent_service

        cert_dir = Path(workdir) / ".pawflow_ag" / "certs"
        certs = generate_leaf(cert_dir, common_name=ANTIGRAVITY_BACKEND_HOST)
        log_dir = Path(workdir) / ".pawflow_ag" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_id = uuid.uuid4().hex[:12]
        log_path = str(log_dir / f"observer-{log_id}.jsonl")
        stderr_path = str(log_dir / f"proxy-{log_id}.stderr.log")

        name = self._spawn_container(user_id=user_id, conversation_id=conversation_id, agent_name=agent_name)
        physical_workdir = self._physical_container_workdir(user_id, conversation_id, agent_name)
        container_workdir = self._container_workdir(user_id, conversation_id, agent_name)
        try:
            self._install_ca(name, physical_workdir)
            self._start_proxy(name=name, container_workdir=physical_workdir,
                              log_path=log_path, stderr_path=stderr_path, certs=certs)
            self._start_agy_tmux(name=name, container_workdir=physical_workdir)
        except Exception:
            subprocess.run(docker_cmd() + ["rm", "-f", name], capture_output=True, timeout=15)  # nosec B603
            raise

        return AntigravityObserverSession(
            key=(user_id, conversation_id, agent_name, service_id),
            name=name,
            workdir=workdir,
            container_workdir=container_workdir,
            log_path=log_path,
        )

    def _write_antigravity_config(self, client: "LLMClient", workdir: str, user_id: str,
                                  conversation_id: str, agent_name: str, model: str) -> None:
        mcp_servers, _internal_token = client._gemini_acp_mcp_servers(user_id, conversation_id, agent_name)
        client._gemini_acp_write_settings(
            workdir, model=model or "", effort="", thinking_budget=0,
            temperature=0.7, max_tokens=0, mcp_servers=mcp_servers,
            mcp_cwd=self._container_workdir(user_id, conversation_id, agent_name),
        )
        gemini_home = Path(workdir) / ".gemini"
        config_dir = gemini_home / "config"
        projects_dir = config_dir / "projects"
        projects_dir.mkdir(parents=True, exist_ok=True)
        mcp_config = client._gemini_acp_settings_mcp_servers(
            mcp_servers, self._container_workdir(user_id, conversation_id, agent_name))
        (config_dir / "mcp_config.json").write_text(
            json.dumps(mcp_config, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        (gemini_home / "mcp_config.json").write_text(
            json.dumps({"mcpServers": mcp_config}, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        antigravity_dir = gemini_home / "antigravity"
        antigravity_dir.mkdir(parents=True, exist_ok=True)
        antigravity_mcp = self._antigravity_mcp_config(mcp_config)
        (antigravity_dir / "mcp_config.json").write_text(
            json.dumps(antigravity_mcp, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        antigravity_cli_dir = gemini_home / "antigravity-cli"
        antigravity_cli_dir.mkdir(parents=True, exist_ok=True)
        (antigravity_cli_dir / "mcp_config.json").write_text(
            json.dumps(antigravity_mcp, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        cli_settings_path = antigravity_cli_dir / "settings.json"
        cli_settings = self._read_json(cli_settings_path)
        trusted = cli_settings.get("trustedWorkspaces")
        if not isinstance(trusted, list):
            trusted = []
        container_workdir = self._container_workdir(user_id, conversation_id, agent_name)
        if container_workdir not in trusted:
            trusted.append(container_workdir)
        cli_permissions = cli_settings.get("permissions")
        if not isinstance(cli_permissions, dict):
            cli_permissions = {}
        cli_allow = cli_permissions.get("allow")
        if not isinstance(cli_allow, list):
            cli_allow = []
        for pattern in ("mcp(pawflow/*)", "mcp_pawflow_*", "mcp_*"):
            if pattern not in cli_allow:
                cli_allow.append(pattern)
        cli_permissions["allow"] = cli_allow
        cli_settings["enableTelemetry"] = False
        cli_settings["trustedWorkspaces"] = trusted
        cli_settings["permissions"] = cli_permissions
        cli_settings["mcpServers"] = mcp_config
        cli_settings["allowMCPServers"] = ["pawflow"]
        cli_settings["mcp"] = {"allowed": ["pawflow"]}
        cli_settings_path.write_text(
            json.dumps(cli_settings, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        settings_path = gemini_home / "settings.json"
        settings = self._read_json(settings_path)
        permissions = settings.get("permissions")
        if not isinstance(permissions, dict):
            permissions = {}
        allow = permissions.get("allow")
        if not isinstance(allow, list):
            allow = []
        if "mcp(pawflow/*)" not in allow:
            allow.append("mcp(pawflow/*)")
        for pattern in ("mcp_pawflow_*", "mcp_*"):
            if pattern not in allow:
                allow.append(pattern)
        permissions["allow"] = allow
        settings["permissions"] = permissions
        settings["mcpServers"] = mcp_config
        settings_path.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        project_id = str(uuid.uuid5(uuid.NAMESPACE_URL, self._container_workdir(user_id, conversation_id, agent_name)))
        agents_dir = Path(workdir) / ".agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        (agents_dir / "mcp_config.json").write_text(
            json.dumps({"mcpServers": mcp_config}, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        project = {
            "id": project_id,
            "name": self._container_workdir(user_id, conversation_id, agent_name),
            "projectResources": {
                "resources": [{
                    "gitFolder": {
                        "folderUri": f"file://{self._container_workdir(user_id, conversation_id, agent_name)}",
                        "allowWrite": True,
                    }
                }]
            },
        }
        (projects_dir / f"{project_id}.json").write_text(
            json.dumps(project, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        keybindings = gemini_home / "antigravity-cli" / "keybindings.json"
        keybindings.parent.mkdir(parents=True, exist_ok=True)
        if not keybindings.exists():
            keybindings.write_text("{}\n", encoding="utf-8")
        self._write_workspace_rules(workdir)

    @staticmethod
    def _write_workspace_rules(workdir: str) -> None:
        rules_dir = Path(workdir) / ".agents" / "rules"
        rules_dir.mkdir(parents=True, exist_ok=True)
        (rules_dir / "pawflow-mcp.md").write_text(
            "# PawFlow MCP Tools\n\n"
            "Use the configured MCP server `pawflow` for filesystem, shell, search, edit, patch, browser, web, image, and desktop actions.\n"
            "Do not create custom WebSocket, HTTP, relay, or token-based clients to call PawFlow directly.\n"
            "If the MCP server or a required MCP tool is unavailable, report that MCP is unavailable instead of bypassing it.\n",
            encoding="utf-8",
        )

    @staticmethod
    def _antigravity_mcp_entry(entry: dict) -> dict:
        """Return the MCP server shape documented by Antigravity."""
        allowed = {
            "type", "command", "serverUrl", "args", "env", "cwd", "headers",
            "authProviderType", "oauth", "disabled", "disabledTools", "timeout", "trust",
        }
        return {k: v for k, v in (entry or {}).items() if k in allowed and v not in (None, "")}

    @classmethod
    def _antigravity_mcp_config(cls, mcp_config: dict) -> dict:
        """Return the Antigravity/Jetski MCP customization shape."""
        servers = []
        for name, entry in (mcp_config or {}).items():
            spec = cls._antigravity_mcp_entry(entry)
            spec["serverName"] = name
            spec.setdefault("disabled", False)
            servers.append(spec)
        return {"mcpServers": servers}

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

    def _spawn_container(self, *, user_id: str, conversation_id: str, agent_name: str) -> str:
        self._base_dir().mkdir(parents=True, exist_ok=True)
        project_root = Path(__file__).resolve().parents[1]
        sessions_host = translate_path(to_host_path(str(self._base_dir().resolve())))
        mounts = ["-v", f"{sessions_host}:/cc_sessions_host"]
        runtime_files = [
            (project_root / "tools" / "mcp_bridge.py", "/opt/pawflow/mcp_bridge.py"),
            (project_root / "core" / "tool_json.py", "/opt/pawflow/tool_json.py"),
            (project_root / "tools" / "ag_observer_proxy.py", "/opt/pawflow/ag_observer_proxy.py"),
            (project_root / "tools" / "ag_observer_semantics.py", "/opt/pawflow/ag_observer_semantics.py"),
            (project_root / "docker" / "pawflow_sdk" / "pawflow.py", "/opt/pawflow/pawflow.py"),
        ]
        pkg_dir = project_root / "pawflow_relay"
        if not ca_private_key_is_host_only([m.split(":", 1)[0] for m in mounts if isinstance(m, str)]):
            raise RuntimeError("Refusing to mount Antigravity observer CA private key")

        owner = get_server_id()
        name = f"pf-{owner[:12]}-agyobs-{uuid.uuid4().hex[:8]}"
        image = os.environ.get("PAWFLOW_ANTIGRAVITY_IMAGE", os.environ.get("PAWFLOW_GEMINI_IMAGE", "pawflow-claude-code:latest"))
        run_args = [
            "-d", "--rm", "--name", name, "--init",
            *mounts,
            "--add-host", f"{ANTIGRAVITY_BACKEND_HOST}:127.0.0.1",
            "--add-host", "host.docker.internal:host-gateway",
            "--cap-add", "SYS_ADMIN",
            *apparmor_security_opts(image),
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
            raise RuntimeError(f"Failed to spawn Antigravity observer container: {result.stderr[:500]}")
        self._copy_runtime_files(name, runtime_files, pkg_dir)
        subprocess.run(docker_cmd() + ["exec", "--user", "root", name, "chronyd"],  # nosec B603
                       capture_output=True, timeout=5)
        return name

    @staticmethod
    def _copy_runtime_files(name: str, files: list[tuple[Path, str]], pkg_dir: Path) -> None:
        mkdir = subprocess.run(  # nosec B603
            docker_cmd() + ["exec", "--user", "root", name, "mkdir", "-p", "/opt/pawflow"],
            capture_output=True, text=True, timeout=10)
        if mkdir.returncode != 0:
            raise RuntimeError(f"Failed to prepare Antigravity observer runtime dir: {mkdir.stderr[:300]}")
        for src, dst in files:
            if not src.exists():
                continue
            cp = subprocess.run(  # nosec B603
                docker_cmd() + ["cp", str(src), f"{name}:{dst}"],
                capture_output=True, text=True, timeout=15)
            if cp.returncode != 0:
                raise RuntimeError(f"Failed to copy {src.name} into Antigravity observer container: {cp.stderr[:300]}")
        if pkg_dir.is_dir():
            cp = subprocess.run(  # nosec B603
                docker_cmd() + ["cp", str(pkg_dir), f"{name}:/opt/pawflow/pawflow_relay"],
                capture_output=True, text=True, timeout=30)
            if cp.returncode != 0:
                raise RuntimeError(f"Failed to copy pawflow_relay into Antigravity observer container: {cp.stderr[:300]}")

    def _install_ca(self, name: str, container_workdir: str) -> None:
        ca_path = f"{container_workdir}/.pawflow_ag/certs/pawflow-ca.crt"
        cmd = f"cp {shlex.quote(ca_path)} /usr/local/share/ca-certificates/pawflow-ag.crt && update-ca-certificates"
        r = subprocess.run(docker_cmd() + ["exec", "--user", "root", name, "bash", "-lc", cmd],  # nosec B603
                           capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            raise RuntimeError(f"Failed to install Antigravity observer CA: {r.stderr[:300]}")

    def _start_proxy(self, *, name: str, container_workdir: str, log_path: str,
                     stderr_path: str = "", certs=None) -> None:
        ips = self._resolve_upstream_ips()
        container_log = self._container_session_path(log_path)
        container_stderr = self._container_session_path(stderr_path or f"{log_path}.stderr.log")
        if stderr_path:
            stderr_file = Path(stderr_path)
            stderr_file.parent.mkdir(parents=True, exist_ok=True)
            stderr_file.write_text(
                f"starting Antigravity observer proxy in {name}; "
                f"log={container_log}; upstream={ANTIGRAVITY_BACKEND_HOST}\n",
                encoding="utf-8",
            )
        env = [
            "-e", f"PAWFLOW_AG_OBSERVER_LOG={container_log}",
            "-e", f"PAWFLOW_AG_UPSTREAM_IPS={','.join(ips)}",
            "-e", f"PAWFLOW_AG_LEAF_CERT={container_workdir}/.pawflow_ag/certs/{Path(certs.cert_path).name}",
            "-e", f"PAWFLOW_AG_LEAF_KEY={container_workdir}/.pawflow_ag/certs/{Path(certs.key_path).name}",
        ]
        for key in ("PAWFLOW_AG_OBSERVER_LOG_B64", "PAWFLOW_AG_OBSERVER_MAX_B64_BYTES"):
            value = os.environ.get(key)
            if value:
                env += ["-e", f"{key}={value}"]
        r = subprocess.run(  # nosec B603
            docker_cmd() + ["exec", "-d", "--user", "root", *env, name,
                            "bash", "-lc",
                            f"exec python3 /opt/pawflow/ag_observer_proxy.py >> {shlex.quote(container_stderr)} 2>&1"],
            capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            raise RuntimeError(f"Failed to start Antigravity observer proxy: {r.stderr[:300]}")
        self._wait_for_proxy_start(log_path, stderr_path=stderr_path)

    def _container_session_path(self, path: str) -> str:
        rel = Path(path).resolve().relative_to(self._base_dir().resolve())
        return "/cc_sessions/" + rel.as_posix()

    def _start_agy_tmux(self, *, name: str, container_workdir: str) -> None:
        parts = container_workdir.lstrip("/").split("/")
        if len(parts) < 3 or parts[0] != "cc_sessions_host":
            raise ValueError(f"container_workdir must look like /cc_sessions_host/<user>/<conv>/<agent>; got {container_workdir!r}")
        user_slot = "/cc_sessions_host/" + parts[1]
        ns_workdir = "/cc_sessions/" + "/".join(parts[2:])
        agy_bin = os.environ.get("PAWFLOW_ANTIGRAVITY_BIN", "agy")
        quoted_cmd = " ".join(shlex.quote(a) for a in [agy_bin, "--dangerously-skip-permissions"])
        drop_privs = f"setpriv --reuid={self.run_uid} --regid={self.run_gid} --clear-groups --"
        shell = (
            "mkdir -p /cc_sessions && "
            f"mount --bind {shlex.quote(user_slot)} /cc_sessions && "
            f"cd {shlex.quote(ns_workdir)} && ("
            f"{drop_privs} tmux kill-session -t pawflow-agy 2>/dev/null || true; "
            f"{drop_privs} tmux new-session -d -s pawflow-agy -x 220 -y 50 "
            f"'env HOME={shlex.quote(ns_workdir)} "
            f"GEMINI_CLI_HOME={shlex.quote(ns_workdir)} "
            f"CASCADE_ENABLE_MCP_TOOLS=true "
            f"USER=pawflow TERM=xterm-256color "
            f"{quoted_cmd}'; "
            # Pin the window size so the webchat tmux viewer attaching/detaching
            # never resizes the agent's terminal (a mid-turn SIGWINCH reflows
            # the TUI and corrupts the in-flight capture). Same fix as the CCI
            # pool; the viewer is a passive, letterboxed view of the fixed pane.
            f"{drop_privs} tmux set-window-option -t pawflow-agy window-size manual 2>/dev/null || true; "
            f"{drop_privs} tmux set-window-option -t pawflow-agy aggressive-resize off 2>/dev/null || true)"
        )
        r = subprocess.run(  # nosec B603
            docker_cmd() + ["exec", "--user", "root", name,
                            "setsid", "--wait", "unshare", "-m",
                            "--propagation", "unchanged", "--",
                            "bash", "-lc", shell],
            capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            raise RuntimeError(f"Failed to start Antigravity tmux: {r.stderr[:500]}")
        probe = subprocess.run(  # nosec B603
            docker_cmd() + ["exec", "--user", self._user_spec(), name,
                            "tmux", "has-session", "-t", "pawflow-agy"],
            capture_output=True, text=True, timeout=10)
        if probe.returncode != 0:
            raise RuntimeError(
                "Antigravity tmux session exited during startup: "
                f"{(probe.stderr or probe.stdout or '').strip()[:500]}")
        self._prime_agy_mcp(name)

    def _prime_agy_mcp(self, name: str) -> None:
        if os.environ.get("PAWFLOW_AGY_SKIP_MCP_PRIME", "").lower() in {"1", "true", "yes"}:
            return
        prime = (
            "sleep 1; "
            "tmux set-buffer -t pawflow-agy -- /mcp && "
            "tmux paste-buffer -t pawflow-agy && "
            "tmux send-keys -t pawflow-agy Enter && "
            "sleep 1; "
            "tmux send-keys -t pawflow-agy Enter && "
            "sleep 0.2; "
            "tmux send-keys -t pawflow-agy Escape"
        )
        r = subprocess.run(  # nosec B603
            docker_cmd() + ["exec", "--user", self._user_spec(), name, "bash", "-lc", prime],
            capture_output=True, text=True, timeout=8)
        if r.returncode != 0:
            logger.warning(
                "Antigravity MCP priming failed for %s: %s",
                name, (r.stderr or r.stdout or "").strip()[:500])

    @staticmethod
    def _resolve_upstream_ips() -> list[str]:
        infos = socket.getaddrinfo(ANTIGRAVITY_BACKEND_HOST, 443, type=socket.SOCK_STREAM)
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

    def _is_usable(self, state: AntigravityObserverSession) -> bool:
        return self._is_alive(state.name) and self._proxy_log_ready(state.log_path)

    @staticmethod
    def _proxy_log_ready(log_path: str) -> bool:
        path = Path(log_path)
        if not path.is_file():
            return False
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (event.get("type") == "proxy_start"
                        and event.get("upstream_host") == ANTIGRAVITY_BACKEND_HOST):
                    return True
        except OSError:
            return False
        return False

    def _wait_for_proxy_start(self, log_path: str, timeout: float = 3.0,
                              stderr_path: str = "") -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._proxy_log_ready(log_path):
                return
            time.sleep(0.05)
        detail = ""
        if stderr_path:
            try:
                stderr = Path(stderr_path).read_text(encoding="utf-8", errors="replace").strip()
                if stderr:
                    detail = f": {stderr[-500:]}"
            except OSError:
                pass
        raise RuntimeError(f"Antigravity observer proxy did not write proxy_start{detail}")

    def kill(self, state: AntigravityObserverSession) -> None:
        state.manual_ingest_stop.set()
        kill_result = subprocess.run(  # nosec B603
            docker_cmd() + ["kill", "--signal=KILL", state.name],
            capture_output=True, timeout=10)
        if kill_result.returncode != 0 and self._is_alive(state.name):
            logger.warning(
                "[antigravity-interactive] docker kill -9 failed for %s: %s",
                state.name, self._command_error("docker kill -9", kill_result))
        rm_result = subprocess.run(  # nosec B603
            docker_cmd() + ["rm", "-f", state.name], capture_output=True, timeout=15)
        if rm_result.returncode != 0:
            logger.warning(
                "[antigravity-interactive] docker rm -f failed for %s: %s",
                state.name, self._command_error("docker rm -f", rm_result))
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if not self._is_alive(state.name):
                break
            time.sleep(0.05)
        else:
            logger.warning(
                "[antigravity-interactive] container still alive after kill: %s",
                state.name)
        with self._lock:
            self._sessions.pop(state.key, None)
