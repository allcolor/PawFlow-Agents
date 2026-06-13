"""OAuth credentials pool + refresh for the Gemini CLI provider.

Mirror of claude_code_session.py / codex_session.py credential helpers,
pointed at Google's standard OAuth2 token endpoint. Independent file from
the other CLIs because the three subsystems evolve separately — see
memory "Separate pools per CLI".

The Gemini CLI persists OAuth credentials in ~/.gemini/oauth_creds.json
plus a sibling ~/.gemini/google_accounts.json that maps the project hash
to the account. We keep an encrypted server-side pool in the
GLOBAL_SECRETS_FILE under '<service_id>_credentials_pool' with the same
shape used by Claude / Codex pools so the pool-management UI can be reused.
"""

import json
import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Google's standard OAuth2 token endpoint. Same one used by gcloud, the
# Gemini CLI, and all GIS clients. Refresh is RFC 6749 standard.
_GEMINI_TOKEN_ENDPOINT_HOST = "oauth2.googleapis.com"  # nosec B105
_GEMINI_TOKEN_ENDPOINT_PATH = "/token"  # nosec B105
# The Gemini CLI client_id + secret pair (public client — these are NOT
# considered secret in the OAuth-installed-app threat model). Picked up
# from the publicly distributed CLI bundle. If Google rotates them we'll
# see the refresh return an error and we update the constants here.
_GEMINI_CLIENT_ID = (
    "681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib135j.apps.googleusercontent.com"
)
_GEMINI_CLIENT_SECRET = "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"  # nosec B105


def _find_gemini_service_id(service_id: str = "", user_id: str = "",
                            conv_id: str = "") -> str:
    """Find the credential-pool owner for Gemini OAuth."""
    try:
        from services.llm_credential_oauth import (
            credential_service_id_from_llm_service,
            resolve_credential_service_id,
        )
        return (resolve_credential_service_id(
            "gemini", service_id, user_id=user_id, conv_id=conv_id)
            or credential_service_id_from_llm_service(
                "gemini", service_id, user_id=user_id, conv_id=conv_id))
    except Exception:
        return ""


def _load_credentials_pool(service_id: str = "", user_id: str = "",
                           conv_id: str = "") -> list:
    from core.secrets import get_secrets_manager
    sid = _find_gemini_service_id(service_id, user_id=user_id, conv_id=conv_id)
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


def _save_credentials_pool(pool: list, service_id: str = "", user_id: str = "",
                           conv_id: str = ""):
    from core.secrets import get_secrets_manager
    sid = _find_gemini_service_id(service_id, user_id=user_id, conv_id=conv_id)
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
    logger.info("[gemini] credentials pool (%d) persisted for '%s'", len(pool), sid)


def add_credential_to_pool(access_token: str, refresh_token: str,
                            expires_at, account: str = "",
                            service_id: str = "", user_id: str = "",
                            conv_id: str = ""):
    sid = _find_gemini_service_id(service_id, user_id=user_id, conv_id=conv_id)
    if not sid:
        raise ValueError(f"Gemini credential service '{service_id}' not found")
    pool = _load_credentials_pool(service_id, user_id=user_id, conv_id=conv_id)
    for i, existing in enumerate(pool):
        if existing.get("refresh_token") == refresh_token:
            pool[i] = {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "expires_at": int(expires_at),
                "account": account or existing.get("account", ""),
                "added_at": int(time.time()),
            }
            _save_credentials_pool(
                pool, service_id, user_id=user_id, conv_id=conv_id)
            return
    pool.append({
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": int(expires_at),
        "account": account,
        "added_at": int(time.time()),
    })
    _save_credentials_pool(pool, service_id, user_id=user_id, conv_id=conv_id)
    logger.info("[gemini] credential added to pool (now %d) for '%s'",
                len(pool), sid)


def remove_credential_from_pool(index: int, service_id: str = "",
                                user_id: str = "", conv_id: str = "") -> bool:
    pool = _load_credentials_pool(service_id, user_id=user_id, conv_id=conv_id)
    if 0 <= index < len(pool):
        pool.pop(index)
        _save_credentials_pool(pool, service_id, user_id=user_id, conv_id=conv_id)
        return True
    return False


