"""Claude Code OAuth credential pool, token persistence, and session-base helpers.

Extracted from claude_code_session.py to keep each module <=800 lines. The
ClaudeCodeSessionMixin and the OAuthRejectedError class stay in
claude_code_session; this module holds the free credential-pool CRUD, OAuth
token validation/persistence, workdir token recovery, relay-proxy URL
transform, and the per-session workdir base.

claude_code_session re-exports every name here, so the public import path
(core.llm_providers.claude_code_session) is unchanged and monkeypatch targets
(_find_cc_service_id, _load_credentials_pool, _save_credentials_pool,
_get_sessions_base, ...) resolve as before.

The pool functions reference each other (e.g. add_credential_to_pool ->
_find_cc_service_id) through a deferred import of the claude_code_session
facade, so that monkeypatches applied on claude_code_session.<name> keep
affecting these callers exactly as they did in the original single module.
"""

import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def _maybe_transform_relay_proxy_url(url: str, user_id: str = "",
                                     conv_id: str = "") -> Optional[str]:
    """Backward-compatible wrapper around the central relay URL helper."""
    from core.relay_proxy_url import maybe_transform_relay_proxy_url
    return maybe_transform_relay_proxy_url(url, user_id=user_id, conv_id=conv_id)


def _find_cc_service_id(service_id: str = "", user_id: str = "",
                        conv_id: str = "") -> str:
    """Find the credential-pool owner for Claude Code OAuth."""
    try:
        from services.llm_credential_oauth import (
            credential_service_id_from_llm_service,
            resolve_credential_service_id,
        )
        return (resolve_credential_service_id(
            "claude-code", service_id, user_id=user_id, conv_id=conv_id)
            or credential_service_id_from_llm_service(
                "claude-code", service_id, user_id=user_id, conv_id=conv_id))
    except Exception:
        return ""


def _load_credentials_pool(service_id: str = "", user_id: str = "",
                           conv_id: str = "") -> list:
    """Load the credentials pool for a CC service.

    Returns list of {"access_token", "refresh_token", "expires_at", "account", "added_at"}.
    """
    from core.llm_providers import claude_code_session as _facade
    from core.secrets import get_secrets_manager

    sid = _facade._find_cc_service_id(service_id, user_id=user_id, conv_id=conv_id)
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
    """Save the credentials pool to secrets (encrypted)."""
    from core.llm_providers import claude_code_session as _facade
    from core.secrets import get_secrets_manager

    sid = _facade._find_cc_service_id(service_id, user_id=user_id, conv_id=conv_id)
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
                           service_id: str = "", user_id: str = "",
                           conv_id: str = ""):
    """Add a credential to the pool."""
    from core.llm_providers import claude_code_session as _facade
    import time
    sid = _facade._find_cc_service_id(service_id, user_id=user_id, conv_id=conv_id)
    if not sid:
        raise ValueError(f"Claude Code credential service '{service_id}' not found")
    pool = _facade._load_credentials_pool(service_id, user_id=user_id, conv_id=conv_id)
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
            _facade._save_credentials_pool(
                pool, service_id, user_id=user_id, conv_id=conv_id)
            logger.info("[claude-code] credential updated in pool (slot %d) for '%s'",
                        i, sid)
            return
    pool.append({
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": int(expires_at),
        "account": account,
        "added_at": int(time.time()),
    })
    _facade._save_credentials_pool(pool, service_id, user_id=user_id, conv_id=conv_id)
    logger.info("[claude-code] credential added to pool (now %d) for '%s'",
                len(pool), sid)


def remove_credential_from_pool(index: int, service_id: str = "",
                                user_id: str = "", conv_id: str = "") -> bool:
    """Remove a credential from the pool by index (0-based)."""
    from core.llm_providers import claude_code_session as _facade
    pool = _facade._load_credentials_pool(service_id, user_id=user_id, conv_id=conv_id)
    if 0 <= index < len(pool):
        pool.pop(index)
        _facade._save_credentials_pool(pool, service_id, user_id=user_id, conv_id=conv_id)
        return True
    return False


