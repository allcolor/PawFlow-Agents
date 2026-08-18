"""Route asynchronous tool results back to their external transport caller.

Published MCP calls execute with a PawFlow agent configuration for capability
resolution, but the configured agent is not the caller.  This module keeps that
distinction explicit: handlers can resolve the current external owner, attach
task ids to it, and consume late results without waking a PawFlow agent.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import threading
import time
from typing import Any, Dict, Iterable, Iterator, List, Optional, Set


_RETENTION_SECONDS = 8 * 3600


@dataclass
class _ExternalCall:
    call_id: str
    conversation_id: str
    source_id: str
    display_name: str
    llm_service: str
    created_at: float = field(default_factory=time.time)
    expected: List[str] = field(default_factory=list)
    results: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    event: threading.Event = field(default_factory=threading.Event)
    backgrounded: bool = False
    late_result: Any = None
    late_event: threading.Event = field(default_factory=threading.Event)


_lock = threading.RLock()
_local = threading.local()
_calls: Dict[str, _ExternalCall] = {}
_task_owners: Dict[str, str] = {}
_task_subscribers: Dict[str, Set[str]] = {}
_completed_tasks: Dict[str, tuple[float, Dict[str, Any]]] = {}


def _cleanup_locked() -> None:
    cutoff = time.time() - _RETENTION_SECONDS
    expired_calls = [
        call_id for call_id, call in _calls.items()
        if call.created_at < cutoff
    ]
    for call_id in expired_calls:
        _calls.pop(call_id, None)
    expired_tasks = [
        task_id for task_id, (completed_at, _result)
        in _completed_tasks.items() if completed_at < cutoff
    ]
    for task_id in expired_tasks:
        _completed_tasks.pop(task_id, None)
        _task_owners.pop(task_id, None)
        _task_subscribers.pop(task_id, None)


def register_call(call_id: str, conversation_id: str, source_id: str,
                  display_name: str, llm_service: str = "") -> bool:
    if not call_id or not conversation_id or not source_id:
        raise ValueError("external call requires call_id, conversation_id, and source_id")
    with _lock:
        _cleanup_locked()
        existing = _calls.get(call_id)
        if existing:
            if (existing.conversation_id != conversation_id
                    or existing.source_id != source_id):
                raise ValueError(
                    f"external call id collision for '{call_id}'")
            existing.display_name = display_name or existing.display_name
            existing.llm_service = llm_service or existing.llm_service
            return False
        _calls[call_id] = _ExternalCall(
            call_id=call_id,
            conversation_id=conversation_id,
            source_id=source_id,
            display_name=display_name or "External client",
            llm_service=llm_service or "",
        )
        return True


@contextmanager
def call_scope(call_id: str) -> Iterator[None]:
    previous = getattr(_local, "call_id", "")
    _local.call_id = call_id
    try:
        yield
    finally:
        _local.call_id = previous


def current_owner() -> Optional[Dict[str, str]]:
    call_id = getattr(_local, "call_id", "") or ""
    if not call_id:
        return None
    with _lock:
        call = _calls.get(call_id)
        if not call:
            return None
        return {
            "call_id": call.call_id,
            "conversation_id": call.conversation_id,
            "source_id": call.source_id,
            "display_name": call.display_name,
            "llm_service": call.llm_service,
            "transport": "published_mcp",
        }


def set_expected_tasks(call_id: str, task_ids: Iterable[str]) -> List[str]:
    ordered = list(dict.fromkeys(
        str(task_id) for task_id in task_ids if str(task_id)
    ))
    with _lock:
        call = _calls.get(call_id)
        if not call:
            raise KeyError(f"unknown external call '{call_id}'")
        for task_id in call.expected:
            subscribers = _task_subscribers.get(task_id)
            if subscribers:
                subscribers.discard(call_id)
        call.expected = ordered
        call.results = {}
        call.event.clear()
        for task_id in ordered:
            _task_owners.setdefault(task_id, call_id)
            _task_subscribers.setdefault(task_id, set()).add(call_id)
            completed = _completed_tasks.get(task_id)
            if completed:
                call.results[task_id] = dict(completed[1])
        if ordered and all(task_id in call.results for task_id in ordered):
            call.event.set()
    return ordered


def complete_task(task_id: str, result: Dict[str, Any]) -> bool:
    """Complete an externally owned task.

    Returns True when the task belongs to an external call.  Callers use that
    signal to suppress the ordinary PawFlow-agent preempt/wake delivery.
    """
    task_id = str(task_id or "")
    if not task_id:
        return False
    payload = dict(result or {})
    payload.setdefault("task_id", task_id)
    with _lock:
        external = (
            task_id in _task_owners
            or task_id in _task_subscribers
            or task_id in _completed_tasks
        )
        if not external:
            return False
        _completed_tasks[task_id] = (time.time(), payload)
        for call_id in list(_task_subscribers.get(task_id, set())):
            call = _calls.get(call_id)
            if not call:
                continue
            call.results[task_id] = dict(payload)
            if call.expected and all(
                    expected in call.results for expected in call.expected):
                call.event.set()
        return True


def wait_for_results(call_id: str, timeout: Optional[float] = None
                     ) -> Optional[List[Dict[str, Any]]]:
    with _lock:
        call = _calls.get(call_id)
        if not call:
            return None
        event = call.event
    if not event.wait(timeout):
        return None
    with _lock:
        call = _calls.get(call_id)
        if not call:
            return None
        return [dict(call.results[task_id]) for task_id in call.expected]


def complete_call(call_id: str, result: Any) -> bool:
    with _lock:
        call = _calls.get(call_id)
        if not call:
            return False
        call.late_result = result
        call.late_event.set()
        return True


def wait_for_call_result(call_id: str, timeout: Optional[float] = None
                         ) -> Any:
    with _lock:
        call = _calls.get(call_id)
        if not call:
            return None
        event = call.late_event
    if not event.wait(timeout):
        return None
    with _lock:
        call = _calls.get(call_id)
        return call.late_result if call else None


def conversation_for_call(call_id: str) -> str:
    with _lock:
        call = _calls.get(call_id)
        return call.conversation_id if call else ""


def mark_background(call_id: str) -> bool:
    with _lock:
        call = _calls.get(call_id)
        if not call:
            return False
        call.backgrounded = True
        return True


def is_backgrounded(call_id: str) -> bool:
    with _lock:
        call = _calls.get(call_id)
        return bool(call and call.backgrounded)


def reset_for_tests() -> None:
    with _lock:
        _calls.clear()
        _task_owners.clear()
        _task_subscribers.clear()
        _completed_tasks.clear()
    _local.call_id = ""
