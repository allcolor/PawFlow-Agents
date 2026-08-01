"""Claude Code interactive MITM event ingest service.

The proxy inside the Claude Code container observes Anthropic SSE bytes and
posts scrubbed copies here over WebSocket. Providers consume per-session
queues; if a queue fills, the session is marked unreliable instead of
silently dropping events.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import queue
import re
import threading
import time
import uuid
from urllib.parse import urlsplit
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from core import ServiceFactory
from core.base_service import BaseService

logger = logging.getLogger(__name__)

_SENSITIVE_HEADER_RE = re.compile(
    rb"(?im)^(authorization|cookie|proxy-authorization|set-cookie|x-api-key|anthropic-api-key):[^\r\n]*"
)


def _redact_wire_bytes(data: bytes) -> bytes:
    return _SENSITIVE_HEADER_RE.sub(
        lambda match: match.group(1) + b": <redacted>", data)


class CCIConsumerEvicted(RuntimeError):
    """Raised when a newer consumer took over a session's event stream."""


def _safe_wire_field(data_b64: str, text_repr: str) -> tuple[str, str]:
    try:
        raw = base64.b64decode(data_b64, validate=True)
    except Exception:
        return "<invalid-base64>", "<invalid-base64>"
    redacted = _redact_wire_bytes(raw)
    return (
        base64.b64encode(redacted).decode("ascii"),
        repr(redacted.decode("utf-8", errors="replace")),
    )


@dataclass
class _InjectedText:
    """One thing PawFlow pasted, and how much of it is still unaccounted for.

    A TUI may split one paste into several submits, so the pieces have to be
    recognisable as ours. Recognising them by "is a substring of something we
    pasted in the last ten minutes" was too generous: after the injection had
    already been consumed in full, a human typing any twelve-character phrase
    that happened to occur inside it was silently swallowed -- never persisted,
    never answered.

    So an injection is CONSUMED as its pieces arrive. ``remaining`` starts as
    the whole normalized text and each claimed piece is cut out of it; when
    what is left is too small to identify anything, the entry is dropped and
    can match nothing more. ``last_seen`` bounds the same thing in time: the
    pieces of one paste belong to one burst, not to the rest of the session.
    """
    at: float
    digest: str
    full: str
    remaining: str
    last_seen: float


@dataclass
class CCInteractiveSessionEvents:
    session_token: str
    events: "queue.Queue[dict]"
    container_id: str = ""
    user_id: str = ""
    conversation_id: str = ""
    agent_name: str = ""
    provider: str = "claude-code-interactive"
    connected: bool = False
    unreliable: bool = False
    error: str = ""
    manual_capture_active: bool = False
    manual_capture_pending: int = 0
    #: Assistant messages persisted by the capture in progress. A captured
    #: turn writes its text before the coordinator returns, so the model and
    #: token counts are only known once it does -- these ids are what the
    #: closing meta update is addressed to.
    captured_msg_ids: list = field(default_factory=list)
    injected_prompts: dict[str, float] = field(default_factory=dict)
    pending_injected_prompt_ignores: list[float] = field(default_factory=list)
    # What PawFlow pasted, as _InjectedText entries. A TUI is free to submit
    # one paste as several prompts; the digest of a fragment matches nothing,
    # so the text itself is what tells a fragment of our own injection apart
    # from something a human typed. Each entry is CONSUMED as its pieces
    # arrive -- see _InjectedText -- so it stops matching once it is spent.
    injected_prompt_texts: list = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    last_event_at: float = 0.0
    # Listener liveness: when a PawFlow-injected prompt is submitted while
    # no request coordinator is polling wait_event anymore (it timed out or
    # died), the turn would run invisibly — these timestamps let the service
    # detect that and capture the orphan turn like a manual tmux one.
    last_wait_at: float = 0.0
    injected_intent_at: float = 0.0
    # A request coordinator claims the stream BEFORE it sends anything: the
    # send blocks on TUI readiness, paste, submit and verification, and only
    # then does run() start polling. Until that first poll neither timestamp
    # above exists, so the orphan-turn net saw an unowned stream and evicted
    # the coordinator that was about to read it. The claim is the ownership
    # fact; these two are its consequences.
    last_request_claim_at: float = 0.0
    # Tool-id dedup fallback, used only when the pool no longer knows the
    # container behind this session (see _capture_dedup_sets). Lives here so
    # two chained captures of the same session still dedup against each other.
    emitted_tool_use_ids: set = field(default_factory=set)
    emitted_tool_result_ids: set = field(default_factory=set)
    # Set when the turn's model runs its tools from inside a code body. Those
    # calls never appear in the stream as calls, so the relay executing them
    # is what reports them -- and it must only do so for a session that is
    # actually in code mode, or it would double every ordinary tool row.
    code_mode_open: bool = False
    # Exactly one consumer may read `events`: queue.Queue hands each item to
    # a single getter, so two live coordinators SPLIT the SSE stream between
    # them. Claiming bumps the epoch; the previous holder is evicted on its
    # next wait_event instead of silently stealing half the turn.
    consumer_epoch: int = 0
    # An event an evicted consumer woke up holding. Checking the epoch only
    # before the blocking get() left one gap: a coordinator already parked in
    # get() when the epoch is bumped is handed the next event -- the first one
    # of the NEW owner's turn -- and used to drop it on its way out. Truncated
    # text, or a tool call severed from its arguments. It goes here instead,
    # and the next waiter takes it before touching the queue, so the event is
    # neither lost nor delivered out of order.
    pushback: list = field(default_factory=list)
    # Claim changes, pushback and queue delivery share one condition. This lets
    # a new claim wake an old waiter before either can take the replacement's
    # first event, and makes choosing pushback-vs-queue one ordered operation.
    stream_condition: threading.Condition = field(default_factory=threading.Condition)


