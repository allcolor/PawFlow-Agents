"""Agent emitter — separates execution mode from core loop logic."""
import copy
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from core.llm_client import LLMMessage
from tasks.ai.agent_exceptions import AgentCancelled

logger = logging.getLogger(__name__)


@dataclass
class AgentResult:
    """Result of an agent loop execution."""
    response_content: str = ""
    conversation_id: str = ""
    model: str = ""
    provider: str = ""
    base_url: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    tools_called: List[str] = field(default_factory=list)
    iterations: int = 0
    duration_ms: float = 0
    finish_reason: str = ""
    source: Dict[str, str] = field(default_factory=dict)
    messages: List[LLMMessage] = field(default_factory=list)
    new_messages: List[LLMMessage] = field(default_factory=list)
    all_msg_ids: List[str] = field(default_factory=list)  # all assistant msg_ids (survives flush)
    cost_usd: float = 0.0


class AgentEmitter:
    """Base emitter — all hooks are no-ops. Subclass to customize."""
    is_streaming: bool = False
    _current_msg_id: str = ""  # set per iteration, shared across token/done events

    def on_loop_start(self, ctx: dict): pass
    def on_iteration_start(self, iteration, round_num, max_iterations,
                           max_rounds, tools_called, poll_silent): pass
    def on_iteration_end(self, iteration, round_num, max_iterations,
                         max_rounds, tools_called): pass
    def on_done(self, result: 'AgentResult'): pass
    def on_error(self, error: Exception): pass
    def on_cancelled(self, result: 'AgentResult', ctx: dict): pass
    def on_interrupted(self, result: 'AgentResult'): pass
    def get_token_callback(self, poll_silent: bool): return None
    def get_thinking_callback(self, poll_silent: bool): return None
    def start_heartbeat(self, poll_silent: bool): return None
    def stop_heartbeat(self, handle): pass
    def on_tool_calls(self, tool_calls, content, thinking, poll_silent): pass
    def on_tool_result(self, tc, result_text, preview): pass
    def check_cancelled(self): pass
    def check_interrupt(self) -> bool: return False
    def drain_pending(self, messages, append_fn, iteration): pass
    def on_no_pending_work(self, content, ctx): return content
    def on_fatal_error(self, error_msg): pass
    def on_overflow_retry(self, iteration): pass


class SyncEmitter(AgentEmitter):
    """Emitter for synchronous (blocking) execution. All hooks are no-ops."""
    is_streaming = False


