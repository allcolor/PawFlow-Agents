"""Claude Code session/workdir management mixin.

Extracted from claude_code.py — handles credentials, MCP config,
session directories, and CLI command building.
"""

import json
import logging
import os
from typing import Optional

from core.docker_utils import docker_cmd as _docker_cmd, to_host_path

logger = logging.getLogger(__name__)

def _persist_tokens_to_service(access_token: str, refresh_token: str,
                               expires_at, service_id: str = ""):
    """Save recovered tokens to secrets (encrypted).

    After a Claude Code run, the CLI may have refreshed the OAuth token.
    We read the updated token from .credentials.json and persist it
    to the global secrets store (encrypted).
    """
    from pathlib import Path
    from core.secrets import get_secrets_manager

    try:
        # Find the service_id if not provided
        sid = service_id
        if not sid:
            from gui.services.global_service_registry import GlobalServiceRegistry
            greg = GlobalServiceRegistry.get_instance()
            for _sid, sdef in greg.get_all_definitions().items():
                if getattr(sdef, "service_type", "") == "llmConnection":
                    _cfg = getattr(sdef, "config", {}) or {}
                    if _cfg.get("provider") == "claude-code":
                        sid = _sid
                        break
        if not sid:
            return

        sm = get_secrets_manager()
        prefix = sid.replace("-", "_")

        secrets_path = Path("config/global_secrets.json")
        secrets_path.parent.mkdir(parents=True, exist_ok=True)
        existing = {}
        if secrets_path.exists():
            existing = json.loads(secrets_path.read_text(encoding="utf-8"))
        existing[f"{prefix}_access_token"] = sm.encrypt(access_token)
        existing[f"{prefix}_refresh_token"] = sm.encrypt(refresh_token)
        existing[f"{prefix}_expires_at"] = str(int(expires_at))
        secrets_path.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")

        logger.info("[claude-code] tokens persisted to secrets for '%s'", sid)
    except Exception as e:
        logger.warning("[claude-code] failed to persist tokens: %s", e)


# Base directory for per-session Claude Code workdirs
_SESSIONS_BASE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "claude_sessions",
)


