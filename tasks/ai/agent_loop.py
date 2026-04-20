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
from core.tool_registry import ToolRegistry, create_default_registry

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
    _live_instance = None  # set by __init__ for wake_poller access

    # ── Shared execution state (class-level) ──────────────────────
    # Shared by ALL instances (AgentLoopTask + AgentActionsTask).
    # Action handlers use self._active_contexts etc. and must see the
    # same state regardless of which instance they run on.
    _active_conversations: Dict[str, int] = {}          # conv_id -> refcount
    _user_active_conversations: set = set()
    _active_thoughts: set = set()
    _active_lock = threading.Lock()
    _conv_generation: Dict[str, int] = {}
    _conv_gen_lock = threading.Lock()
    _conv_interrupt: Dict[str, bool] = {}
    _interrupt_lock = threading.Lock()
    # Active agent contexts — push on enter, pop on exit (finally).
    # Key: "conversation_id:agent_name", Value: ctx dict
    # This is the SOLE source of truth for "which agents are active".
    _active_contexts: Dict[str, dict] = {}
    _active_contexts_lock = threading.Lock()
    _active_claude_client: Dict[str, Any] = {}          # conv_id:agent -> CC client
    _context_op_events: Dict[str, threading.Event] = {}
    _context_op_lock = threading.Lock()
    _calibrated_cpt: Dict[str, float] = {}               # service_id -> chars_per_token
    _calibrated_cpt_lock = threading.Lock()
    _interrupt_cooldowns: Dict[str, float] = {}


    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        # Per-instance state (not shared with AgentActionsTask)
        self._tool_registry: Optional[ToolRegistry] = None
        self._poller_started = False
        self._poller_stop = threading.Event()
        self._poller_wake = threading.Event()
        AgentLoopTask._live_instance = self
        self._drain_pending = None  # type: Optional[Callable[[], List[FlowFile]]]
        self._requeue_flowfiles = None  # type: Optional[Callable[[List[FlowFile]], None]]


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


    @classmethod
    def wake_poller(cls):
        """Wake the poller immediately (call after schedule_delay with delay=0)."""
        inst = cls._live_instance
        if inst and hasattr(inst, '_poller_wake'):
            inst._poller_wake.set()

    @classmethod
    def is_agent_active(cls, conversation_id: str, agent_name: str) -> bool:
        """True if an agent turn is currently running for (conv, agent)."""
        inst = cls._live_instance
        if not inst:
            return False
        key = f"{conversation_id}:{agent_name}" if agent_name else conversation_id
        with inst._active_contexts_lock:
            return key in inst._active_contexts

    @classmethod
    def wake_agent(cls, conversation_id: str, agent_name: str,
                   reason: str = "", user_id: str = "", delay: float = 1.0):
        """Trigger an agent turn — no-op if it's already running.

        When the agent is active, the PendingQueue is drained at the end
        of the current turn; no external wake needed. When idle, schedule
        a turn so the queued messages are picked up.
        """
        if cls.is_agent_active(conversation_id, agent_name):
            return  # current turn will drain pending at its end
        try:
            from core.poll_scheduler import PollScheduler
            key = f"{conversation_id}::pending::{(agent_name or '').lower()}"
            PollScheduler.instance().schedule_delay(
                conversation_id, delay, key=key,
                reason=reason or f"[pending] wake {agent_name or 'default'}",
                user_id=user_id or "",
            )
            cls.wake_poller()
        except Exception as e:
            logger.warning("[wake-agent] failed for %s/%s: %s",
                            conversation_id[:8], agent_name, e)

    @classmethod
    def force_stop_agent(cls, conversation_id: str, agent_name: str):
        """Force stop an agent — kill CC + bump gen + cancel relay.

        The relay cancel is cleared by uncancel_agent at the start of
        every new agent run (agent_core.py line 122). So a force stop
        never affects the NEXT run — only the current one.
        """
        inst = cls._live_instance
        if inst:
            inst.cancel_agent(conversation_id, agent_name=agent_name, silent=True)
            _key = f"{conversation_id}:{agent_name}" if agent_name else conversation_id
            with inst._active_contexts_lock:
                _cc = inst._active_claude_client.get(_key)
            if _cc and hasattr(_cc, 'cancel_claude_code'):
                _cc.cancel_claude_code(force=True)
                # Reset catch-up index so next run re-scans from last own message
                _cc._cc_catchup_idx = 0
        try:
            from services.tool_relay_service import ToolRelayService
            ToolRelayService.cancel_agent(conversation_id, agent_name)
        except Exception:
            logger.debug("swallowed exception at tasks/ai/agent_loop.py:~208", exc_info=True)

    def get_tool_registry(self) -> ToolRegistry:
        """Get or create the tool registry for this agent.

        Priority:
        1. Custom registry set via set_tool_registry()
        2. Default builtin registry
        """
        if self._tool_registry is None:
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
                "type": "integer", "required": False, "default": 1000,
                "description": "Maximum tool-use iterations (0 = default 1000). Overrides LLM service value.",
            },
            "max_consecutive_tool_calls": {
                "type": "integer", "required": False, "default": 100,
                "description": "Max consecutive calls to same tool (0 = default 100). Overrides LLM service value.",
            },
            "resilience_style": {
                "type": "select", "required": False, "default": "",
                "options": ["", "cautious", "balanced", "aggressive"],
                "description": "Tool call resilience (empty = LLM service default or balanced)",
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
            "context_keep_recent": {
                "type": "integer", "required": False, "default": 6,
                "description": "Number of recent messages to keep intact during compaction (never summarized)",
            },
            "summarizer_service": {
                "type": "string", "required": False, "default": "${summarizer_service}",
                "description": "Dedicated LLM service for context compaction/summary.",
            },
            "narrator_service": {
                "type": "string", "required": False, "default": "${narrator_service}",
                "description": "Dedicated LLM service for tool-call narration.",
            },
            "llm_service": {
                "type": "string", "required": False, "default": "",
                "description": "LLM service ID — leave empty when agents have their own (conv_agents config).",
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
            logger.debug("swallowed exception at tasks/ai/agent_loop.py:~379", exc_info=True)
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
            logger.debug("swallowed exception at tasks/ai/agent_loop.py:~414", exc_info=True)
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
                        from core.expression import resolve_value
                        service_id = resolve_value(
                            adef.get("llm_service", ""), owner=user_id) or ""
            except Exception:
                logger.debug("swallowed exception at tasks/ai/agent_loop.py:~479", exc_info=True)

        if not service_id:
            return None

        _, svc = self._resolve_llm_service(service_id, user_id)
        return svc


    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        _rid = flowfile.get_attribute("http.request.id") or ""
        _act_log = "?"
        try:
            _body_preview = flowfile.get_content()[:200].decode("utf-8", errors="replace")
            if _body_preview.lstrip().startswith("{"):
                import json as _j_log
                _b = _j_log.loads(flowfile.get_content().decode("utf-8", errors="replace"))
                _act_log = _b.get("action", "msg") if isinstance(_b, dict) else "?"
        except Exception:
            logger.debug("swallowed exception at tasks/ai/agent_loop.py:~498", exc_info=True)
        import time as _t_al
        _t_al_start = _t_al.monotonic()
        try:
            _result = self._execute_inner(flowfile)
            _dur = (_t_al.monotonic() - _t_al_start) * 1000
            if _dur > 500:
                logger.info("[agent_loop] SLOW req_id=%s action=%s took=%.0fms",
                            _rid[:8] if _rid else "?", _act_log, _dur)
            return _result
        except Exception as _e:
            _dur = (_t_al.monotonic() - _t_al_start) * 1000
            logger.error("[agent_loop] CRASH req_id=%s action=%s took=%.0fms: %s",
                          _rid[:8] if _rid else "?", _act_log, _dur, _e,
                          exc_info=True)
            raise

    def _execute_inner(self, flowfile: FlowFile) -> List[FlowFile]:
        """Original execute body — wrapped by execute() with entry/exit logs."""
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
            logger.debug("[agent_loop] _handle_action returned %s",
                         "result" if action_result is not None else "None (not an action)")
            if action_result is not None:
                return action_result

        streaming = self.config.get("streaming", False)
        logger.debug("[agent_loop] dispatching to %s", "streaming" if streaming else "sync")
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
                     silent: bool = False, reason: str = "user_request"):
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
            # Bump ONLY the keys that match a CURRENTLY RUNNING loop.
            # Do NOT bump keys that don't exist yet — a future loop
            # will capture its own gen at startup and won't be affected.
            _keys_to_bump = set()
            _prefix = f"{conversation_id}:"
            for k in list(self._conv_generation):
                if k == conversation_id or k.startswith(_prefix):
                    if _is_named and agent_name.lower() not in k.lower() and k != conversation_id:
                        continue  # different agent — don't touch
                    _keys_to_bump.add(k)
            # Always include the standard keys (they may be used by the current loop)
            _keys_to_bump.add(conversation_id)
            if _is_named:
                _keys_to_bump.add(f"{conversation_id}:{agent_name}")
            for k in _keys_to_bump:
                self._conv_generation[k] = self._conv_generation.get(k, 0) + 1
        if not silent:
            # Publish cancellation event for SSE listeners
            from core.conversation_event_bus import ConversationEventBus
            ConversationEventBus.instance().publish_event(
                conversation_id, "cancelled", {
                    "reason": reason,
                    "agent_name": agent_name if _is_named else "all",
                }
            )
        # Cancel tool relay for this (conv, agent) — pending tool calls return error
        try:
            from services.tool_relay_service import ToolRelayService
            _cancel_agent = agent_name if _is_named else ""
            ToolRelayService.cancel_agent(conversation_id, _cancel_agent)
        except Exception:
            logger.debug("swallowed exception at tasks/ai/agent_loop.py:~623", exc_info=True)
        # Kill any running Claude Code subprocess for this conversation
        _force = False  # force stop handled separately by FORCE_STOP action
        # Kill Claude Code subprocess (check both conv:agent and conv-only keys)
        with self._active_contexts_lock:
            _cc_keys = [f"{conversation_id}:{agent_name}"] if _is_named else \
                [k for k in self._active_claude_client if (k == conversation_id or k.startswith(conversation_id + ":")) and "::task::" not in k and "::task_verify::" not in k]
            _cc_clients = [(k, self._active_claude_client.get(k)) for k in _cc_keys]
        for _cc_key, client in _cc_clients:
            if client and hasattr(client, 'cancel_claude_code'):
                client.cancel_claude_code(force=_force)
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
        # _active_contexts cleanup happens in _run_agent_loop finally
        import traceback as _tb
        _caller = ""
        try:
            _stack = _tb.extract_stack(limit=6)[:-1]
            _caller = " <- " + " <- ".join(
                f"{__import__('os').path.basename(f.filename)}:{f.lineno}"
                for f in reversed(_stack[-4:]))
        except Exception:
            logger.debug("swallowed exception at tasks/ai/agent_loop.py:~671", exc_info=True)
        logger.info(f"[agent:{conversation_id[:8]}] cancelled ({reason})"
                    f"{f' (agent: {agent_name})' if _is_named else ' (all)'}"
                    f"{_caller}")


    def interrupt_agent(self, conversation_id: str, agent_name: str = ""):
        """Interrupt: cancel the current LLM call and spawn a parallel synthesis.

        Cooldown: ignores repeated interrupts within 10 seconds.
        No-op if no agent is actively running.
        """
        # Check if anything is actually running for this conversation
        with self._active_contexts_lock:
            _any_active = any(
                k == conversation_id or k.startswith(conversation_id + ":")
                for k in self._active_contexts)
        if not _any_active:
            logger.info(f"[agent:{conversation_id[:8]}] interrupt ignored — no active agent")
            return

        import time as _t
        _synth_key = f"{conversation_id}:{agent_name or 'all'}"
        _now = _t.time()
        with self._interrupt_lock:
            _last = self._interrupt_cooldowns.get(_synth_key, 0)
            _is_repeat = _now - _last < 10
            if _is_repeat:
                self._interrupt_cooldowns.pop(_synth_key, None)
            else:
                self._interrupt_cooldowns[_synth_key] = _now
        if _is_repeat:
            # Second interrupt within cooldown = escalate to force stop
            logger.info(f"[agent:{conversation_id[:8]}] repeat interrupt → escalating to force stop")
            self.cancel_agent(conversation_id, agent_name=agent_name, silent=False)
            # Force kill Claude Code subprocess if applicable
            _esc_key = f"{conversation_id}:{agent_name}" if agent_name else conversation_id
            with self._active_contexts_lock:
                _cc_client = self._active_claude_client.get(_esc_key)
            if _cc_client and hasattr(_cc_client, 'cancel_claude_code'):
                _cc_client.cancel_claude_code(force=True)
            # Force cleanup
            from core.conversation_store import ConversationStore as _CS_int
            from core.conversation_event_bus import ConversationEventBus as _CEB_int
            _CEB_int.instance().publish_event(
                conversation_id, "done", {
                    "response": "[Force stopped by user]",
                    "agent_name": agent_name or "",
                    "force_stopped": True,
                })
            with self._active_lock:
                self._active_conversations.pop(conversation_id, None)
                self._user_active_conversations.discard(conversation_id)
            with self._active_contexts_lock:
                # Remove all agents for this conversation
                for k in list(self._active_contexts):
                    if k == conversation_id or k.startswith(conversation_id + ":"):
                        del self._active_contexts[k]
            return

        logger.info(f"[agent:{conversation_id[:8]}] interrupt for '{agent_name or 'agent'}'")

        # For claude-code: graceful interrupt via stdin + cancel in-flight tools
        _is_cc = False
        _int_key = f"{conversation_id}:{agent_name}" if agent_name else conversation_id
        with self._active_contexts_lock:
            _cc_client = self._active_claude_client.get(_int_key)
        if _cc_client and hasattr(_cc_client, 'cancel_claude_code'):
            _proc = getattr(_cc_client, '_claude_proc', None)
            if _proc and _proc.poll() is None:
                _is_cc = True
                # Cancel in-flight MCP tool calls so Claude Code gets
                # tool errors back quickly and can wrap up
                try:
                    from services.tool_relay_service import ToolRelayService
                    ToolRelayService.cancel_agent(conversation_id, agent_name)
                except Exception:
                    logger.debug("swallowed exception at tasks/ai/agent_loop.py:~748", exc_info=True)
                # Send graceful interrupt on stdin
                _cc_client.cancel_claude_code(force=False)
                # Bump generation so the agent loop won't retry after CC concludes
                self.cancel_agent(conversation_id, agent_name=agent_name, silent=True)
                logger.info(f"[agent:{conversation_id[:8]}] claude-code interrupt: "
                            f"tools cancelled + stdin interrupt + generation bumped")
                return
            else:
                # Stale client — process dead, clean up and finish
                with self._active_contexts_lock:
                    self._active_claude_client.pop(_int_key, None)
                logger.warning(f"[agent:{conversation_id[:8]}] CC process already dead — cleaning up")
                self.cancel_agent(conversation_id, agent_name=agent_name, silent=False)
                # No synthesis for dead CC — just emit done
                from core.conversation_event_bus import ConversationEventBus
                ConversationEventBus.instance().publish_event(
                    conversation_id, "done", {
                        "response": "[Interrupted — agent process ended]",
                        "agent_name": agent_name or "",
                        "interrupted": True,
                    })
                from core.conversation_store import ConversationStore
                return

        # Double-check: if the agent's provider is claude-code, skip synthesis
        # (client may already be cleaned up but the agent is still CC)
        try:
            _meta = None
            from core.conversation_store import ConversationStore as _CS_chk
            _meta = _CS_chk.instance().get_metadata(conversation_id)
            _uid = _meta.get("user_id", "") if _meta else ""
            _, _svc_id, _ = self._resolve_agent_client(
                agent_name or "", _uid, conversation_id)
            if _svc_id:
                _c, _s = self._resolve_llm_service(_svc_id, _uid)
                if _c and getattr(_c, 'provider', '') == 'claude-code':
                    logger.info(f"[agent:{conversation_id[:8]}] agent is CC but client gone — skip synthesis")
                    self.cancel_agent(conversation_id, agent_name=agent_name, silent=False)
                    from core.conversation_event_bus import ConversationEventBus
                    ConversationEventBus.instance().publish_event(
                        conversation_id, "done", {
                            "response": "[Interrupted — agent process ended]",
                            "agent_name": agent_name or "",
                            "interrupted": True,
                        })
                    return
        except Exception:
            logger.debug("swallowed exception at tasks/ai/agent_loop.py:~796", exc_info=True)

        # Non-claude-code: cancel the current run
        self.cancel_agent(conversation_id, agent_name=agent_name, silent=False)

        # Note: _active_contexts cleanup happens in _run_agent_loop finally
        # block handles that. The agent is still running (doing synthesis).

        # Spawn parallel synthesis thread
        from core.conversation_event_bus import ConversationEventBus
        bus = ConversationEventBus.instance()
        bus.publish_event(conversation_id, "thinking", {
            "detail": "interrupted — synthesizing response...",
            "agent_name": agent_name or "",
        })

        def _synthesis():
            _synth_gen_key = f"{conversation_id}:__synth__"
            # Mark synthesis as active in _active_contexts
            _synth_ctx = {"active_agent_name": agent_name or "",
                          "conversation_id": conversation_id}
            _synth_ctx_key = f"{conversation_id}:{agent_name}" if agent_name else conversation_id
            with self._active_contexts_lock:
                self._active_contexts[_synth_ctx_key] = _synth_ctx
            try:
                from core.conversation_store import ConversationStore
                store = ConversationStore.instance()
                # Resolve user_id from conversation metadata
                _meta = store.get_metadata(conversation_id)
                user_id = _meta.get("user_id", "") if _meta else ""
                _agent = agent_name or ""
                ctx_data = store.load_agent_context(conversation_id, _agent)
                if not ctx_data:
                    ctx_data = store.load(conversation_id) or []
                messages = self._deserialize_messages(ctx_data) if ctx_data else []
                import uuid as _uuid_synth
                _synth_msg_id = _uuid_synth.uuid4().hex[:12]
                if not messages:
                    bus.publish_event(conversation_id, "done", {
                        "response": "[Interrupted — no context available]",
                        "msg_id": _synth_msg_id,
                        "all_msg_ids": [_synth_msg_id],
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

                # Resolve LLM for synthesis — NEVER use claude-code
                # (would spawn a new subprocess + MCP). Use summarizer.
                _summ_client, _summ_max_ctx, _summ_svc_id = self._get_summarizer_client(user_id)
                client = _summ_client
                _agent_svc = _summ_svc_id
                if not client:
                    # No summarizer — try agent's own client if not claude-code
                    client, _agent_svc, _ = self._resolve_agent_client(
                        _agent, user_id, conversation_id)
                    if client and getattr(client, 'provider', '') == 'claude-code':
                        client = None  # Can't use claude-code for synthesis
                logger.info(f"[interrupt synthesis] LLM service: '{_agent_svc}', "
                            f"client: {getattr(client, 'provider', '?')}/{getattr(client, 'default_model', '?')}")
                if not client:
                    bus.publish_event(conversation_id, "done", {
                        "response": "[Interrupted — no LLM client available]",
                        "msg_id": _synth_msg_id,
                        "all_msg_ids": [_synth_msg_id],
                        "agent_name": agent_name or "",
                    })
                    return

                # Repair orphan tool_calls and remove orphan tool results
                _valid_tc_ids = set()
                for m in messages:
                    if m.role == "assistant" and m.tool_calls:
                        for tc in m.tool_calls:
                            _valid_tc_ids.add(tc.id)
                # Remove tool results with no matching tool_call
                messages = [m for m in messages
                            if m.role != "tool" or m.tool_call_id in _valid_tc_ids]
                # Add missing tool results for tool_calls
                for i, m in enumerate(messages):
                    if m.role == "assistant" and m.tool_calls:
                        tc_ids = {tc.id for tc in m.tool_calls}
                        found = set()
                        for j in range(i + 1, min(i + len(tc_ids) + 2, len(messages))):
                            if messages[j].role == "tool" and messages[j].tool_call_id in tc_ids:
                                found.add(messages[j].tool_call_id)
                        for tc_id in tc_ids - found:
                            messages.insert(i + 1, LLMMessage(
                                role="tool", content="[Unavailable]", tool_call_id=tc_id))

                # Compact + call LLM (stream tokens to user)
                _max_ctx = _summ_max_ctx or 64000
                compact_msgs = self._compact(
                    messages, client, _max_ctx,
                    target_fraction=0.25, conversation_id=conversation_id)


                def _on_token(text):
                    bus.publish_event(conversation_id, "token", {
                        "text": text,
                        "msg_id": _synth_msg_id,
                        "agent_name": agent_name or "",
                        "source": {"type": "agent", "name": agent_name or ""},
                    })

                resp = client.complete_stream(
                    messages=compact_msgs,
                    max_tokens=500,
                    tools=None, callback=_on_token)

                # Save to both transcript and agent context.
                # `done` SSE fires AFTER the last block hits disk.
                _interrupt_msg = LLMMessage(
                    role="assistant", content=resp.content,
                    msg_id=_synth_msg_id,
                    source={"type": "agent", "name": agent_name or "",
                            "tokens_in": resp.tokens_in, "tokens_out": resp.tokens_out,
                            "model": resp.model})
                messages.append(_interrupt_msg)
                _serialized = self._serialize_messages(messages[-2:])
                from core.conversation_writer import ConversationWriter
                _writer = ConversationWriter.for_conversation(conversation_id)
                _done_sse = {
                    "type": "done",
                    "data": {
                        "response": resp.content,
                        "msg_id": _synth_msg_id,
                        "all_msg_ids": [_synth_msg_id],
                        "model": resp.model,
                        "tokens_in": resp.tokens_in,
                        "tokens_out": resp.tokens_out,
                        "agent_name": agent_name or "",
                        "source": {"type": "agent", "name": agent_name or ""},
                        "interrupted": True,
                    },
                }
                for _idx, _sm in enumerate(_serialized):
                    _sse = [_done_sse] if _idx == len(_serialized) - 1 else None
                    _writer.enqueue_message(_sm, agent_name=_agent,
                                            user_id=user_id, sse_events=_sse)
                self._track_tokens(
                    user_id, resp.tokens_in, resp.tokens_out,
                    model=resp.model, agent_name=agent_name or "")
            except Exception as e:
                logger.error(f"[interrupt synthesis] failed: {e}", exc_info=True)
                bus.publish_event(conversation_id, "done", {
                    "response": f"[Interrupted — synthesis failed: {e}]",
                    "msg_id": _synth_msg_id,
                    "all_msg_ids": [_synth_msg_id],
                    "agent_name": agent_name or "",
                })
            finally:
                with self._active_contexts_lock:
                    self._active_contexts.pop(_synth_ctx_key, None)

        t = threading.Thread(target=_synthesis, daemon=True,
                             name=f"interrupt-synth-{conversation_id[:8]}:{agent_name or 'agent'}")
        t.start()


    def _check_interrupt(self, gen_key: str) -> bool:
        """Check and consume the interrupt flag for a gen_key."""
        with self._interrupt_lock:
            return self._conv_interrupt.pop(gen_key, False)



TaskFactory.register(AgentLoopTask)