class StreamEmitter(AgentEmitter):
    """Emitter for streaming execution — publishes SSE events, handles cancel/drain."""

    is_streaming = True

    def __init__(self, conversation_id: str, bus: Any, ctx: dict,
                 agent: Any, gen_key: str, generation: int):
        self.conversation_id = conversation_id
        self.event_cid = ctx.get("_event_cid", conversation_id)
        # Extract task_id from sub-conv ID so frontend can group task events
        self._task_id = ''
        if '::task::' in conversation_id:
            self._task_id = conversation_id.split('::task::')[-1].split('::')[0]
        self.bus = bus
        self.ctx = ctx
        self.agent = agent  # AgentLoopTask instance
        self.gen_key = gen_key
        self.generation = generation
        self._agent_name = ctx.get("active_agent_name", "")
        self._agent_svc = ctx.get("active_llm_service", "")
        self._user_id = ctx.get("user_id", "")
        self._use_conv_store = ctx.get("use_conv_store", False)
        self._current_msg_id = ""  # pre-generated per iteration for dedup
        self._conv_ttl = ctx.get("conv_ttl", 0)
        self._channel = ctx.get("channel", "")
        self._last_token_time = time.time()

    def _emit(self, event_type: str, data: dict):
        if self._task_id:
            data['task_id'] = self._task_id
            data['task_iteration'] = self.ctx.get("_task_iteration", 0)
        if 'ts' not in data:
            data['ts'] = time.time()
        self.bus.publish_event(self.event_cid, event_type, data)

    def _agent_source(self) -> dict:
        import re as _re
        _client = self.ctx.get("client")
        _prov = getattr(_client, "provider", "") or ""
        _burl = getattr(_client, "base_url", "") or ""
        _model = getattr(_client, "default_model", "") or ""
        return {
            "type": "agent",
            "name": self._agent_name or "",
            "llm_service": self._agent_svc or "",
            "provider": _prov if isinstance(_prov, str) else "",
            "model": _model,
            "base_url": _re.sub(r'(key|token|secret)=[^&]+', r'\1=***', _burl) if _burl and isinstance(_burl, str) else "",
            "containerized": _prov == "claude-code",
        }

    def _context_usage_payload(self, reason: str = "") -> Optional[Dict[str, Any]]:
        """Publish the authoritative PawFlow context gauge."""
        if not self._agent_name or self.ctx.get("_context_usage_suspended"):
            return None
        try:
            from tasks.ai.context_usage import (
                compute_context_usage, usage_event_payload)
            usage = compute_context_usage(
                self.conversation_id, self._agent_name,
                user_id=self.ctx.get("user_id", ""), source=reason or "stream_context")
        except Exception:
            logger.debug("stream context usage calculation failed", exc_info=True)
            return None
        if int(usage.get("max", 0) or 0) <= 0:
            return None
        payload = usage_event_payload(usage)
        payload["source"] = self._agent_source()
        payload["context_cache"] = usage
        payload["live"] = True
        return payload

    def _publish_context_usage(self, reason: str = "") -> None:
        payload = self._context_usage_payload(reason)
        if not payload:
            return
        try:
            from tasks.ai.context_usage import persist_context_usage
            persist_context_usage(
                self.event_cid, self._agent_name,
                payload.get("context_cache") or {})
        except Exception:
            logger.debug("stream context_usage persist failed", exc_info=True)
        self._emit("message_meta", payload)

    # ── Lifecycle ──────────────────────────────────────────────────────

    def on_loop_start(self, ctx: dict) -> None:
        _ff_info = {"agent": self._agent_name}
        _scheduled = ctx.get("scheduled_reasons") or []
        if _scheduled:
            _ff_info["reason"] = _scheduled[0] if len(_scheduled) == 1 else f"{len(_scheduled)} triggers"
        if ctx.get("is_poll"):
            _ff_info["type"] = "poll"
        if ctx.get("is_random_thought"):
            _ff_info["type"] = "thought"
        if not ctx.get("is_poll") or _ff_info.get("reason"):
            self._emit("flowfile_in", _ff_info)

    def on_iteration_start(self, iteration, round_num, max_iterations,
                           max_rounds, tools_called, poll_silent):
        # Generate msg_id ONCE per round (not per iteration).
        # All messages from this agent in this turn share the same msg_id.
        if not self._current_msg_id:
            import uuid
            self._current_msg_id = uuid.uuid4().hex[:12]
        logger.info(
            f"[agent:{self.conversation_id[:8]}] round {round_num}/{max_rounds}, "
            f"iteration {iteration}/{max_iterations}, "
            f"messages={len(self.ctx.get('messages', []))}, "
            f"tools_called={len(tools_called)}")
        self._emit("iteration_status", {
            "agent_name": self._agent_name or "",
            "iteration": iteration,
            "max_iterations": max_iterations,
            "round": round_num,
            "max_rounds": max_rounds,
            "tools_called": tools_called[-3:],
            "total_tools": len(tools_called),
        })
        if not poll_silent:
            self._emit("thinking", {
                "iteration": iteration,
                "round": round_num,
                "agent_name": self._agent_name or "",
            })
            self._publish_context_usage("iteration_start")

    def on_iteration_end(self, iteration, round_num, max_iterations,
                         max_rounds, tools_called):
        self._emit("iteration_status", {
            "agent_name": self._agent_name or "",
            "iteration": iteration,
            "max_iterations": max_iterations,
            "round": round_num,
            "max_rounds": max_rounds,
            "tools_called": tools_called[-3:],
            "total_tools": len(tools_called),
        })

    def on_done(self, result: AgentResult) -> None:
        # Use all_msg_ids from the full turn (survives flush)
        _all_ids = result.all_msg_ids or []
        _last_id = _all_ids[-1] if _all_ids else self._current_msg_id
        # The context gauge is emitted once through message_meta above. Done
        # closes the turn only; it must not be a second gauge publisher.
        self._emit("done", {
            "response": result.response_content,
            "msg_id": _last_id,
            "all_msg_ids": _all_ids,
            "model": result.model,
            "provider": result.provider,
            "base_url": result.base_url,
            "tokens_in": result.tokens_in,
            "tokens_out": result.tokens_out,
            "duration_ms": result.duration_ms,
            "cost_usd": result.cost_usd,
            "source": result.source,
            "agent_name": self._agent_name or "",
        })

    def on_error(self, error: Exception) -> None:
        self._emit("error_event", {
            "message": str(error),
            "agent_name": self._agent_name or "",
            "conversation_id": self.conversation_id,
        })

    def on_interrupted(self, result: AgentResult) -> None:
        if self._use_conv_store and self.conversation_id:
            try:
                from core.conversation_writer import ConversationWriter
                ConversationWriter.for_conversation(
                    self.conversation_id).flush(timeout=30.0)
            except Exception as _fw_err:
                logger.warning(
                    "[agent:%s] interrupt writer flush failed: %s",
                    self.conversation_id[:8], _fw_err)
        self.on_done(result)

    def on_cancelled(self, result: AgentResult, ctx: dict) -> None:
        # Flush the writer queue BEFORE publishing done. Cancel can fire
        # mid-turn with multiple tool_call/tool_result messages still
        # queued; if `done` overtakes them, the UI marks the turn as
        # finished while the backlog publishes 5–10s later → ghost agent
        # (messages land after "end of turn", frontend can't reconcile).
        # Same IMMUTABLE RULE fix as 3bfbfaa (on_done) but on the cancel
        # path. Repro: force-stop during a CC tool loop → done fires at
        # T+0, tool_call publishes at T+3s, tool_result at T+8s; webchat
        # kept the agent marked active until a second force-stop.
        if self._use_conv_store and self.conversation_id:
            try:
                from core.conversation_writer import ConversationWriter
                ConversationWriter.for_conversation(
                    self.conversation_id).flush(timeout=30.0)
            except Exception as _fw_err:
                logger.warning(
                    "[agent:%s] cancel writer flush failed: %s",
                    self.conversation_id[:8], _fw_err)
        # Publish done so frontend closes pending tool calls + streaming elements
        self._emit("done", {
            "agent_name": self._agent_name or "",
            "response": "",
            "cancelled": True,
        })
        # Save cancel checkpoint for resume
        if self._use_conv_store and self.conversation_id:
            try:
                from core.conversation_store import ConversationStore
                ConversationStore.instance().set_extra(
                    self.conversation_id,
                    f"cancel_checkpoint:{self._agent_name}",
                    {
                        "iteration": result.iterations,
                        "tools_called": result.tools_called,
                        "partial_response": (result.response_content or "")[:500],
                        "timestamp": time.time(),
                    },
                )
            except Exception as e:
                logger.warning(f"[agent] Failed to save cancel checkpoint: {e}")

    def on_fatal_error(self, error_msg: str) -> None:
        self._emit("error_event", {
            "message": error_msg,
            "agent_name": self._agent_name or "",
            "conversation_id": self.conversation_id,
        })

    def on_overflow_retry(self, iteration: int) -> None:
        self._emit("thinking", {
            "iteration": iteration,
            "detail": "compacting context...",
            "agent_name": self._agent_name or "",
        })

    # ── LLM interaction ───────────────────────────────────────────────

    # ── Token / thinking callbacks ────────────────────────────────────
    # The HTTP providers (openai/anthropic) stream tokens/thinking from
    # the wire, same as CC, but we deliberately DO NOT emit per-chunk
    # `token` / `thinking_content` SSE events to the UI. CC's UX (which
    # surfaces whole assistant blocks via turn_callback, never per-char
    # tokens) is the single canonical experience; HTTP providers now
    # behave identically.
    #
    # The callbacks still exist — the provider hands them every
    # incoming chunk so we can:
    #   1. raise AgentCancelled as soon as the generation is stale,
    #      aborting the streaming HTTP request mid-flight;
    #   2. bump `_last_token_time` so the heartbeat loop knows the
    #      stream is alive and doesn't log a stall.
    # No bus.publish_event here: the final block-level events
    # (message_meta, tool_call, tool_result, done) carry everything the
    # UI needs.

    def get_token_callback(self, poll_silent: bool) -> Optional[Callable]:
        gen_key = self.gen_key
        generation = self.generation
        agent = self.agent
        _emitter = self

        def on_token(text: str):
            if not agent._is_current_generation(gen_key, generation):
                raise AgentCancelled()
            _emitter._last_token_time = time.time()
        return on_token

    def get_thinking_callback(self, poll_silent: bool) -> Optional[Callable]:
        gen_key = self.gen_key
        generation = self.generation
        agent = self.agent

        def on_thinking(text: str):
            if not agent._is_current_generation(gen_key, generation):
                raise AgentCancelled()
        return on_thinking

    def start_heartbeat(self, poll_silent: bool) -> Any:
        stop_event = threading.Event()
        cid = self.event_cid
        bus = self.bus
        agent_name = self._agent_name
        emitter = self
        _tid = self._task_id
        _ctx = self.ctx

        _cancel_detected = threading.Event()

        def heartbeat():
            while not stop_event.wait(2.0):  # check every 2s
                # Check cancel during LLM call — signal main thread
                if not emitter.agent._is_current_generation(emitter.gen_key, emitter.generation):
                    _cancel_detected.set()
                    stop_event.set()  # stop heartbeat
                    return
                if poll_silent:
                    continue
                elapsed = int(time.time() - emitter._last_token_time)
                evt = {
                    "iteration": 0,
                    "waiting_seconds": elapsed,
                    "agent_name": agent_name or "",
                }
                if _tid:
                    evt["task_id"] = _tid
                    evt["task_iteration"] = _ctx.get("_task_iteration", 0)
                bus.publish_event(cid, "thinking", evt)
                emitter._publish_context_usage("heartbeat")

        t = threading.Thread(target=heartbeat, daemon=True)
        t.start()
        return (stop_event, t, _cancel_detected)

    def stop_heartbeat(self, handle: Any) -> None:
        if handle:
            stop_event, t, cancel_flag = handle
            stop_event.set()
            t.join(timeout=1)
            # Don't raise here (we're in a finally block).
            # The cancel_flag is checked by check_cancelled() after the finally.

    # ── Tool events ───────────────────────────────────────────────────

    def on_tool_calls(self, tool_calls, response_content, thinking,
                      poll_silent):
        # tool_call SSE events are published by ConversationWriter after
        # the message is written to the store (single source of truth).
        pass

    def on_tool_result(self, tc, result_text, preview):
        # tool_result SSE events are now published by ConversationWriter
        # after the message is written to the store (single source of truth).
        pass

    # ── Control flow ──────────────────────────────────────────────────

    def check_cancelled(self) -> None:
        if not self.agent._is_current_generation(self.gen_key, self.generation):
            raise AgentCancelled()

    def check_interrupt(self) -> bool:
        return self.agent._check_interrupt(self.gen_key)

    def drain_pending(self, messages, append_fn, iteration):
        # Source 1: executor queue drain
        if hasattr(self.agent, '_drain_pending') and self.agent._drain_pending:
            try:
                _requeue = []
                for _pff in self.agent._drain_pending():
                    _pbody = _pff.get_content()
                    if isinstance(_pbody, bytes):
                        _pbody = _pbody.decode("utf-8", errors="replace")
                    _ptext = None
                    _is_action = False
                    try:
                        _pjson = json.loads(_pbody)
                        if isinstance(_pjson, dict):
                            _is_action = bool(_pjson.get("action"))
                            if not _is_action:
                                _pconv = _pjson.get("conversation_id")
                                if _pconv and _pconv != self.conversation_id:
                                    _requeue.append(_pff)
                                    continue
                                _ptext = _pjson.get("message", "")
                    except (json.JSONDecodeError, ValueError):
                        pass
                    if _is_action:
                        try:
                            result = self.agent._handle_action(_pff)
                            if result:
                                for _rff in result:
                                    self._respond_http(_rff)
                        except Exception as _ae:
                            logger.debug(f"Inline action failed: {_ae}")
                    elif _ptext and _ptext.strip():
                        _pmid = _pff.get_attribute("_user_msg_id") or ""
                        _msg = LLMMessage(
                            role="user", content=_ptext,
                            source={"type": "user",
                                    "name": _pff.get_attribute("http.auth.principal") or self._user_id},
                            conversation_id=self.conversation_id,
                        )
                        if _pmid:
                            _msg.msg_id = _pmid
                        append_fn(_msg)
                        self._respond_http(_pff)
                # Re-enqueue FlowFiles that belong to other conversations
                if _requeue and hasattr(self.agent, '_requeue_flowfiles'):
                    self.agent._requeue_flowfiles(_requeue)
            except Exception as _e:
                logger.debug(f"Queue drain failed: {_e}")

        # Source 2: PendingQueue (persistent per-(conv, agent) disk-backed queue)
        # — the single source of truth for messages that arrived while this
        # agent was busy. Replaces the old in-memory _pending_user_msgs dict
        # AND the old transcript-scan Source 3 (which drifted via
        # _last_known_msg_count and produced phantom retriggers).
        try:
            from core.pending_queue import PendingQueue
            _queued_msgs = PendingQueue.for_agent(
                self.conversation_id, self._agent_name or "").drain()
        except Exception as _qe:
            logger.warning("[drain] PendingQueue read failed: %s", _qe)
            _queued_msgs = []
        for _qmsg in _queued_msgs:
            if not isinstance(_qmsg, dict):
                continue
            _role = _qmsg.get("role", "user")
            _content = _qmsg.get("content", "")
            _atts = _qmsg.get("attachments") or []
            if not _content and not _atts:
                continue
            # Queued attachments were captured at enqueue time but never
            # transformed — the LLM needs them as multimodal content
            # (image_ref / file_ref parts) to actually see images.
            if _atts and _role == "user":
                _content = self.agent._build_user_content(
                    _content if isinstance(_content, str) else "",
                    _atts,
                    conversation_id=self.conversation_id,
                    user_id=getattr(self, "_user_id", "") or "",
                )
            _src = _qmsg.get("source") or {}
            _mid = _qmsg.get("msg_id", "")
            _ts = _qmsg.get("ts") or _qmsg.get("timestamp")
            _seq = _qmsg.get("seq")
            _msg = LLMMessage(
                role=_role, content=_content, source=_src,
                msg_id=_mid, timestamp=_ts or 0.0, seq=_seq or 0,
                conversation_id=self.conversation_id,
            )
            _msg._pending_source = _qmsg.get("_pending_source", "") or ""
            _msg._pending_enqueued_at = _qmsg.get("_pending_enqueued_at") or 0.0
            append_fn(_msg)

        # Source 3 (transcript scan) removed. Cross-channel and cross-agent
        # messages are routed through PendingQueue at their ingress — see
        # core/pending_queue.py. The old scan-the-tail approach drifted
        # against `_last_known_msg_count` and produced phantom retriggers
        # every time a compaction wrote messages outside the enqueue path.
    # ── Persistence ─────────────────────────────────────────────────
    # flush() removed — agent_core._append persists each message immediately
    # via ConversationWriter.enqueue_message → ConversationStore.append_message.


    def on_no_pending_work(self, content: str, ctx: dict) -> Optional[str]:
        """Handle [NO_PENDING_WORK] / [RECHECK_IN:N] tags from poller responses."""
        if not content or not isinstance(content, str):
            return content
        _stripped = content.strip()
        if "[NO_PENDING_WORK]" not in _stripped and "[RECHECK_IN:" not in _stripped:
            return content

        # Random thought that produced no work
        if ctx.get("is_random_thought") and "[NO_PENDING_WORK]" in _stripped:
            logger.info(f"[agent:{self.conversation_id[:8]}] random thought → no pending work, discarding")
            self._emit("discard", {
                "reason": "random_thought_no_work",
                "agent_name": self._agent_name or "",
            })
            return None

        # Schedule recheck
        import re as _re
        _m = _re.search(r'\[RECHECK_IN:(\d+)\]', _stripped)
        if _m:
            _delay = int(_m.group(1))
            try:
                from core.poll_scheduler import PollScheduler
                PollScheduler.instance().schedule_delay(
                    self.conversation_id, _delay,
                    key=f"{self.conversation_id}::recheck",
                    reason=f"[recheck] agent requested in {_delay}s",
                    user_id=self._user_id,
                )
            except Exception:
                logger.debug("exception suppressed", exc_info=True)

        # If the entire response is just tags, discard
        _clean = _stripped.replace("[NO_PENDING_WORK]", "").strip()
        _clean = _re.sub(r'\[RECHECK_IN:\d+\]', '', _clean).strip()
        if not _clean:
            return None
        return _clean

    # ── Internal helpers ──────────────────────────────────────────────

    def _respond_http(self, ff, body=None, status=200):
        """Send HTTP response for a drained FlowFile."""
        _req_id = ff.get_attribute("http.request.id")
        if not _req_id:
            return
        if body is None:
            body = ff.get_content() if ff.get_attribute("http.response.status") else \
                json.dumps({"status": "accepted",
                            "conversation_id": self.conversation_id}).encode()
        if isinstance(body, str):
            body = body.encode("utf-8")
        _status = int(ff.get_attribute("http.response.status") or status)
        _ct = ff.get_attribute("http.response.header.Content-Type") or "application/json"
        try:
            from services.http_listener_service import _instances
            for _port, _svc in _instances.items():
                if _svc._server and _req_id in _svc._server._pending_requests:
                    _svc.complete_response(_req_id, _status, {"Content-Type": _ct}, body)
                    break
        except Exception:
            logger.debug("exception suppressed", exc_info=True)
