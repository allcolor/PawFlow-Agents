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

# Compact source is always: shared bucket header (inside _compact) + this
# bounded raw tail. Do not feed full transcript/shared context into _compact.
COMPACT_TAIL_MESSAGES = 250



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

    def _load_compact_source_messages(self, store, conversation_id: str,
                                      agent_name: str = "",
                                      user_id: str = "",
                                      tail_limit: int = COMPACT_TAIL_MESSAGES
                                      ) -> List[LLMMessage]:
        """Load the only valid non-independent compact source.

        _compact() assembles old history from BucketStore. Its message input
        must be a bounded raw tail only, otherwise /compact and provider
        compact re-tokenize the entire transcript and defeat the bucket model.
        """
        if agent_name and agent_name not in ("", "ALL", "shared"):
            source_data = store.load_transcript_tail_for_agent(
                conversation_id, agent_name, limit=tail_limit)
        else:
            source_data = store.load_shared_tail(
                conversation_id, user_id=user_id, limit=tail_limit)
        source_data = [
            m for m in (source_data or [])
            if isinstance(m, dict) and not m.get("display_only")
        ]
        if not source_data:
            raise RuntimeError("No context to compact")
        return self._deserialize_messages(
            source_data, conversation_id=conversation_id)

    def _compact_context_from_store(self, store, conversation_id: str,
                                    agent_name: str = "",
                                    user_id: str = "",
                                    max_tokens: int = 200000,
                                    compact_client: LLMClient | None = None,
                                    trigger_fraction: float = 0.8,
                                    compact_instructions: str = "",
                                    force: bool = True,
                                    budget_config: dict | None = None,
                                    independent_context: bool = False,
                                    post_hooks_async: bool = False,
                                    tool_defs: list = None,
                                    chars_per_token: float = 0,
                                    tail_limit: int = COMPACT_TAIL_MESSAGES,
                                    stats: dict | None = None,
                                    ) -> List[LLMMessage]:
        """Run the canonical PawFlow compaction procedure for a store context.

        Manual /compact and provider-triggered compact both come through here.
        The only valid shared-context source is the bucket header assembled by
        _compact() plus this bounded raw tail; independent task contexts keep
        their isolated full context because they do not use the shared pyramid.
        """
        if independent_context:
            raw = store.load_agent_context(conversation_id, agent_name)
            if not raw:
                raise RuntimeError("No context to compact")
            messages = self._deserialize_messages(
                raw, conversation_id=conversation_id)
            if compact_client is None:
                compact_client, _, _ = self._get_summarizer_client(
                    user_id, conversation_id=conversation_id)
            if compact_client is None:
                raise RuntimeError(
                    "No summarizer_service configured. Cannot compact.")
        else:
            messages = self._load_compact_source_messages(
                store, conversation_id, agent_name, user_id=user_id,
                tail_limit=tail_limit)

        logger.info("[agent:%s] Loaded %d compact source messages",
                    conversation_id[:8], len(messages))
        try:
            if stats is not None:
                stats["before"] = len(messages)
                stats["tokens_before"] = self._estimate_tokens(
                    messages, tool_defs=tool_defs,
                    chars_per_token=chars_per_token)
        except Exception:
            logger.debug("compact source metric estimate failed", exc_info=True)
        return self._compact(
            messages, compact_client, max_tokens,
            trigger_fraction=trigger_fraction,
            conversation_id=conversation_id,
            agent_name=agent_name,
            tool_defs=tool_defs,
            chars_per_token=chars_per_token,
            compact_instructions=compact_instructions,
            force=force,
            user_id=user_id,
            budget_config=budget_config,
            independent_context=independent_context,
            post_hooks_async=post_hooks_async,
        )

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
        _synth_call_kwargs = {
            "call_user_id": ctx.get("user_id", ""),
            "call_conversation_id": conversation_id,
            "call_agent_name": ctx.get("active_agent_name", ""),
            "call_event_cid": ctx.get("_event_cid", conversation_id),
            "call_ephemeral_stream": False,
        }
        for _attempt in range(2):
            try:
                if use_streaming and token_callback:
                    resp = client.complete_stream(
                        messages=synth_context, model=model,
                        temperature=ctx["temperature"],
                        max_tokens=ctx["max_tokens"],
                        tools=None, callback=token_callback,
                        **_synth_call_kwargs,
                    )
                else:
                    resp = client.complete(
                        messages=synth_context, model=model,
                        temperature=ctx["temperature"],
                        max_tokens=ctx["max_tokens"],
                        tools=None,
                        **_synth_call_kwargs,
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

        # `max_tokens` here is already the compact cap passed in by
        # the caller (cap = real_max × target_fraction = 0.25 × max,
        # i.e. 50k for a 200k model). Don't re-multiply by 0.25 — the
        # earlier `target = int(max_tokens * 0.25) - td_tokens`
        # computed 6.25% of the real max instead of 25%, hard-
        # truncating to 12.5k when force-fit fired. Use the cap
        # directly minus the constant tool-defs overhead.
        target = int(max_tokens) - int(td_tokens)
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
        budget_config: dict | None = None,
        independent_context: bool = False,
        post_hooks_async: bool = False,
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
        # Resolve budget-sensitive settings from the active agent LLM
        # service, not necessarily from `client`: compaction often uses a
        # separate summarizer client to write summaries, while the cap/gauge
        # must follow the service whose context will receive the result.
        _budget_cfg = (budget_config
                       or getattr(client, "config", None)
                       or getattr(client, "_config_ref", None)
                       or getattr(getattr(client, "_client", None), "_config_ref", None)
                       or {})
        from core.token_counter import resolve_token_multiplier
        _tmul = resolve_token_multiplier(_budget_cfg)

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
        # ── Compact contract (instant, deterministic, no LLM call) ──
        #
        #   Output = system + pyramid_header + bridge + recent_tail
        #     pyramid_header   ≤ HEADER_BUDGET (30k)  — what bg already
        #                                              consolidated
        #     recent_tail      ≤ TAIL_TARGET (20k)    — walked back
        #                                              from end of
        #                                              transcript by
        #                                              token budget
        #     total            ≤ cap (50k = 0.25 × max for 200k model)
        #
        # NO bg build_now_sync here — that would spawn a CC subprocess
        # to summarize a pending partial bucket, defeating "compact is
        # instant". Bg runs async via maybe_trigger and stays caught
        # up under TAIL_TOKEN_BUDGET (20k) most of the time. If bg is
        # behind, the pyramid_header reflects fewer covered msgs and
        # the recent_tail walk-back below picks up the slack — same
        # final user-visible content, just sliced differently.
        # NO pre-filter on `m.seq > pyramid.last_seq` either. The
        # contract takes the X MOST RECENT transcript rows by token
        # budget, regardless of whether they're also covered by the
        # summary. Overlap is intentional: the summary is dense
        # context, the raw tail is fidelity for what the agent will
        # immediately respond to.
        _bucket_store = None
        if conversation_id and not independent_context:
            try:
                from core.bucket_store import BucketStore
                from core.conversation_store import ConversationStore
                _conv_dir = ConversationStore.instance()._conv_dir(conversation_id)
                _bucket_store = BucketStore.get(_conv_dir)
            except Exception as _bs_err:
                logger.warning(
                    "[compact] bucket store init failed: %s — falling back "
                    "to tail-only compact (no pyramid header)",
                    _bs_err)
                _bucket_store = None

        # ── Phase 0: Cleanup ──
        # A post-compact context is always rebuilt from the shared pyramid
        # header plus a raw recent tail. Messages injected by a previous
        # compact are neither; drop them before the tail walk-back so a
        # repeated compact never compacts an old bridge as if it were user
        # transcript.
        def _is_synthetic_compact_msg(m: LLMMessage) -> bool:
            source = getattr(m, 'source', None) or {}
            source_type = source.get("type") if isinstance(source, dict) else ""
            return source_type in {"context", "private_compaction"}

        messages = [
            m for m in messages
            if getattr(m, 'role', '') != 'sub_agent_trace'
            and not _is_synthetic_compact_msg(m)
        ]

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
        # Compact target precedence:
        #   1. service config `compact_target_tokens` (absolute, in tokens) —
        #      enforced ≤ 40% of max at service install time; runtime falls
        #      back to the formula if somehow that bound is exceeded here.
        #   2. legacy fraction (`target_fraction`, default 0.25 × max).
        # `_budget_cfg` is the active service budget config. Do not read the
        # summarizer config here: otherwise a Codex appserver compact can use
        # the summarizer's legacy 25% cap instead of the agent service's
        # explicit `compact_target_tokens`.
        try:
            _abs_cap = int(_budget_cfg.get("compact_target_tokens", 0) or 0)
        except (TypeError, ValueError):
            _abs_cap = 0
        if _abs_cap > 0 and _abs_cap <= int(max_tokens * 0.4):
            cap = _abs_cap
        else:
            if _abs_cap > 0:
                logger.warning(
                    "[compact] compact_target_tokens=%d exceeds 40%% of "
                    "max_context_size=%d — falling back to %.0f%% formula",
                    _abs_cap, max_tokens, target_fraction * 100)
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
            """Assemble system + pyramid_header (context bridge) + saved.

            Pure assembly — NO truncation. Earlier versions called
            `self._truncate_tool_results(saved)` here unconditionally,
            which clipped every tool result > 800 chars on every
            assemble call regardless of whether the output actually
            fit the cap. Symptom: a compact with header=10k and a
            tail rich in tool I/O (e.g. 88 transcript rows) would
            truncate to ~30k even when the full content fit easily
            in the 50k cap. Step 2a in the caller now owns the
            decision to truncate (only when new_estimate > cap).
            """
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

        def _estimate_tail_selection_cost(m: LLMMessage) -> int:
            """Estimate the token cost used for tail walk-back selection.

            Oversized tool results are selected at their post-truncation cost,
            because step 2a will truncate them before persisting the final
            compacted context. Using the raw cost here can stop the walk-back
            early and leave thousands of target tokens unused.
            """
            if m.role != "tool":
                return _estimate([m])
            content = m.content
            if isinstance(content, str):
                if len(content) <= self._TOOL_TRUNC_LIMIT:
                    return _estimate([m])
                content = (
                    content[:self._TOOL_TRUNC_LIMIT]
                    + "\n...[compacted — re-call tool if needed]..."
                )
            elif isinstance(content, list):
                text_parts = [p for p in content if p.get("type") == "text"]
                text = " ".join(p.get("text", "") for p in text_parts)
                if len(text) > self._TOOL_TRUNC_LIMIT:
                    content = (
                        text[:self._TOOL_TRUNC_LIMIT]
                        + "\n...[compacted — re-call tool if needed]..."
                    )
                else:
                    content = text
            else:
                return _estimate([m])
            return _estimate([LLMMessage(
                role=m.role,
                content=content,
                tool_call_id=getattr(m, 'tool_call_id', None),
                timestamp=getattr(m, 'timestamp', 0.0),
                seq=getattr(m, 'seq', 0),
                source=getattr(m, 'source', None),
                conversation_id=getattr(m, 'conversation_id', None),
            )])

        def _clone_with_content(m: LLMMessage, content: Any) -> LLMMessage:
            return LLMMessage(
                role=m.role,
                content=content,
                tool_call_id=getattr(m, 'tool_call_id', None),
                tool_calls=getattr(m, 'tool_calls', None),
                timestamp=getattr(m, 'timestamp', 0.0),
                seq=getattr(m, 'seq', 0),
                source=getattr(m, 'source', None),
                conversation_id=getattr(m, 'conversation_id', None),
            )

        def _truncate_message_to_budget(m: LLMMessage,
                                        token_budget: int) -> LLMMessage:
            """Shrink one oversized non-tool tail message to its budget.

            The tail selector keeps at least the newest message. When that
            message is itself larger than the tail budget, the old fallback
            built an over-cap compact and let global force-fit crush the whole
            output far below compact_target_tokens. Fit just that message
            instead so the final compact still uses the configured budget.
            """
            content = getattr(m, 'content', None)
            if isinstance(content, list):
                text = " ".join(
                    str(p.get("text", "")) for p in content
                    if isinstance(p, dict) and p.get("type") == "text")
            elif isinstance(content, str):
                text = content
            else:
                return m
            if _estimate([m]) <= token_budget:
                return m

            marker = "\n...[compacted to fit tail budget; use read_history for full message]...\n"

            def _candidate(keep_chars: int) -> LLMMessage:
                if keep_chars <= 0:
                    body = marker.strip()
                elif len(text) <= keep_chars:
                    body = text
                else:
                    head = max(0, keep_chars // 2)
                    tail = max(0, keep_chars - head)
                    body = text[:head] + marker + (text[-tail:] if tail else "")
                return _clone_with_content(m, body)

            low, high = 0, len(text)
            best = _candidate(0)
            while low <= high:
                mid = (low + high) // 2
                cand = _candidate(mid)
                if _estimate([cand]) <= token_budget:
                    best = cand
                    low = mid + 1
                else:
                    high = mid - 1
            return best

        def _is_independent_summary(m: LLMMessage) -> bool:
            source = getattr(m, 'source', None) or {}
            source_type = source.get("type") if isinstance(source, dict) else ""
            return source_type == "independent_compaction"

        def _compact_independent_context() -> List[LLMMessage]:
            """Compact an isolated context without the shared bg pyramid."""
            _initial_output = [system_msg] if system_msg else []
            _initial_output.extend(messages[start_idx:])
            _original_tokens = _estimate(_initial_output)
            if not force and _original_tokens < trigger:
                return messages

            logger.info("[compact] %s independent context: %d tokens "
                        "(trigger=%d, cap=%d, %d msgs)",
                        "FORCED" if force else "TRIGGERED",
                        _original_tokens, trigger, cap, _original_count)

            _trigger_label = "manual" if force else "auto"
            _pre_ctx = {
                "trigger": _trigger_label,
                "conversation_id": conversation_id,
                "agent_name": agent_name,
                "user_id": user_id,
                "compact_instructions": compact_instructions or "",
                "force": bool(force),
                "original_tokens": _original_tokens,
                "independent_context": True,
            }
            from core.agent_hooks import AgentHookRunner
            _hook_runner = AgentHookRunner(
                user_id=user_id,
                conversation_id=conversation_id,
                agent_name=agent_name,
            )
            _pre_result = _hook_runner.run("pre_compact", _pre_ctx,
                                          fail_policy="closed")
            if _pre_result.get("decision") == "block":
                logger.info("[compact] pre-hook aborted independent compaction")
                return messages
            _pre_payload = _pre_result.get("payload") or {}
            _instructions = _pre_payload.get("compact_instructions", "") or ""
            _instructions = (
                (_instructions + "\n\n") if _instructions else ""
            ) + (
                "This is an isolated task/delegate context, not the main "
                "conversation shared history. Summarise only the messages "
                "provided here. If an earlier independent-context summary is "
                "present, merge it into one updated summary; do not stack or "
                "repeat old summary wrappers. Preserve task goals, decisions, "
                "files touched, tool outcomes, blockers, and remaining work."
            )

            try:
                _summary_target = max(500, min(_bucket_target, max(500, cap // 3)))
                _tail_budget = max(1000, cap - _summary_target - 500)
                _tail_msgs = messages[start_idx:]
                _accum = 0
                _take_from = len(_tail_msgs)
                for _i in range(len(_tail_msgs) - 1, -1, -1):
                    _cost = _estimate([_tail_msgs[_i]])
                    if _accum + _cost > _tail_budget and _i < len(_tail_msgs) - 1:
                        break
                    _accum += _cost
                    _take_from = _i
                saved_recent = _tail_msgs[_take_from:]

                # Previous independent summaries must be folded into the new
                # head summary, never kept as an additional recent-tail message.
                saved_recent = [m for m in saved_recent if not _is_independent_summary(m)]
                _saved_ids = {id(m) for m in saved_recent}
                head = [m for m in _tail_msgs if id(m) not in _saved_ids]

                # Keep tool results paired with their owning tool call at the
                # boundary. If we cannot find the owner, drop the orphan.
                while saved_recent and saved_recent[0].role == "tool":
                    _orphan_id = getattr(saved_recent[0], 'tool_call_id', '')
                    _has_owner = any(
                        m.role == "assistant" and m.tool_calls
                        and any(tc.id == _orphan_id for tc in m.tool_calls)
                        for m in saved_recent[1:]
                    )
                    if _has_owner:
                        break
                    if _take_from > 0:
                        _take_from -= 1
                        saved_recent = [
                            m for m in _tail_msgs[_take_from:]
                            if not _is_independent_summary(m)
                        ]
                        _saved_ids = {id(m) for m in saved_recent}
                        head = [m for m in _tail_msgs if id(m) not in _saved_ids]
                    else:
                        saved_recent = saved_recent[1:]
                        break

                compacted: List[LLMMessage] = []
                if system_msg:
                    compacted.append(system_msg)
                if head:
                    summary = self._summarize_messages(
                        head, client, max_tokens,
                        target_tokens=_summary_target,
                        conversation_id=conversation_id,
                        agent_name=agent_name,
                        compact_instructions=_instructions,
                        user_id=user_id,
                    )
                    _ref_ts = min(
                        [m.timestamp for m in saved_recent if getattr(m, 'timestamp', 0)]
                        or [time.time()])
                    _ref_seq = min(
                        [m.seq for m in saved_recent if getattr(m, 'seq', 0)]
                        or [0])
                    compacted.append(LLMMessage(
                        role="user",
                        content=(
                            "[Independent context summary - earlier messages compacted]\n\n"
                            + (summary or "").strip()
                            + "\n\nThe recent messages below are the current state. "
                            "Continue this isolated task/delegate context from here."
                        ),
                        source={"type": "independent_compaction"},
                        timestamp=_ref_ts - 0.002,
                        seq=_ref_seq - 2 if _ref_seq else 0,
                        conversation_id=conversation_id,
                    ))
                    compacted.append(LLMMessage(
                        role="assistant",
                        content="Understood. I have the task context summary and will continue from the recent messages.",
                        source={"type": "context"},
                        timestamp=_ref_ts - 0.001,
                        seq=_ref_seq - 1 if _ref_seq else 0,
                        conversation_id=conversation_id,
                    ))
                compacted.extend(saved_recent)

                new_estimate = _estimate(compacted)
                if new_estimate > cap:
                    self._truncate_tool_results(saved_recent)
                    compacted = ([system_msg] if system_msg else [])
                    if head:
                        compacted.extend([
                            LLMMessage(
                                role="user",
                                content=(
                                    "[Independent context summary - earlier messages compacted]\n\n"
                                    + (summary or "").strip()
                                    + "\n\nThe recent messages below are the current state. "
                                    "Continue this isolated task/delegate context from here."
                                ),
                                source={"type": "independent_compaction"},
                                conversation_id=conversation_id,
                            ),
                            LLMMessage(
                                role="assistant",
                                content="Understood. I have the task context summary and will continue from the recent messages.",
                                source={"type": "context"},
                                conversation_id=conversation_id,
                            ),
                        ])
                    compacted.extend(saved_recent)
                    new_estimate = _estimate(compacted)
                if new_estimate > cap:
                    compacted = self._force_fit_context(
                        compacted, cap,
                        chars_per_token=chars_per_token,
                        tool_defs=tool_defs,
                        token_multiplier=_tmul,
                        conversation_id=conversation_id)
                    new_estimate = _estimate(compacted)

                logger.info("[compact] Final independent: %d tokens (was %d, cap %d), "
                            "%d messages (was %d)",
                            new_estimate, _original_tokens, cap,
                            len(compacted), _original_count)
                self._persist_context(compacted, conversation_id, agent_name)
                if conversation_id:
                    self._cleanup_orphan_files(compacted, conversation_id)
                _compacted_payload = self._serialize_messages(compacted)
                _post_ctx = {
                    "trigger": _trigger_label,
                    "conversation_id": conversation_id,
                    "agent_name": agent_name,
                    "user_id": user_id,
                    "before_messages": _original_count,
                    "after_messages": len(compacted),
                    "tokens_before": _original_tokens,
                    "tokens_after": new_estimate,
                    "target_tokens": cap,
                    "compacted_messages": _compacted_payload,
                    "compacted": _compacted_payload,
                    "independent_context": True,
                }
                def _run_independent_post_hooks() -> None:
                    _hooks_t0 = time.monotonic()
                    logger.info(
                        "[compact] post hooks start cid=%s agent=%s async=%s independent=True",
                        conversation_id[:8], agent_name, post_hooks_async)
                    try:
                        _hook_runner.run("post_compact", _post_ctx)
                    except Exception:
                        logger.debug("post_compact hooks raised", exc_info=True)
                    finally:
                        logger.info(
                            "[compact] post hooks done cid=%s agent=%s async=%s independent=True elapsed_ms=%.1f",
                            conversation_id[:8], agent_name, post_hooks_async,
                            (time.monotonic() - _hooks_t0) * 1000.0)
                if post_hooks_async:
                    logger.info(
                        "[compact] post hooks scheduled async cid=%s agent=%s independent=True",
                        conversation_id[:8], agent_name)
                    threading.Thread(
                        target=_run_independent_post_hooks,
                        daemon=True,
                        name=f"post-compact-hooks-{conversation_id[:8]}",
                    ).start()
                else:
                    _run_independent_post_hooks()
                self._compact_breaker_record(
                    conversation_id, agent_name, succeeded=True)
                return compacted
            except Exception:
                self._compact_breaker_record(
                    conversation_id, agent_name, succeeded=False)
                raise

        if independent_context:
            return _compact_independent_context()

        # Hot path never calls add_bucket. Bucket creation is owned
        # exclusively by core.bg_bucket_builder — either sync-fired
        # above (force=True) or async via maybe_trigger. The squeeze
        # phase below may LLM-digest the tail, but that digest stays
        # private (source.type="private_compaction") and never enters
        # the shared pyramid.

        # ── Skip/trigger check ──
        _initial_output = _build_output(messages[start_idx:])
        _original_tokens = _estimate(_initial_output)

        if not force and _original_tokens < trigger:
            return messages

        logger.info("[compact] %s: %d tokens (trigger=%d, cap=%d, %d msgs)",
                    "FORCED" if force else "TRIGGERED",
                    _original_tokens, trigger, cap, _original_count)

        # ── Pre-compact hooks ──
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
        from core.agent_hooks import AgentHookRunner
        _hook_runner = AgentHookRunner(
            user_id=user_id,
            conversation_id=conversation_id,
            agent_name=agent_name,
        )
        _pre_result = _hook_runner.run("pre_compact", _pre_ctx,
                                      fail_policy="closed")
        if _pre_result.get("decision") == "block":
            logger.info("[compact] pre-hook aborted compaction — returning "
                        "messages unchanged")
            return messages
        _pre_payload = _pre_result.get("payload") or {}
        compact_instructions = _pre_payload.get("compact_instructions", "") or ""
        _user_display = _pre_payload.get("user_display_message", "") or ""
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
            #  Token-budget tail selection (instant, deterministic)
            # ═════════════════════════════════════════════════════════
            # Walk back from end of transcript, accumulating tokens
            # until we approach the cap when combined with the
            # already-assembled header. Goal: output as close to cap
            # (50k for 200k model) as possible — header_actual + tail
            # ≈ cap. The user's mental model: header is bounded by
            # HEADER_BUDGET (30k) when the pyramid is full, leaving
            # 20k for tail at maximum header; but when the header is
            # smaller (fewer buckets / no rollup yet), the tail can
            # grow proportionally larger to fill the cap. Don't waste
            # cap with a fixed 20k tail ceiling — that artificially
            # caps output at 32k when header is 12k, which the user
            # called out as "Quand je dis 50k, je ne pense pas l'avoir
            # dit en rigolant."
            # Then orphan-fix: if the first kept msg is a tool/tool_
            # result whose tool_call sits OUTSIDE the kept slice,
            # extend backward to include the owning assistant turn
            # (or drop the orphan).
            # ─────────────────────────────────────────────────────────

            # Compute header-side overhead (system + pyramid bridge).
            _header_only = _build_output([])
            _header_tokens = _estimate(_header_only)
            # Bridge / format overhead headroom: each msg adds a small
            # per-message constant in _estimate (role separator etc.);
            # leave ~500 tokens of slack so the final assembled output
            # rounds under cap rather than just-over.
            _SAFETY_MARGIN = 500
            _tail_budget = max(1000, cap - _header_tokens - _SAFETY_MARGIN)

            _tail_msgs = messages[start_idx:]
            # Walk from end accumulating per-msg estimates. Tool results use
            # post-truncation cost here, matching step 2a below, so a large
            # relay/bash output does not block useful older context that still
            # fits inside the target budget after deterministic truncation.
            _accum = 0
            _take_from = len(_tail_msgs)
            _boundary_msg = None
            _boundary_original_cost = 0
            for _i in range(len(_tail_msgs) - 1, -1, -1):
                _cost = _estimate_tail_selection_cost(_tail_msgs[_i])
                if _accum + _cost > _tail_budget and _i < len(_tail_msgs) - 1:
                    # Include at LEAST one msg even if oversized — a single
                    # oversized recent msg beats an empty tail. If we already
                    # have newer messages, use the remaining budget for a
                    # truncated boundary text message instead of stopping at a
                    # tiny tail and wasting most of compact_target_tokens.
                    _remaining = _tail_budget - _accum
                    if _remaining > 0 and _tail_msgs[_i].role != "tool":
                        _candidate = _truncate_message_to_budget(
                            _tail_msgs[_i], _remaining)
                        _candidate_cost = _estimate([_candidate])
                        if _candidate_cost <= _remaining:
                            _boundary_msg = _candidate
                            _boundary_original_cost = _cost
                            _accum += _candidate_cost
                    break
                _accum += _cost
                _take_from = _i
            saved_recent = _tail_msgs[_take_from:]
            if _boundary_msg is not None:
                saved_recent = [_boundary_msg] + saved_recent
                logger.info(
                    "[compact] tail boundary message truncated: %d > "
                    "remaining budget -> %d tokens",
                    _boundary_original_cost, _estimate([_boundary_msg]))

            # Orphan tool_result fix: if the first kept msg is a tool
            # role whose tool_call_id has no preceding assistant
            # tool_call in saved_recent, extend backward until the
            # owning assistant turn is included, or drop the orphan.
            while saved_recent and saved_recent[0].role == "tool":
                _orphan_id = getattr(saved_recent[0], 'tool_call_id', '')
                _has_owner = False
                for _m in saved_recent[1:]:
                    if _m.role == "assistant" and _m.tool_calls:
                        if any(tc.id == _orphan_id for tc in _m.tool_calls):
                            _has_owner = True
                            break
                if _has_owner:
                    break
                # Extend one step back if possible
                if _take_from > 0:
                    _take_from -= 1
                    saved_recent = _tail_msgs[_take_from:]
                else:
                    # Hit start of context — drop the orphan
                    saved_recent = saved_recent[1:]
                    break

            if (len(saved_recent) == 1
                    and saved_recent[0].role != "tool"
                    and _estimate([saved_recent[0]]) > _tail_budget):
                _oversized_before = _estimate([saved_recent[0]])
                saved_recent = [
                    _truncate_message_to_budget(saved_recent[0], _tail_budget)
                ]
                _accum = _estimate(saved_recent)
                logger.info(
                    "[compact] tail oversized message truncated: %d > "
                    "budget %d -> %d tokens",
                    _oversized_before, _tail_budget, _accum)

            logger.info(
                "[compact] tail walk-back: kept %d/%d msgs "
                "(~%d tokens, budget=%d, header=%d, cap=%d)",
                len(saved_recent), len(_tail_msgs),
                _accum, _tail_budget, _header_tokens, cap)

            compacted = _build_output(saved_recent)
            new_estimate = _estimate(compacted)

            # ── STEP 2a: truncate tool results in tail (deterministic) ──
            # Should rarely fire now that the walk-back respects the
            # budget — only triggers if a single huge msg pushed us
            # over (the "include at least one" guarantee above).
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
                        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                    ConversationEventBus.instance().publish_event(
                        conversation_id, "compact_progress", {
                            "stage": "done",
                            "agent": agent_name,
                            "before": _original_count,
                            "after": len(compacted),
                            "tokens_before": _original_tokens,
                            "tokens_after": new_estimate,
                            "target_tokens": cap,
                            "conv_total_messages": _conv_total,
                        })
                except Exception:
                    logger.debug("compact SSE publish failed", exc_info=True)
            # ── Post-compact hooks ──
            _compacted_payload = self._serialize_messages(compacted)
            _post_ctx = {
                "trigger": _trigger_label,
                "conversation_id": conversation_id,
                "agent_name": agent_name,
                "user_id": user_id,
                "before_messages": _original_count,
                "after_messages": len(compacted),
                "tokens_before": _original_tokens,
                "tokens_after": new_estimate,
                "target_tokens": cap,
                "compacted_messages": _compacted_payload,
                "compacted": _compacted_payload,
            }
            def _run_post_hooks() -> None:
                _hooks_t0 = time.monotonic()
                logger.info(
                    "[compact] post hooks start cid=%s agent=%s async=%s",
                    conversation_id[:8], agent_name, post_hooks_async)
                try:
                    _hook_runner.run("post_compact", _post_ctx)
                except Exception:
                    logger.debug("post_compact hooks raised", exc_info=True)
                finally:
                    logger.info(
                        "[compact] post hooks done cid=%s agent=%s async=%s elapsed_ms=%.1f",
                        conversation_id[:8], agent_name, post_hooks_async,
                        (time.monotonic() - _hooks_t0) * 1000.0)
            if post_hooks_async:
                logger.info(
                    "[compact] post hooks scheduled async cid=%s agent=%s",
                    conversation_id[:8], agent_name)
                threading.Thread(
                    target=_run_post_hooks,
                    daemon=True,
                    name=f"post-compact-hooks-{conversation_id[:8]}",
                ).start()
            else:
                _run_post_hooks()
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
            persisted = list(compacted)
            if persisted and persisted[0].role == "system":
                persisted = persisted[1:]
            serialized = self._serialize_messages(persisted)
            ConversationStore.instance().save_agent_context(
                conversation_id, agent_name, serialized,
            )
            logger.info(f"[compact] Persisted context for {conversation_id[:8]} "
                        f"({len(compacted)} messages)")
        except Exception as e:
            logger.warning(f"[compact] Failed to persist context: {e}")
