"""The operator's kill lever stops every in-flight relay call.

Quentin asked for a button in Relay Desktop that kills the calls in progress,
because a relay busy with calls that no longer have a reason to run has no way
to be unblocked from the app. The app cannot signal the worker on Windows, so it
leaves a request file in the runtime root the worker already shares with it; the
worker polls it once a second and writes back how many calls it killed.
"""

import os
import tempfile
import threading
import time

from pawflow_relay import proc_registry, worker


class _FakeProc:
    def __init__(self):
        self.pid = 4242
        self.terminated = False
        self.killed = False
        self.waited = False

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        self.waited = True
        return 0


def test_kill_all_inflight_stops_every_registered_proc():
    first, second = _FakeProc(), _FakeProc()
    proc_registry.register_inflight_proc("r1", first)
    proc_registry.register_inflight_proc("r2", second)
    try:
        assert proc_registry.kill_all_inflight() == 2
        assert proc_registry.inflight_count() == 0
        assert (first.terminated or first.killed)
        assert (second.terminated or second.killed)
        # A repeat request is a no-op, never an error.
        assert proc_registry.kill_all_inflight() == 0
    finally:
        proc_registry.kill_all_inflight()


def test_the_watcher_kills_on_request_and_writes_the_result(monkeypatch):
    with tempfile.TemporaryDirectory() as root:
        monkeypatch.setenv("PAWFLOW_RELAY_RUNTIME_ROOT", root)
        proc = _FakeProc()
        proc_registry.register_inflight_proc("r1", proc)
        stop = threading.Event()
        thread = threading.Thread(
            target=worker._watch_operator_kill, args=(stop,), daemon=True)
        thread.start()
        try:
            request = worker._operator_kill_path()
            assert request is not None
            request.write_text("relay\n", encoding="utf-8")
            result = worker._operator_kill_path(
                worker._OPERATOR_KILL_RESULT_FILE)
            deadline = time.time() + 10
            while time.time() < deadline and not result.exists():
                time.sleep(0.05)
            assert result.exists(), "the watcher must report what it killed"
            assert result.read_text(encoding="utf-8").strip() == "1"
            assert (proc.terminated or proc.killed)
            assert not request.exists(), "the request is consumed once honoured"
        finally:
            stop.set()
            proc_registry.kill_all_inflight()


def test_no_runtime_root_means_no_watcher(monkeypatch):
    monkeypatch.delenv("PAWFLOW_RELAY_RUNTIME_ROOT", raising=False)
    assert worker._operator_kill_path() is None
    # The watcher returns immediately instead of polling a path it does not have.
    worker._watch_operator_kill(threading.Event())
