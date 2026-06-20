"""Behavioral tests for the relay per-connection message router (ConnSession).

Drives ConnSession.run() with a fake recv driver and stub connection
resources to cover the routing paths that used to live inline in
``_ws_connect`` and had no test coverage: keepalive ping on timeout, WS
ping->pong, server close-frame break, and dispatch of relay_response /
cancel_request / terminal_input / terminal_resize / command (both the inline
cs_ws_* path and the thread-pool path).
"""
import json
import socket
import struct
import threading
import types

from pawflow_relay import _relay_msg_loop as ml
from pawflow_relay._relay_msg_loop import ConnContext, ConnSession


def _frames_recv(frames):
    """Build a ws_frame_recv stub that yields queued (opcode, payload) items.

    A queued BaseException is raised instead of returned (to simulate a
    socket.timeout or transport error). Exhausting the queue raises
    StopIteration, which would escape run() — every test ends its queue with
    a close frame so run() returns cleanly first.
    """
    it = iter(frames)

    def _recv(_sock):
        item = next(it)
        if isinstance(item, BaseException):
            raise item
        return item
    return _recv


CLOSE = (0x08, struct.pack("!H", 1000) + b"bye")


def _ctx(frames, **over):
    sends = over.pop("_sends", [])
    base = dict(
        sock=object(),
        send_lock=threading.Lock(),
        ws_frame_send=lambda _s, _f, opcode=0x1: sends.append((_f, opcode)),
        ws_frame_recv=_frames_recv(frames),
        socket_diag={},
        last_activity=[0.0],
        pool=None,
        execute_command=lambda _m, on_output=None: {"data": {"ok": True}},
        term_mgr=types.SimpleNamespace(sessions={}),
        children=types.SimpleNamespace(
            handle_spawn=lambda *a: None, handle_stop=lambda *a: None),
        child_cfg=object(),
        term_send=lambda _f: None,
        fuse_clients=(None, None, None),
        remote_mount_mgr=None,
        resolve_spawn_docker_env=lambda: object(),
    )
    base.update(over)
    return ConnContext(**base)


def _cmd(action, request_id="r1", **extra):
    msg = {"type": "command", "action": action, "request_id": request_id}
    msg.update(extra)
    return (0x01, json.dumps(msg).encode("utf-8"))


def test_close_frame_breaks_and_returns_reason():
    s = ConnSession(_ctx([CLOSE]))
    reason = s.run()
    assert reason.startswith("server close frame")
    assert "code=1000" in reason


def test_timeout_sends_keepalive_ping_then_continues():
    sends = []
    s = ConnSession(_ctx([socket.timeout(), CLOSE], _sends=sends))
    s.run()
    pings = [f for f, op in sends if b'"type": "ping"' in f]
    assert len(pings) == 1


def test_ping_send_failure_breaks_loop():
    def _boom(_s, _f, opcode=0x1):
        raise OSError("socket dead")
    s = ConnSession(_ctx([socket.timeout()], ws_frame_send=_boom))
    reason = s.run()
    assert reason.startswith("ping send failed")
    assert s.socket_diag["last_send_error"].startswith("ping:")


def test_ws_ping_opcode_replies_pong():
    sends = []
    s = ConnSession(_ctx([(0x09, b"hb"), CLOSE], _sends=sends))
    s.run()
    pongs = [(f, op) for f, op in sends if op == 0x0A]
    assert pongs == [(b"hb", 0x0A)]


def test_relay_response_routed_to_owning_fuse_client():
    calls = []

    class _FsClient:
        def __init__(self, owns):
            self._owns = owns

        def dispatch_response(self, msg):
            calls.append((self._owns, msg.get("request_id")))
            return self._owns
    not_mine, mine = _FsClient(False), _FsClient(True)
    frame = (0x01, json.dumps(
        {"type": "relay_response", "request_id": "x9"}).encode("utf-8"))
    s = ConnSession(_ctx([frame, CLOSE], fuse_clients=(not_mine, mine, None)))
    s.run()
    # First client tried (returns False), second owns it and stops the chain.
    assert calls == [(False, "x9"), (True, "x9")]


def test_cancel_request_kills_inflight_proc(monkeypatch):
    killed = []
    monkeypatch.setattr(ml, "kill_inflight_proc",
                        lambda rid: killed.append(rid) or True)
    frame = (0x01, json.dumps(
        {"type": "cancel_request", "request_id": "k7"}).encode("utf-8"))
    ConnSession(_ctx([frame, CLOSE])).run()
    assert killed == ["k7"]


