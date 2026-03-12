"""Control Rate - throttle FlowFile throughput."""

import time
from core.base_task import BaseTask
from core import TaskFactory, FlowFile


class ControlRateTask(BaseTask):
    TYPE = "controlRate"
    VERSION = "1.0.0"
    NAME = "Control Rate"
    DESCRIPTION = "Throttles FlowFile throughput by adding delay"
    ICON = "⏱️"

    @classmethod
    def get_parameter_schema(cls):
        return {
            "rate": {
                "type": "integer", "required": False, "default": 1,
                "description": "Maximum number of FlowFiles per time period",
            },
            "time_period": {
                "type": "string", "required": False, "default": "1s",
                "description": "Time period: e.g. 100ms, 1s, 1m",
            },
        }

    def _parse_duration(self, duration_str: str) -> float:
        """Parse duration string to seconds."""
        duration_str = duration_str.strip().lower()
        if duration_str.endswith("ms"):
            return float(duration_str[:-2]) / 1000.0
        elif duration_str.endswith("s"):
            return float(duration_str[:-1])
        elif duration_str.endswith("m"):
            return float(duration_str[:-1]) * 60.0
        elif duration_str.endswith("h"):
            return float(duration_str[:-1]) * 3600.0
        else:
            return float(duration_str)

    def execute(self, flowfile):
        rate = int(self.config.get("rate", 1))
        time_period = self.config.get("time_period", "1s")

        delay = self._parse_duration(time_period) / max(rate, 1)
        if delay > 0:
            time.sleep(delay)

        output = FlowFile(content=flowfile.get_content(), attributes=flowfile.get_attributes())
        output.set_attribute("controlrate.delay_ms", str(int(delay * 1000)))
        return [output]


TaskFactory.register(ControlRateTask)
