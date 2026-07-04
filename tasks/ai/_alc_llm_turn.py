"""agent_core split (<=800 lines): _ALCLlmTurnMixin."""
import logging
import time

from core.llm_client import (
    CCCompactDetected,
)
from tasks.ai.agent_exceptions import AgentCancelled
from tasks.ai.agent_compaction import COMPACT_TAIL_MESSAGES

from tasks.ai._alc_base import (  # noqa: F401
    _ALCState, _ALC_BREAK, _ALC_CONTINUE, _strip_context_ack,
    _preempt_rescue_requires_retrigger, _apply_bg_results, _svc_rates,
    _usage_cost_usd, _check_budget, _CONTEXT_ACK_PATTERNS)

logger = logging.getLogger(__name__)


class _ALCLlmTurnMixin:
    def _alc_llm_turn(self, st):
        try:
            _check_budget(
                st.ctx, st.total_tokens_in, st.total_tokens_out,
                st.total_cache_read, st.total_cache_write)
            st.response = st._llm_call(st._call_context)
            st._provider_response_completed_at = time.time()
            if st.emitter.check_interrupt():
                logger.info(
                    "[agent:%s] interrupt arrived during provider request — "
                                "discarding current turn and running STOP turn",
                    st.conversation_id[:8])
                st._run_interrupt_turn()
        except AgentCancelled:
            raise
        except CCCompactDetected:
            # A stateful CLI provider started auto-compacting → kill it,
            # compact PawFlow context, then start a new session with the
            # compacted context.
            st._agent_name = st.ctx.get("active_agent_name", "")
            st._set_provider_compact_barrier("provider_compact_detected")
            st._compact_restart_t0 = time.monotonic()

            st._compact_restart_ms = lambda : self._alc_compact_restart_ms(st)

            logger.warning("[agent:%s] provider compact detected — compacting PawFlow context for %s",
                           st.conversation_id[:8], st._agent_name)
            # Tell the UI: auto-compact started (shows the
            # 'Compacting (<agent>)' typing indicator).
            try:
                from core.conversation_event_bus import ConversationEventBus as _CEB
                _CEB.instance().publish_event(
                    st.conversation_id, "compact_progress",
                    {"stage": "start",
                     "detail": "auto-compact",
                     "agent": st._agent_name})
            except Exception:
                logger.debug("exception suppressed", exc_info=True)
            # User messages are persisted to the transcript at
            # ingress (see agent_streaming.py) BEFORE
            # send_user_message is called, so the compacted
            # context — loaded from transcript — already carries
            # any in-flight preempts. No rescue needed here.
            # Recover tokens BEFORE restarting — CC may have refreshed
            # OAuth tokens during the session that was just killed
            if hasattr(st.client, '_recover_tokens') and hasattr(st.client, '_get_session_workdir'):
                try:
                    st._wd = st.client._get_session_workdir(st.conversation_id, st._agent_name, st.user_id)
                    st.client._recover_tokens(
                        st._wd, user_id=st.user_id,
                        conversation_id=st.conversation_id)
                except Exception as _rt_err:
                    logger.debug("[agent:%s] token recovery after compact: %s",
                                 st.conversation_id[:8], _rt_err)
            try:
                # Flush the async ConversationWriter queue BEFORE
                # reading shared.jsonl. turn_callback enqueues each
                # CC turn (tool_use/tool_result/text) via
                # ConversationWriter.enqueue_message() which is
                # non-blocking — messages live in a background
                # queue until the writer thread drains them to
                # disk. Without this flush, compact reads a stale
                # view and drops every turn that CC emitted in
                # the seconds leading up to compact_boundary.
                try:
                    from core.conversation_writer import ConversationWriter
                    ConversationWriter.for_conversation(
                        st.conversation_id).flush(timeout=15.0)
                    logger.info(
                        "[compact-restart:%s/%s] writer flush done elapsed_ms=%.1f",
                        st.conversation_id[:8], st._agent_name,
                        st._compact_restart_ms())
                except Exception as _fl_err:
                    logger.warning(
                        "[agent:%s] writer flush before compact "
                                    "failed: %s", st.conversation_id[:8], _fl_err)
                # 1. Run the same compact procedure used by /compact.
                # Old history comes from the shared bucket header
                # assembled inside _compact(); non-independent compact
                # only gets a bounded raw tail here.
                from core.conversation_store import ConversationStore
                st._store = ConversationStore.instance()

                # 2. FORCE compact — CC said it's saturating, so we compact
                # unconditionally. PawFlow's token estimate may underestimate
                # (different tokenizer, tool schemas not counted), leading to
                # no-op compactions that leave stale summaries in the context.
                # Propagate the agent's configured
                # `compact_threshold_pct` as `trigger_fraction`
                # so the [compact] log shows the value the
                # operator actually set (90% here, not the
                # 0.8 default). `force=True` already bypasses
                # the trigger gate, but the log line is what
                # debugging eyes read first — a misleading
                # 0.8 sent every operator down the wrong
                # path before this. Falls back to 0.8 only
                # when no per-service threshold is set.
                st._ccd_trigger_frac = 0.8
                try:
                    st._ccd_pct = int(
                        (getattr(st.client, "_config_ref", None)
                         or getattr(st.client, "config", None)
                         or {}).get("compact_threshold_pct", 0) or 0)
                    if st._ccd_pct > 0:
                        st._ccd_trigger_frac = st._ccd_pct / 100.0
                except (TypeError, ValueError):
                    pass
                st._compact_stats = {}
                st._compacted_messages = list(self._compact_context_from_store(
                    st._store,
                    conversation_id=st.conversation_id,
                    agent_name=st._agent_name,
                    user_id=st.user_id,
                    max_tokens=st.ctx.get("max_context_size", 200000),
                    compact_client=st.compact_client,
                    trigger_fraction=st._ccd_trigger_frac,
                    compact_instructions=st.ctx.get("compact_instructions", ""),
                    force=True,
                    budget_config=getattr(st.ctx.get("resolved_svc"), "config", None),
                    independent_context=bool(st.ctx.get("_independent_context")),
                    post_hooks_async=True,
                    tool_defs=st.ctx.get("tool_defs"),
                    chars_per_token=st.ctx.get("chars_per_token", 0),
                    tail_limit=COMPACT_TAIL_MESSAGES,
                    stats=st._compact_stats,
                ))
                logger.info(
                    "[compact-restart:%s/%s] compact returned messages=%d elapsed_ms=%.1f",
                    st.conversation_id[:8], st._agent_name,
                    len(st._compacted_messages), st._compact_restart_ms())
                # The user may have sent a wake/restart message while
                # the summarizer provider was still finishing. In that
                # case a newer agent generation is already live; the
                # stale compacting loop must not adopt context or
                # invalidate/kill that fresh runtime.
                st.emitter.check_cancelled()
                logger.info(
                    "[compact-restart:%s/%s] cancellation gate passed elapsed_ms=%.1f",
                    st.conversation_id[:8], st._agent_name,
                    st._compact_restart_ms())
                st._adopt_compacted_context(
                    st._compacted_messages, reason="provider_compact",
                    async_cleanup=True, already_persisted=True)
                logger.info(
                    "[compact-restart:%s/%s] adopted compacted context elapsed_ms=%.1f",
                    st.conversation_id[:8], st._agent_name,
                    st._compact_restart_ms())
                logger.info("[agent:%s] PawFlow compact: %d → %d messages",
                            st.conversation_id[:8],
                            int(st._compact_stats.get("before", 0) or 0),
                            len(st.messages))
                try:
                    from core.pending_queue import PendingQueue
                    st._compacted_ids = {
                        getattr(_m, "msg_id", "") for _m in st.messages
                        if getattr(_m, "msg_id", "")
                    }
                    PendingQueue.for_agent(
                        st.conversation_id, st._agent_name or "").discard_msg_ids(
                            st._compacted_ids)
                except Exception as _pq_err:
                    logger.warning(
                        "[agent:%s] pending compact dedupe failed: %s",
                        st.conversation_id[:8], _pq_err)
                logger.info(
                    "[compact-restart:%s/%s] pending dedupe done elapsed_ms=%.1f",
                    st.conversation_id[:8], st._agent_name,
                    st._compact_restart_ms())

                # 3. Invalidate CLI session: _compact() already
                # persisted the compacted PawFlow context, so
                # adoption must not re-save it on the foreground
                # restart path. Clear the extra AND purge the
                # stale jsonl + companion dir on disk.
                # Otherwise the killed session's jsonl
                # keeps piling up (orphan workers also
                # wrote to it) and fills the session dir.
                # 4. Prepare for new CC session — PawFlow ctx
                # was just compacted and saved; CC receives the
                # same compacted messages (no trimmed view).
                st.llm_context = list(st.messages)
                logger.info("[agent:%s] PawFlow compact done, provider turn will restart immediately",
                            st.conversation_id[:8])
                # _compact() already emits its own compact_progress:done
                # with accurate before/after counts (post bucket-filter).
                # Do NOT duplicate here with _full_messages count which
                # would confuse the UI (showing the raw transcript count
                # as 'before' ignores that most msgs are already bucketed).
                # Refresh the context_usage baseline from the
                # compacted messages already in memory. Reloading
                # stored context here can spend seconds walking
                # segmented JSONL immediately after compaction.
                st.ctx.pop("_context_usage_cache", None)
                st.ctx.pop("_auto_compact_usage_cache", None)
                try:
                    st._gauge_t0 = time.monotonic()
                    from core.conversation_event_bus import ConversationEventBus
                    from tasks.ai.context_usage import (
                        context_usage_for_messages, usage_event_payload)
                    st._svc_cfg = dict(getattr(st.ctx.get("resolved_svc"), "config", None) or {})
                    if int(st.ctx.get("max_context_size") or 0) > 0:
                        st._svc_cfg["max_context_size"] = int(st.ctx.get("max_context_size") or 0)
                    st._post_usage = context_usage_for_messages(
                        st.conversation_id, st._agent_name, st._compacted_messages,
                        svc_cfg=st._svc_cfg,
                        real_window=int(st.ctx.get("real_context_size") or 0),
                        provider=str(st.ctx.get("active_llm_provider", "") or getattr(st.client, "provider", "") or ""),
                        source="compact_post")
                    st.ctx["_context_usage_cache"] = st._post_usage
                    ConversationEventBus.instance().publish_event(
                        st.conversation_id, "message_meta",
                        usage_event_payload(st._post_usage))

                    st._persist_post_compact_usage = lambda : self._alc_persist_post_compact_usage(st)

                    import threading
                    threading.Thread(
                        target=st._persist_post_compact_usage,
                        daemon=True,
                        name=f"post-compact-usage-persist-{st.conversation_id[:8]}",
                    ).start()
                    logger.info(
                        "[compact-restart:%s/%s] post-compact gauge refresh done elapsed_ms=%.1f refresh_ms=%.1f",
                        st.conversation_id[:8], st._agent_name,
                        st._compact_restart_ms(),
                        (time.monotonic() - st._gauge_t0) * 1000.0)
                except Exception:
                    logger.debug("exception suppressed", exc_info=True)
                logger.info(
                    "[compact-restart:%s/%s] post-compact foreground release elapsed_ms=%.1f",
                    st.conversation_id[:8], st._agent_name,
                    st._compact_restart_ms())
                st._clear_provider_compact_barrier()
            except Exception as compact_err:
                st._clear_provider_compact_barrier()
                logger.error("[agent:%s] PawFlow compact failed: %s",
                             st.conversation_id[:8], compact_err)
                try:
                    from core.conversation_event_bus import ConversationEventBus as _CEB
                    _CEB.instance().publish_event(
                        st.conversation_id, "compact_progress",
                        {"stage": "error",
                         "agent": st._agent_name,
                         "error": str(compact_err)})
                except Exception:
                    logger.debug("exception suppressed", exc_info=True)
                st.emitter.on_fatal_error(f"Compact failed: {compact_err}")
                st._fatal_error = True
                st._fatal_error_msg = f"Compact failed: {compact_err}"
                return _ALC_BREAK
            # If generation changed while compacting, a real
            # cancel/restart happened. Do not let the compacting
            # thread re-adopt the newer generation: that resurrects
            # an old provider loop and creates ghost agents.
            st.emitter.check_cancelled()
            return _ALC_CONTINUE
        except Exception as llm_err:
            st.err_str = str(llm_err)
            # AgentCancelled may be wrapped in LLMClientError
            if "AgentCancelled" in st.err_str:
                raise AgentCancelled()
            # Budget exceeded — fatal, no retry
            if "Budget exceeded" in st.err_str:
                logger.warning("[agent:%s] %s", st.conversation_id[:8], st.err_str)
                st.emitter.on_fatal_error(st.err_str)
                st._fatal_error = True
                st._fatal_error_msg = st._fatal_error_msg or st.err_str
                return _ALC_BREAK
            # Claude-code resume failed → invalidate session, retry
            # with full context (first-message flow).
            # Stall kill = transparent retry, no cancel check.
            # Force stop = intentional cancel, raise AgentCancelled.
            st._is_stall = getattr(st.client, '_stall_killed', False)
            if st._is_stall:
                st.client._stall_killed = False  # reset for retry
            else:
                st._was_cancelled = False
                try:
                    st.emitter.check_cancelled()
                except AgentCancelled:
                    st._was_cancelled = True
                if st._was_cancelled:
                    raise AgentCancelled()
            st._is_auth_error = "auth" in st.err_str.lower() or "401" in st.err_str
            if st._is_claude_code and st.ctx.get("_claude_has_session") and not st._is_auth_error:
                # Hard-fail: when CC has an active session,
                # `messages` only carries [system_prompt +
                # last_user_msg] (agent_context skips load on
                # active session — CC owns the history).
                # Silently retrying with that anaemic context
                # WIPES every prior turn from the agent's view
                # (observed: 40% → 5% context drop after a
                # stray sweeper eviction). The only legitimate
                # context replaces are explicit /compact, CC's
                # own compact_boundary, or a context edit.
                # Anything else must surface as an error so
                # the user can decide — NEVER silently rebuild.
                #
                # Distinguish transport-kill from session-loss:
                # "exited with code N" / signal exit / EIO are
                # OS-level kills (server shutdown, OOM, network
                # blip, FUSE EIO). The session jsonl on disk is
                # intact and the next resume should succeed.
                # Only an explicit CC-side "session not found"
                # type error means the session is genuinely gone.
                # Wiping `claude_session:<agent>` on every kill
                # was the cause of post-restart NEW-SESSION
                # context-loss after a routine `^C` shutdown.
                st._is_transport_kill = (
                    "exited with code" in st.err_str
                    or "Stream stalled" in st.err_str
                    or "EIO" in st.err_str
                    or "stream interrupted" in st.err_str.lower()
                    or "broken pipe" in st.err_str.lower()
                )
                logger.error(
                    "[claude-code] resume failed (%s) — "
                                "hard-fail (silent context replace forbidden) "
                                "[transport_kill=%s, session_preserved=%s]",
                    st.err_str[:200], st._is_transport_kill,
                    st._is_transport_kill)
                if not st._is_transport_kill:
                    try:
                        from core.conversation_store import ConversationStore
                        st._an = st.ctx["active_agent_name"]
                        ConversationStore.instance().set_extra(
                            st.conversation_id, f"claude_session:{st._an}", "")
                    except Exception:
                        logger.debug("exception suppressed", exc_info=True)
                    st.ctx["_claude_has_session"] = False
                st.emitter.on_fatal_error(
                    f"Claude Code session lost: {st.err_str}"
                    if not st._is_transport_kill else
                    f"Claude Code stream interrupted: {st.err_str}")
                st._fatal_error = True
                st._fatal_error_msg = (
                    st._fatal_error_msg
                    or (f"Claude Code session lost: {st.err_str}"
                        if not st._is_transport_kill else
                        f"Claude Code stream interrupted: {st.err_str}"))
                return _ALC_BREAK
            if ("exceed_context_size" in st.err_str
                  or "n_prompt_tokens" in st.err_str
                  or "Prompt is too long" in st.err_str
                  or "prompt_too_long" in st.err_str):
                logger.warning(f"[agent:{st.conversation_id[:8]}] Context overflow, retrying...")
                st.emitter.on_overflow_retry(st.iteration)
                # Context too long: compact PawFlow ctx in
                # place (messages list is mutated + persisted)
                # and feed the compacted view to CC. CC ctx
                # and PawFlow ctx stay strictly identical.
                st._agent_for_compact = st.ctx.get("active_agent_name") or ""
                st._compacted = self._compact(
                    list(st.messages), st.compact_client,
                    st.ctx.get("max_context_size", 64000),
                    conversation_id=st.conversation_id,
                    agent_name=st._agent_for_compact,
                    tool_defs=st.ctx.get("tool_defs"),
                    chars_per_token=st.ctx.get("chars_per_token", 0),
                    user_id=st.user_id,
                    budget_config=getattr(st.ctx.get("resolved_svc"), "config", None),
                    independent_context=bool(st.ctx.get("_independent_context")))
                st._adopt_compacted_context(
                    st._compacted, reason="context_overflow")
                st.llm_context = list(st.messages)
                try:
                    _check_budget(
                        st.ctx, st.total_tokens_in, st.total_tokens_out,
                        st.total_cache_read, st.total_cache_write)
                    st.response = st._llm_call(st.llm_context)
                except Exception as retry_err:
                    try:
                        st.emitter.check_cancelled()
                    except AgentCancelled:
                        raise
                    logger.error(f"LLM retry failed: {retry_err}")
                    st.emitter.on_fatal_error(f"LLM call failed: {retry_err}")
                    st._fatal_error = True
                    st._fatal_error_msg = st._fatal_error_msg or f"LLM call failed: {retry_err}"
                    return _ALC_BREAK
            else:
                # Transient errors (500, 503, 529, timeout) — the LLMClient
                # already retried max_retries times. At the agent level, we
                # retry once more with a fresh call (new process for claude-code).
                st._transient = any(p in st.err_str for p in (
                    "500", "503", "502", "529", "overloaded", "timeout",
                    "Internal server error", "api_error", "server_error",
                    "rate_limit", "429"))
                if st._transient and not st.ctx.get("_agent_transient_retried"):
                    st.ctx["_agent_transient_retried"] = True
                    logger.warning("[agent:%s] transient LLM error, retrying: %s",
                                   st.conversation_id[:8], st.err_str[:150])
                    # For claude-code: invalidate session so retry starts fresh
                    if st._is_claude_code and st.ctx.get("_claude_has_session"):
                        try:
                            from core.conversation_store import ConversationStore
                            st._an = st.ctx["active_agent_name"]
                            ConversationStore.instance().set_extra(
                                st.conversation_id, f"claude_session:{st._an}", "")
                        except Exception:
                            logger.debug("exception suppressed", exc_info=True)
                        st.ctx["_claude_has_session"] = False
                        st.llm_context = list(st.messages)
                    time.sleep(5)
                    try:
                        _check_budget(
                            st.ctx, st.total_tokens_in, st.total_tokens_out,
                            st.total_cache_read, st.total_cache_write)
                        st.response = st._llm_call(st.llm_context)
                    except AgentCancelled:
                        raise
                    except Exception as retry_err:
                        logger.error("[agent:%s] transient retry also failed: %s",
                                     st.conversation_id[:8], retry_err)
                        st.emitter.on_fatal_error(f"LLM call failed after retry: {retry_err}")
                        st._fatal_error = True
                        st._fatal_error_msg = st._fatal_error_msg or f"LLM call failed: {retry_err}"
                        return _ALC_BREAK
                else:
                    logger.error(f"LLM call failed (iter {st.iteration}): {llm_err}")
                    st.emitter.on_fatal_error(f"LLM call failed: {llm_err}")
                    st._fatal_error = True
                    st._fatal_error_msg = st._fatal_error_msg or f"LLM call failed: {llm_err}"
                    return _ALC_BREAK
        finally:
            pass  # heartbeat stopped at iteration end
        return None

