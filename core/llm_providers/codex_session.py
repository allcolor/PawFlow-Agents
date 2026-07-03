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

from core.agent_prompt_policy import CLI_MCP_SYSTEM_PROMPT

# OAuth refresh / auth.json parsing / token persistence / sessions-base live
# in _codex_credentials to keep this module <=800 lines; re-exported so
# core.llm_providers.codex_session stays the public path and monkeypatch
# targets (_get_sessions_base, ...) resolve here.
from core.llm_providers._codex_credentials import (  # noqa: F401
    _get_sessions_base,
    _persist_tokens_to_service,
    _validate_oauth_token,
    OAuthRejectedError,
    parse_auth_json,
    recover_tokens_from_workdir,
    refresh_oauth_token,
)

logger = logging.getLogger(__name__)


def _find_codex_service_id(service_id: str = "", user_id: str = "",
                           conv_id: str = "") -> str:
    """Find the credential-pool owner for Codex OAuth."""
    try:
        from services.llm_credential_oauth import (
            credential_service_id_from_llm_service,
            resolve_credential_service_id,
        )
        return (resolve_credential_service_id(
            "codex-app-server", service_id, user_id=user_id, conv_id=conv_id)
            or credential_service_id_from_llm_service(
                "codex-app-server", service_id, user_id=user_id, conv_id=conv_id))
    except Exception:
        return ""