def reset_credentials_pool(service_id: str = "", user_id: str = "",
                           conv_id: str = ""):
    _save_credentials_pool([], service_id, user_id=user_id, conv_id=conv_id)


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
    """Refresh OAuth access token via Google's OAuth2 token endpoint.

    Google's response uses 'expires_in' (seconds). Returns expires_at in
    milliseconds for parity with Claude/Codex pool storage.
    """
    import http.client
    import ssl
    import urllib.parse

    body = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": _GEMINI_CLIENT_ID,
        "client_secret": _GEMINI_CLIENT_SECRET,
    }).encode("utf-8")
    ctx = ssl.create_default_context()
    conn = http.client.HTTPSConnection(
        _GEMINI_TOKEN_ENDPOINT_HOST, 443, context=ctx, timeout=15)
    try:
        conn.request("POST", _GEMINI_TOKEN_ENDPOINT_PATH, body=body, headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Content-Length": str(len(body)),
        })
        resp = conn.getresponse()
        resp_body = resp.read().decode("utf-8")
    finally:
        conn.close()

    if resp.status != 200:
        raise RuntimeError(
            f"Gemini OAuth refresh failed ({resp.status}): {resp_body[:200]}")
    data = json.loads(resp_body)
    new_access = data.get("access_token", "")
    # Google does NOT rotate refresh_token by default; we keep the old one.
    new_refresh = data.get("refresh_token", "") or refresh_token
    expires_in = int(data.get("expires_in", 0) or 0)
    expires_at_ms = time.time() * 1000 + expires_in * 1000
    if not new_access:
        raise RuntimeError(
            f"Gemini OAuth refresh returned no access_token: {resp_body[:200]}")
    return {
        "access_token": new_access,
        "refresh_token": new_refresh,
        "expires_at": int(expires_at_ms),
    }


def parse_oauth_creds_json(creds_text: str) -> dict:
    """Parse a ~/.gemini/oauth_creds.json blob and return PawFlow pool fields.

    Gemini stores expiry_date in milliseconds already, plus an optional
    id_token JWT we don't need for refresh — only access + refresh + expiry.
    """
    try:
        d = json.loads(creds_text)
    except (json.JSONDecodeError, TypeError):
        return {}
    return {
        "access_token": d.get("access_token", "") or "",
        "refresh_token": d.get("refresh_token", "") or "",
        "expires_at": int(d.get("expiry_date", 0) or 0),
        # account_id lives in google_accounts.json (separate file); the
        # service_flow handler may pass it in via the 'account' kwarg.
    }


def _persist_tokens_to_service(access_token: str, refresh_token: str,
                                expires_at, service_id: str = "",
                                pool_index: int = -1,
                                account: str = "", user_id: str = "",
                                conv_id: str = ""):
    """Update a credential in the gemini pool (after refresh).

    Mirror of codex_session._persist_tokens_to_service / cc equivalent.
    """
    if not _validate_oauth_token(access_token, refresh_token, expires_at):
        logger.warning(
            "[gemini] refusing to persist invalid token "
            "(access_token=%r, expires_at=%r) to pool[%s] — keeping old",
            bool(access_token), expires_at, pool_index)
        return
    sid = _find_gemini_service_id(service_id, user_id=user_id, conv_id=conv_id)
    if not sid:
        return
    pool = _load_credentials_pool(sid, user_id=user_id, conv_id=conv_id)
    if not pool:
        add_credential_to_pool(
            access_token, refresh_token, expires_at,
            account=account, service_id=sid, user_id=user_id, conv_id=conv_id)
        return
    if 0 <= pool_index < len(pool):
        pool[pool_index]["access_token"] = access_token
        pool[pool_index]["refresh_token"] = refresh_token
        pool[pool_index]["expires_at"] = int(expires_at)
    else:
        for cred in pool:
            if cred.get("refresh_token") == refresh_token:
                cred["access_token"] = access_token
                cred["expires_at"] = int(expires_at)
                break
        else:
            pool[0]["access_token"] = access_token
            pool[0]["refresh_token"] = refresh_token
            pool[0]["expires_at"] = int(expires_at)
    _save_credentials_pool(pool, sid, user_id=user_id, conv_id=conv_id)
    logger.info("[gemini] credential updated in pool for '%s'", sid)


