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
import threading
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Union

from core.llm_providers import (
    LLMCliSharedMixin,
    LLMOpenaiMixin,
    LLMAnthropicMixin,
    LLMClaudeCodeMixin,
    LLMClaudeCodeInteractiveMixin,
    LLMAntigravityInteractiveMixin,
    LLMCodexAppServerMixin,
    LLMGeminiMixin,
)

logger = logging.getLogger(__name__)

# Last-resort fallback — keep in sync with config/default_models.json,
# which is the shipped source of truth (editable without a release).
_BUILTIN_MODEL_DEFAULTS = {
    "openai": "gpt-5.5",
    "anthropic": "claude-fable-5",
    "claude-code": "best",
    "claude-code-interactive": "best",
    "antigravity-interactive": "gemini-3.5-flash",
    "codex-app-server": "gpt-5.5",
    "gemini": "gemini-3.1-pro",
}


def _load_default_models() -> Dict[str, str]:
    env_path = os.getenv("PAWFLOW_DEFAULT_MODELS_FILE", "")
    if env_path:
        candidates = [Path(env_path)]
    else:
        from core.paths import SYSTEM_DIR
        candidates = [
            # Runtime override (data dir, survives upgrades)
            SYSTEM_DIR / "default_models.json",
            # Shipped defaults (seeded into /app/config in Docker)
            Path(__file__).resolve().parents[1] / "config" / "default_models.json",
        ]
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        except Exception as exc:
            logger.warning("default models config unavailable at %s: %s", path, exc)
            continue
        if not isinstance(data, dict):
            logger.warning("default models config must be an object: %s", path)
            continue
        configured = {str(k): str(v) for k, v in data.items() if k and v}
        if configured:
            return configured
    logger.debug("no default models config found; using builtin defaults")
    return dict(_BUILTIN_MODEL_DEFAULTS)


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
    tool_origin: str = ""

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
    "find_files": "glob", "list_files": "glob",
    "cat": "read", "view": "read", "open": "read",
    "create_file": "write", "save": "write",
    "replace": "edit", "patch": "edit", "modify": "edit",
    "web_fetch": "fetch", "http": "fetch",
    # Image/vision aliases route to `see`; `view` stays -> read (text).
    "image": "see", "image_view": "see", "view_image": "see",
    # CC official legacy aliases
    "Task": "Agent", "Brief": "SendUserMessage",
    "KillShell": "TaskStop",
    "AgentOutputTool": "TaskOutput", "BashOutputTool": "TaskOutput",
}

_MCP_USE_TOOL_WRAPPERS = {
    "mcp__pawflow__use_tool", "mcp__pawflow__.use_tool",
    "mcp_pawflow_use_tool", "mcp_pawflow.use_tool",
    "pawflow.use_tool", "pawflow/use_tool", "use_tool",
}

_MCP_SCHEMA_WRAPPERS = {
    "mcp__pawflow__get_tool_schema", "mcp__pawflow__.get_tool_schema",
    "mcp_pawflow_get_tool_schema", "mcp_pawflow.get_tool_schema",
    "pawflow.get_tool_schema", "pawflow/get_tool_schema", "get_tool_schema",
}


def is_mcp_tool_call_name(name: str) -> bool:
    """Return True when a provider name denotes a PawFlow MCP call."""
    raw_name = str(name or "")
    return (raw_name == "call_mcp_tool"
            or raw_name.startswith("pawflow/")
            or raw_name in _MCP_USE_TOOL_WRAPPERS
            or raw_name in _MCP_SCHEMA_WRAPPERS)


def has_complete_mcp_tool_call(name: str, arguments: dict) -> bool:
    """Return False for MCP wrapper calls missing the inner tool name."""
    raw_name = str(name or "")
    if raw_name == "call_mcp_tool":
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except (ValueError, TypeError):
                return False
        if not isinstance(arguments, dict):
            return False
        return bool(arguments.get("ToolName") or arguments.get("toolName")
                    or arguments.get("tool_name"))
    if raw_name in _MCP_USE_TOOL_WRAPPERS:
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except (ValueError, TypeError):
                return False
        if not isinstance(arguments, dict):
            return False
        payload = arguments
        if ("tool_name" not in payload
                and isinstance(payload.get("parameters"), dict)):
            payload = payload["parameters"]
        return bool(payload.get("tool_name"))
    return True


