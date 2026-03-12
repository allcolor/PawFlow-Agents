"""Task state management - lifecycle states for processors."""

import threading
from enum import Enum
from typing import Dict, Optional, List
from datetime import datetime


class TaskState(Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    ERROR = "error"
    INVALID = "invalid"
    DISABLED = "disabled"


class TaskStateInfo:
    """State information for a single task."""

    def __init__(self, task_id: str, task_type: str = ""):
        self.task_id = task_id
        self.task_type = task_type
        self.state = TaskState.STOPPED
        self.error_message: Optional[str] = None
        self.last_state_change: datetime = datetime.now()
        self.run_count: int = 0
        self.error_count: int = 0
        self.last_run: Optional[datetime] = None
        self.bytes_in: int = 0
        self.bytes_out: int = 0
        self.flowfiles_in: int = 0
        self.flowfiles_out: int = 0

    def reset_counters(self):
        """Reset all counters to zero."""
        self.run_count = 0
        self.error_count = 0
        self.bytes_in = 0
        self.bytes_out = 0
        self.flowfiles_in = 0
        self.flowfiles_out = 0
        self.error_message = None
        self.last_run = None

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "state": self.state.value,
            "error_message": self.error_message,
            "last_state_change": self.last_state_change.isoformat(),
            "run_count": self.run_count,
            "error_count": self.error_count,
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "bytes_in": self.bytes_in,
            "bytes_out": self.bytes_out,
            "flowfiles_in": self.flowfiles_in,
            "flowfiles_out": self.flowfiles_out,
        }


class TaskStateManager:
    """Thread-safe manager for task lifecycle states."""

    def __init__(self):
        self._states: Dict[str, TaskStateInfo] = {}
        self._lock = threading.Lock()

    def register_task(self, task_id: str, task_type: str = ""):
        """Register a task with initial STOPPED state."""
        with self._lock:
            if task_id not in self._states:
                self._states[task_id] = TaskStateInfo(task_id, task_type)

    def get_state(self, task_id: str) -> Optional[TaskState]:
        """Get current state of a task."""
        with self._lock:
            info = self._states.get(task_id)
            return info.state if info else None

    def get_info(self, task_id: str) -> Optional[TaskStateInfo]:
        """Get full state info for a task."""
        with self._lock:
            return self._states.get(task_id)

    def start(self, task_id: str) -> bool:
        """Transition to RUNNING. Only from STOPPED or ERROR."""
        with self._lock:
            info = self._states.get(task_id)
            if not info:
                return False
            if info.state not in (TaskState.STOPPED, TaskState.ERROR):
                return False
            info.state = TaskState.RUNNING
            info.last_state_change = datetime.now()
            info.error_message = None
            return True

    def stop(self, task_id: str) -> bool:
        """Transition to STOPPED. From any state except INVALID."""
        with self._lock:
            info = self._states.get(task_id)
            if not info:
                return False
            if info.state == TaskState.INVALID:
                return False
            info.state = TaskState.STOPPED
            info.last_state_change = datetime.now()
            return True

    def set_error(self, task_id: str, message: str) -> bool:
        """Transition to ERROR."""
        with self._lock:
            info = self._states.get(task_id)
            if not info:
                return False
            info.state = TaskState.ERROR
            info.error_message = message
            info.error_count += 1
            info.last_state_change = datetime.now()
            return True

    def set_invalid(self, task_id: str, reason: str) -> bool:
        """Transition to INVALID (e.g., bad config)."""
        with self._lock:
            info = self._states.get(task_id)
            if not info:
                return False
            info.state = TaskState.INVALID
            info.error_message = reason
            info.last_state_change = datetime.now()
            return True

    def disable(self, task_id: str) -> bool:
        """Transition to DISABLED."""
        with self._lock:
            info = self._states.get(task_id)
            if not info:
                return False
            info.state = TaskState.DISABLED
            info.last_state_change = datetime.now()
            return True

    def enable(self, task_id: str) -> bool:
        """Re-enable a DISABLED task (goes to STOPPED)."""
        with self._lock:
            info = self._states.get(task_id)
            if not info:
                return False
            if info.state != TaskState.DISABLED:
                return False
            info.state = TaskState.STOPPED
            info.last_state_change = datetime.now()
            return True

    def record_run(self, task_id: str, ff_in: int = 1, ff_out: int = 1,
                   bytes_in: int = 0, bytes_out: int = 0):
        """Record a successful task execution."""
        with self._lock:
            info = self._states.get(task_id)
            if info:
                info.run_count += 1
                info.last_run = datetime.now()
                info.flowfiles_in += ff_in
                info.flowfiles_out += ff_out
                info.bytes_in += bytes_in
                info.bytes_out += bytes_out

    def get_all_states(self) -> Dict[str, dict]:
        """Get all task states as dicts."""
        with self._lock:
            return {tid: info.to_dict() for tid, info in self._states.items()}

    def get_tasks_by_state(self, state: TaskState) -> List[str]:
        """Get list of task IDs in a given state."""
        with self._lock:
            return [tid for tid, info in self._states.items() if info.state == state]

    def reset_all_counters(self):
        """Reset counters for all tasks."""
        with self._lock:
            for info in self._states.values():
                info.reset_counters()

    def is_runnable(self, task_id: str) -> bool:
        """Check if task can be executed (RUNNING state)."""
        with self._lock:
            info = self._states.get(task_id)
            return info is not None and info.state == TaskState.RUNNING

    def clear(self):
        """Clear all state data."""
        with self._lock:
            self._states.clear()
