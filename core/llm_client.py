"""Shared LLM HTTP client — zero dependencies (stdlib only).

Used by:
- services/llm_connection.py (LLMConnectionService)
- engine/nifi_script_converter.py (Groovy→Python conversion)
- tasks/ai/agent_loop.py (Agent LLM loop with tool_use)
- Any future PawFlow feature needing LLM calls
"""

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Union

from core.llm_providers import (
    LLMCliSharedMixin,
    LLMOpenaiMixin,
    LLMAnthropicMixin,
    LLMClaudeCodeMixin,
    LLMGeminiCliMixin,
)

logger = logging.getLogger(__name__)


@dataclass
class LLMToolDefinition:
    """A tool definition sent to the LLM."""
    name: str
    description: str
    parameters: Dict[str, Any]  # JSON Schema for the tool's input


@dataclass
class LLMToolCall:
    """A tool call requested by the LLM."""
    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass
class LLMToolResult:
    """Result of executing a tool call, sent back to the LLM."""
    tool_call_id: str
    content: str


@dataclass
class LLMMessage:
    """A single message in a conversation.

    For tool_calls from the assistant: role="assistant", content may be empty,
    tool_calls contains the list of tool calls.
    For tool results: role="tool", content is the result text,
    tool_call_id identifies which call this responds to.

    Content can be:
    - str: plain text message
    - List[dict]: multi-part content (text + images), e.g.:
        [{"type": "text", "text": "Describe this image"},
         {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}]
    """
    role: str  # "system", "user", "assistant", "tool"
    content: Union[str, List[Dict[str, Any]]] = ""
    tool_calls: Optional[List[LLMToolCall]] = None
    tool_call_id: Optional[str] = None
    source: Optional[Dict[str, str]] = None  # {"type": "user"|"agent", "name": "...", "llm_service": "..."}
    msg_id: str = ""  # unique ID — auto-generated if empty
    display_only: bool = False  # True = visible in transcript, excluded from LLM context

    def __post_init__(self):
        if not self.msg_id:
            import uuid
            self.msg_id = uuid.uuid4().hex[:12]

    @property
    def text_content(self) -> str:
        """Extract text content regardless of content format."""
        if isinstance(self.content, str):
            return self.content
        if isinstance(self.content, list):
            return " ".join(
                p.get("text", "") for p in self.content if p.get("type") == "text"
            )
        return ""


@dataclass
class LLMResponse:
    """Response from an LLM API call."""
    content: str = ""
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    total_tokens: int = 0
    finish_reason: str = ""
    duration_ms: float = 0.0
    tool_calls: List[LLMToolCall] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    thinking: str = ""


