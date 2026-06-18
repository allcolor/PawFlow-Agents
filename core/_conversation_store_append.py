"""ConversationStore save/append_message/append_messages/delegate routing."""

import logging
import time
import uuid
from typing import Any, Dict, List


logger = logging.getLogger(__name__)
# Split out of conversation_store.py for the <=800-line rule; composed back into
# ConversationStore (invariant 2: MRO/shared state on the host).

from core._conversation_store_base import (  # noqa: F401,E402
    _CTX_CACHE_MAX_MESSAGES, _CTX_CACHE_MAX_CHARS, _CTX_CACHE_MAX_CONVS, _CONV_LOCK_DIAG_MS, _GIT_RETENTION_DAYS, _GIT_RETENTION_COMMITS, _GIT_RETENTION_INTERVAL_SEC, _HOT_METADATA_FLUSH_INTERVAL_SEC, _HOT_METADATA_FLUSH_MSG_DELTA, _HOT_METADATA_KEYS, _HOT_METADATA_EXECUTOR, _GIT_RETENTION_EXECUTOR, _GIT_RETENTION_RUNNING, _GIT_RETENTION_RUNNING_LOCK, ConversationLockedError, _ConversationTimedRLock)
import core._conversation_store_base as _csb  # noqa: E402


class _CsAppendMixin:
    """save/append_message/append_messages/delegate routing."""

    @staticmethod
    def _validate_message(m: Dict):
        """Every message MUST have msg_id and timestamp at CREATION.

        msg_id and ts are minted at message CREATION (producer side —
        stable through transit, timestamp reflects the moment the
        message existed). seq is the on-disk line index, assigned at
        WRITE time by _stamp_line under the conv lock — producers must
        NOT stamp it in advance (disk order is the sole source of truth).
        """
        role = m.get("role", "")
        if role in ("system",):
            return  # system prompts are ephemeral, no msg_id needed
        if not m.get("msg_id"):
            raise ValueError(
                f"BUG: message without msg_id — role={role}, "
                f"content={str(m.get('content', ''))[:80]}")
        if not m.get("ts") and not m.get("timestamp"):
            raise ValueError(
                f"BUG: message without timestamp — role={role}, "
                f"msg_id={m.get('msg_id')}")

    def generate_id(self) -> str:
        return uuid.uuid4().hex[:16]

    def exists(self, cid: str) -> bool:
        if cid in self._cid_user:
            return True
        with self._cache_lock:
            if cid in self._cache:
                return True
        try:
            return self._conv_dir(cid).is_dir()
        except ValueError:
            return False

    def save(self, cid: str, messages: List[Dict], ttl: int = 0,
             user_id: str = "", status: str = ""):
        _now = time.time()
        if not user_id:
            raise ValueError("user_id is required to create a conversation")
        self._conv_dir(cid, user_id=user_id).mkdir(parents=True, exist_ok=True)

        # Write transcript messages only. Metadata lives in extras.json.
        rows = []
        tool_call_parents: Dict[str, str] = {}
        for m in messages:
            self._validate_message(m)
            for row in self._canonical_message_rows(cid, m, tool_call_parents):
                rows.append(self._stamp_line(cid, row))
        self._transcript_log(cid).replace_dicts(rows)

        # Write extras with metadata
        extras = {
            "_meta_user_id": user_id,
            "_meta_created_at": _now,
            "_meta_expires_at": _now + ttl if ttl > 0 else 0,
            "_meta_status": status or "idle",
            "_meta_updated_at": _now,
            "_meta_msg_count": len(rows),
            "_meta_max_seq": max((int(r.get("seq") or 0) for r in rows), default=0),
            "conv_agents": {},
        }
        for row in rows:
            if row.get("role") == "user":
                content = row.get("content", "")
                if isinstance(content, str) and content.strip():
                    extras["_meta_preview"] = content[:80]
                    break
        self._write_extras(cid, extras)

        # Pre-open the always-written logs so the first user append does not
        # pay Windows/WSL UNC first-open latency for transcript/shared.
        try:
            self._transcript_log(cid).prewarm_append()
            self._shared_ctx_log(cid).prewarm_append()
        except Exception:
            logger.debug("conversation log prewarm failed for %s", cid[:8], exc_info=True)

        # Initialize git repo
        self._git_init(cid)

        # Update cache
        self._reload_cache(cid)

    def append_message(self, cid: str, msg: Dict, agent_name: str = "",
                       user_id: str = "", ttl: int = 0) -> None:
        """Persist one message to every target file it belongs in.

        Sole write path. Callers feed one message at a time; writes are
        atomic under the conv lock.

        Routing rules:
          - transcript.jsonl: everything except source.type=='context'.
          - {agent}/context.jsonl (own): everything except source.type!=
            'context' and agent_name=='' (broadcast-less orphan).
          - shared.jsonl + other agents' contexts: user/assistant
            without tool_calls, not display_only, not context, not
            delegate-reply; assistant+tool_calls contributes a
            stripped-of-tool_calls copy (filter_for_shared).
          - agent_delegate: private A↔B routing via
            _route_delegate_message(); requests also project into shared.
          - display_only: transcript only.

        Raises on any I/O error (no silent swallow).
        """
        _append_started = time.monotonic()
        _timings: Dict[str, float] = {}

        def _mark_timing(name: str, started: float) -> None:
            _timings[name] = _timings.get(name, 0.0) + (
                (time.monotonic() - started) * 1000.0)

        self._validate_message(msg)
        agent_name = self._canon_agent(agent_name) if agent_name else ""
        role = msg.get("role", "")
        source = msg.get("source") or {}
        src_type = source.get("type", "")
        target_agent = self._canon_agent(source.get("target_agent", "")) if source.get("target_agent") else ""
        if role == "user" and src_type != "context" and not target_agent:
            raise ValueError("user messages require source.target_agent")
        route_agent = agent_name or (target_agent if target_agent not in ("ALL", "all") else "")
        display_only = bool(msg.get("display_only"))
        transcript_lines: List[Dict[str, Any]] = []
        transcript_chars_to_notify = 0
        touched_agents = set()

        try:
            from core.llm_client import _has_persisted_seq, _seed_persisted_seq
            if not _has_persisted_seq(cid):
                _seed_persisted_seq(cid, self.peek_persisted_max_seq(cid))
        except Exception:
            logger.debug("persisted seq cheap seed failed for %s",
                         cid[:8], exc_info=True)

        _mark_timing("pre_lock", _append_started)
        lock = self._get_conv_lock(cid)
        _t0 = time.monotonic()
        with lock:
            _mark_timing("lock_wait", _t0)
            # Encryption gate: never persist plaintext into an encrypted but
            # locked conversation. _codec_for is None both when unencrypted
            # (fine) and when encrypted-but-locked (must refuse) — disambiguate.
            if self._is_encryption_enabled(cid) and self._codec_for(cid) is None:
                raise ConversationLockedError(
                    f"conversation {cid[:16]} is encrypted and locked — unlock to write")
            # Create conv if missing
            _t0 = time.monotonic()
            if user_id and (cid in self._cid_user or cid in self._cache):
                conv_exists = True
            elif user_id:
                conv_dir = self._conv_dir(cid, user_id=user_id)
                conv_exists = conv_dir.is_dir()
            else:
                conv_exists = self.exists(cid)
            if not conv_exists:
                if not user_id:
                    raise ValueError(
                        "user_id required for new conversation")
                self.save(cid, [], user_id=user_id, ttl=ttl)
            _mark_timing("create_or_exists", _t0)

            _t0 = time.monotonic()
            canonical_rows = self._canonical_message_rows(cid, msg)
            _mark_timing("canonical_rows", _t0)

            # 1. Transcript — everything except synthetic context injections.
            if src_type != "context":
                _transcript_t0 = time.monotonic()
                lines = [self._stamp_line(cid, dict(row)) for row in canonical_rows]
                transcript_lines = lines
                _mark_timing("transcript_prepare", _transcript_t0)
                _t0 = time.monotonic()
                self._transcript_log(cid).append_dicts(lines)
                self._remember_tool_call_parents(cid, lines)
                _mark_timing("transcript_write", _t0)
                _mark_timing("transcript", _transcript_t0)
                transcript_chars_to_notify = sum(
                    self._row_payload_chars(line) for line in lines)

            if display_only:
                pass  # transcript only, no context files
            elif src_type == "agent_delegate":
                # Private A↔B routing (requests project to shared,
                # replies stay private between from/to).
                _t0 = time.monotonic()
                touched_agents.update(
                    self._route_delegate_message(cid, msg, agent_name))
                _mark_timing("delegate_route", _t0)
            else:
                # 2. Author's own context (brut, keeps tool_calls /
                #    tool results; also target of context injections).
                if route_agent:
                    _t0 = time.monotonic()
                    if src_type != "context":
                        self._seed_agent_context_from_shared_if_missing(
                            cid, route_agent)
                    self._append_ctx_file(cid, route_agent, canonical_rows)
                    touched_agents.add(route_agent)
                    _mark_timing("own_ctx", _t0)

                # 3. Shared + broadcast to other agents — only for
                #    conversation messages (filter_for_shared drops
                #    tool results and context injections; strips
                #    tool_calls but keeps text).
                if src_type != "context":
                    _t0 = time.monotonic()
                    shared_candidates = self.filter_for_shared(canonical_rows)
                    _mark_timing("shared_filter", _t0)
                    if shared_candidates:
                        _t0 = time.monotonic()
                        shared_msgs = [self._transform_for_shared(m)
                                       for m in shared_candidates]
                        self._append_shared_ctx(cid, shared_msgs, _timings)
                        _mark_timing("shared_append", _t0)
                        _t0 = time.monotonic()
                        with self._cache_lock:
                            fanout_cache_warm = (
                                cid in self._append_agents_cache
                                or cid in self._cache)
                        if role == "user" and route_agent and not fanout_cache_warm:
                            known_agents = {route_agent}
                        else:
                            known_agents = self._cache_agents_for_append(cid)
                        if route_agent:
                            known_agents.add(route_agent)
                        _mark_timing("known_agents", _t0)
                        _broadcast_count = 0
                        _t0 = time.monotonic()
                        for other in known_agents:
                            if not other or other == (route_agent or agent_name):
                                continue
                            transformed = [
                                self._transform_for_other_agent(m, other)
                                for m in shared_candidates]
                            self._append_ctx_file(cid, other, transformed)
                            touched_agents.add(other)
                            _broadcast_count += 1
                        _mark_timing("broadcast", _t0)
                        _timings["broadcast_count"] = float(_broadcast_count)

        # Feed bg_bucket_builder outside the conversation write lock. The
        # trigger is best-effort cache maintenance; it must not serialize disk
        # appends on Python-for-Windows over WSL UNC paths.
        if transcript_chars_to_notify:
            _t0 = time.monotonic()
            try:
                _csb._HOT_METADATA_EXECUTOR.submit(
                    self._notify_bg_transcript_chars,
                    cid, transcript_chars_to_notify)
            except Exception:
                logger.debug("bg transcript-chars hint schedule failed", exc_info=True)
            _mark_timing("bg_notify", _t0)

        # Refresh cache (msg_count, agents set) after any write.
        _t0 = time.monotonic()
        self._invalidate_ctx_cache(cid)
        _mark_timing("ctx_cache_invalidate", _t0)
        _t0 = time.monotonic()
        for transcript_line in transcript_lines:
            self._note_cache_append(cid, transcript_line, touched_agents)
        _mark_timing("cache_note", _t0)
        _total_ms = (time.monotonic() - _append_started) * 1000.0
        if _total_ms >= _CONV_LOCK_DIAG_MS:
            logger.warning(
                "[convstore:%s] append slow role=%s msg_id=%s total_ms=%.1f "
                "pre_lock=%.1f lock_wait=%.1f create_or_exists=%.1f "
                "canonical_rows=%.1f "
                "transcript=%.1f transcript_prepare=%.1f "
                "transcript_write=%.1f bg_notify=%.1f own_ctx=%.1f "
                "shared_filter=%.1f "
                "shared_append=%.1f shared_write=%.1f "
                "shared_bg_trigger=%.1f known_agents=%.1f broadcast=%.1f "
                "broadcast_count=%d delegate_route=%.1f "
                "ctx_cache_invalidate=%.1f cache_note=%.1f touched_agents=%d",
                cid[:8], role, msg.get("msg_id", "?"), _total_ms,
                _timings.get("pre_lock", 0.0),
                _timings.get("lock_wait", 0.0),
                _timings.get("create_or_exists", 0.0),
                _timings.get("canonical_rows", 0.0),
                _timings.get("transcript", 0.0),
                _timings.get("transcript_prepare", 0.0),
                _timings.get("transcript_write", 0.0),
                _timings.get("bg_notify", 0.0),
                _timings.get("own_ctx", 0.0),
                _timings.get("shared_filter", 0.0),
                _timings.get("shared_append", 0.0),
                _timings.get("shared_write", 0.0),
                _timings.get("shared_bg_trigger", 0.0),
                _timings.get("known_agents", 0.0),
                _timings.get("broadcast", 0.0),
                int(_timings.get("broadcast_count", 0.0)),
                _timings.get("delegate_route", 0.0),
                _timings.get("ctx_cache_invalidate", 0.0),
                _timings.get("cache_note", 0.0),
                len(touched_agents),
            )

    def append_messages(self, cid: str, items: List[Dict[str, Any]]) -> None:
        """Append a FIFO burst with one conversation lock and one write per file.

        The writer often drains several queued messages at once. Calling
        append_message repeatedly preserves correctness, but it repeats the
        same lock, transcript, context, shared, and cache work for every row.
        This method keeps the append_message routing rules while coalescing
        physical JSONL appends by target file.
        """
        if not items:
            return
        if len(items) == 1:
            item = items[0]
            self.append_message(
                cid, item["msg"],
                agent_name=item.get("agent_name", ""),
                user_id=item.get("user_id", ""),
                ttl=item.get("ttl", 0))
            return

        batch_started = time.monotonic()
        normalized = []
        for item in items:
            msg = item["msg"]
            self._validate_message(msg)
            agent = self._canon_agent(item.get("agent_name", "")) if item.get("agent_name") else ""
            role = msg.get("role", "")
            source = msg.get("source") or {}
            src_type = source.get("type", "")
            target_agent = self._canon_agent(source.get("target_agent", "")) if source.get("target_agent") else ""
            if role == "user" and src_type != "context" and not target_agent:
                raise ValueError("user messages require source.target_agent")
            if src_type == "agent_delegate":
                for single in items:
                    self.append_message(
                        cid, single["msg"],
                        agent_name=single.get("agent_name", ""),
                        user_id=single.get("user_id", ""),
                        ttl=single.get("ttl", 0))
                return
            normalized.append({
                "item": item,
                "msg": msg,
                "agent_name": agent,
                "role": role,
                "source": source,
                "src_type": src_type,
                "target_agent": target_agent,
                "route_agent": agent or (target_agent if target_agent not in ("ALL", "all") else ""),
                "display_only": bool(msg.get("display_only")),
            })

        try:
            from core.llm_client import _has_persisted_seq, _seed_persisted_seq
            if not _has_persisted_seq(cid):
                _seed_persisted_seq(cid, self.peek_persisted_max_seq(cid))
        except Exception:
            logger.debug("persisted seq cheap seed failed for %s",
                         cid[:8], exc_info=True)

        transcript_rows: List[Dict[str, Any]] = []
        shared_rows: List[Dict[str, Any]] = []
        ctx_rows: Dict[str, List[Dict[str, Any]]] = {}
        tool_call_parents: Dict[str, str] = {}
        touched_agents = set()
        seeded_agents = set()
        transcript_chars = 0

        lock = self._get_conv_lock(cid)
        with lock:
            first = normalized[0]["item"]
            first_user_id = first.get("user_id", "")
            if first_user_id and (cid in self._cid_user or cid in self._cache):
                conv_exists = True
            elif first_user_id:
                conv_exists = self._conv_dir(cid, user_id=first_user_id).is_dir()
            else:
                conv_exists = self.exists(cid)
            if not conv_exists:
                if not first_user_id:
                    raise ValueError("user_id required for new conversation")
                self.save(cid, [], user_id=first_user_id,
                          ttl=first.get("ttl", 0))

            for entry in normalized:
                msg = entry["msg"]
                role = entry["role"]
                src_type = entry["src_type"]
                route_agent = entry["route_agent"]
                canonical_rows = self._canonical_message_rows(
                    cid, msg, tool_call_parents)

                if src_type != "context":
                    for row in canonical_rows:
                        line = self._stamp_line(cid, dict(row))
                        transcript_rows.append(line)
                        transcript_chars += self._row_payload_chars(line)

                if entry["display_only"]:
                    continue

                if route_agent:
                    if src_type != "context" and route_agent not in seeded_agents:
                        self._seed_agent_context_from_shared_if_missing(
                            cid, route_agent)
                        seeded_agents.add(route_agent)
                    for row in canonical_rows:
                        ctx_rows.setdefault(route_agent, []).append(
                            self._stamp_line(cid, dict(row)))
                    touched_agents.add(route_agent)

                if src_type != "context":
                    shared_candidates = self.filter_for_shared(canonical_rows)
                    if shared_candidates:
                        for shared_msg in (
                                self._transform_for_shared(m)
                                for m in shared_candidates):
                            shared_rows.append(
                                self._stamp_line(cid, shared_msg))
                        with self._cache_lock:
                            fanout_cache_warm = (
                                cid in self._append_agents_cache
                                or cid in self._cache)
                        if role == "user" and route_agent and not fanout_cache_warm:
                            known_agents = {route_agent}
                        else:
                            known_agents = self._cache_agents_for_append(cid)
                        if route_agent:
                            known_agents.add(route_agent)
                        for other in known_agents:
                            if not other or other == (route_agent or entry["agent_name"]):
                                continue
                            transformed = [
                                self._transform_for_other_agent(m, other)
                                for m in shared_candidates]
                            for m in transformed:
                                ctx_rows.setdefault(other, []).append(
                                    self._stamp_line(cid, m))
                            touched_agents.add(other)

            if transcript_rows:
                self._transcript_log(cid).append_dicts(transcript_rows)
                self._remember_tool_call_parents(cid, transcript_rows)
            for agent, rows in ctx_rows.items():
                self._agent_ctx_log(cid, agent).append_dicts(rows)
            if shared_rows:
                self._shared_ctx_log(cid).append_dicts(shared_rows)

        if transcript_chars:
            try:
                _csb._HOT_METADATA_EXECUTOR.submit(
                    self._notify_bg_transcript_chars, cid, transcript_chars)
            except Exception:
                logger.debug("bg transcript-chars hint schedule failed", exc_info=True)
        if shared_rows:
            _max_seq = max(int(row.get("seq") or 0) for row in shared_rows)
            _shared_chars = sum(self._row_payload_chars(row) for row in shared_rows)
            try:
                _csb._HOT_METADATA_EXECUTOR.submit(
                    self._notify_shared_bg_worker,
                    cid, _max_seq, len(shared_rows), _shared_chars)
            except Exception:
                logger.debug("bg bucket trigger schedule failed", exc_info=True)

        self._invalidate_ctx_cache(cid)
        for line in transcript_rows:
            self._note_cache_append(cid, line, touched_agents)
        total_ms = (time.monotonic() - batch_started) * 1000.0
        if total_ms >= _CONV_LOCK_DIAG_MS:
            logger.warning(
                "[convstore:%s] append batch slow rows=%d transcript=%d "
                "shared=%d ctx_targets=%d total_ms=%.1f touched_agents=%d",
                cid[:8], len(items), len(transcript_rows), len(shared_rows),
                len(ctx_rows), total_ms, len(touched_agents))

    def _route_delegate_message(self, cid: str, msg: Dict,
                                agent_name: str) -> set:
        """Route an agent_delegate message to from's ctx, to's ctx, and
        (for requests only) to shared + other agents.

        Called under the conv lock by append_message.
        """
        src = msg.get("source") or {}
        _from = src.get("from", "") or agent_name
        _to = src.get("to", "")
        if not _to:
            return set()
        _kind = src.get("kind")
        touched_agents = {self._canon_agent(_from)}

        # FROM's own ctx — [delegate <from> → <to>]:
        _for_from = dict(msg)
        _for_from["content"] = self._prefix_content(
            _for_from.get("content", ""),
            f"[delegate {_from} → {_to}]:")
        self._append_ctx_file(cid, _from, [_for_from])

        _visibility = src.get("delegate_visibility") or "final_reply"
        _reply_self_only = (_kind == "reply" and _visibility == "self_only")

        # TO's ctx — role coerced to user with explicit attribution.
        # Delegate reply internals (tool calls/results and intermediate
        # assistant blocks) stay in the responder's context only. The caller
        # receives just the final synthesized reply.
        if not _reply_self_only:
            _for_to = dict(msg)
            if _for_to.get("role") == "assistant":
                _for_to["role"] = "user"
            if _kind == "reply":
                _attr = (f"Here is agent '{_from}''s reply to your "
                         f"delegate:")
            else:
                _attr = f"Here is a message from agent '{_from}':"
            _for_to["content"] = self._prefix_content(
                _for_to.get("content", ""), _attr)
            self._append_ctx_file(cid, _to, [_for_to])
            touched_agents.add(self._canon_agent(_to))

        # Replies stay private between from/to — don't leak to shared.
        if _kind == "reply":
            return touched_agents

        # Request broadcasts to shared + other agents (not from/to,
        # they already got their tailored copy above).
        _for_shared = dict(msg)
        if _for_shared.get("role") == "assistant":
            _for_shared["role"] = "user"
        _for_shared["content"] = self._prefix_content(
            _for_shared.get("content", ""),
            f"[{_from} to agent {_to}]:")
        self._append_shared_ctx(cid, [_for_shared])

        known_agents = self._cache_agents_for_append(cid)
        known_agents.update(touched_agents)
        _delegate_parties = {self._canon_agent(_from), self._canon_agent(_to)}
        for other in known_agents:
            if not other or other in _delegate_parties:
                continue
            transformed = self._transform_for_other_agent(
                _for_shared, other)
            self._append_ctx_file(cid, other, [transformed])
            touched_agents.add(other)
        return touched_agents