def test_terminal_input_and_resize_go_to_term_mgr():
    writes, resizes = [], []
    term = types.SimpleNamespace(
        sessions={"t1": object()},
        write=lambda tid, data: (writes.append((tid, data)) or (True, "")),
        resize=lambda tid, cols, rows: resizes.append((tid, cols, rows)))
    frames = [
        (0x01, json.dumps({"type": "terminal_input",
                           "session_id": "t1", "data": "ls\n"}).encode()),
        (0x01, json.dumps({"type": "terminal_resize", "session_id": "t1",
                           "cols": 120, "rows": 40}).encode()),
        CLOSE,
    ]
    ConnSession(_ctx(frames, term_mgr=term)).run()
    assert writes == [("t1", "ls\n")]
    assert resizes == [("t1", 120, 40)]


def test_terminal_input_ignored_for_unknown_session():
    writes = []
    term = types.SimpleNamespace(
        sessions={},
        write=lambda tid, data: writes.append((tid, data)) or (True, ""))
    frame = (0x01, json.dumps(
        {"type": "terminal_input", "session_id": "ghost", "data": "x"}).encode())
    ConnSession(_ctx([frame, CLOSE], term_mgr=term)).run()
    assert writes == []


def test_command_cs_ws_runs_inline_and_sends_result():
    sends = []
    seen = []
    s = ConnSession(_ctx(
        [_cmd("cs_ws_send", request_id="c1"), CLOSE],
        _sends=sends,
        execute_command=lambda m, on_output=None: seen.append(m["action"]) or {
            "data": {"ok": True}}))
    s.run()
    assert seen == ["cs_ws_send"]
    results = [json.loads(f) for f, op in sends if b'"type": "result"' in f]
    assert results and results[0]["request_id"] == "c1"
    # Inline path never tracks the request as inflight.
    assert s.inflight_cmds == {}


def test_command_normal_submits_to_pool_and_tracks_inflight():
    submitted = []

    class _Pool:
        def submit(self, fn, *args):
            submitted.append((fn, args))
    s = ConnSession(_ctx([_cmd("read_file", request_id="p1"), CLOSE],
                         pool=_Pool()))
    s.run()
    assert len(submitted) == 1
    fn, args = submitted[0]
    assert fn == s._run_command
    # msg, request_id, sock, send_fn
    assert args[1] == "p1"
    # Tracked as inflight at submit time (the pool worker would pop it).
    assert "p1" in s.inflight_cmds
    assert s.inflight_cmds["p1"]["action"] == "read_file"


def test_run_command_executes_sends_result_and_clears_inflight():
    sends = []
    s = ConnSession(_ctx([CLOSE], _sends=sends))
    s.inflight_cmds["p2"] = {"action": "read_file", "ts": 0.0}
    s._run_command({"action": "read_file", "request_id": "p2"},
                   "p2", s.sock, s.ws_frame_send)
    results = [json.loads(f) for f, op in sends if b'"type": "result"' in f]
    assert results[0]["request_id"] == "p2"
    assert s.inflight_cmds == {}  # cleared in finally


def test_run_command_reports_error_in_result():
    sends = []

    def _boom(_m, on_output=None):
        raise RuntimeError("kaboom")
    s = ConnSession(_ctx([CLOSE], _sends=sends, execute_command=_boom))
    s._run_command({"action": "read_file", "request_id": "p3"},
                   "p3", s.sock, s.ws_frame_send)
    results = [json.loads(f) for f, op in sends if b'"type": "result"' in f]
    assert results[0]["data"]["ok"] is False
    assert "kaboom" in results[0]["data"]["error"]


def test_active_cmd_summary_reports_inflight():
    s = ConnSession(_ctx([CLOSE]))
    assert s.active_cmd_summary() == "none"
    s.inflight_cmds["abcd1234ef"] = {"action": "exec", "ts": 0.0}
    summary = s.active_cmd_summary()
    assert "exec:abcd1234" in summary


def test_spawn_relay_uses_worker_supplied_docker_env():
    spawned = []
    sentinel = object()
    children = types.SimpleNamespace(
        handle_spawn=lambda msg, cfg, env, send: spawned.append(env),
        handle_stop=lambda *a: None)
    frame = (0x01, json.dumps({"type": "spawn_relay"}).encode("utf-8"))
    s = ConnSession(_ctx([frame, CLOSE], children=children,
                         resolve_spawn_docker_env=lambda: sentinel))
    s.run()
    assert spawned == [sentinel]


def test_remote_mount_manifest_reconciles(monkeypatch):
    reconciled = []
    done = threading.Event()
    mgr = types.SimpleNamespace(
        reconcile=lambda m: (reconciled.append(m), done.set()))
    frame = (0x01, json.dumps(
        {"type": "remote_mount_manifest",
         "manifest": {"a": 1}}).encode("utf-8"))
    s = ConnSession(_ctx([frame, CLOSE], remote_mount_mgr=mgr))
    s.run()
    assert done.wait(2.0)
    assert reconciled == [{"a": 1}]
