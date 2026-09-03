"""AgentCoreMixin closures extracted as methods (split for <=800 lines)."""
import copy
import logging
import re
import time

from core.llm_client import (
    LLMMessage,
)
from core.interrupt_policy import SOFT_INTERRUPT_USER_COMMAND
from tasks.ai.agent_exceptions import _InterruptComplete

from tasks.ai._alc_base import (  # noqa: F401
    _strip_context_ack, _preempt_rescue_requires_retrigger, _apply_bg_results,
    _svc_rates, _record_response_usage, _usage_cost_usd, _check_budget,
    _CONTEXT_ACK_PATTERNS)

logger = logging.getLogger(__name__)

# Both DELEGATE MODE hint wordings: the cold-path one appended by
# _agentctx_p3 (ends with "answering '<caller>'.") and the hot-path one
# appended by _alc_apply_queued_delegate_turn_mode (ends with
# "delegate() yourself to answer.").
_DELEGATE_HINT_RE = re.compile(
    r"(?:\n\n)?DELEGATE MODE: .*?"
    r"(?:delegate\(\) yourself to answer\.|answering '[^']*'\.)",
    re.DOTALL)


def _alc_carry_pawflow_attrs(src, dst):
    """Carry dynamic context flags onto a rebuilt message.

    Provider-context builders construct fresh LLMMessage objects and would
    otherwise silently drop the dynamic attributes (e.g.
    _pawflow_current_user_message) that drive the vision fallback — the
    current prompt would lose its image eligibility on every turn.
    """
    for _k, _v in vars(src).items():
        if _k.startswith("_pawflow_"):
            setattr(dst, _k, _v)
    return dst