def _decode_str_arg(value):
    """Decode a possibly-JSON-string tool argument via the canonical parser.

    Uses core.tool_json.parse_tool_arguments (autoclose + escape repair) so the
    display/persistence unwrap recovers arguments with the SAME logic as the
    execution path -- identical across every provider. Non-strings pass through;
    on a genuine decode failure the original value is kept (graceful degradation,
    exactly like the previous json.loads/except behavior).
    """
    if not isinstance(value, str):
        return value
    from core.tool_json import parse_tool_arguments, tool_argument_parse_error
    decoded = parse_tool_arguments(value, tool_name="use_tool", provider="unwrap")
    if isinstance(decoded, dict) and not tool_argument_parse_error(decoded):
        return decoded
    return value


def unwrap_mcp_tool(name: str, arguments: dict) -> tuple:
    """Unwrap wrapper tool names to the inner tool name + arguments.

    mcp__pawflow__use_tool({tool_name: X, arguments: Y}) → (X, Y)
    mcp__pawflow__.use_tool({tool_name: X, arguments: Y}) → (X, Y)
    mcp_pawflow_use_tool({tool_name: X, arguments: Y}) → (X, Y)
    use_tool({tool_name: X, arguments: Y}) → (X, Y)
    mcp__pawflow__get_tool_schema(...) → ("get_tool_schema", arguments)
    get_tool_schema(...) → ("get_tool_schema", arguments)
    anything_else → (name, arguments)

    Also resolves tool aliases (shell → bash, etc.) so display is correct.
    """
    if name == "call_mcp_tool":
        arguments = _decode_str_arg(arguments)
        if isinstance(arguments, dict):
            payload = arguments
            tool_name = str(
                payload.get("ToolName") or payload.get("toolName")
                or payload.get("tool_name") or name)
            tool_name = _TOOL_ALIASES.get(tool_name, tool_name)
            inner = (
                payload.get("Arguments") if "Arguments" in payload
                else payload.get("arguments", payload.get("Parameters", payload.get("parameters", {})))
            )
            return tool_name, _decode_str_arg(inner)
    if isinstance(name, str) and name.startswith("pawflow/"):
        name = name.split("/", 1)[1]
        if name not in _MCP_USE_TOOL_WRAPPERS and name not in _MCP_SCHEMA_WRAPPERS:
            return _TOOL_ALIASES.get(name, name), arguments
    if name in _MCP_USE_TOOL_WRAPPERS:
        # Arguments may arrive as a JSON string (some LLMs serialize it).
        arguments = _decode_str_arg(arguments)
        if isinstance(arguments, dict):
            payload = arguments
            if ("tool_name" not in payload and isinstance(payload.get("parameters"), dict)):
                payload = payload["parameters"]
            tool_name = payload.get("tool_name", name)
            tool_name = _TOOL_ALIASES.get(tool_name, tool_name)
            inner = payload.get(
                "arguments_json",
                payload.get("arguments", payload.get("parameters", payload)),
            )
            return tool_name, _decode_str_arg(inner)
    if isinstance(arguments, dict) and arguments.get("tool_name") == name:
        inner = arguments.get("arguments_json")
        if inner is not None:
            return _TOOL_ALIASES.get(name, name), _decode_str_arg(inner)
    if name in _MCP_SCHEMA_WRAPPERS:
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
    thinking_signature: str = ""  # Anthropic extended-thinking signature, when provided
    is_error: bool = False  # True = LLM error message (displayed as error in UI)
    timestamp: float = 0.0  # creation time (epoch seconds)
    seq: int = 0  # per-conversation monotonic — minted at creation from conversation_id's counter
    conversation_id: str = ""  # the conv this message belongs to (required at creation)

    def __post_init__(self):
        # A message exists only inside a conversation. conversation_id
        # is therefore required at every construction — no exception,
        # no "legacy path", no reconstructed-from-disk shortcut (the
        # caller that reads a jsonl knows the cid from the folder name
        # and must pass it). msg_id + timestamp are minted here so the
        # object is identifiable and time-ordered from the moment it
        # exists. seq is NOT stamped at creation — it is the on-disk
        # line index, assigned at write time by
        # ConversationStore._stamp_line under the conv lock. When a
        # message is loaded from disk, seq comes in with the line.
        if not self.conversation_id:
            raise ValueError(
                f"LLMMessage(role={self.role!r}) requires "
                f"conversation_id — a message has no existence "
                f"outside a conversation. Thread the cid from the "
                f"call site instead of leaving it empty.")
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


