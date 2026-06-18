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
import uuid
from concurrent.futures import ThreadPoolExecutor, Future
from threading import Lock
from typing import Any, Callable, Dict, List, Optional

from core.llm_client import (
    LLMClient, LLMMessage, LLMToolDefinition, LLMToolCall,
)
from core.tool_registry import ToolRegistry
from core._agent_executor_base import (  # noqa: F401 -- re-exported (invariant 1)
    AgentResult,
    AgentTask,
    MAX_GLOBAL_DEPTH,
    _cancelled_lock,
    _cancelled_tasks,
    _clear_cancelled,
    _get_depth,
    _is_cancelled,
    _set_depth,
    cancel_sub_agent_task,
    drain_live_delegate_messages,
    get_live_delegate,
    queue_live_delegate_message,
    register_live_delegate,
    unregister_live_delegate,
)
from core._agent_executor_loop import _SubAgentExecutorLoopMixin

logger = logging.getLogger(__name__)

class SubAgentExecutor(_SubAgentExecutorLoopMixin):
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
                    thinking=m.get("thinking", ""),
                    thinking_signature=m.get("thinking_signature", ""),
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
            return self._registry.execute(tc.name, tc.arguments)
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
        return self._registry.execute(tc.name, tc.arguments)

    def _force_synthesis(
        self, messages: List[LLMMessage], model: str,
        call_kwargs: Optional[Dict[str, Any]] = None,
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
                **(call_kwargs or {}),
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
    # Sub-conversations (::task::/::task_verify::/::delegate::) carry no
    # conv_agents roster of their own — fall back to the parent
    # conversation, like every scoped chain does.
    from core.service_registry import _parent_conversation_id
    _cfg_cid = conversation_id
    _resolved_name = None
    for _cand_cid in dict.fromkeys(
            (conversation_id, _parent_conversation_id(conversation_id))):
        if not _cand_cid:
            continue
        _all_configs = get_all_agent_configs(_cand_cid)
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
                    _cand_cid, "agent_nicknames") or {}
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
                logger.debug("exception suppressed", exc_info=True)
        if _resolved_name is not None:
            _cfg_cid = _cand_cid
            break
    if _resolved_name is None:
        raise KeyError(
            f"Agent '{agent_name}' is not in conversation "
            f"'{conversation_id}'. Add it first before delegating.")
    agent_name = _resolved_name

    # 2) Get instance config and load definition from repo
    acfg = get_agent_config(_cfg_cid, agent_name)
    _def_name = acfg.get("definition") or ""
    if not _def_name:
        raise KeyError(
            f"Agent '{agent_name}' is in conversation "
            f"'{conversation_id}' but has no `definition` configured. "
            f"Every instance must explicitly reference a definition.")
    llm_svc = resolve_value(acfg.get("llm_service", ""), owner=user_id,
                            conversation_id=conversation_id) or ""
    if not llm_svc:
        raise KeyError(
            f"Agent '{agent_name}' is in conversation "
            f"'{conversation_id}' but has no llm_service configured.")

    from core.resource_store import ResourceStore
    agent_def = ResourceStore.instance().get_any(
        "agent", _def_name, user_id, conversation_id=conversation_id)
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

    # 4) Advertise skills. Full skill prompts are loaded lazily through
    # load_skill; assigned_skills remains the persistent source of truth.
    _assigned_skills = agent_def.get("assigned_skills") or []
    _all_skills = list(_assigned_skills) + list(extra_skills or [])
    if _all_skills:
        from core.skill_resolver import inject_available_skills_into_prompt
        _sys_prompt = inject_available_skills_into_prompt(
            _sys_prompt, _all_skills, user_id,
            conversation_id=conversation_id)

    # 5) Identity injection
    _nick = None
    try:
        from core.conversation_store import ConversationStore
        _nicks = ConversationStore.instance().get_extra(
            conversation_id, "agent_nicknames") or {}
        _nk = agent_name.lower()
        _nick = next((v for k, v in _nicks.items() if k.lower() == _nk), None)
    except Exception:
        logger.debug("exception suppressed", exc_info=True)
    from core.agent_prompt_policy import inject_common_agent_system_prompt
    _sys_prompt = inject_common_agent_system_prompt(_sys_prompt)
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
