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

_OAUTH_URL = "https://console.anthropic.com/v1/oauth/token"
_OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"


def _refresh_claude_token(refresh_token: str) -> dict:
    """Refresh Claude OAuth token via Anthropic's endpoint.

    Returns dict with accessToken, refreshToken, expiresAt.
    """
    import urllib.request
    import urllib.error

    payload = json.dumps({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": _OAUTH_CLIENT_ID,
    }).encode("utf-8")

    req = urllib.request.Request(
        _OAUTH_URL, data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:200]
        raise RuntimeError(f"OAuth refresh failed ({e.code}): {body}") from e

    if "accessToken" not in data:
        raise RuntimeError(f"OAuth response missing accessToken: {list(data.keys())}")
    return data


def _persist_refreshed_tokens(access_token: str, refresh_token: str, expires_at):
    """Save refreshed tokens back to the service config."""
    try:
        from gui.services.global_service_registry import GlobalServiceRegistry
        greg = GlobalServiceRegistry.get_instance()
        for sid, sdef in greg.get_all_definitions().items():
            if getattr(sdef, "service_type", "") == "llmConnection":
                cfg = getattr(sdef, "config", {}) or {}
                if cfg.get("provider") == "claude-code":
                    cfg["claude_access_token"] = access_token
                    cfg["claude_refresh_token"] = refresh_token
                    cfg["claude_expires_at"] = int(expires_at)
                    greg.update_service(sid, config=cfg)
                    logger.info("[claude-code] refreshed tokens persisted to service '%s'", sid)
                    break
    except Exception as e:
        logger.warning("[claude-code] failed to persist refreshed tokens: %s", e)


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

    def _get_session_workdir(self, conversation_id: str,
                             agent_name: str = "") -> str:
        """Get or create a dedicated working directory for this session."""
        cid = conversation_id or "default"
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

        Reads tokens from the LIVE service config (not cached attrs —
        tokens may have been added after the client was created).
        NO fallback to local ~/.claude/.

        Raises LLMClientError if no credentials configured.
        """
        from core.llm_client import LLMClientError

        # Read from live service config (may have been updated since client creation)
        access_token = getattr(self, 'claude_access_token', '') or ''
        refresh_token = getattr(self, 'claude_refresh_token', '') or ''
        expires_at = getattr(self, 'claude_expires_at', 0) or 0

        if not access_token:
            # Try live config from registry (tokens added after client init)
            try:
                from gui.services.global_service_registry import GlobalServiceRegistry
                for sid, sdef in GlobalServiceRegistry.get_instance().get_all_definitions().items():
                    if getattr(sdef, "service_type", "") == "llmConnection":
                        cfg = getattr(sdef, "config", {}) or {}
                        if cfg.get("provider") == "claude-code" and cfg.get("claude_access_token"):
                            access_token = cfg["claude_access_token"]
                            refresh_token = cfg.get("claude_refresh_token", "")
                            expires_at = cfg.get("claude_expires_at", 0)
                            # Update self for next call
                            self.claude_access_token = access_token
                            self.claude_refresh_token = refresh_token
                            self.claude_expires_at = expires_at
                            break
            except Exception:
                pass

        if not access_token:
            raise LLMClientError(
                "Claude Code credentials not configured. "
                "Go to Admin → Services → claude_code_llm_service → Login "
                "to authenticate with your Claude subscription.")

        # Auto-refresh if token expired or expires within 5 minutes
        import time as _t
        if refresh_token and expires_at and _t.time() * 1000 > (expires_at - 300000):
            try:
                refreshed = _refresh_claude_token(refresh_token)
                access_token = refreshed["accessToken"]
                refresh_token = refreshed.get("refreshToken", refresh_token)
                expires_at = refreshed.get("expiresAt", 0)
                # Persist new tokens
                self.claude_access_token = access_token
                self.claude_refresh_token = refresh_token
                self.claude_expires_at = expires_at
                _persist_refreshed_tokens(access_token, refresh_token, expires_at)
                logger.info("[claude-code] token refreshed (expires in %ds)",
                            (expires_at - _t.time() * 1000) / 1000)
            except Exception as e:
                logger.warning("[claude-code] token refresh failed: %s — using existing token", e)

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

            # Check if tokens changed
            old_access = getattr(self, 'claude_access_token', '')
            if new_access == old_access:
                return

            # Update in-memory
            self.claude_access_token = new_access
            self.claude_refresh_token = new_refresh
            self.claude_expires_at = new_expires

            # Persist to service config
            from gui.services.global_service_registry import GlobalServiceRegistry
            for sid, sdef in GlobalServiceRegistry.get_instance().get_all_definitions().items():
                cfg = getattr(sdef, "config", {}) or {}
                if cfg.get("provider") == "claude-code" and cfg.get("claude_access_token") == old_access:
                    cfg["claude_access_token"] = new_access
                    cfg["claude_refresh_token"] = new_refresh
                    cfg["claude_expires_at"] = new_expires
                    GlobalServiceRegistry.get_instance()._save_to_disk()
                    logger.info("Recovered refreshed Claude Code tokens for '%s'", sid)
                    break
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
            "--name", f"pawflow-claude-{os.getpid()}",
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
