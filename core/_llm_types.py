"""Data types, MCP tool-unwrap helpers, model defaults, and error classes for
the LLM client. Split out of llm_client.py so the client facade, the driver
mixin, and external importers can share them without a circular import.

Every public name here is re-exported from core.llm_client (invariant 1).
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

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



class LLMClientError(Exception):
    """Error from LLM client."""
    pass


class CCCompactDetected(Exception):
    """Raised when Claude Code starts auto-compaction.

    The agent loop should intercept this, kill CC, run a PawFlow
    compaction instead, and relaunch CC with fresh context.
    """
    pass

