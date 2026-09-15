import hashlib
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from tasks.ai.agent_loop import AgentLoopTask
from tasks.ai.agent_poller import AgentPollerMixin


def test_checkpoint_cleanup_runs_in_background(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    calls = []

    def cleanup_old(days=30):
        calls.append(days)
        started.set()
        release.wait(timeout=5.0)
        finished.set()
        return 0

    monkeypatch.setattr(
        "core.checkpoint.CheckpointManager.cleanup_old",
        staticmethod(cleanup_old),
    )

    poller = AgentPollerMixin()
    t0 = time.monotonic()
    poller._maybe_cleanup_checkpoints_async()
    elapsed_ms = (time.monotonic() - t0) * 1000.0

    assert elapsed_ms < 50.0
    assert started.wait(timeout=1.0)
    assert calls == [30]

    poller._maybe_cleanup_checkpoints_async()
    assert calls == [30]

    release.set()
    assert finished.wait(timeout=1.0)
    for _ in range(100):
        if not getattr(poller, "_checkpoint_cleanup_running", False):
            break
        time.sleep(0.01)
    assert getattr(poller, "_checkpoint_cleanup_running", False) is False


# ── Per-agent delivery while another agent keeps the conversation active ──


@pytest.fixture
def poller_env(tmp_path, monkeypatch):
    from core.conversation_store import ConversationStore
    from core.poll_scheduler import PollScheduler
    monkeypatch.setattr("core.paths.POLL_SCHEDULE_FILE", tmp_path / "schedule.json")
    ConversationStore.reset()
    PollScheduler.reset()
    yield
    ConversationStore.reset()
    PollScheduler.reset()


def _active_task(cid, active_agents):
    """An AgentLoopTask whose conversation is held active by `active_agents`."""
    from core.conversation_store import ConversationStore
    ConversationStore.instance().save(
        cid, [{"role": "assistant", "content": "working"}],
        user_id="testuser")
    task = AgentLoopTask({
        "conversation_store": True,
        "system_prompt": "You are helpful.",
        "api_key": "test-key",
        "provider": "openai",
    })
    task._last_task_watchdog = time.time()
    task._last_thought_watchdog = time.time()
    task._active_lock = threading.RLock()
    task._active_conversations = (
        {cid: len(active_agents)} if active_agents else {})
    task._active_contexts_lock = threading.RLock()
    task._active_turns = {
        f"{cid}:{agent}": {"conversation_id": cid, "agent_name": agent}
        for agent in active_agents
    }
    task._active_contexts = {}
    task._active_thoughts = set()
    task._conv_gen_lock = threading.RLock()
    task._conv_generation = {}
    task._poller_wake = threading.Event()
    task._redirect_external_mcp_wake = MagicMock(return_value=False)

    def _fake_context(_cid, _messages, scheduled_reasons=None, **_kw):
        return {"active_agent_name":
                task._extract_agent_from_reasons(scheduled_reasons) or ""}

    task._build_poll_context = MagicMock(side_effect=_fake_context)
    return task


def _schedule(cid, key, reason):
    from core.poll_scheduler import PollScheduler
    PollScheduler.instance().schedule(
        cid, time.time() - 1, key=key, reason=reason, user_id="testuser")


def _poll(task, cid):
    """Run one poll pass; return the poll worker threads it would start."""
    real_thread = threading.Thread

    def thread(*args, **kwargs):
        if kwargs.get("name") == f"agent-poll-{cid[:8]}":
            return MagicMock()
        return real_thread(*args, **kwargs)

    with patch("tasks.ai.agent_poller.threading.Thread", side_effect=thread) as thread_cls:
        task._poll_once()
    return [
        call for call in thread_cls.call_args_list
        if call.kwargs.get("name") == f"agent-poll-{cid[:8]}"
    ]


def _remaining(cid):
    from core.poll_scheduler import PollScheduler
    return [entry for entry in PollScheduler.instance().list_all()
            if entry.get("conversation_id") == cid]


def _reason_digest(reason):
    return hashlib.sha1(reason.encode("utf-8", "ignore"),
                        usedforsecurity=False).hexdigest()[:8]


def test_deferred_wakes_batch_persistence_outside_activity_lock(poller_env, monkeypatch):
    from core.poll_scheduler import PollScheduler

    cid = "batch_deferred"
    task = _active_task(cid, ["assistant", "claude"])
    for agent in ("assistant", "claude"):
        _schedule(cid, f"{cid}::pending::{agent}", f"[pending] wake {agent}")
    scheduler = PollScheduler.instance()
    save = scheduler._save
    saves = []

    def checked_save():
        assert not task._active_lock._is_owned(), "disk I/O under activity lock"
        saves.append(True)
        save()

    monkeypatch.setattr(scheduler, "_save", checked_save)
    assert _poll(task, cid) == []
    assert len(_remaining(cid)) == 2
    assert len(saves) == 2  # due removal, then one batch for both deferrals


def test_cancel_between_activity_decision_and_persistence_wins(poller_env, monkeypatch):
    from core.poll_scheduler import PollScheduler

    cid = "cancel_deferred"
    task = _active_task(cid, ["assistant"])
    _schedule(cid, f"{cid}::pending::assistant", "[pending] wake assistant")
    scheduler = PollScheduler.instance()
    reschedule = scheduler.reschedule_due

    def cancel_first(retries):
        scheduler.cancel_for_conversation(cid)
        return reschedule(retries)

    monkeypatch.setattr(scheduler, "reschedule_due", cancel_first)
    assert _poll(task, cid) == []
    assert _remaining(cid) == []


@pytest.mark.parametrize("active_agents", [[], ["assistant"]])
@pytest.mark.parametrize("reason", [
    "[delegate_reply] queued result for claude",
    "[pending] wake claude",
    "[pending] 1 queued msg(s) after idle",
])
def test_queued_wake_for_idle_agent_starts_while_another_agent_is_active(
        poller_env, reason, active_agents):
    """Idle B receives its wake regardless of the conversation's activity."""
    cid = "starved_delivery"
    task = _active_task(cid, active_agents)
    _schedule(cid, f"{cid}::pending::claude", reason)

    threads = _poll(task, cid)

    assert len(threads) == 1
    ctx = threads[0].kwargs["args"][0]
    assert ctx["_gen_key"] == f"{cid}:claude"
    reasons = task._build_poll_context.call_args.kwargs["scheduled_reasons"]
    assert task._extract_agent_from_reasons(reasons) == "claude"
    assert reasons == [f"[scheduled:claude] {reason}"]
    task._redirect_external_mcp_wake.assert_called_once_with(cid, reasons)
    assert _remaining(cid) == []


def test_queued_wake_for_active_agent_stays_deferred_without_duplicate(
        poller_env):
    """A still-active target keeps its stable per-agent key and reason."""
    cid = "busy_target"
    task = _active_task(cid, ["assistant", "claude"])
    reason = "[delegate_reply] queued result for claude"
    _schedule(cid, f"{cid}::pending::claude", reason)

    assert _poll(task, cid) == []

    remaining = _remaining(cid)
    assert len(remaining) == 1
    assert remaining[0]["key"] == f"{cid}::pending::claude"
    assert remaining[0]["reason"] == reason
    assert remaining[0]["user_id"] == "testuser"
    assert 8 <= remaining[0]["recheck_at"] - time.time() <= 11


def test_active_check_matches_pending_key_case_insensitively(poller_env):
    """wake_agent lowercases the key; the active turn keeps canonical case."""
    cid = "case_target"
    task = _active_task(cid, ["assistant", "Wiki"])
    _schedule(cid, f"{cid}::pending::wiki", "[agent_msg] Wiki")

    assert _poll(task, cid) == []
    assert [e["key"] for e in _remaining(cid)] == [f"{cid}::pending::wiki"]


@pytest.mark.parametrize("reason", [
    "[delegate_reply] queued result for claude",
    "[pending] wake claude",
])
def test_rekeyed_pending_entry_recovers_target_from_reason(poller_env, reason):
    """A deferred retry re-keyed with a reason digest still names its agent."""
    cid = "rekeyed_target"
    task = _active_task(cid, ["assistant"])
    _schedule(cid, f"{cid}::pending::{_reason_digest(reason)}", reason)

    threads = _poll(task, cid)

    assert len(threads) == 1
    assert threads[0].kwargs["args"][0]["_gen_key"] == f"{cid}:claude"
    assert _remaining(cid) == []


def test_digest_suffix_is_not_mistaken_for_an_agent(poller_env):
    """An untargeted re-keyed retry keeps the generic deferral behavior."""
    cid = "digest_only"
    task = _active_task(cid, ["assistant"])
    reason = "[pending] 1 queued msg(s) after idle"
    _schedule(cid, f"{cid}::pending::{_reason_digest(reason)}", reason)

    assert _poll(task, cid) == []

    remaining = _remaining(cid)
    assert len(remaining) == 1
    assert remaining[0]["key"] == f"{cid}::pending::{_reason_digest(reason)}"
    assert remaining[0]["reason"] == reason


def test_untargeted_wake_still_defers_while_conversation_active(poller_env):
    cid = "generic_wake"
    task = _active_task(cid, ["assistant"])
    _schedule(cid, f"{cid}::external-wakeup", "check an external job")

    assert _poll(task, cid) == []

    remaining = _remaining(cid)
    assert len(remaining) == 1
    assert remaining[0]["key"] == (
        f"{cid}::pending::{_reason_digest('check an external job')}")
    assert remaining[0]["reason"] == "check an external job"


def test_continuation_for_active_target_is_delivered_once(poller_env):
    from core.pending_queue import PendingQueue

    cid = "continuation_ack"
    task = _active_task(cid, ["assistant"])
    queue = PendingQueue.for_agent(cid, "assistant")
    _schedule(cid, f"{cid}::continuation::deadbeef",
              "[scheduled:assistant] [continuation] finish the fix")

    assert _poll(task, cid) == []
    assert _remaining(cid) == []
    assert queue.peek_count() == 1
    assert _poll(task, cid) == []
    messages = queue.drain()
    assert len(messages) == 1
    message = messages[0]
    assert "[continuation] finish the fix" in message["content"]
    assert message["source"] == {
        "type": "scheduled_wakeup", "target_agent": "assistant"}
    assert message["msg_id"] and message["ts"]
    assert message["_already_persisted"] is True
    assert PendingQueue.for_agent(cid, "claude").peek_count() == 0
    from core.conversation_store import ConversationStore
    persisted = [m for m in ConversationStore.instance().load(cid)
                 if m.get("msg_id") == message["msg_id"]]
    assert len(persisted) == 1


def test_due_reminder_reaches_active_context_without_duplicate_persistence(poller_env):
    from core.pending_queue import PendingQueue
    from tasks.ai.agent_emitter import StreamEmitter

    cid = "wake_drain"
    task = _active_task(cid, ["assistant"])
    _schedule(cid, cid, "[scheduled:assistant] inspect the finished job")
    assert _poll(task, cid) == []

    emitter = StreamEmitter(cid, MagicMock(), {
        "active_agent_name": "assistant", "user_id": "testuser",
    }, task, f"{cid}:assistant", 0)
    messages = []
    append = MagicMock()
    emitter.drain_pending(messages, append, 1)
    assert len(messages) == 1
    assert "inspect the finished job" in messages[0].content
    assert "deadline has already passed" in messages[0].content
    append.assert_not_called()
    assert PendingQueue.for_agent(cid, "assistant").peek_count() == 0


def test_active_wake_survives_pending_queue_cache_reset(poller_env):
    from core.pending_queue import PendingQueue

    cid = "wake_durable"
    task = _active_task(cid, ["assistant"])
    _schedule(cid, f"{cid}::continuation::deadbeef",
              "[scheduled:assistant] [continuation] inspect durable output")
    assert _poll(task, cid) == []
    PendingQueue.drop_cache()
    messages = PendingQueue.for_agent(cid, "assistant").drain()
    assert len(messages) == 1
    assert "inspect durable output" in messages[0]["content"]


def test_active_wake_delivery_failure_keeps_original_schedule(poller_env, monkeypatch):
    cid = "wake_retry"
    task = _active_task(cid, ["assistant"])
    reason = "[scheduled:assistant] [continuation] retry delivery"
    key = f"{cid}::continuation::deadbeef"
    _schedule(cid, key, reason)
    monkeypatch.setattr(task, "_persist_scheduled_wakeup",
                        MagicMock(side_effect=OSError("unavailable")))
    assert _poll(task, cid) == []
    remaining = _remaining(cid)
    assert len(remaining) == 1
    assert remaining[0]["key"] == key
    assert remaining[0]["reason"] == reason


def test_active_external_wake_uses_runtime_router(poller_env):
    from core.pending_queue import PendingQueue

    cid = "external_active_wake"
    task = _active_task(cid, ["assistant"])
    task._redirect_external_mcp_wake.return_value = True
    reason = "[scheduled:assistant] [continuation] finish externally"
    _schedule(cid, f"{cid}::continuation::deadbeef", reason)
    assert _poll(task, cid) == []
    task._redirect_external_mcp_wake.assert_called_once_with(cid, [reason])
    assert PendingQueue.for_agent(cid, "assistant").peek_count() == 0


@pytest.mark.parametrize("change", ["cancel", "replace", "conversation", "next_poll"])
@pytest.mark.parametrize("external", [False, True])
def test_invalidated_due_claim_cannot_deliver_to_active_agent(
        poller_env, monkeypatch, change, external):
    from core.pending_queue import PendingQueue
    from core.poll_scheduler import PollScheduler

    cid = "stale_active_wake"
    task = _active_task(cid, ["assistant"])
    task._redirect_external_mcp_wake.return_value = external
    key = f"{cid}::continuation::old"
    _schedule(cid, key, "[scheduled:assistant] [continuation] obsolete plan")
    scheduler = PollScheduler.instance()
    deliver = task._queue_active_scheduled_wakeup
    persist = MagicMock()
    monkeypatch.setattr(task, "_persist_scheduled_wakeup", persist)

    def invalidate_then_deliver(*args):
        if change == "cancel":
            assert scheduler.cancel(key)
        elif change == "conversation":
            assert scheduler.cancel_for_conversation(cid) == 1
        elif change == "replace":
            scheduler.schedule(cid, time.time() + 60, key=key, reason="replacement")
        else:
            scheduler.get_due()
        return deliver(*args)

    monkeypatch.setattr(task, "_queue_active_scheduled_wakeup", invalidate_then_deliver)
    assert _poll(task, cid) == []
    persist.assert_not_called()
    task._redirect_external_mcp_wake.assert_not_called()
    assert PendingQueue.for_agent(cid, "assistant").peek_count() == 0
    if change == "replace":
        assert scheduler.get(key)["reason"] == "replacement"
    else:
        assert _remaining(cid) == []


@pytest.mark.parametrize("cutoff_key", ["last_force_stop_at", "last_force_stop_at:assistant"])
def test_force_stop_before_active_wake_delivery_cancels_it(poller_env, monkeypatch, cutoff_key):
    from core.conversation_store import ConversationStore
    from core.pending_queue import PendingQueue

    cid = "cancel_active_wake"
    task = _active_task(cid, ["assistant"])
    _schedule(cid, f"{cid}::continuation::deadbeef",
              "[scheduled:assistant] [continuation] cancelled plan")
    deliver = task._queue_active_scheduled_wakeup

    def stop_then_deliver(*args):
        ConversationStore.instance().set_extra(cid, cutoff_key, time.time())
        return deliver(*args)

    monkeypatch.setattr(task, "_queue_active_scheduled_wakeup", stop_then_deliver)
    assert _poll(task, cid) == []
    assert _remaining(cid) == []
    assert PendingQueue.for_agent(cid, "assistant").peek_count() == 0


def test_active_wake_arriving_after_worker_exit_starts_a_rescue(poller_env, monkeypatch):
    from core.pending_queue import PendingQueue

    cid = "wake_exit_race"
    task = _active_task(cid, ["assistant"])
    _schedule(cid, f"{cid}::continuation::deadbeef",
              "[scheduled:assistant] [continuation] finish after exit")
    deliver = task._queue_active_scheduled_wakeup

    def exit_then_deliver(*args):
        task._active_turns.clear()
        task._active_conversations.clear()
        return deliver(*args)

    monkeypatch.setattr(task, "_queue_active_scheduled_wakeup", exit_then_deliver)
    assert _poll(task, cid) == []
    assert PendingQueue.for_agent(cid, "assistant").peek_count() == 1
    assert [e["key"] for e in _remaining(cid)] == [f"{cid}::pending::assistant"]
    assert len(_poll(task, cid)) == 1


def test_force_stop_during_wake_persistence_is_not_an_error(poller_env, monkeypatch, caplog):
    from core.conversation_store import ConversationStore
    from core.pending_queue import PendingQueue
    from core.poll_scheduler import PollScheduler

    cid = "wake_persist_stop"
    task = _active_task(cid, ["assistant"])
    _schedule(cid, f"{cid}::continuation::deadbeef",
              "[scheduled:assistant] [continuation] cancelled during persistence")
    persist = task._persist_scheduled_wakeup

    def persist_then_stop(*args, **kwargs):
        message = persist(*args, **kwargs)
        ConversationStore.instance().set_extra(cid, "last_force_stop_at", time.time())
        PollScheduler.instance().cancel_for_conversation(cid)
        return message

    monkeypatch.setattr(task, "_persist_scheduled_wakeup", persist_then_stop)
    assert _poll(task, cid) == []
    assert _remaining(cid) == []
    assert PendingQueue.for_agent(cid, "assistant").peek_count() == 0
    assert not [record for record in caplog.records if record.levelname == "ERROR"]


def test_active_wake_arriving_after_final_drain_is_rescued_on_idle(poller_env):
    from core.pending_queue import PendingQueue

    cid = "wake_final_drain_race"
    task = _active_task(cid, ["assistant"])

    def finish_without_another_drain(*args):
        _schedule(cid, f"{cid}::continuation::deadbeef",
                  "[scheduled:assistant] [continuation] finish after drain")
        assert _poll(task, cid) == []

    task._streaming_agent_loop_inner = finish_without_another_drain
    task._is_current_generation = lambda *args: True
    task._streaming_agent_loop({
        "active_agent_name": "assistant", "user_id": "testuser",
        "_gen_key": f"{cid}:assistant", "_generation": 0,
    }, cid, MagicMock())
    assert PendingQueue.for_agent(cid, "assistant").peek_count() == 1
    assert [e["key"] for e in _remaining(cid)] == [f"{cid}::pending::assistant"]


def test_continuation_during_final_response_runs_again_with_its_plan(poller_env, monkeypatch):
    import json

    from core import FlowFile
    from core.llm_client import LLMClient, LLMResponse
    from core.pending_queue import PendingQueue

    cid = "wake_during_response"
    task = _active_task(cid, ["assistant"])
    from core.conversation_store import ConversationStore
    ConversationStore.instance().set_extra(cid, "conv_agents", {
        "assistant": {"definition": "assistant", "params": {"name": "assistant"}},
    })
    ConversationStore.instance().save_agent_context(cid, "assistant", [])
    client = LLMClient(provider="openai", config={"api_key": "test-key"})
    monkeypatch.setattr(task, "_resolve_agent_client",
                        lambda *args, **kwargs: (client, "test-llm", None))
    task._maybe_generate_title = lambda *args: None
    task._maybe_poke_stalled_plan = lambda *args: None
    task._is_current_generation = lambda *args: True
    ff = FlowFile(json.dumps({
        "conversation_id": cid, "target_agent": "assistant",
        "message": "Work until the check is due",
    }).encode())
    ff.set_attribute("http.auth.principal", "testuser")
    ctx = task._prepare_agent_context(ff, preloaded_messages=[])
    ctx.update(active_agent_name="assistant", _gen_key=f"{cid}:assistant", _generation=0)
    seen = []

    def complete(_client, messages, *args, **kwargs):
        seen.append([m.content for m in messages if m.role == "user"])
        if len(seen) == 1:
            _schedule(cid, f"{cid}::continuation::deadbeef",
                      "[scheduled:assistant] [continuation] inspect REDkit output")
            assert _poll(task, cid) == []
            return LLMResponse(content="Continuation scheduled; stopping now.",
                               finish_reason="stop", model="test-model")
        assert len(seen) == 2
        assert any("inspect REDkit output" in str(text) for text in seen[-1])
        return LLMResponse(content="The due check is handled.",
                           finish_reason="stop", model="test-model")

    monkeypatch.setattr(LLMClient, "complete_stream", complete)
    monkeypatch.setattr(LLMClient, "complete", complete)
    with patch("threading.Timer"):
        task._streaming_agent_loop_inner(ctx, cid, MagicMock())
    assert len(seen) == 2
    assert PendingQueue.for_agent(cid, "assistant").peek_count() == 0
    assert not ctx.get("_retrigger_after_done")


@pytest.mark.parametrize("active_agents", [[], ["assistant"]])
def test_multiple_idle_targets_are_not_collapsed_into_one_wake(poller_env, active_agents):
    """Two idle targets due together: one starts, the other is held, not lost."""
    cid = "two_targets"
    task = _active_task(cid, active_agents)
    _schedule(cid, f"{cid}::pending::claude",
              "[delegate_reply] queued result for claude")
    _schedule(cid, f"{cid}::pending::gemini",
              "[delegate_reply] queued result for gemini")

    threads = _poll(task, cid)

    assert len(threads) == 1
    started = threads[0].kwargs["args"][0]["_gen_key"].split(":", 1)[1]
    held = "gemini" if started == "claude" else "claude"
    remaining = _remaining(cid)
    assert len(remaining) == 1
    assert remaining[0]["key"] == f"{cid}::pending::{held}"
    assert remaining[0]["reason"] == f"[delegate_reply] queued result for {held}"
    assert remaining[0]["recheck_at"] <= time.time()
    assert task._poller_wake.is_set()

    # The held target starts on the very next pass.
    task._poller_wake.clear()
    threads = _poll(task, cid)
    assert len(threads) == 1
    assert threads[0].kwargs["args"][0]["_gen_key"] == f"{cid}:{held}"
    assert _remaining(cid) == []


def test_scheduled_entry_target_extraction(poller_env):
    poller = AgentPollerMixin()
    cid = "target_extraction"

    def target(key, reason):
        return poller._scheduled_entry_target(
            cid, {"key": key, "reason": reason})

    assert target(f"{cid}::pending::claude", "[bg-tool] CC result") == "claude"
    assert target(f"{cid}::pending::0badf00d",
                  "[delegate_reply] queued result for claude") == "claude"
    assert target(f"{cid}::pending::0badf00d", "[pending] wake claude") == "claude"
    assert target(f"{cid}::pending::0badf00d",
                  "[pending] 1 queued msg(s) after idle") == ""
    assert target(f"{cid}::pending::", "[pending] wake default") == ""
    assert target(f"{cid}::pending::0badf00d",
                  "[delegate_reply] queued result for default") == ""
    assert target(f"{cid}::continuation::deadbeef",
                  "[scheduled:agent.v2] [continuation] finish") == "agent.v2"
    assert target(f"{cid}::external-wakeup", "check an external job") == ""


@pytest.mark.parametrize("change", ["cancel", "replace", "conversation", "next_poll"])
@pytest.mark.parametrize("keep_other", [False, True])
def test_idle_reminder_invalidated_during_history_load_is_not_delivered(
        poller_env, monkeypatch, change, keep_other):
    from core.conversation_store import ConversationStore
    from core.poll_scheduler import PollScheduler

    cid = "idle_cancel_during_load"
    task = _active_task(cid, [])
    key = f"{cid}::continuation::old"
    _schedule(cid, key, "[scheduled:assistant] obsolete plan")
    if keep_other:
        _schedule(cid, f"{cid}::continuation::keep", "[scheduled:assistant] keep plan")
    scheduler = PollScheduler.instance()
    store = ConversationStore.instance()
    load = store.load

    def invalidate_during_load(*args, **kwargs):
        result = load(*args, **kwargs)
        if change == "cancel":
            assert scheduler.cancel(key)
        elif change == "replace":
            scheduler.schedule(cid, time.time() + 60, key=key, reason="replacement")
        elif change == "conversation":
            assert scheduler.cancel_for_conversation(cid) == 1 + int(keep_other)
        else:
            scheduler.get_due()
        return result

    monkeypatch.setattr(store, "load", invalidate_during_load)
    threads = _poll(task, cid)
    if keep_other and change in {"cancel", "replace"}:
        assert len(threads) == 1
        assert task._build_poll_context.call_args.kwargs["scheduled_reasons"] == [
            "[scheduled:assistant] keep plan"]
    else:
        assert threads == []
        task._build_poll_context.assert_not_called()
        task._redirect_external_mcp_wake.assert_not_called()
        assert cid not in task._active_conversations
    if change == "replace":
        assert scheduler.get(key)["reason"] == "replacement"


@pytest.mark.parametrize("mode", ["active_local", "active_external", "idle_local", "idle_external"])
def test_cancel_does_not_report_success_after_reminder_delivery_starts(
        poller_env, monkeypatch, mode):
    from core.poll_scheduler import PollScheduler
    from core.pending_queue import PendingQueue

    cid = "committed_reminder"
    active = mode.startswith("active")
    external = mode.endswith("external")
    task = _active_task(cid, ["assistant"] if active else [])
    key = f"{cid}::continuation::old"
    _schedule(cid, key, "[scheduled:assistant] deliver once")
    scheduler = PollScheduler.instance()
    cancellations = []
    if external:
        task._redirect_external_mcp_wake = (
            AgentPollerMixin._redirect_external_mcp_wake.__get__(task))
        monkeypatch.setattr("core.conv_agent_config.get_agent_config",
                            lambda *args: {"runtime_kind": "external_mcp"})
        route = MagicMock(return_value=("external_mcp", True))
        monkeypatch.setattr(
            "services.external_agent_runtime_router.route_external_agent_prompt", route)
    method = "_persist_scheduled_wakeup" if active or external else "_build_poll_context"
    deliver = getattr(task, method)

    def cancel_during_delivery(*args, **kwargs):
        cancellations.append(scheduler.cancel(key))
        return deliver(*args, **kwargs)

    monkeypatch.setattr(task, method, cancel_during_delivery)
    threads = _poll(task, cid)
    assert cancellations == [False]
    assert len(threads) == int(not active and not external)
    if external:
        route.assert_called_once()
    elif active:
        assert PendingQueue.for_agent(cid, "assistant").peek_count() == 1


def test_cancel_at_active_queue_handoff_cannot_report_success(poller_env, monkeypatch):
    from core.pending_queue import PendingQueue
    from core.poll_scheduler import PollScheduler

    cid = "cancel_at_queue_handoff"
    task = _active_task(cid, ["assistant"])
    key = f"{cid}::continuation::old"
    _schedule(cid, key, "[scheduled:assistant] deliver once")
    queue = PendingQueue.for_agent(cid, "assistant")
    enqueue = queue.enqueue
    cancellations = []

    def cancel_then_enqueue(*args, **kwargs):
        cancellations.append(PollScheduler.instance().cancel(key))
        return enqueue(*args, **kwargs)

    monkeypatch.setattr(queue, "enqueue", cancel_then_enqueue)
    assert _poll(task, cid) == []
    assert cancellations == [False]
    assert queue.peek_count() == 1


def test_scheduled_entry_target_uses_roster_canonical_name(poller_env):
    from core.conv_agent_config import CONV_AGENTS_KEY
    from core.conversation_store import ConversationStore
    cid = "canonical_target"
    store = ConversationStore.instance()
    store.save(cid, [{"role": "assistant", "content": "hi"}], user_id="u")
    store.set_extra(cid, CONV_AGENTS_KEY, {"Wiki": {"definition": "Wiki"}})

    poller = AgentPollerMixin()
    assert poller._scheduled_entry_target(
        cid, {"key": f"{cid}::pending::wiki", "reason": "[skill-run] x"}
    ) == "Wiki"


def test_digest_shaped_agent_name_keeps_its_target(poller_env):
    from core.conv_agent_config import CONV_AGENTS_KEY
    from core.conversation_store import ConversationStore
    cid = "hex_agent_target"
    agent = "deadbeef"
    task = _active_task(cid, ["assistant"])
    ConversationStore.instance().set_extra(
        cid, CONV_AGENTS_KEY, {agent: {"definition": agent}})
    _schedule(cid, f"{cid}::pending::{agent}", "[bg-tool] result ready")

    threads = _poll(task, cid)

    assert len(threads) == 1
    assert threads[0].kwargs["args"][0]["_gen_key"] == f"{cid}:{agent}"
    assert _remaining(cid) == []