class CCInteractiveEventService(BaseService):
    TYPE = "ccInteractiveEvents"
    VERSION = "1.0.0"
    NAME = "Interactive CLI Events"
    DESCRIPTION = (
        "Receives MITM-observed Claude Code and Codex SSE events over WebSocket")

    _instances_lock = threading.Lock()
    _instances: Dict[str, "CCInteractiveEventService"] = {}

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._service_id = self.config.get("_service_id", "")
        self._connection = None
        self._route_path = ""
        self._sessions: Dict[str, CCInteractiveSessionEvents] = {}
        self._sessions_lock = threading.RLock()
        try:
            self._max_queue = int(self.config.get("max_queue", 4096) or 4096)
        except (TypeError, ValueError):
            self._max_queue = 4096

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "token": {"type": "string", "required": True, "sensitive": True,
                      "description": "Service token required by the container proxy"},
            "max_queue": {"type": "integer", "required": False, "default": 4096,
                          "description": "Maximum queued events per interactive session"},
        }

    @property
    def service_id(self) -> str:
        return self._service_id

    def connect(self):
        from services.http_listener_service import HTTPListenerService
        instances = HTTPListenerService.all_instances()
        if not instances:
            logger.warning(
                "CCInteractiveEventService %s: no HTTPListenerService running yet",
                self._service_id)
            self._initialized = False
            return
        listener = next(iter(instances.values()))
        route = f"/ws/cc-interactive/events/{self._service_id}"
        self._route_path = route
        listener.register_route(
            "GET", route, self._service_id, callback=None,
            ws_handler=self._handle_ws, public=True, private_only=True)
        self._connection = listener
        with self._instances_lock:
            self._instances[self._service_id] = self
        self._initialized = True
        logger.info("CC interactive event service registered at %s", route)

    def disconnect(self):
        if self._connection and self._route_path:
            try:
                self._connection.unregister_routes(self._service_id)
            except Exception:
                logger.debug("CC interactive event route unregister failed", exc_info=True)
        with self._instances_lock:
            self._instances.pop(self._service_id, None)
        with self._sessions_lock:
            self._sessions.clear()
        self._connection = None
        self._route_path = ""
        self._initialized = False

    def register_session(self, session_token: str, *, user_id: str = "",
                         conversation_id: str = "",
                         agent_name: str = "",
                         provider: str = "") -> CCInteractiveSessionEvents:
        if not session_token:
            raise ValueError("session_token is required")
        with self._sessions_lock:
            state = self._sessions.get(session_token)
            if state is None:
                state = CCInteractiveSessionEvents(
                    session_token=session_token,
                    events=queue.Queue(maxsize=self._max_queue),
                )
                self._sessions[session_token] = state
            if user_id:
                state.user_id = user_id
            if conversation_id:
                state.conversation_id = conversation_id
            if agent_name:
                state.agent_name = agent_name
            if provider:
                state.provider = provider
            return state

    def unregister_session(self, session_token: str) -> None:
        with self._sessions_lock:
            self._sessions.pop(session_token, None)

    def mark_code_mode(self, session_token: str) -> None:
        """Record that this session runs its tools from inside a code body."""
        state = self.session_state(session_token)
        if state is not None and not state.code_mode_open:
            state.code_mode_open = True
            logger.info(
                "[cci-events] session=%s entered code mode; tool rows now come "
                "from the relay", session_token[:8])

    @classmethod
    def publish_agent_event(cls, conversation_id: str, agent_name: str,
                            event: dict) -> bool:
        """Feed one observed-tool event into an agent's code-mode session.

        The relay is the only place that knows what a code body ran: the model
        emitted one opaque item and every call happened inside it. Publishing
        them here puts them on the same queue as the MITM's observations, so
        they become rows through the one path all providers share instead of a
        second rendering scheme nobody else uses.

        Returns True when the event reached a session.
        """
        if not conversation_id or not agent_name:
            return False
        with cls._instances_lock:
            services = list(cls._instances.values())
        for service in services:
            with service._sessions_lock:
                # Connected first, then newest. A session is never
                # unregistered, so a conversation accumulates the state of
                # every container it ever had — all still flagged
                # code_mode_open. Insertion order handed the event to the
                # OLDEST of them: publish returned True while it sat in a
                # queue nobody reads, and the live UI drew no tool row at all.
                # `connected` is the evidence `live_session` trusts (the proxy
                # WebSocket is up exactly while the container is alive), but
                # it only orders here — a session that is in code mode is
                # still tried if nothing claims to be connected, so this can
                # only improve on the arbitrary order it replaces.
                candidates = [
                    (bool(state.connected),
                     state.last_event_at or state.created_at, token)
                    for token, state in service._sessions.items()
                    if state.code_mode_open
                    and state.conversation_id == conversation_id
                    and state.agent_name == agent_name]
                tokens = [token for _conn, _ts, token in
                          sorted(candidates, key=lambda c: (c[0], c[1]),
                                 reverse=True)]
            for token in tokens:
                try:
                    service.publish_event(token, dict(event))
                    return True
                except RuntimeError as exc:
                    logger.debug(
                        "[cci-events] code-mode publish refused for %s: %s",
                        token[:8], exc)
        return False

    def remember_injected_prompt(self, session_token: str, prompt: str) -> None:
        if not session_token or not prompt:
            return
        state = self.register_session(session_token)
        digest = self._prompt_digest(prompt)
        now = time.time()
        cutoff = now - 600
        with self._sessions_lock:
            state.injected_prompts = {
                key: ts for key, ts in state.injected_prompts.items()
                if ts >= cutoff
            }
            state.injected_prompts[digest] = now
            state.injected_prompt_texts = [
                item for item in state.injected_prompt_texts
                if item.at >= cutoff
            ]
            normalized = " ".join((prompt or "").split())
            state.injected_prompt_texts.append(_InjectedText(
                at=now, digest=digest, full=normalized,
                remaining=normalized, last_seen=now))
            state.pending_injected_prompt_ignores = [
                ts for ts in state.pending_injected_prompt_ignores
                if ts >= cutoff
            ]
            state.pending_injected_prompt_ignores.append(now)
            # A coordinator will start polling as soon as the tmux send
            # returns — suppress orphan-turn capture for the send window.
            state.injected_intent_at = now

    def session_state(self, session_token: str) -> Optional[CCInteractiveSessionEvents]:
        with self._sessions_lock:
            return self._sessions.get(session_token)

    @classmethod
    def live_session(cls, conversation_id: str,
                     agent_name: str) -> Optional[CCInteractiveSessionEvents]:
        """Return the connected proxy session for (conv, agent), if any.

        The MITM proxy sits between the tmux and PawFlow: its WebSocket is up
        exactly while a container is alive, and every event the session sees
        arrived through it. So a connected session is direct evidence of a
        live tmux — evidence that does not depend on PawFlow's own turn
        bookkeeping, which is precisely what goes stale when Claude Code
        resumes work outside a streaming worker.
        """
        if not conversation_id:
            return None
        with cls._instances_lock:
            services = list(cls._instances.values())
        for svc in services:
            with svc._sessions_lock:
                for state in svc._sessions.values():
                    if (state.conversation_id == conversation_id
                            and (state.agent_name or "") == (agent_name or "")
                            and state.connected):
                        return state
        return None

    def claim_consumer(self, session_token: str, *, kind: str = "request") -> int:
        """Take exclusive ownership of a session's event stream.

        A ``request`` claim always wins: it is the authoritative reader for
        the turn the user is waiting on, and any stale coordinator still
        polling is evicted. A ``capture`` claim (the orphan-turn safety net)
        refuses when a request coordinator is actively polling — the net
        must never take the stream away from the real turn. Returns the
        granted epoch, or 0 when the claim is refused.
        """
        state = self.register_session(session_token)
        with self._sessions_lock:
            if kind != "request" and (
                    time.time() - state.last_wait_at < self._LISTENER_FRESH_SECONDS):
                return 0
            with state.stream_condition:
                state.consumer_epoch += 1
                if kind == "request":
                    state.last_request_claim_at = time.time()
                    # Code mode is a property of the turn, not of the session:
                    # the next turn may call its tools directly, and a stale
                    # flag would have the relay add a row beside the provider's.
                    state.code_mode_open = False
                state.stream_condition.notify_all()
                return state.consumer_epoch

    def release_consumer(self, session_token: str, epoch: int = 0) -> None:
        """Give up a claim whose coordinator never started reading.

        A request claim suppresses the orphan-turn net for two minutes, on the
        reasoning that the coordinator is busy inside its send (TUI readiness,
        paste, settle, double Enter, verification) and will start polling any
        moment. When the send FAILS the coordinator is never built, and
        nothing withdrew the claim: the net stayed muted for the rest of the
        grace window.

        That window is exactly when the turn happens. The user watches the
        send fail, presses Enter in the tmux themselves, and the TUI runs the
        prompt it was holding all along -- a real turn, streamed through the
        proxy, addressed to nobody. The net exists to adopt precisely that
        turn, and it declined because a claim from a dead coordinator was
        still on the books. Releasing hands the stream back so the next
        request_start is adopted and the answer reaches the webchat.

        Scoped by epoch: a claim taken since then belongs to a live turn and
        must not be cleared by the loser's cleanup. Passing 0 releases
        unconditionally.
        """
        state = self.session_state(session_token)
        if state is None:
            return
        with self._sessions_lock:
            with state.stream_condition:
                if epoch and epoch != state.consumer_epoch:
                    return
                state.last_request_claim_at = 0.0
                # The paste intent is the other half of the same suppression:
                # remember_injected_prompt sets it so the net keeps quiet
                # while the send runs. The send is over.
                state.injected_intent_at = 0.0

    def wait_event(self, session_token: str, timeout: Optional[float] = None,
                   epoch: int = 0) -> dict:
        state = self.session_state(session_token)
        if state is None:
            raise RuntimeError("Unknown CC interactive session")
        deadline = (None if timeout is None else
                    time.monotonic() + max(0.0, timeout))
        with state.stream_condition:
            state.last_wait_at = time.time()
            while True:
                if epoch and epoch != state.consumer_epoch:
                    raise CCIConsumerEvicted(
                        "CC interactive session taken over by a newer consumer")
                if state.unreliable:
                    raise RuntimeError(
                        state.error or "CC interactive session is unreliable")
                if state.pushback:
                    return state.pushback.pop(0)
                try:
                    return state.events.get_nowait()
                except queue.Empty:
                    pass
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return {}
                else:
                    remaining = None
                state.stream_condition.wait(remaining)

    def drain_session(self, session_token: str) -> int:
        state = self.session_state(session_token)
        if state is None:
            return 0
        drained = 0
        # A pushed-back event is part of the stream, so a drain has to take it
        # too -- leaving it behind would hand a stale event to the next turn.
        with state.stream_condition:
            drained += len(state.pushback)
            state.pushback.clear()
            while True:
                try:
                    state.events.get_nowait()
                    drained += 1
                except queue.Empty:
                    return drained

    def publish_event(self, session_token: str, event: dict, *, block: bool = True) -> None:
        state = self.session_state(session_token)
        if state is None:
            raise RuntimeError("Unknown CC interactive session")
        if state.unreliable:
            raise RuntimeError(state.error or "CC interactive session is unreliable")
        event.setdefault("session_token", session_token)
        event.setdefault("timestamp", time.time())
        state.last_event_at = time.time()
        self._log_event_summary(session_token, event)
        if event.get("type") == "wire":
            return
        self._maybe_ingest_manual_prompt(state, event)
        self._maybe_adopt_orphan_turn(state, event)
        try:
            # Do not hold stream_condition while a bounded queue put blocks:
            # the consumer needs that condition to drain the queue.
            state.events.put(event, block=block, timeout=5 if block else 0)
        except queue.Full as exc:
            with state.stream_condition:
                state.unreliable = True
                state.error = "CC interactive event queue overflow"
                state.stream_condition.notify_all()
            raise RuntimeError(state.error) from exc
        with state.stream_condition:
            state.stream_condition.notify_all()

    @staticmethod
    def _log_event_summary(session_token: str, event: dict) -> None:
        etype = event.get("type", "")
        if etype == "sse":
            payload = event.get("payload") or {}
            ptype = payload.get("type") or event.get("event", "")
            if ptype == "content_block_delta":
                delta = payload.get("delta") or {}
                dtype = delta.get("type", "")
                text = delta.get("text", "") if dtype == "text_delta" else ""
                logger.debug(
                    "CC interactive MITM event: session=%s request=%s type=%s delta=%s text_len=%d text_preview=%r",
                    session_token[:8], event.get("request_id", ""), ptype, dtype,
                    len(text), text[:24])
                return
            logger.debug(
                "CC interactive MITM event: session=%s request=%s type=%s payload_keys=%s",
                session_token[:8], event.get("request_id", ""), ptype,
                sorted(payload.keys())[:8])
        elif etype == "request_error":
            logger.warning(
                "CC interactive proxy event: session=%s type=%s request=%s path=%s status=%s ctype=%s encoding=%s reason=%s error=%s",
                session_token[:8], etype, event.get("request_id", ""),
                event.get("path", ""), event.get("status", ""),
                event.get("content_type", ""), event.get("content_encoding", ""),
                event.get("reason", ""), event.get("error", ""))
        elif etype in {"request_start", "request_stop", "response_start", "response_ignored"}:
            logger.debug(
                "CC interactive proxy event: session=%s type=%s request=%s path=%s status=%s ctype=%s encoding=%s reason=%s",
                session_token[:8], etype, event.get("request_id", ""),
                event.get("path", ""), event.get("status", ""),
                event.get("content_type", ""), event.get("content_encoding", ""),
                event.get("reason", ""))
        elif etype == "wire":
            safe_b64, safe_text = _safe_wire_field(
                str(event.get("data_b64", "")), str(event.get("text_repr", "")))
            logger.debug(
                "CC interactive proxy wire: session=%s request=%s direction=%s stage=%s seq=%s bytes=%s sha256=%s data_b64=%s text=%s",
                session_token[:8], event.get("request_id", ""),
                event.get("direction", ""), event.get("stage", ""),
                event.get("seq", ""), event.get("bytes", ""),
                event.get("sha256", ""), safe_b64, safe_text)
        elif etype == "hook":
            logger.info(
                "CC interactive hook event: session=%s hook=%s",
                session_token[:8], event.get("hook_event_name", ""))

    def _maybe_ingest_manual_prompt(self, state: CCInteractiveSessionEvents,
                                    event: dict) -> None:
        if event.get("type") != "hook" or event.get("hook_event_name") != "UserPromptSubmit":
            return
        data = event.get("input") or {}
        if not isinstance(data, dict):
            return
        prompt = data.get("prompt", "")
        if not isinstance(prompt, str):
            prompt = ""
        if data.get("pawflow_injected_prompt"):
            self._consume_pending_injected_prompt(state, prompt)
            self._capture_orphan_injected_turn(state)
            return
        if self._consume_injected_prompt(state, prompt):
            self._capture_orphan_injected_turn(state)
            return
        if data.get("pawflow_managed_prompt"):
            return
        if not prompt.strip():
            return
        if not state.conversation_id or not state.agent_name:
            logger.debug("manual CC prompt ignored without session binding")
            return
        try:
            from core.conversation_writer import ConversationWriter
            from core.llm_client import stamp_message
            msg = stamp_message({
                "role": "user",
                "content": prompt,
                "source": {
                    "type": "user",
                    "name": state.user_id,
                    "target_agent": state.agent_name,
                    "input": ("codex_interactive_tmux"
                              if state.provider == "codex-interactive"
                              else "cc_interactive_tmux"),
                },
                "channel": "tmux",
            }, state.conversation_id)
            ConversationWriter.for_conversation(
                state.conversation_id).enqueue_message(
                    msg, agent_name=state.agent_name, user_id=state.user_id,
                    sse_events=[{"type": "new_message", "data": {
                        "role": "user",
                        "content": msg.get("content", ""),
                        "msg_id": msg.get("msg_id", ""),
                        "ts": msg.get("ts"),
                        "source": msg.get("source") or {},
                        "channel": msg.get("channel", ""),
                    }}])
            logger.info(
                "CC interactive manual tmux prompt persisted: conv=%s agent=%s msg=%s chars=%d",
                state.conversation_id[:8], state.agent_name, msg.get("msg_id", ""),
                len(prompt))
        except Exception:
            logger.warning("CC interactive manual prompt persist failed", exc_info=True)
            return
        self._start_manual_capture(state)

    # A live request coordinator polls wait_event at least every 0.25s;
    # a last_wait_at older than this means nobody is streaming the turn.
    _LISTENER_FRESH_SECONDS = 3.0
    # Worst-case gap between the prompt injection (tmux paste) and the
    # coordinator's first wait_event poll — the send blocks through paste,
    # settle, double-Enter and submit verification before run() starts.
    _INJECT_INTENT_GRACE_SECONDS = 60.0
    # Ceiling on claim -> first wait_event. The send it covers is bounded by
    # the TUI readiness wait (PAWFLOW_CCI_PROMPT_READY_TIMEOUT_SECONDS, 45s)
    # plus paste, settle, double Enter and submit verification. A coordinator
    # that claims and then dies inside that window has already failed its
    # turn with an error, so there is no invisible response left to capture --
    # suppressing the net here costs nothing and stops it killing a live turn.
    _REQUEST_CLAIM_GRACE_SECONDS = 120.0

    def _request_listener_recent(self, state: CCInteractiveSessionEvents) -> bool:
        now = time.time()
        return (state.manual_capture_active
                or now - state.last_wait_at < self._LISTENER_FRESH_SECONDS
                or now - state.injected_intent_at < self._INJECT_INTENT_GRACE_SECONDS
                or now - state.last_request_claim_at
                < self._REQUEST_CLAIM_GRACE_SECONDS)

    def _capture_orphan_injected_turn(self, state: CCInteractiveSessionEvents) -> None:
        """Safety net for injected prompts submitted with no listener.

        Scenario: PawFlow injects a prompt, the submit Enter is swallowed by
        the TUI, the request coordinator eventually times out and dies; a
        human then presses Enter in the tmux. The hook reports the injected
        digest, so the manual-prompt path is skipped — and the whole turn
        would run invisibly (tmux active, webchat silent). When no request
        coordinator has polled recently, capture the response like a manual
        tmux turn. The user message is NOT re-persisted: an injected prompt
        already originates from the conversation, so only the assistant
        response is missing.
        """
        self._adopt_orphan_turn(state, "injected prompt submitted")

    def _maybe_adopt_orphan_turn(self, state: CCInteractiveSessionEvents,
                                 event: dict) -> None:
        """Tmux is working but nobody is streaming the turn: adopt it.

        A request_start for a real /v1/messages call means the CC session is
        actively running a turn. If no request coordinator has polled
        wait_event recently (it died or never existed) the turn is invisible
        to the conversation — attach a capture so the activity and response
        reach the webchat. request_start is the trigger (rather than every
        event) because it only fires mid-turn: post-Stop stragglers can
        never spawn a capture that would outlive its turn and steal events
        from the next request's coordinator.
        """
        if event.get("type") != "request_start":
            return
        path = event.get("path", "") or ""
        is_provider_request = (
            urlsplit(path).path.rstrip("/").endswith("/responses")
            if state.provider == "codex-interactive"
            else path.startswith("/v1/messages"))
        if not is_provider_request or event.get("ignore_reason"):
            return
        self._adopt_orphan_turn(state, "request in flight")

    def _adopt_orphan_turn(self, state: CCInteractiveSessionEvents,
                           reason: str) -> None:
        if self._request_listener_recent(state):
            return
        if not state.conversation_id or not state.agent_name:
            logger.debug("orphan CC turn ignored without session binding")
            return
        logger.warning(
            "CC interactive turn with no listening request (%s, session=%s); "
            "capturing orphan turn", reason, state.session_token[:8])
        self._start_manual_capture(state)

    @staticmethod
    def _prompt_digest(prompt: str) -> str:
        normalized = (prompt or "").rstrip("\r\n")
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def _consume_injected_prompt(self, state: CCInteractiveSessionEvents,
                                 prompt: str) -> bool:
        digest = self._prompt_digest(prompt) if prompt else ""
        now = time.time()
        cutoff = now - 600
        with self._sessions_lock:
            state.injected_prompts = {
                key: ts for key, ts in state.injected_prompts.items()
                if ts >= cutoff
            }
            state.pending_injected_prompt_ignores = [
                ts for ts in state.pending_injected_prompt_ignores
                if ts >= cutoff
            ]
            state.injected_prompt_texts = [
                item for item in state.injected_prompt_texts
                if item.at >= cutoff
            ]
            if digest and digest in state.injected_prompts:
                state.injected_prompts.pop(digest, None)
                # The whole paste arrived as ONE submit, so there are no pieces
                # left to recognise. Dropping the text here is what stops it
                # from swallowing a real prompt later: without it the injection
                # stayed matchable for the rest of the 600s window, and any
                # phrase the user typed that occurred inside it was consumed as
                # a fragment -- neither persisted nor answered.
                state.injected_prompt_texts = [
                    item for item in state.injected_prompt_texts
                    if item.digest != digest
                ]
                if state.pending_injected_prompt_ignores:
                    state.pending_injected_prompt_ignores.pop(0)
                return True
            if self._claim_injection_fragment(state, prompt, now):
                # One paste, several submits: the TUI split what PawFlow sent
                # and each piece arrives as its own UserPromptSubmit. Only the
                # first carries the ticket; without this the rest are filed as
                # messages the user typed, published under their name, and
                # answered one by one -- the agent replying to fragments of a
                # tool result it was handed itself.
                #
                # The ticket is still spent on the first piece, exactly as the
                # digest path spends it: left unspent it survives the paste and
                # swallows the next thing the user really types. The pieces
                # after it need no ticket -- the text is what identifies them.
                if state.pending_injected_prompt_ignores:
                    state.pending_injected_prompt_ignores.pop(0)
                return True
            if state.pending_injected_prompt_ignores:
                state.pending_injected_prompt_ignores.pop(0)
                self._pop_oldest_injected_prompt_locked(state)
                return True
            return False

    # A fragment shorter than this proves nothing: "ok", "go", a bare digit
    # occur inside any large paste and are also exactly what a human types.
    # Below it, the prompt is treated as manual -- losing a two-character
    # fragment of our own paste is harmless, swallowing a two-character human
    # message is not.
    _MIN_FRAGMENT_CHARS = 12
    # The pieces of one split paste are submitted as one burst -- keystrokes of
    # a single event, possibly with a turn running between two of them, but not
    # spread across a session. The digests live for 600s because a digest is an
    # exact match and cannot claim anything it did not produce; a substring can,
    # so it gets its own, far shorter window, refreshed by each piece claimed.
    _FRAGMENT_BURST_SECONDS = 180.0

    @staticmethod
    def _is_fragment_of_injection(state: CCInteractiveSessionEvents,
                                  prompt: str, now: float = 0.0):
        """Return the injection `prompt` is an unclaimed slice of, or None.

        Called with the sessions lock held. Whitespace is normalised on both
        sides because a TUI re-wraps what it renders, so a fragment is rarely
        byte-identical to its span of the original.

        Matched against what is LEFT of the injection, not against the whole of
        it: a piece already accounted for must not be recognisable twice, and
        an injection entirely accounted for must be recognisable no more.
        """
        needle = " ".join((prompt or "").split())
        if len(needle) < CCInteractiveEventService._MIN_FRAGMENT_CHARS:
            return None
        moment = now or time.time()
        burst = CCInteractiveEventService._FRAGMENT_BURST_SECONDS
        for item in state.injected_prompt_texts:
            if moment - item.last_seen > burst:
                continue
            # The complete injection is the digest path's business; letting it
            # through here would spend the ticket twice over.
            if needle == item.full:
                continue
            if needle in item.remaining:
                return item
        return None

    @classmethod
    def _claim_injection_fragment(cls, state: CCInteractiveSessionEvents,
                                  prompt: str, now: float) -> bool:
        """Recognise a piece of our own paste and cut it out of what is left."""
        item = cls._is_fragment_of_injection(state, prompt, now)
        if item is None:
            return False
        needle = " ".join((prompt or "").split())
        item.remaining = item.remaining.replace(needle, " ", 1)
        item.last_seen = now
        if len(item.remaining.strip()) < cls._MIN_FRAGMENT_CHARS:
            # Spent: what is left could no longer identify anything as ours,
            # and keeping it would only give it the chance to claim something
            # of the user's.
            state.injected_prompt_texts = [
                other for other in state.injected_prompt_texts
                if other is not item
            ]
        return True

    @staticmethod
    def _pop_oldest_injected_prompt_locked(state: CCInteractiveSessionEvents) -> None:
        if not state.injected_prompts:
            return
        oldest = min(state.injected_prompts, key=state.injected_prompts.get)
        state.injected_prompts.pop(oldest, None)
        # And the text recorded with it. A record whose digest is gone can
        # never be matched exactly again, so all its remaining text can still
        # do is claim a phrase the user typed.
        state.injected_prompt_texts = [
            item for item in state.injected_prompt_texts
            if item.digest != oldest
        ]

    def _consume_pending_injected_prompt(self, state: CCInteractiveSessionEvents,
                                         prompt: str = "") -> bool:
        """Spend a ticket for a submit the hook itself marked as ours.

        The flag says "this submit is PawFlow's". It does not say how much of
        the injection it carried, and spending only the ticket left the text
        behind: digests=0, tickets=0, texts=1, and for the rest of the 600s
        window any twelve-character phrase the user typed that occurred inside
        that text was claimed as a fragment -- neither persisted nor answered.
        The user's message vanished with no trace anywhere.

        So the text is accounted for here exactly as the digest path accounts
        for it: a submit carrying the whole injection drops it, a submit
        carrying a piece cuts that piece out of what is left (a TUI that splits
        one paste into several submits still needs to recognise the rest), and
        only a submit that identifies nothing falls back to the oldest record.
        """
        now = time.time()
        cutoff = now - 600
        with self._sessions_lock:
            state.pending_injected_prompt_ignores = [
                ts for ts in state.pending_injected_prompt_ignores
                if ts >= cutoff
            ]
            state.injected_prompt_texts = [
                item for item in state.injected_prompt_texts
                if item.at >= cutoff
            ]
            if not state.pending_injected_prompt_ignores:
                return False
            state.pending_injected_prompt_ignores.pop(0)
            digest = self._prompt_digest(prompt) if prompt else ""
            if digest and digest in state.injected_prompts:
                state.injected_prompts.pop(digest, None)
                state.injected_prompt_texts = [
                    item for item in state.injected_prompt_texts
                    if item.digest != digest
                ]
                return True
            if self._claim_injection_fragment(state, prompt, now):
                return True
            self._pop_oldest_injected_prompt_locked(state)
            return True

    def _start_manual_capture(self, state: CCInteractiveSessionEvents) -> None:
        with self._sessions_lock:
            if state.manual_capture_active:
                state.manual_capture_pending += 1
                return
            state.manual_capture_active = True
        thread = threading.Thread(
            target=self._run_manual_capture,
            args=(state.session_token,),
            name=f"cci-manual-capture-{state.session_token[:8]}",
            daemon=True,
        )
        thread.start()

    @staticmethod
    def _active_turn_marker(state: CCInteractiveSessionEvents, *,
                           register: bool) -> None:
        """Mirror a captured turn into the UI's active-agent truth.

        A captured turn runs entirely outside the streaming worker, so none
        of the usual bookkeeping fires: `_active_turns` stays empty and the
        webchat shows the agent idle while Claude Code is visibly working in
        the tmux. That happens for a human tmux prompt AND for Claude Code's
        own self-injected prompts (a background-task notification), neither
        of which passes through send_text. `_active_turns` is deliberately
        provider-agnostic and owned by whoever runs the turn — so a capture
        must register itself there, and release it when it ends.
        """
        if not state.conversation_id:
            return
        try:
            from tasks.ai.agent_loop import AgentLoopTask
            inst = AgentLoopTask._live_instance
            if not inst:
                return
            key = (f"{state.conversation_id}:{state.agent_name}"
                   if state.agent_name else state.conversation_id)
            with inst._active_contexts_lock:
                if register:
                    inst._active_turns[key] = {
                        "conversation_id": state.conversation_id,
                        "agent_name": state.agent_name,
                        "started_at": time.time(),
                        "status": "running",
                        "message_preview": "(tmux turn)",
                        "generation": 0,
                    }
                else:
                    inst._active_turns.pop(key, None)
        except Exception:
            logger.debug("CC interactive active-turn marker failed", exc_info=True)

    def _publish_capture_active(self, state: CCInteractiveSessionEvents, *,
                                active: bool) -> None:
        self._active_turn_marker(state, register=active)
        if not state.conversation_id:
            return
        try:
            from core.conversation_writer import ConversationWriter
            event = ({"type": "thinking",
                      "data": {"conversation_id": state.conversation_id,
                               "agent_name": state.agent_name}}
                     if active else
                     {"type": "active_released",
                      "data": {"conversation_id": state.conversation_id,
                               "agent_name": state.agent_name}})
            ConversationWriter.for_conversation(
                state.conversation_id).enqueue_sse_events([event])
        except Exception:
            logger.debug("CC interactive capture SSE failed", exc_info=True)

    @staticmethod
    def _drain_pending_after_capture(
            state: CCInteractiveSessionEvents) -> None:
        """Wake the agent for messages queued while the capture held the turn.

        A captured turn registers `_active_turns` but never creates a
        streaming worker, an `_active_contexts` entry, or an
        `_active_claude_client`. agent_streaming reads that combination as
        "already active but not preemptable" and parks every incoming user
        message in the PendingQueue — and because no worker owns this turn,
        nothing performs the end-of-turn drain that a normal turn does in
        `_agent_streaming_loop`. The messages stay queued until a force stop
        discards them: the webchat looks alive (the marker is up) while
        typing into it reaches nothing. Releasing the marker is therefore
        not enough; the capture must also hand the queue back.
        """
        if not state.conversation_id:
            return
        try:
            from core.pending_queue import PendingQueue
            count = PendingQueue.for_agent(
                state.conversation_id, state.agent_name or "").peek_count()
            if not count:
                return
            from tasks.ai.agent_loop import AgentLoopTask
            AgentLoopTask.wake_agent(
                state.conversation_id, state.agent_name or "",
                reason=f"[pending] {count} queued msg(s) after tmux capture",
                user_id=state.user_id or "",
                delay=0.0,
                even_if_active=True,
            )
            logger.info(
                "CC interactive capture handed back %d queued msg(s): "
                "conv=%s agent=%s",
                count, state.conversation_id[:8], state.agent_name)
        except Exception:
            logger.debug("CC interactive pending handback failed", exc_info=True)

    def _capture_stream_callbacks(self, state: CCInteractiveSessionEvents):
        """Build the live callbacks a captured turn needs to reach the UI.

        A captured turn carries the same wire traffic as any other turn — the
        MITM proxy observed all of it — so it must reach the SSE listeners the
        same way. A PawFlow-driven turn gets there through the agent loop's
        callbacks; a capture has no loop, so it carries its own and persists
        each block as it arrives instead of one lump when the turn ends.
        Mirrors the Antigravity observer's manual ingest, which streams
        out-of-band tmux activity by default.

        Returns ``(text_callback, block_callback)``.
        """
        cid = state.conversation_id
        live = {"msg_id": "", "ts": 0.0}

        def _source():
            # `provider` is what the meta line under a message is built from
            # (buildMetaLine reads model / provider / tokens and renders
            # nothing when it has none of them). A captured turn runs outside
            # the streaming worker, so it never gets the richer source the
            # agent loop builds -- without this it arrived bare and the
            # message showed no meta line at all.
            # Model and token counts stay absent on purpose: this observer
            # sees tmux activity, not the provider's usage, and a meta line
            # is worth less than nothing if it states numbers nobody measured.
            return {"type": "agent", "name": state.agent_name,
                    "provider": state.provider,
                    "input": ("codex_interactive_tmux"
                              if state.provider == "codex-interactive"
                              else "cc_interactive_tmux")}

        def _writer():
            from core.conversation_writer import ConversationWriter
            return ConversationWriter.for_conversation(cid)

        def _text_callback(text: str) -> None:
            """Publish each delta so the answer appears while it is written."""
            if not text:
                return
            if not live["msg_id"]:
                live["msg_id"] = uuid.uuid4().hex[:12]
                live["ts"] = time.time()
            try:
                from core.conversation_event_bus import ConversationEventBus
                ConversationEventBus.instance().publish_event(cid, "token", {
                    "agent_name": state.agent_name,
                    "text": text,
                    "msg_id": live["msg_id"],
                    "ts": live["ts"],
                    "source": _source(),
                })
            except Exception:
                logger.debug("CC interactive capture token publish failed",
                             exc_info=True)

        def _block_callback(event_type: str, payload: dict) -> None:
            from core.llm_client import (
                has_complete_mcp_tool_call, is_mcp_tool_call_name,
                stamp_message, unwrap_mcp_tool)
            try:
                if event_type == "text":
                    text = payload.get("text", "") or ""
                    if not text.strip():
                        return
                    # Reuse the id the streamed tokens carried so the client
                    # replaces its live preview instead of showing it twice.
                    msg = stamp_message({
                        "role": "assistant", "content": text,
                        "source": _source(), "channel": "tmux",
                        "msg_id": live["msg_id"] or None,
                    }, cid)
                    live["msg_id"] = ""
                    state.captured_msg_ids.append(msg.get("msg_id", ""))
                    _writer().enqueue_message(
                        msg, agent_name=state.agent_name,
                        user_id=state.user_id,
                        sse_events=[{"type": "new_message", "data": {
                            "role": "assistant",
                            "content": msg.get("content", ""),
                            "msg_id": msg.get("msg_id", ""),
                            "ts": msg.get("ts"),
                            "source": msg.get("source") or {},
                            "channel": "tmux",
                        }}])
                    return

                if event_type in ("thinking", "thinking_content"):
                    thinking = (payload.get("thinking", "")
                                or payload.get("text", "") or "")
                    if not thinking.strip():
                        return
                    msg = stamp_message({
                        "role": "assistant", "content": "",
                        "thinking": thinking,
                        "source": _source(), "channel": "tmux",
                    }, cid)
                    _writer().enqueue_message(
                        msg, agent_name=state.agent_name,
                        user_id=state.user_id,
                        sse_events=[{"type": "thinking_content", "data": {
                            "agent_name": state.agent_name,
                            "text": thinking,
                            "msg_id": msg.get("msg_id", ""),
                            "ts": msg.get("ts"),
                            "source": msg.get("source") or {},
                        }}])
                    return

                if event_type == "tool_use":
                    raw_name = payload.get("name", "")
                    raw_args = payload.get("arguments", {}) or {}
                    # An incomplete MCP call renders with empty args and is
                    # dropped downstream; do not persist a half call.
                    if not has_complete_mcp_tool_call(raw_name, raw_args):
                        return
                    name, args = unwrap_mcp_tool(raw_name, raw_args)
                    origin = payload.get("tool_origin", "") or ""
                    if not origin and is_mcp_tool_call_name(raw_name):
                        origin = "mcp"
                    tc_id = payload.get("id", "")
                    msg = stamp_message({
                        "role": "assistant", "content": "",
                        "source": _source(), "channel": "tmux",
                        "thinking": payload.get("thinking", "") or "",
                        "tool_calls": [{
                            "id": tc_id, "name": name, "arguments": args,
                            **({"tool_origin": origin} if origin else {}),
                        }],
                    }, cid)
                    tc_data = {
                        "tool": name, "arguments": args, "tc_id": tc_id,
                        "agent_name": state.agent_name,
                        "msg_id": msg.get("msg_id", ""),
                        "ts": msg.get("ts"),
                        "source": msg.get("source") or {},
                    }
                    if origin:
                        tc_data["tool_origin"] = origin
                    _writer().enqueue_message(
                        msg, agent_name=state.agent_name,
                        user_id=state.user_id,
                        sse_events=[{"type": "tool_call", "data": tc_data}])
                    return

                if event_type == "tool_result":
                    name = payload.get("tool", "") or ""
                    result = payload.get("result", "") or "(no output)"
                    tc_id = payload.get("tc_id", "")
                    origin = payload.get("tool_origin", "") or ""
                    # Same wrapping the agent loop and the Antigravity ingest
                    # apply, so a transcript row is identical whoever produced
                    # it.
                    from tasks.ai.agent_core import AgentCoreMixin
                    msg = stamp_message({
                        "role": "tool",
                        "content": AgentCoreMixin._wrap_tool_output(
                            name, result),
                        "tool_call_id": tc_id,
                        **({"tool_origin": origin} if origin else {}),
                    }, cid)
                    tr_data = {
                        "tool": name, "result": str(result)[:2000],
                        "tc_id": tc_id, "agent_name": state.agent_name,
                        "msg_id": msg.get("msg_id", ""),
                        "ts": msg.get("ts"),
                    }
                    if origin:
                        tr_data["tool_origin"] = origin
                    _writer().enqueue_message(
                        msg, agent_name=state.agent_name,
                        user_id=state.user_id,
                        sse_events=[{"type": "tool_result", "data": tr_data}])
            except Exception:
                logger.warning(
                    "CC interactive capture block persist failed (%s)",
                    event_type, exc_info=True)

        return _text_callback, _block_callback

    def _capture_dedup_sets(self, state: CCInteractiveSessionEvents):
        """The tool-id dedup sets a capture must share with PawFlow-driven turns.

        Owned by the pool's container, which is what the replayed context
        belongs to: when the container dies the context dies with it, so the
        sets resetting together is exactly right. The session-local pair is a
        fallback for a capture whose container the pool no longer knows —
        still better than per-coordinator sets, which forget between two
        chained captures of the same session.
        """
        try:
            if state.provider == "codex-interactive":
                from core.codex_interactive_pool import CodexInteractivePool
                pool = CodexInteractivePool.instance()
            else:
                from core.claude_code_interactive_pool import InteractiveClaudeCodePool
                pool = InteractiveClaudeCodePool.instance()
            container = pool.find_by_session_token(state.session_token)
        except Exception:
            logger.debug("CC interactive pool lookup failed for capture",
                         exc_info=True)
            container = None
        if container is not None:
            return (container.emitted_tool_use_ids,
                    container.emitted_tool_result_ids)
        logger.info(
            "CC interactive capture: no pooled container for session=%s — "
            "deduping tool ids on the event session only",
            state.session_token[:8])
        return state.emitted_tool_use_ids, state.emitted_tool_result_ids

    def _publish_capture_meta(self, state, response) -> None:
        """Fill in the meta line once the turn's real numbers are known.

        A captured turn persists its text as it is written, long before the
        coordinator returns -- so at write time the model and the token
        counts do not exist yet and the message goes out with a source that
        carries only the provider. The client renders that as a meta line
        with one item, or none at all.

        ``message_meta`` is the update channel: the client looks the message
        up by id and REPLACES its meta line, so sending the real values here
        completes what was written earlier instead of leaving a stub. A meta
        line that stays half-empty is worse than absent -- it looks like the
        turn cost nothing.

        Best-effort by construction: this closes a display gap, and must
        never be able to fail the capture that produced the answer.
        """
        try:
            msg_id = next(
                (m for m in reversed(state.captured_msg_ids or []) if m), "")
            if not msg_id:
                return
            tokens_in = int(getattr(response, "tokens_in", 0) or 0)
            tokens_out = int(getattr(response, "tokens_out", 0) or 0)
            model = str(getattr(response, "model", "") or "")
            if not (model or tokens_in or tokens_out):
                return
            from core.conversation_event_bus import ConversationEventBus
            ConversationEventBus.instance().publish_event(
                state.conversation_id, "message_meta", {
                    "msg_id": msg_id,
                    "agent_name": state.agent_name,
                    "provider": state.provider,
                    "model": model,
                    "tokens_in": tokens_in,
                    "tokens_out": tokens_out,
                })
        except Exception:
            logger.debug("CC interactive capture meta publish failed",
                         exc_info=True)

    def _run_manual_capture(self, session_token: str) -> None:
        state = self.session_state(session_token)
        try:
            if not state:
                return
            self._publish_capture_active(state, active=True)
            state.captured_msg_ids = []
            if state.provider == "codex-interactive":
                from core.llm_providers._codex_interactive_turn import (
                    _CodexInteractiveTurnCoordinator as _TurnCoordinator)
            else:
                from core.llm_providers.claude_code_interactive import (
                    _CCITurnCoordinator as _TurnCoordinator)
            # Same callbacks a PawFlow-driven turn passes: every observed
            # block is persisted and published as it arrives. Without them
            # the coordinator ran the whole turn silently and the webchat saw
            # nothing until it ended, while the tmux was visibly working.
            _text_cb, _block_cb = self._capture_stream_callbacks(state)
            # The session's own dedup sets, not fresh per-coordinator ones.
            # A live Claude Code session replays its ENTIRE context on every
            # API request, so a capture that starts with empty sets re-emits
            # every tool_use of every earlier turn: each one persisted as a
            # new transcript row and published as a tool_call event. The
            # webchat keys tool blocks by tc_id and absorbs it; a channel
            # bridge does not, so Telegram received the whole history of the
            # previous turn in one burst.
            _use_ids, _result_ids = self._capture_dedup_sets(state)
            coord = _TurnCoordinator(
                self, session_token, callback=_text_cb,
                block_callback=_block_cb, emitted_tool_use_ids=_use_ids,
                emitted_tool_result_ids=_result_ids,
                consumer_kind="capture")
            response = coord.run()
            self._publish_capture_meta(state, response)
            logger.info(
                "CC interactive captured turn streamed: conv=%s agent=%s chars=%d",
                state.conversation_id[:8], state.agent_name,
                len(response.content or ""))
        except CCIConsumerEvicted:
            # A real turn started and took the stream. Blocks already flushed
            # were complete when they were written, so they stay; only the
            # block still being accumulated is lost, and the new coordinator
            # owns the rest of the turn.
            logger.info(
                "CC interactive capture evicted by a live turn: session=%s",
                session_token[:8])
        except Exception:
            logger.warning("CC interactive manual response capture failed", exc_info=True)
        finally:
            state = self.session_state(session_token)
            if state:
                restart = False
                with self._sessions_lock:
                    if state.manual_capture_pending > 0:
                        state.manual_capture_pending -= 1
                        restart = True
                    else:
                        state.manual_capture_active = False
                if not restart:
                    # Release only when no follow-up capture is queued: a
                    # chained capture continues the same visible activity,
                    # and blinking the marker off between the two would show
                    # the agent idle in the middle of the work.
                    self._publish_capture_active(state, active=False)
                    self._drain_pending_after_capture(state)
                if restart:
                    thread = threading.Thread(
                        target=self._run_manual_capture,
                        args=(session_token,),
                        name=f"cci-manual-capture-{session_token[:8]}",
                        daemon=True,
                    )
                    thread.start()

    def _handle_ws(self, sock, path_params, meta):
        from services.filesystem_service import _attach_sync_sock_to_loop
        remote = meta.get("remote_addr", "?")
        try:
            loop = asyncio.new_event_loop()
            try:
                reader, writer = _attach_sync_sock_to_loop(sock, loop)
                loop.run_until_complete(self._serve(reader, writer, remote))
            finally:
                loop.close()
        except Exception:
            logger.error("CC interactive event WS handler failed (%s)", remote, exc_info=True)

    async def _serve(self, reader, writer, remote: str):
        from services.filesystem_service import _ws_recv_frame, _ws_send_frame

        session_token = ""  # nosec B105
        try:
            opcode, payload = await _ws_recv_frame(reader)
            if opcode != 0x01:
                return
            reg = json.loads(payload.decode("utf-8"))
            if reg.get("type") != "register":
                return
            token = reg.get("token", "")
            expected_token = self.config.get("token", "") or ""
            if not token or not hmac.compare_digest(
                    str(token), str(expected_token)):
                await _ws_send_frame(writer, json.dumps({
                    "type": "error", "message": "Token mismatch"}).encode())
                return
            session_token = reg.get("session_token", "")
            client_kind = reg.get("client_kind", "proxy")
            state = self.register_session(session_token)
            if client_kind == "proxy":
                state.container_id = reg.get("container_id", "")
                state.connected = True
            await _ws_send_frame(writer, json.dumps({"type": "registered"}).encode())
            logger.info(
                "CC interactive event client connected: session=%s kind=%s container=%s addr=%s",
                session_token[:8], client_kind, state.container_id, remote)

            while True:
                try:
                    opcode, payload = await _ws_recv_frame(reader)
                except asyncio.IncompleteReadError:
                    break
                if opcode == 0x08:
                    break
                if opcode == 0x09:
                    await _ws_send_frame(writer, payload, opcode=0x0A)
                    continue
                if opcode != 0x01:
                    continue
                msg = json.loads(payload.decode("utf-8"))
                if msg.get("type") == "ping":
                    await _ws_send_frame(writer, json.dumps({"type": "pong"}).encode())
                    continue
                if msg.get("type") != "event":
                    continue
                event = msg.get("event") or {}
                if not isinstance(event, dict):
                    continue
                try:
                    self.publish_event(session_token, event, block=True)
                except Exception as exc:
                    await _ws_send_frame(writer, json.dumps({
                        "type": "error", "message": str(exc)}).encode())
                    break
        finally:
            if session_token:
                state = self.session_state(session_token)
                if state and locals().get("client_kind", "proxy") == "proxy":
                    state.connected = False
            try:
                writer.close()
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)


