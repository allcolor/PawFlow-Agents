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
import stat
import uuid
from typing import Optional

from core.llm_providers.cli_shared import (
    credentials_pool_lock, note_token_recovered, token_recovery_is_stale)

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
    """Add a credential to the pool.

    Under the shared pool lock like every other read-modify-write: a login
    lands while the sweepers are running, and an unlocked append rewrites the
    pool from a snapshot taken before a concurrent token recovery.
    """
    from core.llm_providers import claude_code_session as _facade
    import time
    with credentials_pool_lock():
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
    with credentials_pool_lock():
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
                               conv_id: str = "") -> bool:
    """Update a credential in the pool (after refresh). True if it landed.

    If pool_index >= 0, updates that specific slot. Otherwise finds
    the matching credential by refresh_token.

    Refuses to persist a token that fails basic validation (empty
    fields, expires_at in the past) — better to keep the old broken
    token and let _setup_credentials drop the slot than to pollute
    the pool with a token we already know is dead.

    The whole load/mutate/save cycle runs under the shared pool lock: the
    pool is rewritten whole, so an interleaved writer's snapshot would
    resurrect the token this call is replacing. The return value is what
    tells a caller whether it may memoise the write -- every early return
    here is a slot left untouched.
    """
    from core.llm_providers import claude_code_session as _facade
    if not _validate_oauth_token(access_token, refresh_token, expires_at):
        logger.warning(
            "[claude-code] refusing to persist invalid token "
            "(access_token=%r, expires_at=%r) to pool[%s] — keeping old",
            bool(access_token), expires_at, pool_index)
        return False
    with credentials_pool_lock():
        sid = _facade._find_cc_service_id(service_id, user_id=user_id, conv_id=conv_id)
        if not sid:
            return False
        pool = _facade._load_credentials_pool(sid, user_id=user_id, conv_id=conv_id)
        if not pool:
            # No pool yet — create one
            add_credential_to_pool(access_token, refresh_token, expires_at,
                                   service_id=sid, user_id=user_id, conv_id=conv_id)
            return True

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
                return False

        _facade._save_credentials_pool(pool, sid, user_id=user_id, conv_id=conv_id)
    logger.info("[claude-code] credential updated in pool for '%s'", sid)
    return True


def _pool_slot(service_id: str, pool_index: int, user_id: str = "",
               conv_id: str = "") -> dict:
    """The credential at ``pool_index``, or {} when the pool has none there."""
    from core.llm_providers import claude_code_session as _facade
    pool = _facade._load_credentials_pool(
        service_id, user_id=user_id, conv_id=conv_id)
    if 0 <= pool_index < len(pool):
        return pool[pool_index]
    return {}


