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
import time
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


def _wait_until(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_a_command_runs_even_with_no_pool_configured():
    """The relay must never be the reason a command is late.

    With no ceiling configured there is no pool to wait for: a command runs on
    its own thread, so one long tool run cannot hold up everything else. A fixed
    pool of four made that happen -- other agents' calls failed with "Relay
    timeout for exec" while the relay was healthy, simply queued.
    """
    ran = threading.Event()
    sends = []

    def _exec(msg, on_output=None):
        ran.set()
        return {"data": {"ok": True}}

    s = ConnSession(_ctx([_cmd("read_file", "p1"), CLOSE], _sends=sends,
                         execute_command=_exec))
    assert s.pool is None
    s.run()

    assert ran.wait(5), "the command must run without a pool"
    assert _wait_until(lambda: s.inflight_cmds == {})


def test_a_close_waits_for_the_keystrokes_queued_before_it():
    """close_terminal used to run on a free thread and could overtake them."""
    order = []

    def _exec(msg, on_output=None):
        order.append(msg.get("action"))
        return {"data": {"ok": True}}

    s = ConnSession(_ctx([
        _cmd("write_terminal", "w1", session_id="t1", data="a"),
        _cmd("write_terminal", "w2", session_id="t1", data="b"),
        _cmd("close_terminal", "c1", session_id="t1"),
        CLOSE,
    ], execute_command=_exec))
    s.run()

    assert _wait_until(lambda: "close_terminal" in order)
    assert order == ["write_terminal", "write_terminal", "close_terminal"]


def test_closing_a_session_retires_its_fifo_and_its_thread():
    """Nothing used to stop the worker, or drop it from the map."""
    s = ConnSession(_ctx([
        _cmd("write_terminal", "w1", session_id="t1", data="a"),
        _cmd("close_terminal", "c1", session_id="t1"),
        CLOSE,
    ]))
    s.run()

    assert _wait_until(lambda: not s._term_io_queues)
    assert s._term_io_queues == {}
    assert _wait_until(
        lambda: not any(t.name == "relay-term-io-t1" for t in threading.enumerate()))


def test_a_keystroke_after_close_is_answered_instead_of_hanging():
    sends = []
    s = ConnSession(_ctx([
        _cmd("close_terminal", "c1", session_id="t1"),
        _cmd("write_terminal", "w1", session_id="t1", data="a"),
        CLOSE,
    ], _sends=sends))
    s.run()

    replies = [json.loads(f) for f, _op in sends if b'"type": "result"' in f]
    late = [r for r in replies if r.get("request_id") == "w1"]
    assert late, "the late keystroke must be answered, not queued behind the stop"
    assert late[0]["data"]["ok"] is False
    assert "closed" in late[0]["data"]["error"]
    assert s.inflight_cmds == {}
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
    # The pool runs a zero-argument wrapper: it reports a worker wait before
    # handing over to _run_command.
    assert len(submitted) == 1
    fn, args = submitted[0]
    assert args == ()
    # Tracked as inflight at submit time (the pool worker would pop it).
    assert "p1" in s.inflight_cmds
    assert s.inflight_cmds["p1"]["action"] == "read_file"
    # The wrapper runs the command and reports its result.
    sends = []
    s.ws_frame_send = lambda _s, _f, opcode=0x1: sends.append((_f, opcode))
    fn()
    results = [json.loads(f) for f, _op in sends if b'"type": "result"' in f]
    assert results[0]["request_id"] == "p1"
    assert s.inflight_cmds == {}


def test_terminal_io_keeps_its_order_per_session():
    """Keystrokes must reach the PTY in the order they were typed.

    Each one is its own command, and the server side sends them one after the
    other; without a per-session FIFO two of them (or the two halves of a paste)
    can land the wrong way round.
    """
    order = []

    class _Exec:
        def __call__(self, m, on_output=None):
            order.append(m["data"])
            return {"data": {"ok": True}}

    s = ConnSession(_ctx([CLOSE], execute_command=_Exec()))
    for chunk in ("a", "b", "c"):
        s._handle_command({
            "action": "write_terminal", "session_id": "sess-1",
            "request_id": f"w{chunk}", "data": chunk})

    s._term_io_queue("sess-1").join()
    assert order == ["a", "b", "c"]


def test_a_command_waiting_for_a_worker_says_so(monkeypatch, capfd):
    """A saturated pool must be visible instead of looking like a slow reply."""
    held = []

    class _Pool:
        def submit(self, fn, *args):
            held.append(fn)

    monkeypatch.setattr(ml, "_POOL_WAIT_LOG_SECONDS", 0.0)
    s = ConnSession(_ctx([CLOSE], pool=_Pool()))
    s._handle_command({"action": "read_file", "request_id": "p1"})

    held[0]()
    err = capfd.readouterr().err
    assert "read_file waited" in err
    assert "PAWFLOW_RELAY_COMMAND_WORKERS" in err


def test_an_interactive_action_never_waits_on_the_command_pool():
    """A terminal open must not queue behind long tool runs.

    The pool is a fixed number of workers, and the tool commands of a handful of
    agents keep every one of them busy for minutes: on a live relay an
    ``open_terminal`` queued behind them and took 188s and 97s, both returning
    the moment tool results freed a worker. Interactive ops get their own
    thread instead -- a lane that could still queue would only move the
    starvation.
    """
    submitted = []

    class _Pool:
        def submit(self, fn, *args):
            submitted.append(args[0].get("action"))

    ran = []
    s = ConnSession(_ctx(
        [_cmd("open_terminal", request_id="t1"), CLOSE], pool=_Pool(),
        execute_command=lambda _m, on_output=None: (
            ran.append(_m["action"]) or {"data": {"ok": True}})))

    s.run()

    for _ in range(50):
        if ran:
            break
        time.sleep(0.02)
    assert ran == ["open_terminal"]
    assert submitted == []          # never handed to the shared pool


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
