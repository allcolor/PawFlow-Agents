"""Claude Code session/workdir management mixin.

Extracted from claude_code.py — handles credentials, MCP config,
session directories, and CLI command building.
"""

import json
import logging
import os
from typing import Optional

from core.docker_utils import docker_cmd as _docker_cmd, to_host_path, get_host_ip

logger = logging.getLogger(__name__)


def _maybe_transform_relay_proxy_url(url: str, user_id: str = "") -> Optional[str]:
    """Detect the relay-proxy format and transform to a PawFlow proxy URL.

    Input format:  http(s)://<relay_id>:<host>:<port>/path
    Output format: <pawflow_scheme>://<pawflow_host>:<pawflow_port>/relay-proxy/<relay_id>/<token>/[s/]<host>:<port>/path

    An ephemeral token bound to (user_id, relay_id) is minted and injected
    into the URL — the CC container has no HTTP session and cannot carry
    auth cookies. The token, not the relay_id, is the actual credential;
    the route handler rejects external IPs even if the URL leaks.

    The 's/' prefix in the path indicates the target uses HTTPS.
    Returns None if the URL is not a relay-proxy URL.
    """
    import re
    m = re.match(
        r'^(https?)://([A-Za-z0-9_.\-]+):([A-Za-z0-9_.\-]+):(\d+)(/.*)?$', url)
    if not m:
        return None
    target_scheme = m.group(1)
    relay_id = m.group(2)
    target_host = m.group(3)
    target_port = int(m.group(4))
    target_path = m.group(5) or '/'

    # Locate the PawFlow HTTP listener to expose the proxy route
    try:
        from services import http_listener_service as _hl_mod
        instances = getattr(_hl_mod, "_instances", None) or {}
        if not instances:
            logger.warning("No HTTP listener running — cannot build relay-proxy URL")
            return None
        # Prefer the public listener (highest port that's not internal :19895)
        _public = [(p, lst) for p, lst in instances.items() if p != 19895]
        if _public:
            _port, _listener = _public[0]
        else:
            _port, _listener = next(iter(instances.items()))
        # Always HTTP for the proxy URL: the listener supports both HTTP+HTTPS
        # but the cert may be issued for a hostname (e.g. pawflow.allcolor.org)
        # that doesn't match the LAN IP we expose. Traffic stays on the
        # private network (private_only route restriction + token auth).
        _scheme = "http"
    except Exception as e:
        logger.warning("HTTP listener lookup failed: %s", e)
        return None

    if not user_id:
        logger.warning("Cannot issue proxy token without user_id")
        return None
    try:
        from core.relay_proxy_auth import issue_token
        _token = issue_token(user_id, relay_id)
    except Exception as e:
        logger.warning("Proxy token issue failed: %s", e)
        return None

    _host = get_host_ip()
    _target = f"{target_host}:{target_port}"
    _path = target_path
    _s_prefix = "s/" if target_scheme == "https" else ""
    return f"{_scheme}://{_host}:{_port}/relay-proxy/{relay_id}/{_token}/{_s_prefix}{_target}{_path}"


def _find_cc_service_id(service_id: str = "") -> str:
    """Find the claude-code LLM service ID."""
    if service_id:
        return service_id
    try:
        from core.service_registry import ServiceRegistry
        for sdef in ServiceRegistry.get_instance().resolve_by_type("llmConnection"):
            cfg = getattr(sdef, "config", {}) or {}
            if cfg.get("provider") == "claude-code":
                return sdef.service_id
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

    from core.paths import GLOBAL_SECRETS_FILE
    secrets_path = GLOBAL_SECRETS_FILE
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

    from core.paths import GLOBAL_SECRETS_FILE
    secrets_path = GLOBAL_SECRETS_FILE
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


