"""AgentLoopTask mixin — streaming agent execution.

Thread spawning, ACK return, narration functions.
The actual loop logic is in agent_core.py (_run_agent_loop).
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


def _synthesize_narration(tool_calls: List[LLMToolCall]) -> str:
    """Build a short narration string from tool_calls when the LLM didn't provide text."""
    if not tool_calls:
        return ""
    _VERBS = {
        "generate_image": ("Generating", "image"),
        "web_search": ("Searching the web", None),
        "fetch": ("Fetching", "page"),
        "execute_script": ("Running", "script"),
        "share_file": ("Sharing", "file"),
        "schedule_continuation": ("Scheduling continuation", None),
        "delegate": ("Delegating to", "agent"),
        # Split filesystem tools — tool name IS the action
        "read": ("Reading", "file"),
        "write": ("Writing", "file"),
        "edit": ("Editing", "file"),
        "batch_edit": ("Batch editing", "file"),
        "apply_patch": ("Applying", "patch"),
        "find_replace": ("Find & replace", None),
        "delete": ("Deleting", "file"),
        "mkdir": ("Creating", "directory"),
        "stat": ("Checking", "file"),
        "exists": ("Checking existence", None),
        "list_dir": ("Listing", "directory"),
        "glob": ("Searching", "file"),
        "grep": ("Searching file contents", None),
        "bash": ("Running", "command"),
        "notebook_edit": ("Editing", "notebook"),
        "copy": ("Copying", "file"),
    }
    counts = {}
    for tc in tool_calls:
        counts[tc.name] = counts.get(tc.name, 0) + 1
    parts = []
    for name, count in counts.items():
        if name in _VERBS:
            v = _VERBS[name]
            if v is None:
                continue
            verb, noun = v
            if noun and count > 1:
                parts.append(f"{verb} {count} {noun}s")
            else:
                parts.append(verb)
        else:
            parts.append(f"{name} ({count})" if count > 1 else name)
    if not parts:
        return ""
    return ", ".join(parts) + ".\n"


def _narrate_tool_calls(tool_calls, ctx, bus, conversation_id, agent_name, source,
                        msg_id=""):
    """Call narrator service if configured. No fallback — no narrator = silence."""
    narrator_svc_name = ctx.get("narrator_service", "")
    if not narrator_svc_name:
        return ""  # No narrator configured → tools execute silently
    narration = _call_narrator(narrator_svc_name, tool_calls, ctx, conversation_id)
    if narration:
        # Persist first, then fire SSE (visible ⇒ persisted). Narrator
        # message is display-only → transcript only per router rules.
        try:
            from core.conversation_writer import ConversationWriter
            from core.llm_client import stamp_message
            _narr_msg = stamp_message({
                "role": "assistant",
                "content": narration,
                "source": {**(source or {}), "narrator": True},
                "msg_id": msg_id,
                "display_only": True,
            }, conversation_id)
            _narr_sse = {
                "type": "narration",
                "data": {
                    "text": narration, "agent_name": agent_name,
                    "msg_id": msg_id,
                    "source": source,
                },
            }
            ConversationWriter.for_conversation(conversation_id).enqueue_message(
                _narr_msg, agent_name=agent_name,
                sse_events=[_narr_sse])
        except Exception as _pe:
            logging.getLogger(__name__).error(
                "[narrator] persist failed: %s", _pe, exc_info=True)
    return narration


def _track_narrator(resp, ctx):
    """Track narrator token usage (best-effort)."""
    if not resp or resp.tokens_in <= 0:
        return
    try:
        from core.token_tracker import TokenTracker
        TokenTracker.instance().track(
            ctx.get("user_id", "system"), resp.tokens_in, resp.tokens_out,
            model=resp.model or "", agent_name=ctx.get("active_agent_name", "narrator"),
            llm_service=ctx.get("narrator_service", "narrator"))
        TokenTracker.instance().flush()
    except Exception:
        logger.debug("exception suppressed", exc_info=True)


