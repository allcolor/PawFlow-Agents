"""SubAgentExecutor — Lightweight agent loop for sub-agent execution.

Used by the main AgentLoopTask to:
- Spawn sub-agents in parallel (delegate tool)

Each sub-agent runs its own tool-use loop with:
- Its own system prompt (from ResourceStore agent definition)
- A configurable subset of tools
- Depth tracking to prevent infinite recursion
- Timeout enforcement
- Result aggregation for parallel execution
"""

import json
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, Future, as_completed
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.llm_client import (
    LLMClient, LLMMessage, LLMResponse, LLMToolDefinition, LLMToolCall,
)
from core.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)

# Global depth tracker per thread to prevent infinite recursion
_depth_local = {}  # thread_id -> current_depth
_depth_lock = Lock()

MAX_GLOBAL_DEPTH = 5  # absolute ceiling regardless of agent config

# Global cancel registry for sub-agent tasks (delegate cancel)
_cancelled_tasks: set = set()
_cancelled_lock = Lock()


def cancel_sub_agent_task(task_id: str):
    """Mark a sub-agent task as cancelled. The agent loop checks this."""
    with _cancelled_lock:
        _cancelled_tasks.add(task_id)


def _is_cancelled(task_id: str) -> bool:
    with _cancelled_lock:
        return task_id in _cancelled_tasks


def _clear_cancelled(task_id: str):
    with _cancelled_lock:
        _cancelled_tasks.discard(task_id)


# Live delegate registry: one in-flight delegate per (parent_conv, caller, target).
# A second delegate call for the same triple should INJECT its message into the
# running sub-agent's loop instead of spawning a parallel one.
#   value: {"task_id", "client", "task"}
_live_delegates: Dict[tuple, dict] = {}
_live_delegates_lock = Lock()


def get_live_delegate(parent_conv: str, caller: str, target: str) -> Optional[dict]:
    with _live_delegates_lock:
        return _live_delegates.get((parent_conv, caller, target))


def register_live_delegate(parent_conv: str, caller: str, target: str,
                           task_id: str, client, task) -> None:
    with _live_delegates_lock:
        _live_delegates[(parent_conv, caller, target)] = {
            "task_id": task_id, "client": client, "task": task,
        }


def unregister_live_delegate(parent_conv: str, caller: str, target: str,
                             task_id: str = "") -> None:
    """Remove entry unless another task has already taken over the slot."""
    with _live_delegates_lock:
        entry = _live_delegates.get((parent_conv, caller, target))
        if entry and (not task_id or entry.get("task_id") == task_id):
            _live_delegates.pop((parent_conv, caller, target), None)


@dataclass
class AgentTask:
    """A single sub-agent task to execute."""
    id: str
    agent_name: str
    message: str
    # Resolved at execution time:
    system_prompt: str = ""
    model: str = ""
    tools: Optional[List[str]] = None  # tool name whitelist (None = all)
    max_iterations: int = 50
    max_depth: int = 1
    timeout: int = 300
    llm_service: str = ""  # service ID for LLM routing
    user_id: str = ""  # user ID for service resolution
    source_agent: str = ""  # name of the parent agent (for identity tracking)
    source_agent_nickname: str = ""  # display name of the parent agent
    source_llm_service: str = ""  # LLM service of the parent agent
    context_mode: str = "isolated"  # isolated, last:N, summary:N, full
    context_messages: Optional[List] = None  # pre-resolved context messages
    parent_conversation_id: str = ""  # for read_parent_context tool
    delegate_tc_id: str = ""  # tool_call ID of the delegate call in parent conversation
    persist: bool = False  # keep sub-conversation after completion (for multi-turn delegates)
    source_task_id: str = ""  # task ID when delegate is spawned from within a task


@dataclass
class AgentResult:
    """Result of a sub-agent execution."""
    task_id: str
    agent_name: str
    response: str = ""
    error: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    tools_called: List[str] = field(default_factory=list)
    iterations: int = 0
    duration_ms: float = 0.0
    status: str = "pending"  # pending, running, completed, error, timeout, cancelled, needs_input
    model: str = ""
    provider: str = ""
    question: str = ""  # question for parent agent (ask_parent)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "agent_name": self.agent_name,
            "response": self.response,
            "error": self.error,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "tools_called": self.tools_called,
            "iterations": self.iterations,
            "duration_ms": self.duration_ms,
            "status": self.status,
        }


def _get_depth() -> int:
    """Get current recursion depth for this thread."""
    import threading
    tid = threading.current_thread().ident
    with _depth_lock:
        return _depth_local.get(tid, 0)


def _set_depth(depth: int):
    """Set recursion depth for this thread."""
    import threading
    tid = threading.current_thread().ident
    with _depth_lock:
        if depth <= 0:
            _depth_local.pop(tid, None)
        else:
            _depth_local[tid] = depth


