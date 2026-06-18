"""ConversationStore delete/edit/truncate/list/cleanup."""

import logging
import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.segmented_jsonl import SegmentedJsonl
import core.paths as _paths

logger = logging.getLogger(__name__)
# Split out of conversation_store.py for the <=800-line rule; composed back into
# ConversationStore (invariant 2: MRO/shared state on the host).

from core._conversation_store_base import (  # noqa: F401,E402
    _CTX_CACHE_MAX_MESSAGES, _CTX_CACHE_MAX_CHARS, _CTX_CACHE_MAX_CONVS, _CONV_LOCK_DIAG_MS, _GIT_RETENTION_DAYS, _GIT_RETENTION_COMMITS, _GIT_RETENTION_INTERVAL_SEC, _HOT_METADATA_FLUSH_INTERVAL_SEC, _HOT_METADATA_FLUSH_MSG_DELTA, _HOT_METADATA_KEYS, _HOT_METADATA_EXECUTOR, _GIT_RETENTION_EXECUTOR, _GIT_RETENTION_RUNNING, _GIT_RETENTION_RUNNING_LOCK, ConversationLockedError, _ConversationTimedRLock)


class _CsMaintMixin:
    """delete/edit/truncate/list/cleanup."""

    def delete(self, cid: str, user_id: str = "") -> bool:
        import shutil
        import stat
        conv_dir = self._conv_dir(cid)
        if not conv_dir.is_dir():
            with self._cache_lock:
                self._cache.pop(cid, None)
            return False

        def _force_remove(func, path, _exc_info):
            """Force-remove read-only files (git pack objects on Windows)."""
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
            func(path)

        lock = self._get_conv_lock(cid)
        extras_lock = self._get_extras_lock(cid)
        # Resolve owner BEFORE popping _cid_user so edit-guard and session
        # workdir cleanup can find it.
        _owner = user_id or self._cid_user.get(cid, "")
        with lock:
            # set_extra writes extras.json via a separate lock and atomic
            # extras.tmp -> extras.json rename. Hold that same lock while
            # removing the directory, otherwise delete can race the tmp file
            # creation and hit ENOTEMPTY during rmtree.
            with extras_lock:
                shutil.rmtree(conv_dir, onerror=_force_remove)
        with self._cache_lock:
            self._cache.pop(cid, None)
            self._append_agents_cache.pop(cid, None)
            self._agent_ctx_exists_cache = {
                key for key in self._agent_ctx_exists_cache
                if key[0] != cid
            }
        self._conv_locks.pop(cid, None)
        self._extras_locks.pop(cid, None)
        self._cid_user.pop(cid, None)
        # Clean up all conv-scoped resources
        try:
            from core.file_store import FileStore
            FileStore.instance().delete_by(conversation_id=cid)
        except Exception:
            logger.debug("exception suppressed", exc_info=True)
        # Drop edit-guard state for every agent in this conv — otherwise
        # the read-hashes / failed-edit counters leak until size eviction.
        try:
            if _owner:
                from core.handlers._edit_guard import clear_conversation as _eg_clear
                _eg_clear(_owner, cid)
        except Exception:
            logger.debug("exception suppressed", exc_info=True)
        # Clean up CLI provider session workdirs
        # (sessions/<provider>/<user>/<cid>/). Without this, per-task session
        # dirs accumulate forever since task sub-convs are deleted on
        # completion but their provider runtime state is never reclaimed.
        if _owner:
            try:
                _sanitized_cid = cid.replace(":", "_")
                for _provider, _root in self._cli_session_roots().items():
                    _sess_dir = _root / _owner / _sanitized_cid
                    if _sess_dir.is_dir():
                        shutil.rmtree(_sess_dir, onerror=_force_remove)
            except Exception as _se:
                logger.debug("Failed to remove CLI session workdir for %s: %s",
                             cid, _se)
        self._invalidate_ctx_cache(cid)
        return True

    def edit_message(self, cid: str, msg_id: str, content: Any,
                     role: str = "", user_id: str = "") -> int:
        """Edit a message by msg_id in transcript + shared + all agent contexts."""
        if not msg_id or not self.exists(cid):
            return 0

        lock = self._get_conv_lock(cid)
        updated = 0

        def _rewrite_jsonl(path: Path) -> int:
            log = self._content_seg(cid, path)
            if not log.exists():
                return 0
            changed = 0

            def _transform(line: Dict[str, Any]) -> Dict[str, Any]:
                nonlocal changed
                if (line.get("msg_id") == msg_id
                        or (line.get("role") == "sub_agent_trace"
                            and line.get("trace_id") == msg_id)):
                    line["content"] = content
                    if role:
                        line["role"] = role
                    changed += 1
                return line

            log.rewrite(_transform)
            return changed

        with lock:
            updated += _rewrite_jsonl(self._transcript_path(cid))
            _rewrite_jsonl(self._shared_ctx_path(cid))
            conv_dir = self._conv_dir(cid)
            if conv_dir.is_dir():
                for entry in conv_dir.iterdir():
                    if entry.is_dir() and self._jsonl_exists(entry / "context.jsonl"):
                        _rewrite_jsonl(entry / "context.jsonl")

        if updated:
            with self._cache_lock:
                self._cache.pop(cid, None)
            self._invalidate_ctx_cache(cid)
            self._load_cache(cid)
            self.invalidate_claude_sessions(cid)
        return updated

    def delete_message(self, cid: str, msg_id: str = "", index: int = -1,
                       user_id: str = "") -> bool:
        """Delete a message by msg_id from transcript + all contexts. Atomic."""
        if not msg_id and index < 0:
            return False
        if not self.exists(cid):
            return False

        # If we only have index, resolve to msg_id first
        if not msg_id and index >= 0:
            def _find_id(lines):
                count = 0
                for line in lines:
                    if line.get("role"):
                        if count == index:
                            return line.get("msg_id", "")
                        count += 1
                return ""
            msg_id = self._read(cid, _find_id)
            if not msg_id:
                return False

        removed = self._remove_msg_ids_from_files(cid, {msg_id})
        return removed > 0

    def delete_messages(self, cid: str, msg_ids: list,
                        user_id: str = "") -> int:
        """Delete multiple messages by msg_id. Returns count of removed messages."""
        if not msg_ids or not self.exists(cid):
            return 0
        return self._remove_msg_ids_from_files(cid, set(msg_ids))

    def find_restart_boundary(self, cid: str, msg_id: str) -> Dict[str, Any]:
        """Find the transcript row that restart_from should keep through.

        A restart targeting a user message means "re-run this prompt", so the
        transcript is kept through the previous visible row and the prompt text
        is returned to the UI. Other targets are kept through the target row.
        The search walks from the tail and stops as soon as the target and, for
        user rows, its predecessor are known.
        """
        msg_id = str(msg_id or "").strip()
        if not msg_id or not self.exists(cid):
            return {"found": False}

        target: Optional[Dict[str, Any]] = None
        boundary: Optional[Dict[str, Any]] = None
        for row in self._transcript_log(cid).iter_rows_reverse():
            if not row.get("role"):
                continue
            if target is not None:
                boundary = dict(row)
                break
            if row.get("msg_id") == msg_id:
                target = dict(row)
                if target.get("role") != "user":
                    boundary = target
                    break

        if target is None:
            return {"found": False}
        return {
            "found": True,
            "target": target,
            "boundary": boundary,
            "boundary_msg_id": (boundary or {}).get("msg_id", ""),
        }

    def truncate_after_msg_id(self, cid: str, msg_id: str) -> Dict[str, Any]:
        """Truncate transcript, shared context, and agent contexts after msg_id."""
        msg_id = str(msg_id or "").strip()
        if not msg_id or not self.exists(cid):
            return {"found": False, "kept_messages": 0, "contexts_truncated": 0}

        lock = self._get_conv_lock(cid)
        contexts_truncated = 0
        with lock:
            transcript = self._transcript_log(cid).truncate_after_msg_id(msg_id)
            if not transcript.get("found"):
                return {"found": False, "kept_messages": 0, "contexts_truncated": 0}

            shared = SegmentedJsonl(self._shared_ctx_path(cid)).truncate_after_msg_id(msg_id)
            if shared.get("found"):
                contexts_truncated += 1
            conv_dir = self._conv_dir(cid)
            if conv_dir.is_dir():
                for entry in conv_dir.iterdir():
                    ctx_path = entry / "context.jsonl"
                    if entry.is_dir() and self._jsonl_exists(ctx_path):
                        ctx_res = SegmentedJsonl(ctx_path).truncate_after_msg_id(msg_id)
                        if ctx_res.get("found"):
                            contexts_truncated += 1

        with self._cache_lock:
            self._cache.pop(cid, None)
        self._invalidate_ctx_cache(cid)
        cached = self._reload_cache(cid)
        self._persist_recomputed_hot_metadata(cid, cached)
        self.invalidate_claude_sessions(cid)
        return {
            "found": True,
            "kept_messages": int(cached.get("msg_count") or 0),
            "contexts_truncated": contexts_truncated,
            "boundary": transcript.get("boundary"),
        }

    def _remove_msg_ids_from_files(self, cid: str, ids: set) -> int:
        """Remove messages by msg_id from transcript + shared + all agent contexts."""
        lock = self._get_conv_lock(cid)
        removed = 0

        def _rewrite_jsonl(path: Path) -> int:
            """Rewrite a logical JSONL stream, removing rows with matching msg_id.

            Also removes append-only trace_update events whose trace_id matches
            an id in `ids`.
            """
            log = SegmentedJsonl(path)
            if not log.exists():
                return 0
            count = 0
            def _transform(line: Dict[str, Any]) -> Optional[Dict[str, Any]]:
                nonlocal count
                if line.get("msg_id") in ids:
                    count += 1
                    return None
                if (line.get("t") == "trace_update"
                        and line.get("trace_id") in ids):
                    return None
                return line

            log.rewrite(_transform)
            return count

        with lock:
            # 1. Transcript
            removed += _rewrite_jsonl(self._transcript_path(cid))
            # 2. Shared context
            _rewrite_jsonl(self._shared_ctx_path(cid))
            # 3. All agent contexts
            conv_dir = self._conv_dir(cid)
            if conv_dir.is_dir():
                for entry in conv_dir.iterdir():
                    if entry.is_dir() and self._jsonl_exists(entry / "context.jsonl"):
                        _rewrite_jsonl(entry / "context.jsonl")

        with self._cache_lock:
            self._cache.pop(cid, None)
        if removed:
            self._invalidate_ctx_cache(cid)  # clear ALL agent ctx caches
            cached = self._reload_cache(cid)
            self._persist_recomputed_hot_metadata(cid, cached)
            self.invalidate_claude_sessions(cid)
        return removed

    def list_conversations(self, user_id: str = "") -> List[Dict]:
        self._ensure_loaded()
        self._reconcile_list_cache_from_disk(user_id=user_id)
        result = []
        with self._cache_lock:
            for cid, c in self._cache.items():
                if ":task:" in cid:
                    continue
                if user_id and c.get("user_id") and c["user_id"] != user_id:
                    continue
                if c.get("expires_at", 0) > 0 and c["expires_at"] < time.time():
                    continue
                result.append({
                    "conversation_id": cid,
                    "title": c.get("title", ""),
                    "preview": c.get("preview", ""),
                    "message_count": c.get("msg_count", 0),
                    "status": c.get("status", "idle"),
                    "user_id": c.get("user_id", ""),
                    "created_at": c.get("created_at", 0),
                    "updated_at": c.get("updated_at", 0),
                    "expires_at": c.get("expires_at", 0),
                })
        result.sort(key=lambda x: x["updated_at"], reverse=True)
        return result

    def list_agent_contexts(self, cid: str) -> Dict[str, str]:
        c = self._load_cache(cid)
        result = {"*": "messages"}
        for a in c.get("agents", set()):
            result[a] = "diverged"
        return result

    def create_display_trace(self, cid: str, trace_id: str,
                             source: Dict, user_id: str = "") -> bool:
        lock = self._get_conv_lock(cid)
        # msg_id is required for the context editor's delete path
        # (selection sends msg_ids; without one the row is not deletable).
        with lock:
            line = self._stamp_line(cid, {
                "role": "sub_agent_trace", "display_only": True,
                "trace_id": trace_id, "source": source, "content": "",
                "trace": [],
            })
            self._transcript_log(cid).append_dicts([line])
            self._notify_bg_transcript_chars(
                cid, self._row_payload_chars(line))
        return True

    def append_display_trace(self, cid: str, trace_id: str,
                             entry_data: Dict, content_update: str = "") -> bool:
        entry_data.setdefault("ts", time.time())
        lock = self._get_conv_lock(cid)
        with lock:
            line = self._stamp_line(cid, {
                "t": "trace_update",
                "trace_id": trace_id,
                "entry": entry_data,
                "content_update": content_update or "",
            })
            self._transcript_log(cid).append_dicts([line])
            self._notify_bg_transcript_chars(
                cid, self._row_payload_chars(line))
        return True

    def vacuum(self, cid: str) -> dict:
        """Manual vacuum — no-op (extras are now atomic JSON, contexts are separate files)."""
        return {"status": "ok"}

    def cleanup(self) -> int:
        self._ensure_loaded()
        removed = 0
        now = time.time()
        with self._cache_lock:
            expired = [cid for cid, c in self._cache.items()
                       if c.get("expires_at", 0) > 0 and c["expires_at"] < now]
        for cid in expired:
            self.delete(cid)
            removed += 1
        removed += self.cleanup_orphan_cli_sessions()
        return removed

    def _cli_session_roots(self) -> Dict[str, Path]:
        """Return provider -> runtime CLI session root."""
        return {
            "claude": _paths.CLAUDE_SESSIONS_DIR,
            "codex": _paths.CODEX_SESSIONS_DIR,
            "gemini": _paths.GEMINI_SESSIONS_DIR,
        }

    def cleanup_orphan_claude_sessions(self, prune_live: bool = True) -> int:
        """Remove orphan Claude session dirs and stale live-session JSONLs.

        Claude Code stores per-session JSONL files under a live conversation
        directory. The directory itself is kept while the conversation exists,
        but JSONLs not named by `claude_session:*` extras are stale and can be
        removed. If no Claude session extras exist for a live conversation, keep
        its JSONLs because there is no authoritative current session id.
        """
        removed = self.cleanup_orphan_cli_sessions(providers=["claude"])
        if not prune_live:
            return removed

        base = _paths.CLAUDE_SESSIONS_DIR
        if not base.is_dir():
            return removed

        self._ensure_loaded()
        live: List[tuple[str, str, set[str]]] = []
        with self._cache_lock:
            for cid in self._cache.keys():
                owner = self._cid_user.get(cid, "")
                if not owner:
                    continue
                extras = dict(self._cache.get(cid, {}).get("extras") or {})
                live_sids = {
                    str(value)
                    for key, value in extras.items()
                    if key.startswith("claude_session:") and value
                }
                live.append((cid, owner, live_sids))

        for cid, owner, live_sids in live:
            if not live_sids:
                continue
            safe_owner = self._safe_name(owner)
            candidate_names = {
                self._safe_name(cid),
                cid.replace(":", "_"),
                cid.replace(":", "__"),
            }
            for conv_name in candidate_names:
                sess_dir = base / safe_owner / conv_name
                if not sess_dir.is_dir():
                    continue
                for jf in list(sess_dir.rglob("projects/*/*.jsonl")):
                    if jf.stem in live_sids:
                        continue
                    try:
                        jf.unlink()
                        removed += 1
                    except OSError:
                        continue
                    companion = jf.with_suffix("")
                    if companion.is_dir():
                        shutil.rmtree(companion, ignore_errors=True)
        return removed

    def cleanup_orphan_cli_sessions(self, providers: Optional[List[str]] = None) -> int:
        """Remove CLI provider session dirs whose conversation no longer exists.

        Runtime session roots all use the same top-level shape:
          sessions/<provider>/<user>/<conversation>/...

        If the matching conversation directory exists, the provider session is
        still linked and the whole tree is kept. If it does not exist, the
        provider session directory is removed. No session files are read.
        """
        roots = self._cli_session_roots()
        if providers:
            requested = {str(p) for p in providers}
            roots = {name: root for name, root in roots.items()
                     if name in requested}
        self._ensure_loaded()
        live_by_user: Dict[str, set[str]] = {}
        with self._cache_lock:
            for cid in self._cache.keys():
                user = self._cid_user.get(cid, "")
                if not user:
                    continue
                names = live_by_user.setdefault(self._safe_name(user), set())
                names.add(self._safe_name(cid))
                names.add(cid.replace(":", "_"))
                names.add(cid.replace(":", "__"))
        removed = 0
        for provider, base in roots.items():
            if not base.is_dir():
                continue
            for user_dir in base.iterdir():
                if not user_dir.is_dir():
                    continue
                for sess_dir in user_dir.iterdir():
                    if not sess_dir.is_dir():
                        continue
                    if sess_dir.name.startswith(".stale-"):
                        shutil.rmtree(sess_dir, ignore_errors=True)
                        if not sess_dir.exists():
                            removed += 1
                        continue
                    is_one_shot = sess_dir.name.startswith("_")
                    if (not is_one_shot
                            and sess_dir.name in live_by_user.get(user_dir.name, set())):
                        for agent_dir in list(sess_dir.iterdir()):
                            if not agent_dir.is_dir():
                                continue
                            if agent_dir.name.startswith(".stale-"):
                                shutil.rmtree(agent_dir, ignore_errors=True)
                                if not agent_dir.exists():
                                    removed += 1
                                continue
                            if not agent_dir.name.startswith("_"):
                                continue
                            try:
                                stale = agent_dir.with_name(
                                    f".stale-{provider}-{agent_dir.name}-{uuid.uuid4().hex[:8]}")
                                try:
                                    agent_dir.replace(stale)
                                except OSError:
                                    stale = agent_dir
                                threading.Thread(
                                    target=self._delete_cli_runtime_session_dir_worker,
                                    args=(stale, provider, sess_dir.name, agent_dir.name),
                                    daemon=True,
                                    name=f"cli-one-shot-delete-{provider}",
                                ).start()
                                removed += 1
                                logger.info(
                                    "Removed nested one-shot %s CLI session dir: %s/%s/%s",
                                    provider, user_dir.name, sess_dir.name, agent_dir.name)
                            except Exception as exc:
                                logger.debug(
                                    "Failed to remove nested one-shot %s session %s: %s",
                                    provider, agent_dir, exc)
                        continue
                    try:
                        stale = sess_dir.with_name(
                            f".stale-{provider}-{sess_dir.name}-{uuid.uuid4().hex[:8]}")
                        try:
                            sess_dir.replace(stale)
                        except OSError:
                            stale = sess_dir
                        threading.Thread(
                            target=self._delete_cli_runtime_session_dir_worker,
                            args=(stale, provider, sess_dir.name),
                            daemon=True,
                            name=f"cli-orphan-delete-{provider}",
                        ).start()
                        removed += 1
                        logger.info("Removed %s %s CLI session dir: %s/%s",
                                    "one-shot" if is_one_shot else "orphan",
                                    provider, user_dir.name, sess_dir.name)
                    except Exception as exc:
                        logger.debug("Failed to remove orphan %s session %s: %s",
                                     provider, sess_dir, exc)
        return removed

    def count(self) -> int:
        self._ensure_loaded()
        with self._cache_lock:
            return len(self._cache)

    @staticmethod
    def filter_display_only(msgs: List[Dict]) -> List[Dict]:
        return [m for m in msgs if not (isinstance(m, dict) and m.get("display_only"))]

    def set_metadata_field(self, cid: str, field: str, value: Any) -> bool:
        return self.set_extra(cid, field, value)
