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
from typing import Dict, Any, Optional

from core.token_counter import count_messages_tokens
from core.llm_providers import (
    LLMCliSharedMixin,
    LLMOpenaiMixin,
    LLMOpenaiResponsesMixin,
    LLMAnthropicMixin,
    LLMClaudeCodeMixin,
    LLMClaudeCodeInteractiveMixin,
    LLMAntigravityInteractiveMixin,
    LLMCodexAppServerMixin,
    LLMCodexInteractiveMixin,
    LLMGeminiMixin,
)
from core._llm_types import (  # noqa: F401 -- re-exported for back-compat (invariant 1)
    AgentSuperseded,
    CCCompactDetected,
    ColdStartRequired,
    DeltaContextRequired,
    LLMCallError,
    LLMClientError,
    LLMMessage,
    LLMResponse,
    LLMToolCall,
    LLMToolDefinition,
    LLMToolResult,
    _BUILTIN_MODEL_DEFAULTS,
    _MCP_SCHEMA_WRAPPERS,
    _MCP_USE_TOOL_WRAPPERS,
    _TOOL_ALIASES,
    _decode_str_arg,
    _load_default_models,
    has_complete_mcp_tool_call,
    is_mcp_tool_call_name,
    unwrap_mcp_tool,
)
from core._llm_seq import (  # noqa: F401 -- re-exported for back-compat (invariant 1)
    stamp_message,
    _bootstrap_seq_for,
    _has_persisted_seq,
    _msg_seq_lock,
    _msg_seq_persisted,
    _next_persisted_seq,
    _peek_persisted_seq,
    _record_persisted_seq,
    _seed_persisted_seq,
)
from core._llm_client_driver import _LLMClientDriverMixin

logger = logging.getLogger(__name__)

