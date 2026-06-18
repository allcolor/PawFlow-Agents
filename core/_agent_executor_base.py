"""Shared base for the sub-agent executor: recursion-depth tracking, the
live-delegate registry, the cancel registry, and the AgentTask/AgentResult
dataclasses. Split out of agent_executor.py so the executor facade and the
loop mixin can both depend on these without a circular import.

Public names (AgentTask, cancel_sub_agent_task, get_live_delegate,
queue_live_delegate_message, ...) are re-exported from core.agent_executor.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Dict, List, Optional


# Global depth tracker per thread to prevent infinite recursion
_depth_local = {}  # thread_id -> current_depth
_depth_lock = Lock()

MAX_GLOBAL_DEPTH = 5  # absolute ceiling regardless of agent config

# Global cancel registry for sub-agent tasks (delegate cancel)
_cancelled_tasks: set = set()
_cancelled_lock = Lock()


def cancel_sub_agent_task(task_id: str):
    """Mark a sub-agent task as cancelled. The agent loop checks this."""
    with _cancelled_lock:
        _cancelled_tasks.add(task_id)


def _is_cancelled(task_id: str) -> bool:
    with _cancelled_lock:
        return task_id in _cancelled_tasks


def _clear_cancelled(task_id: str):
    with _cancelled_lock:
        _cancelled_tasks.discard(task_id)


# Live delegate registry: one in-flight delegate per (parent_conv, caller, target).
# A second delegate call for the same triple should INJECT its message into the
# running sub-agent's loop instead of spawning a parallel one.
#   value: {"task_id", "client", "task", "pending"}
_live_delegates: Dict[tuple, dict] = {}
_live_delegates_lock = Lock()


def get_live_delegate(parent_conv: str, caller: str, target: str) -> Optional[dict]:
    with _live_delegates_lock:
        return _live_delegates.get((parent_conv, caller, target))


def register_live_delegate(parent_conv: str, caller: str, target: str,
                           task_id: str, client, task) -> None:
    with _live_delegates_lock:
        _live_delegates[(parent_conv, caller, target)] = {
            "task_id": task_id, "client": client, "task": task,
            "pending": [],
        }


def queue_live_delegate_message(parent_conv: str, caller: str, target: str,
                                message: str) -> bool:
    """Queue a follow-up for a running isolated delegate.

    Claude Code may consume the message inline through send_user_message().
    Codex/Gemini intentionally return False after killing the CLI, so the
    sub-agent loop must pick the follow-up up from this queue on the next
    provider call.
    """
    with _live_delegates_lock:
        entry = _live_delegates.get((parent_conv, caller, target))
        if not entry:
            return False
        entry.setdefault("pending", []).append(message)
        return True


def drain_live_delegate_messages(parent_conv: str, caller: str,
                                 target: str, task_id: str = "") -> List[str]:
    with _live_delegates_lock:
        entry = _live_delegates.get((parent_conv, caller, target))
        if not entry or (task_id and entry.get("task_id") != task_id):
            return []
        pending = list(entry.get("pending") or [])
        entry["pending"] = []
        return pending


def unregister_live_delegate(parent_conv: str, caller: str, target: str,
                             task_id: str = "") -> None:
    """Remove entry unless another task has already taken over the slot."""
    with _live_delegates_lock:
        entry = _live_delegates.get((parent_conv, caller, target))
        if entry and (not task_id or entry.get("task_id") == task_id):
            _live_delegates.pop((parent_conv, caller, target), None)


@dataclass
class AgentTask:
    """A single sub-agent task to execute."""
    id: str
    agent_name: str
    message: str
    # Resolved at execution time:
    system_prompt: str = ""
    model: str = ""
    tools: Optional[List[str]] = None  # tool name whitelist (None = all)
    max_iterations: int = 50
    max_depth: int = 1
    timeout: int = 300
    llm_service: str = ""  # service ID for LLM routing
    user_id: str = ""  # user ID for service resolution
    source_agent: str = ""  # name of the parent agent (for identity tracking)
    source_agent_nickname: str = ""  # display name of the parent agent
    source_llm_service: str = ""  # LLM service of the parent agent
    context_mode: str = "isolated"  # isolated, last:N, summary:N, full
    context_messages: Optional[List] = None  # pre-resolved context messages
    parent_conversation_id: str = ""  # for read_parent_context tool
    delegate_tc_id: str = ""  # tool_call ID of the delegate call in parent conversation
    persist: bool = False  # keep sub-conversation after completion (for multi-turn delegates)
    source_task_id: str = ""  # task ID when delegate is spawned from within a task


@dataclass
class AgentResult:
    """Result of a sub-agent execution."""
    task_id: str
    agent_name: str
    response: str = ""
    error: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    tools_called: List[str] = field(default_factory=list)
    iterations: int = 0
    duration_ms: float = 0.0
    status: str = "pending"  # pending, running, completed, error, timeout, cancelled, needs_input
    model: str = ""
    provider: str = ""
    question: str = ""  # question for parent agent (ask_parent)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "agent_name": self.agent_name,
            "response": self.response,
            "error": self.error,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "tools_called": self.tools_called,
            "iterations": self.iterations,
            "duration_ms": self.duration_ms,
            "status": self.status,
        }


def _get_depth() -> int:
    """Get current recursion depth for this thread."""
    import threading
    tid = threading.current_thread().ident
    with _depth_lock:
        return _depth_local.get(tid, 0)


def _set_depth(depth: int):
    """Set recursion depth for this thread."""
    import threading
    tid = threading.current_thread().ident
    with _depth_lock:
        if depth <= 0:
            _depth_local.pop(tid, None)
        else:
            _depth_local[tid] = depth


