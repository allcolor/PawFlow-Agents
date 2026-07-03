"""Codex OAuth token refresh, auth.json parsing, and pool persistence.

Extracted from codex_session.py to keep each module <=800 lines. The
CodexSessionMixin and the credential-pool CRUD helpers stay in codex_session;
this module holds the OAuth endpoint refresh, auth.json parsing, token
persistence, and the per-session workdir base. codex_session re-exports every
name here, so the public import path (core.llm_providers.codex_session) stays
unchanged and monkeypatch targets (e.g. _get_sessions_base) resolve as before.

Pool-CRUD back-references (_find_codex_service_id, _load_credentials_pool,
add_credential_to_pool, _save_credentials_pool) live in codex_session and are
reached via a deferred import inside _persist_tokens_to_service to avoid a
circular import and to keep honouring monkeypatches applied on codex_session.
"""

import json
import logging
import os
import time

logger = logging.getLogger(__name__)


class OAuthRejectedError(Exception):
    """OpenAI explicitly rejected the OAuth refresh credential.

    Only this error means the saved credential is dead and may be removed
    from the pool. Network errors, 5xx, 429, or malformed responses are
    transient and must not delete the user's login.
    """

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
        _err = ""
        try:
            _err = str(json.loads(resp_body).get("error", "") or "")
        except Exception:
            _err = ""
        _rejected = resp.status in (401, 403) or (
            resp.status == 400 and _err in (
                "invalid_grant", "unauthorized_client", "invalid_client"))
        if _rejected:
            raise OAuthRejectedError(
                f"Codex OAuth refresh rejected ({resp.status}): {resp_body[:200]}")
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
                                account: str = "", user_id: str = "",
                                conv_id: str = ""):
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
    # Pool-CRUD helpers live in codex_session; reach them via a deferred import
    # so this module imports cleanly and monkeypatches on codex_session apply.
    from core.llm_providers import codex_session as _facade
    sid = _facade._find_codex_service_id(service_id, user_id=user_id, conv_id=conv_id)
    if not sid:
        return
    pool = _facade._load_credentials_pool(sid, user_id=user_id, conv_id=conv_id)
    if not pool:
        _facade.add_credential_to_pool(
            access_token, refresh_token, expires_at,
            account=account, id_token=id_token, service_id=sid,
            user_id=user_id, conv_id=conv_id)
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
    _facade._save_credentials_pool(pool, sid, user_id=user_id, conv_id=conv_id)
    logger.info("[codex] credential updated in pool for '%s'", sid)


# Base directory for per-session codex workdirs — read dynamically so a
# test that monkey-patches paths.CODEX_SESSIONS_DIR sees the change.
def recover_tokens_from_workdir(workdir: str, service_id: str,
                               pool_index: int, user_id: str = "",
                               conv_id: str = "") -> bool:
    """Copy any codex-CLI-rotated OAuth token from
    <workdir>/.codex/auth.json back to the exact pool slot.

    Called from live-session teardown (idle sweep / shutdown / evict).
    OpenAI does NOT invalidate the old refresh_token on rotation, so for
    codex this is defense-in-depth rather than a logout fix (unlike CC /
    Anthropic). Targets the slot via the SESSION's own service_id /
    pool_index, not instance state. auth.json carries no TTL, so we stamp
    the same +1h default as parse_auth_json; the next refresh corrects it.
    """
    auth_path = os.path.join(workdir, ".codex", "auth.json")
    if not os.path.exists(auth_path):
        return False
    try:
        with open(auth_path, "r", encoding="utf-8") as f:
            blob = json.load(f)
        tokens = blob.get("tokens", {}) or {}
        new_access = tokens.get("access_token", "")
        new_refresh = tokens.get("refresh_token", "")
        new_id = tokens.get("id_token", "")
        if not new_access:
            return False
        expires_at = int((time.time() + 3600) * 1000)
        _persist_tokens_to_service(
            new_access, new_refresh, expires_at,
            service_id=service_id, pool_index=pool_index,
            id_token=new_id, user_id=user_id, conv_id=conv_id)
        logger.info(
            "[codex] recovered teardown tokens [pool:%s] for '%s'",
            pool_index, service_id)
        return True
    except Exception:
        logger.debug(
            "[codex] teardown token recovery failed", exc_info=True)
        return False


def _get_sessions_base():
    import core.paths as _p
    return str(_p.CODEX_SESSIONS_DIR.resolve())
