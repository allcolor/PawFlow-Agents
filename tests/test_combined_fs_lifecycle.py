"""Unit tests for the out-of-process combined server-fs FUSE lifecycle.

Covers the relay <-> responder link (pawflow_relay.fuse_responder), the
relay-side supervisor (pawflow_relay.combined_fs) and the setup wiring. No
FUSE mount is needed here; tests/test_combined_fs_fuse_integration.py drives
the real kernel path.
"""
import errno
import socket
import struct
import subprocess
import threading
import time

import pytest

from pawflow_relay import combined_fs, fuse_responder
from pawflow_relay.combined_fs import CombinedServerFsMount
from pawflow_relay.fuse_responder import (
    ParentLink, fuse_connection_id, recv_frame, send_frame,
)


# ── Framing ──────────────────────────────────────────────────────────

def test_frame_roundtrip_and_clean_eof():
    a, b = socket.socketpair()
    lock = threading.Lock()
    send_frame(a, lock, {"type": "req", "id": 1, "args": {"p": "é"}})
    assert recv_frame(b) == {"type": "req", "id": 1, "args": {"p": "é"}}
    a.close()
    assert recv_frame(b) is None


def test_truncated_frame_is_end_of_stream():
    a, b = socket.socketpair()
    a.sendall(struct.pack("!I", 10) + b"{\"x\"")
    a.close()
    assert recv_frame(b) is None


def test_oversized_frame_is_refused_both_ways():
    a, b = socket.socketpair()
    a.sendall(struct.pack("!I", fuse_responder.MAX_FRAME + 1))
    with pytest.raises(ValueError):
        recv_frame(b)
    with pytest.raises(ValueError):
        send_frame(a, threading.Lock(),
                   {"blob": "x" * (fuse_responder.MAX_FRAME + 1)})


# ── Connection identification ───────────────────────────────────────

_MOUNTINFO = (
    "22 1 8:1 / / rw,relatime - ext4 /dev/sda1 rw\n"
    "489 542 0:63 / /tmp/pf_combined_fs rw,nosuid,nodev,relatime - fuse "
    "pawflow-combined-fs rw,user_id=1001,group_id=1001,default_permissions\n"
    "490 542 0:64 / /tmp/other rw - fuse sshfs rw\n"
)


def _mountinfo(tmp_path, text=_MOUNTINFO):
    path = tmp_path / "mountinfo"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_connection_id_is_the_device_minor_of_our_mount(tmp_path):
    info = _mountinfo(tmp_path)
    assert fuse_connection_id("/tmp/pf_combined_fs", "pawflow-combined-fs",
                              info) == 63


def test_connection_id_requires_mountpoint_and_fsname_to_match(tmp_path):
    info = _mountinfo(tmp_path)
    assert fuse_connection_id("/tmp/other", "pawflow-combined-fs", info) is None
    assert fuse_connection_id("/tmp/pf_combined_fs", "sshfs", info) is None
    assert fuse_connection_id("/", "/dev/sda1", info) is None
    assert fuse_connection_id("/x", "y", str(tmp_path / "missing")) is None


# ── Responder side: ParentLink ──────────────────────────────────────

def _echo_relay(sock, stop_after=None):
    """Answer every req with its method name, like a healthy relay."""
    lock = threading.Lock()
    seen = 0
    while True:
        frame = recv_frame(sock)
        if frame is None:
            return
        if frame["type"] == "req":
            send_frame(sock, lock, {"type": "rep", "id": frame["id"],
                                    "reply": {"data": frame["method"]}})
            seen += 1
            if stop_after and seen >= stop_after:
                return


def test_parent_link_forwards_request_and_reply():
    child, relay = socket.socketpair()
    link = ParentLink(child)
    threading.Thread(target=link.serve, daemon=True).start()
    threading.Thread(target=_echo_relay, args=(relay,), daemon=True).start()
    assert link.request("sfs.readdir", {"path": "/"}, 5.0) == {"data": "sfs.readdir"}


