"""Managed AG-UI runtime: durable pilot + journal-tailing SSE (P1-F/2).

Drives the real managed flow against a real A2AStore (only the agent
turn is faked). The invariants under test:

- acquire admits synchronously, persists the payload for recovery and
  opens the journal row;
- the PILOT owns the run: it adopts, journals every event, heartbeats,
  and terminalizes through finish (T-freeze injects the batch token);
- the SSE generator only tails the journal: disconnect = detach, the
  pilot still reaches its terminal; a replayed POST tails/replays and
  NEVER resubmits (only a ``reserved`` admission starts the single
  first submission);
- after orphan (lease lost) the pilot performs no further effect: no
  ledger record, no journal append, no finish.
"""

import json
import threading
import time

import pytest

import core._agui_managed_runtime as managed_runtime
from core.a2a_store import A2AStore
from core._agui_managed_runtime import (
    ManagedAcquireError, acquire_managed_turn, ensure_managed_pilot,
    run_managed_agent_stream,
)


def _events(frames):
    out = []
    for frame in frames:
        text = frame.decode("utf-8")
        if text.startswith(":"):
            continue
        assert text.startswith("data: ")
        out.append(json.loads(text[len("data: "):]))
    return out


def _wait_until(condition, timeout=5.0, message="condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {message}")


@pytest.fixture()
def managed(tmp_path, monkeypatch):
    store = A2AStore(tmp_path / "a2a.sqlite3")
    monkeypatch.setattr(A2AStore, "instance", classmethod(lambda cls: store))
    # The managed module binds these names at import: patch ITS bindings
    # so no real ConversationStore is touched by unit tests.
    monkeypatch.setattr(managed_runtime, "_ensure_isolated_conversation",
                        lambda publication, context: None)
    monkeypatch.setattr(managed_runtime, "_prepare_agui_doc",
                        lambda conversation_id, spec: (None, []))
    # Fast cadences so the tests observe heartbeats and tails quickly.
    monkeypatch.setattr(managed_runtime, "_HEARTBEAT_SECONDS", 0.05)
    monkeypatch.setattr(managed_runtime, "_TAIL_POLL_SECONDS", 0.01)
    publication = store.configure_publication(
        "owner", "conv-1", "helper", context_policy="isolated",
        managed_mode=True)
    _, key = store.create_key(publication["publication_id"], "client")
    return {"store": store, "publication": publication, "key": key}


def _run_input(run_id="run-1", parent_run_id=None, tools=None, text="hi"):
    body = {
        "threadId": "t-1", "runId": run_id, "state": None,
        "messages": [{"id": "1", "role": "user", "content": text}],
        "tools": tools if tools is not None else [],
        "context": [], "forwardedProps": None,
    }
    if parent_run_id is not None:
        body["parentRunId"] = parent_run_id
    return body


def _page_tool():
    return [{"name": "page_tool", "description": "act on the page",
             "catalogueId": "host:page_tool", "catalogueVersion": "cv-1"}]


def _patch_turn(monkeypatch, live_script, response="done", error="",
                block=False):
    """Fake ONLY AgentRuntimeAPI. ``live_script`` events are replayed
    through the live callback inside wait_for_done (the pilot's waiter
    thread). ``block=True`` parks wait_for_done on an Event the test
    releases — the live callback is then driven by the test itself."""
    from core import agent_runtime_api as runtime
    captured = {"submits": 0, "release": threading.Event()}

    def submit(request):
        captured["submits"] += 1
        captured["request"] = request
        return runtime.AgentSubmission(status="accepted",
                                       conversation_id=request.conversation_id,
                                       turn_id=request.msg_id,
                                       run_handle=request.run_handle)

    def wait(conversation_id, turn_id, timeout=None):
        if block:
            captured["release"].wait(timeout=10.0)
        for event_type, data in live_script:
            captured["request"].live_callback(conversation_id, event_type,
                                              data)
        return runtime.AgentFinalResult(conversation_id=conversation_id,
                                        turn_id=turn_id, response=response,
                                        error=error)

    monkeypatch.setattr(runtime.AgentRuntimeAPI, "submit_message",
                        staticmethod(submit))
    monkeypatch.setattr(runtime.AgentRuntimeAPI, "wait_for_done",
                        staticmethod(wait))
    return captured


