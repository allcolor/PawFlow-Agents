"""AgentLoopTask mixin — streaming agent execution.

Thread spawning, ACK return. The actual loop logic is in agent_core.py
(_run_agent_loop).
"""
import json
import logging
import os
import threading
import time
import uuid
from typing import List


# One-shot epoch taken when the module is first imported. Included in
# every /api/agent ack so the client can detect a server restart: the
# UI's SSE may believe it's still connected (half-open TCP) while the
# new process has no subscribers for its conversation. On mismatch the
# client force-reconnects SSE and picks up the buffered events.
SERVER_START_TIME = time.time()
_ACK_BG_START_DELAY_SECONDS = float(
    os.getenv("PAWFLOW_AGENT_ACK_BG_START_DELAY_MS", "10") or "10") / 1000.0

from core import FlowFile  # noqa: E402

logger = logging.getLogger(__name__)


def stamp_turn_identity(flowfile, user_msg_id: str) -> str:
    """Carry the submitting user message id into the turn as its turn id.

    The user message id IS the turn id: every assistant, thinking and tool row
    the turn produces persists and emits it, and the simplified chat view groups
    on it. Only the programmatic runtime API ever set `agent.request_msg_id`, so
    web chat turns ran with an empty one -- nothing downstream could stamp a
    `turn_id`, so the simplified view had no turn to build and silently rendered
    a classic transcript. A turn id the caller already chose always wins.
    """
    if not user_msg_id:
        return flowfile.get_attribute("agent.request_msg_id") or ""
    flowfile.set_attribute("_user_msg_id", user_msg_id)
    if not (flowfile.get_attribute("agent.request_msg_id") or ""):
        flowfile.set_attribute("agent.request_msg_id", user_msg_id)
    return flowfile.get_attribute("agent.request_msg_id") or ""

from tasks.ai.agent_sync import AgentSyncMixin  # noqa: E402
from tasks.ai.agent_side_channels import AgentSideChannelsMixin  # noqa: E402
from tasks.ai._agent_streaming_loop import _AgentStreamingLoopMixin  # noqa: E402


