"""SpawnAgentsHandler — extracted from resource_agent.py (<=800 lines).

The shared-delegate delivery/dedup cluster lives in _spawn_delivery.py and is
mixed in here. Re-exported from core.handlers.resource_agent.
"""

import json
import logging
import threading
from typing import Any, Dict, List

from core.tool_handler import ToolHandler
from core.handlers._spawn_delivery import _SpawnDeliveryMixin

logger = logging.getLogger(__name__)


class SpawnAgentsHandler(_SpawnDeliveryMixin, ToolHandler):
    """Spawn one or more sub-agents to work in parallel.

    The main agent can delegate complex sub-tasks to specialized agents
    defined in the resource store. Results are aggregated and returned.
    """

    def __init__(self):
        self._user_id = ""
        self._conversation_id = ""
        self._available_agents: List[str] = []
        self._local = threading.local()  # thread-safe source agent
        self._client_resolver = None  # callable(svc_id, uid) -> (client, svc)
        self._on_event = None  # callable(event_type, data)
        self._default_client = None  # fallback LLM client

    def set_conversation_id(self, conversation_id: str) -> None:
        self._conversation_id = conversation_id

    def set_spawn_deps(self, client, client_resolver, on_event, registry=None):
        """Set dependencies for spawning sub-agents."""
        self._default_client = client
        self._client_resolver = client_resolver
        self._on_event = on_event
        self._registry = registry

    def set_source_agent(self, agent_name: str, llm_service: str = "") -> None:
        self._local.source_agent = agent_name
        self._local.source_llm_service = llm_service

    def set_delegate_tc_id(self, tc_id: str) -> None:
        """Set the tool_call ID of the delegate call (thread-local)."""
        self._local.delegate_tc_id = tc_id

    def set_available_agents(self, agents: List):
        """Set the list of available agents (names or dicts with details)."""
        self._available_agents = list(agents)

    @property
    def name(self) -> str:
        return "delegate"

    @property
    def description(self) -> str:
        base = (
            "Send a private message to another agent in this conversation. "
            "Always ASYNCHRONOUS — returns IMMEDIATELY, YOU ARE NOT BLOCKED.\n\n"
            "Default context='shared': the target agent uses its own "
            "conversation context to read your message and reply. You will "
            "receive their answer as a private '[Delegate result …]' "
            "message that YOU MUST READ and REACT TO (integrate, reply to "
            "the user, or delegate again).\n\n"
            "context='isolated' / 'last:N': spawns a separate sub-agent "
            "with an empty (or sliced) context — use this ONLY when you "
            "genuinely need a fresh workspace (a self-contained research "
            "task). Agents that are themselves running as a delegate can "
            "ONLY use context='shared' (nested private sub-contexts are "
            "forbidden).\n\n"
            "Delegate is bidirectional: an agent called via delegate can "
            "call delegate(caller, …) to reply or ask a follow-up.\n\n"
            "Delegates are de-duplicated per (caller, target) pair: if "
            "you call delegate again for a target that's still working on "
            "your previous request, the new message is injected into "
            "their running loop instead of spawning a second one."
        )
        if self._available_agents:
            lines = []
            for a in self._available_agents:
                if isinstance(a, dict):
                    name = a.get("name", "")
                    desc = a.get("description", "") or ""
                    svc = a.get("llm_service", "") or ""
                    tools = a.get("tools") or []
                    parts = [f"- {name}"]
                    if desc:
                        parts[0] += f": {desc}"
                    extras = []
                    if svc:
                        extras.append(f"via {svc}")
                    if tools:
                        extras.append(f"tools: {', '.join(tools[:8])}")
                    if extras:
                        parts[0] += f" ({', '.join(extras)})"
                    lines.append(parts[0])
                else:
                    lines.append(f"- {a}")
            base += "\n\nAvailable agents:\n" + "\n".join(lines)
            base += "\n\nUse these exact names in the 'agent' field."
        return base

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "agent": {
                                "type": "string",
                                "description": "Exact name of an existing agent (from available agents list)",
                            },
                            "message": {
                                "type": "string",
                                "description": "The task/message to send to the agent",
                            },
                            "id": {
                                "type": "string",
                                "description": "Optional task ID for tracking",
                            },
                            "context": {
                                "type": "string",
                                "description": (
                                    "Context mode (default: 'shared'): "
                                    "'shared' — target agent uses its existing "
                                    "conversation context (no separate sub-agent, "
                                    "just a private message delivered in the conv); "
                                    "'isolated' — fresh empty sub-agent context "
                                    "(spawns a separate sub-agent); "
                                    "'last:N' — fresh sub-agent with last N "
                                    "messages from the parent conv. "
                                    "Agents that are themselves a delegate can "
                                    "only use 'shared' — isolated/last are rejected "
                                    "to prevent nested private sub-contexts."
                                ),
                            },
                            "skills": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Skill names to inject into the delegate agent's prompt (replaces the agent's own assigned_skills)",
                            },
                            "persist": {
                                "type": "boolean",
                                "description": "Only for context='isolated' or 'last:N': keep the sub-agent's sub-conversation after completion for later resume. Ignored in context='shared' (the target uses the main conv, nothing separate to persist).",
                            },
                        },
                        "required": ["agent", "message"],
                    },
                    "description": "List of tasks to spawn",
                },
            },
            "required": ["tasks"],
        }

    def set_user_id(self, uid: str):
        self._user_id = uid

    def execute(self, arguments: Dict[str, Any]) -> str:
        if not self._client_resolver:
            return "Error: Agent executor not configured (missing client_resolver)."

        from core.agent_executor import resolve_agent_task, SubAgentExecutor
        import uuid

        from core.handlers._arg_normalize import validate_object_list
        tasks_spec, _err = validate_object_list(
            arguments.get("tasks"),
            param_name="tasks",
            required_keys=["agent", "message"],
            example=('tasks=[{"agent": "<existing-agent-name>", '
                     '"message": "<text>", "id"?: "<optional>", '
                     '"context"?: "shared"|"isolated"|"last:N"}, ...]'),
        )
        if _err:
            return f"Error: {_err}"
        # Delegate is ALWAYS async (fire-and-forget). Results come back
        # via the preempt (caller running) / wake (caller idle) path.
        # No more 'wait' param — concurrency is the whole point.
        user_id = self._user_id

        # Detect task sub-conv: when an agent running inside a task
        # delegates, self._conversation_id is the sub-conv
        # (parent::task::tid). Agent resolution, routing, and delivery
        # must use the parent conv (where agents are registered).
        # Result delivery back to the calling task agent uses the raw
        # sub-conv ID so the preempt/wake targets the correct context.
        _raw_conv_id = self._conversation_id
        from core.service_registry import _parent_conversation_id
        _parent_conv_id = _parent_conversation_id(_raw_conv_id) or _raw_conv_id
        _source_task_id = (_raw_conv_id.split("::task::", 1)[1]
                           if "::task::" in _raw_conv_id else "")

        # Thread-safe source agent (each agent loop runs in its own thread)
        _src_agent = getattr(self._local, 'source_agent', '') or ''
        _src_svc = getattr(self._local, 'source_llm_service', '') or ''
        _delegate_tc_id = getattr(self._local, 'delegate_tc_id', '') or ''

        # Resolve self-name and nicknames to detect self-calls
        _self_names = {_src_agent.lower()} if _src_agent else set()
        _src_nickname = ""
        if _parent_conv_id and _src_agent:
            try:
                from core.conversation_store import ConversationStore
                _nicks = ConversationStore.instance().get_extra(
                    _parent_conv_id, "agent_nicknames") or {}
                _src_nickname = _nicks.get(_src_agent, "")
                if _src_nickname:
                    _self_names.add(_src_nickname.lower())
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

        # Per-pair de-duplication: if the same caller delegates to the
        # same target while a previous delegate is still running, inject
        # the new message into the running sub-agent's loop (preempt)
        # rather than spawning a parallel one. Only unique (caller,
        # target) pairs with no live delegate go through the spawn path.
        from core.agent_executor import (
            get_live_delegate,
            queue_live_delegate_message,
        )
        agent_tasks = []
        _injected_results = []
        for spec in tasks_spec:
            agent_name = spec.get("agent", "")
            message = spec.get("message", "")
            task_id = spec.get("id", uuid.uuid4().hex[:8])

            # Preempt path: a delegate for (_src_agent, agent_name) is
            # already running in this conversation — inject the message
            # into its loop instead of spawning a second one.
            if (_parent_conv_id and _src_agent and agent_name):
                _live = get_live_delegate(
                    _parent_conv_id, _src_agent, agent_name)
                if _live:
                    _live_client = _live.get("client")
                    _live_tid = _live.get("task_id", "")
                    _delivered = False
                    if _live_client and hasattr(_live_client, "send_user_message"):
                        try:
                            _delivered = bool(
                                _live_client.send_user_message(message))
                        except Exception as _pe:
                            logger.warning(
                                "[delegate] preempt to live delegate %s failed: %s",
                                _live_tid, _pe)
                    if not _delivered:
                        queue_live_delegate_message(
                            _parent_conv_id, _src_agent, agent_name, message)
                    _injected_results.append({
                        "task_id": _live_tid,
                        "agent": agent_name,
                        "status": "injected" if _delivered else "injected_queued",
                        "message": (
                            f"A delegate for '{agent_name}' was already "
                            f"running (task_id={_live_tid}) — your new "
                            f"message was {'sent as preempt' if _delivered else 'queued'}. "
                            f"You will receive a single follow-up result "
                            f"when that delegate finishes."
                        ),
                    })
                    logger.info(
                        "[delegate] preempt: (%s→%s) live task %s, "
                        "new message injected (delivered=%s)",
                        _src_agent, agent_name, _live_tid, _delivered)
                    continue

            # Resolve context mode — default "shared" (new semantics:
            # target agent uses its own conversation context, the
            # delegate is just a private message in the conv; no
            # sub-agent spawning).
            context_mode = spec.get("context", "shared")

            # A delegate agent can only use "shared" when it calls
            # delegate itself — prevents nested private sub-contexts.
            if context_mode != "shared" and self._is_caller_a_delegate():
                return (
                    f"Error: agent '{_src_agent}' is itself a delegate — "
                    f"sub-delegates must use context='shared' (isolated and "
                    f"last:N are reserved for top-level agents). Use "
                    f"context='shared' or drop the parameter."
                )

            if context_mode == "shared":
                # SHARED PATH: no sub-agent spawn. Persist a private
                # delegate message routed (from, to), then trigger the
                # target agent (preempt if running, wake if idle).
                if agent_name.lower() in _self_names:
                    return (
                        f"Error: You ('{_src_agent}') cannot delegate to "
                        f"yourself ('{agent_name}')."
                    )
                _deliver_info = self._deliver_shared_delegate(
                    from_agent=_src_agent, to_agent=agent_name,
                    message=message, user_id=user_id,
                    conv_id=_parent_conv_id)
                _injected_results.append({
                    "task_id": task_id,
                    "agent": agent_name,
                    "status": "delivered",
                    "mode": "shared",
                    "message": (
                        f"Delegate message delivered privately to '{agent_name}' "
                        f"(shared context — they use their own conv context). "
                        f"Target is {_deliver_info['state']}. You will receive "
                        f"'[Delegate result for task_id={task_id}]' when they "
                        f"reply — READ it and REACT."
                    ),
                })
                continue

            # ISOLATED / last:N / summary:N / full path — spawn a real
            # sub-agent via SubAgentExecutor.
            try:
                from core.handlers._arg_normalize import normalize_string_list
                extra_skills = normalize_string_list(spec.get("skills"))
                task = resolve_agent_task(agent_name, message, user_id,
                                         conversation_id=_parent_conv_id,
                                         extra_skills=extra_skills)
                task.id = task_id
                task.source_agent = _src_agent
                task.source_agent_nickname = _src_nickname
                task.source_llm_service = _src_svc
                task.delegate_tc_id = _delegate_tc_id
                task.persist = bool(spec.get("persist", False))

                task.context_mode = context_mode
                task.parent_conversation_id = _parent_conv_id
                task.source_task_id = _source_task_id

                if context_mode != "isolated" and _parent_conv_id:
                    task.context_messages = self._resolve_context(
                        context_mode, _parent_conv_id, user_id)

                # Prevent agent from calling itself
                if agent_name.lower() in _self_names:
                    return (f"Error: You ('{_src_agent}' via {_src_svc}) "
                            f"cannot call yourself as '{agent_name}' (via {task.llm_service}). "
                            f"Use a different agent or respond directly.")

                agent_tasks.append(task)
            except KeyError as e:
                return f"Error: {e}"

        if not agent_tasks:
            # Every spec was a preempt into an already-running delegate.
            if _injected_results:
                return json.dumps(_injected_results, ensure_ascii=False, indent=2)
            return "Error: no valid tasks to spawn."

        # Emit group start event so the UI can create a parent container
        if self._on_event and _delegate_tc_id:
            self._on_event("delegate_group_start", {
                "delegate_tc_id": _delegate_tc_id,
                "source_agent": _src_agent,
                "agents": [
                    {"name": t.agent_name, "task_id": t.id,
                     "message": t.message, "llm_service": t.llm_service}
                    for t in agent_tasks
                ],
                "total": len(agent_tasks),
                "source_task_id": _source_task_id,
            })

        # Create executor on-the-fly
        executor = SubAgentExecutor(
            self._default_client, self._registry, max_workers=4,
            client_resolver=self._client_resolver,
            on_event=self._on_event,
        )

        # Always async. Background completion callback ships the
        # isolated/last:N sub-agent's result back to the caller via
        # preempt/wake.
        # Result delivery uses _raw_conv_id so the preempt/wake
        # targets the caller in the task sub-conv (not the parent).
        _result_conv_id = _raw_conv_id
        _uid = user_id
        _src = _src_agent
        def _bg_callback(result, task):
            self._inject_bg_result(result, task, _result_conv_id, _uid, _src)

        results = executor.spawn(agent_tasks, wait=False,
                                 on_bg_complete=_bg_callback)

        ids = [r.task_id for r in results]
        _reply = {
            "status": "spawned",
            "task_ids": ids,
            "message": (
                f"Spawned {len(ids)} isolated sub-agent(s) in background. "
                f"You are NOT blocked — continue your own work. "
                f"When each sub-agent finishes you will receive a "
                f"message '[Delegate result for task_id=<id>]' "
                f"containing their response: READ IT and REACT "
                f"(integrate into your work, or reply to the user). "
                f"Track these task_ids: {ids}."
            ),
        }
        if _injected_results:
            _reply["injected"] = _injected_results
        return json.dumps(_reply, ensure_ascii=False)

    def _resolve_context(self, mode: str, conversation_id: str,
                         user_id: str) -> list:
        """Resolve context messages based on mode."""
        from core.conversation_store import ConversationStore
        store = ConversationStore.instance()

        if mode == "full":
            raw = store.load(conversation_id, user_id=user_id) or []
            # Filter out system messages, keep user/assistant/tool
            return [m for m in raw if m.get("role") != "system"]

        if mode.startswith("last:"):
            try:
                n = int(mode.split(":")[1])
            except (ValueError, IndexError):
                n = 10
            raw = store.load(conversation_id, user_id=user_id) or []
            non_system = [m for m in raw if m.get("role") != "system"]
            return non_system[-n:]

        if mode.startswith("summary:"):
            try:
                max_tokens = int(mode.split(":")[1])
            except (ValueError, IndexError):
                max_tokens = 2000
            raw = store.load(conversation_id, user_id=user_id) or []
            # Build a simple text summary from recent messages
            text_parts = []
            for m in raw[-50:]:  # last 50 messages for summary input
                role = m.get("role", "")
                content = m.get("content", "")
                if role in ("user", "assistant") and content:
                    text_parts.append(f"{role}: {content[:200]}")
            summary = "\n".join(text_parts)
            # Truncate to approximate token limit
            if len(summary) > max_tokens * 4:
                summary = summary[-(max_tokens * 4):]
            return [{"role": "user",
                     "content": f"[Context summary from parent conversation]"
                                f"\n{summary}"}]

        return []  # isolated

    def _is_caller_a_delegate(self) -> bool:
        """True if the currently-executing agent was itself triggered by a
        shared delegate message. Sub-delegates are restricted to
        context='shared' to prevent nested private sub-contexts.

        Checks AgentLoopTask._active_contexts for the caller's ctx and
        looks for a _turn_mode.type == 'delegate_reply'. Conservative:
        if we can't determine, returns False (allow).
        """
        try:
            _src = getattr(self._local, "source_agent", "") or ""
            if not (self._conversation_id and _src):
                return False
            from tasks.ai.agent_loop import AgentLoopTask
            inst = AgentLoopTask._live_instance
            if not inst:
                return False
            key = f"{self._conversation_id}:{_src}"
            with inst._active_contexts_lock:
                ctx = inst._active_contexts.get(key)
            if not ctx:
                return False
            tm = ctx.get("_turn_mode") or {}
            return tm.get("type") == "delegate_reply"
        except Exception:
            return False
