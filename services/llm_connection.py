"""LLM Connection Service - Connector for AI inference providers.

Supports direct OpenAI/Anthropic APIs, OpenAI-compatible APIs, and CLI-backed
providers (Claude Code, Claude Code interactive, Antigravity interactive, Codex
app-server, Gemini CLI). Uses shared LLM client from core/llm_client.py.
"""

import logging
import threading
from typing import Dict, Any, List, Optional

from core.base_service import BaseService
from core import ServiceFactory, ServiceError
from core.llm_client import LLMClient, LLMMessage, LLMResponse, LLMClientError, LLMToolDefinition
from core.token_counter import count_messages_tokens

logger = logging.getLogger(__name__)


class LLMConnectionService(BaseService):
    """Controller service for LLM API connections.

    Delegates to core.llm_client.LLMClient for actual HTTP calls.

    Config:
        provider: one of LLMClient.PROVIDERS
        api_key: API key (or env var reference); optional for OAuth CLI providers
        credential_service_id: OAuth credential pool for CLI providers
        base_url: API base URL (override for compatible APIs where supported)
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
        # LLM calls are isolated per invocation. `max_concurrent` is kept in
        # the schema for stored service configs, but runtime service-level
        # throttling is deliberately disabled: a foreground agent, compact,
        # memory extraction, bucket build, or sub-agent must never wait behind
        # another call merely because they use the same provider service.
        self._semaphore = None
        self._max_concurrent = 0
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
    def timeout(self):
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
        if self.provider == "omniroute":
            from urllib.parse import urlparse
            from core.llm_providers.omniroute import auth_headers, request_headers
            parsed = urlparse(str(self.base_url or "").strip())
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ServiceError(
                    "omniroute requires an explicit HTTP(S) base_url")
            if not str(self.default_model or "").strip():
                raise ServiceError("omniroute requires default_model")
            try:
                auth_headers(
                    self.api_key, self.config.get("omniroute_auth_mode", ""))
                request_headers(self.config)
            except ValueError as exc:
                raise ServiceError(str(exc)) from exc
        # One credential rule for every provider. It used to fork on provider
        # family -- CLI providers could use an OAuth pool, everything else had
        # to carry an api_key -- but that is not a property of the provider:
        # an API endpoint can sit behind an identity provider, and a CLI can
        # point at an unauthenticated local gateway.
        from core.llm_auth_modes import validation_error
        _auth_problem = validation_error(self.provider, self.config)
        if _auth_problem:
            raise ServiceError(_auth_problem)
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

        default_temperature: numeric value to override, 0 or "none" to skip
        entirely (let the model use its own default), absent = use caller's
        value as-is.
        """
        # self.config is LazyResolveDict — resolves expressions on .get()
        cfg_temp = self.config.get("default_temperature")
        if cfg_temp is not None:
            cfg_temp_str = str(cfg_temp).strip().lower()
            if cfg_temp_str in ("none", "0", "0.0"):
                # 0 / "none" = don't send temperature at all, let the model
                # use its own default (some models reject temperature=0)
                temperature = None
            else:
                temperature = float(cfg_temp)
        cfg_max = self.config.get("default_max_tokens")
        if cfg_max is not None and int(cfg_max) > 0:
            max_tokens = int(cfg_max)
        return temperature, max_tokens, model

    @staticmethod
    def _apply_call_context(client: LLMClient, call_kwargs: Dict[str, Any]) -> None:
        """Attach per-call identity to an isolated client clone.

        LLMClient.base_url resolves relay-aware templates such as
        ``relay://fs_user_relay/localhost:11434/v1`` from instance fields. The
        service creates a fresh clone per call, so the clone must receive the
        same identity that is also passed as ``call_*`` kwargs to providers.
        """
        try:
            user_id = str(call_kwargs.get("call_user_id") or "")
            conversation_id = str(call_kwargs.get("call_conversation_id") or "")
            agent_name = str(call_kwargs.get("call_agent_name") or "")
            event_cid = str(call_kwargs.get("call_event_cid") or "")
            if user_id:
                client._user_id = user_id
            if conversation_id:
                client._conversation_id = conversation_id
            if agent_name:
                client._agent_name = agent_name
            if event_cid:
                client._event_cid = event_cid
        except Exception:
            logger.debug("LLM call context propagation failed", exc_info=True)

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
        messages = self._maybe_apply_vision_fallback(messages, call_kwargs)
        try:
            client = self.get_client()
            self._apply_call_context(client, call_kwargs)
            resp = client.complete(messages, model, temperature, max_tokens,
                                   response_format, tools,
                                   thinking_budget=thinking_budget,
                                   **call_kwargs)
            self._track_tokens(resp, messages)
            return resp
        except LLMClientError as e:
            raise ServiceError(str(e))

    def _maybe_apply_vision_fallback(self, messages, call_kwargs):
        """Replace image parts with vision-service descriptions when this
        connection has supports_vision=false and names a vision_llm_service.

        Runs on the service-level complete/complete_stream path so every
        provider (API and CLI-backed) benefits. Failures leave the
        messages untouched — providers then degrade images to text links
        as before.
        """
        try:
            client = self.get_client()
            sv = getattr(client, "supports_vision", True)
            target = str(self.config.get("vision_llm_service") or "").strip()
            logger.info(
                "[vision-fallback] check: supports_vision=%s vision_llm_service=%s "
                "images_in_msgs=%s",
                sv, target or "(none)",
                any(isinstance(getattr(m, "content", None), list) and any(
                    isinstance(p, dict) and p.get("type") in ("image_ref", "image_url", "image")
                    for p in m.content) for m in messages))
            if sv:
                return messages
            if not target:
                return messages
            from core.vision_describe import apply_vision_fallback
            return apply_vision_fallback(
                messages, target,
                source_service_id=str(self.config.get("_service_id", "") or ""),
                user_id=str(call_kwargs.get("call_user_id") or ""),
                conversation_id=str(call_kwargs.get("call_conversation_id") or ""),
                agent_name=str(call_kwargs.get("call_agent_name") or ""))
        except Exception:
            logger.warning("vision fallback pre-processing failed", exc_info=True)
            return messages

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
        messages = self._maybe_apply_vision_fallback(messages, call_kwargs)
        try:
            client = self.get_client()
            self._apply_call_context(client, call_kwargs)
            resp = client.complete_stream(
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

    def embed(self, texts: List[str], model: Optional[str] = None) -> List[List[float]]:
        """Embedding request through this LLM service.

        Supported by OpenAI-compatible services that expose `/v1/embeddings`.
        The optional `embedding_model` service parameter overrides the
        LLM default model for these calls.
        """
        self.ensure_connected()
        try:
            emb_model = model or str(self.config.get("embedding_model", "") or "")
            return self._client.embed(texts, model=emb_model or None)
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
            tokens_in = count_messages_tokens(messages)
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
        """Return an isolated LLMClient instance for one logical call.

        If this service has an api_keys_pool, set the active key based on
        pool_index (conversation affinity) or round-robin (new conv).
        """
        client = self._client.clone_for_call()
        pool = self._get_api_key_pool()
        if pool:
            if 0 <= pool_index < len(pool):
                idx = pool_index
            else:
                with LLMConnectionService._api_key_lock:
                    idx = LLMConnectionService._api_key_counter % len(pool)
                    LLMConnectionService._api_key_counter += 1
            client._active_api_key = pool[idx]
            client._active_pool_index = idx
        return client

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
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
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
                    f"llm_api_key_idx:{self.config.get('_service_id', '') or ''}",
                    idx)
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        return idx

    def try_acquire(self) -> bool:
        """LLM service calls are never capacity-gated at service level."""
        return True

    def release(self):
        return None

    def has_capacity(self) -> bool:
        """LLM service calls are always independently instantiable."""
        return True

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
            "auth_mode": {
                "type": "select", "default": "",
                "options": ["", "none", "api_key", "oauth"],
                "description": (
                    "How this service authenticates. Empty infers it from "
                    "what is filled in, which is how services behaved before "
                    "this field existed. "
                    "none: the endpoint takes no credential (a local gateway) "
                    "— an explicit choice, so a forgotten key cannot pass for "
                    "one. "
                    "api_key: use api_key or api_keys_pool. "
                    "oauth: use the credential pool named by "
                    "credential_service_id, refreshing the access token as "
                    "needed. Available for every provider, not just the CLI "
                    "ones."
                ),
            },
            "credential_service_id": {
                "type": "service_ref",
                "service_type": "llmCredentialOAuthProvider",
                "provider_field": "provider",
                "provider_aliases": {
                    "claude-code-interactive": "claude-code",
                    "antigravity-interactive": "gemini",
                    "codex-interactive": "codex-app-server",
                },
                "default": "",
                "description": (
                    "OAuth credential pool backing auth_mode=oauth. A pool "
                    "whose provider is 'generic' is accepted by any provider."
                ),
            },
            "base_url": {
                "type": "string", "default": "",
                "description": (
                    "Base URL (override for self-hosted/compatible APIs). "
                    "https://ollama.com/v1 serves Ollama cloud models with a "
                    "free-tier API key from https://ollama.com/settings/keys."
                ),
            },
            "tool_exposure": {
                "type": "select", "default": "api",
                "options": ["api", "full", "api_readonly", "full_readonly"],
                "description": (
                    "How tools are advertised to the model, for every agent "
                    "on this service unless the agent overrides it. "
                    "api: only get_tool_schema + use_tool, everything else "
                    "reached through them (default, smallest prompt). "
                    "full: every tool declared directly — no discovery round "
                    "trip, but every schema sits in the prompt, so keep it "
                    "for narrow tool sets. "
                    "api_readonly / full_readonly: the same two surfaces "
                    "restricted to read-only tools. Same modes as an MCP "
                    "publication."
                ),
            },
            "azure_deployment": {
                "type": "string", "default": "",
                "description": (
                    "Azure deployment name. Azure addresses a deployment, not "
                    "a model: the name you chose in the portal goes here. "
                    "Empty uses the model name, which works when they match."
                ),
            },
            "azure_api_version": {
                "type": "string", "default": "",
                "description": (
                    "Azure OpenAI api-version query parameter "
                    "(empty = provider default). Azure rejects requests "
                    "without one."
                ),
            },
            "omniroute_auth_mode": {
                "type": "select", "default": "bearer",
                "options": ["bearer", "none"],
                "description": "OmniRoute gateway authentication mode",
            },
            "omniroute_mode": {
                "type": "select", "default": "balanced",
                "options": ["balanced", "fast", "quality", "cheap", "reliable", "offline"],
                "description": "Per-request OmniRoute routing mode",
            },
            "omniroute_budget_usd": {
                "type": "float", "default": 0,
                "description": "Strict per-request gateway budget in USD (0 disables it)",
            },
            "omniroute_budget_fallback": {
                "type": "select", "default": "strict",
                "options": ["cheapest", "strict"],
                "description": "Gateway behavior when every route exceeds the budget",
            },
            "relay_local": {
                "type": "boolean", "default": True,
                "description": (
                    "For relay-routed base_url values, execute HTTP requests "
                    "on the relay host helper instead of inside the relay "
                    "Docker container. Disable only when the target service "
                    "runs in the relay container namespace."
                ),
            },
            "default_model": {
                "type": "string", "default": "",
                "description": "Default model name",
            },
            "embedding_model": {
                "type": "string", "default": "",
                "description": (
                    "Embedding model used when this service is selected by "
                    "the embedding_llm_service parameter. Empty uses the "
                    "client default embedding model."
                ),
            },
            "fallback_model": {
                "type": "string", "default": "",
                "description": "Fallback model (used when primary fails)",
            },
            "supports_vision": {
                "type": "boolean", "default": True,
                "description": "Send image attachments to this API provider as native vision input",
            },
            "vision_llm_service": {
                "type": "service_ref",
                "service_type": "llmConnection",
                "default": "",
                "description": (
                    "Vision-enabled llmConnection used to describe images when "
                    "supports_vision is false. Images sent to this model "
                    "(uploads, see/read/browser tool results) are replaced by a "
                    "detailed textual description with element coordinates, "
                    "cached per image."
                ),
            },
            "vision_max_tokens": {
                "type": "integer", "default": 1024,
                "description": (
                    "Max output tokens for each vision-fallback description. "
                    "Precise models like GPT-5.6 Luna are verbose and may need "
                    "2000-4000; cheap models (MiMo, Kimi) stay fast at 1024."
                ),
            },
            "vision_thinking_budget": {
                "type": "integer", "default": 0,
                "description": (
                    "Thinking budget for the vision model when describing images. "
                    "Reasoning models (gpt-5.x, o-series) narrate their process "
                    "('I need to...', 'I'll make sure...') which pollutes the "
                    "persisted description and the main prompt at every turn. "
                    "Set 0 for no requested thinking (models that reason by "
                    "default, like openai/responses, are better controlled via "
                    "reasoning_effort), or a small budget (e.g. 512) to cap it. "
                    "-1 disables thinking explicitly."
                ),
            },
            "timeout": {
                "type": "integer", "default": 0,
                "description": "Request timeout in seconds (0 = no timeout)",
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
                "type": "integer", "default": 25000,
                "description": (
                    "Absolute cap on compact output, in tokens. "
                    "Default 25000. Set 0 to use 25% of max_context_size. "
                    "Must be ≤ 40% of max_context_size (rejected at install time "
                    "otherwise) so the post-compact context still has room to grow."
                ),
            },
            "compact_threshold_pct": {
                "type": "integer", "default": 95,
                "description": (
                    "Proactive compact trigger, in percent of max_context_size. "
                    "PawFlow checks at the start of every agent iteration: if "
                    "the current messages-token count exceeds this threshold, "
                    "PawFlow compacts BEFORE the next LLM call. "
                    "Default 95. Set 0 for no proactive compact (defer to the underlying CLI's "
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
                "description": "Max consecutive calls to the same tool (0 = unlimited)",
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
            "reasoning_effort": {
                "type": "select", "default": "",
                "options": ["", "minimal", "low", "medium", "high", "xhigh", "max"],
                "description": (
                    "Reasoning effort for OpenAI and Anthropic reasoning models. "
                    "Empty omits the field and uses the model default."
                ),
            },
            "max_rounds": {
                "type": "integer", "default": 0,
                "description": "Max conversation rounds (0 = unlimited)",
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
            "subscription": {
                "type": "boolean", "default": False,
                "description": (
                    "Flat-rate/subscription service (e.g. Claude, Codex, or "
                    "Gemini subscription login): usage is recorded as VIRTUAL "
                    "cost at the cost_per_1m_* rates below (API-equivalent "
                    "pricing) instead of real spend — dashboards show what "
                    "the subscription saved, budgets ignore it."
                ),
            },
            "cost_per_1m_input": {
                "type": "string", "default": "0",
                "description": "Cost per 1M input tokens ($)",
            },
            "cost_per_1m_output": {
                "type": "string", "default": "0",
                "description": "Cost per 1M output tokens ($)",
            },
            "cost_per_1m_cache_read": {
                "type": "string", "default": "",
                "description": (
                    "Cost per 1M cache-hit input tokens ($). Empty uses "
                    "provider/default cache ratio from cost tracking."
                ),
            },
            "cost_per_1m_cache_write": {
                "type": "string", "default": "",
                "description": (
                    "Cost per 1M cache-creation input tokens ($). Empty uses "
                    "provider/default cache ratio from cost tracking."
                ),
            },
            "tool_result_max_chars": {
                "type": "integer", "default": 50000,
                "description": "Max chars for tool results in LLM context (0 = default 50000)",
            },
            "extra_body": {
                "type": "object", "default": {},
                "description": (
                    "Extra JSON object merged into OpenAI-compatible request bodies. "
                    "Use for OpenRouter routing/provider options such as provider, "
                    "transforms, route, or include_reasoning."
                ),
            },
            "store": {
                "type": "select", "default": "",
                "options": ["", "true", "false"],
                "description": (
                    "Responses API server-side storage (openai-responses). "
                    "Set 'false' for a Zero Data Retention org: PawFlow then "
                    "asks for encrypted reasoning items, without which a tool "
                    "loop fails with a 400 on the following turn. Empty sends "
                    "no store field at all."
                ),
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
            "cli_environment": {
                "type": "string", "required": False, "default": "",
                "multiline": True,
                "description": (
                    "CLI providers only: environment variables, one NAME=value "
                    "per line. Values support PawFlow expressions and are "
                    "resolved for the user/conversation when a CLI process starts."
                ),
            },
            "codex_config_toml": {
                "type": "string", "required": False, "default": "",
                "multiline": True,
                "description": (
                    "Codex providers only: additional config.toml merged "
                    "structurally with PawFlow's generated configuration."
                ),
            },
            "codex_models_json": {
                "type": "string", "required": False, "default": "",
                "multiline": True,
                "description": (
                    "Codex providers only: models.json catalog used by custom "
                    "model providers such as DeepSeek."
                ),
            },
            "effort": {
                "type": "select", "default": "medium",
                "options": ["low", "medium", "high", "max"],
                "description": "Claude Code effort level (thinking budget)",
            },
            "claude_plugins": {
                "type": "string", "required": False, "default": "",
                "description": "claude-code providers only: comma-separated "
                               "Claude Code plugin ids to enable in sessions "
                               "(plugin@marketplace form). Marketplaces are "
                               "declared in claude_marketplaces; Claude Code "
                               "auto-installs them on session start.",
            },
            "claude_marketplaces": {
                "type": "string", "required": False, "default": "",
                "description": "claude-code providers only: comma-separated "
                               "marketplace declarations, name=owner/repo "
                               "(GitHub) or name=<git-url>. Referenced by "
                               "claude_plugins entries.",
            },
            "codex_plugins": {
                "type": "string", "required": False, "default": "",
                "description": "Codex providers: comma-separated native "
                               "Codex plugin names to enable in sessions "
                               "(e.g. github,linear,gmail). Optionally "
                               "qualified as name@marketplace; default "
                               "marketplace is openai-curated. Plugins are "
                               "authorized at the ChatGPT account level — "
                               "OAuth credential mode only.",
            },
        }

    def get_parameter_rules(self) -> list:
        """Rules for conditional visibility, required, and defaults."""
        return [
            {
                # Every provider except the two Azure-only fields, which the
                # azure-openai rule below turns back on.
                "when": {"provider": ["openai", "openai-responses", "azure-openai",
                                     "copilot", "omniroute", "anthropic",
                                     "claude-code", "claude-code-interactive",
                                     "antigravity-interactive", "codex-app-server",
                                     "codex-interactive",
                                     "gemini"]},
                "set": {
                    "azure_deployment":  {"visible": False},
                    "azure_api_version": {"visible": False},
                    "omniroute_auth_mode": {"visible": False},
                    "omniroute_mode": {"visible": False},
                    "omniroute_budget_usd": {"visible": False},
                    "omniroute_budget_fallback": {"visible": False},
                }
            },
            {
                "when": {"provider": ["openai", "openai-responses", "azure-openai",
                                     "copilot", "omniroute", "anthropic"]},
                "set": {
                    "api_key":       {"visible": True, "required": True},
                    "credential_service_id": {"visible": False},
                    "base_url":      {"visible": True},
                    "relay_local":   {"visible": True},
                    "max_retries":   {"visible": True},
                    "fallback_model": {"visible": True},
                    "supports_vision": {"visible": True},
                    "vision_llm_service": {"visible": False},
                    "docker_image":  {"visible": False},
                    "docker_cpu_limit": {"visible": False},
                    "docker_memory_limit": {"visible": False},
                    "effort":        {"visible": False},
                    "reasoning_effort": {"visible": False},
                    "codex_plugins": {"visible": False},
                    "claude_plugins": {"visible": False},
                    "claude_marketplaces": {"visible": False},
                    "extra_body":    {"visible": False},
                    "store":         {"visible": False},
                    "cli_environment": {"visible": False},
                    "codex_config_toml": {"visible": False},
                    "codex_models_json": {"visible": False},
                }
            },
            {
                "when": {"provider": ["openai", "openai-responses", "azure-openai",
                                     "copilot"]},
                "set": {
                    "extra_body":    {"visible": True},
                }
            },
            {
                "when": {"provider": ["openai", "openai-responses"]},
                "set": {
                    "reasoning_effort": {"visible": True},
                }
            },
            {
                "when": {"provider": ["anthropic"]},
                "set": {
                    "reasoning_effort": {
                        "visible": True,
                        "options": ["", "low", "medium", "high", "xhigh", "max"],
                        "description": (
                            "Anthropic effort level (output_config.effort). Empty "
                            "uses the model default, currently high."
                        ),
                    },
                }
            },
            {
                # `store` is a Responses-API concept: chat/completions has no
                # server-side response objects to turn off.
                "when": {"provider": ["openai-responses"]},
                "set": {
                    "store":         {"visible": True},
                }
            },
            {
                "when": {"provider": ["azure-openai"]},
                "set": {
                    "api_key":       {"visible": True, "required": True,
                                      "description": "Azure OpenAI resource key (sent as the api-key header)"},
                    "base_url":      {"visible": True, "required": True,
                                      "description": "Your resource endpoint, e.g. https://my-resource.openai.azure.com"},
                    "azure_deployment":  {"visible": True},
                    "azure_api_version": {"visible": True},
                }
            },
            {
                "when": {"provider": ["copilot"]},
                "set": {
                    "api_key":       {"visible": True, "required": True,
                                      "description": "GitHub token from the device login below (exchanged for a Copilot token per session)"},
                    "base_url":      {"visible": True,
                                      "description": "Copilot chat endpoint (empty = api.githubcopilot.com)"},
                }
            },
            {
                "when": {"provider": ["omniroute"]},
                "set": {
                    "api_key": {"visible": True, "required": False,
                                "description": "Gateway bearer key; required only in bearer auth mode"},
                    "base_url": {"visible": True, "required": True,
                                 "description": "Explicit OmniRoute gateway URL, normally ending in /v1"},
                    "default_model": {"visible": True, "required": True,
                                      "description": "OmniRoute model or virtual route, for example auto"},
                    "omniroute_auth_mode": {"visible": True, "required": True},
                    "omniroute_mode": {"visible": True},
                    "omniroute_budget_usd": {"visible": True},
                    "omniroute_budget_fallback": {"visible": True},
                    "extra_body": {"visible": False},
                    "reasoning_effort": {"visible": False},
                }
            },
            {
                "when": {"provider": ["claude-code"]},
                "set": {
                    "api_key":       {"visible": True, "description": "Anthropic API key (empty = OAuth credential service)"},
                    "credential_service_id": {"visible": True},
                    "base_url":      {"visible": True, "description": "Anthropic-compatible endpoint (empty = api.anthropic.com)"},
                    "relay_local":   {"visible": True},
                    "max_retries":   {"visible": False},
                    "fallback_model": {"visible": False},
                    "supports_vision": {"visible": True},
                    "vision_llm_service": {"visible": False},
                    "max_concurrent": {"visible": False},
                    "timeout":       {"default": 0},
                    "docker_image":  {"visible": True},
                    "docker_cpu_limit": {"visible": True},
                    "docker_memory_limit": {"visible": True},
                    "effort":        {"visible": True},
                    "codex_plugins": {"visible": False},
                    "claude_plugins": {"visible": True},
                    "claude_marketplaces": {"visible": True},
                    "extra_body":    {"visible": False},
                    "cli_environment": {"visible": True},
                    "codex_config_toml": {"visible": False},
                    "codex_models_json": {"visible": False},
                }
            },
            {
                "when": {"provider": ["claude-code-interactive"]},
                "set": {
                    "api_key":       {"visible": True, "description": "Anthropic API key (empty = OAuth credential service)"},
                    "credential_service_id": {"visible": True},
                    "base_url":      {"visible": True, "description": "Anthropic-compatible endpoint for API-key mode (empty = provider default)"},
                    "relay_local":   {"visible": True},
                    "max_retries":   {"visible": False},
                    "fallback_model": {"visible": False},
                    "supports_vision": {"visible": True},
                    "vision_llm_service": {"visible": False},
                    "max_concurrent": {"visible": False},
                    "timeout":       {"default": 0},
                    "docker_image":  {"visible": True},
                    "docker_cpu_limit": {"visible": True},
                    "docker_memory_limit": {"visible": True},
                    "effort":        {"visible": True},
                    "codex_plugins": {"visible": False},
                    "claude_plugins": {"visible": True},
                    "claude_marketplaces": {"visible": True},
                    "extra_body":    {"visible": False},
                    "cli_environment": {"visible": True},
                    "codex_config_toml": {"visible": False},
                    "codex_models_json": {"visible": False},
                }
            },
            {
                "when": {"provider": ["antigravity-interactive"]},
                "set": {
                    "api_key":       {"visible": True, "description": "Google AI Studio key (empty = Gemini OAuth credential service)"},
                    "credential_service_id": {"visible": True},
                    "base_url":      {"visible": True, "description": "Gemini-compatible endpoint for API-key mode (empty = provider default)"},
                    "relay_local":   {"visible": True},
                    "max_retries":   {"visible": False},
                    "fallback_model": {"visible": False},
                    "supports_vision": {"visible": True},
                    "vision_llm_service": {"visible": False},
                    "max_concurrent": {"visible": False},
                    "timeout":       {"default": 0},
                    "docker_image":  {"visible": True},
                    "docker_cpu_limit": {"visible": True},
                    "docker_memory_limit": {"visible": True},
                    "effort":        {"visible": False},
                    "codex_plugins": {"visible": False},
                    "claude_plugins": {"visible": False},
                    "claude_marketplaces": {"visible": False},
                    "extra_body":    {"visible": False},
                    "cli_environment": {"visible": True},
                    "codex_config_toml": {"visible": False},
                    "codex_models_json": {"visible": False},
                }
            },
            {
                "when": {"provider": ["codex-app-server"]},
                "set": {
                    "api_key":       {"visible": True, "description": "OpenAI API key (empty = OAuth credential service)"},
                    "credential_service_id": {"visible": True},
                    "base_url":      {"visible": True, "description": "OpenAI-compatible endpoint for API-key mode (empty = provider default)"},
                    "relay_local":   {"visible": True},
                    "max_retries":   {"visible": False},
                    "fallback_model": {"visible": False},
                    "supports_vision": {"visible": True},
                    "vision_llm_service": {"visible": False},
                    "max_concurrent": {"visible": False},
                    "timeout":       {"default": 0},
                    "docker_image":  {"visible": True},
                    "docker_cpu_limit": {"visible": True},
                    "docker_memory_limit": {"visible": True},
                    "effort":        {"visible": True, "description": "Codex app-server reasoning effort (low/medium/high/xhigh/max)"},
                    "codex_plugins": {"visible": True},
                    "claude_plugins": {"visible": False},
                    "claude_marketplaces": {"visible": False},
                    "extra_body":    {"visible": False},
                    "cli_environment": {"visible": True},
                    "codex_config_toml": {"visible": True},
                    "codex_models_json": {"visible": True},
                }
            },
            {
                "when": {"provider": ["codex-interactive"]},
                "set": {
                    "api_key":       {"visible": True, "description": "OpenAI API key (empty = shared Codex OAuth credential service)"},
                    "credential_service_id": {"visible": True},
                    "base_url":      {
                        "visible": True, "required": False,
                        "description": (
                            "Optional Responses-compatible upstream for the "
                            "transparent MITM; empty keeps Codex's official "
                            "OAuth/API-key endpoint")},
                    "relay_local":   {"visible": True},
                    "max_retries":   {"visible": False},
                    "fallback_model": {"visible": False},
                    "supports_vision": {"visible": True},
                    "vision_llm_service": {"visible": False},
                    "max_concurrent": {"visible": False},
                    "timeout":       {"default": 0},
                    "docker_image":  {"visible": True},
                    "docker_cpu_limit": {"visible": True},
                    "docker_memory_limit": {"visible": True},
                    "effort":        {"visible": True, "description": "Codex TUI reasoning effort (low/medium/high/xhigh/max)"},
                    "codex_plugins": {"visible": True},
                    "claude_plugins": {"visible": False},
                    "claude_marketplaces": {"visible": False},
                    "extra_body":    {"visible": False},
                    "cli_environment": {"visible": True},
                    "codex_config_toml": {"visible": True},
                    "codex_models_json": {"visible": True},
                }
            },
            {
                "when": {"provider": ["gemini"]},
                "set": {
                    "api_key":       {"visible": True, "description": "Google AI Studio key (empty = OAuth credential service)"},
                    "credential_service_id": {"visible": True},
                    "base_url":      {"visible": True, "description": "Gemini-compatible endpoint for API-key mode (empty = provider default)"},
                    "relay_local":   {"visible": True},
                    "max_retries":   {"visible": False},
                    "fallback_model": {"visible": False},
                    "supports_vision": {"visible": True},
                    "vision_llm_service": {"visible": False},
                    "max_concurrent": {"visible": False},
                    "timeout":       {"default": 0},
                    "docker_image":  {"visible": True},
                    "docker_cpu_limit": {"visible": True},
                    "docker_memory_limit": {"visible": True},
                    "effort":        {"visible": False},
                    "codex_plugins": {"visible": False},
                    "claude_plugins": {"visible": False},
                    "claude_marketplaces": {"visible": False},
                    "extra_body":    {"visible": False},
                    "cli_environment": {"visible": True},
                    "codex_config_toml": {"visible": False},
                    "codex_models_json": {"visible": False},
                }
            },
            {
                # Evaluated last so it wins over the per-provider defaults:
                # the vision fallback picker appears as soon as native vision
                # is unchecked, for every provider (a CLI provider's base_url
                # can point at a non-vision model too).
                "when": {"supports_vision": ["false", False]},
                "set": {
                    "vision_llm_service": {"visible": True},
                    "vision_max_tokens": {"visible": True},
                    "vision_thinking_budget": {"visible": True},
                }
            },
        ]

    def get_service_actions(self) -> list:
        """CLI providers keep their logins in llmCredentialOAuthProvider.

        Copilot is the exception, and deliberately so: its device flow ends on
        a plain GitHub token that belongs in ``api_key``, not on a rotating
        credential pool with accounts and refresh tokens. Putting it here keeps
        the result where the user can see and edit it.
        """
        return [
            {
                "id": "omniroute_models_list",
                "label": "Refresh OmniRoute models",
                "icon": "",
                "when": {"provider": ["omniroute"]},
                "server_action": "omniroute_models_list",
                "flow": "simple",
            },
            {
                "id": "copilot_device_login",
                "label": "Sign in with GitHub",
                "icon": "",
                "when": {"provider": ["copilot"]},
                "server_action": "copilot_device_login",
                "flow": "device_code",
            },
        ]

    def list_omniroute_models(self) -> List[Dict[str, str]]:
        """Return bounded public model metadata from this OmniRoute gateway."""
        if self.provider != "omniroute":
            raise ServiceError("Model discovery is available only for omniroute")
        from core.llm_providers.omniroute import fetch_models
        return fetch_models(self.get_client())


ServiceFactory.register(LLMConnectionService)