def _call_narrator(svc_name: str, tool_calls, ctx, conversation_id: str = "") -> str:
    """Call a narrator LLM to describe what the agent is doing."""
    try:
        from core.service_registry import ServiceRegistry
        svc = ServiceRegistry.get_instance().get_live_instance("global", "", svc_name)
        if not svc:
            logging.getLogger(__name__).info(f"[narrator] service '{svc_name}' NOT FOUND")
            return ""
        logging.getLogger(__name__).info(f"[narrator] calling service '{svc_name}'")

        # Format tool calls with enough detail for meaningful narration
        _KEY_LIMITS = {"command": 500, "code": 500, "prompt": 300,
                       "content": 300, "path": 200, "query": 200}
        def _fmt(args):
            return ", ".join(f"{k}={str(v)[:_KEY_LIMITS.get(k, 80)]}"
                             for k, v in args.items())
        tools_desc = "\n".join(
            f"  - {tc.name}({_fmt(tc.arguments)})"
            for tc in tool_calls[:8])
        if len(tool_calls) > 8:
            tools_desc += f"\n  - ... +{len(tool_calls) - 8} more"

        # Give context: agent name + last user message
        agent_name = ctx.get("active_agent_name", "the agent")
        last_user_msg = ""
        for m in reversed(ctx.get("messages", [])):
            if m.role == "user":
                content = m.content if isinstance(m.content, str) else str(m.content)
                last_user_msg = content[:200]
                break

        prompt = (
            f"Agent '{agent_name}' is executing these tool calls:\n{tools_desc}\n\n"
            + (f"Context — the user asked: \"{last_user_msg}\"\n\n" if last_user_msg else "")
            + "Describe what the agent is doing in 1-2 short sentences. "
            "Be specific about the actual action and its purpose. "
            "Don't say 'the agent' — speak as if narrating: 'Reading the config file to check...'\n\n"
            "IMPORTANT: You MUST output a response. Even a single sentence is fine. "
            "Do NOT output nothing.")

        # Sync call with short timeout — narrator must not block the agent loop
        import concurrent.futures
        _NARRATOR_TIMEOUT = 4  # seconds
        with concurrent.futures.ThreadPoolExecutor(1) as pool:
            future = pool.submit(svc.complete,
                                 [LLMMessage(role="user", content=prompt,
                                              conversation_id=conversation_id)],
                                 None, 0.3, 150)
            try:
                resp = future.result(timeout=_NARRATOR_TIMEOUT)
            except concurrent.futures.TimeoutError:
                logging.getLogger(__name__).info("[narrator] timed out (>%ds), skipping", _NARRATOR_TIMEOUT)
                return ""
        _track_narrator(resp, ctx)
        text = (resp.content or "").strip()
        return text + "\n" if text and not text.endswith("\n") else text
    except Exception as e:
        logging.getLogger(__name__).warning("[narrator] service '%s' failed: %s", svc_name, e)
        return ""