def _load_credentials_pool(service_id: str = "", user_id: str = "",
                           conv_id: str = "") -> list:
    """Load the credentials pool for a codex service."""
    from core.secrets import get_secrets_manager
    sid = _find_codex_service_id(service_id, user_id=user_id, conv_id=conv_id)
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
    from core.secrets import get_secrets_manager
    sid = _find_codex_service_id(service_id, user_id=user_id, conv_id=conv_id)
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
                            id_token: str = "", user_id: str = "",
                            conv_id: str = ""):
    """Add (or update on refresh_token match) a credential.

    `id_token` is the OAuth ID JWT codex expects in auth.json for the
    ChatGPT account claim — codex CLI rejects auth.json without a valid
    id_token ("invalid ID token format"). Stored alongside access/refresh
    so we can reproduce the exact auth.json shape codex wrote on disk.
    """
    sid = _find_codex_service_id(service_id, user_id=user_id, conv_id=conv_id)
    if not sid:
        raise ValueError(f"Codex credential service '{service_id}' not found")
    pool = _load_credentials_pool(service_id, user_id=user_id, conv_id=conv_id)
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
            _save_credentials_pool(
                pool, service_id, user_id=user_id, conv_id=conv_id)
            return
    pool.append({
        "access_token": access_token,
        "refresh_token": refresh_token,
        "id_token": id_token,
        "expires_at": int(expires_at),
        "account": account,
        "added_at": int(time.time()),
    })
    _save_credentials_pool(pool, service_id, user_id=user_id, conv_id=conv_id)
    logger.info("[codex] credential added to pool (now %d) for '%s'",
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

    # Per-slot refresh locks — serialize concurrent refreshes of the SAME
    # pool slot so two sessions sharing a credential can't both POST the
    # same single-use refresh_token (the loser would error and drop a slot
    # the winner just rotated). Mirror of ClaudeCodeSessionMixin; keyed by
    # (service_id, pool_index), private to the codex pool.
    _codex_refresh_locks_guard = __import__('threading').Lock()
    _codex_refresh_locks = {}

    @classmethod
    def _codex_slot_refresh_lock(cls, service_id: str, pool_index: int):
        """Return the process-wide lock guarding refresh of one pool slot."""
        key = (service_id, pool_index)
        with cls._codex_refresh_locks_guard:
            lock = cls._codex_refresh_locks.get(key)
            if lock is None:
                lock = __import__('threading').Lock()
                cls._codex_refresh_locks[key] = lock
            return lock

    def _codex_resolve_service_tokens(self, pool_index: int = -1,
                                      user_id: str = "",
                                      conversation_id: str = "") -> dict:
        """Resolve codex tokens from the credentials pool. Mirror of CC.

        Returns {"access_token", "refresh_token", "expires_at",
                 "pool_index", "id_token", "account"}.
        """
        import time as _t
        svc_id = getattr(self, '_agent_service', '') or ''
        uid = user_id or getattr(self, '_user_id', '') or ''
        cid = conversation_id or getattr(self, '_conversation_id', '') or ''
        pool = _load_credentials_pool(svc_id, user_id=uid, conv_id=cid)
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

    def _codex_force_refresh_pool_entry(self, pool_index: int,
                                        user_id: str = "",
                                        conversation_id: str = "") -> bool:
        """Force-refresh access_token for pool[pool_index]. Mirror of CC.

        On failure, drops the slot from the pool entirely (a credential
        whose refresh is broken is dead and must not be re-attempted).
        """
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
                    "[codex force-refresh] removed dead pool[%d] (%s); "
                    "remaining=%d", pool_index, reason, len(current))

        if not refresh_token:
            _drop_dead_slot("no refresh_token")
            return False
        try:
            new = self._codex_refresh_oauth_token_coordinated(
                refresh_token, service_id=svc_id, pool_index=pool_index,
                user_id=uid, conv_id=cid)
        except OAuthRejectedError as e:
            logger.warning("[codex force-refresh] pool[%d] failed: %s",
                           pool_index, e)
            _drop_dead_slot(f"refresh error: {e}")
            return False
        except Exception as e:
            logger.warning(
                "[codex force-refresh] pool[%d] transient failure, "
                "credential kept: %s", pool_index, e)
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
            service_id=svc_id, pool_index=pool_index, id_token=_new_id,
            user_id=uid, conv_id=cid)
        logger.info("[codex force-refresh] pool[%d] access_token renewed",
                    pool_index)
        return True

    @staticmethod
    def _codex_refresh_oauth_token(refresh_token: str) -> dict:
        """Wrapper around module-level refresh_oauth_token, signature-compat
        with ClaudeCodeSessionMixin._refresh_oauth_token."""
        return refresh_oauth_token(refresh_token)

    def _codex_refresh_oauth_token_coordinated(self, refresh_token: str, *,
                                               service_id: str, pool_index: int,
                                               user_id: str, conv_id: str) -> dict:
        """Serialized, idempotent refresh of one pool slot's single-use token.

        Holds the per-slot lock so two sessions sharing the slot can't both
        POST the same refresh_token (the loser would error and drop a slot
        the winner just rotated). After taking the lock we re-read the pool:
        if a peer already rotated this slot, the freshly-persisted token
        (incl. id_token/account) is returned with NO network call. Mirror of
        ClaudeCodeSessionMixin._refresh_oauth_token_coordinated.
        """
        lock = self._codex_slot_refresh_lock(service_id, pool_index)
        with lock:
            pool = _load_credentials_pool(service_id, user_id=user_id, conv_id=conv_id)
            if 0 <= pool_index < len(pool):
                slot = pool[pool_index]
                if slot.get("access_token") and slot.get("refresh_token") != refresh_token:
                    logger.info(
                        "[codex] OAuth token [pool:%d] already refreshed by a "
                        "peer session; reusing rotated credential (no network call)",
                        pool_index)
                    return {
                        "access_token": slot.get("access_token", ""),
                        "refresh_token": slot.get("refresh_token", ""),
                        "expires_at": slot.get("expires_at", 0),
                        "id_token": slot.get("id_token", ""),
                        "account": slot.get("account", ""),
                    }
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

                def _repl(m):
                    return m.group(1) + "host.docker.internal" + (m.group(2) or '')

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
                            exclude_indices=None, user_id: str = "",
                            conversation_id: str = ""):
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
        uid = user_id or getattr(self, '_user_id', '') or ''
        cid = conversation_id or getattr(self, '_conversation_id', '') or ''
        pool = _load_credentials_pool(svc_id, user_id=uid, conv_id=cid)
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
        had_transient = False
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
                        new_tokens = self._codex_refresh_oauth_token_coordinated(
                            refresh_token, service_id=svc_id, pool_index=_pidx,
                            user_id=uid, conv_id=cid)
                        access_token = new_tokens["access_token"]
                        refresh_token = new_tokens.get("refresh_token", refresh_token)
                        id_token = new_tokens.get("id_token", id_token) or id_token
                        expires_at = new_tokens["expires_at"]
                        _persist_tokens_to_service(
                            access_token, refresh_token, int(expires_at),
                            service_id=svc_id, pool_index=_pidx,
                            id_token=id_token, account=account,
                            user_id=uid, conv_id=cid)
                        pool[_pidx]["access_token"] = access_token
                        pool[_pidx]["refresh_token"] = refresh_token
                        pool[_pidx]["id_token"] = id_token
                        pool[_pidx]["expires_at"] = int(expires_at)
                    except OAuthRejectedError as e:
                        logger.warning(
                            "[codex] pool[%d] refresh rejected, dropping: %s",
                            _pidx, e)
                        dead_indices.append(_pidx)
                        continue
                    except Exception as e:
                        had_transient = True
                        if _remaining < 0:
                            logger.warning(
                                "[codex] pool[%d] expired; refresh temporarily "
                                "failed, credential kept: %s", _pidx, e)
                            continue
                        logger.warning(
                            "[codex] pool[%d] proactive refresh temporarily "
                            "failed, using current token: %s", _pidx, e)
                elif _remaining < 0 and not refresh_token:
                    logger.warning(
                        "[codex] pool[%d] expired, no refresh token", _pidx)
                    dead_indices.append(_pidx)
                    continue
            self._current_pool_index = _pidx - sum(
                1 for _dead_idx in dead_indices if _dead_idx < _pidx)
            if dead_indices:
                pool = [c for i, c in enumerate(pool) if i not in dead_indices]
                _save_credentials_pool(
                    pool, service_id=svc_id, user_id=uid, conv_id=cid)
                logger.info("[codex] removed %d dead credential(s)",
                            len(dead_indices))
            break
        else:
            pool = [c for i, c in enumerate(pool) if i not in dead_indices]
            _save_credentials_pool(
                pool, service_id=svc_id, user_id=uid, conv_id=cid)
            if had_transient:
                raise LLMClientError(
                    "Codex OAuth refresh is temporarily unavailable "
                    "(network/server error). Your saved credentials are "
                    "intact — retry in a moment.")
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

    def _codex_recover_tokens(self, workdir: str, user_id: str = "",
                              conversation_id: str = ""):
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
            _current = self._codex_resolve_service_tokens(
                pool_index=_pidx, user_id=user_id,
                conversation_id=conversation_id)
            if new_access == _current.get("access_token", ""):
                return
            _service_id = getattr(self, '_agent_service', '') or ''
            _persist_tokens_to_service(
                new_access, new_refresh, _current.get("expires_at", 0),
                service_id=_service_id, pool_index=_pidx,
                id_token=new_id, user_id=user_id, conv_id=conversation_id)
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
