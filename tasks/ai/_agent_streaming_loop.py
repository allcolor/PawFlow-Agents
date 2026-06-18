"""AgentStreamingMixin background-loop methods.

Extracted from ``tasks.ai.agent_streaming`` to keep that module <=800 lines.
The streamed agent loop (and title generation) compose onto
``AgentStreamingMixin`` via inheritance; ``_execute_streaming`` (the entry /
setup) stays in agent_streaming and launches these on a background thread.
Leaf module: it must not import agent_streaming (would be circular).
"""

import logging
import threading
import time
from typing import Dict

from core.llm_client import LLMMessage

logger = logging.getLogger(__name__)


class _AgentStreamingLoopMixin:
    """Background streamed-agent loop + title generation."""

    def _streaming_agent_loop(self, ctx: Dict, conversation_id: str, bus) -> None:
        """Background thread wrapper — guaranteed cleanup via finally."""
        try:
            self._streaming_agent_loop_inner(ctx, conversation_id, bus)
        except Exception as e:
            logger.error(f"[agent:{conversation_id[:8]}] streaming loop crashed: {e}", exc_info=True)
            try:
                _crash_agent = ctx.get("active_agent_name", "") or ""
                bus.publish_event(conversation_id, "error_event", {
                    "message": f"Agent loop crashed: {e}",
                    "agent_name": _crash_agent,
                })
            except Exception:
                logger.debug("exception suppressed", exc_info=True)
        finally:
            # Cancel any bg tasks still running for this conversation
            try:
                import core.background_tool as _bg
                for t in _bg.list_tasks(conversation_id):
                    if t["status"] == "running":
                        _bg.cancel(t["tc_id"])
                        logger.info("[agent:%s] cancelled bg task %s on exit",
                                    conversation_id[:8], t["tc_id"])
                # Purge unclaimed results (agent won't pick them up)
                for t in _bg.list_tasks(conversation_id):
                    _bg.pop_completed(conversation_id, t["tc_id"])
            except Exception:
                logger.debug("exception suppressed", exc_info=True)
            # Remove this turn from active tracking before scheduling pending
            # wakes. Otherwise the poller can consume the wake while the current
            # thread is still cleaning up, reschedule it as "active", and leave a
            # just-enqueued user message stuck until another external event.
            _agent_n2 = ctx.get("active_agent_name", "") or ""
            _gen_key2 = ctx.get("_gen_key", conversation_id)
            _generation2 = ctx.get("_generation", 0)
            _was_interrupted2 = not self._is_current_generation(_gen_key2, _generation2)
            self._decrement_active(conversation_id, ctx)
            try:
                from core.pending_queue import PendingQueue
                _pending_count2 = PendingQueue.for_agent(
                    conversation_id, _agent_n2).peek_count()
                if _pending_count2:
                    from tasks.ai.agent_loop import AgentLoopTask
                    _wake_reason = (
                        f"[pending] {_pending_count2} queued msg(s) after interrupted turn"
                        if _was_interrupted2 else
                        f"[pending] {_pending_count2} queued msg(s) after idle")
                    AgentLoopTask.wake_agent(
                        conversation_id, _agent_n2,
                        reason=_wake_reason,
                        user_id=ctx.get("user_id", ""),
                        delay=0.0,
                        even_if_active=True,
                    )
            except Exception:
                logger.debug("exception suppressed", exc_info=True)

    def _streaming_agent_loop_inner(self, ctx: Dict, conversation_id: str, bus) -> None:
        """Create StreamEmitter, delegate to _run_agent_loop, handle finally cleanup."""
        from core.conversation_store import ConversationStore
        from tasks.ai.agent_emitter import StreamEmitter

        # Redirect SSE for sub-conversations
        _sse_conv_id = ctx.get("_sse_conversation_id") or conversation_id
        _real_bus = bus
        if _sse_conv_id != conversation_id:
            class _RedirectBus:
                def publish_event(self, _cid, event_type, data=None):
                    _real_bus.publish_event(_sse_conv_id, event_type, data)
                def subscriber_count(self, _cid):
                    return _real_bus.subscriber_count(_sse_conv_id)
                def __getattr__(self, name):
                    return getattr(_real_bus, name)
            bus = _RedirectBus()

        gen_key = ctx.get("_gen_key", conversation_id)
        my_generation = ctx.get("_generation", 0)

        emitter = StreamEmitter(conversation_id, bus, ctx, self, gen_key, my_generation)

        _had_error = False
        try:
            ctx["_agent_loop_start_time"] = time.time()
            result = self._run_agent_loop(ctx, emitter)
            _had_error = getattr(result, "finish_reason", "") == "error"

            # If messages arrived during the last turn, re-trigger a new loop
            _retrig_flag = ctx.get("_retrigger_after_done")
            logger.info(
                "[agent:%s] post-loop retrigger-check: flag=%s had_error=%s "
                "finish_reason=%r",
                conversation_id[:8], bool(_retrig_flag), _had_error,
                getattr(result, "finish_reason", ""))
            if _retrig_flag and not _had_error:
                ctx.pop("_retrigger_after_done", None)
                logger.info("[agent:%s] re-triggering loop for queued messages",
                            conversation_id[:8])
                result = self._run_agent_loop(ctx, emitter)
                _had_error = getattr(result, "finish_reason", "") == "error"
                logger.info(
                    "[agent:%s] retrigger loop returned: had_error=%s "
                    "finish_reason=%r",
                    conversation_id[:8], _had_error,
                    getattr(result, "finish_reason", ""))

            # Set idle status

            # ── Auto-generate conversation title ──
            if not _had_error:
                self._maybe_generate_title(ctx, conversation_id, bus)

            # Memory extraction now happens exclusively in
            # core/bg_bucket_builder.py: each sealed bucket and each
            # rollup feeds the LLM extractor. The previous periodic
            # pass at 15-message intervals re-extracted text the
            # bucket pass already covered, producing near-duplicate
            # MemoryStore entries (different paraphrases of the
            # same fact) that escape remember()'s exact-text dedup.

        except Exception:
            _had_error = True
        finally:
            _was_interrupted = not self._is_current_generation(gen_key, my_generation)
            # PendingQueue is the single source of truth for pending work.
            # The outer streaming wrapper schedules a fresh wake after active
            # tracking is removed, so this block must not wake the poller while
            # the current thread is still visible as active.
            _agent_n = ctx.get("active_agent_name", "") or ""
            try:
                from core.pending_queue import PendingQueue
                _pending_count = PendingQueue.for_agent(
                    conversation_id, _agent_n).peek_count()
            except Exception:
                _pending_count = 0
            if _pending_count and not _was_interrupted:
                ctx["_pending_wake_after_idle"] = _pending_count

            # Auto-reschedule random thoughts
            _was_cancelled = not self._is_current_generation(gen_key, my_generation)
            if ctx.get("is_random_thought") and not _was_cancelled and not _had_error:
                try:
                    from core.poll_scheduler import PollScheduler as _PS
                    import random as _rng
                    _reasons = ctx.get("_scheduled_reasons", [])
                    _agents = set()
                    for _r in _reasons:
                        if "[random_thought]" in _r and "(" in _r:
                            _agents.add(_r.rsplit("(", 1)[-1].rstrip(")"))
                    if not _agents:
                        _an = ctx.get("active_agent_name", "")
                        if _an:
                            _agents = {_an}
                    _store = ConversationStore.instance()
                    for _ag in _agents:
                        _cfg = _store.get_extra(conversation_id, f"random_thought::{_ag.lower()}")
                        if _cfg and _cfg.get("enabled"):
                            _delay = _rng.randint(_cfg["min_interval"], _cfg["max_interval"])  # nosec B311
                            _PS.instance().schedule_delay(
                                conversation_id, _delay,
                                key=f"{conversation_id}::thought::{_ag.lower()}",
                                reason=f"[random_thought] spontaneous thought ({_ag})",
                                user_id=ctx.get("user_id", ""))
                            bus.publish_event(conversation_id, "thought_scheduled", {
                                "agent": _ag, "delay": _delay,
                                "frequency": _cfg.get("frequency", "")})
                except Exception as e:
                    logger.warning(f"[agent] Failed to reschedule thought: {e}")

            # Auto-reschedule active tasks (even on error — with backoff)
            if not _was_cancelled:
                try:
                    _store = ConversationStore.instance()
                    from core.poll_scheduler import PollScheduler as _PS2
                    # agent_tasks are stored on the parent conv, not the sub-conv
                    from core.service_registry import _parent_conversation_id
                    _parent_cid = (_parent_conversation_id(conversation_id)
                                   or conversation_id)
                    _all_tasks = _store.get_extra(_parent_cid, "agent_tasks") or {}
                    _ag_name = ctx.get("active_agent_name") or ""
                    _tasks_changed = False
                    # Accumulate total_cost from CostTracker and log iteration metrics
                    if "::task::" in conversation_id:
                        try:
                            from core.cost_tracker import CostTracker as _CT
                            _task_sub_cost = _CT.instance().get_conversation_cost(conversation_id)
                            _tid_cost = conversation_id.rsplit("::", 1)[-1]
                            if _tid_cost in _all_tasks:
                                _prev_cost = _all_tasks[_tid_cost].get("total_cost", 0.0)
                                _new_total = _task_sub_cost.get("total", 0.0)
                                _all_tasks[_tid_cost]["total_cost"] = _new_total
                                _tasks_changed = True
                                # Enrich task log with iteration metrics
                                try:
                                    from core.handlers.task_management import _append_task_log
                                    _iter_cost = max(0, _new_total - _prev_cost)
                                    _iter_start = ctx.get("_agent_loop_start_time", 0)
                                    _iter_duration = (time.time() - _iter_start) if _iter_start else 0
                                    # Sum tokens across all models
                                    _by_model = _task_sub_cost.get("by_model", {})
                                    _total_in = sum(m.get("in", 0) for m in _by_model.values())
                                    _total_out = sum(m.get("out", 0) for m in _by_model.values())
                                    # Compute delta from previous iteration
                                    _prev_tokens_in = _all_tasks[_tid_cost].get("_prev_tokens_in", 0)
                                    _prev_tokens_out = _all_tasks[_tid_cost].get("_prev_tokens_out", 0)
                                    _all_tasks[_tid_cost]["_prev_tokens_in"] = _total_in
                                    _all_tasks[_tid_cost]["_prev_tokens_out"] = _total_out
                                    _append_task_log(_parent_cid, _tid_cost, {
                                        "type": "iteration",
                                        "agent": _ag_name,
                                        "cost": round(_iter_cost, 6),
                                        "duration_secs": round(_iter_duration, 1),
                                        "tokens_in": _total_in - _prev_tokens_in,
                                        "tokens_out": _total_out - _prev_tokens_out,
                                        "had_error": _had_error,
                                    })
                                except Exception:
                                    logger.debug("exception suppressed", exc_info=True)
                        except Exception:
                            logger.debug("exception suppressed", exc_info=True)
                    for _tid, _task in _all_tasks.items():
                        if not isinstance(_task, dict) or _task.get("agent") != _ag_name:
                            continue
                        if _task.get("status") != "active":
                            continue
                        _iters = _task.get("reschedule_count", 0)
                        _max = _task.get("max_iterations", 0)
                        if _max > 0 and _iters >= _max:
                            # Remove instance — only task_def + log remain
                            del _all_tasks[_tid]
                            _tasks_changed = True
                            break  # dict changed, exit loop
                        _key = f"{_parent_cid}::task::{_tid}"
                        if _PS2.instance().get(_key):
                            continue
                        from core.tool_registry import AssignTaskHandler as _ATH
                        _normal_delay = _ATH._get_task_delay(_task)
                        if _had_error:
                            # Backoff on error: double delay, cap at 5 min
                            _err_count = _task.get("consecutive_errors", 0) + 1
                            _task["consecutive_errors"] = _err_count
                            _delay = min(_normal_delay * (2 ** _err_count), 300)
                            _task["last_result"] = f"Error (attempt {_err_count}): provider failed"
                            _all_tasks[_tid] = _task
                            _tasks_changed = True
                            logger.warning(
                                "[agent] Task %s error #%d, retry in %ds",
                                _tid, _err_count, _delay)
                        else:
                            _delay = _normal_delay
                            # Reset error counter on success
                            if _task.get("consecutive_errors"):
                                _task["consecutive_errors"] = 0
                                _all_tasks[_tid] = _task
                                _tasks_changed = True
                        _PS2.instance().schedule_delay(
                            _parent_cid, _delay,
                            key=_key,
                            reason=f"[agent_task:{_tid}] auto-reschedule ({_task.get('agent', _ag_name)})",
                            user_id=ctx.get("user_id", ""))
                    if _tasks_changed:
                        _store.set_extra(_parent_cid, "agent_tasks", _all_tasks)
                except Exception as e:
                    logger.warning(f"[agent] Failed to auto-reschedule tasks: {e}")

    def _maybe_generate_title(self, ctx: Dict, conversation_id: str, bus) -> None:
        """Spawn a background thread to generate a conversation title if needed.

        Conditions: title_llm_service configured AND conversation has no title yet.
        """
        title_svc_name = ctx.get("title_llm_service", "")
        if not title_svc_name:
            return
        use_conv_store = ctx.get("use_conv_store", False)
        if not use_conv_store or not conversation_id:
            return
        try:
            from core.conversation_store import ConversationStore
            existing_title = ConversationStore.instance().get_extra(
                conversation_id, "title",
                user_id=ctx.get("user_id", ""),
            )
            if existing_title:
                return  # already has a title
        except Exception:
            return

        def _bg_generate_title():
            """Background thread: call title LLM and publish result."""
            try:
                title_client, title_svc_id = self._get_title_client(
                    ctx.get("user_id", ""),
                    ctx.get("conversation_id", ""))
                if not title_client:
                    logger.debug("[title] service '%s' could not be resolved", title_svc_name)
                    return

                # Extract last 1000 chars of context
                messages = ctx.get("messages", [])
                context_text = ""
                for m in reversed(messages):
                    content = m.content if isinstance(m.content, str) else str(m.content)
                    context_text = content + "\n" + context_text
                    if len(context_text) >= 1000:
                        break
                context_text = context_text[-1000:]

                prompt = (
                    f"Conversation context:\n{context_text}\n\n"
                    "Generate a concise 3-7 word title for this conversation. "
                    "Reply with ONLY the title, no quotes."
                )

                resp = title_client.complete(
                    [LLMMessage(role="user", content=prompt,
                                 conversation_id=conversation_id)],
                    max_tokens=30, temperature=0.3,
                    call_user_id=ctx.get("user_id", ""),
                    call_conversation_id=conversation_id,
                    call_agent_name="title",
                    call_event_cid="",
                    call_ephemeral_stream=True,
                )
                title = (resp.content or "").strip().strip('"\'')
                if not title:
                    return

                # Track token usage
                try:
                    from core.token_tracker import TokenTracker
                    if resp.tokens_in > 0:
                        TokenTracker.instance().track(
                            ctx.get("user_id", "system"),
                            resp.tokens_in, resp.tokens_out,
                            model=resp.model or "",
                            agent_name=ctx.get("active_agent_name", "title"),
                            llm_service=title_svc_id,
                            cache_read=getattr(resp, "cache_read_tokens", 0),
                            cache_write=getattr(resp, "cache_creation_tokens", 0))
                        TokenTracker.instance().flush()
                except Exception:
                    logger.debug("exception suppressed", exc_info=True)

                # Save title
                from core.conversation_store import ConversationStore
                ConversationStore.instance().set_extra(
                    conversation_id, "title", title,
                    user_id=ctx.get("user_id", ""),
                )

                # Publish SSE event
                bus.publish_event(conversation_id, "conversation_title", {
                    "conversation_id": conversation_id,
                    "title": title,
                })
                logger.info("[title] generated for %s: %s", conversation_id[:8], title)
            except Exception as e:
                logger.debug("[title] generation failed for %s: %s",
                             conversation_id[:8], e)

        threading.Thread(
            target=_bg_generate_title, daemon=True,
            name=f"title-gen-{conversation_id[:8]}",
        ).start()
