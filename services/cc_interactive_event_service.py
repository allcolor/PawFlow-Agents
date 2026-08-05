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


def _native_task_id(value: Any) -> str:
    """Extract Claude Code's task id from JSON or its stable text forms."""
    if isinstance(value, dict):
        for key in ("id", "taskId", "task_id"):
            if value.get(key) is not None:
                return str(value[key]).strip()
        for key in ("task", "result", "data", "content"):
            found = _native_task_id(value.get(key))
            if found:
                return found
        return ""
    if isinstance(value, list):
        for item in value:
            found = _native_task_id(item)
            if found:
                return found
        return ""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        parsed = None
    if parsed is not None and parsed != text:
        found = _native_task_id(parsed)
        if found:
            return found
    for pattern in (
        r"\btask\s+#([A-Za-z0-9_.:-]+)",
        r"\btask(?:\s+id)?\s*[:#]\s*([A-Za-z0-9_.:-]+)",
    ):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
    return ""

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
    # Ownership token for the `_active_turns` marker installed by a capture.
    # The same conversation/agent key is also used by the streaming worker, so
    # capture release must never remove a marker it did not create.
    active_turn_owner_id: str = ""
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
    # When the events currently waiting in the queue started waiting, or 0 when
    # nothing is waiting. This is the invariant's only fact: whatever crosses
    # the wire has to reach the webchat, and a queue nobody drains is the
    # proof that it has not. Every other liveness signal here is a guess about
    # whether a reader exists; this one observes whether one is reading.
    oldest_pending_at: float = 0.0
    # Whether the last thing this session's stream said was "the turn is over".
    # Set by the Stop hook, cleared by anything that starts a turn. The
    # undelivered rule below reads it: a turn that has ended leaves its
    # post-Stop stragglers in the queue -- nothing drains until the NEXT turn
    # claims -- and those waiting events are not a turn nobody is showing.
    turn_over: bool = False
    # The timestamp of the event that last decided `turn_over`. Boundary events
    # do not all travel the same way: the proxy holds one persistent event
    # socket, while every hook invocation opens its own short-lived connection.
    # A Stop can therefore land AFTER the next turn's request_start, and
    # applying it in arrival order closed a turn that had just begun.
    turn_boundary_at: float = 0.0
    # Non-destructive submission acknowledgements.  The turn coordinator owns
    # ``events``; transport code must never take an item from that queue merely
    # to learn whether tmux accepted an Enter.  These monotonic side-channel
    # counters are updated from the same published events and observed through
    # ``stream_condition`` without stealing anything from the coordinator.
    prompt_submit_seq: int = 0
    prompt_submit_receipts: list = field(default_factory=list)
    provider_request_seq: int = 0
    # Compatibility mirror for Claude Code's native TaskCreate/TaskUpdate.
    # PawFlow's TodoStore remains authoritative; these fields only correlate
    # an observed native tool_use with its later successful tool_result.
    pending_todo_calls: dict = field(default_factory=dict)
    mirrored_todo_call_ids: set = field(default_factory=set)


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
        self._sweeper: Optional[threading.Thread] = None
        self._sweeper_lock = threading.Lock()
        self._sweeper_stop = threading.Event()
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
        self._ensure_pending_sweeper()
        self._initialized = True
        logger.info("CC interactive event service registered at %s", route)

    def disconnect(self):
        self._sweeper_stop.set()
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

    def submission_marker(self, session_token: str) -> tuple[int, int]:
        """Return the current hook/MITM counters for a prompt about to submit."""
        state = self.session_state(session_token)
        if state is None:
            raise RuntimeError("Unknown CC interactive session")
        with state.stream_condition:
            return state.prompt_submit_seq, state.provider_request_seq

    @staticmethod
    def _hook_prompt_digests(prompt: str) -> set[str]:
        """Digests the hook may report after a TUI trims a terminal newline."""
        return {
            hashlib.sha256(candidate.encode("utf-8")).hexdigest()
            for candidate in (
                prompt or "", (prompt or "") + "\n", (prompt or "") + "\r\n",
                (prompt or "").rstrip("\r\n"),
            )
        }

    def wait_for_prompt_submission(
            self, session_token: str, prompt: str, *,
            after_submit: int, after_request: int,
            timeout: float) -> str:
        """Wait without consuming the event stream for proof of submission.

        Returns ``hook`` for the exact ``UserPromptSubmit``, ``request`` when
        the provider MITM has already seen the model request, ``fragment`` when
        Codex submitted only a piece of PawFlow's paste, ``other`` when a
        different prompt was submitted after the marker, or ``""`` on timeout.
        """
        state = self.session_state(session_token)
        if state is None:
            return ""
        digests = self._hook_prompt_digests(prompt)
        deadline = time.monotonic() + max(0.0, timeout)
        with state.stream_condition:
            while True:
                saw_fragment = False
                saw_other = False
                for seq, digest, kind in state.prompt_submit_receipts:
                    if seq <= after_submit:
                        continue
                    if kind == "exact" and digest in digests:
                        return "hook"
                    if kind == "fragment":
                        saw_fragment = True
                    else:
                        saw_other = True
                if state.provider_request_seq > after_request:
                    return "request"
                if saw_fragment:
                    return "fragment"
                if saw_other:
                    return "other"
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return ""
                state.stream_condition.wait(remaining)

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

        "Actively polling" is two facts, not one. A coordinator that polled
        recently is obviously alive; so is one that has claimed and not polled
        YET, because it is still inside its send -- the send blocks on TUI
        readiness, paste, settle, double Enter and submit verification before
        run() reads anything, and `_REQUEST_CLAIM_GRACE_SECONDS` is the
        ceiling that window was measured against. Only the first fact was
        checked, so the net could take the stream from a turn that had not
        started reading. Bumping the epoch is not a passive act: the
        coordinator then dies with CCIConsumerEvicted on its very first read.
        Observed on codex-interactive when a slow TUI ("prompt not detected
        ready") pushed the first poll past 50s -- the tmux kept working and
        the capture kept the rows flowing, so the webchat showed the whole
        turn while active-agents and the context gauge stayed dead for it.
        """
        state = self.register_session(session_token)
        with self._sessions_lock:
            if kind != "request":
                now = time.time()
                if now - state.last_wait_at < self._LISTENER_FRESH_SECONDS:
                    return 0
                # Claimed more recently than it last polled = has not read
                # since claiming = still sending. Once it polls, last_wait_at
                # overtakes the claim and the check above governs again; if it
                # never polls at all, the grace expires and the net gets the
                # stream, so no turn is left invisible for longer than that.
                if (state.last_request_claim_at > state.last_wait_at
                        and now - state.last_request_claim_at
                        < self._REQUEST_CLAIM_GRACE_SECONDS):
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
            # Stamp the freshness clock only for the consumer that still owns
            # the stream. Stamping first let an EVICTED coordinator refresh it
            # on the way to its own exception: `last_wait_at` then sat newer
            # than the incoming claim, which is exactly the shape
            # `claim_consumer` reads as "has polled since claiming". Three
            # seconds later the freshness check expired too, and the
            # orphan-turn net was granted a capture against a live turn whose
            # 120s claim grace should have refused it -- evicting the real
            # coordinator on its first read. The capture kept the rows
            # flowing, so the webchat filled in while active-agents and the
            # block went dead: the turn read "Completed" while the tmux worked.
            if epoch and epoch != state.consumer_epoch:
                raise CCIConsumerEvicted(
                    "CC interactive session taken over by a newer consumer")
            state.last_wait_at = time.time()
            while True:
                if epoch and epoch != state.consumer_epoch:
                    raise CCIConsumerEvicted(
                        "CC interactive session taken over by a newer consumer")
                if state.unreliable:
                    raise RuntimeError(
                        state.error or "CC interactive session is unreliable")
                if state.pushback:
                    return self._delivered(state, state.pushback.pop(0))
                try:
                    return self._delivered(state, state.events.get_nowait())
                except queue.Empty:
                    pass
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return {}
                else:
                    remaining = None
                state.stream_condition.wait(remaining)

    @staticmethod
    def _delivered(state: CCInteractiveSessionEvents, event: dict) -> dict:
        """Record that one event reached a consumer. Caller holds the condition.

        The clock restarts on what is still waiting rather than stopping: a
        consumer that takes one event and dies leaves the rest waiting, and the
        rest is what the invariant is about.
        """
        state.oldest_pending_at = (
            time.time() if (state.pushback or not state.events.empty()) else 0.0)
        return event

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
                    state.oldest_pending_at = 0.0
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
        self._record_submission_signal(state, event)
        self._log_event_summary(session_token, event)
        if event.get("type") == "wire":
            return
        self._mirror_native_todo_event(state, event)
        self._track_turn_boundary(state, event)
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
            if not state.oldest_pending_at:
                state.oldest_pending_at = time.time()
            state.stream_condition.notify_all()
        self._adopt_if_undelivered(state)

    def _mirror_native_todo_event(self, state: CCInteractiveSessionEvents,
                                  event: dict) -> None:
        """Mirror successful Claude native task mutations into TodoStore."""
        if state.provider != "claude-code-interactive":
            return
        event_type = event.get("type")
        tool_id = str(event.get("tool_use_id") or "")
        if not tool_id:
            return
        if event_type == "tool_use":
            name = str(event.get("name") or "")
            if name not in {"TaskCreate", "TaskUpdate"}:
                return
            arguments = event.get("arguments") or {}
            if not isinstance(arguments, dict):
                return
            ids = {tool_id}
            ids.update(str(item) for item in (event.get("alias_ids") or []) if item)
            record = {
                "call_id": tool_id,
                "ids": ids,
                "name": name,
                "arguments": dict(arguments),
            }
            with state.stream_condition:
                if ids & state.mirrored_todo_call_ids:
                    return
                for call_id in ids:
                    state.pending_todo_calls[call_id] = record
            return
        if event_type != "tool_result":
            return
        with state.stream_condition:
            record = state.pending_todo_calls.get(tool_id)
            if record is None:
                return
            ids = set(record["ids"])
            for call_id in ids:
                state.pending_todo_calls.pop(call_id, None)
            if ids & state.mirrored_todo_call_ids:
                return
        if event.get("is_error"):
            with state.stream_condition:
                state.mirrored_todo_call_ids.update(ids)
            return
        try:
            self._apply_native_todo_result(state, record, event.get("content"))
        except Exception:
            # Observation must never break the provider stream. Do not mark the
            # call mirrored: a replay can retry the store mutation.
            logger.error(
                "[cci-todolist] failed to mirror %s call=%s session=%s",
                record["name"], record["call_id"], state.session_token[:8],
                exc_info=True)
            return
        with state.stream_condition:
            state.mirrored_todo_call_ids.update(ids)

    @staticmethod
    def _apply_native_todo_result(state: CCInteractiveSessionEvents,
                                  record: dict, result: Any) -> None:
        from core.todo_store import TODO_STATUSES, TodoStore

        if not state.user_id or not state.conversation_id or not state.agent_name:
            raise ValueError("CCI todo mirror requires complete session identity")
        store = TodoStore.instance()
        args = record["arguments"]
        if record["name"] == "TaskCreate":
            external_id = _native_task_id(result)
            store.create(
                state.user_id, state.conversation_id, state.agent_name,
                subject=args.get("subject", ""),
                description=args.get("description", ""),
                active_form=args.get("activeForm", args.get("active_form", "")),
                owner=args.get("owner", ""),
                blocks=args.get("blocks"),
                blocked_by=args.get("blockedBy", args.get("blocked_by")),
                metadata=args.get("metadata"),
                external_id=external_id,
                source_call_id=record["call_id"],
            )
            if not external_id:
                logger.warning(
                    "[cci-todolist] TaskCreate call=%s returned no native task id",
                    record["call_id"])
            return

        task_id = str(
            args.get("taskId") or args.get("task_id") or args.get("id") or "")
        if not task_id:
            raise ValueError("TaskUpdate did not include a task id")
        changes = {}
        for native, target_field in (
            ("subject", "subject"), ("description", "description"),
            ("activeForm", "active_form"), ("active_form", "active_form"),
            ("owner", "owner"), ("metadata", "metadata"),
            ("blocks", "blocks"), ("blockedBy", "blocked_by"),
            ("blocked_by", "blocked_by"),
        ):
            if native in args:
                changes[target_field] = args[native]
        status = args.get("status")
        if status in TODO_STATUSES:
            changes["status"] = status
        elif status:
            raise ValueError(f"unsupported native task status: {status}")
        existing = store.get(
            state.user_id, state.conversation_id, state.agent_name, task_id)
        if existing is None:
            raise ValueError(f"native todo task not found: {task_id}")
        for native, target_field in (("addBlocks", "blocks"),
                                     ("addBlockedBy", "blocked_by")):
            if native in args:
                current = list(existing.get(target_field) or [])
                for item in args.get(native) or []:
                    value = str(item)
                    if value not in current:
                        current.append(value)
                changes[target_field] = current
        if changes:
            store.update(
                state.user_id, state.conversation_id, state.agent_name,
                task_id, **changes)

    def _record_submission_signal(self, state: CCInteractiveSessionEvents,
                                  event: dict) -> None:
        """Mirror submit proof into counters without removing the real event."""
        if self._is_provider_request(state, event):
            with state.stream_condition:
                state.provider_request_seq += 1
                state.stream_condition.notify_all()
            return
        if (event.get("type") != "hook"
                or event.get("hook_event_name") != "UserPromptSubmit"):
            return
        data = event.get("input") or {}
        if not isinstance(data, dict):
            return
        digest = str(data.get("prompt_sha256") or "")
        if not digest and data.get("pawflow_injected_prompt"):
            # Older/compact Codex hooks acknowledge that PawFlow's injected
            # prompt was submitted but omit the full prompt and its digest.
            # Bind that receipt to the newest still-tracked injection ticket;
            # otherwise a real UserPromptSubmit is misclassified as no proof
            # and the pool presses Enter repeatedly into a running turn.
            with self._sessions_lock:
                if state.injected_prompt_texts:
                    digest = state.injected_prompt_texts[-1].digest
                elif state.injected_prompts:
                    digest = max(
                        state.injected_prompts.items(), key=lambda item: item[1])[0]
        if not digest:
            return
        kind = "exact" if data.get("pawflow_injected_prompt") else ""
        if not kind:
            prompt = data.get("prompt", "")
            if isinstance(prompt, str):
                with self._sessions_lock:
                    if self._is_fragment_of_injection(state, prompt) is not None:
                        kind = "fragment"
        if not kind:
            # Still record the fact that Enter produced a UserPromptSubmit.
            # Receipt waiters must distinguish "nothing was submitted" from
            # "a stale/manual prompt was submitted instead of mine".
            kind = "other"
        with state.stream_condition:
            state.prompt_submit_seq += 1
            state.prompt_submit_receipts.append(
                (state.prompt_submit_seq, digest, kind))
            del state.prompt_submit_receipts[:-64]
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
        if not self._is_provider_request(state, event):
            return
        self._adopt_orphan_turn(state, "request in flight")

    @staticmethod
    def _is_provider_request(state: CCInteractiveSessionEvents,
                             event: dict) -> bool:
        """A request_start that is the CLI calling its model for a real turn."""
        if event.get("type") != "request_start" or event.get("ignore_reason"):
            return False
        path = event.get("path", "") or ""
        return (urlsplit(path).path.rstrip("/").endswith("/responses")
                if state.provider == "codex-interactive"
                else path.startswith("/v1/messages"))

    def _track_turn_boundary(self, state: CCInteractiveSessionEvents,
                             event: dict) -> None:
        """Remember whether this session is between turns.

        The undelivered rule declares a stream unread when its events have
        waited too long, and nothing drains a session's queue at the END of a
        turn -- ``drain_session`` runs when the NEXT turn claims. So every
        finished turn left its post-Stop stragglers waiting, and 25 seconds
        later the rule adopted a turn that was already over: a capture spawned,
        raised the active-agent marker, and waited for a Stop that had already
        happened. Observed as active-agents switching itself back on five to
        ten seconds after the answer landed, and staying on.

        A Stop says the turn is over. Anything that starts one -- a real
        provider request, a prompt submitted in the tmux -- arms the rule
        again, so a genuine orphan turn is still adopted.

        Decided on the events' own timestamps, not on their arrival order.
        The two kinds of boundary event do not share a route: the proxy emits
        request_start over one persistent event socket, while every hook run
        opens its own connection to deliver a single frame and closes it. A
        Stop delayed on its way in can therefore be published after the next
        turn's request_start, and taking it at face value marked the new turn
        as already over -- disarming the backstop for it, so the answer stayed
        in the queue and nothing ever picked it up. An event older than the
        one that set the current boundary describes a turn that is already
        history.
        """
        if self._is_provider_request(state, event):
            over = False
        elif event.get("type") == "hook":
            hook = event.get("hook_event_name", "")
            if hook == "Stop":
                over = True
            elif hook == "UserPromptSubmit":
                over = False
            else:
                return
        else:
            return
        # `publish_event` stamps anything that arrived without a timestamp, so
        # an event with no clock of its own is ordered by its arrival, as before.
        stamp = float(event.get("timestamp") or 0.0)
        # Boundary events arrive through independent WebSocket handlers. Keep
        # the comparison and both writes atomic, otherwise an older Stop can
        # pass the check, pause, then overwrite turn_over after a newer
        # request_start has advanced turn_boundary_at.
        with state.stream_condition:
            if stamp and stamp < state.turn_boundary_at:
                return
            state.turn_over = over
            state.turn_boundary_at = max(stamp, state.turn_boundary_at)

    def _adopt_orphan_turn(self, state: CCInteractiveSessionEvents,
                           reason: str, *, force: bool = False) -> None:
        if not force and self._request_listener_recent(state):
            return
        if not state.conversation_id or not state.agent_name:
            logger.debug("orphan CC turn ignored without session binding")
            return
        logger.warning(
            "CC interactive turn with no listening request (%s, session=%s); "
            "capturing orphan turn", reason, state.session_token[:8])
        self._start_manual_capture(state)

    # How long events may sit in a session's queue before the stream is
    # declared unread. A live coordinator polls every 0.25s, so any positive
    # value works against a reader that exists; this one clears the worst
    # legitimate gap instead -- a coordinator claims BEFORE its send, and the
    # send can spend a second settling the paste, three proving it landed, one
    # between the two Enters and six verifying the submit before run() polls
    # for the first time. Anything above that is a stream nobody is reading.
    _UNDELIVERED_ADOPT_SECONDS = 25.0
    # How often the sweeper re-asks. A turn adopted this way is late by at
    # most the threshold plus this.
    _PENDING_SWEEP_SECONDS = 5.0

    def _adopt_if_undelivered(self, state: CCInteractiveSessionEvents) -> None:
        """Enforce the rule: what crosses the wire is shown in the webchat.

        Everything else deciding whether a turn is being watched is a guess
        about the reader -- has a coordinator claimed recently, did one poll
        recently, was a prompt injected recently. Each guess has its own way of
        being wrong, and each time it is wrong the same thing happens: the
        proxy streams a real turn into a queue, nobody takes it out, and the
        webchat shows nothing while the tmux visibly works. The claim released
        on a failed send fixed one such way. This is the rule itself, and it
        does not ask about the reader at all: events waiting in the queue for
        longer than any legitimate handover means no one is reading them,
        whatever the timestamps claim, so the turn is adopted.

        Forced past `_request_listener_recent` on purpose -- those graces are
        exactly the guesses this backstops. It is still safe against a live
        coordinator: adoption goes through a `capture` claim, which is refused
        while a request consumer is actually polling. A coordinator that has
        not polled in 25 seconds while its events pile up is not one.

        Adoption stays the decision this makes; whether it may TAKE the stream
        from a coordinator that has claimed but not started reading is
        arbitrated by `claim_consumer`, which is where evicting a live turn
        would do the damage.
        """
        with state.stream_condition:
            pending_since = state.oldest_pending_at
            between_turns = state.turn_over
        if not pending_since:
            return
        # Nothing drains a session's queue when a turn ENDS, only when the next
        # one claims. What waits between the two is the finished turn's
        # post-Stop tail, already streamed and persisted -- not a turn nobody is
        # showing. Adopting it raised the active-agent marker minutes after the
        # answer landed, on a capture waiting for a Stop that had come and gone.
        if between_turns:
            return
        if time.time() - pending_since < self._UNDELIVERED_ADOPT_SECONDS:
            return
        # One guess this rule may NOT force past: a coordinator that has claimed
        # and not polled is inside its send, and `claim_consumer` will refuse
        # the capture on exactly that ground. Forcing anyway produced a
        # capture per sweep tick -- claimed, refused, streaming 0 chars, each
        # one raising and dropping the active-agent marker, and none of them
        # consuming an event, so the queue stayed stale and the next tick did
        # it again. Observed every 5s for the whole 45s a slow codex TUI took
        # to accept its prompt. The stream is owned; there is nothing to adopt.
        with state.stream_condition:
            claimed_at = state.last_request_claim_at
            polled_at = state.last_wait_at
        if (claimed_at > polled_at
                and time.time() - claimed_at < self._REQUEST_CLAIM_GRACE_SECONDS):
            return
        self._adopt_orphan_turn(state, "events undelivered", force=True)

    def _ensure_pending_sweeper(self) -> None:
        """Start the thread that re-asks after the last event has arrived.

        Checking on publish alone leaves the case the rule cares about most: a
        turn that streams in five seconds and then goes quiet has every one of
        its events waiting, and no further publish will ever come to notice.

        Tied to `connect()`/`disconnect()` rather than started on demand from
        `publish_event`: a thread that outlives what started it keeps its
        service alive with it, and a service built without being connected --
        every one in the test suite -- would leak one apiece and go on adopting
        turns from sessions its test had finished with.
        """
        with self._sweeper_lock:
            if self._sweeper is not None and self._sweeper.is_alive():
                return
            self._sweeper_stop.clear()
            self._sweeper = threading.Thread(
                target=self._sweep_pending, name="cci-pending-sweep",
                daemon=True)
            self._sweeper.start()

    def _sweep_pending(self) -> None:
        while not self._sweeper_stop.wait(self._PENDING_SWEEP_SECONDS):
            with self._sessions_lock:
                states = list(self._sessions.values())
            for state in states:
                try:
                    self._adopt_if_undelivered(state)
                except Exception:
                    logger.debug(
                        "undelivered-event sweep failed for %s",
                        state.session_token[:8], exc_info=True)

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
                           register: bool) -> bool:
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
            return False
        try:
            from tasks.ai.agent_loop import AgentLoopTask
            inst = AgentLoopTask._live_instance
            if not inst:
                return False
            key = (f"{state.conversation_id}:{state.agent_name}"
                   if state.agent_name else state.conversation_id)
            with inst._active_contexts_lock:
                if register:
                    owner_id = state.active_turn_owner_id or uuid.uuid4().hex
                    current = inst._active_turns.get(key)
                    current_owner = (
                        current.get("owner_id")
                        if isinstance(current, dict) else None)
                    if current is not None and current_owner != owner_id:
                        return False
                    state.active_turn_owner_id = owner_id
                    inst._active_turns[key] = {
                        "conversation_id": state.conversation_id,
                        "agent_name": state.agent_name,
                        "started_at": time.time(),
                        "status": "running",
                        "message_preview": "(tmux turn)",
                        "generation": 0,
                        "owner_id": owner_id,
                        "owner_type": "cci_capture",
                    }
                    return True
                owner_id = state.active_turn_owner_id
                current = inst._active_turns.get(key)
                current_owner = (
                    current.get("owner_id")
                    if isinstance(current, dict) else None)
                state.active_turn_owner_id = ""
                if not owner_id or current_owner != owner_id:
                    return False
                inst._active_turns.pop(key, None)
                return True
        except Exception:
            logger.debug("CC interactive active-turn marker failed", exc_info=True)
            return False

    def _publish_capture_active(self, state: CCInteractiveSessionEvents, *,
                                active: bool) -> None:
        if not self._active_turn_marker(state, register=active):
            return
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

        Returns ``(text_callback, block_callback, ensure_final_text)``.
        """
        cid = state.conversation_id
        live = {"msg_id": "", "ts": 0.0}
        persisted_texts = []

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
                    persisted_texts.append(text)
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

        def _ensure_final_text(text: str) -> None:
            """Persist the visible final answer if its terminal block vanished.

            Token callbacks are deliberately transient; the block callback is
            what turns their bubble into a durable transcript row.  A capture
            can still return a complete ``response.content`` after losing that
            final block at a Stop/request boundary.  Never release activity with
            a response that exists only in tmux and the transient token bubble.
            """
            final_text = text or ""
            if final_text.strip() and final_text not in persisted_texts:
                _block_callback("text", {"text": final_text})

        return _text_callback, _block_callback, _ensure_final_text

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
        announced = False
        try:
            if not state:
                return
            # Claim before announcing. The claim is refused while a request
            # consumer owns the stream, and that was only discovered inside
            # the coordinator -- after the active-agent marker had been raised.
            # A refused capture then blinked it straight back off, and the
            # sweeper spawned the next one 5s later: active-agents flickering
            # for as long as the real turn stayed inside its send.
            capture_epoch = self.claim_consumer(session_token, kind="capture")
            if not capture_epoch:
                logger.info(
                    "CC interactive capture yielded: session=%s is owned by a "
                    "live request consumer", session_token[:8])
                return
            self._publish_capture_active(state, active=True)
            announced = True
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
            _text_cb, _block_cb, _ensure_final_text = (
                self._capture_stream_callbacks(state))
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
                consumer_kind="capture", consumer_epoch=capture_epoch)
            response = coord.run()
            _ensure_final_text(response.content or "")
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
                if not restart and announced:
                    # Release only when no follow-up capture is queued: a
                    # chained capture continues the same visible activity,
                    # and blinking the marker off between the two would show
                    # the agent idle in the middle of the work. And only when
                    # this capture ever raised the marker -- one that yielded
                    # the stream never claimed to be running, so releasing
                    # would publish an end for a turn it never began.
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
