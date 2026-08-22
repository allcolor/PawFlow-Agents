"""Access tokens for an LLM service running in auth_mode=oauth.

One resolution point so every provider's auth header reads its credential the
same way: a static key, or a live access token out of an encrypted pool that is
refreshed when it has expired.

Refresh is serialised per pool. Several agents share one pool and can notice
the same expiry in the same instant; identity providers that rotate refresh
tokens revoke the previous one when they issue a new one, so a concurrent
double refresh leaves the slower writer storing a token the provider has
already killed -- and the pool is then dead until someone logs in again. The
lock costs one agent a few milliseconds once an hour, which is the cheaper
side of that trade by a wide margin.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: Refresh a token this many seconds before it actually expires, so a request
#: cannot leave with a credential that dies in flight.
EXPIRY_MARGIN_SECONDS = 120

_POOL_LOCKS: Dict[str, threading.Lock] = {}
_POOL_LOCKS_GUARD = threading.Lock()


def pool_lock(service_id: str) -> threading.Lock:
    """Return the process-wide lock guarding one credential pool."""
    key = str(service_id or "")
    with _POOL_LOCKS_GUARD:
        lock = _POOL_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _POOL_LOCKS[key] = lock
        return lock


def _secrets_path():
    from core.paths import GLOBAL_SECRETS_FILE
    return GLOBAL_SECRETS_FILE


def load_pool(service_id: str) -> List[Dict[str, Any]]:
    """Decrypt and return the credential pool for a credential service."""
    if not service_id:
        return []
    from core.secrets import get_secrets_manager
    from services.llm_credential_oauth import credential_pool_secret_key

    path = _secrets_path()
    if not path.exists():
        return []
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.debug("[oauth-cred] unreadable secrets file", exc_info=True)
        return []
    key = credential_pool_secret_key(service_id)
    if key not in existing:
        return []
    try:
        pool = json.loads(get_secrets_manager().decrypt(existing[key]))
    except Exception:
        logger.debug("[oauth-cred] undecryptable pool %s", service_id,
                     exc_info=True)
        return []
    return pool if isinstance(pool, list) else []


def save_pool(service_id: str, pool: List[Dict[str, Any]]) -> None:
    """Encrypt and store the credential pool. Call under ``pool_lock``."""
    if not service_id:
        return
    from core.secrets import get_secrets_manager
    from services.llm_credential_oauth import credential_pool_secret_key

    path = _secrets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
    existing[credential_pool_secret_key(service_id)] = (
        get_secrets_manager().encrypt(json.dumps(pool)))
    path.write_text(json.dumps(existing, indent=2), encoding="utf-8")


def _expired(credential: Dict[str, Any], *, margin: int = EXPIRY_MARGIN_SECONDS) -> bool:
    try:
        expires_at = float(credential.get("expires_at") or 0)
    except (TypeError, ValueError):
        return True
    if expires_at <= 0:
        # No expiry recorded: treat as long-lived rather than refreshing on
        # every call, which would rotate a working token for nothing.
        return False
    return expires_at <= time.time() + margin


def _refresh(credential: Dict[str, Any], service_config: Dict[str, Any],
             endpoints: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """Exchange the refresh token for a new access token, or return None.

    Reuses the existing OAuth client rather than opening a second one: a
    duplicate token exchange is how the two implementations start disagreeing
    about the same identity provider.
    """
    refresh_token = str(credential.get("refresh_token") or "").strip()
    if not refresh_token or not endpoints.get("token_url"):
        return None
    from services.auth_providers.generic_oauth import GenericOAuthProvider

    provider = GenericOAuthProvider({
        "name": "llm-credential-pool",
        "client_id": service_config.get("client_id", ""),
        "client_secret": service_config.get("client_secret", ""),
        "token_url": endpoints.get("token_url", ""),
        "authorize_url": endpoints.get("authorize_url", ""),
        "scope": endpoints.get("scope", ""),
    })
    result = provider.refresh_access_token(refresh_token)
    if not getattr(result, "success", False):
        logger.warning("[oauth-cred] refresh failed: %s",
                       getattr(result, "error", "unknown error"))
        return None
    return {
        **credential,
        "access_token": result.access_token,
        "refresh_token": result.refresh_token or refresh_token,
        "expires_at": result.token_expires_at,
    }


def access_token(service_id: str, service_config: Dict[str, Any],
                 endpoints: Dict[str, str]) -> str:
    """Return a usable access token from the pool, refreshing if needed.

    Empty string when the pool holds nothing usable -- the caller decides
    whether that is fatal, because the answer differs between a request and a
    config validation.
    """
    if not service_id:
        return ""
    with pool_lock(service_id):
        pool = load_pool(service_id)
        if not pool:
            return ""
        for index, credential in enumerate(pool):
            if not isinstance(credential, dict):
                continue
            token = str(credential.get("access_token") or "").strip()
            if token and not _expired(credential):
                return token
            refreshed = _refresh(credential, service_config, endpoints)
            if refreshed and refreshed.get("access_token"):
                pool[index] = refreshed
                save_pool(service_id, pool)
                logger.info(
                    "[oauth-cred] refreshed the access token for pool %s",
                    service_id)
                return str(refreshed["access_token"])
            if token:
                # Expired and unrefreshable: hand it over anyway rather than
                # failing locally. The provider's 401 is the authoritative
                # answer and carries a message worth surfacing.
                return token
        return ""
