"""Behavioral tests for the relay per-message dispatcher (_relay_dispatch).

Drives execute_command with a fake DispatchCtx through the routing paths
that don't spawn real processes: readonly rejection, unknown action,
local=True host-forward, terminal open/list via a fake manager, and the
allow_exec gate. First execution coverage of the dispatch routing.
"""
import sys
import types
import urllib.request
import hashlib
from pathlib import Path

import pytest


# tools/ on path so the dispatcher's lazy `from fs_actions import ACTIONS`
# (only hit on the generic fall-through) resolves like in the relay container.
sys.path.append(str(Path(__file__).resolve().parent.parent / "tools"))

from pawflow_relay import _relay_dispatch as d
import fs_http
from _fs_read import (
    action_append_file,
    action_atomic_write_file,
    action_truncate_file,
)


def _ctx(**over):
    base = dict(
        state=types.SimpleNamespace(),
        term_mgr=None,
        send_lock=__import__("threading").Lock(),
        ws_sock_ref=[object()],
        ws_frame_send=lambda _s, _f: None,
        resolve=lambda p: "/abs/" + p,
        forward_to_host_helper=lambda *a, **k: {"ok": True, "data": {"forwarded": True}},
        root_dir="/root",
        readonly=False,
        allow_exec=True,
        allow_local=True,
        allow_local_screen=True,
        allow_automation=True,
        allow_service_tunnels=False,
    )
    base.update(over)
    return d.DispatchCtx(**base)


def test_readonly_rejects_write_action():
    res = d.execute_command(_ctx(readonly=True), {"action": "write_file", "path": "x"})
    assert res == {"ok": False, "error": "Operation not allowed in readonly mode"}


def test_unknown_action_reports_unknown():
    res = d.execute_command(_ctx(), {"action": "definitely_not_a_real_action", "path": "."})
    assert res["ok"] is False
    assert "Unknown action" in res["error"]


def test_local_true_requires_allow_local():
    res = d.execute_command(_ctx(allow_local=False), {"action": "http_fetch", "local": True})
    assert res["ok"] is False
    assert "Local execution disabled" in res["error"]


def test_local_true_forwards_to_host(monkeypatch):
    monkeypatch.setenv("PAWFLOW_HOST_HELPER", "http://host-helper")
    seen = {}

    def fake_forward(hh, fwd, sock, send):
        seen["hh"] = hh
        return {"ok": True, "data": {"forwarded": True}}

    res = d.execute_command(_ctx(forward_to_host_helper=fake_forward),
                            {"action": "http_fetch", "local": True, "path": "."})
    assert res == {"ok": True, "data": {"forwarded": True}}
    assert seen["hh"] == "http://host-helper"


def test_http_fetch_public_only_rejects_private_literal_without_network(monkeypatch):
    monkeypatch.setattr(
        urllib.request, "build_opener",
        lambda *_args: (_ for _ in ()).throw(AssertionError("network opened")),
        raising=False,
    )

    result = fs_http.action_http_fetch("/root", ".", {
        "url": "http://127.0.0.1/private.png",
        "public_only": True,
    })

    assert result["ok"] is False
    assert "public" in result["error"]


def test_http_fetch_enforces_response_byte_limit(monkeypatch):
    class Response:
        status = 200
        headers = {"Content-Type": "image/png"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size=-1):
            return b"oversized"

        def geturl(self):
            return "https://example.com/image.png"

    class Opener:
        def open(self, _request, timeout=0):
            assert timeout == 30
            return Response()

    monkeypatch.setattr(urllib.request, "build_opener", lambda *_args: Opener())

    result = fs_http.action_http_fetch("/root", ".", {
        "url": "https://example.com/image.png",
        "timeout": 30,
        "max_bytes": 3,
    })

    assert result["ok"] is False
    assert "byte limit" in result["error"]


