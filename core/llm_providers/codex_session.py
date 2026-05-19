"""OAuth credentials pool + refresh for the Codex CLI provider.

Mirror of claude_code_session.py's credential helpers, but pointed at the
OpenAI Auth0 OAuth endpoint and Codex provider type. Kept in its own file
rather than mutualized with the Claude Code helpers because the two CLIs
evolve independently — see memory "Separate pools per CLI".

The Codex CLI stores OAuth credentials in ~/.codex/auth.json (plaintext)
or in the OS keyring. We keep an encrypted server-side pool in the
GLOBAL_SECRETS_FILE under the key '<service_id>_credentials_pool'. The
format matches the Claude Code pool layout for symmetry:
  [{access_token, refresh_token, expires_at (ms), account, added_at}]
so the same pool-management UI / round-robin picker can be reused.
"""

import json
import logging
import os
import time
from typing import Optional

from core.agent_prompt_policy import CLI_MCP_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

# Auth0 endpoint used by the Codex CLI for the OAuth PKCE refresh flow.
# Discovered via the publicly documented behaviour of `codex login` and
# the openai/codex source. Refresh body is RFC 6749 standard.
_CODEX_TOKEN_ENDPOINT_HOST = "auth.openai.com"  # nosec B105
_CODEX_TOKEN_ENDPOINT_PATH = "/oauth/token"  # nosec B105
# The Codex CLI client_id, embedded in the binary. Picked up from the
# documented OAuth PKCE flow; if OpenAI rotate this we'll see refresh
# 401 + need to update the constant. Kept here (not env) because the
# token endpoint will reject a wrong client_id outright.
_CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"


def _find_codex_service_id(service_id: str = "") -> str:
    """Find the credential-pool owner for Codex OAuth."""
    try:
        from services.llm_credential_oauth import resolve_credential_service_id
        return resolve_credential_service_id("codex-app-server", service_id)
    except Exception:
        return service_id or ""


def _load_credentials_pool(service_id: str = "") -> list:
    """Load the credentials pool for a codex service."""
    from core.secrets import get_secrets_manager
    sid = _find_codex_service_id(service_id)
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
    from core.secrets import get_secrets_manager
    sid = _find_codex_service_id(service_id)
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
    logger.info("[codex] credentials pool (%d) persisted for '%s'", len(pool), sid)


def add_credential_to_pool(access_token: str, refresh_token: str,  # nosec B107
                            expires_at, account: str = "",
                            service_id: str = "",
                            id_token: str = ""):
    """Add (or update on refresh_token match) a credential.

    `id_token` is the OAuth ID JWT codex expects in auth.json for the
    ChatGPT account claim — codex CLI rejects auth.json without a valid
    id_token ("invalid ID token format"). Stored alongside access/refresh
    so we can reproduce the exact auth.json shape codex wrote on disk.
    """
    pool = _load_credentials_pool(service_id)
    for i, existing in enumerate(pool):
        if existing.get("refresh_token") == refresh_token:
            pool[i] = {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "id_token": id_token or existing.get("id_token", ""),
                "expires_at": int(expires_at),
                "account": account or existing.get("account", ""),
                "added_at": int(time.time()),
            }
            _save_credentials_pool(pool, service_id)
            return
    pool.append({
        "access_token": access_token,
        "refresh_token": refresh_token,
        "id_token": id_token,
        "expires_at": int(expires_at),
        "account": account,
        "added_at": int(time.time()),
    })
    _save_credentials_pool(pool, service_id)
    logger.info("[codex] credential added to pool (now %d) for '%s'",
                len(pool), _find_codex_service_id(service_id))


def remove_credential_from_pool(index: int, service_id: str = "") -> bool:
    pool = _load_credentials_pool(service_id)
    if 0 <= index < len(pool):
        pool.pop(index)
        _save_credentials_pool(pool, service_id)
        return True
    return False


def reset_credentials_pool(service_id: str = ""):
    _save_credentials_pool([], service_id)


