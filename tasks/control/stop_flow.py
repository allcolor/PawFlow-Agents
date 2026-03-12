"""stopFlow — Stop the current flow execution.

When this task is reached in the flow DAG, it signals the executor to stop.
Use this in flows with persistent sources (HTTP listeners, Telegram bots)
that need an explicit exit condition.

For flows with only one-shot sources, auto-stop is handled by the executor
automatically when all queues are empty.

Config:
    reason: str — Optional reason for stopping (logged)
"""

import logging
from typing import Any, Dict, List

from core import FlowFile, TaskFactory
from core.base_task import BaseTask

logger = logging.getLogger(__name__)


class StopFlowTask(BaseTask):
    """Stop the current flow when this task is reached."""

    TYPE = "stopFlow"
    VERSION = "1.0.0"
    NAME = "Stop Flow"
    DESCRIPTION = "Stop the current flow execution"
    ICON = "stop"
    TAGS = ["control", "flow"]

    PARAMETERS = {
        "reason": {
            "type": "string",
            "description": "Reason for stopping the flow",
            "required": False,
            "default": "",
        },
    }

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        reason = self.config.get("reason", "") or "stopFlow task reached"
        flow_id = flowfile.get_attribute("flow.id") or "unknown"

        logger.info(f"StopFlow triggered for flow '{flow_id}': {reason}")

        # Signal the executor to stop via a special attribute
        flowfile.set_attribute("flow.stop_requested", "true")
        flowfile.set_attribute("flow.stop_reason", reason)

        return [flowfile]


TaskFactory.register(StopFlowTask)
