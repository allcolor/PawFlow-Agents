"""Tests for the typed Desktop inventory actions (_sf_desktop, WS7).

Locks the plan §12.3 contract on the current runtime: visibility-filtered
listing, attach-never-starts, stop request/confirm with exact-session
conflicts, idempotent lost-ack retries, and unknown-state handling for
unreachable relays.
"""
import json

import pytest

from services import desktop_inventory as inv
from tasks.ai.actions._sf_desktop import _handle_sf_desktop
from tasks.ai.actions._sf_base import _UNHANDLED


class FakeFlowFile:
    def __init__(self, attrs=None):
        self._attrs = dict(attrs or {})
        self.content = b""

    def get_attribute(self, key):
        return self._attrs.get(key)

    def set_attribute(self, key, value):
        self._attrs[key] = value

    def set_content(self, data):
        self.content = data

    @property
    def payload(self):
        return json.loads(self.content.decode())


class FakeRelaySvc:
    """Fake relay reproducing the REAL transport contract.

    Handlers return relay-shaped envelopes ({ok, data?, error?}); the wire
    forwards ``result.get("data", result)`` and the server ``_request``
    raises on an unwrapped ``ok: false`` (services/_filesystem_ops.py).
    Tests must exercise that unwrap, not hand the raw envelope to the
    action layer — that gap is exactly what hid the conflict-swallowing
    bug in review.
    """

    def __init__(self, status=None, stop_result=None, config=None):
        self.status = status if status is not None else {}
        self.stop_result = stop_result if stop_result is not None else {"ok": True}
        self.config = config or {}
        self.requests = []
        self.connected = True

    @staticmethod
    def _transport(envelope):
        data = envelope.get("data", envelope)
        if isinstance(data, dict) and data.get("ok") is False:
            raise Exception(data.get("error", "Relay error"))
        return data

    def _request(self, action, **kwargs):
        self.requests.append((action, kwargs))
        if action == "desktop_status":
            if isinstance(self.status, Exception):
                raise self.status
            return self._transport({"ok": True, "data": dict(self.status)})
        if action in ("stop_desktop", "stop_local_desktop"):
            return self._transport(dict(self.stop_result))
        raise AssertionError(f"unexpected relay action {action}")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    inv._reset_for_tests()
    # Default visibility: r1 and r2 belong to the test principal.
    import core.relay_bindings as rb
    monkeypatch.setattr(
        rb, "list_available_relays",
        lambda user_id=None, conv_id=None: [
            {"relay_id": "r1"}, {"relay_id": "r2"}])
    yield
    inv._reset_for_tests()


def _call(action, body, svc_map=None, user_id="u1"):
    ff = FakeFlowFile()
    helpers = (lambda rid: (svc_map or {}).get(rid), None, None, None, None,
               None)
    res = _handle_sf_desktop(None, action, body, None, user_id, ff, helpers)
    return res, ff


def test_unrelated_action_is_unhandled():
    res, _ff = _call("open_desktop", {"relay_id": "r1"})
    assert res is _UNHANDLED


def test_list_active_is_visibility_filtered():
    inv.record_running("r1", "docker", "s1")
    inv.record_running("hidden", "docker", "sX")
    _res, ff = _call("desktop_list_active", {})
    rows = ff.payload["desktops"]
    assert [r["relay_id"] for r in rows] == ["r1"]


def test_list_active_probe_reconciles_and_marks_unknown():
    inv.record_running("r1", "docker", "s1")
    inv.record_running("r2", "docker", "s2")
    svc_map = {
        # r1 answers: docker desktop stopped meanwhile.
        "r1": FakeRelaySvc(status={"running": False}),
        # r2 is unreachable: its row must become unknown, not vanish.
        "r2": FakeRelaySvc(status=RuntimeError("relay gone")),
    }
    _res, ff = _call("desktop_list_active", {"probe": True}, svc_map)
    rows = {r["relay_id"]: r for r in ff.payload["desktops"]}
    assert "r1" not in rows
    assert rows["r2"]["state"] == "unknown"


def test_relay_outside_visibility_is_not_found():
    _res, ff = _call("desktop_stop_request", {"relay_id": "other"})
    assert "not found" in ff.payload["error"]


def test_stop_request_returns_exact_session():
    svc = FakeRelaySvc(status={"running": True, "session_id": "s1",
                               "started_at": 7.0})
    _res, ff = _call("desktop_stop_request", {"relay_id": "r1"}, {"r1": svc})
    body = ff.payload
    assert body["confirm_required"] is True
    assert body["desktop"]["desktop_session_id"] == "s1"