def test_relay_gone_fails_pending_and_later_requests_with_eio_at_once():
    child, relay = socket.socketpair()
    link = ParentLink(child)
    threading.Thread(target=link.serve, daemon=True).start()
    results = []
    t = threading.Thread(target=lambda: results.append(
        link.request("sfs.readdir", {"path": "/"}, 60.0)))
    t.start()
    assert recv_frame(relay)["method"] == "sfs.readdir"  # request is in flight
    started = time.monotonic()
    relay.close()  # the relay dies without answering
    t.join(5)
    assert not t.is_alive()
    assert time.monotonic() - started < 2
    assert results[0]["errno"] == errno.EIO
    assert link.eof.is_set()
    assert link.request("sfs.getattr", {}, 60.0)["errno"] == errno.EIO


def test_silent_relay_is_bounded_by_timeout_plus_grace(monkeypatch):
    monkeypatch.setattr(fuse_responder, "REPLY_GRACE", 0.2)
    child, relay = socket.socketpair()
    link = ParentLink(child)
    threading.Thread(target=link.serve, daemon=True).start()
    started = time.monotonic()
    reply = link.request("sfs.readdir", {"path": "/"}, 0.3)
    assert reply["errno"] == errno.EIO
    assert 0.4 <= time.monotonic() - started < 2
    relay.close()


def test_ping_is_answered_with_loop_age():
    child, relay = socket.socketpair()
    link = ParentLink(child)
    link.loop_tick = time.monotonic() - 42
    threading.Thread(target=link.serve, daemon=True).start()
    send_frame(relay, threading.Lock(), {"type": "ping"})
    pong = recv_frame(relay)
    assert pong["type"] == "pong"
    assert 41 < pong["loop_age"] < 60


# ── Relay side: supervisor ──────────────────────────────────────────

class _Client:
    def __init__(self, name):
        self.name = name
        self.calls = []

    def request(self, method, args, timeout=None):
        self.calls.append((method, args, timeout))
        return {"data": {"backend": self.name}}


def _served_mount():
    clients = [_Client("sfs"), _Client("ffs"), _Client("skfs")]
    mount = CombinedServerFsMount("/nonexistent/mnt", *clients)
    mount._pool = combined_fs.ThreadPoolExecutor(max_workers=4)
    relay, child = socket.socketpair()
    threading.Thread(target=mount._serve, args=(relay, threading.Lock()),
                     daemon=True).start()
    return mount, clients, child


def _ask(child, req_id, method):
    send_frame(child, threading.Lock(), {"type": "req", "id": req_id,
                                         "method": method, "args": {},
                                         "timeout": 5.0})
    frame = recv_frame(child)
    assert frame["type"] == "rep" and frame["id"] == req_id
    return frame["reply"]


def test_supervisor_routes_each_subtree_to_its_client():
    mount, clients, child = _served_mount()
    assert _ask(child, 1, "sfs.readdir") == {"data": {"backend": "sfs"}}
    assert _ask(child, 2, "ffs.getattr") == {"data": {"backend": "ffs"}}
    assert _ask(child, 3, "skfs.read") == {"data": {"backend": "skfs"}}
    assert clients[0].calls == [("sfs.readdir", {}, 5.0)]
    assert _ask(child, 4, "bogus.op")["errno"] == errno.EIO


def test_supervisor_refuses_backend_work_once_stopping():
    mount, clients, child = _served_mount()
    mount._stopping.set()
    reply = _ask(child, 1, "sfs.readdir")
    assert reply["errno"] == errno.EIO and reply["message"] == "relay stopping"
    assert clients[0].calls == []


def test_pong_refreshes_health():
    mount, _clients, child = _served_mount()
    mount._last_pong = 0.0
    send_frame(child, threading.Lock(), {"type": "pong", "loop_age": 1.5})
    deadline = time.monotonic() + 2
    while mount._last_pong == 0.0 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert mount._last_pong > 0.0 and mount._loop_age == 1.5


class _Proc:
    """Popen stand-in; `exits_after` wait() calls time out before exit."""

    pid = 4242

    def __init__(self, log, exits_after=0):
        self.log = log
        self.timeouts_left = exits_after

    def wait(self, timeout=None):
        if self.timeouts_left:
            self.timeouts_left -= 1
            raise subprocess.TimeoutExpired("responder", timeout)
        self.log.append("reaped")
        return 0

    def kill(self):
        self.log.append("kill")

    def poll(self):
        return 0


