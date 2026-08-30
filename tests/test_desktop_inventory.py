"""Tests for services.desktop_inventory — canonical Desktop session registry.

Locks the WS7 contract: authoritative population, exact-session conflicts,
unknown-vs-stopped distinction, visibility-bounded listing, display-safe
projection, and change notifications.
"""
import pytest

from services import desktop_inventory as inv


@pytest.fixture(autouse=True)
def _clean():
    inv._reset_for_tests()
    yield
    inv._reset_for_tests()


def test_record_running_requires_identities():
    with pytest.raises(ValueError):
        inv.record_running("", "docker", "s1")
    with pytest.raises(ValueError):
        inv.record_running("r1", "docker", "")
    with pytest.raises(ValueError):
        inv.record_running("r1", "vm", "s1")


def test_record_running_and_list_active():
    row = inv.record_running("r1", "docker", "s1", started_at=10.0,
                             started_by="alice")
    assert row["state"] == "running"
    assert row["workspace_isolated"] is True
    assert row["can_stop"] is True
    assert inv.list_active(["r1"]) == [row | {
        "last_heartbeat_at": row["last_heartbeat_at"]}]


def test_host_kind_is_not_workspace_isolated():
    row = inv.record_running("r1", "host", "s1")
    assert row["mode"] == "host"
    assert row["workspace_isolated"] is False


def test_list_active_is_visibility_bounded():
    inv.record_running("r1", "docker", "s1")
    inv.record_running("r2", "docker", "s2")
    rows = inv.list_active(["r2"])
    assert [r["relay_id"] for r in rows] == ["r2"]
    assert inv.list_active([]) == []


def test_public_projection_has_no_ports_or_paths():
    row = inv.record_running("r1", "docker", "s1")
    banned = {"novnc_port", "vnc_port", "audio_port", "display", "path",
              "token", "url"}
    assert banned.isdisjoint(row.keys())


def test_stopping_and_stopped_by_exact_session():
    inv.record_running("r1", "docker", "s1")
    assert inv.record_stopping("r1", "docker", "s1")["state"] == "stopping"
    assert inv.record_stopped("r1", "docker", "s1")["state"] == "stopped"
    assert inv.list_active(["r1"]) == []


def test_stale_session_conflicts_never_touch_newer_session():
    inv.record_running("r1", "docker", "s2")
    with pytest.raises(inv.SessionConflict) as e1:
        inv.record_stopping("r1", "docker", "s1")
    assert e1.value.current_session_id == "s2"
    with pytest.raises(inv.SessionConflict):
        inv.record_stopped("r1", "docker", "s1")
    assert inv.get_active("r1", "docker")["state"] == "running"


def test_mark_unknown_keeps_row_visible():
    inv.record_running("r1", "docker", "s1")
    changed = inv.mark_unknown("r1")
    assert changed and changed[0]["state"] == "unknown"
    rows = inv.list_active(["r1"])
    assert rows and rows[0]["state"] == "unknown"
    assert rows[0]["can_stop"] is True


def test_reconcile_status_updates_both_kinds():
    inv.record_running("r1", "docker", "s1")
    inv.record_running("r1", "host", "h1")
    # Probe says docker desktop is gone, host still running with same id.
    inv.reconcile_status("r1", {
        "running": False, "session_id": None,
        "local_screen_running": True, "local_screen_session_id": "h1",
        "local_screen_started_at": 5.0,
    })
    assert inv.get_active("r1", "docker") is None
    host = inv.get_active("r1", "host")
    assert host and host["state"] == "running"


def test_reconcile_replaces_stale_session_id():
    inv.record_running("r1", "docker", "s-old")
    inv.reconcile_status("r1", {"running": True, "session_id": "s-new"})
    assert inv.get_active("r1", "docker")["desktop_session_id"] == "s-new"


def test_reconcile_keeps_initiator_across_refresh():
    inv.record_running("r1", "docker", "s1", started_at=1.0,
                       started_by="alice")
    inv.reconcile_status("r1", {"running": True, "session_id": "s1"})
    row = inv.get_active("r1", "docker")
    assert row["started_by"] == "alice"
    assert row["started_at"] == 1.0


def test_change_listener_fires_on_transitions():
    events = []
    inv.set_change_listener(lambda rid, entry: events.append(
        (rid, entry["state"])))
    inv.record_running("r1", "docker", "s1")
    inv.record_stopping("r1", "docker", "s1")
    inv.record_stopped("r1", "docker", "s1")
    assert events == [("r1", "running"), ("r1", "stopping"),
                      ("r1", "stopped")]


def test_listener_failure_never_breaks_inventory():
    def _boom(_rid, _entry):
        raise RuntimeError("listener down")
    inv.set_change_listener(_boom)
    row = inv.record_running("r1", "docker", "s1")
    assert row["state"] == "running"
