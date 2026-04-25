"""AgentLoopTask mixin — AgentCompaction methods

Auto-extracted from tasks/ai/agent_loop.py.
All methods access self (AgentLoopTask instance).
"""
import json
import logging
import threading
import time
from typing import Dict, Any, List, Optional


from core import FlowFile
from core.llm_client import (
    LLMClient, LLMMessage, LLMResponse, LLMToolDefinition,
    LLMToolCall, LLMToolResult, LLMClientError,
)
from core.tool_registry import ToolRegistry, create_default_registry
from tasks.ai.agent_summarize import AgentSummarizeMixin

logger = logging.getLogger(__name__)



def _select_recent_messages(
    messages: List[LLMMessage],
    start_idx: int = 1,
    min_conversation: int = 25,
    max_total: int = 100,
) -> int:
    """Find split point: keep recent messages with guaranteed conversation ratio.

    Algorithm:
    1. Walk backward, collect the last ``min_conversation`` user/assistant
       messages **with non-empty text content**. Assistant turns that only
       carry ``tool_calls`` (empty text) do NOT count — otherwise a session
       that just did 25 bash/edit calls would preserve "25 messages" that
       all disappear on re-serialization to the LLM (the CLI serializer
       used to drop tool-only turns).
    2. Include ALL messages between them (tool, system, tool-call-only) —
       the recent window carries the full tool plumbing.
    3. If total > max_total, drop oldest tool/system messages until <= max_total

    Returns the split index (messages[split:] = recent to keep).
    Does NOT modify the messages list.
    """
    n = len(messages)
    if n <= start_idx + min_conversation:
        return start_idx  # not enough messages to compact

    # Step 1: walk backward to find min_conversation user/assistant messages
    # WITH TEXT; include every message in between.
    conv_count = 0
    scan = n
    while scan > start_idx and conv_count < min_conversation:
        scan -= 1
        m = messages[scan]
        if m.role in ("user", "assistant"):
            _txt = m.text_content if isinstance(m.content, list) else (m.content or "")
            if isinstance(_txt, str) and _txt.strip():
                conv_count += 1

    split = scan
    total = n - split

    # Step 2: if too many messages, advance split forward — prefer dropping
    # non-conversation messages, but drop conversation messages too if needed.
    if total > max_total:
        # First pass: skip non-user/assistant messages from the front
        while n - split > max_total and split < n:
            if messages[split].role not in ("user", "assistant"):
                split += 1
            else:
                break
        # Second pass: if still over budget, drop from the front regardless
        while n - split > max_total and split < n:
            split += 1

    return split


