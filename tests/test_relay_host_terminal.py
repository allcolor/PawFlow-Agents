import base64
import json
import shutil
import subprocess
import sys

from pawflow_relay import _thread_host


class _Pipe:
    def __init__(self, chunks=None):
        self.chunks = list(chunks or [])
        self.writes = []

    def read(self, _size):
        return self.chunks.pop(0)

    def write(self, data):
        self.writes.append(data)

    def flush(self):
        return None


class _Process:
    def __init__(self):
        self.stdin = _Pipe()
        self.stdout = _Pipe([b"local terminal ready\r\n", b""])
        self.killed = False

    def kill(self):
        self.killed = True


class _Connection:
    def __init__(self):
        self.sent = []
        self.closed = False

    def sendall(self, data):
        self.sent.append(data)

    def close(self):
        self.closed = True


def test_windows_local_terminal_falls_back_when_winpty_is_missing(
        monkeypatch, tmp_path):
    process = _Process()
    spawned = {}

    def _popen(command, **kwargs):
        spawned["command"] = command
        spawned["kwargs"] = kwargs
        return process

    monkeypatch.setattr(_thread_host.sys, "platform", "win32")
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    monkeypatch.setattr(subprocess, "Popen", _popen)
    monkeypatch.setitem(sys.modules, "winpty", None)

    helper = _thread_host._RelayHostHelperMixin()
    helper.directory = str(tmp_path)
    logs = []
    helper._log = logs.append
    conn = _Connection()

    helper._host_terminal_persistent(conn, {"cols": 100, "rows": 30})

    messages = [
        json.loads(line)
        for chunk in conn.sent
        for line in chunk.decode("utf-8").splitlines()
    ]
    assert spawned["command"] == ["cmd.exe"]
    assert spawned["kwargs"]["stdin"] is subprocess.PIPE
    assert messages[0]["type"] == "result"
    assert messages[0]["data"]["session_id"].startswith("local_term_")
    assert base64.b64decode(messages[1]["data"]["data"]) == (
        b"local terminal ready\r\n")
    assert messages[-1]["data"]["type"] == "terminal_exit"
    assert any("winpty unavailable" in message for message in logs)
    assert process.killed is True
    assert conn.closed is True