def recover_tokens_from_workdir(workdir: str, service_id: str,
                               pool_index: int, user_id: str = "",
                               conv_id: str = "") -> bool:
    """Copy any gemini-CLI-rotated OAuth token from
    <workdir>/.gemini/oauth_creds.json back to the exact pool slot.

    Called from live-session teardown (idle sweep / shutdown / evict).
    Google does NOT rotate the refresh_token by default, so for gemini
    this is defense-in-depth rather than a logout fix (unlike CC /
    Anthropic). Targets the slot via the SESSION's own service_id /
    pool_index, not instance state. oauth_creds.json carries expiry_date.
    """
    creds_path = os.path.join(workdir, ".gemini", "oauth_creds.json")
    if not os.path.exists(creds_path):
        return False
    try:
        with open(creds_path, "r", encoding="utf-8") as f:
            blob = json.load(f)
        new_access = blob.get("access_token", "")
        new_refresh = blob.get("refresh_token", "")
        new_expiry = int(blob.get("expiry_date", 0) or 0)
        if not new_access:
            return False
        _persist_tokens_to_service(
            new_access, new_refresh, new_expiry,
            service_id=service_id, pool_index=pool_index,
            user_id=user_id, conv_id=conv_id)
        logger.info(
            "[gemini] recovered teardown tokens [pool:%s] for '%s'",
            pool_index, service_id)
        return True
    except Exception:
        logger.debug(
            "[gemini] teardown token recovery failed", exc_info=True)
        return False


def _get_sessions_base():
    import core.paths as _p
    return str(_p.GEMINI_SESSIONS_DIR.resolve())