class AgentStreamingMixin(AgentSyncMixin, AgentSideChannelsMixin, _AgentStreamingLoopMixin):
    """Streaming agent execution + sync + side channels."""

    @staticmethod
    def _deliver_to_captured_tmux(conversation_id: str, agent_name: str,
                                  text: str) -> bool:
        """Type a user message into a tmux session working outside a worker.

        Reached only when a turn marker says the agent is busy while nothing
        preemptable is registered — the shape of a captured turn. The MITM
        proxy settles whether there is anywhere to deliver: its WebSocket is
        up exactly while a container lives, so a connected session is proof
        of a live tmux, independent of the turn bookkeeping that went stale.
        No connected session means no container to type into, and the message
        falls back to the queue.

        Returns True when the text reached the live container.
        """
        if not conversation_id or not (text or "").strip():
            return False
        try:
            from services.cc_interactive_event_service import (
                CCInteractiveEventService)
            live = CCInteractiveEventService.live_session(
                conversation_id, agent_name or "")
            if live is None:
                return False
            if getattr(live, "provider", "") == "codex-interactive":
                from core.codex_interactive_pool import CodexInteractivePool
                pool = CodexInteractivePool.instance()
            else:
                from core.claude_code_interactive_pool import (
                    InteractiveClaudeCodePool)
                pool = InteractiveClaudeCodePool.instance()
            state = pool.find_live_by_conv_agent(
                conversation_id, agent_name or "")
            # This tmux is visibly running: a normal send waits behind that turn.
            # Escape + receipt-verified Enter is the live-preempt path.
            if state is None or not pool.send_interrupt(state, text):
                return False
        except Exception:
            logger.debug("captured-tmux delivery failed", exc_info=True)
            return False
        logger.info(
            "[agent:%s] preempted captured tmux turn agent=%s",
            conversation_id[:8], agent_name or "default")
        return True

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
        _channel = flowfile.get_attribute("agent.client_channel") or "web"
        if (_user_text.strip() or _attachments_body) and not _target:
            flowfile.set_content(json.dumps({
                "error": "target_agent is required for user messages",
            }).encode("utf-8"))
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        _user_msg_id = _body.get("msg_id", "")
        stamp_turn_identity(flowfile, _user_msg_id)

        # Authorization has to happen HERE, not in _prepare_agent_context.
        # This method pre-persists the user message and publishes it to every
        # SSE subscriber, then hands the turn to a background thread -- so a
        # check that lives downstream of the thread boundary runs after the
        # write it was meant to prevent, and raises where no HTTP response is
        # left to turn into a 404. The preempt logic just below is a reason of
        # its own: it cancels whatever agent is running on this conversation.
        if conversation_id:
            from core.conversation_access import (
                ConversationAccessError, authorize_message_submission,
            )
            try:
                authorize_message_submission(
                    conversation_id,
                    flowfile.get_attribute("http.auth.principal") or "")
            except ConversationAccessError:
                flowfile.set_content(json.dumps({
                    "error": "Conversation not found",
                }).encode())
                flowfile.set_attribute("http.response.status", "404")
                return [flowfile]

        bus = ConversationEventBus.instance()

        def _ack_message_count() -> int:
            try:
                return int(ConversationStore.instance().get_extra_snapshot(
                    conversation_id, "_meta_msg_count", 0) or 0)
            except Exception:
                return 0

        _stream_mark("body_parsed")

        # If agent thread already running FOR THIS AGENT, preempt or queue
        _agent_key = f"{conversation_id}:{_target}" if _target else conversation_id
        _thread_name = f"agent-stream-{_agent_key}"
        _thread_active = any(
            t.is_alive() and t.name == _thread_name
            for t in threading.enumerate())
        with self._active_contexts_lock:
            _active_turn_marker = self._active_turns.get(_agent_key)
            _active_context_marker = self._active_contexts.get(_agent_key)
            _active_client_marker = self._active_claude_client.get(_agent_key)
        _already_active = bool(
            _active_turn_marker
            or _active_context_marker
            or _active_client_marker
            or _thread_active)
        if _already_active:
            _marker_generation = None
            if isinstance(_active_turn_marker, dict):
                _marker_generation = _active_turn_marker.get("generation")
            with self._conv_gen_lock:
                _current_generation = self._conv_generation.get(_agent_key, 0)
            _stale_generation = (
                _marker_generation is not None
                and _marker_generation != _current_generation)
            _thread_only = (
                _thread_active
                and not _active_turn_marker
                and not _active_context_marker
                and not _active_client_marker)
            if _stale_generation or _thread_only:
                logger.info(
                    "[agent:%s] ignoring stale stopped turn before new user message "
                    "(thread=%s marker_gen=%s current_gen=%s)",
                    conversation_id[:8], _thread_active,
                    _marker_generation, _current_generation)
                with self._active_contexts_lock:
                    self._active_turns.pop(_agent_key, None)
                    self._active_contexts.pop(_agent_key, None)
                    self._active_claude_client.pop(_agent_key, None)
                _already_active = False
        _stream_mark("active_check")
        # A live agent can temporarily have no _active_contexts entry: context
        # preparation, provider compact/restart, and final cleanup all run
        # outside _run_agent_loop's push/pop window. Treat either the thread or
        # the provider-agnostic _active_turns marker as authoritative here;
        # missing that marker lets a retry start a duplicate provider turn.
        if _already_active:
            with self._active_contexts_lock:
                if _agent_key not in self._active_contexts:
                    logger.info(
                        "[agent:%s] active turn has no context yet — treating as busy",
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
        _persisted_source = {"type": "user", "name": _uid,
                             "target_agent": _target or None}
        try:
            _source_raw = flowfile.get_attribute("message_source") or ""
            _source_value = (json.loads(_source_raw)
                             if isinstance(_source_raw, str) and _source_raw
                             else _source_raw)
            if (isinstance(_source_value, dict)
                    and _source_value.get("type") in {
                        "a2a", "cross_conversation_delegate"}):
                _persisted_source = dict(_source_value)
                # Transport provenance is internal, but routing identity remains
                # authoritative at the authenticated AgentRequest boundary.
                _persisted_source["target_agent"] = _target or None
        except Exception:
            logger.debug("invalid internal message_source ignored", exc_info=True)
        if _user_text.strip() or _attachments_body:
            _stamped_user = stamp_message({
                "role": "user",
                "content": _user_text,
                "source": dict(_persisted_source),
                "msg_id": _user_msg_id or None,
                "channel": _channel,
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
                    "channel": _channel,
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
                        user_id=_uid,
                        wait=(_channel == "telegram"),
                        sse_events=[{"type": "new_message", "data": {
                            "role": "user",
                            "content": _stamped_user.get("content", ""),
                            "msg_id": _stamped_user.get("msg_id", ""),
                            "ts": _stamped_user.get("ts"),
                            "source": _stamped_user.get("source") or {},
                            "channel": _channel,
                            "attachments": _attachments_body,
                        }}])
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
                    "source": dict(_persisted_source),
                    "msg_id": _user_msg_id or None,
                    "channel": _channel,
                }, conversation_id)
                if _attachments_body:
                    _stamped_user["attachments"] = _attachments_body
            return _stamped_user

        def _queue_pending_user(source: str, publish: bool = True) -> bool:
            msg = _ensure_stamped_user()
            if not msg:
                return False
            _enqueue_dict = dict(msg)
            if not _skip_pre_persist:
                # The message was already persisted (and added to the agent
                # context) by the pre-persist above. The next drain must NOT
                # append it a second time or the transcript/context gets a
                # duplicate user row (seen as the same image described twice).
                _enqueue_dict["_already_persisted"] = True
            from core.pending_queue import PendingQueue
            PendingQueue.for_agent(conversation_id, _target or "").enqueue(
                _enqueue_dict, source=source)
            if publish:
                bus.publish_event(conversation_id, "message_queued", {
                    "conversation_id": conversation_id})
            return True

        # A published external agent owns its local TUI. The canonical row may
        # have been persisted immediately above (web/A2A) or by the private
        # delegate delivery path before this wake (skip_pre_persist=1). Route
        # both shapes; persistence ownership must not decide runtime routing.
        if _stamped_user is not None and not _already_active:
            from services.mcp_terminal_router import (
                route_published_terminal_prompt)
            _external_routed = route_published_terminal_prompt(
                conversation_id, _target, _user_text,
                str(_stamped_user.get("msg_id") or ""),
                channel=_channel, attachments=_attachments_body)
            if _external_routed is True:
                logger.info(
                    "[agent:%s] routed webchat prompt to published MCP terminal "
                    "agent=%s msg_id=%s", conversation_id[:8], _target,
                    _stamped_user.get("msg_id", ""))
                ack = json.dumps({
                    "status": "accepted",
                    "conversation_id": conversation_id,
                    "message_count": _ack_message_count(),
                    "server_start_time": SERVER_START_TIME,
                    # AgentRuntimeAPI/A2A callers may now correlate the eventual
                    # send_agent_message response with this request msg_id.
                    "wait_for_done": True,
                    "external_terminal": True,
                })
                flowfile.set_content(ack.encode("utf-8"))
                flowfile.set_attribute("agent.conversation_id", conversation_id)
                flowfile.set_attribute("agent.streaming", "true")
                return [flowfile]
            if _external_routed is False:
                from services.mcp_terminal_router import (
                    complete_published_terminal_target)
                complete_published_terminal_target(
                    conversation_id, _target,
                    str(_stamped_user.get("msg_id") or ""), "",
                    error="Published MCP terminal is unavailable")
                flowfile.set_content(json.dumps({
                    "error": "Published MCP terminal is unavailable",
                    "code": "external_mcp_terminal_unavailable",
                    "conversation_id": conversation_id,
                }).encode("utf-8"))
                flowfile.set_attribute("http.response.status", "503")
                flowfile.set_attribute("agent.conversation_id", conversation_id)
                return [flowfile]

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
                    elif (isinstance(_ms, dict)
                          and _ms.get("type") in {
                              "a2a", "cross_conversation_delegate"}):
                        _incoming_mode = {
                            "type": "external_request",
                            "source_agent": _ms.get("task_id", ""),
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
                                      "message_count": _ack_message_count(),
                                      "server_start_time": SERVER_START_TIME,
                                      "wait_for_done": False})
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
                    if _preempt_killed:
                        _already_active = False
                        with self._active_contexts_lock:
                            self._active_claude_client.pop(_agent_key, None)
                            self._active_contexts.pop(_agent_key, None)
                    else:
                        logger.warning(
                            "[agent:%s] live preempt was not acknowledged; "
                            "keeping active turn and queuing message",
                            conversation_id[:8])

            # API providers (openai, openai-responses, anthropic, ...) have no
            # stdin/tmux to interrupt, so a new user message cannot be typed
            # into the running session. The only way to make it count NOW is to
            # abort the in-flight HTTP stream (closing the connection), cancel
            # in-flight tool calls, bump the generation so the running worker
            # stops cleanly, and let the fall-through below seed a fresh turn
            # with this same message -- the fast-restart semantics the CLI
            # preempt uses when the provider kills its one-shot session.
            if (_active_client
                    and not getattr(_active_client, 'supports_live_preempt', False)
                    and hasattr(_active_client, 'abort')
                    and _user_text and _modes_match):
                # Soft preempt (API) : si le worker est entre iterations
                # (tool call en cours, ou idle) il n'y a AUCUNE connexion HTTP
                # active (_active_http_conn est None). Tuer le thread puis
                # recharger tout le contexte depuis le disque est un gaspillage
                # pur — le contexte est une liste Python en memoire. On cancel
                # les tool calls in-flight (ils repondent "cancelled"), on
                # queue le message, et le worker VIVANT le draine au debut de
                # la prochaine iteration (_alc_iteration_body appelle
                # drain_pending avant chaque appel LLM) : le LLM lit
                # naturellement le message, sans reload ni perte de contexte.
                _in_flight_stream = bool(
                    getattr(_active_client, "_active_http_conn", None))
                if not _in_flight_stream:
                    logger.info(
                        "[agent:%s] preempting active API provider session"
                        " (soft — cancel toolcalls + queue, no reload) agent=%s",
                        conversation_id[:8], _target or "default")
                    try:
                        from services.tool_relay_service import ToolRelayService
                        ToolRelayService.cancel_agent(
                            conversation_id, _target or "")
                    except Exception as _tc:
                        logger.debug(
                            "[agent:%s] tool relay cancel failed: %s",
                            conversation_id[:8], _tc)
                    _queue_pending_user(source="preempt_soft", publish=True)
                    _ack_soft = json.dumps({
                        "status": "accepted",
                        "conversation_id": conversation_id,
                        "message_count": _ack_message_count(),
                        "server_start_time": SERVER_START_TIME,
                        "wait_for_done": False,
                    })
                    flowfile.set_content(_ack_soft.encode("utf-8"))
                    flowfile.set_attribute("agent.conversation_id", conversation_id)
                    return [flowfile]
                # Un stream LLM est en cours : on doit l'interrompre.
                logger.info(
                    "[agent:%s] preempting active API provider session"
                    " (abort + fast-restart) agent=%s",
                    conversation_id[:8], _target or "default")
                try:
                    _active_client.abort()
                except Exception as _ab:
                    logger.debug(
                        "[agent:%s] API abort failed: %s",
                        conversation_id[:8], _ab)
                try:
                    from services.tool_relay_service import ToolRelayService
                    ToolRelayService.cancel_agent(
                        conversation_id, _target or "")
                except Exception as _tc:
                    logger.debug(
                        "[agent:%s] tool relay cancel failed: %s",
                        conversation_id[:8], _tc)
                with self._conv_gen_lock:
                    self._conv_generation[_agent_key] = (
                        self._conv_generation.get(_agent_key, 0) + 1)
                with self._active_contexts_lock:
                    self._active_claude_client.pop(_agent_key, None)
                    self._active_contexts.pop(_agent_key, None)
                    self._active_turns.pop(_agent_key, None)
                _fast_restart_after_preempt = True
                _already_active = False

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
                # One case is not "not yet": a captured tmux turn. The CC
                # interactive event service registers _active_turns for work
                # Claude Code started on its own (a background-task result
                # landing in its container), with no streaming worker, no
                # context and no client — so this branch holds for the whole
                # turn, not for a brief settling window. Queuing there means
                # the message reaches nothing while the tmux is visibly
                # working. The live container is still typeable: type into it.
                if self._deliver_to_captured_tmux(
                        conversation_id, _target, _user_text):
                    ack = json.dumps({
                        "status": "accepted",
                        "conversation_id": conversation_id,
                        "message_count": _ack_message_count(),
                        "server_start_time": SERVER_START_TIME,
                        "wait_for_done": False})
                    flowfile.set_content(ack.encode("utf-8"))
                    flowfile.set_attribute("agent.conversation_id", conversation_id)
                    return [flowfile]

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
                                  "message_count": _ack_message_count(),
                                  "server_start_time": SERVER_START_TIME,
                                  "wait_for_done": False})
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
        _active_turn_owner_id = uuid.uuid4().hex
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
                "turn_id": flowfile.get_attribute("agent.request_msg_id") or "",
                "started_at": _active_turn_started,
                "status": "preparing",
                "message_preview": _user_text[:160],
                "generation": _starting_generation,
                "owner_id": _active_turn_owner_id,
                "owner_type": "streaming_worker",
            }

        if _target:
            bus.publish_event(conversation_id, "thinking", {
                "conversation_id": conversation_id, "agent_name": _target,
                "turn_id": flowfile.get_attribute("agent.request_msg_id") or "",
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
                    "_active_turn_owner_id": _active_turn_owner_id,
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
            ctx["_active_turn_owner_id"] = _active_turn_owner_id

            if not self._is_current_generation(_gen_key, _starting_generation):
                logger.info(
                    "[agent:%s] abandoning preempted preparing turn for %s",
                    conversation_id[:8], _resolved_agent or "default")
                self._decrement_active(conversation_id, ctx)
                return

            _preparing_superseded = False
            with self._active_contexts_lock:
                _current_turn = self._active_turns.get(_active_turn_key)
                _current_owner = (
                    _current_turn.get("owner_id")
                    if isinstance(_current_turn, dict) else None)
                if (_current_owner and
                        _current_owner != _active_turn_owner_id):
                    _preparing_superseded = True
                else:
                    _turn = self._active_turns.pop(_active_turn_key, None) or {}
                    _turn.update({
                        "conversation_id": conversation_id,
                        "agent_name": _resolved_agent,
                        "started_at": _turn.get("started_at", _active_turn_started),
                        "status": "thinking",
                        "message_preview": _turn.get("message_preview", _user_text[:160]),
                        "max_rounds": ctx.get("max_rounds", 0),
                        "active_llm_provider": ctx.get(
                            "active_llm_provider", ""),
                        "generation": _starting_generation,
                    })
                    self._active_turns[_resolved_turn_key] = _turn
            if _preparing_superseded:
                logger.info(
                    "[agent:%s] preparing worker superseded before context install",
                    conversation_id[:8])
                self._decrement_active(conversation_id, ctx)
                return
            _active_turn_key = _resolved_turn_key
            ctx["_active_turn_key"] = _active_turn_key

            self._streaming_agent_loop(ctx, conversation_id, bus)

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
                          "message_count": _ack_message_count(),
                          "server_start_time": SERVER_START_TIME})
        _stream_step("ack_message_count", _ack_started)
        _stream_mark("ack_built")
        flowfile.set_content(ack.encode("utf-8"))
        flowfile.set_attribute("agent.conversation_id", conversation_id)
        flowfile.set_attribute("agent.streaming", "true")
        _stream_mark("before_thread_start")
        def _bg_streaming_after_ack():
            if _ACK_BG_START_DELAY_SECONDS > 0:
                time.sleep(_ACK_BG_START_DELAY_SECONDS)
            _bg_streaming()

        thread = threading.Thread(
            target=_bg_streaming_after_ack, daemon=True,
            name=_thread_name)
        _thread_started = _t_stream.monotonic()
        thread.start()
        _stream_step("thread_started", _thread_started)
        logger.info("[agent:%s] bg thread started: %s", conversation_id[:8], _thread_name)
        return [flowfile]
