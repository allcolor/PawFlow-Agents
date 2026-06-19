"""AgentLoopTask mixin — AgentCompaction methods

Auto-extracted from tasks/ai/agent_loop.py.
All methods access self (AgentLoopTask instance).
"""
import json
import logging
import threading
import time
from typing import Dict, List


from core.llm_client import (
    LLMClient, LLMMessage,
)
from tasks.ai.agent_summarize import AgentSummarizeMixin
from tasks.ai._agent_compact_core import _AgentCompactCoreMixin
from tasks.ai._agent_compact_independent import _AgentCompactIndependentMixin

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


class AgentCompactionMixin(
        _AgentCompactCoreMixin, _AgentCompactIndependentMixin,
        AgentSummarizeMixin):
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