def test_http_fetch_to_file_streams_hashes_and_atomically_replaces(
    monkeypatch, tmp_path,
):
    class Response:
        status = 200
        headers = {
            "Content-Type": "text/css",
            "Content-Length": "12",
        }

        def __init__(self):
            self._chunks = [b"body{", b"color:red}"]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size=-1):
            return self._chunks.pop(0) if self._chunks else b""

        def geturl(self):
            return "https://example.com/site.css"

    class Opener:
        def open(self, _request, timeout=0):
            assert timeout == 30
            return Response()

    monkeypatch.setattr(fs_http, "_public_http_url", lambda value: value)
    monkeypatch.setattr(urllib.request, "build_opener", lambda *_args: Opener())
    target = tmp_path / "assets" / "site.css"
    target.parent.mkdir()
    target.write_bytes(b"old")

    result = fs_http.action_http_fetch_to_file(
        str(tmp_path),
        str(target),
        {
            "url": "https://example.com/site.css",
            "timeout": 30,
            "max_bytes": 20,
            "public_only": True,
        },
    )

    content = b"body{color:red}"
    assert result["saved"] is True
    assert result["bytes"] == len(content)
    assert result["sha256"] == hashlib.sha256(content).hexdigest()
    assert target.read_bytes() == content
    assert list(target.parent.glob("*.part")) == []


def test_manifest_xml_validation_uses_a_safe_parser():
    assert fs_http._asset_signature(
        "manifest",
        b"<?xml version='1.0'?><manifest><name>PawFlow</name></manifest>",
        ".xml",
    ) == "application/xml"
    with pytest.raises(ValueError, match="declarations are prohibited"):
        fs_http._asset_signature(
            "manifest",
            b"<!DOCTYPE manifest [<!ENTITY leak SYSTEM 'file:///etc/passwd'>]>"
            b"<manifest>&leak;</manifest>",
            ".xml",
        )


def test_http_fetch_to_file_removes_partial_and_preserves_destination_on_cap(
    monkeypatch, tmp_path,
):
    class Response:
        status = 200
        headers = {"Content-Type": "video/mp4"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size=-1):
            return b"too-large"

        def geturl(self):
            return "https://example.com/movie.mp4"

    class Opener:
        def open(self, _request, timeout=0):
            return Response()

    monkeypatch.setattr(fs_http, "_public_http_url", lambda value: value)
    monkeypatch.setattr(urllib.request, "build_opener", lambda *_args: Opener())
    target = tmp_path / "movie.mp4"
    target.write_bytes(b"previous")

    result = fs_http.action_http_fetch_to_file(
        str(tmp_path),
        str(target),
        {
            "url": "https://example.com/movie.mp4",
            "max_bytes": 3,
            "public_only": True,
        },
    )

    assert result["ok"] is False
    assert "byte limit" in result["error"]
    assert target.read_bytes() == b"previous"
    assert list(tmp_path.glob("*.part")) == []


def test_readonly_rejects_http_fetch_to_file():
    result = d.execute_command(
        _ctx(readonly=True),
        {
            "action": "http_fetch_to_file",
            "path": "assets/site.css",
            "url": "https://example.com/site.css",
            "max_bytes": 10,
            "public_only": True,
        },
    )
    assert result == {
        "ok": False,
        "error": "Operation not allowed in readonly mode",
    }


def test_readonly_rejects_template_archive_extraction():
    result = d.execute_command(
        _ctx(readonly=True),
        {
            "action": "extract_zip_subtree",
            "path": "template.zip",
            "dest_path": "template/content",
            "artifact_root": "repo/dist",
        },
    )
    assert result == {
        "ok": False,
        "error": "Operation not allowed in readonly mode",
    }


def test_atomic_append_and_truncate_enforce_checkpoint_offsets(tmp_path):
    target = tmp_path / "inventory" / "pages.ndjson"
    root = str(tmp_path)
    encoded = lambda value: __import__("base64").b64encode(value).decode("ascii")

    action_atomic_write_file(root, str(target), {
        "content": encoded(b"first\n"), "base64": True,
    })
    appended = action_append_file(root, str(target), {
        "content": encoded(b"second\n"), "base64": True, "expected_size": 6,
    })
    assert appended["size"] == 13
    assert target.read_bytes() == b"first\nsecond\n"
    with __import__("pytest").raises(ValueError, match="offset mismatch"):
        action_append_file(root, str(target), {
            "content": encoded(b"duplicate\n"), "base64": True,
            "expected_size": 6,
        })
    action_truncate_file(root, str(target), {"size": 6, "expected_size": 13})
    assert target.read_bytes() == b"first\n"


