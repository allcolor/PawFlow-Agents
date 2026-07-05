"""ShutdownTrigger - one-shot source fired when a running flow is stopped.

The task stays quiet during normal scheduling. ContinuousFlowExecutor.stop()
arms it, wakes the scheduler, and waits briefly so downstream cleanup tasks can
run before services are disconnected.
"""

import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core import FlowFile, TaskFactory
from core.base_task import BaseTask


class ShutdownTriggerTask(BaseTask):
    """One-shot source task armed by the executor during flow shutdown."""

    TYPE = "shutdownTrigger"
    VERSION = "1.0.0"
    NAME = "Shutdown Trigger"
    DESCRIPTION = "Generate a FlowFile when the flow is stopped"
    ICON = "power"
    TAGS = ["trigger", "source", "lifecycle", "shutdown"]

    PARAMETERS = {
        "content": {
            "type": "string",
            "description": "Optional FlowFile content emitted on shutdown",
            "required": False,
            "default": "",
        },
        "timeout": {
            "type": "number",
            "description": "Maximum seconds the executor should wait for shutdown cleanup",
            "required": False,
            "default": 10,
        },
    }

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._pending = False
        self._fired = False
        self._lock = threading.Lock()

    def arm_shutdown(self):
        """Arm the trigger once. Called by ContinuousFlowExecutor.stop()."""
        with self._lock:
            if not self._fired:
                self._pending = True

    def has_pending_input(self) -> bool:
        with self._lock:
            return self._pending

    def execute(self, flowfile: Optional[FlowFile] = None) -> List[FlowFile]:
        with self._lock:
            if not self._pending or self._fired:
                return []
            self._pending = False
            self._fired = True

        ff = FlowFile(content=str(self.config.get("content", "")).encode("utf-8"))
        ff.set_attribute("shutdown.trigger", "true")
        ff.set_attribute("shutdown.fired_at", datetime.now(timezone.utc).isoformat())
        return [ff]

    def reset(self):
        # Queue clears must not re-arm lifecycle cleanup.
        with self._lock:
            self._pending = False


TaskFactory.register(ShutdownTriggerTask)
