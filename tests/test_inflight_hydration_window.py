"""A finished tool call still hydrates until its result reaches the transcript.

A request leaves ``_inflight`` the moment it completes -- it has to, or kill,
cancel and has_unbound_inflight would act on finished work. But its result is
written to the transcript slightly later, by the provider, after execute()
returns.

``load_history`` reads the page first and takes the in-flight snapshot second
(tasks/ai/actions/_conv_core.py). A request landing between the two got the
worst of both: the page had no result yet, and the snapshot no longer had the
entry. The row rendered as an ordinary finished call -- no pending bullet, no
BG/Kill, and no result either.

The reverse ordering was already handled: a row that carries its result is
finished whatever the table says. This is the other half.
"""

import time

import pytest

from services.tool_relay_service import ToolRelayService


@pytest.fixture(autouse=True)
def _clean():
    with ToolRelayService._inflight_lock:
        ToolRelayService._inflight.clear()
        ToolRelayService._recently_finished.clear()
    yield
    with ToolRelayService._inflight_lock:
        ToolRelayService._inflight.clear()
        ToolRelayService._recently_finished.clear()


def _start(rid="r1", conv="c1", agent="claude", tc_id="tc_1"):
    with ToolRelayService._inflight_lock:
        ToolRelayService._inflight[rid] = {
            "conv": conv, "agent": agent, "cc_tc_id": tc_id,
            "tool_name": "read", "started_at": time.time(),
            "background": None,
        }


def test_a_running_call_is_in_the_snapshot():
    _start()
    snap = ToolRelayService.inflight_snapshot("c1")
    assert [row["tc_id"] for row in snap] == ["tc_1"]


def test_a_just_finished_call_is_still_hydratable():
    """The bug: this returned [] and the row lost both its result and its
    live marker."""
    _start()
    ToolRelayService.retire_inflight("r1")
    snap = ToolRelayService.inflight_snapshot("c1")
    assert [row["tc_id"] for row in snap] == ["tc_1"]


def test_the_grace_expires():
    _start()
    ToolRelayService.retire_inflight("r1")
    with ToolRelayService._inflight_lock:
        ToolRelayService._recently_finished["r1"]["finished_at"] = (
            time.time() - ToolRelayService._HYDRATION_GRACE_SECONDS - 1)
    assert ToolRelayService.inflight_snapshot("c1") == []


def test_retiring_prunes_older_entries():
    _start("r1", tc_id="tc_1")
    ToolRelayService.retire_inflight("r1")
    with ToolRelayService._inflight_lock:
        ToolRelayService._recently_finished["r1"]["finished_at"] = (
            time.time() - ToolRelayService._HYDRATION_GRACE_SECONDS - 1)
    _start("r2", tc_id="tc_2")
    ToolRelayService.retire_inflight("r2")
    with ToolRelayService._inflight_lock:
        assert list(ToolRelayService._recently_finished) == ["r2"]


# ── the control paths must NOT see a retired request ────────────────────────


def test_a_retired_request_is_no_longer_in_flight_for_control():
    """Kill, cancel and the unbound check act on work that is still running.
    A finished request answering them would kill the next thing instead."""
    _start(tc_id="")
    assert ToolRelayService.has_unbound_inflight("c1") is True
    ToolRelayService.retire_inflight("r1")
    assert ToolRelayService.has_unbound_inflight("c1") is False
    with ToolRelayService._inflight_lock:
        assert "r1" not in ToolRelayService._inflight


def test_retiring_an_unknown_request_is_harmless():
    ToolRelayService.retire_inflight("never-registered")
    assert ToolRelayService.inflight_snapshot("c1") == []


def test_the_snapshot_still_scopes_by_conversation_and_agent():
    _start("r1", conv="c1", agent="claude", tc_id="tc_1")
    ToolRelayService.retire_inflight("r1")
    assert ToolRelayService.inflight_snapshot("c2") == []
    assert ToolRelayService.inflight_snapshot("c1", "other") == []
    assert len(ToolRelayService.inflight_snapshot("c1", "claude")) == 1