class AgentCompactionMixin(AgentSummarizeMixin):
    """Methods extracted from AgentLoopTask."""

    # Max chars kept per tool result after compaction truncation
    _TOOL_TRUNC_LIMIT = 800

    # Circuit breaker — track consecutive NON-FORCED compact failures
    # per (conv_id, agent_name). After _COMPACT_FAIL_CAP in a row the
    # auto-compact path for that agent skips itself for the rest of
    # the process lifetime. Manual /compact (force=True) bypasses —
    # the user explicitly asked so we still try. Reset on any success.
    # Session-scoped, in-memory. Prevents the "hit context limit, retry
    # compact, fail, hit again, retry, fail, …" loop that CC measured
    # at 3k+ consecutive failures in a single session (~250k wasted
    # API calls/day globally).
    _COMPACT_FAIL_CAP = 3
    _compact_fail_counts: Dict[tuple, int] = {}
    _compact_fail_lock = threading.Lock()

    @classmethod
    def _compact_breaker_key(cls, conversation_id: str, agent_name: str) -> tuple:
        return (conversation_id or "", agent_name or "")

    @classmethod
    def _compact_breaker_should_skip(cls, conversation_id: str,
                                      agent_name: str) -> int:
        """Return current failure count if cap reached, 0 otherwise."""
        with cls._compact_fail_lock:
            count = cls._compact_fail_counts.get(
                cls._compact_breaker_key(conversation_id, agent_name), 0)
            return count if count >= cls._COMPACT_FAIL_CAP else 0

    @classmethod
    def _compact_breaker_fail(cls, conversation_id: str, agent_name: str) -> int:
        with cls._compact_fail_lock:
            k = cls._compact_breaker_key(conversation_id, agent_name)
            cls._compact_fail_counts[k] = cls._compact_fail_counts.get(k, 0) + 1
            return cls._compact_fail_counts[k]

    @classmethod
    def _compact_breaker_reset(cls, conversation_id: str, agent_name: str) -> None:
        with cls._compact_fail_lock:
            cls._compact_fail_counts.pop(
                cls._compact_breaker_key(conversation_id, agent_name), None)

    @classmethod
    def _compact_breaker_record(cls, conversation_id: str, agent_name: str,
                                 succeeded: bool) -> None:
        """Unified success/fail entry point. Called once per compact attempt
        that actually did work (skipped-by-trigger runs don't call this)."""
        if succeeded:
            cls._compact_breaker_reset(conversation_id, agent_name)
            return
        n = cls._compact_breaker_fail(conversation_id, agent_name)
        if n >= cls._COMPACT_FAIL_CAP:
            logger.error(
                "[compact] circuit breaker tripped for %s/%s after %d "
                "consecutive failures — auto-compact disabled for this "
                "agent until server restart. Manual /compact still works.",
                (conversation_id or "?")[:8], agent_name or "-", n)

    def _microcompact_time_based(self, messages: List[LLMMessage],
                                  keep_recent: int = 5,
                                  gap_minutes: int = 60) -> int:
        """Clear old tool results when conversation has been idle.

        Like Claude Code's time-based microcompaction: after a gap of
        `gap_minutes` since the last assistant message, replace old tool
        results with "[Old tool result content cleared]". Keeps the
        `keep_recent` most recent tool results intact.

        Returns the number of tool results cleared.
        """
        # Find last assistant message timestamp
        _last_assistant_ts = 0.0
        for m in reversed(messages):
            if m.role == "assistant" and hasattr(m, '_ts'):
                _last_assistant_ts = m._ts
                break
        if not _last_assistant_ts:
            return 0

        gap_s = time.time() - _last_assistant_ts
        if gap_s < gap_minutes * 60:
            return 0

        # Collect all tool result indices
        tool_indices = [i for i, m in enumerate(messages)
                        if m.role == "tool" and isinstance(m.content, str)
                        and m.content != "[Old tool result content cleared]"]
        if len(tool_indices) <= keep_recent:
            return 0

        # Clear all except the most recent keep_recent
        to_clear = tool_indices[:-keep_recent] if keep_recent > 0 else tool_indices
        cleared = 0
        for i in to_clear:
            if len(messages[i].content) > 50:
                messages[i].content = "[Old tool result content cleared]"
                cleared += 1
        if cleared:
            logger.info("[microcompact] Cleared %d old tool result(s) (gap=%.0fm, kept=%d recent)",
                        cleared, gap_s / 60, keep_recent)
        return cleared

    def _progressive_clear_tool_results(self, messages: List[LLMMessage],
                                          target_tokens: int,
                                          current_tokens: int,
                                          keep_recent: int = 6,
                                          chars_per_token: float = 3.5):
        """Deterministically shrink old tool results to maximize KV cache stability.

        All old tool results (outside keep_recent) are truncated to fixed sizes
        regardless of current token count. This ensures the same prefix is produced
        across calls, preserving the Anthropic KV cache.

        Strategy (all old messages, deterministic):
        - Tool results > 500 chars → truncate to 200 chars
        - Tool results > 100 chars (after pass 1) → truncate to 50 chars if still over target
        - All remaining old tool results > 20 chars → "[result cleared]" if still over target

        Never touches the last `keep_recent` messages.
        Returns the estimated new token count.
        """
        if current_tokens <= target_tokens:
            return current_tokens

        safe_end = max(1, len(messages) - keep_recent)

        def _is_clear_ref(content):
            """Don't re-truncate already-cleared results."""
            return "[result cleared]" in content or "[...truncated]" in content or "[...cleared]" in content

        # Pass 1: truncate ALL old tool results > 500 chars to 200 chars
        # (deterministic — always applied regardless of token count)
        for i in range(1, safe_end):
            m = messages[i]
            if m.role != "tool" or not isinstance(m.content, str):
                continue
            if _is_clear_ref(m.content):
                continue
            if len(m.content) > 500:
                _saved = len(m.content) - 200
                m.content = m.content[:200] + "\n[...truncated]"
                current_tokens -= int(_saved / chars_per_token)

        if current_tokens <= target_tokens:
            return current_tokens

        # Pass 2: shrink ALL old tool results > 100 chars to 50 chars
        for i in range(1, safe_end):
            m = messages[i]
            if m.role != "tool" or not isinstance(m.content, str):
                continue
            if _is_clear_ref(m.content):
                continue
            if len(m.content) > 100:
                _saved = len(m.content) - 50
                m.content = m.content[:50] + "\n[...cleared]"
                current_tokens -= int(_saved / chars_per_token)

        if current_tokens <= target_tokens:
            return current_tokens

        # Pass 3: clear ALL old tool results
        for i in range(1, safe_end):
            m = messages[i]
            if m.role != "tool" or not isinstance(m.content, str):
                continue
            if len(m.content) > 20:
                _saved = len(m.content) - 15
                m.content = "[result cleared]"
                current_tokens -= int(_saved / chars_per_token)

        return current_tokens

    def _force_synthesis(self, messages, client, ctx, *, prompt: str,
                         compact_client=None, use_streaming: bool = False,
                         token_callback=None, tools_called: list = None,
                         compact_threshold: float = 0.6,
                         conversation_id: str = ""):
        """Force a final synthesis from the LLM (no tools).

        Returns (content, tokens_in, tokens_out, model).
        """
        messages.append(LLMMessage(role="user", content=prompt,
                                    conversation_id=conversation_id))
        _cc = compact_client or client
        synth_context = self._compact(
            list(messages), _cc,
            ctx.get("max_context_size", 64000),
            target_fraction=compact_threshold,
            conversation_id=conversation_id,
        )
        model = ctx.get("model") or None
        for _attempt in range(2):
            try:
                if use_streaming and token_callback:
                    resp = client.complete_stream(
                        messages=synth_context, model=model,
                        temperature=ctx["temperature"],
                        max_tokens=ctx["max_tokens"],
                        tools=None, callback=token_callback,
                    )
                else:
                    resp = client.complete(
                        messages=synth_context, model=model,
                        temperature=ctx["temperature"],
                        max_tokens=ctx["max_tokens"],
                        tools=None,
                    )
                messages.append(LLMMessage(role="assistant", content=resp.content,
                                            conversation_id=conversation_id))
                return resp.content, resp.tokens_in, resp.tokens_out, resp.model
            except Exception as synth_err:
                err_str = str(synth_err)
                if _attempt == 0 and ("exceed_context_size" in err_str or "n_prompt_tokens" in err_str):
                    logger.warning("[agent] synthesis overflow, forcing aggressive compaction...")
                    synth_context = self._compact(
                        synth_context, _cc,
                        ctx.get("max_context_size", 64000),
                        target_fraction=0.25,
                        conversation_id=conversation_id,
                    )
                    continue
                logger.error("Forced synthesis failed: %s", synth_err)
                break
        # Fallback
        fallback = (
            "I performed research but encountered an error generating the response.\n"
            f"Tools used: {', '.join(tools_called or [])}"
        )
        return fallback, 0, 0, ""

    # ── Image deflation (multimodal → text-only after LLM sees it) ──


    def _force_fit_context(
        self,
        messages: List[LLMMessage],
        max_tokens: int,
        chars_per_token: float = 0,
        tool_defs: list = None,
        token_multiplier: float = 1.0,
        conversation_id: str = "",
    ) -> List[LLMMessage]:
        """Last resort: brute-force truncate messages to fit within max_tokens.

        Strategy (from least to most destructive):
        1. Truncate all message contents to a max char budget
        2. Drop middle messages, keep system + last N
        """
        cpt = chars_per_token if chars_per_token > 0 else 2.0
        # Budget for tool defs (constant overhead)
        td_tokens = 0
        if tool_defs:
            for td in tool_defs:
                td_tokens += len(getattr(td, 'name', '') or '') / cpt
                td_tokens += len(getattr(td, 'description', '') or '') / cpt
                params = getattr(td, 'parameters', None)
                if params:
                    td_tokens += len(json.dumps(params) if isinstance(params, dict) else str(params)) / cpt
            td_tokens = int(td_tokens)

        # Target: 25% of max (same as summarization — leave 75% for response)
        target = int(max_tokens * 0.25) - int(td_tokens)
        if target < 1000:
            target = 1000

        # Step 1: Truncate every message to a per-message char budget
        n_msgs = max(1, len(messages))
        chars_budget_per_msg = int(target * cpt / n_msgs)
        # Give recent messages more budget
        keep_n = min(6, n_msgs)
        old_budget = max(100, int(chars_budget_per_msg * 0.3))
        recent_budget = max(500, int(target * cpt * 0.6 / max(1, keep_n)))

        result = []
        for i, m in enumerate(messages):
            budget = recent_budget if i >= n_msgs - keep_n else old_budget
            # Preserve system prompt
            if i == 0 and m.role == "system":
                budget = max(budget, 5000)
            new_m = LLMMessage(
                role=m.role,
                tool_call_id=getattr(m, 'tool_call_id', None),
                tool_calls=m.tool_calls,
                source=getattr(m, 'source', None),
                conversation_id=conversation_id or getattr(m, 'conversation_id', ''),
            )
            if isinstance(m.content, str):
                new_m.content = m.content[:budget] if len(m.content) > budget else m.content
                if len(m.content) > budget:
                    new_m.content += "\n...[truncated to fit context]..."
            elif isinstance(m.content, list):
                # Drop images, keep text truncated
                text = " ".join(p.get("text", "") for p in m.content if p.get("type") == "text")
                new_m.content = text[:budget] + ("\n...[truncated]..." if len(text) > budget else "")
            else:
                new_m.content = m.content
            result.append(new_m)

        est = self._estimate_tokens(result, tool_defs=tool_defs,
                                     chars_per_token=chars_per_token,
                                     token_multiplier=token_multiplier)
        if est <= max_tokens:
            logger.info(f"[compact] force-fit step 1 OK: {est} tokens")
            return result

        # Step 2: Drop middle messages, keep system + last N
        logger.warning(f"[compact] force-fit step 1 insufficient ({est} > {max_tokens}), dropping middle")
        keep = []
        if result and result[0].role == "system":
            keep.append(result[0])
            _cid = conversation_id or getattr(result[0], 'conversation_id', '')
            keep.append(LLMMessage(
                role="user",
                content=f"[{len(result) - keep_n - 1} earlier messages dropped to fit context limit]",
                conversation_id=_cid,
            ))
            keep.append(LLMMessage(role="assistant", content="Understood, continuing.",
                                    source={"type": "context"},
                                    conversation_id=_cid))
        keep.extend(result[-keep_n:])
        return keep


    def _auto_extract_memories(
        self,
        summary: str,
        client: LLMClient,
        user_id: str,
        agent_name: str = "",
        conversation_id: str = "",
    ):
        """Extract key facts from a compaction summary and store as memories.

        Best-effort: failures are logged but never propagate.
        """
        if not user_id or not summary or len(summary) < 50:
            return
        try:
            prompt = (
                "Extract the most important facts from this conversation summary "
                "that should be remembered for future conversations.\n"
                "Return a JSON array of objects with keys: "
                '"text" (the fact), "tags" (list of tags), "category" (one of: '
                'facts, events, discoveries, preferences, advice).\n'
                "Only include facts worth remembering long-term "
                "(user preferences, key decisions, project context). "
                "Skip ephemeral details (file contents, error messages, tool output).\n"
                "Return 0-5 items. If nothing is worth remembering, return [].\n\n"
                f"SUMMARY:\n{summary[:4000]}"
            )
            # Per-call identity via call_* kwargs (no mutation of shared
            # client state for those 5 fields). Pool-tracking state
            # still lives on the client and is overwritten by spawn/
            # cleanup of this memory-extract stream — save/restore so
            # main's send_user_message + cc-live registry survive.
            _inner = getattr(client, "_client", client)
            _saved_claude_proc = getattr(_inner, "_claude_proc", None)
            _saved_pool_name = getattr(_inner, "_pool_container_name", None)
            _saved_cc_pid = getattr(_inner, "_cc_container_pid", 0)
            _saved_pool_idx = getattr(_inner, "_current_pool_index", -1)
            _saved_session_id = getattr(_inner, "_current_session_id", "")
            _saved_result_emitted = getattr(_inner, "_result_emitted", False)
            try:
                resp = client.complete(
                    messages=[LLMMessage(role="user", content=prompt,
                                          conversation_id="_memory_extract")],
                    temperature=0.3,
                    max_tokens=1000,
                    call_user_id=user_id,
                    call_conversation_id="_memory_extract",
                    call_agent_name="memory",
                    call_event_cid="",
                    call_ephemeral_stream=True,
                )
            finally:
                if _saved_claude_proc is not None:
                    _inner._claude_proc = _saved_claude_proc
                if _saved_pool_name:
                    _inner._pool_container_name = _saved_pool_name
                if _saved_cc_pid:
                    _inner._cc_container_pid = _saved_cc_pid
                if _saved_pool_idx >= 0:
                    _inner._current_pool_index = _saved_pool_idx
                if _saved_session_id:
                    _inner._current_session_id = _saved_session_id
                _inner._result_emitted = _saved_result_emitted
                # One-shot helper: wipe the _memory_extract workdir for this
                # user. Nothing here needs to persist between extractions.
                try:
                    import os as _os, shutil as _shutil
                    from core.llm_providers.claude_code import _get_sessions_base
                    _uid = (user_id or "default").replace(":", "_").replace("/", "_").replace("\\", "_")
                    _mem_workdir = _os.path.join(_get_sessions_base(), _uid, "_memory_extract")
                    if _os.path.isdir(_mem_workdir):
                        _shutil.rmtree(_mem_workdir, ignore_errors=True)
                except Exception:
                    pass
            import re as _re_mem
            _match = _re_mem.search(r'\[.*\]', resp.content or "", _re_mem.DOTALL)
            if not _match:
                return
            items = json.loads(_match.group())
            if not isinstance(items, list):
                return
            from core.memory_store import MemoryStore
            from core.embeddings import EmbeddingProvider
            _api_key = getattr(client, "api_key", "") or ""
            _base_url = getattr(client, "base_url", "") or ""
            def _embed_fact(t: str):
                try:
                    vecs = EmbeddingProvider.instance().embed(
                        [t], provider="auto",
                        api_key=_api_key, base_url=_base_url,
                    )
                    return vecs[0] if vecs else None
                except Exception:
                    return None
            store = MemoryStore.instance()
            stored = 0
            for item in items[:5]:
                if not isinstance(item, dict):
                    continue
                text = item.get("text", "").strip()
                if not text or len(text) < 10:
                    continue
                tags = item.get("tags", [])
                if not isinstance(tags, list):
                    tags = []
                tags = [str(t).lower().strip() for t in tags if t][:5]
                if "auto-extracted" not in tags:
                    tags.append("auto-extracted")
                category = item.get("category", "") or item.get("hall", "facts")
                if category not in ("facts", "events", "discoveries", "preferences", "advice"):
                    category = "facts"
                store.remember(
                    user_id, text, tags, source="compaction",
                    embedding=_embed_fact(text),
                    agent=agent_name, category=category,
                )
                stored += 1
            if stored:
                logger.info("[compact] Auto-extracted %d memories from summary", stored)
        except Exception as e:
            logger.debug("[compact] LLM auto-extract failed: %s", e)

    def _compact(
        self,
        messages: List[LLMMessage],
        client: LLMClient,
        max_tokens: int,
        trigger_fraction: float = 0.8,
        target_fraction: float = 0.25,
        conversation_id: str = "",
        agent_name: str = "",
        tool_defs: list = None,
        chars_per_token: float = 0,
        compact_instructions: str = "",
        force: bool = False,
        user_id: str = "",
    ) -> List[LLMMessage]:
        """Iterative reduce-to-cap compaction. Output ≤ target_fraction × max.

        Every token count is the REAL tokenizer cost for the target model:
        tiktoken cl100k_base output × service config token_multiplier
        (Opus 4.7 = 1.6, Sonnet/Haiku 4.6 = 1.1, OpenAI = 1.0). Thresholds,
        logs, and SSE events all operate in real-token space so behaviour
        matches what the context gauge displays.

        Two fractions govern behaviour (both of max_tokens):
          * trigger_fraction (default 0.8) — when NOT forced, skip compact
            while estimated tokens are still below this. 0.8 matches the
            LLM API auto-trigger: compact kicks in once the context hits
            80% of the budget. Manual /compact and CC compact_boundary
            pass force=True and bypass this check.
          * target_fraction (default 0.25) — HARD cap on output size. The
            iterative algorithm below guarantees output ≤ cap; a terminal
            force_fit brute-truncates content if earlier steps fell short.

        Algorithm (always converges, 5 steps):
          0. Cleanup (orphans, images, base64).
          1. Summarise tail[:-RECENT] into a new level-1 bucket; output =
             header + saved_recent. If ≤ cap → done.
          2. rollup_all_except_last (needs ≥ 3 buckets) → retry output.
          3. collapse_all (needs ≥ 2 buckets) → retry output.
          4. Shrink saved_recent from (25 conv / 100 msgs) to (6 / 20),
             summarise ejected messages into a new bucket → retry.
          5. force_fit brute-truncate message contents to cap.
        """
        _cpt = chars_per_token if chars_per_token > 0 else 3.5
        # Circuit breaker: skip auto-compact after N consecutive failures.
        # Forced compacts (manual /compact, CC compact_boundary) bypass —
        # the user / CC explicitly asked so we still try.
        if not force:
            _tripped = self._compact_breaker_should_skip(
                conversation_id, agent_name)
            if _tripped:
                logger.warning(
                    "[compact] circuit breaker tripped for %s/%s "
                    "(%d consecutive failures) — skipping auto-compact; "
                    "manual /compact still works.",
                    conversation_id[:8] if conversation_id else "?",
                    agent_name or "-", _tripped)
                return messages
        # Resolve token_multiplier once from the service config so every
        # _estimate_tokens below returns real-tokenizer cost.
        from core.token_counter import resolve_token_multiplier
        _tmul = resolve_token_multiplier(
            getattr(client, "_config_ref", None))

        # ── Phase -1: Advance the shared pyramid ──
        # The shared pyramid is the authoritative history asset, built
        # and maintained by core.bg_bucket_builder (the only writer).
        # This hot path is READ-ONLY on the pyramid.
        #
        # Always block on build_now_sync (when user_id is known) so
        # the pyramid is caught up before we assemble — partial flush
        # included for any msgs in progress. Strict guarantee: the
        # tail handed to assemble never exceeds TAIL_TOKEN_BUDGET, so
        # downstream steps stay deterministic (2a truncate + 2d
        # force_fit) without ever needing an LLM-summarize fallback.
        # In steady state the bg-builder keeps the gap small and this
        # call is a no-op (`_pick_chunk` returns []); it only blocks
        # if bg fell behind, which is exactly when blocking is the
        # correct behaviour — it pays a few seconds once to keep
        # output fidelity intact, instead of silently truncating
        # content via force_fit. Manual /compact and CC compact_
        # boundary already passed force=True; auto-trigger at 80%
        # benefits from the same guarantee.
        _bucket_store = None
        if conversation_id:
            try:
                from core.bg_bucket_builder import BgBucketBuilder
                from core.bucket_store import BucketStore
                from core.conversation_store import ConversationStore
                _bb = BgBucketBuilder.instance()
                if user_id:
                    try:
                        _bb.build_now_sync(
                            conversation_id, user_id,
                            allow_partial=True)
                    except Exception:
                        logger.warning(
                            "[compact] build_now_sync failed — continuing "
                            "with whatever pyramid state exists",
                            exc_info=True)
                _conv_dir = ConversationStore.instance()._conv_dir(conversation_id)
                _bucket_store = BucketStore.get(_conv_dir)
                _last_seq = _bucket_store.last_seq
                if _last_seq > 0:
                    _before = len(messages)
                    messages = [
                        m for m in messages
                        if m.role == "system" or m.seq > _last_seq
                    ]
                    _dropped = _before - len(messages)
                    if _dropped:
                        logger.info(
                            "[compact] pyramid pre-filter: dropped %d msgs "
                            "covered (last_seq=%d, pyramid_objects=%d)",
                            _dropped, _last_seq, _bucket_store.object_count)
            except Exception as _bs_err:
                logger.warning(
                    "[compact] bucket store init failed: %s — falling back "
                    "to tail-only compact (no pyramid header)",
                    _bs_err)
                _bucket_store = None

        # ── Phase 0: Cleanup ──
        messages = [m for m in messages if getattr(m, 'role', '') != 'sub_agent_trace']

        # Remove orphan tool results
        _valid_tc_ids = set()
        for m in messages:
            if m.role == "assistant" and m.tool_calls:
                for tc in m.tool_calls:
                    _valid_tc_ids.add(tc.id)
        _pre_orphan = len(messages)
        messages = [
            m for m in messages
            if m.role != "tool"
            or getattr(m, 'tool_call_id', None) in _valid_tc_ids
        ]
        if len(messages) < _pre_orphan:
            logger.info(f"[compact] Removed {_pre_orphan - len(messages)} orphan tool result(s)")

        # Deflate old images
        self._deflate_image_messages(messages, keep_last=True,
                                      user_id=user_id, conversation_id=conversation_id)

        # Strip base64 blobs
        import re as _re_b64
        for m in messages:
            if not isinstance(m.content, str) or len(m.content) < 5000:
                continue
            if not self._detect_base64_blob(m.content):
                continue
            m.content = _re_b64.sub(
                r'data:[^;]*;base64,[A-Za-z0-9+/=]+',
                '[base64 image removed — use show_file to view]',
                m.content,
            )
            m.content = _re_b64.sub(
                r'[A-Za-z0-9+/=]{1000,}',
                '[binary data removed]',
                m.content,
            )

        # NOTE: do NOT call _clear_seen_tool_results here — it stores to FileStore
        # which is wrong during compaction (thousands of files). Compaction uses
        # _truncate_tool_results (in-place truncation) in the output window.

        # ════════════════════════════════════════════════════════════════
        #  Iterative reduce-to-cap algorithm (5 steps, always converges)
        # ════════════════════════════════════════════════════════════════
        _original_count = len(messages)
        cap = int(max_tokens * target_fraction)
        trigger = int(max_tokens * trigger_fraction)
        _bucket_target = max(1000, int(max_tokens * 0.05))

        system_msg = messages[0] if messages and messages[0].role == "system" else None
        start_idx = 1 if system_msg else 0

        from core.llm_client import _peek_persisted_seq
        import time as _t_compact

        def _collect_recent_files(msgs: List[LLMMessage], limit: int = 5) -> List[Dict]:
            """Walk msgs, pull the last `limit` file tool_call args.

            Each entry: {path, offset?, limit?, service?}. Keeps the most
            recent read/edit/write per path (dedup by path). Used to
            inject a "you were editing X" hint in the compact output so
            the agent doesn't lose track of the files it was working on.
            Inspired by CC's postCompact file-restore attachment.
            """
            out: Dict[str, Dict] = {}  # path -> info (overwritten = most recent)
            _FILE_TOOLS = {"read", "edit", "write"}
            for m in msgs:
                if m.role != "assistant" or not m.tool_calls:
                    continue
                for tc in m.tool_calls:
                    _name = (getattr(tc, "name", "") or "").lower()
                    if _name not in _FILE_TOOLS:
                        continue
                    _args = tc.arguments if isinstance(tc.arguments, dict) else {}
                    _path = _args.get("path") or _args.get("file_path") or ""
                    if not _path:
                        continue
                    entry = {"path": _path, "tool": _name}
                    if _name == "read":
                        _off = _args.get("offset")
                        _lim = _args.get("limit")
                        if _off:
                            entry["offset"] = int(_off)
                        if _lim:
                            entry["limit"] = int(_lim)
                    _svc = _args.get("source") or _args.get("filesystem") or ""
                    if _svc:
                        entry["service"] = _svc
                    out[_path] = entry  # overwrite → keep latest
            return list(out.values())[-limit:]

        def _format_files_note(files: List[Dict]) -> str:
            if not files:
                return ""
            lines = [
                "\n\n[Files you were working with (state lost after compact). "
                "Re-read them now with the exact same parameters to restore "
                "your working view before continuing:]"
            ]
            for fi in files:
                _p = fi.get("path", "")
                _call = f"  - read(path={_p!r}"
                params = []
                if "offset" in fi:
                    params.append(f"offset={fi['offset']}")
                if "limit" in fi:
                    params.append(f"limit={fi['limit']}")
                if params:
                    _call += ", " + ", ".join(params)
                if "service" in fi:
                    _call += f", source={fi['service']!r}"
                _call += ")"
                lines.append(_call)
            return "\n".join(lines)

        # Pyramid header read ONCE here (fresh from store). Step 2c may
        # replace _pyramid_header with a compressed version (private to
        # this compact, does NOT touch the shared pyramid). _build_output
        # uses the current value of _pyramid_header at call time via
        # closure — re-binding it is how 2c takes effect.
        _pyramid_header = (
            _bucket_store.assemble_summary_header() if _bucket_store else "")

        def _build_output(saved: List[LLMMessage]) -> List[LLMMessage]:
            """Assemble system + pyramid_header (context bridge) + saved."""
            self._truncate_tool_results(saved)
            out: List[LLMMessage] = []
            if system_msg:
                out.append(system_msg)
            if _pyramid_header:
                if saved:
                    _frt = min(m.timestamp for m in saved)
                    _frs = min(m.seq for m in saved)
                else:
                    _frt = _t_compact.time()
                    _frs = _peek_persisted_seq(conversation_id) + 2
                _postamble = (
                    "\nThe recent messages below are the current state. "
                    "Do NOT restart or re-propose completed work. If you need "
                    "more detail than the summary above (commits, file contents, "
                    "tool arguments), call read_history."
                )
                _files_note = _format_files_note(
                    _collect_recent_files(messages, limit=5))
                out.append(LLMMessage(
                    role="user",
                    content=_pyramid_header + _postamble + _files_note,
                    timestamp=_frt - 0.002,
                    seq=_frs - 2,
                    source={"type": "context"},
                    conversation_id=conversation_id,
                ))
                out.append(LLMMessage(
                    role="assistant",
                    content="Understood. I have the summary and will continue from the recent messages.",
                    source={"type": "context"},
                    timestamp=_frt - 0.001,
                    seq=_frs - 1,
                    conversation_id=conversation_id,
                ))
            out.extend(saved)
            return out

        def _estimate(msgs: List[LLMMessage]) -> int:
            return self._estimate_tokens(
                msgs, tool_defs=tool_defs,
                chars_per_token=chars_per_token,
                token_multiplier=_tmul)

        # Hot path never calls add_bucket. Bucket creation is owned
        # exclusively by core.bg_bucket_builder — either sync-fired
        # above (force=True) or async via maybe_trigger. The squeeze
        # phase below may LLM-digest the tail, but that digest stays
        # private (source.type="private_compaction") and never enters
        # the shared pyramid.

        # ── Skip/trigger check ──
        _initial_output = _build_output(messages[start_idx:])
        _original_tokens = _estimate(_initial_output)

        if not force and _original_tokens <= trigger:
            return messages

        logger.info("[compact] %s: %d tokens (trigger=%d, cap=%d, %d msgs)",
                    "FORCED" if force else "TRIGGERED",
                    _original_tokens, trigger, cap, _original_count)

        # ── Pre-compact hooks ──
        # Third-party code can modify compact_instructions or abort via
        # core.compact_hooks.subscribe_pre_compact(...).
        from core.compact_hooks import fire_pre_compact, fire_post_compact
        _trigger_label = "manual" if force else "auto"
        _pre_ctx = {
            "trigger": _trigger_label,
            "conversation_id": conversation_id,
            "agent_name": agent_name,
            "user_id": user_id,
            "compact_instructions": compact_instructions or "",
            "force": bool(force),
            "original_tokens": _original_tokens,
        }
        _pre_result = fire_pre_compact(_pre_ctx)
        if _pre_result.get("abort"):
            logger.info("[compact] pre-hook aborted compaction — returning "
                        "messages unchanged")
            return messages
        compact_instructions = _pre_result.get("compact_instructions", "") or ""
        _user_display = _pre_result.get("user_display_message", "") or ""
        if _user_display and conversation_id:
            try:
                from core.conversation_event_bus import ConversationEventBus
                ConversationEventBus.instance().publish_event(
                    conversation_id, "compact_progress",
                    {"stage": "hook_message", "message": _user_display,
                     "agent": agent_name})
            except Exception:
                logger.debug("compact hook_message publish failed", exc_info=True)

        # Circuit breaker tracking: everything past this point counts as
        # "compact attempted" — a clean return resets the counter, any
        # exception increments. Skipped-by-trigger runs above don't
        # touch the counter so they don't mask real failures.
        try:
            # ═════════════════════════════════════════════════════════
            #  Hot-path squeeze (4 steps, pyramid read-only)
            # ═════════════════════════════════════════════════════════
            # Section A = [sys] + [pyramid_header bridge]
            # Section B = messages post pre-filter (uncovered tail)
            # Output   = A + B, must fit ≤ cap.
            # ─────────────────────────────────────────────────────────

            saved_recent = messages[start_idx:]
            compacted = _build_output(saved_recent)
            new_estimate = _estimate(compacted)

            # ── STEP 2a: truncate tool results in tail (deterministic) ──
            if new_estimate > cap:
                logger.info(
                    "[compact] step 2a tool-result truncate (%d > cap %d)",
                    new_estimate, cap)
                self._truncate_tool_results(saved_recent)
                compacted = _build_output(saved_recent)
                new_estimate = _estimate(compacted)

            # ── STEP 2d: force-fit (brute truncate) — hard guarantee ──
            # If we're still over cap after build_now_sync + 2a, the bg
            # builder's TAIL_TOKEN_BUDGET invariant is broken: either it
            # didn't fire (config mismatch / starved executor) or one
            # tool result is so big that even truncated to _TOOL_TRUNC_LIMIT
            # the tail busts cap. Either way we don't run an LLM call in
            # the hot path — force_fit is deterministic and guarantees
            # convergence. The WARNING is the alarm bell so chronic
            # invariant breakage gets noticed.
            if new_estimate > cap:
                logger.warning(
                    "[compact] step 2d force-fit: %d > cap %d after "
                    "tool-truncate. bg_bucket_builder TAIL_TOKEN_BUDGET "
                    "invariant likely broken — investigate why tail wasn't "
                    "absorbed.",
                    new_estimate, cap)
                compacted = self._force_fit_context(
                    compacted, cap,
                    chars_per_token=chars_per_token,
                    tool_defs=tool_defs,
                    token_multiplier=_tmul,
                    conversation_id=conversation_id)
                new_estimate = _estimate(compacted)

            logger.info("[compact] Final: %d tokens (was %d, cap %d), "
                        "%d messages (was %d)",
                        new_estimate, _original_tokens, cap,
                        len(compacted), _original_count)

            # ── Phase final: persist + orphan cleanup + SSE ──
            self._persist_context(compacted, conversation_id, agent_name)
            if conversation_id:
                self._cleanup_orphan_files(compacted, conversation_id)
            if conversation_id:
                try:
                    from core.conversation_event_bus import ConversationEventBus
                    from core.conversation_store import ConversationStore
                    # Total messages in the conversation — what the
                    # user actually thinks of as "the conversation
                    # size". `before` / `_original_count` is the
                    # PER-AGENT context size, which starts much
                    # smaller and is meaningless to display without
                    # the total as a reference.
                    _conv_total = 0
                    try:
                        _conv_total = int(ConversationStore.instance()
                            .message_count(conversation_id))
                    except Exception:
                        pass
                    ConversationEventBus.instance().publish_event(
                        conversation_id, "compact_progress", {
                            "stage": "done",
                            "agent": agent_name,
                            "before": _original_count,
                            "after": len(compacted),
                            "tokens_before": _original_tokens,
                            "tokens_after": new_estimate,
                            "conv_total_messages": _conv_total,
                        })
                except Exception:
                    logger.debug("compact SSE publish failed", exc_info=True)
            # ── Post-compact hooks ──
            # Hooks can mutate _post_ctx["compacted"] in place to append
            # extra messages (e.g. plan attachment, skill listing).
            _post_ctx = {
                "trigger": _trigger_label,
                "conversation_id": conversation_id,
                "agent_name": agent_name,
                "user_id": user_id,
                "before_messages": _original_count,
                "after_messages": len(compacted),
                "tokens_before": _original_tokens,
                "tokens_after": new_estimate,
                "compacted": compacted,
            }
            try:
                fire_post_compact(_post_ctx)
            except Exception:
                logger.debug("post_compact hooks raised", exc_info=True)
            self._compact_breaker_record(
                conversation_id, agent_name, succeeded=True)
            return compacted
        except Exception:
            self._compact_breaker_record(
                conversation_id, agent_name, succeeded=False)
            raise


    @staticmethod
    def _cleanup_orphan_files(compacted: List[LLMMessage],
                              conversation_id: str):
        """Delete tool_result spillover files no longer referenced in context.

        After compaction, old tool_result file_ids are lost from the context.
        Delete them from FileStore to avoid orphan files.
        """
        try:
            import re
            from core.file_store import FileStore
            store = FileStore.instance()

            # Collect all file_ids still referenced in compacted context
            _fid_pattern = re.compile(r'/files/([a-f0-9]{12})')
            referenced = set()
            for m in compacted:
                text = m.content if isinstance(m.content, str) else str(m.content)
                referenced.update(_fid_pattern.findall(text))

            # Find tool_result files for this conv that are no longer referenced
            orphans = []
            for fid, entry in store._entries.items():
                if (entry.get("category") == "tool_result"
                        and entry.get("conversation_id") == conversation_id
                        and fid not in referenced):
                    orphans.append(fid)

            for fid in orphans:
                store._delete_entry(fid)

            if orphans:
                logger.info("[compact] Cleaned %d orphan tool_result file(s)",
                            len(orphans))
        except Exception as e:
            logger.debug("[compact] Orphan file cleanup failed: %s", e)

    def _truncate_tool_results(self, messages: List[LLMMessage]):
        """Truncate oversized tool results in-place."""
        for m in messages:
            if m.role == "tool" and isinstance(m.content, str) and len(m.content) > self._TOOL_TRUNC_LIMIT:
                m.content = m.content[:self._TOOL_TRUNC_LIMIT] + "\n...[compacted — re-call tool if needed]..."
            elif m.role == "tool" and isinstance(m.content, list):
                text_parts = [p for p in m.content if p.get("type") == "text"]
                text = " ".join(p.get("text", "") for p in text_parts)
                if len(text) > self._TOOL_TRUNC_LIMIT:
                    m.content = text[:self._TOOL_TRUNC_LIMIT] + "\n...[compacted — re-call tool if needed]..."
                else:
                    m.content = text

    def _persist_context(self, compacted: List[LLMMessage],
                         conversation_id: str, agent_name: str):
        """Persist compacted context to ConversationStore."""
        if not conversation_id:
            return
        try:
            from core.conversation_store import ConversationStore
            serialized = self._serialize_messages(compacted)
            ConversationStore.instance().save_agent_context(
                conversation_id, agent_name, serialized,
            )
            logger.info(f"[compact] Persisted context for {conversation_id[:8]} "
                        f"({len(compacted)} messages)")
        except Exception as e:
            logger.warning(f"[compact] Failed to persist context: {e}")