# ── acquire before SSE ───────────────────────────────────────────────

def test_acquire_admits_and_persists_recovery_payload(managed):
    prepared = acquire_managed_turn(managed["publication"], managed["key"],
                                    _run_input())
    assert prepared["spec"]["run_id"] == "run-1"
    assert prepared["context"]["context_id"]
    assert prepared["context"]["internal_conversation_id"]
    context_id = prepared["context"]["context_id"]
    admission = managed["store"].get_agui_admission(context_id, "run-1")
    # The canonical body is persisted at admission for recovery and the
    # journal row exists before any pilot runs.
    assert admission["state"] == "reserved"
    assert json.loads(admission["payload_json"])["runId"] == "run-1"
    assert managed["store"].get_agui_run(context_id, "run-1") is not None


def test_acquire_rejects_a_disabled_publication(managed):
    managed["store"].configure_publication("owner", "conv-1", "helper",
                                           enabled=False)
    pub = managed["store"].get_publication(
        managed["publication"]["publication_id"])
    with pytest.raises(ManagedAcquireError) as exc:
        acquire_managed_turn(pub, managed["key"], _run_input())
    assert exc.value.status == 403


def test_acquire_stale_generation_is_409_thread_rotated(managed):
    # B1-T: every POST presents a generation; after a rotation the old
    # one (default 0) is refused BEFORE any SSE opens.
    store = managed["store"]
    store.ensure_agui_thread(managed["publication"],
                             managed["key"]["key_id"], "t-1")
    store.rotate_agui_thread(managed["publication"],
                             managed["key"]["key_id"], "t-1",
                             expected_generation=0,
                             delete_conversation=lambda cid: None)
    with pytest.raises(ManagedAcquireError) as exc:
        acquire_managed_turn(managed["publication"], managed["key"],
                             _run_input())
    assert (exc.value.status, exc.value.code) == (409, "thread_rotated")
    # Presenting the CURRENT generation is admitted.
    body = _run_input()
    body["threadGeneration"] = 1
    prepared = acquire_managed_turn(managed["publication"], managed["key"],
                                    body)
    assert prepared["spec"]["generation"] == 1


def test_rotation_cleanup_failure_propagates_fail_closed(managed,
                                                         monkeypatch):
    # A pending rotation cleanup whose deletion FAILS must surface —
    # never be swallowed (the durable marker keeps the thread refused).
    store = managed["store"]
    # A real generation-0 context must exist for the rotation to leave
    # a cleanup marker behind — and its run must be terminal (a rotation
    # never happens under an active admission).
    prepared0 = acquire_managed_turn(managed["publication"], managed["key"],
                                     _run_input())
    context0 = prepared0["context"]["context_id"]
    store.adopt_agui_run(context0, "run-1", "w1")
    store.finish_agui_turn(context0, "run-1", "w1", "success")
    with pytest.raises(RuntimeError, match="disk on fire"):
        store.rotate_agui_thread(
            managed["publication"], managed["key"]["key_id"], "t-1",
            expected_generation=0,
            delete_conversation=lambda cid: (_ for _ in ()).throw(
                RuntimeError("disk on fire")))
    # The marker is durable: the managed acquire now runs OUR cleanup
    # callback; a failing ConversationStore.delete propagates.
    from core.conversation_store import ConversationStore

    class _Boom:
        def delete(self, cid, user_id=""):
            raise RuntimeError("disk on fire")

    monkeypatch.setattr(ConversationStore, "instance",
                        classmethod(lambda cls: _Boom()))
    body = _run_input()
    body["threadGeneration"] = 1
    with pytest.raises(RuntimeError, match="disk on fire"):
        acquire_managed_turn(managed["publication"], managed["key"], body)

    class _Deleted:
        def __init__(self):
            self.calls = []

        def delete(self, cid, user_id=""):
            self.calls.append((cid, user_id))
            return True

    deleter = _Deleted()
    monkeypatch.setattr(ConversationStore, "instance",
                        classmethod(lambda cls: deleter))
    prepared = acquire_managed_turn(managed["publication"], managed["key"],
                                    body)
    assert prepared["spec"]["run_id"] == "run-1"
    # Owner-scoped deletion of the rotated conversation actually ran.
    assert deleter.calls and deleter.calls[0][1] == "owner"


