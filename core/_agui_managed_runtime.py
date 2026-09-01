"""Managed AG-UI runtime: drive a published agent as a managed
frontend-execution turn (plan ``docs/WEBMCP_INTEGRATION_PLAN.md`` §B1-X,
step P1-F/2).

The classic runtime (``core.agui_runtime.run_agent_stream``) is a plain
POST→SSE bridge. A MANAGED publication runs the closed protocol instead,
and the HTTP subscriber NEVER owns the run:

1. **Acquire synchronously, BEFORE the stream opens** (so a busy thread,
   a parent/idempotency mismatch or an incomplete prior batch is a real
   ``409`` and not an SSE ``RUN_ERROR``): ``acquire_managed_turn`` runs
   the ``acquire_agui_turn`` admission — which persists the canonical
   ``payload_json`` for recovery and opens the journal row — and hands
   back a prepared context.
2. **A durable background PILOT owns the run** (``ensure_managed_pilot``):
   it adopts the run (``reserved → running``, single-winner CAS), submits
   the agent turn, appends every translated AG-UI event to the durable
   journal, records frontend calls in the batch ledger with their
   catalogue identity, heartbeats the lease, and terminalizes through
   ``finish_agui_turn`` (T-freeze injects the ``batch_token`` into the
   journaled ``RUN_FINISHED`` in the same transaction). The pilot lives
   and dies with the RUN, not with any HTTP connection.
3. **The SSE generator only TAILS the journal**: the initial POST and any
   attach/replay read committed sequences and stream them. A client
   disconnect (``GeneratorExit``) detaches the subscriber and nothing
   else — the pilot keeps running and the heartbeat keeps the sweep away.

Retry is state-driven and never resubmits: only a ``reserved`` admission
may start THE single first submission (initial POST, or recovery of a
crash between acquire and pilot start, from the same idempotent body);
``accepted``/``running``/``terminal``/``orphaned`` admissions are
tail/replay only. ``adopt_agui_run`` clears ``payload_json`` and takes the
lease, so a second pilot can never win the same run.

The follow-up run consumes the completed batch via ``parentRunId``
(handled inside ``acquire_agui_turn``).
"""

from __future__ import annotations

import hashlib
import json
import logging
import queue
import threading
import time
import uuid
from typing import Any, Dict, Iterator, Set, Tuple

from core.agui_runtime import (
    _KEEPALIVE_SECONDS, _TurnTranslator, _assemble_prompt,
    _ensure_isolated_conversation, _prepare_agui_doc, agui_event,
    parse_run_input, sse_frame,
)

logger = logging.getLogger(__name__)

_DEFAULT_BATCH_DEADLINE_SECONDS = 900.0
# Lease heartbeat cadence — well under the sweep's default 120s timeout.
_HEARTBEAT_SECONDS = 20.0
# Journal tail poll cadence for SSE subscribers.
_TAIL_POLL_SECONDS = 0.25

# In-process single-start guard: one pilot thread per (context, run).
# Cross-process the adopt CAS (single lease winner) is the authority.
# Each row also carries the admitted turn's identities so an explicit
# cancel can force-stop EXACTLY that run_handle (B1-O), never a sibling.
_PILOTS: Dict[Tuple[str, str], Dict[str, Any]] = {}
_PILOTS_LOCK = threading.Lock()


