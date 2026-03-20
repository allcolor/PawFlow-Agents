"""SubAgentExecutor — Lightweight agent loop for sub-agent and skill execution.

Used by the main AgentLoopTask to:
- Spawn sub-agents in parallel (spawn_agents tool)
- Execute skills as single-shot LLM calls (use_skill tool)

Each sub-agent runs its own tool-use loop with:
- Its own system prompt (from ResourceStore agent definition)
- A configurable subset of tools
- Depth tracking to prevent infinite recursion
- Timeout enforcement
- Result aggregation for parallel execution
"""

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
    status: str = "pending"  # pending, running, completed, error, timeout
    model: str = ""
    provider: str = ""

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
        })
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

        # Set parent conversation ID on read_parent_context tool
        for h in self._registry.list_tools():
            if hasattr(h, 'set_parent_conversation_id') and task.parent_conversation_id:
                h.set_parent_conversation_id(task.parent_conversation_id)
            if hasattr(h, 'set_user_id') and task.user_id:
                h.set_user_id(task.user_id)

        # Detect CLI-based providers that cannot execute tools directly
        _provider = getattr(client, "provider", "") or ""
        _is_cli_provider = _provider in ("claude-code", "gemini-cli")
        if _is_cli_provider:
            # CLI providers (claude-code, gemini-cli) cannot execute tools
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
        if task.parent_conversation_id and task.context_mode != "full":
            sub_conv_id = f"{task.parent_conversation_id}::task::{task.id}"
            # Try to resume existing sub-conversation
            try:
                from core.conversation_store import ConversationStore
                store = ConversationStore.instance()
                existing = store.load(sub_conv_id)
                if existing and len(existing) > 1:
                    messages = self._deserialize_sub_messages(existing)
                    logger.info("Resuming sub-conv %s with %d messages",
                                sub_conv_id, len(messages))
                else:
                    messages = self._build_initial_context(
                        task, sys_prompt, user_source)
            except Exception:
                messages = self._build_initial_context(
                    task, sys_prompt, user_source)
        else:
            messages = self._build_initial_context(
                task, sys_prompt, user_source)

        try:
            for iteration in range(1, max_iter + 1):
                if time.time() > deadline:
                    result.error = f"Timeout after {task.timeout}s"
                    result.status = "timeout"
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
                })

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

                # No tool calls → final response
                if not response.tool_calls:
                    result.response = response.content
                    result.status = "completed"
                    break

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
                    result.tools_called.append(tc.name)
                    self._emit("sub_agent_tool", {
                        "agent_name": task.agent_name,
                        "task_id": task.id,
                        "tool": tc.name,
                        "iteration": result.iterations,
                    })
                    tool_result = self._execute_tool(tc, tool_handlers, task.agent_name)
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

                if result.status == "timeout":
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

        result.duration_ms = (time.time() - start) * 1000
        self._emit("sub_agent_done", {
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
        })

        # Persist final result in parent conversation before cleanup
        if sub_conv_id and result.response and task.parent_conversation_id:
            try:
                from core.conversation_store import ConversationStore
                _parent_store = ConversationStore.instance()
                _parent_msgs = _parent_store.load(task.parent_conversation_id) or []
                _result_msg = {
                    "role": "assistant",
                    "content": result.response,
                    "source": {
                        "type": "agent",
                        "name": task.agent_name,
                        "llm_service": task.llm_service,
                    },
                }
                _parent_msgs.append(_result_msg)
                _parent_store.save(task.parent_conversation_id, _parent_msgs,
                                    user_id=task.user_id)
            except Exception as _pe:
                logger.debug("Failed to persist task result to parent: %s", _pe)

        # Cleanup sub-conversation (unless agent scheduled continuation)
        if sub_conv_id and result.status in ("completed", "error", "timeout"):
            try:
                from core.conversation_store import ConversationStore
                ConversationStore.instance().delete(sub_conv_id)
            except Exception:
                pass

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
    ) -> str:
        """Execute a single tool call."""
        handler = handlers.get(tc.name)
        if handler is None:
            return f"Error: unknown tool '{tc.name}'"
        # Set agent identity for ownership tracking (ManageResourceHandler)
        if agent_name and hasattr(handler, 'set_agent_name'):
            handler.set_agent_name(agent_name)
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
    ) -> List[AgentResult]:
        """Spawn multiple sub-agents, optionally waiting for all to complete.

        Args:
            tasks: List of AgentTask to execute.
            wait: If True, block until all complete and return results.
                  If False, return immediately with pending results
                  (use get_results() to check later).

        Returns:
            List of AgentResult (completed if wait=True, pending if wait=False).
        """
        futures = {}
        for task in tasks:
            future = self._pool.submit(self.execute_agent, task)
            futures[task.id] = future
            with self._lock:
                self._pending[task.id] = future

        if not wait:
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

    # ── Skill execution (single-shot) ─────────────────────────────────

    def execute_skill(
        self, skill_prompt: str, input_text: str,
        model: str = "", timeout: int = 60,
    ) -> AgentResult:
        """Execute a skill as a single-shot LLM call (no tools, no loop).

        Args:
            skill_prompt: The skill's system prompt.
            input_text: User input to process.
            model: Model override.
            timeout: Timeout in seconds.

        Returns:
            AgentResult with the skill output.
        """
        task_id = uuid.uuid4().hex[:12]
        start = time.time()

        try:
            messages = [
                LLMMessage(role="system", content=skill_prompt),
                LLMMessage(role="user", content=input_text),
            ]
            response = self._client.complete(
                messages=messages,
                model=model or None,
                temperature=0.7,
                max_tokens=0,
                tools=None,
            )
            return AgentResult(
                task_id=task_id,
                agent_name="skill",
                response=response.content,
                tokens_in=response.tokens_in,
                tokens_out=response.tokens_out,
                duration_ms=(time.time() - start) * 1000,
                status="completed",
            )
        except Exception as e:
            return AgentResult(
                task_id=task_id,
                agent_name="skill",
                error=str(e),
                duration_ms=(time.time() - start) * 1000,
                status="error",
            )


def resolve_agent_task(
    agent_name: str, message: str, user_id: str,
    conversation_id: str = "",
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

    # "assistant" is the default persona, not a ResourceStore agent
    if agent_def is None and agent_name.lower() == "assistant":
        agent_def = {
            "name": "assistant",
            "prompt": "You are a helpful assistant.",
            "llm_service": "",
        }

    if agent_def is None:
        raise KeyError(f"Agent '{agent_name}' not found for user '{user_id}'")

    # Resolve expressions in llm_service (e.g. ${user.grok_llm_service})
    llm_svc = agent_def.get("llm_service", "")
    if llm_svc and "${" in llm_svc:
        from core.expression import resolve_expression
        llm_svc = resolve_expression(llm_svc, owner=user_id)
        if "${" in llm_svc:
            llm_svc = ""  # unresolved → skip

    # Build system prompt with identity block
    _sys_prompt = agent_def.get("prompt", "You are a helpful assistant.")
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
        model=agent_def.get("model", ""),
        tools=agent_def.get("tools") or None,
        max_iterations=agent_def.get("max_iterations", 50),
        max_depth=agent_def.get("max_depth", 1),
        timeout=agent_def.get("timeout", 300),
        llm_service=llm_svc,
        user_id=user_id,
    )
