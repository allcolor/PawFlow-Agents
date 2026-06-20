"""Behavioral tests for the relay per-message dispatcher (_relay_dispatch).

Drives execute_command with a fake DispatchCtx through the routing paths
that don't spawn real processes: readonly rejection, unknown action,
local=True host-forward, terminal open/list via a fake manager, and the
allow_exec gate. First execution coverage of the dispatch routing.
"""
import sys
import types
from pathlib import Path

import pytest

# tools/ on path so the dispatcher's lazy `from fs_actions import ACTIONS`
# (only hit on the generic fall-through) resolves like in the relay container.
sys.path.append(str(Path(__file__).resolve().parent.parent / "tools"))

from pawflow_relay import _relay_dispatch as d


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