class _Sock:
    def __init__(self, log):
        self.log = log

    def shutdown(self, how):
        self.log.append("eof")

    def close(self):
        self.log.append("close")


def _stoppable(monkeypatch, exits_after):
    log = []
    mount = CombinedServerFsMount("/nonexistent/mnt", None, None, None)
    mount._proc, mount._sock = _Proc(log, exits_after), _Sock(log)
    mount._connection = 63
    monkeypatch.setattr(mount, "_try_unmount",
                        lambda silent: log.append("unmount"))
    monkeypatch.setattr(mount, "_abort_connection",
                        lambda conn: log.append(f"abort:{conn}"))
    return mount, log


def test_stop_detaches_before_ending_the_responder(monkeypatch):
    mount, log = _stoppable(monkeypatch, exits_after=0)
    mount.stop()
    assert log == ["unmount", "eof", "reaped", "close"]
    assert mount._stopping.is_set()


def test_stop_kills_a_responder_that_ignores_end_of_stream(monkeypatch):
    mount, log = _stoppable(monkeypatch, exits_after=1)
    mount.stop()
    assert log == ["unmount", "eof", "kill", "reaped", "close"]


def test_stop_aborts_our_connection_only_when_kill_fails(monkeypatch):
    mount, log = _stoppable(monkeypatch, exits_after=2)
    mount.stop()
    assert log == ["unmount", "eof", "kill", "abort:63", "close"]


def test_stop_is_idempotent(monkeypatch):
    mount, log = _stoppable(monkeypatch, exits_after=0)
    mount.stop()
    mount.stop()
    assert log.count("unmount") == 1


def test_abort_writes_only_the_recorded_connection(monkeypatch):
    written = {}

    class _File:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def write(self, data):
            written[self.path] = data

    monkeypatch.setattr(combined_fs.os.path, "exists", lambda p: True)
    monkeypatch.setattr(combined_fs, "open", lambda p, *a, **k: _File(p),
                        raising=False)
    mount = CombinedServerFsMount("/nonexistent/mnt", None, None, None)
    mount._abort_connection(63)
    assert written == {"/sys/fs/fuse/connections/63/abort": "1"}
    mount._abort_connection(None)  # unknown connection: nothing touched
    assert len(written) == 1


def test_unmount_is_lazy_and_never_stats_the_mountpoint(monkeypatch):
    calls = []
    monkeypatch.setattr(combined_fs.os.path, "exists",
                        lambda p: pytest.fail("stat of a FUSE mountpoint"))
    monkeypatch.setattr(combined_fs.subprocess, "run",
                        lambda cmd, **k: calls.append(cmd) or
                        subprocess.CompletedProcess(cmd, 0))
    CombinedServerFsMount("/m", None, None, None)._try_unmount(silent=True)
    assert calls == [["fusermount3", "-u", "-z", "/m"]]


# ── Setup wiring ────────────────────────────────────────────────────

def test_setup_stops_the_mount_at_interpreter_exit(monkeypatch):
    """SIGTERM ends the worker with sys.exit(0), which never reached the
    KeyboardInterrupt-only stop(); atexit covers every exit path."""
    from pawflow_relay import _relay_fs_setup

    registered, started = [], []

    class _Mount:
        def __init__(self, *a, **k):
            pass

        def start(self):
            started.append(True)

        def stop(self):
            pass

    monkeypatch.setattr(combined_fs, "CombinedServerFsMount", _Mount)
    monkeypatch.setattr(_relay_fs_setup.atexit, "register", registered.append)
    monkeypatch.setattr(_relay_fs_setup.subprocess, "run",
                        lambda cmd, **k: subprocess.CompletedProcess(cmd, 0, "", ""))
    out = _relay_fs_setup.setup_combined_fs("/nonexistent/cc_sessions", "", "")
    mount = out[3]
    assert started == [True]
    assert registered == [mount.stop]
