import threading
import time

from tasks.ai.agent_poller import AgentPollerMixin


def test_checkpoint_cleanup_runs_in_background(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    calls = []

    def cleanup_old(days=30):
        calls.append(days)
        started.set()
        release.wait(timeout=5.0)
        finished.set()
        return 0

    monkeypatch.setattr(
        "core.checkpoint.CheckpointManager.cleanup_old",
        staticmethod(cleanup_old),
    )

    poller = AgentPollerMixin()
    t0 = time.monotonic()
    poller._maybe_cleanup_checkpoints_async()
    elapsed_ms = (time.monotonic() - t0) * 1000.0

    assert elapsed_ms < 50.0
    assert started.wait(timeout=1.0)
    assert calls == [30]

    poller._maybe_cleanup_checkpoints_async()
    assert calls == [30]

    release.set()
    assert finished.wait(timeout=1.0)
    for _ in range(100):
        if not getattr(poller, "_checkpoint_cleanup_running", False):
            break
        time.sleep(0.01)
    assert getattr(poller, "_checkpoint_cleanup_running", False) is False