# Base directory for per-session Claude Code workdirs — read dynamically
def _get_sessions_base():
    import core.paths as _p
    return str(_p.CLAUDE_SESSIONS_DIR.resolve())


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
        import time as _t
        svc_id = getattr(self, '_agent_service', '') or ''
        pool = _load_credentials_pool(svc_id)
        if not pool:
            return {"access_token": "", "refresh_token": "", "expires_at": 0, "pool_index": -1}

        # Purge expired credentials (expires_at is in milliseconds)
        now_ms = int(_t.time() * 1000)
        valid = [(i, c) for i, c in enumerate(pool) if c.get("expires_at", 0) > now_ms]
        if not valid and pool:
            # All expired — try the most recent one (might still refresh)
            valid = [(len(pool) - 1, pool[-1])]

        if 0 <= pool_index < len(pool) and pool[pool_index].get("expires_at", 0) > now_ms:
            idx = pool_index
        else:
            with ClaudeCodeSessionMixin._pool_lock:
                pick = ClaudeCodeSessionMixin._pool_counter % len(valid)
                ClaudeCodeSessionMixin._pool_counter += 1
                idx = valid[pick][0]
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
        uid = user_id or getattr(self, '_user_id', '')
        if not uid:
            raise ValueError("BUG: user_id required for CC session workdir")
        cid = conversation_id
        if not cid:
            raise ValueError("BUG: conversation_id required for CC session workdir")
        if not agent_name:
            raise ValueError("BUG: agent_name required for CC session workdir")
        # Sanitize for safe paths
        uid = uid.replace(':', '_').replace('/', '_').replace('\\', '_')
        cid = cid.replace(":", "_")
        agent = agent_name
        workdir = os.path.join(_get_sessions_base(), uid, cid, agent)
        os.makedirs(workdir, exist_ok=True)
        return workdir

    def _claude_code_env(self, workdir: str = "") -> dict:
        """Build environment for claude subprocess.

        Sets CLAUDE_CONFIG_DIR to the session workdir so Claude Code
        reads credentials from our managed .credentials.json instead
        of the user's ~/.claude/.credentials.json.

        If the llm_service has api_key/base_url configured, passes them
        as ANTHROPIC_API_KEY/ANTHROPIC_BASE_URL so CC uses the API
        instead of OAuth credentials. This enables routing CC to any
        compatible endpoint (Ollama, llama.cpp, vLLM, etc.).
        """
        env = os.environ.copy()
        if workdir:
            env["CLAUDE_CONFIG_DIR"] = workdir
        # API key mode: bypass OAuth credentials entirely
        _api_key = getattr(self, 'api_key', '')
        if callable(_api_key):
            _api_key = _api_key()
        elif isinstance(_api_key, property):
            _api_key = ''
        if _api_key:
            env["ANTHROPIC_API_KEY"] = _api_key
        # Custom endpoint: route CC to any Anthropic-compatible server
        _base_url = getattr(self, 'base_url', '')
        if callable(_base_url):
            _base_url = _base_url()
        elif isinstance(_base_url, property):
            _base_url = ''
        # base_url is already transformed (relay-proxy) by LLMClient.base_url
        # property. If still a localhost URL in Docker, translate to
        # host.docker.internal so the container can reach the host.
        if _base_url:
            if getattr(self, 'containerize', False) and "/relay-proxy/" not in _base_url:
                import re
                _repl = lambda m: m.group(1) + "host.docker.internal" + (m.group(2) or '')
                _base_url = re.sub(
                    r'(https?://)localhost(:\d+)?', _repl, _base_url)
                _base_url = re.sub(
                    r'(https?://)127\.0\.0\.1(:\d+)?', _repl, _base_url)
            env["ANTHROPIC_BASE_URL"] = _base_url
            logger.info("Claude Code using custom endpoint: %s", _base_url)
        else:
            logger.info("Claude Code no custom base_url configured")
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
            from core.service_registry import ServiceRegistry, SCOPE_GLOBAL
            reg = ServiceRegistry.get_instance()

            # Check if a live tool relay already exists (from this server run)
            for sdef in reg.resolve_by_type("toolRelay"):
                svc = reg.get_live_instance(sdef.scope, sdef.scope_id, sdef.service_id)
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
                    reg.uninstall(sdef.scope, sdef.scope_id, sdef.service_id)
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
            reg.install(SCOPE_GLOBAL, "", service_id=service_id,
                        service_type="toolRelay", config={
                "port": free_port,
                "path": "/ws/tools",
                "token": token,
                "_service_id": service_id,
            }, description="Auto-created tool relay for Claude Code MCP bridge")
            svc = reg.get_live_instance(SCOPE_GLOBAL, "", service_id)
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

        If ANTHROPIC_API_KEY is set (via api_key config), skips OAuth
        credentials entirely — CC uses the API key directly.

        Otherwise tries credentials from the pool. If a credential is
        expired, attempts refresh. If refresh fails, removes it from the
        pool and tries the next. Only raises if NO valid credential can
        be obtained.
        """
        from core.llm_client import LLMClientError
        import time as _time

        # API key mode: no OAuth credentials needed
        _api_key = getattr(self, 'api_key', '')
        if callable(_api_key):
            _api_key = _api_key()
        elif isinstance(_api_key, property):
            _api_key = ''
        if _api_key:
            logger.info("Claude Code using API key (skipping OAuth credentials)")
            return

        svc_id = getattr(self, '_agent_service', '') or ''
        pool = _load_credentials_pool(svc_id)
        if not pool:
            raise LLMClientError(
                "Claude Code credentials not configured. "
                "Use /cls to authenticate with your Claude subscription.")

        # Build ordered list of indices to try
        if 0 <= pool_index < len(pool):
            indices = [pool_index] + [i for i in range(len(pool)) if i != pool_index]
        else:
            # Round-robin start, then try all others
            with ClaudeCodeSessionMixin._pool_lock:
                start = ClaudeCodeSessionMixin._pool_counter % len(pool)
                ClaudeCodeSessionMixin._pool_counter += 1
            indices = [(start + i) % len(pool) for i in range(len(pool))]

        dead_indices = []
        for _pidx in indices:
            cred = pool[_pidx]
            access_token = cred.get("access_token", "")
            refresh_token = cred.get("refresh_token", "")
            expires_at = cred.get("expires_at", 0)

            if not access_token:
                dead_indices.append(_pidx)
                continue

            # Check expiry — refresh if expired or near expiry (5min buffer)
            if expires_at:
                _exp_s = int(expires_at) / 1000 if int(expires_at) > 1e12 else int(expires_at)
                _remaining = _exp_s - _time.time()
                if _remaining < 300 and refresh_token:
                    logger.info("OAuth token [pool:%d] %s — attempting refresh", _pidx,
                                "expired" if _remaining < 0 else f"expiring in {_remaining:.0f}s")
                    try:
                        new_tokens = self._refresh_oauth_token(refresh_token)
                        access_token = new_tokens["access_token"]
                        refresh_token = new_tokens.get("refresh_token", refresh_token)
                        expires_at = new_tokens["expires_at"]
                        _persist_tokens_to_service(
                            access_token, refresh_token, int(expires_at),
                            service_id=svc_id, pool_index=_pidx)
                        logger.info("OAuth token [pool:%d] refreshed — expires in %.1fh",
                                    _pidx, (int(expires_at)/1000 - _time.time()) / 3600)
                    except Exception as e:
                        logger.warning("OAuth token [pool:%d] refresh failed, removing: %s",
                                       _pidx, e)
                        dead_indices.append(_pidx)
                        continue
                elif _remaining < 0 and not refresh_token:
                    logger.warning("OAuth token [pool:%d] expired, no refresh token", _pidx)
                    dead_indices.append(_pidx)
                    continue

            # This credential works — use it
            self._current_pool_index = _pidx

            # Clean up dead credentials
            if dead_indices:
                pool = [c for i, c in enumerate(pool) if i not in dead_indices]
                _save_credentials_pool(pool, service_id=svc_id)
                logger.info("Removed %d dead credential(s) from pool, %d remaining",
                            len(dead_indices), len(pool))
            break
        else:
            # All credentials failed
            pool = [c for i, c in enumerate(pool) if i not in dead_indices]
            _save_credentials_pool(pool, service_id=svc_id)
            raise LLMClientError(
                "All Claude Code credentials expired and refresh failed. "
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
        claude_args = ["-p"]
        if session_id:
            claude_args.extend(["--resume", session_id])
        claude_args.extend([
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--model", model or "sonnet",
            "--dangerously-skip-permissions",
            "--max-turns", "1000",
            "--verbose",
            "--strict-mcp-config",
        ])
        _effort = self._cfg("effort", "")
        if _effort:
            claude_args.extend(["--effort", _effort])
        if mcp_config_path:
            claude_args.extend(["--mcp-config", mcp_config_path])
        # disallowedTools LAST — variadic option, must not consume other flags
        claude_args.extend(["--disallowedTools", self._DISALLOWED_BUILTIN_TOOLS])

        if not getattr(self, 'containerize', False):
            return [self.claude_binary] + claude_args

        # Docker pool mode: acquire container, store args for exec
        self._pool_claude_args = claude_args
        # _pool_container_name is set by the caller (_stream_claude_code)
        # which calls pool.acquire() and pool.exec_claude()
        return claude_args  # just the claude args, caller handles docker exec
