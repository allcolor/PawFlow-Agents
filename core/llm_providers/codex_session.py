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
import time

logger = logging.getLogger(__name__)

# Auth0 endpoint used by the Codex CLI for the OAuth PKCE refresh flow.
# Discovered via the publicly documented behaviour of `codex login` and
# the openai/codex source. Refresh body is RFC 6749 standard.
_CODEX_TOKEN_ENDPOINT_HOST = "auth.openai.com"
_CODEX_TOKEN_ENDPOINT_PATH = "/oauth/token"
# The Codex CLI client_id, embedded in the binary. Picked up from the
# documented OAuth PKCE flow; if OpenAI rotate this we'll see refresh
# 401 + need to update the constant. Kept here (not env) because the
# token endpoint will reject a wrong client_id outright.
_CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"


def _find_codex_service_id(service_id: str = "") -> str:
    """Find a codex LLM service ID in the registry (or echo back the arg)."""
    if service_id:
        return service_id
    try:
        from core.service_registry import ServiceRegistry
        for sdef in ServiceRegistry.get_instance().resolve_by_type("llmConnection"):
            cfg = getattr(sdef, "config", {}) or {}
            if cfg.get("provider") == "codex":
                return sdef.service_id
    except Exception:
        pass
    return ""


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


def add_credential_to_pool(access_token: str, refresh_token: str,
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
