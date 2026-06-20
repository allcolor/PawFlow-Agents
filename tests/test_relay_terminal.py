"""Behavioral tests for the relay PTY TerminalManager.

These exercise a real forkpty + shell round-trip (open -> write -> read
output -> close), giving the terminal domain genuine runtime coverage
before/while it is split out of worker.py. Unix-only.
"""
import base64
import json
import os
import threading
import time


import pytest

from pawflow_relay._relay_terminal import TerminalManager

pytestmark = pytest.mark.skipif(
    not hasattr(os, "forkpty"), reason="PTY terminals require os.forkpty (Unix only)"
)


class _Sink:
    """Thread-safe collector for frames emitted by reader threads."""

    def __init__(self):
        self._lock = threading.Lock()
        self.frames = []

    def __call__(self, frame_bytes):
        with self._lock:
            self.frames.append(json.loads(frame_bytes.decode("utf-8")))

    def snapshot(self):
        with self._lock:
            return list(self.frames)

    def terminal_data_text(self, session_id):
        out = b""
        for f in self.snapshot():
            if f.get("type") == "terminal_data" and f.get("session_id") == session_id:
                out += base64.b64decode(f["data"])
        return out

    def has_exit(self, session_id):
        return any(
            f.get("type") == "terminal_exit" and f.get("session_id") == session_id
            for f in self.snapshot()
        )


def _wait(predicate, timeout=5.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _b64(s):
    return base64.b64encode(s.encode("utf-8")).decode("ascii")


def test_open_write_read_close_roundtrip(tmp_path):
    sink = _Sink()
    mgr = TerminalManager(str(tmp_path), sink)

    sid = mgr.open(cols=80, rows=24, shell="/bin/sh")
    assert sid in mgr.sessions
    assert mgr.list() == [{"session_id": sid, "shell": "/bin/sh"}]

    ok, err = mgr.write(sid, _b64("echo pawflow_marker_123\n"))
    assert ok and err == ""

    assert _wait(lambda: b"pawflow_marker_123" in sink.terminal_data_text(sid)), (
        "shell echo output never streamed back"
    )

    assert mgr.close(sid) is True
    assert sid not in mgr.sessions
    # reader thread observes EOF and emits a terminal_exit frame
    assert _wait(lambda: sink.has_exit(sid)), "no terminal_exit frame after close"


def test_write_to_unknown_session_errors():
    mgr = TerminalManager("/tmp", lambda _f: None)  # nosec B108
    ok, err = mgr.write("nope", _b64("x"))
    assert ok is False
    assert "not found" in err


def test_resize_unknown_session_errors():
    mgr = TerminalManager("/tmp", lambda _f: None)  # nosec B108
    ok, err = mgr.resize("nope", cols=100, rows=40)
    assert ok is False
    assert "not found" in err


def test_resize_open_session(tmp_path):
    mgr = TerminalManager(str(tmp_path), _Sink())
    sid = mgr.open(shell="/bin/sh")
    try:
        ok, err = mgr.resize(sid, cols=120, rows=40)
        assert ok and err == ""
    finally:
        mgr.close(sid)


def test_close_unknown_returns_false():
    mgr = TerminalManager("/tmp", lambda _f: None)  # nosec B108
    assert mgr.close("nope") is False


def test_close_all_clears_sessions(tmp_path):
    sink = _Sink()
    mgr = TerminalManager(str(tmp_path), sink)
    sids = [mgr.open(shell="/bin/sh") for _ in range(3)]
    assert len(mgr.sessions) == 3
    mgr.close_all()
    assert mgr.sessions == {}
    for sid in sids:
        assert _wait(lambda s=sid: sink.has_exit(s))
