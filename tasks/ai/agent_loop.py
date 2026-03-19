"""AgentLoop Task — Composite task implementing an LLM agent with tool-use loop.

The agent receives a user message (from FlowFile content), calls the LLM with
tool definitions, executes tool calls, feeds results back, and loops until the
LLM produces a final text response (no more tool calls).

Flow pattern: httpReceiver → agentLoop → handleHTTPResponse

Config:
    provider: "openai" or "anthropic" (default: openai)
    api_key: API key (required)
    base_url: API base URL (optional)
    model: Model name (optional)
    system_prompt: System prompt for the agent
    temperature: Sampling temperature (default: 0.7)
    max_tokens: Max response tokens per LLM call (default: 4096)
    max_iterations: Max tool-use loop iterations (default: 200, safety limit)
    tools: JSON list of tool definitions (optional, overrides builtin)
    timeout: Request timeout in seconds (default: 120)
    conversation_attribute: If set, store conversation history in this attribute (JSON)

Output attributes:
    agent.iterations: Number of loop iterations
    agent.tools_called: Comma-separated list of tools called
    agent.model: Model used
    agent.tokens_in: Total input tokens
    agent.tokens_out: Total output tokens
    agent.duration_ms: Total duration
    agent.finish_reason: Final stop reason
"""

import json
import logging
import threading
import time
from typing import Dict, Any, List, Optional

from core.base_task import BaseTask
from core import FlowFile, TaskFactory
from core.llm_client import (
    LLMClient, LLMMessage, LLMResponse, LLMToolDefinition,
    LLMToolCall, LLMToolResult, LLMClientError,
)
from core.tool_registry import ToolRegistry, create_default_registry, load_agent_tools

logger = logging.getLogger(__name__)


class AgentCancelled(Exception):
    """Raised when agent generation is cancelled by user."""
    pass


class _InterruptComplete(Exception):
    """Internal: raised when interrupt-synthesis is done to break out of the loop."""
    pass


