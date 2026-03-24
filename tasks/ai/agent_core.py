"""AgentLoopTask mixin — unified agent execution loop."""
import copy
import json
import logging
import time
from typing import Dict, List

from core.llm_client import (
    LLMClient, LLMMessage, LLMToolDefinition,
)
from tasks.ai.agent_emitter import AgentEmitter, AgentResult
from tasks.ai.agent_exceptions import AgentCancelled, _InterruptComplete

logger = logging.getLogger(__name__)

class AgentCoreMixin:
    def _run_agent_loop(self, ctx: Dict, emitter: AgentEmitter) -> AgentResult:
        """The ONE agent execution loop — used by both sync and streaming."""
        start_time = time.time()
        total_tokens_in = 0
        total_tokens_out = 0
        tools_called: List[str] = []
        iteration = 0
        final_model = ""
        finish_reason = ""
        response_content = ""
        _need_more_retried = False
        _fatal_error = False

        client: LLMClient = ctx["client"]
        registry = ctx["registry"]
        tool_defs = ctx["tool_defs"]
        messages: List[LLMMessage] = ctx["messages"]
        model = ctx["model"]
        conversation_id = ctx.get("conversation_id", "")
        use_conv_store = ctx.get("use_conv_store", False)
        user_id = ctx.get("user_id", "")
        max_rounds = int(ctx.get("max_rounds", 1)) if emitter.is_streaming else 1
        _consecutive_tool: Dict[str, int] = {}
        _max_consec = ctx.get("max_consecutive_tool_calls", 100)
        # Apply per-agent model override
        if use_conv_store and conversation_id:
            try:
                from core.conversation_store import ConversationStore
                _agent_n = ctx.get("active_agent_name") or ""
                _mo = ConversationStore.instance().get_extra(
                    conversation_id, f"model_override:{_agent_n}")
                if _mo:
                    model = _mo
            except Exception:
                pass
        # Client metadata
        _client_provider = getattr(client, "provider", "") or ""
        if not isinstance(_client_provider, str):
            _client_provider = ""
        _client_model = getattr(client, "default_model", "") or ""
        _client_base_url = getattr(client, "base_url", "") or ""
        if not isinstance(_client_base_url, str):
            _client_base_url = ""
        def _agent_source():
            import re as _re
            return {
                "type": "agent", "name": ctx.get("active_agent_name", ""),
                "llm_service": ctx.get("active_llm_service", ""),
                "provider": _client_provider,
                "model": _client_model,
                "base_url": _re.sub(r'(key|token|secret)=[^&]+', r'\1=***',
                                    _client_base_url) if _client_base_url else "",
            }
        # SpawnAgentsHandler source tracking
        from core.tool_registry import SpawnAgentsHandler as _SAH
        for _h in registry.list_tools():
            if isinstance(_h, _SAH):
                _h.set_source_agent(
                    ctx.get("active_agent_name", ""),
                    ctx.get("active_llm_service", ""))
                break
        # New messages tracking
        new_messages: List[LLMMessage] = []
        base_count = ctx.get("_base_message_count", 0)
        if len(messages) > base_count:
            new_messages.extend(messages[base_count:])
        def _append(msg: LLMMessage):
            messages.append(msg)
            new_messages.append(msg)

        def _flush():
            nonlocal new_messages
            emitter.flush(new_messages)
            new_messages = []
        # Track known message count
        if use_conv_store and conversation_id:
            try:
                from core.conversation_store import ConversationStore
                ctx["_last_known_msg_count"] = ConversationStore.instance().message_count(
                    conversation_id)
            except Exception:
                ctx["_last_known_msg_count"] = 0

        emitter.on_loop_start(ctx)
        _flush()
        _summ = ctx.get("summarizer", (None, 0))
        compact_client = _summ[0] if _summ[0] else (ctx.get("default_client") or client)

        # Note: post-response compact is done by auto-compact on load
        # in _prepare_agent_context. No lazy flag needed.

        try:
            for current_round in range(1, max_rounds + 1):
                continuation_plan = None
                continuation_delay = 3
                for _ in iter(lambda: iteration < ctx["max_iterations"], False):
                    emitter.check_cancelled()
                    emitter.drain_pending(messages, _append, iteration)
                    iteration += 1

                    poll_silent = ctx.get("is_poll", False) and iteration == 1
                    emitter.on_iteration_start(
                        iteration, current_round, ctx["max_iterations"],
                        max_rounds, tools_called, poll_silent)

                    # Compaction
                    llm_context = self._compact_if_needed(
                        copy.deepcopy(messages), compact_client,
                        ctx.get("max_context_size", 64000),
                        ctx.get("context_compact_threshold", 0.75),
                        ctx.get("context_keep_recent", 6),
                        conversation_id=conversation_id,
                        agent_name=ctx.get("active_agent_name") or "",
                        tool_defs=ctx.get("tool_defs"),
                        chars_per_token=ctx.get("chars_per_token", 0),
                    )
                    if len(llm_context) < len(messages):
                        ctx["_context_diverged"] = True

                    # Pre-injection char count for CPT calibration
                    _pre_inject_chars = self._estimate_tokens(
                        llm_context, tool_defs=tool_defs, chars_per_token=1.0)

                    # Identity injection
                    _id_nicks = ctx.get("_nicknames") or {}
                    llm_context = self._inject_identity(llm_context, _id_nicks)
                    llm_context = self._apply_identity_suffix(
                        llm_context, ctx.get("_identity_suffix", ""))

                    # Token budget note
                    _max_ctx = ctx.get("max_context_size", 200000)
                    _est_used = self._estimate_tokens(
                        llm_context, tool_defs=tool_defs,
                        chars_per_token=ctx.get("chars_per_token", 0))
                    _remaining = max(0, _max_ctx - _est_used)
                    if llm_context and llm_context[0].role == "system":
                        llm_context[0] = LLMMessage(
                            role="system",
                            content=(llm_context[0].content or "") +
                            f"\n\n[Context: ~{_est_used} of {_max_ctx} tokens used, ~{_remaining} remaining]",
                        )

                    emitter.check_cancelled()

                    # Interrupt check
                    if emitter.check_interrupt():
                        logger.info(f"[agent:{conversation_id[:8]}] interrupted — forcing synthesis")
                        _append(LLMMessage(
                            role="user",
                            content=(
                                "[System: The user has requested an immediate response. "
                                "Stop all tool usage. Summarize your progress so far and "
                                "provide your best answer with the information you have "
                                "gathered. Mention what you were still working on so the "
                                "user can ask you to continue if needed.]"),
                        ))
                        _irpt_resp = client.complete_stream(
                            messages=self._compact_if_needed(
                                copy.deepcopy(messages), compact_client,
                                ctx.get("max_context_size", 64000), 0.6,
                                ctx.get("context_keep_recent", 6)),
                            model=model or None,
                            temperature=ctx["temperature"],
                            max_tokens=ctx["max_tokens"],
                            tools=None,
                            callback=emitter.get_token_callback(False),
                        ) if emitter.is_streaming else client.complete(
                            messages=self._compact_if_needed(
                                copy.deepcopy(messages), compact_client,
                                ctx.get("max_context_size", 64000), 0.6,
                                ctx.get("context_keep_recent", 6)),
                            model=model or None,
                            temperature=ctx["temperature"],
                            max_tokens=ctx["max_tokens"],
                            tools=None,
                        )
                        _append(LLMMessage(
                            role="assistant", content=_irpt_resp.content,
                            source=_agent_source()))
                        response_content = _irpt_resp.content
                        total_tokens_in += _irpt_resp.tokens_in
                        total_tokens_out += _irpt_resp.tokens_out
                        final_model = _irpt_resp.model
                        _flush()
                        raise _InterruptComplete()

                    # Force-fit guard
                    _pre_send_est = self._estimate_tokens(
                        llm_context, tool_defs=ctx.get("tool_defs"),
                        chars_per_token=ctx.get("chars_per_token", 0))
                    logger.debug(
                        f"[compact] pre-send: {_pre_send_est} est. tokens, "
                        f"{len(llm_context)} msgs, max={_max_ctx}")
                    if _pre_send_est > _max_ctx:
                        logger.warning(
                            f"[compact] STILL OVER ({_pre_send_est} > {_max_ctx}), force-fitting...")
                        llm_context = self._force_fit_context(
                            llm_context, _max_ctx,
                            chars_per_token=ctx.get("chars_per_token", 0),
                            tool_defs=ctx.get("tool_defs"))

                    # Dynamic lazy tools fallback — for tools_mode=full that needs switching
                    if (tool_defs and not ctx.get("_lazy_tools_active")
                            and len(tool_defs) > 4 and iteration > 5):
                        _tools_chars = sum(
                            len(td.name) + len(td.description or "")
                            + len(json.dumps(td.parameters or {}))
                            for td in tool_defs)
                        _tools_pct = (_tools_chars / max(_pre_send_est * 3.5, 1)) * 100
                        if _tools_pct > 15:
                            logger.info(f"[compact] Dynamic lazy switch: tools={_tools_pct:.0f}%% of context")
                            from core.tool_registry import GetToolSchemaHandler, UseToolHandler
                            _gts = GetToolSchemaHandler(registry)
                            _ut = UseToolHandler(registry)
                            registry.register(_gts)
                            registry.register(_ut)
                            tool_defs = [
                                LLMToolDefinition(name=_gts.name, description=_gts.description,
                                                  parameters=_gts.parameters_schema),
                                LLMToolDefinition(name=_ut.name, description=_ut.description,
                                                  parameters=_ut.parameters_schema),
                            ]
                            ctx["tool_defs"] = tool_defs
                            ctx["_lazy_tools_active"] = True

                    # LLM call
                    _tb = ctx.get("thinking_budget", 0)

                    def _llm_call(msgs, ps=poll_silent):
                        if emitter.is_streaming:
                            return client.complete_stream(
                                messages=msgs, model=model or None,
                                temperature=ctx["temperature"], max_tokens=ctx["max_tokens"],
                                tools=tool_defs if tool_defs else None,
                                callback=emitter.get_token_callback(ps),
                                thinking_budget=_tb,
                                thinking_callback=emitter.get_thinking_callback(ps) if _tb > 0 else None)
                        return client.complete(
                            messages=msgs, model=model or None,
                            temperature=ctx["temperature"], max_tokens=ctx["max_tokens"],
                            tools=tool_defs if tool_defs else None, thinking_budget=_tb)

                    hb = emitter.start_heartbeat(poll_silent)
                    try:
                        response = _llm_call(llm_context)
                    except AgentCancelled:
                        raise
                    except Exception as llm_err:
                        err_str = str(llm_err)
                        if "exceed_context_size" in err_str or "n_prompt_tokens" in err_str:
                            logger.warning(f"[agent:{conversation_id[:8]}] Context overflow, retrying...")
                            emitter.on_overflow_retry(iteration)
                            llm_context = self._compact_if_needed(
                                llm_context, compact_client,
                                ctx.get("max_context_size", 64000), 0.5,
                                ctx.get("context_keep_recent", 6),
                                conversation_id=conversation_id,
                                tool_defs=ctx.get("tool_defs"),
                                chars_per_token=ctx.get("chars_per_token", 0))
                            try:
                                response = _llm_call(llm_context)
                            except Exception as retry_err:
                                logger.error(f"LLM retry failed: {retry_err}")
                                emitter.on_fatal_error(f"LLM call failed: {retry_err}")
                                _fatal_error = True
                                break
                        else:
                            logger.error(f"LLM call failed (iter {iteration}): {llm_err}")
                            emitter.on_fatal_error(f"LLM call failed: {llm_err}")
                            _fatal_error = True
                            break
                    finally:
                        emitter.stop_heartbeat(hb)

                    emitter.check_cancelled()

                    # Post-response
                    total_tokens_in += response.tokens_in
                    total_tokens_out += response.tokens_out
                    final_model = response.model
                    finish_reason = response.finish_reason

                    self._deflate_image_messages(messages)
                    # Clear old tool results — keep last 3 (2 was too aggressive, caused repeats)
                    _keep = 3
                    self._clear_seen_tool_results(
                        messages, keep_recent=_keep,
                        conversation_id=conversation_id, user_id=user_id,
                        agent_name=ctx.get("active_agent_name", ""))

                    if response.tokens_in > 0:
                        _svc_id = ctx.get("active_llm_service") or ""
                        self._calibrate_cpt(_svc_id, _pre_inject_chars, response.tokens_in)
                        ctx["chars_per_token"] = self._get_cpt(
                            _svc_id, ctx.get("chars_per_token", 0))

                    # No tools → final response
                    if not response.tool_calls:
                        _resp_text = response.content or ""
                        _has_thinking = bool(getattr(response, 'thinking', ''))
                        # Empty response with thinking = LLM is stuck in reasoning
                        # Give it one more chance with explicit instruction
                        if not _resp_text and _has_thinking and not _need_more_retried:
                            logger.warning(f"[agent:{conversation_id[:8]}] thinking-only response (no text/tools), nudging")
                            _append(LLMMessage(role="assistant", content="", source=_agent_source()))
                            _append(LLMMessage(role="user", content=(
                                "[System: You produced reasoning but no visible response or tool calls. "
                                "You MUST either call a tool or provide a text response to the user. "
                                "Do not just think — act or respond.]")))
                            _need_more_retried = True
                            continue
                        action, msgs, final, _need_more_retried = self._handle_response_no_tools(
                            _resp_text, _client_provider, tool_defs,
                            _need_more_retried, source=_agent_source())
                        for _m in msgs:
                            _append(_m)
                        if action == "break":
                            response_content = final
                            _flush()
                            break
                        continue

                    # Tool calls
                    _need_more_retried = False
                    _append(LLMMessage(
                        role="assistant", content=response.content,
                        tool_calls=response.tool_calls, source=_agent_source()))

                    if poll_silent and response.tool_calls:
                        poll_silent = False

                    emitter.on_tool_calls(
                        response.tool_calls, response.content or "",
                        response.thinking or "", poll_silent)

                    results = self._execute_tool_calls(
                        response.tool_calls, registry, _consecutive_tool,
                        _max_consec, parallel=emitter.is_streaming,
                        agent_name=ctx.get("active_agent_name") or "",
                        agent_svc=ctx.get("active_llm_service", ""),
                        conversation_id=conversation_id, user_id=user_id)

                    for tc, result_text in results:
                        tools_called.append(tc.name)
                        if tc.name == "schedule_continuation":
                            continuation_plan = tc.arguments.get("plan", "Continue")
                            continuation_delay = int(tc.arguments.get("delay_seconds", 3))
                        _append(LLMMessage(role="tool", content=result_text, tool_call_id=tc.id))
                        # Preview for SSE
                        _prev = result_text[:2000] if isinstance(result_text, str) else str(result_text)[:2000]
                        if isinstance(_prev, str) and _prev.startswith("[TOOL OUTPUT"):
                            _nl = _prev.find("\n")
                            if _nl >= 0:
                                _prev = _prev[_nl + 1:]
                            if _prev.endswith("[/TOOL OUTPUT]"):
                                _prev = _prev[:-len("[/TOOL OUTPUT]")].rstrip("\n")
                        emitter.on_tool_result(tc, result_text, _prev)

                    emitter.on_iteration_end(
                        iteration, current_round, ctx["max_iterations"],
                        max_rounds, tools_called)
                    emitter.drain_pending(messages, _append, iteration)
                    emitter.check_cancelled()

                    # Mid-turn compaction: every 5 iterations, progressively clear
                    # old tool results on the canonical messages to stop context growth
                    if iteration % 5 == 0 and len(messages) > 20:
                        _cpt = ctx.get("chars_per_token", 0) or 3.5
                        _mid_est = self._estimate_tokens(messages, chars_per_token=_cpt)
                        _mid_target = int(ctx.get("max_context_size", 200000) * 0.5)
                        if _mid_est > _mid_target:
                            logger.info(f"[agent:{conversation_id[:8]}] mid-turn compact: "
                                        f"{_mid_est} tokens > {_mid_target} target")
                            self._progressive_clear_tool_results(
                                messages, _mid_target, _mid_est,
                                keep_recent=4, chars_per_token=_cpt)

                    _flush()
                else:
                    # Max iterations reached
                    logger.warning("Agent reached max iterations (%d), forcing synthesis",
                                   ctx["max_iterations"])
                    _pre = len(messages)
                    content, ti, to, fm = self._force_synthesis(
                        messages, client, ctx,
                        prompt=(
                            "[System: You have reached the maximum number of tool calls. "
                            "You MUST now provide your final response to the user. "
                            "Synthesize all the information you gathered from your tool calls "
                            "and present a clear, comprehensive answer. Do NOT call any more tools.]"),
                        compact_client=compact_client,
                        use_streaming=emitter.is_streaming,
                        token_callback=emitter.get_token_callback(False) if emitter.is_streaming else None,
                        tools_called=tools_called, compact_threshold=1.0,
                        conversation_id=conversation_id)
                    new_messages.extend(messages[_pre:])
                    response_content = content
                    total_tokens_in += ti
                    total_tokens_out += to
                    if fm:
                        final_model = fm

                _flush()

                if _fatal_error:
                    break

                # Continuation
                if continuation_plan and current_round < max_rounds:
                    _append(LLMMessage(
                        role="user",
                        content=(
                            f"[System: Automatic continuation — round {current_round + 1}]\n"
                            f"Continue with your plan: {continuation_plan}\n"
                            f"Build on your previous findings. When done, provide a final synthesis. "
                            f"If you still have more work, call schedule_continuation again.")))
                    response_content = ""
                    time.sleep(continuation_delay)
                    continue
                else:
                    break

            # Empty response synthesis
            if not response_content and not _fatal_error:
                logger.warning(f"[agent:{conversation_id[:8]}] empty response — forcing synthesis")
                _pre = len(messages)
                content, ti, to, fm = self._force_synthesis(
                    messages, client, ctx,
                    prompt=(
                        "[System: You did not provide a response to the user. "
                        "You MUST respond now. Synthesize any information you have and present "
                        "a clear answer. Do NOT call any tools.]"),
                    compact_client=compact_client,
                    use_streaming=emitter.is_streaming,
                    token_callback=emitter.get_token_callback(False) if emitter.is_streaming else None,
                    tools_called=tools_called, conversation_id=conversation_id)
                new_messages.extend(messages[_pre:])
                response_content = content
                total_tokens_in += ti
                total_tokens_out += to
                if fm:
                    final_model = fm
                _flush()

            def _make_result(reason=""):
                return AgentResult(
                    response_content=response_content,
                    conversation_id=conversation_id,
                    model=final_model or _client_model,
                    provider=_client_provider, base_url=_client_base_url,
                    tokens_in=total_tokens_in, tokens_out=total_tokens_out,
                    tools_called=tools_called, iterations=iteration,
                    duration_ms=(time.time() - start_time) * 1000,
                    finish_reason=reason or finish_reason, source=_agent_source(),
                    messages=messages, new_messages=new_messages)

            # NO_PENDING_WORK handling (streaming/poller only via emitter)
            _processed = emitter.on_no_pending_work(response_content or "", ctx)
            if _processed is None:
                new_messages.clear()
                return _make_result("discarded")
            response_content = _processed

            self._track_tokens(
                user_id or "anonymous", total_tokens_in, total_tokens_out,
                model=final_model or _client_model,
                agent_name=ctx.get("active_agent_name", "") or "",
                llm_service=ctx.get("active_llm_service", ""))

            # Mark for lazy compaction on next turn (avoid blocking done event
            # and prevent poll ghosts from the summarize LLM call)
            # Post-response compact is handled by auto-compact on next load

            self._cleanup_tool_result_files(
                conversation_id=conversation_id,
                agent_name=ctx.get("active_agent_name", ""))

            result = _make_result()
            emitter.on_done(result)
            return result

        except _InterruptComplete:
            def _make_result(reason=""):
                return AgentResult(
                    response_content=response_content, conversation_id=conversation_id,
                    model=final_model or _client_model, provider=_client_provider,
                    base_url=_client_base_url, tokens_in=total_tokens_in,
                    tokens_out=total_tokens_out, tools_called=tools_called,
                    iterations=iteration, duration_ms=(time.time() - start_time) * 1000,
                    finish_reason=reason, source=_agent_source(),
                    messages=messages, new_messages=new_messages)
            emitter.on_interrupted(_make_result("interrupted"))
            return _make_result("interrupted")

        except AgentCancelled:
            logger.info(f"[agent:{conversation_id[:8]}] cancelled — checkpointing")
            _flush()
            def _make_result(reason=""):
                return AgentResult(
                    response_content=response_content, conversation_id=conversation_id,
                    model=final_model or _client_model, tokens_in=total_tokens_in,
                    tokens_out=total_tokens_out, tools_called=tools_called,
                    iterations=iteration, duration_ms=(time.time() - start_time) * 1000,
                    finish_reason=reason, source=_agent_source(),
                    messages=messages, new_messages=new_messages)
            emitter.on_cancelled(_make_result("cancelled"), ctx)
            return _make_result("cancelled")

        except Exception as e:
            logger.error(f"Agent loop error: {e}", exc_info=True)
            _flush()
            emitter.on_error(e)
            raise
