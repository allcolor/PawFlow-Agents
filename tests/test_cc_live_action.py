"""Tests for the /cc_restart and /cc_live slash-command actions.

Exercises the action handler directly (not via HTTP) with a minimal
fake `self` context and a monkeypatched LiveSessionRegistry. The
handlers are thin — they translate action bodies into registry calls
and serialize the response — so the tests focus on (a) the registry
method picked for each shape and (b) the response payload.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from core import FlowFile
from tasks.ai.actions.cc_live import _handle_cc_live


class _FakeSelf:
    """Minimal stand-in for the AgentLoopTask instance."""

    def _resolve_agent_name(self, name, conv_id):
        return name  # pass-through — we don't need resolution here


@pytest.fixture
def fake_registry(monkeypatch):
    """Replace LiveSessionRegistry.instance() with a mock."""
    mock = MagicMock()
    mock.status.return_value = [
        {"conv_id": "C1", "agent_name": "agentA", "service_id": "svc",
         "svc_pool_idx": 0, "live": True, "idle_seconds": 10,
         "reuse_count": 2, "spawn_at": 0.0, "lived_seconds": 120,
         "user_id": "u"},
        {"conv_id": "C1", "agent_name": "agentB", "service_id": "svc",
         "svc_pool_idx": 0, "live": True, "idle_seconds": 30,
         "reuse_count": 0, "spawn_at": 0.0, "lived_seconds": 60,
         "user_id": "u"},
        {"conv_id": "C2", "agent_name": "agentA", "service_id": "svc",
         "svc_pool_idx": 0, "live": True, "idle_seconds": 5,
         "reuse_count": 1, "spawn_at": 0.0, "lived_seconds": 90,
         "user_id": "u"},
    ]
    mock.kill_and_evict_by_conv.return_value = 2
    mock.kill_and_evict_by_conv_agent.return_value = 1

    import core.cc_live_registry as _reg_mod
    monkeypatch.setattr(
        _reg_mod.LiveSessionRegistry, "instance",
        classmethod(lambda cls: mock))
    return mock


# ── cc_live_status ──────────────────────────────────────


def test_cc_live_status_filters_by_conv(fake_registry):
    ff = FlowFile(content=b"")
    body = {"conversation_id": "C1", "agent_name": ""}
    result = _handle_cc_live(_FakeSelf(), "cc_live_status", body,
                              store=None, user_id="u", flowfile=ff)
    assert result == [ff]
    payload = json.loads(ff.get_content())
    assert payload["action"] == "cc_live_status"
    assert payload["count"] == 2
    assert all(s["conv_id"] == "C1" for s in payload["sessions"])


def test_cc_live_status_filters_by_conv_and_agent(fake_registry):
    ff = FlowFile(content=b"")
    body = {"conversation_id": "C1", "agent_name": "agentA"}
    result = _handle_cc_live(_FakeSelf(), "cc_live_status", body,
                              store=None, user_id="u", flowfile=ff)
    payload = json.loads(ff.get_content())
    assert payload["count"] == 1
    assert payload["sessions"][0]["agent_name"] == "agentA"


def test_cc_live_status_empty_conv_returns_zero(fake_registry):
    ff = FlowFile(content=b"")
    body = {"conversation_id": "C_nonexistent", "agent_name": ""}
    result = _handle_cc_live(_FakeSelf(), "cc_live_status", body,
                              store=None, user_id="u", flowfile=ff)
    payload = json.loads(ff.get_content())
    assert payload["count"] == 0


# ── cc_restart ────────────────────────────────────────────


def test_cc_restart_without_agent_kills_whole_conv(fake_registry):
    ff = FlowFile(content=b"")
    body = {"conversation_id": "C1", "agent_name": ""}
    result = _handle_cc_live(_FakeSelf(), "cc_restart", body,
                              store=None, user_id="u", flowfile=ff)
    assert result == [ff]
    fake_registry.kill_and_evict_by_conv.assert_called_once()
    fake_registry.kill_and_evict_by_conv_agent.assert_not_called()
    payload = json.loads(ff.get_content())
    assert payload["action"] == "cc_restart"
    assert payload["killed"] == 2
    assert payload["agent_name"] is None


def test_cc_restart_with_agent_kills_only_that_agent(fake_registry):
    ff = FlowFile(content=b"")
    body = {"conversation_id": "C1", "agent_name": "agentA"}
    result = _handle_cc_live(_FakeSelf(), "cc_restart", body,
                              store=None, user_id="u", flowfile=ff)
    fake_registry.kill_and_evict_by_conv_agent.assert_called_once()
    fake_registry.kill_and_evict_by_conv.assert_not_called()
    payload = json.loads(ff.get_content())
    assert payload["killed"] == 1
    assert payload["agent_name"] == "agentA"


def test_missing_conv_returns_400(fake_registry):
    ff = FlowFile(content=b"")
    body = {"conversation_id": "", "agent_name": ""}
    result = _handle_cc_live(_FakeSelf(), "cc_restart", body,
                              store=None, user_id="u", flowfile=ff)
    assert result == [ff]
    assert ff.get_attribute("http.response.status") == "400"
    payload = json.loads(ff.get_content())
    assert "error" in payload
    # Neither kill path should fire.
    fake_registry.kill_and_evict_by_conv.assert_not_called()
    fake_registry.kill_and_evict_by_conv_agent.assert_not_called()


# ── unknown action pass-through ──────────────────────────────────


def test_unknown_action_returns_none(fake_registry):
    """Handler must return None for actions it doesn’t own so the chain
    continues to the next dispatcher."""
    ff = FlowFile(content=b"")
    body = {"conversation_id": "C1"}
    assert _handle_cc_live(
        _FakeSelf(), "some_other_action", body,
        store=None, user_id="u", flowfile=ff) is None