# Per-conversation on-disk seq counter.
#
# seq is the on-disk line index — assigned at WRITE time by
# ConversationStore._stamp_line, which reads+advances this counter
# under the per-conv lock. One counter per cid, bootstrapped from the
# store's hot metadata + transcript tail so monotony survives process
# restarts without scanning a long transcript in the append lock.
import threading as _threading
_msg_seq_persisted: Dict[str, int] = {}   # cid -> last seq written to disk
_msg_seq_lock = _threading.Lock()


def _bootstrap_seq_for(conversation_id: str) -> int:
    """Return the max seq already persisted for ``conversation_id``.

    The conversation store keeps `_meta_max_seq` in extras.json and can read
    the latest transcript row from the tail. That is enough for the next
    append because seq is monotonically increasing in disk order; scanning the
    entire transcript here would run under ConversationStore's append lock on
    the first post-restart write.
    """
    if not conversation_id:
        return 0
    try:
        from core.conversation_store import ConversationStore
        return ConversationStore.instance().peek_persisted_max_seq(conversation_id)
    except Exception:
        return 0


def _peek_persisted_seq(conversation_id: str) -> int:
    """Return the highest seq already written to disk for this conv.

    _stamp_line uses ``_peek + 1`` as the next line's seq, then calls
    _record_persisted_seq to advance the counter. Bootstraps from the
    transcript on first access so monotony holds across restarts.
    """
    if not conversation_id:
        return 0
    with _msg_seq_lock:
        cur = _msg_seq_persisted.get(conversation_id)
        if cur is not None:
            return cur
    bootstrapped = _bootstrap_seq_for(conversation_id)
    with _msg_seq_lock:
        cur = _msg_seq_persisted.get(conversation_id)
        if cur is None or bootstrapped > cur:
            cur = bootstrapped
            _msg_seq_persisted[conversation_id] = cur
        return cur


def _record_persisted_seq(conversation_id: str, seq: int) -> None:
    """Mark ``seq`` as the latest seq written to disk for this conv."""
    if not conversation_id or not isinstance(seq, int):
        return
    with _msg_seq_lock:
        cur = _msg_seq_persisted.get(conversation_id)
        if cur is None or seq > cur:
            _msg_seq_persisted[conversation_id] = seq


def _next_persisted_seq(conversation_id: str) -> int:
    """Reserve and return the next on-disk seq for a conversation.

    Disk bootstrap can be slow on large conversations, so it must never run
    while the process-wide seq lock is held. The caller still serializes this
    per conversation with ConversationStore's append lock.
    """
    if not conversation_id:
        return 1
    with _msg_seq_lock:
        cur = _msg_seq_persisted.get(conversation_id)
        if cur is not None:
            nxt = cur + 1
            _msg_seq_persisted[conversation_id] = nxt
            return nxt
    bootstrapped = _bootstrap_seq_for(conversation_id)
    with _msg_seq_lock:
        cur = _msg_seq_persisted.get(conversation_id)
        if cur is None or bootstrapped > cur:
            cur = bootstrapped
        nxt = cur + 1
        _msg_seq_persisted[conversation_id] = nxt
        return nxt


def _has_persisted_seq(conversation_id: str) -> bool:
    """True when this process already bootstrapped the persisted seq."""
    if not conversation_id:
        return False
    with _msg_seq_lock:
        return conversation_id in _msg_seq_persisted


def _seed_persisted_seq(conversation_id: str, seq: int) -> None:
    """Seed the persisted seq cache from a caller that already scanned disk."""
    if not conversation_id or not isinstance(seq, int):
        return
    with _msg_seq_lock:
        cur = _msg_seq_persisted.get(conversation_id)
        if cur is None or seq > cur:
            _msg_seq_persisted[conversation_id] = seq