class ClaudeCodeSessionMixin:
    """Session/workdir management for Claude Code CLI.

    Provides:
    - _get_session_workdir: per-conversation working directories
    - _claude_code_env: subprocess environment setup
    - _setup_credentials: OAuth token management
    - _recover_tokens: post-run token refresh recovery
    - _setup_mcp_config: MCP bridge configuration
    - _build_claude_cmd: CLI command construction
    """

    def _resolve_service_tokens(self) -> dict:
        """Resolve Claude tokens from the service config store — always fresh.

        Returns {"access_token": ..., "refresh_token": ..., "expires_at": ...}
        """
        from core.expression import resolve_value
        try:
            from gui.services.global_service_registry import GlobalServiceRegistry
            for sid, sdef in GlobalServiceRegistry.get_instance().get_all_definitions().items():
                if getattr(sdef, "service_type", "") == "llmConnection":
                    cfg = getattr(sdef, "config", {}) or {}
                    if cfg.get("provider") == "claude-code" and cfg.get("claude_access_token"):
                        resolved = resolve_value(cfg)
                        return {
                            "access_token": resolved.get("claude_access_token", "") or "",
                            "refresh_token": resolved.get("claude_refresh_token", "") or "",
                            "expires_at": resolved.get("claude_expires_at", 0) or 0,
                        }
        except Exception:
            pass
        return {"access_token": "", "refresh_token": "", "expires_at": 0}

    def _get_session_workdir(self, conversation_id: str,
                             agent_name: str = "") -> str:
        """Get or create a dedicated working directory for this session."""
        cid = conversation_id or "default"
        # Sanitize: replace :: (used in sub-conv keys) with __ for safe paths
        cid = cid.replace(":", "_")
        agent = agent_name or "default"
        workdir = os.path.join(_SESSIONS_BASE, cid, agent)
        os.makedirs(workdir, exist_ok=True)
        return workdir

    def _claude_code_env(self, workdir: str = "") -> dict:
        """Build environment for claude subprocess.

        Sets CLAUDE_CONFIG_DIR to the session workdir so Claude Code
        reads credentials from our managed .credentials.json instead
        of the user's ~/.claude/.credentials.json.
        """
        env = os.environ.copy()
        if workdir:
            env["CLAUDE_CONFIG_DIR"] = workdir
        return env

    # Cached tool relay info (shared across all claude-code agents)
    _tool_relay_cache: Optional[tuple] = None

    @classmethod
    def _get_tool_relay_info(cls) -> tuple:
        """Get the shared tool relay service (created once, reused by all agents).

        Returns (url, token). Creates the service on first call.
        """
        if cls._tool_relay_cache:
            return cls._tool_relay_cache
        try:
            from gui.services.global_service_registry import GlobalServiceRegistry
            greg = GlobalServiceRegistry.get_instance()

            # Check if a live tool relay already exists (from this server run)
            for sid, sdef in greg.get_all_definitions().items():
                if getattr(sdef, "service_type", "") == "toolRelay":
                    svc = greg.get_live_instance(sid)
                    if svc:
                        cfg = getattr(sdef, "config", {}) or {}
                        port = int(cfg.get("port", 0))
                        token = cfg.get("token", "")
                        if port and token:
                            cls._tool_relay_cache = (
                                f"wss://localhost:{port}/ws/tools", token)
                            return cls._tool_relay_cache
                    # Stale from previous run — remove it
                    try:
                        greg.uninstall(sid)
                    except Exception:
                        pass

            # Create fresh tool relay with dynamic port
            import uuid
            import socket as _sock
            with _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM) as _s:
                _s.bind(("", 0))
                free_port = _s.getsockname()[1]
            token = uuid.uuid4().hex
            service_id = "_tool_relay"
            greg.install(service_id, "toolRelay", {
                "port": free_port,
                "path": "/ws/tools",
                "token": token,
                "_service_id": service_id,
            }, description="Auto-created tool relay for Claude Code MCP bridge")
            svc = greg.get_live_instance(service_id)
            if svc:
                logger.info("Tool relay created: port=%d", free_port)
                cls._tool_relay_cache = (
                    f"wss://localhost:{free_port}/ws/tools", token)
                return cls._tool_relay_cache
        except Exception as e:
            logger.error("Failed to get/create tool relay: %s", e)
        return "", ""

    def _setup_credentials(self, workdir: str):
        """Write .credentials.json in session workdir for Claude Code auth.

        Always resolves tokens just-in-time from the store — never cached.

        Raises LLMClientError if no credentials configured.
        """
        from core.llm_client import LLMClientError

        tokens = self._resolve_service_tokens()
        access_token = tokens["access_token"]
        refresh_token = tokens["refresh_token"]
        expires_at = tokens["expires_at"]

        if not access_token:
            raise LLMClientError(
                "Claude Code credentials not configured. "
                "Go to Admin → Services → claude_code_llm_service → Login "
                "to authenticate with your Claude subscription.")

        # Check expiry — warn early instead of getting a 401 mid-session
        import time as _time
        if expires_at:
            _exp_s = int(expires_at) / 1000 if int(expires_at) > 1e12 else int(expires_at)
            _remaining = _exp_s - _time.time()
            if _remaining < 0:
                raise LLMClientError(
                    f"Claude Code OAuth token expired ({abs(_remaining)/3600:.0f}h ago). "
                    "Use /cls to re-authenticate.")

        creds = {
            "claudeAiOauth": {
                "accessToken": access_token,
                "refreshToken": refresh_token,
                "expiresAt": int(expires_at),
                "scopes": [
                    "org:create_api_key",
                    "user:profile",
                    "user:inference",
                    "user:sessions:claude_code",
                    "user:mcp_servers",
                    "user:file_upload",
                ],
            }
        }
        creds_path = os.path.join(workdir, ".credentials.json")
        with open(creds_path, "w", encoding="utf-8") as f:
            json.dump(creds, f)

    def _recover_tokens(self, workdir: str):
        """Read back tokens from workdir after a run.

        Claude Code may have refreshed the access_token during the run.
        If tokens changed, update the service config so next run uses them.
        """
        creds_path = os.path.join(workdir, ".credentials.json")
        if not os.path.exists(creds_path):
            return
        try:
            with open(creds_path, "r", encoding="utf-8") as f:
                creds = json.load(f)
            oauth = creds.get("claudeAiOauth", {})
            new_access = oauth.get("accessToken", "")
            new_refresh = oauth.get("refreshToken", "")
            new_expires = oauth.get("expiresAt", 0)
            if not new_access:
                return

            # Check if tokens changed vs what's in the store
            from core.expression import resolve_value
            _current = self._resolve_service_tokens()
            if new_access == _current.get("access_token", ""):
                return

            # Persist to service config (no in-memory caching)
            _service_id = getattr(self, '_agent_service', '') or ''
            _persist_tokens_to_service(
                new_access, new_refresh, new_expires,
                service_id=_service_id)
            logger.info("Recovered refreshed Claude Code tokens for '%s'", _service_id)
        except Exception as e:
            logger.debug("Token recovery failed: %s", e)

    def _setup_mcp_config(self, workdir: str, user_id: str = "",
                          conversation_id: str = "",
                          agent_name: str = "") -> str:
        """Write MCP config to workdir and return the file path."""
        _containerize = getattr(self, 'containerize', False)

        if _containerize:
            mcp_bridge = "/opt/pawflow/mcp_bridge.py"
            python_bin = "python3"
        else:
            mcp_bridge = self._get_mcp_bridge_path()
            if not os.path.exists(mcp_bridge):
                return ""
            import sys as _sys
            python_bin = _sys.executable or "python"

        relay_url, relay_token = self._get_tool_relay_info()
        if not relay_url:
            logger.warning("No toolRelay service — MCP bridge will have no tools")

        # In Docker mode, replace localhost with the host IP reachable from container
        if _containerize and relay_url:
            from core.docker_utils import get_host_ip
            _host_ip = get_host_ip()
            relay_url = relay_url.replace("localhost", _host_ip)
            relay_url = relay_url.replace("127.0.0.1", _host_ip)

        config = {
            "mcpServers": {
                "pawflow": {
                    "command": python_bin,
                    "args": [mcp_bridge],
                    "env": {
                        "PAWFLOW_TOOL_RELAY_URL": relay_url,
                        "PAWFLOW_TOOL_RELAY_TOKEN": relay_token,
                        "PAWFLOW_USER_ID": user_id or "",
                        "PAWFLOW_CONVERSATION_ID": conversation_id or "",
                        "PAWFLOW_AGENT_NAME": agent_name or "",
                    },
                }
            }
        }

        mcp_path = os.path.join(workdir, ".mcp.json")
        with open(mcp_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)

        logger.info("MCP config written: %s (relay=%s)", mcp_path, relay_url)
        return mcp_path

    def _get_mcp_bridge_path(self) -> str:
        """Path to the MCP bridge script (tools/mcp_bridge.py at project root)."""
        project_root = os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))
        return os.path.join(project_root, "tools", "mcp_bridge.py")

    # All built-in Claude Code tools that must be disabled
    # (server filesystem != user's filesystem — everything goes through MCP)
    _DISALLOWED_BUILTIN_TOOLS = (
        "Bash,Edit,Read,Write,Glob,Grep,NotebookEdit,WebFetch,WebSearch,"
        "Task,Agent,ToolSearch,ListMcpResourcesTool,ReadMcpResourceTool,"
        "EnterPlanMode,ExitPlanMode,EnterWorktree,ExitWorktree,"
        "RemoteTrigger,Skill,TaskOutput,TaskStop,TodoWrite,"
        "CronCreate,CronDelete,CronList,AskUserQuestion"
    )

    def _build_claude_cmd(self, model: str,
                          session_id: str = "",
                          mcp_config_path: str = "",
                          workdir: str = "") -> list:
        """Build claude CLI command with bidirectional stream-json.

        --disallowedTools: blocks ALL built-in tools (filesystem is remote)
        --strict-mcp-config: ignores pre-existing MCP configs
        Only our pawflow MCP tools (get_tool_schema, use_tool) remain.
        If MCP fails, Claude Code has ZERO tools and stops.

        When containerize=True, wraps the command in docker run.
        """
        claude_args = [
            "-p",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--model", model or "sonnet",
            "--dangerously-skip-permissions",
            "--max-turns", "1000",
            "--verbose",
            "--strict-mcp-config",
            "--disallowedTools", self._DISALLOWED_BUILTIN_TOOLS,
        ]
        _effort = self._cfg("effort", "")
        if _effort:
            claude_args.extend(["--effort", _effort])
        if mcp_config_path:
            claude_args.extend(["--mcp-config", mcp_config_path])
        if session_id:
            claude_args.extend(["--resume", session_id])

        if not getattr(self, 'containerize', False):
            return [self.claude_binary] + claude_args

        # Docker mode: run Claude Code in a container
        image = getattr(self, 'docker_image', '') or "pawflow-claude-code:latest"
        cpu = getattr(self, 'docker_cpu_limit', '') or "2"
        mem = getattr(self, 'docker_memory_limit', '') or "2g"

        # Resolve host address for MCP bridge to connect back
        from core.docker_utils import get_host_ip
        host_addr = get_host_ip()

        docker_run_args = [
            "--rm", "-i",
            "--cpus", cpu,
            "--memory", mem,
            "--name", f"pawflow-claude-{os.getpid()}-{os.urandom(4).hex()}",
            # Mount session dir for persistence (memories, CLAUDE.md)
            "-v", f"{to_host_path(workdir)}:/workspace",
            # Environment — HOME must be /workspace so Claude Code
            # finds .credentials.json at $CLAUDE_CONFIG_DIR/
            "-e", "CLAUDE_CONFIG_DIR=/workspace",
            "-e", "HOME=/workspace",
            "-e", "NODE_OPTIONS=--max-old-space-size=1536",
            "-e", f"PAWFLOW_HOST={host_addr}",
            # Fix git "dubious ownership" — workdir is mounted from host with different uid
            "-e", "GIT_CONFIG_COUNT=1",
            "-e", "GIT_CONFIG_KEY_0=safe.directory",
            "-e", "GIT_CONFIG_VALUE_0=/workspace",
            # Network: allow MCP bridge to reach host tool relay
            "--add-host", f"host.docker.internal:host-gateway",
            # Run as non-root: Claude Code refuses --dangerously-skip-permissions as root
            "--user", "1000:1000",
            # Security
            "--tmpfs", "/tmp:rw,nosuid,size=256m",
            "--security-opt", "no-new-privileges",
            image,
        ] + claude_args

        # Store args for docker_popen (used in _stream_claude_code)
        self._docker_run_args = docker_run_args
        return _docker_cmd() + ["run"] + docker_run_args