class SubAgentExecutor:
    """Execute sub-agent loops with tool use, parallel spawning, and depth control.

    This is NOT a singleton — each main agent loop creates its own instance
    with the appropriate LLM client and tool registry.
    """

    def __init__(
        self,
        client: LLMClient,
        registry: ToolRegistry,
        *,
        max_workers: int = 4,
        default_max_iterations: int = 50,
        default_timeout: int = 300,
        client_resolver: Optional[Callable] = None,
        on_event: Optional[Callable] = None,
    ):
        self._client = client
        self._registry = registry
        self._client_resolver = client_resolver  # (service_id, user_id) -> (LLMClient, svc)
        self._on_event = on_event  # (event_type, data_dict) callback
        self._max_workers = max_workers
        self._default_max_iterations = default_max_iterations
        self._default_timeout = default_timeout
        # Track pending async results
        self._pending: Dict[str, Future] = {}
        self._results: Dict[str, AgentResult] = {}
        self._lock = Lock()
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="sub-agent",
        )

    def shutdown(self):
        """Shutdown the thread pool."""
        self._pool.shutdown(wait=False)

    # ── Single agent execution ────────────────────────────────────────

    def execute_agent(self, task: AgentTask) -> AgentResult:
        """Execute a single sub-agent synchronously.

        Runs a tool-use loop until the agent produces a final response
        or hits max_iterations/timeout.
        """
        current_depth = _get_depth()
        effective_max = min(task.max_depth, MAX_GLOBAL_DEPTH)

        if current_depth >= effective_max:
            return AgentResult(
                task_id=task.id,
                agent_name=task.agent_name,
                error=f"Max agent depth ({effective_max}) reached — "
                      f"cannot spawn deeper agents",
                status="error",
            )

        # Increase depth for this thread
        _set_depth(current_depth + 1)
        try:
            return self._run_agent_loop(task)
        finally:
            _set_depth(current_depth)

    def _emit(self, event_type: str, data: Dict[str, Any]):
        """Emit an event via the on_event callback if set."""
        if self._on_event:
            try:
                self._on_event(event_type, data)
            except Exception:
                logger.debug("on_event callback failed for %s", event_type)

    def _run_agent_loop(self, task: AgentTask) -> AgentResult:
        """Core agent loop: LLM → tool_call → execute → LLM → ..."""
        result = AgentResult(
            task_id=task.id,
            agent_name=task.agent_name,
            status="running",
        )
        start = time.time()
        self._emit("sub_agent_start", {
            "agent_name": task.agent_name,
            "task_id": task.id,
            "message": task.message,
            "source_agent": task.source_agent,
            "source_llm_service": task.source_llm_service,
            "llm_service": task.llm_service,
            "delegate_tc_id": task.delegate_tc_id,
            "source_task_id": task.source_task_id,
        })

        # Create display-only trace message in parent conversation
        _trace_created = False
        if task.parent_conversation_id:
            try:
                from core.conversation_store import ConversationStore
                _depth = _get_depth()
                ConversationStore.instance().create_display_trace(
                    task.parent_conversation_id, task.id,
                    source={
                        "name": task.agent_name,
                        "parent_agent": task.source_agent or "",
                        "task_id": task.id,
                        "depth": _depth,
                        "delegate_tc_id": task.delegate_tc_id,
                        "source_task_id": task.source_task_id,
                        "message": task.message,
                        "llm_service": task.llm_service,
                    },
                    user_id=task.user_id,
                )
                _trace_created = True
            except Exception as _te:
                logger.debug("Failed to create display trace: %s", _te)

        # Tools have NO timeout — only cancellation breaks the sub-agent.
        max_iter = task.max_iterations or self._default_max_iterations

        # Resolve LLM client strictly from the agent's conv_agents link.
        # NO default client — an unresolvable llm_service is a hard error.
        client = None
        resolved_svc = None
        if not task.llm_service:
            _msg = (f"Sub-agent '{task.agent_name}' has no llm_service "
                    f"— conv_agents link is missing or misconfigured.")
            logger.error("[sub-agent:%s] %s", task.agent_name, _msg)
            return AgentResult(
                task_id=task.id,
                agent_name=task.agent_name,
                error=_msg,
                status="error",
            )
        if task.llm_service and self._client_resolver:
            try:
                resolved_client, resolved_svc = self._client_resolver(
                    task.llm_service, task.user_id,
                )
            except Exception as e:
                logger.exception("Failed to resolve LLM service '%s' for sub-agent '%s'",
                                 task.llm_service, task.agent_name)
                return AgentResult(
                    task_id=task.id,
                    agent_name=task.agent_name,
                    error=(f"Failed to resolve LLM service '{task.llm_service}': "
                           f"{type(e).__name__}: {e or 'no details'}"),
                    status="error",
                )
            if not resolved_client:
                _msg = (f"Sub-agent '{task.agent_name}' requested llm_service "
                        f"'{task.llm_service}' but it could not be resolved "
                        f"for user '{task.user_id}'. Check that the service "
                        f"exists and is enabled in the user or global scope.")
                logger.error("[sub-agent:%s] %s", task.agent_name, _msg)
                return AgentResult(
                    task_id=task.id,
                    agent_name=task.agent_name,
                    error=_msg,
                    status="error",
                )
            client = resolved_client
        elif task.llm_service and not self._client_resolver:
            _msg = (f"Sub-agent '{task.agent_name}' requested llm_service "
                    f"'{task.llm_service}' but no client_resolver is "
                    f"configured on the SubAgentExecutor.")
            logger.error("[sub-agent:%s] %s", task.agent_name, _msg)
            return AgentResult(
                task_id=task.id,
                agent_name=task.agent_name,
                error=_msg,
                status="error",
            )

        # Acquire capacity slot if service has limits
        if resolved_svc and hasattr(resolved_svc, 'try_acquire'):
            if not resolved_svc.try_acquire():
                return AgentResult(
                    task_id=task.id,
                    agent_name=task.agent_name,
                    error=f"LLM service '{task.llm_service}' at capacity",
                    status="error",
                )

        # Build tool definitions (filtered if agent specifies a whitelist)
        tool_defs, tool_handlers = self._build_tools(task.tools)

        # ask_parent has been removed. Delegate is bidirectional — a
        # sub-agent that needs to talk back to its caller just calls
        # delegate(caller_agent, "…") itself (it's a first-class agent
        # in the conv). preempt/wake handles the delivery.

        # Set parent conversation ID on read_parent_context tool
        for h in self._registry.list_tools():
            if hasattr(h, 'set_parent_conversation_id') and task.parent_conversation_id:
                h.set_parent_conversation_id(task.parent_conversation_id)
            if hasattr(h, 'set_user_id') and task.user_id:
                h.set_user_id(task.user_id)

        # Detect CLI-based providers that cannot execute tools directly
        _provider = getattr(client, "provider", "") or ""
        _is_cli_provider = _provider == "claude-code"
        if _is_cli_provider:
            # CLI providers (claude-code) cannot execute tools
            # natively — they try to run them interactively which causes
            # permission prompts and timeouts.  Instead, we pass tool
            # definitions as text in the system prompt and let the model
            # emit <tool_call> tags which LLMClient will parse.
            # tool_defs stays populated (LLMClient._build_tool_prompt
            # injects them into the prompt), but the model knows it must
            # use tags, not native tool execution.
            logger.info("[sub-agent:%s] CLI provider '%s' — tools will be "
                        "injected as text prompt, not native tool_use",
                        task.agent_name, _provider)

        # Build system prompt with spawn context
        sys_prompt = task.system_prompt
        if _is_cli_provider:
            sys_prompt += (
                "\n\n[TOOL CONSTRAINTS] You are running through a CLI pipe "
                "and CANNOT execute tools directly. You must respond with "
                "plain text only. If tools are listed above, you may request "
                "their execution by emitting <tool_call> XML tags in your "
                "response — the orchestrator will execute them and feed you "
                "the results. Do NOT attempt to use any built-in tools "
                "(Read, Write, Bash, etc.) — they are not available."
            )
        if task.source_agent:
            src_svc = f" via {task.source_llm_service}" if task.source_llm_service else ""
            if task.source_agent_nickname:
                src_id = (
                    f"agent '{task.source_agent}' (real name) "
                    f"also known as \"{task.source_agent_nickname}\" (nickname). "
                    f"When referring to them, use their nickname \"{task.source_agent_nickname}\""
                )
            else:
                src_id = f"agent '{task.source_agent}'"
            sys_prompt += (
                f"\n\n[CONTEXT] You were spawned by {src_id}{src_svc}. "
                f"They sent you a message and expect a response. "
                f"Answer their request directly."
            )

        # Add source metadata to messages
        user_source = (
            {"type": "agent", "name": task.source_agent}
            if task.source_agent
            else {"type": "user", "name": task.user_id or "unknown"}
        )

        # Sub-conversation persistence
        # "full" context mode = work directly in parent conv (no sub-context)
        sub_conv_id = ""
        _resumed = False
        if task.parent_conversation_id and task.context_mode != "full":
            sub_conv_id = f"{task.parent_conversation_id}::task::{task.id}"
        elif not task.parent_conversation_id:
            # Orphan sub-agent (tests / standalone execution): mint an
            # ephemeral cid so LLMMessage invariant holds throughout
            # the run. Nothing is persisted since there's no parent.
            from core.conversation_store import ConversationStore
            sub_conv_id = ConversationStore.instance().generate_id()

        # CC provider needs conv_id/agent_name/user_id on the client. Set
        # them before the LLM call so per-session workdir + credentials
        # resolve correctly for the sub-agent.
        # Save fields we will overwrite, so we can restore them in finally.
        _saved_client_state = {
            "_conversation_id": getattr(client, "_conversation_id", ""),
            "_agent_name": getattr(client, "_agent_name", ""),
            "_user_id": getattr(client, "_user_id", ""),
            "_event_cid": getattr(client, "_event_cid", ""),
            "_subagent_event_cb": getattr(client, "_subagent_event_cb", None),
        }
        # A delegate has its OWN context — exactly what the caller
        # passed via task.context_mode (built into task.context_messages
        # by resource_agent._resolve_context). The sub-agent must NOT
        # inherit the main agent's shared context, nor a previous
        # delegate's session of the same agent. Each delegate call is a
        # fresh CC session rooted in sub_conv_id (parent::task::<tid>),
        # so the initial prompt IS the context_mode-resolved messages.
        _delegate_conv_id = sub_conv_id or task.parent_conversation_id or ""
        try:
            client._conversation_id = _delegate_conv_id
            client._agent_name = task.agent_name or ""
            client._user_id = task.user_id or ""
            # Expose max_context_size so CC provider can publish context-fill
            # % via message_meta. Resolved from service config (200k default).
            _svc_max_ctx = 200000
            if resolved_svc and getattr(resolved_svc, 'config', None):
                try:
                    _svc_max_ctx = int(resolved_svc.config.get(
                        "max_context_size", _svc_max_ctx) or _svc_max_ctx)
                except (TypeError, ValueError):
                    pass
            client._max_context_size = _svc_max_ctx
            # Suppress raw provider SSE events to the parent bus — the
            # SubAgentExecutor already emits sub_agent_* events that the UI
            # uses to render delegate sub-blocks. Leaving _event_cid at the
            # default "" would make the provider fall back to conv_id
            # (= parent_conv) and leak assistant_text / tool_call events
            # into the parent chat as a task-block duplicate of the
            # delegate response — use None as an explicit suppress sentinel.
            client._event_cid = None
            # Intercept CC's native tool_call / tool_result / thinking
            # SSE events and re-emit them as sub_agent_* so they land
            # in the delegate sub-block instead of being dropped.
            def _subagent_cb(event_type, data):
                if not task.delegate_tc_id:
                    return
                _base = {
                    "agent_name": task.agent_name,
                    "task_id": task.id,
                    "delegate_tc_id": task.delegate_tc_id,
                    "source_task_id": task.source_task_id,
                }
                try:
                    if event_type == "tool_call":
                        self._emit("sub_agent_tool", {
                            **_base,
                            "tool": data.get("tool", ""),
                            "arguments": data.get("arguments", {}),
                            "tc_id": data.get("tc_id", ""),
                        })
                    elif event_type == "tool_result":
                        self._emit("sub_agent_tool_result", {
                            **_base,
                            "tool": data.get("tool", ""),
                            "tc_id": data.get("tc_id", ""),
                            "result": data.get("result", ""),
                        })
                    elif event_type == "thinking_content":
                        self._emit("sub_agent_thinking", {
                            **_base,
                            "thinking": (data.get("text") or "")[:2000],
                        })
                except Exception:
                    logger.debug("swallowed exception at core/agent_executor.py:~493", exc_info=True)
            client._subagent_event_cb = _subagent_cb
            logger.info("[sub-agent:%s] client wired conv=%s agent=%s user=%s",
                        task.agent_name,
                        client._conversation_id, client._agent_name, client._user_id)
        except Exception as _e:
            logger.warning("[sub-agent:%s] failed to wire client: %s",
                            task.agent_name, _e)
            # Try to resume existing sub-conversation
            try:
                from core.conversation_store import ConversationStore
                store = ConversationStore.instance()
                existing = store.load(sub_conv_id)
                if existing and len(existing) > 1:
                    messages = self._deserialize_sub_messages(existing, sub_conv_id)
                    # Append the new message as parent's response (resume)
                    messages.append(LLMMessage(
                        role="user", content=task.message,
                        source=user_source,
                        conversation_id=sub_conv_id,
                    ))
                    _resumed = True
                    logger.info("Resuming sub-conv %s with %d messages (+1 new)",
                                sub_conv_id, len(messages) - 1)
                else:
                    messages = self._build_initial_context(
                        task, sys_prompt, user_source, sub_conv_id)
            except Exception:
                messages = self._build_initial_context(
                    task, sys_prompt, user_source, sub_conv_id)
        else:
            messages = self._build_initial_context(
                task, sys_prompt, user_source, sub_conv_id)

        # Register in the live-delegate registry so a second delegate
        # call from the same caller to the same agent PREEMPTS this
        # running sub-agent (via client.send_user_message) instead of
        # spawning a parallel one.
        if task.parent_conversation_id and task.source_agent and task.agent_name:
            register_live_delegate(
                task.parent_conversation_id,
                task.source_agent, task.agent_name,
                task.id, client, task)

        # Register in AgentLoopTask._active_contexts so the chat UI
        # active-agents panel surfaces this sub-agent. The key uses the
        # sub-conv id (parent::task::tid:agent) so it matches the
        # list_active query (startswith parent_conv + ":").
        _active_ctx_key = ""
        _active_inst = None
        try:
            from tasks.ai.agent_loop import AgentLoopTask
            _active_inst = AgentLoopTask._live_instance
            if _active_inst:
                _ctx_cid = sub_conv_id or task.parent_conversation_id or ""
                _active_ctx_key = f"{_ctx_cid}:{task.agent_name}"
                _active_ctx = {
                    "active_agent_name": task.agent_name,
                    "_started_at": start,
                    "_iteration": 0,
                    "_round": 0,
                    "max_rounds": max_iter,
                    "_last_tool": "",
                }
                with _active_inst._active_contexts_lock:
                    _active_inst._active_contexts[_active_ctx_key] = _active_ctx
                    # Register CC client under the task-shaped key so a
                    # task-targeted force-stop (cancel_interrupt.py line
                    # 60: `f"::task::{task_id}" in k`) finds and kills
                    # the sub-agent's CC subprocess. Register only for
                    # CC clients that expose cancel_claude_code.
                    if hasattr(client, "cancel_claude_code"):
                        _active_inst._active_claude_client[_active_ctx_key] = client
        except Exception:
            _active_inst = None

        try:
            for iteration in range(1, max_iter + 1):
                if _active_inst and _active_ctx_key:
                    try:
                        _active_ctx["_iteration"] = iteration
                    except Exception:
                        logger.debug("swallowed exception at core/agent_executor.py:~574", exc_info=True)
                # Tools (delegate) have NO timeout — no deadline check.

                if _is_cancelled(task.id):
                    result.error = "Cancelled by user"
                    result.status = "cancelled"
                    break

                result.iterations = iteration
                self._emit("sub_agent_iteration", {
                    "agent_name": task.agent_name,
                    "task_id": task.id,
                    "iteration": iteration,
                    "max_iterations": max_iter,
                    "tools_called": result.tools_called[-3:],
                    "total_tools": len(result.tools_called),
                    "tokens_in": result.tokens_in,
                    "tokens_out": result.tokens_out,
                    "delegate_tc_id": task.delegate_tc_id,
                    "source_task_id": task.source_task_id,
                })
                if _trace_created:
                    try:
                        from core.conversation_store import ConversationStore
                        ConversationStore.instance().append_display_trace(
                            task.parent_conversation_id, task.id,
                            {"type": "iteration", "iteration": iteration,
                             "total_tools": len(result.tools_called)},
                        )
                    except Exception:
                        logger.debug("swallowed exception at core/agent_executor.py:~604", exc_info=True)

                # Stream text chunks back to the parent SSE bus so the
                # delegate sub-block fills progressively (mirrors the
                # main agent's experience).
                def _stream_cb(_chunk: str):
                    if not _chunk or not task.delegate_tc_id:
                        return
                    try:
                        self._emit("sub_agent_text", {
                            "agent_name": task.agent_name,
                            "task_id": task.id,
                            "text": _chunk,
                            "delegate_tc_id": task.delegate_tc_id,
                            "source_task_id": task.source_task_id,
                        })
                    except Exception:
                        logger.debug("swallowed exception at core/agent_executor.py:~621", exc_info=True)

                # CC-internal multi-turn: bump _iteration per turn so the
                # active-agents panel counter moves just like the main
                # agent's does (one increment per CC output message).
                _cc_turn_count = [0]

                def _turn_cb(_text, _tool_calls):
                    _cc_turn_count[0] += 1
                    if _active_inst and _active_ctx_key:
                        try:
                            _active_ctx["_iteration"] = _cc_turn_count[0]
                        except Exception:
                            logger.debug("swallowed exception at core/agent_executor.py:~634", exc_info=True)

                _compact_attempts = 0
                while True:
                    try:
                        response = client.complete_stream(
                            messages=messages,
                            model=task.model or None,
                            temperature=0.7,
                            max_tokens=0,
                            tools=tool_defs if tool_defs else None,
                            callback=_stream_cb,
                            turn_callback=_turn_cb,
                        )
                        break
                    except Exception as _llm_err:
                        if ("CC auto-compact detected" not in str(_llm_err)
                                or _compact_attempts >= 2):
                            raise
                        _compact_attempts += 1
                        # Same mechanism as the main agent: PawFlow-side
                        # summarize of the in-memory messages, clear the
                        # CC session so a fresh one starts, then retry.
                        # Transparent — never surfaces as an error.
                        logger.warning(
                            "[sub-agent:%s] CCCompactDetected — compacting PawFlow context",
                            task.agent_name)
                        try:
                            from tasks.ai.agent_loop import AgentLoopTask
                            _alt = AgentLoopTask._live_instance
                        except Exception:
                            _alt = None
                        if _alt and hasattr(_alt, "_auto_compact_messages"):
                            try:
                                _max_ctx = 200000
                                if resolved_svc and getattr(resolved_svc, 'config', None):
                                    _max_ctx = int(resolved_svc.config.get(
                                        "max_context_size", _max_ctx) or _max_ctx)
                                messages = list(_alt._auto_compact_messages(
                                    messages,
                                    conversation_id=task.parent_conversation_id or "",
                                    agent_name=task.agent_name,
                                    user_id=task.user_id,
                                    max_context=_max_ctx,
                                ))
                                logger.info(
                                    "[sub-agent:%s] PawFlow compact done (%d messages)",
                                    task.agent_name, len(messages))
                            except Exception as _ce:
                                logger.warning(
                                    "[sub-agent:%s] PawFlow compact failed: %s — falling back to session reset",
                                    task.agent_name, _ce)
                        try:
                            from core.conversation_store import ConversationStore
                            ConversationStore.instance().set_extra(
                                _delegate_conv_id,
                                f"claude_session:{task.agent_name}", "")
                        except Exception:
                            logger.debug("swallowed exception at core/agent_executor.py:~692", exc_info=True)
                        # Recover OAuth tokens (CC may have refreshed during
                        # the killed session) before the retry.
                        if hasattr(client, '_recover_tokens') and hasattr(client, '_get_session_workdir'):
                            try:
                                _wd = client._get_session_workdir(
                                    _delegate_conv_id,
                                    task.agent_name, task.user_id)
                                client._recover_tokens(_wd)
                            except Exception:
                                logger.debug("swallowed exception at core/agent_executor.py:~702", exc_info=True)

                result.tokens_in += response.tokens_in
                result.tokens_out += response.tokens_out
                if response.model:
                    result.model = response.model
                if not result.provider and client:
                    result.provider = getattr(client, 'provider', '') or ''

                # Emit thinking if present
                if response.thinking and task.delegate_tc_id:
                    self._emit("sub_agent_thinking", {
                        "agent_name": task.agent_name,
                        "task_id": task.id,
                        "thinking": response.thinking[:2000],
                        "delegate_tc_id": task.delegate_tc_id,
                        "source_task_id": task.source_task_id,
                    })

                # No tool calls → final response
                if not response.tool_calls:
                    result.response = response.content
                    result.status = "completed"
                    break

                # Emit intermediate text (assistant said something + tool_calls)
                if response.content and task.delegate_tc_id:
                    self._emit("sub_agent_text", {
                        "agent_name": task.agent_name,
                        "task_id": task.id,
                        "text": response.content[:1000],
                        "delegate_tc_id": task.delegate_tc_id,
                        "source_task_id": task.source_task_id,
                    })

                # Process tool calls
                agent_source = {"type": "agent", "name": task.agent_name}
                if task.llm_service:
                    agent_source["llm_service"] = task.llm_service
                messages.append(LLMMessage(
                    role="assistant",
                    content=response.content,
                    tool_calls=response.tool_calls,
                    source=agent_source,
                    conversation_id=sub_conv_id or task.parent_conversation_id,
                ))

                for tc in response.tool_calls:
                    # Tools have NO timeout — only user cancel breaks the loop.
                    if _is_cancelled(task.id):
                        result.error = "Cancelled by user"
                        result.status = "cancelled"
                        break

                    # Unwrap MCP wrapper so UI displays the inner tool
                    # (Read/Grep/...) instead of `use_tool`. Same as CC provider
                    # (claude_code.py:1170) and agent_core.py:320.
                    from core.llm_client import unwrap_mcp_tool
                    _disp_name, _disp_args = unwrap_mcp_tool(tc.name, tc.arguments or {})
                    result.tools_called.append(_disp_name)
                    # Truncate args for SSE (avoid huge payloads)
                    _tc_args_preview = {}
                    if _disp_args and isinstance(_disp_args, dict):
                        for k, v in _disp_args.items():
                            vs = str(v)
                            _tc_args_preview[k] = vs[:200] if len(vs) > 200 else vs
                    self._emit("sub_agent_tool", {
                        "agent_name": task.agent_name,
                        "task_id": task.id,
                        "tool": _disp_name,
                        "arguments": _tc_args_preview,
                        "tc_id": tc.id,
                        "iteration": result.iterations,
                        "delegate_tc_id": task.delegate_tc_id,
                        "source_task_id": task.source_task_id,
                    })
                    if _trace_created:
                        try:
                            from core.conversation_store import ConversationStore
                            ConversationStore.instance().append_display_trace(
                                task.parent_conversation_id, task.id,
                                {"type": "tool_call", "tool": _disp_name,
                                 "arguments": _tc_args_preview},
                            )
                        except Exception:
                            logger.debug("swallowed exception at core/agent_executor.py:~786", exc_info=True)
                    tool_result = self._execute_tool(
                        tc, tool_handlers, task.agent_name,
                        conversation_id=task.parent_conversation_id,
                        user_id=task.user_id)
                    # Emit tool result for delegate block display
                    _result_preview = (tool_result[:500] if isinstance(tool_result, str)
                                       else str(tool_result)[:500])
                    self._emit("sub_agent_tool_result", {
                        "agent_name": task.agent_name,
                        "task_id": task.id,
                        "tool": _disp_name,
                        "tc_id": tc.id,
                        "result": _result_preview,
                        "delegate_tc_id": task.delegate_tc_id,
                        "source_task_id": task.source_task_id,
                    })
                    messages.append(LLMMessage(
                        role="tool",
                        content=tool_result,
                        tool_call_id=tc.id,
                        conversation_id=sub_conv_id or task.parent_conversation_id,
                    ))

                # Persist sub-conversation after each iteration
                if sub_conv_id:
                    try:
                        from core.conversation_store import ConversationStore
                        _store = ConversationStore.instance()
                        def _serialize_msg(m):
                            d = {"role": m.role, "content": m.content}
                            if hasattr(m, 'tool_calls') and m.tool_calls:
                                d["tool_calls"] = [
                                    {"id": tc.id, "name": tc.name,
                                     "arguments": tc.arguments}
                                    for tc in m.tool_calls
                                ]
                            if hasattr(m, 'tool_call_id') and m.tool_call_id:
                                d["tool_call_id"] = m.tool_call_id
                            return d
                        _store.save(sub_conv_id,
                                    [_serialize_msg(m) for m in messages],
                                    user_id=task.user_id)
                    except Exception:
                        logger.debug("swallowed exception at core/agent_executor.py:~829", exc_info=True)

                if result.status in ("timeout", "cancelled", "needs_input"):
                    break
            else:
                # Max iterations reached — force synthesis
                result.response = self._force_synthesis(
                    messages, task.model,
                )
                result.status = "completed"

        except Exception as e:
            # Always log the traceback so we can diagnose silent failures
            # instead of qwen looping forever on an empty "status: error".
            logger.exception("Sub-agent '%s' error: %s", task.agent_name, e)
            _err = str(e) or f"{type(e).__name__} (no message)"
            result.error = _err
            result.status = "error"
        finally:
            # Release capacity slot
            if resolved_svc and hasattr(resolved_svc, 'release'):
                resolved_svc.release()
            # Clean up cancel registry
            _clear_cancelled(task.id)
            # Restore client state we mutated for the sub-agent invocation
            try:
                for _k, _v in _saved_client_state.items():
                    setattr(client, _k, _v)
            except Exception:
                logger.debug("swallowed exception at core/agent_executor.py:~858", exc_info=True)
            # Unregister from active-agents panel + claude-client registry
            if _active_inst and _active_ctx_key:
                try:
                    with _active_inst._active_contexts_lock:
                        _active_inst._active_contexts.pop(_active_ctx_key, None)
                        _active_inst._active_claude_client.pop(_active_ctx_key, None)
                except Exception:
                    logger.debug("swallowed exception at core/agent_executor.py:~866", exc_info=True)
            # Clear live-delegate slot so the next delegate call spawns fresh.
            if task.parent_conversation_id and task.source_agent and task.agent_name:
                try:
                    unregister_live_delegate(
                        task.parent_conversation_id,
                        task.source_agent, task.agent_name,
                        task.id)
                except Exception:
                    logger.debug("swallowed exception at core/agent_executor.py:~875", exc_info=True)

        result.duration_ms = (time.time() - start) * 1000
        _done_data = {
            "agent_name": task.agent_name,
            "task_id": task.id,
            "status": result.status,
            "response": result.response or "",
            "error": result.error,
            "tokens_in": result.tokens_in,
            "tokens_out": result.tokens_out,
            "duration_s": round(result.duration_ms / 1000, 1),
            "iterations": result.iterations,
            "tools_called": result.tools_called,
            "source_agent": task.source_agent,
            "source_llm_service": task.source_llm_service,
            "llm_service": task.llm_service,
            "model": result.model,
            "provider": result.provider,
            "delegate_tc_id": task.delegate_tc_id,
            "source_task_id": task.source_task_id,
        }
        if result.question:
            _done_data["question"] = result.question[:500]
        self._emit("sub_agent_done", _done_data)

        if _trace_created:
            try:
                from core.conversation_store import ConversationStore
                _trace_done = {
                    "type": "done", "status": result.status,
                    "tokens_in": result.tokens_in, "tokens_out": result.tokens_out,
                    "iterations": result.iterations,
                    "tools_called": result.tools_called,
                    "model": result.model, "error": result.error,
                }
                if result.question:
                    _trace_done["question"] = result.question[:500]
                ConversationStore.instance().append_display_trace(
                    task.parent_conversation_id, task.id,
                    _trace_done,
                    content_update=result.response or result.question or result.error or "",
                )
            except Exception as _te:
                logger.debug("Failed to append done trace: %s", _te)

        # Cleanup sub-conversation (unless persist=True)
        if sub_conv_id and not task.persist and result.status in ("completed", "error", "timeout", "cancelled"):
            try:
                from core.conversation_store import ConversationStore
                ConversationStore.instance().delete(
                    sub_conv_id, user_id=task.user_id or "")
            except Exception:
                logger.debug("swallowed exception at core/agent_executor.py:~928", exc_info=True)
        elif sub_conv_id and task.persist:
            logger.info("[sub-agent:%s] Persisting sub-conversation %s",
                        task.agent_name, sub_conv_id)

        return result

    def _build_initial_context(self, task, sys_prompt, user_source,
                                sub_conv_id: str = ""):
        """Build initial messages for a sub-agent based on context_mode."""
        _cid = sub_conv_id or task.parent_conversation_id
        if not _cid:
            # Orphan sub-agent (tests / standalone execution with no
            # parent conv) — mint an ephemeral cid so the LLMMessage
            # invariant holds. Messages aren't persisted anyway.
            from core.conversation_store import ConversationStore
            _cid = ConversationStore.instance().generate_id()
        messages = [LLMMessage(role="system", content=sys_prompt,
                                conversation_id=_cid)]
        # Inject context messages if provided
        if task.context_messages:
            for cm in task.context_messages:
                if isinstance(cm, LLMMessage):
                    messages.append(cm)
                elif isinstance(cm, dict):
                    messages.append(LLMMessage(
                        role=cm.get("role", "user"),
                        content=cm.get("content", ""),
                        conversation_id=_cid,
                    ))
        # Add the actual task message
        messages.append(LLMMessage(role="user", content=task.message,
                                   source=user_source,
                                   conversation_id=_cid))
        return messages

    @staticmethod
    def _deserialize_sub_messages(raw_messages, sub_conv_id: str = ""):
        """Convert stored dicts back to LLMMessage (preserving tool_calls)."""
        result = []
        for m in raw_messages:
            if isinstance(m, dict):
                msg = LLMMessage(
                    role=m.get("role", "user"),
                    content=m.get("content", ""),
                    conversation_id=(m.get("conversation_id") or sub_conv_id),
                    msg_id=m.get("msg_id", ""),
                    timestamp=m.get("ts") or m.get("timestamp") or 0.0,
                    seq=m.get("seq") or 0,
                )
                if m.get("tool_calls"):
                    msg.tool_calls = [
                        LLMToolCall(
                            id=tc.get("id", ""),
                            name=tc.get("name", ""),
                            arguments=tc.get("arguments", {}),
                        )
                        for tc in m["tool_calls"]
                    ]
                if m.get("tool_call_id"):
                    msg.tool_call_id = m["tool_call_id"]
                result.append(msg)
            elif isinstance(m, LLMMessage):
                result.append(m)
        return result

    def _build_tools(
        self, whitelist: Optional[List[str]],
    ) -> tuple:
        """Build tool definitions and handler map, optionally filtered."""
        handlers = {}
        defs = []
        for h in self._registry.list_tools():
            if whitelist is not None and h.name not in whitelist:
                continue
            handlers[h.name] = h
            defs.append(LLMToolDefinition(
                name=h.name,
                description=h.description,
                parameters=h.parameters_schema,
            ))
        return defs, handlers

    def _execute_tool(
        self, tc: LLMToolCall, handlers: Dict,
        agent_name: str = "",
        conversation_id: str = "",
        user_id: str = "",
    ) -> str:
        """Execute a single tool call with per-agent approval gate."""
        handler = handlers.get(tc.name)
        if handler is None:
            return f"Error: unknown tool '{tc.name}'"
        # Set agent identity for ownership tracking (ManageResourceHandler)
        if agent_name and hasattr(handler, 'set_agent_name'):
            handler.set_agent_name(agent_name)
        # Permission check (per-agent scoped)
        if conversation_id:
            try:
                from core.conversation_store import ConversationStore
                _perm_mode = ConversationStore.instance().get_extra(
                    conversation_id, "permission_mode") or "default"
                if _perm_mode != "auto":
                    from core.tool_approval import ToolApprovalGate
                    if tc.name not in ToolApprovalGate.EXEMPT_TOOLS:
                        approval = ToolApprovalGate.check(
                            tc.name,
                            f"{tc.name}({json.dumps(tc.arguments)[:200]})",
                            conversation_id, user_id,
                            arguments=tc.arguments,
                            agent_name=agent_name,
                        )
                        if approval != "approved":
                            return f"Error: Tool '{tc.name}' was {approval} by the user."
            except Exception as e:
                logger.debug("Sub-agent approval check failed: %s", e)
        try:
            return handler.execute(tc.arguments)
        except Exception as e:
            logger.warning("Tool '%s' error in sub-agent: %s", tc.name, e)
            return f"Error executing {tc.name}: {e}"

    def _force_synthesis(
        self, messages: List[LLMMessage], model: str,
    ) -> str:
        """Force a final response when max iterations reached."""
        # Inherit conv_id from the last message (all must share it).
        _cid = next(
            (m.conversation_id for m in reversed(messages)
             if getattr(m, "conversation_id", "")), "")
        messages.append(LLMMessage(
            role="user",
            content=(
                "[System: Maximum iterations reached. Provide your final "
                "response now. Synthesize all information gathered. "
                "Do NOT call any more tools.]"
            ),
            conversation_id=_cid,
        ))
        try:
            resp = self._client.complete(
                messages=messages,
                model=model or None,
                temperature=0.7,
                max_tokens=0,
                tools=None,
            )
            return resp.content
        except Exception as e:
            logger.error("Sub-agent synthesis failed: %s", e)
            return f"[Synthesis failed: {e}]"

    # ── Parallel spawning ─────────────────────────────────────────────

    def spawn(
        self, tasks: List[AgentTask], wait: bool = True,
        on_bg_complete: Optional[Callable] = None,
    ) -> List[AgentResult]:
        """Spawn multiple sub-agents, optionally waiting for all to complete.

        Args:
            tasks: List of AgentTask to execute.
            wait: If True, block until all complete and return results.
                  If False, return immediately with pending results.
            on_bg_complete: Callback(AgentResult, AgentTask) called when a
                  background task (wait=False) finishes. Used to inject
                  results into the parent conversation.

        Returns:
            List of AgentResult (completed if wait=True, pending if wait=False).
        """
        futures = {}
        task_map = {t.id: t for t in tasks}
        for task in tasks:
            future = self._pool.submit(self.execute_agent, task)
            futures[task.id] = future
            with self._lock:
                self._pending[task.id] = future

        if not wait:
            # Attach completion callbacks for background notification
            if on_bg_complete:
                for task in tasks:
                    fut = futures[task.id]
                    def _on_done(f, _task=task):
                        try:
                            result = f.result(timeout=0)
                        except Exception as e:
                            result = AgentResult(
                                task_id=_task.id, agent_name=_task.agent_name,
                                error=str(e), status="error",
                            )
                        try:
                            on_bg_complete(result, _task)
                        except Exception:
                            logger.debug("on_bg_complete callback failed for %s", _task.id)
                    fut.add_done_callback(_on_done)
            return [
                AgentResult(task_id=t.id, agent_name=t.agent_name, status="pending")
                for t in tasks
            ]

        # Wait for all to complete. Tools have NO timeout — delegate is
        # a tool, so we wait indefinitely for the sub-agent to finish.
        # Only real exceptions propagate here (cancellation, crash).
        results = []
        for task in tasks:
            future = futures[task.id]
            try:
                result = future.result()
            except Exception as e:
                logger.exception(
                    "[sub-agent:%s] future raised: task_id=%s",
                    task.agent_name, task.id)
                _err = str(e) or f"{type(e).__name__} (no message)"
                result = AgentResult(
                    task_id=task.id,
                    agent_name=task.agent_name,
                    error=_err,
                    status="error",
                )
            results.append(result)
            with self._lock:
                self._results[task.id] = result
                self._pending.pop(task.id, None)

        return results

    def get_results(self, task_ids: List[str]) -> List[AgentResult]:
        """Get results for previously spawned tasks.

        Returns completed results or pending status for each task ID.
        """
        results = []
        for tid in task_ids:
            with self._lock:
                # Already completed?
                if tid in self._results:
                    results.append(self._results[tid])
                    continue
                # Still pending?
                future = self._pending.get(tid)

            if future is None:
                results.append(AgentResult(
                    task_id=tid, agent_name="unknown",
                    error=f"No task found with id '{tid}'",
                    status="error",
                ))
            elif future.done():
                try:
                    result = future.result(timeout=0)
                except Exception as e:
                    result = AgentResult(
                        task_id=tid, agent_name="unknown",
                        error=str(e), status="error",
                    )
                with self._lock:
                    self._results[tid] = result
                    self._pending.pop(tid, None)
                results.append(result)
            else:
                results.append(AgentResult(
                    task_id=tid, agent_name="unknown",
                    status="running",
                ))

        return results



