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

    # Class-level round-robin counter for API key pools
    _api_key_counter = 0
    _api_key_lock = threading.Lock()

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._client = LLMClient.from_config(self.config)
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

    @property
    def provider(self) -> str:
        return self._client.provider

    @property
    def api_key(self) -> str:
        return self._client.api_key

    @property
    def base_url(self) -> str:
        return self._client.base_url

    @property
    def default_model(self) -> str:
        return self._client.default_model

    @property
    def timeout(self) -> int:
        return self._client.timeout

    @property
    def max_retries(self) -> int:
        return self._client.max_retries

    @property
    def fallback_model(self) -> str:
        return self._client.fallback_model

    def _create_connection(self):
        """Validate config and return a marker (actual HTTP is per-request)."""
        if self.provider not in self.PROVIDERS:
            raise ServiceError(
                f"Unknown provider '{self.provider}'. "
                f"Supported: {', '.join(self.PROVIDERS)}"
            )
        if self.provider in ("claude-code", "codex-app-server", "gemini"):
            # CLI providers — binary auto-detected at runtime, OAuth pool is
            # the default credential source, api_key is an optional fallback.
            pass
        else:
            # API-based providers (openai, anthropic) need an api_key.
            if not self.api_key:
                raise ServiceError("api_key is required")
        # compact_target_tokens > 40% of max_context_size leaves no room for
        # the post-compact context to grow before re-triggering compact, which
        # would loop. Reject at install time when both are set; if max_context
        # is 0 (model default) we can't validate here — runtime falls back to
        # the 25% formula and logs a warning.
        try:
            _ctt = int(self.config.get("compact_target_tokens", 0) or 0)
            _mcs = int(self.config.get("max_context_size", 0) or 0)
            _cthp = int(self.config.get("compact_threshold_pct", 0) or 0)
        except (TypeError, ValueError):
            _ctt, _mcs, _cthp = 0, 0, 0
        if _ctt > 0 and _mcs > 0 and _ctt > int(_mcs * 0.4):
            raise ServiceError(
                f"compact_target_tokens ({_ctt}) must be ≤ 40% of "
                f"max_context_size ({_mcs}) = {int(_mcs * 0.4)} — got "
                f"{_ctt / _mcs * 100:.1f}%."
            )
        if _cthp < 0 or _cthp > 100:
            raise ServiceError(
                f"compact_threshold_pct must be in [0, 100], got {_cthp}")
        return {"provider": self.provider, "ready": True}

    def _close_connection(self):
        pass

    def _apply_defaults(self, temperature, max_tokens, model):
        """Apply service-level defaults from config.

        default_temperature: numeric value to override, "none" to skip entirely,
        absent = use caller's value as-is.
        """
        # self.config is LazyResolveDict — resolves expressions on .get()
        cfg_temp = self.config.get("default_temperature")
        if cfg_temp is not None:
            if str(cfg_temp).strip().lower() == "none":
                temperature = None  # don't send temperature at all
            else:
                temperature = float(cfg_temp)
        cfg_max = self.config.get("default_max_tokens")
        if cfg_max is not None and int(cfg_max) > 0:
            max_tokens = int(cfg_max)
        return temperature, max_tokens, model

    def complete(
        self,
        messages: List[LLMMessage],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 0,
        response_format: Optional[str] = None,
        tools: Optional[List[LLMToolDefinition]] = None,
        thinking_budget: int = 0,
        **call_kwargs,
    ) -> LLMResponse:
        """Send a completion request to the LLM.

        Forwards `call_*` identity kwargs (call_user_id /
        call_conversation_id / call_agent_name / call_event_cid /
        call_ephemeral_stream) untouched to the underlying LLMClient
        — they're the per-call identity scope used by providers
        instead of mutating shared client state.
        """
        self.ensure_connected()
        temperature, max_tokens, model = self._apply_defaults(temperature, max_tokens, model)
        try:
            resp = self._client.complete(messages, model, temperature, max_tokens,
                                          response_format, tools,
                                          thinking_budget=thinking_budget,
                                          **call_kwargs)
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
        thinking_budget: int = 0,
        thinking_callback=None,
        turn_callback=None,
        block_callback=None,
        **call_kwargs,
    ) -> LLMResponse:
        """Streaming completion — delegates to LLMClient.complete_stream().

        Forwards `call_*` identity kwargs untouched to the underlying
        client (see complete docstring).
        """
        self.ensure_connected()
        temperature, max_tokens, model = self._apply_defaults(temperature, max_tokens, model)
        try:
            resp = self._client.complete_stream(
                messages, model, temperature, max_tokens, tools, callback,
                thinking_budget=thinking_budget,
                thinking_callback=thinking_callback,
                turn_callback=turn_callback,
                block_callback=block_callback,
                **call_kwargs)
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

    def get_client(self, pool_index: int = -1) -> LLMClient:
        """Return the underlying LLMClient instance.

        If this service has an api_keys_pool, set the active key based on
        pool_index (conversation affinity) or round-robin (new conv).
        """
        pool = self._get_api_key_pool()
        if pool:
            if 0 <= pool_index < len(pool):
                idx = pool_index
            else:
                with LLMConnectionService._api_key_lock:
                    idx = LLMConnectionService._api_key_counter % len(pool)
                    LLMConnectionService._api_key_counter += 1
            self._client._active_api_key = pool[idx]
            self._client._active_pool_index = idx
        return self._client

    def _get_api_key_pool(self) -> list:
        """Get the API key pool from config. Returns list of key strings."""
        raw = self.config.get("api_keys_pool", "")
        if not raw:
            return []
        if isinstance(raw, list):
            return [k for k in raw if k]
        if isinstance(raw, str):
            # Could be a resolved expression → JSON array or comma-separated
            raw = raw.strip()
            if raw.startswith("["):
                try:
                    import json as _json
                    return [k for k in _json.loads(raw) if k]
                except Exception:
                    pass
            return [k.strip() for k in raw.split(",") if k.strip()]
        return []

    def get_pool_size(self) -> int:
        """Return the number of API keys in the pool (0 = no pool, single key)."""
        return len(self._get_api_key_pool())

    def rotate_key(self, conversation_id: str = ""):
        """Force rotate to the next API key for a conversation."""
        pool = self._get_api_key_pool()
        if not pool:
            return -1
        with LLMConnectionService._api_key_lock:
            idx = LLMConnectionService._api_key_counter % len(pool)
            LLMConnectionService._api_key_counter += 1
        # Store in conversation extras if conv_id provided
        if conversation_id:
            try:
                from core.conversation_store import ConversationStore
                ConversationStore.instance().set_extra(
                    conversation_id,
                    f"llm_api_key_idx:{self._service_id}",
                    idx)
            except Exception:
                pass
        return idx

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
        """Parameters — no conditional logic here (rules handle that)."""
        return {
            "provider": {
                "type": "select", "required": True, "default": "openai",
                "options": list(self.PROVIDERS),
                "description": "LLM provider",
            },
            "api_key": {
                "type": "string", "sensitive": True,
                "description": "API key for the provider",
            },
            "base_url": {
                "type": "string", "default": "",
                "description": "Base URL (override for self-hosted/compatible APIs)",
            },
            "default_model": {
                "type": "string", "default": "",
                "description": "Default model name",
            },
            "fallback_model": {
                "type": "string", "default": "",
                "description": "Fallback model (used when primary fails)",
            },
            "timeout": {
                "type": "integer", "default": 180,
                "description": "Request timeout in seconds",
            },
            "max_retries": {
                "type": "integer", "default": 2,
                "description": "Number of retries on transient errors",
            },
            "max_context_size": {
                "type": "integer", "default": 0,
                "description": "Context window in tokens (0 = model default)",
            },
            "compact_target_tokens": {
                "type": "integer", "default": 0,
                "description": (
                    "Absolute cap on compact output, in tokens. "
                    "0 = use 25% of max_context_size (the legacy default). "
                    "Must be ≤ 40% of max_context_size (rejected at install time "
                    "otherwise) so the post-compact context still has room to grow."
                ),
            },
            "compact_threshold_pct": {
                "type": "integer", "default": 0,
                "description": (
                    "Proactive compact trigger, in percent of max_context_size. "
                    "PawFlow checks at the start of every agent iteration: if "
                    "the current messages-token count exceeds this threshold, "
                    "PawFlow compacts BEFORE the next LLM call. "
                    "0 = no proactive compact (defer to the underlying CLI's "
                    "mechanism, e.g. CC's compact_boundary). With CC + "
                    "threshold > 0, both triggers stay active — whichever "
                    "fires first wins. With codex/gemini the CLI never "
                    "auto-compacts, so threshold = 0 means no auto-compact at "
                    "all. Range [0, 100]."
                ),
            },
            "max_iterations": {
                "type": "integer", "default": 0,
                "description": "Max tool call iterations per turn (0 = default 1000)",
            },
            "temperature": {
                "type": "float", "default": 0,
                "description": "Sampling temperature (0 = default 0.7)",
            },
            "max_consecutive_tool_calls": {
                "type": "integer", "default": 0,
                "description": "Max consecutive calls to the same tool (0 = default 100)",
            },
            "resilience_style": {
                "type": "select", "default": "",
                "options": ["", "cautious", "balanced", "aggressive"],
                "description": "Tool call resilience (empty = default balanced)",
            },
            "thinking_budget": {
                "type": "integer", "default": 0,
                "description": "Thinking token budget for reasoning models (0 = auto, -1 = disabled)",
            },
            "max_rounds": {
                "type": "integer", "default": 0,
                "description": "Max conversation rounds (0 = default 1)",
            },
            "tool_result_max_chars": {
                "type": "integer", "default": 0,
                "description": "Max chars per tool result (0 = default 50000)",
            },
            "token_multiplier": {
                "type": "float", "default": 0,
                "description": (
                    "Correction factor between tiktoken cl100k_base counts "
                    "and this model's real tokenizer (0 = default 1.0). "
                    "Opus 4.7 ~1.6, Sonnet 4.6 / Haiku 4.5 ~1.1, OpenAI ~1.0. "
                    "Applied to bucket rollup threshold and post-compact "
                    "gauge so both reflect real context usage."
                ),
            },
            "max_concurrent": {
                "type": "integer", "default": 0,
                "description": "Max concurrent requests (0 = unlimited)",
            },
            "cost_per_1m_input": {
                "type": "string", "default": "0",
                "description": "Cost per 1M input tokens ($)",
            },
            "cost_per_1m_output": {
                "type": "string", "default": "0",
                "description": "Cost per 1M output tokens ($)",
            },
            "tool_result_max_chars": {
                "type": "integer", "default": 50000,
                "description": "Max chars for tool results in LLM context (0 = default 50000)",
            },
            "docker_image": {
                "type": "string", "default": "pawflow-claude-code:latest",
                "description": "Docker image for containerized execution",
            },
            "docker_cpu_limit": {
                "type": "string", "default": "2",
                "description": "CPU limit for container (e.g. '2' = 2 cores)",
            },
            "docker_memory_limit": {
                "type": "string", "default": "2g",
                "description": "Memory limit for container (e.g. '2g')",
            },
            "effort": {
                "type": "select", "default": "medium",
                "options": ["low", "medium", "high", "max"],
                "description": "Claude Code effort level (thinking budget)",
            },
        }

    def get_parameter_rules(self) -> list:
        """Rules for conditional visibility, required, and defaults."""
        return [
            {
                "when": {"provider": ["openai", "anthropic"]},
                "set": {
                    "api_key":       {"visible": True, "required": True},
                    "base_url":      {"visible": True},
                    "max_retries":   {"visible": True},
                    "fallback_model": {"visible": True},
                }
            },
            {
                "when": {"provider": ["claude-code"]},
                "set": {
                    "api_key":       {"visible": True, "description": "Anthropic API key (empty = OAuth login via claude CLI)"},
                    "base_url":      {"visible": True, "description": "Anthropic-compatible endpoint (empty = api.anthropic.com)"},
                    "max_retries":   {"visible": False},
                    "fallback_model": {"visible": False},
                    "max_concurrent": {"visible": False},
                    "timeout":       {"default": 600},
                    "docker_image":  {"visible": True},
                    "docker_cpu_limit": {"visible": True},
                    "docker_memory_limit": {"visible": True},
                    "effort":        {"visible": True},
                }
            },
            {
                "when": {"provider": ["codex-app-server"]},
                "set": {
                    "api_key":       {"visible": True, "description": "OpenAI API key (empty = OAuth login via codex CLI)"},
                    "base_url":      {"visible": False},
                    "max_retries":   {"visible": False},
                    "fallback_model": {"visible": False},
                    "max_concurrent": {"visible": False},
                    "timeout":       {"default": 600},
                    "docker_image":  {"visible": True},
                    "docker_cpu_limit": {"visible": True},
                    "docker_memory_limit": {"visible": True},
                    "effort":        {"visible": True, "description": "Codex app-server reasoning effort (low/medium/high/xhigh/max)"},
                }
            },
            {
                "when": {"provider": ["gemini"]},
                "set": {
                    "api_key":       {"visible": True, "description": "Google AI Studio key (empty = OAuth login via gemini CLI)"},
                    "base_url":      {"visible": False},
                    "max_retries":   {"visible": False},
                    "fallback_model": {"visible": False},
                    "max_concurrent": {"visible": False},
                    "timeout":       {"default": 600},
                    "docker_image":  {"visible": True},
                    "docker_cpu_limit": {"visible": True},
                    "docker_memory_limit": {"visible": True},
                    "effort":        {"visible": False},
                }
            },
        ]

    def get_service_actions(self) -> list:
        """Custom actions for the admin UI."""
        return [
            # Claude Code
            {
                "id": "claude_code_relay_login",
                "label": "Login via relay",
                "icon": "\U0001f50c",
                "when": {"provider": ["claude-code"]},
                "server_action": "claude_code_list_relays",
                "flow": "claude_login_relay",
            },
            {
                "id": "claude_code_server_login",
                "label": "Login via server",
                "icon": "\U0001f310",
                "when": {"provider": ["claude-code"]},
                "server_action": "claude_code_server_login",
                "flow": "claude_login_server",
            },
            {
                "id": "claude_code_login",
                "label": "Set credentials",
                "icon": "\U0001f511",
                "when": {"provider": ["claude-code"]},
                "server_action": "claude_code_login_url",
                "flow": "oauth_code",
            },
            # Codex app-server — same 3 flows, dedicated actions in service_flow.py
            {
                "id": "codex_relay_login",
                "label": "Login via relay",
                "icon": "\U0001f50c",
                "when": {"provider": ["codex-app-server"]},
                "server_action": "claude_code_list_relays",
                "flow": "codex_login_relay",
            },
            {
                "id": "codex_server_login",
                "label": "Login via server",
                "icon": "\U0001f310",
                "when": {"provider": ["codex-app-server"]},
                "server_action": "codex_server_login",
                "flow": "codex_login_server",
            },
            {
                "id": "codex_login",
                "label": "Set credentials",
                "icon": "\U0001f511",
                "when": {"provider": ["codex-app-server"]},
                "server_action": "codex_login_url",
                "flow": "oauth_code",
            },
            # Gemini CLI — same 3 flows, dedicated actions in service_flow.py
            {
                "id": "gemini_relay_login",
                "label": "Login via relay",
                "icon": "\U0001f50c",
                "when": {"provider": ["gemini"]},
                "server_action": "claude_code_list_relays",
                "flow": "gemini_login_relay",
            },
            {
                "id": "gemini_server_login",
                "label": "Login via server",
                "icon": "\U0001f310",
                "when": {"provider": ["gemini"]},
                "server_action": "gemini_server_login",
                "flow": "gemini_login_server",
            },
            {
                "id": "gemini_login",
                "label": "Set credentials",
                "icon": "\U0001f511",
                "when": {"provider": ["gemini"]},
                "server_action": "gemini_login_url",
                "flow": "oauth_code",
            },
        ]


ServiceFactory.register(LLMConnectionService)
