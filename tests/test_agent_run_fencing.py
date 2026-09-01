"""Run fencing + idempotent ingress (plan B1-O, step P1-E).

Covers the run fence (monotonic per (conversation, agent), captured at
run start, bumped by force stop), the effect-boundary guard in
``_execute_tool_calls`` (zombie refused, successor passes), the
(run_handle, call_id) in-flight keying, the relay-side high-water
(monotonic, restart-safe via idempotent resync), targeted cancellation
and the durable idempotent-ingress boundary.
"""

import json
import threading
from pathlib import Path

import pytest

from core.llm_client import LLMToolCall
from services.tool_relay_service import ToolRelayService
from tasks.ai.agent_loop import AgentLoopTask
from tasks.ai.agent_tool_exec import AgentToolExecMixin


@pytest.fixture(autouse=True)
def _reset_state():
    def _clear():
        with ToolRelayService._inflight_lock:
            ToolRelayService._inflight.clear()
        with ToolRelayService._fence_highwater_lock:
            ToolRelayService._fence_highwater.clear()
        with AgentLoopTask._run_fence_lock:
            AgentLoopTask._run_fences.clear()
    _clear()
    yield
    _clear()


@pytest.fixture(autouse=True)
def _approve_all(monkeypatch):
    from core.tool_approval import ToolApprovalGate
    monkeypatch.setattr(ToolApprovalGate, "check",
                        lambda *args, **kwargs: "approved")


class _Agent(AgentToolExecMixin):
    pass


class _Registry:
    def __init__(self):
        self.executed = []

    def list_tools(self):
        return []

    def execute(self, name, arguments):
        self.executed.append(name)
        return "ok"


def _call(tc_id="tc-1"):
    return LLMToolCall(id=tc_id, name="screen",
                       arguments={"action": "screenshot"})


def _exec(registry, tc_id="tc-1", *, run_handle, fence_token,
          conv="c1", agent="helper"):
    return _Agent()._execute_tool_calls(
        [_call(tc_id)], registry, {}, 100,
        agent_name=agent, conversation_id=conv,
        run_handle=run_handle, fence_token=fence_token)


# ── fence tokens ─────────────────────────────────────────────────────

def test_fence_is_monotonic_and_bump_raises_the_relay_highwater():
    assert AgentLoopTask.run_fence_token("c1", "helper") == 0
    assert AgentLoopTask.bump_run_fence("c1", "helper") == 1
    assert AgentLoopTask.bump_run_fence("c1", "helper") == 2
    assert AgentLoopTask.run_fence_token("c1", "helper") == 2
    assert AgentLoopTask.run_fence_valid("c1", "helper", 2)
    assert not AgentLoopTask.run_fence_valid("c1", "helper", 1)
    # The bump raised the relay watermark in the same movement.
    assert not ToolRelayService.fence_highwater_allows("c1", "helper", 1)
    assert ToolRelayService.fence_highwater_allows("c1", "helper", 2)
    # Other (conversation, agent) pairs are untouched.
    assert AgentLoopTask.run_fence_token("c1", "other") == 0
    assert ToolRelayService.fence_highwater_allows("c1", "other", 0)


# ── effect boundary: zombie vs successor ─────────────────────────────

def test_zombie_is_refused_at_the_effect_boundary_successor_executes():
    zombie_token = AgentLoopTask.run_fence_token("c1", "helper")
    registry = _Registry()
    results = _exec(registry, "tc-1", run_handle="zombie",
                    fence_token=zombie_token)
    assert registry.executed == ["screen"]  # valid before the bump
    assert "ok" in results[0][1]

    successor_token = AgentLoopTask.bump_run_fence("c1", "helper")

    # The zombie prepared/authorized fine but NEVER crosses the boundary.
    registry = _Registry()
    results = _exec(registry, "tc-2", run_handle="zombie",
                    fence_token=zombie_token)
    assert registry.executed == []
    assert "superseded" in results[0][1]
    assert "NOT executed" in results[0][1]

    # The successor, which captured the bumped token, keeps passing.
    registry = _Registry()
    results = _exec(registry, "tc-3", run_handle="successor",
                    fence_token=successor_token)
    assert registry.executed == ["screen"]


