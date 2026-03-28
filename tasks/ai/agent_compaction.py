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



def _select_recent_messages(
    messages: List[LLMMessage],
    start_idx: int = 1,
    min_conversation: int = 25,
    max_total: int = 100,
) -> int:
    """Find split point: keep recent messages with guaranteed conversation ratio.

    Algorithm:
    1. Walk backwards to find the last `min_conversation` user/assistant messages
    2. Include all tool/other messages between them
    3. Cap at max_total messages
    4. If at max_total and < min_conversation user/assistant, continue backwards:
       skip non-user/assistant, keep user/assistant, until we have min_conversation

    Returns the split index (messages[split:] = recent to keep).
    """
    n = len(messages)
    if n <= start_idx + min_conversation:
        return start_idx  # not enough messages to compact

    # Step 1: walk backwards to find min_conversation user/assistant messages
    # Include all messages between them (tool, system, etc.)
    conv_count = 0
    scan = n
    while scan > start_idx and conv_count < min_conversation:
        scan -= 1
        if messages[scan].role in ("user", "assistant"):
            conv_count += 1

    split = scan
    total = n - split

    # Step 2: if total > max_total, we have too many messages
    # Drop oldest non-user/assistant messages to fit, then if still short
    # on conversation messages, continue backwards selectively
    if total > max_total:
        selected = list(messages[split:])
        # Count conversation messages in selected
        _conv_in_selected = sum(1 for m in selected if m.role in ("user", "assistant"))

        if _conv_in_selected >= min_conversation:
            # We have enough conversation, just drop oldest tools to fit max_total
            while len(selected) > max_total:
                dropped = False
                for i, m in enumerate(selected):
                    if m.role not in ("user", "assistant"):
                        selected.pop(i)
                        dropped = True
                        break
                if not dropped:
                    break
        else:
            # Not enough conversation in max_total window — go further back selectively
            # Keep what we have, drop tools from front to make room
            while len(selected) > max_total:
                dropped = False
                for i, m in enumerate(selected):
                    if m.role not in ("user", "assistant"):
                        selected.pop(i)
                        dropped = True
                        break
                if not dropped:
                    break
            # Now scan further back, picking only user/assistant
            _extra_scan = split - 1
            while _extra_scan >= start_idx and _conv_in_selected < min_conversation:
                m = messages[_extra_scan]
                if m.role in ("user", "assistant"):
                    # Drop an old non-conversation message to make room
                    room_made = False
                    for i, sm in enumerate(selected):
                        if sm.role not in ("user", "assistant"):
                            selected.pop(i)
                            room_made = True
                            break
                    if not room_made and len(selected) >= max_total:
                        break  # can't make room, all are conversation
                    selected.insert(0, m)
                    _conv_in_selected += 1
                _extra_scan -= 1

        messages[split:] = selected
        return len(messages) - len(selected)

    return split