class LLMClient(
    LLMCliSharedMixin,
    LLMOpenaiMixin,
    LLMAnthropicMixin,
    LLMClaudeCodeMixin,
    LLMGeminiCliMixin,
):
    """Standalone LLM HTTP client (no BaseService dependency).

    Supports OpenAI-compatible and Anthropic APIs via stdlib HTTP.

    Args:
        provider: "openai" or "anthropic"
        api_key: API key
        base_url: API base URL (optional, uses provider default)
        default_model: Default model name (optional)
        timeout: Request timeout in seconds
        max_retries: Number of retries on transient errors
    """

    PROVIDERS = ("openai", "anthropic", "claude-code", "gemini-cli")

    DEFAULT_URLS = {
        "openai": "https://api.openai.com",
        "anthropic": "https://api.anthropic.com",
    }

    DEFAULT_MODELS = {
        "openai": "gpt-4o-mini",
        "anthropic": "claude-sonnet-4-20250514",
        "claude-code": "sonnet",
        "gemini-cli": "gemini-2.5-flash",
    }

    # Regex for parsing <tool_call>...</tool_call> tags from claude-code responses
    TOOL_CALL_RE = re.compile(r'<tool_call>\s*(\{.*?\})\s*</tool_call>', re.DOTALL)

    def __init__(
        self,
        provider: str = "openai",
        api_key: str = "",
        base_url: str = "",
        default_model: str = "",
        timeout: int = 60,
        max_retries: int = 2,
        claude_binary: str = "claude",
        gemini_binary: str = "gemini",
        refresh_token: str = "",
        token_expires_at: float = 0.0,
        token_url: str = "",
        fallback_model: str = "",
    ):
        self.provider = provider
        self._api_key = api_key
        self._base_url = base_url
        self.default_model = default_model or self.DEFAULT_MODELS.get(provider, "")
        self.timeout = timeout
        self.max_retries = max_retries
        self.claude_binary = claude_binary
        self.gemini_binary = gemini_binary
        self.refresh_token = refresh_token
        self.token_expires_at = token_expires_at
        self.token_url = token_url or "https://console.anthropic.com/v1/oauth/token"
        self.fallback_model = fallback_model
        self._token_lock = threading.Lock() if refresh_token else None
        # Token tracking callback — set by LLMConnectionService
        self._on_tokens = None  # callable(tokens_in, tokens_out, model)
        # Abort signal — set from another thread to cancel the current LLM call
        self._abort = threading.Event()
        # If created via from_config, _config_ref holds the lazy dict
        self._config_ref = None  # set by from_config

    @property
    def api_key(self):
        if self._config_ref:
            return self._config_ref.get("api_key", "") or self._api_key
        return self._api_key

    @api_key.setter
    def api_key(self, val):
        self._api_key = val

    @property
    def base_url(self):
        if self._config_ref:
            v = self._config_ref.get("base_url", "")
            return v or self.DEFAULT_URLS.get(self.provider, "")
        return self._base_url or self.DEFAULT_URLS.get(self.provider, "")

    @staticmethod
    def _parse_retry_after(error_text: str) -> float:
        """Parse retry delay from error message. Returns seconds to wait (default 2.0)."""
        # "Please try again in 1.427s"
        m = re.search(r'try again in ([\d.]+)s', error_text, re.IGNORECASE)
        if m:
            return float(m.group(1)) + 0.1  # add small buffer
        # "Retry-After: 2" header style
        m = re.search(r'retry[- ]after:?\s*([\d.]+)', error_text, re.IGNORECASE)
        if m:
            return float(m.group(1)) + 0.1
        return 2.0  # default wait

    def _report_tokens(self, response, messages):
        """Report token usage via callback if set. Estimates if not returned by provider."""
        if not self._on_tokens:
            return
        tokens_in = response.tokens_in
        tokens_out = response.tokens_out
        # Estimate if provider didn't return counts
        if not tokens_in and messages:
            total_chars = sum(
                len(m.content) if isinstance(m.content, str)
                else sum(len(str(p)) for p in m.content) if isinstance(m.content, list)
                else 0 for m in messages
            )
            tokens_in = total_chars // 4
        if not tokens_out and response.content:
            tokens_out = len(response.content) // 4
        try:
            self._on_tokens(tokens_in, tokens_out, response.model or self.default_model)
        except Exception:
            pass

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> "LLMClient":
        """Create from a config dict (may be LazyResolveDict).

        The config ref is kept so api_key/base_url resolve lazily
        on every access — changes to global params/secrets take
        effect immediately.
        """
        client = cls(
            provider=config.get("provider", "openai"),
            api_key=config.get("api_key", ""),
            base_url=config.get("base_url", ""),
            default_model=config.get("default_model", ""),
            timeout=int(config.get("timeout", 60)),
            max_retries=int(config.get("max_retries", 2)),
            claude_binary=config.get("claude_binary", "claude"),
            gemini_binary=config.get("gemini_binary", "gemini"),
            refresh_token=config.get("refresh_token", ""),
            token_expires_at=float(config.get("token_expires_at", 0)),
            token_url=config.get("token_url", ""),
            fallback_model=config.get("fallback_model", ""),
        )
        client._config_ref = config
        # reasoning_effort for reasoning models (read from service config)
        _re = config.get("reasoning_effort", "")
        if _re:
            client._reasoning_effort = _re
        return client

    def complete(
        self,
        messages: List[LLMMessage],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 0,
        response_format: Optional[str] = None,
        tools: Optional[List[LLMToolDefinition]] = None,
        thinking_budget: int = 0,
    ) -> LLMResponse:
        """Send a completion request to the LLM.

        Args:
            messages: Conversation messages (supports tool_calls and tool results).
            model: Model name override.
            temperature: Sampling temperature.
            max_tokens: Max response tokens.
            response_format: "json" for JSON mode (OpenAI only).
            tools: Tool definitions for function calling / tool_use.

        Returns:
            LLMResponse with content and/or tool_calls populated.
        """
        if not self.api_key and self.provider not in ("claude-code", "gemini-cli"):
            raise LLMClientError("api_key is required")
        if self.provider not in self.PROVIDERS:
            raise LLMClientError(
                f"Unknown provider '{self.provider}'. Supported: {', '.join(self.PROVIDERS)}"
            )

        model = model or self.default_model

        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                start = time.time()
                if self.provider == "openai":
                    result = self._complete_openai(messages, model, temperature, max_tokens, response_format, tools)
                elif self.provider == "claude-code":
                    result = self._complete_claude_code(messages, model, temperature, max_tokens, tools)
                elif self.provider == "gemini-cli":
                    result = self._complete_gemini_cli(messages, model, temperature, max_tokens, tools)
                else:
                    result = self._complete_anthropic(messages, model, temperature, max_tokens, tools, thinking_budget=thinking_budget)
                result.duration_ms = (time.time() - start) * 1000
                # Estimate tokens if provider didn't return counts
                if not result.tokens_in and messages:
                    result.tokens_in = sum(
                        len(m.content) if isinstance(m.content, str) else
                        sum(len(str(p)) for p in m.content) if isinstance(m.content, list)
                        else 0 for m in messages) // 4
                if not result.tokens_out and result.content:
                    result.tokens_out = len(result.content) // 4
                self._report_tokens(result, messages)
                return result
            except LLMClientError as e:
                # Auto-retry on rate limit (429)
                if "429" in str(e) or "rate_limit" in str(e).lower():
                    wait = self._parse_retry_after(str(e))
                    if attempt < self.max_retries:
                        logger.warning(f"Rate limited, retrying in {wait:.1f}s (attempt {attempt}/{self.max_retries})")
                        time.sleep(wait)
                        continue
                raise
            except Exception as e:
                last_error = e
                # Check if transient/rate-limit
                wait = self._parse_retry_after(str(e))
                if attempt < self.max_retries:
                    delay = max(wait, attempt * 0.5)
                    logger.warning(f"LLM request attempt {attempt} failed: {e}, retrying in {delay:.1f}s...")
                    time.sleep(delay)
                else:
                    # All retries exhausted — try fallback model if configured
                    if self.fallback_model and self.fallback_model != model:
                        logger.warning(
                            "Primary model '%s' failed after %d attempts, trying fallback '%s'",
                            model, self.max_retries, self.fallback_model,
                        )
                        try:
                            start = time.time()
                            fb = self.fallback_model
                            if self.provider == "openai":
                                result = self._complete_openai(messages, fb, temperature, max_tokens, response_format, tools)
                            elif self.provider == "claude-code":
                                result = self._complete_claude_code(messages, fb, temperature, max_tokens, tools)
                            elif self.provider == "gemini-cli":
                                result = self._complete_gemini_cli(messages, fb, temperature, max_tokens, tools)
                            else:
                                result = self._complete_anthropic(messages, fb, temperature, max_tokens, tools, thinking_budget=thinking_budget)
                            result.duration_ms = (time.time() - start) * 1000
                            if not result.tokens_in and messages:
                                result.tokens_in = sum(
                                    len(m.content) if isinstance(m.content, str) else
                                    sum(len(str(p)) for p in m.content) if isinstance(m.content, list)
                                    else 0 for m in messages) // 4
                            if not result.tokens_out and result.content:
                                result.tokens_out = len(result.content) // 4
                            self._report_tokens(result, messages)
                            return result
                        except Exception as fallback_err:
                            logger.error("Fallback model '%s' also failed: %s", self.fallback_model, fallback_err)
                    raise LLMClientError(f"LLM request failed after {self.max_retries} attempts: {last_error}")

    def abort(self):
        """Signal the current LLM call to abort (thread-safe)."""
        self._abort.set()

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
    ) -> LLMResponse:
        """Streaming completion — calls callback(token: str) for each token.

        Also returns the full LLMResponse at the end.  If callback is None,
        behaves like complete() but uses the streaming API under the hood.

        turn_callback(text, tool_calls): called by multi-turn providers
        (claude-code) at the end of each internal turn. Allows the agent
        loop to persist intermediate messages.

        Supports both OpenAI and Anthropic streaming.
        """
        if not self.api_key and self.provider not in ("claude-code", "gemini-cli"):
            raise LLMClientError("api_key is required")

        model = model or self.default_model

        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                start = time.time()

                if self.provider == "openai":
                    result = self._stream_openai(messages, model, temperature, max_tokens, tools, callback,
                                                  thinking_callback=thinking_callback)
                elif self.provider == "claude-code":
                    result = self._stream_claude_code(messages, model, temperature, max_tokens, tools, callback,
                                                      turn_callback=turn_callback)
                elif self.provider == "gemini-cli":
                    result = self._stream_gemini_cli(messages, model, temperature, max_tokens, tools, callback)
                elif self.provider == "anthropic":
                    result = self._stream_anthropic(messages, model, temperature, max_tokens, tools, callback, thinking_budget=thinking_budget, thinking_callback=thinking_callback)
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
                return result
            except Exception as e:
                # Don't retry on cancellation
                from tasks.ai.agent_exceptions import AgentCancelled as _AC
                if isinstance(e, _AC):
                    raise
                last_error = e
                err_str = str(e)
                # Retry on transient errors (429, 503, 502, connection reset)
                retryable = any(code in err_str for code in ("429", "503", "502", "reset", "timeout"))
                if retryable and attempt < self.max_retries:
                    wait = 2 ** attempt  # exponential backoff
                    if "429" in err_str:
                        wait = self._parse_retry_after(err_str)
                    logger.warning(f"LLM stream attempt {attempt}/{self.max_retries} failed "
                                   f"({type(e).__name__}), retrying in {wait:.0f}s")
                    time.sleep(wait)
                    continue
                # Final attempt failed — try fallback model
                if self.fallback_model and self.fallback_model != model:
                    logger.warning("Streaming '%s' failed, trying fallback '%s'",
                                   model, self.fallback_model)
                    try:
                        model = self.fallback_model
                        continue  # retry with fallback model
                    except Exception:
                        pass
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


class LLMClientError(Exception):
    """Error from LLM client."""
    pass
