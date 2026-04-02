"""AgentLoopTask mixin — summarization methods.

Extracted from tasks/ai/agent_compaction.py.
All methods access self (AgentLoopTask instance).
"""
import json
import logging
import os
import time
import uuid
from typing import Dict, Any, List, Optional

from core.llm_client import (
    LLMClient, LLMMessage, LLMResponse, LLMToolDefinition,
    LLMToolCall, LLMToolResult, LLMClientError,
)

logger = logging.getLogger(__name__)


class AgentSummarizeMixin:
    """Summarization methods extracted from AgentCompactionMixin."""

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

        # Claude-code: no chunking — write full text to file, Claude reads it via MCP
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
            f"You are a summarizer. You have EXACTLY 2 tasks:\n\n"
            f"TASK 1: Read the file by calling:\n"
            f"  mcp__pawflow__use_tool(tool_name='read', arguments={{\"path\": \"{file_id}\", \"source\": \"filestore\"}})\n"
            f"  The file is large — paginate with offset/limit until you've read it all.\n\n"
            f"TASK 2: After reading ALL pages, summarize and deliver by calling:\n"
            f"  mcp__pawflow__use_tool(tool_name='compact_result', arguments={{\"summary\": \"<your summary>\", \"compact_key\": \"{compact_key}\"}})\n\n"
            f"RULES:\n"
            f"- You may ONLY call these 2 tools: 'read' and 'compact_result'. NO other tools.\n"
            f"- Do NOT call get_tool_schema, execute_script, bash, grep, or anything else.\n"
            f"- Do NOT respond with text. Your ONLY output is tool calls.\n"
            f"- Summary must be maximum {target_tokens} tokens.\n"
            f"- Use this checklist — every section MUST be present:\n"
            f"  1. USER_INTENT 2. DECISIONS 3. FILES_MODIFIED (with paths)\n"
            f"  4. ERRORS 5. CURRENT_STATE 6. PENDING 7. CONTEXT\n"
            f"- Skip raw tool output, JSON blobs, and technical plumbing.\n"
            + (f"- FOCUS: {compact_instructions}\n" if compact_instructions else "") +
            f"\ncompact_key (use EXACTLY this, do NOT invent one): {compact_key}"
        )

        _pub(f"Compacting {len(text)} chars via Claude Code...")

        # Save and clear session — compact uses a temporary session
        # The LLMClient may be nested inside a service wrapper
        _inner = getattr(client, '_client', client)
        _saved_conv = getattr(_inner, '_conversation_id', '')
        _saved_agent = getattr(_inner, '_agent_name', '')
        _saved_event_cid = getattr(_inner, '_event_cid', '')
        _inner._conversation_id = ''
        _inner._agent_name = 'compact'
        _inner._event_cid = ''  # prevent SSE events from leaking to parent conv

        max_retries = 3
        try:
            for attempt in range(1, max_retries + 1):
                _pub(f"Compacting... attempt {attempt}/{max_retries}")
                logger.info("[compact-cc] attempt %d/%d", attempt, max_retries)
                if attempt > 1:
                    prompt = (
                        f"RETRY {attempt}/{max_retries}. ONLY 2 tools allowed:\n"
                        f"1. mcp__pawflow__use_tool(tool_name='read', arguments={{\"path\": \"{file_id}\", \"source\": \"filestore\"}})\n"
                        f"2. mcp__pawflow__use_tool(tool_name='compact_result', arguments={{\"summary\": \"...\", \"compact_key\": \"{compact_key}\"}})\n"
                        f"Read the file, summarize in {target_tokens} tokens, call compact_result. NO other tools."
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
                        raise
                    continue

                try:
                    summary = wait_for_compact_result(compact_key, timeout=10)
                    if summary:
                        logger.info("[compact-cc] got %d chars summary (attempt %d)", len(summary), attempt)
                        return summary
                except TimeoutError:
                    logger.warning("[compact-cc] attempt %d: compact_result not called", attempt)

            raise RuntimeError("Claude Code failed to call compact_result after 3 attempts")
        finally:
            _inner._conversation_id = _saved_conv
            _inner._agent_name = _saved_agent
            _inner._event_cid = _saved_event_cid
            try:
                FileStore.instance().delete(file_id)
            except Exception:
                pass
            # Clean compact workdir — session data is disposable
            try:
                import shutil
                from core.llm_providers.claude_code import _SESSIONS_BASE
                _compact_workdir = os.path.join(_SESSIONS_BASE, "default", "compact")
                for _subdir in ("projects", "sessions", ".cache"):
                    _p = os.path.join(_compact_workdir, _subdir)
                    if os.path.isdir(_p):
                        shutil.rmtree(_p, ignore_errors=True)
            except Exception:
                pass

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