def test_stop_request_without_desktop_conflicts():
    svc = FakeRelaySvc(status={"running": False})
    _res, ff = _call("desktop_stop_request", {"relay_id": "r1"}, {"r1": svc})
    assert ff.payload["code"] == "not_running"


def test_stop_confirm_requires_session_id():
    svc = FakeRelaySvc(status={"running": True, "session_id": "s1"})
    _res, ff = _call("desktop_stop_confirm", {"relay_id": "r1"}, {"r1": svc})
    assert "desktop_session_id" in ff.payload["error"]


def test_stop_confirm_stale_session_conflicts(monkeypatch):
    svc = FakeRelaySvc(status={"running": True, "session_id": "s-new"})
    _res, ff = _call("desktop_stop_confirm",
                     {"relay_id": "r1", "desktop_session_id": "s-old"},
                     {"r1": svc})
    body = ff.payload
    assert body["code"] == "session_conflict"
    assert body["current_session_id"] == "s-new"
    # The newer session must remain untouched.
    assert not [r for r in svc.requests if r[0] == "stop_desktop"]
    assert inv.get_active("r1", "docker")["state"] == "running"


def test_stop_confirm_exact_session_stops(monkeypatch):
    import services.vnc_proxy as vp
    import services.audio_proxy as ap
    unregistered = []
    monkeypatch.setattr(vp, "unregister_session",
                        lambda sid: unregistered.append(("vnc", sid)))
    monkeypatch.setattr(ap, "unregister_audio_source",
                        lambda sid: unregistered.append(("audio", sid)))
    svc = FakeRelaySvc(status={"running": True, "session_id": "s1"},
                       stop_result={"ok": True})
    _res, ff = _call("desktop_stop_confirm",
                     {"relay_id": "r1", "desktop_session_id": "s1"},
                     {"r1": svc})
    assert ff.payload == {"ok": True, "stopped_session_id": "s1"}
    stop_calls = [r for r in svc.requests if r[0] == "stop_desktop"]
    assert stop_calls == [("stop_desktop", {"session_id": "s1"})]
    assert ("vnc", "desktop_r1") in unregistered
    assert ("audio", "desktop_r1") in unregistered
    assert inv.get_active("r1", "docker") is None


def test_stop_confirm_lost_ack_retry_is_idempotent():
    svc = FakeRelaySvc(status={"running": False})
    _res, ff = _call("desktop_stop_confirm",
                     {"relay_id": "r1", "desktop_session_id": "s1"},
                     {"r1": svc})
    assert ff.payload == {"ok": True, "was_running": False}


def test_stop_confirm_relay_conflict_is_propagated():
    svc = FakeRelaySvc(
        status={"running": True, "session_id": "s1"},
        # Relay-real conflict envelope, exercised through the transport
        # unwrap (see pawflow_relay/_relay_desktop.py stop_desktop).
        stop_result={"ok": True, "data": {
            "stopped": False, "conflict": True,
            "current_session_id": "s-raced"}})
    _res, ff = _call("desktop_stop_confirm",
                     {"relay_id": "r1", "desktop_session_id": "s1"},
                     {"r1": svc})
    assert ff.payload["code"] == "session_conflict"
    assert ff.payload["current_session_id"] == "s-raced"
    # A swallowed conflict must not release routes or mark stopped.
    assert inv.get_active("r1", "docker") is not None


def test_stop_confirm_relay_failure_is_an_error_not_success(monkeypatch):
    import services.vnc_proxy as vp
    unregistered = []
    monkeypatch.setattr(vp, "unregister_session",
                        lambda sid: unregistered.append(sid))
    svc = FakeRelaySvc(
        status={"running": True, "session_id": "s1"},
        stop_result={"ok": False, "error": "stop failed on relay"})
    _res, ff = _call("desktop_stop_confirm",
                     {"relay_id": "r1", "desktop_session_id": "s1"},
                     {"r1": svc})
    assert "stop failed on relay" in ff.payload["error"]
    assert not unregistered  # routes stay until the relay confirms


def test_attach_refuses_when_not_running():
    svc = FakeRelaySvc(status={"running": False})
    _res, ff = _call("desktop_attach", {"relay_id": "r1"}, {"r1": svc})
    assert ff.payload["code"] == "not_running"
    # Attach must never start anything.
    assert [r[0] for r in svc.requests] == ["desktop_status"]


