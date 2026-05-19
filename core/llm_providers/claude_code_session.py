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
        # Mirror the listener's scheme: HTTPS when SSL is configured,
        # HTTP otherwise. For HTTPS, prefer the hostname the cert was
        # issued for (via listener.public_hostname) over the bare LAN
        # IP. Previously we used get_host_ip() which returns the LAN IP,
        # producing URLs like `https://10.13.13.13:9090/…` that hit the
        # listener OK at the TCP layer but caused the upstream HTTP
        # path (TLS cert CN mismatch, Node fetch bailing out on an IP
        # target with self-signed cert, or some middleware routing by
        # Host) to bail silently with "API returned an empty or
        # malformed response (HTTP 200)". Using the cert's hostname
        # sidesteps CN validation entirely — the container resolves
        # the hostname to the same LAN IP via its own DNS/hosts, but
        # the cert matches. For plain HTTP we keep the LAN IP (no cert
        # to match) since a custom hostname would require container-
        # side DNS setup we don't manage.
        _is_ssl = bool(getattr(_listener, "is_ssl", False))
        _scheme = "https" if _is_ssl else "http"
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

    if _is_ssl:
        _host = (getattr(_listener, "public_hostname", "") or "").strip()
        if not _host:
            # No hostname found on the cert — fall back to LAN IP. The
            # container will need NODE_TLS_REJECT_UNAUTHORIZED=0 to
            # accept the CN/SAN mismatch (set in _claude_code_env).
            _host = get_host_ip()
            logger.warning(
                "relay-proxy URL using LAN IP %s with HTTPS: cert CN "
                "validation will be skipped in the container. Configure "
                "`public_hostname` on the HTTP listener (or a matching "
                "SNI cert) so the container can verify normally.",
                _host)
    else:
        _host = get_host_ip()
    _target = f"{target_host}:{target_port}"
    _path = target_path
    _s_prefix = "s/" if target_scheme == "https" else ""
    return f"{_scheme}://{_host}:{_port}/relay-proxy/{relay_id}/{_token}/{_s_prefix}{_target}{_path}"


def _find_cc_service_id(service_id: str = "") -> str:
    """Find the credential-pool owner for Claude Code OAuth."""
    try:
        from services.llm_credential_oauth import resolve_credential_service_id
        return resolve_credential_service_id("claude-code", service_id)
    except Exception:
        return service_id or ""


def _load_credentials_pool(service_id: str = "") -> list:
    """Load the credentials pool for a CC service.

    Returns list of {"access_token", "refresh_token", "expires_at", "account", "added_at"}.
    """
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
    pool_key = f"{prefix}_credentials_pool"
    if pool_key not in existing:
        return []
    try:
        return json.loads(sm.decrypt(existing[pool_key]))
    except Exception:
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


def _validate_oauth_token(access_token: str, refresh_token: str,
                           expires_at) -> bool:
    """Sanity check: never persist a token we can already see is broken.

    - access_token + refresh_token must be non-empty strings
    - expires_at must be a number in the future (handles both seconds
      and milliseconds — Anthropic uses ms).
    """
    import time as _t
    if not access_token or not isinstance(access_token, str):
        return False
    if not refresh_token or not isinstance(refresh_token, str):
        return False
    try:
        _exp = int(expires_at)
    except (TypeError, ValueError):
        return False
    # Accept both sec and ms. If value > 1e12, it's ms; otherwise sec.
    _exp_s = _exp / 1000 if _exp > 1e12 else _exp
    return _exp_s > _t.time()