def test_acquire_parent_mismatch_is_409(managed):
    # A run declaring a parentRunId that never completed a batch → 409.
    with pytest.raises(ManagedAcquireError) as exc:
        acquire_managed_turn(managed["publication"], managed["key"],
                             _run_input(run_id="run-2",
                                        parent_run_id="ghost"))
    assert exc.value.status == 409
    assert exc.value.code == "parent_mismatch"


# ── full managed stream: freeze + batchToken ─────────────────────────

def test_managed_success_freezes_batch_and_emits_batch_token(managed,
                                                             monkeypatch):
    _patch_turn(monkeypatch, [
        ("token", {"text": "on it", "msg_id": "m1"}),
        ("tool_call", {"tool": "page_tool", "tc_id": "tc1",
                       "arguments": {"x": 1}}),
    ])
    prepared = acquire_managed_turn(managed["publication"], managed["key"],
                                    _run_input(tools=_page_tool()))
    events = _events(list(run_managed_agent_stream(prepared)))
    types = [e["type"] for e in events]
    assert types[0] == "RUN_STARTED"
    # RUN_STARTED carries the run's attach/cancel credentials (B1-J).
    tokens = managed["store"].agui_run_tokens_for(
        prepared["context"]["context_id"], "run-1")
    assert events[0]["attachToken"] == tokens["attach_token"]
    assert events[0]["cancelToken"] == tokens["cancel_token"]
    assert "TOOL_CALL_START" in types
    finished = events[-1]
    assert finished["type"] == "RUN_FINISHED"
    assert finished["outcome"] == {"type": "managed_batch"}
    token = finished["batchToken"]
    assert token
    # The batch is really frozen and the token claims it.
    context_id = prepared["context"]["context_id"]
    batch = managed["store"].get_agui_batch(context_id, "run-1")
    assert batch["state"] == "frozen"
    claim = managed["store"].claim_agui_batch(token, "cl-1")
    assert claim["receipts"][0]["tool_call_id"] == "tc1"


def test_managed_success_without_frontend_calls_has_no_batch_token(managed,
                                                                   monkeypatch):
    _patch_turn(monkeypatch, [
        ("token", {"text": "just text", "msg_id": "m1"}),
    ])
    prepared = acquire_managed_turn(managed["publication"], managed["key"],
                                    _run_input())
    finished = _events(list(run_managed_agent_stream(prepared)))[-1]
    assert finished["type"] == "RUN_FINISHED"
    assert finished.get("batchToken") is None
    assert finished["outcome"] == {"type": "success"}


def test_managed_frontend_tool_without_catalogue_identity_fails_closed(
        managed, monkeypatch):
    _patch_turn(monkeypatch, [
        ("tool_call", {"tool": "page_tool", "tc_id": "tc1",
                       "arguments": {}}),
    ])
    # Declared WITHOUT catalogueId/catalogueVersion → the managed run
    # must refuse rather than freeze an unverifiable call.
    bare_tool = [{"name": "page_tool", "description": "act"}]
    prepared = acquire_managed_turn(managed["publication"], managed["key"],
                                    _run_input(tools=bare_tool))
    events = _events(list(run_managed_agent_stream(prepared)))
    assert events[-1]["type"] == "RUN_ERROR"
    assert events[-1]["code"] == "catalogue_incomplete"


def test_managed_agent_error_abandons_the_run(managed, monkeypatch):
    _patch_turn(monkeypatch, [], error="boom")
    prepared = acquire_managed_turn(managed["publication"], managed["key"],
                                    _run_input(tools=_page_tool()))
    events = _events(list(run_managed_agent_stream(prepared)))
    assert events[-1]["type"] == "RUN_ERROR"
    assert events[-1]["code"] == "agent_error"
    context_id = prepared["context"]["context_id"]
    run = managed["store"].get_agui_run(context_id, "run-1")
    assert run["state"] == "terminal"
    assert run["outcome"] == "error"


# ── follow-up run consumes the batch via parentRunId ─────────────────