def _call_narrator_with_client(client, tool_calls, ctx=None,
                                 conversation_id: str = "") -> str:
    """Use the current LLM client to narrate tool_calls in one sentence."""
    if not client:
        return ""
    try:
        _KEY_LIMITS = {"command": 300, "code": 300, "prompt": 150}
        def _fmt(args):
            return ", ".join(f"{k}={str(v)[:_KEY_LIMITS.get(k, 50)]}" for k, v in args.items())
        tools_desc = "; ".join(f"{tc.name}({_fmt(tc.arguments)})" for tc in tool_calls[:5])
        if len(tool_calls) > 5:
            tools_desc += f"; ... +{len(tool_calls) - 5} more"
        prompt = (
            f"The AI agent is about to call these tools: {tools_desc}\n"
            f"Write ONE short sentence (max 15 words) describing what it's doing. "
            f"Be specific about the actual action, not generic. Write only the sentence.")
        resp = client.complete([LLMMessage(role="user", content=prompt,
                                             conversation_id=conversation_id)],
                                max_tokens=50, temperature=0.3)
        if ctx:
            _track_narrator(resp, ctx)
        text = (resp.content or "").strip()
        return text + "\n" if text and not text.endswith("\n") else text
    except Exception as e:
        logging.getLogger(__name__).debug("Narrator via current LLM failed: %s", e)
        return ""


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
        from core.conversation_event_bus import ConversationEventBus
        from core.conversation_store import ConversationStore
        _stream_mark("imports")

        # Parse body for conversation_id and user text (lightweight, no LLM)
        raw = flowfile.get_content().decode("utf-8", errors="replace")
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
        _target = _body.get("target_agent", "")
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
        # Safety: if thread is "active" but no context entry, it's a zombie.
        # Kill its CC process + release pool slot before starting a new loop.
        if _already_active:
            with self._active_contexts_lock:
                if _agent_key not in self._active_contexts:
                    logger.warning("[agent:%s] zombie thread detected — killing CC process", conversation_id[:8])
                    # Kill any orphaned CC process for this agent
                    try:
                        _zombie_cc = self._active_claude_client.get(_agent_key)
                        if _zombie_cc and hasattr(_zombie_cc, 'cancel_claude_code'):
                            _zombie_cc.cancel_claude_code(force=True)
                            logger.info("[agent:%s] zombie CC process killed", conversation_id[:8])
                        self._active_claude_client.pop(_agent_key, None)
                    except Exception as _ze:
                        logger.debug("[agent:%s] zombie cleanup failed: %s", conversation_id[:8], _ze)
                    _already_active = False

        # Source of truth: persist the user message to the transcript
        # IMMEDIATELY, before any routing decision (fresh turn, preempt
        # via stdin, or PendingQueue). This MUST run on every path —
        # otherwise a fresh-turn user message exists only in CC's
        # in-memory session and is lost from PawFlow's on-disk state
        # (transcript/shared/agent ctx) the moment CC compacts or dies.
        from core.conversation_writer import ConversationWriter
        from core.llm_client import stamp_message
        _uid = flowfile.get_attribute("http.auth.principal") or ""
        _attachments_body = _body.get("attachments", []) if isinstance(_body, dict) else []
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
            if not _skip_pre_persist:
                try:
                    _cw = ConversationWriter.for_conversation(conversation_id)
                    _stream_mark("writer_obtained")
                    _cw.enqueue_message(
                        dict(_stamped_user), agent_name=_target or "",
                        user_id=_uid)
                    _stream_mark("pre_persist")
                except Exception as _pe:
                    logger.warning(
                        "[agent:%s] pre-persist user message failed: %s",
                        conversation_id[:8], _pe)

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

            if (_active_client and hasattr(_active_client, 'send_user_message')
                    and _user_text and _modes_match):
                _attachments = _body.get("attachments", [])
                if _active_client.send_user_message(_user_text, attachments=_attachments):
                    logger.debug("[agent:%s] preempted active CC session", conversation_id[:8])
                    ack = json.dumps({"status": "accepted", "conversation_id": conversation_id,
                                      "message_count": ConversationStore.instance().message_count(conversation_id),
                                      "server_start_time": SERVER_START_TIME})
                    flowfile.set_content(ack.encode("utf-8"))
                    flowfile.set_attribute("agent.conversation_id", conversation_id)
                    return [flowfile]
                else:
                    # send_user_message returned False — proc dead, compacting,
                    # or CC already emitted its final result. Fall through to
                    # the PendingQueue path to trigger a new turn. The user
                    # message is already on disk (pre-persisted above).
                    _already_active = False
                    with self._active_contexts_lock:
                        self._active_claude_client.pop(_agent_key, None)

            # Queue this user message in the agent's PendingQueue —
            # the active turn will drain at its end, or a wake will
            # fire if the turn somehow ended before we got here.
            from core.pending_queue import PendingQueue
            if _stamped_user is None:
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
            PendingQueue.for_agent(conversation_id, _target or "").enqueue(
                dict(_stamped_user), source="http")
            bus.publish_event(conversation_id, "message_queued", {"conversation_id": conversation_id})
            ack = json.dumps({"status": "queued", "conversation_id": conversation_id,
                              "message_count": ConversationStore.instance().message_count(conversation_id),
                              "server_start_time": SERVER_START_TIME})
            flowfile.set_content(ack.encode("utf-8"))
            flowfile.set_attribute("agent.conversation_id", conversation_id)
            return [flowfile]

        # Mark active
        with self._active_lock:
            self._active_conversations[conversation_id] = self._active_conversations.get(conversation_id, 0) + 1
            self._user_active_conversations.add(conversation_id)

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
                with self._active_lock:
                    self._active_conversations[conversation_id] = max(0,
                        self._active_conversations.get(conversation_id, 1) - 1)
                return

            _gen_key = f"{conversation_id}:{_target}" if _target else conversation_id
            with self._conv_gen_lock:
                gen = self._conv_generation.get(_gen_key, 0)
            ctx["_generation"] = gen
            ctx["_gen_key"] = _gen_key

            self._streaming_agent_loop(ctx, conversation_id, bus)

        _stream_mark("before_thread_start")
        thread = threading.Thread(
            target=_bg_streaming, daemon=True,
            name=_thread_name)
        thread.start()
        _stream_mark("thread_started")
        logger.info("[agent:%s] bg thread started: %s", conversation_id[:8], _thread_name)

        # Start poller if configured
        poll_interval = int(self.config.get("poll_interval", 0))
        if poll_interval > 0 and not self._poller_started:
            self._poller_started = True
            threading.Thread(
                target=self._poll_conversations, args=(poll_interval,),
                daemon=True, name="agent-poller").start()
            logger.info(f"Agent poller started (interval={poll_interval}s)")

        ack = json.dumps({"status": "accepted", "conversation_id": conversation_id,
                          "message_count": ConversationStore.instance().message_count(conversation_id),
                          "server_start_time": SERVER_START_TIME})
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
            # Final safety net: wake if PendingQueue has anything before we exit
            _agent_n2 = ctx.get("active_agent_name", "") or ""
            try:
                from core.pending_queue import PendingQueue
                if PendingQueue.for_agent(conversation_id, _agent_n2).peek_count():
                    from tasks.ai.agent_loop import AgentLoopTask
                    AgentLoopTask.wake_agent(
                        conversation_id, _agent_n2,
                        reason="[pending] safety-net wake",
                        user_id=ctx.get("user_id", ""),
                    )
            except Exception:
                logger.debug("exception suppressed", exc_info=True)
            self._decrement_active(conversation_id, ctx)

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
            if ctx.get("_retrigger_after_done") and not _had_error:
                ctx.pop("_retrigger_after_done", None)
                logger.info("[agent:%s] re-triggering loop for queued messages",
                            conversation_id[:8])
                result = self._run_agent_loop(ctx, emitter)
                _had_error = getattr(result, "finish_reason", "") == "error"

            # Set idle status

            # ── Auto-generate conversation title ──
            if not _had_error:
                self._maybe_generate_title(ctx, conversation_id, bus)

            # ── Periodic auto-save memories ──
            if not _had_error and ctx.get("use_conv_store"):
                try:
                    self._maybe_auto_save_memories(ctx, conversation_id)
                except Exception as _asm_err:
                    logger.debug("[agent:%s] auto-save memories failed: %s",
                                 conversation_id[:8], _asm_err)

        except Exception:
            _had_error = True
        finally:
            use_conv_store = ctx.get("use_conv_store", False)

            _was_interrupted = not self._is_current_generation(gen_key, my_generation)
            _is_cc = ctx.get("_is_claude_code", False)
            # PendingQueue is the single source of truth for pending work.
            # If anything is in it at end of turn (because the drain_pending
            # call was skipped on this code path, e.g. error exit), schedule
            # a wake so it's picked up.
            _agent_n = ctx.get("active_agent_name", "") or ""
            try:
                from core.pending_queue import PendingQueue
                _pending_count = PendingQueue.for_agent(
                    conversation_id, _agent_n).peek_count()
            except Exception:
                _pending_count = 0
            if _pending_count and not _was_interrupted:
                try:
                    from tasks.ai.agent_loop import AgentLoopTask
                    AgentLoopTask.wake_agent(
                        conversation_id, _agent_n,
                        reason=f"[pending] {_pending_count} queued msg(s)",
                        user_id=ctx.get("user_id", ""),
                        delay=1.0,
                    )
                except Exception:
                    logger.debug("exception suppressed", exc_info=True)

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
                            _delay = _rng.randint(_cfg["min_interval"], _cfg["max_interval"])
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

    _AUTO_SAVE_INTERVAL = 15  # messages between auto-saves

    def _maybe_auto_save_memories(self, ctx: Dict, conversation_id: str) -> None:
        """Periodic auto-save: extract memories every ~15 user messages."""
        user_id = ctx.get("user_id", "")
        agent_name = ctx.get("active_agent_name", "")
        if not user_id:
            return
        try:
            from core.conversation_store import ConversationStore
            store = ConversationStore.instance()
            # Track count via extras
            _key = f"_auto_save_count:{agent_name or 'default'}"
            _count = int(store.get_extra(conversation_id, _key) or 0)
            _msg_count = store.message_count(conversation_id)
            if _msg_count - _count < self._AUTO_SAVE_INTERVAL:
                return
            # Update counter
            store.set_extra(conversation_id, _key, _msg_count)
            # Get recent messages for extraction
            page = store.load_page(conversation_id, limit=self._AUTO_SAVE_INTERVAL)
            if not page or not page.get("messages"):
                return
            recent_text = "\n".join(
                m.get("content", "")[:200] for m in page["messages"]
                if isinstance(m.get("content"), str) and m.get("role") in ("user", "assistant")
            )
            if len(recent_text) < 100:
                return
            # Use summarizer_service for extraction
            _client = None
            try:
                _client, _, _ = self._get_summarizer_client(user_id)
            except Exception:
                logger.debug("exception suppressed", exc_info=True)
            if not _client:
                return
            self._auto_extract_memories(
                recent_text, _client, user_id,
                agent_name=agent_name, conversation_id=conversation_id)
            logger.info("[agent:%s] periodic auto-save memories (msg_count=%d)",
                        conversation_id[:8], _msg_count)
        except Exception as e:
            logger.debug("[agent:%s] auto-save memories failed: %s",
                         conversation_id[:8], e)

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
                            llm_service=title_svc_id)
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
