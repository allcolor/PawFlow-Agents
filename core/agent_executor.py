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
    timeout: int = 120
    llm_service: str = ""  # service ID for LLM routing
    user_id: str = ""  # user ID for service resolution
    source_agent: str = ""  # name of the parent agent (for identity tracking)


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
        default_timeout: int = 120,
        client_resolver: Optional[Callable] = None,
    ):
        self._client = client
        self._registry = registry
        self._client_resolver = client_resolver  # (service_id, user_id) -> (LLMClient, svc)
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

    def _run_agent_loop(self, task: AgentTask) -> AgentResult:
        """Core agent loop: LLM → tool_call → execute → LLM → ..."""
        result = AgentResult(
            task_id=task.id,
            agent_name=task.agent_name,
            status="running",
        )
        start = time.time()
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

        # Build system prompt with source context
        sys_prompt = task.system_prompt
        if task.source_agent:
            sys_prompt += f"\n\n[You are talking to agent '{task.source_agent}']"

        # Add source metadata to messages
        user_source = (
            {"type": "agent", "name": task.source_agent}
            if task.source_agent
            else {"type": "user", "name": task.user_id or "unknown"}
        )
        messages = [
            LLMMessage(role="system", content=sys_prompt),
            LLMMessage(role="user", content=task.message, source=user_source),
        ]

        try:
            for iteration in range(1, max_iter + 1):
                if time.time() > deadline:
                    result.error = f"Timeout after {task.timeout}s"
                    result.status = "timeout"
                    break

                result.iterations = iteration

                response = client.complete(
                    messages=messages,
                    model=task.model or None,
                    temperature=0.7,
                    max_tokens=4096,
                    tools=tool_defs if tool_defs else None,
                )

                result.tokens_in += response.tokens_in
                result.tokens_out += response.tokens_out

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
                    tool_result = self._execute_tool(tc, tool_handlers)
                    messages.append(LLMMessage(
                        role="tool",
                        content=tool_result,
                        tool_call_id=tc.id,
                    ))

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
    ) -> str:
        """Execute a single tool call."""
        handler = handlers.get(tc.name)
        if handler is None:
            return f"Error: unknown tool '{tc.name}'"
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
                max_tokens=4096,
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
                max_tokens=4096,
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
) -> AgentTask:
    """Resolve an agent name to an AgentTask using the ResourceStore.

    Loads the agent definition and fills in all fields.
    Raises KeyError if agent not found.
    """
    from core.resource_store import ResourceStore
    store = ResourceStore.instance()
    agent_def = store.get_any("agent", agent_name, user_id)
    if agent_def is None:
        raise KeyError(f"Agent '{agent_name}' not found for user '{user_id}'")

    # Resolve expressions in llm_service (e.g. ${user.grok_llm_service})
    llm_svc = agent_def.get("llm_service", "")
    if llm_svc and "${" in llm_svc:
        from core.expression import resolve_expression
        llm_svc = resolve_expression(llm_svc, owner=user_id)
        if "${" in llm_svc:
            llm_svc = ""  # unresolved → skip

    return AgentTask(
        id=uuid.uuid4().hex[:12],
        agent_name=agent_name,
        message=message,
        system_prompt=agent_def.get("prompt", "You are a helpful assistant."),
        model=agent_def.get("model", ""),
        tools=agent_def.get("tools") or None,
        max_iterations=agent_def.get("max_iterations", 50),
        max_depth=agent_def.get("max_depth", 1),
        timeout=agent_def.get("timeout", 120),
        llm_service=llm_svc,
        user_id=user_id,
    )