class AgentLoopTask(BaseTask):
    """LLM agent with tool-use loop.

    Loops: user message → LLM → tool_call → execute → LLM → ... → final text.
    """

    TYPE = "agentLoop"
    VERSION = "1.0.0"
    NAME = "Agent Loop"
    DESCRIPTION = "LLM agent with tool-use loop (function calling)"
    ICON = "ai"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._tool_registry: Optional[ToolRegistry] = None
        self._poller_started = False
        self._poller_stop = threading.Event()
        self._active_conversations: Dict[str, int] = {}  # conv_id -> refcount
        self._user_active_conversations: set = set()  # convs with active USER interaction
        self._active_thoughts: set = set()  # active thought keys (conv_id::thought::agent)
        self._active_lock = threading.Lock()
        # Track poll cooldown per conversation:
        # conv_id -> (updated_at_when_checked, recheck_after_timestamp)
        # Only re-poll if: user interacted (updated_at changed) OR recheck time passed
        self._poll_cooldown: Dict[str, tuple] = {}  # conv_id -> (last_updated_at, recheck_at)
        # Generation counter per conversation — prevents stale threads from overwriting
        # newer data.  Incremented on each user request; poller threads capture the
        # generation at start and skip saves if it changed.
        self._conv_generation: Dict[str, int] = {}
        self._conv_gen_lock = threading.Lock()
        # Interrupt signal — asks agent to conclude gracefully instead of cancelling
        self._conv_interrupt: Dict[str, bool] = {}
        self._interrupt_lock = threading.Lock()
        # Active interactions tracker — gen_key → metadata dict
        self._active_interactions: Dict[str, Dict] = {}
        self._interactions_lock = threading.Lock()
        # Context operation locks — prevents FlowFile processing during context mutations
        # conv_id -> threading.Event (set = free, cleared = blocked)
        self._context_op_events: Dict[str, threading.Event] = {}
        self._context_op_lock = threading.Lock()

    @staticmethod
    def _resolve_agent_name(name: str, conv_id: str) -> str:
        """Resolve a nickname or case-variant to the canonical real agent name.

        Resolution order:
        1. Check nickname map (reverse lookup: nick → real name)
        2. Check nickname map keys (case-insensitive: real name match)
        3. Return original name if no mapping found

        Always returns the real (canonical) agent name.
        """
        if not name or not conv_id:
            return name or "assistant"
        from core.conversation_store import ConversationStore
        nicknames = ConversationStore.instance().get_extra(
            conv_id, "agent_nicknames") or {}
        if not nicknames:
            return name
        name_lower = name.lower()
        # 1) nickname → real name (reverse lookup)
        for real, nick in nicknames.items():
            if nick.lower() == name_lower:
                return real
        # 2) case-insensitive real name match
        for real in nicknames:
            if real.lower() == name_lower:
                return real
        return name

    def initialize(self):
        """Start the poller at flow startup (not just on first request).

        This ensures scheduled rechecks from PollScheduler fire even if
        no user has sent a message yet after a server restart.
        """
        poll_interval = int(self.config.get("poll_interval", 0))
        if poll_interval > 0 and not self._poller_started:
            self._poller_started = True
            poller = threading.Thread(
                target=self._poll_conversations,
                args=(poll_interval,),
                daemon=True,
                name="agent-poller",
            )
            poller.start()
            logger.info(f"Agent poller started at flow init (interval={poll_interval}s)")

    def get_tool_registry(self) -> ToolRegistry:
        """Get or create the tool registry for this agent.

        Priority:
        1. Custom registry set via set_tool_registry()
        2. Flow-level agent_tools (injected by parser)
        3. Default builtin registry
        """
        if self._tool_registry is None:
            agent_tools_config = self.config.get("agent_tools", {})
            if agent_tools_config:
                self._tool_registry = load_agent_tools(agent_tools_config)
            else:
                self._tool_registry = create_default_registry()
            # Merge dynamic user-uploaded tools
            try:
                from core.dynamic_tool_store import DynamicToolStore
                for name, handler in DynamicToolStore.instance().get_all_handlers().items():
                    if not self._tool_registry.get(name):
                        self._tool_registry.register(handler)
            except Exception as e:
                logger.warning(f"Failed to load dynamic tools: {e}")
        return self._tool_registry

    def set_tool_registry(self, registry: ToolRegistry):
        """Set a custom tool registry (for testing or extension)."""
        self._tool_registry = registry

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "model": {
                "type": "string", "required": False, "default": "",
                "description": "Model name override (empty = service default)",
            },
            "system_prompt": {
                "type": "string", "required": False, "default": "You are a helpful assistant.",
                "description": "System prompt for the agent",
            },
            "temperature": {
                "type": "float", "required": False, "default": 0.7,
                "description": "Sampling temperature (0-2)",
            },
            "max_tokens": {
                "type": "integer", "required": False, "default": 4096,
                "description": "Maximum response tokens per LLM call",
            },
            "max_iterations": {
                "type": "integer", "required": False, "default": 200,
                "description": "Maximum tool-use loop iterations (safety limit — agent synthesizes at the end if reached)",
            },
            "max_consecutive_tool_calls": {
                "type": "integer", "required": False, "default": 5,
                "description": "Max consecutive calls to the same tool before the agent must ask for confirmation (0 = unlimited)",
            },
            "tools": {
                "type": "string", "required": False, "default": "",
                "description": "JSON list of custom tool definitions (overrides builtins)",
            },
            "timeout": {
                "type": "integer", "required": False, "default": 120,
                "description": "Request timeout in seconds",
            },
            "conversation_attribute": {
                "type": "string", "required": False, "default": "",
                "description": "Attribute to store/restore conversation history (JSON)",
            },
            "conversation_store": {
                "type": "boolean", "required": False, "default": False,
                "description": "Use persistent ConversationStore (for multi-turn HTTP)",
            },
            "conversation_ttl": {
                "type": "integer", "required": False, "default": 0,
                "description": "Conversation TTL in seconds (0 = no expiry)",
            },
            "file_base_url": {
                "type": "string", "required": False, "default": "",
                "description": "Base URL for generated file download links",
            },
            "streaming": {
                "type": "boolean", "required": False, "default": False,
                "description": "Enable SSE streaming mode (publishes events to ConversationEventBus)",
            },
            "max_rounds": {
                "type": "integer", "required": False, "default": 1,
                "description": "Max autonomous continuation rounds (agent calls schedule_continuation to trigger next round)",
            },
            "poll_interval": {
                "type": "integer", "required": False, "default": 0,
                "description": "Autonomous poll interval in seconds (0 = disabled). When enabled, the agent periodically checks active conversations for pending work.",
            },
            "poll_recheck_delay": {
                "type": "integer", "required": False, "default": 7200,
                "description": "When the agent finds no pending work, wait this many seconds before rechecking (default 2 hours). Ignored if user interacts before the delay expires.",
            },
            "max_context_size": {
                "type": "integer", "required": False, "default": 64000,
                "description": "Maximum context size in tokens (estimated). When reached, older messages are compacted into a summary.",
            },
            "context_compact_threshold": {
                "type": "float", "required": False, "default": 0.8,
                "description": "Compact when context reaches this fraction of max_context_size (default 0.8 = 80%)",
            },
            "context_keep_recent": {
                "type": "integer", "required": False, "default": 6,
                "description": "Number of recent messages to keep intact during compaction (never summarized)",
            },
            "summarizer_service": {
                "type": "string", "required": False, "default": "${global.summarizer_service}",
                "description": "Dedicated LLM service for context compaction/summary. If empty, uses the default client.",
            },
            "llm_service": {
                "type": "string", "required": False, "default": "${global.llm_default_service}",
                "description": "LLM service ID (from global/user services). Defaults to ${global.llm_default_service}.",
            },
        }

    def _resolve_client(self, service_id: str, user_id: str, *,
                        resolve_expressions: bool = True,
                        raise_on_missing: bool = False,
                        default_model: str = ""):
        """Unified LLM client resolution.

        Returns (LLMClient | None, service | None).  When *raise_on_missing*
        is True a ``ValueError`` is raised instead of returning ``(None, None)``.
        """
        svc_id = service_id
        if resolve_expressions and svc_id and "${" in svc_id:
            from core.expression import resolve_expression
            svc_id = resolve_expression(svc_id, owner=user_id)
        if not svc_id or "${" in svc_id:
            if resolve_expressions:
                svc_id = ""  # expression unresolved → let registries try
            else:
                svc_id = "default"
        client, svc = self._resolve_llm_service(svc_id, user_id)
        if not client and self.config.get("api_key"):
            client = LLMClient(
                provider=self.config.get("provider", "openai"),
                api_key=self.config["api_key"],
                base_url=self.config.get("base_url", ""),
                default_model=default_model,
                timeout=int(self.config.get("timeout", 120)),
            )
            svc = None
        if not client and raise_on_missing:
            raise ValueError(
                f"LLM service '{service_id}' not found. "
                f"Define it in global services or set 'llm.default.service' "
                f"in config/global_parameters.json."
            )
        return client, svc

    def _get_default_client(self, user_id: str = ""):
        """Get the task's default LLM client (for compaction/summarization).

        Always uses the task-level llm_service, never the agent-switched one.
        """
        client, _ = self._resolve_client(
            self.config.get("llm_service", ""), user_id,
            resolve_expressions=True,
        )
        return client

    def _resolve_llm_service(self, service_id: str, user_id: str):
        """Resolve an LLM service by ID. Returns (LLMClient, service) or (None, None).

        Resolution order: flow services → UserServiceRegistry → GlobalServiceRegistry.
        """
        if not service_id:
            return None, None
        # 1. Flow-level services (defined in flow JSON)
        if self._services:
            svc = self._services.get(service_id)
            if svc and hasattr(svc, 'get_client'):
                return svc.get_client(), svc
        # 2. User-scoped services
        try:
            from gui.services.user_service_registry import UserServiceRegistry
            svc = UserServiceRegistry.get_instance().get_live_instance(user_id, service_id)
            if svc and hasattr(svc, 'get_client'):
                return svc.get_client(), svc
        except Exception as e:
            logger.debug("User service '%s' for '%s': %s", service_id, user_id, e)
        # 3. Global services
        try:
            from gui.services.global_service_registry import GlobalServiceRegistry
            svc = GlobalServiceRegistry.get_instance().get_live_instance(service_id)
            if svc and hasattr(svc, 'get_client'):
                return svc.get_client(), svc
        except Exception as e:
            logger.warning("Global service '%s' resolution failed: %s", service_id, e)
        return None, None

    def _get_summarizer_client(self, user_id: str = ""):
        """Resolve a dedicated summarizer LLM service for compaction/summary.

        Returns (client, max_context_tokens) or (None, 0) if not configured.
        """
        svc_id = self.config.get("summarizer_service", "")
        if svc_id and "${" in svc_id:
            from core.expression import resolve_expression
            svc_id = resolve_expression(svc_id, owner=user_id)
        if not svc_id or "${" in svc_id:
            return None, 0
        client, svc = self._resolve_llm_service(svc_id, user_id)
        if client and svc:
            ctx_max = int((getattr(svc, 'config', {}) or {}).get("max_context_size", 0))
            return client, ctx_max
        return None, 0

    # ── Media service discovery (generic for image/video) ───────────

    @staticmethod
    def _get_media_types(base_class) -> set:
        """Get all registered service_type strings that inherit from base_class."""
        try:
            from tasks import _register_all_services
            _register_all_services()
        except Exception:
            pass
        from core import ServiceFactory
        types = set()
        for stype, sclass in ServiceFactory._services.items():
            try:
                if issubclass(sclass, base_class):
                    types.add(stype)
            except TypeError:
                pass
        return types

    def _discover_media_services(self, user_id: str, base_class) -> list:
        """Discover all deployed and enabled services of a given type.

        Uses the service definitions from global + user registries.
        Matches service_type against known types for the base_class.
        Rechecked every time (services can be added at runtime).

        Returns list of (service_id, service_type, scope) tuples.
        """
        valid_types = self._get_media_types(base_class)

        results = []
        seen = set()
        try:
            from gui.services.global_service_registry import GlobalServiceRegistry
            greg = GlobalServiceRegistry.get_instance()
            for sid, sdef in greg.get_all_definitions().items():
                if not getattr(sdef, "enabled", True):
                    continue
                stype = getattr(sdef, "service_type", "") or ""
                if stype in valid_types:
                    results.append((sid, stype, "global"))
                    seen.add(sid)
        except Exception as e:
            logger.error("Global service discovery failed: %s", e, exc_info=True)
        if user_id:
            try:
                from gui.services.user_service_registry import UserServiceRegistry
                ureg = UserServiceRegistry.get_instance()
                for sid, sdef in ureg.get_all_for_user(user_id).items():
                    if sid in seen:
                        continue
                    if not getattr(sdef, "enabled", True):
                        continue
                    stype = getattr(sdef, "service_type", "") or ""
                    if stype in valid_types:
                        results.append((sid, stype, "user"))
            except Exception as e:
                logger.error("User service discovery failed: %s", e, exc_info=True)
        return results

    @staticmethod
    def _resolve_media_service_by_id(service_id: str, user_id: str):
        """Resolve a media service by ID. Returns instance or None."""
        if not service_id:
            return None
        try:
            from gui.services.user_service_registry import UserServiceRegistry
            svc = UserServiceRegistry.get_instance().get_live_instance(user_id, service_id)
            if svc and hasattr(svc, 'generate'):
                return svc
        except Exception:
            pass
        try:
            from gui.services.global_service_registry import GlobalServiceRegistry
            svc = GlobalServiceRegistry.get_instance().get_live_instance(service_id)
            if svc and hasattr(svc, 'generate'):
                return svc
        except Exception:
            pass
        return None

    def _make_media_resolver(self, user_id: str, conversation_id: str,
                             agent_name: str, base_class,
                             extra_key: str, label: str, command: str):
        """Build a generic resolver closure for any media service type."""
        _self = self
        def resolver():
            available = _self._discover_media_services(user_id, base_class)
            if not available:
                return None, f"No {label} service deployed"
            if len(available) == 1:
                svc = _self._resolve_media_service_by_id(available[0][0], user_id)
                if svc:
                    return svc, None
                return None, f"{label.title()} service '{available[0][0]}' failed to connect"
            # Multiple → check per-agent preference, then wildcard
            if conversation_id:
                from core.conversation_store import ConversationStore
                prefs = ConversationStore.instance().get_extra(
                    conversation_id, extra_key,
                ) or {}
                preferred = prefs.get(agent_name or "assistant") or prefs.get("*")
                if preferred:
                    svc = _self._resolve_media_service_by_id(preferred, user_id)
                    if svc:
                        return svc, None
            names = [s[0] for s in available]
            return None, (
                f"Multiple {label} services available: {', '.join(names)}. "
                f"Use {command} select <name> to choose one for this "
                f"conversation, or {command} select <name> <agent> for "
                f"a specific agent."
            )
        return resolver

    def _make_image_resolver(self, user_id, conversation_id, agent_name):
        from services.base_image_generation import BaseImageGenerationService
        return self._make_media_resolver(
            user_id, conversation_id, agent_name,
            BaseImageGenerationService, "image_services",
            "image generation", "/imgservice",
        )

    def _make_video_resolver(self, user_id, conversation_id, agent_name):
        from services.base_video_generation import BaseVideoGenerationService
        return self._make_media_resolver(
            user_id, conversation_id, agent_name,
            BaseVideoGenerationService, "video_services",
            "video generation", "/vidservice",
        )

    @staticmethod
    def _build_identity_block(agent_name: str, conversation_id: str = "",
                              nicknames: dict = None,
                              llm_service: str = "",
                              model: str = "",
                              provider: str = "") -> str:
        """Build the [IDENTITY] prefix for a system prompt."""
        real_name = agent_name or "assistant"
        if conversation_id and nicknames is None:
            from core.conversation_store import ConversationStore
            nicknames = ConversationStore.instance().get_extra(
                conversation_id, "agent_nicknames",
            ) or {}

        # Build authoritative identity — must override LLM training biases
        lines = [f'[SYSTEM IDENTITY — AUTHORITATIVE, DO NOT OVERRIDE]']
        lines.append(f'agent_id: "{real_name}"')
        if model:
            lines.append(f"model: {model}")
        if provider:
            lines.append(f"provider: {provider}")
        if llm_service:
            lines.append(f"llm_service: {llm_service}")
        if model or provider:
            lines.append(
                "RULE: When the user asks your model, name, creator, or cutoff, "
                f'you MUST answer "{model}" for model and "{provider}" for creator. '
                "These values come from the platform configuration and are CORRECT. "
                "Do NOT say 'unknown', 'not exposed', or default to generic training answers."
            )

        if nicknames:
            nick_key = real_name.lower()
            nickname = next(
                (v for k, v in nicknames.items() if k.lower() == nick_key), None,
            )
            if nickname:
                lines.append(
                    f'The user has given you the nickname "{nickname}". '
                    f'When other agents or tools refer to "{real_name}" or '
                    f'"{nickname}" (case-insensitive), they mean YOU.'
                )

        return " ".join(lines) + "\n\n"

    def _build_done_event(self, conversation_id: str, response_content: str,
                         agent_name: str, model: str, provider: str,
                         tokens_in: int, tokens_out: int,
                         tools_called: list, iteration: int, start_time: float,
                         source: dict = None, *,
                         continuing: bool = False, interrupted: bool = False):
        """Build a 'done' event dict for SSE publishing."""
        from core.conversation_store import ConversationStore
        duration_ms = (time.time() - start_time) * 1000
        event = {
            "response": response_content,
            "conversation_id": conversation_id,
            "agent_name": agent_name or "assistant",
            "model": model,
            "provider": provider,
            "base_url": (source or {}).get("base_url", ""),
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "tools_called": tools_called,
            "iterations": iteration,
            "duration_ms": round(duration_ms, 1),
            "message_count": ConversationStore.instance().message_count(conversation_id),
            "source": source or {},
        }
        if continuing:
            event["continuing"] = True
        if interrupted:
            event["interrupted"] = True
        return event

    def _decrement_active(self, conversation_id: str, ctx: dict = None):
        """Decrement the active-conversation refcount and clean up tracking."""
        with self._active_lock:
            rc = self._active_conversations.get(conversation_id, 1) - 1
            if rc <= 0:
                self._active_conversations.pop(conversation_id, None)
            else:
                self._active_conversations[conversation_id] = rc
            if ctx and not ctx.get("is_poll"):
                self._user_active_conversations.discard(conversation_id)
            if ctx:
                _tk = ctx.get("_thought_key")
                if _tk:
                    self._active_thoughts.discard(_tk)
        if ctx:
            gen_key = ctx.get("_gen_key", conversation_id)
            with self._interactions_lock:
                self._active_interactions.pop(gen_key, None)

    @staticmethod
    def _track_tokens(user_id: str, tokens_in: int, tokens_out: int,
                      model: str, agent_name: str = "assistant",
                      llm_service: str = ""):
        """Track token usage via TokenTracker (best-effort)."""
        try:
            from core.token_tracker import TokenTracker
            TokenTracker.instance().track(
                user_id, tokens_in, tokens_out,
                model=model, agent_name=agent_name,
                llm_service=llm_service,
            )
            TokenTracker.instance().flush()
        except Exception:
            pass

    @staticmethod
    def _strip_echo_prefix(text: str) -> str:
        """Strip identity prefix that the LLM may echo back (e.g. '[agent]: ...')."""
        if not text:
            return text
        stripped = text.lstrip()
        if stripped.startswith("["):
            import re
            return re.sub(r'^\[[^\]]+\]:\s*', '', stripped)
        return text

    def _force_synthesis(self, messages, client, ctx, *, prompt: str,
                         compact_client=None, use_streaming: bool = False,
                         token_callback=None, tools_called: list = None,
                         compact_threshold: float = 0.6,
                         conversation_id: str = ""):
        """Force a final synthesis from the LLM (no tools).

        Returns (content, tokens_in, tokens_out, model).
        """
        messages.append(LLMMessage(role="user", content=prompt))
        _cc = compact_client or client
        synth_context = self._compact_if_needed(
            list(messages), _cc,
            ctx.get("max_context_size", 64000),
            compact_threshold,
            ctx.get("context_keep_recent", 6),
            conversation_id=conversation_id,
        )
        model = ctx.get("model") or None
        for _attempt in range(2):
            try:
                if use_streaming and token_callback:
                    resp = client.complete_stream(
                        messages=synth_context, model=model,
                        temperature=ctx["temperature"],
                        max_tokens=ctx["max_tokens"],
                        tools=None, callback=token_callback,
                    )
                else:
                    resp = client.complete(
                        messages=synth_context, model=model,
                        temperature=ctx["temperature"],
                        max_tokens=ctx["max_tokens"],
                        tools=None,
                    )
                messages.append(LLMMessage(role="assistant", content=resp.content))
                return resp.content, resp.tokens_in, resp.tokens_out, resp.model
            except Exception as synth_err:
                err_str = str(synth_err)
                if _attempt == 0 and ("exceed_context_size" in err_str or "n_prompt_tokens" in err_str):
                    logger.warning("[agent] synthesis overflow, forcing aggressive compaction...")
                    synth_context = self._compact_if_needed(
                        synth_context, _cc,
                        ctx.get("max_context_size", 64000),
                        0.4, ctx.get("context_keep_recent", 4),
                        conversation_id=conversation_id,
                    )
                    continue
                logger.error("Forced synthesis failed: %s", synth_err)
                break
        # Fallback
        fallback = (
            "I performed research but encountered an error generating the response.\n"
            f"Tools used: {', '.join(tools_called or [])}"
        )
        return fallback, 0, 0, ""

    def _execute_tool_calls(self, tool_calls, registry, consecutive_tracker: dict,
                            max_consecutive: int, *, parallel: bool = True,
                            agent_name: str = "assistant", agent_svc: str = ""):
        """Execute tool calls with consecutive-call limiting.

        Returns list of (tool_call, result_text) in original order.
        """
        # Determine blocked tools
        blocked = set()
        if max_consecutive > 0:
            for tc in tool_calls:
                consecutive_tracker[tc.name] = consecutive_tracker.get(tc.name, 0) + 1
                for tn in list(consecutive_tracker):
                    if tn != tc.name:
                        consecutive_tracker[tn] = 0
                if consecutive_tracker[tc.name] > max_consecutive:
                    blocked.add(tc.name)

        def _exec_one(tc):
            if tc.name in blocked:
                return tc, (
                    f"Tool '{tc.name}' has been called {consecutive_tracker.get(tc.name, 0)} times "
                    f"consecutively (limit: {max_consecutive}). "
                    f"Stop and explain to the user what you've tried so far, "
                    f"and ask if they want you to continue."
                )
            # Re-inject thread-local source agent (needed in pool threads)
            from core.tool_registry import SpawnAgentsHandler
            for h in registry.list_tools():
                if isinstance(h, SpawnAgentsHandler):
                    h.set_source_agent(agent_name, agent_svc)
                    break
            try:
                logger.info("Agent calling tool '%s' with args: %s", tc.name, tc.arguments)
                return tc, registry.execute(tc.name, tc.arguments) or ""
            except Exception as e:
                logger.error("Tool '%s' failed: %s", tc.name, e)
                return tc, f"Error: {e}"

        if not parallel or len(tool_calls) == 1:
            return [_exec_one(tc) for tc in tool_calls]

        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=len(tool_calls)) as pool:
            futures = {pool.submit(_exec_one, tc): tc for tc in tool_calls}
            results_map = {}
            for future in as_completed(futures):
                tc, result_text = future.result()
                results_map[tc.id] = (tc, result_text)
        return [results_map[tc.id] for tc in tool_calls]

    def _handle_response_no_tools(self, response_text: str, client_provider: str,
                                  tool_defs, need_more_retried: bool,
                                  source: dict = None):
        """Handle an LLM response with no tool calls.

        Returns (action, msgs_to_append, final_text, need_more_retried).
        - action="continue": append msgs_to_append and loop again
        - action="break": final_text is the agent's response; append msgs_to_append
        """
        # [NEED_MORE] signal: model requests another turn
        if "[NEED_MORE]" in response_text:
            clean = self._strip_echo_prefix(response_text.replace("[NEED_MORE]", "").strip())
            msgs = []
            if clean:
                msgs.append(LLMMessage(role="assistant", content=clean, source=source))
            msgs.append(LLMMessage(role="system", content=(
                "Continue. You have another turn. "
                "Use <tool_call> tags if you need tools, "
                "or provide your final answer."
            )))
            return "continue", msgs, "", need_more_retried

        # Heuristic: tool mentioned by name without <tool_call> tag
        if client_provider in ("claude-code", "gemini-cli") and tool_defs:
            tool_names = [td.name for td in tool_defs]
            mentioned = [tn for tn in tool_names if tn in response_text]
            if mentioned and not need_more_retried:
                msgs = [
                    LLMMessage(role="assistant", content=response_text, source=source),
                    LLMMessage(role="system", content=(
                        f"You mentioned tool(s) {mentioned} but did not emit <tool_call> tags. "
                        "You MUST use <tool_call> tags to invoke tools. Example:\n"
                        '<tool_call>{"name": "' + mentioned[0] + '", "arguments": {...}}</tool_call>\n'
                        "Please emit the correct <tool_call> tag(s) now, "
                        "or provide your final answer without mentioning tools."
                    )),
                ]
                return "continue", msgs, "", True

        # Final response
        final = self._strip_echo_prefix(response_text)
        msgs = [LLMMessage(role="assistant", content=final, source=source)]
        return "break", msgs, final, need_more_retried

    def _prepare_agent_context(self, flowfile: FlowFile):
        """Extract common context from flowfile and config for both sync and streaming modes."""
        model = self.config.get("model", "")
        timeout = int(self.config.get("timeout", 120))

        # LLM service routing — all LLM access goes through services
        task_llm_service = self.config.get("llm_service", "")
        if not task_llm_service or "${" in task_llm_service:
            task_llm_service = "default"
        _user_id_for_svc = flowfile.get_attribute("http.auth.principal") or ""
        client, resolved_svc = self._resolve_client(
            task_llm_service, _user_id_for_svc,
            resolve_expressions=False, raise_on_missing=True,
            default_model=model,
        )

        registry = self.get_tool_registry()
        # Handlers are fully configured later (after conversation_id/user_id are known)

        # Wire embedding function for semantic memory handlers
        self._wire_embed_fn(registry, client)

        # Set up SubAgentExecutor for spawn_agents/use_skill/get_agent_results
        from core.agent_executor import SubAgentExecutor
        from core.tool_registry import (
            SpawnAgentsHandler, GetAgentResultsHandler, UseSkillHandler,
        )
        # Create a resolver closure for per-agent LLM service routing
        _self = self
        def _client_resolver(svc_id, uid):
            return _self._resolve_llm_service(svc_id, uid)
        # on_event callback for sub-agent visibility (SSE events)
        def _sub_on_event(event_type, data):
            try:
                from core.conversation_event_bus import ConversationEventBus
                ConversationEventBus.instance().publish_event(conversation_id, event_type, data)
            except Exception:
                pass
        sub_executor = SubAgentExecutor(
            client, registry, max_workers=4,
            client_resolver=_client_resolver,
            on_event=_sub_on_event,
        )
        # Inject available agent names into SpawnAgentsHandler for tool description
        _uid_for_agents = flowfile.get_attribute("http.auth.principal") or "anonymous"
        try:
            from core.resource_store import ResourceStore
            _all_agents = ResourceStore.instance().list_all("agent", _uid_for_agents)
            _agent_names = ["assistant"] + [a["name"] for a in _all_agents]
        except Exception:
            _agent_names = []

        for h in registry.list_tools():
            if isinstance(h, SpawnAgentsHandler):
                h.set_spawn_deps(client, _client_resolver, _sub_on_event, registry=registry)
                if _agent_names:
                    h.set_available_agents(_agent_names)
            elif isinstance(h, UseSkillHandler):
                h.set_spawn_deps(client, _client_resolver)

        user_role = flowfile.get_attribute("http.auth.roles") or ""
        if user_role:
            registry = self._filter_tools_by_role(registry, user_role)

        custom_tools_json = self.config.get("tools", "")
        if custom_tools_json:
            try:
                custom_tools = json.loads(custom_tools_json)
                tool_defs = [
                    LLMToolDefinition(
                        name=t["name"],
                        description=t.get("description", ""),
                        parameters=t.get("parameters", {"type": "object", "properties": {}}),
                    )
                    for t in custom_tools
                ]
            except (json.JSONDecodeError, KeyError) as e:
                raise ValueError(f"Invalid tools JSON: {e}")
        else:
            tool_defs = [
                LLMToolDefinition(
                    name=h.name, description=h.description, parameters=h.parameters_schema,
                )
                for h in registry.list_tools()
            ]

        system_prompt = self.config.get("system_prompt", "You are a helpful assistant.")
        # Inject current date/time so the agent is always aware
        from datetime import datetime
        system_prompt += f"\n\nCurrent date and time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        # Will be overridden below if a persona is selected (after conversation_id is known)
        _base_system_prompt = system_prompt
        temperature = float(self.config.get("temperature", 0.7))
        max_tokens = int(self.config.get("max_context_size", 0))
        max_iterations = int(self.config.get("max_iterations", 200))
        max_consecutive_tool_calls = int(self.config.get("max_consecutive_tool_calls", 5))

        use_conv_store = self.config.get("conversation_store", False)
        conv_ttl = int(self.config.get("conversation_ttl", 0))
        conv_attr = self.config.get("conversation_attribute", "")

        raw_body = flowfile.get_content().decode("utf-8", errors="replace")
        user_text = raw_body
        conversation_id = None
        attachments = []  # list of {"type": "image"|"document", ...}
        body_json = None

        if raw_body.strip().startswith("{"):
            try:
                body_json = json.loads(raw_body)
                if isinstance(body_json, dict) and "message" in body_json:
                    user_text = body_json["message"]
                    conversation_id = body_json.get("conversation_id")
                    attachments = body_json.get("attachments", [])
                    # Per-conversation TTL override from chat UI
                    if "ttl" in body_json:
                        conv_ttl = int(body_json["ttl"])
            except json.JSONDecodeError:
                pass

        # Telegram multimodal: inject image from attributes
        tg_image = flowfile.get_attribute("telegram.image_base64") or ""
        if tg_image:
            attachments.append({
                "filename": "telegram_photo.jpg",
                "mime_type": "image/jpeg",
                "data": tg_image,
            })

        # Cross-channel identity resolution (generic for all channels)
        CHANNEL_ATTRS = {
            "telegram": ("telegram.chat_id", "telegram.user_id"),
            "discord":  ("discord.channel_id", "discord.user_id"),
            "whatsapp": ("whatsapp.phone", "whatsapp.phone"),
            "slack":    ("slack.channel_id", "slack.user_id"),
        }

        channel = "web"
        channel_chat_id = ""
        channel_user_id = ""
        for ch, (chat_attr, user_attr) in CHANNEL_ATTRS.items():
            val = flowfile.get_attribute(chat_attr) or ""
            if val:
                channel = ch
                channel_chat_id = val
                channel_user_id = flowfile.get_attribute(user_attr) or ""
                break

        if channel_chat_id:
            if use_conv_store and channel_user_id:
                from core.identity_service import IdentityService
                ids = IdentityService.instance()
                resolved_user = ids.resolve_user(channel, channel_user_id)
                if resolved_user:
                    flowfile.set_attribute("http.auth.principal", resolved_user)
                    active = ids.get_active_conv(resolved_user, channel)
                    if active:
                        conversation_id = active
                    self._pending_channel_chat_id = channel_chat_id
                    self._pending_channel_name = channel
                else:
                    self._pending_channel_chat_id = channel_chat_id
                    self._pending_channel_name = channel
            else:
                self._pending_channel_chat_id = channel_chat_id
                self._pending_channel_name = channel

        messages: List[LLMMessage] = []

        # Determine active agent name early (needed for per-agent context loading)
        _early_target = body_json.get("target_agent", "") if body_json else ""
        _early_agent = ""
        if use_conv_store and conversation_id:
            try:
                from core.conversation_store import ConversationStore as _CSEarly
                _early_res = _CSEarly.instance().get_extra(
                    conversation_id, "active_resources",
                ) or {}
                _early_agent = _early_target or _early_res.get("agent", "")
            except Exception:
                pass
        _context_agent = _early_agent or "assistant"

        _context_diverged = False
        if use_conv_store and conversation_id:
            from core.conversation_store import ConversationStore
            store = ConversationStore.instance()
            context_data = store.load_agent_context(conversation_id, _context_agent)
            if context_data is not None:
                # Context has diverged — use it directly
                try:
                    messages = self._deserialize_messages(context_data)
                    _context_diverged = True
                    logger.info(f"[context:{conversation_id[:8]}] loaded diverged context: "
                                f"{len(messages)} messages")
                except (KeyError, TypeError) as deser_err:
                    logger.error(f"[context:{conversation_id[:8]}] context load failed: {deser_err}")
            else:
                # No divergence — use messages as context
                existing = store.load(conversation_id)
                if existing:
                    try:
                        messages = self._deserialize_messages(existing)
                        logger.info(f"[context:{conversation_id[:8]}] loaded messages as context: "
                                    f"{len(messages)} messages")
                    except (KeyError, TypeError) as deser_err:
                        logger.error(f"[context:{conversation_id[:8]}] message load failed: {deser_err}")
                else:
                    logger.warning(f"[context:{conversation_id[:8]}] store.load() returned None — "
                                   f"starting fresh conversation")
        elif conv_attr:
            existing = flowfile.get_attribute(conv_attr)
            if existing:
                try:
                    messages = self._deserialize_messages(json.loads(existing))
                except (json.JSONDecodeError, KeyError):
                    pass

        if not messages:
            messages = [LLMMessage(role="system", content=system_prompt)]
            # Fresh conversation — everything is new (including system prompt)
            base_message_count = 0
        else:
            # Loaded from store — these messages are already persisted
            base_message_count = len(messages)


        if use_conv_store and not conversation_id:
            from core.conversation_store import ConversationStore
            conversation_id = ConversationStore.instance().generate_id()

        if use_conv_store and not conversation_id:
            raise ValueError(
                "BUG: no conversation_id after generate_id() — this should never happen"
            )

        # target_agent: temporary agent override for /agent msg (not persisted)
        _target_agent = body_json.get("target_agent", "") if body_json else ""
        if _target_agent and conversation_id:
            _target_agent = self._resolve_agent_name(_target_agent, conversation_id)

        # Apply pending_agent from the first message (agent selected before conversation existed)
        _pending_agent = body_json.get("pending_agent", "") if body_json else ""
        if _pending_agent and use_conv_store and conversation_id:
            try:
                from core.conversation_store import ConversationStore
                store = ConversationStore.instance()
                # Ensure conversation entry exists (save minimal data)
                if not store.load(conversation_id):
                    _uid = flowfile.get_attribute("http.auth.principal") or ""
                    store.save(conversation_id, [], user_id=_uid)
                active = store.get_extra(conversation_id, "active_resources") or {}
                active["agent"] = _pending_agent
                store.set_extra(conversation_id, "active_resources", active)
                logger.info("Applied pending agent '%s' on new conversation %s",
                            _pending_agent, conversation_id[:8])
            except Exception as e:
                logger.warning("Failed to apply pending agent '%s': %s", _pending_agent, e)

        # Store channel chat_id for cross-channel notifications
        if use_conv_store and conversation_id and getattr(self, '_pending_channel_chat_id', ''):
            try:
                from core.conversation_store import ConversationStore
                ch_name = getattr(self, '_pending_channel_name', 'telegram')
                ConversationStore.instance().set_extra(
                    conversation_id, f"{ch_name}_chat_id",
                    self._pending_channel_chat_id,
                )
            except Exception:
                pass
            self._pending_channel_chat_id = ""
            self._pending_channel_name = ""

        # Check for selected agent persona and active skills
        _selected_agent_def = None
        if use_conv_store and conversation_id:
            try:
                from core.conversation_store import ConversationStore
                from core.resource_store import ResourceStore
                cstore = ConversationStore.instance()
                rs = ResourceStore.instance()
                active_res = cstore.get_extra(conversation_id, "active_resources") or {}
                _uid = flowfile.get_attribute("http.auth.principal") or "anonymous"

                # Active agent overrides system prompt (target_agent takes priority)
                selected = _target_agent or active_res.get("agent", "")
                if selected:
                    agent_def = rs.get_any("agent", selected, _uid,
                                           conversation_id=conversation_id)
                    if not agent_def and _target_agent:
                        # "assistant" is the default persona, not a ResourceStore agent
                        if _target_agent != "assistant":
                            # /agent msg <name> with unknown agent — reject early
                            raise ValueError(f"Agent '{_target_agent}' not found")
                    if agent_def:
                        _selected_agent_def = agent_def
                        system_prompt = agent_def["prompt"]
                        # Identity is injected later (with nickname awareness)

                        system_prompt += f"Current date and time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                        # List other available agents (user + global + assistant)
                        all_agents = rs.list_all("agent", _uid, conversation_id=conversation_id)
                        others = [a["name"] for a in all_agents if a["name"] != selected]
                        if selected != "assistant" and "assistant" not in others:
                            others.insert(0, "assistant")
                        if others:
                            system_prompt += (
                                f"\n\nOther agents available: "
                                f"{', '.join(others)}. Use spawn_agents or "
                                f"manage_resource to work with them."
                            )
                else:
                    # No agent selected — still list available agents so default can use spawn_agents
                    all_agents = rs.list_all("agent", _uid, conversation_id=conversation_id)
                    if all_agents:
                        agent_lines = []
                        for a in all_agents:
                            desc = a.get("description") or a.get("prompt", "")[:80]
                            agent_lines.append(f"  - {a['name']}: {desc}")
                        system_prompt += (
                            f"\n\n## Available agents\n"
                            f"You have access to the following agents via the spawn_agents tool:\n"
                            + "\n".join(agent_lines) + "\n\n"
                            f"When the user asks you to talk to or contact an agent, "
                            f"you MUST use the spawn_agents tool to send them a message. "
                            f"Example: spawn_agents(tasks=[{{\"agent\": \"grok\", \"message\": \"Hello from the default agent!\"}}])\n"
                            f"The user can also switch to an agent directly with /agent select <name>."
                        )

                # Inject active skills into system prompt
                active_skills = active_res.get("skills", [])
                if active_skills:
                    skill_sections = []
                    for sname in active_skills:
                        skill_def = rs.get_any("skill", sname, _uid)
                        if skill_def:
                            skill_sections.append(
                                f"### Skill: {sname}\n{skill_def['prompt']}"
                            )
                    if skill_sections:
                        system_prompt += (
                            "\n\n## Active Skills\n"
                            "The following skills are active. You can apply them "
                            "via the use_skill tool or follow their instructions "
                            "directly:\n\n" + "\n\n".join(skill_sections)
                        )
            except Exception as e:
                logger.error("Error loading agent persona/skills: %s", e, exc_info=True)

        # If the system_prompt was overridden (by agent persona or skills),
        # update messages[0] so the LLM sees the correct prompt — even when
        # messages were loaded from conversation history.
        if messages and messages[0].role == "system" and system_prompt != _base_system_prompt:
            messages[0] = LLMMessage(role="system", content=system_prompt)

        model_name = self.config.get("model", "")
        user_id = flowfile.get_attribute("http.auth.principal") or ""

        if user_text.strip() or attachments:
            user_content = self._build_user_content(user_text, attachments)
            user_source = {"type": "user", "name": user_id or "anonymous"}
            if _target_agent:
                user_source["target_agent"] = _target_agent
            # Also tag btw messages
            _is_btw = body_json.get("btw", False) if body_json else False
            if _is_btw:
                user_source["btw"] = True
            messages.append(LLMMessage(role="user", content=user_content, source=user_source))

        # Determine active agent name and llm_service for source tracking
        _active_agent_name = "assistant"
        _active_llm_service = task_llm_service
        if use_conv_store and conversation_id:
            try:
                from core.conversation_store import ConversationStore
                _ares = ConversationStore.instance().get_extra(
                    conversation_id, "active_resources",
                ) or {}
                _active_agent_name = _target_agent or _ares.get("agent", "") or "assistant"
                if _active_agent_name:
                    # Check per-conversation LLM service override first
                    _llm_overrides = ConversationStore.instance().get_extra(
                        conversation_id, "agent_llm_overrides",
                    ) or {}
                    _override_svc = _llm_overrides.get(_active_agent_name or "assistant")
                    if _override_svc:
                        _active_llm_service = _override_svc
                    from core.resource_store import ResourceStore
                    _adef = ResourceStore.instance().get_any(
                        "agent", _active_agent_name, user_id,
                        conversation_id=conversation_id,
                    )
                    if not _override_svc and _adef and _adef.get("llm_service", ""):
                        _agent_llm = _adef["llm_service"]
                        # Resolve expressions in llm_service (e.g. ${user.grok_llm_service})
                        if "${" in _agent_llm:
                            from core.expression import resolve_expression
                            _agent_llm = resolve_expression(
                                _agent_llm, owner=user_id,
                            )
                        if _agent_llm and "${" not in _agent_llm:
                            _active_llm_service = _agent_llm
                # If active agent has its own LLM service, resolve it now
                if _active_llm_service and _active_llm_service != task_llm_service:
                    logger.info("Agent '%s' switching LLM service: '%s' → '%s'",
                                _active_agent_name, task_llm_service, _active_llm_service)
                    _rc, _rs = self._resolve_llm_service(_active_llm_service, user_id)
                    if _rc:
                        client = _rc
                        resolved_svc = _rs
                        # Use service's default model, not the task's model
                        model_name = ""
                        logger.info("Agent '%s' now using LLM service '%s' (provider: %s)",
                                    _active_agent_name, _active_llm_service,
                                    getattr(_rs, 'provider', '?'))
                    else:
                        logger.warning("Agent '%s': LLM service '%s' NOT FOUND — falling back to '%s'",
                                       _active_agent_name, _active_llm_service, task_llm_service)
                        _active_llm_service = task_llm_service  # Reset so badge reflects reality
                elif _active_llm_service == task_llm_service and _active_agent_name:
                    logger.info("Agent '%s' llm_service='%s' same as task default — no switch needed",
                                _active_agent_name, _active_llm_service)
                elif _active_agent_name and not _adef:
                    logger.warning("Agent '%s' definition not found in ResourceStore", _active_agent_name)
                elif _active_agent_name and not _adef.get("llm_service", ""):
                    logger.info("Agent '%s' has no llm_service — using task default '%s'",
                                _active_agent_name, task_llm_service)
            except Exception as e:
                logger.error("Error resolving agent LLM service: %s", e, exc_info=True)

        # Resolve max_tokens for LLM output (0 = unlimited)
        # This is NOT the context size — it's the max output the LLM can generate
        if not max_tokens:
            max_tokens = 0  # no artificial limit on output

        # Inject identity block into system prompt
        _nicknames = {}
        if conversation_id:
            from core.conversation_store import ConversationStore as _CSNick
            _nicknames = _CSNick.instance().get_extra(conversation_id, "agent_nicknames") or {}
        # Read identity from the resolved service (source of truth)
        _client_model_name = ""
        _client_provider_name = ""
        _client_base_url = ""
        if resolved_svc:
            _svc_cfg = getattr(resolved_svc, 'config', {}) or {}
            _client_model_name = getattr(resolved_svc, 'default_model', "") or _svc_cfg.get("default_model", "")
            _client_provider_name = getattr(resolved_svc, 'provider', "") or _svc_cfg.get("provider", "")
            _client_base_url = getattr(resolved_svc, 'base_url', "") or _svc_cfg.get("base_url", "")
        if not _client_model_name:
            _client_model_name = getattr(client, "default_model", "") or model_name or ""
        if not _client_provider_name:
            _client_provider_name = getattr(client, "provider", "") or ""
        if not _client_base_url:
            _client_base_url = getattr(client, "base_url", "") or ""
        system_prompt = self._build_identity_block(
            _active_agent_name, conversation_id, _nicknames,
            llm_service=_active_llm_service,
            model=_client_model_name,
            provider=_client_provider_name,
        ) + system_prompt

        # Build ephemeral identity suffix (injected into system prompt at call
        # time, NEVER persisted — each agent gets its own identity per request)
        _identity_suffix = ""
        if _client_model_name or _client_provider_name:
            _id_parts = []
            if _client_model_name:
                _id_parts.append(f"model={_client_model_name}")
            if _client_provider_name:
                _id_parts.append(f"provider={_client_provider_name}")
            if _active_llm_service:
                _id_parts.append(f"service={_active_llm_service}")
            _identity_suffix = (
                f"\n\n[Platform identity] agent_id={_active_agent_name}, "
                + ", ".join(_id_parts) + ". "
                "Report these exact values when asked about your model/identity."
            )

        # Configure all handlers with full context
        self._configure_tool_handlers(
            registry, conversation_id=conversation_id or "",
            user_id=user_id or "",
            llm_client=client, llm_model=model_name,
            agent_name=_active_agent_name or "assistant",
            agent_svc=_active_llm_service or "",
        )

        return {
            "client": client, "registry": registry, "tool_defs": tool_defs,
            "messages": messages, "model": model_name,
            "_identity_suffix": _identity_suffix,
            "temperature": temperature, "max_tokens": max_tokens,
            "max_iterations": max_iterations,
            "max_consecutive_tool_calls": max_consecutive_tool_calls,
            "max_rounds": int(self.config.get("max_rounds", 1)),
            "use_conv_store": use_conv_store, "conv_ttl": conv_ttl,
            "conv_attr": conv_attr, "conversation_id": conversation_id,
            "user_id": user_id,
            "_base_message_count": base_message_count,
            "max_context_size": int(
                # Per-agent: use service max_tokens (= context window size)
                (getattr(resolved_svc, 'config', {}) or {}).get("max_context_size", 0)
                or (_selected_agent_def or {}).get("max_context_size", 0)
                or self.config.get("max_context_size", 64000)
            ),
            "context_compact_threshold": float(self.config.get("context_compact_threshold", 0.8)),
            "context_keep_recent": int(self.config.get("context_keep_recent", 6)),
            "channel": channel,
            "active_agent_name": _active_agent_name,
            "active_llm_service": _active_llm_service,
            "resolved_svc": resolved_svc,
            "default_client": self._get_default_client(user_id),
            "summarizer": self._get_summarizer_client(user_id),
            "sub_executor": sub_executor,
            "_target_agent": _target_agent,
            "_context_diverged": _context_diverged,
            "_nicknames": _nicknames if conversation_id else {},
        }



    # ── Context operation pause/resume ─────────────────────────────────

    def _run_bg_context_op(self, conv_id: str, op_name: str, fn, flowfile):
        """Run a context operation in background with lock + SSE progress.

        Returns immediately with an ack. The background thread:
        1. Cancels the active agent
        2. Acquires the context op lock (blocks FlowFiles)
        3. Runs fn() which returns a result dict
        4. Publishes SSE done/error event
        5. Releases the lock
        """
        from core.conversation_event_bus import ConversationEventBus
        bus = ConversationEventBus.instance()

        def _bg():
            self.cancel_agent(conv_id, silent=True)
            if not self._acquire_context_op(conv_id, timeout=60.0):
                bus.publish_event(conv_id, "compact_progress", {
                    "stage": "error",
                    "error": f"Timeout waiting for active agent ({op_name})",
                })
                return
            try:
                bus.publish_event(conv_id, "compact_progress", {
                    "stage": "start", "detail": op_name,
                })
                result = fn()
                bus.publish_event(conv_id, "compact_progress", {
                    "stage": "done", **result,
                })
            except Exception as e:
                bus.publish_event(conv_id, "compact_progress", {
                    "stage": "error", "error": str(e),
                })
                logger.error("%s failed: %s", op_name, e, exc_info=True)
            finally:
                self._release_context_op(conv_id)

        thread = threading.Thread(target=_bg, daemon=True,
                                  name=f"{op_name}-{conv_id[:8]}")
        thread.start()
        flowfile.set_content(json.dumps({
            "status": "accepted", "action": op_name,
        }).encode())
        return [flowfile]

    def _get_context_op_event(self, conversation_id: str) -> threading.Event:
        """Get or create a per-conversation context-op Event (set = free)."""
        with self._context_op_lock:
            evt = self._context_op_events.get(conversation_id)
            if evt is None:
                evt = threading.Event()
                evt.set()  # initially free
                self._context_op_events[conversation_id] = evt
            return evt

    def _acquire_context_op(self, conversation_id: str, timeout: float = 30.0) -> bool:
        """Acquire exclusive context-op lock.  Returns True if acquired."""
        evt = self._get_context_op_event(conversation_id)
        # Wait for any previous op to finish
        if not evt.wait(timeout=timeout):
            return False
        evt.clear()  # mark as busy
        return True

    def _release_context_op(self, conversation_id: str):
        """Release the context-op lock, unblocking waiting FlowFiles."""
        evt = self._get_context_op_event(conversation_id)
        evt.set()

    def _is_context_op_free(self, conversation_id: str) -> bool:
        """Non-blocking check: True if no context op is running."""
        with self._context_op_lock:
            evt = self._context_op_events.get(conversation_id)
            if evt is None:
                return True
            return evt.is_set()

    # All context ops manage their own lock in background threads
    _CONTEXT_OPS = frozenset()

    @staticmethod
    def _extract_conversation_id(ff) -> Optional[str]:
        """Extract conversation_id from a FlowFile's JSON body, if present."""
        raw = ff.get_content().decode("utf-8", errors="replace")
        if not raw.strip().startswith("{"):
            return None
        try:
            return json.loads(raw).get("conversation_id")
        except (json.JSONDecodeError, AttributeError):
            return None

    @classmethod
    def _detect_context_op(cls, ff) -> Optional[str]:
        """If the FlowFile is a context-mutating action, return the conversation_id."""
        raw = ff.get_content().decode("utf-8", errors="replace")
        if not raw.strip().startswith("{"):
            return None
        try:
            body = json.loads(raw)
        except (json.JSONDecodeError, AttributeError):
            return None
        if not isinstance(body, dict):
            return None
        if body.get("action") in cls._CONTEXT_OPS:
            return body.get("conversation_id") or None
        return None

    @staticmethod
    def _is_action_flowfile(ff) -> bool:
        """Check if a FlowFile is an action request (no LLM needed)."""
        try:
            raw = ff.get_content().decode("utf-8", errors="replace")
            if raw.strip().startswith("{"):
                body = json.loads(raw)
                return isinstance(body, dict) and "action" in body
        except Exception:
            pass
        return False

    # Priority levels for FlowFile queue ordering
    _ACTION_PRIORITIES = {
        "cancel": 20,       # /stop — highest, user wants to stop NOW
        "interrupt": 20,    # /stop (interrupt variant)
        "btw": 15,          # /btw — side-channel, should not wait
        "compact": 10,      # context ops
        "rebuild": 10,
        "rebuild_full": 10,
        "rebuild_clean": 10,
        "restart_from": 10,
        "resume_conversation": 10,
    }

    def prioritize(self, flowfile) -> int:
        """Assign priority based on FlowFile content.

        20 = cancel/interrupt (immediate)
        15 = btw (side-channel)
        10 = actions (no LLM needed)
        0  = normal messages
        """
        try:
            raw = flowfile.get_content().decode("utf-8", errors="replace")
            if raw.strip().startswith("{"):
                body = json.loads(raw)
                if isinstance(body, dict) and "action" in body:
                    action = body["action"]
                    return self._ACTION_PRIORITIES.get(action, 10)
        except Exception:
            pass
        return 0

    def select_processable(self, connections):
        """Queue-aware scheduling: skip FlowFiles targeting saturated LLM services
        or conversations with a context operation in progress.

        Action FlowFiles (JSON with "action" key) are always processable —
        they don't need LLM capacity and should never be blocked.

        Called by ContinuousFlowExecutor instead of peek-first.
        Returns (FlowFile, Connection) or None if nothing is processable.
        """
        # Pass 1: prioritize action FlowFiles (no LLM needed, always process)
        for conn in connections:
            for ff in conn.peek_all():
                if self._is_action_flowfile(ff):
                    conv_id = self._extract_conversation_id(ff)
                    if conv_id and not self._is_context_op_free(conv_id):
                        continue
                    return ff, conn

        # Pass 2: normal FlowFiles (need LLM capacity)
        for conn in connections:
            for ff in conn.peek_all():
                conv_id = self._extract_conversation_id(ff)
                if conv_id and not self._is_context_op_free(conv_id):
                    continue
                svc = self._get_service_for_flowfile(ff)
                if svc is None or svc.has_capacity():
                    return ff, conn
        return None

    def _get_service_for_flowfile(self, ff):
        """Determine the LLM service a FlowFile would use. Returns service or None."""
        # Check conversation → active agent → agent.llm_service
        raw_body = ff.get_content().decode("utf-8", errors="replace")
        conversation_id = None
        if raw_body.strip().startswith("{"):
            try:
                body = json.loads(raw_body)
                conversation_id = body.get("conversation_id")
            except (json.JSONDecodeError, AttributeError):
                pass

        user_id = ff.get_attribute("http.auth.principal") or ""
        service_id = self.config.get("llm_service", "")

        if conversation_id and not service_id:
            try:
                from core.conversation_store import ConversationStore
                from core.resource_store import ResourceStore
                ares = ConversationStore.instance().get_extra(
                    conversation_id, "active_resources",
                ) or {}
                agent_name = ares.get("agent", "")
                if agent_name:
                    adef = ResourceStore.instance().get_any("agent", agent_name, user_id)
                    if adef:
                        service_id = adef.get("llm_service", "")
                        if service_id and "${" in service_id:
                            from core.expression import resolve_expression
                            service_id = resolve_expression(service_id, owner=user_id)
                            if "${" in service_id:
                                service_id = ""
            except Exception:
                pass

        if not service_id:
            return None

        _, svc = self._resolve_llm_service(service_id, user_id)
        return svc

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        # Reject unlinked Telegram users (require identity link for security)
        tg_user_id = flowfile.get_attribute("telegram.user_id") or ""
        if tg_user_id:
            raw_text = flowfile.get_content().decode("utf-8", errors="replace").strip()
            # Allow /conv commands (they handle their own auth messages)
            if not raw_text.startswith("/conv"):
                from core.identity_service import IdentityService
                resolved = IdentityService.instance().resolve_user("telegram", tg_user_id)
                if not resolved:
                    flowfile.set_content(
                        "Access denied. Your Telegram account is not linked to a PyFi2 user.\n"
                        "Ask an administrator to link your account from the web chat using:\n"
                        "/link telegram YOUR_TELEGRAM_USER_ID"
                        .encode("utf-8")
                    )
                    return [flowfile]

        # Check for action-based requests (list/load/delete conversations)
        use_conv_store = self.config.get("conversation_store", False)
        if use_conv_store:
            # Detect context-mutating operations and pause FlowFile processing
            _ctx_op_conv_id = self._detect_context_op(flowfile)
            if _ctx_op_conv_id:
                self.cancel_agent(_ctx_op_conv_id, silent=True)
                if not self._acquire_context_op(_ctx_op_conv_id, timeout=30.0):
                    flowfile.set_content(json.dumps({
                        "error": "Timeout waiting for active agent to finish",
                    }).encode())
                    flowfile.set_attribute("http.response.status", "409")
                    return [flowfile]
                try:
                    action_result = self._handle_action(flowfile)
                finally:
                    self._release_context_op(_ctx_op_conv_id)
            else:
                action_result = self._handle_action(flowfile)
            if action_result is not None:
                return action_result

        streaming = self.config.get("streaming", False)
        if streaming:
            return self._execute_streaming(flowfile)
        return self._execute_sync(flowfile)

    def _handle_action(self, flowfile: FlowFile) -> Optional[List[FlowFile]]:
        """Handle action-based requests (list/load/delete conversations).

        Returns None if the request is not an action (i.e. a normal message).
        Also handles Telegram /conv commands for cross-channel conversation management.
        """
        raw_body = flowfile.get_content().decode("utf-8", errors="replace")

        # Handle Telegram /conv commands (text-based, not JSON)
        tg_user_id = flowfile.get_attribute("telegram.user_id") or ""
        if tg_user_id and raw_body.strip().startswith("/conv"):
            result = self._handle_telegram_conv_command(
                raw_body.strip(), tg_user_id, flowfile,
            )
            if result is not None:
                return result

        if not raw_body.strip().startswith("{"):
            return None
        try:
            body = json.loads(raw_body)
        except json.JSONDecodeError:
            return None
        if not isinstance(body, dict) or "action" not in body:
            return None

        action = body["action"]
        user_id = flowfile.get_attribute("http.auth.principal") or ""

        from core.conversation_store import ConversationStore
        store = ConversationStore.instance()

        if action == "list_conversations":
            convs = store.list_conversations(user_id=user_id)
            result = json.dumps({"conversations": convs}, ensure_ascii=False)
            flowfile.set_content(result.encode("utf-8"))
            return [flowfile]

        if action == "load_history":
            conv_id = body.get("conversation_id", "")
            if not conv_id:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            messages = store.load(conv_id, user_id=user_id)
            if messages is None:
                flowfile.set_content(json.dumps({"error": "Conversation not found"}).encode())
                flowfile.set_attribute("http.response.status", "404")
                return [flowfile]
            # Return all display-relevant messages with type classification
            history = self._classify_messages_for_display(messages)
            nicknames = store.get_extra(conv_id, "agent_nicknames") or {}
            active_res = store.get_extra(conv_id, "active_resources") or {}
            result = json.dumps({
                "conversation_id": conv_id,
                "messages": history,
                "message_count": len(messages),  # total raw count for polling
                "nicknames": nicknames,
                "active_agent": active_res.get("agent", ""),
            }, ensure_ascii=False)
            flowfile.set_content(result.encode("utf-8"))
            return [flowfile]

        if action == "delete_conversation":
            conv_id = body.get("conversation_id", "")
            if not conv_id:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            # Collect file IDs from conversation before deleting
            history = store.load(conv_id, user_id=user_id)
            if history:
                self._cleanup_conversation_files(history)
            # Cascade cleanup: flows, dynamic tools, secrets
            self._cleanup_conversation_resources(conv_id)
            deleted = store.delete(conv_id, user_id=user_id)
            logger.info(f"[action] delete_conversation {conv_id}: deleted={deleted}, "
                        f"user_id={user_id}")
            result = json.dumps({"deleted": deleted, "conversation_id": conv_id})
            flowfile.set_content(result.encode("utf-8"))
            return [flowfile]

        if action == "set_agent_nickname":
            conv_id = body.get("conversation_id", "")
            agent_name = body.get("agent_name", "").strip()
            nickname = body.get("nickname", "").strip()
            if agent_name and conv_id:
                agent_name = self._resolve_agent_name(agent_name, conv_id)
            if not conv_id or not agent_name or not nickname:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id, agent_name, or nickname"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            nicknames = store.get_extra(conv_id, "agent_nicknames") or {}
            nicknames[agent_name] = nickname
            store.set_extra(conv_id, "agent_nicknames", nicknames)
            flowfile.set_content(json.dumps({
                "ok": True, "agent_name": agent_name, "nickname": nickname,
            }).encode())
            return [flowfile]

        if action == "cancel":
            conv_id = body.get("conversation_id", "")
            agent_name = body.get("agent_name", "")
            if agent_name:
                agent_name = self._resolve_agent_name(agent_name, conv_id)
            if not conv_id:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            self.cancel_agent(conv_id, agent_name=agent_name)
            flowfile.set_content(json.dumps({
                "cancelled": True, "conversation_id": conv_id,
                "agent_name": agent_name or "all",
            }).encode())
            return [flowfile]

        if action == "cost":
            # Read persistent stats from TokenTracker (survives restarts)
            from core.token_tracker import TokenTracker
            from gui.services.global_service_registry import GlobalServiceRegistry
            tracker = TokenTracker.instance()
            usage = tracker.get_usage(user_id)
            agents_data = usage.get("agents", {})
            req_agent = body.get("agent", "ALL")

            # Build service cost info from registry
            greg = GlobalServiceRegistry.get_instance()
            svc_costs = {}
            for svc_id, svc_def in greg.get_all_definitions().items():
                if getattr(svc_def, "service_type", "") == "llmConnection":
                    svc_costs[svc_id] = {
                        "cost_per_1m_input": float(svc_def.config.get("cost_per_1m_input", 0) or 0),
                        "cost_per_1m_output": float(svc_def.config.get("cost_per_1m_output", 0) or 0),
                    }

            stats = []
            for key, agent_stats in agents_data.items():
                agent_name = agent_stats.get("agent", "assistant")
                svc_id = agent_stats.get("llm_service", "default")
                # Filter by agent
                if req_agent.upper() != "ALL" and agent_name.lower() != req_agent.lower():
                    continue
                tok_in = agent_stats.get("in", 0)
                tok_out = agent_stats.get("out", 0)
                calls = agent_stats.get("calls", 0)
                costs = svc_costs.get(svc_id, {})
                cost_in_1m = costs.get("cost_per_1m_input", 0)
                cost_out_1m = costs.get("cost_per_1m_output", 0)
                cost = 0.0
                if cost_in_1m or cost_out_1m:
                    cost = round(tok_in / 1_000_000 * cost_in_1m +
                                 tok_out / 1_000_000 * cost_out_1m, 6)
                stats.append({
                    "agent": agent_name, "llm_service": svc_id,
                    "tokens_in": tok_in, "tokens_out": tok_out,
                    "calls": calls, "cost": cost,
                    "cost_per_1m_input": cost_in_1m,
                    "cost_per_1m_output": cost_out_1m,
                })

            flowfile.set_content(json.dumps({
                "services": stats,
                "total_in": usage.get("total_in", 0),
                "total_out": usage.get("total_out", 0),
            }, ensure_ascii=False).encode())
            return [flowfile]

        if action == "list_active":
            conv_id = body.get("conversation_id", "")
            if not conv_id:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            now = time.time()
            active = []
            with self._interactions_lock:
                for key, info in list(self._active_interactions.items()):
                    if info.get("conversation_id") != conv_id:
                        continue
                    # Auto-cleanup stale entries (>10 min)
                    if now - info.get("started_at", now) > 600:
                        self._active_interactions.pop(key, None)
                        continue
                    active.append({
                        "agent_name": info.get("agent_name", "assistant"),
                        "message_preview": info.get("message_preview", ""),
                        "duration_s": round(now - info.get("started_at", now), 1),
                        "iteration": info.get("iteration", 0),
                        "last_tool": info.get("last_tool", ""),
                        "status": info.get("status", "thinking"),
                    })
            flowfile.set_content(json.dumps({"active": active}).encode())
            return [flowfile]

        if action == "interrupt":
            conv_id = body.get("conversation_id", "")
            agent_name = body.get("agent_name", "")
            if agent_name:
                agent_name = self._resolve_agent_name(agent_name, conv_id)
            if not conv_id:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            self.interrupt_agent(conv_id, agent_name)
            flowfile.set_content(json.dumps({
                "interrupted": True, "conversation_id": conv_id,
                "agent_name": agent_name or "assistant",
            }).encode())
            return [flowfile]

        if action == "btw":
            conv_id = body.get("conversation_id", "")
            agent_name = body.get("agent_name", "")
            if agent_name and agent_name.upper() != "ALL":
                agent_name = self._resolve_agent_name(agent_name, conv_id)
            question = body.get("message", "")
            if not conv_id or not question:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id or message"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            user_id = flowfile.get_attribute("http.auth.principal") or ""
            # Handle ALL — spawn btw for each agent + default
            if agent_name.upper() == "ALL":
                from core.resource_store import ResourceStore
                rs = ResourceStore.instance()
                all_agents = rs.list_all("agent", user_id)
                targets = ["assistant"] + [a["name"] for a in all_agents]
                for t in targets:
                    thread = threading.Thread(
                        target=self._btw_query,
                        args=(conv_id, t, question, user_id),
                        daemon=True,
                        name=f"btw-{t}-{conv_id[:8]}",
                    )
                    thread.start()
            else:
                thread = threading.Thread(
                    target=self._btw_query,
                    args=(conv_id, agent_name, question, user_id),
                    daemon=True,
                    name=f"btw-{agent_name or 'assistant'}-{conv_id[:8]}",
                )
                thread.start()
            flowfile.set_content(json.dumps({
                "ok": True, "conversation_id": conv_id,
            }).encode())
            return [flowfile]

        if action == "restart_from":
            conv_id = body.get("conversation_id", "")
            _rf_agent = body.get("agent_name", "")
            keep_last = int(body.get("keep_last", 5))
            if not conv_id:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            _rf_msgs = store.load(conv_id, user_id=user_id)
            if not _rf_msgs:
                flowfile.set_content(json.dumps({"error": "Conversation not found"}).encode())
                flowfile.set_attribute("http.response.status", "404")
                return [flowfile]

            def _do_restart():
                deserialized = self._deserialize_messages(_rf_msgs)
                system_msgs = [m for m in deserialized if m.role == "system"]
                non_system = [m for m in deserialized if m.role != "system"]
                if keep_last == 0:
                    new_context = system_msgs
                else:
                    kept = non_system[-keep_last:] if len(non_system) > keep_last else non_system
                    new_context = system_msgs + kept
                serialized_ctx = self._serialize_messages(new_context)
                store.save_agent_context(conv_id, _rf_agent, serialized_ctx)
                return {"kept_messages": len(new_context) - len(system_msgs),
                        "agent": _rf_agent or "shared"}

            return self._run_bg_context_op(conv_id, "restart_from", _do_restart, flowfile)

        if action == "delete_message":
            conv_id = body.get("conversation_id", "")
            msg_index = body.get("index")
            if not conv_id or msg_index is None:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id or index"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            deleted = store.delete_message(conv_id, int(msg_index), user_id=user_id)
            flowfile.set_content(json.dumps({
                "deleted": deleted, "conversation_id": conv_id,
                "message_count": store.message_count(conv_id),
            }).encode())
            return [flowfile]

        if action == "resume_conversation":
            conv_id = body.get("conversation_id", "")
            _rs_agent = body.get("agent_name", "")
            max_summary_tokens = int(body.get("max_tokens", 500))
            if not conv_id:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            _rs_msgs = store.load(conv_id, user_id=user_id)
            if not _rs_msgs:
                flowfile.set_content(json.dumps({"error": "Conversation not found"}).encode())
                flowfile.set_attribute("http.response.status", "404")
                return [flowfile]
            # Resolve LLM client
            _summ_client, _ = self._get_summarizer_client(user_id)
            _rs_client = _summ_client
            if not _rs_client:
                _rs_client, _ = self._resolve_client(
                    self.config.get("llm_service", "default"),
                    user_id, resolve_expressions=False,
                )
            if not _rs_client:
                flowfile.set_content(json.dumps({"error": "No LLM service for summarization"}).encode())
                return [flowfile]

            def _do_resume():
                deserialized = self._deserialize_messages(_rs_msgs)
                content_msgs = [m for m in deserialized if m.role != "system"]
                context_max = int(self.config.get("max_context_size", 64000))
                # Resolve agent's max_tokens
                if _rs_agent:
                    try:
                        from core.resource_store import ResourceStore as _RS_r
                        _ad = _RS_r.instance().get_any("agent", _rs_agent, user_id)
                        if _ad and _ad.get("llm_service"):
                            _sid = _ad["llm_service"]
                            if "${" in _sid:
                                from core.expression import resolve_expression as _re_r
                                _sid = _re_r(_sid, owner=user_id)
                            if _sid and "${" not in _sid:
                                _, _sv = self._resolve_llm_service(_sid, user_id)
                                if _sv:
                                    _v = int((getattr(_sv, 'config', {}) or {}).get("max_context_size", 0))
                                    if _v:
                                        context_max = _v
                    except Exception:
                        pass
                summary = self._summarize_messages(
                    content_msgs, _rs_client, context_max,
                    target_tokens=max_summary_tokens,
                    conversation_id=conv_id,
                )
                sys_prompt = self.config.get("system_prompt", "You are a helpful assistant.")
                from datetime import datetime
                sys_prompt += f"\n\nCurrent date and time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                new_context = [
                    LLMMessage(role="system", content=sys_prompt),
                    LLMMessage(role="user",
                               content=f"[Conversation summary — earlier messages compacted]\n\n{summary}"),
                    LLMMessage(role="assistant",
                               content="Understood. I have the context from our earlier conversation. Continuing from where we left off."),
                ]
                store.save_agent_context(conv_id, _rs_agent, self._serialize_messages(new_context))
                return {"summary_length": len(summary),
                        "messages_summarized": len(_rs_msgs),
                        "agent": _rs_agent or "shared"}

            return self._run_bg_context_op(conv_id, "summary", _do_resume, flowfile)

        if action == "ping":
            # Keep-alive: session renewal happens in validateSessionAuth upstream
            flowfile.set_content(json.dumps({"status": "ok"}).encode())
            return [flowfile]

        if action == "broadcast_agents":
            # Send the same message to ALL defined agents in parallel
            conv_id = body.get("conversation_id", "")
            message = body.get("message", "")
            if not conv_id or not message:
                flowfile.set_content(json.dumps({
                    "error": "conversation_id and message are required",
                }).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            # Launch broadcast in background thread
            thread = threading.Thread(
                target=self._broadcast_agents,
                args=(conv_id, message, user_id),
                daemon=True,
                name=f"broadcast-{conv_id[:8]}",
            )
            thread.start()
            flowfile.set_content(json.dumps({
                "status": "broadcasting",
                "conversation_id": conv_id,
            }).encode())
            return [flowfile]

        if action == "poll":
            # Efficient delta check: client sends last known message_count,
            # server returns new messages only if count increased.
            conv_id = body.get("conversation_id", "")
            last_count = int(body.get("last_count", 0))
            if not conv_id:
                flowfile.set_content(json.dumps({"new_messages": []}).encode())
                return [flowfile]
            current_count = store.message_count(conv_id)
            if current_count <= last_count:
                flowfile.set_content(json.dumps({
                    "new_messages": [], "message_count": current_count,
                }).encode())
                return [flowfile]
            # Load full history and return only the new portion
            all_messages = store.load(conv_id, user_id=user_id)
            if all_messages is None:
                flowfile.set_content(json.dumps({
                    "new_messages": [], "message_count": 0,
                }).encode())
                return [flowfile]
            new_raw = all_messages[last_count:]
            new_classified = self._classify_messages_for_display(new_raw)
            flowfile.set_content(json.dumps({
                "new_messages": new_classified,
                "message_count": len(all_messages),
            }, ensure_ascii=False).encode())
            return [flowfile]

        if action == "add_secret":
            key = body.get("key", "").strip()
            value = body.get("value", "")
            if not key or not value:
                flowfile.set_content(json.dumps({"error": "key and value are required"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            uid = user_id or "anonymous"
            from pathlib import Path
            from core.secrets import get_secrets_manager
            sm = get_secrets_manager()
            encrypted = sm.encrypt(value)
            secrets_path = Path("config/users") / uid / "secrets.json"
            secrets_path.parent.mkdir(parents=True, exist_ok=True)
            secrets = {}
            if secrets_path.exists():
                try:
                    secrets = json.loads(secrets_path.read_text(encoding="utf-8"))
                except Exception:
                    pass
            secrets[key] = encrypted
            secrets_path.write_text(json.dumps(secrets, ensure_ascii=False, indent=2), encoding="utf-8")
            flowfile.set_content(json.dumps({
                "result": f"Secret '{key}' stored. Use ${{secrets.user.{key}}} in flows.",
                "key": key,
            }).encode())
            return [flowfile]

        if action == "add_variable":
            key = body.get("key", "").strip()
            value = body.get("value", "")
            if not key or not value:
                flowfile.set_content(json.dumps({"error": "key and value are required"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            uid = user_id or "anonymous"
            from pathlib import Path
            params_path = Path("config/users") / uid / "parameters.json"
            params_path.parent.mkdir(parents=True, exist_ok=True)
            params = {}
            if params_path.exists():
                try:
                    params = json.loads(params_path.read_text(encoding="utf-8"))
                except Exception:
                    pass
            params[key] = value
            params_path.write_text(json.dumps(params, ensure_ascii=False, indent=2), encoding="utf-8")
            flowfile.set_content(json.dumps({
                "result": f"Parameter '{key}' stored. Use ${{user.{key}}} in flows.",
                "key": key,
            }).encode())
            return [flowfile]

        if action == "list_secrets":
            uid = user_id or "anonymous"
            from pathlib import Path
            secrets_path = Path("config/users") / uid / "secrets.json"
            if not secrets_path.exists():
                flowfile.set_content(json.dumps({"result": "No secrets stored."}).encode())
                return [flowfile]
            try:
                secrets = json.loads(secrets_path.read_text(encoding="utf-8"))
            except Exception:
                flowfile.set_content(json.dumps({"result": "Error reading secrets."}).encode())
                return [flowfile]
            if not secrets:
                flowfile.set_content(json.dumps({"result": "No secrets stored."}).encode())
                return [flowfile]
            lines = [f"Secrets ({len(secrets)}):"]
            for k in sorted(secrets.keys()):
                lines.append(f"- {k} → ${{secrets.user.{k}}}")
            flowfile.set_content(json.dumps({"result": "\n".join(lines)}).encode())
            return [flowfile]

        if action == "list_variables":
            uid = user_id or "anonymous"
            from pathlib import Path
            params_path = Path("config/users") / uid / "parameters.json"
            if not params_path.exists():
                flowfile.set_content(json.dumps({"result": "No parameters stored."}).encode())
                return [flowfile]
            try:
                params = json.loads(params_path.read_text(encoding="utf-8"))
            except Exception:
                flowfile.set_content(json.dumps({"result": "Error reading parameters."}).encode())
                return [flowfile]
            if not params:
                flowfile.set_content(json.dumps({"result": "No parameters stored."}).encode())
                return [flowfile]
            lines = [f"Parameters ({len(params)}):"]
            for k, v in sorted(params.items()):
                lines.append(f"- {k} = {v} → ${{user.{k}}}")
            flowfile.set_content(json.dumps({"result": "\n".join(lines)}).encode())
            return [flowfile]

        if action == "file_result":
            # Browser responding to a local_files tool request
            request_id = body.get("request_id", "")
            result = body.get("result", {})
            if not request_id:
                flowfile.set_content(json.dumps({"error": "Missing request_id"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            from core.tool_registry import LocalFilesHandler
            LocalFilesHandler.resolve_request(request_id, result)
            flowfile.set_content(json.dumps({"status": "ok"}).encode())
            return [flowfile]

        if action == "exec_result":
            # User responding to a remote_exec approval request
            request_id = body.get("request_id", "")
            result = body.get("result", {})
            if not request_id:
                flowfile.set_content(json.dumps({"error": "Missing request_id"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            from core.tool_registry import RemoteExecutorHandler
            RemoteExecutorHandler.resolve_request(request_id, result)
            flowfile.set_content(json.dumps({"status": "ok"}).encode())
            return [flowfile]

        if action == "tool_approval_result":
            # Plan A: User responding to a universal tool approval dialog
            request_id = body.get("request_id", "")
            result = body.get("result", {})
            if not request_id:
                flowfile.set_content(json.dumps({"error": "Missing request_id"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            from core.tool_approval import ToolApprovalGate
            ToolApprovalGate.resolve_request(request_id, result)
            flowfile.set_content(json.dumps({"status": "ok"}).encode())
            return [flowfile]

        if action == "list_schedules":
            conv_id = body.get("conversation_id", "")
            from core.poll_scheduler import PollScheduler
            all_scheds = PollScheduler.instance().list_all()
            # Filter to current conversation
            scheds = [s for s in all_scheds if s["conversation_id"] == conv_id]
            flowfile.set_content(json.dumps({"schedules": scheds}, ensure_ascii=False).encode())
            return [flowfile]

        if action == "add_schedule":
            conv_id = body.get("conversation_id", "")
            at_str = body.get("at", "")
            reason = body.get("reason", "manual schedule")
            if not conv_id or not at_str:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id or at"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            from datetime import datetime, timezone as tz
            from core.poll_scheduler import PollScheduler
            try:
                dt = datetime.strptime(at_str, "%Y%m%d%H%M%S")
                dt = dt.replace(tzinfo=tz.utc)
                recheck_at = dt.timestamp()
            except ValueError:
                flowfile.set_content(json.dumps({"error": f"Invalid date: {at_str}"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            PollScheduler.instance().schedule(conv_id, recheck_at, user_id, reason)
            store.set_status(conv_id, "active")
            flowfile.set_content(json.dumps({"scheduled": True, "at": recheck_at}).encode())
            return [flowfile]

        if action == "delete_schedule":
            conv_id = body.get("conversation_id", "")
            from core.poll_scheduler import PollScheduler
            cancelled = PollScheduler.instance().cancel(conv_id)
            flowfile.set_content(json.dumps({"cancelled": cancelled}).encode())
            return [flowfile]

        if action == "list_conv_files":
            conv_id = body.get("conversation_id", "")
            if not conv_id:
                flowfile.set_content(json.dumps({"files": []}).encode())
                return [flowfile]
            messages_data = store.load(conv_id, user_id=user_id)
            if not messages_data:
                flowfile.set_content(json.dumps({"files": []}).encode())
                return [flowfile]
            import re as _re
            from core.file_store import FileStore
            fstore = FileStore.instance()
            pattern = _re.compile(r'/files/([a-f0-9]{12})/([^\s"<>]+)')
            seen = set()
            files = []
            for msg in messages_data:
                content = msg.get("content", "")
                if not isinstance(content, str):
                    continue
                for match in pattern.finditer(content):
                    fid = match.group(1)
                    fname = match.group(2)
                    if fid in seen:
                        continue
                    seen.add(fid)
                    available = fstore.exists(fid)
                    files.append({
                        "file_id": fid, "filename": fname,
                        "available": available,
                    })
            flowfile.set_content(json.dumps({"files": files}, ensure_ascii=False).encode())
            return [flowfile]

        if action == "delete_file":
            file_id = body.get("file_id", "")
            conv_id = body.get("conversation_id", "")
            if not file_id:
                flowfile.set_content(json.dumps({"error": "Missing file_id"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            # Verify the file belongs to a conversation owned by this user
            if conv_id and user_id:
                conv_data = store.load(conv_id, user_id=user_id)
                if conv_data is None:
                    flowfile.set_content(json.dumps({"error": "Access denied"}).encode())
                    flowfile.set_attribute("http.response.status", "403")
                    return [flowfile]
                # Verify file_id is referenced in this conversation
                import re as _re_del
                found = any(
                    file_id in (m.get("content", "") if isinstance(m.get("content"), str) else "")
                    for m in conv_data
                )
                if not found:
                    flowfile.set_content(json.dumps({"error": "File not in this conversation"}).encode())
                    flowfile.set_attribute("http.response.status", "403")
                    return [flowfile]
            from core.file_store import FileStore
            fstore = FileStore.instance()
            if not fstore.exists(file_id):
                flowfile.set_content(json.dumps({"error": "File not found"}).encode())
                flowfile.set_attribute("http.response.status", "404")
                return [flowfile]
            fstore.delete(file_id)
            flowfile.set_content(json.dumps({"ok": True, "file_id": file_id}).encode())
            return [flowfile]

        if action == "list_conv_flows":
            # Show all flows belonging to this user (not conversation-scoped)
            try:
                from gui.services.deployment_registry import DeploymentRegistry
                dep_reg = DeploymentRegistry.get_instance()
                dep_reg.sync_with_executors()
                uid = user_id or None
                instances = dep_reg.get_by_owner(uid) if uid else []
                flows_list = []
                for inst in instances:
                    tasks_count = 0
                    try:
                        from pathlib import Path as _Path
                        raw = json.loads(_Path(inst.flow_path).read_text(encoding="utf-8"))
                        tasks_count = len(raw.get("tasks", {}))
                    except Exception:
                        pass
                    flows_list.append({
                        "id": inst.instance_id,
                        "name": inst.flow_name,
                        "status": inst.status,
                        "template": inst.flow_id if inst.flow_id != inst.instance_id else "",
                        "tasks_count": tasks_count,
                    })
            except Exception:
                flows_list = []
            flowfile.set_content(
                json.dumps({"flows": flows_list}, ensure_ascii=False).encode())
            return [flowfile]

        if action == "manage_conv_flow":
            flow_id = body.get("flow_id", "")
            flow_action = body.get("flow_action", "")
            if not flow_id or not flow_action:
                flowfile.set_content(json.dumps(
                    {"error": "flow_id and flow_action required"}).encode())
                return [flowfile]

            from gui.services.deployment_registry import DeploymentRegistry
            dep_reg = DeploymentRegistry.get_instance()
            inst = dep_reg.get(flow_id)
            if not inst:
                flowfile.set_content(json.dumps(
                    {"error": f"Flow '{flow_id}' not found"}).encode())
                return [flowfile]
            # Ownership check
            if user_id and inst.owner != user_id:
                flowfile.set_content(json.dumps(
                    {"error": "Permission denied"}).encode())
                return [flowfile]

            if flow_action == "start":
                try:
                    from gui.services.executor_registry import ExecutorRegistry
                    from engine.parser import FlowParser
                    from engine.continuous_executor import ContinuousFlowExecutor
                    from tasks import register_all_tasks
                    register_all_tasks()
                    raw = json.loads(
                        open(inst.flow_path, encoding="utf-8").read())
                    clean = {k: v for k, v in raw.items()
                             if not k.startswith("_")}
                    if inst.parameters:
                        clean.setdefault("parameters", {}).update(inst.parameters)
                    flow = FlowParser.parse(clean)
                    reg = ExecutorRegistry.get_instance()
                    existing = reg.get(flow_id)
                    if existing:
                        try:
                            existing.stop()
                        except Exception:
                            pass
                        reg.unregister(flow_id)
                    executor = ContinuousFlowExecutor(
                        flow, max_workers=inst.max_workers,
                        max_retries=inst.max_retries,
                        parameters=inst.parameters or None)
                    executor.start()
                    reg.register(flow_id, executor)
                    flowfile.set_content(json.dumps(
                        {"message": f"Flow '{flow_id}' started"}).encode())
                except Exception as e:
                    dep_reg.update_status(flow_id, "error", str(e))
                    flowfile.set_content(json.dumps(
                        {"error": f"Start failed: {e}"}).encode())

            elif flow_action == "stop":
                try:
                    from gui.services.executor_registry import ExecutorRegistry
                    reg = ExecutorRegistry.get_instance()
                    ex = reg.get(flow_id)
                    if ex:
                        ex.stop()
                        reg.unregister(flow_id)
                    flowfile.set_content(json.dumps(
                        {"message": f"Flow '{flow_id}' stopped"}).encode())
                except Exception as e:
                    flowfile.set_content(json.dumps(
                        {"error": f"Stop failed: {e}"}).encode())

            elif flow_action == "delete":
                try:
                    from gui.services.executor_registry import ExecutorRegistry
                    reg = ExecutorRegistry.get_instance()
                    ex = reg.get(flow_id)
                    if ex:
                        ex.stop()
                        reg.unregister(flow_id)
                    dep_reg.undeploy(flow_id)
                    flowfile.set_content(json.dumps(
                        {"message": f"Flow '{flow_id}' deleted"}).encode())
                except Exception as e:
                    flowfile.set_content(json.dumps(
                        {"error": f"Delete failed: {e}"}).encode())
            else:
                flowfile.set_content(json.dumps(
                    {"error": f"Unknown action: {flow_action}"}).encode())
            return [flowfile]

        # ── Per-agent context routing helpers ───────────────────────
        # All context actions below support agent_name param.
        # "ALL" means apply to all agents with diverged contexts.
        def _ctx_load(conv_id, agent_name=""):
            """Load context for an agent (falls back to shared → messages)."""
            if agent_name and agent_name != "ALL":
                return store.load_agent_context(conv_id, agent_name)
            return store.load_context(conv_id, user_id=user_id)

        def _ctx_save(conv_id, data, agent_name=""):
            """Save context for an agent (or shared if no agent)."""
            if agent_name and agent_name != "ALL":
                store.save_agent_context(conv_id, agent_name, data)
            else:
                store.save_context(conv_id, data)

        def _resolve_agent_max_tokens(agent_name):
            """Get max_tokens from an agent's LLM service config."""
            try:
                from core.resource_store import ResourceStore
                adef = ResourceStore.instance().get_any("agent", agent_name, user_id)
                if adef and adef.get("llm_service"):
                    svc_id = adef["llm_service"]
                    if "${" in svc_id:
                        from core.expression import resolve_expression
                        svc_id = resolve_expression(svc_id, owner=user_id)
                    if svc_id and "${" not in svc_id:
                        _, svc = self._resolve_llm_service(svc_id, user_id)
                        if svc:
                            v = int((getattr(svc, 'config', {}) or {}).get("max_context_size", 0))
                            if v:
                                return v
            except Exception:
                pass
            return 0

        def _ctx_max_tokens(agent_name=""):
            """Get max_context_size for an agent or shared context.

            For a specific agent: use that agent's LLM service max_tokens.
            For shared ("" or "ALL"): use the LARGEST max_tokens among all
            agents (the shared context must fit the biggest consumer).
            """
            flow_default = int(self.config.get("max_context_size", 64000))
            if agent_name and agent_name not in ("", "ALL"):
                return _resolve_agent_max_tokens(agent_name) or flow_default
            # Shared: max of all agent LLM services
            try:
                from core.resource_store import ResourceStore
                all_agents = ResourceStore.instance().list_all("agent", user_id)
                max_val = 0
                for a in all_agents:
                    v = _resolve_agent_max_tokens(a["name"])
                    if v > max_val:
                        max_val = v
                # Also check the default LLM service
                default_svc = self.config.get("llm_service", "default")
                if default_svc and "${" not in default_svc:
                    _, svc = self._resolve_llm_service(default_svc, user_id)
                    if svc:
                        v = int((getattr(svc, 'config', {}) or {}).get("max_context_size", 0))
                        if v > max_val:
                            max_val = v
                return max_val or flow_default
            except Exception:
                return flow_default

        if action == "compact":
            conv_id = body.get("conversation_id", "")
            _ctx_agent = body.get("agent_name", "")
            if not conv_id:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            # Load source data
            context_data = _ctx_load(conv_id, _ctx_agent)
            source_data = context_data if context_data is not None else store.load(conv_id, user_id=user_id)
            if not source_data or len(source_data) < 4:
                flowfile.set_content(json.dumps({"error": "Not enough messages to compact"}).encode())
                return [flowfile]
            # Resolve client
            _summ_client, _ = self._get_summarizer_client(user_id)
            if _summ_client:
                _compact_client = _summ_client
            else:
                svc_id = self.config.get("llm_service", "")
                if not svc_id or "${" in svc_id:
                    svc_id = "default"
                _compact_client, _ = self._resolve_client(
                    svc_id, user_id, resolve_expressions=False,
                )
            if not _compact_client:
                flowfile.set_content(json.dumps({"error": "LLM service not found"}).encode())
                return [flowfile]
            _compact_max = _ctx_max_tokens(_ctx_agent)
            _compact_source = source_data
            _compact_conv = conv_id
            _compact_agent_name = _ctx_agent
            _compact_keep = int(self.config.get("context_keep_recent", 6))

            def _do_compact():
                msgs = self._deserialize_messages(_compact_source)
                before = len(msgs)
                estimated = self._estimate_tokens(msgs)
                compacted = self._compact_if_needed(
                    msgs, _compact_client, _compact_max, 0.5,
                    _compact_keep, conversation_id=_compact_conv,
                    agent_name=_compact_agent_name,
                )
                after_tokens = self._estimate_tokens(compacted)
                return {"before": before, "after": len(compacted),
                        "tokens_before": estimated, "tokens_after": after_tokens,
                        "agent": _compact_agent_name or "shared"}

            return self._run_bg_context_op(conv_id, "compact", _do_compact, flowfile)

        if action == "rebuild":
            conv_id = body.get("conversation_id", "")
            _rb_agent = body.get("agent_name", "")
            if not conv_id:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            _rb_msgs = store.load(conv_id, user_id=user_id)
            if not _rb_msgs:
                flowfile.set_content(json.dumps({"error": "Conversation not found"}).encode())
                flowfile.set_attribute("http.response.status", "404")
                return [flowfile]
            # Resolve client for potential compaction
            _summ_client, _ = self._get_summarizer_client(user_id)
            _rb_client = _summ_client
            if not _rb_client:
                _rb_client, _ = self._resolve_client(
                    self.config.get("llm_service", "default"),
                    user_id, resolve_expressions=False,
                )
            _rb_max = _ctx_max_tokens(_rb_agent)

            def _do_rebuild():
                deserialized = self._deserialize_messages(_rb_msgs)
                estimated = self._estimate_tokens(deserialized)
                limit = int(_rb_max * 0.8)
                if estimated <= limit:
                    _ctx_save(conv_id, _rb_msgs, _rb_agent)
                    return {"action": "full_restore", "before": len(_rb_msgs),
                            "after": len(_rb_msgs), "tokens_after": estimated}
                if not _rb_client:
                    raise ValueError("No LLM service for compaction")
                compacted = self._compact_if_needed(
                    deserialized, _rb_client, _rb_max, 0.8,
                    int(self.config.get("context_keep_recent", 6)),
                    conversation_id=conv_id, agent_name=_rb_agent,
                )
                return {"action": "compacted", "before": len(_rb_msgs),
                        "after": len(compacted),
                        "tokens_after": self._estimate_tokens(compacted)}

            return self._run_bg_context_op(conv_id, "rebuild", _do_rebuild, flowfile)

        if action in ("rebuild_clean", "rebuild_full"):
            conv_id = body.get("conversation_id", "")
            _rf_agent = body.get("agent_name", "")
            if not conv_id:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            _rf_msgs = store.load(conv_id, user_id=user_id)
            if not _rf_msgs:
                flowfile.set_content(json.dumps({"error": "Conversation not found"}).encode())
                flowfile.set_attribute("http.response.status", "404")
                return [flowfile]

            def _do_rebuild_full():
                deserialized = self._deserialize_messages(_rf_msgs)
                estimated = self._estimate_tokens(deserialized)
                if _rf_agent == "ALL":
                    agent_map = store.list_agent_contexts(conv_id)
                    for name in agent_map:
                        if name == "*":
                            store.save_context(conv_id, list(_rf_msgs))
                        else:
                            store.save_agent_context(conv_id, name, list(_rf_msgs))
                else:
                    _ctx_save(conv_id, list(_rf_msgs), _rf_agent)
                return {"action": "full_restore", "messages": len(_rf_msgs),
                        "tokens_after": estimated,
                        "agent": _rf_agent or "shared"}

            return self._run_bg_context_op(conv_id, "rebuild_full", _do_rebuild_full, flowfile)
            return [flowfile]

        if action == "get_context":
            conv_id = body.get("conversation_id", "")
            _ctx_agent = body.get("agent_name", "")
            if not conv_id:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            context_data = _ctx_load(conv_id, _ctx_agent)
            diverged = context_data is not None
            if context_data is None:
                context_data = store.load(conv_id, user_id=user_id) or []
            deserialized = self._deserialize_messages(context_data)
            estimated = self._estimate_tokens(deserialized)
            # Classify messages for display
            display_msgs = []
            for m in context_data:
                role = m.get("role", "unknown")
                content = m.get("content", "")
                if isinstance(content, list):
                    text_parts = [p.get("text", "") for p in content if p.get("type") == "text"]
                    content = "\n".join(text_parts) if text_parts else str(content)
                has_tool_calls = bool(m.get("tool_calls"))
                display_msgs.append({
                    "role": role,
                    "content": content[:300] if isinstance(content, str) else str(content)[:300],
                    "has_tool_calls": has_tool_calls,
                    "source": m.get("source"),
                })
            # Include agent context status map
            _agent_ctx_map = store.list_agent_contexts(conv_id)
            flowfile.set_content(json.dumps({
                "context": display_msgs,
                "message_count": len(context_data),
                "token_estimate": estimated,
                "diverged": diverged,
                "agent_name": _ctx_agent or "",
                "agent_contexts": _agent_ctx_map,
            }, ensure_ascii=False).encode())
            return [flowfile]

        if action == "get_context_full":
            conv_id = body.get("conversation_id", "")
            _ctx_agent = body.get("agent_name", "")
            if not conv_id:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            context_data = _ctx_load(conv_id, _ctx_agent)
            diverged = context_data is not None
            if context_data is None:
                context_data = store.load(conv_id, user_id=user_id) or []
            flowfile.set_content(json.dumps({
                "context": context_data,
                "message_count": len(context_data),
                "diverged": diverged,
            }, ensure_ascii=False).encode())
            return [flowfile]

        if action == "edit_context":
            conv_id = body.get("conversation_id", "")
            _ctx_agent = body.get("agent_name", "")
            index = body.get("index")
            new_content = body.get("content", "")
            new_role = body.get("role")
            if not conv_id or index is None:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id or index"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            context_data = _ctx_load(conv_id, _ctx_agent)
            _using_context = context_data is not None
            if context_data is None:
                context_data = store.load(conv_id, user_id=user_id) or []
            if index < 0 or index >= len(context_data):
                flowfile.set_content(json.dumps({
                    "error": f"Index {index} out of range (0-{len(context_data)-1}). "
                             "The context may have changed — please refresh.",
                }).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            context_data[index]["content"] = new_content
            if new_role:
                context_data[index]["role"] = new_role
            _ctx_save(conv_id, context_data)
            deserialized = self._deserialize_messages(context_data)
            estimated = self._estimate_tokens(deserialized)
            flowfile.set_content(json.dumps({
                "ok": True,
                "message_count": len(context_data),
                "token_estimate": estimated,
            }).encode())
            return [flowfile]

        if action == "delete_context_message":
            conv_id = body.get("conversation_id", "")
            _ctx_agent = body.get("agent_name", "")
            index = body.get("index")
            if not conv_id or index is None:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id or index"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            context_data = _ctx_load(conv_id, _ctx_agent)
            if context_data is None:
                context_data = store.load(conv_id, user_id=user_id) or []
            if index < 0 or index >= len(context_data):
                # Index from overlay may target messages if context was compacted;
                # fall back to messages list
                msgs = store.load(conv_id, user_id=user_id) or []
                if 0 <= index < len(msgs):
                    msgs.pop(index)
                    store.save(conv_id, msgs, user_id=user_id)
                    deserialized = self._deserialize_messages(msgs)
                    estimated = self._estimate_tokens(deserialized)
                    flowfile.set_content(json.dumps({
                        "ok": True,
                        "message_count": len(msgs),
                        "token_estimate": estimated,
                    }).encode())
                    return [flowfile]
                flowfile.set_content(json.dumps({
                    "error": f"Index {index} out of range (0-{len(context_data)-1}). "
                             "The context may have changed — please refresh.",
                }).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            context_data.pop(index)
            _ctx_save(conv_id, context_data)
            deserialized = self._deserialize_messages(context_data)
            estimated = self._estimate_tokens(deserialized)
            flowfile.set_content(json.dumps({
                "ok": True,
                "message_count": len(context_data),
                "token_estimate": estimated,
            }).encode())
            return [flowfile]

        if action == "replace_context":
            conv_id = body.get("conversation_id", "")
            _ctx_agent = body.get("agent_name", "")
            new_context = body.get("context", [])
            if not conv_id:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            for msg in new_context:
                if "role" not in msg or "content" not in msg:
                    flowfile.set_content(json.dumps({"error": "Each message must have 'role' and 'content'"}).encode())
                    flowfile.set_attribute("http.response.status", "400")
                    return [flowfile]
            _ctx_save(conv_id, new_context)
            deserialized = self._deserialize_messages(new_context)
            estimated = self._estimate_tokens(deserialized)
            flowfile.set_content(json.dumps({
                "ok": True,
                "message_count": len(new_context),
                "token_estimate": estimated,
            }).encode())
            return [flowfile]

        if action == "add_context_message":
            conv_id = body.get("conversation_id", "")
            _ctx_agent = body.get("agent_name", "")
            role = body.get("role", "user")
            content = body.get("content", "")
            index = body.get("index")
            if not conv_id:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            context_data = _ctx_load(conv_id, _ctx_agent)
            if context_data is None:
                context_data = store.load(conv_id, user_id=user_id) or []
            msg = {"role": role, "content": content}
            if index is not None:
                context_data.insert(index, msg)
            else:
                context_data.append(msg)
            _ctx_save(conv_id, context_data)
            deserialized = self._deserialize_messages(context_data)
            estimated = self._estimate_tokens(deserialized)
            flowfile.set_content(json.dumps({
                "ok": True,
                "message_count": len(context_data),
                "token_estimate": estimated,
            }).encode())
            return [flowfile]

        if action == "create_agent":
            conv_id = body.get("conversation_id", "")
            agent_name = body.get("name", "").strip()
            agent_prompt = body.get("prompt", "").strip()
            if not agent_name or not agent_prompt:
                flowfile.set_content(json.dumps({
                    "error": "Missing name or prompt",
                }).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            from core.resource_store import ResourceStore
            rs = ResourceStore.instance()
            uid = user_id or "anonymous"
            try:
                data = {"prompt": agent_prompt}
                model = body.get("model", "")
                if model:
                    data["model"] = model
                tools = body.get("tools")
                if tools:
                    data["tools"] = tools
                description = body.get("description", "")
                if description:
                    data["description"] = description
                if rs.exists("agent", agent_name, uid):
                    rs.update("agent", agent_name, uid, data)
                else:
                    rs.create("agent", agent_name, uid, data)
                # Auto-activate in conversation
                if conv_id:
                    active = store.get_extra(conv_id, "active_resources") or {}
                    active["agent"] = agent_name
                    store.set_extra(conv_id, "active_resources", active)
                flowfile.set_content(json.dumps({
                    "created": True, "name": agent_name,
                }).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]

        if action == "list_agents":
            conv_id = body.get("conversation_id", "")
            from core.resource_store import ResourceStore
            uid = user_id or "anonymous"
            agents_list = ResourceStore.instance().list_all("agent", uid,
                                                           conversation_id=conv_id)
            agents = {a["name"]: a for a in agents_list}
            # Get selected agent from active_resources
            selected = ""
            if conv_id:
                active = store.get_extra(conv_id, "active_resources") or {}
                selected = active.get("agent", "")
            flowfile.set_content(json.dumps({
                "agents": agents, "selected": selected,
            }, ensure_ascii=False).encode())
            return [flowfile]

        if action == "agent_disable":
            conv_id = body.get("conversation_id", "")
            agent = body.get("agent_name", "")
            if not conv_id or not agent:
                flowfile.set_content(json.dumps({"error": "Missing params"}).encode())
                return [flowfile]
            disabled = store.get_extra(conv_id, "disabled_agents") or []
            if agent not in disabled:
                disabled.append(agent)
                store.set_extra(conv_id, "disabled_agents", disabled)
            flowfile.set_content(json.dumps({"result": f"Agent '{agent}' disabled in this conversation."}).encode())
            return [flowfile]

        if action == "agent_enable":
            conv_id = body.get("conversation_id", "")
            agent = body.get("agent_name", "")
            if not conv_id or not agent:
                flowfile.set_content(json.dumps({"error": "Missing params"}).encode())
                return [flowfile]
            disabled = store.get_extra(conv_id, "disabled_agents") or []
            if agent in disabled:
                disabled.remove(agent)
                store.set_extra(conv_id, "disabled_agents", disabled)
            flowfile.set_content(json.dumps({"result": f"Agent '{agent}' enabled in this conversation."}).encode())
            return [flowfile]

        if action == "agent_promote":
            conv_id = body.get("conversation_id", "")
            agent = body.get("agent_name", "")
            target_scope = body.get("target_scope", "user")
            if not agent:
                flowfile.set_content(json.dumps({"error": "Missing agent_name"}).encode())
                return [flowfile]
            from core.resource_store import ResourceStore, GLOBAL_USER_ID
            rs = ResourceStore.instance()
            item = rs.get_any("agent", agent, user_id, conversation_id=conv_id)
            if not item:
                flowfile.set_content(json.dumps({"error": f"Agent '{agent}' not found"}).encode())
                return [flowfile]
            current_scope = item.get("_scope", "user")
            promote_data = {k: v for k, v in item.items() if not k.startswith("_") and k != "name"}
            if target_scope == "user":
                rs.create("agent", agent, user_id, promote_data)
            elif target_scope == "global":
                rs.create("agent", agent, GLOBAL_USER_ID, promote_data)
            elif target_scope == "conversation" and conv_id:
                conv_agents = store.get_extra(conv_id, "conversation_agents") or {}
                conv_agents[agent] = promote_data
                store.set_extra(conv_id, "conversation_agents", conv_agents)
            flowfile.set_content(json.dumps({
                "result": f"Agent '{agent}' promoted from {current_scope} to {target_scope}."
            }).encode())
            return [flowfile]

        if action == "create_agent":
            conv_id = body.get("conversation_id", "")
            agent = body.get("name", "")
            prompt = body.get("prompt", "")
            scope = body.get("scope", "user")
            if not agent or not prompt:
                flowfile.set_content(json.dumps({"error": "Missing name or prompt"}).encode())
                return [flowfile]
            agent_data = {"prompt": prompt}
            if scope == "conversation" and conv_id:
                conv_agents = store.get_extra(conv_id, "conversation_agents") or {}
                conv_agents[agent] = agent_data
                store.set_extra(conv_id, "conversation_agents", conv_agents)
            else:
                from core.resource_store import ResourceStore
                ResourceStore.instance().create("agent", agent, user_id, agent_data)
            flowfile.set_content(json.dumps({
                "result": f"Agent '{agent}' created (scope: {scope})."
            }).encode())
            return [flowfile]

        if action == "set_llm_service":
            conv_id = body.get("conversation_id", "")
            agent = body.get("agent_name", "assistant")
            svc_value = body.get("llm_service", "")
            if not conv_id:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
                return [flowfile]
            overrides = store.get_extra(conv_id, "agent_llm_overrides") or {}
            if svc_value == "restore" or svc_value == "":
                overrides.pop(agent, None)
                store.set_extra(conv_id, "agent_llm_overrides", overrides)
                flowfile.set_content(json.dumps({
                    "result": f"LLM service for '{agent}' restored to default."
                }).encode())
            else:
                # Accept expressions like ${global.xxx} or direct service names
                overrides[agent] = svc_value
                store.set_extra(conv_id, "agent_llm_overrides", overrides)
                flowfile.set_content(json.dumps({
                    "result": f"LLM service for '{agent}' set to '{svc_value}' in this conversation."
                }).encode())
            return [flowfile]

        if action == "select_agent":
            conv_id = body.get("conversation_id", "")
            agent_name = body.get("name", "").strip()
            if agent_name:
                agent_name = self._resolve_agent_name(agent_name, conv_id)
            if not conv_id:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            # "assistant" is the built-in default — treat as deselect
            is_default = not agent_name or agent_name.lower() == "assistant"
            if not is_default:
                from core.resource_store import ResourceStore
                uid = user_id or "anonymous"
                if ResourceStore.instance().get_any("agent", agent_name, uid) is None:
                    flowfile.set_content(json.dumps({
                        "error": f"Agent '{agent_name}' not found",
                    }).encode())
                    flowfile.set_attribute("http.response.status", "404")
                    return [flowfile]
            active = store.get_extra(conv_id, "active_resources") or {}
            if is_default:
                active.pop("agent", None)
            else:
                active["agent"] = agent_name
            store.set_extra(conv_id, "active_resources", active)
            flowfile.set_content(json.dumps({
                "selected": agent_name if not is_default else "assistant (default)",
            }).encode())
            return [flowfile]

        if action == "delete_agent":
            agent_name = body.get("name", "").strip()
            conv_id = body.get("conversation_id", "")
            if agent_name and conv_id:
                agent_name = self._resolve_agent_name(agent_name, conv_id)
            if not agent_name:
                flowfile.set_content(json.dumps({
                    "error": "Missing name",
                }).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            from core.resource_store import ResourceStore
            uid = user_id or "anonymous"
            deleted = ResourceStore.instance().delete("agent", agent_name, uid)
            # Deactivate if it was active
            if conv_id:
                active = store.get_extra(conv_id, "active_resources") or {}
                if active.get("agent") == agent_name:
                    active.pop("agent", None)
                    store.set_extra(conv_id, "active_resources", active)
            flowfile.set_content(json.dumps({
                "deleted": deleted, "name": agent_name,
            }).encode())
            return [flowfile]

        if action in ("create_skill", "add_skill"):
            skill_name = body.get("name", "").strip()
            skill_prompt = body.get("prompt", "").strip()
            conv_id = body.get("conversation_id", "")
            if not skill_name or not skill_prompt:
                flowfile.set_content(json.dumps({
                    "error": "Missing name or prompt",
                }).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            from core.resource_store import ResourceStore
            rs = ResourceStore.instance()
            uid = user_id or "anonymous"
            try:
                data = {"prompt": skill_prompt}
                description = body.get("description", "")
                if description:
                    data["description"] = description
                if rs.exists("skill", skill_name, uid):
                    rs.update("skill", skill_name, uid, data)
                else:
                    rs.create("skill", skill_name, uid, data)
                # Auto-activate in conversation
                if conv_id:
                    active = store.get_extra(conv_id, "active_resources") or {}
                    skills = active.get("skills", [])
                    if skill_name not in skills:
                        skills.append(skill_name)
                    active["skills"] = skills
                    store.set_extra(conv_id, "active_resources", active)
                flowfile.set_content(json.dumps({
                    "created": True, "name": skill_name,
                }).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]

        if action == "delete_skill":
            skill_name = body.get("name", "").strip()
            conv_id = body.get("conversation_id", "")
            if not skill_name:
                flowfile.set_content(json.dumps({"error": "Missing name"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            from core.resource_store import ResourceStore
            uid = user_id or "anonymous"
            deleted = ResourceStore.instance().delete("skill", skill_name, uid)
            if conv_id:
                active = store.get_extra(conv_id, "active_resources") or {}
                skills = active.get("skills", [])
                if skill_name in skills:
                    skills.remove(skill_name)
                active["skills"] = skills
                store.set_extra(conv_id, "active_resources", active)
            flowfile.set_content(json.dumps({
                "deleted": deleted, "name": skill_name,
            }).encode())
            return [flowfile]

        if action == "list_skills":
            from core.resource_store import ResourceStore
            uid = user_id or "anonymous"
            skills = ResourceStore.instance().list_all("skill", uid)
            conv_id = body.get("conversation_id", "")
            active_skills = []
            if conv_id:
                active = store.get_extra(conv_id, "active_resources") or {}
                active_skills = active.get("skills", [])
            flowfile.set_content(json.dumps({
                "skills": [{
                    "name": s["name"],
                    "description": s.get("description", ""),
                    "prompt": s.get("prompt", "")[:80],
                    "active": s["name"] in active_skills,
                } for s in skills],
            }, ensure_ascii=False).encode())
            return [flowfile]

        if action == "list_resources":
            # List all resource types for the user
            conv_id = body.get("conversation_id", "")
            from core.resource_store import ResourceStore
            rs = ResourceStore.instance()
            uid = user_id or "anonymous"
            active = {}
            if conv_id:
                active = store.get_extra(conv_id, "active_resources") or {}
            result = {
                "agents": [{
                    "name": a["name"],
                    "description": a.get("description", ""),
                    "active": active.get("agent") == a["name"],
                } for a in rs.list_all("agent", uid)],
                "skills": [{
                    "name": s["name"],
                    "description": s.get("description", ""),
                    "active": s["name"] in active.get("skills", []),
                } for s in rs.list_all("skill", uid)],
                "mcp_servers": [{
                    "name": m["name"],
                    "url": m.get("url", ""),
                    "active": m["name"] in active.get("mcps", []),
                } for m in rs.list_all("mcp", uid)],
            }
            flowfile.set_content(json.dumps(result, ensure_ascii=False).encode())
            return [flowfile]

        if action == "activate_resource":
            conv_id = body.get("conversation_id", "")
            rtype = body.get("resource_type", "")
            rname = body.get("name", "").strip()
            if not conv_id or not rtype or not rname:
                flowfile.set_content(json.dumps({
                    "error": "Missing conversation_id, resource_type, or name",
                }).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            active = store.get_extra(conv_id, "active_resources") or {}
            if rtype == "agent":
                active["agent"] = rname
            elif rtype == "skill":
                skills = active.get("skills", [])
                if rname not in skills:
                    skills.append(rname)
                active["skills"] = skills
            elif rtype == "mcp":
                mcps = active.get("mcps", [])
                if rname not in mcps:
                    mcps.append(rname)
                active["mcps"] = mcps
            store.set_extra(conv_id, "active_resources", active)
            flowfile.set_content(json.dumps({
                "activated": True, "type": rtype, "name": rname,
            }).encode())
            return [flowfile]

        if action == "deactivate_resource":
            conv_id = body.get("conversation_id", "")
            rtype = body.get("resource_type", "")
            rname = body.get("name", "").strip()
            if not conv_id or not rtype or not rname:
                flowfile.set_content(json.dumps({
                    "error": "Missing conversation_id, resource_type, or name",
                }).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            active = store.get_extra(conv_id, "active_resources") or {}
            if rtype == "agent":
                if active.get("agent") == rname:
                    active.pop("agent", None)
            elif rtype == "skill":
                skills = active.get("skills", [])
                if rname in skills:
                    skills.remove(rname)
                active["skills"] = skills
            elif rtype == "mcp":
                mcps = active.get("mcps", [])
                if rname in mcps:
                    mcps.remove(rname)
                active["mcps"] = mcps
            store.set_extra(conv_id, "active_resources", active)
            flowfile.set_content(json.dumps({
                "deactivated": True, "type": rtype, "name": rname,
            }).encode())
            return [flowfile]

        if action == "share_resource":
            rtype = body.get("resource_type", "")
            rname = body.get("name", "").strip()
            target_conv = body.get("target_conversation_id", "")
            if not rtype or not rname or not target_conv:
                flowfile.set_content(json.dumps({
                    "error": "Missing resource_type, name, or target_conversation_id",
                }).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            # Verify ownership of target conversation
            target_meta = store.get_metadata(target_conv)
            if not target_meta or (user_id and target_meta.get("user_id") != user_id):
                flowfile.set_content(json.dumps({
                    "error": "Target conversation not found or access denied",
                }).encode())
                flowfile.set_attribute("http.response.status", "404")
                return [flowfile]
            # Activate in target
            active = store.get_extra(target_conv, "active_resources") or {}
            if rtype == "agent":
                active["agent"] = rname
            elif rtype == "skill":
                skills = active.get("skills", [])
                if rname not in skills:
                    skills.append(rname)
                active["skills"] = skills
            elif rtype == "mcp":
                mcps = active.get("mcps", [])
                if rname not in mcps:
                    mcps.append(rname)
                active["mcps"] = mcps
            store.set_extra(target_conv, "active_resources", active)
            flowfile.set_content(json.dumps({
                "shared": True, "type": rtype, "name": rname,
                "target": target_conv,
            }).encode())
            return [flowfile]

        if action == "link_telegram":
            tg_user_id = body.get("telegram_user_id", "").strip()
            bot_token = body.get("bot_token", "").strip()
            if not user_id:
                flowfile.set_content(json.dumps({
                    "error": "Authentication required",
                }).encode())
                flowfile.set_attribute("http.response.status", "401")
                return [flowfile]
            if not tg_user_id:
                flowfile.set_content(json.dumps({
                    "error": "Missing telegram_user_id",
                }).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            from core.identity_service import IdentityService
            linked = IdentityService.instance().link(
                user_id, "telegram", tg_user_id, bot_token=bot_token,
            )
            if not linked:
                flowfile.set_content(json.dumps({
                    "error": "This Telegram ID is already linked to another user",
                }).encode())
                flowfile.set_attribute("http.response.status", "409")
                return [flowfile]
            result = {"linked": True, "telegram_user_id": tg_user_id}
            # Register personal bot in the pool
            if bot_token:
                try:
                    from services.telegram_bot_service import TelegramBotPool
                    username = TelegramBotPool.instance().register_bot(
                        bot_token, user_id,
                    )
                    result["bot_username"] = username
                except Exception as e:
                    result["bot_warning"] = f"Bot token invalid: {e}"
            flowfile.set_content(json.dumps(result).encode())
            return [flowfile]

        if action == "unlink_telegram":
            if not user_id:
                flowfile.set_content(json.dumps({
                    "error": "Authentication required",
                }).encode())
                flowfile.set_attribute("http.response.status", "401")
                return [flowfile]
            from core.identity_service import IdentityService
            ids = IdentityService.instance()
            # Unregister personal bot from pool before unlinking
            bot_token = ids.get_bot_token(user_id, "telegram")
            if bot_token:
                try:
                    from services.telegram_bot_service import TelegramBotPool
                    TelegramBotPool.instance().unregister_bot(bot_token)
                except Exception:
                    pass
            unlinked = ids.unlink(user_id, "telegram")
            flowfile.set_content(json.dumps({
                "unlinked": unlinked,
            }).encode())
            return [flowfile]

        if action == "get_links":
            if not user_id:
                flowfile.set_content(json.dumps({"links": {}}).encode())
                return [flowfile]
            from core.identity_service import IdentityService
            ids = IdentityService.instance()
            links = ids.get_links(user_id)
            active_conv = ids.get_active_conv(user_id, "telegram")
            flowfile.set_content(json.dumps({
                "links": links, "active_telegram_conv": active_conv,
            }, ensure_ascii=False).encode())
            return [flowfile]

        if action == "get_usage":
            try:
                from core.token_tracker import TokenTracker
                is_admin = "admin" in (flowfile.get_attribute("http.auth.roles") or "")
                if is_admin:
                    usage = TokenTracker.instance().get_all_usage()
                else:
                    usage = {user_id: TokenTracker.instance().get_usage(user_id)}
                flowfile.set_content(json.dumps({
                    "usage": usage,
                }, ensure_ascii=False).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]

        if action == "list_memories":
            try:
                from core.memory_store import MemoryStore
                ms = MemoryStore.instance()
                agent_filter = body.get("agent_name")  # None = all
                if agent_filter is not None:
                    entries = ms.list_by_agent(user_id, agent_filter)
                else:
                    entries = ms.list_all(user_id)
                result = [{
                    "id": e.id, "text": e.text, "tags": e.tags,
                    "created_at": e.created_at, "updated_at": e.updated_at,
                    "source": e.source, "agent": e.agent,
                    "conversation_id": e.conversation_id,
                } for e in entries]
                flowfile.set_content(json.dumps({
                    "memories": result, "count": len(result),
                }, ensure_ascii=False).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]

        if action == "delete_memory":
            memory_id = body.get("memory_id", "")
            if not memory_id:
                flowfile.set_content(json.dumps({"error": "Missing memory_id"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            try:
                from core.memory_store import MemoryStore
                deleted = MemoryStore.instance().forget(user_id, memory_id)
                flowfile.set_content(json.dumps({"deleted": deleted}).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]

        if action == "edit_memory":
            memory_id = body.get("memory_id", "")
            if not memory_id:
                flowfile.set_content(json.dumps({"error": "Missing memory_id"}).encode())
                return [flowfile]
            from core.memory_store import MemoryStore
            ms = MemoryStore.instance()
            updated = False
            if "text" in body:
                updated = ms.update_text(user_id, memory_id, body["text"]) or updated
            if "tags" in body:
                updated = ms.update_tags(user_id, memory_id, body["tags"]) or updated
            if "agent" in body:
                updated = ms.update_agent(user_id, memory_id, body["agent"]) or updated
            flowfile.set_content(json.dumps({"updated": updated}).encode())
            return [flowfile]

        if action == "add_memory":
            text = body.get("text", "")
            if not text:
                flowfile.set_content(json.dumps({"error": "Missing text"}).encode())
                return [flowfile]
            tags = body.get("tags", [])
            agent = body.get("agent", "")
            conv_id = body.get("conversation_id", "")
            scope = body.get("scope", "agent")  # global/agent/conversation/private
            # Resolve scope
            if scope == "global":
                agent, conv_id = "", ""
            elif scope == "conversation":
                agent = ""
            elif scope == "private":
                pass  # keep both
            else:  # agent
                conv_id = ""
            from core.memory_store import MemoryStore
            entry = MemoryStore.instance().remember(
                user_id, text, tags, source="user",
                agent=agent, conversation_id=conv_id,
            )
            flowfile.set_content(json.dumps({
                "id": entry.id, "text": entry.text,
                "tags": entry.tags, "agent": entry.agent,
                "conversation_id": entry.conversation_id,
            }, ensure_ascii=False).encode())
            return [flowfile]

        if action == "install_tool":
            filename = body.get("filename", "")
            source = body.get("source", "")
            if not source:
                flowfile.set_content(json.dumps({"error": "Missing source code"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            try:
                from core.dynamic_tool_store import DynamicToolStore
                result = DynamicToolStore.instance().install(user_id, filename, source)
                # Reset tool registry so new tool is picked up
                self._tool_registry = None
                flowfile.set_content(json.dumps({
                    "installed": True, **result,
                }).encode())
            except (ValueError, PermissionError) as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
                flowfile.set_attribute("http.response.status", "400")
            return [flowfile]

        if action == "uninstall_tool":
            tool_name = body.get("tool_name", "")
            if not tool_name:
                flowfile.set_content(json.dumps({"error": "Missing tool_name"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            try:
                from core.dynamic_tool_store import DynamicToolStore
                is_admin = "admin" in (flowfile.get_attribute("http.auth.roles") or "")
                removed = DynamicToolStore.instance().uninstall(
                    user_id, tool_name, is_admin=is_admin,
                )
                # Reset tool registry
                self._tool_registry = None
                flowfile.set_content(json.dumps({
                    "uninstalled": removed, "tool_name": tool_name,
                }).encode())
            except PermissionError as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
                flowfile.set_attribute("http.response.status", "403")
            return [flowfile]

        if action == "list_tools":
            try:
                from core.dynamic_tool_store import DynamicToolStore
                is_admin = "admin" in (flowfile.get_attribute("http.auth.roles") or "")
                tools = DynamicToolStore.instance().list_tools(
                    user_id=user_id, is_admin=is_admin,
                )
                flowfile.set_content(json.dumps({
                    "tools": tools,
                }, ensure_ascii=False).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]

        # ── User tool call ─────────────────────────────────────────
        if action == "get_tool_schemas":
            # Return all builtin tool definitions for /call help
            registry = self.get_tool_registry()
            tools = [{
                "name": h.name,
                "description": h.description,
                "parameters": h.parameters_schema,
            } for h in registry.list_tools()]
            flowfile.set_content(json.dumps({"tools": tools}, ensure_ascii=False).encode())
            return [flowfile]

        if action == "call_tool":
            tool_name = body.get("tool_name", "")
            tool_args = body.get("arguments", {})
            positional = body.get("positional_args", [])
            conv_id = body.get("conversation_id", "")
            if not tool_name:
                flowfile.set_content(json.dumps({"error": "Missing tool_name"}).encode())
                return [flowfile]
            registry = self.get_tool_registry()
            if conv_id or user_id:
                self._configure_tool_handlers(
                    registry, conversation_id=conv_id, user_id=user_id,
                )
            # Find handler
            handler = None
            for h in registry.list_tools():
                if h.name == tool_name:
                    handler = h
                    break
            if not handler:
                flowfile.set_content(json.dumps({
                    "error": f"Tool '{tool_name}' not found",
                }).encode())
                return [flowfile]
            # Map positional args to named params using schema
            if positional:
                schema = handler.parameters_schema or {}
                param_names = list((schema.get("properties") or {}).keys())
                for i, val in enumerate(positional):
                    if i < len(param_names):
                        key = param_names[i]
                        if key not in tool_args:
                            tool_args[key] = val
                    else:
                        flowfile.set_content(json.dumps({
                            "error": (
                                f"Too many positional arguments ({len(positional)}) "
                                f"for tool '{tool_name}' which has "
                                f"{len(param_names)} parameters: {param_names}"
                            ),
                        }).encode())
                        return [flowfile]
            # Execute in background thread — publish SSE events + persist
            # exactly like the agent streaming loop does
            _call_registry = registry
            _call_tool_name = tool_name
            _call_tool_args = tool_args
            _call_conv_id = conv_id
            _call_user_id = user_id

            def _run_user_tool_call():
                from core.conversation_event_bus import ConversationEventBus
                from core.conversation_store import ConversationStore
                bus = ConversationEventBus.instance()
                source = {"type": "user", "name": _call_user_id or "anonymous"}
                # Publish tool_call event (same as agent loop)
                bus.publish_event(_call_conv_id, "tool_call", {
                    "tool": _call_tool_name,
                    "arguments": _call_tool_args,
                    "agent_name": "user",
                    "llm_service": "",
                })
                # Execute
                try:
                    result_text = _call_registry.execute(
                        _call_tool_name, _call_tool_args,
                    ) or ""
                except Exception as _te:
                    result_text = f"Error: {_te}"
                    logger.error("User /call tool '%s' failed: %s",
                                 _call_tool_name, _te)
                # Publish tool_result event
                _result_preview = (result_text or "")[:2000]
                bus.publish_event(_call_conv_id, "tool_result", {
                    "tool": _call_tool_name,
                    "result": _result_preview,
                    "agent_name": "user",
                    "llm_service": "",
                })
                # Persist tool_call + tool_result messages in conversation
                if _call_conv_id:
                    import uuid as _uuid
                    tc_id = _uuid.uuid4().hex[:12]
                    msgs = [
                        {
                            "role": "assistant", "content": "",
                            "source": source,
                            "tool_calls": [{
                                "id": tc_id,
                                "name": _call_tool_name,
                                "arguments": _call_tool_args,
                            }],
                        },
                        {
                            "role": "tool",
                            "content": result_text,
                            "tool_call_id": tc_id,
                        },
                    ]
                    try:
                        cstore = ConversationStore.instance()
                        cstore.append_messages(
                            _call_conv_id, msgs,
                            user_id=_call_user_id,
                        )
                    except Exception as _pe:
                        logger.warning("Failed to persist /call messages: %s", _pe)

            thread = threading.Thread(
                target=_run_user_tool_call, daemon=True,
                name=f"user-call-{tool_name}",
            )
            thread.start()
            # Return ack immediately
            flowfile.set_content(json.dumps({
                "status": "accepted", "tool": tool_name,
            }).encode())
            return [flowfile]

        # ── User services ─────────────────────────────────────────
        if action == "service_list":
            try:
                from gui.services.user_service_registry import UserServiceRegistry
                registry = UserServiceRegistry.get_instance()
                defs = registry.get_all_for_user(user_id)
                services = []
                for sid, sdef in sorted(defs.items()):
                    services.append({
                        "id": sid,
                        "type": sdef.service_type,
                        "enabled": sdef.enabled,
                        "connected": registry.is_connected(user_id, sid),
                        "description": sdef.description,
                    })
                flowfile.set_content(json.dumps({
                    "services": services,
                }, ensure_ascii=False).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]

        if action == "service_install":
            try:
                from gui.services.user_service_registry import UserServiceRegistry
                registry = UserServiceRegistry.get_instance()
                svc_type = body.get("service_type", "")
                svc_name = body.get("service_name", "")
                config_str = body.get("config_str", "")
                if not svc_type or not svc_name:
                    flowfile.set_content(json.dumps({
                        "error": "Usage: /service install <type> <name> [key=val,...]",
                    }).encode())
                    return [flowfile]
                # Parse config_str: "key=val,key2=val2" → dict
                config = {}
                if config_str:
                    for pair in config_str.split(","):
                        pair = pair.strip()
                        if "=" in pair:
                            k, v = pair.split("=", 1)
                            config[k.strip()] = v.strip()
                sdef = registry.install(
                    user_id=user_id,
                    service_id=svc_name,
                    service_type=svc_type,
                    config=config,
                )
                flowfile.set_content(json.dumps({
                    "installed": True, "id": svc_name, "type": svc_type,
                }, ensure_ascii=False).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]

        if action == "service_uninstall":
            try:
                from gui.services.user_service_registry import UserServiceRegistry
                registry = UserServiceRegistry.get_instance()
                svc_id = body.get("service_id", "")
                if not registry.get_definition(user_id, svc_id):
                    flowfile.set_content(json.dumps({
                        "error": f"Service '{svc_id}' not found.",
                    }).encode())
                    return [flowfile]
                registry.uninstall(user_id, svc_id)
                flowfile.set_content(json.dumps({
                    "uninstalled": True, "id": svc_id,
                }, ensure_ascii=False).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]

        if action == "service_enable":
            try:
                from gui.services.user_service_registry import UserServiceRegistry
                registry = UserServiceRegistry.get_instance()
                svc_id = body.get("service_id", "")
                if not registry.get_definition(user_id, svc_id):
                    flowfile.set_content(json.dumps({
                        "error": f"Service '{svc_id}' not found.",
                    }).encode())
                    return [flowfile]
                registry.enable(user_id, svc_id)
                flowfile.set_content(json.dumps({
                    "enabled": True, "id": svc_id,
                }, ensure_ascii=False).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]

        if action == "service_disable":
            try:
                from gui.services.user_service_registry import UserServiceRegistry
                registry = UserServiceRegistry.get_instance()
                svc_id = body.get("service_id", "")
                if not registry.get_definition(user_id, svc_id):
                    flowfile.set_content(json.dumps({
                        "error": f"Service '{svc_id}' not found.",
                    }).encode())
                    return [flowfile]
                registry.disable(user_id, svc_id)
                flowfile.set_content(json.dumps({
                    "disabled": True, "id": svc_id,
                }, ensure_ascii=False).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]

        if action == "list_prompts":
            from core.resource_store import ResourceStore
            rs = ResourceStore.instance()
            prompts = rs.list_all("prompt", user_id)
            items = [
                {
                    "name": p["name"],
                    "title": p.get("title", p["name"]),
                    "category": p.get("category", ""),
                    "description": p.get("description", ""),
                    "preview": p.get("content", "")[:100],
                }
                for p in prompts
            ]
            flowfile.set_content(json.dumps({"prompts": items}, ensure_ascii=False).encode())
            return [flowfile]

        if action == "get_prompt":
            prompt_name = body.get("name", "")
            if not prompt_name:
                flowfile.set_content(json.dumps({"error": "Missing name"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            from core.resource_store import ResourceStore
            rs = ResourceStore.instance()
            prompt_def = rs.get_any("prompt", prompt_name, user_id)
            if not prompt_def:
                flowfile.set_content(json.dumps({"error": "Prompt not found"}).encode())
                flowfile.set_attribute("http.response.status", "404")
                return [flowfile]
            flowfile.set_content(json.dumps({
                "name": prompt_name,
                "title": prompt_def.get("title", prompt_name),
                "content": prompt_def.get("content", ""),
                "category": prompt_def.get("category", ""),
                "description": prompt_def.get("description", ""),
            }, ensure_ascii=False).encode())
            return [flowfile]

        if action == "random_thought":
            return self._handle_random_thought(body, body.get("conversation_id", ""), user_id, flowfile)

        # ── Task management ───────────────────────────────────────────
        if action == "assign_task":
            conv_id = body.get("conversation_id", "")
            agent = body.get("agent_name", "")
            task_desc = body.get("task", "")
            if not conv_id or not agent or not task_desc:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id, agent_name, or task"}).encode())
                return [flowfile]
            from core.tool_registry import AssignTaskHandler
            h = AssignTaskHandler()
            h.set_conversation_id(conv_id)
            h.set_agent_name("user")
            h.set_user_id(user_id)
            result = h.execute({
                "agent": agent, "task": task_desc,
                "completion_criteria": body.get("completion_criteria", ""),
                "interval": body.get("interval", 60),
                "max_iterations": body.get("max_iterations", 50),
                "verifier": body.get("verifier", ""),
            })
            # Ensure poller is running (task needs it for scheduled wake-ups)
            poll_interval = int(self.config.get("poll_interval", 0))
            if poll_interval > 0 and not self._poller_started:
                self._poller_started = True
                poller_thread = threading.Thread(
                    target=self._poll_conversations,
                    args=(poll_interval,),
                    daemon=True,
                    name="agent-poller",
                )
                poller_thread.start()
                logger.info("Agent poller started (triggered by task assignment)")
            flowfile.set_content(json.dumps({"ok": True, "result": result}).encode())
            return [flowfile]

        if action == "task_status":
            conv_id = body.get("conversation_id", "")
            if not conv_id:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
                return [flowfile]
            all_tasks = store.get_extra(conv_id, "agent_tasks") or {}
            agent_filter = body.get("agent_name", "")
            tasks_out = []
            for tid, t in all_tasks.items():
                if not isinstance(t, dict):
                    continue
                if agent_filter and t.get("agent") != agent_filter:
                    continue
                tasks_out.append({
                    "task_id": tid, "agent": t.get("agent", ""),
                    "task": t.get("task", ""), "status": t.get("status", ""),
                    "iterations": t.get("iterations_done", 0),
                    "max_iterations": t.get("max_iterations", 50),
                    "last_result": t.get("last_result", ""),
                    "verifier": t.get("verifier", ""),
                    "interval": t.get("interval", 60),
                })
            flowfile.set_content(json.dumps({"tasks": tasks_out}).encode())
            return [flowfile]

        if action in ("pause_task", "resume_task", "cancel_task"):
            conv_id = body.get("conversation_id", "")
            target = body.get("task_id", "") or body.get("agent_name", "")
            if not conv_id or not target:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id or task_id/agent_name"}).encode())
                return [flowfile]
            all_tasks = store.get_extra(conv_id, "agent_tasks") or {}
            # Find tasks: by task_id or by agent_name (all tasks of that agent)
            matched = {}
            if target in all_tasks:
                matched[target] = all_tasks[target]
            else:
                for tid, t in all_tasks.items():
                    if isinstance(t, dict) and t.get("agent") == target:
                        matched[tid] = t
            if not matched:
                flowfile.set_content(json.dumps({"error": f"No task found for '{target}'"}).encode())
                return [flowfile]
            from core.poll_scheduler import PollScheduler
            scheduler = PollScheduler.instance()
            for tid, task in matched.items():
                if action == "cancel_task":
                    # Remove cancelled task from dict
                    all_tasks.pop(tid, None)
                    scheduler.cancel(f"{conv_id}::task::{tid}")
                    scheduler.cancel(f"{conv_id}::task_verify::{tid}")
                    continue  # skip the all_tasks[tid] = task below
                elif action == "pause_task":
                    task["status"] = "paused"
                    scheduler.cancel(f"{conv_id}::task::{tid}")
                elif action == "resume_task":
                    task["status"] = "active"
                    scheduler.schedule_delay(
                        conv_id, task.get("interval", 60),
                        key=f"{conv_id}::task::{tid}",
                        reason=f"[agent_task:{tid}] resumed ({task.get('agent', '?')})",
                        user_id=user_id,
                    )
                all_tasks[tid] = task
            store.set_extra(conv_id, "agent_tasks", all_tasks)
            flowfile.set_content(json.dumps({
                "ok": True, "affected": list(matched.keys()),
            }).encode())
            return [flowfile]

        # ── Image service management ──────────────────────────────────
        if action == "list_image_services":
            from services.base_image_generation import BaseImageGenerationService
            services = self._discover_media_services(user_id, BaseImageGenerationService)
            conv_id = body.get("conversation_id", "")
            prefs = {}
            if conv_id:
                prefs = store.get_extra(conv_id, "image_services") or {}
            result = [{
                "id": sid, "type": stype, "scope": scope,
                "selected_for": [
                    k for k, v in prefs.items() if v == sid
                ],
            } for sid, stype, scope in services]
            flowfile.set_content(json.dumps(result, ensure_ascii=False).encode())
            return [flowfile]

        if action == "set_image_service":
            conv_id = body.get("conversation_id", "")
            service_name = body.get("service_name", "")
            agent = body.get("agent_name", "*")
            if not conv_id or not service_name:
                flowfile.set_content(json.dumps({
                    "error": "conversation_id and service_name required",
                }).encode())
                return [flowfile]
            prefs = store.get_extra(conv_id, "image_services") or {}
            prefs[agent] = service_name
            store.set_extra(conv_id, "image_services", prefs)
            flowfile.set_content(json.dumps({
                "ok": True, "service": service_name, "agent": agent,
            }).encode())
            return [flowfile]

        if action == "clear_image_service":
            conv_id = body.get("conversation_id", "")
            agent = body.get("agent_name", "")
            if not conv_id:
                flowfile.set_content(json.dumps({
                    "error": "conversation_id required",
                }).encode())
                return [flowfile]
            if agent:
                prefs = store.get_extra(conv_id, "image_services") or {}
                prefs.pop(agent, None)
                store.set_extra(conv_id, "image_services", prefs)
            else:
                store.set_extra(conv_id, "image_services", {})
            flowfile.set_content(json.dumps({"ok": True}).encode())
            return [flowfile]

        # ── Video service management ──────────────────────────────────
        if action == "list_video_services":
            from services.base_video_generation import BaseVideoGenerationService
            services = self._discover_media_services(user_id, BaseVideoGenerationService)
            conv_id = body.get("conversation_id", "")
            prefs = store.get_extra(conv_id, "video_services") or {} if conv_id else {}
            result = [{
                "id": sid, "type": stype, "scope": scope,
                "selected_for": [k for k, v in prefs.items() if v == sid],
            } for sid, stype, scope in services]
            flowfile.set_content(json.dumps(result, ensure_ascii=False).encode())
            return [flowfile]

        if action == "set_video_service":
            conv_id = body.get("conversation_id", "")
            service_name = body.get("service_name", "")
            agent = body.get("agent_name", "*")
            if not conv_id or not service_name:
                flowfile.set_content(json.dumps({
                    "error": "conversation_id and service_name required",
                }).encode())
                return [flowfile]
            prefs = store.get_extra(conv_id, "video_services") or {}
            prefs[agent] = service_name
            store.set_extra(conv_id, "video_services", prefs)
            flowfile.set_content(json.dumps({
                "ok": True, "service": service_name, "agent": agent,
            }).encode())
            return [flowfile]

        if action == "clear_video_service":
            conv_id = body.get("conversation_id", "")
            agent = body.get("agent_name", "")
            if not conv_id:
                flowfile.set_content(json.dumps({
                    "error": "conversation_id required",
                }).encode())
                return [flowfile]
            if agent:
                prefs = store.get_extra(conv_id, "video_services") or {}
                prefs.pop(agent, None)
                store.set_extra(conv_id, "video_services", prefs)
            else:
                store.set_extra(conv_id, "video_services", {})
            flowfile.set_content(json.dumps({"ok": True}).encode())
            return [flowfile]

        return None  # Unknown action — treat as normal message

    def _handle_telegram_conv_command(
        self, text: str, tg_user_id: str, flowfile: FlowFile,
    ) -> Optional[List[FlowFile]]:
        """Handle /conv commands from Telegram for cross-channel conversation management.

        Commands:
          /conv list       — list the user's conversations
          /conv select ID  — switch active conversation
          /conv new        — start a new conversation
          /conv info       — show current active conversation
        """
        from core.identity_service import IdentityService
        ids = IdentityService.instance()
        resolved_user = ids.resolve_user("telegram", tg_user_id)
        if not resolved_user:
            flowfile.set_content(
                "Your Telegram account is not linked to a PyFi2 user.\n"
                "Use /link telegram YOUR_TG_ID from the web chat to link it."
                .encode("utf-8")
            )
            return [flowfile]

        parts = text.split(maxsplit=2)
        subcmd = parts[1] if len(parts) > 1 else "info"

        from core.conversation_store import ConversationStore
        store = ConversationStore.instance()

        if subcmd == "list":
            convs = store.list_conversations(user_id=resolved_user)
            active = ids.get_active_conv(resolved_user, "telegram") or ""
            if not convs:
                flowfile.set_content("No conversations found.".encode("utf-8"))
                return [flowfile]
            lines = []
            for c in convs[:20]:  # limit to 20
                cid = c.get("conversation_id", "")
                short_id = cid[:12]
                marker = " *" if cid == active else ""
                msg_count = c.get("message_count", 0)
                lines.append(f"{'>' if cid == active else ' '} {short_id} ({msg_count} msgs){marker}")
            header = f"Your conversations ({len(convs)}):\n"
            footer = "\n\nUse /conv select ID to switch."
            flowfile.set_content((header + "\n".join(lines) + footer).encode("utf-8"))
            return [flowfile]

        if subcmd == "select":
            conv_id_prefix = parts[2].strip() if len(parts) > 2 else ""
            if not conv_id_prefix:
                flowfile.set_content(
                    "Usage: /conv select <conversation_id>".encode("utf-8")
                )
                return [flowfile]
            # Find conversation matching prefix
            convs = store.list_conversations(user_id=resolved_user)
            match = None
            for c in convs:
                cid = c.get("conversation_id", "")
                if cid == conv_id_prefix or cid.startswith(conv_id_prefix):
                    match = cid
                    break
            if not match:
                flowfile.set_content(
                    f"Conversation '{conv_id_prefix}' not found.".encode("utf-8")
                )
                return [flowfile]
            ids.set_active_conv(resolved_user, "telegram", match)
            flowfile.set_content(
                f"Switched to conversation {match[:12]}".encode("utf-8")
            )
            return [flowfile]

        if subcmd == "new":
            new_id = store.generate_id()
            ids.set_active_conv(resolved_user, "telegram", new_id)
            flowfile.set_content(
                f"New conversation started: {new_id[:12]}".encode("utf-8")
            )
            return [flowfile]

        # /conv info (default)
        active = ids.get_active_conv(resolved_user, "telegram")
        if active:
            count = store.message_count(active)
            flowfile.set_content(
                f"Active conversation: {active[:12]} ({count} msgs)\n"
                f"User: {resolved_user}".encode("utf-8")
            )
        else:
            flowfile.set_content(
                f"No active conversation. Use /conv new or /conv select ID.\n"
                f"User: {resolved_user}".encode("utf-8")
            )
        return [flowfile]

    # ── Random Thought ────────────────────────────────────────────

    @staticmethod
    def _parse_thought_frequency(spec: str):
        """Parse frequency spec like '2-3/h' → (min_interval, max_interval) in seconds.

        Format: ``<count_min>[-<count_max>]/<number?><unit>``
        Units: s=1, m=60, h=3600, d=86400.

        Returns ``(min_interval_sec, max_interval_sec)`` or raises ValueError.
        """
        import re
        m = re.match(r'^(\d+)(?:-(\d+))?/(\d*)([smhd])$', spec)
        if not m:
            raise ValueError(f"Invalid frequency: {spec}")
        count_min = int(m.group(1))
        count_max = int(m.group(2) or count_min)
        if count_min <= 0 or count_max < count_min:
            raise ValueError(f"Invalid frequency counts: {spec}")
        duration_num = int(m.group(3) or 1)
        unit = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}[m.group(4)]
        period = duration_num * unit
        # More counts → shorter intervals
        max_interval = period // count_min
        min_interval = period // count_max
        return (min_interval, max_interval)

    def _handle_random_thought(self, body: Dict, conv_id: str,
                               user_id: str, flowfile: FlowFile) -> List[FlowFile]:
        """Handle the ``random_thought`` action (on/off/status/now)."""
        import random as _rng
        from core.conversation_store import ConversationStore
        from core.poll_scheduler import PollScheduler

        sub = body.get("sub", "status")
        agent_name = body.get("agent", "")
        store = ConversationStore.instance()
        # If no agent specified, use the currently selected agent for this conversation
        if not agent_name and conv_id:
            active_res = store.get_extra(conv_id, "active_resources") or {}
            agent_name = active_res.get("agent", "") or "assistant"
        agent_name = agent_name or "assistant"
        # Resolve nickname → real name (case-insensitive)
        if agent_name not in ("", "assistant"):
            agent_name = self._resolve_agent_name(agent_name, conv_id)
        # Normalize agent name for key consistency (case-insensitive)
        _agent_key = agent_name.lower()
        thought_key = f"{conv_id}::thought::{_agent_key}"
        extra_key = f"random_thought::{_agent_key}"
        scheduler = PollScheduler.instance()

        if not conv_id:
            flowfile.set_content(json.dumps({"error": "No conversation"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]

        # Resolve target agents (ALL = assistant + all ResourceStore agents)
        if agent_name.upper() == "ALL":
            from core.resource_store import ResourceStore
            all_agents = ResourceStore.instance().list_all("agent", user_id)
            target_agents = ["assistant"] + [a["name"] for a in all_agents]
        else:
            target_agents = [agent_name]

        if sub == "on":
            freq = body.get("frequency", "6/1m")
            try:
                min_iv, max_iv = self._parse_thought_frequency(freq)
            except ValueError as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]

            results = []
            for _tgt in target_agents:
                _tgt_key = _tgt.lower()
                _tgt_thought_key = f"{conv_id}::thought::{_tgt_key}"
                _tgt_extra_key = f"random_thought::{_tgt_key}"

                scheduler.cancel(_tgt_thought_key)
                if not store.set_extra(conv_id, _tgt_extra_key, {"_probe": True}):
                    store.save(conv_id, [], user_id=user_id)
                store.set_extra(conv_id, _tgt_extra_key, {
                    "enabled": True,
                    "min_interval": min_iv,
                    "max_interval": max_iv,
                    "agent": _tgt,
                    "frequency": freq,
                })
                delay = _rng.randint(min_iv, max_iv)
                scheduler.schedule_delay(
                    conv_id, delay, key=_tgt_thought_key,
                    reason=f"[random_thought] spontaneous thought ({_tgt})",
                    user_id=user_id,
                )
                try:
                    from core.conversation_event_bus import ConversationEventBus
                    ConversationEventBus.instance().publish_event(conv_id, "thought_scheduled", {
                        "agent": _tgt, "delay": delay, "frequency": freq,
                    })
                except Exception:
                    pass
                results.append({"agent": _tgt, "delay": delay})

            flowfile.set_content(json.dumps({
                "ok": True, "agent": agent_name, "frequency": freq,
                "next_in_seconds": results[0]["delay"] if results else 0,
                "agents": [r["agent"] for r in results],
            }).encode())
            return [flowfile]

        if sub == "off":
            for _tgt in target_agents:
                _tgt_key = _tgt.lower()
                _tgt_extra_key = f"random_thought::{_tgt_key}"
                _tgt_thought_key = f"{conv_id}::thought::{_tgt_key}"
                store.set_extra(conv_id, _tgt_extra_key, {"enabled": False})
                scheduler.cancel(_tgt_thought_key)
            flowfile.set_content(json.dumps({
                "ok": True, "agent": agent_name, "disabled": True,
                "agents": target_agents,
            }).encode())
            return [flowfile]

        if sub == "now":
            for _tgt in target_agents:
                _tgt_key = _tgt.lower()
                _tgt_thought_key = f"{conv_id}::thought::{_tgt_key}"
                scheduler.schedule_delay(
                    conv_id, 1, key=_tgt_thought_key,
                    reason=f"[random_thought] manual trigger ({_tgt})",
                    user_id=user_id,
                )
            store.set_status(conv_id, "active")
            flowfile.set_content(json.dumps({
                "ok": True, "agent": agent_name, "triggered": True,
                "agents": target_agents,
            }).encode())
            return [flowfile]

        # sub == "status" (default)
        import time as _t
        statuses = []
        for _tgt in target_agents:
            _tgt_key = _tgt.lower()
            _tgt_extra_key = f"random_thought::{_tgt_key}"
            _tgt_thought_key = f"{conv_id}::thought::{_tgt_key}"
            cfg = store.get_extra(conv_id, _tgt_extra_key)
            enabled = bool(cfg and cfg.get("enabled"))
            sched = scheduler.get(_tgt_thought_key)
            next_at = sched["recheck_at"] if sched else None
            next_in = int(next_at - _t.time()) if next_at else None
            statuses.append({
                "agent": _tgt, "enabled": enabled,
                "frequency": cfg.get("frequency", "") if cfg else "",
                "next_in_seconds": max(0, next_in) if next_in is not None else None,
            })

        any_enabled = any(s["enabled"] for s in statuses)
        flowfile.set_content(json.dumps({
            "enabled": any_enabled, "agent": agent_name,
            "agents": statuses,
        }).encode())
        return [flowfile]

    def _execute_sync(self, flowfile: FlowFile) -> List[FlowFile]:
        start_time = time.time()
        total_tokens_in = 0
        total_tokens_out = 0
        tools_called: List[str] = []

        ctx = self._prepare_agent_context(flowfile)
        client = ctx["client"]
        registry = ctx["registry"]
        tool_defs = ctx["tool_defs"]
        messages = ctx["messages"]
        model = ctx["model"]
        conversation_id = ctx["conversation_id"]
        use_conv_store = ctx["use_conv_store"]
        conv_ttl = ctx["conv_ttl"]
        conv_attr = ctx["conv_attr"]
        base_count = ctx.get("_base_message_count", 0)

        iteration = 0
        final_model = ""
        finish_reason = ""
        response_content = ""
        _need_more_retried_ns = False  # guards heuristic tool-mention retry
        _consecutive_tool: Dict[str, int] = {}  # tool_name → consecutive call count
        _max_consec = ctx.get("max_consecutive_tool_calls", 5)

        _client_provider_ns = getattr(client, "provider", "") or ""
        if not isinstance(_client_provider_ns, str):
            _client_provider_ns = ""

        while iteration < ctx["max_iterations"]:
            iteration += 1

            _id_nicks_ns = ctx.get("_nicknames") or {}
            _llm_msgs = self._inject_identity(messages, _id_nicks_ns)
            _llm_msgs = self._apply_identity_suffix(_llm_msgs, ctx.get("_identity_suffix", ""))

            response = client.complete(
                messages=_llm_msgs,
                model=model or None,
                temperature=ctx["temperature"],
                max_tokens=ctx["max_tokens"],
                tools=tool_defs if tool_defs else None,
            )

            total_tokens_in += response.tokens_in
            total_tokens_out += response.tokens_out
            final_model = response.model
            finish_reason = response.finish_reason

            if not response.tool_calls:
                _source_ns = {"type": "agent", "name": ctx.get("active_agent_name") or "assistant"}
                action, msgs, final, _need_more_retried_ns = self._handle_response_no_tools(
                    response.content or "", _client_provider_ns, tool_defs,
                    _need_more_retried_ns, source=_source_ns,
                )
                messages.extend(msgs)
                if action == "break":
                    response_content = final
                    break
                continue

            _need_more_retried_ns = False  # reset on successful tool_call
            _source_tc_ns = {"type": "agent", "name": ctx.get("active_agent_name") or "assistant"}
            messages.append(LLMMessage(
                role="assistant", content=response.content,
                tool_calls=response.tool_calls,
                source=_source_tc_ns,
            ))

            results = self._execute_tool_calls(
                response.tool_calls, registry, _consecutive_tool, _max_consec,
                parallel=False,
                agent_name=ctx.get("active_agent_name") or "assistant",
                agent_svc=ctx.get("active_llm_service", ""),
            )
            for tc, result_text in results:
                tools_called.append(tc.name)
                messages.append(LLMMessage(
                    role="tool", content=result_text, tool_call_id=tc.id,
                ))
        else:
            logger.warning("Agent reached max iterations (%d), forcing synthesis",
                           ctx["max_iterations"])
            content, ti, to, fm = self._force_synthesis(
                messages, client, ctx,
                prompt=(
                    "[System: You have reached the maximum number of tool calls. "
                    "You MUST now provide your final response to the user. "
                    "Synthesize all the information you gathered from your tool calls "
                    "and present a clear, comprehensive answer. Do NOT call any more tools.]"
                ),
                tools_called=tools_called, compact_threshold=1.0,
            )
            response_content = content
            total_tokens_in += ti
            total_tokens_out += to
            if fm:
                final_model = fm

        # If the agent produced no final text, force a synthesis
        if not response_content:
            logger.warning("[agent] empty response — forcing synthesis")
            content, ti, to, fm = self._force_synthesis(
                messages, client, ctx,
                prompt=(
                    "[System: You did not provide a response to the user. "
                    "You MUST respond now. Synthesize any information you have and present "
                    "a clear answer. Do NOT call any tools.]"
                ),
                tools_called=tools_called,
            )
            response_content = content
            total_tokens_in += ti
            total_tokens_out += to
            if fm:
                final_model = fm

        duration_ms = (time.time() - start_time) * 1000
        flowfile.set_attribute("agent.iterations", str(iteration))
        flowfile.set_attribute("agent.tools_called", ",".join(tools_called))
        flowfile.set_attribute("agent.model", final_model)
        flowfile.set_attribute("agent.tokens_in", str(total_tokens_in))
        flowfile.set_attribute("agent.tokens_out", str(total_tokens_out))
        flowfile.set_attribute("agent.duration_ms", f"{duration_ms:.1f}")
        flowfile.set_attribute("agent.finish_reason", finish_reason)

        # Track token usage
        _client_model = getattr(client, "default_model", "") or ""
        self._track_tokens(
            ctx.get("user_id", "anonymous"),
            total_tokens_in, total_tokens_out,
            model=final_model or _client_model,
            agent_name=ctx.get("active_agent_name", "") or "assistant",
            llm_service=ctx.get("active_llm_service", ""),
        )

        if use_conv_store and conversation_id:
            from core.conversation_store import ConversationStore
            new_msgs = messages[base_count:]
            if new_msgs:
                ConversationStore.instance().append_messages(
                    conversation_id,
                    self._serialize_messages(new_msgs, channel=ctx.get("channel", "")),
                    ttl=conv_ttl, user_id=ctx.get("user_id", ""),
                )

        if conv_attr:
            flowfile.set_attribute(conv_attr, json.dumps(
                self._serialize_messages(messages, channel=ctx.get("channel", "")),
                ensure_ascii=False,
            ))

        if use_conv_store:
            _agent_name = ctx.get("active_agent_name", "")
            _llm_svc = ctx.get("active_llm_service", "")
            _client_prov = getattr(client, "provider", "") if client else ""
            if not isinstance(_client_prov, str):
                _client_prov = ""
            _client_burl = getattr(client, "base_url", "") if client else ""
            if not isinstance(_client_burl, str):
                _client_burl = ""
            _source = {"type": "agent", "name": _agent_name or "assistant"}
            if _llm_svc:
                _source["llm_service"] = _llm_svc
            if _client_prov:
                _source["provider"] = _client_prov
            if _client_burl and isinstance(_client_burl, str):
                import re as _re2
                _source["base_url"] = _re2.sub(r'(key|token|secret)=[^&]+', r'\1=***', _client_burl)
            output = json.dumps({
                "response": response_content,
                "conversation_id": conversation_id,
                "model": final_model or _client_model,
                "provider": _client_prov,
                "tokens_in": total_tokens_in,
                "tokens_out": total_tokens_out,
                "source": _source,
            }, ensure_ascii=False)
            flowfile.set_content(output.encode("utf-8"))
            flowfile.set_attribute("agent.conversation_id", conversation_id)
        else:
            flowfile.set_content(response_content.encode("utf-8"))

        return [flowfile]

    def _execute_streaming(self, flowfile: FlowFile) -> List[FlowFile]:
        """Streaming mode: publish SSE events to ConversationEventBus.

        Returns immediately with a JSON ack.  The agent loop runs in a
        background thread, publishing events as it goes.
        """
        from core.conversation_event_bus import ConversationEventBus

        try:
            ctx = self._prepare_agent_context(flowfile)
        except ValueError as e:
            # Agent not found or other validation error — return error to client
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
            flowfile.set_attribute("http.status.code", "400")
            return [flowfile]
        conversation_id = ctx["conversation_id"]
        bus = ConversationEventBus.instance()

        # Wait for any context operation to complete before proceeding
        if not self._is_context_op_free(conversation_id):
            evt = self._get_context_op_event(conversation_id)
            if not evt.wait(timeout=60.0):
                flowfile.set_content(json.dumps({
                    "error": "Context operation in progress, try again",
                }).encode())
                flowfile.set_attribute("http.response.status", "409")
                return [flowfile]

        # Bump generation counter — any older thread (e.g. poller) for this
        # conversation will see the mismatch and skip its save.
        # When target_agent is set, use a per-agent generation key so that
        # concurrent /agent msg to different agents don't cancel each other.
        _target = ctx.get("_target_agent", "")

        # Publish "thinking" immediately
        bus.publish_event(conversation_id, "thinking", {
            "conversation_id": conversation_id,
            "agent_name": _target or "assistant",
        })
        _gen_key = f"{conversation_id}:{_target}" if _target else conversation_id
        with self._conv_gen_lock:
            gen = self._conv_generation.get(_gen_key, 0) + 1
            self._conv_generation[_gen_key] = gen
            # Also bump the base conversation key for non-targeted messages
            # so a regular message still cancels all agent threads
            if not _target:
                # Cancel all per-agent keys for this conversation
                # but NOT task/thought threads (they have their own lifecycle)
                for k in list(self._conv_generation):
                    if k.startswith(conversation_id + ":") and \
                       "::thought::" not in k and "::task::" not in k:
                        self._conv_generation[k] += 1
        ctx["_generation"] = gen
        ctx["_gen_key"] = _gen_key

        # Mark conversation as active (prevents poller from picking it up)
        # Also clear cooldown so poller can check again after this interaction
        with self._active_lock:
            self._active_conversations[conversation_id] = self._active_conversations.get(conversation_id, 0) + 1
            self._user_active_conversations.add(conversation_id)
            self._poll_cooldown.pop(conversation_id, None)

        # Set conversation status to active
        from core.conversation_store import ConversationStore
        ConversationStore.instance().set_status(conversation_id, "active")

        # Register active interaction for UI tracking
        _user_msgs = [m for m in ctx["messages"] if m.role == "user"]
        _msg_preview = ""
        if _user_msgs:
            _last = _user_msgs[-1].text_content if isinstance(_user_msgs[-1].content, list) else (_user_msgs[-1].content or "")
            _msg_preview = _last[:80]
        with self._interactions_lock:
            self._active_interactions[_gen_key] = {
                "agent_name": _target or "assistant",
                "message_preview": _msg_preview,
                "started_at": time.time(),
                "iteration": 0,
                "last_tool": "",
                "status": "thinking",
                "conversation_id": conversation_id,
            }

        # Start agent loop in background thread
        thread = threading.Thread(
            target=self._streaming_agent_loop,
            args=(ctx, conversation_id, bus),
            daemon=True,
            name=f"agent-stream-{conversation_id}",
        )
        thread.start()

        # Start poller if configured and not already running
        poll_interval = int(self.config.get("poll_interval", 0))
        if poll_interval > 0 and not self._poller_started:
            self._poller_started = True
            poller = threading.Thread(
                target=self._poll_conversations,
                args=(poll_interval,),
                daemon=True,
                name="agent-poller",
            )
            poller.start()
            logger.info(f"Agent poller started (interval={poll_interval}s)")

        # Return immediately with ack (include message_count so client can sync)
        from core.conversation_store import ConversationStore as _CS
        msg_count = _CS.instance().message_count(conversation_id)
        ack = json.dumps({
            "status": "accepted",
            "conversation_id": conversation_id,
            "message_count": msg_count,
        }, ensure_ascii=False)
        flowfile.set_content(ack.encode("utf-8"))
        flowfile.set_attribute("agent.conversation_id", conversation_id)
        flowfile.set_attribute("agent.streaming", "true")

        return [flowfile]


    def _is_current_generation(self, conversation_id: str, generation: int) -> bool:
        """Check if this thread's generation is still current.

        Returns False if a newer user request has started for this conversation,
        meaning this thread should NOT overwrite the conversation store.
        """
        with self._conv_gen_lock:
            return self._conv_generation.get(conversation_id, 0) == generation

    def cancel_agent(self, conversation_id: str, agent_name: str = "",
                     silent: bool = False):
        """Cancel a running agent for this conversation.

        If agent_name is a named sub-agent (not "assistant"/""),
        only cancel that specific agent's thread.
        Otherwise cancel ALL agents for this conversation.

        Increments the generation counter so the running thread detects
        staleness at the next check point and stops gracefully.

        If silent=True, no SSE event is published (used by context ops
        that cancel as a precaution, not as user-visible action).
        """
        # "assistant" refers to the main (unnamed) agent
        # whose gen_key is just conversation_id, not conversation_id:assistant
        _is_named = agent_name and agent_name not in ("", "assistant")
        with self._conv_gen_lock:
            if _is_named:
                # Cancel this agent — it may be running under either:
                #   gen_key = "conv:agent" (from /agent msg)
                #   gen_key = "conv" (from selected agent, normal message)
                # Bump BOTH to be safe.
                key = f"{conversation_id}:{agent_name}"
                self._conv_generation[key] = \
                    self._conv_generation.get(key, 0) + 1
                self._conv_generation[conversation_id] = \
                    self._conv_generation.get(conversation_id, 0) + 1
            else:
                # Cancel default assistant + all per-agent threads
                # but NOT thought threads (they manage their own lifecycle)
                self._conv_generation[conversation_id] = \
                    self._conv_generation.get(conversation_id, 0) + 1
                for k in list(self._conv_generation):
                    if k.startswith(conversation_id + ":") and "::thought::" not in k:
                        self._conv_generation[k] += 1
        if not silent:
            # Publish cancellation event for SSE listeners
            from core.conversation_event_bus import ConversationEventBus
            ConversationEventBus.instance().publish_event(
                conversation_id, "cancelled", {
                    "reason": "user_request",
                    "agent_name": agent_name if _is_named else "all",
                }
            )
        # Also cancel thought threads and schedules for this agent
        from core.poll_scheduler import PollScheduler
        scheduler = PollScheduler.instance()
        if _is_named:
            # Cancel specific agent's thought
            _thought_key = f"{conversation_id}::thought::{agent_name.lower()}"
            with self._conv_gen_lock:
                self._conv_generation[_thought_key] = \
                    self._conv_generation.get(_thought_key, 0) + 1
            with self._interrupt_lock:
                self._conv_interrupt[_thought_key] = True
            scheduler.cancel(_thought_key)
        else:
            # Cancel ALL thought threads for this conversation
            with self._conv_gen_lock:
                for k in list(self._conv_generation):
                    if "::thought::" in k and k.startswith(conversation_id):
                        self._conv_generation[k] += 1
            for k in list(scheduler._schedules):
                if k.startswith(conversation_id) and "::task::" not in k and "::task_verify::" not in k:
                    scheduler.cancel(k)
        # Clear poll cooldown so poller doesn't re-trigger immediately
        with self._active_lock:
            self._active_conversations.pop(conversation_id, None)
            self._user_active_conversations.discard(conversation_id)
            self._poll_cooldown.pop(conversation_id, None)

        # Reset status
        from core.conversation_store import ConversationStore
        ConversationStore.instance().set_status(conversation_id, "idle")
        # Remove from interaction tracker (including thought entries)
        with self._interactions_lock:
            for k in list(self._active_interactions):
                if _is_named:
                    if k == f"{conversation_id}:{agent_name}" or \
                       k == f"{conversation_id}::thought::{agent_name.lower()}":
                        self._active_interactions.pop(k, None)
                else:
                    if k == conversation_id or k.startswith(conversation_id + ":") or \
                       k.startswith(conversation_id + "::"):
                        self._active_interactions.pop(k, None)
        logger.info(f"[agent:{conversation_id[:8]}] cancelled by user"
                    f"{f' (agent: {agent_name})' if _is_named else ' (all)'}")

    def interrupt_agent(self, conversation_id: str, agent_name: str = ""):
        """Signal an agent to finish gracefully — conclude with what it has.

        Unlike cancel_agent, does NOT kill the thread. Instead sets a flag
        that the loop checks and triggers a forced synthesis response.
        """
        with self._interrupt_lock:
            if agent_name and agent_name not in ("", "assistant"):
                self._conv_interrupt[f"{conversation_id}:{agent_name}"] = True
            else:
                # Interrupt default assistant + all per-agent threads
                self._conv_interrupt[conversation_id] = True
                for k in list(self._conv_interrupt):
                    if k.startswith(conversation_id + ":"):
                        self._conv_interrupt[k] = True
        from core.conversation_event_bus import ConversationEventBus
        ConversationEventBus.instance().publish_event(
            conversation_id, "interrupting",
            {"agent": agent_name or "assistant"},
        )
        logger.info(f"[agent:{conversation_id[:8]}] interrupt requested for "
                    f"'{agent_name or 'assistant'}'")

    def _check_interrupt(self, gen_key: str) -> bool:
        """Check and consume the interrupt flag for a gen_key."""
        with self._interrupt_lock:
            return self._conv_interrupt.pop(gen_key, False)

    def _btw_query(self, conversation_id: str, agent_name: str,
                   question: str, user_id: str) -> None:
        """Side-channel query — separate LLM call, no tools.

        Loads a lightweight context (system prompt + last few messages),
        makes a single LLM call without tools, and publishes the response
        via SSE. Persists btw Q&A to conversation history with btw flag.
        """
        from core.conversation_event_bus import ConversationEventBus
        from core.conversation_store import ConversationStore
        from core.resource_store import ResourceStore

        bus = ConversationEventBus.instance()
        store = ConversationStore.instance()

        try:
            # 1. Resolve agent's system prompt + LLM client
            if agent_name and agent_name not in ("", "assistant"):
                rs = ResourceStore.instance()
                adef = rs.get_any("agent", agent_name, user_id)
                if not adef:
                    bus.publish_event(conversation_id, "btw_done", {
                        "agent_name": agent_name,
                        "error": f"Agent '{agent_name}' not found",
                    })
                    return
                sys_prompt = adef["prompt"]
                llm_svc = adef.get("llm_service", "")
                if llm_svc and "${" in llm_svc:
                    from core.expression import resolve_expression
                    llm_svc = resolve_expression(llm_svc, owner=user_id)
                    if "${" in llm_svc:
                        llm_svc = ""
                client = None
                if llm_svc:
                    client, _ = self._resolve_llm_service(llm_svc, user_id)
                if not client:
                    task_svc = self.config.get("llm_service", "default")
                    if "${" in task_svc:
                        task_svc = "default"
                    client, _ = self._resolve_llm_service(task_svc, user_id)
            else:
                agent_name = "assistant"
                sys_prompt = self.config.get("system_prompt", "You are a helpful assistant.")
                task_svc = self.config.get("llm_service", "default")
                if "${" in task_svc:
                    task_svc = "default"
                client, _ = self._resolve_llm_service(task_svc, user_id)

            if not client:
                bus.publish_event(conversation_id, "btw_done", {
                    "agent_name": agent_name,
                    "error": "No LLM service available",
                })
                return

            # 2. Build lightweight context: system + last N messages (truncated)
            raw = store.load(conversation_id) or []
            recent = self._deserialize_messages(raw[-6:]) if len(raw) > 6 else self._deserialize_messages(raw)
            # Truncate each message content to keep context small
            summary_parts = []
            for m in recent:
                content = m.content if isinstance(m.content, str) else str(m.content)
                role_label = m.role.upper()
                truncated = content[:200] + ("..." if len(content) > 200 else "")
                summary_parts.append(f"[{role_label}]: {truncated}")
            context_summary = "\n".join(summary_parts)

            # Inject identity into btw system prompt
            _btw_nicknames = store.get_extra(conversation_id, "agent_nicknames") or {}
            _btw_nick_key = (agent_name or "assistant").lower()
            _btw_nick = next((v for k, v in _btw_nicknames.items() if k.lower() == _btw_nick_key), None)
            if _btw_nick:
                _id_block = (
                    f"[IDENTITY] Your real agent id is \"{agent_name}\". "
                    f"The user has given you the nickname \"{_btw_nick}\". "
                    f"When other agents or tools refer to \"{agent_name}\" or "
                    f"\"{_btw_nick}\" (case-insensitive), they mean YOU.\n\n"
                )
            else:
                _id_block = f"[IDENTITY] Your agent id is \"{agent_name}\".\n\n"
            btw_system = (
                _id_block + sys_prompt + "\n\n"
                "[SIDE QUESTION: The user is asking a quick question while you are working. "
                "Answer briefly and concisely. Do NOT use any tools. "
                "This does not affect your current task.]"
            )
            btw_messages = [
                LLMMessage(role="system", content=btw_system),
                LLMMessage(role="user", content=(
                    f"[Brief context of our conversation:\n{context_summary}]\n\n"
                    f"Quick question: {question}"
                )),
            ]

            # 3. Single LLM call, no tools, stream tokens via SSE
            bus.publish_event(conversation_id, "btw_thinking", {
                "agent_name": agent_name,
            })

            def on_btw_token(text):
                bus.publish_event(conversation_id, "btw_token", {
                    "agent_name": agent_name, "text": text,
                })

            response = client.complete_stream(
                messages=btw_messages,
                tools=None,
                temperature=0.5,
                max_tokens=1024,
                callback=on_btw_token,
            )

            # 4. Persist btw Q&A in conversation history
            import time as _btw_time
            _btw_now = _btw_time.time()
            _btw_user_source = {"type": "user", "name": user_id or "anonymous",
                                "btw": True, "target_agent": agent_name}
            _btw_agent_source = {"type": "agent", "name": agent_name, "btw": True}
            store.append_messages(conversation_id, [
                {"role": "user", "content": f"[btw] {question}",
                 "source": _btw_user_source, "timestamp": _btw_now},
                {"role": "assistant", "content": response.content,
                 "source": _btw_agent_source, "timestamp": _btw_now},
            ])

            # 5. Publish done event
            bus.publish_event(conversation_id, "btw_done", {
                "agent_name": agent_name,
                "question": question,
                "response": response.content,
                "source": _btw_agent_source,
            })
            logger.info(f"[btw:{conversation_id[:8]}] {agent_name} answered "
                        f"({len(response.content)} chars)")

        except Exception as e:
            logger.error(f"[btw:{conversation_id[:8]}] error: {e}", exc_info=True)
            bus.publish_event(conversation_id, "btw_done", {
                "agent_name": agent_name,
                "error": str(e),
            })

    def _streaming_agent_loop(self, ctx: Dict, conversation_id: str,
                              bus) -> None:
        """Background thread: run agent loop, publish events to EventBus.

        Supports autonomous continuation: if the agent calls the
        ``schedule_continuation`` tool during a round, the loop will
        publish a ``done`` event with the intermediate response, wait
        the requested delay, then start a new round with the
        continuation plan injected as a system message.
        """
        from core.conversation_event_bus import ConversationEventBus

        my_generation = ctx.get("_generation", 0)
        gen_key = ctx.get("_gen_key", conversation_id)
        start_time = time.time()
        total_tokens_in = 0
        total_tokens_out = 0

        def _update_interaction(**kwargs):
            """Update the active interaction tracker."""
            with self._interactions_lock:
                info = self._active_interactions.get(gen_key)
                if info:
                    info.update(kwargs)

        # Publish flowfile_in so the chat shows incoming activity
        _agent_name = ctx.get("active_agent_name", "")
        _is_poll = ctx.get("is_poll", False)
        _is_thought = ctx.get("is_random_thought", False)
        _scheduled_reasons = ctx.get("scheduled_reasons") or []
        _ff_reason = ""
        if _scheduled_reasons:
            _ff_reason = _scheduled_reasons[0] if len(_scheduled_reasons) == 1 else f"{len(_scheduled_reasons)} triggers"
        _ff_info = {"agent": _agent_name}
        if _ff_reason:
            _ff_info["reason"] = _ff_reason
        if _is_poll:
            _ff_info["type"] = "poll"
        if _is_thought:
            _ff_info["type"] = "thought"
        if not _is_poll or _ff_reason:
            # Don't publish for routine empty polls (no reason = nothing interesting)
            bus.publish_event(conversation_id, "flowfile_in", _ff_info)

        tools_called: List[str] = []

        client = ctx["client"]
        registry = ctx["registry"]
        tool_defs = ctx["tool_defs"]
        messages = ctx["messages"]  # LLM working context (may be compacted)
        model = ctx["model"]
        use_conv_store = ctx["use_conv_store"]
        conv_ttl = ctx["conv_ttl"]
        channel = ctx.get("channel", "")

        # Track new messages added during this run for append-only persistence.
        # The canonical conversation history lives in the ConversationStore and
        # is only extended — never overwritten — by this thread.
        new_messages: List[LLMMessage] = []
        # The user message was already appended to `messages` by _prepare_agent_context.
        # Record it as a new message so it gets persisted.
        base_count = ctx.get("_base_message_count", 0)
        if len(messages) > base_count:
            new_messages.extend(messages[base_count:])

        max_rounds = int(ctx.get("max_rounds", 1))
        iteration = 0
        final_model = ""
        finish_reason = ""
        response_content = ""
        _need_more_retried = False  # guards heuristic tool-mention retry (once per response)

        user_id = ctx.get("user_id", "")

        # Source metadata for identity tracking
        _agent_name = ctx.get("active_agent_name", "")
        _agent_svc = ctx.get("active_llm_service", "")

        # Set thread-local source agent on SpawnAgentsHandler
        from core.tool_registry import SpawnAgentsHandler as _SAH_stream
        for _h in registry.list_tools():
            if isinstance(_h, _SAH_stream):
                _h.set_source_agent(_agent_name or "assistant", _agent_svc)
                break
        # LLM client metadata for traceability
        _client_provider = getattr(client, "provider", "")
        _client_base_url = getattr(client, "base_url", "")

        # Resolve model from client (always available, unlike final_model which needs a response)
        _client_model = getattr(client, "default_model", "") or ""

        def _agent_source():
            import re as _re
            return {
                "type": "agent",
                "name": _agent_name or "assistant",
                "llm_service": _agent_svc or "",
                "provider": _client_provider or "",
                "model": _client_model,
                "base_url": _re.sub(r'(key|token|secret)=[^&]+', r'\1=***', _client_base_url) if _client_base_url else "",
            }

        _strip_echo_prefix = self._strip_echo_prefix

        def _append(msg: LLMMessage):
            """Append a message to both the LLM context and the new-messages list."""
            messages.append(msg)
            new_messages.append(msg)

        def _flush_new():
            """Persist new messages to the canonical conversation history (and context if diverged).

            Always persists — even when generation is stale. Messages shown
            to the user via SSE must be in the store.  ``append_messages``
            only appends (never overwrites), so concurrent appends are safe.
            The generation check now only gates the final ``save()`` in the
            done path (which sets status/metadata), not message persistence.
            """
            nonlocal new_messages
            if not (use_conv_store and conversation_id and new_messages):
                return
            if not self._is_current_generation(gen_key, my_generation):
                logger.info(f"[agent:{conversation_id[:8]}] generation {my_generation} "
                            f"is stale — flushing messages anyway (append-only)")
            from core.conversation_store import ConversationStore
            serialized = self._serialize_messages(new_messages, channel=channel)
            store = ConversationStore.instance()
            store.append_messages(
                conversation_id, serialized,
                ttl=conv_ttl, user_id=user_id,
            )
            if ctx.get("_context_diverged"):
                _flush_agent = ctx.get("active_agent_name") or "assistant"
                store.append_to_agent_context(conversation_id, _flush_agent, serialized)
            new_messages = []

        # Persist the user message immediately so it's never lost
        _flush_new()

        # Consecutive tool call limiter
        _consecutive_tool_s: Dict[str, int] = {}
        _max_consec_s = ctx.get("max_consecutive_tool_calls", 5)

        try:
            for current_round in range(1, max_rounds + 1):
                # Track continuation requests for this round
                continuation_plan = None
                continuation_delay = 3

                while iteration < ctx["max_iterations"]:
                    # Check cancellation at the very start of each iteration
                    if not self._is_current_generation(gen_key, my_generation):
                        raise AgentCancelled()

                    iteration += 1

                    # During poll first iteration, suppress streaming to avoid
                    # showing [NO_PENDING_WORK] in the UI. If tool calls happen,
                    # poll_silent flips off and subsequent iterations stream normally.
                    poll_silent = ctx.get("is_poll", False) and iteration == 1

                    # Notify client that LLM is being called
                    logger.info(f"[agent:{conversation_id[:8]}] round {current_round}/{max_rounds}, "
                                f"iteration {iteration}/{ctx['max_iterations']}, "
                                f"messages={len(messages)}, tools_called={len(tools_called)}")
                    # Always publish iteration_status (even during poll_silent)
                    bus.publish_event(conversation_id, "iteration_status", {
                        "agent_name": _agent_name or "assistant",
                        "iteration": iteration,
                        "max_iterations": ctx["max_iterations"],
                        "round": current_round,
                        "max_rounds": max_rounds,
                        "tools_called": tools_called[-3:],
                        "total_tools": len(tools_called),
                    })
                    if not poll_silent:
                        bus.publish_event(conversation_id, "thinking", {
                            "iteration": iteration,
                            "round": current_round,
                            "agent_name": _agent_name or "",
                        })

                    # Use streaming LLM call with token callback
                    token_parts: List[str] = []
                    last_token_time = time.time()

                    def on_token(text: str):
                        nonlocal last_token_time
                        if not self._is_current_generation(gen_key, my_generation):
                            raise AgentCancelled()
                        last_token_time = time.time()
                        token_parts.append(text)
                        if not poll_silent:
                            bus.publish_event(conversation_id, "token", {
                                "text": text,
                                "agent_name": _agent_name or "assistant",
                                "source": _agent_source(),
                            })

                    # Heartbeat thread (suppressed during silent poll)
                    heartbeat_stop = threading.Event()

                    def heartbeat():
                        while not heartbeat_stop.wait(5.0):
                            if poll_silent:
                                continue
                            elapsed = int(time.time() - last_token_time)
                            bus.publish_event(conversation_id, "thinking", {
                                "iteration": iteration,
                                "round": current_round,
                                "waiting_seconds": elapsed,
                                "agent_name": _agent_name or "",
                            })

                    hb_thread = threading.Thread(target=heartbeat, daemon=True)
                    hb_thread.start()

                    # Compact context if approaching token limit.
                    # Use summarizer service if available, else default client.
                    _summ = ctx.get("summarizer", (None, 0))
                    if _summ[0]:
                        compact_client = _summ[0]
                    else:
                        compact_client = ctx.get("default_client") or client
                    _pre_compact_len = len(messages)
                    llm_context = self._compact_if_needed(
                        list(messages), compact_client,
                        ctx.get("max_context_size", 64000),
                        ctx.get("context_compact_threshold", 0.8),
                        ctx.get("context_keep_recent", 6),
                        conversation_id=conversation_id,
                        agent_name=_agent_name or "assistant",
                        tool_defs=ctx.get("tool_defs"),
                    )
                    # If compaction happened, mark context as diverged so
                    # _flush_new() appends subsequent messages to the agent
                    # context (not just to the canonical messages).
                    if len(llm_context) < _pre_compact_len:
                        ctx["_context_diverged"] = True

                    # Inject identity prefixes so LLM knows who said what
                    _id_nicks = ctx.get("_nicknames") or {}
                    llm_context = self._inject_identity(llm_context, _id_nicks)
                    llm_context = self._apply_identity_suffix(llm_context, ctx.get("_identity_suffix", ""))

                    # Check cancellation before LLM call
                    if not self._is_current_generation(gen_key, my_generation):
                        raise AgentCancelled()

                    # Check interrupt — force synthesis instead of continuing
                    if self._check_interrupt(gen_key):
                        logger.info(f"[agent:{conversation_id[:8]}] interrupted — forcing synthesis")
                        _append(LLMMessage(
                            role="user",
                            content=(
                                "[System: The user has requested an immediate response. "
                                "Stop all tool usage. Summarize your progress so far and "
                                "provide your best answer with the information you have "
                                "gathered. Mention what you were still working on so the "
                                "user can ask you to continue if needed.]"
                            ),
                        ))
                        bus.publish_event(conversation_id, "thinking", {
                            "iteration": iteration, "round": "interrupt",
                            "agent_name": _agent_name or "",
                        })
                        interrupt_resp = client.complete_stream(
                            messages=self._compact_if_needed(
                                list(messages), compact_client,
                                ctx.get("max_context_size", 64000), 0.6,
                                ctx.get("context_keep_recent", 6),
                            ),
                            model=model or None,
                            temperature=ctx["temperature"],
                            max_tokens=ctx["max_tokens"],
                            tools=None,  # No tools — just answer
                            callback=on_token,
                        )
                        _append(LLMMessage(
                            role="assistant", content=interrupt_resp.content,
                            source=_agent_source(),
                        ))
                        response_content = interrupt_resp.content
                        total_tokens_in += interrupt_resp.tokens_in
                        total_tokens_out += interrupt_resp.tokens_out
                        final_model = interrupt_resp.model
                        _flush_new()
                        # Break out of both while and for loops
                        raise _InterruptComplete()

                    try:
                        response = client.complete_stream(
                            messages=llm_context,
                            model=model or None,
                            temperature=ctx["temperature"],
                            max_tokens=ctx["max_tokens"],
                            tools=tool_defs if tool_defs else None,
                            callback=on_token,
                        )
                    except AgentCancelled:
                        raise
                    except Exception as llm_err:
                        err_str = str(llm_err)
                        # Detect context overflow — force aggressive compaction and retry once
                        if "exceed_context_size" in err_str or "n_prompt_tokens" in err_str:
                            logger.warning(f"[agent:{conversation_id[:8]}] Context overflow detected, "
                                           f"forcing aggressive compaction and retrying...")
                            bus.publish_event(conversation_id, "thinking", {
                                "iteration": iteration, "detail": "compacting context...",
                                "agent_name": _agent_name or "",
                            })
                            llm_context = self._compact_if_needed(
                                llm_context, compact_client,
                                ctx.get("max_context_size", 64000),
                                0.5,  # aggressive threshold
                                ctx.get("context_keep_recent", 6),
                                conversation_id=conversation_id,
                                tool_defs=ctx.get("tool_defs"),
                            )
                            try:
                                heartbeat_stop.clear()
                                hb_thread = threading.Thread(target=heartbeat, daemon=True)
                                hb_thread.start()
                                response = client.complete_stream(
                                    messages=llm_context,
                                    model=model or None,
                                    temperature=ctx["temperature"],
                                    max_tokens=ctx["max_tokens"],
                                    tools=tool_defs if tool_defs else None,
                                    callback=on_token,
                                )
                            except Exception as retry_err:
                                logger.error(f"LLM retry also failed (iter {iteration}): {retry_err}")
                                bus.publish_event(conversation_id, "error_event", {
                                    "message": f"LLM call failed after compaction: {retry_err}",
                                })
                                response_content = f"Error: {retry_err}"
                                break
                            finally:
                                heartbeat_stop.set()
                                hb_thread.join(timeout=1)
                        else:
                            logger.error(f"LLM call failed (iter {iteration}): {llm_err}")
                            bus.publish_event(conversation_id, "error_event", {
                                "message": f"LLM call failed: {llm_err}",
                            })
                            response_content = f"Error: {llm_err}"
                            break
                    finally:
                        heartbeat_stop.set()
                        hb_thread.join(timeout=1)

                    # Check cancellation immediately after LLM call returns
                    if not self._is_current_generation(gen_key, my_generation):
                        raise AgentCancelled()

                    total_tokens_in += response.tokens_in
                    total_tokens_out += response.tokens_out
                    final_model = response.model
                    finish_reason = response.finish_reason

                    logger.info(f"[agent:{conversation_id[:8]}] LLM responded: "
                                f"tokens_in={response.tokens_in}, tokens_out={response.tokens_out}, "
                                f"tool_calls={len(response.tool_calls) if response.tool_calls else 0}, "
                                f"finish={finish_reason}, content_len={len(response.content or '')}")

                    if not response.tool_calls:
                        action, msgs, final, _need_more_retried = self._handle_response_no_tools(
                            response.content or "", _client_provider, tool_defs,
                            _need_more_retried, source=_agent_source(),
                        )
                        for _m in msgs:
                            _append(_m)
                        if action == "break":
                            response_content = final
                            _flush_new()
                            break
                        continue

                    # Tool calls
                    _need_more_retried = False  # reset on successful tool_call
                    _append(LLMMessage(
                        role="assistant", content=response.content,
                        tool_calls=response.tool_calls,
                        source=_agent_source(),
                    ))

                    # If poll was silent but LLM made tool calls → real work detected
                    # Emit thinking event to wake up the UI
                    if poll_silent and response.tool_calls:
                        poll_silent = False
                        bus.publish_event(conversation_id, "thinking", {
                            "iteration": iteration, "round": current_round,
                            "agent_name": _agent_name or "",
                        })

                    # Publish all tool_call events upfront
                    _sub_count = bus.subscriber_count(conversation_id)
                    for tc in response.tool_calls:
                        tools_called.append(tc.name)
                        logger.info(f"[agent:{conversation_id[:8]}] publishing tool_call SSE: "
                                    f"tool={tc.name}, subscribers={_sub_count}")
                        bus.publish_event(conversation_id, "tool_call", {
                            "tool": tc.name, "arguments": tc.arguments,
                            "agent_name": _agent_name or "assistant",
                            "llm_service": _agent_svc or "",
                        })
                    _update_interaction(
                        iteration=iteration, last_tool=response.tool_calls[-1].name,
                        status="tool_call",
                    )

                    # Execute tools with consecutive-call limiting
                    results_ordered = self._execute_tool_calls(
                        response.tool_calls, registry, _consecutive_tool_s,
                        _max_consec_s, parallel=True,
                        agent_name=_agent_name or "assistant",
                        agent_svc=_agent_svc or "",
                    )

                    # Process results in original order
                    for tc, result_text in results_ordered:
                        if tc.name == "schedule_continuation":
                            continuation_plan = tc.arguments.get("plan", "Continue working")
                            continuation_delay = int(tc.arguments.get("delay_seconds", 3))
                        _append(LLMMessage(role="tool", content=result_text, tool_call_id=tc.id))
                        _result_preview = result_text if tc.name == "spawn_agents" else (result_text or "")[:2000]
                        bus.publish_event(conversation_id, "tool_result", {
                            "tool": tc.name, "result": _result_preview,
                            "agent_name": _agent_name or "assistant",
                            "llm_service": _agent_svc or "",
                        })

                    bus.publish_event(conversation_id, "iteration_status", {
                        "agent_name": _agent_name or "assistant",
                        "iteration": iteration,
                        "max_iterations": ctx["max_iterations"],
                        "round": current_round,
                            "max_rounds": max_rounds,
                            "tools_called": tools_called[-3:],
                            "total_tools": len(tools_called),
                        })

                    # Check cancellation after tool execution
                    if not self._is_current_generation(gen_key, my_generation):
                        raise AgentCancelled()

                    # Flush tool calls + results to disk after each iteration
                    _flush_new()
                else:
                    # Max iterations reached — force synthesis
                    logger.warning("Agent reached max iterations (%d), forcing synthesis",
                                   ctx["max_iterations"])
                    bus.publish_event(conversation_id, "thinking", {
                        "iteration": iteration + 1, "round": current_round,
                        "agent_name": _agent_name or "",
                    })
                    _pre = len(messages)
                    content, ti, to, fm = self._force_synthesis(
                        messages, client, ctx,
                        prompt=(
                            "[System: You have reached the maximum number of tool calls. "
                            "You MUST now provide your final response to the user. "
                            "Synthesize all the information you gathered from your tool calls "
                            "and present a clear, comprehensive answer. Do NOT call any more tools.]"
                        ),
                        compact_client=compact_client,
                        use_streaming=True,
                        token_callback=lambda text: bus.publish_event(
                            conversation_id, "token", {"text": text}),
                        tools_called=tools_called, compact_threshold=1.0,
                        conversation_id=conversation_id,
                    )
                    new_messages.extend(messages[_pre:])
                    response_content = content
                    total_tokens_in += ti
                    total_tokens_out += to
                    if fm:
                        final_model = fm

                # Flush any remaining new messages to the canonical history
                _flush_new()

                # Check if continuation was requested
                if continuation_plan and current_round < max_rounds:
                    # Publish intermediate done so the UI shows the current response
                    bus.publish_event(conversation_id, "done", self._build_done_event(
                        conversation_id, response_content, _agent_name,
                        final_model or _client_model, _client_provider,
                        total_tokens_in, total_tokens_out, tools_called,
                        iteration, start_time, source=_agent_source(),
                        continuing=True,
                    ))

                    logger.info(f"[agent:{conversation_id[:8]}] continuation scheduled: "
                                f"plan='{continuation_plan}', delay={continuation_delay}s, "
                                f"next_round={current_round + 1}/{max_rounds}")

                    # Wait before continuing
                    time.sleep(continuation_delay)

                    # Inject continuation as a system message
                    _append(LLMMessage(
                        role="user",
                        content=(
                            f"[System: Automatic continuation — round {current_round + 1}]\n"
                            f"Continue with your plan: {continuation_plan}\n"
                            f"Build on your previous findings. When done, provide a final synthesis. "
                            f"If you still have more work, call schedule_continuation again."
                        ),
                    ))

                    # Reset response_content for next round
                    response_content = ""
                    continue
                else:
                    # No continuation — we're done
                    break

            # If the agent produced no final text, force a synthesis
            if not response_content:
                logger.warning(f"[agent:{conversation_id[:8]}] empty response — forcing synthesis")
                bus.publish_event(conversation_id, "thinking", {
                    "iteration": iteration + 1, "round": "synthesis",
                    "agent_name": _agent_name or "",
                })
                _pre = len(messages)
                content, ti, to, fm = self._force_synthesis(
                    messages, client, ctx,
                    prompt=(
                        "[System: You did not provide a response to the user. "
                        "You MUST respond now. Synthesize any information you have and present "
                        "a clear answer. Do NOT call any tools.]"
                    ),
                    compact_client=compact_client,
                    use_streaming=True,
                    token_callback=lambda text: bus.publish_event(
                        conversation_id, "token", {"text": text}),
                    tools_called=tools_called,
                    conversation_id=conversation_id,
                )
                new_messages.extend(messages[_pre:])
                response_content = content
                total_tokens_in += ti
                total_tokens_out += to
                if fm:
                    final_model = fm
                _flush_new()

            # Handle [NO_PENDING_WORK] / [RECHECK_IN:...] tags
            if "[NO_PENDING_WORK]" in (response_content or ""):
                import re as _re

                # Random thoughts must ALWAYS produce a response — reject NO_PENDING_WORK
                if _is_thought:
                    stripped_thought = _re.sub(r'\s*\[NO_PENDING_WORK\]', '', response_content or "")
                    stripped_thought = _re.sub(r'\s*\[RECHECK_IN:\d+\]', '', stripped_thought).strip()
                    if stripped_thought:
                        # Has real content — use it
                        response_content = stripped_thought
                    else:
                        # Empty — discard silently, next random thought will fire
                        logger.info(f"[agent:{conversation_id[:8]}] random thought returned "
                                    f"NO_PENDING_WORK — discarding (next thought will fire)")
                        bus.publish_event(conversation_id, "discard", {
                            "agent_name": _agent_name or "assistant",
                        })
                        new_messages.clear()
                        return
                    # Skip the cooldown/recheck logic for thoughts
                else:

                    recheck_match = _re.search(r'\[RECHECK_IN:(\d+)\]', response_content or "")
                    default_recheck = int(self.config.get("poll_recheck_delay", 7200))
                    recheck_delay = int(recheck_match.group(1)) if recheck_match else default_recheck

                    # Strip tags to see if there's real content underneath
                    stripped = _re.sub(r'\s*\[NO_PENDING_WORK\]', '', response_content)
                    stripped = _re.sub(r'\s*\[RECHECK_IN:\d+\]', '', stripped)
                    stripped = _re.sub(r'\[System:[^\]]*\]', '', stripped)
                    stripped = stripped.strip()

                    # Set cooldown (in-memory) AND persistent schedule
                    from core.conversation_store import ConversationStore
                    from core.poll_scheduler import PollScheduler
                    convs = ConversationStore.instance().list_conversations()
                    conv_meta = next((c for c in convs if c["conversation_id"] == conversation_id), None)
                    current_updated_at = conv_meta["updated_at"] if conv_meta else time.time()
                    self._poll_cooldown[conversation_id] = (current_updated_at, time.time() + recheck_delay)
                    # Persist to PollScheduler so it survives restarts
                    user_id = ctx.get("user_id", "")
                    PollScheduler.instance().schedule_delay(
                        conversation_id, recheck_delay, user_id=user_id,
                        reason="[RECHECK_IN] tag from agent response",
                    )

                    if not stripped:
                        # Pure poll check-in with nothing to say — discard entirely
                        logger.info(f"[agent:{conversation_id[:8]}] poll check-in: no pending work, "
                                    f"recheck in {recheck_delay}s (discarded)")
                        bus.publish_event(conversation_id, "discard", {
                            "agent_name": _agent_name or "assistant",
                        })
                        new_messages.clear()
                        # Mark conversation idle — agent has no pending work
                        ConversationStore.instance().set_status(conversation_id, "idle")
                        return
                    else:
                        # Real content + tags — keep the content, strip the tags
                        logger.info(f"[agent:{conversation_id[:8]}] response with NO_PENDING_WORK tag, "
                                    f"keeping {len(stripped)} chars, recheck in {recheck_delay}s")
                        response_content = stripped
                        # Also strip from the persisted assistant message
                        if new_messages:
                            last_assistant = None
                            for msg in reversed(new_messages):
                                if msg.role == "assistant":
                                    last_assistant = msg
                                    break
                            if last_assistant and "[NO_PENDING_WORK]" in (last_assistant.content or ""):
                                last_assistant.content = stripped
                        _flush_new()

            # Publish final done event
            logger.info(f"[agent:{conversation_id[:8]}] done: response_len={len(response_content or '')}, "
                        f"tools={tools_called}")
            bus.publish_event(conversation_id, "done", self._build_done_event(
                conversation_id, response_content, _agent_name,
                final_model or _client_model, _client_provider,
                total_tokens_in, total_tokens_out, tools_called,
                iteration, start_time, source=_agent_source(),
            ))

            # Track token usage
            self._track_tokens(
                ctx.get("user_id", "anonymous"),
                total_tokens_in, total_tokens_out,
                model=final_model or _client_model,
                agent_name=_agent_name or "assistant",
                llm_service=_agent_svc or "",
            )

            # Update conversation status — idle unless tools were used (active = may need follow-up)
            from core.conversation_store import ConversationStore as _CS
            _CS.instance().set_status(
                conversation_id,
                "active" if tools_called else "idle",
            )

        except _InterruptComplete:
            logger.info(f"[agent:{conversation_id[:8]}] interrupt synthesis done")
            bus.publish_event(conversation_id, "done", self._build_done_event(
                conversation_id, response_content, _agent_name,
                final_model or _client_model, _client_provider,
                total_tokens_in, total_tokens_out, tools_called,
                iteration, start_time, source=_agent_source(),
                interrupted=True,
            ))
            from core.conversation_store import ConversationStore as _CSi
            _CSi.instance().set_status(conversation_id, "active")
        except AgentCancelled:
            logger.info(f"[agent:{conversation_id[:8]}] cancelled — stopping gracefully")
            # Flush any partial messages accumulated so far
            _flush_new()
            # cancel_agent() already published the "cancelled" event and set status
        except Exception as e:
            logger.error(f"Streaming agent loop error: {e}", exc_info=True)
            # Flush any partial messages before reporting error
            _flush_new()
            bus.publish_event(conversation_id, "error_event", {
                "message": str(e),
                "conversation_id": conversation_id,
            })
        finally:
            self._decrement_active(conversation_id, ctx)

            # Auto-reschedule random thought if still enabled
            # BUT NOT if the agent was cancelled (generation is stale)
            _was_cancelled = not self._is_current_generation(gen_key, my_generation)
            if ctx.get("is_random_thought") and not _was_cancelled:
                try:
                    from core.conversation_store import ConversationStore as _CSrt
                    from core.poll_scheduler import PollScheduler as _PSrt
                    import random as _rng_rt
                    # Extract ALL agent names from scheduled reasons (not just first)
                    _rt_reasons = ctx.get("_scheduled_reasons", [])
                    _rt_agents = set()
                    for _rr in _rt_reasons:
                        if "[random_thought]" in _rr and "(" in _rr:
                            _rt_agents.add(_rr.rsplit("(", 1)[-1].rstrip(")"))
                    if not _rt_agents:
                        _rt_agents = {"assistant"}
                    from core.conversation_event_bus import ConversationEventBus as _EBrt
                    _rt_bus = _EBrt.instance()
                    _rt_store = _CSrt.instance()
                    for _rt_agent in _rt_agents:
                        _rt_agent_key = _rt_agent.lower()
                        _rt_extra_key = f"random_thought::{_rt_agent_key}"
                        _rt_config = _rt_store.get_extra(conversation_id, _rt_extra_key)
                        if _rt_config and _rt_config.get("enabled"):
                            _rt_delay = _rng_rt.randint(
                                _rt_config["min_interval"], _rt_config["max_interval"],
                            )
                            _PSrt.instance().schedule_delay(
                                conversation_id, _rt_delay,
                                key=f"{conversation_id}::thought::{_rt_agent_key}",
                                reason=f"[random_thought] spontaneous thought ({_rt_agent})",
                                user_id=ctx.get("user_id", ""),
                            )
                            _rt_bus.publish_event(conversation_id, "thought_scheduled", {
                                "agent": _rt_agent,
                                "delay": _rt_delay,
                                "frequency": _rt_config.get("frequency", ""),
                            })
                    # Set idle after thought
                    _rt_store.set_status(conversation_id, "idle")
                except Exception as _rt_err:
                    logger.warning(f"[agent] Failed to reschedule thought: {_rt_err}")

            # Auto-reschedule active tasks if agent didn't call complete_task
            if not _was_cancelled:
                try:
                    from core.conversation_store import ConversationStore as _CSat
                    from core.poll_scheduler import PollScheduler as _PSat
                    _at_store = _CSat.instance()
                    _at_sched = _PSat.instance()
                    _at_all = _at_store.get_extra(conversation_id, "agent_tasks") or {}
                    _at_agent = ctx.get("active_agent_name") or "assistant"
                    for _at_tid, _at_task in _at_all.items():
                        if not isinstance(_at_task, dict):
                            continue
                        if _at_task.get("agent") != _at_agent:
                            continue
                        if _at_task.get("status") != "active":
                            continue
                        _at_key = f"{conversation_id}::task::{_at_tid}"
                        if _at_sched.get(_at_key):
                            continue  # already scheduled
                        from core.tool_registry import AssignTaskHandler as _ATH
                        _at_delay = _ATH._get_task_delay(_at_task)
                        _at_sched.schedule_delay(
                            conversation_id, _at_delay,
                            key=_at_key,
                            reason=f"[agent_task:{_at_tid}] auto-reschedule ({_at_agent})",
                            user_id=ctx.get("user_id", ""),
                        )
                        logger.info(f"[task] Auto-rescheduled {_at_tid} for {_at_agent} "
                                    f"(agent didn't call complete_task)")
                except Exception as _at_err:
                    logger.warning(f"[agent] Failed to auto-reschedule tasks: {_at_err}")

    def _broadcast_agents(self, conversation_id: str, message: str,
                          user_id: str) -> None:
        """Send a message to ALL defined agents in parallel.

        Each response is published as an SSE 'agent_response' event,
        and a final 'broadcast_done' is sent when all are complete.
        """
        from core.conversation_event_bus import ConversationEventBus
        from core.conversation_store import ConversationStore
        from core.resource_store import ResourceStore
        from core.agent_executor import SubAgentExecutor, resolve_agent_task

        bus = ConversationEventBus.instance()

        try:
            rs = ResourceStore.instance()
            all_agents = rs.list_all("agent", user_id)
            if not all_agents:
                bus.publish_event(conversation_id, "error_event", {
                    "message": "No agents defined. Use /agent create first.",
                })
                return

            agent_names = [a["name"] for a in all_agents]
            all_targets = ["assistant"] + agent_names
            bus.publish_event(conversation_id, "thinking", {
                "detail": f"Broadcasting to {len(all_targets)} targets: {', '.join(all_targets)}",
            })

            # Resolve default LLM client
            task_llm_service = self.config.get("llm_service", "")
            if not task_llm_service or "${" in task_llm_service:
                task_llm_service = "default"
            client, _ = self._resolve_client(
                task_llm_service, user_id, resolve_expressions=False,
            )
            if not client:
                bus.publish_event(conversation_id, "error_event", {
                    "message": "No LLM service available for broadcast.",
                })
                return

            # Build tasks
            registry = self.get_tool_registry()
            self._configure_tool_handlers(registry)

            def _client_resolver(svc_id, uid):
                return self._resolve_llm_service(svc_id, uid)

            def _bc_on_event(event_type, data):
                try:
                    bus.publish_event(conversation_id, event_type, data)
                except Exception:
                    pass

            sub_executor = SubAgentExecutor(
                client, registry, max_workers=len(agent_names) + 1,
                client_resolver=_client_resolver,
                on_event=_bc_on_event,
            )

            tasks = []
            # Include the default assistant as a pseudo-agent
            from core.agent_executor import AgentTask
            import uuid
            default_prompt = self.config.get("system_prompt", "You are a helpful assistant.")
            tasks.append(AgentTask(
                id=uuid.uuid4().hex[:12],
                agent_name="assistant",
                message=message,
                system_prompt=default_prompt,
                llm_service=task_llm_service if task_llm_service != "default" else "",
                user_id=user_id,
            ))
            for name in agent_names:
                try:
                    task = resolve_agent_task(name, message, user_id)
                    tasks.append(task)
                except KeyError:
                    logger.warning("Broadcast: agent '%s' not found, skipping", name)

            if not tasks:
                bus.publish_event(conversation_id, "error_event", {
                    "message": "No valid agents to broadcast to.",
                })
                return

            # Spawn all agents in parallel
            results = sub_executor.spawn(tasks, wait=True)

            # Publish each result and persist in conversation
            cstore = ConversationStore.instance()
            for result in results:
                source = {
                    "type": "agent",
                    "name": result.agent_name,
                }
                content = result.response if result.status == "completed" else (
                    f"[Error: {result.error}]"
                )
                # Persist in conversation
                msg = LLMMessage(
                    role="assistant",
                    content=content,
                    source=source,
                )
                cstore.append_messages(
                    conversation_id,
                    self._serialize_messages([msg]),
                    user_id=user_id,
                )
                # Publish SSE event
                bus.publish_event(conversation_id, "agent_response", {
                    "agent_name": result.agent_name,
                    "response": content,
                    "source": source,
                    "status": result.status,
                    "tokens_in": result.tokens_in,
                    "tokens_out": result.tokens_out,
                    "duration_ms": round(result.duration_ms, 1),
                })

            # Broadcast complete
            bus.publish_event(conversation_id, "broadcast_done", {
                "agent_count": len(results),
                "message_count": cstore.message_count(conversation_id),
            })

            sub_executor.shutdown()

        except Exception as e:
            logger.error("Broadcast error: %s", e, exc_info=True)
            bus.publish_event(conversation_id, "error_event", {
                "message": f"Broadcast failed: {e}",
            })

    def _reschedule_active_tasks(self):
        """On poller startup, reschedule any active tasks that survived a restart."""
        from core.conversation_store import ConversationStore
        from core.poll_scheduler import PollScheduler
        store = ConversationStore.instance()
        scheduler = PollScheduler.instance()
        count = 0
        for conv in store.list_conversations():
            cid = conv["conversation_id"]
            entry = store._conversations.get(cid, {})
            all_tasks = entry.get("extra", {}).get("agent_tasks", {})
            if not isinstance(all_tasks, dict):
                continue
            for task_id, task in all_tasks.items():
                if not isinstance(task, dict):
                    continue
                if task.get("status") not in ("active", "verifying"):
                    continue
                agent = task.get("agent", "assistant")
                sched_key = f"{cid}::task::{task_id}"
                existing = scheduler.get(sched_key)
                if existing:
                    continue
                from core.tool_registry import AssignTaskHandler as _ATH_rs
                interval_s = _ATH_rs._get_task_delay(task)
                scheduler.schedule_delay(
                    cid, interval_s,
                    key=sched_key,
                    reason=f"[agent_task:{task_id}] resumed after restart ({agent})",
                    user_id=task.get("assigned_by", ""),
                )
                count += 1
                logger.info(f"[task] Rescheduled {task_id} for {agent} "
                            f"in conv {cid[:8]} (interval={interval_s}s)")
        if count:
            logger.info(f"[task] Rescheduled {count} active task(s) on startup")

    def _poll_conversations(self, interval: int) -> None:
        """Background poller: periodically check active conversations for pending work.

        For each eligible conversation (has an SSE subscriber, not currently being
        processed, last message was from assistant with tool usage), re-run the
        agent loop with a check-in prompt.
        """
        from core.conversation_event_bus import ConversationEventBus
        from core.conversation_store import ConversationStore

        logger.info(f"Agent poller running (interval={interval}s)")

        # On startup: reschedule any active tasks that have no pending schedule
        try:
            self._reschedule_active_tasks()
        except Exception as e:
            logger.warning(f"Failed to reschedule active tasks on startup: {e}")

        while not self._poller_stop.wait(interval):
            try:
                self._poll_once()
            except Exception as e:
                logger.error(f"Agent poller error: {e}", exc_info=True)

    def _poll_once(self) -> None:
        """Single poll iteration: check scheduled rechecks and active conversations."""
        from core.conversation_event_bus import ConversationEventBus
        from core.conversation_store import ConversationStore
        from core.poll_scheduler import PollScheduler

        bus = ConversationEventBus.instance()
        store = ConversationStore.instance()
        scheduler = PollScheduler.instance()

        # Watchdog: ensure active tasks always have a pending schedule
        try:
            self._ensure_tasks_scheduled()
        except Exception as _wt_err:
            logger.warning(f"Task watchdog failed: {_wt_err}")

        # Collect conversations to poll from two sources:
        # 1. Scheduled rechecks that are due (persistent, works without SSE)
        # 2. Active SSE conversations with cooldown expired (legacy behavior)
        to_poll: set[str] = set()
        # Scheduled rechecks bypass eligibility checks (they were explicitly requested)
        scheduled_ids: set[str] = set()

        # Source 1: PollScheduler — persistent scheduled rechecks
        # Map cid -> list of reasons for scheduled wakeups (non-thought)
        scheduled_reasons: Dict[str, List[str]] = {}
        # Thought entries are processed individually (each agent gets its own loop)
        thought_entries: List[Dict] = []
        due_entries = scheduler.get_due()
        for entry in due_entries:
            cid = entry["conversation_id"]
            entry_key = entry.get("key", cid)
            reason = entry.get("reason", "scheduled recheck")

            if "::thought::" in entry_key:
                # Thoughts are never blocked — they can arrive anytime
                thought_entries.append(entry)
                continue

            if "::task::" in entry_key or "::task_verify::" in entry_key:
                # Tasks are like thoughts — never cancelled by user interaction
                thought_entries.append(entry)
                continue

            logger.info(f"[poller] Scheduled recheck due for {cid[:8]}: {reason}")
            # Set status to active so the poll runs
            store.set_status(cid, "active")
            # Clear any in-memory cooldown that would block the poll
            self._poll_cooldown.pop(cid, None)
            to_poll.add(cid)
            scheduled_ids.add(cid)
            scheduled_reasons.setdefault(cid, []).append(reason)

        # Source 2: Active SSE conversations (user has UI open)
        # Only wake if cooldown expired — NOT on user interaction alone.
        # User interaction triggers the agent via the normal HTTP request path,
        # not via the poller.  The poller is only for autonomous check-ins.
        active_sse = bus.active_conversations()
        for conversation_id in active_sse:
            # Skip if this conversation already has a PollScheduler entry
            # (it will be woken at the right time by source 1)
            if scheduler.get(conversation_id):
                continue
            # Check cooldown
            cooldown = self._poll_cooldown.get(conversation_id)
            if cooldown:
                _last_updated_at, recheck_at = cooldown
                now = time.time()
                if now < recheck_at:
                    continue  # cooldown not expired yet
                # Cooldown expired — eligible for poll
                del self._poll_cooldown[conversation_id]
            else:
                # No cooldown set — skip (no autonomous work expected)
                continue
            to_poll.add(conversation_id)

        if not to_poll and not thought_entries:
            return

        # Process non-thought polls (grouped by conversation, one at a time)
        for conversation_id in to_poll:
            # Skip if already being processed — but reschedule so we don't lose it
            with self._active_lock:
                if conversation_id in self._active_conversations:
                    # Re-schedule the reasons so they're not lost
                    reasons = scheduled_reasons.get(conversation_id, [])
                    for r in reasons:
                        import re as _re_resched
                        # Extract task_id from reason pattern [agent_task:t_xxx]
                        _tid_m = _re_resched.search(r'\[agent_task:(t_\w+)\]', r)
                        if _tid_m:
                            scheduler.schedule_delay(
                                conversation_id, 10,  # retry in 10s
                                key=f"{conversation_id}::task::{_tid_m.group(1)}",
                                reason=r,
                            )
                    continue

            # Load conversation history
            messages_data = store.load(conversation_id)
            if not messages_data:
                continue

            # Scheduled rechecks bypass eligibility (explicitly requested by agent)
            if conversation_id not in scheduled_ids:
                if not self._is_eligible_for_poll(conversation_id, messages_data):
                    continue

            logger.info(f"[poller] Waking up conversation {conversation_id[:8]}")

            # Bump generation for the poll run
            with self._conv_gen_lock:
                gen = self._conv_generation.get(conversation_id, 0) + 1
                self._conv_generation[conversation_id] = gen

            # Mark as active
            with self._active_lock:
                self._active_conversations[conversation_id] = self._active_conversations.get(conversation_id, 0) + 1

            # Build context and run agent loop
            try:
                reasons = scheduled_reasons.get(conversation_id, [])
                ctx = self._build_poll_context(conversation_id, messages_data,
                                               scheduled_reasons=reasons)
                if ctx is None:
                    with self._active_lock:
                        rc = self._active_conversations.get(conversation_id, 1) - 1
                        if rc <= 0:
                            self._active_conversations.pop(conversation_id, None)
                        else:
                            self._active_conversations[conversation_id] = rc
                    continue
                ctx["_generation"] = gen
                ctx["_gen_key"] = conversation_id

                # Register in active interactions
                _poll_agent = ctx.get("active_agent_name", "") or "assistant"
                with self._interactions_lock:
                    self._active_interactions[conversation_id] = {
                        "agent_name": _poll_agent,
                        "message_preview": ", ".join(reasons)[:80] if reasons else "poll",
                        "started_at": time.time(),
                        "iteration": 0,
                        "last_tool": "",
                        "status": "thinking",
                        "conversation_id": conversation_id,
                    }

                bus.publish_event(conversation_id, "thinking", {
                    "iteration": 0,
                    "poll": True,
                })

                thread = threading.Thread(
                    target=self._streaming_agent_loop,
                    args=(ctx, conversation_id, bus),
                    daemon=True,
                    name=f"agent-poll-{conversation_id[:8]}",
                )
                thread.start()
            except Exception as e:
                logger.error(f"[poller] Failed to wake {conversation_id[:8]}: {e}")
                with self._active_lock:
                    rc = self._active_conversations.get(conversation_id, 1) - 1
                    if rc <= 0:
                        self._active_conversations.pop(conversation_id, None)
                    else:
                        self._active_conversations[conversation_id] = rc

        # Process thought entries individually (each agent gets its own loop)
        for entry in thought_entries:
            cid = entry["conversation_id"]
            entry_key = entry.get("key", cid)
            reason = entry.get("reason", "scheduled recheck")

            messages_data = store.load(cid)
            if not messages_data:
                continue

            # Extract agent name from key
            if "::task::" in entry_key or "::task_verify::" in entry_key:
                # Task key: conv::task::t_xxx — resolve agent from task data
                _task_id = entry_key.rsplit("::", 1)[-1]
                _all_tasks = store.get_extra(cid, "agent_tasks") or {}
                _task_entry = _all_tasks.get(_task_id, {})
                _thought_agent = _task_entry.get("agent", "assistant")
            elif "::" in entry_key:
                # Thought key: conv::thought::agent_name
                _thought_agent = entry_key.rsplit("::", 1)[-1]
            else:
                _thought_agent = "assistant"

            # Skip if this agent already has a thought running
            with self._active_lock:
                if entry_key in self._active_thoughts:
                    logger.info(f"[poller] Skipping thought {entry_key} — already running")
                    continue
                self._active_thoughts.add(entry_key)

            logger.info(f"[poller] Waking thought {entry_key} (agent={_thought_agent})")
            store.set_status(cid, "active")
            bus.publish_event(cid, "thought_firing", {"agent": _thought_agent})

            # Each thought agent gets its own gen_key so multiple thoughts
            # on the same conversation don't invalidate each other.
            _thought_gen_key = entry_key  # e.g. "conv_id::thought::grok"
            with self._conv_gen_lock:
                gen = self._conv_generation.get(_thought_gen_key, 0) + 1
                self._conv_generation[_thought_gen_key] = gen

            # Mark as active (but NOT user-active — won't block other thoughts)
            with self._active_lock:
                self._active_conversations[cid] = self._active_conversations.get(cid, 0) + 1

            try:
                ctx = self._build_poll_context(cid, messages_data,
                                               scheduled_reasons=[reason])
                if ctx is None:
                    with self._active_lock:
                        rc = self._active_conversations.get(cid, 1) - 1
                        if rc <= 0:
                            self._active_conversations.pop(cid, None)
                        else:
                            self._active_conversations[cid] = rc
                        self._active_thoughts.discard(entry_key)
                    continue
                ctx["_generation"] = gen
                ctx["_gen_key"] = _thought_gen_key
                ctx["_thought_key"] = entry_key

                # Register in active interactions so list_active reports it
                with self._interactions_lock:
                    self._active_interactions[_thought_gen_key] = {
                        "agent_name": _thought_agent,
                        "message_preview": reason[:80],
                        "started_at": time.time(),
                        "iteration": 0,
                        "last_tool": "",
                        "status": "thinking",
                        "conversation_id": cid,
                    }

                bus.publish_event(cid, "thinking", {
                    "iteration": 0,
                    "poll": True,
                    "agent_name": _thought_agent if _thought_agent != "assistant" else "",
                })

                thread = threading.Thread(
                    target=self._streaming_agent_loop,
                    args=(ctx, cid, bus),
                    daemon=True,
                    name=f"agent-thought-{entry_key[-16:]}",
                )
                thread.start()
            except Exception as e:
                logger.error(f"[poller] Failed thought {entry_key}: {e}")
                with self._active_lock:
                    rc = self._active_conversations.get(cid, 1) - 1
                    if rc <= 0:
                        self._active_conversations.pop(cid, None)
                    else:
                        self._active_conversations[cid] = rc
                    self._active_thoughts.discard(entry_key)

    def _ensure_tasks_scheduled(self):
        """Watchdog: ensure every active task has a pending schedule.

        Called at each poll cycle. If a task is active but has no schedule
        (lost due to race condition, restart, etc.), recreate it.
        """
        from core.conversation_store import ConversationStore
        from core.poll_scheduler import PollScheduler
        sched = PollScheduler.instance()
        store = ConversationStore.instance()
        for conv in store.list_conversations():
            cid = conv["conversation_id"]
            entry = store._conversations.get(cid, {})
            all_tasks = entry.get("extra", {}).get("agent_tasks", {})
            if not isinstance(all_tasks, dict):
                continue
            for tid, task in all_tasks.items():
                if not isinstance(task, dict):
                    continue
                if task.get("status") not in ("active",):
                    continue
                sched_key = f"{cid}::task::{tid}"
                if sched.get(sched_key):
                    continue  # already scheduled
                # Don't reschedule if task is currently running
                with self._active_lock:
                    if sched_key in self._active_thoughts:
                        continue
                from core.tool_registry import AssignTaskHandler
                delay = AssignTaskHandler._get_task_delay(task)
                sched.schedule_delay(
                    cid, delay, key=sched_key,
                    reason=f"[agent_task:{tid}] watchdog reschedule ({task.get('agent', '?')})",
                    user_id=task.get("assigned_by", ""),
                )
                logger.info(f"[task-watchdog] Rescheduled lost task {tid} for "
                            f"{task.get('agent', '?')} in {cid[:8]}")

    def _is_eligible_for_poll(self, conversation_id: str,
                              messages_data: List[Dict]) -> bool:
        """Check if a conversation is eligible for autonomous polling.

        Eligible if conversation status is ``active`` (set by the agent when
        it used tools and may have follow-up work).  Falls back to message
        heuristics if status is not set.
        """
        if not messages_data or len(messages_data) < 3:
            return False

        # Primary check: use conversation status
        from core.conversation_store import ConversationStore
        meta = ConversationStore.instance().get_metadata(conversation_id)
        if meta:
            status = meta.get("status", "idle")
            # Only poll active conversations
            if status != "active":
                return False

        # Find the last non-system message
        last_msg = None
        for msg in reversed(messages_data):
            role = msg.get("role", "")
            if role in ("assistant", "user", "tool"):
                last_msg = msg
                break

        if not last_msg:
            return False

        # Must end with assistant message (not waiting for user)
        if last_msg.get("role") != "assistant":
            return False

        # Don't re-poll if last message is already a poll check-in response
        content = last_msg.get("content", "")
        if "[NO_PENDING_WORK]" in content:
            return False

        # Must have had tool calls in history (active work, not just chat)
        has_tools = any(
            msg.get("role") == "tool" or msg.get("tool_calls")
            for msg in messages_data
        )
        if not has_tools:
            return False

        return True

    def _build_poll_context(self, conversation_id: str,
                            messages_data: List[Dict],
                            scheduled_reasons: Optional[List[str]] = None,
                            ) -> Optional[Dict]:
        """Build an agent context for a poll-triggered run."""
        model = self.config.get("model", "")

        svc_id = self.config.get("llm_service", "")
        if not svc_id or "${" in svc_id:
            svc_id = "default"
        # Recover user_id early for service resolution
        from core.conversation_store import ConversationStore as _CS2
        _meta = _CS2.instance().get_metadata(conversation_id)
        _poll_uid = _meta["user_id"] if _meta else ""

        # Resolve the agent executing this poll/thought (from scheduled reasons)
        _active_agent = None
        if scheduled_reasons:
            for _sr in scheduled_reasons:
                import re as _re_sched
                # Extract agent name from reason patterns
                if "[random_thought]" in _sr and "(" in _sr:
                    _active_agent = _sr.rsplit("(", 1)[-1].rstrip(")")
                    break
                # [agent_task:task_id] ... (agent_name)
                if "[agent_task:" in _sr and "(" in _sr:
                    _active_agent = _sr.rsplit("(", 1)[-1].rstrip(")")
                    break
                # [task_verify:task_id] verify by verifier (agent)
                _tv_match = _re_sched.search(r'\[task_verify:(\w+)\].*by (\w+)', _sr)
                if _tv_match:
                    _active_agent = _tv_match.group(2)
                    break
                # [scheduled:agent_name] reason text
                _sched_match = _re_sched.match(r'\[scheduled:(\w+)\]', _sr)
                if _sched_match:
                    _active_agent = _sched_match.group(1)
                    break
            if _active_agent and _active_agent != "assistant":
                try:
                    from core.resource_store import ResourceStore
                    rs = ResourceStore.instance()
                    uid = _poll_uid or "anonymous"
                    agent_def = rs.get_any("agent", _active_agent, uid)
                    if agent_def:
                        agent_svc = agent_def.get("llm_service", "")
                        if agent_svc:
                            # Resolve expressions like ${user.grok_llm_service}
                            if "${" in agent_svc:
                                from core.expression import resolve_expression
                                agent_svc = resolve_expression(agent_svc, owner=uid)
                            if agent_svc and "${" not in agent_svc:
                                svc_id = agent_svc
                                logger.info(f"[poll] Using agent '{_active_agent}' LLM service: {svc_id}")
                        agent_model = agent_def.get("model", "")
                        if agent_model:
                            model = agent_model
                except Exception as _e:
                    logger.debug(f"[poll] Could not resolve agent '{_active_agent}': {_e}")

        client, _poll_svc = self._resolve_client(
            svc_id, _poll_uid, resolve_expressions=False,
            default_model=model,
        )
        if not client:
            logger.warning("Poll: LLM service '%s' not found", svc_id)
            return None

        poll_user_id = _poll_uid

        registry = self.get_tool_registry()
        self._configure_tool_handlers(
            registry, conversation_id=conversation_id, user_id=poll_user_id,
        )

        # Create SubAgentExecutor for poll context (enables spawn_agents tool)
        from core.agent_executor import SubAgentExecutor
        def _client_resolver(svc_id, uid):
            return self._resolve_llm_service(svc_id, uid)
        def _poll_on_event(event_type, data):
            try:
                from core.conversation_event_bus import ConversationEventBus
                ConversationEventBus.instance().publish_event(conversation_id, event_type, data)
            except Exception:
                pass
        sub_executor = SubAgentExecutor(
            client, registry, max_workers=4,
            client_resolver=_client_resolver,
            on_event=_poll_on_event,
        )
        # Set spawn dependencies on SpawnAgentsHandler and UseSkillHandler
        from core.tool_registry import SpawnAgentsHandler as _SAH, UseSkillHandler as _USH
        _poll_source = _active_agent or "assistant"
        _poll_svc = svc_id or ""
        for h in registry.list_tools():
            if isinstance(h, _SAH):
                h.set_spawn_deps(client, _client_resolver, _poll_on_event, registry=registry)
                h.set_source_agent(_poll_source, _poll_svc)
            elif isinstance(h, _USH):
                h.set_spawn_deps(client, _client_resolver)

        tool_defs = [
            LLMToolDefinition(
                name=h.name, description=h.description, parameters=h.parameters_schema,
            )
            for h in registry.list_tools()
        ]

        # Load context (diverged) or fall back to messages
        system_prompt = self.config.get("system_prompt", "You are a helpful assistant.")
        # Use agent-specific prompt for non-default thought agents
        if _active_agent and _active_agent != "assistant":
            try:
                from core.resource_store import ResourceStore as _RSp
                _agent_def = _RSp.instance().get_any("agent", _active_agent, _poll_uid or "anonymous")
                if _agent_def and _agent_def.get("prompt"):
                    system_prompt = _agent_def["prompt"]
            except Exception:
                pass
        from datetime import datetime
        system_prompt += f"\n\nCurrent date and time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        # Inject identity block into thought agent's system prompt
        from core.conversation_store import ConversationStore as _CS3
        _nicknames = _CS3.instance().get_extra(conversation_id, "agent_nicknames") or {}
        _poll_model = getattr(client, "default_model", "") or model or ""
        _poll_provider = getattr(client, "provider", "") or ""
        system_prompt = self._build_identity_block(
            _active_agent, conversation_id, _nicknames,
            llm_service=svc_id,
            model=_poll_model,
            provider=_poll_provider,
        ) + system_prompt
        _poll_agent_key = _active_agent or "assistant"
        _context_data = _CS3.instance().load_agent_context(conversation_id, _poll_agent_key)
        _context_diverged = False
        try:
            if _context_data is not None:
                messages = self._deserialize_messages(_context_data)
                _context_diverged = True
            else:
                messages = self._deserialize_messages(messages_data)
        except (KeyError, TypeError):
            return None

        # Replace system prompt with identity-enriched version
        if messages and messages[0].role == "system":
            messages[0] = LLMMessage(role="system", content=system_prompt)

        # Inject poll check-in prompt (not persisted unless real work happens)

        # Check for agent task wake-up
        _is_task = any(
            "[agent_task:" in r for r in (scheduled_reasons or [])
        )
        _is_task_verify = any(
            "[task_verify:" in r for r in (scheduled_reasons or [])
        )
        is_random_thought = False

        if _is_task:
            # Load ALL active tasks for this agent from agent_tasks dict
            _task_agent = _active_agent or "assistant"
            _all_tasks = _CS3.instance().get_extra(conversation_id, "agent_tasks") or {}
            _my_tasks = [t for t in _all_tasks.values()
                         if isinstance(t, dict) and t.get("agent") == _task_agent
                         and t.get("status") in ("active",)]
            if not _my_tasks:
                checkin_content = "[System: No active tasks found.]"
            elif len(_my_tasks) == 1:
                _td = _my_tasks[0]
                _tid = _td["task_id"]
                _iter = _td.get("iterations_done", 0)
                _max = _td.get("max_iterations", 50)
                _rejection = _td.get("last_rejection")
                _rej_text = ""
                if _rejection:
                    _rej_text = (
                        f"\n\n[REJECTION] Rejected by {_rejection.get('by', '?')}: "
                        f"\"{_rejection.get('reason', '')}\". Address this."
                    )
                if _iter >= _max:
                    _td["status"] = "failed"
                    _all_tasks[_tid] = _td
                    _CS3.instance().set_extra(conversation_id, "agent_tasks", _all_tasks)
                    checkin_content = (
                        f"[System: Task {_tid} failed — max iterations ({_max}) reached]\n"
                        f"Inform the user."
                    )
                else:
                    checkin_content = (
                        f"[System: Task {_tid} — iteration {_iter + 1}/{_max}]\n\n"
                        f"**Task:** {_td.get('task', '?')}\n"
                        + (f"**Criteria:** {_td.get('completion_criteria', '')}\n" if _td.get("completion_criteria") else "")
                        + (f"**Progress:** {_td.get('last_result', '')}\n" if _td.get("last_result") else "")
                        + _rej_text + "\n\n"
                        f"Call complete_task(task_id=\"{_tid}\", done=true/false, progress=\"...\").\n"
                        "Do NOT repeat information already shared in previous iterations. "
                        "Focus on NEW progress only. Be concise.\n"
                        "Do NOT respond with [NO_PENDING_WORK]."
                    )
            else:
                # Multiple tasks
                lines = []
                for _td in _my_tasks:
                    _tid = _td["task_id"]
                    _iter = _td.get("iterations_done", 0)
                    _max = _td.get("max_iterations", 50)
                    lines.append(
                        f"- **{_tid}** (iter {_iter + 1}/{_max}): {_td.get('task', '?')[:100]}"
                        + (f" | Progress: {_td.get('last_result', '')[:60]}" if _td.get("last_result") else "")
                    )
                checkin_content = (
                    f"[System: {len(_my_tasks)} active tasks]\n\n"
                    + "\n".join(lines) + "\n\n"
                    "Work on your tasks. Call complete_task(task_id=\"...\", done=true/false, progress=\"...\") for each.\n"
                    "Do NOT repeat information from previous iterations. Focus on NEW progress only.\n"
                    "Do NOT respond with [NO_PENDING_WORK]."
                )
        elif _is_task_verify:
            # Find the task_id from the reason
            import re as _re_tv
            _verify_reason = next(
                (r for r in scheduled_reasons if "[task_verify:" in r), ""
            )
            _tv_match = _re_tv.search(r'\[task_verify:(t_\w+)\]', _verify_reason)
            _verify_tid = _tv_match.group(1) if _tv_match else ""
            _all_tasks = _CS3.instance().get_extra(conversation_id, "agent_tasks") or {}
            _task_data = _all_tasks.get(_verify_tid, {})
            _verified_agent = _task_data.get("agent", "?")
            checkin_content = (
                f"[System: Task verification request]\n\n"
                f"Agent '{_verified_agent}' claims to have completed task {_verify_tid}.\n\n"
                f"**Task:** {_task_data.get('task', '?')}\n"
                f"**Completion criteria:** {_task_data.get('completion_criteria', 'none specified')}\n"
                f"**Agent's result:** {_task_data.get('last_result', 'no result provided')}\n\n"
                f"Review the result against the criteria. Call "
                f"verify_task(agent='{_verified_agent}', approved=true/false, reason='...')."
            )
        else:

            is_random_thought = any(
                r.startswith("[random_thought]") for r in (scheduled_reasons or [])
            )
            if is_random_thought:
                checkin_content = (
                    "[System: You are continuing the conversation naturally.]\n"
                    "Think about what has been discussed so far. If something comes to mind — "
                    "a follow-up, a question, a new angle, something you forgot to mention, "
                    "a connection you just made — share it directly.\n"
                    "Respond as if you're still in the conversation, not arriving from somewhere else. "
                    "No preamble like 'a thought occurred to me' or 'while thinking about it'. "
                    "Just say what you have to say, naturally.\n"
                    "You can also engage other agents via spawn_agents if you want their perspective.\n"
                    "Do NOT respond with [NO_PENDING_WORK] — always contribute something."
                )
            elif scheduled_reasons:
                reasons_text = "\n".join(f"- {r}" for r in scheduled_reasons)
                checkin_content = (
                    "[System: Scheduled wake-up]\n"
                    f"You are being woken up because of scheduled reminder(s):\n"
                    f"{reasons_text}\n\n"
                    "Act on these scheduled reasons. Respond to the user accordingly.\n"
                    "If the reason is a reminder, remind the user.\n"
                    "If the reason is to continue work, continue using your tools.\n"
                    "Do NOT respond with [NO_PENDING_WORK] unless you have fully "
                    "addressed all scheduled reasons above."
                )
            else:
                checkin_content = (
                    "[System: Autonomous check-in]\n"
                    "Review the conversation above. Is there pending research or work "
                    "that you started but didn't finish? If yes, continue working on it "
                    "using your available tools.\n"
                    "If everything is complete, respond with [NO_PENDING_WORK].\n"
                    "You can also use the schedule_recheck tool to schedule a future check-in "
                    "at a specific time or after a delay."
                )
        messages.append(LLMMessage(role="user", content=checkin_content))
        # Set base count AFTER check-in prompt so it's not treated as "new"
        # and won't be persisted unless the agent does real work after it.
        base_message_count = len(messages)

        temperature = float(self.config.get("temperature", 0.7))
        max_tokens = int(self.config.get("max_context_size", 0))
        if not max_tokens and _poll_svc:
            max_tokens = int(getattr(_poll_svc, 'config', {}).get("max_context_size", 0))
        if not max_tokens:
            max_tokens = 4096
        max_iterations = int(self.config.get("max_iterations", 200))
        max_consecutive_tool_calls = int(self.config.get("max_consecutive_tool_calls", 5))
        conv_ttl = int(self.config.get("conversation_ttl", 0))


        # Source tracking
        _agent_name = _active_agent if _active_agent and _active_agent != "assistant" else ""
        _agent_svc = svc_id if svc_id != "default" else ""

        # Context window from service config
        _poll_ctx_max = int(
            (getattr(_poll_svc, 'config', {}) or {}).get("max_context_size", 0)
            or self.config.get("max_context_size", 64000)
        )

        return {
            "client": client, "registry": registry, "tool_defs": tool_defs,
            "messages": messages, "model": model,
            "temperature": temperature, "max_tokens": max_tokens,
            "max_iterations": max_iterations,
            "max_consecutive_tool_calls": max_consecutive_tool_calls,
            "max_rounds": int(self.config.get("max_rounds", 1)),
            "use_conv_store": True, "conv_ttl": conv_ttl,
            "conv_attr": "", "conversation_id": conversation_id,
            "user_id": poll_user_id,
            "max_context_size": _poll_ctx_max,
            "context_compact_threshold": float(self.config.get("context_compact_threshold", 0.8)),
            "context_keep_recent": int(self.config.get("context_keep_recent", 6)),
            "active_agent_name": _agent_name,
            "active_llm_service": _agent_svc,
            "is_poll": True,
            "is_random_thought": is_random_thought,
            "_scheduled_reasons": scheduled_reasons or [],
            "_base_message_count": base_message_count,
            "_context_diverged": _context_diverged,
            "sub_executor": sub_executor,
            "_nicknames": _nicknames,
        }

    def _wire_embed_fn(
        self, registry: ToolRegistry, client: LLMClient,
    ) -> None:
        """Wire embedding function into RememberHandler and SemanticRecallHandler."""
        from core.tool_registry import RememberHandler, SemanticRecallHandler

        if not client.api_key:
            return  # No API key, can't embed

        _api_key = client.api_key
        _base_url = client.base_url

        def embed_fn(text: str) -> List[float]:
            from core.embeddings import EmbeddingProvider
            results = EmbeddingProvider.instance().embed(
                [text], provider="auto", api_key=_api_key, base_url=_base_url,
            )
            return results[0] if results else []

        for h in registry.list_tools():
            if isinstance(h, RememberHandler):
                h.set_embed_fn(embed_fn)
            elif isinstance(h, SemanticRecallHandler):
                h.set_embed_fn(embed_fn)

    def _configure_tool_handlers(
        self, registry: ToolRegistry,
        conversation_id: str = "", user_id: str = "",
        llm_client=None, llm_model: str = "",
        agent_name: str = "", agent_svc: str = "",
    ) -> None:
        """Configure tool handlers with runtime settings (base_url, API keys, TTL)."""
        from core.tool_registry import (
            AskAgentHandler, BrowserActionHandler, CreateFileHandler,
            CreatePlanHandler,
            CreateToolHandler, ExecuteScriptHandler, FilesystemToolHandler,
            FlowManagerHandler,
            ForgetHandler, GetAgentResultsHandler,
            ImageGenerationHandler, VideoGenerationHandler,
            LinkIdentityHandler, LocalFilesHandler, ManageResourceHandler,
            NotifyUserHandler,
            RecallHandler, RememberHandler, RemoteExecutorHandler,
            SemanticRecallHandler,
            AssignTaskHandler, CompleteTaskHandler, VerifyTaskHandler,
            ListSecretsHandler,
            ScheduleRecheckHandler, ShowFileHandler, SpawnAgentsHandler,
            StoreSecretHandler, UpdatePlanHandler, UseSkillHandler,
        )

        file_base_url = self.config.get("file_base_url", "")
        # file_ttl is set per-request to match conversation TTL
        # (see _prepare_agent_context and _build_poll_context)
        # Resolve any remaining expressions (e.g. ${secrets.*} from cascaded ${flow.parameters.*})
        from core.expression import resolve_expression as _re
        _params = self._parameter_context._params if hasattr(self, '_parameter_context') and self._parameter_context else None
        if file_base_url and "${" in file_base_url:
            file_base_url = _re(file_base_url, parameters=_params)
            if "${" in file_base_url:
                file_base_url = ""

        for h in registry.list_tools():
            if isinstance(h, CreateFileHandler):
                if file_base_url:
                    h.set_base_url(file_base_url)
            elif isinstance(h, ExecuteScriptHandler):
                if file_base_url:
                    h.set_base_url(file_base_url)
                # Inject filesystem service resolver for fs:// URLs in scripts
                def _fs_resolver(svc_id):
                    try:
                        from gui.services.user_service_registry import UserServiceRegistry
                        svc = UserServiceRegistry.get_instance().get_live_instance(user_id, svc_id)
                        if svc:
                            return svc
                    except Exception:
                        pass
                    try:
                        from gui.services.global_service_registry import GlobalServiceRegistry
                        return GlobalServiceRegistry.get_instance().get_live_instance(svc_id)
                    except Exception:
                        return None
                h.set_fs_resolver(_fs_resolver)
            elif isinstance(h, ImageGenerationHandler):
                if file_base_url:
                    h.set_base_url(file_base_url)
                h.set_service_resolver(self._make_image_resolver(
                    user_id, conversation_id, agent_name,
                ))
            elif isinstance(h, VideoGenerationHandler):
                if file_base_url:
                    h.set_base_url(file_base_url)
                h.set_service_resolver(self._make_video_resolver(
                    user_id, conversation_id, agent_name,
                ))
                if conversation_id or user_id:
                    h.set_service_resolver(self._make_video_resolver(
                        user_id, conversation_id, agent_name,
                    ))
            elif isinstance(h, ScheduleRecheckHandler):
                if conversation_id:
                    h.set_conversation_id(conversation_id)
                if user_id:
                    h.set_user_id(user_id)
            elif isinstance(h, LocalFilesHandler):
                if conversation_id:
                    h.set_conversation_id(conversation_id)
            elif isinstance(h, (RememberHandler, RecallHandler, SemanticRecallHandler, ForgetHandler)):
                h.set_user_id(user_id)
                if hasattr(h, 'set_agent_name'):
                    h.set_agent_name(agent_name)
                if hasattr(h, 'set_conversation_id'):
                    h.set_conversation_id(conversation_id)
            elif isinstance(h, (AssignTaskHandler, CompleteTaskHandler, VerifyTaskHandler)):
                h.set_conversation_id(conversation_id)
                h.set_agent_name(agent_name)
                if hasattr(h, 'set_user_id'):
                    h.set_user_id(user_id)
                if hasattr(h, 'set_agent_name'):
                    h.set_agent_name(agent_name)
                if hasattr(h, 'set_conversation_id'):
                    h.set_conversation_id(conversation_id)
            elif isinstance(h, BrowserActionHandler):
                if conversation_id:
                    h.set_conversation_id(conversation_id)
            elif isinstance(h, LinkIdentityHandler):
                if user_id:
                    h.set_user_id(user_id)
            elif isinstance(h, (CreatePlanHandler, UpdatePlanHandler)):
                if conversation_id:
                    h.set_conversation_id(conversation_id)
            elif isinstance(h, NotifyUserHandler):
                if conversation_id:
                    h.set_conversation_id(conversation_id)
                if user_id:
                    h.set_user_id(user_id)
            elif isinstance(h, CreateToolHandler):
                if user_id:
                    h.set_user_id(user_id)
                if conversation_id:
                    h.set_conversation_id(conversation_id)
            elif isinstance(h, FlowManagerHandler):
                if user_id:
                    h.set_user_id(user_id)
                if conversation_id:
                    h.set_conversation_id(conversation_id)
            elif isinstance(h, StoreSecretHandler):
                if user_id:
                    h.set_user_id(user_id)
                if conversation_id:
                    h.set_conversation_id(conversation_id)
            elif isinstance(h, ListSecretsHandler):
                if user_id:
                    h.set_user_id(user_id)
            elif isinstance(h, AskAgentHandler):
                if conversation_id:
                    h.set_conversation_id(conversation_id)
                if llm_client:
                    h.set_llm_client(llm_client, llm_model)
            elif isinstance(h, ManageResourceHandler):
                h.set_user_id(user_id)
                h.set_conversation_id(conversation_id)
                h.set_agent_name(agent_name)
                h.set_llm_service(agent_svc)
            elif isinstance(h, (SpawnAgentsHandler, UseSkillHandler)):
                if user_id:
                    h.set_user_id(user_id)
                if isinstance(h, SpawnAgentsHandler):
                    if conversation_id:
                        h.set_conversation_id(conversation_id)
                    if agent_name:
                        h.set_source_agent(agent_name, agent_svc)
                # SubAgentExecutor is set up lazily in _prepare_agent_context
            elif isinstance(h, ShowFileHandler):
                if file_base_url:
                    h.set_base_url(file_base_url)
            elif isinstance(h, RemoteExecutorHandler):
                if conversation_id:
                    h.set_conversation_id(conversation_id)
                if user_id:
                    h.set_user_id(user_id)
                exec_svc = self._find_executor_service(user_id)
                if exec_svc:
                    h.set_service(exec_svc)
                # Plan D: pass available services list
                exec_services = self._list_available_services(user_id, "remoteExecutor")
                if exec_services:
                    h.set_available_services(exec_services)
            elif isinstance(h, FilesystemToolHandler):
                if user_id:
                    h.set_user_id(user_id)
                # Try to inject filesystem service (Plan B: cross-channel)
                fs_svc = self._find_filesystem_service(user_id)
                if fs_svc:
                    if hasattr(fs_svc, 'set_user_id') and user_id:
                        fs_svc.set_user_id(user_id)
                    h.set_fs_service(fs_svc)
                # Plan D: pass available services list
                fs_services = self._list_available_services(user_id, "filesystem")
                if fs_services:
                    h.set_available_services(fs_services)

    def _find_filesystem_service(self, user_id: str = ""):
        """Find the first available filesystem service.

        Search order: flow services → UserServiceRegistry (Plan B cross-channel).
        """
        services = getattr(self, '_services', {})
        fs_types = ("localFilesystem", "wsFilesystem", "browserFilesystem",
                     "serverFilesystem", "googleDrive", "oneDrive")
        for svc in services.values():
            svc_type = getattr(svc, 'TYPE', '')
            if svc_type in fs_types:
                return svc
        # Plan B: fallback to user-installed services
        if user_id:
            try:
                from gui.services.user_service_registry import UserServiceRegistry
                registry = UserServiceRegistry.get_instance()
                for fs_type in fs_types:
                    compatible = registry.get_compatible(fs_type, user_id)
                    for sdef in compatible:
                        if sdef.enabled:
                            svc = registry.get_live_instance(user_id, sdef.service_id)
                            if svc:
                                return svc
            except Exception:
                pass
        # Plan B: check RelayConnectionManager for WS relays
        if user_id:
            try:
                from core.relay_manager import RelayConnectionManager
                mgr = RelayConnectionManager.instance()
                conn = mgr.get(user_id, relay_type="filesystem")
                if conn:
                    from gui.services.user_service_registry import UserServiceRegistry
                    registry = UserServiceRegistry.get_instance()
                    svc = registry.get_live_instance(user_id, conn.relay_id)
                    if svc:
                        return svc
            except Exception:
                pass
        return None

    def _find_executor_service(self, user_id: str = ""):
        """Find the first available remote executor service.

        Search order: flow services → UserServiceRegistry (Plan B cross-channel).
        """
        services = getattr(self, '_services', {})
        for svc in services.values():
            svc_type = getattr(svc, 'TYPE', '')
            if svc_type == "remoteExecutor":
                return svc
        # Plan B: fallback to user-installed services
        if user_id:
            try:
                from gui.services.user_service_registry import UserServiceRegistry
                registry = UserServiceRegistry.get_instance()
                compatible = registry.get_compatible("remoteExecutor", user_id)
                for sdef in compatible:
                    if sdef.enabled:
                        svc = registry.get_live_instance(user_id, sdef.service_id)
                        if svc:
                            return svc
            except Exception:
                pass
        # Plan B: check RelayConnectionManager for WS relays
        if user_id:
            try:
                from core.relay_manager import RelayConnectionManager
                mgr = RelayConnectionManager.instance()
                conn = mgr.get(user_id, relay_type="executor")
                if conn:
                    from gui.services.user_service_registry import UserServiceRegistry
                    registry = UserServiceRegistry.get_instance()
                    svc = registry.get_live_instance(user_id, conn.relay_id)
                    if svc:
                        return svc
            except Exception:
                pass
        return None

    def _list_available_services(self, user_id: str, service_type: str) -> list:
        """Plan D: list all available services of a type for the user."""
        result = []
        # Flow services
        services = getattr(self, '_services', {})
        for sid, svc in services.items():
            svc_type = getattr(svc, 'TYPE', '')
            if service_type == "remoteExecutor" and svc_type == "remoteExecutor":
                info = svc.get_relay_info() if hasattr(svc, 'get_relay_info') else {}
                result.append({"id": sid, "type": svc_type, "root": info.get("root", "?")})
            elif service_type == "filesystem" and svc_type in (
                "localFilesystem", "wsFilesystem", "browserFilesystem",
                "serverFilesystem", "googleDrive", "oneDrive",
            ):
                result.append({"id": sid, "type": svc_type, "root": "?"})
        # User services
        if user_id:
            try:
                from gui.services.user_service_registry import UserServiceRegistry
                registry = UserServiceRegistry.get_instance()
                all_defs = registry.get_all_for_user(user_id)
                for sid, sdef in all_defs.items():
                    if not sdef.enabled:
                        continue
                    if service_type == "remoteExecutor" and sdef.service_type == "remoteExecutor":
                        if not any(s["id"] == sid for s in result):
                            result.append({
                                "id": sid, "type": sdef.service_type,
                                "root": sdef.description or "?",
                            })
                    elif service_type == "filesystem" and sdef.service_type in (
                        "localFilesystem", "wsFilesystem", "browserFilesystem",
                        "serverFilesystem", "googleDrive", "oneDrive",
                    ):
                        if not any(s["id"] == sid for s in result):
                            result.append({
                                "id": sid, "type": sdef.service_type,
                                "root": sdef.description or "?",
                            })
            except Exception:
                pass
        return result

    @staticmethod
    def _cleanup_conversation_resources(conversation_id: str):
        """Cascade-delete all resources tied to a conversation: flows, tools, secrets."""
        from core.tool_registry import FlowManagerHandler, StoreSecretHandler
        try:
            FlowManagerHandler.cleanup_conversation(conversation_id)
        except Exception as e:
            logger.warning(f"[cleanup] flow cleanup failed: {e}")
        try:
            StoreSecretHandler.cleanup_conversation(conversation_id)
        except Exception as e:
            logger.warning(f"[cleanup] secret cleanup failed: {e}")
        try:
            from core.dynamic_tool_store import DynamicToolStore
            DynamicToolStore.instance().cleanup_conversation(conversation_id)
        except Exception as e:
            logger.warning(f"[cleanup] dynamic tool cleanup failed: {e}")

    @staticmethod
    def _cleanup_conversation_files(messages: List[Dict[str, Any]]):
        """Delete files referenced in conversation messages (on conv delete)."""
        import re
        from core.file_store import FileStore
        store = FileStore.instance()
        file_ids = set()
        # Scan for /files/{file_id}/ patterns in message content
        pattern = re.compile(r'/files/([a-f0-9]{12})/')
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                for match in pattern.finditer(content):
                    file_ids.add(match.group(1))
        for fid in file_ids:
            store.delete(fid)
        if file_ids:
            logger.info(f"[cleanup] deleted {len(file_ids)} files from conversation")

    def _filter_tools_by_role(self, registry: ToolRegistry,
                              user_role: str) -> ToolRegistry:
        """Return a filtered registry containing only tools the user can access.

        Each tool handler may have an ``allowed_roles`` attribute (set by
        load_agent_tools from the flow config).  If not set, the tool is
        accessible to everyone.
        """
        filtered = ToolRegistry()
        for handler in registry.list_tools():
            allowed = getattr(handler, "allowed_roles", None)
            if allowed is None or user_role in allowed:
                filtered.register(handler)
        return filtered

    # ── Context rebuild ─────────────────────────────────────────────

    # ── Context compaction ────────────────────────────────────────────

    @staticmethod
    @staticmethod
    def _estimate_tokens(messages: List[LLMMessage],
                         tool_defs: list = None) -> int:
        """Token estimate: ~3 chars per token (conservative to avoid overflow).

        Uses 3 chars/token instead of 3.5-4 to ensure compaction triggers
        before the real limit is hit.  Each message also adds ~4 tokens of
        overhead (role, separators).

        *tool_defs* are the tool schemas sent alongside messages — these
        consume tokens too and must be counted.
        """
        total_chars = 0
        for m in messages:
            total_chars += 12  # message overhead (role, separators)
            if isinstance(m.content, str):
                total_chars += len(m.content)
            elif isinstance(m.content, list):
                for part in m.content:
                    if part.get("type") == "text":
                        total_chars += len(part.get("text", ""))
                    elif part.get("type") == "document":
                        total_chars += len(part.get("text", ""))
                    elif part.get("type") == "image_url":
                        total_chars += 1000
            if m.tool_calls:
                for tc in m.tool_calls:
                    total_chars += len(tc.name) + len(json.dumps(tc.arguments))
        # Tool definitions (JSON schemas) are sent with every request
        if tool_defs:
            for td in tool_defs:
                # name + description + parameter schema
                total_chars += len(getattr(td, 'name', '') or '')
                total_chars += len(getattr(td, 'description', '') or '')
                params = getattr(td, 'parameters', None)
                if params:
                    total_chars += len(json.dumps(params) if isinstance(params, dict) else str(params))
        return int(total_chars / 3)

    def _compact_if_needed(
        self,
        messages: List[LLMMessage],
        client: LLMClient,
        max_tokens: int,
        threshold: float,
        keep_recent: int,
        conversation_id: str = "",
        agent_name: str = "",
        tool_defs: list = None,
    ) -> List[LLMMessage]:
        """Compact conversation history if approaching the token limit.

        Strategy:
        1. First pass: truncate long tool_results (>500 chars → 200 + "...truncated")
        2. If still over threshold: summarize old messages via LLM call

        Always preserves:
        - System prompt (first message)
        - Last `keep_recent` messages (never compacted)

        If *conversation_id* is given, the resulting summary is persisted
        to the ConversationStore so it can be reused after a restart.
        """
        estimated = self._estimate_tokens(messages, tool_defs=tool_defs)
        limit = int(max_tokens * threshold)

        if estimated <= limit:
            return messages

        logger.info(f"[compact] Estimated {estimated} tokens (limit {limit}), compacting...")

        # Pass 1: Truncate long tool results
        truncated = False
        for m in messages:
            if m.role == "tool" and isinstance(m.content, str) and len(m.content) > 500:
                m.content = m.content[:200] + "\n...[truncated]..."
                truncated = True

        if truncated:
            estimated = self._estimate_tokens(messages)
            if estimated <= limit:
                logger.info(f"[compact] Pass 1 (truncate tool results) sufficient: {estimated} tokens")
                return messages

        # Pass 2: LLM-based summarization of old messages
        if len(messages) <= keep_recent + 1:
            # Not enough messages to compact
            logger.info(f"[compact] Only {len(messages)} messages, cannot compact further")
            return messages

        # Split: system prompt | old messages | recent messages
        system_msg = messages[0] if messages[0].role == "system" else None
        start_idx = 1 if system_msg else 0
        split_point = len(messages) - keep_recent
        if split_point <= start_idx:
            return messages

        old_messages = messages[start_idx:split_point]
        recent_messages = messages[split_point:]

        # Summarize old messages — target = 1/4 of context max
        _summary_target = max(500, int(max_tokens / 4))
        try:
            summary = self._summarize_messages(old_messages, client, max_tokens,
                                               target_tokens=_summary_target,
                                               conversation_id=conversation_id)
        except Exception as e:
            logger.error(f"[compact] Summarization failed: {e}")
            return messages  # Keep original if summarization fails

        # Rebuild messages: system + summary + recent
        compacted: List[LLMMessage] = []
        if system_msg:
            compacted.append(system_msg)
        compacted.append(LLMMessage(
            role="user",
            content=f"[Conversation summary — earlier messages compacted]\n\n{summary}",
        ))
        compacted.append(LLMMessage(
            role="assistant",
            content="Understood. I have the context from our earlier conversation. Continuing from where we left off.",
        ))
        compacted.extend(recent_messages)

        new_estimate = self._estimate_tokens(compacted)
        logger.info(f"[compact] Final: {new_estimate} tokens (was {estimated}), "
                    f"{len(compacted)} messages (was {len(messages)})")

        # Persist the compacted context so it survives restarts
        if conversation_id:
            try:
                from core.conversation_store import ConversationStore
                serialized = self._serialize_messages(compacted)
                ConversationStore.instance().save_agent_context(
                    conversation_id, agent_name, serialized,
                )
                logger.info(f"[compact] Persisted context for {conversation_id[:8]} "
                            f"({len(compacted)} messages)")
            except Exception as e:
                logger.warning(f"[compact] Failed to persist context: {e}")

        return compacted

    def _summarize_messages(
        self,
        old_messages: List[LLMMessage],
        client: LLMClient,
        max_tokens: int,
        target_tokens: int = 0,
        conversation_id: str = "",
    ) -> str:
        """Summarize messages, chunking if the text is too large for the LLM.

        Args:
            max_tokens: context window of the summarizer LLM
            target_tokens: desired output size (0 = max_tokens/4)

        Strategy: estimate how many tokens the summary request will use.
        If it exceeds ~70% of max_tokens, split messages in half, summarize
        each half recursively, combine, and do a final summary pass.
        """
        if not target_tokens:
            target_tokens = max(500, int(max_tokens / 4))

        summary_text = self._sanitize_for_llm(self._messages_to_text(old_messages))
        text_tokens = self._estimate_tokens([LLMMessage(role="user", content=summary_text)])
        safe_limit = int(max_tokens * 0.65)  # leave room for system prompt + output

        def _pub(stage, detail=""):
            if conversation_id:
                try:
                    from core.conversation_event_bus import ConversationEventBus
                    ConversationEventBus.instance().publish_event(
                        conversation_id, "compact_progress",
                        {"stage": stage, "detail": detail},
                    )
                except Exception:
                    pass

        if text_tokens <= safe_limit:
            _pub("summarizing", f"single pass ({text_tokens} tokens)")
            return self._call_summarize(client, summary_text, target_tokens)

        # Too large — split in half and summarize each part recursively
        mid = len(old_messages) // 2
        if mid == 0:
            truncated = summary_text[:safe_limit * 3]
            _pub("summarizing", "single message (truncated)")
            return self._call_summarize(client, truncated, target_tokens)

        _pub("chunking", f"splitting {len(old_messages)} messages into 2 chunks")
        logger.info(f"[compact] Text too large ({text_tokens} tokens > {safe_limit}), "
                    f"splitting {len(old_messages)} messages into 2 chunks")

        chunk_target = max(250, target_tokens // 2)
        _pub("summarizing", f"chunk 1/{2} ({mid} messages)")
        summary_a = self._summarize_messages(old_messages[:mid], client, max_tokens,
                                             chunk_target, conversation_id)
        _pub("summarizing", f"chunk 2/{2} ({len(old_messages) - mid} messages)")
        summary_b = self._summarize_messages(old_messages[mid:], client, max_tokens,
                                             chunk_target, conversation_id)

        combined = f"Part 1:\n{summary_a}\n\nPart 2:\n{summary_b}"
        combined_tokens = self._estimate_tokens([LLMMessage(role="user", content=combined)])

        if combined_tokens <= safe_limit:
            _pub("summarizing", "merging chunks")
            return self._call_summarize(client, combined, target_tokens)
        else:
            # Still too big — just concatenate (will be compacted on next cycle)
            logger.warning(f"[compact] Combined summaries still large ({combined_tokens} tokens), concatenating")
            return combined

    def _call_summarize(self, client: LLMClient, text: str,
                        target_tokens: int = 0) -> str:
        """Single LLM call to summarize text."""
        if not target_tokens:
            target_tokens = 2000
        clean_text = self._sanitize_for_llm(text)
        target_instruction = (
            f"Target length: approximately {target_tokens} tokens. "
            f"Use the full budget — do not produce a shorter summary than needed."
        )
        try:
            response = client.complete(
                messages=[
                    LLMMessage(role="system", content=(
                        "You are a conversation summarizer. Summarize the following conversation "
                        "exchange concisely, preserving all key facts, decisions, research findings, "
                        "URLs discovered, tool results, and any important context. "
                        "Do NOT lose any factual information. Be concise but complete. "
                        + target_instruction
                    )),
                    LLMMessage(role="user", content=clean_text),
                ],
                temperature=0.3,
                max_tokens=0,  # no output limit — target is in the prompt
            )
        except Exception as e:
            err_str = str(e)
            # Log debug info to help diagnose malformed content
            if "parse" in err_str.lower() or "500" in err_str:
                # Find the approximate problematic position
                import re as _re
                pos_match = _re.search(r'pos (\d+)', err_str)
                pos = int(pos_match.group(1)) if pos_match else -1
                context_start = max(0, pos - 100)
                context_end = min(len(clean_text), pos + 100)
                snippet = clean_text[context_start:context_end]
                # Show char codes around the problem area
                if pos >= 0 and pos < len(clean_text):
                    char_codes = [f"0x{ord(c):04x}" for c in clean_text[max(0,pos-5):pos+5]]
                else:
                    char_codes = []
                logger.error(
                    f"[compact] Summarization parse error at pos {pos}, "
                    f"text_len={len(clean_text)}, "
                    f"nearby_chars={char_codes}, "
                    f"snippet=...{repr(snippet)}..."
                )
                # Fallback: aggressively strip non-ASCII and retry
                ascii_text = clean_text.encode("ascii", errors="replace").decode("ascii")
                try:
                    response = client.complete(
                        messages=[
                            LLMMessage(role="system", content=(
                                "You are a conversation summarizer. Summarize concisely, "
                                "preserving key facts, decisions, and findings."
                            )),
                            LLMMessage(role="user", content=ascii_text),
                        ],
                        model=model or None,
                        temperature=0.3,
                        max_tokens=2000,
                    )
                    logger.info("[compact] ASCII fallback succeeded")
                except Exception as e2:
                    logger.error(f"[compact] ASCII fallback also failed: {e2}")
                    raise
            else:
                raise
        summary = response.content
        logger.info(f"[compact] Summarized {len(text)} chars into {len(summary)} chars "
                    f"({self._estimate_tokens([LLMMessage(role='user', content=summary)])} tokens)")
        return summary

    def _call_summarize_with_budget(self, client: LLMClient,
                                     text: str, max_tokens: int) -> str:
        """Re-summarize text to fit within an approximate token budget."""
        clean = self._sanitize_for_llm(text)
        response = client.complete(
            messages=[
                LLMMessage(role="system", content=(
                    f"Summarize the following text in approximately {max_tokens} tokens. "
                    "Preserve all key facts, decisions, findings, and context. "
                    "Be concise but complete. Do NOT exceed the token budget."
                )),
                LLMMessage(role="user", content=clean),
            ],
            temperature=0.3,
            max_tokens=min(max_tokens * 2, 4096),
        )
        return response.content

    @staticmethod
    def _sanitize_for_llm(text: str) -> str:
        """Remove characters that break LLM API JSON parsing."""
        import re as _re
        # Strip C0/C1 control chars except \n \r \t
        text = _re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', text)
        # Remove lone surrogates (invalid in JSON)
        text = text.encode("utf-8", errors="replace").decode("utf-8", errors="replace")
        # Replace null bytes that may survive
        text = text.replace('\x00', '')
        return text

    @staticmethod
    def _messages_to_text(messages: List[LLMMessage]) -> str:
        """Convert a list of messages to readable text for summarization."""
        lines = []
        for m in messages:
            role = m.role.upper()
            if isinstance(m.content, str):
                content = m.content
            elif isinstance(m.content, list):
                parts = []
                for p in m.content:
                    if p.get("type") == "text":
                        parts.append(p["text"])
                    elif p.get("type") == "document":
                        parts.append(f"[Document: {p.get('filename', 'file')}] {p.get('text', '')[:500]}")
                    elif p.get("type") == "image_url":
                        parts.append("[Image attached]")
                content = "\n".join(parts)
            else:
                content = str(m.content)

            if m.tool_calls:
                tc_desc = ", ".join(f"{tc.name}({json.dumps(tc.arguments)[:100]})" for tc in m.tool_calls)
                lines.append(f"{role}: {content}\n  Tool calls: {tc_desc}")
            elif m.role == "tool":
                lines.append(f"TOOL_RESULT (id={m.tool_call_id}): {content[:300]}")
            else:
                lines.append(f"{role}: {content}")
        return "\n\n".join(lines)

    # ── Attachment handling ──────────────────────────────────────────

    def _build_user_content(self, text: str, attachments: List[Dict]) -> Any:
        """Build user message content from text and optional attachments.

        If no attachments, returns plain str.
        If attachments exist, returns multi-part list for vision/document support.

        Attachment format from client:
            {"filename": "photo.png", "mime_type": "image/png", "data": "base64..."}
            {"filename": "doc.pdf", "mime_type": "application/pdf", "data": "base64..."}
        """
        if not attachments:
            return text

        import base64

        _IMAGE_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp"}
        _TEXT_TYPES = {
            "text/plain", "text/html", "text/markdown", "text/csv",
            "application/json", "application/xml",
        }

        parts: List[Dict[str, Any]] = []

        # Add text first
        if text.strip():
            parts.append({"type": "text", "text": text})

        for att in attachments:
            mime = att.get("mime_type", "application/octet-stream")
            filename = att.get("filename", "file")
            data_b64 = att.get("data", "")

            if mime in _IMAGE_TYPES:
                # Image: send as image_url with data URI (OpenAI format, converted for Anthropic)
                parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{data_b64}"},
                })
            elif mime == "application/pdf":
                # PDF: try to extract text
                try:
                    raw = base64.b64decode(data_b64)
                    pdf_text = self._extract_pdf_text(raw)
                    parts.append({
                        "type": "document",
                        "filename": filename,
                        "text": pdf_text,
                    })
                except Exception as e:
                    parts.append({
                        "type": "text",
                        "text": f"[Attached PDF: {filename} — could not extract text: {e}]",
                    })
            elif mime in _TEXT_TYPES or filename.endswith((".txt", ".md", ".html", ".csv", ".json")):
                # Text file: decode and inject
                try:
                    raw = base64.b64decode(data_b64)
                    file_text = raw.decode("utf-8", errors="replace")
                    parts.append({
                        "type": "document",
                        "filename": filename,
                        "text": file_text,
                    })
                except Exception as e:
                    parts.append({
                        "type": "text",
                        "text": f"[Attached file: {filename} — could not decode: {e}]",
                    })
            else:
                # Unknown type — mention it
                parts.append({
                    "type": "text",
                    "text": f"[Attached file: {filename} ({mime}) — binary content not supported]",
                })

        return parts if len(parts) > 1 or any(p["type"] != "text" for p in parts) else (parts[0]["text"] if parts else text)

    @staticmethod
    def _extract_pdf_text(raw_bytes: bytes) -> str:
        """Extract text from PDF bytes using available libraries."""
        # Try PyPDF2 first (most common)
        try:
            import io
            from PyPDF2 import PdfReader
            reader = PdfReader(io.BytesIO(raw_bytes))
            pages = []
            for page in reader.pages:
                t = page.extract_text()
                if t:
                    pages.append(t)
            if pages:
                return "\n\n---\n\n".join(pages)
        except ImportError:
            pass
        except Exception:
            pass

        # Try pdfminer
        try:
            import io
            from pdfminer.high_level import extract_text as _pdfminer_extract
            return _pdfminer_extract(io.BytesIO(raw_bytes))
        except ImportError:
            pass

        # Fallback: raw text extraction (basic)
        text = raw_bytes.decode("latin-1", errors="replace")
        # Extract readable strings (crude but works for simple PDFs)
        import re
        strings = re.findall(r'[\x20-\x7E]{10,}', text)
        if strings:
            return "\n".join(strings[:200])

        raise RuntimeError("No PDF library available (install PyPDF2 or pdfminer.six)")

    @staticmethod
    def _classify_messages_for_display(
        raw_messages: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Classify stored messages for chat UI display.

        Returns list of dicts with:
          type: "user" | "assistant" | "tool_call" | "tool_result" | "system"
          role: original role
          content: text content
          tool_name: (for tool_call/tool_result) tool name
          tool_args: (for tool_call) stringified arguments
        System messages are excluded (internal to LLM context).
        """
        result = []
        for raw_idx, m in enumerate(raw_messages):
            role = m.get("role", "")
            if role == "system":
                continue  # skip system prompts
            raw_content = m.get("content", "")
            # Normalize content to string (may be a list for multipart messages)
            if isinstance(raw_content, list):
                text_parts = []
                for p in raw_content:
                    if isinstance(p, dict):
                        if p.get("type") == "text":
                            text_parts.append(p.get("text", ""))
                        elif p.get("type") == "image_url":
                            text_parts.append("[Image]")
                        elif p.get("type") == "document":
                            text_parts.append(f"[Document: {p.get('filename', 'file')}]")
                    elif isinstance(p, str):
                        text_parts.append(p)
                content = "\n".join(text_parts)
            elif isinstance(raw_content, str):
                content = raw_content
            else:
                content = str(raw_content) if raw_content else ""

            tool_calls = m.get("tool_calls")
            tool_call_id = m.get("tool_call_id")

            if role == "assistant" and tool_calls:
                # Assistant message that contains tool calls
                if content:
                    _tc_entry = {
                        "type": "assistant", "role": "assistant",
                        "content": content,
                    }
                    if m.get("source"):
                        _tc_entry["source"] = m["source"]
                    if m.get("timestamp"):
                        _tc_entry["timestamp"] = m["timestamp"]
                    result.append(_tc_entry)
                _tc_source = m.get("source")
                for tc in tool_calls:
                    # Build rich display matching SSE tool_call format
                    _tc_name = tc.get("name", "?")
                    _tc_args = tc.get("arguments", {})
                    _tc_args_str = json.dumps(_tc_args, ensure_ascii=False)[:500] if _tc_args else ""
                    # Format source label
                    _src_agent = (_tc_source or {}).get("name", "assistant") if _tc_source else "assistant"
                    _src_svc = (_tc_source or {}).get("llm_service", "") if _tc_source else ""
                    _src_label = _src_agent
                    if _src_svc:
                        _src_label += f" via {_src_svc}"
                    # Special formatting for spawn_agents
                    if _tc_name == "spawn_agents" and isinstance(_tc_args, dict):
                        tasks = _tc_args.get("tasks", [])
                        if tasks and isinstance(tasks, list):
                            lines = []
                            for t in tasks:
                                dst = t.get("agent", "?")
                                preview = (t.get("message", "") or "")[:80]
                                lines.append(f"➡ {_src_label} → {dst}" + (f": {preview}" if preview else ""))
                            _display = "\n".join(lines)
                        else:
                            _display = f"🔧 [{_src_label}] {_tc_name}"
                    else:
                        # Format args preview
                        _args_preview = ""
                        if isinstance(_tc_args, dict) and _tc_args:
                            _parts = []
                            for k, v in _tc_args.items():
                                vs = v[:60] if isinstance(v, str) else json.dumps(v, ensure_ascii=False)[:60]
                                _parts.append(f"{k}={vs}")
                            _args_preview = ", ".join(_parts)
                            if len(_args_preview) > 120:
                                _args_preview = _args_preview[:120] + "..."
                        _display = f"🔧 [{_src_label}] {_tc_name}"
                        if _args_preview:
                            _display += f"({_args_preview})"
                    result.append({
                        "type": "tool_call", "role": "assistant",
                        "content": _display,
                        "tool_name": _tc_name,
                        "tool_args": _tc_args_str,
                        "source": _tc_source,
                    })
            elif role == "tool" and tool_call_id:
                # Tool result message
                preview = content[:300]
                result.append({
                    "type": "tool_result", "role": "tool",
                    "content": preview + ("..." if len(content) > 300 else ""),
                    "tool_call_id": tool_call_id,
                })
            elif role in ("user", "assistant"):
                # Skip internal system instructions injected as user messages
                if role == "user" and content.startswith("[System:"):
                    continue
                entry = {"type": role, "role": role, "content": content, "raw_index": raw_idx}
                if m.get("timestamp"):
                    entry["timestamp"] = m["timestamp"]
                if m.get("channel"):
                    entry["channel"] = m["channel"]
                if m.get("source"):
                    entry["source"] = m["source"]
                elif role == "assistant":
                    # Infer source from identity prefix if present
                    import re as _re_src
                    _prefix_match = _re_src.match(r'^\[([^\]]+)\]:\s*', content)
                    if _prefix_match:
                        entry["source"] = {"type": "agent", "name": _prefix_match.group(1)}
                    else:
                        entry["source"] = {"type": "agent", "name": "assistant"}
                result.append(entry)
        return result

    @staticmethod
    def _apply_identity_suffix(messages: List[LLMMessage],
                               suffix: str) -> List[LLMMessage]:
        """Append identity suffix to system prompt for LLM call only.

        Returns a shallow copy with messages[0] replaced — the original
        list is NOT mutated, so the suffix is never persisted.
        """
        if not suffix or not messages or messages[0].role != "system":
            return messages
        result = list(messages)
        m0 = result[0]
        result[0] = LLMMessage(
            role="system",
            content=m0.content + suffix,
            source=m0.source,
        )
        return result

    @staticmethod
    def _inject_identity(messages: List[LLMMessage],
                         nicknames: Optional[Dict[str, str]] = None,
                         ) -> List[LLMMessage]:
        """Return a copy of messages with identity prefixes for the LLM.

        Assistant messages from named agents get ``[DisplayName]: `` prepended
        so the LLM can distinguish who said what in multi-agent conversations.
        User messages get ``[User]: `` prefix when there are multiple
        participants (more than just the user and one assistant).
        The original messages are NOT mutated — a shallow copy is returned.
        """
        nicks = nicknames or {}
        # Check if there are multiple distinct agents in the conversation
        agents_seen: set = set()
        for m in messages:
            if m.source and m.source.get("type") == "agent":
                agents_seen.add(m.source.get("name", "assistant"))
        multi_agent = len(agents_seen) > 1
        if not multi_agent and len(agents_seen) <= 1:
            return messages  # Single agent conversation — no prefixing needed

        result = []
        _skip_next_assistant = False
        for m in messages:
            if isinstance(m.content, str) and m.content.startswith(
                    "[Conversation summary"):
                # Summary messages — mark as such, don't prefix with agent name
                _skip_next_assistant = True  # The "Understood..." response
                result.append(m)
                continue
            if _skip_next_assistant and m.role == "assistant":
                _skip_next_assistant = False
                result.append(m)
                continue
            _skip_next_assistant = False
            if m.role == "assistant" and isinstance(m.content, str) and m.content:
                name = "assistant"
                if m.source:
                    name = m.source.get("name", "assistant")
                display = nicks.get(name, name)
                prefix = f"[{display}]: "
                if not m.content.startswith("[") and not m.content.startswith(prefix):
                    m = LLMMessage(
                        role=m.role,
                        content=prefix + m.content,
                        tool_calls=m.tool_calls,
                        tool_call_id=m.tool_call_id,
                        source=m.source,
                    )
            elif m.role == "user" and isinstance(m.content, str) and m.content:
                # Don't prefix system-injected user messages or summaries
                if not m.content.startswith("["):
                    name = ""
                    if m.source:
                        name = m.source.get("name", "")
                    display = name or "User"
                    prefix = f"[{display}]: "
                    if not m.content.startswith(prefix):
                        m = LLMMessage(
                            role=m.role,
                            content=prefix + m.content,
                            tool_calls=m.tool_calls,
                            tool_call_id=m.tool_call_id,
                            source=m.source,
                        )
            result.append(m)
        return result

    def _serialize_messages(self, messages: List[LLMMessage],
                           channel: str = "") -> List[Dict[str, Any]]:
        """Serialize messages for storage (ephemeral messages are excluded)."""
        result = []
        for m in messages:
            if m.source and m.source.get("type") == "ephemeral":
                continue
            entry: Dict[str, Any] = {"role": m.role, "content": m.content}
            if m.tool_calls:
                entry["tool_calls"] = [
                    {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                    for tc in m.tool_calls
                ]
            if m.tool_call_id:
                entry["tool_call_id"] = m.tool_call_id
            if channel and m.role in ("user", "assistant"):
                entry["channel"] = channel
            if m.source:
                entry["source"] = m.source
            result.append(entry)
        return result

    def _deserialize_messages(self, data: List[Dict[str, Any]]) -> List[LLMMessage]:
        """Deserialize messages from storage."""
        messages = []
        for entry in data:
            tool_calls = None
            if "tool_calls" in entry:
                tool_calls = [
                    LLMToolCall(
                        id=tc["id"],
                        name=tc["name"],
                        arguments=tc.get("arguments", {}),
                    )
                    for tc in entry["tool_calls"]
                ]
            messages.append(LLMMessage(
                role=entry["role"],
                content=entry.get("content", ""),
                tool_calls=tool_calls,
                tool_call_id=entry.get("tool_call_id"),
                source=entry.get("source"),
            ))
        return messages


TaskFactory.register(AgentLoopTask)
