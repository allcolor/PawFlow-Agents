"""LLM Connection Service - Connector for AI inference providers.

Supports OpenAI, Anthropic, and any OpenAI-compatible API (Ollama, vLLM, LiteLLM, etc.)
Uses shared LLM client from core/llm_client.py (stdlib HTTP, zero dependencies).
"""

import logging
import threading
from typing import Dict, Any, List, Optional

from core.base_service import BaseService
from core import ServiceFactory, ServiceError
from core.llm_client import LLMClient, LLMMessage, LLMResponse, LLMClientError, LLMToolDefinition, LLMToolCall, LLMToolResult

logger = logging.getLogger(__name__)


class LLMConnectionService(BaseService):
    """Controller service for LLM API connections.

    Delegates to core.llm_client.LLMClient for actual HTTP calls.

    Config:
        provider: "openai" or "anthropic"
        api_key: API key (or env var reference)
        base_url: API base URL (override for self-hosted/compatible APIs)
        default_model: Default model to use
        timeout: Request timeout in seconds
        max_retries: Number of retries on transient errors
    """

    TYPE = "llmConnection"
    VERSION = "1.1.0"
    NAME = "LLM Connection Service"
    DESCRIPTION = "Connector for AI inference (OpenAI, Anthropic, Claude Code CLI, Gemini CLI, compatible APIs)"

    PROVIDERS = LLMClient.PROVIDERS

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._client = LLMClient.from_config(self.config)
        self.provider = self._client.provider
        self.api_key = self._client.api_key
        self.base_url = self._client.base_url
        self.default_model = self._client.default_model
        self.timeout = self._client.timeout
        self.max_retries = self._client.max_retries
        # Capacity management
        max_conc = int(self.config.get("max_concurrent", 0))
        self._semaphore = threading.Semaphore(max_conc) if max_conc > 0 else None
        self._max_concurrent = max_conc
        # Token tracking (at service level — tracks ALL calls through this service)
        self._total_tokens_in = 0
        self._total_tokens_out = 0
        self._call_count = 0
        # Wire tracking callback into the client
        self._client._on_tokens = self._on_client_tokens

    def _create_connection(self):
        """Validate config and return a marker (actual HTTP is per-request)."""
        if self.provider not in self.PROVIDERS:
            raise ServiceError(
                f"Unknown provider '{self.provider}'. "
                f"Supported: {', '.join(self.PROVIDERS)}"
            )
        if self.provider == "claude-code":
            import shutil
            binary = self.config.get("claude_binary", "claude")
            if not shutil.which(binary):
                logger.warning("Claude CLI binary '%s' not found in PATH", binary)
        elif self.provider == "gemini-cli":
            import shutil
            binary = self.config.get("gemini_binary", "gemini")
            if not shutil.which(binary):
                logger.warning("Gemini CLI binary '%s' not found in PATH", binary)
        else:
            # API-based providers need an api_key
            if not self.api_key:
                raise ServiceError("api_key is required")
        return {"provider": self.provider, "ready": True}

    def _close_connection(self):
        pass

    def complete(
        self,
        messages: List[LLMMessage],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 0,
        response_format: Optional[str] = None,
        tools: Optional[List[LLMToolDefinition]] = None,
    ) -> LLMResponse:
        """Send a completion request to the LLM."""
        self.ensure_connected()
        try:
            resp = self._client.complete(messages, model, temperature, max_tokens, response_format, tools)
            self._track_tokens(resp, messages)
            return resp
        except LLMClientError as e:
            raise ServiceError(str(e))

    def complete_stream(
        self,
        messages: List[LLMMessage],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 0,
        tools: Optional[List[LLMToolDefinition]] = None,
        callback=None,
    ) -> LLMResponse:
        """Streaming completion — delegates to LLMClient.complete_stream()."""
        self.ensure_connected()
        try:
            resp = self._client.complete_stream(messages, model, temperature, max_tokens, tools, callback)
            self._track_tokens(resp, messages)
            return resp
        except LLMClientError as e:
            raise ServiceError(str(e))

    def _on_client_tokens(self, tokens_in: int, tokens_out: int, model: str):
        """Callback from LLMClient — tracks every LLM call through this service."""
        self._total_tokens_in += tokens_in
        self._total_tokens_out += tokens_out
        self._call_count += 1

    def _track_tokens(self, response: LLMResponse, messages: List[LLMMessage]):
        """Track token usage at the service level."""
        tokens_in = response.tokens_in
        tokens_out = response.tokens_out

        # Estimate if provider didn't return token counts
        if not tokens_in and messages:
            # Rough estimate: ~4 chars per token
            total_chars = sum(len(m.content or "") if isinstance(m.content, str)
                              else sum(len(str(p)) for p in m.content) if isinstance(m.content, list)
                              else 0 for m in messages)
            tokens_in = total_chars // 4
        if not tokens_out and response.content:
            tokens_out = len(response.content) // 4

        if tokens_in or tokens_out:
            self._total_tokens_in += tokens_in
            self._total_tokens_out += tokens_out
            self._call_count += 1

    def get_token_stats(self) -> Dict[str, Any]:
        """Return token usage stats for this service instance."""
        return {
            "tokens_in": self._total_tokens_in,
            "tokens_out": self._total_tokens_out,
            "calls": self._call_count,
        }

    def get_client(self) -> LLMClient:
        """Return the underlying LLMClient instance."""
        return self._client

    def try_acquire(self) -> bool:
        """Non-blocking acquire of a concurrency slot. Returns True if acquired."""
        if self._semaphore is None:
            return True
        return self._semaphore.acquire(blocking=False)

    def release(self):
        """Release a concurrency slot."""
        if self._semaphore is not None:
            self._semaphore.release()

    def has_capacity(self) -> bool:
        """Check if a concurrency slot is available (non-destructive peek)."""
        if self._semaphore is None:
            return True
        # Acquire + immediate release to peek
        if self._semaphore.acquire(blocking=False):
            self._semaphore.release()
            return True
        return False

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "provider": {
                "type": "select",
                "required": True,
                "default": "openai",
                "options": list(self.PROVIDERS),
                "description": "LLM provider",
            },
            "api_key": {
                "type": "string",
                "required": {"provider": ["openai", "anthropic"]},
                "sensitive": True,
                "description": "API key for the provider",
                "show_when": {"provider": ["openai", "anthropic", "gemini-cli"]},
            },
            "base_url": {
                "type": "string",
                "required": {"provider": ["openai", "anthropic"]},
                "default": "",
                "description": "Base URL (override for self-hosted/compatible APIs)",
                "show_when": {"provider": ["openai", "anthropic"]},
            },
            "default_model": {
                "type": "string",
                "required": False,
                "default": "",
                "description": "Default model name",
            },
            "timeout": {
                "type": "integer",
                "required": False,
                "default": 60,
                "description": "Request timeout in seconds",
            },
            "max_retries": {
                "type": "integer",
                "required": False,
                "default": 2,
                "description": "Number of retries on transient errors",
                "show_when": {"provider": ["openai", "anthropic"]},
            },
            "max_context_size": {
                "type": "integer",
                "required": False,
                "default": 0,
                "description": "Context window size in tokens (0 = use model default). Used for automatic compaction.",
            },
            "max_concurrent": {
                "type": "integer",
                "required": False,
                "default": 0,
                "description": "Max concurrent requests (0 = unlimited)",
            },
            "cost_per_1m_input": {
                "type": "string",
                "required": False,
                "default": "0",
                "description": "Cost per 1M input tokens ($), e.g. 0.20",
            },
            "cost_per_1m_output": {
                "type": "string",
                "required": False,
                "default": "0",
                "description": "Cost per 1M output tokens ($), e.g. 0.50",
            },
            "claude_binary": {
                "type": "string",
                "required": {"provider": ["claude-code"]},
                "default": "claude",
                "description": "Path to Claude CLI binary (uses `claude login` auth)",
                "show_when": {"provider": ["claude-code"]},
            },
            "gemini_binary": {
                "type": "string",
                "required": {"provider": ["gemini-cli"]},
                "default": "gemini",
                "description": "Path to Gemini CLI binary",
                "show_when": {"provider": ["gemini-cli"]},
            },
        }


ServiceFactory.register(LLMConnectionService)
