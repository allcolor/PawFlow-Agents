"""Shared LLM HTTP client — zero dependencies (stdlib only).

Used by:
- services/llm_connection.py (LLMConnectionService)
- engine/nifi_script_converter.py (Groovy→Python conversion)
- tasks/ai/agent_loop.py (Agent LLM loop with tool_use)
- Any future PawFlow feature needing LLM calls
"""

import json
import logging
import os
import random
import re
import shutil
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Union

from core.llm_providers import (
    LLMCliSharedMixin,
    LLMOpenaiMixin,
    LLMAnthropicMixin,
    LLMClaudeCodeMixin,
)

logger = logging.getLogger(__name__)


def _find_cli_binary(name: str) -> str:
    """Auto-detect a CLI binary (claude) in known locations."""
    home = os.path.expanduser("~")
    if sys.platform == "win32":
        candidates = [
            os.path.join(home, ".local", "bin", f"{name}.exe"),
            os.path.join(home, "AppData", "Roaming", "npm", f"{name}.cmd"),
            os.path.join(home, "AppData", "Roaming", "npm", name),
            os.path.join(home, ".npm-global", "bin", f"{name}.cmd"),
        ]
    else:
        candidates = [
            os.path.join(home, ".local", "bin", name),
            os.path.join(home, ".npm-global", "bin", name),
            f"/usr/local/bin/{name}",
            f"/usr/bin/{name}",
        ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return shutil.which(name) or name


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
    timestamp: float = 0.0

    def __post_init__(self):
        if not self.timestamp:
            import time
            self.timestamp = time.time()


@dataclass
class LLMToolResult:
    """Result of executing a tool call, sent back to the LLM."""
    tool_call_id: str
    content: str


_TOOL_ALIASES = {
    # CC hallucinations (common LLM mistakes)
    "run_command": "bash", "shell": "bash", "execute": "bash",
    "run": "bash", "terminal": "bash", "exec": "bash",
    "search": "grep", "find_files": "glob", "list_files": "glob",
    "cat": "read", "view": "read", "open": "read",
    "create_file": "write", "save": "write",
    "replace": "edit", "patch": "edit", "modify": "edit",
    "web_fetch": "fetch", "http": "fetch",
    # CC official legacy aliases
    "Task": "Agent", "Brief": "SendUserMessage",
    "KillShell": "TaskStop",
    "AgentOutputTool": "TaskOutput", "BashOutputTool": "TaskOutput",
}


def unwrap_mcp_tool(name: str, arguments: dict) -> tuple:
    """Unwrap wrapper tool names to the inner tool name + arguments.

    mcp__pawflow__use_tool({tool_name: X, arguments: Y}) → (X, Y)
    use_tool({tool_name: X, arguments: Y}) → (X, Y)
    mcp__pawflow__get_tool_schema(...) → ("get_tool_schema", arguments)
    get_tool_schema(...) → ("get_tool_schema", arguments)
    anything_else → (name, arguments)

    Also resolves tool aliases (shell → bash, etc.) so display is correct.
    """
    if name in ("mcp__pawflow__use_tool", "use_tool"):
        # Arguments may arrive as a JSON string (some LLMs serialize it).
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except (ValueError, TypeError):
                pass
        if isinstance(arguments, dict):
            tool_name = arguments.get("tool_name", name)
            tool_name = _TOOL_ALIASES.get(tool_name, tool_name)
            inner = arguments.get("arguments", arguments)
            if isinstance(inner, str):
                try:
                    inner = json.loads(inner)
                except (ValueError, TypeError):
                    pass
            return tool_name, inner
    if name in ("mcp__pawflow__get_tool_schema", "get_tool_schema"):
        return "get_tool_schema", arguments
    return name, arguments


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
    thinking: str = ""  # LLM thinking/reasoning output (part of context, visible in transcript)
    is_error: bool = False  # True = LLM error message (displayed as error in UI)
    timestamp: float = 0.0  # creation time (epoch seconds)

    def __post_init__(self):
        if not self.msg_id:
            import uuid
            self.msg_id = uuid.uuid4().hex[:12]
        if not self.timestamp:
            import time
            self.timestamp = time.time()

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

    PROVIDERS = ("openai", "anthropic", "claude-code")

    DEFAULT_URLS = {
        "openai": "https://api.openai.com",
        "anthropic": "https://api.anthropic.com",
    }

    DEFAULT_MODELS = {
        "openai": "gpt-4o-mini",
        "anthropic": "claude-opus-4-6",
        "claude-code": "claude-opus-4-6",
    }

    # Regex for parsing <tool_call>...</tool_call> tags from claude-code responses
    TOOL_CALL_RE = re.compile(r'<tool_call>\s*(\{.*?\})\s*</tool_call>', re.DOTALL)

    def __init__(self, provider: str = "openai", config: Dict[str, Any] = None):
        self.provider = provider
        self._config_ref = config or {}
        # Token tracking callback — set by LLMConnectionService
        self._on_tokens = None
        # Abort signal — set from another thread to cancel the current LLM call
        self._abort = threading.Event()

    def _cfg(self, key: str, default: Any = "") -> Any:
        """Read a config value just-in-time (resolves expressions on every call)."""
        return self._config_ref.get(key, default) if self._config_ref else default

    @property
    def api_key(self):
        # Pool override: if LLMConnectionService set an active key, use it
        _active = getattr(self, '_active_api_key', None)
        if _active:
            return _active
        return self._cfg("api_key", "")

    @property
    def base_url(self):
        # Read the raw template (LazyResolveDict's auto-resolve doesn't have
        # conversation context — we must resolve manually with it).
        _raw_template = ""
        if self._config_ref:
            try:
                _raw_template = dict.__getitem__(self._config_ref, "base_url")
            except KeyError:
                _raw_template = ""
        _uid = getattr(self, "_user_id", "") or ""
        _cid = getattr(self, "_conversation_id", "") or ""
        if _raw_template and isinstance(_raw_template, str) and "${" in _raw_template:
            try:
                from core.expression import resolve_expression
                _raw = resolve_expression(_raw_template, owner=_uid, conversation_id=_cid)
            except Exception:
                _raw = _raw_template
        else:
            _raw = _raw_template or ""
        if not _raw:
            _raw = self.DEFAULT_URLS.get(self.provider, "")
        # Relay-proxy format: http(s)://<relay_id>:<host>:<port>/path
        # Transform to a PawFlow-exposed proxy URL with an ephemeral token.
        try:
            from core.llm_providers.claude_code_session import (
                _maybe_transform_relay_proxy_url,
            )
            _proxy = _maybe_transform_relay_proxy_url(_raw, user_id=_uid)
            if _proxy:
                return _proxy
        except Exception:
            pass
        return _raw

    @property
    def default_model(self):
        return self._cfg("default_model", "") or self.DEFAULT_MODELS.get(self.provider, "")

    @property
    def timeout(self):
        return int(self._cfg("timeout", 60))

    @property
    def max_retries(self):
        return int(self._cfg("max_retries", 5))

    @property
    def claude_binary(self):
        return _find_cli_binary("claude")



    @property
    def fallback_model(self):
        return self._cfg("fallback_model", "")

    @property
    def containerize(self):
        return bool(self._cfg("containerize", False))

    @property
    def docker_image(self):
        return self._cfg("docker_image", "pawflow-claude-code:latest")

    @property
    def docker_cpu_limit(self):
        return self._cfg("docker_cpu_limit", "2")

    @property
    def docker_memory_limit(self):
        return self._cfg("docker_memory_limit", "2g")

    @property
    def reasoning_effort(self):
        return self._cfg("reasoning_effort", "")

    @property
    def prompt_cache_key(self):
        return self._cfg("prompt_cache_key", "")

    @property
    def prompt_cache_retention(self):
        return self._cfg("prompt_cache_retention", "")

    @staticmethod
    def _parse_context_overflow(error_text: str) -> Optional[int]:
        """Parse context length overflow from error message.

        Returns the number of tokens to reduce max_tokens by, or None if
        the error is not a context overflow.

        Matches patterns like:
        - "input length and max_tokens exceed context limit"
        - "context length exceeded"
        - "maximum context length is 128000 tokens, however you requested 130000 tokens"
        - Anthropic: "prompt is too long: 130000 tokens > 128000 maximum"
        """
        err = error_text.lower()
        if not (("exceed" in err and "context" in err) or
                ("exceed" in err and "length" in err) or
                ("too long" in err and "token" in err) or
                ("max_tokens" in err and "exceed" in err)):
            return None

        # Try to parse overflow amount from various patterns
        # "requested X tokens ... maximum context length is Y"
        import re
        # Pattern: "requested N tokens" + "maximum ... is M tokens"
        m_req = re.search(r'requested\s+([\d,]+)\s*tokens', error_text, re.IGNORECASE)
        m_max = re.search(r'(?:maximum|limit|context)[^0-9]*([\d,]+)\s*tokens', error_text, re.IGNORECASE)
        if m_req and m_max:
            requested = int(m_req.group(1).replace(",", ""))
            maximum = int(m_max.group(1).replace(",", ""))
            if requested > maximum:
                return requested - maximum

        # Pattern: "N tokens > M maximum"
        m = re.search(r'([\d,]+)\s*tokens?\s*>\s*([\d,]+)', error_text, re.IGNORECASE)
        if m:
            used = int(m.group(1).replace(",", ""))
            limit = int(m.group(2).replace(",", ""))
            if used > limit:
                return used - limit

        # Can't parse exact overflow — return a conservative estimate
        return 4000

    @staticmethod
    def _parse_retry_after(error_text: str) -> float:
        """Parse retry delay from error message. Returns seconds to wait (default 2.0).

        Checks (in priority order):
        1. "Please try again in N.NNNs" from Anthropic error bodies
        2. "Retry-After: N" header value
        3. "anthropic-ratelimit-unified-reset" ISO timestamp
        4. Default 2.0s
        """
        # "Please try again in 1.427s"
        m = re.search(r'try again in ([\d.]+)s', error_text, re.IGNORECASE)
        if m:
            return float(m.group(1)) + 0.1  # add small buffer
        # "Retry-After: 2" header style
        m = re.search(r'retry[- ]after:?\s*([\d.]+)', error_text, re.IGNORECASE)
        if m:
            return float(m.group(1)) + 0.1
        # "anthropic-ratelimit-unified-reset: 2025-03-30T12:00:00Z" ISO timestamp
        m = re.search(r'anthropic-ratelimit-unified-reset:?\s*(\d{4}-\d{2}-\d{2}T[\d:.]+Z?)', error_text, re.IGNORECASE)
        if m:
            try:
                from datetime import datetime, timezone
                reset_time = datetime.fromisoformat(m.group(1).replace("Z", "+00:00"))
                now = datetime.now(timezone.utc)
                delta = (reset_time - now).total_seconds()
                if delta > 0:
                    return delta + 0.1
            except (ValueError, TypeError):
                pass
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

        All values resolve just-in-time via _cfg() on every access.
        """
        client = cls(provider=config.get("provider", "openai"), config=config)
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
        if not self.api_key and self.provider != "claude-code":
            raise LLMClientError("api_key is required")
        if self.provider not in self.PROVIDERS:
            raise LLMClientError(
                f"Unknown provider '{self.provider}'. Supported: {', '.join(self.PROVIDERS)}"
            )

        model = model or self.default_model

        def _do_complete(mdl):
            start = time.time()
            if self.provider == "openai":
                result = self._complete_openai(messages, mdl, temperature, max_tokens, response_format, tools)
            elif self.provider == "claude-code":
                # CC only has stream-json mode — complete() and stream()
                # share the same path; complete() simply doesn't pass a
                # streaming callback. The LLMResponse carries the final
                # text + tool_calls.
                result = self._stream_claude_code(
                    messages, mdl, temperature, max_tokens, tools,
                )
            else:
                result = self._complete_anthropic(messages, mdl, temperature, max_tokens, tools, thinking_budget=thinking_budget)
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

                is_429 = "429" in err_str or "rate_limit" in err_str.lower()
                is_529 = "529" in err_str or "overloaded" in err_str.lower()
                is_500 = "500" in err_str or "Internal server error" in err_str

                if is_529:
                    overloaded_attempts += 1
                    if overloaded_attempts >= max_overloaded:
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

                retryable = is_429 or is_529 or is_500 or any(
                    code in err_str for code in ("503", "502", "reset", "timeout",
                                                  "api_error", "server_error")
                )
                if retryable and attempt < self.max_retries:
                    server_delay = self._parse_retry_after(err_str)
                    base_delay = 2.0
                    exp_delay = base_delay * (2 ** (attempt - 1)) * (0.75 + random.random() * 0.5)
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
        if not self.api_key and self.provider != "claude-code":
            raise LLMClientError("api_key is required")

        model = model or self.default_model

        def _do_stream(mdl):
            start = time.time()
            if self.provider == "openai":
                result = self._stream_openai(messages, mdl, temperature, max_tokens, tools, callback,
                                              thinking_callback=thinking_callback)
            elif self.provider == "claude-code":
                result = self._stream_claude_code(messages, mdl, temperature, max_tokens, tools, callback,
                                                  turn_callback=turn_callback)
            elif self.provider == "anthropic":
                result = self._stream_anthropic(messages, mdl, temperature, max_tokens, tools, callback, thinking_budget=thinking_budget, thinking_callback=thinking_callback)
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

                is_429 = "429" in err_str or "rate_limit" in err_str.lower()
                is_529 = "529" in err_str or "overloaded" in err_str.lower()
                is_500 = "500" in err_str or "Internal server error" in err_str
                is_compact_stall = "compact_stall" in err_str
                retryable = is_429 or is_529 or is_500 or is_compact_stall or any(
                    code in err_str for code in ("503", "502", "reset", "timeout",
                                                  "api_error", "server_error")
                )

                if is_529:
                    overloaded_attempts += 1
                    if overloaded_attempts >= max_overloaded:
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

                if is_compact_stall:
                    # Compact stall: CC was killed after being unresponsive
                    # post-compaction. Retry immediately (no backoff needed,
                    # the session is already compacted).
                    logger.warning("[stream] Compact stall detected — retrying immediately")
                    continue

                if retryable and attempt < self.max_retries:
                    # Prefer server-specified delay, fall back to exponential backoff with jitter
                    server_delay = self._parse_retry_after(err_str)
                    base_delay = 2.0
                    exp_delay = base_delay * (2 ** (attempt - 1)) * (0.75 + random.random() * 0.5)
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


class LLMClientError(Exception):
    """Error from LLM client."""
    pass


class CCCompactDetected(Exception):
    """Raised when Claude Code starts auto-compaction.

    The agent loop should intercept this, kill CC, run a PawFlow
    compaction instead, and relaunch CC with fresh context.
    """
    pass