def test_followup_run_consumes_the_completed_batch(managed, monkeypatch):
    _patch_turn(monkeypatch, [
        ("tool_call", {"tool": "page_tool", "tc_id": "tc1",
                       "arguments": {}}),
    ])
    prepared = acquire_managed_turn(managed["publication"], managed["key"],
                                    _run_input(tools=_page_tool()))
    finished = _events(list(run_managed_agent_stream(prepared)))[-1]
    token = finished["batchToken"]
    store = managed["store"]
    context_id = prepared["context"]["context_id"]
    claim = store.claim_agui_batch(token, "cl-1")
    receipt = claim["receipts"][0]["receipt"]
    store.begin_agui_call(receipt, catalogue_id="host:page_tool",
                          catalogue_version="cv-1")
    store.deposit_agui_call(receipt, "result", '{"ok":1}')
    assert store.get_agui_batch(context_id, "run-1")["state"] == "complete"
    # The follow-up run references the completed batch and is admitted.
    _patch_turn(monkeypatch, [("token", {"text": "next", "msg_id": "m2"})])
    follow = acquire_managed_turn(managed["publication"], managed["key"],
                                  _run_input(run_id="run-2",
                                             parent_run_id="run-1"))
    assert follow["spec"]["run_id"] == "run-2"
    assert store.get_agui_batch(context_id, "run-1")["state"] == "consumed"


# ── the pilot survives its subscriber ────────────────────────────────

def test_disconnect_mid_run_detaches_only_and_pilot_terminalizes(
        managed, monkeypatch):
    captured = _patch_turn(monkeypatch, [
        ("tool_call", {"tool": "page_tool", "tc_id": "tc1",
                       "arguments": {}}),
    ], block=True)
    prepared = acquire_managed_turn(managed["publication"], managed["key"],
                                    _run_input(tools=_page_tool()))
    store = managed["store"]
    context_id = prepared["context"]["context_id"]

    stream = run_managed_agent_stream(prepared)
    first = json.loads(next(stream).decode("utf-8")[len("data: "):])
    assert first["type"] == "RUN_STARTED"
    # Client disconnects mid-run: detach ONLY — no cancel, no abandon.
    stream.close()

    _wait_until(lambda: (store.get_agui_admission(context_id, "run-1")
                         or {}).get("state") == "running",
                message="pilot adoption")
    beat0 = store.get_agui_admission(context_id, "run-1")["lease_heartbeat_at"]
    _wait_until(lambda: store.get_agui_admission(
        context_id, "run-1")["lease_heartbeat_at"] > beat0,
        message="heartbeat without any subscriber")
    # A concurrent ensure on a running admission must NOT start a second
    # pilot (never resubmit outside `reserved`).
    ensure_managed_pilot(prepared)
    assert captured["submits"] == 1

    # The agent finishes while nobody is attached: the terminal is
    # journaled and the batch frozen all the same.
    captured["release"].set()
    _wait_until(lambda: (store.get_agui_run(context_id, "run-1")
                         or {}).get("state") == "terminal",
                message="journaled terminal without subscriber")
    run = store.get_agui_run(context_id, "run-1")
    assert run["outcome"] == "success"
    assert store.get_agui_batch(context_id, "run-1")["state"] == "frozen"

    # A replayed POST after the fact only tails the journal: the full
    # event history comes back and no new submission happens.
    replay = acquire_managed_turn(managed["publication"], managed["key"],
                                  _run_input(tools=_page_tool()))
    assert replay["replay"] is True
    events = _events(list(run_managed_agent_stream(replay)))
    types = [e["type"] for e in events]
    assert types[0] == "RUN_STARTED"
    assert events[-1]["type"] == "RUN_FINISHED"
    assert events[-1]["batchToken"]
    assert captured["submits"] == 1


