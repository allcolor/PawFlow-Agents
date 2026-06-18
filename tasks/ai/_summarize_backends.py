"""Provider-specific summarizer backends for AgentSummarizeMixin:
claude-code-interactive (_summarize_via_cc) and API providers
(_summarize_via_api).

Split out of agent_summarize.py as a leaf mixin so the file stays <= 800 lines.
Methods rely on host state/methods from AgentSummarizeMixin via the MRO.
"""
from __future__ import annotations

import logging
import os

from core.llm_client import LLMMessage

from tasks.ai._summarize_text import (
    _COMPACT_RESULT_TOOL,
    _READ_TOOL,
    _compact_scope_id,
    _strip_analysis_wrapper,
)

logger = logging.getLogger(__name__)


class _AgentSummarizeBackendMixin:
    """Provider-specific summarizer backends for AgentSummarizeMixin."""

    def _summarize_via_cc(self, client, prompt: str, file_id: str,
                          compact_key: str, target_tokens: int,
                          max_retries: int, _pub, conversation_id: str,
                          user_id: str = "", compact_scope: str = "") -> str:
        """Run summarization via Claude Code streaming (CC handles tool loop)."""
        from core.handlers.compact_result import set_compact_key, wait_for_compact_result

        if not user_id:
            raise ValueError(
                "BUG: user_id is required for CC-based summarization "
                "(every conversation belongs to a user)")

        # Compact runs on its OWN cloned client. Each Claude Code
        # stream already has its own Docker container; the Python
        # orchestration state (proc/container/pid/result_emitted/
        # compacting/preempt_*/stderr_buffer/...) must also be its
        # own — otherwise a concurrent compact/memory/btw stream
        # clobbers the main agent's tracking simply by writing
        # attributes on a shared singleton.
        _inner = getattr(client, '_client', client)
        _compact_client = _inner.clone_for_call()

        compact_scope = compact_scope or _compact_scope_id(
            conversation_id, compact_key)
        _compact_call_kwargs = {
            "call_user_id": user_id,
            "call_conversation_id": compact_scope,
            "call_agent_name": "compact",
            "call_event_cid": "",
            "call_ephemeral_stream": True,
        }

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
                _stream_response = None
                _stream_exc = None
                try:
                    _stream_response = _compact_client.complete_stream(
                        messages=[LLMMessage(role="user", content=prompt,
                                               conversation_id=compact_scope)],
                        max_tokens=min(target_tokens * 3, 8000),
                        **_compact_call_kwargs,
                    )
                except Exception as e:
                    # Don't treat this as fatal YET. We deliberately kill
                    # CC the moment compact_result delivers (see
                    # _stream_claude_code), which makes CC exit non-zero
                    # and complete_stream raise. If the summary is
                    # already on the event, the attempt actually
                    # SUCCEEDED — poll the event before reporting.
                    _stream_exc = e

                # Primary success path: the compact_result handler set
                # the event. timeout=0 means non-blocking peek if
                # already delivered; small timeout lets a racy tool
                # dispatch land if it fired right before the kill.
                try:
                    summary = wait_for_compact_result(compact_key, timeout=2)
                    if summary:
                        logger.info("[compact-cc] got %d chars summary "
                                     "(attempt %d%s)",
                                     len(summary), attempt,
                                     " — CC exit was from our kill, ignored"
                                     if _stream_exc else "")
                        return _strip_analysis_wrapper(summary)
                except TimeoutError:
                    pass

                # No summary delivered. If complete_stream raised, the
                # stream exception is the real story — but only if it's
                # a real infra error (auth, network). Exit code 1 after
                # our own kill of CC following a REJECTED compact_result
                # tool call (empty summary, wrong key, etc.) is a CC
                # misbehaviour case — retry is legitimate but logging
                # "Claude CLI stream exited with code 1" hides the real
                # cause. Relabel those so operators see what actually
                # happened.
                if _stream_exc is not None:
                    _exc_str = str(_stream_exc)
                    _is_our_kill_exit = (
                        "exited with code 1" in _exc_str
                        or "exited with code 137" in _exc_str)
                    if _is_our_kill_exit:
                        logger.warning(
                            "[compact-cc] attempt %d: CC called "
                            "compact_result but handler rejected it "
                            "(empty summary or wrong key) — retrying",
                            attempt)
                    else:
                        logger.error(
                            "[compact-cc] attempt %d failed: %s",
                            attempt, _stream_exc)
                    _is_auth = ("auth" in _exc_str.lower()
                                 or "401" in _exc_str)
                    if _is_auth or attempt == max_retries:
                        raise _stream_exc
                    continue

                logger.warning("[compact-cc] attempt %d: compact_result "
                                "not called", attempt)

                # Fallback: CC under context pressure sometimes emits the
                # summary as plain text instead of calling compact_result.
                # Salvage it rather than retrying from scratch (costly).
                _text = getattr(_stream_response, "content", "") or ""
                if _text.strip() and len(_text.strip()) > 50:
                    logger.warning(
                        "[compact-cc] attempt %d: CC returned text instead "
                        "of compact_result tool call, using as summary "
                        "(%d chars)", attempt, len(_text))
                    return _strip_analysis_wrapper(_text)

            raise RuntimeError("Claude Code failed to call compact_result after retries")
        finally:
            # _compact_client is the cloned isolated instance — nothing
            # to restore on the shared singleton (we never wrote to it).
            # One-shot helper: wipe the entire _compact workdir for this
            # user. Nothing here needs to persist between compactions.
            try:
                import shutil
                from core.llm_providers.claude_code import _get_sessions_base
                _uid = (user_id or "default").replace(":", "_").replace("/", "_").replace("\\", "_")
                _compact_workdir = os.path.join(
                    _get_sessions_base(), _uid, compact_scope, "compact")
                if os.path.isdir(_compact_workdir):
                    shutil.rmtree(_compact_workdir, ignore_errors=True)
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

    def _summarize_via_api(self, client, prompt: str, file_id: str,
                           compact_key: str, target_tokens: int,
                           max_retries: int, _pub,
                           conversation_id: str, user_id: str,
                           compact_scope: str = "") -> str:
        """Run summarization via API tool loop (OpenAI, Anthropic, Gemini).

        Mini agent loop: send prompt with read + compact_result tools,
        execute tool calls, feed results back, repeat until compact_result
        is called or max iterations.
        """
        from core.handlers.compact_result import set_compact_key, wait_for_compact_result
        from core.handlers.read import ReadHandler

        if not user_id:
            raise ValueError(
                "BUG: user_id is required for API-based summarization "
                "so every summarizer provider gets the same call scope")
        if not conversation_id:
            raise ValueError(
                "BUG: conversation_id is required for API-based summarization")

        read_handler = ReadHandler()
        if hasattr(read_handler, "set_user_id"):
            read_handler.set_user_id(user_id)
        compact_scope = compact_scope or _compact_scope_id(
            conversation_id, compact_key)
        if hasattr(read_handler, "set_conversation_id"):
            read_handler.set_conversation_id(compact_scope)
        tools = [_READ_TOOL, _COMPACT_RESULT_TOOL]
        max_loop = 15  # max tool-loop iterations (read pages + compact)
        call_scope = {
            "call_user_id": user_id,
            "call_conversation_id": compact_scope,
            "call_agent_name": "compact",
            "call_event_cid": "",
            "call_ephemeral_stream": True,
        }

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

            messages = [LLMMessage(role="user", content=prompt,
                                     conversation_id=compact_scope)]

            for iteration in range(max_loop):
                try:
                    response = client.complete(
                        messages=messages,
                        max_tokens=min(target_tokens * 3, 8000),
                        tools=tools,
                        temperature=0.3,
                        **call_scope,
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
                            return _strip_analysis_wrapper(summary)
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
                    tool_calls=response.tool_calls,
                    thinking=getattr(response, "thinking", "") or "",
                    thinking_signature=getattr(response, "thinking_signature", "") or "",
                    conversation_id=compact_scope)
                messages.append(assistant_msg)

                for tc in response.tool_calls:
                    args = tc.arguments if isinstance(tc.arguments, dict) else {}
                    tool_name = tc.name

                    if tool_name == "compact_result":
                        # Execute compact_result directly
                        from core.handlers.compact_result import CompactResultHandler
                        handler = CompactResultHandler()
                        if hasattr(handler, "set_user_id"):
                            handler.set_user_id(user_id)
                        if hasattr(handler, "set_conversation_id"):
                            handler.set_conversation_id(compact_scope)
                        handler.execute(args)
                        # Retrieve result
                        try:
                            summary = wait_for_compact_result(compact_key, timeout=5)
                            if summary:
                                logger.info("[compact-api] got %d chars summary "
                                            "(attempt %d, iter %d)",
                                            len(summary), attempt, iteration)
                                return _strip_analysis_wrapper(summary)
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
                            tool_call_id=tc.id,
                            conversation_id=compact_scope))

                    elif tool_name == "read":
                        # Execute read via handler
                        result = read_handler.execute(args)
                        messages.append(LLMMessage(
                            role="tool", content=result,
                            tool_call_id=tc.id,
                            conversation_id=compact_scope))

                    else:
                        messages.append(LLMMessage(
                            role="tool",
                            content="Error: only 'read' and 'compact_result' tools are available.",
                            tool_call_id=tc.id,
                            conversation_id=compact_scope))

            # Check if compact_result was called during this attempt
            try:
                summary = wait_for_compact_result(compact_key, timeout=2)
                if summary:
                    return _strip_analysis_wrapper(summary)
            except (TimeoutError, RuntimeError):
                pass
            logger.warning("[compact-api] attempt %d: compact_result not called", attempt)

        raise RuntimeError(f"API summarizer failed to call compact_result after {max_retries} attempts")
