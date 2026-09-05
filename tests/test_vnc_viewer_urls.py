"""Viewer URLs must resolve to the authenticated VNC route, without duplication."""

import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
from urllib.parse import parse_qs, urljoin, urlsplit

import pytest

from core.flowfile import FlowFile
from tasks.ai.actions import _sf_k7


@pytest.mark.parametrize("local_screen", [False, True])
@pytest.mark.parametrize("managed", [False, True])
@pytest.mark.parametrize("already_running", [False, True])
def test_desktop_viewer_websocket_resolves_to_session_route(
        monkeypatch, local_screen, managed, already_running):
    calls = []

    def request(action, **kwargs):
        calls.append(action)
        if action == "desktop_status":
            return {
                "running": already_running,
                "local_screen_running": already_running,
                "novnc_port": 6080,
                "local_screen_novnc_port": 6080,
            }
        assert action in ("start_desktop", "start_local_desktop")
        return {"novnc_port": 6080}

    relay = SimpleNamespace(
        config={"server_managed": managed, "server_local_exec": managed},
        _relay_addr="198.51.100.10", _request=request)
    monkeypatch.setattr("services.vnc_proxy.register_session",
                        lambda *args, **kwargs: "test-capability")
    monkeypatch.setattr(_sf_k7, "_ensure_vnc_routes", lambda ff: None)
    monkeypatch.setattr(_sf_k7, "_novnc_backend_http_ready", lambda *args: True)
    helpers = (lambda rid: relay, None, None, None,
               lambda rid, port: ("127.0.0.1", port) if managed else ("", 0),
               None)
    ff = FlowFile(attributes={"auth.session_id": "test-login"})
    _sf_k7._handle_sf_k7(
        None, "open_desktop",
        {"relay_id": "r1", "local_screen": local_screen,
         "no_start": already_running},
        None, "alice", ff, helpers)
    result = json.loads(ff.get_content())
    assert result["ok"], result
    viewer = urljoin("https://pawflow.example/chat", result["url"])
    ws_path = parse_qs(urlsplit(viewer).query)["path"][0]
    prefix = "local_desktop" if local_screen else "desktop"
    assert urljoin(viewer, ws_path) == (
        f"https://pawflow.example/vnc/{prefix}_r1/test-capability/websockify")
    if already_running:
        assert calls == ["desktop_status"]


def test_login_viewer_urls_resolve_in_javascript():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the login viewer URL regression")
    result = subprocess.run(
        [node, "tests/js/vnc_viewer_urls_spec.js"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