def stamp_message(msg: Dict[str, Any],
                   conversation_id: str) -> Dict[str, Any]:
    """Set ts + msg_id on a message dict at CREATION time.

    ``conversation_id`` is required: a message only exists inside a
    conversation.

    Every non-system message MUST have ts + msg_id by the time it
    reaches the writer. seq is NOT stamped here — it is the on-disk
    line index, assigned at write time by
    ConversationStore._stamp_line under the conv lock.

    Producer rule: stamp msg_id + ts at the moment the message is
    conceptually created, NOT at enqueue. The creation timestamp is
    what drives sort order on disk (seq breaks ts ties).
    """
    if not conversation_id:
        raise ValueError(
            "stamp_message requires a non-empty conversation_id")
    import time as _time
    import uuid as _uuid
    if not (msg.get("ts") or msg.get("timestamp")):
        msg["ts"] = _time.time()
    if not msg.get("msg_id"):
        msg["msg_id"] = _uuid.uuid4().hex[:12]
    return msg


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
    thinking_signature: str = ""

class LLMClient(
    LLMCliSharedMixin,
    LLMOpenaiMixin,
    LLMAnthropicMixin,
    LLMClaudeCodeMixin,
    LLMClaudeCodeInteractiveMixin,
    LLMAntigravityInteractiveMixin,
    LLMCodexAppServerMixin,
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

    PROVIDERS = ("openai", "anthropic", "claude-code", "claude-code-interactive", "antigravity-interactive", "codex-app-server", "gemini")

    DEFAULT_URLS = {
        "openai": "https://api.openai.com",
        "anthropic": "https://api.anthropic.com",
    }

    DEFAULT_MODELS = _load_default_models()

    _LIVE_PREEMPT_SUPPORT = {
        "claude-code": True,
        "claude-code-interactive": True,
        "antigravity-interactive": True,
        "codex-app-server": True,
        "gemini": True,
    }

    _circuit_lock = threading.Lock()
    _circuit_state: Dict[str, Dict[str, Any]] = {}

    def __init__(self, provider: str = "openai", config: Dict[str, Any] = None):
        self.provider = provider
        self._config_ref = config or {}
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
        _active_key = getattr(self, '_active_api_key', None)
        if _active_key:
            clone._active_api_key = _active_key
        _max_ctx = getattr(self, '_max_context_size', 0)
        if _max_ctx:
            clone._max_context_size = _max_ctx
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
        # Relay-proxy format: http(s)://<relay_id>/<host>:<port>/path.
        # Transform to a PawFlow-exposed proxy URL with an ephemeral token.
        try:
            from core.relay_proxy_url import maybe_transform_relay_proxy_url
            _proxy = maybe_transform_relay_proxy_url(
                _raw, user_id=_uid, conv_id=_cid)
            if _proxy:
                return _proxy
        except Exception:
            logger.debug("exception suppressed", exc_info=True)
        return _raw

    @property
    def default_model(self):
        configured = self._cfg("default_model", "")
        if configured:
            return configured
        if self.provider in ("claude-code", "claude-code-interactive", "antigravity-interactive", "codex-app-server", "gemini"):
            return ""
        return self.DEFAULT_MODELS.get(self.provider, "")

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
        return "|".join((self.provider or "", self.base_url or "", model or ""))

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
        elif self.provider == "gemini":
            fn = getattr(self, "_gemini_send_user_message", None)
        else:
            return False
        if fn is None:
            return False
        if self.provider in ("claude-code-interactive", "antigravity-interactive", "codex-app-server"):
            return fn(text, attachments, **kwargs)
        return fn(text, attachments)

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

        model = model or self.default_model

        def _do_stream(mdl):
            self._circuit_before_call(mdl)
            start = time.time()
            if self.provider == "openai":
                result = self._stream_openai(messages, mdl, temperature, max_tokens, tools, callback,
                                              thinking_callback=thinking_callback,
                                              call_user_id=call_user_id or "",
                                              call_conversation_id=call_conversation_id or "")
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


class LLMClientError(Exception):
    """Error from LLM client."""
    pass


class CCCompactDetected(Exception):
    """Raised when Claude Code starts auto-compaction.

    The agent loop should intercept this, kill CC, run a PawFlow
    compaction instead, and relaunch CC with fresh context.
    """
    pass
