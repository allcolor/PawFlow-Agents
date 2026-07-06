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

from core._llm_types import (
    CCCompactDetected,
    LLMClientError,
    LLMMessage,
    LLMResponse,
    LLMToolDefinition,
)

logger = logging.getLogger(__name__)


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
    ) -> LLMResponse:
        """Send a completion request to the LLM.

        Args:
            messages: Conversation messages (supports tool_calls and tool results).
            model: Model name override.
            temperature: Sampling temperature.
            max_tokens: Max response tokens.
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
        if not self.api_key and self.provider not in ("claude-code", "claude-code-interactive", "antigravity-interactive", "codex-app-server", "gemini"):
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

        def _do_complete(mdl):
            self._circuit_before_call(mdl)
            start = time.time()
            if self.provider == "openai":
                result = self._complete_openai(messages, mdl, temperature, max_tokens, response_format, tools,
                                                call_user_id=call_user_id or "",
                                                call_conversation_id=call_conversation_id or "")
            elif self.provider == "claude-code":
                # CC only has stream-json mode — complete() and stream()
                # share the same path; complete() simply doesn't pass a
                # streaming callback. The LLMResponse carries the final
                # text + tool_calls.
                result = self._stream_claude_code(
                    messages, mdl, temperature, max_tokens, tools,
                    call_user_id=call_user_id,
                    call_conversation_id=call_conversation_id,
                    call_agent_name=call_agent_name,
                    call_event_cid=call_event_cid,
                    call_ephemeral_stream=call_ephemeral_stream,
                )
            elif self.provider == "claude-code-interactive":
                result = self._stream_claude_code_interactive(
                    messages, mdl, temperature, max_tokens, tools,
                    call_user_id=call_user_id,
                    call_conversation_id=call_conversation_id,
                    call_agent_name=call_agent_name,
                    call_event_cid=call_event_cid,
                    call_ephemeral_stream=call_ephemeral_stream,
                )
            elif self.provider == "antigravity-interactive":
                result = self._stream_antigravity_interactive(
                    messages, mdl, temperature, max_tokens, tools,
                    call_user_id=call_user_id,
                    call_conversation_id=call_conversation_id,
                    call_agent_name=call_agent_name,
                    call_event_cid=call_event_cid,
                    call_ephemeral_stream=call_ephemeral_stream,
                )
            elif self.provider == "codex-app-server":
                result = self._stream_codex_app_server(
                    messages, mdl, temperature, max_tokens, tools,
                    thinking_budget=thinking_budget,
                    call_user_id=call_user_id,
                    call_conversation_id=call_conversation_id,
                    call_agent_name=call_agent_name,
                    call_event_cid=call_event_cid,
                    call_ephemeral_stream=call_ephemeral_stream,
                )
            elif self.provider == "gemini":
                result = self._stream_gemini(
                    messages, mdl, temperature, max_tokens, tools,
                    call_user_id=call_user_id,
                    call_conversation_id=call_conversation_id,
                    call_agent_name=call_agent_name,
                    call_event_cid=call_event_cid,
                    call_ephemeral_stream=call_ephemeral_stream,
                )
            else:
                result = self._complete_anthropic(messages, mdl, temperature, max_tokens, tools, thinking_budget=thinking_budget,
                                                   call_user_id=call_user_id or "",
                                                   call_conversation_id=call_conversation_id or "")
            result.duration_ms = (time.time() - start) * 1000
            if not result.tokens_in and messages:
                result.tokens_in = sum(
                    len(m.content) if isinstance(m.content, str) else
                    sum(len(str(p)) for p in m.content) if isinstance(m.content, list)
                    else 0 for m in messages) // 4
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
                last_error = e
                err_str = str(e)

                # Context overflow auto-recovery: reduce max_tokens and retry once
                overflow = self._parse_context_overflow(err_str)
                if overflow is not None and max_tokens > 0:
                    safety_buffer = 1000
                    reduced = max_tokens - overflow - safety_buffer
                    if reduced > 0:
                        logger.warning(
                            "Context overflow detected (overflow=%d tokens). "
                            "Reducing max_tokens %d → %d and retrying.",
                            overflow, max_tokens, reduced,
                        )
                        max_tokens = reduced
                        try:
                            return _do_complete(model)
                        except Exception as retry_err:
                            logger.error("Context overflow retry also failed: %s", retry_err)
                            raise
                    else:
                        logger.error(
                            "Context overflow detected (overflow=%d) but reduced max_tokens "
                            "would be non-positive (%d). Cannot auto-recover.",
                            overflow, reduced,
                        )

                if self._is_permanent_request_error(err_str):
                    if isinstance(last_error, LLMClientError):
                        raise last_error
                    raise LLMClientError(str(last_error))

                # Match HTTP codes as standalone tokens — plain substring
                # matching fired false positives on captured CC PIDs like
                # 165500 / 1429xx, turning our own intentional kills into
                # retriable "500"/"429" errors.
                is_429 = bool(re.search(r'\b429\b', err_str)) or "rate_limit" in err_str.lower()
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
                retryable = (
                    (is_429 or is_529 or is_500 or is_transport_drop
                     or bool(_other_code_re.search(err_str)))
                    and not _is_cc_our_exit)
                if retryable and attempt < self.max_retries:
                    server_delay = self._parse_retry_after(err_str)
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
    ) -> LLMResponse:
        """Streaming completion — calls callback(token: str) for each token.

        Also returns the full LLMResponse at the end.  If callback is None,
        behaves like complete() but uses the streaming API under the hood.

        turn_callback(text, tool_calls): called by multi-turn providers
        (claude-code) at the end of each internal turn. Allows the agent
        loop to persist intermediate messages.

        Supports both OpenAI and Anthropic streaming.
        """
        if not self.api_key and self.provider not in ("claude-code", "claude-code-interactive", "antigravity-interactive", "codex-app-server", "gemini"):
            raise LLMClientError("api_key is required")

        self._apply_call_identity(
            call_user_id=call_user_id,
            call_conversation_id=call_conversation_id,
            call_agent_name=call_agent_name,
            call_event_cid=call_event_cid,
        )
        model = model or self.default_model

        def _do_stream(mdl):
            self._circuit_before_call(mdl)
            start = time.time()
            if self.provider == "openai":
                try:
                    result = self._stream_openai(messages, mdl, temperature, max_tokens, tools, callback,
                                                  thinking_callback=thinking_callback,
                                                  call_user_id=call_user_id or "",
                                                  call_conversation_id=call_conversation_id or "")
                except Exception as exc:
                    base_url = self.base_url or ""
                    err = f"{type(exc).__name__}: {exc}"
                    is_relay_proxy = "/relay-proxy/" in base_url
                    is_broken_pipe = self._is_broken_pipe_error(exc)
                    if not (is_relay_proxy and is_broken_pipe):
                        raise
                    logger.warning(
                        "OpenAI relay streaming failed with broken pipe; retrying non-streaming fallback "
                        "model=%s base_url=%s error=%s",
                        mdl, self._redact_relay_proxy_url(base_url), err,
                    )
                    result = self._complete_openai(
                        messages, mdl, temperature, max_tokens, None, tools,
                        call_user_id=call_user_id or "",
                        call_conversation_id=call_conversation_id or "",
                    )
                    if result.thinking and thinking_callback:
                        thinking_callback(result.thinking)
                    if result.content and callback:
                        callback(result.content)
                    logger.info(
                        "OpenAI relay non-streaming fallback succeeded model=%s base_url=%s tokens_out=%s",
                        result.model or mdl, self._redact_relay_proxy_url(base_url), result.tokens_out,
                    )
            elif self.provider == "claude-code":
                result = self._stream_claude_code(messages, mdl, temperature, max_tokens, tools, callback,
                                                  turn_callback=turn_callback,
                                                  block_callback=block_callback,
                                                  call_user_id=call_user_id,
                                                  call_conversation_id=call_conversation_id,
                                                  call_agent_name=call_agent_name,
                                                  call_event_cid=call_event_cid,
                                                  call_ephemeral_stream=call_ephemeral_stream)
            elif self.provider == "claude-code-interactive":
                result = self._stream_claude_code_interactive(
                    messages, mdl, temperature, max_tokens, tools, callback,
                    thinking_callback=thinking_callback,
                    turn_callback=turn_callback,
                    block_callback=block_callback,
                    call_user_id=call_user_id,
                    call_conversation_id=call_conversation_id,
                    call_agent_name=call_agent_name,
                    call_event_cid=call_event_cid,
                    call_ephemeral_stream=call_ephemeral_stream)
            elif self.provider == "antigravity-interactive":
                result = self._stream_antigravity_interactive(
                    messages, mdl, temperature, max_tokens, tools, callback,
                    thinking_callback=thinking_callback,
                    turn_callback=turn_callback,
                    block_callback=block_callback,
                    call_user_id=call_user_id,
                    call_conversation_id=call_conversation_id,
                    call_agent_name=call_agent_name,
                    call_event_cid=call_event_cid,
                    call_ephemeral_stream=call_ephemeral_stream)
            elif self.provider == "codex-app-server":
                result = self._stream_codex_app_server(messages, mdl, temperature, max_tokens, tools, callback,
                                                       thinking_budget=thinking_budget,
                                                       thinking_callback=thinking_callback,
                                                       turn_callback=turn_callback,
                                                       block_callback=block_callback,
                                                       call_user_id=call_user_id,
                                                       call_conversation_id=call_conversation_id,
                                                       call_agent_name=call_agent_name,
                                                       call_event_cid=call_event_cid,
                                                       call_ephemeral_stream=call_ephemeral_stream)
            elif self.provider == "gemini":
                result = self._stream_gemini(messages, mdl, temperature, max_tokens, tools, callback,
                                               thinking_budget=thinking_budget,
                                               turn_callback=turn_callback,
                                               block_callback=block_callback,
                                               call_user_id=call_user_id,
                                               call_conversation_id=call_conversation_id,
                                               call_agent_name=call_agent_name,
                                               call_event_cid=call_event_cid,
                                               call_ephemeral_stream=call_ephemeral_stream)
            elif self.provider == "anthropic":
                result = self._stream_anthropic(messages, mdl, temperature, max_tokens, tools, callback, thinking_budget=thinking_budget, thinking_callback=thinking_callback,
                                                 call_user_id=call_user_id or "",
                                                 call_conversation_id=call_conversation_id or "")
            else:
                raise LLMClientError(f"Unknown provider '{self.provider}'")
            result.duration_ms = (time.time() - start) * 1000
            if not result.tokens_in and messages:
                result.tokens_in = sum(
                    len(m.content) if isinstance(m.content, str) else
                    sum(len(str(p)) for p in m.content) if isinstance(m.content, list)
                    else 0 for m in messages) // 4
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
                # Don't retry on cancellation or CC compact detection
                from tasks.ai.agent_exceptions import AgentCancelled as _AC
                if isinstance(e, (_AC, CCCompactDetected)):
                    raise
                last_error = e
                err_str = str(e)

                # Context overflow auto-recovery: reduce max_tokens and retry once
                overflow = self._parse_context_overflow(err_str)
                if overflow is not None and max_tokens > 0:
                    safety_buffer = 1000
                    reduced = max_tokens - overflow - safety_buffer
                    if reduced > 0:
                        logger.warning(
                            "Context overflow detected in stream (overflow=%d tokens). "
                            "Reducing max_tokens %d → %d and retrying.",
                            overflow, max_tokens, reduced,
                        )
                        max_tokens = reduced
                        try:
                            return _do_stream(model)
                        except Exception as retry_err:
                            logger.error("Context overflow stream retry also failed: %s", retry_err)
                            raise
                    else:
                        logger.error(
                            "Context overflow in stream (overflow=%d) but reduced max_tokens "
                            "would be non-positive (%d). Cannot auto-recover.",
                            overflow, reduced,
                        )

                if self._is_permanent_request_error(err_str):
                    if isinstance(last_error, LLMClientError):
                        raise last_error
                    raise LLMClientError(str(last_error))

                # HTTP status codes matched as standalone tokens — plain
                # substring matching was catastrophic: a captured CC
                # container PID like "165500" or "1429xx" matched "500"/
                # "429" and the retry loop treated our own intentional
                # kills as transient upstream failures, spawning
                # concurrent compact/main CC replays that ate pool slots.
                is_429 = bool(re.search(r'\b429\b', err_str)) or "rate_limit" in err_str.lower()
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
                retryable = (
                    (is_429 or is_529 or is_500 or is_compact_stall
                     or is_tool_stall or is_transport_drop
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

                if retryable and attempt < self.max_retries:
                    # Prefer server-specified delay, fall back to exponential backoff with jitter
                    server_delay = self._parse_retry_after(err_str)
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


