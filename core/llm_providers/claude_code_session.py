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

def _find_cc_service_id(service_id: str = "") -> str:
    """Find the claude-code LLM service ID."""
    if service_id:
        return service_id
    try:
        from gui.services.global_service_registry import GlobalServiceRegistry
        for sid, sdef in GlobalServiceRegistry.get_instance().get_all_definitions().items():
            if getattr(sdef, "service_type", "") == "llmConnection":
                cfg = getattr(sdef, "config", {}) or {}
                if cfg.get("provider") == "claude-code":
                    return sid
    except Exception:
        pass
    return ""


def _load_credentials_pool(service_id: str = "") -> list:
    """Load the credentials pool for a CC service.

    Returns list of {"access_token", "refresh_token", "expires_at", "account", "added_at"}.
    Handles migration from single-credential format.
    """
    from pathlib import Path
    from core.secrets import get_secrets_manager

    sid = _find_cc_service_id(service_id)
    if not sid:
        return []
    sm = get_secrets_manager()
    prefix = sid.replace("-", "_")

    secrets_path = Path("config/global_secrets.json")
    if not secrets_path.exists():
        return []
    existing = json.loads(secrets_path.read_text(encoding="utf-8"))

    # New format: pool
    pool_key = f"{prefix}_credentials_pool"
    if pool_key in existing:
        try:
            pool_json = sm.decrypt(existing[pool_key])
            return json.loads(pool_json)
        except Exception:
            return []

    # Migration: old single-credential format → pool of 1
    at = existing.get(f"{prefix}_access_token", "")
    if at:
        try:
            cred = {
                "access_token": sm.decrypt(at),
                "refresh_token": sm.decrypt(existing.get(f"{prefix}_refresh_token", "")),
                "expires_at": int(existing.get(f"{prefix}_expires_at", 0)),
                "account": "",
                "added_at": 0,
            }
            # Migrate: save as pool, delete old keys
            pool = [cred]
            _save_credentials_pool(pool, service_id=sid)
            for old_key in [f"{prefix}_access_token", f"{prefix}_refresh_token", f"{prefix}_expires_at"]:
                existing.pop(old_key, None)
            secrets_path.write_text(
                json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
            logger.info("[claude-code] migrated single credential to pool for '%s'", sid)
            return pool
        except Exception:
            pass
    return []


def _save_credentials_pool(pool: list, service_id: str = ""):
    """Save the credentials pool to secrets (encrypted)."""
    from pathlib import Path
    from core.secrets import get_secrets_manager

    sid = _find_cc_service_id(service_id)
    if not sid:
        return
    sm = get_secrets_manager()
    prefix = sid.replace("-", "_")

    secrets_path = Path("config/global_secrets.json")
    secrets_path.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if secrets_path.exists():
        existing = json.loads(secrets_path.read_text(encoding="utf-8"))
    existing[f"{prefix}_credentials_pool"] = sm.encrypt(json.dumps(pool))
    secrets_path.write_text(
        json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("[claude-code] credentials pool (%d) persisted for '%s'", len(pool), sid)


def add_credential_to_pool(access_token: str, refresh_token: str,
                           expires_at, account: str = "",
                           service_id: str = ""):
    """Add a credential to the pool."""
    import time
    pool = _load_credentials_pool(service_id)
    # Dedup: if same refresh_token exists, update it (same account re-login)
    for i, existing in enumerate(pool):
        if existing.get("refresh_token") == refresh_token:
            pool[i] = {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "expires_at": int(expires_at),
                "account": account or existing.get("account", ""),
                "added_at": int(time.time()),
            }
            _save_credentials_pool(pool, service_id)
            logger.info("[claude-code] credential updated in pool (slot %d) for '%s'",
                        i, _find_cc_service_id(service_id))
            return
    pool.append({
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": int(expires_at),
        "account": account,
        "added_at": int(time.time()),
    })
    _save_credentials_pool(pool, service_id)
    logger.info("[claude-code] credential added to pool (now %d) for '%s'",
                len(pool), _find_cc_service_id(service_id))


def remove_credential_from_pool(index: int, service_id: str = "") -> bool:
    """Remove a credential from the pool by index (0-based)."""
    pool = _load_credentials_pool(service_id)
    if 0 <= index < len(pool):
        pool.pop(index)
        _save_credentials_pool(pool, service_id)
        return True
    return False


def reset_credentials_pool(service_id: str = ""):
    """Clear all credentials from the pool."""
    _save_credentials_pool([], service_id)


def _persist_tokens_to_service(access_token: str, refresh_token: str,
                               expires_at, service_id: str = "",
                               pool_index: int = -1):
    """Update a credential in the pool (after refresh).

    If pool_index >= 0, updates that specific slot. Otherwise finds
    the matching credential by access_token.
    """
    sid = _find_cc_service_id(service_id)
    if not sid:
        return
    pool = _load_credentials_pool(sid)
    if not pool:
        # No pool yet — create one
        add_credential_to_pool(access_token, refresh_token, expires_at,
                               service_id=sid)
        return

    if 0 <= pool_index < len(pool):
        pool[pool_index]["access_token"] = access_token
        pool[pool_index]["refresh_token"] = refresh_token
        pool[pool_index]["expires_at"] = int(expires_at)
    else:
        # Find by matching refresh_token (access_token changes on refresh)
        for cred in pool:
            if cred.get("refresh_token") == refresh_token:
                cred["access_token"] = access_token
                cred["expires_at"] = int(expires_at)
                break
        else:
            # Not found — update first credential (legacy compat)
            pool[0]["access_token"] = access_token
            pool[0]["refresh_token"] = refresh_token
            pool[0]["expires_at"] = int(expires_at)

    _save_credentials_pool(pool, sid)
    logger.info("[claude-code] credential updated in pool for '%s'", sid)


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

    # Class-level round-robin counter
    _pool_counter = 0
    _pool_lock = __import__('threading').Lock()

    def _resolve_service_tokens(self, pool_index: int = -1) -> dict:
        """Resolve Claude tokens from the credentials pool.

        If pool_index >= 0, returns that specific credential.
        Otherwise, round-robin through the pool.

        Returns {"access_token", "refresh_token", "expires_at", "pool_index"}
        """
        svc_id = getattr(self, '_agent_service', '') or ''
        pool = _load_credentials_pool(svc_id)
        if not pool:
            return {"access_token": "", "refresh_token": "", "expires_at": 0, "pool_index": -1}

        if 0 <= pool_index < len(pool):
            idx = pool_index
        else:
            with ClaudeCodeSessionMixin._pool_lock:
                idx = ClaudeCodeSessionMixin._pool_counter % len(pool)
                ClaudeCodeSessionMixin._pool_counter += 1
        cred = pool[idx]
        return {
            "access_token": cred.get("access_token", ""),
            "refresh_token": cred.get("refresh_token", ""),
            "expires_at": cred.get("expires_at", 0),
            "pool_index": idx,
        }

    @staticmethod
    def _refresh_oauth_token(refresh_token: str) -> dict:
        """Refresh OAuth token via Anthropic's platform endpoint.

        Returns {"access_token": ..., "refresh_token": ..., "expires_at": ... (ms)}
        """
        import http.client
        import ssl
        import time

        body = json.dumps({
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": "9d1c250a-e61b-44d9-88ed-5944d1962f5e",
        }).encode("utf-8")

        ctx = ssl.create_default_context()
        conn = http.client.HTTPSConnection("platform.claude.com", 443,
                                           context=ctx, timeout=15)
        try:
            conn.request("POST", "/v1/oauth/token", body=body, headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
            })
            resp = conn.getresponse()
            resp_body = resp.read().decode("utf-8")
        finally:
            conn.close()

        if resp.status != 200:
            raise RuntimeError(f"OAuth refresh failed ({resp.status}): {resp_body[:200]}")

        data = json.loads(resp_body)
        new_access = data.get("access_token", "")
        new_refresh = data.get("refresh_token", "")
        expires_in = data.get("expires_in", 0)
        expires_at_ms = time.time() * 1000 + expires_in * 1000

        if not new_access:
            raise RuntimeError(f"OAuth refresh returned no access_token: {resp_body[:200]}")

        return {
            "access_token": new_access,
            "refresh_token": new_refresh or refresh_token,
            "expires_at": int(expires_at_ms),
        }

    def _get_session_workdir(self, conversation_id: str,
                             agent_name: str = "",
                             user_id: str = "") -> str:
        """Get or create a dedicated working directory for this session.

        Path: data/claude_sessions/<user_id>/<conv_id>/<agent>/
        Falls back to data/claude_sessions/default/<conv_id>/<agent>/ if no user.
        """
        uid = user_id or getattr(self, '_user_id', '') or 'default'
        # Sanitize user_id for safe paths
        uid = uid.replace(':', '_').replace('/', '_').replace('\\', '_')
        cid = conversation_id or "default"
        # Sanitize: replace :: (used in sub-conv keys) with __ for safe paths
        cid = cid.replace(":", "_")
        agent = agent_name or "default"
        workdir = os.path.join(_SESSIONS_BASE, uid, cid, agent)
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

    def _setup_credentials(self, workdir: str, pool_index: int = -1):
        """Write .credentials.json in session workdir for Claude Code auth.

        Uses round-robin credential from pool, or specific pool_index for
        session resume (same credential that created the session).

        Raises LLMClientError if no credentials configured.
        """
        from core.llm_client import LLMClientError

        tokens = self._resolve_service_tokens(pool_index=pool_index)
        access_token = tokens["access_token"]
        refresh_token = tokens["refresh_token"]
        expires_at = tokens["expires_at"]
        _pidx = tokens["pool_index"]

        if not access_token:
            raise LLMClientError(
                "Claude Code credentials not configured. "
                "Use /cls to authenticate with your Claude subscription.")

        # Check expiry — refresh automatically if expired or near expiry (5min buffer)
        import time as _time
        if expires_at:
            _exp_s = int(expires_at) / 1000 if int(expires_at) > 1e12 else int(expires_at)
            _remaining = _exp_s - _time.time()
            if _remaining < 300 and refresh_token:  # 5min buffer, same as CC
                logger.info("OAuth token [pool:%d] %s — attempting refresh", _pidx,
                            "expired" if _remaining < 0 else f"expiring in {_remaining:.0f}s")
                try:
                    new_tokens = self._refresh_oauth_token(refresh_token)
                    access_token = new_tokens["access_token"]
                    refresh_token = new_tokens.get("refresh_token", refresh_token)
                    expires_at = new_tokens["expires_at"]
                    _svc_id = getattr(self, '_agent_service', '') or ''
                    _persist_tokens_to_service(
                        access_token, refresh_token, int(expires_at),
                        service_id=_svc_id, pool_index=_pidx)
                    logger.info("OAuth token [pool:%d] refreshed — expires in %.1fh",
                                _pidx, (int(expires_at)/1000 - _time.time()) / 3600)
                except Exception as e:
                    if _remaining < 0:
                        raise LLMClientError(
                            f"OAuth token [pool:{_pidx}] expired and refresh failed: {e}. "
                            "Use /cls to re-authenticate.")
                    else:
                        logger.warning("OAuth refresh failed [pool:%d] (still valid %.0fs): %s",
                                       _pidx, _remaining, e)

        # Store the pool index used for this session (for resume)
        self._current_pool_index = _pidx

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
        If tokens changed, update the correct pool slot.
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

            _pidx = getattr(self, '_current_pool_index', -1)
            _current = self._resolve_service_tokens(pool_index=_pidx)
            if new_access == _current.get("access_token", ""):
                return

            _service_id = getattr(self, '_agent_service', '') or ''
            _persist_tokens_to_service(
                new_access, new_refresh, new_expires,
                service_id=_service_id, pool_index=_pidx)
            logger.info("Recovered refreshed tokens [pool:%d] for '%s'", _pidx, _service_id)
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

        When containerize=True, uses the pool (docker exec) or falls back
        to docker run if pool is disabled.
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

        # Docker pool mode: acquire container, store args for exec
        self._pool_claude_args = claude_args
        # _pool_container_name is set by the caller (_stream_claude_code)
        # which calls pool.acquire() and pool.exec_claude()
        return claude_args  # just the claude args, caller handles docker exec
