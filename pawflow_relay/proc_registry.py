"""Per-relay registry of in-flight subprocesses, keyed by request_id.

Why a separate module: `pawflow_relay.worker` imports the action handlers
(`fs_actions`, `fs_exec`, ...) at dispatch time, and those handlers need
to register their `subprocess.Popen` so the server's `cancel_request`
envelope can terminate them. If we put the registry inside `worker.py`,
the handlers would have to import `worker`, creating a cycle. Living in
its own stdlib-only module breaks that.

Usage in an action that spawns a process:

    from pawflow_relay.proc_registry import (
        register_inflight_proc, unregister_inflight_proc,
    )

    request_id = req.get("request_id", "")
    proc = subprocess.Popen(...)
    register_inflight_proc(request_id, proc)
    try:
        ... wait / read ...
    finally:
        unregister_inflight_proc(request_id)

The worker's main loop calls `kill_inflight_proc(request_id)` when it
receives a `cancel_request` envelope from the server.
"""
import logging
import os
import signal

import subprocess  # nosec B404
import threading
from typing import Any, Dict

_inflight_procs_lock = threading.Lock()
_inflight_procs: Dict[str, Any] = {}  # request_id → subprocess.Popen


def register_inflight_proc(request_id: str, proc) -> None:
    """Register a Popen so the server can kill it via cancel_request.

    Idempotent and safe to call without a request_id (no-op).
    """
    if not request_id or proc is None:
        return
    with _inflight_procs_lock:
        _inflight_procs[request_id] = proc


def unregister_inflight_proc(request_id: str) -> None:
    """Drop the registration. Call from the action's `finally` block so
    a normal exit never leaves a stale entry around."""
    if not request_id:
        return
    with _inflight_procs_lock:
        _inflight_procs.pop(request_id, None)


def kill_inflight_proc(request_id: str) -> bool:
    """Terminate the proc registered for request_id. Returns True on hit.

    SIGTERM, wait up to 2s, then SIGKILL. Always pops the entry so a
    repeat cancel_request becomes a no-op. The action thread blocked on
    `proc.wait()` returns as soon as the process dies.
    """
    with _inflight_procs_lock:
        proc = _inflight_procs.pop(request_id, None)
    if proc is None:
        return False
    try:
        if os.name == "posix":
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except Exception:
                proc.terminate()
        else:
            proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                if os.name == "posix":
                    os.killpg(proc.pid, signal.SIGKILL)
                else:
                    proc.kill()
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
    except Exception:
        return False
    return True


def inflight_count() -> int:
    """Diagnostic hook — number of procs currently registered."""
    with _inflight_procs_lock:
        return len(_inflight_procs)