class AgentCompactionMixin:
    """Methods extracted from AgentLoopTask."""

    # Max chars kept per tool result after compaction truncation
    _TOOL_TRUNC_LIMIT = 800

    def _prepare_cc_file_context(
        self,
        messages: List[LLMMessage],
        max_recent: int = 50,
    ) -> List[LLMMessage]:
        """Prepare context for Claude Code by offloading old messages to FileStore.

        Instead of sending all messages as the API prompt (which hits "Prompt too long"),
        writes old messages to a JSONL file in FileStore and returns a short context:
          [0] system prompt (original)
          [1] user: "Conversation history is in file {file_id}. Read it."
          [2] assistant: "Understood."
          [3..N] recent messages (last ~50)

        Claude Code reads the JSONL file via read_file MCP tool — no prompt size limit.
        """
        if not messages:
            return messages

        system_msg = messages[0] if messages[0].role == "system" else None
        start_idx = 1 if system_msg else 0

        # If few enough messages, no need to offload
        if len(messages) <= max_recent + start_idx + 5:
            return messages

        # Split: old messages → file, recent messages → prompt
        split = _select_recent_messages(messages, start_idx,
                                         min_conversation=25, max_total=max_recent)
        if split <= start_idx:
            return messages

        old_messages = messages[start_idx:split]
        recent_messages = messages[split:]

        # Serialize old messages to JSONL
        serialized = self._serialize_messages(old_messages)
        jsonl_lines = []
        for entry in serialized:
            jsonl_lines.append(json.dumps(entry, ensure_ascii=False))
        jsonl_content = "\n".join(jsonl_lines)

        # Write to FileStore — fallback to direct messages if store fails
        from core.file_store import FileStore
        try:
            file_id = FileStore.instance().store(
                "conversation_history.jsonl",
                jsonl_content.encode("utf-8"),
                "application/jsonl",
                category="context",
            )
        except Exception as e:
            logger.error("[cc-context] FileStore write failed: %s — sending messages directly", e)
            return messages

        logger.info("[cc-context] offloaded %d old messages (%d chars) to FileStore %s, "
                    "keeping %d recent messages in prompt",
                    len(old_messages), len(jsonl_content), file_id, len(recent_messages))

        # Build compact context
        result: List[LLMMessage] = []
        if system_msg:
            result.append(system_msg)
        result.append(LLMMessage(
            role="user",
            content=(
                f"[Conversation context — {len(old_messages)} earlier messages offloaded]\n\n"
                f"Your conversation history ({len(old_messages)} messages) is stored in "
                f"FileStore file '{file_id}' (JSONL format, one message per line with "
                f"role/content/tool_calls/tool_call_id fields).\n\n"
                f"Read it with: mcp__pawflow__use_tool(tool_name='read', "
                f"arguments={{path: '{file_id}', source: 'filestore'}}) to understand the full context.\n"
                f"The file may be large — use offset/limit arguments to paginate.\n\n"
                f"The {len(recent_messages)} most recent messages are below in the prompt. "
                f"Continue from where you left off."
            ),
        ))
        result.append(LLMMessage(
            role="assistant",
            content="Understood. I'll read the conversation history file to get full context, "
                    "then continue from the recent messages.",
        ))
        result.extend(recent_messages)
        return result

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

        def _is_clear_ref(content):
            """Don't re-truncate already-cleared results (they contain critical info)."""
            return "[result cleared]" in content or "[...truncated]" in content or "[...cleared]" in content

        # Pass 1: truncate long tool results to 200 chars
        for i in range(1, safe_end):
            if current_tokens <= target_tokens:
                break
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

        # Pass 2: shrink to 50 chars (skip already-cleared refs)
        for i in range(1, safe_end):
            if current_tokens <= target_tokens:
                break
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

        # File re-read: extract last read_file per file from OLD messages
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
                _desc = f"  - read_file {_fr['path']}"
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
            _files_note += "Call read_file with the exact same parameters to restore your working context."
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


    def _summarize_messages(
        self,
        old_messages: List[LLMMessage],
        client: LLMClient,
        max_tokens: int,
        target_tokens: int = 0,
        conversation_id: str = "",
        agent_name: str = "",
        compact_instructions: str = "",
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

        # Claude-code: no chunking — write full text to file, Claude reads it
        _provider = getattr(client, 'provider', '') or (
            getattr(client, '_client', None) and getattr(client._client, 'provider', ''))
        if _provider == "claude-code":
            total_text = "\n".join(
                self._sanitize_for_llm(self._messages_to_text([m]))
                for m in old_messages)
            return self._call_summarize(client, total_text, target_tokens,
                                       agent_name=agent_name, conversation_id=conversation_id,
                                       compact_instructions=compact_instructions)

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
                return self._call_summarize(client, total_text, target_tokens, agent_name=agent_name)

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
                    s = self._call_summarize(client, group_text, chunk_target, agent_name=agent_name)
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
                        target_tokens: int = 0,
                        user_id: str = "", agent_name: str = "",
                        llm_service: str = "",
                        conversation_id: str = "",
                        compact_instructions: str = "") -> str:
        """Single LLM call to summarize text. Routes to claude-code path if needed."""
        logger.info(f"[compact] summarize via service='{llm_service or 'default'}', "
                     f"target={target_tokens} tokens, input={len(text)} chars")
        if not target_tokens:
            target_tokens = 2000

        # Claude-code: use streaming with file + compact_result tool
        _provider = getattr(client, 'provider', '') or (
            getattr(client, '_client', None) and getattr(client._client, 'provider', ''))
        if _provider == "claude-code":
            return self._call_summarize_via_cc(
                client, text, target_tokens, user_id, agent_name, llm_service,
                conversation_id, compact_instructions)

        clean_text = self._sanitize_for_llm(text)
        _focus = f"\n<focus>{compact_instructions}</focus>\n" if compact_instructions else ""
        _prompt = (
            "Summarize this work session into a structured summary.\n"
            "Use this checklist — every section MUST be present:\n\n"
            "<checklist>\n"
            "1. USER_INTENT: What the user asked for / is working on\n"
            "2. DECISIONS: Key technical and architectural decisions made\n"
            "3. FILES_MODIFIED: Files changed with paths (and line ranges if relevant)\n"
            "4. ERRORS: Errors encountered and how they were resolved (verbatim if short)\n"
            "5. CURRENT_STATE: What was accomplished, current project state\n"
            "6. PENDING: Unfinished tasks, next steps, what the user expects next\n"
            "7. CONTEXT: Any important constraints, preferences, or rules established\n"
            "</checklist>\n\n"
            f"STRICT LIMIT: maximum {target_tokens} tokens.\n"
            f"{_focus}\n"
            f"Wrap your output in <summary></summary> tags.\n\n"
            f"SESSION:\n{clean_text}"
        )
        try:
            response = client.complete(
                messages=[
                    LLMMessage(role="user", content=_prompt),
                ],
                max_tokens=min(target_tokens * 2, 4000),
            )
            logger.info(f"[compact] LLM response: content={len(response.content or '')} chars, "
                        f"thinking={len(getattr(response, 'thinking', '') or '')} chars, "
                        f"tokens_in={response.tokens_in}, tokens_out={response.tokens_out}, "
                        f"model={response.model}")
        except Exception as e:
            err_str = str(e)
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
        # Track summarizer token usage
        if response.tokens_in > 0 and (user_id or agent_name):
            self._track_tokens(
                user_id or "system", response.tokens_in, response.tokens_out,
                model=response.model or "", agent_name=agent_name or "summarizer",
                llm_service=llm_service or "summarizer")
        return summary


    def _call_summarize_via_cc(self, client, text: str, target_tokens: int,
                              user_id: str = "", agent_name: str = "",
                              llm_service: str = "",
                              conversation_id: str = "",
                              compact_instructions: str = "") -> str:
        """Summarize via Claude Code streaming — write file, ask Claude to read + call compact_result."""
        from core.file_store import FileStore
        from core.handlers.compact_result import set_compact_key, wait_for_compact_result
        import uuid

        def _pub(detail):
            if conversation_id:
                try:
                    from core.conversation_event_bus import ConversationEventBus
                    ConversationEventBus.instance().publish_event(
                        conversation_id, "compact_progress",
                        {"stage": "summarizing", "detail": detail})
                except Exception:
                    pass

        compact_key = "CK_" + uuid.uuid4().hex[:8]
        file_id = FileStore.instance().store(
            "compact_input.txt", text.encode("utf-8"), "text/plain",
            category="compact")
        logger.info("[compact-cc] wrote %d chars as %s, key=%s", len(text), file_id, compact_key)

        set_compact_key(compact_key)

        prompt = (
            f"Summarize the conversation transcript stored in FileStore.\n\n"
            f"Steps:\n"
            f"1. Call mcp__pawflow__get_tool_schema() to discover tools\n"
            f"2. Use mcp__pawflow__use_tool(tool_name='read', arguments={{path: '{file_id}', source: 'filestore'}}) "
            f"to read the content — it may be large, use offset/limit to paginate\n"
            f"3. Summarize ALL the content in maximum {target_tokens} tokens\n"
            f"4. Call mcp__pawflow__use_tool(tool_name='compact_result', "
            f"arguments={{summary: 'your summary here', compact_key: '{compact_key}'}})\n\n"
            f"Use this checklist — every section MUST be present:\n"
            f"1. USER_INTENT 2. DECISIONS 3. FILES_MODIFIED (with paths) "
            f"4. ERRORS 5. CURRENT_STATE 6. PENDING 7. CONTEXT\n"
            f"Skip raw tool output, JSON blobs, and technical plumbing details.\n"
            + (f"\nFOCUS: {compact_instructions}\n" if compact_instructions else "") +
            f"\n"
            f"CRITICAL: After reading and summarizing, you MUST call compact_result via "
            f"mcp__pawflow__use_tool. Do NOT respond with text.\n\n"
            f"REMINDER — use EXACTLY this compact_key (copy-paste, do NOT invent one):\n"
            f"compact_key: '{compact_key}'"
        )

        _pub(f"Compacting {len(text)} chars via Claude Code...")

        # Save and clear session — compact uses a temporary session
        # The LLMClient may be nested inside a service wrapper
        _inner = getattr(client, '_client', client)
        _saved_session = getattr(_inner, '_claude_session_id', '')
        _saved_conv = getattr(_inner, '_conversation_id', '')
        _saved_agent = getattr(_inner, '_agent_name', '')
        _inner._claude_session_id = ''
        _inner._conversation_id = ''
        _inner._agent_name = 'compact'

        max_retries = 3
        for attempt in range(1, max_retries + 1):
            _pub(f"Compacting... attempt {attempt}/{max_retries}")
            logger.info("[compact-cc] attempt %d/%d", attempt, max_retries)
            if attempt > 1:
                prompt = (
                    f"RETRY {attempt}/{max_retries}: You must call compact_result.\n"
                    f"Use mcp__pawflow__use_tool(tool_name='read', arguments={{path: '{file_id}', source: 'filestore'}}) to read, "
                    f"summarize in {target_tokens} tokens, then "
                    f"mcp__pawflow__use_tool(tool_name='compact_result', arguments={{summary: '...', compact_key: '{compact_key}'}}). "
                    f"DO IT NOW."
                )
                set_compact_key(compact_key)
            try:
                client.complete_stream(
                    messages=[LLMMessage(role="user", content=prompt)],
                    max_tokens=min(target_tokens * 3, 8000),
                )
            except Exception as e:
                logger.error("[compact-cc] attempt %d failed: %s", attempt, e)
                _is_auth = "auth" in str(e).lower() or "401" in str(e)
                if _is_auth or attempt == max_retries:
                    try:
                        FileStore.instance().delete(file_id)
                    except Exception:
                        pass
                    _inner._claude_session_id = _saved_session
                    _inner._conversation_id = _saved_conv
                    _inner._agent_name = _saved_agent
                    raise
                continue

            try:
                summary = wait_for_compact_result(compact_key, timeout=10)
                if summary:
                    logger.info("[compact-cc] got %d chars summary (attempt %d)", len(summary), attempt)
                    try:
                        FileStore.instance().delete(file_id)
                    except Exception:
                        pass
                    _inner._claude_session_id = _saved_session
                    _inner._conversation_id = _saved_conv
                    _inner._agent_name = _saved_agent
                    return summary
            except TimeoutError:
                logger.warning("[compact-cc] attempt %d: compact_result not called", attempt)

        try:
            FileStore.instance().delete(file_id)
        except Exception:
            pass
        _inner._claude_session_id = _saved_session
        _inner._conversation_id = _saved_conv
        _inner._agent_name = _saved_agent
        raise RuntimeError("Claude Code failed to call compact_result after 3 attempts")

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

