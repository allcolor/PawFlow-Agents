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


# Exceptions shared across agent loop mixins
from tasks.ai.agent_exceptions import AgentCancelled, _InterruptComplete  # noqa: F401



# Mixins — methods extracted into separate files
from tasks.ai.agent_actions import AgentActionsMixin
from tasks.ai.agent_streaming import AgentStreamingMixin
from tasks.ai.agent_compaction import AgentCompactionMixin
from tasks.ai.agent_context import AgentContextMixin
from tasks.ai.agent_poller import AgentPollerMixin
from tasks.ai.agent_identity import AgentIdentityMixin
from tasks.ai.agent_serialization import AgentSerializationMixin
from tasks.ai.agent_utils import AgentUtilsMixin
from tasks.ai.agent_core import AgentCoreMixin


class AgentLoopTask(
    AgentActionsMixin,
    AgentStreamingMixin,
    AgentCoreMixin,
    AgentCompactionMixin,
    AgentContextMixin,
    AgentPollerMixin,
    AgentIdentityMixin,
    AgentSerializationMixin,
    AgentUtilsMixin,
    BaseTask,
):
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
        # Set by ContinuousFlowExecutor before execute() — callable that
        # dequeues and returns all pending FlowFiles from input queue
        self._drain_pending = None  # type: Optional[Callable[[], List[FlowFile]]]
        self._user_active_conversations: set = set()  # convs with active USER interaction
        self._active_thoughts: set = set()  # active thought keys (conv_id::thought::agent)
        self._active_lock = threading.Lock()
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
        # Calibrated chars-per-token ratio per LLM service (learned from actual usage)
        self._calibrated_cpt: Dict[str, float] = {}  # service_id -> chars_per_token


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
                "type": "integer", "required": False, "default": 25,
                "description": "Max consecutive calls to the same tool before the agent must ask for confirmation (0 = unlimited)",
            },
            "thinking_budget": {
                "type": "integer", "required": False, "default": 0,
                "description": "Anthropic extended thinking budget in tokens (0 = disabled). When enabled, temperature is forced to 1.",
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
                "type": "string", "required": False, "default": "${conv.summarizer_service}",
                "description": "Dedicated LLM service for context compaction/summary. Cascades: conv → user → global params.",
            },
            "narrator_service": {
                "type": "string", "required": False, "default": "${conv.narrator_service}",
                "description": "Dedicated LLM service for tool-call narration. Cascades: conv → user → global params.",
            },
            "llm_service": {
                "type": "string", "required": False, "default": "${conv.llm_default_service}",
                "description": "LLM service ID (from global/user services). Cascades: conv → user → global params.",
            },
            "resilience_style": {
                "type": "string", "required": False, "default": "balanced",
                "description": "Agent resilience style: 'cautious' (stop on doubt, always ask), 'balanced' (default), 'aggressive' (retry hard, continue on errors)",
            },
        }


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
        "clear": 10,
        "theme": 30,
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
                        "Access denied. Your Telegram account is not linked to a PawFlow user.\n"
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

        If agent_name is specified, only cancel that specific agent's thread.
        Otherwise cancel ALL agents for this conversation.

        Increments the generation counter so the running thread detects
        staleness at the next check point and stops gracefully.

        If silent=True, no SSE event is published (used by context ops
        that cancel as a precaution, not as user-visible action).
        """
        # Empty agent_name = cancel all agents
        # whose gen_key is just conversation_id, not conversation_id:assistant
        _is_named = agent_name and agent_name != ""
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


    _interrupt_synthesis_active: set = set()  # tracks active interrupt synthesis threads

    def interrupt_agent(self, conversation_id: str, agent_name: str = ""):
        """Interrupt: cancel the current LLM call and spawn a parallel synthesis.

        1. Cancel the current generation (LLM call result will be discarded)
        2. Spawn a new thread that loads context + interrupt message → LLM → done
        Idempotent: ignores if a synthesis is already running for this conversation.
        """
        _synth_key = f"{conversation_id}:{agent_name or 'all'}"
        if _synth_key in self._interrupt_synthesis_active:
            logger.info(f"[agent:{conversation_id[:8]}] interrupt already in progress, skipping")
            return

        logger.info(f"[agent:{conversation_id[:8]}] interrupt → cancel + parallel synthesis "
                    f"for '{agent_name or 'agent'}'")

        # Cancel the current run (generation bump — result will be discarded)
        self.cancel_agent(conversation_id, agent_name=agent_name, silent=True)

        # Spawn parallel synthesis thread
        from core.conversation_event_bus import ConversationEventBus
        bus = ConversationEventBus.instance()
        bus.publish_event(conversation_id, "thinking", {
            "detail": "interrupted — synthesizing response...",
            "agent_name": agent_name or "",
        })

        self._interrupt_synthesis_active.add(_synth_key)

        def _synthesis():
            try:
                user_id = ""
                # Load current context
                from core.conversation_store import ConversationStore
                store = ConversationStore.instance()
                _agent = agent_name or "assistant"
                ctx_data = store.load_agent_context(conversation_id, _agent)
                if not ctx_data:
                    ctx_data = store.load(conversation_id) or []
                messages = self._deserialize_messages(ctx_data) if ctx_data else []
                if not messages:
                    bus.publish_event(conversation_id, "done", {
                        "response": "[Interrupted — no context available]",
                        "agent_name": agent_name or "",
                    })
                    return

                # Add interrupt instruction
                messages.append(LLMMessage(
                    role="user",
                    content=(
                        "[System: INTERRUPTED. Reply in 2-3 sentences max: "
                        "what you did, what's left to do. No details, no code.]"),
                ))

                # Resolve client
                client = self._get_default_client(user_id)
                if not client:
                    bus.publish_event(conversation_id, "done", {
                        "response": "[Interrupted — no LLM client available]",
                        "agent_name": agent_name or "",
                    })
                    return

                # Compact + call LLM (stream tokens to user)
                compact_msgs = self._compact_if_needed(
                    messages, client,
                    ctx.get("max_context_size", 64000) if 'ctx' in dir() else 64000,
                    0.6, 6, conversation_id=conversation_id)

                def _on_token(text):
                    bus.publish_event(conversation_id, "token", {
                        "text": text,
                        "agent_name": agent_name or "",
                        "source": {"type": "agent", "name": agent_name or ""},
                    })

                resp = client.complete_stream(
                    messages=compact_msgs,
                    temperature=0.7, max_tokens=500,
                    tools=None, callback=_on_token)

                # Save to both transcript (visible on reload) and agent context
                _interrupt_msg = LLMMessage(
                    role="assistant", content=resp.content,
                    source={"type": "agent", "name": agent_name or "",
                            "tokens_in": resp.tokens_in, "tokens_out": resp.tokens_out,
                            "model": resp.model})
                messages.append(_interrupt_msg)
                _serialized = self._serialize_messages(messages[-2:])
                store.append_messages(conversation_id, _serialized)
                store.append_to_agent_context(
                    conversation_id, _agent, _serialized)

                bus.publish_event(conversation_id, "done", {
                    "response": resp.content,
                    "model": resp.model,
                    "tokens_in": resp.tokens_in,
                    "tokens_out": resp.tokens_out,
                    "agent_name": agent_name or "",
                    "source": {"type": "agent", "name": agent_name or ""},
                    "interrupted": True,
                })
                store.set_status(conversation_id, "idle")
                self._track_tokens(
                    user_id or "anonymous", resp.tokens_in, resp.tokens_out,
                    model=resp.model, agent_name=agent_name or "")
            except Exception as e:
                logger.error(f"[interrupt synthesis] failed: {e}", exc_info=True)
                bus.publish_event(conversation_id, "done", {
                    "response": f"[Interrupted — synthesis failed: {e}]",
                    "agent_name": agent_name or "",
                })
                try:
                    ConversationStore.instance().set_status(conversation_id, "idle")
                except Exception:
                    pass
            finally:
                self._interrupt_synthesis_active.discard(_synth_key)

        t = threading.Thread(target=_synthesis, daemon=True,
                             name=f"interrupt-synth-{conversation_id[:8]}")
        t.start()


    def _check_interrupt(self, gen_key: str) -> bool:
        """Check and consume the interrupt flag for a gen_key."""
        with self._interrupt_lock:
            return self._conv_interrupt.pop(gen_key, False)



TaskFactory.register(AgentLoopTask)