def _validate_oauth_token(access_token: str, refresh_token: str,
                          expires_at) -> bool:
    if not access_token or not isinstance(access_token, str):
        return False
    if not refresh_token or not isinstance(refresh_token, str):
        return False
    try:
        _exp = int(expires_at)
    except (TypeError, ValueError):
        return False
    _exp_s = _exp / 1000 if _exp > 1e12 else _exp
    return _exp_s > time.time()


def refresh_oauth_token(refresh_token: str) -> dict:
    """Refresh OAuth access token via OpenAI's Auth0 token endpoint.

    Returns {"access_token": ..., "refresh_token": ..., "expires_at": ... (ms)}.
    Raises RuntimeError on any non-200 response or empty token.
    """
    import http.client
    import ssl

    body = json.dumps({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": _CODEX_CLIENT_ID,
    }).encode("utf-8")
    ctx = ssl.create_default_context()
    conn = http.client.HTTPSConnection(
        _CODEX_TOKEN_ENDPOINT_HOST, 443, context=ctx, timeout=15)
    try:
        conn.request("POST", _CODEX_TOKEN_ENDPOINT_PATH, body=body, headers={
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
        })
        resp = conn.getresponse()
        resp_body = resp.read().decode("utf-8")
    finally:
        conn.close()

    if resp.status != 200:
        raise RuntimeError(
            f"Codex OAuth refresh failed ({resp.status}): {resp_body[:200]}")
    data = json.loads(resp_body)
    new_access = data.get("access_token", "")
    new_refresh = data.get("refresh_token", "")
    expires_in = data.get("expires_in", 0)
    expires_at_ms = time.time() * 1000 + expires_in * 1000
    if not new_access:
        raise RuntimeError(
            f"Codex OAuth refresh returned no access_token: {resp_body[:200]}")
    return {
        "access_token": new_access,
        "refresh_token": new_refresh or refresh_token,
        "expires_at": int(expires_at_ms),
    }


def parse_auth_json(auth_json_text: str) -> dict:
    """Parse a ~/.codex/auth.json blob and return the (access, refresh,
    expires_at) tuple-as-dict that PawFlow's pool wants.

    The codex auth.json shape is:
      {
        "OPENAI_API_KEY": "<may be empty>",
        "tokens": {
          "id_token": "...",
          "access_token": "...",
          "refresh_token": "...",
          "account_id": "..."
        },
        "last_refresh": "<iso8601>"
      }
    The CLI does not store the access_token TTL in auth.json, so we use a
    conservative 1-hour default for expires_at; the first refresh on use
    will replace it with the real value from the token endpoint.
    """
    try:
        d = json.loads(auth_json_text)
    except (json.JSONDecodeError, TypeError):
        return {}
    tokens = d.get("tokens", {}) or {}
    return {
        "access_token": tokens.get("access_token", "") or "",
        "refresh_token": tokens.get("refresh_token", "") or "",
        "id_token": tokens.get("id_token", "") or "",
        "expires_at": int((time.time() + 3600) * 1000),
        "account": tokens.get("account_id", "") or "",
        "openai_api_key": d.get("OPENAI_API_KEY", "") or "",
    }


def _persist_tokens_to_service(access_token: str, refresh_token: str,  # nosec B107
                                expires_at, service_id: str = "",
                                pool_index: int = -1,
                                id_token: str = "",
                                account: str = ""):
    """Update a credential in the codex pool (after refresh).

    Mirror of claude_code_session._persist_tokens_to_service. If pool_index
    ≥ 0, updates that slot; otherwise finds by matching refresh_token.
    Refuses to persist a token that fails basic validation — better to
    keep the old broken token than overwrite with garbage.
    """
    if not _validate_oauth_token(access_token, refresh_token, expires_at):
        logger.warning(
            "[codex] refusing to persist invalid token "
            "(access_token=%r, expires_at=%r) to pool[%s] — keeping old",
            bool(access_token), expires_at, pool_index)
        return
    sid = _find_codex_service_id(service_id)
    if not sid:
        return
    pool = _load_credentials_pool(sid)
    if not pool:
        add_credential_to_pool(
            access_token, refresh_token, expires_at,
            account=account, id_token=id_token, service_id=sid)
        return
    if 0 <= pool_index < len(pool):
        pool[pool_index]["access_token"] = access_token
        pool[pool_index]["refresh_token"] = refresh_token
        pool[pool_index]["expires_at"] = int(expires_at)
        if id_token:
            pool[pool_index]["id_token"] = id_token
    else:
        for cred in pool:
            if cred.get("refresh_token") == refresh_token:
                cred["access_token"] = access_token
                cred["expires_at"] = int(expires_at)
                if id_token:
                    cred["id_token"] = id_token
                break
        else:
            pool[0]["access_token"] = access_token
            pool[0]["refresh_token"] = refresh_token
            pool[0]["expires_at"] = int(expires_at)
            if id_token:
                pool[0]["id_token"] = id_token
    _save_credentials_pool(pool, sid)
    logger.info("[codex] credential updated in pool for '%s'", sid)