def test_relay_highwater_refuses_a_stale_token_independently():
    token = AgentLoopTask.bump_run_fence("c1", "helper")
    assert AgentLoopTask.run_fence_valid("c1", "helper", token)
    # The relay watermark can be AHEAD of the runtime fence (e.g. raised
    # by another worker of the same commit domain): it refuses alone.
    ToolRelayService.raise_fence_highwater("c1", "helper", token + 4)
    registry = _Registry()
    results = _exec(registry, run_handle="r1", fence_token=token)
    assert registry.executed == []
    assert "superseded" in results[0][1]


# ── relay high-water: monotonic + restart-safe resync ────────────────

def test_highwater_is_monotonic_and_resync_survives_a_restart():
    AgentLoopTask.bump_run_fence("c1", "helper")
    AgentLoopTask.bump_run_fence("c1", "helper")  # runtime fence = 2
    # A lower token NEVER lowers the watermark.
    assert ToolRelayService.raise_fence_highwater("c1", "helper", 1) == 2
    # Simulated restart: the in-memory watermark is gone — the fence
    # would be reopened...
    with ToolRelayService._fence_highwater_lock:
        ToolRelayService._fence_highwater.clear()
    assert ToolRelayService.fence_highwater_allows("c1", "helper", 1)
    # ...until the (re)connection resync re-arms it from the runtime.
    assert ToolRelayService.resync_fence_highwaters() == 1
    assert not ToolRelayService.fence_highwater_allows("c1", "helper", 1)
    assert ToolRelayService.fence_highwater_allows("c1", "helper", 2)
    # Idempotent: a second resync raises nothing.
    assert ToolRelayService.resync_fence_highwaters() == 0


def test_relay_registration_resyncs_before_acknowledging():
    src = Path("services/tool_relay_service.py").read_text(encoding="utf-8")
    i_resync = src.index("resync_fence_highwaters()")
    i_ack = src.index("'type': 'registered'")
    assert i_resync < i_ack, (
        "the fence resync must happen BEFORE the relay registration ack")


# ── in-flight ledger keyed (run_handle, call_id) ─────────────────────

def test_inflight_is_keyed_by_run_handle_and_call_id():
    seen = {}

    class _Spy(_Registry):
        def execute(self, name, arguments):
            with ToolRelayService._inflight_lock:
                seen.update({k: dict(v) for k, v in
                             ToolRelayService._inflight.items()})
            return super().execute(name, arguments)

    _exec(_Spy(), "tc-1", run_handle="r1", fence_token=None)
    assert "r1:tc-1" in seen
    assert seen["r1:tc-1"]["run_handle"] == "r1"
    assert seen["r1:tc-1"]["cc_tc_id"] == "tc-1"  # UI kill still resolves
    # Popped under the SAME key once done.
    with ToolRelayService._inflight_lock:
        assert "r1:tc-1" not in ToolRelayService._inflight


