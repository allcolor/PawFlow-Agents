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

logger = logging.getLogger(__name__)



class AgentCompactionMixin:
    """Methods extracted from AgentLoopTask."""

    @staticmethod
    def _progressive_clear_tool_results(messages: List[LLMMessage],
                                          target_tokens: int,
                                          current_tokens: int,
                                          keep_recent: int = 6,
                                          chars_per_token: float = 3.5):
        """Progressively shrink old tool results until under target_tokens.

        Strategy (oldest first, in passes):
        - Pass 1: truncate tool results > 500 chars → 200 chars
        - Pass 2: truncate tool results > 100 chars → 50 chars summary
        - Pass 3: replace all old tool results with "[result cleared]"

        Never touches the last `keep_recent` messages.
        Returns the estimated new token count.
        """
        if current_tokens <= target_tokens:
            return current_tokens

        safe_end = max(1, len(messages) - keep_recent)

        # Pass 1: truncate long tool results to 200 chars
        for i in range(1, safe_end):
            if current_tokens <= target_tokens:
                break
            m = messages[i]
            if m.role != "tool" or not isinstance(m.content, str):
                continue
            if len(m.content) > 500:
                _saved = len(m.content) - 200
                m.content = m.content[:200] + "\n[...truncated]"
                current_tokens -= int(_saved / chars_per_token)

        if current_tokens <= target_tokens:
            return current_tokens

        # Pass 2: shrink to 50 chars
        for i in range(1, safe_end):
            if current_tokens <= target_tokens:
                break
            m = messages[i]
            if m.role != "tool" or not isinstance(m.content, str):
                continue
            if len(m.content) > 100:
                _saved = len(m.content) - 50
                m.content = m.content[:50] + "\n[...cleared]"
                current_tokens -= int(_saved / chars_per_token)

        if current_tokens <= target_tokens:
            return current_tokens

        # Pass 3: clear all old tool results
        for i in range(1, safe_end):
            if current_tokens <= target_tokens:
                break
            m = messages[i]
            if m.role != "tool" or not isinstance(m.content, str):
                continue
            if len(m.content) > 20:
                _saved = len(m.content) - 15
                m.content = "[result cleared]"
                current_tokens -= int(_saved / chars_per_token)

        return current_tokens

    @staticmethod
    def _compact_tool_chains(messages: List[LLMMessage], keep_recent: int = 4):
        """Compact old tool call chains into summaries.

        Replaces sequences of [assistant(tool_calls), tool, tool, ..., tool]
        with a single assistant message summarizing what was done.
        Only compacts chains that are NOT in the last `keep_recent` messages.

        This is called BEFORE _compact_if_needed to reduce token count
        without needing an LLM summarization call.
        """
        if len(messages) <= keep_recent + 2:
            return messages  # too few to compact

        # Find the boundary: don't touch system prompt (idx 0)
        # and last keep_recent messages
        safe_end = len(messages) - keep_recent

        # Scan for tool chains in the compactable region
        result = [messages[0]]  # system prompt
        i = 1
        while i < len(messages):
            m = messages[i]
            # If we're past the safe zone, keep as-is
            if i >= safe_end:
                result.append(m)
                i += 1
                continue

            # Detect tool chain: assistant with tool_calls followed by tool results
            if m.role == "assistant" and m.tool_calls:
                chain_tools = list(m.tool_calls)
                chain_results = []
                # Collect ALL subsequent tool result messages (even past safe_end)
                # The chain is atomic — can't separate assistant from its results
                _tc_ids = {tc.id for tc in chain_tools}
                j = i + 1
                while j < len(messages) and messages[j].role == "tool":
                    if getattr(messages[j], 'tool_call_id', '') in _tc_ids:
                        chain_results.append(messages[j])
                    j += 1

                if chain_tools and chain_results:
                    # Build compact summary
                    tool_counts = {}
                    for tc in chain_tools:
                        tool_counts[tc.name] = tool_counts.get(tc.name, 0) + 1
                    parts = []
                    for name, count in tool_counts.items():
                        if count > 1:
                            parts.append(f"{name} x{count}")
                        else:
                            # Include first arg hint
                            tc0 = next(tc for tc in chain_tools if tc.name == name)
                            _hint = ""
                            for k in ("path", "prompt", "command", "query"):
                                v = tc0.arguments.get(k, "")
                                if v:
                                    _hint = f"({str(v)[:40]})"
                                    break
                            parts.append(f"{name}{_hint}")

                    # Collect short result summaries
                    result_hints = []
                    for tr in chain_results[:3]:
                        _rc = tr.content if isinstance(tr.content, str) else str(tr.content)
                        # Strip TOOL OUTPUT wrapper
                        if "[TOOL OUTPUT" in _rc:
                            _rc = _rc.split("\n", 1)[-1] if "\n" in _rc else _rc
                        if "[/TOOL OUTPUT]" in _rc:
                            _rc = _rc.replace("[/TOOL OUTPUT]", "").strip()
                        result_hints.append(_rc[:60])
                    if len(chain_results) > 3:
                        result_hints.append(f"... +{len(chain_results) - 3} more")

                    summary = f"[Used {len(chain_tools)} tool(s): {', '.join(parts)}]"
                    if result_hints:
                        summary += "\n[Results: " + " | ".join(result_hints) + "]"

                    # Preserve the LLM's own text/reasoning if present
                    _llm_text = m.content if isinstance(m.content, str) else ""
                    if _llm_text:
                        summary = _llm_text + "\n" + summary

                    # Replace the chain with a single assistant message
                    result.append(LLMMessage(
                        role="assistant", content=summary,
                        source=getattr(m, 'source', None),
                    ))
                    i = j  # skip past the tool results
                    continue

                # assistant with tool_calls but no results yet → keep as-is
                # (shouldn't happen in compactable region but be safe)

            result.append(m)
            i += 1

        # Safety: remove orphan tool results (no matching tool_use)
        _valid_tc_ids = set()
        for m in result:
            if m.role == "assistant" and m.tool_calls:
                for tc in m.tool_calls:
                    _valid_tc_ids.add(tc.id)
        result = [
            m for m in result
            if m.role != "tool" or getattr(m, 'tool_call_id', '') in _valid_tc_ids
        ]

        return result

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
        synth_context = self._compact_if_needed(
            list(messages), _cc,
            ctx.get("max_context_size", 64000),
            compact_threshold,
            ctx.get("context_keep_recent", 6),
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
                    synth_context = self._compact_if_needed(
                        synth_context, _cc,
                        ctx.get("max_context_size", 64000),
                        0.4, ctx.get("context_keep_recent", 4),
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
                td_tokens += len(getattr(td, 'name', '') or '') // cpt
                td_tokens += len(getattr(td, 'description', '') or '') // cpt
                params = getattr(td, 'parameters', None)
                if params:
                    td_tokens += len(json.dumps(params) if isinstance(params, dict) else str(params)) // cpt

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
            print(f"[COMPACT-GUARD] force-fit step 1 OK: {est} tokens", flush=True)
            return result

        # Step 2: Drop middle messages, keep system + last N
        print(f"[COMPACT-GUARD] step 1 insufficient ({est} > {max_tokens}), dropping middle", flush=True)
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


    def _compact_if_needed(
        self,
        messages: List[LLMMessage],
        client: LLMClient,
        max_tokens: int,
        threshold: float,
        keep_recent: int,
        conversation_id: str = "",
        agent_name: str = "",
        tool_defs: list = None,
        chars_per_token: float = 0,
    ) -> List[LLMMessage]:
        """Compact conversation history if approaching the token limit.

        Strategy:
        1. First pass: truncate long tool_results (>500 chars → 200 + "...truncated")
        2. If still over threshold: summarize old messages via LLM call

        Always preserves:
        - System prompt (first message)
        - Last `keep_recent` messages (never compacted)

        If *conversation_id* is given, the resulting summary is persisted
        to the ConversationStore so it can be reused after a restart.
        """
        # Ensure no display-only messages leak into compaction
        messages = [m for m in messages if getattr(m, 'role', '') != 'sub_agent_trace']

        # Compact old tool chains first (cheap, no LLM call)
        _pre_compact = len(messages)
        messages = self._compact_tool_chains(messages, keep_recent=keep_recent)
        if len(messages) < _pre_compact:
            logger.info("[compact] Tool chains: %d → %d messages",
                        _pre_compact, len(messages))

        # Deflate old images before estimating — but keep the last one
        # (the LLM hasn't seen it yet in this iteration)
        self._deflate_image_messages(messages, keep_last=True)
        # Strip base64 blobs from ALL messages — images, tool results, user attachments
        import re as _re_b64
        for m in messages:
            if not isinstance(m.content, str) or len(m.content) < 5000:
                continue
            if not self._detect_base64_blob(m.content):
                continue
            # Strip data URIs (data:image/png;base64,...)
            m.content = _re_b64.sub(
                r'data:[^;]*;base64,[A-Za-z0-9+/=]+',
                '[base64 image removed — use show_file to view]',
                m.content,
            )
            # Strip raw base64 blobs (>1000 chars of base64 alphabet)
            m.content = _re_b64.sub(
                r'[A-Za-z0-9+/=]{1000,}',
                '[binary data removed]',
                m.content,
            )

        estimated = self._estimate_tokens(messages, tool_defs=tool_defs,
                                          chars_per_token=chars_per_token)
        limit = int(max_tokens * threshold)

        print(f"[COMPACT] check: {estimated} est. tokens, limit={limit} "
              f"(max={max_tokens}×{threshold}), {len(messages)} msgs, "
              f"cpt={chars_per_token:.2f}", flush=True)

        if estimated <= limit:
            return messages

        print(f"[COMPACT] TRIGGERED: {estimated} > {limit}, compacting...", flush=True)

        # Pass 1: Progressive clearing of old tool results (oldest first)
        estimated = self._progressive_clear_tool_results(
            messages, limit, estimated,
            keep_recent=keep_recent,
            chars_per_token=cpt,
        )
        if estimated <= limit:
            logger.info(f"[compact] Pass 1 (progressive clear) sufficient: ~{estimated} tokens")
            return messages

        # Pass 1b: If still way over, truncate ALL non-recent messages aggressively
        if estimated > limit * 2:
            logger.warning(f"[compact] Still {estimated} tokens after truncation, "
                           f"aggressive truncation of old messages")
            _keep_n = max(keep_recent, 6)
            _cutoff = len(messages) - _keep_n
            for i, m in enumerate(messages):
                if i == 0 and m.role == "system":
                    continue  # preserve system prompt
                if i >= _cutoff:
                    break  # preserve recent
                if isinstance(m.content, str) and len(m.content) > 200:
                    m.content = m.content[:100] + "\n...[aggressively truncated]..."
                elif isinstance(m.content, list):
                    m.content = "[content truncated for context limit]"
            estimated = self._estimate_tokens(messages, tool_defs=tool_defs,
                                              chars_per_token=chars_per_token)

        # Pass 2: LLM-based summarization of old messages
        if len(messages) <= keep_recent + 1:
            # Not enough messages to compact
            logger.info(f"[compact] Only {len(messages)} messages, cannot compact further")
            return messages

        # Split: system prompt | old messages | recent messages
        # Guarantee: keep at least 3 complete assistant responses AND all
        # trailing tool-call chains (assistant+tool_results) intact.
        system_msg = messages[0] if messages[0].role == "system" else None
        start_idx = 1 if system_msg else 0

        # Walk backwards to find the split point:
        # 1. Count "conversation messages" = user messages + assistant TEXT responses
        #    (assistant with tool_calls and tool results are plumbing, don't count)
        # 2. Keep at least `keep_recent` conversation messages (default 6)
        # 3. All tool-call plumbing between kept messages is kept too
        _msg_count = 0
        _split = len(messages)

        while _split > start_idx and _msg_count < keep_recent:
            _split -= 1
            m = messages[_split]
            # Count user messages and assistant text responses (not tool_calls)
            if m.role == "user" and not (
                    isinstance(m.content, str) and m.content.startswith("[System:")):
                _msg_count += 1
            elif m.role == "assistant" and not getattr(m, "tool_calls", None):
                _msg_count += 1

        # Never split inside a tool-call chain: if messages[_split] is a tool
        # result, walk back to include the preceding assistant + all its tool results
        while _split > start_idx and messages[_split].role == "tool":
            _split -= 1
        # If we landed on an assistant with tool_calls, include it
        if (_split > start_idx and messages[_split].role == "assistant"
                and getattr(messages[_split], "tool_calls", None)):
            pass  # include this assistant message in recent
        # Ensure we don't include the system prompt in old_messages
        if _split <= start_idx:
            return messages

        split_point = _split
        old_messages = messages[start_idx:split_point]
        recent_messages = messages[split_point:]

        # Filter old_messages for summarizer: only conversation messages
        # (user + assistant text). Tool calls/results are noise — they cost
        # tokens and the summarizer can't do anything useful with JSON args
        # or raw tool output. The relevant tool state is in recent_messages.
        old_conversation = [
            m for m in old_messages
            if m.role in ("user", "assistant") and not getattr(m, "tool_calls", None)
            and not (m.role == "user" and isinstance(m.content, str)
                     and m.content.startswith("[System:"))
        ]
        if not old_conversation:
            # Only tool plumbing in old zone — nothing to summarize
            old_conversation = old_messages[-2:] if len(old_messages) > 1 else old_messages

        # Check if dropping tool plumbing alone is enough — skip summarizer if so
        _slim_messages = ([system_msg] if system_msg else []) + old_conversation + recent_messages
        _slim_est = self._estimate_tokens(_slim_messages, tool_defs=tool_defs,
                                           chars_per_token=chars_per_token)
        if _slim_est <= limit:
            logger.info(f"[compact] Dropping tool plumbing sufficient: "
                        f"{estimated} → {_slim_est} tokens (limit={limit}), no summary needed")
            # Rebuild: system + old conversation messages + recent
            compacted = []
            if system_msg:
                compacted.append(system_msg)
            compacted.extend(old_conversation)
            compacted.extend(recent_messages)
            # Truncate large tool results in recent zone
            _tool_trunc_limit = 800
            for m in compacted:
                if m.role == "tool" and isinstance(m.content, str) and len(m.content) > _tool_trunc_limit:
                    m.content = m.content[:_tool_trunc_limit] + "\n...[compacted — re-call tool if needed]..."
            # Persist stripped context so we don't re-compact next iteration
            if conversation_id:
                try:
                    from core.conversation_store import ConversationStore
                    serialized = self._serialize_messages(compacted)
                    ConversationStore.instance().save_agent_context(
                        conversation_id, agent_name, serialized,
                    )
                except Exception as e:
                    logger.warning(f"[compact] Failed to persist stripped context: {e}")
            return compacted

        # Summarize old conversation — target = 1/4 of context max
        _summary_target = max(500, int(max_tokens / 4))
        try:
            summary = self._summarize_messages(old_conversation, client, max_tokens,
                                               target_tokens=_summary_target,
                                               conversation_id=conversation_id)
        except Exception as e:
            logger.error(f"[compact] Summarization failed: {e}")
            # NEVER return the original messages if they exceed the limit.
            # Build a minimal context with just system + placeholder + recent.
            logger.warning("[compact] Falling back to drop-old strategy (no summary)")
            summary = (
                f"[Earlier conversation ({len(old_messages)} messages) could not be "
                f"summarized due to: {e}. Context was dropped to fit within limits.]"
            )

        # Rebuild messages: system + summary + recent
        compacted: List[LLMMessage] = []
        if system_msg:
            compacted.append(system_msg)
        compacted.append(LLMMessage(
            role="user",
            content=(
                f"[Conversation summary — earlier messages compacted]\n\n{summary}\n\n"
                f"IMPORTANT: The above is a summary of our earlier conversation. "
                f"The recent messages below contain the CURRENT state of our work. "
                f"Do NOT restart or re-propose work that is already done. "
                f"Continue from where you left off based on the recent messages. "
                f"Some tool outputs may have been compacted — if you need data "
                f"from a compacted tool output, re-call the tool to get fresh results."
            ),
        ))
        compacted.append(LLMMessage(
            role="assistant",
            content=(
                "Understood. I've read the summary of our earlier conversation. "
                "I'll continue from the current state shown in the recent messages below, "
                "without restarting or re-proposing completed work. "
                "If I need data from a compacted tool output, I'll re-call the tool."
            ),
        ))
        compacted.extend(recent_messages)

        # Truncate large tool results in the recent zone — they can blow up
        # the context budget on their own (e.g. scrape_url, read_file).
        _tool_trunc_limit = 800  # chars kept per tool result
        for m in compacted:
            if m.role == "tool" and isinstance(m.content, str) and len(m.content) > _tool_trunc_limit:
                m.content = m.content[:_tool_trunc_limit] + "\n...[compacted — re-call tool if needed]..."
            elif m.role == "tool" and isinstance(m.content, list):
                text_parts = [p for p in m.content if p.get("type") == "text"]
                text = " ".join(p.get("text", "") for p in text_parts)
                m.content = text[:_tool_trunc_limit] + "\n...[compacted — re-call tool if needed]..." if len(text) > _tool_trunc_limit else text

        new_estimate = self._estimate_tokens(compacted, tool_defs=tool_defs,
                                              chars_per_token=chars_per_token)
        logger.info(f"[compact] Final: {new_estimate} tokens (was {estimated}), "
                    f"{len(compacted)} messages (was {len(messages)})")

        # Notify UI that compaction is done
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

        # Persist the compacted context so it survives restarts
        if conversation_id:
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

        return compacted


    def _summarize_messages(
        self,
        old_messages: List[LLMMessage],
        client: LLMClient,
        max_tokens: int,
        target_tokens: int = 0,
        conversation_id: str = "",
    ) -> str:
        """Summarize messages iteratively until they fit.

        Strategy:
        1. Convert messages to text
        2. If text fits in LLM context (< 60% of max_tokens) → single summarize call
        3. If too big → split into N chunks (each < 60% of max_tokens),
           summarize each independently
        4. Concatenate summaries. If still too big, repeat from step 2
        5. Final pass: summarize combined result to ~25% of max_tokens
        """
        if not target_tokens:
            target_tokens = max(500, int(max_tokens / 4))

        # 60% of context = safe input limit (leaves room for system prompt + output)
        safe_limit = int(max_tokens * 0.60)

        def _pub(stage, detail=""):
            if conversation_id:
                try:
                    from core.conversation_event_bus import ConversationEventBus
                    ConversationEventBus.instance().publish_event(
                        conversation_id, "compact_progress",
                        {"stage": stage, "detail": detail},
                    )
                except Exception:
                    pass

        def _est(text: str) -> int:
            return self._estimate_tokens([LLMMessage(role="user", content=text)])

        # Convert messages to text chunks (one per message for granular splitting)
        text_chunks = []
        for m in old_messages:
            text_chunks.append(self._sanitize_for_llm(self._messages_to_text([m])))

        _pass = 0
        _max_passes = 5  # safety valve

        while _pass < _max_passes:
            _pass += 1
            total_text = "\n".join(text_chunks)
            total_tokens = _est(total_text)

            logger.info(f"[compact] Pass {_pass}: {total_tokens} tokens in "
                        f"{len(text_chunks)} chunks (safe_limit={safe_limit})")

            # If everything fits → single summary call
            if total_tokens <= safe_limit:
                _pub("summarizing", f"pass {_pass}: single call ({total_tokens} tokens)")
                return self._call_summarize(client, total_text, target_tokens)

            # Split chunks into groups that each fit in safe_limit
            groups: List[str] = []
            current_group: List[str] = []
            current_tokens = 0
            # Leave 20% margin within each group for overhead
            group_limit = int(safe_limit * 0.80)

            for chunk in text_chunks:
                chunk_tokens = _est(chunk)
                # If a single chunk exceeds the limit, hard-truncate it
                if chunk_tokens > group_limit:
                    cpt = max(1.0, len(chunk) / max(1, chunk_tokens))
                    max_chars = int(group_limit * cpt)
                    chunk = chunk[:max_chars] + "\n...[truncated]..."
                    chunk_tokens = _est(chunk)
                if current_tokens + chunk_tokens > group_limit and current_group:
                    groups.append("\n".join(current_group))
                    current_group = []
                    current_tokens = 0
                current_group.append(chunk)
                current_tokens += chunk_tokens

            if current_group:
                groups.append("\n".join(current_group))

            n_groups = len(groups)
            logger.info(f"[compact] Pass {_pass}: split into {n_groups} groups")
            _pub("chunking", f"pass {_pass}: {n_groups} groups")

            # Summarize each group independently
            chunk_target = max(200, target_tokens // max(1, n_groups))
            summaries = []
            for i, group_text in enumerate(groups):
                _pub("summarizing", f"pass {_pass}: group {i+1}/{n_groups}")
                try:
                    s = self._call_summarize(client, group_text, chunk_target)
                    summaries.append(s)
                except Exception as e:
                    logger.error(f"[compact] Group {i+1} summarization failed: {e}")
                    # Hard fallback: just truncate
                    cpt = max(1.0, len(group_text) / max(1, _est(group_text)))
                    summaries.append(group_text[:int(chunk_target * cpt)] + "\n...[truncated]...")

            # Replace text_chunks with the summaries for next iteration
            text_chunks = summaries

        # Exhausted max passes — concatenate what we have
        final = "\n\n".join(text_chunks)
        logger.warning(f"[compact] Exhausted {_max_passes} passes, "
                       f"final size: {_est(final)} tokens")
        return final


    def _call_summarize(self, client: LLMClient, text: str,
                        target_tokens: int = 0) -> str:
        """Single LLM call to summarize text."""
        if not target_tokens:
            target_tokens = 2000
        clean_text = self._sanitize_for_llm(text)
        target_instruction = (
            f"Target length: approximately {target_tokens} tokens. "
            f"Use the full budget — do not produce a shorter summary than needed."
        )
        try:
            response = client.complete(
                messages=[
                    LLMMessage(role="system", content=(
                        "You are a conversation summarizer for an AI agent work session. "
                        "Summarize the following exchange. You MUST preserve:\n"
                        "1. CURRENT STATE: What project/task is being worked on, what version/stage\n"
                        "2. FILES & ARTIFACTS: All files created, modified, or referenced (with paths)\n"
                        "3. DECISIONS: Key decisions made, architecture choices, user preferences\n"
                        "4. LAST ACTION: What the agent was doing right before this point\n"
                        "5. PENDING WORK: What still needs to be done (user requests not yet fulfilled)\n"
                        "6. KEY FACTS: URLs, credentials, config values, variable names, tool names\n\n"
                        "Tool call details (arguments, raw outputs) can be summarized briefly — "
                        "the agent can re-call tools if it needs fresh data. "
                        "But NEVER lose the project state, file paths, or what was being worked on.\n\n"
                        + target_instruction
                    )),
                    LLMMessage(role="user", content=clean_text),
                ],
                temperature=0.3,
                max_tokens=0,  # no output limit — target is in the prompt
            )
        except Exception as e:
            err_str = str(e)
            # Log debug info to help diagnose malformed content
            if "parse" in err_str.lower() or "500" in err_str:
                # Find the approximate problematic position
                import re as _re
                pos_match = _re.search(r'pos (\d+)', err_str)
                pos = int(pos_match.group(1)) if pos_match else -1
                context_start = max(0, pos - 100)
                context_end = min(len(clean_text), pos + 100)
                snippet = clean_text[context_start:context_end]
                # Show char codes around the problem area
                if pos >= 0 and pos < len(clean_text):
                    char_codes = [f"0x{ord(c):04x}" for c in clean_text[max(0,pos-5):pos+5]]
                else:
                    char_codes = []
                logger.error(
                    f"[compact] Summarization parse error at pos {pos}, "
                    f"text_len={len(clean_text)}, "
                    f"nearby_chars={char_codes}, "
                    f"snippet=...{repr(snippet)}..."
                )
                # Fallback: aggressively strip non-ASCII and retry
                ascii_text = clean_text.encode("ascii", errors="replace").decode("ascii")
                try:
                    response = client.complete(
                        messages=[
                            LLMMessage(role="system", content=(
                                "You are a conversation summarizer. Summarize concisely, "
                                "preserving key facts, decisions, and findings."
                            )),
                            LLMMessage(role="user", content=ascii_text),
                        ],
                        model=model or None,
                        temperature=0.3,
                        max_tokens=2000,
                    )
                    logger.info("[compact] ASCII fallback succeeded")
                except Exception as e2:
                    logger.error(f"[compact] ASCII fallback also failed: {e2}")
                    raise
            else:
                raise
        summary = response.content
        logger.info(f"[compact] Summarized {len(text)} chars into {len(summary)} chars "
                    f"({self._estimate_tokens([LLMMessage(role='user', content=summary)])} tokens)")
        return summary


    def _call_summarize_with_budget(self, client: LLMClient,
                                     text: str, max_tokens: int) -> str:
        """Re-summarize text to fit within an approximate token budget."""
        clean = self._sanitize_for_llm(text)
        response = client.complete(
            messages=[
                LLMMessage(role="system", content=(
                    f"Summarize the following text in approximately {max_tokens} tokens. "
                    "Preserve all key facts, decisions, findings, and context. "
                    "Be concise but complete. Do NOT exceed the token budget."
                )),
                LLMMessage(role="user", content=clean),
            ],
            temperature=0.3,
            max_tokens=min(max_tokens * 2, 4096),
        )
        return response.content