def _expiry(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def recover_tokens_from_workdir(workdir: str, service_id: str,
                               pool_index: int, user_id: str = "",
                               conv_id: str = "") -> bool:
    """Read CC-refreshed OAuth tokens back from <workdir>/.credentials.json
    and persist them to the exact pool slot.

    Called from live-session teardown (idle sweep / shutdown / evict) AND
    from the sweeper tick for sessions that are still running, where the
    claude CLI inside the long-lived container may have rotated the OAuth
    token on its own. Anthropic's refresh_token is single-use:
    issuing a new one invalidates the old, so if teardown drops the
    container without copying the renewed credential back, the pool keeps
    a dead refresh_token and the user is logged out on the next turn.
    Teardown is not a reliable moment to be the only one: a server killed
    hard (an update whose stop grace expires) never reaches it at all.

    Unlike the instance method ``_recover_tokens`` (which reads
    ``self._current_pool_index`` / ``self._agent_service``), this targets
    the slot via the SESSION's own ``service_id`` / ``pool_index`` -- the
    sweeper tears down arbitrary sessions, so instance state is not a
    reliable source for which credential to update.

    Returns True if a token was recovered and persisted, else False --
    including when this exact token was already copied back, which is the
    ordinary case on a periodic call.

    The stale check, the persist and the memo are one critical section. Split
    apart, two ticks both read "not stale", both write, and the loser's
    snapshot puts back the token the winner had just replaced.

    Several containers share one login, so a workdir can hold a token the
    pool has already moved past: a sibling CLI rotated it after this file was
    written. That token's refresh_token is dead, and copying it back would
    log every container on the slot out. Only a token that expires LATER than
    the pool's is copied; an older one is remembered as handled.
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
        _signature = f"{new_access}\x1f{new_refresh}\x1f{new_expires}"
        with credentials_pool_lock():
            if token_recovery_is_stale(workdir, service_id, pool_index, _signature):
                return False
            _pooled = _expiry(_pool_slot(
                service_id, pool_index, user_id, conv_id).get("expires_at"))
            if _pooled > _expiry(new_expires):
                note_token_recovered(workdir, service_id, pool_index, _signature)
                logger.info(
                    "[claude-code] workdir token older than pool[%s] for "
                    "'%s'; not copied back", pool_index, service_id)
                return False
            # _persist_tokens_to_service refuses invalid tokens (empty /
            # already-expired) and addresses pool_index directly when >= 0.
            if not _persist_tokens_to_service(
                    new_access, new_refresh, new_expires,
                    service_id=service_id, pool_index=pool_index,
                    user_id=user_id, conv_id=conv_id):
                # Nothing reached the pool, so nothing may be memoised: the
                # next tick has to try again or the rotation is lost.
                return False
            note_token_recovered(workdir, service_id, pool_index, _signature)
        logger.info(
            "[claude-code] recovered teardown tokens [pool:%s] for '%s'",
            pool_index, service_id)
        return True
    except Exception:
        logger.debug(
            "[claude-code] teardown token recovery failed", exc_info=True)
        return False


def _replace_file_atomically(path: str, text: str) -> None:
    """Rewrite ``path`` so a concurrent reader sees the old or the new file.

    The CLI inside the container reads this file whenever it checks its
    token, so a half-written file must never be visible. The new file keeps
    the old one's mode and owner: the CLI runs as the launcher's uid and may
    have rewritten the file as that uid. Where the owner cannot be carried
    over, the file is rewritten in place instead, which keeps it readable.
    """
    st = os.stat(path)
    tmp = f"{path}.pawflow-{uuid.uuid4().hex}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, stat.S_IMODE(st.st_mode))
        if hasattr(os, "chown"):
            try:
                os.chown(tmp, st.st_uid, st.st_gid)
            except OSError:
                if (st.st_uid, st.st_gid) != (os.getuid(), os.getgid()):
                    os.unlink(tmp)
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(text)
                    return
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def push_pool_tokens_to_workdir(workdir: str, service_id: str,
                                pool_index: int, user_id: str = "",
                                conv_id: str = "") -> bool:
    """Hand a running CLI the newest token its login has in the pool.

    The reverse of ``recover_tokens_from_workdir``. Containers sharing one
    login each got their own copy of the slot at launch. When one CLI rotates
    the single-use refresh_token, the others keep the dead one and fail with
    authentication_failed the next time they renew. Claude Code stats
    ``.credentials.json`` before every renewal and re-reads it when its mtime
    changed, then skips the renewal when the token on disk is still valid --
    so rewriting the file is enough; the CLI needs no restart.

    Writes only when the pool's token expires later than the file's and is
    itself valid. Returns True when the file was rewritten.
    """
    if pool_index < 0:
        return False
    creds_path = os.path.join(workdir, ".credentials.json")
    if not os.path.exists(creds_path):
        return False
    try:
        with credentials_pool_lock():
            slot = _pool_slot(service_id, pool_index, user_id, conv_id)
            access = slot.get("access_token", "")
            refresh = slot.get("refresh_token", "")
            expires = _expiry(slot.get("expires_at"))
            if not _validate_oauth_token(access, refresh, expires):
                return False
            with open(creds_path, "r", encoding="utf-8") as f:
                creds = json.load(f)
            oauth = creds.get("claudeAiOauth")
            if not isinstance(oauth, dict):
                return False
            if _expiry(oauth.get("expiresAt")) >= expires:
                return False
            oauth["accessToken"] = access
            oauth["refreshToken"] = refresh
            oauth["expiresAt"] = expires
            _replace_file_atomically(creds_path, json.dumps(creds))
            # What the file now holds is what the pool holds: the next
            # recovery must not count it as a rotation to copy back.
            note_token_recovered(workdir, service_id, pool_index,
                                 f"{access}\x1f{refresh}\x1f{expires}")
        logger.info(
            "[claude-code] pushed pool[%s] token of '%s' to a live session",
            pool_index, service_id)
        return True
    except Exception:
        logger.warning(
            "[claude-code] pushing pool[%s] token to %s failed",
            pool_index, workdir, exc_info=True)
        return False


def refresh_pool_slot_if_expiring(service_id: str, pool_index: int,
                                  user_id: str = "",
                                  conv_id: str = "") -> bool:
    """Renew one slot centrally before the CLIs sharing it renew it themselves.

    Every container on a login got the same expiry, so their CLIs reach their
    own renewal threshold together and race with one single-use
    refresh_token: the loser is logged out. PawFlow renews earlier (the same
    margin as a launch, ``_OAUTH_REFRESH_MIN_TTL_SEC``) under the per-slot
    lock, and the caller pushes the result to every container on the slot.

    Only when the pool allows PawFlow-managed refresh. A rejected or failed
    renewal changes nothing here: the slot is left to the launch path, which
    owns dropping dead credentials. Returns True when the slot was renewed.
    """
    import time as _time
    from core.llm_providers import claude_code_session as _facade
    from services.llm_credential_oauth import credential_pool_allows_refresh
    if pool_index < 0:
        return False
    try:
        if not credential_pool_allows_refresh(
                service_id, user_id=user_id, conv_id=conv_id):
            return False
        slot = _pool_slot(service_id, pool_index, user_id, conv_id)
        refresh = slot.get("refresh_token", "")
        expires = _expiry(slot.get("expires_at"))
        if not refresh or not expires:
            return False
        mixin = _facade.ClaudeCodeSessionMixin
        remaining = expires / 1000 - _time.time()
        if remaining >= mixin._OAUTH_REFRESH_MIN_TTL_SEC:
            return False
        logger.info("[claude-code] pool[%s] of '%s' expires in %.0fs; "
                    "renewing before its CLIs do", pool_index, service_id,
                    remaining)
        mixin()._refresh_oauth_token_coordinated(
            refresh, service_id=service_id, pool_index=pool_index,
            user_id=user_id, conv_id=conv_id)
        return True
    except Exception as e:
        logger.warning("[claude-code] central renewal of pool[%s] failed: %s",
                       pool_index, e)
        return False


# Base directory for per-session Claude Code workdirs — read dynamically
def _get_sessions_base():
    import core.paths as _p
    return str(_p.CLAUDE_SESSIONS_DIR.resolve())
