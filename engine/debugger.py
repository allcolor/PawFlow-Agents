"""Flow debugger -- breakpoints, step-by-step execution, FlowFile inspection."""

import threading
import time
from typing import Dict, List, Optional, Set, Any
from dataclasses import dataclass, field
from enum import Enum


class DebugAction(Enum):
    CONTINUE = "continue"       # Resume until next breakpoint
    STEP = "step"               # Execute one task then pause
    STEP_OVER = "step_over"     # Execute current task, pause at next
    STOP = "stop"               # Stop debugging


@dataclass
class Breakpoint:
    task_id: str
    condition: str = ""         # Optional: expression that must be true
    hit_count: int = 0
    enabled: bool = True
    log_message: str = ""       # If set, log instead of breaking


@dataclass
class DebugSnapshot:
    """Snapshot of a FlowFile at a debug point."""
    task_id: str
    timestamp: float
    flowfile_id: str
    content_preview: str        # First 1000 chars of content
    content_size: int
    attributes: Dict[str, str]
    direction: str              # "input" or "output"


class FlowDebugger:
    """Debugger that attaches to a ContinuousFlowExecutor.

    Usage:
        debugger = FlowDebugger()
        debugger.add_breakpoint("task_1")
        debugger.attach(executor)
        # Executor pauses at breakpoints
        # Call debugger.step() or debugger.continue_execution()
        debugger.detach()
    """

    def __init__(self):
        self._breakpoints: Dict[str, Breakpoint] = {}
        self._paused = False
        self._paused_at: Optional[str] = None
        self._pause_event = threading.Event()
        self._pause_event.set()  # Not paused initially
        self._action = DebugAction.CONTINUE
        self._snapshots: List[DebugSnapshot] = []
        self._max_snapshots = 100
        self._attached_executor = None
        self._step_mode = False
        self._lock = threading.Lock()
        self._callbacks: Dict[str, list] = {
            "paused": [],
            "resumed": [],
            "snapshot": [],
            "breakpoint_hit": [],
        }

    def on(self, event: str, callback):
        """Register callback for debug events."""
        if event in self._callbacks:
            self._callbacks[event].append(callback)

    def _emit(self, event: str, **kwargs):
        for cb in self._callbacks.get(event, []):
            try:
                cb(**kwargs)
            except Exception:
                pass

    # --- Breakpoint management ---

    def add_breakpoint(self, task_id: str, condition: str = "",
                       log_message: str = "") -> Breakpoint:
        bp = Breakpoint(task_id=task_id, condition=condition, log_message=log_message)
        self._breakpoints[task_id] = bp
        return bp

    def remove_breakpoint(self, task_id: str) -> bool:
        return self._breakpoints.pop(task_id, None) is not None

    def toggle_breakpoint(self, task_id: str) -> bool:
        if task_id in self._breakpoints:
            self._breakpoints[task_id].enabled = not self._breakpoints[task_id].enabled
            return self._breakpoints[task_id].enabled
        return False

    def get_breakpoints(self) -> Dict[str, Breakpoint]:
        return dict(self._breakpoints)

    def clear_breakpoints(self):
        self._breakpoints.clear()

    # --- Execution control ---

    def should_pause(self, task_id: str, flowfile=None) -> bool:
        """Called by executor before executing a task. Returns True if should pause."""
        if self._step_mode:
            return True

        bp = self._breakpoints.get(task_id)
        if not bp or not bp.enabled:
            return False

        # Check condition
        if bp.condition and flowfile:
            try:
                # Evaluate condition against FlowFile attributes
                attrs = {}
                if hasattr(flowfile, 'get_attributes'):
                    attrs = flowfile.get_attributes()
                elif hasattr(flowfile, 'attributes'):
                    attrs = flowfile.attributes
                if not eval(bp.condition, {"__builtins__": {}}, {"attrs": attrs, "ff": flowfile}):
                    return False
            except Exception:
                pass  # If condition fails, break anyway

        bp.hit_count += 1

        if bp.log_message:
            # Logpoint -- don't actually pause
            return False

        return True

    def pause_at(self, task_id: str, flowfile=None):
        """Pause execution at a task."""
        with self._lock:
            self._paused = True
            self._paused_at = task_id
            self._step_mode = False
            self._pause_event.clear()

        # Capture snapshot
        if flowfile:
            self._capture_snapshot(task_id, flowfile, "input")

        self._emit("paused", task_id=task_id)
        self._emit("breakpoint_hit", task_id=task_id)

        # Block until resumed
        self._pause_event.wait()

    def continue_execution(self):
        """Resume execution until next breakpoint."""
        with self._lock:
            self._paused = False
            self._paused_at = None
            self._step_mode = False
            self._action = DebugAction.CONTINUE
            self._pause_event.set()
        self._emit("resumed")

    def step(self):
        """Execute one task then pause again."""
        with self._lock:
            self._paused = False
            self._paused_at = None
            self._step_mode = True
            self._action = DebugAction.STEP
            self._pause_event.set()
        self._emit("resumed")

    def stop_debugging(self):
        """Stop debugging, resume normal execution."""
        with self._lock:
            self._paused = False
            self._paused_at = None
            self._step_mode = False
            self._action = DebugAction.STOP
            self._breakpoints.clear()
            self._pause_event.set()
        self._emit("resumed")

    # --- Inspection ---

    def _capture_snapshot(self, task_id: str, flowfile, direction: str):
        """Capture FlowFile state for inspection."""
        try:
            content = ""
            content_size = 0
            if hasattr(flowfile, 'get_content'):
                raw = flowfile.get_content()
                if isinstance(raw, bytes):
                    content = raw[:1000].decode('utf-8', errors='replace')
                    content_size = len(raw)
                else:
                    content = str(raw)[:1000]
                    content_size = len(str(raw))

            attrs = {}
            if hasattr(flowfile, 'get_attributes'):
                attrs = flowfile.get_attributes()
            elif hasattr(flowfile, 'attributes'):
                attrs = dict(flowfile.attributes)

            snapshot = DebugSnapshot(
                task_id=task_id,
                timestamp=time.time(),
                flowfile_id=attrs.get('uuid', attrs.get('id', str(id(flowfile)))),
                content_preview=content,
                content_size=content_size,
                attributes=attrs,
                direction=direction,
            )

            self._snapshots.append(snapshot)
            if len(self._snapshots) > self._max_snapshots:
                self._snapshots = self._snapshots[-self._max_snapshots:]

            self._emit("snapshot", snapshot=snapshot)
        except Exception:
            pass

    def capture_output(self, task_id: str, flowfiles):
        """Capture output FlowFiles after task execution."""
        if isinstance(flowfiles, list):
            for ff in flowfiles:
                self._capture_snapshot(task_id, ff, "output")
        elif flowfiles:
            self._capture_snapshot(task_id, flowfiles, "output")

    def get_snapshots(self, task_id: str = None, limit: int = 50) -> List[Dict]:
        """Get debug snapshots."""
        snaps = self._snapshots
        if task_id:
            snaps = [s for s in snaps if s.task_id == task_id]
        result = []
        for s in snaps[-limit:]:
            result.append({
                "task_id": s.task_id,
                "timestamp": s.timestamp,
                "flowfile_id": s.flowfile_id,
                "content_preview": s.content_preview,
                "content_size": s.content_size,
                "attributes": s.attributes,
                "direction": s.direction,
            })
        return result

    def get_status(self) -> Dict[str, Any]:
        """Get debugger status."""
        return {
            "attached": self._attached_executor is not None,
            "paused": self._paused,
            "paused_at": self._paused_at,
            "breakpoints": {
                tid: {"enabled": bp.enabled, "hit_count": bp.hit_count,
                       "condition": bp.condition, "log_message": bp.log_message}
                for tid, bp in self._breakpoints.items()
            },
            "snapshots_count": len(self._snapshots),
            "step_mode": self._step_mode,
        }

    # --- Attach/Detach ---

    def attach(self, executor):
        """Attach debugger to a ContinuousFlowExecutor."""
        self._attached_executor = executor
        executor._debugger = self

    def detach(self):
        """Detach debugger."""
        if self._attached_executor:
            self._attached_executor._debugger = None
            self._attached_executor = None
        self.stop_debugging()

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def paused_at(self) -> Optional[str]:
        return self._paused_at
