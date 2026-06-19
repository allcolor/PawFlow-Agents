"""AgentCoreMixin closures extracted as methods (split for <=800 lines)."""
import copy
import logging
import time

from core.llm_client import (
    LLMMessage, CCCompactDetected,
)

from tasks.ai._alc_base import (  # noqa: F401
    _strip_context_ack, _preempt_rescue_requires_retrigger, _apply_bg_results,
    _svc_rates, _usage_cost_usd, _check_budget, _CONTEXT_ACK_PATTERNS)

logger = logging.getLogger(__name__)


class _ALCClosures1Mixin:
    def _alc_agent_source(self, st, tok_in=0, tok_out=0, model_override='', tok_cache_creation=0, tok_cache_read=0, include_context: bool = True):
        import re as _re
        src = {
            "type": "agent", "name": st.ctx.get("active_agent_name", ""),
            "llm_service": st.ctx.get("active_llm_service", ""),
            "provider": st._client_provider,
            "model": model_override or st._client_model,
            "base_url": _re.sub(r'(key|token|secret)=[^&]+', r'\1=***',
                                st._client_base_url) if st._client_base_url else "",
            "containerized": st._client_provider == "claude-code",
        }
        if tok_in or tok_out:
            src["tokens_in"] = tok_in
            src["tokens_out"] = tok_out
        _ctx_usage = None
        if include_context:
            try:
                from tasks.ai.context_usage import compute_context_usage
                _ctx_usage = compute_context_usage(
                    st.conversation_id, st.ctx.get("active_agent_name", ""),
                    user_id=st.user_id, source="pawflow_context")
            except Exception:
                logger.debug("context gauge calculation failed", exc_info=True)
        if _ctx_usage and int(_ctx_usage.get("max", 0) or 0) > 0:
            src["context_used"] = int(_ctx_usage.get("used", 0) or 0)
            src["context_max"] = int(_ctx_usage.get("max", 0) or 0)
            src["context_pct"] = float(_ctx_usage.get("pct", 0.0) or 0.0)
            src["context_source"] = _ctx_usage.get("source", "")
            src["context_message_count"] = _ctx_usage.get("message_count", 0)
            src["context_cache_mode"] = _ctx_usage.get("cache_mode", "")
            src["context_cache"] = _ctx_usage
        return src

    def _alc_agent_source_cached(self, st, tok_in=0, tok_out=0, model_override='', tok_cache_creation=0, tok_cache_read=0):
        """Build source metadata without doing any store/token work."""
        src = st._agent_source(
            tok_in, tok_out, model_override,
            tok_cache_creation=tok_cache_creation,
            tok_cache_read=tok_cache_read,
            include_context=False)
        usage = (st.ctx.get("_context_usage_cache")
                 or st.ctx.get("_auto_compact_usage_cache") or {})
        try:
            if int(usage.get("max", 0) or 0) > 0:
                src["context_used"] = int(usage.get("used", 0) or 0)
                src["context_max"] = int(usage.get("max", 0) or 0)
                src["context_pct"] = float(usage.get("pct", 0.0) or 0.0)
                src["context_source"] = usage.get("source", "")
                src["context_message_count"] = usage.get("message_count", 0)
                src["context_cache_mode"] = usage.get("cache_mode", "")
                src["context_cache"] = usage
        except Exception:
            logger.debug("cached agent source build failed", exc_info=True)
        return src

    def _alc_patch_cc_turn_gauge(self, st, response, msg_id: str):
        """Patch a claude-code turn message with token + context-gauge data.

            The context gauge is the PawFlow stored-context calculation
            (via _agent_source -> compute_context_usage), which is stable
            across CLI session/tmux restarts and only moves on compaction
            or a context edit. The provider's per-session reported usage
            is deliberately NOT used: it resets when the CLI session
            cold-starts, which made the gauge jump. Used by both the
            normal final-turn path and the CCI interrupt turn.
            """
        if not msg_id or not (response.tokens_in or response.tokens_out):
            return
        _cc_src = st._agent_source(
            response.tokens_in, response.tokens_out, response.model,
            tok_cache_creation=getattr(response, 'cache_creation_tokens', 0),
            tok_cache_read=getattr(response, 'cache_read_tokens', 0))
        # Update in-memory message
        for _m in reversed(st.messages):
            if getattr(_m, 'msg_id', '') == msg_id:
                _m.source = _cc_src
                break
        # Persist durable gauge state without rewriting conversation rows.
        # The live UI gets per-message metadata from message_meta below;
        # context_usage in extras is the durable restart baseline. Rewriting
        # transcript/context JSONL here is disproportionate and can block
        # the post-answer hotpath on large conversations.
        if st.use_conv_store and st.conversation_id:
            try:
                from tasks.ai.context_usage import persist_context_usage
                _agent_for_usage = _cc_src.get("name", "")
                _usage = dict(_cc_src.get("context_cache") or {})
                if not _usage and "context_used" in _cc_src:
                    _usage = {
                        "conversation_id": st.conversation_id,
                        "agent_name": _agent_for_usage,
                        "used": int(_cc_src.get("context_used", 0) or 0),
                        "max": int(_cc_src.get("context_max", 0) or 0),
                        "pct": float(_cc_src.get("context_pct", 0.0) or 0.0),
                        "source": _cc_src.get("context_source", "pawflow_context"),
                        "message_count": _cc_src.get("context_message_count", 0),
                        "cache_mode": _cc_src.get("context_cache_mode", ""),
                        "updated_at": time.time(),
                    }
                persist_context_usage(
                    st.conversation_id, _agent_for_usage, _usage)
            except Exception:
                logger.debug("exception suppressed", exc_info=True)
        if "context_used" not in _cc_src:
            return
        try:
            from core.conversation_event_bus import ConversationEventBus
            _ctx_cache = _cc_src.get("context_cache") or {}
            ConversationEventBus.instance().publish_event(
                st.ctx.get("_event_cid", st.conversation_id),
                "message_meta", {
                    "conversation_id": st.ctx.get("_event_cid", st.conversation_id),
                    "msg_id": msg_id,
                    "agent_name": _cc_src.get("name", ""),
                    "source": _cc_src,
                    "model": _cc_src.get("model", ""),
                    "provider": _cc_src.get("provider", ""),
                    "tokens_in": _cc_src.get("tokens_in", 0),
                    "tokens_out": _cc_src.get("tokens_out", 0),
                    "context_used": _cc_src["context_used"],
                    "context_max": _cc_src["context_max"],
                    "context_pct": _cc_src["context_pct"],
                    "context_source": _cc_src.get("context_source", ""),
                    "context_message_count": _cc_src.get("context_message_count", 0),
                    "context_cache_mode": _cc_src.get("context_cache_mode", ""),
                    "updated_at": float(
                        _ctx_cache.get("updated_at") or time.time()),
                })
        except Exception as _meta_err:
            logger.error(
                "[claude-code] context message_meta publish failed "
                    "msg_id=%s: %s", msg_id, _meta_err, exc_info=True)

    def _alc_schedule_cc_turn_gauge_patch(self, st, response, msg_id: str, reason: str):
        """Run slow final metadata/gauge refresh outside the done hotpath."""
        if not msg_id or not (response.tokens_in or response.tokens_out):
            return
        import threading as _threading_gauge
        def _run():
            _t0 = time.monotonic()
            try:
                st._patch_cc_turn_gauge(response, msg_id)
                logger.info(
                    "[agent:%s] async cc turn gauge patch finished reason=%s elapsed_ms=%.1f",
                    st.conversation_id[:8], reason,
                    (time.monotonic() - _t0) * 1000.0)
            except Exception as _err:
                logger.error(
                    "[agent:%s] async cc turn gauge patch failed reason=%s: %s",
                    st.conversation_id[:8], reason, _err, exc_info=True)
        _threading_gauge.Thread(
            target=_run, daemon=True,
            name=f"cc-gauge-{st.conversation_id[:8]}-{reason}").start()

    def _alc_set_provider_compact_barrier(self, st, reason: str):
        st._auto_compact_state["running"] = True
        st.ctx["_provider_compact_in_progress"] = True
        st.ctx["_provider_compact_reason"] = reason

    def _alc_clear_provider_compact_barrier(self, st):
        st._auto_compact_state["running"] = False
        st._auto_compact_state["handoff"] = False
        st.ctx.pop("_provider_compact_in_progress", None)
        st.ctx.pop("_provider_compact_reason", None)

    def _alc_compact_threshold_fraction(self, st, raw_value):
        """Normalize compact_threshold_pct to a 0..1 fraction.

            Accept both the documented percent form (80 or "80%") and the
            common UI/API fractional form (0.8). Values <= 0 disable proactive
            compaction.
            """
        try:
            if isinstance(raw_value, str):
                raw = raw_value.strip()
                if raw.endswith("%"):
                    raw = raw[:-1].strip()
                value = float(raw or 0)
            else:
                value = float(raw_value or 0)
        except (TypeError, ValueError):
            return 0.0
        if value <= 0:
            return 0.0
        if value < 1:
            return value
        return value / 100.0

    def _alc_agent_compact_threshold_fraction(self, st):
        try:
            cfg = (
                getattr(st.ctx.get("resolved_svc"), "config", None)
                or getattr(st.client, "config", None)
                or getattr(st.client, "_config_ref", None)
                or getattr(getattr(st.client, "_client", None),
                           "_config_ref", None)
                or {})
            raw_pct = cfg.get("compact_threshold_pct", 0)
        except (AttributeError, TypeError):
            raw_pct = 0
        return st._compact_threshold_fraction(raw_pct)

    def _alc_auto_compact_usage(self, st, max_ctx: int, source: str):
        """Return the same used/max pair as the live gauge."""
        from tasks.ai.context_usage import compute_context_usage
        usage = compute_context_usage(
            st.conversation_id, st.ctx.get("active_agent_name", ""),
            user_id=st.user_id, source=source)
        st.ctx["_auto_compact_usage_cache"] = usage
        return usage

    def _alc_maybe_auto_compact_after_append(self, st, msg: LLMMessage, reason: str):
        """Enforce compact_threshold_pct as a live invariant.

            The pre-send compact only protects the next LLM call. Streaming
            CLI providers can append many large tool results during one active
            turn, so enforce the threshold after visible result/message appends
            too. Skip bare tool_call messages: compacting between a call and
            its result can orphan the result that is about to arrive.
            """
        if not st.conversation_id or st._auto_compact_state.get("running"):
            return
        if msg.role == "assistant" and msg.tool_calls and not msg.content:
            return
        if msg.role not in ("assistant", "tool"):
            return
        trigger_fraction = st._agent_compact_threshold_fraction()
        if trigger_fraction <= 0:
            logger.info(
                "[compact-check] %s role=%s SKIP: trigger_fraction=%.3f "
                    "(compact_threshold_pct not reaching the agent client config)",
                reason, msg.role, trigger_fraction)
            return
        max_ctx = int(st.ctx.get("max_context_size", 0) or 0)
        if max_ctx <= 0:
            logger.info(
                "[compact-check] %s role=%s SKIP: max_ctx=%d (ctx.max_context_size unset)",
                reason, msg.role, max_ctx)
            return
        trigger_tokens = int(max_ctx * trigger_fraction)
        try:
            usage = (st.ctx.get("_context_usage_cache")
                     or st.ctx.get("_auto_compact_usage_cache") or {})
            used = int(usage.get("used", 0) or 0)
        except Exception:
            logger.debug("[compact] auto threshold estimate failed", exc_info=True)
            return
        _cache_src = (
            "context_usage_cache" if st.ctx.get("_context_usage_cache")
            else ("auto_compact_usage_cache" if st.ctx.get("_auto_compact_usage_cache")
                  else "NONE"))
        logger.info(
            "[compact-check] %s role=%s provider=%s trigger_fraction=%.3f "
                "max_ctx=%d trigger_tokens=%d used=%d cache_src=%s will_compact=%s",
            reason, msg.role, st._client_provider, trigger_fraction, max_ctx,
            trigger_tokens, used, _cache_src, used >= trigger_tokens)
        if used < trigger_tokens:
            return
        st._set_provider_compact_barrier(f"post_append:{reason}")
        try:
            logger.warning(
                "[compact] auto threshold crossed after %s: %d >= %d (%.0f%%)",
                reason, used, trigger_tokens, trigger_fraction * 100)
            if st._client_provider in (
                "claude-code", "claude-code-interactive",
                "antigravity-interactive", "codex-app-server", "gemini"):
                # Stateful CLI/live providers must not be killed from a
                # streaming callback. Propagate the threshold crossing to
                # the normal provider-compact path: it tears down the old
                # instance, compacts PawFlow, starts a fresh session with
                # the compacted context, then lets the provider keep it
                # live for the next user message.
                st._auto_compact_state["handoff"] = True
                raise CCCompactDetected(
                    "PawFlow post-append compact threshold crossed")
            compact_owner = st.ctx.get("resolved_svc") or st.client or st.compact_client
            compacted = self._compact(
                copy.deepcopy(st.messages), compact_owner, max_ctx,
                trigger_fraction=trigger_fraction,
                force=True,
                conversation_id=st.conversation_id,
                agent_name=st.ctx.get("active_agent_name") or "",
                tool_defs=st.ctx.get("tool_defs"),
                chars_per_token=st.ctx.get("chars_per_token", 0),
                user_id=st.user_id,
                budget_config=getattr(st.ctx.get("resolved_svc"), "config", None),
                independent_context=bool(st.ctx.get("_independent_context")),
            )
            if compacted and len(compacted) <= len(st.messages):
                st._adopt_compacted_context(
                    compacted, reason="post_append")
        except CCCompactDetected:
            raise
        except Exception as compact_err:
            logger.error(
                "[compact] auto compact after %s failed: %s",
                reason, compact_err, exc_info=True)
            try:
                from core.conversation_event_bus import ConversationEventBus
                ConversationEventBus.instance().publish_event(
                    st.conversation_id, "compact_progress", {
                        "stage": "error",
                        "agent": st.ctx.get("active_agent_name") or "",
                        "error": str(compact_err),
                    })
            except Exception:
                logger.debug("auto compact error SSE failed", exc_info=True)
        finally:
            if not st._auto_compact_state.get("handoff"):
                st._clear_provider_compact_barrier()

    def _alc_append(self, st, msg: LLMMessage):
        _append_started = time.monotonic()
        _enqueue_ms = None
        _mirror_enqueue_ms = None
        # FORCE STOP is a hard barrier. Provider/tool callbacks can still
        # arrive briefly after the live process has been asked to die; none
        # of those late messages may be persisted or published.
        st.emitter.check_cancelled()
        if (st.ctx.get("_provider_compact_in_progress")
                and msg.role in ("assistant", "tool")):
            logger.warning(
                "[compact] rejected late provider callback during compact "
                    "role=%s msg_id=%s reason=%s",
                msg.role, getattr(msg, "msg_id", "?"),
                st.ctx.get("_provider_compact_reason", ""))
            raise CCCompactDetected("PawFlow compact already in progress")
        # Sync msg_id: assistant messages use emitter's pre-generated ID
        # so SSE streaming tokens, done event, and persisted message all
        # share the SAME msg_id — enabling client-side dedup.
        if msg.role == "assistant" and st.emitter._current_msg_id:
            msg.msg_id = st.emitter._current_msg_id
            st.all_assistant_msg_ids.append(msg.msg_id)
            # After this message, generate a NEW msg_id for the next one
            import uuid as _uuid_append
            st.emitter._current_msg_id = _uuid_append.uuid4().hex[:12]
        # Auto-tag: if this turn was triggered by an agent_delegate
        # message, the assistant's reply routes privately back to
        # the delegator only. Re-stamp the source as agent_delegate
        # so ConversationStore.append_message routes it correctly
        # (transcript + from+to ctx only, NOT shared, NOT peers).
        _tm = st.ctx.get("_turn_mode") or {}
        if (_tm.get("type") == "delegate_reply"
                and _tm.get("source_agent")
                and msg.role in ("assistant", "tool")):
            _self_name = st.ctx.get("active_agent_name", "") or ""
            _delegate_visibility = "self_only"
            _delegate_visibility_explicit = False
            if isinstance(msg.source, dict):
                _explicit_visibility = msg.source.get("delegate_visibility")
                if _explicit_visibility:
                    _delegate_visibility = _explicit_visibility
                    _delegate_visibility_explicit = True
            elif (msg.role == "assistant"
                  and not msg.tool_calls
                  and isinstance(msg.content, str)
                  and msg.content.strip()):
                _delegate_visibility = "final_reply"
            if (msg.role == "assistant"
                    and not msg.tool_calls
                    and isinstance(msg.content, str)
                    and msg.content.strip()
                    and _delegate_visibility == "self_only"
                    and not _delegate_visibility_explicit):
                _delegate_visibility = "final_reply"
            # Preserve the LLM meta fields from the original source
            # (provider/model/tokens/context/base_url/containerized)
            # so the recipient's UI can render the badge + meta line
            # on the delegate block. Without this merge, the receiver
            # sees a bare delegate block with no provider/tokens info.
            _preserved = {}
            if isinstance(msg.source, dict):
                for _k in ("provider", "model", "llm_service",
                           "base_url", "containerized",
                           "tokens_in", "tokens_out",
                           "context_used", "context_max",
                           "context_pct"):
                    if _k in msg.source:
                        _preserved[_k] = msg.source[_k]
            msg.source = {
                **_preserved,
                "type": "agent_delegate",
                "from": _self_name,
                "to": _tm["source_agent"],
                # Mark as a REPLY so conversation_store renders the
                # right prefix in the target's ctx ("Here is agent
                # X's reply to your delegate:") instead of treating
                # it like a fresh inbound request.
                "kind": "reply",
                "delegate_visibility": _delegate_visibility,
            }
        if msg.role == "assistant" and st.conversation_id:
            try:
                from core.agent_hooks import AgentHookRunner
                _src = msg.source if isinstance(msg.source, dict) else {}
                _runner = AgentHookRunner(
                    user_id=st.user_id,
                    conversation_id=st.conversation_id,
                    agent_name=st.ctx.get("active_agent_name", "") or _src.get("name", ""),
                    agent_service=_src.get("llm_service", ""),
                    provider=_src.get("provider", ""),
                    model=_src.get("model", ""),
                )
                if getattr(msg, "thinking", ""):
                    _think = _runner.run("post_llm_thinking", {
                        "message_id": getattr(msg, "msg_id", ""),
                        "thinking": msg.thinking,
                        "source": msg.source,
                    })
                    if _think.get("decision") == "replace":
                        _payload = _think.get("payload") or {}
                        if "thinking" in _payload:
                            msg.thinking = str(_payload.get("thinking") or "")
                _msg_hook = _runner.run("post_llm_message", {
                    "message_id": getattr(msg, "msg_id", ""),
                    "content": msg.content,
                    "thinking": getattr(msg, "thinking", ""),
                    "source": msg.source,
                })
                if _msg_hook.get("decision") == "block":
                    logger.info("post_llm_message hook blocked assistant message")
                    return
                if _msg_hook.get("decision") == "replace":
                    _payload = _msg_hook.get("payload") or {}
                    if "content" in _payload:
                        msg.content = _payload.get("content")
                    if "thinking" in _payload:
                        msg.thinking = str(_payload.get("thinking") or "")
            except Exception as _hook_err:
                logger.warning("post_llm_message hook failed: %s", _hook_err,
                               exc_info=True)
        st.messages.append(msg)
        st.new_messages.append(msg)
        # Persist via conversation writer + publish SSE (single source of truth)
        # Skip context-internal messages (compaction acks) — they stay in agent
        # context but must never appear in transcript or SSE.
        _src_type = (msg.source or {}).get("type") if isinstance(msg.source, dict) else None
        if _src_type == "context":
            return
        if st.use_conv_store and st.conversation_id and msg.role in ("assistant", "tool"):
            try:
                from core.conversation_writer import ConversationWriter
                _store_msg = {
                    "role": msg.role, "content": msg.content,
                    "source": msg.source,
                    "msg_id": getattr(msg, "msg_id", None),
                    "tool_call_id": getattr(msg, "tool_call_id", None),
                    # Carry CREATION ts + seq so order on disk reflects
                    # when the message was minted, not when the writer
                    # happened to dequeue it.
                    "ts": getattr(msg, "timestamp", 0) or None,
                    "seq": getattr(msg, "seq", 0) or None,
                }
                if msg.thinking:
                    _store_msg["thinking"] = msg.thinking
                if msg.tool_calls:
                    _store_msg["tool_calls"] = []
                    for tc in msg.tool_calls:
                        _tc_entry = {
                            "id": tc.id,
                            "name": tc.name,
                            "arguments": tc.arguments,
                        }
                        if getattr(tc, "tool_origin", ""):
                            _tc_entry["tool_origin"] = tc.tool_origin
                        _store_msg["tool_calls"].append(_tc_entry)
                # Build SSE events to publish AFTER write.
                # IMMUTABLE RULE: stream block → LLMMessage → writer →
                # transcript/shared/ctx → SSE (emitted post-write by the
                # writer via sse_events). Applies to ALL providers —
                # claude-code AND LLM-API (openai/anthropic). No
                # provider is allowed to publish SSE for message-level
                # content (tool_call, tool_result, new_message,
                # thinking_content) out-of-band.
                _sse = []
                _agent = (msg.source or {}).get("name", "") if msg.source else ""
                if not _agent:
                    _agent = st.ctx.get("active_agent_name", "")
                _svc = (msg.source or {}).get("llm_service", "") if msg.source else ""
                # Thinking: assistant block may carry a `thinking`
                # payload (Anthropic extended thinking, CC thinking
                # block). Emit as a separate thinking_content SSE so
                # the UI shows the reasoning bubble alongside the
                # message. Persisted as msg.thinking in the same
                # store_msg (_store_msg["thinking"] above).
                _think_text = getattr(msg, "thinking", "") or ""
                if msg.role == "assistant" and _think_text:
                    _sse.append({"type": "thinking_content", "data": {
                        "text": _think_text,
                        "msg_id": getattr(msg, "msg_id", ""),
                        "ts": getattr(msg, "timestamp", 0) or None,
                        "agent_name": _agent,
                        "source": msg.source,
                    }})
                # Assistant text → `new_message` so the UI renders it.
                if (msg.role == "assistant"
                        and isinstance(msg.content, str)
                        and msg.content.strip()):
                    _sse.append({"type": "new_message", "data": {
                        "role": "assistant",
                        "content": msg.content,
                        "msg_id": getattr(msg, "msg_id", ""),
                        "ts": getattr(msg, "timestamp", 0) or None,
                        "source": msg.source,
                    }})
                # Assistant tool_calls → one tool_call SSE per tc.
                if msg.role == "assistant" and msg.tool_calls:
                    from core.llm_client import unwrap_mcp_tool
                    _hidden_schema_tools = {
                        "get_tool_schema",
                        "mcp_pawflow_get_tool_schema",
                        "mcp__pawflow__get_tool_schema",
                    }
                    for tc in msg.tool_calls:
                        _tc_name, _tc_args = unwrap_mcp_tool(tc.name, tc.arguments)
                        if _tc_name in _hidden_schema_tools:
                            continue
                        _tc_data = {
                            "tool": _tc_name, "arguments": _tc_args,
                            "tc_id": tc.id,
                            "agent_name": _agent, "llm_service": _svc,
                            "msg_id": getattr(msg, "msg_id", ""),
                            "ts": getattr(msg, "timestamp", 0) or None,
                            "source": msg.source,
                        }
                        if getattr(tc, "tool_origin", ""):
                            _tc_data["tool_origin"] = tc.tool_origin
                        _sse.append({"type": "tool_call", "data": _tc_data})
                # role=tool → tool_result SSE. Prefer the `_tool_name`
                # attr stamped by the dispatching side, but do not make
                # live result delivery depend on that optional label: the
                # `tool_call_id` is enough for the UI to attach the output
                # to the already-rendered tool_call.
                if msg.role == "tool":
                    _raw_tool_name = getattr(msg, '_tool_name', '')
                    _raw_tool_origin = getattr(msg, '_tool_origin', '')
                    if not _raw_tool_name and getattr(msg, 'tool_call_id', ''):
                        _tcid = getattr(msg, 'tool_call_id', '')
                        for _prev in reversed(st.messages[:-1]):
                            if getattr(_prev, 'role', '') != "assistant":
                                continue
                            for _tc in (getattr(_prev, 'tool_calls', None) or []):
                                if getattr(_tc, 'id', '') == _tcid:
                                    _raw_tool_name = getattr(_tc, 'name', '') or ""
                                    _raw_tool_origin = getattr(_tc, 'tool_origin', '') or ""
                                    break
                            if _raw_tool_name:
                                break
                    if _raw_tool_name in {
                        "get_tool_schema",
                        "mcp_pawflow_get_tool_schema",
                        "mcp__pawflow__get_tool_schema",
                    }:
                        _raw_tool_name = ""
                    if getattr(msg, 'tool_call_id', ''):
                        _preview = (msg.content if isinstance(msg.content, str)
                                    else str(msg.content))
                        # Strip outer <tool_output tool="..."> wrapper
                        # before showing in chat. Inner <tool_output>
                        # literals (grep hits, nested summaries) are
                        # kept verbatim — only the top-level envelope
                        # is removed.
                        if _preview.startswith("<tool_output tool="):
                            _nl = _preview.find("\n")
                            if _nl >= 0:
                                _preview = _preview[_nl + 1:]
                            _close = _preview.rfind("</tool_output>")
                            if _close >= 0:
                                _preview = _preview[:_close].rstrip("\n")
                        # Truncate AFTER strip so we keep the real
                        # content, not the wrapper eating our budget.
                        _preview = _preview[:2000]
                        _tr_data = {
                            "tool": _raw_tool_name,
                            "result": _preview,
                            "tc_id": getattr(msg, 'tool_call_id', ''),
                            "msg_id": getattr(msg, "msg_id", ""),
                            "ts": getattr(msg, "timestamp", 0) or None,
                            "agent_name": _agent, "llm_service": _svc,
                        }
                        if _raw_tool_origin:
                            _tr_data["tool_origin"] = _raw_tool_origin
                        _sse.append({"type": "tool_result", "data": _tr_data})
                _agent_for_route = st.ctx.get("active_agent_name", "") or ""
                logger.info(
                    "[_append] role=%s msg_id=%s content_len=%d "
                        "thinking_len=%d tool_calls=%d → sse_events=%s",
                    msg.role,
                    getattr(msg, "msg_id", "?"),
                    len(msg.content) if isinstance(msg.content, str) else 0,
                    len(_think_text),
                    len(msg.tool_calls) if msg.tool_calls else 0,
                    [s["type"] for s in _sse] if _sse else [],
                )
                _task_parent_cid = ""
                _task_id = ""
                _task_iteration = st.ctx.get("_task_iteration", 1)
                _parent_sse = None
                if "::task::" in st.conversation_id:
                    _task_parent_cid = st.conversation_id.split("::task::", 1)[0]
                    _task_id = st.conversation_id.split("::task::", 1)[1].split("::", 1)[0]
                    if _sse:
                        _parent_sse = []
                        for _evt in _sse:
                            _evt2 = dict(_evt)
                            _data = dict(_evt2.get("data") or {})
                            _src = dict(_data.get("source") or {})
                            _src["task_id"] = _task_id
                            _src["task_iteration"] = _task_iteration
                            _data["source"] = _src
                            _data["task_id"] = _task_id
                            _data["task_iteration"] = _task_iteration
                            _evt2["data"] = _data
                            _evt2["cid"] = _task_parent_cid
                            _parent_sse.append(_evt2)
                # thinking-only live delta: provider reasoning streams are
                # transient UI updates, not durable conversation messages.
                # Persisting each chunk causes cumulative JSONL writes and
                # compact-check work while adding no replay value.
                if (msg.role == "assistant" and _think_text
                        and not (isinstance(msg.content, str)
                                 and msg.content.strip())
                        and not msg.tool_calls):
                    from core.conversation_event_bus import ConversationEventBus
                    _bus = ConversationEventBus.instance()
                    for _evt in (_parent_sse if _task_parent_cid else _sse):
                        _bus.publish_event(
                            _evt.get("cid") or st.conversation_id,
                            _evt["type"], _evt.get("data"))
                    return
                _writer = ConversationWriter.for_conversation(st.conversation_id)
                _enqueue_started = time.monotonic()
                _writer.enqueue_message(
                    _store_msg, agent_name=_agent_for_route,
                    user_id=st.user_id,
                    sse_events=None if _task_parent_cid else (_sse if _sse else None))
                _enqueue_ms = (time.monotonic() - _enqueue_started) * 1000.0
                # Task sub-conversation: mirror to parent conv so the
                # user sees task progress in their main feed. Tag with
                # task_id + task_iteration for UI grouping.
                if _task_parent_cid:
                    _mirror = dict(_store_msg)
                    _msrc = dict(_mirror.get("source") or {})
                    _msrc["task_id"] = _task_id
                    _msrc["task_iteration"] = _task_iteration
                    _mirror["source"] = _msrc
                    _mirror_started = time.monotonic()
                    ConversationWriter.for_conversation(_task_parent_cid).enqueue_message(
                        _mirror, agent_name=_agent_for_route,
                        user_id=st.user_id,
                        sse_events=_parent_sse if _parent_sse else None)
                    _mirror_enqueue_ms = ((time.monotonic() - _mirror_started)
                                          * 1000.0)
            except Exception as _persist_err:
                # HARD INVARIANT: visible ⇒ persisted. A failure to enqueue
                # means the message was (or will be) shown to the user but
                # is not on disk — data loss. Never swallow. Log loudly and
                # re-raise so the caller (turn_callback, etc.) fails fast
                # instead of continuing on corrupted state.
                logger.error(
                    "[_append] ENQUEUE FAILED — visible/persisted invariant "
                        "broken. conv=%s agent=%s role=%s msg_id=%s err=%s",
                    st.conversation_id[:8] if st.conversation_id else "?",
                    st.ctx.get("active_agent_name", "?"),
                    msg.role,
                    getattr(msg, "msg_id", "?"),
                    _persist_err,
                    exc_info=True,
                )
                raise
        # Publish per-message metadata (model, tokens, service) so client
        # can attach badge + info to the correct element by msg_id
        if msg.role == "assistant" and msg.source and st.emitter.is_streaming:
            _src = msg.source
            if _src.get("tokens_in") or _src.get("tokens_out") or _src.get("llm_service"):
                from core.conversation_event_bus import ConversationEventBus
                try:
                    _payload = {
                        "conversation_id": st.ctx.get("_event_cid", st.conversation_id),
                        "msg_id": msg.msg_id,
                        "agent_name": _src.get("name", ""),
                        "source": _src,
                        "model": _src.get("model", ""),
                        "provider": _src.get("provider", ""),
                        "tokens_in": _src.get("tokens_in", 0),
                        "tokens_out": _src.get("tokens_out", 0),
                    }
                    # Context-fill fields (only present when computed in source)
                    if "context_used" in _src:
                        _payload["context_used"] = _src["context_used"]
                        _payload["context_max"] = _src["context_max"]
                        _payload["context_pct"] = _src["context_pct"]
                        _payload["updated_at"] = float((
                            _src.get("context_cache") or {}).get("updated_at")
                            or time.time())
                        # Hot path: provider callbacks must not write
                        # extras.json or wait on conversation-store locks.
                        # Durable context_usage is refreshed by the
                        # regular iteration/heartbeat paths.
                    ConversationEventBus.instance().publish_event(
                        st.ctx.get("_event_cid", st.conversation_id), "message_meta", _payload)
                except Exception as _meta_err:
                    # Never-swallow: message_meta is the source of the
                    # per-message model/tokens badge. Failure here
                    # silently drops the badge in the UI — surface it.
                    logger.error(
                        "[_append] message_meta publish failed "
                            "msg_id=%s: %s",
                        getattr(msg, "msg_id", "?"), _meta_err,
                        exc_info=True)
        # Context gauge: the gauge is the size of the agent's PawFlow
        # context, so it must move with every message appended to that
        # context — not only at iteration/turn boundaries. _append is
        # the single point every provider routes through (the classic
        # loop AND the claude-code/CCI block callback), so refreshing it
        # here keeps the gauge live and identical for all providers.
        # compute_context_usage reuses the delta token cache, so the
        # per-append recount stays cheap. Run it BEFORE the compaction
        # check so _maybe_auto_compact_after_append reads a gauge that
        # already includes this message — otherwise a long CLI turn can
        # blow past compact_threshold_pct on a stale (turn-start) value.
        _context_usage_ms = 0.0
        try:
            _usage_started = time.monotonic()
            st.emitter._publish_context_usage("append")
            _context_usage_ms = (time.monotonic() - _usage_started) * 1000.0
        except Exception:
            logger.debug("append context gauge refresh failed",
                         exc_info=True)
        _before_compact_ms = (time.monotonic() - _append_started) * 1000.0
        _compact_started = time.monotonic()
        st._maybe_auto_compact_after_append(msg, msg.role)
        _compact_ms = (time.monotonic() - _compact_started) * 1000.0
        logger.info(
            "[_append-perf] role=%s msg_id=%s enqueue_ms=%s "
                "mirror_enqueue_ms=%s context_usage_ms=%.1f "
                "pre_compact_ms=%.1f compact_ms=%.1f total_ms=%.1f",
            msg.role,
            getattr(msg, "msg_id", "?"),
            "" if _enqueue_ms is None else f"{_enqueue_ms:.1f}",
            "" if _mirror_enqueue_ms is None else f"{_mirror_enqueue_ms:.1f}",
            _context_usage_ms,
            _before_compact_ms,
            _compact_ms,
            (time.monotonic() - _append_started) * 1000.0,
        )