def reset_credentials_pool(service_id: str = "", user_id: str = "",
                           conv_id: str = ""):
    """Clear all credentials from the pool."""
    from core.llm_providers import claude_code_session as _facade
    _facade._save_credentials_pool([], service_id, user_id=user_id, conv_id=conv_id)


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
                               pool_index: int = -1, user_id: str = "",
                               conv_id: str = ""):
    """Update a credential in the pool (after refresh).

    If pool_index >= 0, updates that specific slot. Otherwise finds
    the matching credential by refresh_token.

    Refuses to persist a token that fails basic validation (empty
    fields, expires_at in the past) — better to keep the old broken
    token and let _setup_credentials drop the slot than to pollute
    the pool with a token we already know is dead.
    """
    from core.llm_providers import claude_code_session as _facade
    if not _validate_oauth_token(access_token, refresh_token, expires_at):
        logger.warning(
            "[claude-code] refusing to persist invalid token "
            "(access_token=%r, expires_at=%r) to pool[%s] — keeping old",
            bool(access_token), expires_at, pool_index)
        return
    sid = _facade._find_cc_service_id(service_id, user_id=user_id, conv_id=conv_id)
    if not sid:
        return
    pool = _facade._load_credentials_pool(sid, user_id=user_id, conv_id=conv_id)
    if not pool:
        # No pool yet — create one
        add_credential_to_pool(access_token, refresh_token, expires_at,
                               service_id=sid, user_id=user_id, conv_id=conv_id)
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
            logger.warning(
                "[claude-code] refusing to persist refreshed token: "
                "no matching credential in pool '%s'", sid)
            return

    _facade._save_credentials_pool(pool, sid, user_id=user_id, conv_id=conv_id)
    logger.info("[claude-code] credential updated in pool for '%s'", sid)


def recover_tokens_from_workdir(workdir: str, service_id: str,
                               pool_index: int, user_id: str = "",
                               conv_id: str = "") -> bool:
    """Read CC-refreshed OAuth tokens back from <workdir>/.credentials.json
    and persist them to the exact pool slot.

    Called from live-session teardown (idle sweep / shutdown / evict),
    where the claude CLI inside the long-lived container may have rotated
    the OAuth token on its own. Anthropic's refresh_token is single-use:
    issuing a new one invalidates the old, so if teardown drops the
    container without copying the renewed credential back, the pool keeps
    a dead refresh_token and the user is logged out on the next turn.

    Unlike the instance method ``_recover_tokens`` (which reads
    ``self._current_pool_index`` / ``self._agent_service``), this targets
    the slot via the SESSION's own ``service_id`` / ``pool_index`` -- the
    sweeper tears down arbitrary sessions, so instance state is not a
    reliable source for which credential to update.

    Returns True if a token was recovered and persisted, else False.
    """
    creds_path = os.path.join(workdir, ".credentials.json")
    if not os.path.exists(creds_path):
        return False
    try:
        with open(creds_path, "r", encoding="utf-8") as f:
            creds = json.load(f)
        oauth = creds.get("claudeAiOauth", {})
        new_access = oauth.get("accessToken", "")
        new_refresh = oauth.get("refreshToken", "")
        new_expires = oauth.get("expiresAt", 0)
        if not new_access:
            return False
        # _persist_tokens_to_service refuses invalid tokens (empty /
        # already-expired) and addresses pool_index directly when >= 0.
        _persist_tokens_to_service(
            new_access, new_refresh, new_expires,
            service_id=service_id, pool_index=pool_index,
            user_id=user_id, conv_id=conv_id)
        logger.info(
            "[claude-code] recovered teardown tokens [pool:%s] for '%s'",
            pool_index, service_id)
        return True
    except Exception:
        logger.debug(
            "[claude-code] teardown token recovery failed", exc_info=True)
        return False


# Base directory for per-session Claude Code workdirs — read dynamically
def _get_sessions_base():
    import core.paths as _p
    return str(_p.CLAUDE_SESSIONS_DIR.resolve())
