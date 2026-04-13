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
                        "message": task.message,
                        "llm_service": task.llm_service,
                    },
                    user_id=task.user_id,
                )
                _trace_created = True
            except Exception as _te:
                logger.debug("Failed to create display trace: %s", _te)

        deadline = start + (task.timeout or self._default_timeout)
        max_iter = task.max_iterations or self._default_max_iterations

        # Resolve LLM client: per-agent service or default
        client = self._client
        resolved_svc = None
        if task.llm_service and self._client_resolver:
            try:
                resolved_client, resolved_svc = self._client_resolver(
                    task.llm_service, task.user_id,
                )
                if resolved_client:
                    client = resolved_client
            except Exception as e:
                logger.warning("Failed to resolve LLM service '%s': %s",
                               task.llm_service, e)

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

        # Inject ask_parent tool for sub-agents (enables ping-pong dialog)
        if task.source_agent:
            tool_defs.append(LLMToolDefinition(
                name="ask_parent",
                description=(
                    "Ask the parent agent a question and wait for their response. "
                    "Use this when you need clarification, a decision, or additional "
                    "input from the agent that spawned you. Your execution will pause "
                    "until the parent responds. The parent may choose not to respond."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "The question or request for the parent agent",
                        },
                    },
                    "required": ["question"],
                },
            ))

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

        # CC provider needs conv_id/agent_name/user_id on the client. Set
        # them before the LLM call so per-session workdir + credentials
        # resolve correctly for the sub-agent.
        # Save fields we will overwrite, so we can restore them in finally.
        _saved_client_state = {
            "_conversation_id": getattr(client, "_conversation_id", ""),
            "_agent_name": getattr(client, "_agent_name", ""),
            "_user_id": getattr(client, "_user_id", ""),
            "_event_cid": getattr(client, "_event_cid", ""),
        }
        try:
            # Use parent_conversation_id (not sub_conv_id) so the CC
            # workdir + session_id lookup are stable across delegate
            # calls — the sub-agent's session survives between delegates
            # to the same agent in the same parent conversation. CC keys
            # its session under (parent_conv, agent_name).
            client._conversation_id = task.parent_conversation_id or sub_conv_id or ""
            client._agent_name = task.agent_name or ""
            client._user_id = task.user_id or ""
            # Suppress raw provider SSE events to the parent bus — the
            # SubAgentExecutor already emits sub_agent_* events that the UI
            # uses to render delegate sub-blocks. Leaving _event_cid pointed
            # at the parent conv would make the sub-agent's assistant_text
            # / tool_call events leak into the parent chat as a task-block
            # duplicate of the delegate response.
            client._event_cid = ""
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
                    messages = self._deserialize_sub_messages(existing)
                    # Append the new message as parent's response (resume)
                    messages.append(LLMMessage(
                        role="user", content=task.message,
                        source=user_source,
                    ))
                    _resumed = True
                    logger.info("Resuming sub-conv %s with %d messages (+1 new)",
                                sub_conv_id, len(messages) - 1)
                else:
                    messages = self._build_initial_context(
                        task, sys_prompt, user_source)
            except Exception:
                messages = self._build_initial_context(
                    task, sys_prompt, user_source)
        else:
            messages = self._build_initial_context(
                task, sys_prompt, user_source)

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
        except Exception:
            _active_inst = None

        try:
            for iteration in range(1, max_iter + 1):
                if _active_inst and _active_ctx_key:
                    try:
                        _active_ctx["_iteration"] = iteration
                    except Exception:
                        pass
                if time.time() > deadline:
                    result.error = f"Timeout after {task.timeout}s"
                    result.status = "timeout"
                    break

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
                        pass

                response = client.complete(
                    messages=messages,
                    model=task.model or None,
                    temperature=0.7,
                    max_tokens=0,
                    tools=tool_defs if tool_defs else None,
                )

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
                ))

                for tc in response.tool_calls:
                    if time.time() > deadline:
                        result.error = f"Timeout during tool execution"
                        result.status = "timeout"
                        break
                    if _is_cancelled(task.id):
                        result.error = "Cancelled by user"
                        result.status = "cancelled"
                        break

                    # Intercept ask_parent — break loop, return question to parent
                    if tc.name == "ask_parent":
                        _question = (tc.arguments or {}).get("question", "")
                        result.question = _question
                        result.status = "needs_input"
                        # Force persist so the sub-conv survives for resume
                        task.persist = True
                        # Add the ask_parent call + a synthetic tool result to messages
                        # so the LLM context is coherent on resume
                        messages.append(LLMMessage(
                            role="assistant", content="",
                            tool_calls=[tc], source=agent_source,
                        ))
                        messages.append(LLMMessage(
                            role="tool",
                            content="[Waiting for parent agent response...]",
                            tool_call_id=tc.id,
                        ))
                        self._emit("sub_agent_tool", {
                            "agent_name": task.agent_name,
                            "task_id": task.id,
                            "tool": "ask_parent",
                            "arguments": {"question": _question[:200]},
                            "tc_id": tc.id,
                            "iteration": result.iterations,
                            "delegate_tc_id": task.delegate_tc_id,
                        })
                        break

                    result.tools_called.append(tc.name)
                    # Truncate args for SSE (avoid huge payloads)
                    _tc_args_preview = {}
                    if tc.arguments:
                        for k, v in tc.arguments.items():
                            vs = str(v)
                            _tc_args_preview[k] = vs[:200] if len(vs) > 200 else vs
                    self._emit("sub_agent_tool", {
                        "agent_name": task.agent_name,
                        "task_id": task.id,
                        "tool": tc.name,
                        "arguments": _tc_args_preview,
                        "tc_id": tc.id,
                        "iteration": result.iterations,
                        "delegate_tc_id": task.delegate_tc_id,
                    })
                    if _trace_created:
                        try:
                            from core.conversation_store import ConversationStore
                            ConversationStore.instance().append_display_trace(
                                task.parent_conversation_id, task.id,
                                {"type": "tool_call", "tool": tc.name,
                                 "arguments": _tc_args_preview},
                            )
                        except Exception:
                            pass
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
                        "tool": tc.name,
                        "tc_id": tc.id,
                        "result": _result_preview,
                        "delegate_tc_id": task.delegate_tc_id,
                    })
                    messages.append(LLMMessage(
                        role="tool",
                        content=tool_result,
                        tool_call_id=tc.id,
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
                        pass

                if result.status in ("timeout", "cancelled", "needs_input"):
                    break
            else:
                # Max iterations reached — force synthesis
                result.response = self._force_synthesis(
                    messages, task.model, deadline,
                )
                result.status = "completed"

        except Exception as e:
            logger.error("Sub-agent '%s' error: %s", task.agent_name, e)
            result.error = str(e)
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
                pass
            # Unregister from active-agents panel
            if _active_inst and _active_ctx_key:
                try:
                    with _active_inst._active_contexts_lock:
                        _active_inst._active_contexts.pop(_active_ctx_key, None)
                except Exception:
                    pass

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
                ConversationStore.instance().delete(sub_conv_id)
            except Exception:
                pass
        elif sub_conv_id and task.persist:
            logger.info("[sub-agent:%s] Persisting sub-conversation %s",
                        task.agent_name, sub_conv_id)

        return result

    def _build_initial_context(self, task, sys_prompt, user_source):
        """Build initial messages for a sub-agent based on context_mode."""
        messages = [LLMMessage(role="system", content=sys_prompt)]
        # Inject context messages if provided
        if task.context_messages:
            for cm in task.context_messages:
                if isinstance(cm, LLMMessage):
                    messages.append(cm)
                elif isinstance(cm, dict):
                    messages.append(LLMMessage(
                        role=cm.get("role", "user"),
                        content=cm.get("content", ""),
                    ))
        # Add the actual task message
        messages.append(LLMMessage(role="user", content=task.message,
                                   source=user_source))
        return messages

    @staticmethod
    def _deserialize_sub_messages(raw_messages):
        """Convert stored dicts back to LLMMessage (preserving tool_calls)."""
        result = []
        for m in raw_messages:
            if isinstance(m, dict):
                msg = LLMMessage(
                    role=m.get("role", "user"),
                    content=m.get("content", ""),
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
        deadline: float,
    ) -> str:
        """Force a final response when max iterations reached."""
        if time.time() > deadline:
            return "[Agent timed out before completing]"

        messages.append(LLMMessage(
            role="user",
            content=(
                "[System: Maximum iterations reached. Provide your final "
                "response now. Synthesize all information gathered. "
                "Do NOT call any more tools.]"
            ),
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

        # Wait for all to complete
        results = []
        for task in tasks:
            future = futures[task.id]
            try:
                result = future.result(timeout=task.timeout + 10)
            except Exception as e:
                result = AgentResult(
                    task_id=task.id,
                    agent_name=task.agent_name,
                    error=str(e),
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
    """Resolve an agent name to an AgentTask using the ResourceStore.

    Loads the agent definition and fills in all fields.
    Supports nickname resolution and case-insensitive matching.
    Raises KeyError if agent not found.
    """
    from core.resource_store import ResourceStore
    store = ResourceStore.instance()

    # 1) Direct lookup
    agent_def = store.get_any("agent", agent_name, user_id)

    # 2) Case-insensitive lookup
    if agent_def is None:
        all_agents = store.list_all("agent", user_id)
        for a in all_agents:
            if a["name"].lower() == agent_name.lower():
                agent_name = a["name"]
                agent_def = store.get_any("agent", agent_name, user_id)
                break

    # 3) Nickname → real name resolution via conversation store
    if agent_def is None and conversation_id:
        try:
            from core.conversation_store import ConversationStore
            nicknames = ConversationStore.instance().get_extra(
                conversation_id, "agent_nicknames") or {}
            # nicknames = {real_name: display_name} — reverse lookup
            for real_name, nick in nicknames.items():
                if nick.lower() == agent_name.lower():
                    agent_name = real_name
                    agent_def = store.get_any("agent", agent_name, user_id)
                    break
            # Also try case-insensitive on real names in nicknames
            if agent_def is None:
                for real_name in nicknames:
                    if real_name.lower() == agent_name.lower():
                        agent_name = real_name
                        agent_def = store.get_any("agent", agent_name, user_id)
                        break
        except Exception:
            pass

    if agent_def is None:
        raise KeyError(f"Agent '{agent_name}' not found for user '{user_id}'")

    # Resolve runtime config from conv_agents (or defaults)
    from core.expression import resolve_value
    from core.conv_agent_config import get_agent_config, AGENT_CONFIG_DEFAULTS
    acfg = get_agent_config(conversation_id, agent_name) if conversation_id else dict(AGENT_CONFIG_DEFAULTS)
    llm_svc = resolve_value(acfg.get("llm_service", ""), owner=user_id) or ""

    # Inject skills: conv_agent_config skills (inherited) + extra_skills from delegate call
    _sys_prompt = agent_def.get("prompt", "You are a helpful assistant.")
    _conv_skills = acfg.get("skills") or []
    _all_skills = list(_conv_skills) + list(extra_skills or [])
    if _all_skills:
        from core.skill_resolver import inject_skills_into_prompt
        _sys_prompt = inject_skills_into_prompt(_sys_prompt, _all_skills, user_id)
    _nick = None
    if conversation_id:
        try:
            from core.conversation_store import ConversationStore
            _nicks = ConversationStore.instance().get_extra(
                conversation_id, "agent_nicknames") or {}
            _nk = agent_name.lower()
            _nick = next((v for k, v in _nicks.items() if k.lower() == _nk), None)
        except Exception:
            pass
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