def get_or_create_cc_interactive_event_service() -> tuple[str, str, CCInteractiveEventService]:
    """Return ``(wss_url, token, service)`` for the shared event service."""
    from core.service_registry import ServiceRegistry, SCOPE_GLOBAL
    from services.http_listener_service import HTTPListenerService

    instances = HTTPListenerService.all_instances()
    if not instances:
        raise RuntimeError("No HTTPListenerService running for CC interactive events")
    main_port = next(iter(instances.keys()))
    service_id = "_cc_interactive_events"
    reg = ServiceRegistry.get_instance()

    for sdef in reg.resolve_by_type(CCInteractiveEventService.TYPE):
        svc = reg.get_live_instance(sdef.scope, sdef.scope_id, sdef.service_id)
        cfg = getattr(sdef, "config", {}) or {}
        token = cfg.get("token", "")
        if svc and token:
            if not getattr(svc, "_initialized", False) or not getattr(svc, "_route_path", ""):
                svc.connect()
            url = f"wss://localhost:{main_port}/ws/cc-interactive/events/{sdef.service_id}"
            return url, token, svc
        try:
            reg.uninstall(sdef.scope, sdef.scope_id, sdef.service_id)
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

    token = uuid.uuid4().hex
    reg.install(SCOPE_GLOBAL, "", service_id=service_id,
                service_type=CCInteractiveEventService.TYPE,
                config={"token": token, "_service_id": service_id},
                description="Auto-created event ingest for claude-code-interactive")
    svc = reg.get_live_instance(SCOPE_GLOBAL, "", service_id)
    if not svc:
        raise RuntimeError("CC interactive event service did not start")
    url = f"wss://localhost:{main_port}/ws/cc-interactive/events/{service_id}"
    return url, token, svc


ServiceFactory.register(CCInteractiveEventService)
