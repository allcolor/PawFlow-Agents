"""Desktop acceptance evidence must reject missing or crossed runtime state."""

import base64
import queue
import struct
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

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


@pytest.mark.parametrize("painted", [True, False])
def test_browser_capture_requires_visible_render_and_cleans_up_on_failure(monkeypatch, tmp_path, painted):
    monkeypatch.setenv("PAWFLOW_DISPOSABLE_ACCEPTANCE", "1")
    profile = tmp_path / "profile"
    (profile / "Default").mkdir(parents=True)
    (profile / "Default/Preferences").write_text("{}")
    monkeypatch.setattr(probe, "Path", lambda _path: profile)
    server = MagicMock()
    monkeypatch.setattr(probe.http.server, "ThreadingHTTPServer", MagicMock(return_value=server))
    thread = MagicMock()
    monkeypatch.setattr(probe.threading, "Thread", MagicMock(return_value=thread))
    context = MagicMock()
    context.__enter__.return_value = context
    context.cookies.return_value = []
    page = context.new_page.return_value
    page.evaluate.return_value = None
    ready = False

    def wait_for_render(*_args, **_kwargs):
        nonlocal ready
        page.bring_to_front.assert_called_once_with()
        if not painted:
            raise TimeoutError("Page never painted")
        ready = True

    def screenshot():
        assert ready, "Capture ran before the visible page rendered"
        return b"rendered page"

    page.wait_for_function.side_effect = wait_for_render
    page.screenshot.side_effect = screenshot
    playwright = MagicMock()
    playwright.__enter__.return_value = playwright
    playwright.chromium.launch_persistent_context.return_value = context
    monkeypatch.setitem(sys.modules, "playwright.sync_api",
                        SimpleNamespace(sync_playwright=lambda: playwright))

    if painted:
        report = probe.browser_probe("alpha", 1)
        assert base64.b64decode(report["image"]) == b"rendered page"
        assert report["previous_state"] == {"storage": None, "cookie": None}
        page.screenshot.assert_called_once_with()
    else:
        with pytest.raises(TimeoutError, match="never painted"):
            probe.browser_probe("alpha", 1)
        page.screenshot.assert_not_called()
    context.__exit__.assert_called_once()
    server.shutdown.assert_called_once_with()
    server.server_close.assert_called_once_with()
    thread.join.assert_called_once_with(timeout=5)


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
