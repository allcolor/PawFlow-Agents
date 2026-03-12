"""cronTrigger — Persistent source task that emits a FlowFile on a CRON schedule.

Works like httpReceiver: it's a persistent source that keeps the
ContinuousFlowExecutor alive and generates FlowFiles at scheduled times.

Config:
    schedule: str  — CRON expression (minute hour day month weekday)
                     e.g. "0 7 * * *" = every day at 07:00

Output FlowFile attributes:
    cron.schedule    — the CRON expression
    cron.fired_at    — ISO timestamp when the trigger fired
"""

import logging
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from core import FlowFile, TaskFactory
from core.base_task import BaseTask

logger = logging.getLogger(__name__)


class CronTriggerTask(BaseTask):
    """Persistent source task that fires on a CRON schedule."""

    TYPE = "cronTrigger"
    VERSION = "1.0.0"
    NAME = "CRON Trigger"
    DESCRIPTION = "Generate a FlowFile on a CRON schedule"
    ICON = "clock"
    TAGS = ["trigger", "source", "schedule", "cron"]

    PARAMETERS = {
        "schedule": {
            "type": "string",
            "description": "CRON expression: minute hour day month weekday (e.g. '0 7 * * *')",
            "required": True,
        },
    }

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._pending = False
        self._lock = threading.Lock()
        self._last_fire_minute: Optional[int] = None  # (year, month, day, hour, minute) to avoid double-fire
        self._cron_expression = self.config.get("schedule", "")

    @property
    def is_persistent_source(self) -> bool:
        return True

    def has_pending_input(self) -> bool:
        """Check if the CRON expression matches the current time."""
        now = datetime.now()
        current_minute = (now.year, now.month, now.day, now.hour, now.minute)

        # Don't fire twice in the same minute
        with self._lock:
            if current_minute == self._last_fire_minute:
                return self._pending

        if not self._cron_expression:
            return False

        from engine.scheduler import SimpleCronParser
        parser = SimpleCronParser()
        if parser.matches(self._cron_expression, now):
            with self._lock:
                if current_minute != self._last_fire_minute:
                    self._pending = True
                    self._last_fire_minute = current_minute
                    logger.info(f"cronTrigger fired: {self._cron_expression} at {now.isoformat()}")
            return True
        return self._pending

    def execute(self, flowfile: Optional[FlowFile] = None) -> List[FlowFile]:
        """Emit a FlowFile when the CRON fires."""
        with self._lock:
            if not self._pending:
                return []
            self._pending = False

        now = datetime.now()
        ff = FlowFile(content=b"")
        ff.set_attribute("cron.schedule", self._cron_expression)
        ff.set_attribute("cron.fired_at", now.isoformat())
        return [ff]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "schedule": {
                "type": "string",
                "required": True,
                "description": "CRON expression: minute hour day month weekday",
            },
        }


TaskFactory.register(CronTriggerTask)