def _persist_tokens_to_service(access_token: str, refresh_token: str,
                               expires_at, service_id: str = "",
                               pool_index: int = -1):
    """Update a credential in the pool (after refresh).

    If pool_index >= 0, updates that specific slot. Otherwise finds
    the matching credential by access_token.

    Refuses to persist a token that fails basic validation (empty
    fields, expires_at in the past) — better to keep the old broken
    token and let _setup_credentials drop the slot than to pollute
    the pool with a token we already know is dead.
    """
    if not _validate_oauth_token(access_token, refresh_token, expires_at):
        logger.warning(
            "[claude-code] refusing to persist invalid token "
            "(access_token=%r, expires_at=%r) to pool[%s] — keeping old",
            bool(access_token), expires_at, pool_index)
        return
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
            return {"access_token": "", "refresh_token": "", "expires_at": 0, "pool_index": -1}  # nosec B105

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

    def _force_refresh_pool_entry(self, pool_index: int) -> bool:
        """Force-refresh the access_token for pool[pool_index] without
        regard to its expiry. Used on mid-stream auth failures where the
        access_token was rejected but the refresh_token may still be valid.

        If the refresh fails OR returns an invalid token, REMOVES the
        slot from the pool entirely — a credential whose refresh is
        broken is dead to us and must not be re-attempted.

        Returns True if the refresh succeeded and the pool slot was
        updated with a validated token; False otherwise.
        """
        svc_id = getattr(self, '_agent_service', '') or ''
        pool = _load_credentials_pool(svc_id)
        if pool_index < 0 or pool_index >= len(pool):
            return False
        refresh_token = pool[pool_index].get("refresh_token", "")

        def _drop_dead_slot(reason: str):
            current = _load_credentials_pool(svc_id)
            if 0 <= pool_index < len(current):
                dead = current.pop(pool_index)
                _save_credentials_pool(current, service_id=svc_id)
                logger.warning(
                    "[force-refresh] removed dead pool[%d] (reason: %s); "
                    "remaining=%d", pool_index, reason, len(current))
                return dead
            return None

        if not refresh_token:
            _drop_dead_slot("no refresh_token")
            return False
        try:
            tokens = self._refresh_oauth_token(refresh_token)
        except Exception as e:
            logger.warning("[force-refresh] pool[%d] refresh call failed: %s",
                           pool_index, e)
            _drop_dead_slot(f"refresh error: {e}")
            return False
        _new_at = tokens.get("access_token", "")
        _new_rt = tokens.get("refresh_token", refresh_token)
        _new_exp = int(tokens.get("expires_at", 0) or 0)
        if not _validate_oauth_token(_new_at, _new_rt, _new_exp):
            logger.warning(
                "[force-refresh] pool[%d] returned invalid token "
                "(access_token=%r, expires_at=%r)",
                pool_index, bool(_new_at), _new_exp)
            _drop_dead_slot("refresh returned invalid token")
            return False
        _persist_tokens_to_service(
            _new_at, _new_rt, _new_exp,
            service_id=svc_id, pool_index=pool_index)
        logger.info("[force-refresh] pool[%d] access_token renewed", pool_index)
        return True

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
            # CC runs inside a Docker container — any localhost / 127.0.0.1
            # URL must be translated to host.docker.internal so the
            # container can reach the host-side listener. Relay-proxy URLs
            # already point at the in-container MCP bridge; leave untouched.
            if "/relay-proxy/" not in _base_url:
                import re
                _repl = lambda m: m.group(1) + "host.docker.internal" + (m.group(2) or '')
                _base_url = re.sub(
                    r'(https?://)localhost(:\d+)?', _repl, _base_url)
                _base_url = re.sub(
                    r'(https?://)127\.0\.0\.1(:\d+)?', _repl, _base_url)
            env["ANTHROPIC_BASE_URL"] = _base_url
            # HTTPS relay-proxy URLs hit PawFlow via a LAN IP whose
            # self-signed cert isn't in the container's trust store.
            # Claude CLI is Node-based and honours NODE_TLS_REJECT_UNAUTHORIZED=0
            # to skip verification — safe here because the request
            # stays on the private network (private_only route) and
            # the ephemeral token is the real credential, not TLS.
            _tls_skip = (_base_url.startswith("https://")
                         and "/relay-proxy/" in _base_url)
            if _tls_skip:
                env["NODE_TLS_REJECT_UNAUTHORIZED"] = "0"
            logger.info(
                "Claude Code using custom endpoint: %s (NODE_TLS_REJECT_UNAUTHORIZED=%s)",
                _base_url, "0" if _tls_skip else "unset")
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
            from services.http_listener_service import HTTPListenerService
            reg = ServiceRegistry.get_instance()

            # Main HTTP listener port — the relay registers a route on it
            # rather than binding its own dedicated port (post-refactor).
            # Without a listener running, there's nothing to register on.
            listener_instances = HTTPListenerService.all_instances()
            if not listener_instances:
                logger.error(
                    "[tool-relay] no HTTPListenerService running — "
                    "Claude Code MCP bridge cannot reach PawFlow tools "
                    "until the main listener is up.")
                return "", ""
            main_port = next(iter(listener_instances.keys()))

            # wss:// — the main listener force-redirects plain HTTP to
            # HTTPS when TLS is configured (correct behaviour), so even
            # on the internal hop we stay on TLS. Auth is handled by the
            # ephemeral pawflow_internal cookie (see core/internal_auth.py)
            # plus the tool relay's own register-step token.
            def _build_url(service_id: str) -> str:
                return f"wss://localhost:{main_port}/ws/tools/{service_id}"

            # Reuse a live tool relay from this run if present.
            for sdef in reg.resolve_by_type("toolRelay"):
                svc = reg.get_live_instance(sdef.scope, sdef.scope_id, sdef.service_id)
                if svc:
                    cfg = getattr(sdef, "config", {}) or {}
                    token = cfg.get("token", "")
                    _sid = cfg.get("_service_id", sdef.service_id)
                    if token and _sid:
                        cls._tool_relay_cache = (_build_url(_sid), token)
                        return cls._tool_relay_cache
                # Stale from a previous run — remove it.
                try:
                    reg.uninstall(sdef.scope, sdef.scope_id, sdef.service_id)
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

            # Create a fresh tool relay. ToolRelayService registers its
            # WS route on the main HTTPListenerService at
            # /ws/tools/{service_id} — no separate port or path.
            import uuid
            token = uuid.uuid4().hex
            service_id = "_tool_relay"
            reg.install(SCOPE_GLOBAL, "", service_id=service_id,
                        service_type="toolRelay", config={
                "token": token,
                "_service_id": service_id,
            }, description="Auto-created tool relay for Claude Code MCP bridge")
            svc = reg.get_live_instance(SCOPE_GLOBAL, "", service_id)
            if svc:
                logger.info(
                    "[tool-relay] registered route /ws/tools/%s on main "
                    "listener port %d",
                    service_id, main_port)
                cls._tool_relay_cache = (_build_url(service_id), token)
                return cls._tool_relay_cache
        except Exception as e:
            logger.error("Failed to get/create tool relay: %s", e)
        return "", ""

    # Proactively refresh OAuth tokens that have less than this many
    # seconds of validity left, so we rarely hit mid-stream 'Not logged
    # in' failures (refresh is quick, mid-turn death is catastrophic).
    _OAUTH_REFRESH_MIN_TTL_SEC = 30 * 60

    def _setup_credentials(self, workdir: str, pool_index: int = -1,
                            exclude_indices=None):
        """Write .credentials.json in session workdir for Claude Code auth.

        If ANTHROPIC_API_KEY is set (via api_key config), skips OAuth
        credentials entirely — CC uses the API key directly.

        Otherwise tries credentials from the pool. If a credential is
        expired OR will expire within _OAUTH_REFRESH_MIN_TTL_SEC, attempts
        a proactive refresh. If refresh fails, removes it from the pool
        and tries the next. exclude_indices skips pool slots that have
        already failed during the current stream (set by the retry loop
        after a mid-stream auth error).

        Only raises if NO valid credential can be obtained.
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

        _exclude = set(exclude_indices or ())

        # Build ordered list of indices to try
        if 0 <= pool_index < len(pool) and pool_index not in _exclude:
            indices = [pool_index] + [
                i for i in range(len(pool))
                if i != pool_index and i not in _exclude]
        else:
            # Round-robin start, then try all others
            with ClaudeCodeSessionMixin._pool_lock:
                start = ClaudeCodeSessionMixin._pool_counter % len(pool)
                ClaudeCodeSessionMixin._pool_counter += 1
            indices = [
                (start + i) % len(pool) for i in range(len(pool))
                if (start + i) % len(pool) not in _exclude]
        if not indices:
            raise LLMClientError(
                "All Claude Code credentials in the pool have already "
                "failed during this stream. Use /cls to re-authenticate.")

        dead_indices = []
        for _pidx in indices:
            cred = pool[_pidx]
            access_token = cred.get("access_token", "")
            refresh_token = cred.get("refresh_token", "")
            expires_at = cred.get("expires_at", 0)

            if not access_token:
                dead_indices.append(_pidx)
                continue

            # Proactive refresh: if the token is expired OR will expire
            # inside _OAUTH_REFRESH_MIN_TTL_SEC, refresh now so a
            # long-running agent turn doesn't hit 'Not logged in'
            # half-way through.
            if expires_at:
                _exp_s = int(expires_at) / 1000 if int(expires_at) > 1e12 else int(expires_at)
                _remaining = _exp_s - _time.time()
                if _remaining < self._OAUTH_REFRESH_MIN_TTL_SEC and refresh_token:
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
                          agent_name: str = "") -> tuple:
        """Write MCP config to workdir.

        Returns (mcp_path, internal_token). The caller owns the lifecycle of
        ``internal_token`` and MUST pass it to ``core.internal_auth.revoke_token``
        once the CC invocation ends (success or failure), otherwise the token
        lingers valid in memory until server restart.
        """
        # CC runs inside a Docker container — the MCP bridge script is
        # bind-mounted at /opt/pawflow/mcp_bridge.py (see pool._spawn_container).
        mcp_bridge = "/opt/pawflow/mcp_bridge.py"
        python_bin = "python3"

        relay_url, relay_token = self._get_tool_relay_info()
        if not relay_url:
            logger.warning("No toolRelay service — MCP bridge will have no tools")

        # Replace localhost with the host IP reachable from the container.
        if relay_url:
            from core.docker_utils import get_host_ip
            _host_ip = get_host_ip()
            relay_url = relay_url.replace("localhost", _host_ip)
            relay_url = relay_url.replace("127.0.0.1", _host_ip)

        # Server-spawned CC container has no user session cookies. Mint a
        # fresh internal-auth token scoped to this call: the MCP bridge
        # sends it in the WS upgrade Cookie, and the listener bypasses the
        # private-gateway + session checks for /ws/tools/* on valid tokens.
        # Regenerated on every spawn/config write, TTL-bound, in-memory only.
        from core.internal_auth import mint_token
        internal_token = mint_token()

        config = {
            "mcpServers": {
                "pawflow": {
                    "command": python_bin,
                    "args": [mcp_bridge],
                    "env": {
                        "PAWFLOW_TOOL_RELAY_URL": relay_url,
                        "PAWFLOW_TOOL_RELAY_TOKEN": relay_token,
                        "PAWFLOW_INTERNAL_TOKEN": internal_token,
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
        return mcp_path, internal_token

    # All built-in Claude Code tools that must be disabled
    # (server filesystem != user's filesystem — everything goes through MCP).
    # Monitor is included because its stdout-log file lives on the agent
    # sandbox (/tmp/claude-*/tasks/*.output) and is invisible to the
    # relay-mediated MCP tools; use mcp__pawflow__use_tool(bash,
    # run_in_background=true) for long-running commands instead.
    # ScheduleWakeup and PushNotification are replaced by pawflow-native
    # MCP tools of the same name (see core.handlers.file_ops and
    # core.handlers.push_notification). The built-ins are blocked so the
    # agent never falls through — it calls mcp__pawflow__use_tool(
    # ScheduleWakeup, ...) / (PushNotification, ...) instead.
    _DISALLOWED_BUILTIN_TOOLS = (
        "Bash,Edit,Read,Write,Glob,Grep,NotebookEdit,WebFetch,WebSearch,"
        "Task,Agent,ToolSearch,ListMcpResourcesTool,ReadMcpResourceTool,"
        "EnterPlanMode,ExitPlanMode,EnterWorktree,ExitWorktree,"
        "RemoteTrigger,Skill,TaskOutput,TaskStop,TodoWrite,"
        "CronCreate,CronDelete,CronList,AskUserQuestion,Monitor,"
        "ScheduleWakeup,PushNotification"
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

        CC always runs inside a pool-managed Docker container (1 CC per
        container); `_pool_popen` injects docker exec and the setsid /
        unshare wrapper around this arg list.
        """
        claude_args = ["-p"]
        if session_id:
            claude_args.extend(["--resume", session_id])
        claude_args.extend([
            "--input-format", "stream-json",
            "--output-format", "stream-json",
        ])
        model = (model or "").strip()
        if model:
            claude_args.extend(["--model", model])
        claude_args.extend([
            "--dangerously-skip-permissions",
            "--max-turns", "1000",
            "--verbose",
            "--thinking-display", "summarized",
            "--strict-mcp-config",
        ])
        _effort = self._cfg("effort", "")
        if _effort:
            claude_args.extend(["--effort", _effort])
        if mcp_config_path:
            claude_args.extend(["--mcp-config", mcp_config_path])
        # disallowedTools LAST — variadic option, must not consume other flags
        claude_args.extend(["--disallowedTools", self._DISALLOWED_BUILTIN_TOOLS])

        # Container is acquired and exec'd by the caller via _pool_popen /
        # pool.exec_claude; we just return the CLI args.
        self._pool_claude_args = claude_args
        return claude_args