class _ALCClosures2Mixin:
    def _alc_apply_vision_fallback(self, st, messages, call_kwargs):
        """Apply the active llmConnection's vision fallback before a direct
        LLMClient call.

        The agent loop intentionally calls its per-turn client directly, so it
        bypasses LLMConnectionService.complete[_stream] where this preprocessing
        normally happens.  Delegate back to the resolved service's existing
        fail-open helper without mutating the persisted conversation messages.
        """
        try:
            service = (
                getattr(st, "resolved_svc", None)
                or st.ctx.get("resolved_svc")
            )
            if service is None:
                return messages
            # Historical and unrelated tool images must not even enter the
            # delegated service path. Only the active prompt and current-turn
            # read/see results are eligible.
            from core.vision_describe import has_current_vision_inputs
            if not has_current_vision_inputs(messages):
                return messages
            fallback = getattr(service, "_maybe_apply_vision_fallback", None)
            if fallback:
                result = fallback(messages, call_kwargs)
                if result is not messages:
                    logger.info(
                        "[agent-loop] vision fallback applied via '%s' — "
                        "messages transformed",
                        getattr(service, "_service_id", "") or type(service).__name__)
                else:
                    logger.info(
                        "[agent-loop] vision fallback returned messages unchanged "
                        "(supports_vision=%s, vision_llm_service=%s)",
                        getattr(service, "config", {}).get("supports_vision", "(unset)"),
                        getattr(service, "config", {}).get("vision_llm_service", "(unset)"))
                return result
            logger.debug(
                "[agent-loop] resolved service has no _maybe_apply_vision_fallback: %s",
                type(service).__name__)
        except Exception:
            logger.warning(
                "agent-loop vision fallback pre-processing failed",
                exc_info=True)
        return messages

    def _alc_with_provider_system_prompt(self, st, stored_msgs):
        prompt = st.ctx.get("_provider_system_prompt", "") or ""
        out = list(stored_msgs)
        if not prompt:
            return out
        if st.ctx.get("_is_cli_provider") and st.ctx.get("_cli_has_session"):
            return out
        sys_msg = LLMMessage(
            role="system", content=prompt,
            source={"type": "provider_prompt"},
            conversation_id=st.conversation_id)
        if out and out[0].role == "system":
            out[0] = sys_msg
        else:
            out.insert(0, sys_msg)
        return out

    def _alc_build_provider_context(self, st, stored_msgs):
        provider_context = st._with_provider_system_prompt(list(stored_msgs))
        pre_inject_chars = self._estimate_tokens(
            provider_context, tool_defs=st.tool_defs, chars_per_token=1.0)
        return provider_context, pre_inject_chars

    def _alc_inject_dynamic_metadata(self, st, provider_context):
        # Single source of truth: the "Context: ~x/y" note the LLM sees must be
        # the number the UI gauge and the compact check use. Two different
        # quantities answer "how full is the context", and which one is right
        # depends on the provider:
        #
        #   * API providers -- PawFlow resends the whole context on every call,
        #     so provider_context (system prompt already in head position) plus
        #     the tool defs IS the occupancy. Unchanged.
        #   * CLI providers -- the window belongs to the CLI session, not to
        #     PawFlow. On a warm session _alc_with_provider_system_prompt drops
        #     the system prompt and the history the CLI already holds, so
        #     provider_context is only this turn's delta. Counting it announced
        #     864 tokens for a session the gauge measured at 77965, and the
        #     same conversation announced 602307 one restart earlier while
        #     cold: the note silently switched quantity with the session state.
        #     Ask the gauge, which reports the size the provider itself
        #     measured, and divide by the window it measured against.
        _est_used_local = 0
        _max_ctx_local = int(getattr(st, "_max_ctx", 0) or 0)
        if st.ctx.get("_is_cli_provider"):
            try:
                from tasks.ai.context_usage import compute_context_usage
                _gauge_local = compute_context_usage(
                    st.conversation_id,
                    st.ctx.get("active_agent_name", ""),
                    user_id=getattr(st, "user_id", ""), source="context_note")
                _est_used_local = max(0, int(_gauge_local.get("used", 0) or 0))
                if int(_gauge_local.get("max", 0) or 0) > 0:
                    _max_ctx_local = int(_gauge_local.get("max", 0) or 0)
            except Exception:
                logger.debug("context note gauge lookup failed", exc_info=True)
                _est_used_local = 0
        # API providers, and any CLI session the gauge cannot measure yet (cold
        # start, provider that has not reported): counting provider_context is
        # then the honest answer, because it carries the whole context.
        if _est_used_local <= 0:
            try:
                from core.token_counter import (
                    count_context_tokens, resolve_token_multiplier)
                _est_used_local = count_context_tokens(
                    provider_context, tool_defs=st.tool_defs,
                    multiplier=resolve_token_multiplier(
                        getattr(st.ctx.get("resolved_svc"), "config", None)
                        or {}))
            except Exception:
                _est_used_local = self._estimate_tokens(
                    provider_context, tool_defs=st.tool_defs,
                    chars_per_token=st.ctx.get("chars_per_token", 0))
        # Denominator stays local to the note: st._max_ctx also budgets the
        # compaction path, and moving it here would change what gets compacted.
        _remaining_local = max(0, _max_ctx_local - _est_used_local)
        _meta_parts_local = []
        if st.ctx.get("_datetime_str", ""):
            _meta_parts_local.append(
                f"Current date/time: {st.ctx.get('_datetime_str', '')}")
        _meta_parts_local.append(
            f"Context: ~{_est_used_local}/{_max_ctx_local} tokens "
                            f"(~{_remaining_local} remaining)")
        _meta_note_local = "\n\n[System: " + ". ".join(_meta_parts_local) + "]"
        # Cognitive digests ride the same channel: they are rebuilt from live
        # stores every turn, so putting them in the system prompt would move
        # the cached prefix on any remember/diary_write/kg_add and invalidate
        # the whole KV cache behind it.
        for _title, _body in (st.ctx.get("_dynamic_blocks") or []):
            if _body:
                _meta_note_local += f"\n\n## {_title}\n{_body}"
        for _mi in range(len(provider_context) - 1, -1, -1):
            if provider_context[_mi].role == "user":
                _um = provider_context[_mi]
                if isinstance(_um.content, list):
                    _new_content = list(_um.content) + [
                        {"type": "text", "text": _meta_note_local}]
                    _rebuilt = LLMMessage(
                        role="user", content=_new_content,
                        tool_calls=_um.tool_calls, tool_call_id=_um.tool_call_id,
                        source=_um.source, msg_id=_um.msg_id,
                        timestamp=_um.timestamp, seq=_um.seq,
                        conversation_id=st.conversation_id,
                    )
                else:
                    _uc = _um.content or ""
                    _rebuilt = LLMMessage(
                        role="user", content=_uc + _meta_note_local,
                        tool_calls=_um.tool_calls, tool_call_id=_um.tool_call_id,
                        source=_um.source, msg_id=_um.msg_id,
                        timestamp=_um.timestamp, seq=_um.seq,
                        conversation_id=st.conversation_id,
                    )
                # Rebuilds must keep the dynamic flags (e.g.
                # _pawflow_current_user_message) or the vision fallback
                # silently loses the current prompt's image eligibility.
                provider_context[_mi] = _alc_carry_pawflow_attrs(_um, _rebuilt)
                break
        return provider_context

    def _alc_threshold_estimate(self, st, stored_msgs, cpt):
        from core.token_counter import resolve_token_multiplier as _rtm
        _tmul = _rtm(getattr(
            st.ctx.get("resolved_svc"), "config", None))
        return self._estimate_tokens(
            st._with_provider_system_prompt(list(stored_msgs or [])),
            tool_defs=st.tool_defs,
            chars_per_token=cpt,
            token_multiplier=_tmul)

    def _alc_cold_start_trigger_fraction(self, st):
        """Return the configured proactive-compaction threshold unchanged."""
        return st._trigger_frac

    def _alc_should_proactive_compact(self, st, stored_msgs, max_ctx, cpt):
        # Must agree with what _compact is handed, or the decision made here
        # is re-taken there against a different bar and silently reversed.
        frac = st._cold_start_trigger_fraction()
        if frac <= 0:
            return False
        trigger_tokens = int(max_ctx * frac)
        if trigger_tokens <= 0:
            return False
        used_tokens = st._threshold_estimate(stored_msgs, cpt)
        return used_tokens >= trigger_tokens

    def _alc_messages_changed(self, st, candidate, current):
        if not candidate:
            return False
        try:
            return self._serialize_messages(candidate) != self._serialize_messages(current)
        except Exception:
            if len(candidate) != len(current):
                return True
            for left, right in zip(candidate, current):
                if (getattr(left, "role", None) != getattr(right, "role", None)
                        or getattr(left, "content", None) != getattr(right, "content", None)
                        or getattr(left, "tool_calls", None) != getattr(right, "tool_calls", None)
                        or getattr(left, "tool_call_id", None) != getattr(right, "tool_call_id", None)):
                    return True
            return False

    def _alc_adopt_compacted_context(self, st, compacted_messages, *, reason: str, async_cleanup: bool = False, already_persisted: bool = False):
        """Replace the active PawFlow agent context after any compact."""
        compacted_list = list(compacted_messages or [])
        st.messages[:] = compacted_list
        st.ctx["messages"] = st.messages
        st.ctx["_base_message_count"] = len(st.messages)
        st.base_count = len(st.messages)
        st.new_messages.clear()
        st.ctx.pop("_context_usage_cache", None)
        st.ctx.pop("_auto_compact_usage_cache", None)
        _adopt_agent = st.ctx.get("active_agent_name") or ""
        if st.use_conv_store and st.conversation_id and _adopt_agent:
            try:
                from core.conversation_store import ConversationStore
                _adopt_store = ConversationStore.instance()
                if not already_persisted:
                    _adopt_store.save_agent_context(
                        st.conversation_id, _adopt_agent,
                        self._serialize_messages(st.messages))
                if st.ctx.get("_is_cli_provider"):
                    _adopt_store.invalidate_claude_session_for_agent(
                        st.conversation_id, _adopt_agent,
                        async_cleanup=async_cleanup)
                    st.ctx["_cli_has_session"] = False
                    st.ctx["_claude_has_session"] = False
            except Exception as _adopt_err:
                logger.warning(
                    "[agent:%s] adopt compacted context failed (%s): %s",
                    st.conversation_id[:8], reason, _adopt_err,
                    exc_info=True)
                raise
        logger.info(
            "[agent:%s] adopted compacted PawFlow context for %s: %d messages (%s)",
            st.conversation_id[:8], _adopt_agent, len(st.messages), reason)

    def _alc_run_interrupt_turn(self, st):
        if st._client_provider in ("claude-code-interactive", "antigravity-interactive", "codex-interactive", "cc_mcp", "codex_mcp", "agy_mcp"):
            logger.info(
                "[agent:%s] interrupted — sending %s STOP via tmux only",
                st.conversation_id[:8], st._client_provider)
            _turn_cb = getattr(st, "_claude_code_turn_callback", None)
            _block_cb = getattr(st, "_cli_block_callback", None)
            if st._client_provider == "antigravity-interactive":
                _interrupt_fn = st.client.interrupt_antigravity_interactive
            elif st._client_provider == "codex-interactive":
                _interrupt_fn = st.client.interrupt_codex_interactive
            elif st._client_provider in ("cc_mcp", "codex_mcp", "agy_mcp"):
                _interrupt_fn = getattr(
                    st.client, f"interrupt_{st._client_provider}")
            else:
                _interrupt_fn = st.client.interrupt_claude_code_interactive
            _irpt_resp = _interrupt_fn(
                SOFT_INTERRUPT_USER_COMMAND,
                user_id=st.user_id,
                conversation_id=st.conversation_id,
                agent_name=st.ctx.get("active_agent_name", ""),
                model=st.model or None,
                callback=st.emitter.get_token_callback(False) if st.emitter.is_streaming else None,
                thinking_callback=st.emitter.get_thinking_callback(False),
                turn_callback=_turn_cb,
                block_callback=_block_cb,
            )
            _irpt_mid = getattr(st.client, '_last_turn_msg_id', '')
            if _turn_cb is None and (_irpt_resp.content or "").strip():
                _irpt_msg = LLMMessage(
                    role="assistant", content=_irpt_resp.content,
                    source=st._agent_source(),
                    conversation_id=st.conversation_id)
                st._append(_irpt_msg)
                _irpt_mid = getattr(_irpt_msg, 'msg_id', '')
            st.response_content = _irpt_resp.content
            _record_response_usage(st, _irpt_resp)
            st.final_model = _irpt_resp.model
            # Refresh the context gauge from the provider's
            # reported usage for the interrupted CCI turn.
            st._schedule_cc_turn_gauge_patch(
                _irpt_resp, _irpt_mid, "interrupt")
            raise _InterruptComplete()

        logger.info(f"[agent:{st.conversation_id[:8]}] interrupted — injecting user STOP command")
        st._append(LLMMessage(
            role="user",
            content=SOFT_INTERRUPT_USER_COMMAND,
            source={"type": "user", "interrupt": True},
            conversation_id=st.conversation_id,
        ))
        _interrupt_call_kwargs = {
            "call_user_id": st.user_id,
            "call_conversation_id": st.conversation_id,
            "call_agent_name": st.ctx.get("active_agent_name", ""),
            "call_event_cid": st.ctx.get("_event_cid", st.conversation_id),
            "call_ephemeral_stream": False,
            "call_is_initial_user_turn": st.iteration == 1,
        }
        _interrupt_messages = st._with_provider_system_prompt(self._compact(
            copy.deepcopy(st.messages), st.compact_client,
            st.ctx.get("max_context_size", 64000),
            target_fraction=0.25,
            conversation_id=st.conversation_id,
            agent_name=st.ctx.get("active_agent_name") or "",
            user_id=st.user_id,
            budget_config=getattr(st.ctx.get("resolved_svc"), "config", None),
            independent_context=bool(st.ctx.get("_independent_context"))))
        _interrupt_messages = self._alc_apply_vision_fallback(
            st, _interrupt_messages, _interrupt_call_kwargs)
        _irpt_resp = st.client.complete_stream(
            messages=_interrupt_messages,
            model=st.model or None,
            temperature=st.ctx["temperature"],
            max_tokens=st.ctx["max_tokens"],
            tools=None,
            callback=st.emitter.get_token_callback(False),
            **_interrupt_call_kwargs,
        ) if st.emitter.is_streaming else st.client.complete(
            messages=_interrupt_messages,
            model=st.model or None,
            temperature=st.ctx["temperature"],
            max_tokens=st.ctx["max_tokens"],
            tools=None,
            **_interrupt_call_kwargs,
        )
        st._append(LLMMessage(
            role="assistant", content=_irpt_resp.content,
            source=st._agent_source(),
            conversation_id=st.conversation_id))
        st.response_content = _irpt_resp.content
        _record_response_usage(st, _irpt_resp)
        st.final_model = _irpt_resp.model
        raise _InterruptComplete()

    def _alc_release_active_after_terminal_visible_answer(self, st, force: bool = False):
        if (not force and not getattr(
                st.client, "_codex_app_turn_completed_for_callback", False)):
            return
        if st.ctx.get("_active_cleanup_done"):
            return
        _ctx_key_done = st.ctx.get("_active_context_key")
        if _ctx_key_done:
            with self._active_contexts_lock:
                if self._active_contexts.get(_ctx_key_done) is st.ctx:
                    self._active_contexts.pop(_ctx_key_done, None)
        self._decrement_active(st.conversation_id, st.ctx)
        st.client._codex_app_turn_completed_for_callback = False
        try:
            from core.conversation_writer import ConversationWriter
            ConversationWriter.for_conversation(
                st.conversation_id).enqueue_sse_events([{
                    "type": "active_released",
                    "cid": st.ctx.get("_event_cid", st.conversation_id),
                    "data": {
                        "conversation_id": st.conversation_id,
                        "agent_name": st.ctx.get("active_agent_name", ""),
                        "turn_id": st.ctx.get("request_msg_id", ""),
                        "request_msg_id": st.ctx.get("request_msg_id", ""),
                    },
                }])
        except Exception:
            logger.debug("active_released enqueue failed", exc_info=True)
        logger.info(
            "[agent:%s] active released after terminal visible answer agent=%s",
            st.conversation_id[:8],
            st.ctx.get("active_agent_name", ""))

    def _alc_claude_code_turn_callback(self, st, text, tool_calls, turn_thinking=''):
        logger.info(
            "[cc-callback] IN text=%d tc=%d thinking=%d",
            len(text) if text else 0,
            len(tool_calls) if tool_calls else 0,
            len(turn_thinking) if turn_thinking else 0)
        """Called by claude-code at each internal turn boundary.

                        IMMUTABLE RULE: stream block → LLMMessage → writer →
                        transcript + shared + contexts → SSE (post-write).
                        This callback is where the rule lands for CC: every
                        block flushed by _flush_turn becomes an LLMMessage
                        and goes through _append, which enqueues the message
                        on ConversationWriter with the matching sse_events
                        (new_message / tool_call / tool_result /
                        thinking_content). Nothing in claude_code.py's stream
                        loop is allowed to publish SSE for message-level
                        content anymore — only status events (heartbeat,
                        turn_complete, message_meta) still fire live.

                        Signature: (text, tool_calls[, turn_thinking]). The
                        3rd arg is present so that thinking emitted on a
                        text-only turn (no tool_use blocks) still reaches
                        the writer; _flush_turn handles the back-compat
                        inspection for any legacy 2-arg callback.
                        """
        from core.llm_client import LLMToolCall

        st.emitter.check_cancelled()
        st._cc_turn_count[0] += 1
        st.ctx["_iteration"] = st._cc_turn_count[0]

        _bus = st.emitter.bus
        _cid = st.ctx.get("_event_cid", st.conversation_id)
        turn_msgs = []
        _src = st._agent_source(include_context=False)
        _agent = _src.get("name", "")

        # display_only messages are NOT persisted in the transcript.
        # The transcript contains LLM context messages (assistant, tool)
        # and _classify_messages_for_display reconstructs the visual
        # representation (tool_call, tool_result, thinking) from them.
        # Persisting display_only would create duplicates at reload.

        # Strip context-ack echoes the LLM may produce after compaction
        _raw_len = len(text) if text else 0
        _had_text = bool(text and text.strip())
        text = _strip_context_ack(text)
        _post_len = len(text) if text else 0
        if _raw_len and _post_len != _raw_len:
            # Loud log so a future lost-message investigation can
            # tell whether the strip pass swallowed real content
            # (raw>0, post=0) vs. just trimmed the ack prefix.
            logger.info(
                "[cc-callback] _strip_context_ack: raw=%d post=%d (stripped=%d) %s",
                _raw_len, _post_len, _raw_len - _post_len,
                "DROPPED-ENTIRELY" if _post_len == 0 else "prefix-trimmed")

        # Thinking is carried on the tool_call message's
        # `thinking` field when tool_calls exist
        # (_flush_turn attaches it to tc[0]). When the turn
        # is text-only, attach thinking to the assistant
        # text message so it still reaches transcript +
        # context and the thinking_content SSE fires from
        # _append.
        _text_thinking = turn_thinking if (not tool_calls) else ""
        if text:
            _text_src = _src
            if tool_calls:
                _text_src = {
                    **_src,
                    "delegate_visibility": "self_only",
                }
            msg = LLMMessage(
                role="assistant", content=text,
                thinking=_text_thinking,
                source=_text_src,
                conversation_id=st.conversation_id)
            st._append(msg)  # persists immediately + publishes new_message (+ thinking_content)
            turn_msgs.append(msg)
            st.client._last_turn_msg_id = getattr(msg, "msg_id", "")
            # Only a message with visible content may be named the turn's
            # final answer: a later thinking-only flush overwrites
            # _last_turn_msg_id, and naming that row final made the
            # turn_final patch miss and the UI hoist an empty answer.
            st.client._last_turn_visible_msg_id = getattr(msg, "msg_id", "")
            if not tool_calls:
                st._release_active_after_terminal_visible_answer()
        elif _text_thinking:
            # Thinking without text and without tool_calls —
            # rare but valid (CC emits a thinking block
            # then yields). Persist a standalone assistant
            # message whose only payload is thinking so
            # the reasoning survives reload + context.
            msg = LLMMessage(
                role="assistant", content="",
                thinking=_text_thinking,
                source=_src,
                conversation_id=st.conversation_id)
            st._append(msg)
            turn_msgs.append(msg)
            st.client._last_turn_msg_id = getattr(msg, "msg_id", "")

        # Finalize streaming element — next turn creates a new one
        # If text was suppressed (context-ack), still send turn_complete
        # with suppress=true so the frontend removes the streaming element.
        _suppressed = _had_text and not text
        if text or tool_calls or _suppressed:
            # Estimate tokens from text length (real values come in done)
            _cpt = st.ctx.get("chars_per_token", 0) or 3.5
            _est_out = int(len(text) / _cpt) if text else 0
            _tc_evt = {
                "agent_name": _agent,
                "msg_id": st.client._last_turn_msg_id if text else "",
                "source": _src,
                "model": _src.get("model", ""),
                "provider": _src.get("provider", ""),
                "tokens_out": _est_out,
            }
            if _suppressed:
                _tc_evt["suppress"] = True
            _bus.publish_event(_cid, "turn_complete", _tc_evt)

        if tool_calls:
            # Extract thinking from first tool_call (claude-code bundles it there)
            _thinking_text = tool_calls[0].get("thinking", "") if tool_calls else ""

            # Unwrap MCP wrapper BEFORE building LLMToolCall so
            # the persisted transcript carries the inner tool name
            # (Read/Grep/...) instead of raw mcp__pawflow__use_tool.
            # _classify_messages_for_display reads these entries
            # directly -- without unwrap here, reloads and post-turn
            # renders show use_tool(tool_name=..., arguments=[object Object])
            # even though live SSE (claude_code.py:1220) was clean.
            from core.llm_client import (
                has_complete_mcp_tool_call, is_mcp_tool_call_name,
                unwrap_mcp_tool)
            tc_objects = []
            for tc in tool_calls:
                _raw_name = tc.get("name", "")
                _raw_args = tc.get("arguments", {})
                if not has_complete_mcp_tool_call(_raw_name, _raw_args):
                    continue
                _inner_name, _inner_args = unwrap_mcp_tool(_raw_name, _raw_args)
                _tool_origin = tc.get("tool_origin", "") or ""
                if not _tool_origin and is_mcp_tool_call_name(_raw_name):
                    _tool_origin = "mcp"
                tc_objects.append(LLMToolCall(
                    id=tc.get("id", ""),
                    name=_inner_name,
                    arguments=_inner_args,
                    tool_origin=_tool_origin,
                ))
            for tc_obj in tc_objects:
                st.tools_called.append(tc_obj.name)
                st.ctx["_last_tool"] = tc_obj.name

            # Tool call message (in LLM context, includes thinking)
            tc_msg = LLMMessage(
                role="assistant", content="",
                tool_calls=tc_objects, thinking=_thinking_text,
                source=_src,
                conversation_id=st.conversation_id)
            st._append(tc_msg)
            turn_msgs.append(tc_msg)

            for i, tc_obj in enumerate(tc_objects):
                tc_raw = tool_calls[i] if i < len(tool_calls) else {}
                _result = tc_raw.get("result") or ""

                from core.llm_client import unwrap_mcp_tool
                _display_name, _display_args = unwrap_mcp_tool(
                    tc_obj.name, tc_obj.arguments)

                # Tool result (in LLM context) — wrap as
                # untrusted content before persisting.
                tr_content = _result or "(no output)"
                _tool_max = int(getattr(st, "_tool_max", 0) or 50000)
                # Same image exemption as the ToolRegistry cap: truncating a
                # string that carries an inline image payload would cut the
                # base64 mid-stream and destroy the image.
                if (isinstance(tr_content, str) and len(tr_content) > _tool_max
                        and "__image_data__:" not in tr_content):
                    tr_content = (tr_content[:_tool_max]
                                  + f"\n\n[... truncated — {len(tr_content):,} chars total]")
                tr_content = self._materialize_tool_result_images(
                    tr_content, user_id=st.user_id,
                    conversation_id=st.conversation_id)
                tr_content = self._wrap_tool_output(_display_name, tr_content)
                tr_msg = LLMMessage(
                    role="tool", content=tr_content,
                    tool_call_id=tc_obj.id,
                    conversation_id=st.conversation_id)
                tr_msg._tool_name = _display_name
                st._append(tr_msg)
                turn_msgs.append(tr_msg)

    def _alc_apply_queued_delegate_turn_mode(self, st, _new_user_msgs):
        """Recompute the next turn's mode from the drained pending batch.

        The end-of-turn pending drain happens without a new
        _prepare_agent_context() call, so the trigger-site rule of
        _agentctx_p3 is replayed here on the whole batch. Precedence:

        1. Any human message in the batch wins: the next turn is a plain
           user turn, visible in the chat. While an agent answers a delegator
           every webchat message is queued with "mode mismatch"; letting the
           newest queued delegate broadcast re-select delegate_reply routed
           the agent's replies privately to the delegator and queued the
           user's next messages again, starving the user until a force stop.
        2. Newest external request (a2a / cross-conversation) → isolated
           external_request turn.
        3. Newest delegate request (kind != reply) → delegate_reply turn.
        4. Otherwise a user turn. The previous turn's mode is never
           inherited: a batch of delegate replies is a user turn, exactly as
           on the cold path.

        Returns True when a non-user mode was installed.
        """
        _delegate_src = None
        _external_src = None
        _human_msg = False
        for _m in reversed(_new_user_msgs or []):
            _src = getattr(_m, "source", None) or {}
            _src_type = _src.get("type") if isinstance(_src, dict) else None
            if _src_type in {"a2a", "cross_conversation_delegate"}:
                if _external_src is None:
                    _external_src = _src
                continue
            if _src_type == "agent_delegate":
                if (_delegate_src is None and _src.get("kind") != "reply"
                        and _src.get("from")):
                    _delegate_src = _src
                continue
            _human_msg = True
        if _human_msg or (not _external_src and not _delegate_src):
            _previous = (st.ctx.get("_turn_mode") or {}).get("type", "user")
            st.ctx["_turn_mode"] = {"type": "user", "source_agent": None}
            self._alc_strip_delegate_hint(st)
            if _previous != "user":
                logger.info(
                    "[agent:%s] queued %s message resets next turn mode: "
                    "user turn (was %s)",
                    st.conversation_id[:8],
                    "human" if _human_msg else "delegate reply", _previous)
            return False
        if _external_src:
            st.ctx["_turn_mode"] = {
                "type": "external_request",
                "source_agent": _external_src.get("task_id", ""),
                "source": dict(_external_src),
            }
            self._alc_strip_delegate_hint(st)
            logger.info(
                "[agent:%s] queued external request sets isolated next turn mode",
                st.conversation_id[:8])
            return True
        _caller = _delegate_src.get("from", "") or ""
        st.ctx["_turn_mode"] = {
            "type": "delegate_reply",
            "source_agent": _caller,
            "task_id": _delegate_src.get("task_id", ""),
            "external_transport": _delegate_src.get(
                "external_transport", ""),
            "external_call_id": _delegate_src.get(
                "external_call_id", ""),
            "source_label": _delegate_src.get("from_label", ""),
        }
        _caller_label = (
            _delegate_src.get("from_label", "") or _caller)
        _hint = (
            "\n\nDELEGATE MODE: '" + _caller_label + "' is "
                            "waiting for your answer. Write your response as "
                            "normal text; it will be routed back to '" + _caller_label + "' "
                            "automatically as a private reply. Do NOT call "
                            "delegate() yourself to answer."
        )
        for _idx, _m in enumerate(st.messages):
            if getattr(_m, "role", "") == "system":
                if "DELEGATE MODE:" not in (_m.content or ""):
                    st.messages[_idx] = LLMMessage(
                        role="system",
                        content=(_m.content or "") + _hint,
                        conversation_id=st.conversation_id)
                break
        else:
            st.messages.insert(0, LLMMessage(
                role="system", content=_hint.strip(),
                conversation_id=st.conversation_id))
        logger.info(
            "[agent:%s] queued delegate message sets next turn mode: reply to %s",
            st.conversation_id[:8], _caller)
        return True

    def _alc_strip_delegate_hint(self, st):
        """Remove a DELEGATE MODE hint left in the system message by an
        earlier delegate_reply turn (cold-path _agentctx_p3 wording or the
        hot-path wording above). A user turn must not tell the agent that a
        delegator is waiting for a private reply."""
        for _idx, _m in enumerate(st.messages):
            if getattr(_m, "role", "") != "system":
                continue
            _content = _m.content or ""
            if "DELEGATE MODE:" not in _content:
                return
            _stripped = _DELEGATE_HINT_RE.sub("", _content)
            if _stripped != _content:
                st.messages[_idx] = LLMMessage(
                    role="system", content=_stripped,
                    conversation_id=st.conversation_id)
            return


    def _alc_cli_block_callback(self, st, event_type, payload):
        """Persist one live CLI tool block through the writer.

                        Codex emits item.started and item.completed for the
                        same item. Waiting for turn_callback bundles the
                        tool_call with the result, leaving no BG/Kill window.
                        This callback keeps the visible=>persisted invariant by
                        routing each live block through _append/ConversationWriter.
                        """
        from core.llm_client import (
            LLMToolCall, has_complete_mcp_tool_call,
            is_mcp_tool_call_name, unwrap_mcp_tool)

        _src = st._agent_source(include_context=False)
        if event_type == "text":
            _text = payload.get("text", "") or ""
            if not _text.strip():
                return
            msg = LLMMessage(
                role="assistant", content=_text,
                source=_src,
                conversation_id=st.conversation_id)
            st._append(msg)
            st.client._last_turn_msg_id = getattr(msg, "msg_id", "")
            # See the turn-flush twin above: only visible content may be
            # named the turn's final answer.
            st.client._last_turn_visible_msg_id = getattr(msg, "msg_id", "")
            return

        if event_type in ("thinking", "thinking_content"):
            _thinking = (
                payload.get("thinking", "")
                or payload.get("text", "")
                or "")
            if not _thinking.strip():
                return
            msg = LLMMessage(
                role="assistant", content="",
                thinking=_thinking,
                source=_src,
                conversation_id=st.conversation_id)
            st._append(msg)
            st.client._last_turn_msg_id = getattr(msg, "msg_id", "")
            return

        if event_type == "tool_use":
            _raw_name = payload.get("name", "")
            _raw_args = payload.get("arguments", {}) or {}
            if not has_complete_mcp_tool_call(_raw_name, _raw_args):
                return
            _tool_name, _tool_args = unwrap_mcp_tool(_raw_name, _raw_args)
            _tool_origin = payload.get("tool_origin", "") or ""
            if not _tool_origin and is_mcp_tool_call_name(_raw_name):
                _tool_origin = "mcp"
            tc_obj = LLMToolCall(
                id=payload.get("id", ""),
                name=_tool_name,
                arguments=_tool_args,
                tool_origin=_tool_origin,
            )
            _tool_src = _src
            try:
                from tasks.ai.context_usage_cache import (
                    _is_cli_bootstrap_read)
                if _is_cli_bootstrap_read(tc_obj):
                    # Every read of the bootstrap file is tracked, not just
                    # the first: the agent pages through a file that exceeds
                    # the native read ceiling, and each page carries another
                    # slice of a context the gauge already counts as
                    # messages. The linked results are flagged below.
                    _ids = st.ctx.get("_cli_bootstrap_read_call_ids")
                    if not isinstance(_ids, set):
                        _ids = set()
                        st.ctx["_cli_bootstrap_read_call_ids"] = _ids
                    if tc_obj.id:
                        _ids.add(str(tc_obj.id))
                    if (not st.ctx.get("_cli_has_session")
                            and not st.ctx.get("_cli_bootstrap_read_seen")):
                        st.ctx["_cli_bootstrap_read_seen"] = True
                        _tool_src = dict(_src)
                        _tool_src["context_usage_boundary"] = (
                            "cli_bootstrap_read")
            except Exception:
                logger.debug(
                    "CLI bootstrap context boundary detection failed",
                    exc_info=True)
            st.tools_called.append(tc_obj.name)
            st.ctx["_last_tool"] = tc_obj.name
            msg = LLMMessage(
                role="assistant", content="",
                tool_calls=[tc_obj],
                thinking=payload.get("thinking", "") or "",
                source=_tool_src,
                conversation_id=st.conversation_id)
            st._append(msg)
            return

        if event_type == "tool_result":
            _tool_name = payload.get("tool", "") or ""
            _result = payload.get("result", "") or "(no output)"
            _tool_max = int(getattr(st, "_tool_max", 0) or 50000)
            # Same image exemption as the ToolRegistry cap: truncating a
            # string that carries an inline image payload would cut the
            # base64 mid-stream and destroy the image.
            if (isinstance(_result, str) and len(_result) > _tool_max
                    and "__image_data__:" not in _result):
                _result = (_result[:_tool_max]
                           + f"\n\n[... truncated — {len(_result):,} chars total]")
            _result = self._materialize_tool_result_images(
                _result, user_id=st.user_id,
                conversation_id=st.conversation_id)
            _tc_id = payload.get("tc_id", "")
            _result_src = None
            _bootstrap_ids = st.ctx.get("_cli_bootstrap_read_call_ids")
            if (isinstance(_bootstrap_ids, set) and _tc_id
                    and str(_tc_id) in _bootstrap_ids):
                # This body IS the serialized PawFlow context. It is persisted
                # like any other result -- the transcript must show what the
                # agent did -- but the gauge must not charge the same bytes
                # twice, so it travels with a flag every counting path can
                # read off the message itself, including the single-message
                # append delta that has no list to correlate against.
                _result_src = {"context_usage_bootstrap_body": True}
            msg = LLMMessage(
                role="tool",
                content=self._wrap_tool_output(_tool_name, _result),
                tool_call_id=_tc_id,
                source=_result_src,
                conversation_id=st.conversation_id)
            msg._tool_name = _tool_name
            if payload.get("tool_origin"):
                msg._tool_origin = payload.get("tool_origin")
            st._append(msg)

    def _alc_llm_call(self, st, msgs, ps):
        from core.observability import span
        _call_kwargs = {
            "call_user_id": st.user_id,
            "call_conversation_id": st.conversation_id,
            "call_agent_name": st.ctx.get("active_agent_name", ""),
            "call_event_cid": st.ctx.get("_event_cid", st.conversation_id),
            "call_ephemeral_stream": False,
        }
        msgs = self._alc_apply_vision_fallback(st, msgs, _call_kwargs)
        # The provider boundary: the only step of a turn that leaves the
        # process, and the one whose wall-clock the logs cannot attribute on
        # their own -- a slow turn is either this or the tools, and nothing
        # else distinguishes them after the fact.
        # Every attribute is read defensively, for the same reason as the
        # iteration span: a span describes the call, it must never be the
        # reason the call fails.
        with span("agent.llm_call",
                  **{"pawflow.provider": getattr(
                         st, "_client_provider", "") or "",
                     "pawflow.model": str(getattr(st, "model", "") or ""),
                     "pawflow.streaming": bool(getattr(
                         st.emitter, "is_streaming", False)),
                     "pawflow.conversation_id": getattr(
                         st, "conversation_id", "") or "",
                     "pawflow.tools": len(getattr(st, "tool_defs", None) or [])}):
            return self._alc_llm_call_dispatch(st, msgs, ps, _call_kwargs)

    def _alc_llm_call_dispatch(self, st, msgs, ps, _call_kwargs):
        if st.emitter.is_streaming:
            return st.client.complete_stream(
                messages=msgs, model=st.model or None,
                temperature=st.ctx["temperature"], max_tokens=st.ctx["max_tokens"],
                tools=st.tool_defs if st.tool_defs else None,
                callback=st.emitter.get_token_callback(ps),
                thinking_budget=st._tb,
                # Compatible endpoints may emit reasoning deltas even when
                # PawFlow did not request an explicit token budget. Always
                # provide the preview callback; unused callbacks are inert.
                thinking_callback=st.emitter.get_thinking_callback(ps),
                turn_callback=st._claude_code_turn_callback if st._client_provider in ("claude-code", "claude-code-interactive", "antigravity-interactive", "codex-app-server", "codex-interactive", "gemini", "cc_mcp", "codex_mcp", "agy_mcp") else None,
                block_callback=st._cli_block_callback if st._client_provider in ("claude-code", "claude-code-interactive", "antigravity-interactive", "codex-app-server", "codex-interactive", "gemini", "cc_mcp", "codex_mcp", "agy_mcp") else None,
                **_call_kwargs)
        return st.client.complete(
            messages=msgs, model=st.model or None,
            temperature=st.ctx["temperature"], max_tokens=st.ctx["max_tokens"],
            tools=st.tool_defs if st.tool_defs else None, thinking_budget=st._tb,
            **_call_kwargs)

    def _alc_is_error_message(self, st, m: LLMMessage):
        if not st._err_text:
            return False
        content = (m.content or "").strip()
        return bool(content) and (
            content == st._err_text
            or content.startswith(st._err_text)
            or st._err_text in content
        )

    def _alc_commit_turn_bg(self, st):
        from core.conversation_git import commit_turn
        _commit_t0 = time.monotonic()
        try:
            commit_turn(st.conversation_id, reason=st._commit_reason)
        finally:
            logger.info(
                "[agent:%s] async commit_turn finished agent=%s elapsed_ms=%.1f",
                st.conversation_id[:8], st._agent_tag,
                (time.monotonic() - _commit_t0) * 1000.0)

    def _alc_is_real_user_msg(self, st, m):
        if m.role != "user":
            return False
        c = m.content
        if isinstance(c, list):
            return True  # multipart (text+image) = real user msg
        t = c or ""
        return not t.startswith("[System:") and not t.startswith("[Conversation summary")

    def _alc_compact_restart_ms(self, st):
        return (time.monotonic() - st._compact_restart_t0) * 1000.0

    def _alc_persist_post_compact_usage(self, st):
        try:
            from core.conversation_store import ConversationStore
            from tasks.ai.context_usage import persist_context_usage
            persist_context_usage(
                st.conversation_id, st._agent_name, st._post_usage,
                store=ConversationStore.instance())
        except Exception:
            logger.debug("exception suppressed", exc_info=True)
