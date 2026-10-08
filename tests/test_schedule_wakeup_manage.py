"""ScheduleWakeup list/cancel: agents can remove stale wake-ups and loops."""

import time

import pytest

from core.handlers.file_ops import ScheduleWakeupHandler
from core.poll_scheduler import PollScheduler


@pytest.fixture
def scheduler(tmp_path, monkeypatch):
    monkeypatch.setattr("core.paths.POLL_SCHEDULE_FILE", tmp_path / "schedule.json")
    sched = PollScheduler()
    monkeypatch.setattr(PollScheduler, "_instance", sched)
    return sched


def _handler(cid="conv-a"):
    h = ScheduleWakeupHandler()
    h.set_conversation_id(cid)
    h.set_user_id("u")
    return h


def test_reason_not_required_by_schema():
    assert ScheduleWakeupHandler().parameters_schema["required"] == []


def test_schedule_requires_reason(scheduler):
    out = _handler().execute({"delay_seconds": 60})
    assert out.startswith("Error:")
    assert scheduler.list_all() == []


def test_schedule_reports_key(scheduler):
    out = _handler().execute({"delay_seconds": 60, "reason": "check"})
    assert "Key: conv-a" in out


def test_list_shows_only_own_conversation(scheduler):
    loop_key = scheduler.schedule_loop("conv-a", 1800, prompt="[scheduled:GameDev] KIMODO")
    scheduler.schedule("conv-b", time.time() + 60, reason="other conv")
    out = _handler().execute({"action": "list"})
    assert loop_key in out
    assert "every 1800s" in out
    assert "KIMODO" in out
    assert "other conv" not in out


def test_list_empty(scheduler):
    assert "No scheduled" in _handler().execute({"action": "list"})


def test_cancel_own_loop(scheduler):
    loop_key = scheduler.schedule_loop("conv-a", 1800, prompt="stale")
    out = _handler().execute({"action": "cancel", "key": loop_key})
    assert out == f"Cancelled {loop_key}."
    assert scheduler.list_loops("conv-a") == []
    assert PollScheduler().list_all() == []  # persisted


def test_cancel_other_conversation_refused(scheduler):
    loop_key = scheduler.schedule_loop("conv-b", 1800, prompt="not yours")
    out = _handler().execute({"action": "cancel", "key": loop_key})
    assert out.startswith("Error:")
    assert len(scheduler.list_loops("conv-b")) == 1


def test_cancel_requires_key(scheduler):
    assert _handler().execute({"action": "cancel"}).startswith("Error:")


def test_cancel_legacy_entry_without_key_field(scheduler):
    scheduler._schedules["conv-a"] = {
        "conversation_id": "conv-a", "recheck_at": time.time() + 60,
        "reason": "legacy"}
    assert "conv-a next" in _handler().execute({"action": "list"})
    assert _handler().execute({"action": "cancel", "key": "conv-a"}) == "Cancelled conv-a."


def test_unknown_action(scheduler):
    assert _handler().execute({"action": "purge"}).startswith("Error: unknown action")
