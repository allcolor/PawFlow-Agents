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
            # Isolate this LLM call from the user's conversation. Without
            # this, CC's client.complete would resume the live conv session
            # (conv_id is still set on the shared client instance) and the
            # extract prompt + model's answer would leak into the user's
            # chat as rogue turns. Same sentinel pattern as _summarize_via_cc.
            _inner = getattr(client, "_client", client)
            _saved = (
                getattr(_inner, "_conversation_id", ""),
                getattr(_inner, "_agent_name", ""),
                getattr(_inner, "_user_id", ""),
                getattr(_inner, "_event_cid", ""),
            )
            _inner._conversation_id = "_memory_extract"
            _inner._agent_name = "memory"
            _inner._user_id = user_id
            _inner._event_cid = ""
            try:
                resp = client.complete(
                    messages=[LLMMessage(role="user", content=prompt,
                                          conversation_id="_memory_extract")],
                    temperature=0.3,
                    max_tokens=1000,
                )
            finally:
                (_inner._conversation_id, _inner._agent_name,
                 _inner._user_id, _inner._event_cid) = _saved
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
                    agent=agent_name, category=category,
                )
                stored += 1
            if stored:
                logger.info("[compact] Auto-extracted %d memories from summary", stored)
        except Exception as e:
            logger.debug("[compact] LLM auto-extract failed: %s", e)

    def _consolidate_buckets(self, bucket_docs: list, client: LLMClient,
                              user_id: str = "", conversation_id: str = "",
                              agent_name: str = "") -> str:
        """Consolidate N prior phase summaries into ONE higher-level summary.

        Inputs may be a mix of level-1 buckets (single compacts) and
        already-consolidated level>=2 super-buckets. The prompt is
        explicit about this so the LLM does not try to re-expand an
        input that is already a summary-of-summaries.

        Target output size: ~1/3 of concatenated input, computed with
        tiktoken (precise) rather than chars/4 (estimate).
        """
        if not bucket_docs:
            return ""
        parts = []
        for d in bucket_docs:
            lv = int(d.get("level", 1))
            parts.append(
                f"\n=== Phase {d.get('bucket_id')} (level={lv}, "
                f"seq {d.get('first_seq')}..{d.get('last_seq')}) ===\n"
                f"{d.get('summary', '')}\n"
            )
        combined = "".join(parts)
        try:
            from core.token_counter import count_tokens
            total_tokens = count_tokens(combined)
        except Exception:
            total_tokens = len(combined) // 4
        target_tokens = max(1000, total_tokens // 3)

        instructions = (
            "You are consolidating several prior phase summaries into ONE "
            "higher-level summary. Some inputs are already consolidations "
            "of earlier phases (see level= in each header) - do NOT try "
            "to re-expand them, treat them as already-distilled material.\n\n"
            "Each phase follows the 7-section structure (USER_INTENT, "
            "DECISIONS, FILES_MODIFIED, ERRORS, CURRENT_STATE, PENDING, "
            "CONTEXT).\n\n"
            "RULES:\n"
            "- Preserve concrete facts, file paths, commit SHAs, and "
            "decisions that are still in force at the END of the window.\n"
            "- If a later phase contradicts or supersedes an earlier one, "
            "KEEP ONLY the later version - the older one is outdated.\n"
            "- DROP topics that were started AND finished within this "
            "window (a feature planned -> designed -> shipped should "
            "only mention 'shipped').\n"
            "- DROP abandoned or superseded lines of work entirely.\n"
            "- Collapse redundant bullets across phases.\n"
            f"- Output must be at most {target_tokens} tokens (~1/3 of input)."
        )
        from core.llm_client import LLMMessage
        synth = [LLMMessage(role="user", content=combined,
                             conversation_id=conversation_id)]
        try:
            return self._summarize_messages(
                synth, client, max_tokens=target_tokens * 4,
                target_tokens=target_tokens,
                conversation_id=conversation_id,
                agent_name=agent_name,
                compact_instructions=instructions,
                user_id=user_id,
            )
        except Exception as e:
            logger.error("[compact] super-bucket consolidation failed: %s", e)
            return ""

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

        # ── Phase -1: Bucket-store pre-filter ──
        # Hierarchical cache: messages already covered by an existing bucket
        # never need to be re-summarized. Drop them from the input so the
        # summarizer only sees the new tail since the last bucket.
        _bucket_store = None
        _historical_header = ""
        if conversation_id and agent_name:
            try:
                from core.bucket_store import BucketStore
                from core.conversation_store import ConversationStore
                _conv_dir = ConversationStore.instance()._conv_dir(conversation_id)
                _bucket_store = BucketStore.get(_conv_dir, agent_name)
                _historical_header = _bucket_store.assemble_summary_header()
                # Pre-filter by seq. seq is a strictly-monotonic global
                # counter (bootstrap from disk + monotonic per-process +
                # on-disk invariant enforced by the migration) — filtering
                # by seq alone is correct and unambiguous.
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
                            "[compact] bucket pre-filter: dropped %d msgs "
                            "already covered by buckets (last_seq=%d)",
                            _dropped, _last_seq)
            except Exception as _bs_err:
                logger.warning("[compact] bucket store init failed: %s — "
                                "falling back to full-transcript compact",
                                _bs_err)
                _bucket_store = None
                _historical_header = ""

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

        from core.llm_client import _next_msg_seq
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

        def _build_output(saved: List[LLMMessage]) -> List[LLMMessage]:
            """Assemble system + historical_header(from bucket_store) + saved."""
            self._truncate_tool_results(saved)
            out: List[LLMMessage] = []
            if system_msg:
                out.append(system_msg)
            header = _bucket_store.assemble_summary_header() if _bucket_store else ""
            if header:
                if saved:
                    _frt = min(m.timestamp for m in saved)
                    _frs = min(m.seq for m in saved)
                else:
                    _frt = _t_compact.time()
                    _frs = _next_msg_seq(conversation_id) + 2
                _postamble = (
                    "\nThe recent messages below are the current state. "
                    "Do NOT restart or re-propose completed work. If you need "
                    "more detail than the summary above (commits, file contents, "
                    "tool arguments), call read_history."
                )
                # File re-read hint: scan EVERY input message (both the
                # already-bucketed part and saved_recent) so the agent sees
                # any file it was working on, even if that work has been
                # compacted away. Capped to 5 most-recent-per-path.
                _files_note = _format_files_note(
                    _collect_recent_files(messages, limit=5))
                out.append(LLMMessage(
                    role="user",
                    content=header + _postamble + _files_note,
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

        def _build_bucket(msgs_in: List[LLMMessage]) -> None:
            """Summarize msgs_in → new level-1 bucket, appended to store."""
            if not msgs_in or _bucket_store is None:
                return
            try:
                # Find endpoints by seq so first_msg_id and last_msg_id
                # line up with the first_seq / last_seq span — the agent's
                # read_history(action="range", ...) call needs the exact
                # ids of the true span extremes, not just min/max of a
                # shuffled input list.
                _sorted = sorted(msgs_in, key=lambda m: m.seq)
                _first = _sorted[0]
                _last = _sorted[-1]
                _seqs = [m.seq for m in msgs_in]
                _tss = [m.timestamp for m in msgs_in]
                _summary = self._summarize_messages(
                    msgs_in, client,
                    max_tokens=max_tokens,
                    target_tokens=_bucket_target,
                    conversation_id=conversation_id,
                    agent_name=agent_name,
                    compact_instructions=compact_instructions,
                    user_id=user_id,
                )
                if _summary and len(_summary.strip()) >= 20:
                    _bucket_store.add_bucket(
                        first_seq=min(_seqs), last_seq=max(_seqs),
                        first_ts=min(_tss), last_ts=max(_tss),
                        summary=_summary,
                        first_msg_id=getattr(_first, "msg_id", "") or "",
                        last_msg_id=getattr(_last, "msg_id", "") or "",
                        msg_count=len(msgs_in),
                        model=getattr(client, "default_model", "") or "")
                    if user_id:
                        try:
                            _ex_client = client
                            _sum_llm, _, _ = self._get_summarizer_client(user_id)
                            if _sum_llm:
                                _ex_client = _sum_llm
                            self._auto_extract_memories(
                                _summary, _ex_client, user_id,
                                agent_name=agent_name,
                                conversation_id=conversation_id)
                        except Exception:
                            logger.debug("memory extract failed", exc_info=True)
            except Exception as _e:
                logger.error("[compact] bucket summarize failed: %s", _e)

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
            # ── STEP 1: new bucket from (messages - last 25-conv-turn window) ──
            _split = _select_recent_messages(
                messages, start_idx=start_idx,
                min_conversation=25, max_total=100)
            saved_recent = messages[_split:]
            _to_summarize = messages[start_idx:_split]
            _build_bucket(_to_summarize)

            compacted = _build_output(saved_recent)
            new_estimate = _estimate(compacted)

            # ── STEP 2: rollup [B_1..B_{N-1}] → SB, keep B_N ──
            if new_estimate > cap and _bucket_store and _bucket_store.object_count >= 3:
                logger.info("[compact] step 2 rollup (%d > cap %d, %d buckets)",
                            new_estimate, cap, _bucket_store.object_count)
                try:
                    _inputs = _bucket_store.get_rollup_input()
                    _sb = self._consolidate_buckets(
                        _inputs, client, user_id=user_id,
                        conversation_id=conversation_id, agent_name=agent_name)
                    if _sb and len(_sb.strip()) >= 20:
                        _bucket_store.rollup_all_except_last(
                            _sb, model=getattr(client, "default_model", "") or "")
                except Exception as _e:
                    logger.warning("[compact] step 2 rollup failed: %s", _e)
                compacted = _build_output(saved_recent)
                new_estimate = _estimate(compacted)

            # ── STEP 3: collapse all buckets → single SB ──
            if new_estimate > cap and _bucket_store and _bucket_store.object_count >= 2:
                logger.info("[compact] step 3 collapse (%d > cap %d, %d buckets)",
                            new_estimate, cap, _bucket_store.object_count)
                try:
                    _inputs = _bucket_store.get_collapse_input()
                    _single = self._consolidate_buckets(
                        _inputs, client, user_id=user_id,
                        conversation_id=conversation_id, agent_name=agent_name)
                    if _single and len(_single.strip()) >= 20:
                        _bucket_store.collapse_all(
                            _single, model=getattr(client, "default_model", "") or "")
                except Exception as _e:
                    logger.warning("[compact] step 3 collapse failed: %s", _e)
                compacted = _build_output(saved_recent)
                new_estimate = _estimate(compacted)

            # ── STEP 4: shrink saved window 25/100 → 6/20, eject 30 into new bucket ──
            if new_estimate > cap and len(saved_recent) > 20:
                logger.info("[compact] step 4 shrink-saved (%d > cap %d)",
                            new_estimate, cap)
                _fb_split = _select_recent_messages(
                    messages, start_idx=start_idx,
                    min_conversation=6, max_total=20)
                if _fb_split > _split:
                    _ejected = messages[_split:_fb_split]
                    _build_bucket(_ejected)
                    saved_recent = messages[_fb_split:]
                    compacted = _build_output(saved_recent)
                    new_estimate = _estimate(compacted)

            # ── STEP 5: force-fit content (brute truncate) — invariant garantee ──
            if new_estimate > cap:
                logger.warning(
                    "[compact] step 5 force-fit: %d > cap %d", new_estimate, cap)
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
                    ConversationEventBus.instance().publish_event(
                        conversation_id, "compact_progress", {
                            "stage": "done",
                            "agent": agent_name,
                            "before": _original_count,
                            "after": len(compacted),
                            "tokens_before": _original_tokens,
                            "tokens_after": new_estimate,
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