def test_orphaned_run_gets_no_further_effects(managed, monkeypatch):
    captured = _patch_turn(monkeypatch, [], block=True)
    prepared = acquire_managed_turn(managed["publication"], managed["key"],
                                    _run_input(tools=_page_tool()))
    store = managed["store"]
    context_id = prepared["context"]["context_id"]
    ensure_managed_pilot(prepared)
    _wait_until(lambda: "request" in captured, message="pilot submission")

    # The sweep orphans the run (stale heartbeat from its point of view).
    assert store.orphan_expired_agui_turns(
        heartbeat_timeout_seconds=1.0, now=time.time() + 3600.0) == 1
    admission = store.get_agui_admission(context_id, "run-1")
    assert (admission["state"], admission["outcome"]) == ("orphaned",
                                                          "run_lost")
    terminal_seq = store.get_agui_run(context_id,
                                      "run-1")["committed_sequence"]

    # A frontend call arriving AFTER the orphan must produce no effect:
    # the pilot's lease gate refuses, nothing is recorded, nothing is
    # appended, and the pilot stops.
    captured["request"].live_callback(
        prepared["context"]["internal_conversation_id"], "tool_call",
        {"tool": "page_tool", "tc_id": "tc-late", "arguments": {}})
    _wait_until(lambda: not any(
        row["thread"].is_alive()
        for row in managed_runtime._PILOTS.values()),
        message="pilot stop after lease loss")
    captured["release"].set()

    assert store.pending_agui_calls(context_id, "run-1") == []
    run = store.get_agui_run(context_id, "run-1")
    assert run["state"] == "terminal"
    assert run["outcome"] == "run_lost"
    assert run["committed_sequence"] == terminal_seq  # no post-orphan append

    # The tail replays the journaled run_lost terminal for late clients.
    events = _events(list(run_managed_agent_stream(prepared)))
    assert events[-1]["type"] == "RUN_ERROR"
    assert events[-1]["code"] == "run_lost"
    assert captured["submits"] == 1


def test_cancelled_run_gets_no_further_effects(managed, monkeypatch):
    # Explicit cancel mid-run behaves like the orphan for the pilot: the
    # lease is gone, so no record, no append, no finish can follow.
    captured = _patch_turn(monkeypatch, [], block=True)
    prepared = acquire_managed_turn(managed["publication"], managed["key"],
                                    _run_input(tools=_page_tool()))
    store = managed["store"]
    context_id = prepared["context"]["context_id"]
    ensure_managed_pilot(prepared)
    _wait_until(lambda: "request" in captured, message="pilot submission")

    tokens = store.agui_run_tokens_for(context_id, "run-1")
    result = store.cancel_agui_run(tokens["cancel_token"])
    assert (result["outcome"], result["already"]) == ("cancelled", False)
    terminal_seq = store.get_agui_run(context_id,
                                      "run-1")["committed_sequence"]

    captured["request"].live_callback(
        prepared["context"]["internal_conversation_id"], "tool_call",
        {"tool": "page_tool", "tc_id": "tc-late", "arguments": {}})
    _wait_until(lambda: not any(
        row["thread"].is_alive()
        for row in managed_runtime._PILOTS.values()),
        message="pilot stop after cancel")
    captured["release"].set()

    assert store.pending_agui_calls(context_id, "run-1") == []
    run = store.get_agui_run(context_id, "run-1")
    assert (run["state"], run["outcome"]) == ("terminal", "cancelled")
    assert run["committed_sequence"] == terminal_seq  # no post-cancel append

    # Late subscribers replay the journaled cancelled terminal.
    events = _events(list(run_managed_agent_stream(prepared)))
    assert events[-1]["type"] == "RUN_ERROR"
    assert events[-1]["code"] == "cancelled"
    assert captured["submits"] == 1


def test_only_a_reserved_admission_starts_the_pilot(managed, monkeypatch):
    captured = _patch_turn(monkeypatch, [
        ("token", {"text": "ok", "msg_id": "m1"}),
    ])
    prepared = acquire_managed_turn(managed["publication"], managed["key"],
                                    _run_input())
    # Recovery shape: acquire happened (state reserved, payload durable)
    # but no pilot ran yet — a replayed POST starts THE single first
    # submission from the same idempotent body.
    replay = acquire_managed_turn(managed["publication"], managed["key"],
                                  _run_input())
    assert replay["replay"] is True
    finished = _events(list(run_managed_agent_stream(replay)))[-1]
    assert finished["type"] == "RUN_FINISHED"
    assert captured["submits"] == 1
    # Terminal now: ensure never resubmits, the stream only replays.
    ensure_managed_pilot(prepared)
    events = _events(list(run_managed_agent_stream(prepared)))
    assert events[-1]["type"] == "RUN_FINISHED"
    assert captured["submits"] == 1
