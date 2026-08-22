"""Core request driver for LLMClient: complete / complete_stream / embed and
abort control. Split out of llm_client.py as a leaf mixin so the file stays
<= 800 lines. Provider-specific work is resolved through the LLMClient MRO
(self.*); this mixin must precede the provider mixins so these methods win.
"""
from __future__ import annotations

import logging
import random
import re
import time
import errno
from typing import List, Optional

from core.token_counter import count_messages_tokens, truncate_tokens
from core._llm_types import (
    INTERACTIVE_CLI_PROVIDERS,
    AgentSuperseded,
    CCCompactDetected,
    ColdStartRequired,
    DeltaContextRequired,
    LLMCallError,
    LLMClientError,
    LLMMessage,
    LLMResponse,
    LLMToolDefinition,
    TRUNCATED_STREAM_CATEGORIES,
)

logger = logging.getLogger(__name__)

#: Providers whose requests are OpenAI chat-completions bodies. They share the
#: whole OpenAI path; only the URL layout and auth header differ, and that is
#: decided in core.llm_providers.openai_dialects.
OPENAI_WIRE_PROVIDERS = ("openai", "azure-openai", "copilot", "omniroute")

#: Endpoints speaking OpenAI's Responses API. A different wire format from
#: chat/completions -- typed input items, `instructions`, flat tools, and a
#: semantic SSE stream with no [DONE] sentinel -- so it dispatches separately
#: rather than joining OPENAI_WIRE_PROVIDERS.
RESPONSES_WIRE_PROVIDERS = ("openai-responses",)


