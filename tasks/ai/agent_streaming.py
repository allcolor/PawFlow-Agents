"""AgentLoopTask mixin — streaming agent execution.

Thread spawning, ACK return. The actual loop logic is in agent_core.py
(_run_agent_loop).
"""
import json
import logging
import threading
import time
from typing import Dict, Any, List, Optional


# One-shot epoch taken when the module is first imported. Included in
# every /api/agent ack so the client can detect a server restart: the
# UI's SSE may believe it's still connected (half-open TCP) while the
# new process has no subscribers for its conversation. On mismatch the
# client force-reconnects SSE and picks up the buffered events.
SERVER_START_TIME = time.time()

from core import FlowFile
from core.llm_client import (
    LLMClient, LLMMessage, LLMResponse, LLMToolDefinition,
    LLMToolCall, LLMToolResult, LLMClientError,
)
from core.tool_registry import ToolRegistry, create_default_registry
from tasks.ai.agent_exceptions import AgentCancelled, _InterruptComplete


logger = logging.getLogger(__name__)

from tasks.ai.agent_sync import AgentSyncMixin
from tasks.ai.agent_side_channels import AgentSideChannelsMixin


class AgentStreamingMixin(AgentSyncMixin, AgentSideChannelsMixin):
    """Streaming agent execution + sync + side channels."""

    def _execute_streaming(self, flowfile: FlowFile) -> List[FlowFile]:
        """Streaming mode: returns ACK immediately, runs loop in background thread.

        _prepare_agent_context (which may compact) runs in the background
        thread, NOT here. This method returns in < 1s.
        """
        import time as _t_stream
        _stream_t0 = _t_stream.monotonic()
        def _stream_mark(label):
            _dt = (_t_stream.monotonic() - _stream_t0) * 1000
            if _dt > 200:
                logger.info("[stream-timing] %s: +%.0fms", label, _dt)

        def _stream_step(label, started, **extra):
            _step_ms = (_t_stream.monotonic() - started) * 1000
            _total_ms = (_t_stream.monotonic() - _stream_t0) * 1000
            if _step_ms > 50 or _total_ms > 200:
                _suffix = "".join(
                    f" {k}={v}" for k, v in extra.items()
                    if v is not None)
                logger.info(
                    "[stream-timing] %s step_ms=%.1f total_ms=%.1f%s",
                    label, _step_ms, _total_ms, _suffix)
        from core.conversation_event_bus import ConversationEventBus
        from core.conversation_store import ConversationStore
        _stream_mark("imports")

        # Parse body for conversation_id and user text (lightweight, no LLM)
        _original_content = flowfile.get_content()
        raw = _original_content.decode("utf-8", errors="replace")
        try:
            _body = json.loads(raw) if raw.strip().startswith("{") else {}
        except (json.JSONDecodeError, TypeError):
            _body = {}
        conversation_id = (
            _body.get("conversation_id")
            or flowfile.get_attribute("agent.conversation_id")
            or ""
        )
        _user_text = _body.get("message", "")
        _target = _body.get("target_agent", "") or _body.get("agent_name", "")
        _attachments_body = _body.get("attachments", []) if isinstance(_body, dict) else []
        if (_user_text.strip() or _attachments_body) and not _target:
            flowfile.set_content(json.dumps({
                "error": "target_agent is required for user messages",
            }).encode("utf-8"))
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        _user_msg_id = _body.get("msg_id", "")
        if _user_msg_id:
            flowfile.set_attribute("_user_msg_id", _user_msg_id)
        bus = ConversationEventBus.instance()

        _stream_mark("body_parsed")

        # If agent thread already running FOR THIS AGENT, preempt or queue
        _agent_key = f"{conversation_id}:{_target}" if _target else conversation_id
        _thread_name = f"agent-stream-{_agent_key}"
        _already_active = any(
            t.is_alive() and t.name == _thread_name
            for t in threading.enumerate())
        _stream_mark("active_check")
        # A live agent thread can temporarily have no _active_contexts entry:
        # context preparation, provider compact/restart, and final cleanup all
        # run outside _run_agent_loop's push/pop window. Treat the thread name
        # as authoritative here and queue/preempt below; killing it as a
        # "zombie" creates duplicate ghost loops while the old thread can still
        # flush callbacks into the transcript.
        if _already_active:
            with self._active_contexts_lock:
                if _agent_key not in self._active_contexts:
                    logger.info(
                        "[agent:%s] active thread has no context yet — treating as busy",
                        conversation_id[:8])

        # Source of truth: persist the user message to the transcript
        # IMMEDIATELY, before any routing decision (fresh turn, preempt
        # via stdin, or PendingQueue). This MUST run on every path —
        # otherwise a fresh-turn user message exists only in CC's
        # in-memory session and is lost from PawFlow's on-disk state
        # (transcript/shared/agent ctx) the moment CC compacts or dies.
        from core.conversation_writer import ConversationWriter
        from core.llm_client import stamp_message
        _uid = flowfile.get_attribute("http.auth.principal") or ""
        _stamped_user = None
        _skip_pre_persist = bool(flowfile.get_attribute("skip_pre_persist"))
        if _user_text.strip() or _attachments_body:
            _stamped_user = stamp_message({
                "role": "user",
                "content": _user_text,
                "source": {"type": "user", "name": _uid,
                           "target_agent": _target or None},
                "msg_id": _user_msg_id or None,
                "channel": "web",
            }, conversation_id)
            _stream_mark("stamped")
            if _attachments_body:
                _stamped_user["attachments"] = _attachments_body
            try:
                from core.agent_hooks import AgentHookRunner
                _hook_started = _t_stream.monotonic()
                _pre_user = AgentHookRunner(
                    user_id=_uid,
                    conversation_id=conversation_id,
                    agent_name=_target or "",
                ).run("pre_user_message", {
                    "message": dict(_stamped_user),
                    "content": _stamped_user.get("content", ""),
                    "attachments": _attachments_body,
                    "target_agent": _target or "",
                    "channel": "web",
                }, fail_policy="closed")
                _stream_step(
                    "pre_user_hook",
                    _hook_started,
                    decision=_pre_user.get("decision"))
                if _pre_user.get("decision") == "block":
                    reason = _pre_user.get("reason") or "blocked by hook"
                    flowfile.set_content(json.dumps({"error": reason}).encode())
                    flowfile.set_attribute("http.response.status", "403")
                    return [flowfile]
                if _pre_user.get("decision") == "replace":
                    _payload = _pre_user.get("payload") or {}
                    _msg = _payload.get("message")
                    if isinstance(_msg, dict):
                        _stamped_user.update(_msg)
                    elif "content" in _payload:
                        _stamped_user["content"] = _payload.get("content")
                    if isinstance(_stamped_user.get("content"), str):
                        _user_text = _stamped_user.get("content") or ""
                        if isinstance(_body, dict):
                            _body["message"] = _user_text
                            flowfile.set_content(json.dumps(_body).encode("utf-8"))
                flowfile.set_attribute("pre_user_message_hook_applied", "1")
            except Exception as _hook_err:
                flowfile.set_content(json.dumps({
                    "error": f"pre_user_message hook failed: {_hook_err}",
                }).encode())
                flowfile.set_attribute("http.response.status", "403")
                return [flowfile]
            if not _skip_pre_persist:
                try:
                    _writer_started = _t_stream.monotonic()
                    _cw = ConversationWriter.for_conversation(conversation_id)
                    _stream_step("writer_obtained", _writer_started)
                    _enqueue_started = _t_stream.monotonic()
                    _cw.enqueue_message(
                        dict(_stamped_user), agent_name=_target or "",
                        user_id=_uid)
                    _stream_step("pre_persist_enqueue", _enqueue_started)
                except Exception as _pe:
                    logger.warning(
                        "[agent:%s] pre-persist user message failed: %s",
                        conversation_id[:8], _pe)

        def _ensure_stamped_user():
            nonlocal _stamped_user
            if _stamped_user is None and (_user_text.strip() or _attachments_body):
                _stamped_user = stamp_message({
                    "role": "user",
                    "content": _user_text,
                    "source": {"type": "user", "name": _uid,
                               "target_agent": _target or None},
                    "msg_id": _user_msg_id or None,
                    "channel": "web",
                }, conversation_id)
                if _attachments_body:
                    _stamped_user["attachments"] = _attachments_body
            return _stamped_user

        def _queue_pending_user(source: str, publish: bool = True) -> bool:
            msg = _ensure_stamped_user()
            if not msg:
                return False
            from core.pending_queue import PendingQueue
            PendingQueue.for_agent(conversation_id, _target or "").enqueue(
                dict(msg), source=source)
            if publish:
                bus.publish_event(conversation_id, "message_queued", {
                    "conversation_id": conversation_id})
            return True

        _fast_restart_after_preempt = False
        if _already_active:
            # Sticky-mode rule: only preempt the running turn if the
            # incoming trigger matches its mode + source. Mismatches go
            # to the queue so the current turn finishes with a coherent
            # reply tag (you can't mix a user message into a
            # delegate_reply turn, etc.).
            _incoming_mode = {"type": "user", "source_agent": None}
            try:
                _raw_ms = flowfile.get_attribute("message_source") or ""
                if _raw_ms:
                    _ms = json.loads(_raw_ms) if isinstance(_raw_ms, str) else _raw_ms
                    if isinstance(_ms, dict) and _ms.get("type") == "agent_delegate":
                        _incoming_mode = {
                            "type": "delegate_reply",
                            "source_agent": _ms.get("from", ""),
                        }
            except Exception:
                logger.debug("exception suppressed", exc_info=True)
            with self._active_contexts_lock:
                _active_client = self._active_claude_client.get(_agent_key)
                _active_ctx = self._active_contexts.get(_agent_key) or {}
                _active_turn = self._active_turns.get(_agent_key) or {}
            _running_mode = _active_ctx.get("_turn_mode") or {
                "type": "user", "source_agent": None}
            _modes_match = (
                _incoming_mode.get("type") == _running_mode.get("type")
                and _incoming_mode.get("source_agent") == _running_mode.get("source_agent")
            )
            if not _modes_match:
                logger.info(
                    "[agent:%s] mode mismatch (incoming=%s/%s, running=%s/%s) "
                    "→ queuing for next turn",
                    conversation_id[:8],
                    _incoming_mode.get("type"),
                    _incoming_mode.get("source_agent") or "-",
                    _running_mode.get("type"),
                    _running_mode.get("source_agent") or "-")

            if (_active_client and getattr(_active_client, 'supports_live_preempt', False)
                    and hasattr(_active_client, 'send_user_message')
                    and _user_text and _modes_match):
                _attachments = _body.get("attachments", [])
                if _active_client.send_user_message(
                    _user_text,
                    attachments=_attachments,
                    user_id=_uid,
                    conversation_id=conversation_id,
                    agent_name=_target,
                ):
                    _rescued = _queue_pending_user(source="preempt_rescue", publish=False)
                    logger.info(
                        "[agent:%s] preempted active provider session%s",
                        conversation_id[:8],
                        "; queued rescue" if _rescued else "")
                    ack = json.dumps({"status": "accepted", "conversation_id": conversation_id,
                                      "message_count": ConversationStore.instance().message_count(conversation_id),
                                      "server_start_time": SERVER_START_TIME})
                    flowfile.set_content(ack.encode("utf-8"))
                    flowfile.set_attribute("agent.conversation_id", conversation_id)
                    return [flowfile]
                else:
                    # send_user_message returned False. For Codex/Gemini this
                    # can mean the provider killed the one-shot CLI and needs
                    # an immediate resume turn. For non-kill cases we keep the
                    # existing queue-and-wait behavior.
                    _preempt_killed = bool(
                        getattr(_active_client, "_preempt_killed", False)
                        or getattr(_active_client, "_fast_restart_after_preempt", False)
                    )
                    if _preempt_killed:
                        _fast_restart_after_preempt = True
                        with self._conv_gen_lock:
                            self._conv_generation[_agent_key] = (
                                self._conv_generation.get(_agent_key, 0) + 1)
                        logger.info(
                            "[agent:%s] preempt killed provider CLI — fast-restarting %s",
                            conversation_id[:8], _target or "default")
                    _already_active = False
                    with self._active_contexts_lock:
                        self._active_claude_client.pop(_agent_key, None)
                        if _preempt_killed:
                            self._active_contexts.pop(_agent_key, None)

            if (not _active_client and _active_turn and _user_text
                    and _modes_match):
                # The thread exists, but the provider client is not currently
                # published to _active_claude_client. This can happen while the
                # worker is between context prep and provider registration, or
                # while a CLI provider live session is still settling. Bumping
                # the generation here cancels the running worker and makes CLI
                # providers tear down their live container. Queue instead: the
                # active turn will drain the message or the poller will wake the
                # agent after the turn completes.
                logger.info(
                    "[agent:%s] active turn not preemptable yet — queuing for next drain",
                    conversation_id[:8])

            if _fast_restart_after_preempt:
                # The stale/preempted worker has been generation-cancelled and
                # this same FlowFile will seed the fresh loop below. Do not also
                # enqueue it: that would make the new loop wait for a
                # PendingQueue drain and can duplicate the same msg_id.
                flowfile.set_attribute("agent.fast_restart_after_preempt", "true")
            else:
                # Queue this user message in the agent's PendingQueue —
                # the active turn will drain at its end, or a wake will
                # fire if the turn somehow ended before we got here.
                _queue_pending_user(source="http")
                ack = json.dumps({"status": "queued", "conversation_id": conversation_id,
                                  "message_count": ConversationStore.instance().message_count(conversation_id),
                                  "server_start_time": SERVER_START_TIME})
                flowfile.set_content(ack.encode("utf-8"))
                flowfile.set_attribute("agent.conversation_id", conversation_id)
                return [flowfile]

        # Mark active before context preparation. Active Agents is a PawFlow
        # execution-state panel, so it must stay correct while compact/context
        # loading runs before _run_agent_loop pushes _active_contexts.
        _active_agent_guess = _target
        if not _active_agent_guess and conversation_id:
            try:
                _ares = ConversationStore.instance().get_extra(
                    conversation_id, "active_resources") or {}
                _active_agent_guess = _ares.get("agent", "") or ""
            except Exception:
                logger.debug("exception suppressed", exc_info=True)
        _active_turn_key = (
            f"{conversation_id}:{_active_agent_guess}"
            if _active_agent_guess else conversation_id
        )
        _active_turn_started = time.time()
        _gen_key = f"{conversation_id}:{_target}" if _target else conversation_id
        with self._conv_gen_lock:
            _starting_generation = self._conv_generation.get(_gen_key, 0)
        with self._active_lock:
            self._active_conversations[conversation_id] = self._active_conversations.get(conversation_id, 0) + 1
            self._user_active_conversations.add(conversation_id)
        with self._active_contexts_lock:
            self._active_turns[_active_turn_key] = {
                "conversation_id": conversation_id,
                "agent_name": _active_agent_guess,
                "started_at": _active_turn_started,
                "status": "preparing",
                "message_preview": _user_text[:160],
                "generation": _starting_generation,
            }

        if _target:
            bus.publish_event(conversation_id, "thinking", {
                "conversation_id": conversation_id, "agent_name": _target,
            })

        # Clone flowfile for background thread (main thread overwrites with ack)
        from core import FlowFile as _FF
        _bg_ff = _FF(content=flowfile.get_content(),
                      attributes=dict(flowfile.attributes))

        # Background thread: prepare context (may compact), then run agent loop
        def _bg_streaming():
            nonlocal _active_turn_key
            logger.info("[agent:%s] bg_streaming started for %s",
                        conversation_id[:8], _target or "(default)")
            try:
                ctx = self._prepare_agent_context(_bg_ff)
            except Exception as e:
                logger.error("[agent:%s] prepare_context failed: %s",
                             conversation_id[:8], e, exc_info=True)
                # Resolve agent name for error events
                _err_agent = _target
                if not _err_agent:
                    try:
                        _ares = ConversationStore.instance().get_extra(
                            conversation_id, "active_resources") or {}
                        _err_agent = _ares.get("agent", "")
                    except Exception:
                        logger.debug("exception suppressed", exc_info=True)
                bus.publish_event(conversation_id, "compact_progress", {
                    "stage": "error", "error": str(e),
                })
                bus.publish_event(conversation_id, "error_event", {
                    "message": f"Context preparation failed: {e}",
                    "agent_name": _err_agent,
                })
                bus.publish_event(conversation_id, "done", {
                    "agent_name": _err_agent, "response": "",
                    "finish_reason": "error",
                })
                self._decrement_active(conversation_id, {
                    "active_agent_name": _err_agent or _active_agent_guess,
                    "_active_turn_key": _active_turn_key,
                })
                return

            _resolved_agent = ctx.get("active_agent_name", "") or _active_agent_guess
            _resolved_turn_key = (
                f"{conversation_id}:{_resolved_agent}"
                if _resolved_agent else conversation_id
            )
            ctx["_generation"] = _starting_generation
            ctx["_gen_key"] = _gen_key
            ctx["_active_turn_key"] = _active_turn_key

            if not self._is_current_generation(_gen_key, _starting_generation):
                logger.info(
                    "[agent:%s] abandoning preempted preparing turn for %s",
                    conversation_id[:8], _resolved_agent or "default")
                self._decrement_active(conversation_id, ctx)
                return

            with self._active_contexts_lock:
                _turn = self._active_turns.pop(_active_turn_key, None) or {}
                _turn.update({
                    "conversation_id": conversation_id,
                    "agent_name": _resolved_agent,
                    "started_at": _turn.get("started_at", _active_turn_started),
                    "status": "thinking",
                    "message_preview": _turn.get("message_preview", _user_text[:160]),
                    "max_rounds": ctx.get("max_rounds", 0),
                    "generation": _starting_generation,
                })
                self._active_turns[_resolved_turn_key] = _turn
            _active_turn_key = _resolved_turn_key
            ctx["_active_turn_key"] = _active_turn_key

            self._streaming_agent_loop(ctx, conversation_id, bus)

        _stream_mark("before_thread_start")
        thread = threading.Thread(
            target=_bg_streaming, daemon=True,
            name=_thread_name)
        _thread_started = _t_stream.monotonic()
        thread.start()
        _stream_step("thread_started", _thread_started)
        logger.info("[agent:%s] bg thread started: %s", conversation_id[:8], _thread_name)

        # Start poller if configured
        poll_interval = int(self.config.get("poll_interval", 0))
        if poll_interval > 0 and not self._poller_started:
            self._poller_started = True
            threading.Thread(
                target=self._poll_conversations, args=(poll_interval,),
                daemon=True, name="agent-poller").start()
            logger.info(f"Agent poller started (interval={poll_interval}s)")

        _ack_started = _t_stream.monotonic()
        ack = json.dumps({"status": "accepted", "conversation_id": conversation_id,
                          "message_count": ConversationStore.instance().message_count(conversation_id),
                          "server_start_time": SERVER_START_TIME})
        _stream_step("ack_message_count", _ack_started)
        _stream_mark("ack_built")
        flowfile.set_content(ack.encode("utf-8"))
        flowfile.set_attribute("agent.conversation_id", conversation_id)
        flowfile.set_attribute("agent.streaming", "true")
        return [flowfile]

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
        from core.conversation_event_bus import ConversationEventBus
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
            use_conv_store = ctx.get("use_conv_store", False)

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
                    _parent_cid = conversation_id.split("::task::")[0] if "::task::" in conversation_id else conversation_id
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
                    ctx.get("user_id", ""))
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
