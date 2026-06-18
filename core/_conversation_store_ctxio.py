"""ConversationStore shared-context transform/personalize + ctx/extras file IO."""

import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from core.segmented_jsonl import SegmentedJsonl

logger = logging.getLogger(__name__)
# Split out of conversation_store.py for the <=800-line rule; composed back into
# ConversationStore (invariant 2: MRO/shared state on the host).

from core._conversation_store_base import (  # noqa: F401,E402
    _CTX_CACHE_MAX_MESSAGES, _CTX_CACHE_MAX_CHARS, _CTX_CACHE_MAX_CONVS, _CONV_LOCK_DIAG_MS, _GIT_RETENTION_DAYS, _GIT_RETENTION_COMMITS, _GIT_RETENTION_INTERVAL_SEC, _HOT_METADATA_FLUSH_INTERVAL_SEC, _HOT_METADATA_FLUSH_MSG_DELTA, _HOT_METADATA_KEYS, _HOT_METADATA_EXECUTOR, _GIT_RETENTION_EXECUTOR, _GIT_RETENTION_RUNNING, _GIT_RETENTION_RUNNING_LOCK, ConversationLockedError, _ConversationTimedRLock)
import core._conversation_store_base as _csb  # noqa: E402


class _CsCtxIoMixin:
    """shared-context transform/personalize + ctx/extras file IO."""

    @staticmethod
    def _row_payload_chars(row: Dict) -> int:
        """Char weight of a transcript row's payload — content + tool I/O.

        Used to feed bg_bucket_builder's transcript-token cache. Rough
        estimate (raw chars, no tokenizer); /3.5 gives the bg-side
        token-budget metric. Counts:
          - row['content'] (str or list of {type,text} blocks)
          - role=tool_call arguments payload size
          - row['trace'] / row['entry'] (display trace payload)
          - row['content_update'] (str)

        Anything else (metadata, ids, timestamps) is constant overhead
        and ignored — we want growth to track real LLM-visible payload.
        """
        total = 0
        c = row.get("content")
        if isinstance(c, str):
            total += len(c)
        elif isinstance(c, list):
            for p in c:
                if isinstance(p, dict):
                    t = p.get("text") or ""
                    if isinstance(t, str):
                        total += len(t)
        for tc in (row.get("tool_calls") or []):
            if isinstance(tc, dict):
                args = tc.get("arguments") or tc.get("function", {}).get("arguments") or ""
                if isinstance(args, str):
                    total += len(args)
                elif isinstance(args, dict):
                    try:
                        total += len(json.dumps(args, ensure_ascii=False))
                    except Exception:
                        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        trace = row.get("trace")
        if isinstance(trace, list):
            total += len(str(trace))
        entry = row.get("entry")
        if isinstance(entry, dict):
            total += len(str(entry))
        cu = row.get("content_update")
        if isinstance(cu, str):
            total += len(cu)
        return total

    @staticmethod
    def _notify_bg_transcript_chars(cid: str, n_chars: int):
        """Best-effort hook to feed bg_bucket_builder. Failures swallowed:
        the trigger logic falls back to seq-gap if the cache stays cold.
        """
        if n_chars <= 0:
            return
        try:
            from core.bg_bucket_builder import BgBucketBuilder
            BgBucketBuilder.instance().note_transcript_bytes_appended(
                cid, n_chars)
        except Exception:
            logger.debug("bg transcript-chars hint failed", exc_info=True)

    def _notify_shared_bg_worker(self, cid: str, max_seq: int,
                                 row_count: int, char_count: int) -> None:
        try:
            from core.bg_bucket_builder import BgBucketBuilder
            bb = BgBucketBuilder.instance()
            if max_seq:
                bb.note_shared_seq(cid, max_seq)
            if row_count:
                bb.note_shared_rows_appended(cid, row_count)
            if char_count:
                bb.note_shared_chars_appended(cid, char_count)
            uid = self._cid_user.get(cid, "") or ""
            if uid:
                trigger = getattr(bb, "maybe_trigger_async", bb.maybe_trigger)
                trigger(cid, uid)
        except Exception:
            logger.debug("bg bucket trigger failed", exc_info=True)

    def _append_ctx_file(self, cid: str, agent: str, messages: List[Dict]):
        """Append messages to an agent's context file.

        No dedup: msg_id is minted at message creation (uuid4) and the
        unified append_message router is the sole write path, so a
        duplicate msg_id on disk is a caller bug -- fix it at the root
        rather than silently dropping the second write here.
        """
        rows = []
        for m in messages:
            self._validate_message(m)
            rows.append(self._stamp_line(cid, dict(m)))
        self._agent_ctx_log(cid, agent).append_dicts(rows)
        if agent and rows:
            with self._cache_lock:
                self._agent_ctx_exists_cache.add((cid, self._canon_agent(agent)))

    def _seed_agent_context_from_shared_if_missing(self, cid: str, agent: str) -> int:
        """Initialize a new agent context from shared before its first row.

        The first user message routed to an agent must not create a private
        context containing only that message. If no private context exists yet,
        copy the current shared context personalized for this agent, then let
        the caller append the new row.
        """
        agent = self._canon_agent(agent) if agent else ""
        if not agent:
            return 0
        key = (cid, agent)
        with self._cache_lock:
            if key in self._agent_ctx_exists_cache:
                return 0
        log = self._agent_ctx_log(cid, agent)
        if log.exists():
            with self._cache_lock:
                self._agent_ctx_exists_cache.add(key)
            return 0
        seed = self.load_shared_for_agent(cid, agent) or []
        if not seed:
            return 0
        self._write_ctx_file(self._agent_ctx_path(cid, agent), seed, cid=cid)
        with self._cache_lock:
            self._agent_ctx_exists_cache.add(key)
        logger.info(
            "[context:%s] seeded %s context from shared before first append: %d messages",
            cid[:8], agent, len(seed))
        return len(seed)

    @staticmethod
    def _prefix_content(content, prefix: str):
        """Prefix content with a tag. Handles both string and multipart (list)."""
        if isinstance(content, str):
            if content.startswith(prefix + "\n") or content.startswith(prefix + " "):
                return content
            return f"{prefix}\n{content}"
        if isinstance(content, list):
            if content:
                first = content[0]
                if isinstance(first, dict) and first.get("type") == "text" and first.get("text") == prefix:
                    return list(content)
            return [{"type": "text", "text": prefix}] + list(content)
        text = str(content)
        if text.startswith(prefix + "\n") or text.startswith(prefix + " "):
            return text
        return f"{prefix}\n{text}"

    @staticmethod
    def _strip_prefix(content, prefix: str):
        """Strip a prefix tag from content. Handles both string and multipart (list)."""
        if isinstance(content, str):
            full = prefix + "\n"
            return content[len(full):] if content.startswith(full) else content
        if isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, dict) and first.get("type") == "text" and first.get("text") == prefix:
                return content[1:]
        return content

    @staticmethod
    def _agent_prefix(agent_name: str, source: Dict) -> str:
        """Build the [Agent X]: or [Agent X in Task Y]: or [Agent X in /btw]: prefix."""
        task_id = source.get("task_id", "")
        if task_id:
            return f"[Agent {agent_name} in Task {task_id}]:"
        if source.get("btw"):
            return f"[Agent {agent_name} in /btw]:"
        return f"[Agent {agent_name}]:"

    @staticmethod
    def _user_prefix(target: str, source: Dict) -> str:
        """Build the [User to agent X]: or [User to agent X in /btw]: prefix."""
        if source.get("btw"):
            return f"[User to agent {target} in /btw]:"
        return f"[User to agent {target}]:"

    @staticmethod
    def _transform_for_shared(msg: Dict) -> Dict:
        """Transform a message for the shared (agent-neutral) context.

        ALL messages are prefixed — shared belongs to no agent.
        - Agent messages: role→user, content prefixed [Agent X]: or [Agent X in Task Y]:
        - User messages: content prefixed [User to agent X]:
        - Agent_delegate messages: SHOULD NEVER REACH HERE (filtered upstream
          in append_message). If we're called on one, return as-is rather
          than mislabel it.
        """
        m = dict(msg)
        src = m.get("source") or {}
        src_type = src.get("type", "")

        if src_type == "agent_delegate":
            return m  # private channel — caller must not broadcast

        if src_type == "agent":
            agent_name = src.get("name")
            if not agent_name:
                raise ValueError(f"Agent message without source.name — msg_id={m.get('msg_id', '?')}")
            m["role"] = "user"
            m["content"] = _CsCtxIoMixin._prefix_content(
                m.get("content", ""), _CsCtxIoMixin._agent_prefix(agent_name, src))

        elif src_type == "user":
            target = src.get("target_agent", "")
            if target:
                m["content"] = _CsCtxIoMixin._prefix_content(
                    m.get("content", ""), _CsCtxIoMixin._user_prefix(target, src))

        return m

    @staticmethod
    def _transform_for_other_agent(msg: Dict, receiving_agent: str) -> Dict:
        """Transform a message for injection into a specific agent's context.

        - Own agent messages WITHOUT task: unchanged (role=assistant)
        - Own agent messages FROM task: prefixed [Agent X in Task Y]: (task is a sub-context)
        - Other agent messages: role→user, content prefixed [Agent X]:
        - User messages to receiving_agent: unchanged
        - User messages to other agent: content prefixed [User to agent X]:
        - Agent_delegate messages: SHOULD NEVER REACH HERE — private A↔B
          channel, filtered upstream. Returned as-is as a safety net.
        """
        m = dict(msg)
        src = m.get("source") or {}
        src_type = src.get("type", "")

        if src_type == "agent_delegate":
            return m  # private channel — should not be broadcast

        if src_type == "agent":
            agent_name = src.get("name")
            if not agent_name:
                raise ValueError(f"Agent message without source.name — msg_id={m.get('msg_id', '?')}")
            is_own = (agent_name == receiving_agent)
            is_sub_context = bool(src.get("task_id")) or bool(src.get("btw"))
            # Own messages from sub-contexts (task, btw) are prefixed
            # Own messages from normal conv are NOT prefixed (role=assistant)
            if not is_own or is_sub_context:
                m["role"] = "user"
                m["content"] = _CsCtxIoMixin._prefix_content(
                    m.get("content", ""), _CsCtxIoMixin._agent_prefix(agent_name, src))

        elif src_type == "user":
            target = src.get("target_agent", "")
            is_btw_msg = bool(src.get("btw"))
            # btw user messages are ALWAYS prefixed (sub-context, even for target agent)
            # Normal user messages only prefixed for non-target agents
            if target and (target != receiving_agent or is_btw_msg):
                m["content"] = _CsCtxIoMixin._prefix_content(
                    m.get("content", ""), _CsCtxIoMixin._user_prefix(target, src))

        return m

    @staticmethod
    def _personalize_from_shared(msg: Dict, agent_name: str) -> Dict:
        """Personalize a shared-context message for a specific agent.

        Reverses _transform_for_shared for this agent's own NON-TASK messages:
        - [Agent {me}]: (no task) → strip prefix, role=assistant
        - [Agent {me} in Task Y]: → keep prefix (task = sub-context, not own response)
        - [User to agent {me}]: → strip prefix
        - Everything else: keep as-is (already prefixed for "others")
        """
        m = dict(msg)
        src = m.get("source") or {}
        src_type = src.get("type", "")

        if src_type == "agent" and src.get("name") == agent_name:
            # Only un-prefix own messages that are NOT from a sub-context
            if not src.get("task_id") and not src.get("btw"):
                m["content"] = _CsCtxIoMixin._strip_prefix(
                    m.get("content", ""), f"[Agent {agent_name}]:")
                m["role"] = "assistant"
            # Sub-context messages (task, btw) stay prefixed

        elif src_type == "user" and src.get("target_agent") == agent_name:
            m["content"] = _CsCtxIoMixin._strip_prefix(
                m.get("content", ""), f"[User to agent {agent_name}]:")

        return m

    @staticmethod
    def filter_for_shared(messages: List[Dict]) -> List[Dict]:
        """Pick messages eligible for shared.jsonl.

        Shared context = conversation only: no tool rows, no thinking rows,
        no context injections. Stored rows are already canonical; this filter
        never unwraps assistant.tool_calls or assistant.thinking.
        """
        out = []
        for m in messages:
            if m.get("role") in ("tool", "tool_call", "thinking"):
                continue
            if (m.get("source") or {}).get("type") == "context":
                continue
            if m.get("role") == "assistant" and not str(m.get("content", "")).strip():
                continue
            out.append(m)
        return out

    def _append_shared_ctx(self, cid: str, messages: List[Dict],
                           timings: Optional[Dict[str, float]] = None):
        """Append already-shared-normalized messages to shared context.

        No dedup: see _append_ctx_file for rationale.

        After the write, updates core.bg_bucket_builder's in-memory counters
        and queues the trigger decision outside the foreground writer path.
        """
        def _add_timing(name: str, started: float) -> None:
            if timings is not None:
                timings[name] = timings.get(name, 0.0) + (
                    (time.monotonic() - started) * 1000.0)

        _max_seq = 0
        _shared_chars = 0
        rows = []
        _t0 = time.monotonic()
        for m in messages:
            self._validate_message(m)
            xf = self._stamp_line(cid, m)
            rows.append(xf)
            _shared_chars += self._row_payload_chars(xf)
            _s = int(xf.get("seq") or 0)
            if _s > _max_seq:
                _max_seq = _s
        self._shared_ctx_log(cid).append_dicts(rows)
        _add_timing("shared_write", _t0)

        _t0 = time.monotonic()
        try:
            _csb._HOT_METADATA_EXECUTOR.submit(
                self._notify_shared_bg_worker,
                cid, _max_seq, len(messages), _shared_chars)
        except Exception:
            logger.debug("bg bucket trigger schedule failed", exc_info=True)
        _add_timing("shared_bg_trigger", _t0)

    def _read_ctx_file(self, path: Path, cid: str = "") -> List[Dict]:
        """Read all messages from a context JSONL file, sorted by (ts, seq).

        File order is producer-FIFO but multi-producer races (different
        agents writing to the same conv, late tool_results arriving after
        newer turns) can put messages on disk in non-creation order.
        We sort by (ts, seq) here so the order reflects when each
        message was MINTED, not when the writer happened to flush it —
        matching what the user saw in the live SSE stream.
        """
        log = self._content_seg(cid, path) if cid else SegmentedJsonl(path)
        if not log.exists():
            return []
        result = list(log.iter_rows())
        result.sort(key=lambda m: (
            m.get("ts") or m.get("timestamp") or 0.0,
            m.get("seq") or 0,
        ))
        return result

    def _write_ctx_file(self, path: Path, messages: List[Dict], cid: str = ""):
        """Overwrite a context file with messages (atomic: tmp + rename)."""
        for m in messages:
            self._validate_message(m)
        log = self._content_seg(cid, path) if cid else SegmentedJsonl(path)
        log.replace_dicts(messages)

    def _read_extras(self, cid: str) -> dict:
        """Read extras from the atomic JSON file."""
        lock = self._get_extras_lock(cid)
        with lock:
            path = self._extras_path(cid)
            if path.exists():
                try:
                    return json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    logger.debug("exception suppressed", exc_info=True)
        return {}

    def _write_extras(self, cid: str, data: dict, attempts: int = 8):
        """Atomically write extras JSON (tmp + rename).

        Callers mutating shared extras MUST hold `_get_extras_lock(cid)`.
        Readers intentionally do not take the hot conversation lock:
        this file is replaced atomically from a complete tmp file, so a
        reader sees either the old or the new JSON document. The retry loop
        covers Windows cases where anti-virus / Windows Defender / OneDrive
        or a concurrent reader briefly holds a handle on the destination and
        `os.replace` raises WinError 5. A handful of short retries lets the
        handle close and the rename succeed.
        """
        import time as _t
        path = self._extras_path(cid)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(
            f"{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        _last_err: Optional[Exception] = None
        try:
            for _attempt in range(max(1, int(attempts))):
                try:
                    tmp.replace(path)
                    return
                except PermissionError as _pe:
                    _last_err = _pe
                    _t.sleep(0.025 * (1 + _attempt))  # 25, 50, 75, ... up to 200ms
            raise _last_err if _last_err else RuntimeError(
                "_write_extras: replace failed without an exception")
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
