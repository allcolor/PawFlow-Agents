"""Shared agent runtime API for non-HTTP transports.

This module is the first extraction step away from treating ``/api/agent`` as
the only agent client contract. It normalizes a client request into the same
FlowFile shape consumed by ``AgentLoopTask`` and provides a correlated wait for
the final ``done`` event.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from core import FlowFile

logger = logging.getLogger(__name__)

# Attributes this API alone decides. A caller's provenance metadata must never
# reach them: `http.auth.principal` is what every conversation ACL downstream
# authorizes against, and a flow that forwards a visitor payload into
# source_attributes would otherwise let that visitor choose an identity.
_RESERVED_REQUEST_ATTRIBUTES = frozenset({
    "http.auth.principal",
    "agent.client_channel",
    "agent.request_msg_id",
    "agent.permission_mode",
    "agent.run_handle",
})


@dataclass
class AgentRequest:
    user_id: str
    message: str
    conversation_id: str = ""
    target_agent: str = ""
    attachments: list = field(default_factory=list)
    msg_id: str = ""
    channel: str = "web"
    runtime_port: str = ""
    permission_mode: str = ""
    run_handle: str = ""
    source_attributes: Dict[str, str] = field(default_factory=dict)
    live_callback: Optional[Callable[[str, str, Any], None]] = None


@dataclass
class AgentSubmission:
    status: str
    conversation_id: str
    turn_id: str
    target_agent: str = ""
    server_start_time: float = 0.0
    wait_for_done: bool = True
    # True when this turn_id was already durably ingressed: the retry was
    # acknowledged as the original submission and NO second turn started.
    duplicate: bool = False
    # Filled on a duplicate whose original turn ALREADY finished: the
    # durable terminal is replayed here (wait_for_done is False then).
    response: str = ""
    final_msg_id: str = ""
    run_handle: str = ""


@dataclass
class AgentFinalResult:
    conversation_id: str
    turn_id: str
    response: str = ""
    agent_name: str = ""
    channel: str = ""
    finish_reason: str = ""
    error: str = ""
    event_type: str = "done"
    data: Dict[str, Any] = field(default_factory=dict)


class AgentSubmissionRejected(RuntimeError):
    """The runtime refused the submission instead of starting a turn.

    Raised rather than returned because the acknowledgement of a refusal
    carries no ``status`` and no ``wait_for_done``, so both default to
    "accepted" and True -- a caller that waits on the correlated ``done``
    would block forever on a turn that was never started (the Telegram
    bridge waits with no timeout, by project rule).
    """

    def __init__(self, message: str, *, status_code: str = ""):
        super().__init__(message)
        self.status_code = status_code


class AgentResultWaiter:
    """Wait for correlated agent final events without changing SSE broadcast."""

    _instance: Optional["AgentResultWaiter"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._pending: Dict[str, Dict[str, Any]] = {}
        self._pending_lock = threading.Lock()
        self._listener_registered = False
        # Hygiene ceiling for DEAD entries only: a turn that emits no event at
        # all for this long (agent crash, preempt, cancelled queued
        # submission) is swept instead of leaking for the server's lifetime.
        # LIVE turns keep emitting events (progress, tool results, tokens)
        # through the bus, which refreshes last_activity — so a turn that
        # legitimately runs for hours is never swept and its wait() is never
        # bounded. This is NOT a functional timeout (project rule).
        self._WAITER_TTL_SECONDS = 1800.0
        self._last_sweep = 0.0

    def _sweep_stale(self) -> None:
        """Drop pending entries with no activity for the TTL. Called on every
        register (throttled to once per minute)."""
        now = time.time()
        if now - self._last_sweep < 60.0:
            return
        self._last_sweep = now
        stale = [
            key for key, item in self._pending.items()
            if now - float(item.get("last_activity")
                           or item.get("created_at") or 0) > self._WAITER_TTL_SECONDS
        ]
        if not stale:
            return
        with self._pending_lock:
            for key in stale:
                self._pending.pop(key, None)
        import logging
        logging.getLogger(__name__).info(
            "[AgentResultWaiter] swept %d stale waiter entr%s",
            len(stale), "y" if len(stale) == 1 else "ies")

    @classmethod
    def instance(cls) -> "AgentResultWaiter":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def _ensure_listener(self) -> None:
        if self._listener_registered:
            return
        from core.conversation_event_bus import ConversationEventBus
        ConversationEventBus.instance().add_listener(self._on_event)
        self._listener_registered = True

    def register(self, conversation_id: str, turn_id: str,
                 live_callback: Optional[Callable[[str, str, Any], None]] = None) -> None:
        if not conversation_id or not turn_id:
            return
        self._ensure_listener()
        self._sweep_stale()
        key = self._key(conversation_id, turn_id)
        with self._pending_lock:
            existing = self._pending.get(key)
            if existing is not None:
                # NEVER replace: a second waiter (idempotent retry) joins
                # the same entry — and if the turn already finished, the
                # retained terminal result answers it immediately instead
                # of blanking a resolved wait (B1-O idempotent ingress).
                if live_callback is not None:
                    existing.setdefault("live_callbacks", []).append(
                        live_callback)
                existing["last_activity"] = time.time()
                return
            self._pending[key] = {
                "event": threading.Event(),
                "result": None,
                "created_at": time.time(),
                "last_activity": time.time(),
                "live_callbacks": ([live_callback]
                                   if live_callback is not None else []),
            }

    def wait(self, conversation_id: str, turn_id: str,
             timeout: Optional[float] = None) -> Optional[AgentFinalResult]:
        # NO implicit functional timeout — project rule. A LIVE turn (one that
        # keeps emitting events through the bus) is waited on for as long as
        # it runs, however long that is. The only bound is the hygiene
        # ceiling: an entry with NO activity for the waiter TTL (agent crash,
        # preempt, cancelled queued submission) releases the caller instead
        # of holding it forever.
        key = self._key(conversation_id, turn_id)
        deadline = None if timeout is None else time.time() + max(0.0, float(timeout))
        while True:
            with self._pending_lock:
                item = self._pending.get(key)
            if item is None:
                # Swept/cancelled while waiting: nothing will ever arrive.
                return None
            if item["event"].is_set():
                # The resolved entry is RETAINED (until the TTL sweep):
                # concurrent and late waiters of the same turn all read
                # the same terminal instead of racing over one pop.
                return item.get("result")
            if deadline is not None and time.time() >= deadline:
                return None
            if time.time() - float(item.get("last_activity")
                                   or item.get("created_at") or 0) > self._WAITER_TTL_SECONDS:
                # Dead entry (no event at all for the TTL): release the
                # caller; the sweeper drops the entry on its next pass.
                return None
            # Bounded slice: re-check activity/result periodically so a turn
            # that went silent is released shortly after the TTL, while an
            # explicit deadline is honoured to the second. The slice adapts
            # to the TTL (60 s max in production; short for test TTLs).
            _slice = min(60.0, max(0.05, self._WAITER_TTL_SECONDS))
            if deadline is not None:
                _slice = min(_slice, deadline - time.time())
            if _slice <= 0:
                continue
            item["event"].wait(timeout=_slice)

    def cancel(self, conversation_id: str, turn_id: str) -> None:
        with self._pending_lock:
            self._pending.pop(self._key(conversation_id, turn_id), None)

    def _on_event(self, conversation_id: str, event_type: str, data: Any) -> None:
        if not isinstance(data, dict):
            return
        turn_id = str(data.get("turn_id") or data.get("request_msg_id") or "")
        aliases = tuple(dict.fromkeys(
            str(value) for value in (data.get("answered_turn_ids") or ())
            if str(value)))
        lookup_ids = tuple(dict.fromkeys(
            value for value in (turn_id, *aliases) if value))
        with self._pending_lock:
            if lookup_ids:
                matched = [
                    (value, self._pending.get(self._key(conversation_id, value)))
                    for value in lookup_ids
                ]
                matched = [(value, pending)
                           for value, pending in matched if pending is not None]
                item = matched[0][1] if matched else None
                key = self._key(conversation_id, matched[0][0]) if matched else ""
            else:
                matches = [pending for pending_key, pending in self._pending.items()
                           if pending_key.startswith(conversation_id + "\x1f")]
                item = matches[0] if len(matches) == 1 else None
        if not item:
            return
        if event_type not in {"done", "error_event"}:
            for live_callback in list(item.get("live_callbacks") or ()):
                try:
                    live_callback(conversation_id, event_type, data)
                except Exception:
                    import logging
                    logging.getLogger(__name__).debug(
                        "Agent runtime live callback failed", exc_info=True)
        if event_type not in {"done", "error_event"}:
            # Any live event proves the turn is still running: refresh its
            # activity stamp so the TTL sweep/wait bound never mistakes a
            # long-running turn for a dead one.
            with self._pending_lock:
                if self._pending.get(key if turn_id else None) is item:
                    item["last_activity"] = time.time()
            return
        if not turn_id:
            return
        for resolved_turn_id, pending in matched:
            result = AgentFinalResult(
                conversation_id=conversation_id,
                turn_id=resolved_turn_id,
                response=str(data.get("response") or ""),
                agent_name=str(data.get("agent_name") or ""),
                channel=str(data.get("channel") or ""),
                finish_reason=str(data.get("finish_reason") or ""),
                error=(str(data.get("message") or "")
                       if event_type == "error_event" else ""),
                event_type=event_type,
                data=dict(data),
            )
            pending["result"] = result
            pending["event"].set()

    @staticmethod
    def _key(conversation_id: str, turn_id: str) -> str:
        return f"{conversation_id}\x1f{turn_id}"


class AgentRuntimeAPI:
    """Shared submission API used by transports such as Telegram."""

    @staticmethod
    def submit_message(request: AgentRequest) -> AgentSubmission:
        if not request.user_id:
            raise ValueError("AgentRequest.user_id is required")
        if not request.message and not request.attachments:
            raise ValueError("AgentRequest.message or attachments is required")

        turn_id = request.msg_id or f"{request.channel}:{uuid.uuid4().hex}"
        body = {
            "conversation_id": request.conversation_id,
            "message": request.message,
            "attachments": request.attachments,
            "msg_id": turn_id,
        }
        if request.target_agent:
            body["target_agent"] = request.target_agent

        ff = FlowFile(content=json.dumps(body, ensure_ascii=False).encode("utf-8"))
        ff.set_attribute("http.auth.principal", request.user_id)
        ff.set_attribute("agent.client_channel", request.channel or "web")
        ff.set_attribute("agent.request_msg_id", turn_id)
        if request.run_handle:
            ff.set_attribute("agent.run_handle", request.run_handle)
        if request.permission_mode:
            if request.permission_mode not in {"read_only", "default"}:
                raise ValueError("AgentRequest.permission_mode must be read_only or default")
            ff.set_attribute("agent.permission_mode", request.permission_mode)
        # Provenance the flow wants carried, never identity. This is the one
        # place a bot's turn gets its authenticated principal -- every ACL gate
        # downstream reads it -- and source_attributes is the field a flow is
        # most likely to fill from its own visitor payload.
        for key, value in (request.source_attributes or {}).items():
            if str(key) in _RESERVED_REQUEST_ATTRIBUTES:
                logger.warning(
                    "agent runtime: ignoring source attribute %s (reserved)", key)
                continue
            ff.set_attribute(str(key), str(value))

        inst = None
        if request.runtime_port:
            from core.agent_runtime_ports import resolve_agent_runtime_task
            inst = resolve_agent_runtime_task(request.runtime_port)
        else:
            from tasks.ai.agent_loop import AgentLoopTask
            inst = AgentLoopTask._live_instance
        if inst is None:
            if request.runtime_port:
                raise RuntimeError(
                    f"No live AgentLoopTask is available for runtime port: {request.runtime_port}")
            raise RuntimeError("No live AgentLoopTask instance is available")

        waiter = AgentResultWaiter.instance()
        if request.conversation_id:
            waiter.register(request.conversation_id, turn_id, request.live_callback)
        outputs = inst.execute(ff)
        out = outputs[0] if outputs else ff
        try:
            ack = json.loads(out.get_content().decode("utf-8", errors="replace"))
        except Exception:
            ack = {}
        if not isinstance(ack, dict):
            ack = {}
        http_status = str(out.get_attribute("http.response.status") or "")
        if http_status[:1] in {"4", "5"} or ack.get("error"):
            # An authorization refusal answers with the same 404 an unknown
            # conversation_id gets. Nothing was enqueued, so no `done` will
            # ever fire: drop the waiter rather than leave the caller
            # blocked on it.
            if request.conversation_id:
                waiter.cancel(request.conversation_id, turn_id)
            raise AgentSubmissionRejected(
                str(ack.get("error") or "Agent submission was refused"),
                status_code=http_status)
        conversation_id = str(ack.get("conversation_id") or request.conversation_id or
                              out.get_attribute("agent.conversation_id") or "")
        if conversation_id and not request.conversation_id:
            waiter.register(conversation_id, turn_id, request.live_callback)
        return AgentSubmission(
            status=str(ack.get("status") or "accepted"),
            conversation_id=conversation_id,
            turn_id=turn_id,
            target_agent=request.target_agent,
            server_start_time=float(ack.get("server_start_time") or 0.0),
            wait_for_done=bool(ack.get("wait_for_done", True)),
            duplicate=bool(ack.get("duplicate", False)),
            response=str(ack.get("response") or ""),
            final_msg_id=str(ack.get("final_msg_id") or ""),
            run_handle=str(ack.get("run_handle") or ""),
        )

    @staticmethod
    def wait_for_done(conversation_id: str, turn_id: str,
                      timeout: Optional[float] = None) -> Optional[AgentFinalResult]:
        # NO implicit timeout — project rule. Pass an explicit timeout only
        # when the caller genuinely needs a bounded wait.
        return AgentResultWaiter.instance().wait(conversation_id, turn_id, timeout)
