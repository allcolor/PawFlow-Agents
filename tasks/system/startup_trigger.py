# StartupTrigger Task

"""
StartupTrigger - one-shot source that fires a single FlowFile when the flow
starts, then stays quiet.

Use it to run flow initialization exactly once on deploy/restart: provision a
schema, seed config rows, warm a cache. It is a pure source (no incoming
connections); the ContinuousFlowExecutor fires it once via has_pending_input()
and the no-op reset() keeps queue clears from re-arming it.
"""

import threading
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from core import FlowFile, TaskFactory
from core.base_task import BaseTask


class StartupTriggerTask(BaseTask):
    """Fire a single FlowFile once when the flow starts."""

    TYPE = "startupTrigger"
    VERSION = "1.0.0"
    NAME = "Startup Trigger"
    DESCRIPTION = "Emit one FlowFile when the flow starts (one-shot source)"
    ICON = "power"
    TAGS = ["trigger", "source", "startup", "init"]

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.content = self.config.get('content', '')
        self.content_type = self.config.get('content_type', 'text/plain')
        self.encoding = self.config.get('encoding', 'utf-8')
        self._fired = False
        self._lock = threading.Lock()

    def has_pending_input(self) -> bool:
        """Fire once when used as a root task (no incoming connections)."""
        with self._lock:
            return not self._fired

    def reset(self):
        """No-op: clearing queues must NOT re-arm a one-shot trigger."""
        pass

    def execute(self, flowfile: Optional[FlowFile] = None) -> List[FlowFile]:
        with self._lock:
            if self._fired:
                return []
            self._fired = True

        content_bytes = self.content.encode(self.encoding)
        attributes = flowfile.get_attributes().copy() if flowfile else {}
        attributes['startup.trigger'] = 'true'
        attributes['startup.fired_at'] = datetime.now(timezone.utc).isoformat()
        attributes['mime.type'] = self.content_type

        return [FlowFile(content=content_bytes, attributes=attributes)]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'content': {
                'type': 'textarea', 'required': False, 'default': '',
                'description': 'Content for the emitted FlowFile',
            },
            'content_type': {
                'type': 'string', 'required': False, 'default': 'text/plain',
                'description': 'MIME type of the emitted content',
            },
            'encoding': {
                'type': 'string', 'required': False, 'default': 'utf-8',
                'options': ['utf-8', 'ascii', 'latin-1'],
                'description': 'Content encoding',
            },
        }


TaskFactory.register(StartupTriggerTask)
