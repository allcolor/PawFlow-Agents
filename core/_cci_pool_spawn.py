"""Container spawn/build machinery for InteractiveClaudeCodePool: leaf-cert
generation, docker run, runtime-file copy, CA install, proxy + tmux start, and
liveness checks. Also holds the InteractiveContainer state dataclass.

Split out of claude_code_interactive_pool.py as a leaf mixin so the pool file
stays <= 800 lines. Spawn methods rely on host state/methods provided by
InteractiveClaudeCodePool (self._lock, self._containers, self._fmt_key,
self._kill_container, self._user_spec, self._container_workdir, etc.).
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import socket
import subprocess  # nosec B404
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from core.cc_interactive_certs import generate_leaf, ca_private_key_is_host_only
from core.docker_utils import get_host_ip, get_server_id, to_host_path, translate_path
from core.apparmor import apparmor_security_opts
import core.paths as _paths

logger = logging.getLogger(__name__)


def docker_cmd():
    """Resolve docker_cmd through the pool module at call time so tests that
    monkeypatch core.claude_code_interactive_pool.docker_cmd still take effect
    after the spawn methods moved here."""
    import core.claude_code_interactive_pool as _pool
    return _pool.docker_cmd()


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
    # Set once the Claude Code TUI has drawn its input prompt and is ready
    # to accept a pasted message. Stays False until the first send confirms
    # readiness, which gates the cold-start paste race (see send_text).
    prompt_ready: bool = False
    last_error: str = ""
    # Session-scoped dedup of observed tool_use/tool_result ids. A live
    # Claude Code session replays its full context (every prior tool_use
    # and tool_result block) on each API request, so the proxy re-emits
    # them every turn. These sets live on the session — not the per-turn
    # _CCITurnCoordinator — so an id seen on an earlier turn is never
    # re-emitted and re-appended to the PawFlow context.
    emitted_tool_use_ids: set = field(default_factory=set)
    emitted_tool_result_ids: set = field(default_factory=set)
    # Credential-pool coordinates captured at spawn so teardown can release the
    # exclusive slot (1 login = 1 live container) and recover any CLI-rotated
    # OAuth refresh_token back to the right pool slot. Defaults keep back-compat
    # for tests that construct a bare InteractiveContainer.
    service_id: str = ""
    svc_pool_idx: int = -1
    user_id: str = ""
    conv_id: str = ""



class _InteractiveContainerSpawnMixin:
    """Container build/start machinery for InteractiveClaudeCodePool."""

    @staticmethod
    def _anthropic_base_url(client) -> str:
        base_url = getattr(client, "base_url", "")
        if callable(base_url):
            base_url = base_url()
        elif isinstance(base_url, property):
            base_url = ""
        return str(base_url or "").strip().rstrip("/")

    @classmethod
    def _anthropic_endpoint(cls, client) -> tuple[str, int, str, str, int]:
        base_url = cls._anthropic_base_url(client)
        if not base_url:
            return "api.anthropic.com", 443, "https", "", 443
        parsed = urlparse(base_url if "//" in base_url else "https://" + base_url)
        scheme = (parsed.scheme or "https").lower()
        if scheme not in ("http", "https", "ws", "wss"):
            raise ValueError(
                "claude-code-interactive MITM requires an HTTP(S) base_url; "
                f"got {base_url!r}")
        host = parsed.hostname or "api.anthropic.com"
        upstream_port = parsed.port or (443 if scheme in ("https", "wss") else 80)
        listen_port = upstream_port
        netloc = parsed.netloc or host
        if scheme in ("http", "ws"):
            if parsed.port:
                netloc = f"{host}:{parsed.port}"
            else:
                netloc = host
        normalized = f"https://{netloc}{(parsed.path or '').rstrip('/')}"
        upstream_scheme = "https" if scheme in ("https", "wss") else "http"
        return host, upstream_port, upstream_scheme, normalized.rstrip("/"), listen_port

    def _start_new(self, client, model: str, user_id: str, conversation_id: str,
                   agent_name: str, key: tuple[str, str, str, str],
                   pool_index: int = -1) -> InteractiveContainer:
        from services.cc_interactive_event_service import get_or_create_cc_interactive_event_service

        workdir = client._get_session_workdir(conversation_id, agent_name, user_id)
        # pool_index is claimed exclusively by ensure_started (1 login = 1 live
        # container) so two concurrent containers never share a single-use OAuth
        # refresh_token. -1 only when called outside the pool (legacy/tests).
        client._setup_credentials(workdir, pool_index=pool_index,
                                   user_id=user_id, conversation_id=conversation_id)
        mcp_path, internal_token = client._setup_mcp_config(workdir, user_id, conversation_id, agent_name)
        cert_dir = Path(workdir) / ".pawflow_cci" / "certs"
        (upstream_host, upstream_port, upstream_scheme,
         anthropic_base_url, listen_port) = self._anthropic_endpoint(client)
        generate_leaf(
            cert_dir,
            common_name=upstream_host,
            extra_dns=(() if upstream_host == "api.anthropic.com" else ("api.anthropic.com",)),
        )  # writes leaf cert/key + CA into cert_dir (side effect)

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
            agent_name=agent_name, upstream_host=upstream_host)
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
            service_id=getattr(client, "_agent_service", "") or "",
            svc_pool_idx=pool_index,
            user_id=user_id,
            conv_id=conversation_id,
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
                upstream_host=upstream_host,
                upstream_port=upstream_port,
                upstream_scheme=upstream_scheme,
                listen_port=listen_port,
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
                anthropic_base_url=anthropic_base_url,
            )
            state.claude_started = True
        except Exception:
            subprocess.run(docker_cmd() + ["rm", "-f", name], capture_output=True, timeout=15)  # nosec B603
            raise
        return state

    def _spawn_container(self, *, user_id: str = "", conversation_id: str = "",
                         agent_name: str = "", upstream_host: str = "api.anthropic.com") -> str:
        _paths.CLAUDE_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        project_root = Path(__file__).resolve().parents[1]
        sessions_host = translate_path(to_host_path(str(_paths.CLAUDE_SESSIONS_DIR.resolve())))
        mounts = ["-v", f"{sessions_host}:/cc_sessions_host"]
        runtime_files = [
            (project_root / "tools" / "mcp_bridge.py", "/opt/pawflow/mcp_bridge.py"),
            (project_root / "core" / "tool_json.py", "/opt/pawflow/tool_json.py"),
            (project_root / "tools" / "cc_interactive_filters.py", "/opt/pawflow/cc_interactive_filters.py"),
            (project_root / "tools" / "cc_interactive_proxy.py", "/opt/pawflow/cc_interactive_proxy.py"),
            (project_root / "tools" / "cc_interactive_common.py", "/opt/pawflow/cc_interactive_common.py"),
            (project_root / "tools" / "cc_interactive_observers.py", "/opt/pawflow/cc_interactive_observers.py"),
            (project_root / "tools" / "cc_interactive_hook.py", "/opt/pawflow/cc_interactive_hook.py"),
            (project_root / "docker" / "pawflow_sdk" / "pawflow.py", "/opt/pawflow/pawflow.py"),
        ]
        pkg_dir = project_root / "pawflow_relay"
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
            "-d", "--rm", "--name", name, "--init",
            *mounts,
            "--add-host", f"{upstream_host or 'api.anthropic.com'}:127.0.0.1",
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
            raise RuntimeError(f"Failed to spawn CC interactive container: {result.stderr[:500]}")
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
            raise RuntimeError(f"Failed to prepare CC interactive runtime dir: {mkdir.stderr[:300]}")
        for src, dst in files:
            if not src.exists():
                continue
            cp = subprocess.run(  # nosec B603
                docker_cmd() + ["cp", str(src), f"{name}:{dst}"],
                capture_output=True, text=True, timeout=15)
            if cp.returncode != 0:
                raise RuntimeError(f"Failed to copy {src.name} into CC interactive container: {cp.stderr[:300]}")
        if pkg_dir.is_dir():
            cp = subprocess.run(  # nosec B603
                docker_cmd() + ["cp", str(pkg_dir), f"{name}:/opt/pawflow/pawflow_relay"],
                capture_output=True, text=True, timeout=30)
            if cp.returncode != 0:
                raise RuntimeError(f"Failed to copy pawflow_relay into CC interactive container: {cp.stderr[:300]}")

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
                     internal_token: str, upstream_host: str = "api.anthropic.com",
                     upstream_port: int = 443, upstream_scheme: str = "https",
                     listen_port: int = 443) -> None:
        ips = self._resolve_upstream_ips(upstream_host, upstream_port)
        upstream_scheme = "http" if str(upstream_scheme).lower() in {"http", "ws"} else "https"
        env = [
            "-e", f"PAWFLOW_CCI_SESSION_TOKEN={session_token}",
            "-e", f"PAWFLOW_CCI_EVENT_URL={event_url}",
            "-e", f"PAWFLOW_CCI_EVENT_TOKEN={event_token}",
            "-e", f"PAWFLOW_INTERNAL_TOKEN={internal_token}",
            "-e", f"PAWFLOW_ANTHROPIC_UPSTREAM_HOST={upstream_host or 'api.anthropic.com'}",
            "-e", f"PAWFLOW_ANTHROPIC_UPSTREAM_PORT={int(upstream_port or 443)}",
            "-e", f"PAWFLOW_ANTHROPIC_UPSTREAM_SCHEME={upstream_scheme}",
            "-e", f"PAWFLOW_ANTHROPIC_UPSTREAM_IPS={','.join(ips)}",
            "-e", f"PAWFLOW_CCI_PROXY_PORT={int(listen_port or 443)}",
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
                            "bash", "-lc",
                            "exec python3 /opt/pawflow/cc_interactive_proxy.py >> /tmp/cci_proxy.log 2>&1"],
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
                           event_token: str, internal_token: str,
                           anthropic_base_url: str = "") -> None:
        parts = container_workdir.lstrip("/").split("/")
        if len(parts) < 3 or parts[0] != "cc_sessions_host":
            raise ValueError(
                f"container_workdir must look like /cc_sessions_host/<user>/<conv>/...; "
                f"got {container_workdir!r}")
        user_slot = "/cc_sessions_host/" + parts[1]
        ns_workdir = "/cc_sessions/" + "/".join(parts[2:])
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
        drop_privs = (f"setpriv --reuid={self.run_uid} --regid={self.run_gid} "
                      "--clear-groups --")
        endpoint_env = (
            f"ANTHROPIC_BASE_URL={shlex.quote(anthropic_base_url)} "
            if anthropic_base_url else "")
        shell = (
            "mkdir -p /cc_sessions && "
            f"mount --bind {shlex.quote(user_slot)} /cc_sessions && "
            f"cd {shlex.quote(ns_workdir)} && "
            # The server provisions the workdir host-side under its own uid
            # with 755 dirs. We run the CLI as that SAME uid (run_uid =
            # PAWFLOW_RUN_UID), so pre-create the CLI-written subtrees (task
            # store, transcripts/memory under projects/) and chown them to
            # run_uid. Sharing one uid lets both the CLI and server-side tools
            # (the memory-skill `write` via the combined-fs) write here without
            # an EACCES across the uid boundary.
            f"mkdir -p tasks projects && chown -R {self.run_uid}:{self.run_gid} "
            "tasks projects && ("
            f"{drop_privs} tmux kill-session -t pawflow 2>/dev/null || true; "
            f"{drop_privs} tmux new-session -d -s pawflow -x 220 -y 50 "
            f"'env HOME={shlex.quote(ns_workdir)} USER=pawflow "
            f"CLAUDE_CONFIG_DIR={shlex.quote(ns_workdir)} "
            f"NODE_EXTRA_CA_CERTS={shlex.quote(ca_path.replace(container_workdir, ns_workdir, 1))} "
            f"PAWFLOW_CCI_SESSION_TOKEN={shlex.quote(session_token)} "
            f"PAWFLOW_CCI_EVENT_URL={shlex.quote(event_url)} "
            f"PAWFLOW_CCI_EVENT_TOKEN={shlex.quote(event_token)} "
            f"PAWFLOW_INTERNAL_TOKEN={shlex.quote(internal_token)} "
            f"PAWFLOW_CCI_INJECTED_PROMPTS={shlex.quote(ns_workdir + '/.pawflow_cci/injected_prompts.jsonl')} "
            f"{endpoint_env}"
            "CLAUDE_CODE_CERT_STORE=system TERM=xterm-256color "
            f"{quoted}'; "
            # Pin the window size so a webchat tmux viewer attaching/detaching
            # never resizes Claude Code's terminal. tmux otherwise resizes the
            # window to the attached client (measured: 20x6 detached -> 320x86
            # when the viewer opens), and that SIGWINCH reflows the Ink TUI
            # mid-turn, corrupting the in-flight capture (garbled/spliced text,
            # phantom empty rows, stuck-active). With window-size manual the
            # viewer is a passive, letterboxed view of the fixed pane.
            f"{drop_privs} tmux set-window-option -t pawflow window-size manual 2>/dev/null || true; "
            f"{drop_privs} tmux set-window-option -t pawflow aggressive-resize off 2>/dev/null || true)"
        )
        r = subprocess.run(  # nosec B603
            docker_cmd() + ["exec", "--user", "root", name,
                            "setsid", "--wait", "unshare", "-m",
                            "--propagation", "unchanged", "--",
                            "bash", "-lc", shell],
            capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            raise RuntimeError(f"Failed to start Claude tmux: {r.stderr[:500]}")
        probe = subprocess.run(  # nosec B603
            docker_cmd() + ["exec", "--user", self._user_spec(), name,
                            "tmux", "has-session", "-t", "pawflow"],
            capture_output=True, text=True, timeout=10)
        if probe.returncode != 0:
            raise RuntimeError(
                "Claude tmux session exited during startup: "
                f"{(probe.stderr or probe.stdout or '').strip()[:500]}")

    @staticmethod
    def _resolve_upstream_ips(host: str = "api.anthropic.com", port: int = 443) -> list[str]:
        infos = socket.getaddrinfo(host or "api.anthropic.com", int(port or 443), type=socket.SOCK_STREAM)
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