def test_two_runs_with_the_same_provider_tc_id_never_collide():
    barrier = threading.Barrier(2, timeout=5)
    keys_during = []

    class _Sync(_Registry):
        def execute(self, name, arguments):
            barrier.wait()  # both runs in flight simultaneously
            with ToolRelayService._inflight_lock:
                keys_during.append(sorted(ToolRelayService._inflight))
            barrier.wait()
            return "ok"

    threads = [
        threading.Thread(target=_exec, args=(_Sync(), "tc-same"),
                         kwargs={"run_handle": handle, "fence_token": None})
        for handle in ("run-A", "run-B")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert sorted(keys_during[0]) == ["run-A:tc-same", "run-B:tc-same"]


# ── targeted cancellation & force stop ───────────────────────────────

def _seed_inflight(rid, run_handle, conv="c1", agent="helper"):
    cancel = threading.Event()
    with ToolRelayService._inflight_lock:
        ToolRelayService._inflight[rid] = {
            "conv": conv, "agent": agent, "cancel": cancel,
            "kill_hooks": [], "tool_name": "bash",
            "run_handle": run_handle,
        }
    return cancel


def test_cancel_agent_targets_one_run_handle():
    cancel_a = _seed_inflight("zombie:tc-1", "zombie")
    cancel_b = _seed_inflight("successor:tc-1", "successor")
    ToolRelayService.cancel_agent("c1", "helper", run_handle="zombie")
    assert cancel_a.is_set()
    assert not cancel_b.is_set()
    # Without a handle the cancel stays conversation/agent-wide.
    ToolRelayService.cancel_agent("c1", "helper")
    assert cancel_b.is_set()


def test_force_stop_of_a_superseded_handle_is_a_no_op(monkeypatch):
    AgentLoopTask.bump_run_fence("c1", "helper")  # fence = 1
    monkeypatch.setattr(
        AgentLoopTask, "current_run_handle",
        classmethod(lambda cls, conv, agent: "successor"))
    # Stopping the ZOMBIE by handle must not fence out the successor.
    AgentLoopTask.force_stop_agent("c1", "helper", run_handle="zombie")
    assert AgentLoopTask.run_fence_token("c1", "helper") == 1
    # Stopping the LIVE handle bumps as usual.
    AgentLoopTask.force_stop_agent("c1", "helper", run_handle="successor")
    assert AgentLoopTask.run_fence_token("c1", "helper") == 2


def test_force_stop_bumps_the_fence_before_any_cancel():
    src = Path("tasks/ai/agent_loop.py").read_text(encoding="utf-8")
    body = src[src.index("def force_stop_agent"):]
    i_bump = body.index("cls.bump_run_fence(")
    i_cancel = body.index("inst.cancel_agent(")
    assert i_bump < i_cancel, (
        "the fence bump must come FIRST: the stopped run may no longer "
        "cross the effect boundary while cancels propagate")


# ── idempotent ingress (accepted = durably persisted) ────────────────

def test_duplicate_turn_id_is_refused_by_the_durable_boundary(
        monkeypatch, tmp_path):
    import core.paths as paths
    from core.conversation_store import ConversationStore
    from core.conversation_writer import ConversationWriter
    from core._llm_seq import stamp_message

    monkeypatch.setattr(paths, "CONVERSATIONS_DIR", tmp_path / "conversations")
    ConversationStore.reset()
    try:
        writer = ConversationWriter.for_conversation("conv-ing")
        message = stamp_message(
            {"role": "user", "content": "hi", "msg_id": "turn-1",
             "source": {"type": "user", "target_agent": "helper"}},
            "conv-ing")
        assert writer.enqueue_message_if_absent(
            dict(message), agent_name="helper", user_id="u1") is True
        # The retry crosses the same durable boundary and is refused —
        # exactly what the streaming ingress turns into a duplicate ack.
        assert writer.enqueue_message_if_absent(
            dict(message), agent_name="helper", user_id="u1") is False
        rows = ConversationStore.instance().load("conv-ing", user_id="u1")
        assert [r.get("msg_id") for r in rows
                if r.get("msg_id") == "turn-1"] == ["turn-1"]
    finally:
        ConversationStore.reset()


def test_streaming_ingress_branches_on_the_programmatic_turn_id():
    src = Path("tasks/ai/agent_streaming.py").read_text(encoding="utf-8")
    # The programmatic flag is captured BEFORE stamping copies the web
    # msg_id into the same attribute — web chat stays asynchronous.
    i_flag = src.index("_programmatic_turn_id = flowfile.get_attribute")
    i_stamp = src.index("stamp_turn_identity(flowfile, _user_msg_id)")
    assert i_flag < i_stamp
    # A duplicate programmatic turn_id is acknowledged as the original
    # (accepted + duplicate) and NEVER starts a second worker.
    i_if_absent = src.index("enqueue_message_if_absent",
                            src.index("elif not _skip_pre_persist"))
    i_duplicate = src.index('"duplicate": True')
    assert i_if_absent < i_duplicate
    # Ack-after-persist: a failed durable write refuses the submission.
    assert "ingress_persistence_failed" in src


def test_runtime_api_surfaces_the_duplicate_acknowledgement():
    from core.agent_runtime_api import AgentSubmission
    submission = AgentSubmission(
        status="accepted", conversation_id="c", turn_id="t",
        duplicate=True)
    assert submission.duplicate is True
    assert AgentSubmission(
        status="accepted", conversation_id="c", turn_id="t").duplicate \
        is False


def test_run_handle_reaches_the_active_turn_marker_and_ctx():
    src = Path("tasks/ai/agent_streaming.py").read_text(encoding="utf-8")
    assert '"run_handle": _active_turn_owner_id' in src
    assert 'ctx["run_handle"] = _active_turn_owner_id' in src
    core_src = Path("tasks/ai/agent_core.py").read_text(encoding="utf-8")
    assert 'ctx.setdefault("run_handle"' in core_src
    assert 'ctx["run_fence_token"]' in core_src


# ── review v1 probes (P1-E v2) ───────────────────────────────────────

def _drain_runs():
    with AgentLoopTask._agent_runs_lock:
        AgentLoopTask._agent_runs.clear()


@pytest.fixture(autouse=True)
def _reset_runs():
    _drain_runs()
    yield
    _drain_runs()


def test_relay_admission_is_atomic_and_refuses_a_stale_token():
    """Probe 1: the fence check and the in-flight registration live in
    _handle_execute under the service locks — a stale token is refused
    BEFORE any tool machinery runs, no entry is left behind."""
    from services.tool_relay_service import (
        ToolRelayService, _set_current_run_identity)
    service = ToolRelayService({})
    ToolRelayService.raise_fence_highwater("c1", "helper", 5)
    _set_current_run_identity(("zombie", 3, "c1", "helper"))
    try:
        result = service._handle_execute(
            "rid-stale", "echo", {}, "u1", "c1", "helper")
    finally:
        _set_current_run_identity(None)
    assert "NOT executed" in str(result.get("data"))
    with ToolRelayService._inflight_lock:
        assert "rid-stale" not in ToolRelayService._inflight
    with ToolRelayService._cache_lock:
        assert "rid-stale" not in ToolRelayService._executing


def test_mid_execution_bump_reaches_the_running_tool(monkeypatch):
    """Probe 1 (in-process complement): the in-flight entry — with its
    run_handle and cancel event — is registered BEFORE the effect, so a
    bump landing DURING execute is deliverable to the running tool via
    the targeted cancel, instead of being unobservable."""
    from services.tool_relay_service import current_cancel_event
    observed = {}

    class _Bumpy(_Registry):
        def execute(self, name, arguments):
            # The bump lands while the tool is ALREADY executing.
            AgentLoopTask.bump_run_fence("c1", "helper")
            ToolRelayService.cancel_agent("c1", "helper",
                                          run_handle="r1")
            event = current_cancel_event()
            observed["cancelled"] = bool(event and event.is_set())
            return "ran"

    token = AgentLoopTask.run_fence_token("c1", "helper")
    _exec(_Bumpy(), "tc-1", run_handle="r1", fence_token=token)
    assert observed["cancelled"] is True


def test_stopping_a_zombie_while_its_successor_is_preparing(monkeypatch):
    """Probe 2: the successor exists ONLY in the run registry (marker
    phase, context not installed yet) — stopping the zombie by handle
    must neither bump the shared fence nor touch the successor."""
    AgentLoopTask.register_agent_run("c1", "helper", "zombie")
    AgentLoopTask.register_agent_run("c1", "helper", "successor")
    assert AgentLoopTask.current_run_handle("c1", "helper") == "successor"
    token_before = AgentLoopTask.run_fence_token("c1", "helper")
    cancel_zombie = _seed_inflight("req-z", "zombie")
    cancel_successor = _seed_inflight("req-s", "successor")
    AgentLoopTask.force_stop_agent("c1", "helper", run_handle="zombie")
    # The shared fence did NOT move — the successor keeps executing.
    assert AgentLoopTask.run_fence_token("c1", "helper") == token_before
    # Only the zombie's resources were cancelled.
    assert cancel_zombie.is_set()
    assert not cancel_successor.is_set()
    # The zombie's registry row is retired; the successor's remains.
    assert AgentLoopTask.live_run_handles("c1", "helper") == ["successor"]


def test_zombie_cleanup_never_evicts_the_successors_client():
    """Probe 2 (registry race): the client entry is owner-guarded — a
    cleanup or targeted stop by another handle leaves it alone."""
    inst = AgentLoopTask.__new__(AgentLoopTask)  # class-level state only
    AgentLoopTask._live_instance = inst
    sentinel = object()
    key = "c1:helper"
    try:
        with AgentLoopTask._active_contexts_lock:
            AgentLoopTask._active_claude_client[key] = sentinel
            AgentLoopTask._active_client_owners[key] = "successor"
        AgentLoopTask.register_agent_run("c1", "helper", "zombie")
        AgentLoopTask.register_agent_run("c1", "helper", "successor")
        AgentLoopTask.force_stop_agent("c1", "helper", run_handle="zombie")
        with AgentLoopTask._active_contexts_lock:
            assert AgentLoopTask._active_claude_client.get(key) is sentinel
    finally:
        AgentLoopTask._live_instance = None
        with AgentLoopTask._active_contexts_lock:
            AgentLoopTask._active_claude_client.pop(key, None)
            AgentLoopTask._active_client_owners.pop(key, None)


def test_late_retry_reads_the_retained_terminal():
    """Probe 3: register() never blanks an existing entry, wait() never
    pops the resolved terminal — a retry arriving AFTER done still gets
    the original result."""
    from core.agent_runtime_api import AgentResultWaiter
    from core.conversation_event_bus import ConversationEventBus
    ConversationEventBus.reset()
    AgentResultWaiter._instance = None
    waiter = AgentResultWaiter.instance()
    waiter.register("conv-r", "turn-1")
    ConversationEventBus.instance().publish_event(
        "conv-r", "done", {"turn_id": "turn-1", "response": "final"})
    first = waiter.wait("conv-r", "turn-1", timeout=1)
    assert first is not None and first.response == "final"
    # The LATE retry registers after done — it must not blank the entry.
    waiter.register("conv-r", "turn-1")
    late = waiter.wait("conv-r", "turn-1", timeout=1)
    assert late is not None and late.response == "final"


def test_duplicate_ack_replays_the_durable_terminal():
    src = Path("tasks/ai/agent_streaming.py").read_text(encoding="utf-8")
    # A late duplicate looks up the durable turn_final row and answers
    # with the replayed terminal instead of a dead wait_for_done.
    i_dup = src.index('"duplicate": True')
    i_return = src.index("return [flowfile]", i_dup)
    block = src[i_dup - 4000:i_return]
    assert 'turn_final' in block
    assert '"wait_for_done": not bool(_final)' in block


def test_targeted_cancel_reaches_the_internal_relay_entry():
    """Probe 4: the entry that OWNS the cancel event and kill hooks —
    keyed by request_id, created by _handle_execute — carries the run
    handle, so cancel_agent(run_handle=...) selects it."""
    fired = []
    cancel = _seed_inflight("internal-request-id", "zombie")
    with ToolRelayService._inflight_lock:
        ToolRelayService._inflight["internal-request-id"][
            "kill_hooks"].append(lambda: fired.append("killed"))
    ToolRelayService.cancel_agent("c1", "helper", run_handle="zombie")
    assert cancel.is_set()
    assert fired == ["killed"]


def test_handle_execute_stamps_the_run_identity_on_its_entry():
    src = Path("services/_tool_relay_execute.py").read_text(
        encoding="utf-8")
    i_identity = src.index("current_run_identity()")
    i_entry = src.index('"run_handle": _run_handle')
    assert i_identity < i_entry


def test_same_msg_id_with_a_different_payload_is_a_conflict(
        monkeypatch, tmp_path):
    """Probe 5: an idempotency key designates ONE request — a different
    payload under the same id is an explicit conflict, never a silent
    duplicate."""
    import core.paths as paths
    from core.conversation_store import ConversationStore
    from core.conversation_writer import ConversationWriter
    from core._conversation_store_append import MessageIdempotencyConflict
    from core._llm_seq import stamp_message

    monkeypatch.setattr(paths, "CONVERSATIONS_DIR",
                        tmp_path / "conversations")
    ConversationStore.reset()
    try:
        writer = ConversationWriter.for_conversation("conv-cf")
        original = stamp_message(
            {"role": "user", "content": "hi", "msg_id": "turn-1",
             "source": {"type": "user", "target_agent": "helper"}},
            "conv-cf")
        assert writer.enqueue_message_if_absent(
            dict(original), agent_name="helper", user_id="u1") is True
        divergent = stamp_message(
            {"role": "user", "content": "SOMETHING ELSE",
             "msg_id": "turn-1",
             "source": {"type": "user", "target_agent": "other"}},
            "conv-cf")
        with pytest.raises(MessageIdempotencyConflict):
            writer.enqueue_message_if_absent(
                dict(divergent), agent_name="helper", user_id="u1")
        # The identical retry stays a plain duplicate.
        assert writer.enqueue_message_if_absent(
            dict(original), agent_name="helper", user_id="u1") is False
    finally:
        ConversationStore.reset()


# ── relay-side fence protocol ────────────────────────────────────────

def _conn_session():
    from pawflow_relay._relay_msg_loop import ConnContext, ConnSession
    frames = []

    def _send(_sock, payload):
        frames.append(json.loads(payload.decode("utf-8")))

    context = ConnContext(
        sock=object(), send_lock=threading.Lock(),
        ws_frame_send=_send, ws_frame_recv=lambda _s: (0x01, b"{}"),
        socket_diag={}, last_activity=[0.0], pool=None,
        execute_command=lambda _m: {"ok": True},
        term_mgr=None, children=None, child_cfg=None,
        term_send=lambda _f: None, fuse_clients=(None, None, None),
        remote_mount_mgr=None,
        resolve_spawn_docker_env=lambda: None)
    return ConnSession(context), frames


def test_relay_worker_enforces_the_fence_at_dispatch():
    session, frames = _conn_session()
    session._handle_fence_update({
        "type": "fence_snapshot", "highwaters": {"c1:helper": 5}})
    assert frames[-1]["type"] == "fence_ack"
    # Stale command → refused with a result error, never executed.
    session._handle_command({
        "type": "command", "request_id": "rid-1", "action": "write",
        "fence_key": "c1:helper", "fence_token": 3})
    refusal = frames[-1]
    assert refusal["type"] == "result"
    assert "fence_stale" in str(refusal["data"].get("error"))
    # A raise is monotonic; the watermark also learns from tokens.
    session._handle_fence_update({
        "type": "fence_raise", "highwaters": {"c1:helper": 2}})
    assert session.fence_highwaters["c1:helper"] == 5
    assert session._fence_refuses({"fence_key": "c1:helper",
                                   "fence_token": 7}) is False
    assert session.fence_highwaters["c1:helper"] == 7
    assert session._fence_refuses({"fence_key": "c1:helper",
                                   "fence_token": 6}) is True


def test_server_requires_the_fence_ack_before_registration():
    src = Path("services/_relay_conn.py").read_text(encoding="utf-8")
    i_snapshot = src.index("'type': 'fence_snapshot'")
    i_ack = src.index("'fence_ack'")
    i_registered = src.index("'type': 'registered'")
    assert i_snapshot < i_ack < i_registered
    # Fail closed: no acknowledgement, no registration.
    assert "refusing the registration (fail closed)" in src


def test_fs_commands_carry_the_fence_token():
    src = Path("services/_filesystem_ops.py").read_text(encoding="utf-8")
    assert '"fence_key": f"{_fence_conv}:{_fence_agent}"' in src
    assert "**_fence_fields" in src
    # The send is scheduled UNDER the fence lock (check-and-schedule
    # atomic with the bump): the guard passes that lock to _send_to_pool.
    assert "fence_guard=_fence_guard" in src
    pool_body = src[src.index("def _send_to_pool"):src.index(
        "def _request(")]
    i_lock = pool_body.index("with _guard_lock:")
    i_check = pool_body.index("_guard_check()")
    i_schedule = pool_body.index("run_coroutine_threadsafe")
    assert i_lock < i_check < i_schedule


def test_send_and_bump_share_one_lock_so_the_check_is_atomic():
    """The deterministic interleaving: the send's guard and the fence bump
    contend for the SAME lock. While the guard holds it, a concurrent
    bump cannot raise the watermark — so a frame is never scheduled with
    a token that a bump has already invalidated."""
    from services.tool_relay_service import ToolRelayService
    # The guard uses this exact lock; the bump takes it to raise.
    guard_lock = ToolRelayService._fence_highwater_lock
    started = threading.Event()
    bumped = threading.Event()

    with guard_lock:
        # A run is being admitted: the guard holds the lock here.
        def _bump():
            started.set()
            AgentLoopTask.bump_run_fence("c1", "helper")
            bumped.set()

        thread = threading.Thread(target=_bump, daemon=True)
        thread.start()
        assert started.wait(timeout=2)
        # The bump is blocked on the lock: the relay watermark cannot move
        # while the guard decides — check-and-schedule is atomic. Read the
        # map DIRECTLY here (fence_highwater_allows would re-take the lock
        # we already hold).
        assert not bumped.wait(timeout=0.3)
        assert ToolRelayService._fence_highwater.get("c1:helper", 0) == 0
    # Lock released → the bump completes and the watermark advances.
    assert bumped.wait(timeout=2)
    thread.join(timeout=2)
    assert not ToolRelayService.fence_highwater_allows("c1", "helper", 0)


def test_send_to_pool_refuses_a_stale_guard_without_scheduling():
    """With a stale guard, _send_to_pool returns the refusal and never
    reaches the scheduling call (the fake loop records no frame)."""
    from services.filesystem_service import RelayService
    from services.tool_relay_service import ToolRelayService
    ToolRelayService.raise_fence_highwater("c1", "helper", 5)
    service = RelayService.__new__(RelayService)
    service._pending_lock = threading.Lock()
    service._pending = {"rid": (threading.Event(), {})}
    scheduled = []

    class _Loop:
        def call_soon_threadsafe(self, *a, **k):
            scheduled.append("frame")

    pool = [{"writer": object(), "loop": _Loop(),
             "send_lock": None, "reader": object()}]

    def _check():
        # Called under the lock — read the map directly (mirrors the
        # production guard), never re-enter fence_highwater_allows.
        if 3 < ToolRelayService._fence_highwater.get("c1:helper", 0):
            return Exception("run superseded (fence lost)")
        return None

    result = service._send_to_pool(
        pool, b"{}", request_id="rid",
        fence_guard=(ToolRelayService._fence_highwater_lock, _check))
    assert isinstance(result, Exception)
    assert "fence lost" in str(result)
    assert scheduled == []


def test_bump_notifies_connected_relays_mid_session():
    """The watermark reaches a relay that is ALREADY holding an older
    command frame, before the superseding run sends any request."""
    from services.filesystem_service import RelayService
    pushed = []

    class _FakeService:
        def push_fence_raise(self, highwaters):
            pushed.append(dict(highwaters))
            return True

    fake = _FakeService()
    with RelayService._live_relay_services_lock:
        RelayService._live_relay_services.add(fake)
    try:
        token = AgentLoopTask.bump_run_fence("c1", "helper")
    finally:
        with RelayService._live_relay_services_lock:
            RelayService._live_relay_services.discard(fake)
    assert pushed and pushed[-1] == {"c1:helper": token}


def test_relay_learns_the_watermark_from_the_broadcast():
    """The relay session applies a broadcast watermark and then refuses a
    command that carries the superseded token — no successor traffic
    needed."""
    session, frames = _conn_session()
    session._handle_fence_update({
        "type": "fence_raise", "highwaters": {"c1:helper": 9}})
    # No acknowledgement for a raise (only the handshake snapshot).
    assert not frames
    session._handle_command({
        "type": "command", "request_id": "rid-old", "action": "write",
        "fence_key": "c1:helper", "fence_token": 8})
    assert "fence_stale" in str(frames[-1]["data"].get("error"))
