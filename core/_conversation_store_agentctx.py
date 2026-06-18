"""ConversationStore agent-context load/save/personalize."""

import logging
from typing import Any, Dict, List, Optional

from core.segmented_jsonl import SegmentedJsonl

logger = logging.getLogger(__name__)
# Split out of conversation_store.py for the <=800-line rule; composed back into
# ConversationStore (invariant 2: MRO/shared state on the host).

from core._conversation_store_base import (  # noqa: F401,E402
    _CTX_CACHE_MAX_MESSAGES, _CTX_CACHE_MAX_CHARS, _CTX_CACHE_MAX_CONVS, _CONV_LOCK_DIAG_MS, _GIT_RETENTION_DAYS, _GIT_RETENTION_COMMITS, _GIT_RETENTION_INTERVAL_SEC, _HOT_METADATA_FLUSH_INTERVAL_SEC, _HOT_METADATA_FLUSH_MSG_DELTA, _HOT_METADATA_KEYS, _HOT_METADATA_EXECUTOR, _GIT_RETENTION_EXECUTOR, _GIT_RETENTION_RUNNING, _GIT_RETENTION_RUNNING_LOCK, ConversationLockedError, _ConversationTimedRLock)


class _CsAgentCtxMixin:
    """agent-context load/save/personalize."""

    @staticmethod
    def _context_cache_chars(messages: Optional[List[Dict]]) -> int:
        if not messages:
            return 0
        total = 0
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, str):
                total += len(content)
            elif content is not None:
                total += len(str(content))
            if msg.get("role") == "tool_call" and msg.get("arguments"):
                total += len(str(msg.get("arguments")))
        return total

    @staticmethod
    def _should_cache_context(messages: Optional[List[Dict]]) -> bool:
        if messages is None:
            return True
        return (len(messages) <= _CTX_CACHE_MAX_MESSAGES and
                _CsAgentCtxMixin._context_cache_chars(messages) <= _CTX_CACHE_MAX_CHARS)

    def _trim_ctx_cache_locked(self) -> None:
        while len(self._ctx_cache) > _CTX_CACHE_MAX_CONVS:
            oldest = next(iter(self._ctx_cache), None)
            if oldest is None:
                return
            self._ctx_cache.pop(oldest, None)

    def load_agent_context(self, cid: str, agent_name: str) -> Optional[List[Dict]]:
        """Load agent context from {agent}/context.jsonl file.

        If agent_name is set but no context file exists, returns None
        (caller falls back to shared via load_context).
        If agent_name is empty, loads from shared.jsonl directly.
        """
        agent_name = self._canon_agent(agent_name) if agent_name else ""
        with self._ctx_cache_lock:
            if cid in self._ctx_cache and agent_name in self._ctx_cache[cid]:
                cached = self._ctx_cache[cid][agent_name]
                return list(cached) if cached is not None else None

        if agent_name:
            path = self._agent_ctx_path(cid, agent_name)
        else:
            path = self._shared_ctx_path(cid)
        # This read is on the agent hot path before every provider send.
        # Do not take the conversation write lock: context files are append-only
        # during normal turns, and full rewrites are rare/manual. A concurrent
        # rewrite may return the old or new complete file, which is acceptable
        # for prompt construction and avoids blocking append_message batches.
        result = self._read_ctx_file(path, cid=cid) or None
        with self._ctx_cache_lock:
            if self._should_cache_context(result):
                self._ctx_cache.setdefault(cid, {})[agent_name] = result
                self._trim_ctx_cache_locked()
            else:
                self._ctx_cache.get(cid, {}).pop(agent_name, None)
                logger.debug("ConversationStore: skipped ctx cache for %s/%s (%s messages, %s chars)",
                             cid[:8], agent_name or "shared", len(result or []),
                             self._context_cache_chars(result))
        return result

    def load_agent_context_page(self, cid: str, agent_name: str,
                                limit: int = 50, offset: int = 0) -> Optional[Dict]:
        """Load a newest-first page from an agent/shared context file.

        Returns None when the context file does not exist. Messages stay
        chronological inside the page, matching load_page().
        """
        agent_name = self._canon_agent(agent_name) if agent_name else ""
        path = self._agent_ctx_path(cid, agent_name) if agent_name else self._shared_ctx_path(cid)
        log = self._content_seg(cid, path)
        if not log.exists():
            return None
        try:
            limit_i = max(1, int(limit or 50))
        except (TypeError, ValueError):
            limit_i = 50
        try:
            offset_i = max(0, int(offset or 0))
        except (TypeError, ValueError):
            offset_i = 0
        total = log.total_rows()
        selected = []
        stop = offset_i + limit_i
        for idx, row in enumerate(log.iter_rows_reverse()):
            if idx < offset_i:
                continue
            if idx >= stop:
                break
            selected.append(row)
        selected.reverse()
        return {
            "messages": selected,
            "total_count": total,
            "has_more": stop < total,
            "offset": offset_i,
            "limit": limit_i,
        }

    def load_transcript_for_agent(self, cid: str, agent_name: str
                                   ) -> Optional[List[Dict]]:
        """Return the full transcript personalized for one agent.

        This is the right source for compaction: it contains everything
        (user messages, every agent's assistant turns, tool_call rows,
        tool_results) and — unlike agent context — never includes a
        previously-injected compaction summary. Compacting from here
        can never layer stale summaries on top of each other.

        Personalization:
        - Own assistant/thinking/tool_call rows: kept as-is
        - Own tool messages: kept as-is (they belong to the agent's turn)
        - Other agents' messages: role=user, content prefixed "[Agent X]:"
        - User messages: role=user, prefixed "[User to agent X]:" when targeted
        - Other agents' thinking/tool_call/tool results: dropped (private to them)
        """
        if not self.exists(cid):
            return None
        canon = self._canon_agent(agent_name) if agent_name else ""
        raw = self.load(cid)
        if not raw:
            return None
        return self._personalize_transcript_for_agent(raw, canon)

    def load_transcript_tail_for_agent(self, cid: str, agent_name: str,
                                       limit: int = 1000) -> Optional[List[Dict]]:
        """Return a recent transcript tail personalized for one agent.

        Provider-triggered compaction only needs recent raw fidelity; old
        history comes from the shared bucket header. Read from the tail so a
        compact boundary never has to materialize a giant transcript just to
        walk back to the last few dozen useful messages.
        """
        if not self.exists(cid):
            return None
        try:
            limit_i = max(1, int(limit or 1000))
        except (TypeError, ValueError):
            limit_i = 1000
        page = self.load_page(cid, limit=limit_i, offset=0) or {}
        raw = page.get("messages") or []
        if not raw:
            return None
        canon = self._canon_agent(agent_name) if agent_name else ""
        return self._personalize_transcript_for_agent(raw, canon)

    def load_transcript_page_for_agent(self, cid: str, agent_name: str,
                                       limit: int = 50, offset: int = 0
                                       ) -> Optional[Dict]:
        """Return a paginated transcript page personalized for one agent."""
        page = self.load_page(cid, limit=limit, offset=offset) or {}
        raw = page.get("messages") or []
        canon = self._canon_agent(agent_name) if agent_name else ""
        page["messages"] = self._personalize_transcript_for_agent(raw, canon)
        return page

    def load_shared_tail(self, cid: str, user_id: str = "",
                         limit: int = 1000) -> Optional[List[Dict]]:
        """Return a recent transcript tail normalized for shared context.

        Manual compaction combines this bounded raw tail with the shared
        bucket header. It must not materialize shared.jsonl in long chats just
        to rebuild the last few rows.
        """
        page = self.load_page(cid, limit=limit, offset=0, user_id=user_id) or {}
        raw = page.get("messages") or []
        if not raw:
            return None
        shared_candidates = self.filter_for_shared(raw)
        return [self._transform_for_shared(m) for m in shared_candidates]

    def load_shared_page(self, cid: str, user_id: str = "",
                         limit: int = 50, offset: int = 0) -> Optional[Dict]:
        """Return a paginated transcript page normalized for shared context."""
        page = self.load_page(cid, limit=limit, offset=offset, user_id=user_id) or {}
        raw = page.get("messages") or []
        shared_candidates = self.filter_for_shared(raw)
        page["messages"] = [self._transform_for_shared(m)
                            for m in shared_candidates]
        return page

    def _personalize_transcript_for_agent(self, raw: List[Dict],
                                          canon: str) -> List[Dict]:
        """Personalize already-loaded transcript messages for one agent."""

        # First pass: collect tool_call_ids that belong to THIS agent so we
        # can keep matching tool results and drop everybody else's.
        own_tc_ids: set = set()
        for m in raw:
            if m.get("role") != "tool_call":
                continue
            src = m.get("source") or {}
            if src.get("type") != "agent":
                continue
            sname = src.get("name", "")
            if not (canon and sname and sname.lower() == canon.lower()):
                continue
            tid = m.get("tool_call_id") or m.get("tc_id")
            if tid:
                own_tc_ids.add(tid)

        out: List[Dict] = []
        for m in raw:
            role = m.get("role", "")
            src = m.get("source") or {}
            src_type = src.get("type", "")
            src_name = src.get("name", "")

            # Private per-agent traces — only keep this agent's own.
            if role == "sub_agent_trace":
                if src_type == "agent" and canon and src_name \
                        and src_name.lower() == canon.lower():
                    out.append(dict(m))
                continue

            # Tool results — keep only those answering this agent's tool_calls.
            # Orphans (other agents' tool results) are dropped so the summarizer
            # never sees them.
            if role == "tool":
                tcid = m.get("tool_call_id", "")
                if tcid and tcid in own_tc_ids:
                    out.append(dict(m))
                continue

            if role in ("thinking", "tool_call"):
                if src_type == "agent" and canon and src_name \
                        and src_name.lower() == canon.lower():
                    out.append(dict(m))
                continue

            if role == "assistant" and src_type == "agent":
                if canon and src_name and src_name.lower() == canon.lower():
                    # Own turn anchor.
                    out.append(dict(m))
                else:
                    # Another agent's turn — demote to user with prefix
                    # and drop btw/task
                    # side-channels entirely (these aren't addressed to us).
                    if src.get("task_id") or src.get("btw"):
                        continue
                    # Tool-only turns have no text. Don't emit empty
                    # "[Agent X]:" stubs into the view. Handle both string
                    # and list (multimodal) content formats.
                    content = m.get("content", "")
                    if isinstance(content, str):
                        if not content.strip():
                            continue
                        text = content
                    elif isinstance(content, list):
                        # Collect text from every text block; drop the rest
                        # (tool_use blocks become meaningless once tool_calls
                        # are stripped).
                        _parts = [
                            b.get("text", "") for b in content
                            if isinstance(b, dict) and b.get("type") == "text"
                        ]
                        text = "\n".join(p for p in _parts if p.strip())
                        if not text.strip():
                            continue
                    else:
                        continue
                    mm = dict(m)
                    mm["role"] = "user"
                    prefix = f"[Agent {src_name}]: " if src_name else "[Agent]: "
                    mm["content"] = prefix + text
                    out.append(mm)
                continue

            if role == "user":
                tgt = src.get("target_agent", "") if isinstance(src, dict) else ""
                # Drop btw/sub-task user messages addressed to another agent —
                # those are private side-channels, not part of this agent's
                # conversation view.
                if src.get("btw") and tgt and canon \
                        and tgt.lower() != canon.lower():
                    continue
                mm = dict(m)
                if tgt and canon and tgt.lower() != canon.lower():
                    prefix = f"[User to agent {tgt}]: "
                    content = mm.get("content", "")
                    if isinstance(content, str):
                        mm["content"] = prefix + content
                out.append(mm)
                continue

            # system, etc. — passthrough
            out.append(dict(m))
        return out

    def load_shared_for_agent(self, cid: str, agent_name: str) -> Optional[List[Dict]]:
        """Load shared context personalized for a specific agent.

        Shared stores agent-neutral messages (all prefixed).
        This reverses prefixes for the agent's own messages.
        """
        agent_name = self._canon_agent(agent_name) if agent_name else ""
        lock = self._get_conv_lock(cid)
        with lock:
            raw = self._read_ctx_file(self._shared_ctx_path(cid), cid=cid)
        if not raw:
            return None
        return [self._personalize_from_shared(m, agent_name) for m in raw]

    def _invalidate_ctx_cache(self, cid: str, agent_name: str = ""):
        with self._ctx_cache_lock:
            if agent_name:
                if cid in self._ctx_cache:
                    self._ctx_cache[cid].pop(agent_name, None)
            else:
                self._ctx_cache.pop(cid, None)

    def save_agent_context(self, cid: str, agent_name: str,
                           context_messages: List[Dict]) -> bool:
        """Write agent context to {agent}/context.jsonl (full replace)."""
        if not self.exists(cid):
            return False
        agent_name = self._canon_agent(agent_name) if agent_name else ""
        clean: List[Dict[str, Any]] = []
        tool_call_parents: Dict[str, str] = {}
        for m in context_messages:
            if m.get("display_only"):
                continue
            clean.extend(self._canonical_message_rows(cid, m, tool_call_parents))
        # Cross-file UUID invariant: the same logical message must carry
        # the same msg_id here as in the transcript — preserved by
        # construction (msg_id is minted once in LLMMessage.__post_init__
        # and flows through every write path via dict(msg) transforms).
        for _m in clean:
            self._validate_message(_m)
        lock = self._get_conv_lock(cid)
        with lock:
            if agent_name:
                self._write_ctx_file(self._agent_ctx_path(cid, agent_name), clean, cid=cid)
            else:
                # Shared context full-rewrite: user-driven edit / delete
                # via the context editor (agent="shared"). Compare the
                # new state against what's on disk to find the earliest
                # seq whose content diverges, then wipe all pyramid
                # buckets that covered that range — they now point at
                # stale content. Wiping is deliberately coarse (any
                # bucket with last_seq >= min_changed_seq goes); the
                # bg worker will rebuild from the new shared state on
                # the next maybe_trigger.
                _old_shared = self._read_ctx_file(self._shared_ctx_path(cid), cid=cid)
                self._write_ctx_file(self._shared_ctx_path(cid), clean, cid=cid)
                try:
                    _min_changed = self._compute_min_changed_seq(
                        _old_shared, clean)
                    if _min_changed is not None:
                        from core.bucket_store import BucketStore
                        _bs = BucketStore.get(self._conv_dir(cid))
                        _wiped = _bs.invalidate_from_seq(_min_changed)
                        if _wiped:
                            logger.info(
                                "[convstore] shared edit at seq %d "
                                "invalidated %d pyramid bucket(s) "
                                "for cid=%s",
                                _min_changed, _wiped, cid[:8])
                            self._invalidate_pyramid_cache(cid)
                except Exception:
                    logger.warning(
                        "[convstore] pyramid invalidation on shared "
                        "edit failed for cid=%s", cid[:8],
                        exc_info=True)
        self._invalidate_ctx_cache(cid, agent_name)
        # Refresh the main cache's `agents` set. Without this, writing
        # the first context for a new agent (e.g. /compact on an agent
        # that had no ctx yet) leaves cache["agents"] stale — the UI
        # context-editor reads from list_agent_contexts → cache and
        # wouldn't see the newly created agent until a server restart.
        if agent_name:
            with self._cache_lock:
                self._agent_ctx_exists_cache.add((cid, agent_name))
                self._append_agents_cache.setdefault(cid, set()).add(agent_name)
                cached = self._cache.get(cid)
                if cached is not None:
                    cached.setdefault("agents", set()).add(agent_name)
        return True

    def patch_agent_context_message(self, cid: str, agent_name: str,
                                    msg_id: str, fields: Dict[str, Any]) -> bool:
        """Patch one row in one context file without loading the full context."""
        if not self.exists(cid) or not msg_id or not fields:
            return False
        agent_name = self._canon_agent(agent_name) if agent_name else ""
        path = self._agent_ctx_path(cid, agent_name) if agent_name else self._shared_ctx_path(cid)
        log = self._content_seg(cid, path)
        if not log.exists():
            return False
        allowed = {"role", "content"}
        patch_fields = {k: v for k, v in fields.items() if k in allowed}
        if not patch_fields:
            return False
        lock = self._get_conv_lock(cid)
        with lock:
            patched = log.patch_first_by_msg_id(msg_id, patch_fields)
        if not patched:
            return False
        self._invalidate_ctx_cache(cid, agent_name)
        if not agent_name:
            try:
                from core.bucket_store import BucketStore
                seq = int(patched.get("seq") or 0)
                if seq:
                    BucketStore.get(self._conv_dir(cid)).invalidate_from_seq(seq)
                    self._invalidate_pyramid_cache(cid)
            except Exception:
                logger.warning(
                    "[convstore] pyramid invalidation on context patch failed for cid=%s",
                    cid[:8], exc_info=True)
        return True

    def delete_agent_context_messages(self, cid: str, agent_name: str,
                                      msg_ids: List[str]) -> int:
        """Delete rows from one context file without loading the full context."""
        if not self.exists(cid) or not msg_ids:
            return 0
        agent_name = self._canon_agent(agent_name) if agent_name else ""
        path = self._agent_ctx_path(cid, agent_name) if agent_name else self._shared_ctx_path(cid)
        log = SegmentedJsonl(path)
        if not log.exists():
            return 0
        lock = self._get_conv_lock(cid)
        with lock:
            deleted = log.delete_by_msg_ids(set(msg_ids))
        if deleted:
            self._invalidate_ctx_cache(cid, agent_name)
            if not agent_name:
                try:
                    from core.bucket_store import BucketStore
                    BucketStore.get(self._conv_dir(cid)).wipe()
                    self._invalidate_pyramid_cache(cid)
                except Exception:
                    logger.warning(
                        "[convstore] pyramid invalidation on context delete failed for cid=%s",
                        cid[:8], exc_info=True)
        return deleted

    def append_agent_context_message(self, cid: str, agent_name: str,
                                     message: Dict[str, Any]) -> bool:
        """Append one row to one context file without reading existing rows."""
        if not self.exists(cid):
            return False
        agent_name = self._canon_agent(agent_name) if agent_name else ""
        from core.llm_client import stamp_message
        row = stamp_message(dict(message), cid)
        clean: List[Dict[str, Any]] = []
        tool_call_parents: Dict[str, str] = {}
        clean.extend(self._canonical_message_rows(cid, row, tool_call_parents))
        for item in clean:
            self._validate_message(item)
        path = self._agent_ctx_path(cid, agent_name) if agent_name else self._shared_ctx_path(cid)
        lock = self._get_conv_lock(cid)
        with lock:
            self._content_seg(cid, path).append_dicts(clean)
        self._invalidate_ctx_cache(cid, agent_name)
        return True

    def prewarm_agent_context(self, cid: str, agent_name: str) -> None:
        """Open an agent context append handle before the first routed message."""
        agent_name = self._canon_agent(agent_name) if agent_name else ""
        if not agent_name or not self.exists(cid):
            return
        try:
            self._seed_agent_context_from_shared_if_missing(cid, agent_name)
            self._agent_ctx_log(cid, agent_name).prewarm_append()
            with self._cache_lock:
                self._agent_ctx_exists_cache.add((cid, agent_name))
                self._append_agents_cache.setdefault(cid, set()).add(agent_name)
                cached = self._cache.get(cid)
                if cached is not None:
                    cached.setdefault("agents", set()).add(agent_name)
        except Exception:
            logger.debug(
                "agent context prewarm failed for %s/%s",
                cid[:8], agent_name, exc_info=True)

    def prewarm_append_targets(self, cid: str, agent_name: str = "") -> None:
        """Open append handles normally touched by the next user message."""
        if not self.exists(cid):
            return
        try:
            self._transcript_log(cid).prewarm_append()
            self._shared_ctx_log(cid).prewarm_append()
        except Exception:
            logger.debug("conversation append prewarm failed for %s",
                         cid[:8], exc_info=True)
        if agent_name:
            self.prewarm_agent_context(cid, agent_name)

    @staticmethod
    def _compute_min_changed_seq(old: List[Dict],
                                   new: List[Dict]) -> Optional[int]:
        """Find the smallest seq whose presence or content differs
        between old and new shared state. Returns None if identical.

        A seq is considered "changed" if:
          - it was in old but is missing from new (deleted)
          - it was added in new but not in old (inserted)
          - same msg_id is in both but content or role differ (edited)

        The msg_id is the identity — compare by that, not by index, so
        reorderings in the list without content changes don't trigger
        unnecessary invalidation.
        """
        _old_by_id: Dict[str, Dict] = {
            m.get("msg_id"): m for m in old if m.get("msg_id")}
        _new_by_id: Dict[str, Dict] = {
            m.get("msg_id"): m for m in new if m.get("msg_id")}
        _changed_seqs: List[int] = []

        for mid, oldm in _old_by_id.items():
            newm = _new_by_id.get(mid)
            if newm is None:
                _s = oldm.get("seq") or 0
                if _s:
                    _changed_seqs.append(int(_s))
            elif (newm.get("content") != oldm.get("content")
                    or newm.get("role") != oldm.get("role")):
                _s = oldm.get("seq") or 0
                if _s:
                    _changed_seqs.append(int(_s))

        for mid, newm in _new_by_id.items():
            if mid not in _old_by_id:
                _s = newm.get("seq") or 0
                if _s:
                    _changed_seqs.append(int(_s))

        if not _changed_seqs:
            return None
        return min(_changed_seqs)

    def delete_agent_context(self, cid: str, agent_name: str) -> bool:
        agent_name = self._canon_agent(agent_name) if agent_name else ""
        """Delete agent context file + directory."""
        if not self.exists(cid):
            return False
        if agent_name:
            path = self._agent_ctx_path(cid, agent_name)
        else:
            path = self._shared_ctx_path(cid)
        lock = self._get_conv_lock(cid)
        with lock:
            SegmentedJsonl(path).delete()
        # Remove empty agent directory
        if agent_name and path.parent.is_dir():
            try:
                path.parent.rmdir()  # only succeeds if empty
            except OSError:
                pass
        self._invalidate_ctx_cache(cid, agent_name)
        # Reload main cache so agents set is updated
        with self._cache_lock:
            if agent_name:
                self._agent_ctx_exists_cache.discard((cid, agent_name))
            self._cache.pop(cid, None)
        self._reload_cache(cid)
        return True

    def save_context(self, cid: str, ctx: List[Dict]) -> bool:
        return self.save_agent_context(cid, "", ctx)

    def load_context(self, cid: str, user_id: str = "") -> Optional[List[Dict]]:
        return self.load_agent_context(cid, "")
