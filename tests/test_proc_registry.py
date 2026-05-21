"""Tests for pawflow_relay.proc_registry — in-flight subprocess registry.

The registry lets the server's `cancel_request` envelope reach the
actual `subprocess.Popen` a tool action spawned, so FORCE STOP
actually terminates the workload instead of leaving a phantom thread
running side-effecting code.
"""

import subprocess
import sys
import threading
import time

import pytest

from pawflow_relay.proc_registry import (
    register_inflight_proc,
    unregister_inflight_proc,
    kill_inflight_proc,
    inflight_count,
    _inflight_procs,
)

from tools.fs_common import run_cancellable


@pytest.fixture(autouse=True)
def _clean_registry():
    _inflight_procs.clear()
    yield
    _inflight_procs.clear()


def _spawn_sleep(seconds: float = 30) -> subprocess.Popen:
    """Spawn a sleeping subprocess we can kill during the test."""
    return subprocess.Popen(
        [sys.executable, "-c", f"import time; time.sleep({seconds})"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


class TestRegistration:

    def test_register_then_count_increments(self):
        proc = _spawn_sleep()
        try:
            register_inflight_proc("r1", proc)
            assert inflight_count() == 1
        finally:
            proc.terminate()
            proc.wait()

    def test_unregister_drops_entry(self):
        proc = _spawn_sleep()
        try:
            register_inflight_proc("r1", proc)
            unregister_inflight_proc("r1")
            assert inflight_count() == 0
        finally:
            proc.terminate()
            proc.wait()

    def test_register_with_empty_request_id_is_noop(self):
        proc = _spawn_sleep()
        try:
            register_inflight_proc("", proc)
            assert inflight_count() == 0
        finally:
            proc.terminate()
            proc.wait()

    def test_register_with_none_proc_is_noop(self):
        register_inflight_proc("r1", None)
        assert inflight_count() == 0

    def test_unregister_unknown_id_is_noop(self):
        unregister_inflight_proc("never-registered")  # must not raise


class TestKill:

    def test_kill_terminates_proc(self):
        proc = _spawn_sleep()
        register_inflight_proc("r1", proc)
        ok = kill_inflight_proc("r1")
        assert ok is True
        # Process should be dead within a short window
        proc.wait(timeout=5)
        assert proc.returncode is not None

    def test_kill_drops_registration(self):
        proc = _spawn_sleep()
        register_inflight_proc("r1", proc)
        kill_inflight_proc("r1")
        assert inflight_count() == 0

    def test_kill_unknown_id_returns_false(self):
        assert kill_inflight_proc("never") is False

    def test_double_kill_is_noop(self):
        proc = _spawn_sleep()
        register_inflight_proc("r1", proc)
        assert kill_inflight_proc("r1") is True
        # Second call: registration was popped on first kill
        assert kill_inflight_proc("r1") is False
        proc.wait(timeout=5)

    def test_kill_unblocks_blocked_thread(self):
        """The point of the registry: a thread blocked on proc.wait()
        must return as soon as kill_inflight_proc fires."""
        proc = _spawn_sleep(seconds=30)
        register_inflight_proc("r1", proc)
        result = []

        def _waiter():
            proc.wait()
            result.append("unblocked")

        t = threading.Thread(target=_waiter, daemon=True)
        t.start()
        time.sleep(0.1)
        assert not result, "waiter unblocked too soon"
        kill_inflight_proc("r1")
        t.join(timeout=5)
        assert result == ["unblocked"]

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX process group behavior")
    def test_run_cancellable_timeout_kills_child_holding_pipe(self):
        cmd = [
            sys.executable,
            "-c",
            (
                "import subprocess, sys, time; "
                "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(3)']); "
                "time.sleep(3)"
            ),
        ]

        started = time.monotonic()
        with pytest.raises(subprocess.TimeoutExpired):
            run_cancellable(
                "timeout-child-pipe",
                cmd,
                capture_output=True,
                text=True,
                timeout=0.2,
            )

        assert time.monotonic() - started < 2.0


class TestIsolation:

    def test_kill_only_affects_targeted_request(self):
        proc1 = _spawn_sleep()
        proc2 = _spawn_sleep()
        try:
            register_inflight_proc("r1", proc1)
            register_inflight_proc("r2", proc2)
            kill_inflight_proc("r1")
            assert inflight_count() == 1
            assert proc2.poll() is None  # still running
        finally:
            proc1.terminate(); proc2.terminate()
            proc1.wait(); proc2.wait()
