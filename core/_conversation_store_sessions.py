"""ConversationStore metadata/extras getters + claude-session invalidation + bindings."""

import json
import logging
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import core.paths as _paths

logger = logging.getLogger(__name__)
# Split out of conversation_store.py for the <=800-line rule; composed back into
# ConversationStore (invariant 2: MRO/shared state on the host).

from core._conversation_store_base import (  # noqa: F401,E402
    _CTX_CACHE_MAX_MESSAGES, _CTX_CACHE_MAX_CHARS, _CTX_CACHE_MAX_CONVS, _CONV_LOCK_DIAG_MS, _GIT_RETENTION_DAYS, _GIT_RETENTION_COMMITS, _GIT_RETENTION_INTERVAL_SEC, _HOT_METADATA_FLUSH_INTERVAL_SEC, _HOT_METADATA_FLUSH_MSG_DELTA, _HOT_METADATA_KEYS, _HOT_METADATA_EXECUTOR, _GIT_RETENTION_EXECUTOR, _GIT_RETENTION_RUNNING, _GIT_RETENTION_RUNNING_LOCK, ConversationLockedError, _ConversationTimedRLock)


class _CsSessionsMixin:
    """metadata/extras getters + claude-session invalidation + bindings."""

    def message_count(self, cid: str) -> int:
        return self._load_cache(cid).get("msg_count", 0)

    def get_metadata(self, cid: str) -> Optional[Dict]:
        if not self.exists(cid):
            return None
        c = self._load_cache(cid)
        return {"user_id": c.get("user_id", ""), "status": c.get("status", "idle"),
                "created_at": c.get("created_at", 0), "updated_at": c.get("updated_at", 0),
                "expires_at": c.get("expires_at", 0), "message_count": c.get("msg_count", 0)}

    def get_extra_cached(self, cid: str, key: str, default: Any = None) -> Any:
        """Get extra from extras.json file."""
        key = self._canon_extra_key(key)
        data = self._read_extras(cid)
        self._merge_hot_metadata_snapshot(cid, data)
        if key == "context_usage":
            return self._repair_context_usage_from_transcript(cid, data) or default
        return data.get(key, default)

    def get_extra_snapshot(self, cid: str, key: str,
                           default: Any = None) -> Any:
        """Return a cache-only extra snapshot without disk IO or repair.

        UI polling paths use this to stay O(1). If the conversation cache is
        not warm yet, callers get ``default`` instead of forcing a transcript
        scan or waiting behind a writer lock.
        """
        key = self._canon_extra_key(key)
        with self._cache_lock:
            value = ((self._cache.get(cid) or {}).get("extras") or {}).get(
                key, default)
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, list):
            return list(value)
        return value

    def get_extras_snapshot(self, cid: str) -> Dict[str, Any]:
        """Return all cached extras without disk IO or repair."""
        with self._cache_lock:
            data = dict(((self._cache.get(cid) or {}).get("extras") or {}))
        return data

    def get_extra(self, cid: str, key: str, default: Any = None,
                  user_id: str = "") -> Any:
        if not self.exists(cid):
            return default
        key = self._canon_extra_key(key)
        data = self._read_extras(cid)
        self._merge_hot_metadata_snapshot(cid, data)
        if key == "context_usage":
            return self._repair_context_usage_from_transcript(cid, data) or default
        return data.get(key, default)

    def get_extras(self, cid: str, user_id: str = "") -> Optional[dict]:
        if not self.exists(cid):
            return None
        data = self._read_extras(cid)
        self._merge_hot_metadata_snapshot(cid, data)
        if "context_usage" in data:
            data["context_usage"] = self._repair_context_usage_from_transcript(
                cid, data)
        return dict(data)

    def set_extra(self, cid: str, key: str, value: Any,
                  user_id: str = "") -> bool:
        if not self.exists(cid):
            # File gone but extras may still exist — clean up cache
            with self._cache_lock:
                self._cache.pop(cid, None)
            return False
        key = self._canon_extra_key(key)
        lock = self._get_extras_lock(cid)
        with lock:
            data = self._read_extras(cid)
            data[key] = value
            self._merge_hot_metadata_snapshot(cid, data)
            self._write_extras(cid, data)
        # Update in-memory cache for list_conversations (title, updated_at)
        with self._cache_lock:
            if key == "conv_agents":
                agents = set()
                if isinstance(value, dict):
                    agents.update(self._canon_agent(a) for a in value if a)
                self._append_agents_cache[cid] = set(agents)
            if cid in self._cache:
                self._cache[cid]["extra_keys"].add(key)
                self._cache[cid].setdefault("extras", {})[key] = value
                if key == "conv_agents":
                    self._cache[cid]["agents"] = set(agents)
                if key == "title":
                    self._cache[cid]["title"] = value
                self._cache[cid]["updated_at"] = time.time()
        return True

    def _delete_cli_runtime_session_dirs(self, cid: str, provider: str,
                                         agent_name: str = "",
                                         async_cleanup: bool = False) -> int:
        """Delete runtime session dirs for one CLI provider/conv.

        Used when a PawFlow context edit invalidates the provider's session.
        The live process is evicted separately; once extras are cleared, every
        file under the targeted provider dir is stale history.
        """
        try:
            owner = self._cid_user.get(cid, "") or self.get_user_id(cid) or ""
        except Exception:
            owner = self._cid_user.get(cid, "") or ""
        if not owner:
            return 0
        base_map = {
            "claude": _paths.CLAUDE_SESSIONS_DIR,
            "codex": _paths.CODEX_SESSIONS_DIR,
            "gemini": _paths.GEMINI_SESSIONS_DIR,
        }
        base = base_map.get(provider)
        if base is None:
            return 0
        safe_owner = owner.replace(":", "_").replace("/", "_").replace("\\", "_")
        conv_dir = base / safe_owner / cid.replace(":", "_")
        if agent_name:
            targets = [conv_dir / agent_name]
        else:
            targets = [conv_dir]
        removed = 0
        for target in targets:
            try:
                if not target.is_dir():
                    continue
                if async_cleanup:
                    stale_name = (f".stale-{provider}-{target.name}-"
                                  f"{uuid.uuid4().hex[:8]}")
                    stale = target.with_name(stale_name)
                    try:
                        target.replace(stale)
                        cleanup_target = stale
                    except OSError:
                        # If the directory is locked, do not block the caller.
                        # The cleared session pointer is the correctness barrier;
                        # a later cleanup/orphan sweep can remove the stale files.
                        logger.warning(
                            "Deferred %s runtime session cleanup for %s%s; "
                            "directory is still locked: %s",
                            provider, cid[:8],
                            f"/{agent_name}" if agent_name else "", target)
                        continue
                    threading.Thread(
                        target=self._delete_cli_runtime_session_dir_worker,
                        args=(cleanup_target, provider, cid, agent_name),
                        daemon=True,
                        name=f"cli-runtime-cleanup-{cid[:8]}-{provider}",
                    ).start()
                    removed += 1
                else:
                    shutil.rmtree(target, ignore_errors=True)
                    removed += 1
            except Exception:
                logger.debug("exception suppressed", exc_info=True)
        if removed:
            action = "Scheduled deletion of" if async_cleanup else "Deleted"
            logger.info("%s %d %s runtime session dir(s) for %s%s",
                        action, removed, provider, cid[:8],
                        f"/{agent_name}" if agent_name else "")
        return removed

    @staticmethod
    def _delete_cli_runtime_session_dir_worker(target: Path, provider: str,
                                               cid: str,
                                               agent_name: str = "") -> None:
        try:
            shutil.rmtree(target, ignore_errors=True)
            logger.info("Deleted stale %s runtime session dir for %s%s",
                        provider, cid[:8], f"/{agent_name}" if agent_name else "")
        except Exception:
            logger.debug("async cli runtime cleanup failed", exc_info=True)

    def invalidate_claude_sessions(self, cid: str) -> None:
        """Clear all claude-code session IDs for this conversation.

        Called when the user manually modifies context (delete message,
        manual compact, etc.). Forces a fresh session on next message.

        Also wipes the stale session jsonls + companion dirs on disk so
        they don't pile up indefinitely across invalidations.

        Live-session reuse: any warm CC proc for this conv is now
        operating on a stale view of history. Kill every live session
        bound to `cid` so the next turn spawns fresh.
        """
        extras = self.get_extras(cid) or {}
        # Session invalidation means the next CLI turn must rebuild from the
        # current PawFlow context on disk. Drop the context cache too, otherwise
        # a stale short private context can survive after the provider session
        # pointer was correctly cleared.
        self._invalidate_ctx_cache(cid)
        _had_any = False
        # Clear ALL CLI session pointers. With the pointer wiped, the next
        # turn starts a fresh session instead of resuming the now-stale one.
        for key in list(extras.keys()):
            if (key.startswith("claude_session:")
                    or key.startswith("codex_session:")
                    or key.startswith("codex_app_server_thread:")
                    or key.startswith("codex_app_pool_idx:")
                    or key.startswith("gemini_acp_session:")
                    or key.startswith("gemini_acp_pool_idx:")
                    or key.startswith("gemini_acp_session_version:")):
                self.set_extra(cid, key, "")
                logger.info("Invalidated %s for conv %s", key, cid[:8])
                _had_any = True
        # Move stale provider runtime dirs out of the active path immediately;
        # recursive deletion runs in background so restart_from stays hot.
        try:
            self._delete_cli_runtime_session_dirs(
                cid, "claude", async_cleanup=True)
            self._delete_cli_runtime_session_dirs(
                cid, "codex", async_cleanup=True)
            self._delete_cli_runtime_session_dirs(
                cid, "gemini", async_cleanup=True)
        except Exception as _e:
            logger.debug("invalidate_claude_sessions disk prune failed for %s: %s",
                         cid[:8], _e)
        # Kill any warm CC / CCI / codex / gemini / antigravity session running
        # in this conv — its view of history is now stale
        # (edit/compact/branch-switch).
        try:
            from core.cc_live_registry import LiveSessionRegistry
            n = LiveSessionRegistry.instance().kill_and_evict_by_conv(
                cid, reason="invalidate_claude_sessions")
            if n:
                logger.info(
                    "Invalidated %d live CC session(s) for conv %s",
                    n, cid[:8])
        except Exception as _e:
            logger.debug(
                "invalidate_claude_sessions live-evict failed for %s: %s",
                cid[:8], _e)
        try:
            from core.claude_code_interactive_pool import InteractiveClaudeCodePool
            n = InteractiveClaudeCodePool.instance().kill_and_evict_by_conv(
                cid, reason="invalidate_claude_sessions")
            if n:
                logger.info(
                    "Invalidated %d live CCI container(s) for conv %s",
                    n, cid[:8])
        except Exception as _e:
            logger.debug(
                "invalidate_claude_sessions cci-evict failed for %s: %s",
                cid[:8], _e)
        try:
            from core.codex_live_registry import CodexLiveRegistry
            n = CodexLiveRegistry.instance().kill_and_evict_by_conv(
                cid, reason="invalidate_claude_sessions")
            if n:
                logger.info(
                    "Invalidated %d live codex container(s) for conv %s",
                    n, cid[:8])
        except Exception as _e:
            logger.debug(
                "invalidate_claude_sessions codex-evict failed for %s: %s",
                cid[:8], _e)
        try:
            from core.gemini_live_registry import GeminiLiveRegistry
            n = GeminiLiveRegistry.instance().kill_and_evict_by_conv(
                cid, reason="invalidate_claude_sessions")
            if n:
                logger.info(
                    "Invalidated %d live gemini container(s) for conv %s",
                    n, cid[:8])
        except Exception as _e:
            logger.debug(
                "invalidate_claude_sessions gemini-evict failed for %s: %s",
                cid[:8], _e)
        try:
            from core.antigravity_observer_pool import AntigravityObserverPool
            n = AntigravityObserverPool.instance().kill_and_evict_by_conv(
                cid, reason="invalidate_claude_sessions")
            if n:
                logger.info(
                    "Invalidated %d live Antigravity container(s) for conv %s",
                    n, cid[:8])
        except Exception as _e:
            logger.debug(
                "invalidate_claude_sessions antigravity-evict failed for %s: %s",
                cid[:8], _e)

    def invalidate_claude_session_for_agent(self, cid: str,
                                             agent_name: str,
                                             async_cleanup: bool = False) -> None:
        """Clear the claude-code session for ONE agent, purging its
        jsonl + companion dir on disk.

        Per-agent variant of `invalidate_claude_sessions`. Used after
        PawFlow compact: we killed that agent's CC session and want its
        stale jsonl gone, without touching other agents' live sessions
        in the same conversation.

        Implementation deletes by exact sid path rather than going
        through `_prune_stale_cc_sessions`, because the latter returns
        early when `live_sids` is empty (its contract is "don't guess")
        and we'd just have cleared the only extra for a single-agent
        conversation.
        """
        if not agent_name:
            return
        self._invalidate_ctx_cache(cid, agent_name)
        # Clear the resume pointer for ALL three CLIs (claude / codex / gemini)
        # so the next turn for this (conv, agent) starts a fresh session
        # regardless of which CLI is configured. Symmetric with the all-agent
        # variant `invalidate_claude_sessions`.
        session_keys = (
                f"claude_session:{agent_name}",
                f"codex_session:{agent_name}",
                f"codex_app_server_thread:{agent_name}",
                f"codex_app_pool_idx:{agent_name}",
                f"gemini_acp_session:{agent_name}",
                f"gemini_acp_pool_idx:{agent_name}",
                f"gemini_acp_session_version:{agent_name}")
        original_extras = {}
        cleared_keys = []
        if self.exists(cid):
            lock = self._get_extras_lock(cid)
            _wait_t0 = time.monotonic()
            with lock:
                _wait_ms = (time.monotonic() - _wait_t0) * 1000.0
                extras = self._read_extras(cid)
                original_extras = dict(extras)
                for _k in session_keys:
                    if extras.get(_k):
                        extras[_k] = ""
                        cleared_keys.append(_k)
                if cleared_keys:
                    _write_t0 = time.monotonic()
                    self._write_extras(cid, extras)
                    _write_ms = (time.monotonic() - _write_t0) * 1000.0
                    if _wait_ms >= _CONV_LOCK_DIAG_MS or _write_ms >= _CONV_LOCK_DIAG_MS:
                        logger.warning(
                            "[convstore:%s] agent session extras clear slow agent=%s "
                            "keys=%d wait_ms=%.1f write_ms=%.1f",
                            cid[:8], agent_name, len(cleared_keys), _wait_ms, _write_ms)
        for _k in cleared_keys:
            logger.info("Invalidated %s for conv %s", _k, cid[:8])
        if cleared_keys and self._cache_lock.acquire(blocking=False):
            try:
                cached = self._cache.get(cid)
                if cached is not None:
                    cached_extra_keys = cached.setdefault("extra_keys", set())
                    cached_extras = cached.setdefault("extras", {})
                    for _k in cleared_keys:
                        cached_extra_keys.add(_k)
                        cached_extras[_k] = ""
                    cached["updated_at"] = time.time()
            finally:
                self._cache_lock.release()
        # CC-specific disk prune happens below by sid; codex/gemini runtime
        # dirs are removed by exact (conv, agent) because their resume pointers
        # have just been cleared.
        key = f"claude_session:{agent_name}"
        sid = str(original_extras.get(key) or "")
        if sid:
            try:
                owner = self._cid_user.get(cid, "")
                if owner:
                    from core import paths as _paths
                    import shutil as _shutil
                    sanitized_cid = cid.replace(":", "_")
                    sess_dir = _paths.CLAUDE_SESSIONS_DIR / owner / sanitized_cid
                    if sess_dir.is_dir():
                        for jf in sess_dir.rglob(f"projects/*/{sid}.jsonl"):
                            try:
                                jf.unlink()
                                logger.info("Pruned CC session jsonl %s for %s/%s",
                                            jf.name, cid[:8], agent_name)
                                companion = jf.with_suffix("")
                                if companion.is_dir():
                                    _shutil.rmtree(companion, ignore_errors=True)
                            except OSError:
                                pass
            except Exception as _e:
                logger.debug(
                    "invalidate_claude_session_for_agent disk prune failed "
                    "for %s/%s: %s", cid[:8], agent_name, _e)
        try:
            self._delete_cli_runtime_session_dirs(
                cid, "codex", agent_name, async_cleanup=async_cleanup)
            self._delete_cli_runtime_session_dirs(
                cid, "gemini", agent_name, async_cleanup=async_cleanup)
        except Exception as _e:
            logger.debug(
                "invalidate_claude_session_for_agent cli disk prune failed "
                "for %s/%s: %s", cid[:8], agent_name, _e)
        # Kill any warm CC / CCI / codex / gemini / antigravity session for this
        # (conv, agent) pair so the next turn spawns fresh.
        try:
            from core.cc_live_registry import LiveSessionRegistry
            n = LiveSessionRegistry.instance().kill_and_evict_by_conv_agent(
                cid, agent_name,
                reason="invalidate_claude_session_for_agent")
            if n:
                logger.info(
                    "Invalidated %d live CC session(s) for %s/%s",
                    n, cid[:8], agent_name)
        except Exception as _e:
            logger.debug(
                "invalidate_claude_session_for_agent live-evict failed "
                "for %s/%s: %s", cid[:8], agent_name, _e)
        try:
            from core.claude_code_interactive_pool import InteractiveClaudeCodePool
            n = InteractiveClaudeCodePool.instance().kill_and_evict_by_conv_agent(
                cid, agent_name,
                reason="invalidate_claude_session_for_agent")
            if n:
                logger.info(
                    "Invalidated %d live CCI container(s) for %s/%s",
                    n, cid[:8], agent_name)
        except Exception as _e:
            logger.debug(
                "invalidate_claude_session_for_agent cci-evict failed "
                "for %s/%s: %s", cid[:8], agent_name, _e)
        try:
            from core.codex_live_registry import CodexLiveRegistry
            n = CodexLiveRegistry.instance().kill_and_evict_by_conv_agent(
                cid, agent_name,
                reason="invalidate_claude_session_for_agent")
            if n:
                logger.info(
                    "Invalidated %d live codex container(s) for %s/%s",
                    n, cid[:8], agent_name)
        except Exception as _e:
            logger.debug(
                "invalidate_claude_session_for_agent codex-evict failed "
                "for %s/%s: %s", cid[:8], agent_name, _e)
        try:
            from core.gemini_live_registry import GeminiLiveRegistry
            n = GeminiLiveRegistry.instance().kill_and_evict_by_conv_agent(
                cid, agent_name,
                reason="invalidate_claude_session_for_agent")
            if n:
                logger.info(
                    "Invalidated %d live gemini container(s) for %s/%s",
                    n, cid[:8], agent_name)
        except Exception as _e:
            logger.debug(
                "invalidate_claude_session_for_agent gemini-evict failed "
                "for %s/%s: %s", cid[:8], agent_name, _e)
        try:
            from core.antigravity_observer_pool import AntigravityObserverPool
            n = AntigravityObserverPool.instance().kill_and_evict_by_conv_agent(
                cid, agent_name,
                reason="invalidate_claude_session_for_agent")
            if n:
                logger.info(
                    "Invalidated %d live Antigravity container(s) for %s/%s",
                    n, cid[:8], agent_name)
        except Exception as _e:
            logger.debug(
                "invalidate_claude_session_for_agent antigravity-evict failed "
                "for %s/%s: %s", cid[:8], agent_name, _e)

    def _bindings_path(self, cid: str) -> Path:
        return self._conv_dir(cid) / "bindings.json"

    def get_bindings(self, cid: str) -> Dict[str, list]:
        """Read all bindings for a conversation.

        Returns dict like {"agents": [{"name": "x", "scope": "global"}, ...], ...}
        Takes the per-conv lock to serialize with set_bindings's atomic
        replace — otherwise an open read handle can block MoveFileEx on
        Windows.
        """
        path = self._bindings_path(cid)
        lock = self._get_conv_lock(cid)
        with lock:
            if not path.exists():
                return {}
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}

    def set_bindings(self, cid: str, bindings: Dict[str, list]) -> None:
        """Replace all bindings for a conversation.

        Locks the per-conv lock so no reader holds an open handle on the
        destination during the atomic rename.
        """
        path = self._bindings_path(cid)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(bindings, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        lock = self._get_conv_lock(cid)
        with lock:
            tmp.replace(path)

    def add_binding(self, cid: str, rtype: str, name: str,
                    scope: str = "global") -> None:
        """Add a single binding (idempotent)."""
        lock = self._get_conv_lock(cid)
        with lock:
            data = self.get_bindings(cid)
            entries = data.setdefault(rtype, [])
            if not any(e["name"] == name for e in entries):
                entries.append({"name": name, "scope": scope})
                self.set_bindings(cid, data)

    def remove_binding(self, cid: str, rtype: str, name: str) -> bool:
        """Remove a binding by name. Returns True if found and removed."""
        lock = self._get_conv_lock(cid)
        with lock:
            data = self.get_bindings(cid)
            entries = data.get(rtype, [])
            before = len(entries)
            entries = [e for e in entries if e["name"] != name]
            if len(entries) == before:
                return False
            data[rtype] = entries
            self.set_bindings(cid, data)
            return True

    def list_bound(self, cid: str, rtype: str) -> List[Dict]:
        """List all bound items of a given type for a conversation."""
        return self.get_bindings(cid).get(rtype, [])