class LLMClient(
    _LLMClientDriverMixin,
    LLMCliSharedMixin,
    LLMOpenaiMixin,
    LLMOpenaiResponsesMixin,
    LLMAnthropicMixin,
    LLMClaudeCodeMixin,
    LLMClaudeCodeInteractiveMixin,
    LLMAntigravityInteractiveMixin,
    LLMCodexAppServerMixin,
    LLMCodexInteractiveMixin,
    LLMGeminiMixin,
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

    PROVIDERS = ("openai", "openai-responses", "azure-openai", "copilot", "omniroute", "anthropic", "claude-code", "claude-code-interactive", "antigravity-interactive", "codex-app-server", "codex-interactive", "gemini")

    DEFAULT_URLS = {
        "openai": "https://api.openai.com",
        # Same host as chat/completions -- the Responses API is a different
        # endpoint on it, not a different service.
        "openai-responses": "https://api.openai.com",
        "anthropic": "https://api.anthropic.com",
        # Azure has no shared host: every resource has its own, so base_url is
        # required rather than defaulted.
        "copilot": "https://api.githubcopilot.com",
    }

    DEFAULT_MODELS = _load_default_models()

    _LIVE_PREEMPT_SUPPORT = {
        "claude-code": True,
        "claude-code-interactive": True,
        "antigravity-interactive": True,
        "codex-app-server": True,
        "codex-interactive": True,
        "gemini": True,
    }

    _circuit_lock = threading.Lock()
    _circuit_state: Dict[str, Dict[str, Any]] = {}

    def __init__(self, provider: str = "openai", config: Dict[str, Any] = None):
        self.provider = provider
        self._config_ref = config or {}
        # Bootstrap text is written to the session file and discarded, but its
        # token count must outlive whichever call clone created that file.
        # Active-context gauge reads use the resolver client, so all descendants
        # share this small authoritative per-stream state by reference.
        self._cli_bootstrap_tokens_by_stream = {}
        # Prompt size the provider itself reported on its last observed
        # exchange, per (conversation_id, agent_name). For an interactive CLI
        # this IS the context gauge: PawFlow cannot enumerate what is in that
        # window, but the proxy sees the number the provider counted. Shared
        # by reference with call clones, like the bootstrap counts above.
        self._cli_observed_context_tokens_by_stream = {}
        # How each native measurement may be advanced between observations.
        # Persistent CLI sessions already contain appended messages (session),
        # while stateless API measurements describe only the completed request
        # and may be advanced by PawFlow messages until the next response usage.
        self._observed_context_mode_by_stream = {}
        # A value can remain numerically identical across two requests.  The
        # revision still changes so live consumers can observe the new sample.
        self._observed_context_revision_by_stream = {}
        # The window those prompt sizes are measured against, per (conversation,
        # agent). The Responses API never reports it, so for Codex it is derived
        # from the TUI's own "context left N%" status bar (see
        # core.codex_interactive_pool.derive_context_window). Without it the
        # gauge and the auto-compact threshold divide by whatever
        # max_context_size happens to say. Shared by reference like the counts.
        self._cli_observed_context_window_by_stream = {}
        # Token tracking callback — set by LLMConnectionService
        self._on_tokens = None
        # Abort signal — set from another thread to cancel the current LLM call
        self._abort = threading.Event()
        self._active_http_conn = None

    def clone_for_call(self) -> "LLMClient":
        """Return a fresh LLMClient instance sharing this one's config but
        with NO per-stream state.

        Valid for every provider (claude-code, openai, anthropic):
        per-stream state lives on instance attributes and the clone
        starts with __init__ defaults. Each Claude Code stream owns its
        own Docker container and CLI subprocess; the Python orchestration
        state (`_claude_proc`, `_pool_container_name`, `_cc_container_pid`,
        `_current_pool_index`, `_current_session_id`, `_result_emitted`,
        `_compacting`, `_preempt_pending`, `_had_preempts_this_turn`,
        `_stderr_buffer`, …) MUST also be per-stream — otherwise a
        concurrent compact / memory-extract / btw / sub-agent stream
        clobbers the main agent's tracking via simple attribute writes
        on a shared singleton. OpenAI / Anthropic don't carry as much
        per-stream state but their `_cache_detector` and friends are
        also instance-scoped, so the clone gets a fresh one — exactly
        what an isolated one-shot call wants.

        Use this whenever a code path runs an isolated stream that
        should not see or affect the main agent's state. Compact,
        memory_extract, btw, and sub-agent delegate paths must each
        clone for their call.

        State propagated to the clone:
          * config (by reference — LazyResolveDict semantics).
          * CLI bootstrap token counts (by reference) so a provider call clone
            and the resolver client expose one authoritative gauge state.
          * `_on_tokens` callback so the owning service still receives
            usage updates from the clone's calls.
          * `_active_api_key` — required by api_keys_pool (LLMConnection
            Service sets this to pick a pool slot; the api_key property
            reads it first). Without propagation, a non-CC clone would
            fall through to config's flat `api_key` which is typically
            empty when a pool is configured → 401 on the first call.
          * `_max_context_size` — set by agent_executor for sub-agents
            so the CC provider can publish context-fill % via
            message_meta. Per-call but propagated for SSE accuracy.
        State explicitly NOT propagated:
          * Pool-tracking attrs, _claude_proc, session ids, result
            flags, preempt state, stderr buffer — these are exactly
            what we want fresh.
          * `_abort` — each clone has its own Event. Cancellation
            targeting the parent does not propagate to clones; the
            isolated streams have their own cancellation paths
            (compact_result kill, sub-agent task cancel, etc).
        """
        clone = self.__class__(provider=self.provider,
                                config=self._config_ref)
        clone._on_tokens = self._on_tokens
        clone._cli_bootstrap_tokens_by_stream = (
            self._cli_bootstrap_tokens_by_stream)
        clone._cli_observed_context_tokens_by_stream = (
            self._cli_observed_context_tokens_by_stream)
        clone._observed_context_mode_by_stream = (
            self._observed_context_mode_by_stream)
        clone._observed_context_revision_by_stream = (
            self._observed_context_revision_by_stream)
        clone._cli_observed_context_window_by_stream = (
            self._cli_observed_context_window_by_stream)
        _active_key = getattr(self, '_active_api_key', None)
        if _active_key:
            clone._active_api_key = _active_key
        _max_ctx = getattr(self, '_max_context_size', 0)
        if _max_ctx:
            clone._max_context_size = _max_ctx
        # Per-TURN state that must follow the call: the context phase leaves
        # this here when it emptied the message list for a live session, and
        # only the provider -- running on this clone -- can discover the
        # process is gone and refuse to launch with a delta. Dropping it here
        # silently turned that refusal into a no-op.
        _is_delta = getattr(self, '_pawflow_context_is_delta', False)
        if _is_delta:
            clone._pawflow_context_is_delta = True
        return clone

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

    def bearer_credential(self) -> str:
        """The credential to authenticate with, whatever the mode.

        One resolution point for every provider: a static key in `api_key`
        mode, a live access token from the credential pool in `oauth` mode,
        nothing in `none` mode. Providers keep deciding the *shape* of their
        auth header; only the value's source changes, so this never has to be
        taught about individual dialects.
        """
        from core.llm_auth_modes import NONE, OAUTH, resolve_mode

        config = self._config_ref or {}
        mode = resolve_mode(getattr(self, "provider", ""), config)
        if mode == NONE:
            return ""
        if mode != OAUTH:
            return self.api_key
        service_id = str(self._cfg("credential_service_id", "") or "").strip()
        if not service_id:
            return self.api_key
        try:
            from core.llm_oauth_credential import access_token
            from services.llm_credential_oauth import get_service_def
            sdef = get_service_def(
                service_id,
                user_id=getattr(self, "_user_id", "") or "",
                conv_id=getattr(self, "_conversation_id", "") or "")
            pool_config = (getattr(sdef, "config", {}) or {}) if sdef else {}
            endpoints = {
                "authorize_url": pool_config.get("authorize_url", ""),
                "token_url": pool_config.get("token_url", ""),
                "scope": pool_config.get("scope", ""),
            }
            if sdef is not None and not endpoints["token_url"]:
                # Resolve the identity-provider preset the same way the
                # credential service does, placeholders included.
                from core import ServiceFactory
                try:
                    service = ServiceFactory.create(sdef)
                    endpoints = service._generic_endpoints()
                except Exception:
                    logger.debug("[oauth-cred] preset resolution failed",
                                 exc_info=True)
            token = access_token(service_id, pool_config, endpoints)
        except Exception:
            logger.warning(
                "[oauth-cred] could not read the credential pool '%s'",
                service_id, exc_info=True)
            return self.api_key
        return token or self.api_key

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
        # Relay-proxy format: relay(s)://<relay_id>/<host>:<port>/path.
        # Legacy http(s)://<relay_id>/<host>:<port>/path remains supported.
        # Transform to a PawFlow-exposed proxy URL with an ephemeral token.
        try:
            from core.relay_proxy_url import maybe_transform_relay_proxy_url
            _relay_local_raw = self._cfg("relay_local", None)
            if _relay_local_raw is None or _relay_local_raw == "":
                _relay_local = None
            elif isinstance(_relay_local_raw, bool):
                _relay_local = _relay_local_raw
            else:
                _relay_local = str(_relay_local_raw).strip().lower() not in {
                    "0", "false", "no", "off"}
            _proxy = maybe_transform_relay_proxy_url(
                _raw, user_id=_uid, conv_id=_cid, relay_local=_relay_local)
            if _proxy:
                return _proxy
        except Exception:
            logger.debug("exception suppressed", exc_info=True)
        return _raw

    @property
    def default_model(self):
        return resolve_default_model(self.provider, self._config_ref)

    @property
    def supports_live_preempt(self) -> bool:
        raw = self._cfg("_supports_live_preempt", None)
        if raw is not None and raw != "":
            if isinstance(raw, bool):
                return raw
            return str(raw).strip().lower() not in {"0", "false", "no", "off"}
        return bool(self._LIVE_PREEMPT_SUPPORT.get(self.provider, False))

    @property
    def supports_vision(self) -> bool:
        raw = self._cfg("supports_vision", True)
        if isinstance(raw, bool):
            return raw
        if raw is None or raw == "":
            return True
        return str(raw).strip().lower() not in {"0", "false", "no", "off"}

    @property
    def timeout(self):
        raw = self._cfg("timeout", None)
        if raw in (None, "", 0, "0"):
            return None
        return int(raw)

    @property
    def idle_ttl_seconds(self):
        """Idle eviction TTL for warm-container pools, from the service config.

        ``timeout`` maps both "unset" and an explicit ``0`` to ``None``, so a
        pool reading it could not tell "no timeout configured -- never evict"
        from "nothing said -- use your own policy", and kept reaping live
        containers. This keeps them apart: ``None`` means unset, ``0`` means
        the service explicitly asked for no timeout.
        """
        raw = self._cfg("timeout", None)
        if raw is None or raw == "":
            return None
        return max(0, int(raw))

    @property
    def max_retries(self):
        return int(self._cfg("max_retries", 5))

    @property
    def fallback_model(self):
        return self._cfg("fallback_model", "")

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

    @property
    def extra_body(self) -> Dict[str, Any]:
        """Provider-specific OpenAI-compatible request body additions."""
        raw = self._cfg("extra_body", {})
        if raw in (None, ""):
            return {}
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("Ignoring invalid llm extra_body JSON string")
                return {}
        if not isinstance(raw, dict):
            logger.warning("Ignoring llm extra_body because it is not an object")
            return {}
        protected = {
            "api_key", "authorization", "messages", "model", "tools",
            "stream", "stream_options", "temperature", "max_tokens",
            "max_completion_tokens", "prompt_cache_key",
            "prompt_cache_retention",
        }
        result: Dict[str, Any] = {}
        for key, value in raw.items():
            if str(key).lower() in protected:
                logger.warning("Ignoring protected llm extra_body key: %s", key)
                continue
            result[key] = value
        return result

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
    def _is_permanent_request_error(error_text: str) -> bool:
        """Return True for auth/config errors that retries cannot fix."""
        text = error_text or ""
        lower = text.lower()
        if re.search(r'\b(400|401|403|404)\b', text):
            return True
        permanent_markers = (
            "unauthorized",
            "forbidden",
            "invalid api key",
            "invalid_api_key",
            "incorrect api key",
            "authentication_error",
            "permission_denied",
            "not found",
            "model_not_found",
            "invalid_request_error",
        )
        return any(marker in lower for marker in permanent_markers)

    @staticmethod
    def _is_circuit_breaker_error(error_text: str) -> bool:
        if LLMClient._is_permanent_request_error(error_text):
            return False
        return bool(re.search(
            r'\b(429|500|502|503|529)\b|rate_limit|overloaded|timeout|reset|api_error|server_error',
            error_text or "",
            re.IGNORECASE,
        ))

    def _circuit_key(self, model: str) -> str:
        base = ""
        if self._config_ref:
            try:
                base = str(dict.__getitem__(self._config_ref, "base_url") or "")
            except KeyError:
                base = ""
        return "|".join((self.provider or "", base, model or ""))

    def _circuit_threshold(self) -> int:
        return max(1, int(self._cfg("circuit_breaker_failures", 3) or 3))

    def _circuit_cooldown_s(self) -> float:
        return max(1.0, float(self._cfg("circuit_breaker_cooldown", 60) or 60))

    def _circuit_before_call(self, model: str) -> None:
        key = self._circuit_key(model)
        now = time.time()
        with self._circuit_lock:
            st = self._circuit_state.get(key)
            if not st:
                return
            open_until = float(st.get("open_until", 0) or 0)
            if open_until > now:
                remaining = int(open_until - now) + 1
                raise LLMClientError(
                    f"LLM circuit open for {self.provider}/{model}; retry in {remaining}s")
            if open_until and not st.get("half_open"):
                st["half_open"] = True
                logger.warning("LLM circuit half-open for %s/%s", self.provider, model)

    def _circuit_after_success(self, model: str) -> None:
        key = self._circuit_key(model)
        with self._circuit_lock:
            if key in self._circuit_state:
                logger.info("LLM circuit closed after successful call: %s/%s", self.provider, model)
            self._circuit_state.pop(key, None)

    def _circuit_after_failure(self, model: str, error_text: str) -> None:
        if not self._is_circuit_breaker_error(error_text):
            return
        key = self._circuit_key(model)
        with self._circuit_lock:
            st = self._circuit_state.setdefault(key, {"failures": 0, "open_until": 0.0, "half_open": False})
            st["failures"] = int(st.get("failures", 0) or 0) + 1
            if st.get("half_open") or st["failures"] >= self._circuit_threshold():
                st["open_until"] = time.time() + self._circuit_cooldown_s()
                st["half_open"] = False
                logger.warning(
                    "LLM circuit opened for %s/%s after %d failure(s)",
                    self.provider, model, st["failures"])

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

    @staticmethod
    def _is_transient_transport_error(error_text: str) -> bool:
        """Return True for provider transport drops that are safe to retry."""
        text = (error_text or "").lower()
        return any(marker in text for marker in (
            "responsestreamdisconnected",
            "stream disconnected before completion",
            "websocket closed by server",
            "connection closed before completion",
            "connection reset by peer",
        ))

    def _report_tokens(self, response, messages):
        """Report token usage via callback if set. Estimates if not returned by provider."""
        if not self._on_tokens:
            return
        tokens_in = response.tokens_in
        tokens_out = response.tokens_out
        # Estimate if provider didn't return counts
        if not tokens_in and messages:
            tokens_in = count_messages_tokens(messages)
        if not tokens_out and response.content:
            tokens_out = len(response.content) // 4
        try:
            self._on_tokens(tokens_in, tokens_out, response.model or self.default_model)
        except Exception:
            logger.debug("exception suppressed", exc_info=True)

    def send_user_message(self, text: str, attachments: list = None, **kwargs):
        """Provider-agnostic preempt entrypoint.

        Each provider's mixin defines its own `_<cli>_send_user_message`.
        CC writes on stdin, Codex app-server steers an active turn, and Gemini
        kills/retries. Without this dispatch, Python's MRO would resolve to
        whichever mixin happens to be listed first in `LLMClient`'s bases —
        the wrong implementation would run for another CLI provider.
        """
        if self.provider == "claude-code":
            fn = getattr(self, "_cc_send_user_message", None)
        elif self.provider == "claude-code-interactive":
            fn = getattr(self, "_cci_send_user_message", None)
        elif self.provider == "antigravity-interactive":
            fn = getattr(self, "_agi_send_user_message", None)
        elif self.provider == "codex-app-server":
            fn = getattr(self, "_codex_app_send_user_message", None)
        elif self.provider == "codex-interactive":
            fn = getattr(self, "_codex_interactive_send_user_message", None)
        elif self.provider == "gemini":
            fn = getattr(self, "_gemini_send_user_message", None)
        else:
            return False
        if fn is None:
            return False
        if self.provider in ("claude-code", "claude-code-interactive", "antigravity-interactive", "codex-app-server", "codex-interactive"):
            return fn(text, attachments, **kwargs)
        return fn(text, attachments)

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> "LLMClient":
        """Create from a config dict (may be LazyResolveDict).

        All values resolve just-in-time via _cfg() on every access.
        """
        client = cls(provider=config.get("provider", "openai"), config=config)
        return client


_PROVIDERS_WITHOUT_DEFAULT_MODEL = (
    "claude-code", "claude-code-interactive", "antigravity-interactive",
    "codex-app-server", "codex-interactive", "gemini")


def resolve_default_model(provider: str, config: Optional[Dict[str, Any]]) -> str:
    """Default model an LLMClient with this provider/config would report.

    The adaptive LLM router keys candidate health on (service, model). It must
    resolve the model from the stored definition through this same logic, or
    plan-time health reads miss the keys record_failure/record_success wrote
    with client.default_model.
    """
    configured = str((config or {}).get("default_model", "") or "")
    if configured:
        return configured
    if provider in _PROVIDERS_WITHOUT_DEFAULT_MODEL:
        return ""
    return LLMClient.DEFAULT_MODELS.get(provider, "")