def test_attach_running_delegates_to_open_with_no_start(monkeypatch):
    svc = FakeRelaySvc(status={"running": True, "session_id": "s1"})
    seen = {}

    def fake_open(self, action, body, store, user_id, flowfile, helpers):
        seen.update(body)
        flowfile.set_content(json.dumps({"ok": True, "url": "/vnc/x"}).encode())
        return [flowfile]

    import tasks.ai.actions._sf_k7 as k7
    monkeypatch.setattr(k7, "_handle_sf_k7", fake_open)
    _res, ff = _call("desktop_attach", {"relay_id": "r1"}, {"r1": svc})
    assert ff.payload["ok"] is True
    assert seen["no_start"] is True
    assert seen["relay_id"] == "r1"


def test_disconnected_relay_marks_unknown():
    inv.record_running("r1", "docker", "s1")
    _res, ff = _call("desktop_stop_request", {"relay_id": "r1"}, {})
    assert "not connected" in ff.payload["error"]
    assert inv.get_active("r1", "docker")["state"] == "unknown"


def test_list_probe_marks_absent_relay_unknown():
    inv.record_running("r1", "docker", "s1")
    # r1 has no service at all; probe must not leave the row 'running'.
    _res, ff = _call("desktop_list_active", {"probe": True}, {})
    rows = {r["relay_id"]: r for r in ff.payload["desktops"]}
    assert rows["r1"]["state"] == "unknown"


def test_list_probe_marks_disconnected_relay_unknown():
    inv.record_running("r1", "docker", "s1")
    svc = FakeRelaySvc(status={"running": True, "session_id": "s1"})
    svc.connected = False
    _res, ff = _call("desktop_list_active", {"probe": True}, {"r1": svc})
    rows = {r["relay_id"]: r for r in ff.payload["desktops"]}
    assert rows["r1"]["state"] == "unknown"
    assert not svc.requests  # a disconnected relay is not probed


def test_desktop_actions_have_authorization_roles():
    # WP8 §12.4: view/control gates. The concrete primitive is the
    # conversation-role table consumed by _authorize_relay_action.
    from tasks.ai.actions.service_flow import _RELAY_ACTION_ROLES
    assert _RELAY_ACTION_ROLES["desktop_list_active"] == "read"
    assert _RELAY_ACTION_ROLES["desktop_stop_request"] == "read"
    assert _RELAY_ACTION_ROLES["desktop_attach"] == "write"
    assert _RELAY_ACTION_ROLES["desktop_stop_confirm"] == "write"


def test_invalid_mode_rejected():
    svc = FakeRelaySvc()
    _res, ff = _call("desktop_stop_request",
                     {"relay_id": "r1", "mode": "vm"}, {"r1": svc})
    assert "Invalid mode" in ff.payload["error"]


def test_host_mode_stop_uses_local_desktop_path(monkeypatch):
    import services.vnc_proxy as vp
    import services.audio_proxy as ap
    monkeypatch.setattr(vp, "unregister_session", lambda sid: None)
    monkeypatch.setattr(ap, "unregister_audio_source", lambda sid: None)
    svc = FakeRelaySvc(
        status={"running": False, "local_screen_running": True,
                "local_screen_session_id": "h1"},
        stop_result={"ok": True})
    _res, ff = _call("desktop_stop_confirm",
                     {"relay_id": "r1", "desktop_session_id": "h1",
                      "mode": "host"},
                     {"r1": svc})
    assert ff.payload["ok"] is True
    # The host stop carries the exact session for relay-side compare.
    assert ("stop_local_desktop", {"session_id": "h1"}) in svc.requests


def test_host_mode_stale_stop_conflicts_via_relay(monkeypatch):
    svc = FakeRelaySvc(
        status={"running": False, "local_screen_running": True,
                "local_screen_session_id": "h-new"},
        stop_result={"ok": True, "data": {
            "stopped": False, "conflict": True,
            "current_session_id": "h-new"}})
    _res, ff = _call("desktop_stop_confirm",
                     {"relay_id": "r1", "desktop_session_id": "h-old",
                      "mode": "host"},
                     {"r1": svc})
    # Server-side compare already rejects before reaching the relay.
    assert ff.payload["code"] == "session_conflict"
    assert not [r for r in svc.requests if r[0] == "stop_local_desktop"]