class GeminiSessionMixin:
    """OAuth, workdir, and environment helpers for Gemini CLI/ACP.

    The runtime provider speaks Gemini's Agent Client Protocol over stdio and
    supplies MCP server configuration in the ACP `session/new` payload. This
    mixin deliberately owns only reusable PawFlow infrastructure: credential
    pool refresh, per-agent HOME/workdir layout, token recovery, and relay env
    construction.
    """

    _pool_counter = 0
    _pool_lock = __import__('threading').Lock()

    def _gemini_resolve_service_tokens(self, pool_index: int = -1,
                                       user_id: str = "",
                                       conversation_id: str = "") -> dict:
        import time as _t
        svc_id = getattr(self, '_agent_service', '') or ''
        uid = user_id or getattr(self, '_user_id', '') or ''
        cid = conversation_id or getattr(self, '_conversation_id', '') or ''
        pool = _load_credentials_pool(svc_id, user_id=uid, conv_id=cid)
        if not pool:
            return {"access_token": "", "refresh_token": "",  # nosec B105
                    "expires_at": 0, "pool_index": -1, "account": ""}
        now_ms = int(_t.time() * 1000)
        valid = [(i, c) for i, c in enumerate(pool) if c.get("expires_at", 0) > now_ms]
        if not valid and pool:
            valid = [(len(pool) - 1, pool[-1])]
        if 0 <= pool_index < len(pool) and pool[pool_index].get("expires_at", 0) > now_ms:
            idx = pool_index
        else:
            with GeminiSessionMixin._pool_lock:
                pick = GeminiSessionMixin._pool_counter % len(valid)
                GeminiSessionMixin._pool_counter += 1
                idx = valid[pick][0]
        cred = pool[idx]
        return {
            "access_token": cred.get("access_token", ""),
            "refresh_token": cred.get("refresh_token", ""),
            "expires_at": cred.get("expires_at", 0),
            "pool_index": idx,
            "account": cred.get("account", ""),
        }

    def _gemini_force_refresh_pool_entry(self, pool_index: int,
                                         user_id: str = "",
                                         conversation_id: str = "") -> bool:
        svc_id = getattr(self, '_agent_service', '') or ''
        uid = user_id or getattr(self, '_user_id', '') or ''
        cid = conversation_id or getattr(self, '_conversation_id', '') or ''
        pool = _load_credentials_pool(svc_id, user_id=uid, conv_id=cid)
        if pool_index < 0 or pool_index >= len(pool):
            return False
        refresh_token = pool[pool_index].get("refresh_token", "")

        def _drop_dead_slot(reason: str):
            current = _load_credentials_pool(svc_id, user_id=uid, conv_id=cid)
            if 0 <= pool_index < len(current):
                current.pop(pool_index)
                _save_credentials_pool(
                    current, service_id=svc_id, user_id=uid, conv_id=cid)
                logger.warning(
                    "[gemini force-refresh] removed dead pool[%d] (%s); "
                    "remaining=%d", pool_index, reason, len(current))

        if not refresh_token:
            _drop_dead_slot("no refresh_token")
            return False
        try:
            new = refresh_oauth_token(refresh_token)
        except Exception as e:
            logger.warning("[gemini force-refresh] pool[%d] failed: %s",
                           pool_index, e)
            _drop_dead_slot(f"refresh error: {e}")
            return False
        _new_at = new.get("access_token", "")
        _new_rt = new.get("refresh_token", refresh_token)
        _new_exp = int(new.get("expires_at", 0) or 0)
        if not _validate_oauth_token(_new_at, _new_rt, _new_exp):
            _drop_dead_slot("refresh returned invalid token")
            return False
        _persist_tokens_to_service(
            _new_at, _new_rt, _new_exp,
            service_id=svc_id, pool_index=pool_index, user_id=uid, conv_id=cid)
        return True

    @staticmethod
    def _gemini_refresh_oauth_token(refresh_token: str) -> dict:
        return refresh_oauth_token(refresh_token)

    def _gemini_get_session_workdir(self, conversation_id: str,
                              agent_name: str = "",
                              user_id: str = "") -> str:
        """Per-session gemini workdir.

        Path: data/runtime/sessions/gemini/<user>/<conv>/<agent>/
        Gemini reads ~/.gemini/ from $HOME, so we set HOME=workdir in env
        and create workdir/.gemini/ for oauth_creds.json + settings.json.
        """
        uid = user_id or getattr(self, '_user_id', '')
        if not uid:
            raise ValueError("BUG: user_id required for gemini session workdir")
        cid = conversation_id
        if not cid:
            raise ValueError("BUG: conversation_id required for gemini session workdir")
        if not agent_name:
            raise ValueError("BUG: agent_name required for gemini session workdir")
        uid = uid.replace(':', '_').replace('/', '_').replace('\\', '_')
        cid = cid.replace(":", "_")
        workdir = os.path.join(_get_sessions_base(), uid, cid, agent_name)
        os.makedirs(workdir, exist_ok=True)
        return workdir

    def _gemini_env(self, workdir: str = "") -> dict:
        """Build environment for gemini subprocess.

        Gemini reads `~/.gemini/oauth_creds.json` and `~/.gemini/settings.json`
        from $HOME, so we override HOME to the per-session workdir to keep
        credentials and MCP config isolated per (conv, agent).
        """
        env = os.environ.copy()
        if workdir:
            env["HOME"] = workdir
        _api_key = getattr(self, 'api_key', '')
        if callable(_api_key):
            _api_key = _api_key()
        elif isinstance(_api_key, property):
            _api_key = ''
        if _api_key:
            env["GEMINI_API_KEY"] = _api_key
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
            env["GEMINI_BASE_URL"] = _base_url
            if _base_url.startswith("https://") and "/relay-proxy/" in _base_url:
                env["NODE_TLS_REJECT_UNAUTHORIZED"] = "0"
        return env

    @classmethod
    def _get_tool_relay_info(cls) -> tuple:
        from core.llm_providers.claude_code_session import ClaudeCodeSessionMixin
        return ClaudeCodeSessionMixin._get_tool_relay_info()

    _OAUTH_REFRESH_MIN_TTL_SEC = 30 * 60

    def _gemini_setup_credentials(self, workdir: str, pool_index: int = -1,
                            exclude_indices=None, user_id: str = "",
                            conversation_id: str = ""):
        """Write oauth_creds.json under <workdir>/.gemini/.

        Mirror of CC/codex _gemini_setup_credentials. Touchpoint differences:
          - File: <workdir>/.gemini/oauth_creds.json
          - Schema: {access_token, refresh_token, scope, token_type:"Bearer",
                     expiry_date} (Google standard OAuth2 format)
          - Refresh endpoint: oauth2.googleapis.com
        Everything else (proactive refresh, dead-slot removal, exclude_indices,
        round-robin start) is byte-for-byte identical.
        """
        from core.llm_client import LLMClientError
        import time as _time

        _api_key = getattr(self, 'api_key', '')
        if callable(_api_key):
            _api_key = _api_key()
        elif isinstance(_api_key, property):
            _api_key = ''
        if _api_key:
            logger.info("Gemini using API key (skipping OAuth credentials)")
            return

        svc_id = getattr(self, '_agent_service', '') or ''
        uid = user_id or getattr(self, '_user_id', '') or ''
        cid = conversation_id or getattr(self, '_conversation_id', '') or ''
        pool = _load_credentials_pool(svc_id, user_id=uid, conv_id=cid)
        if not pool:
            raise LLMClientError(
                "Gemini credentials not configured. "
                "Use the gemini Login button to authenticate.")
        _exclude = set(exclude_indices or ())

        if 0 <= pool_index < len(pool) and pool_index not in _exclude:
            indices = [pool_index] + [
                i for i in range(len(pool))
                if i != pool_index and i not in _exclude]
        else:
            with GeminiSessionMixin._pool_lock:
                start = GeminiSessionMixin._pool_counter % len(pool)
                GeminiSessionMixin._pool_counter += 1
            indices = [
                (start + i) % len(pool) for i in range(len(pool))
                if (start + i) % len(pool) not in _exclude]
        if not indices:
            raise LLMClientError(
                "All gemini credentials in the pool have already failed "
                "during this stream. Re-authenticate via the Login button.")

        dead_indices = []
        access_token = refresh_token = account = ""  # nosec B105
        expires_at = 0
        for _pidx in indices:
            cred = pool[_pidx]
            access_token = cred.get("access_token", "")
            refresh_token = cred.get("refresh_token", "")
            account = cred.get("account", "")
            expires_at = cred.get("expires_at", 0)
            if not access_token:
                dead_indices.append(_pidx)
                continue
            if expires_at:
                _exp_s = int(expires_at) / 1000 if int(expires_at) > 1e12 else int(expires_at)
                _remaining = _exp_s - _time.time()
                if _remaining < self._OAUTH_REFRESH_MIN_TTL_SEC and refresh_token:
                    logger.info("[gemini] pool[%d] %s — refreshing", _pidx,
                                "expired" if _remaining < 0 else f"expiring in {_remaining:.0f}s")
                    try:
                        new_tokens = self._gemini_refresh_oauth_token(refresh_token)
                        access_token = new_tokens["access_token"]
                        refresh_token = new_tokens.get("refresh_token", refresh_token)
                        expires_at = new_tokens["expires_at"]
                        _persist_tokens_to_service(
                            access_token, refresh_token, int(expires_at),
                            service_id=svc_id, pool_index=_pidx,
                            account=account, user_id=uid, conv_id=cid)
                    except Exception as e:
                        logger.warning(
                            "[gemini] pool[%d] refresh failed, dropping: %s",
                            _pidx, e)
                        dead_indices.append(_pidx)
                        continue
                elif _remaining < 0 and not refresh_token:
                    dead_indices.append(_pidx)
                    continue
            self._current_pool_index = _pidx
            if dead_indices:
                pool = [c for i, c in enumerate(pool) if i not in dead_indices]
                _save_credentials_pool(
                    pool, service_id=svc_id, user_id=uid, conv_id=cid)
            break
        else:
            pool = [c for i, c in enumerate(pool) if i not in dead_indices]
            _save_credentials_pool(
                pool, service_id=svc_id, user_id=uid, conv_id=cid)
            raise LLMClientError(
                "All gemini credentials expired or refresh failed. "
                "Re-authenticate via the Login button.")

        gemini_home = os.path.join(workdir, ".gemini")
        os.makedirs(gemini_home, exist_ok=True)
        creds_blob = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "scope": "https://www.googleapis.com/auth/cloud-platform openid email profile",
            "token_type": "Bearer",  # nosec B105
            "expiry_date": int(expires_at),
        }
        creds_path = os.path.join(gemini_home, "oauth_creds.json")
        with open(creds_path, "w", encoding="utf-8") as f:
            json.dump(creds_blob, f)
        os.chmod(creds_path, 0o600)
        # google_accounts.json maps the project hash to account; gemini
        # tolerates an empty stub if the account has been seen before.
        if account:
            accounts_path = os.path.join(gemini_home, "google_accounts.json")
            with open(accounts_path, "w", encoding="utf-8") as f:
                json.dump({"accounts": {account: {}}, "active": account}, f)
            os.chmod(accounts_path, 0o600)

    def _gemini_recover_tokens(self, workdir: str, user_id: str = "",
                               conversation_id: str = ""):
        """Read back tokens from <workdir>/.gemini/oauth_creds.json."""
        creds_path = os.path.join(workdir, ".gemini", "oauth_creds.json")
        if not os.path.exists(creds_path):
            return
        try:
            with open(creds_path, "r", encoding="utf-8") as f:
                blob = json.load(f)
            new_access = blob.get("access_token", "")
            new_refresh = blob.get("refresh_token", "")
            new_expiry = int(blob.get("expiry_date", 0) or 0)
            if not new_access:
                return
            _pidx = getattr(self, '_current_pool_index', -1)
            _current = self._gemini_resolve_service_tokens(
                pool_index=_pidx, user_id=user_id,
                conversation_id=conversation_id)
            if new_access == _current.get("access_token", ""):
                return
            _service_id = getattr(self, '_agent_service', '') or ''
            _persist_tokens_to_service(
                new_access, new_refresh, new_expiry,
                service_id=_service_id, pool_index=_pidx,
                user_id=user_id, conv_id=conversation_id)
            logger.info("[gemini] recovered refreshed tokens [pool:%d]", _pidx)
        except Exception as e:
            logger.debug("[gemini] token recovery failed: %s", e)