def resolve_agent_task(
    agent_name: str, message: str, user_id: str,
    conversation_id: str = "",
    extra_skills: list = None,
) -> AgentTask:
    """Resolve an agent instance name to an AgentTask.

    Looks up the instance in conv_agents, loads the definition from the
    repository, and resolves expressions in the prompt with instance params.
    Supports nickname resolution and case-insensitive matching.
    Raises KeyError if agent not found.
    """
    from core.expression import resolve_value
    from core.conv_agent_config import (
        get_all_agent_configs, get_agent_config, flatten_agent_params,
    )
    if not conversation_id:
        raise KeyError(
            f"Agent '{agent_name}' cannot be delegated to without a "
            f"conversation \u2014 an agent's llm_service lives on the "
            f"conv_agents link.")

    # 1) Resolve instance name: case-insensitive + nickname lookup
    _all_configs = get_all_agent_configs(conversation_id)
    _resolved_name = None
    if agent_name in _all_configs:
        _resolved_name = agent_name
    else:
        _needle = agent_name.lower()
        for _k in _all_configs:
            if isinstance(_k, str) and _k.lower() == _needle:
                _resolved_name = _k
                break
    # Nickname resolution
    if _resolved_name is None:
        try:
            from core.conversation_store import ConversationStore
            nicknames = ConversationStore.instance().get_extra(
                conversation_id, "agent_nicknames") or {}
            for real_name, nick in nicknames.items():
                if nick.lower() == agent_name.lower():
                    _resolved_name = real_name
                    break
            if _resolved_name is None:
                for real_name in nicknames:
                    if real_name.lower() == agent_name.lower():
                        _resolved_name = real_name
                        break
        except Exception:
            logger.debug("swallowed exception at core/agent_executor.py:~1228", exc_info=True)
    if _resolved_name is None:
        raise KeyError(
            f"Agent '{agent_name}' is not in conversation "
            f"'{conversation_id}'. Add it first before delegating.")
    agent_name = _resolved_name

    # 2) Get instance config and load definition from repo
    acfg = get_agent_config(conversation_id, agent_name)
    _def_name = acfg.get("definition") or ""
    if not _def_name:
        raise KeyError(
            f"Agent '{agent_name}' is in conversation "
            f"'{conversation_id}' but has no `definition` configured. "
            f"Every instance must explicitly reference a definition.")
    llm_svc = resolve_value(acfg.get("llm_service", ""), owner=user_id) or ""
    if not llm_svc:
        raise KeyError(
            f"Agent '{agent_name}' is in conversation "
            f"'{conversation_id}' but has no llm_service configured.")

    from core.resource_store import ResourceStore
    agent_def = ResourceStore.instance().get_any("agent", _def_name, user_id)
    if agent_def is None:
        raise KeyError(
            f"Definition '{_def_name}' for agent '{agent_name}' not found")

    # 3) Resolve prompt with instance params
    _raw_prompt = agent_def.get("prompt", "You are a helpful assistant.")
    _inst_params = acfg.get("params") or {}
    if _inst_params:
        from core.expression import resolve_expression
        _flat = flatten_agent_params(agent_name, _inst_params)
        _sys_prompt = resolve_expression(
            _raw_prompt, parameters=_flat,
            owner=user_id, conversation_id=conversation_id)
    else:
        _sys_prompt = _raw_prompt

    # 4) Inject skills
    _conv_skills = acfg.get("skills") or []
    _all_skills = list(_conv_skills) + list(extra_skills or [])
    if _all_skills:
        from core.skill_resolver import inject_skills_into_prompt
        _sys_prompt = inject_skills_into_prompt(_sys_prompt, _all_skills, user_id)

    # 5) Identity injection
    _nick = None
    try:
        from core.conversation_store import ConversationStore
        _nicks = ConversationStore.instance().get_extra(
            conversation_id, "agent_nicknames") or {}
        _nk = agent_name.lower()
        _nick = next((v for k, v in _nicks.items() if k.lower() == _nk), None)
    except Exception:
        logger.debug("swallowed exception at core/agent_executor.py:~1283", exc_info=True)
    if _nick:
        _sys_prompt = (
            f"[IDENTITY] Your real agent id is \"{agent_name}\". "
            f"The user has given you the nickname \"{_nick}\". "
            f"When other agents or tools refer to \"{agent_name}\" or "
            f"\"{_nick}\" (case-insensitive), they mean YOU.\n\n"
        ) + _sys_prompt
    else:
        _sys_prompt = (
            f"[IDENTITY] Your agent id is \"{agent_name}\".\n\n"
        ) + _sys_prompt

    return AgentTask(
        id=uuid.uuid4().hex[:12],
        agent_name=agent_name,
        message=message,
        system_prompt=_sys_prompt,
        model=acfg.get("model", ""),
        tools=acfg.get("tools") or None,
        max_iterations=50,
        max_depth=acfg.get("max_depth", 5),
        timeout=acfg.get("timeout", 180),
        llm_service=llm_svc,
        user_id=user_id,
    )
