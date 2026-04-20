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
        messages.append(LLMMessage(role="user", content=prompt))
        _cc = compact_client or client
        synth_context = self._compact(
            list(messages), _cc,
            ctx.get("max_context_size", 64000),
            threshold=compact_threshold,
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
                messages.append(LLMMessage(role="assistant", content=resp.content))
                return resp.content, resp.tokens_in, resp.tokens_out, resp.model
            except Exception as synth_err:
                err_str = str(synth_err)
                if _attempt == 0 and ("exceed_context_size" in err_str or "n_prompt_tokens" in err_str):
                    logger.warning("[agent] synthesis overflow, forcing aggressive compaction...")
                    synth_context = self._compact(
                        synth_context, _cc,
                        ctx.get("max_context_size", 64000),
                        threshold=0.4,
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

        est = self._estimate_tokens(result, tool_defs=tool_defs, chars_per_token=chars_per_token)
        if est <= max_tokens:
            logger.info(f"[compact] force-fit step 1 OK: {est} tokens")
            return result

        # Step 2: Drop middle messages, keep system + last N
        logger.warning(f"[compact] force-fit step 1 insufficient ({est} > {max_tokens}), dropping middle")
        keep = []
        if result and result[0].role == "system":
            keep.append(result[0])
            keep.append(LLMMessage(
                role="user",
                content=f"[{len(result) - keep_n - 1} earlier messages dropped to fit context limit]",
            ))
            keep.append(LLMMessage(role="assistant", content="Understood, continuing.", source={"type": "context"}))
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
                    messages=[LLMMessage(role="user", content=prompt)],
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
        synth = [LLMMessage(role="user", content=combined)]
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
        threshold: float = 0.9,
        conversation_id: str = "",
        agent_name: str = "",
        tool_defs: list = None,
        chars_per_token: float = 0,
        compact_instructions: str = "",
        force: bool = False,
        user_id: str = "",
    ) -> List[LLMMessage]:
        """Unified compaction: cleanup + threshold check + summarize + rebuild.

        When force=True, always compacts (used at context load time).
        When force=False, only compacts if estimated tokens exceed threshold.

        Strategy:
        Phase 0: Cleanup (orphans, images, base64, oversized tool results)
        Phase 1: Progressive clearing of old tool results
        Phase 2: LLM-based summarization of old messages
        Phase 3: Rebuild (system + summary + recent + file re-read)
        Phase 4: Persist + notify
        """
        _cpt = chars_per_token if chars_per_token > 0 else 3.5

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
        # _truncate_tool_results (in-place truncation) and _progressive_clear instead.

        # ── Threshold check (skip when forced) ──
        _original_count = len(messages)
        estimated = self._estimate_tokens(messages, tool_defs=tool_defs,
                                          chars_per_token=chars_per_token)
        _original_tokens = estimated
        limit = int(max_tokens * threshold)

        logger.debug(f"[compact] check: {estimated} est. tokens, limit={limit} "
                     f"(max={max_tokens}×{threshold}), {len(messages)} msgs, "
                     f"force={force}")

        if not force and estimated <= limit:
            return messages

        logger.info(f"[compact] {'FORCED' if force else 'TRIGGERED'}: "
                    f"{estimated} tokens, limit={limit}, compacting...")

        # ── Phase 1: Progressive clearing of old tool results ──
        estimated = self._progressive_clear_tool_results(
            messages, limit, estimated,
            keep_recent=6,
            chars_per_token=_cpt,
        )
        if not force and estimated <= limit:
            logger.info(f"[compact] Progressive clear sufficient: ~{estimated} tokens")
            return messages

        # Aggressive truncation if still way over
        if estimated > limit * 2:
            logger.warning(f"[compact] Still {estimated} tokens, aggressive truncation")
            _cutoff = len(messages) - 6
            for i, m in enumerate(messages):
                if i == 0 and m.role == "system":
                    continue
                if i >= _cutoff:
                    break
                if isinstance(m.content, str) and len(m.content) > 200:
                    m.content = m.content[:100] + "\n...[aggressively truncated]..."
                elif isinstance(m.content, list):
                    m.content = "[content truncated for context limit]"
            estimated = self._estimate_tokens(messages, tool_defs=tool_defs,
                                              chars_per_token=chars_per_token)

        # ── Phase 2: Split + summarize ──
        if len(messages) <= 7:
            logger.info(f"[compact] Only {len(messages)} messages, cannot compact further")
            return messages

        system_msg = messages[0] if messages[0].role == "system" else None
        start_idx = 1 if system_msg else 0

        split_point = _select_recent_messages(messages, start_idx)
        if split_point <= start_idx:
            return messages

        old_messages = messages[start_idx:split_point]
        recent_messages = messages[split_point:]

        # Build summarizer input from old messages. Keep conversation turns
        # AND preserve tool calls as synopsis + truncated tool results, so
        # the summary captures concrete work (commit SHAs, file edits, test
        # results) — not just the free-text chatter around them. Previously
        # this filtered out everything with `tool_calls`, which meant the
        # summary never saw the actual actions and summaries would claim
        # "Phase 3 in progress, not committed" even when commits had landed.
        from core.llm_providers.cli_shared import textualize_message as _textualize
        old_conversation: List[LLMMessage] = []
        for m in old_messages:
            # Drop system-injected user notes (re-read hints etc.)
            if (m.role == "user" and isinstance(m.content, str)
                    and m.content.startswith("[System:")):
                continue
            if m.role == "user":
                old_conversation.append(m)
                continue
            if m.role == "assistant":
                _rendered = _textualize(m)
                if not _rendered:
                    continue
                # Clone to a plain-text assistant message so the summarizer
                # reads commits/edits as content, not as opaque tool_calls.
                old_conversation.append(LLMMessage(
                    role="assistant",
                    content=_rendered,
                    source=getattr(m, "source", None),
                    timestamp=m.timestamp,
                    seq=m.seq,
                ))
                continue
            if m.role == "tool":
                _rendered = _textualize(m)
                if not _rendered:
                    continue
                old_conversation.append(LLMMessage(
                    role="user",  # summarizer sees it as narrative context
                    content=_rendered,
                    timestamp=m.timestamp,
                    seq=m.seq,
                ))
        if not old_conversation:
            old_conversation = old_messages[-2:] if len(old_messages) > 1 else old_messages

        # Check if dropping tool plumbing alone is enough
        # But only if the message count is sane (< 200) — 4000 conversation
        # messages may fit in tokens but no LLM handles that many messages.
        if not force:
            _slim = ([system_msg] if system_msg else []) + old_conversation + recent_messages
            _slim_est = self._estimate_tokens(_slim, tool_defs=tool_defs,
                                               chars_per_token=chars_per_token)
            if _slim_est <= limit and len(_slim) < 200:
                logger.info(f"[compact] Dropping tool plumbing sufficient: "
                            f"{estimated} → {_slim_est} tokens, {len(_slim)} msgs")
                compacted = []
                if system_msg:
                    compacted.append(system_msg)
                compacted.extend(old_conversation)
                compacted.extend(recent_messages)
                self._truncate_tool_results(compacted)
                self._persist_context(compacted, conversation_id, agent_name)
                return compacted
            elif _slim_est <= limit:
                logger.info(f"[compact] Tool plumbing drop fits tokens ({_slim_est}) "
                            f"but too many messages ({len(_slim)}), summarizing...")

        # Summarize
        _summary_target = max(1000, int(max_tokens * 0.05))
        try:
            summary = self._summarize_messages(
                old_conversation, client, max_tokens,
                target_tokens=_summary_target,
                conversation_id=conversation_id,
                agent_name=agent_name,
                compact_instructions=compact_instructions,
                user_id=user_id,
            )
        except Exception as e:
            if force:
                logger.error(f"[compact] Summary FAILED: {e}", exc_info=True)
                raise RuntimeError(f"Compaction failed: {e}") from e
            logger.error(f"[compact] Summarization failed: {e}")
            summary = (
                f"[Earlier conversation ({len(old_messages)} messages) could not be "
                f"summarized due to: {e}. Context was dropped to fit within limits.]"
            )

        # Guard: empty summary
        if not summary or len(summary.strip()) < 20:
            if force:
                raise RuntimeError(
                    f"Compaction failed: summarizer returned {len(summary or '')} chars")
            summary = f"[Summary unavailable — {len(old_messages)} earlier messages dropped]"

        # ── Persist fresh summary as a new bucket ──
        # Immutable — this summary will be reused verbatim at every future
        # compact of this agent instead of regenerated from raw messages.
        if (_bucket_store is not None and summary
                and not summary.startswith("[")
                and old_messages):
            try:
                # Every message must have seq + timestamp (invariant enforced
                # by the migration + LLMMessage). No defensive fallbacks.
                _seqs = [m.seq for m in old_messages]
                _tss = [m.timestamp for m in old_messages]
                _model = getattr(client, "default_model", "") or ""
                _bucket_store.add_bucket(
                    first_seq=min(_seqs), last_seq=max(_seqs),
                    first_ts=min(_tss), last_ts=max(_tss),
                    summary=summary, model=_model,
                )
                # Rollup: if the (multiplier-scaled) header exceeds 1/3 of
                # ctx, consolidate all objects except the most recent into
                # one new higher-level SB. Scaling via the service's
                # token_multiplier makes the trigger fire at the real
                # threshold for models whose tokenizer diverges from
                # cl100k_base (Opus 4.7 in particular).
                from core.token_counter import resolve_token_multiplier
                _tmul = resolve_token_multiplier(
                    getattr(client, "_config_ref", None))
                if _bucket_store.should_rollup(max_tokens,
                                                token_multiplier=_tmul):
                    try:
                        _sb_inputs = _bucket_store.get_consolidation_input()
                        _sb_text = self._consolidate_buckets(
                            _sb_inputs, client, user_id=user_id,
                            conversation_id=conversation_id,
                            agent_name=agent_name)
                        if _sb_text and len(_sb_text.strip()) >= 20:
                            _bucket_store.rollup(_sb_text, model=_model)
                    except Exception as _sb_err:
                        logger.warning(
                            "[compact] super-bucket rollup failed "
                            "(buckets kept, will retry next compact): %s",
                            _sb_err)
                # Rebuild header with the new bucket (and SB if rolled up)
                _historical_header = _bucket_store.assemble_summary_header()
            except Exception as _bs_err:
                logger.warning("[compact] bucket persistence failed: %s",
                                _bs_err)

        # Auto-extract memories from compaction summary
        if user_id and summary and not summary.startswith("["):
            _extract_client = client
            try:
                _sum_llm, _, _ = self._get_summarizer_client(user_id)
                if _sum_llm:
                    _extract_client = _sum_llm
            except Exception:
                pass
            self._auto_extract_memories(
                summary, _extract_client, user_id,
                agent_name=agent_name,
                conversation_id=conversation_id,
            )

        # ── Phase 3: Rebuild ──
        compacted: List[LLMMessage] = []
        if system_msg:
            compacted.append(system_msg)
        # If a historical header exists (bucket store), it already contains
        # the new bucket we just added. Use it as-is. Otherwise fall back to
        # the standalone fresh summary (cold start, bucket store disabled).
        _postamble = (
            "The recent messages below are the current state.\n"
            "Do NOT restart or re-propose completed work.\n\n"
            "If you need more detail than the summary above (e.g. exact "
            "commit SHAs, full test output, file contents you edited, tool "
            "arguments, error tracebacks), call `read_history` with a "
            "keyword or message range — the full transcript is preserved "
            "there. Do NOT assume missing info means the work was not done; "
            "check the transcript first."
        )
        if _historical_header:
            _body = f"{_historical_header}\n{_postamble}"
        else:
            _body = (
                f"[Conversation summary — earlier messages compacted]\n\n"
                f"{summary}\n\n{_postamble}"
            )
        # Summary + ack are synthetic messages created at compact time,
        # but they conceptually belong BEFORE the recent messages (they
        # cover the history that's been rolled up). The store sorts by
        # (ts, seq) on read, so force BOTH their ts AND their seq to be
        # strictly below the first recent message — ts handles the usual
        # case, seq guards against ts ties from float imprecision.
        from core.llm_client import _next_msg_seq
        if recent_messages:
            _first_recent_ts = min(m.timestamp for m in recent_messages)
            _first_recent_seq = min(m.seq for m in recent_messages)
        else:
            import time as _t_compact
            _first_recent_ts = _t_compact.time()
            _first_recent_seq = _next_msg_seq() + 2
        compacted.append(LLMMessage(
            role="user", content=_body,
            timestamp=_first_recent_ts - 0.002,
            seq=_first_recent_seq - 2,
        ))
        compacted.append(LLMMessage(
            role="assistant",
            content="Understood. I have the summary and will continue from the recent messages.",
            source={"type": "context"},
            timestamp=_first_recent_ts - 0.001,
            seq=_first_recent_seq - 1,
        ))
        compacted.extend(recent_messages)

        # File re-read: extract last read per file from OLD messages
        # with exact offset/limit so re-read matches what the LLM saw.
        _file_reads = {}
        for m in old_messages:
            if m.role == "assistant" and m.tool_calls:
                for tc in m.tool_calls:
                    _args = tc.arguments if isinstance(tc.arguments, dict) else {}
                    _path = _args.get("path", "")
                    _tool = getattr(tc, "name", "") or ""
                    if _tool == "read" and _path:
                        _read_info = {"path": _path}
                        _off = _args.get("offset")
                        _lim = _args.get("limit")
                        _svc = _args.get("source", "")
                        if _off:
                            _read_info["offset"] = int(_off)
                        if _lim:
                            _read_info["limit"] = int(_lim)
                        if _svc:
                            _read_info["service"] = _svc
                        _file_reads[_path] = _read_info
                    elif _tool in ("edit", "write") and _path:
                        _file_reads[_path] = {"path": _path}
        for m in recent_messages:
            if m.role == "assistant" and m.tool_calls:
                for tc in m.tool_calls:
                    _args = tc.arguments if isinstance(tc.arguments, dict) else {}
                    _path = _args.get("path", "")
                    if _path:
                        _file_reads.pop(_path, None)
        _file_list = list(_file_reads.values())[-5:]
        if _file_list:
            _files_note = "Files you were working with (lost after compaction). Re-read them now to restore context:\n"
            for _fr in _file_list:
                _desc = f"  - read(path=\"{_fr['path']}\""
                if _fr.get("offset") or _fr.get("limit"):
                    _params = []
                    if _fr.get("offset"):
                        _params.append(f"offset={_fr['offset']}")
                    if _fr.get("limit"):
                        _params.append(f"limit={_fr['limit']}")
                    _desc += f" ({', '.join(_params)})"
                if _fr.get("service"):
                    _desc += f" [service: {_fr['service']}]"
                _files_note += _desc + "\n"
            _files_note += "Call read with the exact same parameters to restore your working context."
            compacted.append(LLMMessage(
                role="user",
                content=f"[System: {_files_note}]"
            ))
            compacted.append(LLMMessage(
                role="assistant",
                content="I'll re-read these files now to restore my working context."
            ))

        # Truncate large tool results in recent zone
        self._truncate_tool_results(compacted)

        # Fallback: if still over max after summary, retry with aggressive split
        new_estimate = self._estimate_tokens(compacted, tool_defs=tool_defs,
                                              chars_per_token=chars_per_token)
        if new_estimate > max_tokens and len(recent_messages) > 10:
            logger.info(f"[compact] Still over max ({new_estimate}), aggressive split (6 conv, max 20)")
            _split2 = _select_recent_messages(messages, start_idx,
                                               min_conversation=6, max_total=20)
            if _split2 > start_idx:
                recent_messages = messages[_split2:]
                compacted = []
                if system_msg:
                    compacted.append(system_msg)
                if recent_messages:
                    _frt = min(m.timestamp for m in recent_messages)
                    _frs = min(m.seq for m in recent_messages)
                else:
                    import time as _t_aggr
                    _frt = _t_aggr.time()
                    _frs = _next_msg_seq() + 2
                compacted.append(LLMMessage(
                    role="user",
                    content=(f"[Conversation summary — earlier messages compacted]\n\n{summary}\n\n"
                             f"Use read_history tool to access older messages if needed."),
                    timestamp=_frt - 0.002,
                    seq=_frs - 2,
                ))
                compacted.append(LLMMessage(
                    role="assistant", content="Understood.",
                    source={"type": "context"},
                    timestamp=_frt - 0.001,
                    seq=_frs - 1,
                ))
                compacted.extend(recent_messages)
                self._truncate_tool_results(compacted)
                new_estimate = self._estimate_tokens(compacted, tool_defs=tool_defs,
                                                      chars_per_token=chars_per_token)
                logger.info(f"[compact] Aggressive: {len(compacted)} msgs (~{new_estimate} tokens)")

        logger.info(f"[compact] Final: {new_estimate} tokens (was {_original_tokens}), "
                    f"{len(compacted)} messages (was {_original_count})")

        # ── Phase 4: Persist + cleanup + notify ──
        self._persist_context(compacted, conversation_id, agent_name)

        # Clean up tool_result spillover files no longer in context
        if conversation_id:
            self._cleanup_orphan_files(compacted, conversation_id)

        if conversation_id:
            try:
                from core.conversation_event_bus import ConversationEventBus
                ConversationEventBus.instance().publish_event(
                    conversation_id, "compact_progress", {
                        "stage": "done",
                        "agent": agent_name,
                        "before": len(messages),
                        "after": len(compacted),
                        "tokens_before": estimated,
                        "tokens_after": new_estimate,
                    })
            except Exception:
                pass

        return compacted

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
