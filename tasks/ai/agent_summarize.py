"""AgentLoopTask mixin — summarization methods.

Unified approach: ALL providers use the file-based method.
1. Write text to FileStore
2. LLM reads pages via tool loop, then calls compact_result
3. Works for any size (LLM paginates), no chunking needed

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

# Tool definitions for the mini summarizer loop (API providers)
_READ_TOOL = LLMToolDefinition(
    name="read",
    description=(
        "Read a file. Use source='filestore' for compaction files. "
        "Supports pagination via offset (1-based line) and limit."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path or FileStore ID"},
            "offset": {"type": "integer", "description": "Start line (1-based)"},
            "limit": {"type": "integer", "description": "Max lines to read"},
            "source": {"type": "string", "description": "Filesystem service (use 'filestore')"},
        },
        "required": ["path"],
    },
)

_COMPACT_RESULT_TOOL = LLMToolDefinition(
    name="compact_result",
    description=(
        "Return the compaction summary. Call this ONCE after reading all pages. "
        "This is the ONLY way to return a summary — do NOT respond with text."
    ),
    parameters={
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "The summary text"},
            "compact_key": {"type": "string", "description": "The compact key from instructions"},
        },
        "required": ["summary", "compact_key"],
    },
)


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
        user_id: str = "",
    ) -> str:
        """Summarize messages using the file-based approach.

        Unified strategy (all providers):
        1. Convert messages to text
        2. Write to FileStore
        3. LLM reads pages via tool loop, calls compact_result
        No chunking — the LLM paginates through the file itself.
        """
        if not target_tokens:
            target_tokens = max(500, int(max_tokens / 4))

        total_text = "\n".join(
            self._sanitize_for_llm(self._messages_to_text([m]))
            for m in old_messages)

        return self._call_summarize(
            client, total_text, target_tokens,
            user_id=user_id,
            agent_name=agent_name,
            conversation_id=conversation_id,
            compact_instructions=compact_instructions,
        )

    def _summarize_chunked(self, client: LLMClient, text: str,
                            chunk_char_limit: int,
                            target_tokens: int = 0,
                            user_id: str = "", agent_name: str = "",
                            llm_service: str = "",
                            conversation_id: str = "",
                            compact_instructions: str = "",
                            final: bool = True) -> str:
        """Divide-and-conquer summarization for inputs that don't fit one pass.

        Splits `text` into chunks ≤ `chunk_char_limit` on natural newline
        boundaries, summarizes each via `_call_summarize` (recursive call,
        each chunk fits so chunking branch never re-fires), then a final
        pass summarizes the concatenated chunk-summaries.

        Per-chunk target is sized so that the final pass input is itself
        bounded (cap chunk summaries to keep the joined input small enough
        for one CC session).
        """
        # Split on newlines, never mid-line. Greedy fill.
        lines = text.split("\n")
        chunks: List[str] = []
        cur: List[str] = []
        cur_len = 0
        for line in lines:
            ln_len = len(line) + 1  # +1 for the newline
            if cur and cur_len + ln_len > chunk_char_limit:
                chunks.append("\n".join(cur))
                cur = [line]
                cur_len = ln_len
            else:
                cur.append(line)
                cur_len += ln_len
        if cur:
            chunks.append("\n".join(cur))

        n = len(chunks)
        # Per-chunk target so the joined output fits the final pass.
        # final_input ≈ n * per_chunk_chars; we want it ≤ chunk_char_limit
        # so the final pass does a single _call_summarize without re-chunking.
        per_chunk_target = max(500, (chunk_char_limit // n) // 4)  # chars→tokens ~/4
        logger.info(
            "[compact] chunked: %d chars → %d chunks of ≤%d chars, "
            "per-chunk target=%d tokens, final target=%d tokens",
            len(text), n, chunk_char_limit, per_chunk_target, target_tokens)

        chunk_summaries: List[str] = []
        for i, chunk in enumerate(chunks, 1):
            logger.info("[compact] chunk %d/%d: %d chars", i, n, len(chunk))
            _instr = (
                f"This is chunk {i}/{n} of a larger conversation. "
                f"Output bullet notes only — facts, file paths, decisions, "
                f"errors. No template, no headers."
            )
            if compact_instructions:
                _instr = f"{compact_instructions}\n\n{_instr}"
            # Per-chunk passes are intermediate: free-form notes, no
            # 7-section template (would impose a ~4000-char floor and
            # waste output tokens on small chunks).
            summary = self._call_summarize(
                client, chunk,
                target_tokens=per_chunk_target,
                user_id=user_id, agent_name=agent_name,
                llm_service=llm_service,
                conversation_id=conversation_id,
                compact_instructions=_instr,
                final=False,
            )
            chunk_summaries.append(
                f"=== Chunk {i}/{n} notes ===\n{summary}")

        joined = "\n\n".join(chunk_summaries)
        logger.info("[compact] chunked: joined summaries = %d chars, "
                     "running %s pass",
                     len(joined), "final" if final else "intermediate")
        _final_instr = (
            "Below are bullet notes from consecutive chunks of one large "
            "conversation. Build the overall summary from them. Drop "
            "redundancy across chunks. Apply recency weighting — emphasize "
            "the LATEST chunks."
        )
        if compact_instructions:
            _final_instr = f"{compact_instructions}\n\n{_final_instr}"
        # Recursive call: same `final` semantic as the caller. If the
        # joined chunk notes still exceed the chunk limit (rare, only
        # when n was very large), this re-chunks one more level — same
        # rule applies: intermediate stays free-form, final builds the
        # 7-section structure.
        return self._call_summarize(
            client, joined,
            target_tokens=target_tokens,
            user_id=user_id, agent_name=agent_name,
            llm_service=llm_service,
            conversation_id=conversation_id,
            compact_instructions=_final_instr,
            final=final,
        )

    def _call_summarize(self, client: LLMClient, text: str,
                        target_tokens: int = 0,
                        user_id: str = "", agent_name: str = "",
                        llm_service: str = "",
                        conversation_id: str = "",
                        compact_instructions: str = "",
                        final: bool = True) -> str:
        """Summarize text via file-based tool loop (unified for all providers).

        1. Write text to FileStore
        2. For CC: use complete_stream (CC handles tool loop)
        3. For API: run mini tool loop with read + compact_result

        `final=True`  → produce the structured 7-section summary that the
                        agent will see (USER_INTENT/DECISIONS/…).
        `final=False` → intermediate chunk pass: free-form, just preserve
                        facts. Avoids the 7-section minimum bloat (~4000
                        chars floor) when summarizing small chunks.
        """
        _svc_id = llm_service
        if not _svc_id:
            try:
                _, _, _svc_id = self._get_summarizer_client(user_id)
            except Exception:
                _svc_id = ""
        if not _svc_id:
            raise RuntimeError(
                "No summarizer_service configured. Set `summarizer_service` "
                "in the flow/agent config — compaction has no default.")
        logger.info(f"[compact] summarize via summarizer_service='{_svc_id}', "
                     f"target={target_tokens} tokens, input={len(text)} chars")
        if not target_tokens:
            target_tokens = 2000

        # Divide-and-conquer for inputs that don't fit one summarizer pass.
        # CC has a hard ~200K-token context. Reading a huge file via the
        # paginated `read` tool accumulates all pages in CC's context,
        # which saturates well before the summary is emitted. Cap each
        # chunk at 50K chars (~12K tokens) so a single chunk + tool loop
        # leaves CC plenty of headroom. Each chunk gets a per-chunk
        # summary; we then summarize the concatenated summaries (final
        # pass), naturally bounded because each summary ≤ target_tokens.
        _CHUNK_CHAR_LIMIT = 50_000
        if len(text) > _CHUNK_CHAR_LIMIT:
            return self._summarize_chunked(
                client, text,
                chunk_char_limit=_CHUNK_CHAR_LIMIT,
                target_tokens=target_tokens,
                user_id=user_id, agent_name=agent_name,
                llm_service=llm_service,
                conversation_id=conversation_id,
                compact_instructions=compact_instructions,
                final=final,
            )

        from core.file_store import FileStore
        from core.handlers.compact_result import set_compact_key, wait_for_compact_result

        compact_key = "CK_" + uuid.uuid4().hex[:8]
        file_id = FileStore.instance().store(
            "compact_input.txt", text.encode("utf-8"), "text/plain",
            user_id=user_id, conversation_id=conversation_id,
            category="compact")
        logger.info("[compact] wrote %d chars as %s, key=%s", len(text), file_id, compact_key)

        set_compact_key(compact_key)

        def _pub(detail):
            # No-op SSE: the UI only displays "Compacting..." which is
            # already published by _run_bg_context_op (start/done). Per-
            # chunk / per-attempt detail is server-log territory only —
            # publishing it would flood SSE with N×retries events that
            # the UI ignores anyway.
            return

        _focus = f"\n- FOCUS: {compact_instructions}" if compact_instructions else ""
        if final:
            # Final pass: full structured summary the agent will read.
            _format_rules = (
                f"- Summary must be maximum {target_tokens} tokens.\n"
                f"- Use this checklist — every section MUST be present:\n"
                f"  1. USER_INTENT 2. DECISIONS 3. FILES_MODIFIED (with paths)\n"
                f"  4. ERRORS 5. CURRENT_STATE 6. PENDING 7. CONTEXT\n"
                f"- Skip raw tool output, JSON blobs, and technical plumbing.\n"
                f"- RECENCY WEIGHTING: emphasize the LATEST work — what the user "
                f"is currently focused on. Older threads (especially any content "
                f"tagged as 'earlier planning work' or carried over from a prior "
                f"compacted summary) should be compressed into at most one short "
                f"bullet under CONTEXT — just enough that a reader knows it "
                f"happened, without re-stating goals or decisions. If an older "
                f"topic has clearly been completed or superseded, drop it. The "
                f"summary's job is to set up the CURRENT state, not to preserve "
                f"history indefinitely."
            )
        else:
            # Intermediate chunk pass: free-form, no 7-section template.
            # The 7-section structure has a ~4000-char floor that bloats
            # per-chunk summaries 5× over their target. The final pass
            # builds the structure from the chunk notes.
            _format_rules = (
                f"- Output AT MOST {target_tokens} tokens. Stay terse.\n"
                f"- No headers, no template — free-form bullet notes.\n"
                f"- Preserve concrete facts ONLY: file paths, decisions "
                f"made, errors hit, commands run, file contents discussed. "
                f"No fluff, no narration, no meta-commentary.\n"
                f"- Skip raw tool output and JSON plumbing."
            )
        prompt = (
            f"You are a summarizer. Read the file and produce a summary.\n\n"
            f"STEP 1: Read the file:\n"
            f"  read(path=\"{file_id}\", source=\"filestore\")\n"
            f"  The file may be large — paginate with offset/limit until you've read ALL of it.\n\n"
            f"STEP 2: After reading ALL pages, deliver your summary:\n"
            f"  compact_result(summary=\"<your summary>\", compact_key=\"{compact_key}\")\n\n"
            f"RULES:\n"
            f"- You may ONLY use these 2 tools: read and compact_result.\n"
            f"- Do NOT respond with text. Your ONLY output is tool calls.\n"
            f"{_format_rules}"
            f"{_focus}\n"
            f"\ncompact_key (use EXACTLY this): {compact_key}"
        )

        _pub(f"Compacting {len(text)} chars...")

        # Detect provider
        _provider = getattr(client, 'provider', '') or (
            getattr(client, '_client', None) and getattr(client._client, 'provider', ''))

        max_retries = 3
        try:
            if _provider == "claude-code":
                return self._summarize_via_cc(
                    client, prompt, file_id, compact_key, target_tokens,
                    max_retries, _pub, conversation_id, user_id)
            else:
                return self._summarize_via_api(
                    client, prompt, file_id, compact_key, target_tokens,
                    max_retries, _pub)
        finally:
            try:
                FileStore.instance().delete(file_id)
            except Exception:
                pass

    def _summarize_via_cc(self, client, prompt: str, file_id: str,
                          compact_key: str, target_tokens: int,
                          max_retries: int, _pub, conversation_id: str,
                          user_id: str = "") -> str:
        """Run summarization via Claude Code streaming (CC handles tool loop)."""
        from core.handlers.compact_result import set_compact_key, wait_for_compact_result

        if not user_id:
            raise ValueError(
                "BUG: user_id is required for CC-based summarization "
                "(every conversation belongs to a user)")

        # Save and clear session — compact uses a dedicated workdir per user
        # so CC's _get_session_workdir doesn't raise on empty conv_id.
        _inner = getattr(client, '_client', client)
        _saved_conv = getattr(_inner, '_conversation_id', '')
        _saved_agent = getattr(_inner, '_agent_name', '')
        _saved_user = getattr(_inner, '_user_id', '')
        _saved_event_cid = getattr(_inner, '_event_cid', '')
        _inner._conversation_id = '_compact'
        _inner._agent_name = 'compact'
        _inner._user_id = user_id
        _inner._event_cid = ''

        try:
            for attempt in range(1, max_retries + 1):
                _pub("Compacting...")
                logger.info("[compact-cc] attempt %d/%d", attempt, max_retries)
                if attempt > 1:
                    prompt = (
                        f"RETRY {attempt}/{max_retries}. ONLY 2 tools allowed:\n"
                        f"1. read(path=\"{file_id}\", source=\"filestore\")\n"
                        f"2. compact_result(summary=\"...\", compact_key=\"{compact_key}\")\n"
                        f"Read the file, summarize in {target_tokens} tokens, call compact_result."
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
                        logger.info("[compact-cc] got %d chars summary (attempt %d)",
                                    len(summary), attempt)
                        return summary
                except TimeoutError:
                    logger.warning("[compact-cc] attempt %d: compact_result not called", attempt)

            raise RuntimeError("Claude Code failed to call compact_result after retries")
        finally:
            _inner._conversation_id = _saved_conv
            _inner._agent_name = _saved_agent
            _inner._user_id = _saved_user
            _inner._event_cid = _saved_event_cid
            # Clean compact workdir
            try:
                import shutil
                from core.llm_providers.claude_code import _get_sessions_base
                _compact_workdir = os.path.join(_get_sessions_base(), "default", "compact")
                for _subdir in ("projects", "sessions", ".cache"):
                    _p = os.path.join(_compact_workdir, _subdir)
                    if os.path.isdir(_p):
                        shutil.rmtree(_p, ignore_errors=True)
            except Exception:
                pass

    def _summarize_via_api(self, client, prompt: str, file_id: str,
                           compact_key: str, target_tokens: int,
                           max_retries: int, _pub) -> str:
        """Run summarization via API tool loop (OpenAI, Anthropic, Gemini).

        Mini agent loop: send prompt with read + compact_result tools,
        execute tool calls, feed results back, repeat until compact_result
        is called or max iterations.
        """
        from core.handlers.compact_result import set_compact_key, wait_for_compact_result
        from core.handlers.read import ReadHandler

        read_handler = ReadHandler()
        tools = [_READ_TOOL, _COMPACT_RESULT_TOOL]
        max_loop = 15  # max tool-loop iterations (read pages + compact)

        for attempt in range(1, max_retries + 1):
            _pub("Compacting...")
            logger.info("[compact-api] attempt %d/%d", attempt, max_retries)

            if attempt > 1:
                prompt = (
                    f"RETRY {attempt}/{max_retries}. ONLY 2 tools:\n"
                    f"1. read(path=\"{file_id}\", source=\"filestore\")\n"
                    f"2. compact_result(summary=\"...\", compact_key=\"{compact_key}\")\n"
                    f"Read the file, summarize in {target_tokens} tokens, call compact_result."
                )
                set_compact_key(compact_key)

            messages = [LLMMessage(role="user", content=prompt)]

            for iteration in range(max_loop):
                try:
                    response = client.complete(
                        messages=messages,
                        max_tokens=min(target_tokens * 3, 8000),
                        tools=tools,
                        temperature=0.3,
                    )
                except Exception as e:
                    logger.error("[compact-api] LLM call failed (attempt %d, iter %d): %s",
                                 attempt, iteration, e)
                    break

                # No tool calls = LLM responded with text (shouldn't happen, but handle it)
                if not response.tool_calls:
                    # Check if compact_result was delivered via the global mechanism
                    try:
                        summary = wait_for_compact_result(compact_key, timeout=1)
                        if summary:
                            return summary
                    except (TimeoutError, RuntimeError):
                        pass
                    # If the LLM just returned text, use it as the summary directly
                    if response.content and len(response.content.strip()) > 50:
                        logger.warning("[compact-api] LLM returned text instead of tool call, "
                                       "using as summary (%d chars)", len(response.content))
                        return response.content
                    break

                # Process tool calls
                assistant_msg = LLMMessage(
                    role="assistant", content=response.content or "",
                    tool_calls=response.tool_calls)
                messages.append(assistant_msg)

                for tc in response.tool_calls:
                    args = tc.arguments if isinstance(tc.arguments, dict) else {}
                    tool_name = tc.name

                    if tool_name == "compact_result":
                        # Execute compact_result directly
                        from core.handlers.compact_result import CompactResultHandler
                        handler = CompactResultHandler()
                        handler.execute(args)
                        # Retrieve result
                        try:
                            summary = wait_for_compact_result(compact_key, timeout=5)
                            if summary:
                                logger.info("[compact-api] got %d chars summary "
                                            "(attempt %d, iter %d)",
                                            len(summary), attempt, iteration)
                                return summary
                        except (TimeoutError, RuntimeError):
                            pass
                        # Fallback: extract from arguments directly
                        direct_summary = args.get("summary", "")
                        if direct_summary and len(direct_summary.strip()) > 50:
                            logger.info("[compact-api] got summary from args directly "
                                        "(%d chars)", len(direct_summary))
                            return direct_summary
                        messages.append(LLMMessage(
                            role="tool", content="Summary received.",
                            tool_call_id=tc.id))

                    elif tool_name == "read":
                        # Execute read via handler
                        result = read_handler.execute(args)
                        messages.append(LLMMessage(
                            role="tool", content=result,
                            tool_call_id=tc.id))

                    else:
                        messages.append(LLMMessage(
                            role="tool",
                            content=f"Error: only 'read' and 'compact_result' tools are available.",
                            tool_call_id=tc.id))

            # Check if compact_result was called during this attempt
            try:
                summary = wait_for_compact_result(compact_key, timeout=2)
                if summary:
                    return summary
            except (TimeoutError, RuntimeError):
                pass
            logger.warning("[compact-api] attempt %d: compact_result not called", attempt)

        raise RuntimeError(f"API summarizer failed to call compact_result after {max_retries} attempts")
