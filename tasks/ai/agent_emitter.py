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
    def flush(self, new_messages): pass
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
            "containerized": bool(getattr(_client, 'containerize', False)),
        }

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
        # Context fill: include cache tokens (dominant for CC with prompt caching).
        # tokens_in alone is input-only and tiny; real context usage is
        # input + cache_creation + cache_read (per-call, Anthropic semantics).
        _ctx_used = (int(result.tokens_in or 0)
                     + int(getattr(result, 'cache_creation_tokens', 0) or 0)
                     + int(getattr(result, 'cache_read_tokens', 0) or 0))
        _ctx_max = int(self.ctx.get("max_context_size", 0) or 200000)
        _ctx_pct = (_ctx_used / _ctx_max) if _ctx_max > 0 else 0.0
        self._emit("done", {
            "response": result.response_content,
            "msg_id": _last_id,
            "all_msg_ids": _all_ids,
            "model": result.model,
            "provider": result.provider,
            "base_url": result.base_url,
            "tokens_in": result.tokens_in,
            "tokens_out": result.tokens_out,
            "context_used": _ctx_used,
            "context_max": _ctx_max,
            "context_pct": _ctx_pct,
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

    def on_cancelled(self, result: AgentResult, ctx: dict) -> None:
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

    def get_token_callback(self, poll_silent: bool) -> Optional[Callable]:
        cid = self.event_cid
        bus = self.bus
        agent_name = self._agent_name
        source = self._agent_source()
        # Override source when this turn is a delegate reply so the UI
        # routes the live stream into the private delegate block.
        _tm = (self.ctx.get("_turn_mode") or {}) if isinstance(self.ctx, dict) else {}
        if (_tm.get("type") == "delegate_reply"
                and _tm.get("source_agent")):
            source = {
                "type": "agent_delegate",
                "from": agent_name or "",
                "to": _tm["source_agent"],
            }
        gen_key = self.gen_key
        generation = self.generation
        agent = self.agent
        _tid = self._task_id

        _emitter = self  # capture for closure
        _ctx = self.ctx

        def on_token(text: str):
            if not agent._is_current_generation(gen_key, generation):
                raise AgentCancelled()
            _emitter._last_token_time = time.time()
            if not poll_silent:
                evt = {
                    "text": text,
                    "msg_id": _emitter._current_msg_id,
                    "agent_name": agent_name or "",
                    "source": source,
                }
                if _tid:
                    evt["task_id"] = _tid
                    evt["task_iteration"] = _ctx.get("_task_iteration", 0)
                bus.publish_event(cid, "token", evt)
        return on_token

    def get_thinking_callback(self, poll_silent: bool) -> Optional[Callable]:
        cid = self.event_cid
        bus = self.bus
        agent_name = self._agent_name
        gen_key = self.gen_key
        generation = self.generation
        agent = self.agent
        _tid = self._task_id
        _ctx = self.ctx
        # Tag thinking events with agent_delegate source in delegate_reply
        # turns so the UI groups them under the private delegate frame.
        _tm = (_ctx.get("_turn_mode") or {}) if isinstance(_ctx, dict) else {}
        _delegate_src = None
        if (_tm.get("type") == "delegate_reply"
                and _tm.get("source_agent")):
            _delegate_src = {
                "type": "agent_delegate",
                "from": agent_name or "",
                "to": _tm["source_agent"],
            }

        def on_thinking(text: str):
            if not agent._is_current_generation(gen_key, generation):
                raise AgentCancelled()
            if not poll_silent:
                evt = {
                    "text": text,
                    "agent_name": agent_name or "",
                }
                if _delegate_src:
                    evt["source"] = _delegate_src
                if _tid:
                    evt["task_id"] = _tid
                    evt["task_iteration"] = _ctx.get("_task_iteration", 0)
                bus.publish_event(cid, "thinking_content", evt)
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
        # tool_call SSE events are now published by ConversationWriter
        # after the message is written to the store (single source of truth).
        # Narration: ONLY if LLM said absolutely nothing
        if not response_content and not thinking and tool_calls:
            _nsvc = self.ctx.get("narrator_service", "")
            if not _nsvc:
                return  # no narrator configured → skip narration
            logger.info(f"[narration] triggering narrator_service='{_nsvc}'")
            from tasks.ai.agent_streaming import _narrate_tool_calls
            _narrate_tool_calls(
                tool_calls, self.ctx, self.bus, self.conversation_id,
                self._agent_name or "", self._agent_source(),
                msg_id=self._current_msg_id,
            )

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
            )
            append_fn(_msg)

        # Source 3 (transcript scan) removed. Cross-channel and cross-agent
        # messages are routed through PendingQueue at their ingress — see
        # core/pending_queue.py. The old scan-the-tail approach drifted
        # against `_last_known_msg_count` and produced phantom retriggers
        # every time a compaction wrote messages outside the enqueue path.

    # ── Persistence ───────────────────────────────────────────────────

    def flush(self, new_messages: List[LLMMessage]) -> None:
        if not (self._use_conv_store and self.conversation_id and new_messages):
            return
        from core.conversation_store import ConversationStore

        # Deflate on copy — don't affect live context
        _persist = copy.deepcopy(new_messages)
        self.agent._deflate_image_messages(
            _persist,
            user_id=getattr(self, "_user_id", "") or "",
            conversation_id=getattr(self, "_conversation_id", "") or "")

        all_serialized = self.agent._serialize_messages(_persist, channel=self._channel)

        # Split: public (user + assistant text) vs private (tools)
        # Exclude system messages disguised as user (e.g. "[System: ...]")
        # and the system prompt itself — these are internal, not conversation
        def _is_public(m):
            role = m.get("role", "")
            if role == "system":
                return False
            if role not in ("user", "assistant"):
                return False
            if m.get("tool_calls"):
                return False
            content = m.get("content", "")
            if isinstance(content, str) and content.startswith("[System:"):
                return False
            # Synthetic context messages (compaction acks, resume acks)
            src = m.get("source") or {}
            if src.get("type") == "context":
                return False
            return True
        public = [m for m in all_serialized if _is_public(m)]
        # Private = tools + tool results. Exclude system and context-only messages.
        def _is_persistable(m):
            if m.get("role") == "system":
                return False
            src = m.get("source") or {}
            if src.get("type") == "context":
                return False
            content = m.get("content", "")
            if isinstance(content, str) and content.startswith("[System:"):
                return False
            return True
        private = [m for m in all_serialized if m not in public and _is_persistable(m)]

        _agent_n = self.ctx.get("active_agent_name") or ""
        from core.conversation_writer import ConversationWriter
        writer = ConversationWriter.for_conversation(self.conversation_id)

        writer.enqueue_agent_flush(
            _agent_n, public_messages=public, private_messages=private,
            user_id=self._user_id, ttl=self._conv_ttl)
        self.ctx["_context_diverged"] = True

        if "::task::" in self.conversation_id:
            _parent = self.conversation_id.split("::task::")[0]
            # Write all task messages to parent conv in ORIGINAL order
            # (not public+private which breaks chronological order)
            _task_msgs = [m for m in all_serialized if m.get("role") != "system"]
            if _task_msgs:
                # Deep copy to avoid mutating originals (shared with sub-conv write)
                import copy as _copy
                _task_msgs = _copy.deepcopy(_task_msgs)
                # Tag each with task_id + iteration in source for frontend grouping
                _tid = self._task_id
                _iter = self.ctx.get("_task_iteration", 1)
                if _tid:
                    for _tm in _task_msgs:
                        _src = _tm.get("source") or {}
                        _src["task_id"] = _tid
                        _src["task_iteration"] = _iter
                        _tm["source"] = _src
                _parent_writer = ConversationWriter.for_conversation(_parent)
                for _tm in _task_msgs:
                    _parent_writer.enqueue_message(
                        _tm, agent_name=_agent_n, user_id=self._user_id)

        # _last_known_msg_count removed — no more transcript-scan drain,
        # no need to track what we've seen on disk.

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
                pass

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
            pass
