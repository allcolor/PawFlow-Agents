"""LLM Connection Service - Connector for AI inference providers.

Supports OpenAI, Anthropic, and any OpenAI-compatible API (Ollama, vLLM, LiteLLM, etc.)
Uses shared LLM client from core/llm_client.py (stdlib HTTP, zero dependencies).
"""

import logging
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
    DESCRIPTION = "Connector for AI inference (OpenAI, Anthropic, compatible APIs)"

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

    def _create_connection(self):
        """Validate config and return a marker (actual HTTP is per-request)."""
        if self.provider not in self.PROVIDERS:
            raise ServiceError(
                f"Unknown provider '{self.provider}'. "
                f"Supported: {', '.join(self.PROVIDERS)}"
            )
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
        max_tokens: int = 1024,
        response_format: Optional[str] = None,
        tools: Optional[List[LLMToolDefinition]] = None,
    ) -> LLMResponse:
        """Send a completion request to the LLM.

        Args:
            messages: Conversation messages.
            model: Model name override.
            temperature: Sampling temperature.
            max_tokens: Max response tokens.
            response_format: "json" for JSON mode.
            tools: Tool definitions for function calling / tool_use.
        """
        self.ensure_connected()
        try:
            return self._client.complete(messages, model, temperature, max_tokens, response_format, tools)
        except LLMClientError as e:
            raise ServiceError(str(e))

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "provider": {
                "type": "string",
                "required": True,
                "default": "openai",
                "description": "LLM provider: openai, anthropic",
                "allowed_values": list(self.PROVIDERS),
            },
            "api_key": {
                "type": "string",
                "required": True,
                "sensitive": True,
                "description": "API key for the provider",
            },
            "base_url": {
                "type": "string",
                "required": False,
                "default": "",
                "description": "Base URL (override for self-hosted/compatible APIs)",
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
            },
        }


ServiceFactory.register(LLMConnectionService)
