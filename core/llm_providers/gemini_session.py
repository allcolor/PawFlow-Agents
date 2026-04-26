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
import time

logger = logging.getLogger(__name__)

# Google's standard OAuth2 token endpoint. Same one used by gcloud, the
# Gemini CLI, and all GIS clients. Refresh is RFC 6749 standard.
_GEMINI_TOKEN_ENDPOINT_HOST = "oauth2.googleapis.com"
_GEMINI_TOKEN_ENDPOINT_PATH = "/token"
# The Gemini CLI client_id + secret pair (public client — these are NOT
# considered secret in the OAuth-installed-app threat model). Picked up
# from the publicly distributed CLI bundle. If Google rotates them we'll
# see the refresh return an error and we update the constants here.
_GEMINI_CLIENT_ID = (
    "681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib135j.apps.googleusercontent.com"
)
_GEMINI_CLIENT_SECRET = "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"


def _find_gemini_service_id(service_id: str = "") -> str:
    """Find a gemini LLM service ID in the registry."""
    if service_id:
        return service_id
    try:
        from core.service_registry import ServiceRegistry
        for sdef in ServiceRegistry.get_instance().resolve_by_type("llmConnection"):
            cfg = getattr(sdef, "config", {}) or {}
            if cfg.get("provider") == "gemini":
                return sdef.service_id
    except Exception:
        pass
    return ""


def _load_credentials_pool(service_id: str = "") -> list:
    from core.secrets import get_secrets_manager
    sid = _find_gemini_service_id(service_id)
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
    from core.secrets import get_secrets_manager
    sid = _find_gemini_service_id(service_id)
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
                            service_id: str = ""):
    pool = _load_credentials_pool(service_id)
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
            return
    pool.append({
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": int(expires_at),
        "account": account,
        "added_at": int(time.time()),
    })
    _save_credentials_pool(pool, service_id)
    logger.info("[gemini] credential added to pool (now %d) for '%s'",
                len(pool), _find_gemini_service_id(service_id))


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
