"""Process-local readiness state for the mandatory PawFlow runtime."""

import threading
from typing import Dict


_lock = threading.Lock()
_required_flow = ""
_ready = True
_failure = ""


def require_flow_readiness(instance_id: str) -> None:
    """Make health checks fail until ``instance_id`` passes startup checks."""
    global _required_flow, _ready, _failure
    with _lock:
        _required_flow = str(instance_id or "")
        _ready = not bool(_required_flow)
        _failure = ""


def mark_required_flow_ready(instance_id: str) -> None:
    """Mark the configured mandatory flow ready after strict validation."""
    global _ready, _failure
    with _lock:
        if _required_flow and str(instance_id or "") == _required_flow:
            _ready = True
            _failure = ""


def mark_required_flow_failed(instance_id: str, error: str = "") -> None:
    """Keep health unavailable after a mandatory-flow startup failure."""
    global _ready, _failure
    with _lock:
        if _required_flow and str(instance_id or "") == _required_flow:
            _ready = False
            _failure = str(error or "")


def health_snapshot() -> Dict[str, object]:
    """Return the public, non-sensitive readiness payload."""
    with _lock:
        if not _required_flow:
            return {"ok": True, "status": "ready"}
        return {
            "ok": _ready,
            "status": "ready" if _ready else "starting",
            "required_flow": _required_flow,
        }


def failure_detail() -> str:
    """Return the internal diagnostic retained for server logs."""
    with _lock:
        return _failure


def reset_runtime_readiness() -> None:
    """Return to phase-neutral health, primarily for installer rollback/tests."""
    global _required_flow, _ready, _failure
    with _lock:
        _required_flow = ""
        _ready = True
        _failure = ""
