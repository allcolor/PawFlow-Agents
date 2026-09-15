"""Desktop acceptance evidence must reject missing or crossed runtime state."""

import base64
import queue
import struct
from types import SimpleNamespace

import pytest

from tests import physical_desktop_probe as probe


def test_desktop_probe_refuses_non_disposable_execution(monkeypatch):
    monkeypatch.delenv("PAWFLOW_DISPOSABLE_ACCEPTANCE", raising=False)
    with pytest.raises(RuntimeError, match="explicitly disposable"):
        probe.run_acceptance()


def test_desktop_exports_retain_separate_mounts_and_enable_automation():
    first, second = probe.exports("ws://192.0.2.1:9000")
    assert first["root_mount"] != second["root_mount"]
    assert first["home_mount"] != second["home_mount"]
    for export in (first, second):
        assert export["mode"] == "rw"
        assert "--allow-automation" in export["command"]
        assert "--readonly" not in export["command"]


@pytest.mark.parametrize("state", [
    {"storage": "beta", "cookie": "beta"},
    {"storage": "alpha", "cookie": None},
    {"storage": None, "cookie": "alpha"},
    {},
])
def test_browser_restart_requires_both_values_for_the_same_logical_identity(state):
    with pytest.raises(RuntimeError, match="profile"):
        probe.validate_browser_state(state, "alpha", 2)


def test_new_and_restarted_browser_profiles_have_distinct_expected_state():
    probe.validate_browser_state({"storage": None, "cookie": None}, "alpha", 1)
    probe.validate_browser_state({"storage": "alpha", "cookie": "alpha"}, "alpha", 2)
    with pytest.raises(RuntimeError, match="profile"):
        probe.validate_browser_state({"storage": "alpha", "cookie": "alpha"}, "alpha", 1)


def test_screenshot_evidence_rejects_wrong_size_or_non_png():
    for data in (b"not an image", b"\x89PNG\r\n\x1a\n" + bytes(8) + struct.pack("!II", 1, 1) + b"x"):
        with pytest.raises(RuntimeError, match="Screenshot"):
            probe.png_bytes({"image": base64.b64encode(data).decode()})


def test_vnc_bridge_joins_split_binary_frames():
    events = queue.Queue()
    for payload in (b"RFB ", b"003.008\n"):
        events.put({"type": "desktop_ws_data", "session_id": "session",
                    "opcode": 2, "data": base64.b64encode(payload).decode()})
    peer = SimpleNamespace(events=events, name="alpha")
    assert probe.vnc_bytes(peer, "session", 12) == b"RFB 003.008\n"


@pytest.mark.parametrize("event,reason", [
    ({"type": "desktop_ws_data", "session_id": "beta"}, "crossed identities"),
    ({"type": "desktop_ws_close", "session_id": "alpha"}, "closed"),
])
def test_vnc_bridge_refuses_crossed_sessions_and_early_close(event, reason):
    events = queue.Queue()
    events.put(event)
    with pytest.raises(RuntimeError, match=reason):
        probe.vnc_bytes(SimpleNamespace(events=events, name="alpha"), "alpha", 12)