class ManagedAcquireError(Exception):
    """The synchronous acquire refused the run BEFORE the stream opened.

    Carries the HTTP status and a stable error code the endpoint returns
    verbatim, so a managed client sees a real ``409``/``400`` instead of
    an SSE ``RUN_ERROR``.
    """

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _body_hash(canonical: str) -> str:
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _canonical_body(run_input: Dict[str, Any]) -> str:
    """Canonical JSON of a RunAgentInput — the idempotency identity AND
    the payload persisted at admission for recovery (a retried runId with
    the SAME body replays; a different body is a conflict)."""
    try:
        return json.dumps(run_input, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"))
    except (TypeError, ValueError):
        return str(run_input)


def acquire_managed_turn(publication: Dict[str, Any], key: Dict[str, Any],
                         run_input: Dict[str, Any]) -> Dict[str, Any]:
    """Parse + synchronously admit a managed run. Returns a prepared dict
    for ``ensure_managed_pilot`` / ``run_managed_agent_stream``, or raises
    :class:`ManagedAcquireError` with the HTTP status the endpoint should
    return before opening SSE.
    """
    from core.a2a_store import A2AStore
    from core._a2a_turn_acquire import (
        AguiParentMismatch, AguiTurnBusy, AguiIdempotencyConflict,
        AguiIdempotencyExpired,
    )
    from core._a2a_turn_batch import AguiBatchIncomplete
    from core._a2a_turn_machine import AguiThreadRotated

    if not publication.get("enabled"):
        raise ManagedAcquireError(403, "disabled",
                                   "This publication is disabled")
    try:
        spec = parse_run_input(run_input)
    except ValueError as exc:
        raise ManagedAcquireError(400, "invalid_input", str(exc))

    store = A2AStore.instance()
    canonical = _canonical_body(run_input)

    def _delete_conversation(conversation_id: str) -> None:
        # Owner-scoped deletion; idempotent when already gone (returns
        # False). A REAL failure must propagate: the durable pending-
        # cleanup marker then keeps the rotation refused (fail closed)
        # instead of silently leaking the old conversation.
        from core.conversation_store import ConversationStore
        ConversationStore.instance().delete(
            conversation_id, user_id=publication["owner_user_id"])

    try:
        admission = store.acquire_agui_turn(
            publication, key["key_id"], spec["thread_id"],
            spec["generation"],
            spec["run_id"], _body_hash(canonical),
            payload_json=canonical,
            parent_run_id=spec["parent_run_id"],
            delete_conversation=_delete_conversation)
    except AguiTurnBusy as exc:
        raise ManagedAcquireError(409, "thread_busy", str(exc))
    except AguiParentMismatch as exc:
        raise ManagedAcquireError(409, "parent_mismatch", str(exc))
    except AguiBatchIncomplete as exc:
        raise ManagedAcquireError(409, "batch_incomplete", str(exc))
    except AguiIdempotencyConflict as exc:
        raise ManagedAcquireError(409, "idempotency_conflict", str(exc))
    except AguiIdempotencyExpired as exc:
        raise ManagedAcquireError(409, "idempotency_expired", str(exc))
    except AguiThreadRotated as exc:
        raise ManagedAcquireError(409, "thread_rotated", str(exc))
    except ValueError as exc:
        raise ManagedAcquireError(400, "invalid_input", str(exc))

    # The admission row does not carry the internal conversation id; the
    # thread resolver does. Both are deterministic for this generation.
    context = store.resolve_agui_thread(publication, key["key_id"],
                                        spec["thread_id"],
                                        spec["generation"],
                                        delete_conversation=_delete_conversation)
    _ensure_isolated_conversation(publication, context)
    return {
        "publication": publication,
        "key": key,
        "spec": spec,
        "context": context,
        "replay": bool(admission.get("replay")),
    }


# ── pilot (owns the run; independent of any subscriber) ──────────────

def ensure_managed_pilot(prepared: Dict[str, Any]) -> None:
    """Start THE run's pilot iff its admission is still ``reserved``.

    State-driven retry contract: ``reserved`` → the single first
    submission (or its recovery after a crash between acquire and pilot
    start); ``accepted``/``dispatching`` (outbox path), ``running``
    (a live pilot here or elsewhere — a stale one is the sweep's job),
    ``terminal``/``orphaned`` (replay) never resubmit. Cross-process the
    adopt CAS keeps a single lease winner even if two pilots start.
    """
    from core.a2a_store import A2AStore

    context_id = prepared["context"]["context_id"]
    run_id = prepared["spec"]["run_id"]
    store = A2AStore.instance()
    admission = store.get_agui_admission(context_id, run_id)
    if admission is None or admission["state"] != "reserved":
        return
    key = (context_id, run_id)
    with _PILOTS_LOCK:
        existing = _PILOTS.get(key)
        if existing is not None and existing["thread"].is_alive():
            return
        thread = threading.Thread(
            target=_pilot_main, args=(prepared,), daemon=True,
            name=f"agui-pilot-{run_id[:16]}")
        _PILOTS[key] = {
            "thread": thread,
            "conversation_id": prepared["context"]["internal_conversation_id"],
            "agent_name": prepared["publication"]["agent_name"],
            "run_handle": "",
        }
        thread.start()


def _pilot_main(prepared: Dict[str, Any]) -> None:
    from core.a2a_store import A2AStore
    from core._a2a_turn_acquire import AguiFenceLost

    publication = prepared["publication"]
    spec = prepared["spec"]
    context = prepared["context"]
    thread_id, run_id = spec["thread_id"], spec["run_id"]
    context_id = context["context_id"]
    conversation_id = context["internal_conversation_id"]
    store = A2AStore.instance()
    worker = "agui-pilot:" + uuid.uuid4().hex[:12]

    try:
        try:
            store.adopt_agui_run(context_id, run_id, worker)
        except AguiFenceLost:
            return  # another pilot won the lease — never a second submission
        _run_pilot(store, publication, spec, context_id, conversation_id,
                   thread_id, run_id, worker)
    except Exception:  # pragma: no cover - defensive backstop
        logger.exception("managed AG-UI pilot crashed")
        _finish_quietly(store, context_id, run_id, worker, "error",
                        agui_event("RUN_ERROR", message="pilot crashed",
                                   code="pilot_crashed"))
    finally:
        with _PILOTS_LOCK:
            row = _PILOTS.get((context_id, run_id))
            if row is not None \
                    and row["thread"] is threading.current_thread():
                _PILOTS.pop((context_id, run_id), None)


def _run_pilot(store, publication: Dict[str, Any], spec: Dict[str, Any],
               context_id: str, conversation_id: str, thread_id: str,
               run_id: str, worker: str) -> None:
    from core.agent_runtime_api import AgentRequest, AgentRuntimeAPI

    # RUN_STARTED carries the run's attach/cancel credentials (B1-J):
    # minted with the admission, re-derived byte-identically here, and
    # replayed to any legitimate (Bearer-gated) subscriber of this run.
    tokens = store.agui_run_tokens_for(context_id, run_id)
    if not _journal(store, context_id, run_id, worker,
                    agui_event("RUN_STARTED", threadId=thread_id,
                               runId=run_id,
                               attachToken=tokens["attach_token"],
                               cancelToken=tokens["cancel_token"])):
        return

    # Catalogue identity per declared frontend tool (name → id/version).
    catalogue: Dict[str, Tuple[str, str]] = {
        t["name"]: (t.get("catalogue_id", ""), t.get("catalogue_version", ""))
        for t in spec["tools"]}
    frontend_names: Set[str] = set(catalogue)

    events: "queue.Queue[Tuple[str, Any, Any]]" = queue.Queue()

    def _live(_cid: str, event_type: str, data: Any) -> None:
        events.put(("evt", event_type, data))

    try:
        state, resume_texts = _prepare_agui_doc(conversation_id, spec)
        prompt = _assemble_prompt(spec, resume_texts, frontend_tools_live=True)
        if not prompt and not spec["attachments"]:
            raise ValueError("RunAgentInput carries no new user input")
        if state is not None:
            if not _journal(store, context_id, run_id, worker,
                            agui_event("STATE_SNAPSHOT", snapshot=state)):
                return
        # The msg_id IS the admission's deterministic turn id (P1-E
        # idempotent ingress): a recovery pilot re-submitting the same
        # run is acknowledged as a duplicate instead of starting a
        # second agent turn.
        turn_id = store.agui_turn_id(context_id, run_id)
        # The deterministic turn id is also this managed run's exact agent
        # handle. Register it before ingress, then carry it through the
        # reserved FlowFile attribute into the streaming worker. Cancellation
        # can therefore target this run even while submit_message is returning;
        # no newest-run polling or sibling ambiguity exists.
        run_handle = turn_id
        _set_run_handle(context_id, run_id, run_handle)
        source = {
            "type": "a2a", "name": "AG-UI client",
            "target_agent": publication["agent_name"],
            "visibility": "target_only",
            "publication_id": publication["publication_id"],
            "context_id": context_id, "agui_thread_id": thread_id,
            "agui_run_id": run_id, "agui_managed": True,
        }
        submission = AgentRuntimeAPI.submit_message(AgentRequest(
            user_id=publication["owner_user_id"],
            conversation_id=conversation_id,
            target_agent=publication["agent_name"], message=prompt,
            attachments=spec["attachments"], msg_id=turn_id, channel="agui",
            run_handle=run_handle,
            source_attributes={"message_source": json.dumps(source)},
            live_callback=_live))
        if submission.run_handle and submission.run_handle != run_handle:
            raise RuntimeError("agent ingress acknowledged a different run handle")
    except Exception as exc:
        logger.exception("managed AG-UI run submission failed")
        _finish_quietly(store, context_id, run_id, worker, "error",
                        agui_event("RUN_ERROR", message=str(exc),
                                   code="submission_failed"))
        return

    if submission.duplicate and not submission.wait_for_done:
        # The original turn already finished durably: its terminal was
        # replayed in the acknowledgement — no `done` will ever fire.
        from core.agent_runtime_api import AgentFinalResult
        _finish_managed_run(
            store, context_id, run_id, thread_id, worker,
            AgentFinalResult(conversation_id=conversation_id,
                             turn_id=turn_id,
                             response=submission.response), "")
        return

    def _wait_done() -> None:
        try:
            result = AgentRuntimeAPI.wait_for_done(conversation_id, turn_id)
        except Exception as exc:  # pragma: no cover - defensive
            events.put(("error", "", str(exc)))
            return
        events.put(("done", "", result))

    waiter = threading.Thread(target=_wait_done, daemon=True,
                              name=f"agui-managed-wait-{run_id[:16]}")
    waiter.start()

    translator = _TurnTranslator(frontend_tool_names=frontend_names)
    recorded: Set[str] = set()
    catalogue_error = ""
    last_beat = time.monotonic()
    while True:
        try:
            kind, event_type, data = events.get(
                timeout=max(0.05, _HEARTBEAT_SECONDS / 2.0))
        except queue.Empty:
            kind, event_type, data = "", "", None
        # Heartbeat keeps the lease alive with or without subscribers;
        # a failed beat means the lease is gone (orphaned/cancelled):
        # STOP — no journal append, no ledger write, no finish (the
        # reconciliation already journaled the terminal).
        if time.monotonic() - last_beat >= _HEARTBEAT_SECONDS:
            if not store.heartbeat_agui_turn(context_id, run_id, worker):
                logger.info("managed AG-UI pilot lost its lease "
                            "(run %s); stopping without effects", run_id)
                _force_stop_own_turn(context_id, run_id)
                return
            last_beat = time.monotonic()
        if not kind:
            continue
        if kind == "evt":
            if not isinstance(data, dict):
                continue
            if str(event_type) == "tool_call":
                # Effect boundary: verify the lease IN the store before
                # recording — after orphan/cancel this refuses, and the
                # pilot stops instead of writing.
                if not store.heartbeat_agui_turn(context_id, run_id, worker):
                    logger.info("managed AG-UI pilot lost its lease before "
                                "a call record (run %s); stopping", run_id)
                    _force_stop_own_turn(context_id, run_id)
                    return
                last_beat = time.monotonic()
                err = _record_frontend_call(store, context_id, run_id,
                                            data, catalogue, recorded)
                if err:
                    catalogue_error = err
            for event in translator.translate(str(event_type), data):
                if not _journal(store, context_id, run_id, worker, event):
                    return
            continue
        if kind == "error":
            for event in translator.close_open_blocks():
                if not _journal(store, context_id, run_id, worker, event):
                    return
            _finish_quietly(store, context_id, run_id, worker, "error",
                            agui_event("RUN_ERROR", message=str(data),
                                       code="run_failed"))
            return
        # kind == "done"
        for event in translator.close_open_blocks():
            if not _journal(store, context_id, run_id, worker, event):
                return
        _finish_managed_run(store, context_id, run_id, thread_id, worker,
                            data, catalogue_error)
        return


def _journal(store, context_id: str, run_id: str, worker: str,
             event: Dict[str, Any]) -> bool:
    """Append one non-terminal event to the durable journal. Returns
    False when the pilot must stop (journal already terminal — the run
    was reconciled under it — or quota-terminalized)."""
    from core._a2a_turn_journal import AguiRunQuotaExceeded, AguiRunTerminal
    payload = json.dumps(event, separators=(",", ":"), ensure_ascii=False)
    try:
        store.append_agui_event(context_id, run_id, payload)
        return True
    except AguiRunTerminal:
        logger.info("managed AG-UI journal is already terminal "
                    "(run %s); pilot stops", run_id)
        return False
    except AguiRunQuotaExceeded:
        # The quota terminal is journaled; reconcile the admission.
        _finish_quietly(store, context_id, run_id, worker, "error",
                        agui_event("RUN_ERROR", code="run_quota_exceeded",
                                   message="run quota exceeded"))
        return False
    except Exception:
        logger.exception("managed AG-UI journal append failed")
        return False


def _set_run_handle(context_id: str, run_id: str, handle: str) -> None:
    """Publish the caller-chosen exact handle before agent ingress."""
    with _PILOTS_LOCK:
        row = _PILOTS.get((context_id, run_id))
        if row is not None:
            row["run_handle"] = handle


def _force_stop_own_turn(context_id: str, run_id: str) -> None:
    """Lease lost (orphan or explicit cancel): stop the internal agent
    turn instead of letting it run to completion with every effect
    refused. Handle-targeted — a successor run is never touched."""
    with _PILOTS_LOCK:
        row = dict(_PILOTS.get((context_id, run_id)) or {})
    if not row.get("run_handle"):
        return
    try:
        from tasks.ai.agent_loop import AgentLoopTask
        AgentLoopTask.force_stop_agent(row["conversation_id"],
                                       row["agent_name"],
                                       run_handle=row["run_handle"])
    except Exception:
        logger.debug("managed AG-UI self force-stop failed", exc_info=True)


def force_stop_managed_run(context_id: str, run_id: str) -> bool:
    """Best-effort handle-targeted force stop of one admitted run's
    internal agent turn — used by the explicit cancel endpoint after
    the store terminalized the run. Returns True when a stop was
    dispatched (a pilot of this process knew the handle)."""
    with _PILOTS_LOCK:
        row = dict(_PILOTS.get((context_id, run_id)) or {})
    if not row.get("run_handle"):
        return False
    try:
        from tasks.ai.agent_loop import AgentLoopTask
        AgentLoopTask.force_stop_agent(row["conversation_id"],
                                       row["agent_name"],
                                       run_handle=row["run_handle"])
        return True
    except Exception:
        logger.debug("managed AG-UI cancel force-stop failed",
                     exc_info=True)
        return False


def _record_frontend_call(store, context_id: str, run_id: str,
                          data: Dict[str, Any],
                          catalogue: Dict[str, Tuple[str, str]],
                          recorded: Set[str]) -> str:
    """Record one emitted frontend call in the batch ledger with its
    catalogue identity. Returns an error string when a managed frontend
    tool was declared without a catalogue identity (fail closed)."""
    name = str(data.get("tool") or "")
    if name not in catalogue:
        return ""  # not a frontend tool — a server tool, ignored here
    tc_id = str(data.get("tc_id") or "")
    if not tc_id or tc_id in recorded:
        return ""
    catalogue_id, catalogue_version = catalogue[name]
    if not catalogue_id or not catalogue_version:
        return (f"managed frontend tool '{name}' was declared without a "
                "catalogue identity (catalogueId + catalogueVersion)")
    try:
        store.record_agui_call(context_id, run_id, tc_id, name,
                               catalogue_id=catalogue_id,
                               catalogue_version=catalogue_version)
        recorded.add(tc_id)
    except Exception:
        logger.exception("recording managed frontend call failed")
        return f"could not record frontend call '{name}'"
    return ""


def _finish_managed_run(store, context_id: str, run_id: str, thread_id: str,
                        worker: str, result: Any,
                        catalogue_error: str) -> None:
    """Terminalize the run through ``finish_agui_turn``: on success the
    T-freeze happens in the same transaction and the journaled
    ``RUN_FINISHED`` carries the ``batch_token``."""
    if result is None:
        _finish_quietly(store, context_id, run_id, worker, "error",
                        agui_event(
                            "RUN_ERROR", code="run_lost",
                            message="The agent turn ended without a final "
                                    "event"))
        return
    if getattr(result, "error", "") or catalogue_error:
        message = catalogue_error or getattr(result, "error", "")
        code = "catalogue_incomplete" if catalogue_error else "agent_error"
        _finish_quietly(store, context_id, run_id, worker, "error",
                        agui_event("RUN_ERROR", message=message, code=code))
        return
    # The pilot is the run's only writer: pending calls here == the batch
    # finish_agui_turn will freeze, so the outcome shape is decided now.
    pending = bool(store.pending_agui_calls(context_id, run_id))
    terminal = agui_event(
        "RUN_FINISHED", threadId=thread_id, runId=run_id,
        result=getattr(result, "response", "") or "",
        outcome={"type": "managed_batch"} if pending
        else {"type": "success"})
    _finish_quietly(store, context_id, run_id, worker, "success", terminal)


def _finish_quietly(store, context_id: str, run_id: str, worker: str,
                    outcome: str, terminal_event: Dict[str, Any]) -> None:
    """Best-effort terminalization. ``AguiFenceLost`` (lease gone —
    orphaned or cancelled under the pilot) means the reconciliation
    already owns the terminal: log and do nothing more."""
    from core._a2a_turn_acquire import AguiFenceLost
    if not worker:
        return
    try:
        store.finish_agui_turn(
            context_id, run_id, worker, outcome,
            terminal_event_json=json.dumps(terminal_event,
                                           separators=(",", ":"),
                                           ensure_ascii=False),
            batch_deadline_seconds=_DEFAULT_BATCH_DEADLINE_SECONDS)
    except AguiFenceLost:
        logger.info("managed AG-UI finish skipped: lease lost (run %s)",
                    run_id)
    except Exception:
        logger.exception("managed AG-UI finish failed (run %s)", run_id)


# ── SSE subscriber (tails the journal; never owns the run) ───────────

def _wire_event(event: Dict[str, Any]) -> Dict[str, Any]:
    """Journal event → AG-UI wire shape (camelCase ``batchToken``; the
    journal stores the store-injected ``batch_token``)."""
    if "batch_token" in event:
        event = dict(event)
        event["batchToken"] = event.pop("batch_token")
    return event


def tail_agui_journal(store, context_id: str, run_id: str, *,
                      subscriber_epoch: int,
                      after_seq: int = 0) -> Iterator[bytes]:
    """Yield SSE frames by tailing one run's durable journal: gapless
    replay of the committed prefix after ``after_seq``, then follow
    until the run is terminal. Pure subscriber — never admits, never
    starts a pilot; a ``GeneratorExit`` (client disconnect) detaches
    this subscriber and nothing else. Also the attach path (B1-J)."""
    from core._a2a_turn_journal import (
        AguiReplayExpired, AguiSubscriberTakenOver,
    )

    cursor = int(after_seq)
    subscriber_epoch = int(subscriber_epoch)
    last_activity = time.monotonic()
    while True:
        try:
            rows = store.read_agui_events(context_id, run_id,
                                          after_seq=cursor,
                                          subscriber_epoch=subscriber_epoch)
        except AguiSubscriberTakenOver:
            return
        except AguiReplayExpired as expired:
            if not store.is_agui_subscriber_current(
                    context_id, run_id, subscriber_epoch):
                return
            snapshot = _wire_event(dict(expired.snapshot))
            yield sse_frame(agui_event(
                "RUN_ERROR", code="replay_expired",
                message="the journal span was pruned; use the terminal "
                        "snapshot", snapshot=snapshot))
            return
        except Exception:
            logger.exception("managed AG-UI journal tail failed")
            if not store.is_agui_subscriber_current(
                    context_id, run_id, subscriber_epoch):
                return
            yield sse_frame(agui_event("RUN_ERROR", code="tail_failed",
                                       message="journal tail failed"))
            return
        for row in rows:
            if not store.is_agui_subscriber_current(
                    context_id, run_id, subscriber_epoch):
                return
            cursor = int(row["seq"])
            try:
                event = json.loads(row["event_json"])
            except ValueError:  # pragma: no cover - defensive
                continue
            yield sse_frame(_wire_event(event))
            last_activity = time.monotonic()
        if rows:
            continue  # drain the committed tail before any sleep
        run = store.get_agui_run(context_id, run_id)
        if run is not None and run["state"] != "active" \
                and cursor >= int(run["committed_sequence"]):
            return
        if time.monotonic() - last_activity >= _KEEPALIVE_SECONDS:
            if not store.is_agui_subscriber_current(
                    context_id, run_id, subscriber_epoch):
                return
            yield b": ping\n\n"
            last_activity = time.monotonic()
        time.sleep(_TAIL_POLL_SECONDS)


def run_managed_agent_stream(
        prepared: Dict[str, Any], *, subscriber_epoch: Any = None,
        after_seq: int = 0) -> Iterator[bytes]:
    """Return the AG-UI SSE tail of one MANAGED run.

    This is intentionally a normal function, not a generator: the pilot
    start and subscriber-epoch acquisition happen when the HTTP request is
    accepted, even if the client never consumes the first response byte.
    The endpoint may pass an epoch it acquired for the response header.

    The returned iterator tails the
    durable journal. Starts the pilot iff the admission is still
    ``reserved`` (single first submission); a replayed POST only replays.
    Client disconnect detaches this subscriber and nothing else."""
    from core.a2a_store import A2AStore

    context_id = prepared["context"]["context_id"]
    run_id = prepared["spec"]["run_id"]
    store = A2AStore.instance()
    ensure_managed_pilot(prepared)
    if subscriber_epoch is None:
        subscriber = store.acquire_agui_subscriber(
            context_id, run_id, after_seq=after_seq)
        subscriber_epoch = subscriber["subscriber_epoch"]
    return tail_agui_journal(
        store, context_id, run_id, after_seq=after_seq,
        subscriber_epoch=int(subscriber_epoch))


__all__ = ["acquire_managed_turn", "ensure_managed_pilot",
           "run_managed_agent_stream", "tail_agui_journal",
           "force_stop_managed_run", "ManagedAcquireError"]
