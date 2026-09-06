import json
import time
from unittest.mock import Mock

import pytest

from core.poll_scheduler import PollScheduler


@pytest.fixture
def scheduler(tmp_path, monkeypatch):
    monkeypatch.setattr("core.paths.POLL_SCHEDULE_FILE", tmp_path / "schedule.json")
    return PollScheduler()


def _due(scheduler, key="c::pending::a", cid="c"):
    scheduler.schedule(cid, time.time() - 1, key=key, user_id="u", reason="retry")
    return scheduler.get_due()[0]


def test_due_retries_are_saved_in_one_batch_and_survive_restart(scheduler, monkeypatch):
    for agent in ("a", "b"):
        scheduler.schedule("c", time.time() - 1, key=f"c::pending::{agent}")
    due = scheduler.get_due()
    save = Mock(wraps=scheduler._save)
    monkeypatch.setattr(scheduler, "_save", save)

    assert scheduler.reschedule_due([(entry, entry["key"], 10) for entry in due]) == 2
    save.assert_called_once()
    assert sorted(e["key"] for e in PollScheduler().list_all()) == [
        "c::pending::a", "c::pending::b"]
    import core.paths as paths
    assert all("_claim" not in row for row in json.loads(
        paths.POLL_SCHEDULE_FILE.read_text()))


@pytest.mark.parametrize("cancel_mode", ["key", "conversation", "prefix"])
def test_cancelled_due_entry_cannot_be_resurrected(scheduler, cancel_mode):
    entry = _due(scheduler)
    if cancel_mode == "key":
        assert scheduler.cancel(entry["key"])
    elif cancel_mode == "conversation":
        assert scheduler.cancel_for_conversation("c") == 1
    else:
        assert scheduler.cancel_for_conversation(
            "c", key_prefixes=["c::pending::"], reason_prefixes=["retry"]) == 1
    assert scheduler.reschedule_due([(entry, entry["key"], 10)]) == 0
    assert scheduler.list_all() == []
    assert PollScheduler().list_all() == []


def test_newer_schedule_wins_over_a_consumed_entry(scheduler):
    entry = _due(scheduler)
    scheduler.schedule("c", time.time() + 100, key=entry["key"], reason="newer")
    assert scheduler.reschedule_due([(entry, entry["key"], 0)]) == 0
    assert scheduler.get(entry["key"])["reason"] == "newer"


def test_unrelated_schedule_does_not_discard_a_due_retry(scheduler):
    entry = _due(scheduler)
    scheduler.schedule("other", time.time() + 100)
    assert scheduler.reschedule_due([(entry, entry["key"], 10)]) == 1
    assert len(scheduler.list_all()) == 2


def test_rekey_does_not_overwrite_an_existing_new_wake(scheduler):
    entry = _due(scheduler, key="c::external")
    scheduler.schedule("c", time.time() + 5, key="c::pending::a", reason="newer")
    assert scheduler.reschedule_due([(entry, "c::pending::a", 10)]) == 0
    assert scheduler.get("c::pending::a")["reason"] == "newer"


def test_previous_poll_batch_cannot_be_replayed(scheduler):
    entry = _due(scheduler)
    assert scheduler.get_due() == []
    assert scheduler.reschedule_due([(entry, entry["key"], 0)]) == 0


@pytest.mark.parametrize("by_prefix", [False, True])
def test_cancellation_also_covers_a_not_yet_created_retry_key(scheduler, by_prefix):
    entry = _due(scheduler, key="c::external")
    if by_prefix:
        scheduler.cancel_for_conversation(
            "c", key_prefixes=["c::pending::"], reason_prefixes=["retry"])
    else:
        scheduler.cancel("c::pending::a")
    assert scheduler.reschedule_due([(entry, "c::pending::a", 10)]) == 0
    assert scheduler.list_all() == []


def test_filtered_cancellation_preserves_unrelated_due_work(scheduler):
    entry = _due(scheduler, key="c::external")
    scheduler.cancel_for_conversation(
        "c", key_prefixes=["c::pending::"], reason_prefixes=["[pending]"])
    assert scheduler.reschedule_due([(entry, "c::pending::a", 10)]) == 1


def test_deferring_recurring_entry_keeps_its_recurrence(scheduler):
    key = scheduler.schedule_loop("c", -1, prompt="check", user_id="u")
    entry = scheduler.get_due()[0]
    assert scheduler.reschedule_due([(entry, key, 10)]) == 1
    assert scheduler.get(key)["recurring"] is True
    assert scheduler.get(key)["prompt"] == "check"
