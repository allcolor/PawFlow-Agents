"""
Wait/Notify Tasks - Synchronisation intra-process via SignalRegistry.
"""

from typing import Dict, Any, List
from core import FlowFile, TaskFactory, TaskError
from core.base_task import BaseTask
from core.signals import SignalRegistry


class WaitTask(BaseTask):
    """Waits for a signal before passing the FlowFile."""

    TYPE = "waitForSignal"
    VERSION = "1.0.0"
    NAME = "Wait For Signal"
    DESCRIPTION = "Waits for a SignalRegistry signal before continuing"
    ICON = "clock"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.signal_id = self.config.get("signal_id", "")
        self.target_count = self.config.get("target_count", 1)
        self.timeout = self.config.get("timeout", 30)

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        if not self.signal_id:
            raise TaskError("Le parametre 'signal_id' est requis.")

        registry = SignalRegistry.get_instance()
        received = registry.wait_for(self.signal_id, self.target_count, self.timeout)

        if received:
            flowfile.set_attribute("wait.status", "signaled")
            value = registry.get_value(self.signal_id)
            if value:
                flowfile.set_attribute("wait.signal.value", value)
            return [flowfile]
        else:
            raise TaskError(f"Signal timeout: {self.signal_id}")

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "signal_id": {
                "type": "string", "required": True,
                "description": "Signal identifier a attendre",
            },
            "target_count": {
                "type": "integer", "required": False, "default": 1,
                "description": "Nombre de notifications necessaires",
            },
            "timeout": {
                "type": "integer", "required": False, "default": 30,
                "description": "Timeout en secondes",
            },
        }


class NotifyTask(BaseTask):
    """Emit a signal to the SignalRegistry."""

    TYPE = "notify"
    VERSION = "1.0.0"
    NAME = "Notify Signal"
    DESCRIPTION = "Emet un signal au SignalRegistry"
    ICON = "bell"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.signal_id = self.config.get("signal_id", "")
        self.signal_value = self.config.get("signal_value", "")
        self.delta = self.config.get("delta", 1)

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        if not self.signal_id:
            raise TaskError("Le parametre 'signal_id' est requis.")

        registry = SignalRegistry.get_instance()
        result = registry.notify(self.signal_id, self.signal_value, self.delta)

        flowfile.set_attribute("notify.signal.id", self.signal_id)
        flowfile.set_attribute("notify.signal.count", str(result["count"]))

        return [flowfile]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "signal_id": {
                "type": "string", "required": True,
                "description": "Signal identifier a emettre",
            },
            "signal_value": {
                "type": "string", "required": False, "default": "",
                "description": "Optional value associated with the signal",
            },
            "delta": {
                "type": "integer", "required": False, "default": 1,
                "description": "Increment du compteur de signal",
            },
        }


TaskFactory.register(WaitTask)
TaskFactory.register(NotifyTask)
