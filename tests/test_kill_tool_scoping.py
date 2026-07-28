"""A kill aimed at one tool call must never kill a different one.

Observed 2026-07-28: an `edit` tool_call stayed rendered as in-flight after
it had actually completed (its tool_result never reached the UI). Clicking
its kill button ran a targeted cancel that found nothing — and the broad
agent-level fallback then cancelled the `bash` that WAS running.
"""

from services.tool_relay_service import ToolRelayService


def _inflight(monkeypatch, entries):
    monkeypatch.setattr(ToolRelayService, "_inflight", dict(entries),
                        raising=False)


def test_finished_call_does_not_justify_widening(monkeypatch):
    # The only live request is a DIFFERENT, already-bound tool: the kill
    # target is gone and widening would hit a bystander.
    _inflight(monkeypatch, {
        "rid_bash": {"conv": "80c37670", "agent": "claude",
                     "cc_tc_id": "toolu_bash", "tool_name": "bash"},
    })

    assert ToolRelayService.has_unbound_inflight("80c37670") is False


def test_unpublished_call_still_justifies_widening(monkeypatch):
    # A request dispatched before its tool_call id was published carries no
    # cc_tc_id — a targeted cancel cannot match it, so widening is the only
    # way to stop it.
    _inflight(monkeypatch, {
        "rid_pending": {"conv": "80c37670", "agent": "claude",
                        "cc_tc_id": "", "tool_name": "bash"},
    })

    assert ToolRelayService.has_unbound_inflight("80c37670") is True


def test_other_conversations_are_never_considered(monkeypatch):
    _inflight(monkeypatch, {
        "rid_other": {"conv": "deadbeef", "agent": "claude",
                      "cc_tc_id": "", "tool_name": "bash"},
    })

    assert ToolRelayService.has_unbound_inflight("80c37670") is False


def test_agent_filter_is_applied_when_given(monkeypatch):
    _inflight(monkeypatch, {
        "rid_pending": {"conv": "80c37670", "agent": "other",
                        "cc_tc_id": "", "tool_name": "bash"},
    })

    assert ToolRelayService.has_unbound_inflight("80c37670", "claude") is False
    assert ToolRelayService.has_unbound_inflight("80c37670", "other") is True


def test_nothing_in_flight_means_nothing_to_widen_to(monkeypatch):
    _inflight(monkeypatch, {})

    assert ToolRelayService.has_unbound_inflight("80c37670") is False
