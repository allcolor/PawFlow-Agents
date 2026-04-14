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

        def on_thinking(text: str):
            if not agent._is_current_generation(gen_key, generation):
                raise AgentCancelled()
            if not poll_silent:
                evt = {
                    "text": text,
                    "agent_name": agent_name or "",
                }
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

        # Source 2: internal "already active" queue (FlowFiles queued while agent was busy)
        _agent_key = f"{self.conversation_id}:{self._agent_name}" if self._agent_name else self.conversation_id
        _queued_key = f"_queued_msgs:{_agent_key}"
        with self.agent._active_lock:
            _queued = self.agent._pending_user_msgs.pop(_queued_key, [])
        if _queued:
            for _qff in _queued:
                # _qff is a FlowFile — extract user text
                _parsed = {}
                try:
                    # Prefer the preserved attribute (set before ack overwrites content)
                    _text = (_qff.get_attribute("_queued_user_text")
                             if hasattr(_qff, "get_attribute") else "") or ""
                    if not _text:
                        _raw = _qff.get_content().decode("utf-8") if hasattr(_qff, "get_content") else str(_qff)
                        _parsed = json.loads(_raw) if _raw.strip().startswith("{") else {}
                        _text = _parsed.get("message", "") or _parsed.get("text", "") or ""
                    _uid = (_qff.get_attribute("http.auth.principal")
                            if hasattr(_qff, "get_attribute") else "") or self._user_id
                except Exception:
                    _text = ""
                    _uid = self._user_id
                    _parsed = {}
                if _text:
                    _qmid = (_qff.get_attribute("_user_msg_id")
                             if hasattr(_qff, "get_attribute") else "") or (_parsed.get("msg_id", "") if _parsed else "")
                    _msg = LLMMessage(
                        role="user", content=_text,
                        source={"type": "user", "name": _uid},
                    )
                    if _qmid:
                        _msg.msg_id = _qmid
                    append_fn(_msg)

        # Source 3: conversation store (cross-channel messages)
        if self._use_conv_store and self.conversation_id and iteration > 1:
            try:
                from core.conversation_store import ConversationStore
                _cs = ConversationStore.instance()
                _my_agent = self.ctx.get("active_agent_name") or ""
                _current = _cs.message_count(self.conversation_id)
                _known = self.ctx.get("_last_known_msg_count", 0)
                if _current > _known:
                    _page = _cs.load_page(self.conversation_id,
                                          limit=_current - _known, offset=_known)
                    _tail = _page["messages"] if _page else []
                    # Collect msg_ids already in context to avoid duplicates
                    _existing_ids = {m.msg_id for m in messages if m.msg_id}
                    for m in (_tail or []):
                        if not isinstance(m, dict):
                            continue
                        _mid = m.get("msg_id", "")
                        if _mid and _mid in _existing_ids:
                            continue
                        _role = m.get("role", "")
                        if _role not in ("user", "assistant"):
                            continue
                        _content = m.get("content", "")
                        if isinstance(_content, str) and _content.startswith("[System:"):
                            continue
                        _src = m.get("source") or {}
                        if _src.get("type") == "context":
                            continue
                        # Skip own messages (already in context)
                        if _src.get("type") == "agent" and _src.get("name") == _my_agent:
                            continue
                        # Transform for this agent's perspective
                        _xf = _cs._transform_for_other_agent(m, _my_agent)
                        messages.append(LLMMessage(
                            role=_xf.get("role", "user"),
                            content=_xf.get("content", ""),
                            source=_xf.get("source"),
                            msg_id=_mid,
                        ))
                    self.ctx["_last_known_msg_count"] = _current
            except Exception as e:
                logger.debug(f"Message checkpoint failed: {e}")

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
                ConversationWriter.for_conversation(_parent).enqueue(
                    _task_msgs, user_id=self._user_id)

        # Update known count: add the number of public messages we just enqueued
        # (don't re-read from store — the async writer may not have written yet)
        _public_count = len(public)
        self.ctx["_last_known_msg_count"] = self.ctx.get("_last_known_msg_count", 0) + _public_count

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