# Base directory for per-session codex workdirs — read dynamically so a
# test that monkey-patches paths.CODEX_SESSIONS_DIR sees the change.
def _get_sessions_base():
    import core.paths as _p
    return str(_p.CODEX_SESSIONS_DIR.resolve())


class CodexSessionMixin:
    """Session/workdir management for the Codex CLI.

    Structural mirror of ClaudeCodeSessionMixin — same method surface,
    same call sites in the stream loop. Only the four PawFlow-touchpoints
    differ:
      1. CLI command         (`codex exec --json` vs `claude -p`)
      2. JSON event schema   (handled in `_consume_codex_stream`)
      3. Image attachments   (codex — see `_codex_extract_images` in the provider)
      4. Credentials format  (codex auth.json vs CC .credentials.json)
    Everything else (tool relay info, workdir layout, env, MCP config
    plumbing) is PawFlow infrastructure and identical across CLIs.
    """

    # Class-level round-robin counter (private to codex pool — NOT shared
    # with CC's _pool_counter). Each CLI rotates through its own credential
    # pool independently.
    _pool_counter = 0
    _pool_lock = __import__('threading').Lock()

    def _codex_resolve_service_tokens(self, pool_index: int = -1) -> dict:
        """Resolve codex tokens from the credentials pool. Mirror of CC.

        Returns {"access_token", "refresh_token", "expires_at",
                 "pool_index", "id_token", "account"}.
        """
        import time as _t
        svc_id = getattr(self, '_agent_service', '') or ''
        pool = _load_credentials_pool(svc_id)
        if not pool:
            return {"access_token": "", "refresh_token": "",  # nosec B105
                    "expires_at": 0, "pool_index": -1,
                    "id_token": "", "account": ""}  # nosec B105
        now_ms = int(_t.time() * 1000)
        valid = [(i, c) for i, c in enumerate(pool) if c.get("expires_at", 0) > now_ms]
        if not valid and pool:
            valid = [(len(pool) - 1, pool[-1])]
        if 0 <= pool_index < len(pool) and pool[pool_index].get("expires_at", 0) > now_ms:
            idx = pool_index
        else:
            with CodexSessionMixin._pool_lock:
                pick = CodexSessionMixin._pool_counter % len(valid)
                CodexSessionMixin._pool_counter += 1
                idx = valid[pick][0]
        cred = pool[idx]
        return {
            "access_token": cred.get("access_token", ""),
            "refresh_token": cred.get("refresh_token", ""),
            "expires_at": cred.get("expires_at", 0),
            "pool_index": idx,
            "id_token": cred.get("id_token", ""),
            "account": cred.get("account", ""),
        }

    def _codex_force_refresh_pool_entry(self, pool_index: int) -> bool:
        """Force-refresh access_token for pool[pool_index]. Mirror of CC.

        On failure, drops the slot from the pool entirely (a credential
        whose refresh is broken is dead and must not be re-attempted).
        """
        svc_id = getattr(self, '_agent_service', '') or ''
        pool = _load_credentials_pool(svc_id)
        if pool_index < 0 or pool_index >= len(pool):
            return False
        refresh_token = pool[pool_index].get("refresh_token", "")

        def _drop_dead_slot(reason: str):
            current = _load_credentials_pool(svc_id)
            if 0 <= pool_index < len(current):
                current.pop(pool_index)
                _save_credentials_pool(current, service_id=svc_id)
                logger.warning(
                    "[codex force-refresh] removed dead pool[%d] (%s); "
                    "remaining=%d", pool_index, reason, len(current))

        if not refresh_token:
            _drop_dead_slot("no refresh_token")
            return False
        try:
            new = refresh_oauth_token(refresh_token)
        except Exception as e:
            logger.warning("[codex force-refresh] pool[%d] failed: %s",
                           pool_index, e)
            _drop_dead_slot(f"refresh error: {e}")
            return False
        _new_at = new.get("access_token", "")
        _new_rt = new.get("refresh_token", refresh_token)
        _new_id = new.get("id_token", pool[pool_index].get("id_token", ""))
        _new_exp = int(new.get("expires_at", 0) or 0)
        if not _validate_oauth_token(_new_at, _new_rt, _new_exp):
            logger.warning(
                "[codex force-refresh] pool[%d] returned invalid token",
                pool_index)
            _drop_dead_slot("refresh returned invalid token")
            return False
        _persist_tokens_to_service(
            _new_at, _new_rt, _new_exp,
            service_id=svc_id, pool_index=pool_index, id_token=_new_id)
        logger.info("[codex force-refresh] pool[%d] access_token renewed",
                    pool_index)
        return True

    @staticmethod
    def _codex_refresh_oauth_token(refresh_token: str) -> dict:
        """Wrapper around module-level refresh_oauth_token, signature-compat
        with ClaudeCodeSessionMixin._refresh_oauth_token."""
        return refresh_oauth_token(refresh_token)

    def _codex_get_session_workdir(self, conversation_id: str,
                              agent_name: str = "",
                              user_id: str = "") -> str:
        """Per-session codex workdir. Mirror of CC — same path layout, only
        the root differs (CODEX_SESSIONS_DIR vs CLAUDE_SESSIONS_DIR).

        Path: data/runtime/sessions/codex/<user>/<conv>/<agent>/
        """
        uid = user_id or getattr(self, '_user_id', '')
        if not uid:
            raise ValueError("BUG: user_id required for codex session workdir")
        cid = conversation_id
        if not cid:
            raise ValueError("BUG: conversation_id required for codex session workdir")
        if not agent_name:
            raise ValueError("BUG: agent_name required for codex session workdir")
        uid = uid.replace(':', '_').replace('/', '_').replace('\\', '_')
        cid = cid.replace(":", "_")
        workdir = os.path.join(_get_sessions_base(), uid, cid, agent_name)
        os.makedirs(workdir, exist_ok=True)
        return workdir

    def _codex_env(self, workdir: str = "") -> dict:
        """Build environment for codex subprocess. Mirror of CC's
        _claude_code_env, but uses CODEX_HOME instead of CLAUDE_CONFIG_DIR.

        Codex respects CODEX_HOME as the directory containing auth.json
        and config.toml — set it to the per-session workdir so credentials
        and MCP config don't leak across sessions.
        """
        env = os.environ.copy()
        if workdir:
            env["CODEX_HOME"] = workdir
        # API key mode: bypass OAuth entirely. Codex reads CODEX_API_KEY
        # AND OPENAI_API_KEY (CODEX_API_KEY wins).
        _api_key = getattr(self, 'api_key', '')
        if callable(_api_key):
            _api_key = _api_key()
        elif isinstance(_api_key, property):
            _api_key = ''
        if _api_key:
            env["CODEX_API_KEY"] = _api_key
            env["OPENAI_API_KEY"] = _api_key
        # Custom endpoint: codex picks up OPENAI_BASE_URL.
        _base_url = getattr(self, 'base_url', '')
        if callable(_base_url):
            _base_url = _base_url()
        elif isinstance(_base_url, property):
            _base_url = ''
        if _base_url:
            if "/relay-proxy/" not in _base_url:
                import re
                _repl = lambda m: m.group(1) + "host.docker.internal" + (m.group(2) or '')
                _base_url = re.sub(
                    r'(https?://)localhost(:\d+)?', _repl, _base_url)
                _base_url = re.sub(
                    r'(https?://)127\.0\.0\.1(:\d+)?', _repl, _base_url)
            env["OPENAI_BASE_URL"] = _base_url
            if _base_url.startswith("https://") and "/relay-proxy/" in _base_url:
                env["NODE_TLS_REJECT_UNAUTHORIZED"] = "0"
            logger.info("Codex using custom endpoint: %s", _base_url)
        return env

    # Reuse CC's tool relay info — it's a global PawFlow service, not
    # CC-specific. Importing the classmethod directly avoids redundant
    # relay creation when CC is also active in the same process.
    @classmethod
    def _get_tool_relay_info(cls) -> tuple:
        from core.llm_providers.claude_code_session import ClaudeCodeSessionMixin
        return ClaudeCodeSessionMixin._get_tool_relay_info()

    _OAUTH_REFRESH_MIN_TTL_SEC = 30 * 60

    def _codex_setup_credentials(self, workdir: str, pool_index: int = -1,
                            exclude_indices=None):
        """Write codex auth.json in <workdir>/.codex/ for codex CLI auth.

        Mirror of CC's _codex_setup_credentials. The four touchpoint differences:
          - File: `<workdir>/.codex/auth.json` (vs CC's `<workdir>/.credentials.json`)
          - Schema: `{OPENAI_API_KEY, tokens:{access_token, refresh_token,
                      id_token, account_id}, last_refresh}` (vs CC's `claudeAiOauth`)
          - Refresh endpoint: auth.openai.com (vs platform.claude.com)
          - id_token must be present — codex rejects auth.json without it
            ("invalid ID token format")
        Everything else (proactive refresh, dead-slot removal, exclude_indices,
        round-robin start) is identical to CC.
        """
        from core.llm_client import LLMClientError
        import time as _time

        _api_key = getattr(self, 'api_key', '')
        if callable(_api_key):
            _api_key = _api_key()
        elif isinstance(_api_key, property):
            _api_key = ''
        if _api_key:
            logger.info("Codex using API key (skipping OAuth credentials)")
            return

        svc_id = getattr(self, '_agent_service', '') or ''
        pool = _load_credentials_pool(svc_id)
        if not pool:
            raise LLMClientError(
                "Codex credentials not configured. "
                "Use the codex Login button to authenticate.")
        _exclude = set(exclude_indices or ())

        if 0 <= pool_index < len(pool) and pool_index not in _exclude:
            indices = [pool_index] + [
                i for i in range(len(pool))
                if i != pool_index and i not in _exclude]
        else:
            with CodexSessionMixin._pool_lock:
                start = CodexSessionMixin._pool_counter % len(pool)
                CodexSessionMixin._pool_counter += 1
            indices = [
                (start + i) % len(pool) for i in range(len(pool))
                if (start + i) % len(pool) not in _exclude]
        if not indices:
            raise LLMClientError(
                "All codex credentials in the pool have already failed "
                "during this stream. Re-authenticate via the Login button.")

        dead_indices = []
        access_token = refresh_token = id_token = account = ""  # nosec B105
        expires_at = 0
        for _pidx in indices:
            cred = pool[_pidx]
            access_token = cred.get("access_token", "")
            refresh_token = cred.get("refresh_token", "")
            id_token = cred.get("id_token", "")
            account = cred.get("account", "")
            expires_at = cred.get("expires_at", 0)
            if not access_token:
                dead_indices.append(_pidx)
                continue
            # codex rejects auth.json with empty id_token — a slot without
            # one is dead until the user re-logs in.
            if not id_token:
                logger.warning(
                    "[codex] pool[%d] missing id_token — skipping", _pidx)
                dead_indices.append(_pidx)
                continue
            if expires_at:
                _exp_s = int(expires_at) / 1000 if int(expires_at) > 1e12 else int(expires_at)
                _remaining = _exp_s - _time.time()
                if _remaining < self._OAUTH_REFRESH_MIN_TTL_SEC and refresh_token:
                    logger.info("[codex] pool[%d] %s — refreshing", _pidx,
                                "expired" if _remaining < 0 else f"expiring in {_remaining:.0f}s")
                    try:
                        new_tokens = self._codex_refresh_oauth_token(refresh_token)
                        access_token = new_tokens["access_token"]
                        refresh_token = new_tokens.get("refresh_token", refresh_token)
                        id_token = new_tokens.get("id_token", id_token) or id_token
                        expires_at = new_tokens["expires_at"]
                        _persist_tokens_to_service(
                            access_token, refresh_token, int(expires_at),
                            service_id=svc_id, pool_index=_pidx,
                            id_token=id_token, account=account)
                    except Exception as e:
                        logger.warning(
                            "[codex] pool[%d] refresh failed, dropping: %s",
                            _pidx, e)
                        dead_indices.append(_pidx)
                        continue
                elif _remaining < 0 and not refresh_token:
                    logger.warning(
                        "[codex] pool[%d] expired, no refresh token", _pidx)
                    dead_indices.append(_pidx)
                    continue
            self._current_pool_index = _pidx
            if dead_indices:
                pool = [c for i, c in enumerate(pool) if i not in dead_indices]
                _save_credentials_pool(pool, service_id=svc_id)
                logger.info("[codex] removed %d dead credential(s)",
                            len(dead_indices))
            break
        else:
            pool = [c for i, c in enumerate(pool) if i not in dead_indices]
            _save_credentials_pool(pool, service_id=svc_id)
            raise LLMClientError(
                "All codex credentials expired or refresh failed. "
                "Re-authenticate via the Login button.")

        # Write auth.json under <workdir>/.codex/ (CODEX_HOME).
        # OPENAI_API_KEY MUST be JSON null (Python None) so codex falls
        # through to the OAuth tokens path — an empty string is treated
        # as a real api_key and forwarded to api.openai.com which 401s.
        codex_home = os.path.join(workdir, ".codex")
        os.makedirs(codex_home, exist_ok=True)
        auth_blob = {
            "OPENAI_API_KEY": None,
            "tokens": {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "id_token": id_token,
                "account_id": account,
            },
            "last_refresh": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        auth_path = os.path.join(codex_home, "auth.json")
        with open(auth_path, "w", encoding="utf-8") as f:
            json.dump(auth_blob, f)
        os.chmod(auth_path, 0o600)

    def _codex_recover_tokens(self, workdir: str):
        """Read back tokens from <workdir>/.codex/auth.json after a run.

        Codex may have refreshed access_token mid-turn (via /v1/oauth/token).
        If we see a new value vs what we wrote, persist it back to the pool
        slot we picked at _codex_setup_credentials time.
        """
        auth_path = os.path.join(workdir, ".codex", "auth.json")
        if not os.path.exists(auth_path):
            return
        try:
            with open(auth_path, "r", encoding="utf-8") as f:
                blob = json.load(f)
            tokens = blob.get("tokens", {}) or {}
            new_access = tokens.get("access_token", "")
            new_refresh = tokens.get("refresh_token", "")
            new_id = tokens.get("id_token", "")
            if not new_access:
                return
            _pidx = getattr(self, '_current_pool_index', -1)
            _current = self._codex_resolve_service_tokens(pool_index=_pidx)
            if new_access == _current.get("access_token", ""):
                return
            _service_id = getattr(self, '_agent_service', '') or ''
            _persist_tokens_to_service(
                new_access, new_refresh, _current.get("expires_at", 0),
                service_id=_service_id, pool_index=_pidx,
                id_token=new_id)
            logger.info("[codex] recovered refreshed tokens [pool:%d]",
                        _pidx)
        except Exception as e:
            logger.debug("[codex] token recovery failed: %s", e)

    def _codex_setup_mcp_config(self, workdir: str, user_id: str = "",
                           conversation_id: str = "",
                           agent_name: str = "") -> tuple:
        """Write MCP config to <workdir>/.codex/config.toml.

        Returns (mcp_path, internal_token). The caller owns the
        internal_token lifecycle and MUST call core.internal_auth.revoke_token
        once the codex invocation ends.

        Mirror of CC's _codex_setup_mcp_config; only the file format differs
        (codex uses TOML for config.toml, CC uses JSON for .mcp.json).
        """
        mcp_bridge = "/opt/pawflow/mcp_bridge.py"
        python_bin = "/usr/bin/python3"

        relay_url, relay_token = self._get_tool_relay_info()
        if not relay_url:
            logger.warning("No toolRelay service — codex MCP bridge will have no tools")
        if relay_url:
            from core.docker_utils import get_host_ip
            _host_ip = get_host_ip()
            relay_url = relay_url.replace("localhost", _host_ip).replace("127.0.0.1", _host_ip)

        from core.internal_auth import mint_token
        internal_token = mint_token()
        # Sanity-check: an empty internal_token would silently kill MCP
        # bridge auth (server rejects with `cookie_header has 1 parts`).
        # Log presence + length so we can confirm it actually got minted.
        logger.info("[codex] minted internal_token len=%d preview=%s relay_url=%s relay_token_set=%s",
                    len(internal_token or ""),
                    (internal_token[:8] + "…") if internal_token else "EMPTY",
                    relay_url or "EMPTY",
                    "yes" if relay_token else "no")

        # codex config.toml: the binary parses TOML, not JSON.
        # `model_auto_compact_token_limit = 999999999` disables codex's
        # built-in compaction so PawFlow's bucket compact owns the rollover.
        def _toml_escape(s: str) -> str:
            # Escape for TOML basic string (double-quoted): backslash, quote,
            # control chars. Values here are URLs / tokens / identifiers —
            # no newlines expected, simple escaping suffices.
            return s.replace("\\", "\\\\").replace('"', '\\"')

        # TOML sub-section format `[mcp_servers.pawflow.env]` is what
        # codex's MCP config parser actually honours. Earlier attempt
        # with the inline `env = { K = "V" }` syntax (commit 583d9a9)
        # caused codex to NOT spawn the MCP bridge at all — no log file
        # ever appeared in the session dir, no /ws/tools/* connection
        # was attempted from the bridge. Reverted to sub-section.
        toml = (
            "# Auto-generated by PawFlow — do not edit.\n"
            f'model_auto_compact_token_limit = 999999999\n'
            "suppress_unstable_features_warning = true\n"
            "\n"
            "[features]\n"
            "enable_fanout = true\n"
            "\n"
            "[mcp_servers.pawflow]\n"
            f'command = "{_toml_escape(python_bin)}"\n'
            f'args = ["{_toml_escape(mcp_bridge)}"]\n'
            "startup_timeout_sec = 20\n"
            "tool_timeout_sec = 3600\n"
            "supports_parallel_tool_calls = true\n"
            "enabled = true\n"
            "required = true\n"
            "\n"
            "[mcp_servers.pawflow.env]\n"
            f'PAWFLOW_TOOL_RELAY_URL = "{_toml_escape(relay_url or "")}"\n'
            f'PAWFLOW_TOOL_RELAY_TOKEN = "{_toml_escape(relay_token or "")}"\n'
            f'PAWFLOW_INTERNAL_TOKEN = "{_toml_escape(internal_token)}"\n'
            f'PAWFLOW_USER_ID = "{_toml_escape(user_id or "")}"\n'
            f'PAWFLOW_CONVERSATION_ID = "{_toml_escape(conversation_id or "")}"\n'
            f'PAWFLOW_AGENT_NAME = "{_toml_escape(agent_name or "")}"\n'
        )
        codex_home = os.path.join(workdir, ".codex")
        os.makedirs(codex_home, exist_ok=True)
        config_path = os.path.join(codex_home, "config.toml")
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(toml)
        os.chmod(config_path, 0o600)
        logger.info("[codex] config.toml written: %s (relay=%s)",
                    config_path, relay_url)
        return config_path, internal_token

    # Codex's equivalent of CC's `--disallowedTools` is per-feature
    # `--disable <name>` (see `codex features list`) plus per-tool
    # `-c tools.<name>=false` overrides for hosted Responses-API tools.
    # `_build_codex_cmd` below applies the full block list — the legacy
    # _DISALLOWED_BUILTIN_TOOLS string is kept empty just so the symbol
    # stays defined in case any cross-provider helper introspects it.
    _DISALLOWED_BUILTIN_TOOLS = ""

    def _build_codex_cmd(self, model: str,
                          session_id: str = "",
                          mcp_config_path: str = "",
                          workdir: str = "") -> list:
        """Build the codex CLI command.

        Codex `exec --json` is one-shot (reads stdin once, exits at end
        of turn) but supports `exec resume <session_id>` to continue a
        previous rollout — same role as CC's `--resume`.

        --sandbox is REJECTED by `exec resume`; we pass
        `--dangerously-bypass-approvals-and-sandbox` instead which is
        accepted on both paths and disables the sandbox completely.

        `--disable shell_tool` removes codex's NATIVE shell so the agent
        can only reach files / commands through PawFlow's MCP bridge
        (pawflow.use_tool). Without this codex would call its own bash
        which sees the per-session workdir as cwd, NOT the user's
        `/workspace` (a virtual MCP-only path), and silently fail with
        "No such file or directory" while never reaching the relay.
        """
        codex_args = ["exec"]
        if session_id:
            codex_args.extend(["resume", session_id])
        codex_args.extend([
            "--json",
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
        ])
        # Disable EVERY codex builtin tool that's `stable + enabled by
        # default` (per `codex features list`) so the model is forced to
        # reach for PawFlow's MCP bridge for filesystem / shell / browser
        # / image-gen / desktop-control. Mirror of CC's --disallowedTools
        # for claude-code. Without this, codex calls its native shell /
        # browser / etc. against the container's local filesystem (NOT
        # the user's /workspace) and silently fails or leaks state.
        # Web_search emits as item.type='web_search' (hosted OpenAI tool,
        # not in `features list`) — not blockable via --disable, would
        # need a separate config knob; tracked separately.
        for _builtin in (
            "shell_tool",         # shell exec
            "shell_snapshot",     # shell-state snapshot/restore
            "unified_exec",       # generic exec
            "apps",               # OS-app launcher
            "browser_use",        # browser automation
            "in_app_browser",     # in-app browser
            "computer_use",       # desktop control (mouse/kbd)
            "image_generation",   # DALL-E image gen
        ):
            codex_args.extend(["--disable", _builtin])
        # Hosted Responses-API tools toggled via the `[tools]` config
        # table. Verified against the codex binary's TOML config schema
        # (struct `WebSearchToolConfigInput` enum has variants
        # `web_search` and `view_image`). Pass via -c so the value
        # overrides whatever the binary defaults to.
        # NOTE: with the user's ChatGPT-plan auth, the model may still
        # emit `web_search_call` items at the Responses-API level
        # (server-side decision); the parser handles those gracefully.
        for _hosted in (
            "web_search",   # OpenAI hosted web search
            "view_image",   # hosted vision/image-view tool
        ):
            codex_args.extend(["-c", f"tools.{_hosted}=false"])
        model = (model or "").strip()
        if model:
            codex_args.extend(["--model", model])
        codex_args.extend([
            "-",  # read prompt from stdin
        ])
        # Codex picks up MCP config from CODEX_HOME/config.toml automatically;
        # there's no equivalent of CC's --mcp-config flag. mcp_config_path
        # is unused but kept in the signature so the call site mirrors CC's.
        self._pool_codex_args = codex_args
        return codex_args

    # System prompt prepend codex receives on the FIRST turn of every
    # session. Resume turns rely on the rollout for prior instructions
    # so this is one-shot per rollout. Stay short — codex weights system
    # context heavily and over-instruction degrades reasoning quality.
    _CODEX_PAWFLOW_PREAMBLE = CLI_MCP_SYSTEM_PROMPT