class _LLMClientDriverMixin:
    """complete / complete_stream / embed + abort control for LLMClient."""

    @staticmethod
    def _redact_relay_proxy_url(url: str) -> str:
        """Hide relay proxy bearer tokens before writing URLs to logs."""
        return re.sub(r"(/relay-proxy/[^/]+/)[^/]+/", r"\1<token>/", url or "")

    @staticmethod
    def _is_broken_pipe_error(exc: BaseException) -> bool:
        """Return True for direct or wrapped EPIPE/BrokenPipe failures."""
        seen = set()
        current = exc
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            if isinstance(current, BrokenPipeError):
                return True
            if isinstance(current, OSError) and getattr(current, "errno", None) == errno.EPIPE:
                return True
            current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
        return False

    @staticmethod
    def _limit_final_content(result: LLMResponse,
                             max_tokens: int) -> LLMResponse:
        """Limit terminal visible text without touching reasoning or tools."""
        if max_tokens <= 0 or result.tool_calls or not result.content:
            return result
        limited = truncate_tokens(result.content, max_tokens)
        if limited != result.content:
            result.content = limited
            result.finish_reason = "length"
        return result

    def _apply_call_identity(self, *, call_user_id=None,
                             call_conversation_id=None, call_agent_name=None,
                             call_event_cid=None) -> None:
        """Attach per-call identity needed by relay-aware base_url resolution.

        Call sites should still prefer isolated clients. This method only writes
        non-empty call-scoped fields, keeping clone_for_call() itself free of
        mutable parent stream state.
        """
        if call_user_id:
            self._user_id = call_user_id
        if call_conversation_id:
            self._conversation_id = call_conversation_id
        if call_agent_name:
            self._agent_name = call_agent_name
        if call_event_cid:
            self._event_cid = call_event_cid

    def complete(
        self,
        messages: List[LLMMessage],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 0,
        response_format: Optional[str] = None,
        tools: Optional[List[LLMToolDefinition]] = None,
        thinking_budget: int = 0,
        *,
        call_user_id: Optional[str] = None,
        call_conversation_id: Optional[str] = None,
        call_agent_name: Optional[str] = None,
        call_event_cid: Optional[str] = None,
        call_ephemeral_stream: Optional[bool] = None,
        call_is_initial_user_turn: Optional[bool] = None,
    ) -> LLMResponse:
        """Send a completion request to the LLM.

        Args:
            messages: Conversation messages (supports tool_calls and tool results).
            model: Model name override.
            temperature: Sampling temperature.
            max_tokens: Maximum visible tokens in the terminal response. Hidden
                reasoning and tool calls do not consume this budget.
            response_format: "json" for JSON mode (OpenAI only).
            tools: Tool definitions for function calling / tool_use.
            call_user_id, call_conversation_id, call_agent_name,
            call_event_cid, call_ephemeral_stream: per-call identity for
                providers that need it (currently CC). Pass these from
                the call site rather than mutating shared client state —
                concurrent compact / memory-extract / sub-agent streams
                on the same client instance would otherwise race via
                try/finally save-restore on `self.*`.

        Returns:
            LLMResponse with content and/or tool_calls populated.
        """
        if not self.api_key and self.provider not in ("claude-code", "claude-code-interactive", "antigravity-interactive", "codex-app-server", "codex-interactive", "gemini"):
            raise LLMClientError("api_key is required")
        if self.provider not in self.PROVIDERS:
            raise LLMClientError(
                f"Unknown provider '{self.provider}'. Supported: {', '.join(self.PROVIDERS)}"
            )

        self._apply_call_identity(
            call_user_id=call_user_id,
            call_conversation_id=call_conversation_id,
            call_agent_name=call_agent_name,
            call_event_cid=call_event_cid,
        )
        model = model or self.default_model
        # Provider fields such as max_completion_tokens, max_output_tokens and
        # Anthropic max_tokens include hidden reasoning and/or tool-call payloads.
        # They therefore cannot represent PawFlow's final-visible-answer budget.
        # Let the transport use its own ceiling and enforce max_tokens locally
        # after the response type (tool turn vs terminal answer) is known.
        wire_max_tokens = 0

        def _do_complete(mdl):
            self._circuit_before_call(mdl)
            start = time.time()
            if self.provider in OPENAI_WIRE_PROVIDERS:
                result = self._complete_openai(messages, mdl, temperature, wire_max_tokens, response_format, tools,
                                                call_user_id=call_user_id or "",
                                                call_conversation_id=call_conversation_id or "")
            elif self.provider in RESPONSES_WIRE_PROVIDERS:
                # The Responses API has one parser, and it is the streaming
                # one; complete() is the same call without callbacks.
                result = self._stream_openai_responses(
                    messages, mdl, temperature, wire_max_tokens, tools, None,
                    call_user_id=call_user_id or "",
                    call_conversation_id=call_conversation_id or "")
            elif self.provider == "claude-code":
                # CC only has stream-json mode — complete() and stream()
                # share the same path; complete() simply doesn't pass a
                # streaming callback. The LLMResponse carries the final
                # text + tool_calls.
                result = self._stream_claude_code(
                    messages, mdl, temperature, wire_max_tokens, tools,
                    call_user_id=call_user_id,
                    call_conversation_id=call_conversation_id,
                    call_agent_name=call_agent_name,
                    call_event_cid=call_event_cid,
                    call_ephemeral_stream=call_ephemeral_stream,
                )
            elif self.provider == "claude-code-interactive":
                result = self._stream_claude_code_interactive(
                    messages, mdl, temperature, wire_max_tokens, tools,
                    call_user_id=call_user_id,
                    call_conversation_id=call_conversation_id,
                    call_agent_name=call_agent_name,
                    call_event_cid=call_event_cid,
                    call_ephemeral_stream=call_ephemeral_stream,
                )
            elif self.provider == "antigravity-interactive":
                result = self._stream_antigravity_interactive(
                    messages, mdl, temperature, wire_max_tokens, tools,
                    call_user_id=call_user_id,
                    call_conversation_id=call_conversation_id,
                    call_agent_name=call_agent_name,
                    call_event_cid=call_event_cid,
                    call_ephemeral_stream=call_ephemeral_stream,
                )
            elif self.provider == "codex-app-server":
                result = self._stream_codex_app_server(
                    messages, mdl, temperature, wire_max_tokens, tools,
                    thinking_budget=thinking_budget,
                    call_user_id=call_user_id,
                    call_conversation_id=call_conversation_id,
                    call_agent_name=call_agent_name,
                    call_event_cid=call_event_cid,
                    call_ephemeral_stream=call_ephemeral_stream,
                )
            elif self.provider == "codex-interactive":
                result = self._stream_codex_interactive(
                    messages, mdl, temperature, wire_max_tokens, tools,
                    thinking_budget=thinking_budget,
                    call_user_id=call_user_id,
                    call_conversation_id=call_conversation_id,
                    call_agent_name=call_agent_name,
                    call_event_cid=call_event_cid,
                    call_ephemeral_stream=call_ephemeral_stream,
                )
            elif self.provider == "gemini":
                result = self._stream_gemini(
                    messages, mdl, temperature, wire_max_tokens, tools,
                    call_user_id=call_user_id,
                    call_conversation_id=call_conversation_id,
                    call_agent_name=call_agent_name,
                    call_event_cid=call_event_cid,
                    call_ephemeral_stream=call_ephemeral_stream,
                )
            else:
                result = self._complete_anthropic(messages, mdl, temperature, wire_max_tokens, tools, thinking_budget=thinking_budget,
                                                   call_user_id=call_user_id or "",
                                                   call_conversation_id=call_conversation_id or "")
            result.duration_ms = (time.time() - start) * 1000
            self._limit_final_content(result, max_tokens)
            self._record_response_context_usage(
                result,
                call_conversation_id=call_conversation_id or "",
                call_agent_name=call_agent_name or "",
                call_user_id=call_user_id or "",
                call_event_cid=call_event_cid or "",
            )
            if not result.tokens_in and messages:
                result.tokens_in = count_messages_tokens(messages)
            if not result.tokens_out and result.content:
                result.tokens_out = len(result.content) // 4
            self._report_tokens(result, messages)
            self._circuit_after_success(mdl)
            return result

        last_error = None
        overloaded_attempts = 0
        max_overloaded = 3  # hard cap for 529 overloaded errors
        for attempt in range(1, self.max_retries + 1):
            try:
                return _do_complete(model)
            except (LLMClientError, Exception) as e:
                if self.provider in INTERACTIVE_CLI_PROVIDERS:
                    # The prompt is already consumed by the live CLI session,
                    # which did its own API retries; calling the provider
                    # again would paste it twice or trip the cold/delta guard.
                    raise
                last_error = e
                err_str = str(e)

                if ((isinstance(e, LLMCallError) and not e.retryable)
                        or self._is_permanent_request_error(err_str)):
                    if isinstance(last_error, LLMClientError):
                        raise last_error
                    raise LLMClientError(str(last_error))

                # Match HTTP codes as standalone tokens — plain substring
                # matching fired false positives on captured CC PIDs like
                # 165500 / 1429xx, turning our own intentional kills into
                # retriable "500"/"429" errors.
                is_429 = ((isinstance(e, LLMCallError)
                           and e.category in {"rate_limited", "quota_exhausted"})
                          or bool(re.search(r'\b429\b', err_str))
                          or "rate_limit" in err_str.lower())
                is_529 = bool(re.search(r'\b529\b', err_str)) or "overloaded" in err_str.lower()
                is_500 = (bool(re.search(r'\b500\b', err_str))
                           or "Internal server error" in err_str)

                if is_529:
                    overloaded_attempts += 1
                    if overloaded_attempts >= max_overloaded:
                        self._circuit_after_failure(model, err_str)
                        # 529 cap reached — try fallback model
                        if self.fallback_model and self.fallback_model != model:
                            logger.warning(
                                "Overloaded (529): %d/%d attempts exhausted on '%s', trying fallback '%s'",
                                overloaded_attempts, max_overloaded, model, self.fallback_model,
                            )
                            try:
                                return _do_complete(self.fallback_model)
                            except Exception as fb_err:
                                logger.error("Fallback model '%s' also failed: %s", self.fallback_model, fb_err)
                        raise LLMClientError(f"Overloaded (529) after {overloaded_attempts} attempts: {last_error}")

                _is_cc_our_exit = "Claude CLI stream exited" in err_str
                _other_code_re = re.compile(
                    r'\b(503|502|reset|timeout|api_error|server_error)\b',
                    re.IGNORECASE)
                is_transport_drop = self._is_transient_transport_error(err_str)
                # A 200 that stopped: no status code to match on, so the
                # category is the only signal. See VALID_FINISH_REASONS in
                # core/llm_providers/openai.py.
                is_truncated_stream = (
                    isinstance(e, LLMCallError)
                    and e.category in TRUNCATED_STREAM_CATEGORIES)
                retryable = (
                    (is_429 or is_529 or is_500 or is_transport_drop
                     or is_truncated_stream
                     or bool(_other_code_re.search(err_str)))
                    and not _is_cc_our_exit)
                if retryable and attempt < self.max_retries:
                    server_delay = (e.retry_after_seconds
                                    if isinstance(e, LLMCallError)
                                    and e.retry_after_seconds > 0
                                    else self._parse_retry_after(err_str))
                    base_delay = 2.0
                    exp_delay = base_delay * (2 ** (attempt - 1)) * (0.75 + random.random() * 0.5)  # nosec B311
                    wait = server_delay if server_delay != 2.0 else exp_delay
                    if is_429:
                        logger.warning(f"Rate limited (429), waiting {wait:.1f}s (attempt {attempt}/{self.max_retries})")
                    elif is_529:
                        logger.warning(f"Overloaded (529), waiting {wait:.1f}s (attempt {attempt}/{self.max_retries})")
                    elif is_500:
                        logger.warning(f"Server error (500), waiting {wait:.1f}s (attempt {attempt}/{self.max_retries})")
                    else:
                        logger.warning(f"LLM request attempt {attempt}/{self.max_retries} failed: {e}, retrying in {wait:.1f}s...")
                    time.sleep(wait)
                    continue

                # All retries exhausted — try fallback model if configured
                self._circuit_after_failure(model, err_str)
                if self.fallback_model and self.fallback_model != model:
                    logger.warning(
                        "Primary model '%s' failed after %d attempts, trying fallback '%s'",
                        model, self.max_retries, self.fallback_model,
                    )
                    try:
                        return _do_complete(self.fallback_model)
                    except Exception as fallback_err:
                        logger.error("Fallback model '%s' also failed: %s", self.fallback_model, fallback_err)
                if isinstance(last_error, LLMClientError):
                    raise last_error
                raise LLMClientError(f"LLM request failed after {self.max_retries} attempts: {last_error}")

    def abort(self):
        """Signal the current LLM call to abort (thread-safe)."""
        self._abort.set()
        if getattr(self, "provider", "") == "codex-app-server":
            try:
                self._codex_app_abort_active(force=True)
            except Exception:
                logger.debug("Codex app-server abort failed", exc_info=True)
        if getattr(self, "provider", "") == "claude-code-interactive":
            try:
                self.cancel_claude_code_interactive(force=True)
            except Exception:
                logger.debug("Claude Code interactive abort failed", exc_info=True)
        if getattr(self, "provider", "") == "codex-interactive":
            try:
                self.cancel_codex_interactive(force=True)
            except Exception:
                logger.debug("Codex interactive abort failed", exc_info=True)
        if getattr(self, "provider", "") == "antigravity-interactive":
            try:
                self.cancel_antigravity_interactive(force=True)
            except Exception:
                logger.debug("Antigravity interactive abort failed", exc_info=True)
        conn = getattr(self, "_active_http_conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                logger.debug("LLM abort connection close failed", exc_info=True)

    def reset_abort(self):
        """Clear the abort signal before a new call."""
        self._abort.clear()

    def complete_stream(
        self,
        messages: List[LLMMessage],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 0,
        tools: Optional[List[LLMToolDefinition]] = None,
        callback=None,
        thinking_budget: int = 0,
        thinking_callback=None,
        turn_callback=None,
        block_callback=None,
        *,
        call_user_id: Optional[str] = None,
        call_conversation_id: Optional[str] = None,
        call_agent_name: Optional[str] = None,
        call_event_cid: Optional[str] = None,
        call_ephemeral_stream: Optional[bool] = None,
        call_is_initial_user_turn: Optional[bool] = None,
    ) -> LLMResponse:
        """Streaming completion — calls callback(token: str) for each token.

        Also returns the full LLMResponse at the end.  If callback is None,
        behaves like complete() but uses the streaming API under the hood.

        turn_callback(text, tool_calls, thinking=""): called by multi-turn
        providers at the end of each internal turn. Providers may omit the
        optional thinking argument. Allows the agent loop to persist
        intermediate messages.

        Supports both OpenAI and Anthropic streaming.
        """
        if not self.api_key and self.provider not in ("claude-code", "claude-code-interactive", "antigravity-interactive", "codex-app-server", "codex-interactive", "gemini"):
            raise LLMClientError("api_key is required")

        self._apply_call_identity(
            call_user_id=call_user_id,
            call_conversation_id=call_conversation_id,
            call_agent_name=call_agent_name,
            call_event_cid=call_event_cid,
        )
        model = model or self.default_model
        wire_max_tokens = 0
        streamed_raw = ""
        streamed_visible = ""

        def _visible_callback(delta):
            nonlocal streamed_raw, streamed_visible
            if not callback or not delta:
                return
            streamed_raw += delta
            if max_tokens <= 0:
                callback(delta)
                streamed_visible += delta
                return
            limited = truncate_tokens(streamed_raw, max_tokens)
            if limited.startswith(streamed_visible):
                visible_delta = limited[len(streamed_visible):]
                if visible_delta:
                    callback(visible_delta)
                streamed_visible = limited

        def _terminal_turn_callback(text, tool_calls, thinking=""):
            nonlocal streamed_raw, streamed_visible
            if tool_calls:
                if callback and streamed_raw.startswith(streamed_visible):
                    remainder = streamed_raw[len(streamed_visible):]
                    if remainder:
                        callback(remainder)
                # A tool turn is outside the terminal-response budget. Start a
                # fresh visible counter for the answer produced after the tool.
                streamed_raw = ""
                streamed_visible = ""
            if not turn_callback:
                return
            visible = text
            if not tool_calls and max_tokens > 0:
                visible = truncate_tokens(text or "", max_tokens)
            try:
                turn_callback(visible, tool_calls, thinking)
            except TypeError:
                turn_callback(visible, tool_calls)

        def _do_stream(mdl):
            nonlocal streamed_raw, streamed_visible
            self._circuit_before_call(mdl)
            start = time.time()
            if self.provider in OPENAI_WIRE_PROVIDERS:
                try:
                    result = self._stream_openai(messages, mdl, temperature, wire_max_tokens, tools, _visible_callback,
                                                  thinking_callback=thinking_callback,
                                                  call_user_id=call_user_id or "",
                                                  call_conversation_id=call_conversation_id or "")
                except Exception as exc:
                    base_url = self.base_url or ""
                    err = f"{type(exc).__name__}: {exc}"
                    is_relay_proxy = "/relay-proxy/" in base_url
                    is_broken_pipe = self._is_broken_pipe_error(exc)
                    is_truncated_stream = (
                        isinstance(exc, LLMCallError)
                        and exc.category in TRUNCATED_STREAM_CATEGORIES)
                    if not ((is_relay_proxy and is_broken_pipe)
                            or is_truncated_stream):
                        raise
                    streamed_raw = ""
                    streamed_visible = ""
                    fallback_reason = exc.category if is_truncated_stream else "broken_pipe"
                    logger.warning(
                        "OpenAI streaming failed (%s); retrying the same request "
                        "without streaming model=%s base_url=%s error=%s",
                        fallback_reason, mdl,
                        self._redact_relay_proxy_url(base_url), err,
                    )
                    result = self._complete_openai(
                        messages, mdl, temperature, wire_max_tokens, None, tools,
                        call_user_id=call_user_id or "",
                        call_conversation_id=call_conversation_id or "",
                    )
                    if result.thinking and thinking_callback:
                        thinking_callback(result.thinking)
                    if result.content:
                        _visible_callback(result.content)
                    logger.info(
                        "OpenAI non-streaming fallback succeeded model=%s base_url=%s tokens_out=%s",
                        result.model or mdl, self._redact_relay_proxy_url(base_url), result.tokens_out,
                    )
            elif self.provider in RESPONSES_WIRE_PROVIDERS:
                result = self._stream_openai_responses(
                    messages, mdl, temperature, wire_max_tokens, tools, _visible_callback,
                    thinking_callback=thinking_callback,
                    call_user_id=call_user_id or "",
                    call_conversation_id=call_conversation_id or "")
            elif self.provider == "claude-code":
                result = self._stream_claude_code(messages, mdl, temperature, wire_max_tokens, tools, _visible_callback,
                                                  thinking_callback=thinking_callback,
                                                  turn_callback=_terminal_turn_callback,
                                                  block_callback=block_callback,
                                                  call_user_id=call_user_id,
                                                  call_conversation_id=call_conversation_id,
                                                  call_agent_name=call_agent_name,
                                                  call_event_cid=call_event_cid,
                                                  call_ephemeral_stream=call_ephemeral_stream)
            elif self.provider == "claude-code-interactive":
                result = self._stream_claude_code_interactive(
                    messages, mdl, temperature, wire_max_tokens, tools, _visible_callback,
                    thinking_callback=thinking_callback,
                    turn_callback=_terminal_turn_callback,
                    block_callback=block_callback,
                    call_user_id=call_user_id,
                    call_conversation_id=call_conversation_id,
                    call_agent_name=call_agent_name,
                    call_event_cid=call_event_cid,
                    call_ephemeral_stream=call_ephemeral_stream)
            elif self.provider == "antigravity-interactive":
                result = self._stream_antigravity_interactive(
                    messages, mdl, temperature, wire_max_tokens, tools, _visible_callback,
                    thinking_callback=thinking_callback,
                    turn_callback=_terminal_turn_callback,
                    block_callback=block_callback,
                    call_user_id=call_user_id,
                    call_conversation_id=call_conversation_id,
                    call_agent_name=call_agent_name,
                    call_event_cid=call_event_cid,
                    call_ephemeral_stream=call_ephemeral_stream)
            elif self.provider == "codex-app-server":
                result = self._stream_codex_app_server(messages, mdl, temperature, wire_max_tokens, tools, _visible_callback,
                                                       thinking_budget=thinking_budget,
                                                       thinking_callback=thinking_callback,
                                                       turn_callback=_terminal_turn_callback,
                                                       block_callback=block_callback,
                                                       call_user_id=call_user_id,
                                                       call_conversation_id=call_conversation_id,
                                                       call_agent_name=call_agent_name,
                                                       call_event_cid=call_event_cid,
                                                       call_ephemeral_stream=call_ephemeral_stream)
            elif self.provider == "codex-interactive":
                result = self._stream_codex_interactive(
                    messages, mdl, temperature, wire_max_tokens, tools, _visible_callback,
                    thinking_budget=thinking_budget,
                    thinking_callback=thinking_callback,
                    turn_callback=_terminal_turn_callback,
                    block_callback=block_callback,
                    call_user_id=call_user_id,
                    call_conversation_id=call_conversation_id,
                    call_agent_name=call_agent_name,
                    call_event_cid=call_event_cid,
                    call_ephemeral_stream=call_ephemeral_stream)
            elif self.provider == "gemini":
                result = self._stream_gemini(messages, mdl, temperature, wire_max_tokens, tools, _visible_callback,
                                               thinking_budget=thinking_budget,
                                               turn_callback=_terminal_turn_callback,
                                               block_callback=block_callback,
                                               call_user_id=call_user_id,
                                               call_conversation_id=call_conversation_id,
                                               call_agent_name=call_agent_name,
                                               call_event_cid=call_event_cid,
                                               call_ephemeral_stream=call_ephemeral_stream)
            elif self.provider == "anthropic":
                result = self._stream_anthropic(messages, mdl, temperature, wire_max_tokens, tools, _visible_callback, thinking_budget=thinking_budget, thinking_callback=thinking_callback,
                                                 call_user_id=call_user_id or "",
                                                 call_conversation_id=call_conversation_id or "")
            else:
                raise LLMClientError(f"Unknown provider '{self.provider}'")
            result.duration_ms = (time.time() - start) * 1000
            if result.tool_calls and callback and streamed_raw.startswith(streamed_visible):
                remainder = streamed_raw[len(streamed_visible):]
                if remainder:
                    callback(remainder)
                    streamed_visible = streamed_raw
            self._limit_final_content(result, max_tokens)
            self._record_response_context_usage(
                result,
                call_conversation_id=call_conversation_id or "",
                call_agent_name=call_agent_name or "",
                call_user_id=call_user_id or "",
                call_event_cid=call_event_cid or "",
            )
            if not result.tokens_in and messages:
                result.tokens_in = count_messages_tokens(messages)
            if not result.tokens_out and result.content:
                result.tokens_out = len(result.content) // 4
            self._report_tokens(result, messages)
            self._circuit_after_success(mdl)
            return result

        last_error = None
        overloaded_attempts = 0
        max_overloaded = 3
        for attempt in range(1, self.max_retries + 1):
            try:
                return _do_stream(model)
            except Exception as e:
                # Don't retry on cancellation, CC compact detection, or a
                # cold start the caller must rebuild the context for: this
                # loop would re-send the same delta to the same launch.
                from tasks.ai.agent_exceptions import AgentCancelled as _AC
                if isinstance(e, (_AC, AgentSuperseded, CCCompactDetected, ColdStartRequired,
                                  DeltaContextRequired)):
                    raise
                if self.provider in INTERACTIVE_CLI_PROVIDERS:
                    # Same rule as complete(): an interactive CLI turn is never
                    # re-run from here. A StopFailure (e.g. an upstream 429)
                    # matched the 429 branch below and re-pasted the prompt
                    # into the live tmux, leaving the agent "working" while the
                    # CLI had already shown the error.
                    raise
                last_error = e
                err_str = str(e)

                if ((isinstance(e, LLMCallError) and not e.retryable)
                        or self._is_permanent_request_error(err_str)):
                    if isinstance(last_error, LLMClientError):
                        raise last_error
                    raise LLMClientError(str(last_error))

                # HTTP status codes matched as standalone tokens — plain
                # substring matching was catastrophic: a captured CC
                # container PID like "165500" or "1429xx" matched "500"/
                # "429" and the retry loop treated our own intentional
                # kills as transient upstream failures, spawning
                # concurrent compact/main CC replays that ate pool slots.
                is_429 = ((isinstance(e, LLMCallError)
                           and e.category in {"rate_limited", "quota_exhausted"})
                          or bool(re.search(r'\b429\b', err_str))
                          or "rate_limit" in err_str.lower())
                is_529 = bool(re.search(r'\b529\b', err_str)) or "overloaded" in err_str.lower()
                is_500 = (bool(re.search(r'\b500\b', err_str))
                           or "Internal server error" in err_str)
                is_compact_stall = "compact_stall" in err_str
                # Tool-result stall: PawFlow's watchdog killed CC because
                # it went idle mid-turn. Our own recovery action — transparent
                # to the user, always retry.
                is_tool_stall = "tool_stall" in err_str
                # Claude CLI stream exit with a non-retryable reason is OUR
                # own kill (compact_result delivered, user cancel, MCP
                # teardown). The provider already absorbed the intentional
                # exits where the payload was delivered; anything reaching
                # here is a real local failure, NOT a transient API issue.
                # Retrying it spawns another CC container on every attempt.
                _is_cc_our_exit = (
                    "Claude CLI stream exited" in err_str
                    and not is_compact_stall
                    and not is_tool_stall)
                # Match other HTTP codes and error markers as standalone
                # tokens too — same substring risk.
                _other_code_re = re.compile(
                    r'\b(503|502|reset|timeout|api_error|server_error)\b',
                    re.IGNORECASE)
                is_transport_drop = self._is_transient_transport_error(err_str)
                is_truncated_stream = (
                    isinstance(e, LLMCallError)
                    and e.category in TRUNCATED_STREAM_CATEGORIES)
                retryable = (
                    (is_429 or is_529 or is_500 or is_compact_stall
                     or is_tool_stall or is_transport_drop
                     or is_truncated_stream
                     or bool(_other_code_re.search(err_str)))
                    and not _is_cc_our_exit)

                if is_529:
                    overloaded_attempts += 1
                    if overloaded_attempts >= max_overloaded:
                        self._circuit_after_failure(model, err_str)
                        if self.fallback_model and self.fallback_model != model:
                            logger.warning(
                                "Overloaded (529): %d/%d attempts exhausted on '%s', trying fallback '%s'",
                                overloaded_attempts, max_overloaded, model, self.fallback_model,
                            )
                            try:
                                return _do_stream(self.fallback_model)
                            except Exception as fb_err:
                                logger.error("Fallback model '%s' also failed: %s", self.fallback_model, fb_err)
                        raise LLMClientError(
                            f"Overloaded (529) after {overloaded_attempts} attempts: {last_error}")

                if is_compact_stall or is_tool_stall:
                    # Stall kill by our own watchdog — CC went idle mid-turn
                    # (no assistant output after init, or no assistant after
                    # its tool_results landed). Retry immediately (no backoff):
                    # this is our own recovery action, not a transient upstream
                    # failure. Still bounded by max_retries so we eventually
                    # surface if CC is genuinely stuck.
                    _kind = "Compact" if is_compact_stall else "Tool-result"
                    logger.warning(
                        "[stream] %s stall detected — retrying immediately "
                        "(attempt %d/%d)", _kind, attempt, self.max_retries)
                    continue

                if is_truncated_stream and attempt < self.max_retries:
                    # The provider cut the stream. Anything it already streamed
                    # is half an answer that must not be prefixed onto the
                    # retry's output, so drop the visible accounting before
                    # re-asking. Retry immediately: this is a transport drop,
                    # not a rate limit, and backing off only widens the silence.
                    logger.warning(
                        "[stream] truncated stream (%s) after %d streamed chars "
                        "— retrying immediately (attempt %d/%d)",
                        e.category, len(streamed_raw), attempt, self.max_retries)
                    streamed_raw = ""
                    streamed_visible = ""
                    continue

                if retryable and attempt < self.max_retries:
                    # Prefer server-specified delay, fall back to exponential backoff with jitter
                    server_delay = (e.retry_after_seconds
                                    if isinstance(e, LLMCallError)
                                    and e.retry_after_seconds > 0
                                    else self._parse_retry_after(err_str))
                    base_delay = 2.0
                    exp_delay = base_delay * (2 ** (attempt - 1)) * (0.75 + random.random() * 0.5)  # nosec B311
                    wait = server_delay if server_delay != 2.0 else exp_delay
                    if is_429:
                        logger.warning(f"Rate limited (429), waiting {wait:.1f}s (attempt {attempt}/{self.max_retries})")
                    elif is_529:
                        logger.warning(f"Overloaded (529), attempt {overloaded_attempts}/{max_overloaded}, waiting {wait:.1f}s")
                    elif is_500:
                        logger.warning(f"Server error (500), waiting {wait:.1f}s (attempt {attempt}/{self.max_retries})")
                    else:
                        logger.warning(f"LLM stream attempt {attempt}/{self.max_retries} failed "
                                       f"({type(e).__name__}), retrying in {wait:.1f}s")
                    time.sleep(wait)
                    continue

                # Final attempt failed — try fallback model
                self._circuit_after_failure(model, err_str)
                if self.fallback_model and self.fallback_model != model:
                    logger.warning("Streaming '%s' failed, trying fallback '%s'",
                                   model, self.fallback_model)
                    try:
                        return _do_stream(self.fallback_model)
                    except Exception as fb_err:
                        logger.error("Fallback model '%s' also failed: %s", self.fallback_model, fb_err)
                raise LLMClientError(
                    f"LLM streaming failed after {attempt} attempt(s): "
                    f"{type(e).__name__}: {e or 'no details'}")

    def embed(
        self,
        texts: List[str],
        model: Optional[str] = None,
    ) -> List[List[float]]:
        """Call OpenAI /v1/embeddings API. Batches max 2048 texts per call.

        Only supported for OpenAI provider (Anthropic has no embeddings API).

        Args:
            texts: List of texts to embed.
            model: Model name (default: text-embedding-3-small).

        Returns:
            List of embedding vectors (one per input text).
        """
        if not self.api_key:
            raise LLMClientError("api_key is required")
        if self.provider != "openai":
            raise LLMClientError("Embeddings are only supported with OpenAI provider")

        model = model or "text-embedding-3-small"
        all_embeddings: List[List[float]] = []
        batch_size = 2048

        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            body = {"model": model, "input": batch}
            data = self._http_post(
                "/v1/embeddings",
                body,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
            # Sort by index to ensure order matches input
            emb_data = sorted(data.get("data", []), key=lambda x: x.get("index", 0))
            for item in emb_data:
                all_embeddings.append(item.get("embedding", []))

        return all_embeddings
