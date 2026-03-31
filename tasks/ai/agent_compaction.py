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
from core.tool_registry import ToolRegistry, create_default_registry, load_agent_tools
from tasks.ai.agent_summarize import AgentSummarizeMixin
from tasks.ai.agent_cc_context import AgentCCContextMixin

logger = logging.getLogger(__name__)



def _select_recent_messages(
    messages: List[LLMMessage],
    start_idx: int = 1,
    min_conversation: int = 25,
    max_total: int = 100,
) -> int:
    """Find split point: keep recent messages with guaranteed conversation ratio.

    Algorithm:
    1. Walk backward, collect the last min_conversation user/assistant messages
    2. Include ALL messages between them (tool, system, etc.) — the recent window
    3. If total > max_total, drop oldest tool/system messages until <= max_total

    Returns the split index (messages[split:] = recent to keep).
    Does NOT modify the messages list.
    """
    n = len(messages)
    if n <= start_idx + min_conversation:
        return start_idx  # not enough messages to compact

    # Step 1: walk backward to find min_conversation user/assistant messages;
    # include every message in between.
    conv_count = 0
    scan = n
    while scan > start_idx and conv_count < min_conversation:
        scan -= 1
        if messages[scan].role in ("user", "assistant"):
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


class AgentCompactionMixin(AgentSummarizeMixin, AgentCCContextMixin):
    """Methods extracted from AgentLoopTask."""

    # Max chars kept per tool result after compaction truncation
    _TOOL_TRUNC_LIMIT = 800

    @staticmethod
    @staticmethod
    def _microcompact_time_based(messages: List[LLMMessage],
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

    def _progressive_clear_tool_results(messages: List[LLMMessage],
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
            keep.append(LLMMessage(role="assistant", content="Understood, continuing."))
        keep.extend(result[-keep_n:])
        return keep


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
        self._deflate_image_messages(messages, keep_last=True)

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

        # Clear oversized tool results to FileStore refs
        self._clear_seen_tool_results(messages, keep_recent=0,
                                       conversation_id=conversation_id,
                                       agent_name=agent_name)

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

        # Filter old messages for summarizer: only conversation (no tool plumbing)
        old_conversation = [
            m for m in old_messages
            if m.role in ("user", "assistant") and not getattr(m, "tool_calls", None)
            and not (m.role == "user" and isinstance(m.content, str)
                     and m.content.startswith("[System:"))
        ]
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

        # ── Phase 3: Rebuild ──
        compacted: List[LLMMessage] = []
        if system_msg:
            compacted.append(system_msg)
        compacted.append(LLMMessage(
            role="user",
            content=(
                f"[Conversation summary — earlier messages compacted]\n\n{summary}\n\n"
                f"The recent messages below are the current state. "
                f"Do NOT restart or re-propose completed work. "
                f"Use read_history tool to access older messages if needed."
            ),
        ))
        compacted.append(LLMMessage(
            role="assistant",
            content="Understood. I have the summary and will continue from the recent messages.",
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
                compacted.append(LLMMessage(role="user", content=(
                    f"[Conversation summary — earlier messages compacted]\n\n{summary}\n\n"
                    f"Use read_history tool to access older messages if needed.")))
                compacted.append(LLMMessage(role="assistant", content="Understood."))
                compacted.extend(recent_messages)
                self._truncate_tool_results(compacted)
                new_estimate = self._estimate_tokens(compacted, tool_defs=tool_defs,
                                                      chars_per_token=chars_per_token)
                logger.info(f"[compact] Aggressive: {len(compacted)} msgs (~{new_estimate} tokens)")

        logger.info(f"[compact] Final: {new_estimate} tokens (was {_original_tokens}), "
                    f"{len(compacted)} messages (was {_original_count})")

        # ── Phase 4: Persist + notify ──
        self._persist_context(compacted, conversation_id, agent_name)

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
