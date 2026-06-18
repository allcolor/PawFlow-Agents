"""Core sub-agent loop (_run_agent_loop) for SubAgentExecutor.

Split out of agent_executor.py as a leaf mixin so the file stays <= 800 lines.
The method is moved verbatim; it calls SubAgentExecutor helpers (self._build_*,
self._execute_tool, self._emit, ...) resolved through the MRO.
"""
from __future__ import annotations

import logging
import time

from core.llm_client import LLMMessage

from core._agent_executor_base import (
    AgentResult,
    AgentTask,
    _clear_cancelled,
    _get_depth,
    _is_cancelled,
    drain_live_delegate_messages,
    register_live_delegate,
    unregister_live_delegate,
)

logger = logging.getLogger(__name__)


class _SubAgentExecutorLoopMixin:
    """Core agent loop for SubAgentExecutor."""

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

        # Build system prompt with spawn context
        sys_prompt = task.system_prompt
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

        # Sub-agent runs on its OWN cloned client — fully isolated from
        # the parent's singleton. Each Claude Code stream already has
        # its own container; the Python orchestration state must be
        # per-stream too. _subagent_event_cb (UI sub_agent_* event
        # redirect) is set on the clone, not the parent.
        _delegate_conv_id = sub_conv_id or task.parent_conversation_id or ""
        if hasattr(client, 'clone_for_call'):
            client = client.clone_for_call()
        _saved_client_state = {}  # nothing to restore — clone is private
        # Suppress raw provider SSE events to the parent bus — the
        # SubAgentExecutor already emits sub_agent_* events that the UI
        # uses to render delegate sub-blocks. event_cid=None is the
        # explicit suppress sentinel (passed via call_event_cid below).
        _delegate_call_kwargs = {
            "call_user_id": task.user_id or "",
            "call_conversation_id": _delegate_conv_id,
            "call_agent_name": task.agent_name or "",
            "call_event_cid": None,
            "call_ephemeral_stream": False,
        }
        try:
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
                    logger.debug("exception suppressed", exc_info=True)
            client._subagent_event_cb = _subagent_cb
            logger.info("[sub-agent:%s] client wired conv=%s agent=%s user=%s",
                        task.agent_name,
                        _delegate_conv_id, task.agent_name or "",
                        task.user_id or "")
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

        def _append_live_delegate_pending() -> int:
            pending = drain_live_delegate_messages(
                task.parent_conversation_id, task.source_agent,
                task.agent_name, task.id)
            for _msg in pending:
                messages.append(LLMMessage(
                    role="user", content=_msg, source=user_source,
                    conversation_id=sub_conv_id or task.parent_conversation_id,
                ))
            if pending:
                logger.info(
                    "[sub-agent:%s] drained %d queued delegate follow-up(s)",
                    task.agent_name, len(pending))
            return len(pending)

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
                    # Register CLI clients under the task-shaped key so a
                    # task-targeted force-stop (cancel_interrupt.py line
                    # 60: `f"::task::{task_id}" in k`) finds and cancels
                    # the sub-agent's provider process/session.
                    if (hasattr(client, "cancel_claude_code")
                            or hasattr(client, "cancel_claude_code_interactive")
                            or hasattr(client, "abort")):
                        _active_inst._active_claude_client[_active_ctx_key] = client
        except Exception:
            _active_inst = None

        try:
            for iteration in range(1, max_iter + 1):
                if _active_inst and _active_ctx_key:
                    try:
                        _active_ctx["_iteration"] = iteration
                    except Exception:
                        logger.debug("exception suppressed", exc_info=True)
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
                        logger.debug("exception suppressed", exc_info=True)

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
                        logger.debug("exception suppressed", exc_info=True)

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
                            logger.debug("exception suppressed", exc_info=True)

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
                            **_delegate_call_kwargs,
                        )
                        break
                    except Exception as _llm_err:
                        try:
                            from core.llm_client import CCCompactDetected
                            _compact_detected = isinstance(
                                _llm_err, CCCompactDetected)
                        except Exception:
                            _compact_detected = False
                        if not _compact_detected:
                            _compact_detected = (
                                "CC auto-compact detected" in str(_llm_err)
                                or "contextCompaction" in str(_llm_err))
                        if (not _compact_detected or _compact_attempts >= 2):
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
                                    conversation_id=_delegate_conv_id,
                                    agent_name=task.agent_name,
                                    user_id=task.user_id,
                                    max_context=_max_ctx,
                                    independent_context=bool(sub_conv_id),
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
                            ConversationStore.instance().invalidate_claude_session_for_agent(
                                _delegate_conv_id, task.agent_name)
                        except Exception:
                            logger.debug("exception suppressed", exc_info=True)
                        # Recover OAuth tokens (CC may have refreshed during
                        # the killed session) before the retry.
                        if hasattr(client, '_recover_tokens') and hasattr(client, '_get_session_workdir'):
                            try:
                                _wd = client._get_session_workdir(
                                    _delegate_conv_id,
                                    task.agent_name, task.user_id)
                                client._recover_tokens(_wd)
                            except Exception:
                                logger.debug("exception suppressed", exc_info=True)

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

                # If a duplicate delegate arrived while a CLI provider was
                # running, Codex/Gemini killed the process and returned to
                # this loop. Preserve any partial assistant text, append the
                # queued follow-up, and continue instead of treating the
                # interrupted response as final.
                if not response.tool_calls:
                    _queued_followups = _append_live_delegate_pending()
                    if _queued_followups:
                        if response.content:
                            messages.append(LLMMessage(
                                role="assistant", content=response.content,
                                source={"type": "agent", "name": task.agent_name},
                                conversation_id=sub_conv_id or task.parent_conversation_id,
                            ))
                        continue

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
                    thinking=response.thinking or "",
                    thinking_signature=getattr(response, "thinking_signature", "") or "",
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
                            logger.debug("exception suppressed", exc_info=True)
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
                            if getattr(m, 'thinking', ''):
                                d["thinking"] = m.thinking
                            if getattr(m, 'thinking_signature', ''):
                                d["thinking_signature"] = m.thinking_signature
                            if hasattr(m, 'tool_call_id') and m.tool_call_id:
                                d["tool_call_id"] = m.tool_call_id
                            return d
                        _store.save(sub_conv_id,
                                    [_serialize_msg(m) for m in messages],
                                    user_id=task.user_id)
                    except Exception:
                        logger.debug("exception suppressed", exc_info=True)

                if _append_live_delegate_pending():
                    continue

                if result.status in ("timeout", "cancelled", "needs_input"):
                    break
            else:
                # Max iterations reached — force synthesis
                result.response = self._force_synthesis(
                    messages, task.model, _delegate_call_kwargs,
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
                logger.debug("exception suppressed", exc_info=True)
            # Unregister from active-agents panel + claude-client registry
            if _active_inst and _active_ctx_key:
                try:
                    with _active_inst._active_contexts_lock:
                        _active_inst._active_contexts.pop(_active_ctx_key, None)
                        _active_inst._active_claude_client.pop(_active_ctx_key, None)
                except Exception:
                    logger.debug("exception suppressed", exc_info=True)
            # Clear live-delegate slot so the next delegate call spawns fresh.
            if task.parent_conversation_id and task.source_agent and task.agent_name:
                try:
                    unregister_live_delegate(
                        task.parent_conversation_id,
                        task.source_agent, task.agent_name,
                        task.id)
                except Exception:
                    logger.debug("exception suppressed", exc_info=True)

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
                logger.debug("exception suppressed", exc_info=True)
        elif sub_conv_id and task.persist:
            logger.info("[sub-agent:%s] Persisting sub-conversation %s",
                        task.agent_name, sub_conv_id)

        return result

