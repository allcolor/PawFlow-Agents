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
def poller_env():
    from core.conversation_store import ConversationStore
    from core.poll_scheduler import PollScheduler
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
    with patch("tasks.ai.agent_poller.threading.Thread") as thread_cls:
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


def test_continuation_for_active_target_is_still_acknowledged_once(poller_env):
    cid = "continuation_ack"
    task = _active_task(cid, ["assistant"])
    _schedule(cid, f"{cid}::continuation::deadbeef",
              "[scheduled:assistant] [continuation] finish the fix")

    assert _poll(task, cid) == []
    assert _remaining(cid) == []


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
