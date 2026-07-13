"""agent_core split (<=800 lines): _ALCIterationMixin."""
import copy
import logging

from core.llm_client import (
    LLMMessage,
)

from tasks.ai._alc_base import (  # noqa: F401
    _ALCState, _ALC_BREAK, _ALC_CONTINUE, _strip_context_ack,
    _preempt_rescue_requires_retrigger, _apply_bg_results, _svc_rates,
    _usage_cost_usd, _check_budget, _CONTEXT_ACK_PATTERNS)

logger = logging.getLogger(__name__)


class _ALCIterationMixin:
    def _alc_iteration(self, st):
        st.emitter.check_cancelled()
        st.emitter.drain_pending(st.messages, st._append, st.iteration)
        st.iteration += 1
        st.ctx["_iteration"] = st.iteration
        st.ctx["_round"] = st.current_round

        # Tasks are explicit user actions — always stream their output
        st._is_task = bool(st.conversation_id and "::task::" in st.conversation_id)
        st.poll_silent = st.ctx.get("is_poll", False) and st.iteration == 1 and not st._is_task
        # Heartbeat covers the ENTIRE iteration (LLM + tools)
        st._iter_hb = st.emitter.start_heartbeat(st.poll_silent)
        st.emitter.on_iteration_start(
            st.iteration, st.current_round, st.ctx["max_iterations"],
            st.max_rounds, st.tools_called, st.poll_silent)
        # Read the service-config proactive-compact threshold
        # from the AGENT's client (e.g. codex_llm_service),
        # NOT from the summarizer client (`compact_client`,
        # typically claude_code_llm_service). The threshold
        # describes when the agent's own context is too
        # large — the summarizer is just the tool used to
        # shrink it.
        #
        # `compact_threshold_pct` accepts percent values
        # (80 / "80%") and UI/API fractional values (0.8):
        #   0   = no proactive PawFlow compact (defer entirely to
        #         the CLI's mechanism — e.g. CC's compact_boundary,
        #         or for codex/gemini, no auto-compact at all).
        #   N>0 = compact when tokens(messages) ≥ N% of max_ctx,
        #         BEFORE the next LLM call. For CC this is
        #         additive on top of compact_boundary — first
        #         trigger to fire wins.
        st._trigger_frac = 0.0
        try:
            st._agent_client_cfg = (
                getattr(st.ctx.get("resolved_svc"), "config", None)
                or getattr(st.client, "config", None)
                or getattr(st.client, "_config_ref", None)
                or getattr(getattr(st.client, "_client", None),
                           "_config_ref", None)
                or {})
            st._trigger_frac = st._compact_threshold_fraction(
                st._agent_client_cfg.get("compact_threshold_pct", 0))
        except (AttributeError, TypeError):
            st._trigger_frac = 0.0

        st._with_provider_system_prompt = lambda stored_msgs: self._alc_with_provider_system_prompt(st, stored_msgs)

        st._build_provider_context = lambda stored_msgs: self._alc_build_provider_context(st, stored_msgs)

        st._inject_dynamic_metadata = lambda provider_context: self._alc_inject_dynamic_metadata(st, provider_context)

        st._threshold_estimate = lambda stored_msgs, cpt: self._alc_threshold_estimate(st, stored_msgs, cpt)

        st._should_proactive_compact = lambda stored_msgs, max_ctx, cpt: self._alc_should_proactive_compact(st, stored_msgs, max_ctx, cpt)

        st._messages_changed = lambda candidate, current: self._alc_messages_changed(st, candidate, current)

        st._adopt_compacted_context = lambda compacted_messages, *, reason, async_cleanup=False, already_persisted=False: self._alc_adopt_compacted_context(st, compacted_messages, reason=reason, async_cleanup=async_cleanup, already_persisted=already_persisted)

        # Claude-code: CC session and PawFlow ctx MUST stay
        # identical. On a new session we feed the full PawFlow
        # ctx (already compacted at load time if needed).
        # On resume, CC's jsonl is the authoritative continuation
        # — we don't re-send messages.
        if st.ctx.get("_is_claude_code"):
            st._max_ctx = st.ctx.get("max_context_size", 64000)
            st._cpt = st.ctx.get("chars_per_token", 0)
            # Optional proactive compact for CC when
            # `compact_threshold_pct > 0`: fire BEFORE letting
            # CC see the over-budget context. Both this and
            # CC's own `compact_boundary` event remain active;
            # whichever fires first compacts. Skip when
            # threshold = 0 (default) — CC's mechanism handles it.
            if st._should_proactive_compact(st.messages, st._max_ctx, st._cpt):
                st.compacted_messages = self._compact(
                    copy.deepcopy(st.messages), st.compact_client, st._max_ctx,
                    trigger_fraction=st._trigger_frac,
                    conversation_id=st.conversation_id,
                    agent_name=st.ctx.get("active_agent_name") or "",
                    tool_defs=st.ctx.get("tool_defs"),
                    chars_per_token=st._cpt,
                    user_id=st.user_id,
                    budget_config=getattr(st.ctx.get("resolved_svc"), "config", None),
                    independent_context=bool(st.ctx.get("_independent_context")),
                )

                if st._messages_changed(st.compacted_messages, st.messages):
                    st._adopt_compacted_context(
                        st.compacted_messages, reason="proactive_cli",
                        already_persisted=True)
            st.llm_context = list(st.messages)
        else:
            st._max_ctx = st.ctx.get("max_context_size", 64000)
            st._cpt = st.ctx.get("chars_per_token", 0)

            # Microcompaction: clear old tool results after idle gap
            if st.iteration == 1:
                self._microcompact_time_based(st.messages)

            # codex / gemini / etc. — the CLI never auto-compacts
            # (codex's `model_auto_compact_token_limit` is set
            # very high by PawFlow, gemini doesn't have one), so
            # threshold = 0 means no auto-compact at all and the
            # context grows until the LLM rejects an over-budget
            # call. With threshold > 0, fire the proactive
            # compact at that fraction. Guarantees output ≤
            # compact_target_tokens (or 0.25 × max_context).
            if st._should_proactive_compact(st.messages, st._max_ctx, st._cpt):
                st.compacted_messages = self._compact(
                    copy.deepcopy(st.messages), st.compact_client, st._max_ctx,
                    trigger_fraction=st._trigger_frac,
                    conversation_id=st.conversation_id,
                    agent_name=st.ctx.get("active_agent_name") or "",
                    tool_defs=st.ctx.get("tool_defs"),
                    chars_per_token=st._cpt,
                    user_id=st.user_id,
                    budget_config=getattr(st.ctx.get("resolved_svc"), "config", None),
                    independent_context=bool(st.ctx.get("_independent_context")),
                )
                if st._messages_changed(st.compacted_messages, st.messages):
                    st._adopt_compacted_context(
                        st.compacted_messages, reason="proactive",
                        already_persisted=True)
                st.llm_context = list(st.messages)
            else:
                # threshold = 0: no proactive compact, send the
                # raw messages (PawFlow's reactive compact at the
                # *_compact site below — e.g. context-overflow
                # retry — still applies as a safety net).
                st.llm_context = list(st.messages)

        # Pre-injection char count
        st.llm_context, st._pre_inject_chars = st._build_provider_context(st.messages)

        # Identity injection
        st._id_nicks = st.ctx.get("_nicknames") or {}
        st.llm_context = self._inject_identity(st.llm_context, st._id_nicks)
        st.llm_context = self._apply_identity_suffix(
            st.llm_context, st.ctx.get("_identity_suffix", ""))

        # Dynamic metadata — merged into the last user message
        # (AFTER cache breakpoints, so prefix is stable)
        st._max_ctx = st.ctx.get("max_context_size", 200000)
        st.llm_context = st._inject_dynamic_metadata(st.llm_context)

        st.emitter.check_cancelled()

        st._run_interrupt_turn = lambda : self._alc_run_interrupt_turn(st)

        # Interrupt check before starting a new provider request.
        if st.emitter.check_interrupt():
            st._run_interrupt_turn()

        # Force-fit guard (skip for claude-code — it manages its own context)
        if not st.ctx.get("_is_claude_code"):
            from core.token_counter import resolve_token_multiplier as _rtm
            st._ff_tmul = _rtm(getattr(
                st.ctx.get("resolved_svc"), "config", None))
            st._pre_send_est = self._estimate_tokens(
                st.llm_context, tool_defs=st.ctx.get("tool_defs"),
                chars_per_token=st.ctx.get("chars_per_token", 0),
                token_multiplier=st._ff_tmul)
            logger.debug(
                f"[compact] pre-send: {st._pre_send_est} est. tokens, "
                            f"{len(st.llm_context)} msgs, max={st._max_ctx}")
            if st._trigger_frac > 0:
                st._trigger_tokens = int(st._max_ctx * st._trigger_frac)
                st._threshold_used = st._pre_send_est
                if st._threshold_used >= st._trigger_tokens:
                    logger.info(
                        "[compact] pre-send threshold crossed: "
                                    "%d >= %d (%.0f%%)",
                        st._threshold_used, st._trigger_tokens,
                        st._trigger_frac * 100)
                    # The prompt that will actually be sent crossed
                    # the configured threshold. Do not use the live
                    # gauge here: resumable CLI sessions may keep a
                    # large persisted context while the provider call
                    # only sends the latest delta.
                    st.compacted_messages = self._compact(
                        copy.deepcopy(st.messages), st.compact_client,
                        st._max_ctx,
                        force=True,
                        trigger_fraction=st._trigger_frac,
                        conversation_id=st.conversation_id,
                        agent_name=st.ctx.get("active_agent_name") or "",
                        tool_defs=st.ctx.get("tool_defs"),
                        chars_per_token=st.ctx.get("chars_per_token", 0),
                        user_id=st.user_id,
                        budget_config=getattr(st.ctx.get("resolved_svc"), "config", None),
                        independent_context=bool(st.ctx.get("_independent_context")),
                    )
                    if st._messages_changed(st.compacted_messages, st.messages):
                        st._adopt_compacted_context(
                            st.compacted_messages, reason="pre_send")
                        st.llm_context, st._pre_inject_chars = st._build_provider_context(st.messages)
                        st.llm_context = self._inject_identity(st.llm_context, st._id_nicks)
                        st.llm_context = self._apply_identity_suffix(
                            st.llm_context, st.ctx.get("_identity_suffix", ""))
                        st.llm_context = st._inject_dynamic_metadata(st.llm_context)
                    st._pre_send_est = self._estimate_tokens(
                        st.llm_context, tool_defs=st.ctx.get("tool_defs"),
                        chars_per_token=st.ctx.get("chars_per_token", 0),
                        token_multiplier=st._ff_tmul)
            if st._pre_send_est > st._max_ctx:
                st._before_force_fit = st._pre_send_est
                logger.warning(
                    f"[compact] STILL OVER ({st._pre_send_est} > {st._max_ctx}), force-fitting...")
                st.llm_context = self._force_fit_context(
                    st.llm_context, st._max_ctx,
                    chars_per_token=st.ctx.get("chars_per_token", 0),
                    tool_defs=st.ctx.get("tool_defs"),
                    token_multiplier=st._ff_tmul)
                st._after_force_fit = self._estimate_tokens(
                    st.llm_context, tool_defs=st.ctx.get("tool_defs"),
                    chars_per_token=st.ctx.get("chars_per_token", 0),
                    token_multiplier=st._ff_tmul)
                if st._after_force_fit < st._before_force_fit and st.conversation_id:
                    try:
                        from core.conversation_event_bus import ConversationEventBus
                        ConversationEventBus.instance().publish_event(
                            st.conversation_id, "compact_progress", {
                                "stage": "done",
                                "agent": st.ctx.get("active_agent_name") or "",
                                "before": len(st.messages),
                                "after": len(st.llm_context),
                                "tokens_before": st._before_force_fit,
                                "tokens_after": st._after_force_fit,
                                "reason": "force_fit",
                            })
                    except Exception:
                        logger.debug("force-fit compact SSE publish failed", exc_info=True)

        # LLM call
        st._tb = st.ctx.get("thinking_budget", 0)
        # `_is_claude_code` historically meant "this provider
        # uses turn_callback to persist content during streaming;
        # do NOT re-append response.content at end of turn".
        # Codex app-server and gemini share that pattern (see
        # `turn_callback=_claude_code_turn_callback if
        # _client_provider in ("claude-code","codex-app-server","gemini")`
        # below). Without including them here, agent_core
        # double-persists at end of turn because turn_callback
        # already pushed everything and response.content is
        # the concatenation of pieces already on disk.
        st._is_claude_code = st._client_provider in (
            "claude-code", "claude-code-interactive",
            "antigravity-interactive", "codex-app-server", "gemini")

        st._cc_turn_count = [0]

        st._release_active_after_terminal_visible_answer = lambda force=False: self._alc_release_active_after_terminal_visible_answer(st, force)

        st._claude_code_turn_callback = lambda text, tool_calls, turn_thinking='': self._alc_claude_code_turn_callback(st, text, tool_calls, turn_thinking)

                    # display_only NOT persisted — _classify_messages_for_display
                    # reconstructs tool_call/tool_result from LLM context messages

        st._apply_queued_delegate_turn_mode = lambda _new_user_msgs: self._alc_apply_queued_delegate_turn_mode(st, _new_user_msgs)

        st._cli_block_callback = lambda event_type, payload: self._alc_cli_block_callback(st, event_type, payload)

        st._llm_call = lambda msgs, ps=st.poll_silent: self._alc_llm_call(st, msgs, ps)

        # Claude-code with existing session: send only the latest
        # user message (session has full context via --resume)
        st._call_context = st.llm_context
        if st._is_claude_code and st.ctx.get("_cli_has_session"):
            st._is_real_user_msg = lambda m: self._alc_is_real_user_msg(st, m)
            st._new_msgs = [m for m in st.llm_context if st._is_real_user_msg(m)]
            if st._new_msgs:
                # Only the latest user message — no system prompt
                # (session already has it from initial context)
                st._call_context = [st._new_msgs[-1]]

        st._provider_response_completed_at = 0.0
        _sig = self._alc_llm_turn(st)
        if _sig is not None:
            return _sig

        st.emitter.check_cancelled()

        # Post-response — mark session as active for next iteration
        if st._is_claude_code and not st.ctx.get("_claude_has_session"):
            try:
                from core.conversation_store import ConversationStore
                st._an = st.ctx["active_agent_name"]
                if ConversationStore.instance().get_extra(
                        st.conversation_id, f"claude_session:{st._an}"):
                    st.ctx["_claude_has_session"] = True
            except Exception:
                logger.debug("exception suppressed", exc_info=True)
        st.total_tokens_in += st.response.tokens_in
        st.total_tokens_out += st.response.tokens_out
        st.total_cache_read += getattr(st.response, 'cache_read_tokens', 0)
        st.total_cache_write += getattr(st.response, 'cache_creation_tokens', 0)
        st._aggregation_usage = (st.response.raw or {}).get(
            "_pawflow_aggregation", {})
        st.ctx["_additional_usage_cost_usd"] = (
            float(st.ctx.get("_additional_usage_cost_usd", 0) or 0)
            + float(st._aggregation_usage.get(
                "advisor_cost_usd_delta", 0) or 0)
        )
        st.final_model = st.response.model
        st.finish_reason = st.response.finish_reason

        # Budget warning at 80%
        st._bud = st.ctx.get("max_budget_usd", 0)
        if st._bud and not st.ctx.get("_budget_warning_sent"):
            st._spent = _usage_cost_usd(
                st.ctx, st.total_tokens_in, st.total_tokens_out,
                st.total_cache_read, st.total_cache_write)
            if st._spent >= st._bud * 0.8:
                st.ctx["_budget_warning_sent"] = True
                st.emitter.bus.publish_event(st.ctx.get("_event_cid", st.conversation_id), "budget_warning", {
                    "spent_usd": round(st._spent, 4),
                    "budget_usd": st._bud,
                    "percent": round(st._spent / st._bud * 100, 1),
                    "agent_name": st.ctx.get("active_agent_name", ""),
                })

        self._deflate_image_messages(
            st.messages, user_id=st.user_id, conversation_id=st.conversation_id)
        # Apply pending background tool results to in-memory messages
        import core.background_tool as _bg_mod
        _apply_bg_results(st.messages, st.conversation_id)

        if st.response.tokens_in > 0:
            st._svc_id = st.ctx.get("active_llm_service") or ""
            self._calibrate_cpt(st._svc_id, st._pre_inject_chars, st.response.tokens_in)
            st.ctx["chars_per_token"] = self._get_cpt(
                st._svc_id, st.ctx.get("chars_per_token", 0))

        # No tools → final response (but wait for bg tasks first)
        if not st.response.tool_calls:
            if _bg_mod.has_pending(st.conversation_id):
                logger.info("[agent:%s] waiting for background tasks before exit",
                            st.conversation_id[:8])
                st.emitter.on_status("Waiting for background tasks...")
                _bg_mod.wait_pending(
                    st.conversation_id,
                    cancel_check=st.emitter.check_cancelled)
                _apply_bg_results(st.messages, st.conversation_id)
                return _ALC_CONTINUE

            st._resp_text = _strip_context_ack(st.response.content or "")
            # Claude-code: turn_callback persisted all content.
            # response.content is "" — don't persist an empty msg.
            # response_content stays "" — done event uses last turn text.
            if st._is_claude_code:
                st.response_content = st._resp_text
                # Patch the persisted turn message with token +
                # context-gauge data (turn_callback persisted it
                # without tokens).
                st._schedule_cc_turn_gauge_patch(
                    st.response, getattr(st.client, '_last_turn_msg_id', ''),
                    "final")
                st._release_active_after_terminal_visible_answer(force=True)
                st.emitter.stop_heartbeat(st._iter_hb)
                return _ALC_BREAK
            st._has_thinking = bool(getattr(st.response, 'thinking', ''))
            # Empty response with thinking = surface the thinking
            # live, then nudge for an actual action/answer. Pure
            # thinking deltas are intentionally not persisted as
            # standalone conversation rows; doing so makes every
            # tiny reasoning chunk rewrite transcript + contexts.
            if not st._resp_text and st._has_thinking:
                logger.warning(f"[agent:{st.conversation_id[:8]}] thinking-only response (no text/tools)")
                st._append(LLMMessage(role="assistant", content="",
                                   thinking=st.response.thinking or "",
                                   thinking_signature=getattr(st.response, "thinking_signature", "") or "",
                                   source=st._agent_source(st.response.tokens_in, st.response.tokens_out,
                                                        tok_cache_creation=st.response.cache_creation_tokens,
                                                        tok_cache_read=st.response.cache_read_tokens),
                                   conversation_id=st.conversation_id))
                if not st._need_more_retried:
                    st._append(LLMMessage(role="user", content=(
                        "[System: You produced reasoning but no visible response or tool calls. "
                                    "You MUST either call a tool or provide a text response to the user. "
                                    "Do not just think — act or respond.]"),
                        conversation_id=st.conversation_id))
                    st._need_more_retried = True
                    return _ALC_CONTINUE
            st._src_no_tools = st._agent_source(st.response.tokens_in, st.response.tokens_out, st.response.model,
                                          tok_cache_creation=st.response.cache_creation_tokens,
                                          tok_cache_read=st.response.cache_read_tokens)
            st.action, st.msgs, st.final, st._need_more_retried = self._handle_response_no_tools(
                st._resp_text, st._client_provider, st.tool_defs,
                st._need_more_retried, source=st._src_no_tools,
                conversation_id=st.conversation_id)
            # Attach thinking to the first assistant message
            st._thinking_txt = st.response.thinking or ""
            st._thinking_sig = getattr(st.response, "thinking_signature", "") or ""
            for st._m in st.msgs:
                if st._m.role == "assistant" and st._thinking_txt:
                    st._m.thinking = st._thinking_txt
                    st._m.thinking_signature = st._thinking_sig
                    st._thinking_txt = ""  # only on the first one
                    st._thinking_sig = ""
                st._append(st._m)
            if st.action == "break":
                st.response_content = st.final
                st._release_active_after_terminal_visible_answer(force=True)
                st.emitter.stop_heartbeat(st._iter_hb)
                return _ALC_BREAK
            return _ALC_CONTINUE

        # Tool calls
        st._need_more_retried = False
        st._append(LLMMessage(
            role="assistant", content=st.response.content,
            tool_calls=st.response.tool_calls,
            thinking=st.response.thinking or "",
            thinking_signature=getattr(st.response, "thinking_signature", "") or "",
            source=st._agent_source(st.response.tokens_in, st.response.tokens_out, st.response.model,
                                 tok_cache_creation=st.response.cache_creation_tokens,
                                 tok_cache_read=st.response.cache_read_tokens),
            conversation_id=st.conversation_id))

        if st.poll_silent and st.response.tool_calls:
            st.poll_silent = False

        st.emitter.on_tool_calls(
            st.response.tool_calls, st.response.content or "",
            st.response.thinking or "", st.poll_silent)
        # Update running agent with tool info
        st._tool_names = [tc.name for tc in st.response.tool_calls]
        st.results = self._execute_tool_calls(
            st.response.tool_calls, st.registry, st._consecutive_tool,
            st._max_consec, parallel=st.emitter.is_streaming,
            agent_name=st.ctx.get("active_agent_name") or "",
            agent_svc=st.ctx.get("active_llm_service", ""),
            conversation_id=st.conversation_id, user_id=st.user_id,
            is_claude_code=st._is_claude_code,
            cancel_check=st.emitter.check_cancelled,
            event_cid=st.ctx.get("_event_cid", ""))

        for st.tc, st.result_text in st.results:
            st.display_tc = self._tool_result_display_call(st.tc)
            st.tools_called.append(st.display_tc.name)
            st.ctx["_last_tool"] = st.display_tc.name
            # schedule_continuation persists its wake-up in the
            # handler itself. Do not also sleep/re-enter inline here;
            # that would duplicate the continuation and would not
            # survive server restarts.
            # Wrap tool output in an untrusted-content envelope so
            # any instructions embedded in file contents, web pages,
            # grep matches, etc. are read as data, not as orders.
            st.result_text = self._materialize_tool_result_images(
                st.result_text, user_id=st.user_id,
                conversation_id=st.conversation_id)
            st._wrapped = self._wrap_tool_output(st.display_tc.name, st.result_text)
            st._tr_msg = LLMMessage(role="tool", content=st._wrapped, tool_call_id=st.tc.id,
                                  conversation_id=st.conversation_id)
            st._tr_msg._tool_name = st.display_tc.name
            st._append(st._tr_msg)
            # Preview for SSE — result_text is raw from the
            # tool executor (wrap above is on _wrapped, not
            # result_text), so no strip is needed.
            st._prev = st.result_text[:2000] if isinstance(st.result_text, str) else str(st.result_text)[:2000]
            st.emitter.on_tool_result(st.display_tc, st.result_text, st._prev)

        # Check only after publishing the whole result batch.
        # Compact, cancel, and preempt paths can interrupt while
        # tools are in-flight; _execute_tool_calls returns
        # placeholder results for the cancelled calls so the UI
        # can close every live technical-details row before this
        # generation exits.
        st.emitter.check_cancelled()

        # Per-turn aggregate cap: if total tool results > 200K chars,
        # persist the largest to FileStore to avoid context bloat
        st._AGG_CAP = 200_000
        st._turn_tool_msgs = [m for m in st.messages if m.role == "tool"
                           and m in st.new_messages]
        st._total_chars = sum(len(m.content) for m in st._turn_tool_msgs
                           if isinstance(m.content, str))
        if st._total_chars > st._AGG_CAP:
            for st.m in sorted(st._turn_tool_msgs,
                            key=lambda x: len(x.content or ''), reverse=True):
                if st._total_chars <= st._AGG_CAP:
                    break
                if isinstance(st.m.content, str) and len(st.m.content) > 5000:
                    from core.file_store import FileStore
                    st.fid = FileStore.instance().store(
                        "tool_result.txt", st.m.content.encode(), "text/plain",
                        user_id=st.user_id or "",
                        conversation_id=st.conversation_id or "")
                    st._saved = len(st.m.content)
                    st.m.content = (
                        f"[Result too large ({st._saved:,} chars) — saved to "
                                    f"fs://filestore/{st.fid}/tool_result.txt. Use "
                                    f"read(path='fs://filestore/{st.fid}/tool_result.txt') to access.]")
                    st._total_chars -= st._saved - len(st.m.content)
            logger.info("[agent:%s] aggregate cap: persisted large tool results to FileStore",
                        st.conversation_id[:8])

        st.emitter.stop_heartbeat(st._iter_hb)  # stop iteration heartbeat
        st.emitter.on_iteration_end(
            st.iteration, st.current_round, st.ctx["max_iterations"],
            st.max_rounds, st.tools_called)
        st.emitter.drain_pending(st.messages, st._append, st.iteration)
        st.emitter.check_cancelled()
        # CCI gauge refresh between tool rounds. For
        # claude-code-interactive the emitter skips the gauge
        # (_context_usage_payload returns None) and the
        # final-turn _patch_cc_turn_gauge only fires once the
        # agent stops. Without this, a long tool-looping run
        # freezes the context gauge at the last completed turn.
        if st._client_provider == "claude-code-interactive":
            st._patch_cc_turn_gauge(
                st.response, getattr(st.client, '_last_turn_msg_id', ''))
        return None