def test_open_terminal_gated_by_allow_exec():
    res = d.execute_command(_ctx(allow_exec=False), {"action": "open_terminal"})
    assert res == {"ok": False, "error": "Exec not allowed"}


def test_open_and_list_terminal_via_manager():
    class FakeTM:
        def __init__(self):
            self._sessions = {}

        def open(self, cols=80, rows=24, shell=None):
            self._sessions["t1"] = {"shell": shell or "/bin/sh"}
            return "t1"

        def list(self):
            return [{"session_id": s, "shell": v["shell"]} for s, v in self._sessions.items()]

    tm = FakeTM()
    ctx = _ctx(term_mgr=tm)
    res = d.execute_command(ctx, {"action": "open_terminal", "shell": "/bin/bash"})
    assert res == {"ok": True, "data": {"session_id": "t1"}}
    res2 = d.execute_command(ctx, {"action": "list_terminals"})
    assert res2 == {"ok": True, "data": {"sessions": [{"session_id": "t1", "shell": "/bin/bash"}]}}


def test_http_proxy_gated_by_allow_exec():
    res = d.execute_command(_ctx(allow_exec=False), {"action": "http_proxy", "port": 9})
    assert res == {"ok": False, "error": "Exec not allowed"}


def test_local_terminal_write_forwards_to_host(monkeypatch):
    # local_term_* terminal ops are forwarded to the host helper when set.
    monkeypatch.setenv("PAWFLOW_HOST_HELPER", "http://hh")
    seen = {}

    def fake_forward(hh, fwd, sock, send):
        seen["action"] = fwd.get("action")
        return {"ok": True, "data": {"fwd": True}}

    res = d.execute_command(
        _ctx(forward_to_host_helper=fake_forward),
        {"action": "write_terminal", "session_id": "local_term_x", "data": "abc"})
    assert res == {"ok": True, "data": {"fwd": True}}
    assert seen["action"] == "write_terminal"


def test_local_terminal_write_falls_through_without_host(monkeypatch):
    # No host helper -> the op runs against the in-relay terminal manager.
    monkeypatch.delenv("PAWFLOW_HOST_HELPER", raising=False)

    class TM:
        def write(self, sid, data):
            return True, ""

    res = d.execute_command(
        _ctx(term_mgr=TM()),
        {"action": "write_terminal", "session_id": "local_term_x", "data": "abc"})
    assert res == {"ok": True}


def test_desktop_status_routes_via_table():
    from pawflow_relay._relay_state import RelayWorkerState
    res = d.execute_command(_ctx(state=RelayWorkerState()), {"action": "desktop_status"})
    assert res["ok"] is True
    assert res["data"]["running"] is False


def test_start_local_desktop_forwards_when_host_helper(monkeypatch):
    # start_local_desktop is NOT in the dispatch table: it must reach the
    # explicitly-local forward block and go to the host helper (proves the
    # table consulted earlier didn't swallow an order-dependent action).
    monkeypatch.setenv("PAWFLOW_HOST_HELPER", "http://hh")
    seen = {}

    def fake_forward(hh, fwd, sock, send):
        seen["action"] = fwd.get("action")
        return {"ok": True, "data": {"fwd": True}}

    res = d.execute_command(
        _ctx(forward_to_host_helper=fake_forward),
        {"action": "start_local_desktop"})
    assert res == {"ok": True, "data": {"fwd": True}}
    assert seen["action"] == "start_local_desktop"


def test_service_tunnel_actions_require_dedicated_permission(monkeypatch):
    res = d.execute_command(
        _ctx(allow_service_tunnels=False),
        {"action": "service_tunnel_status", "tunnel_id": "t1", "role": "access"})
    assert res == {"ok": False, "error": "Service tunnels are disabled on this relay"}

    monkeypatch.setattr(
        d._service_tunnels, "handle_action",
        lambda action, message: {"running": True, "action": action})
    allowed = d.execute_command(
        _ctx(allow_service_tunnels=True),
        {"action": "service_tunnel_status", "tunnel_id": "t1", "role": "access"})
    assert allowed["ok"] is True
    assert allowed["data"]["running"] is True
