# Input/Output Port Tasks

"""
Ports d'entrée et de sortie pour les Process Groups.
"""

from typing import Dict, Any, List
from core import FlowFile, TaskFactory
from core.base_task import BaseTask


class InputPortTask(BaseTask):
    """Port d'entrée - point d'entrée d'un ProcessGroup."""

    TYPE = "inputPort"
    VERSION = "1.0.0"
    NAME = "Input Port"
    DESCRIPTION = "Port d'entrée pour un Process Group"
    ICON = "log-in"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.port_name = self.config.get('port_name', 'input')

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        flowfile.set_attribute('port.name', self.port_name)
        return [flowfile]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'port_name': {
                'type': 'string', 'required': False, 'default': 'input',
                'description': "Nom du port d'entrée",
            },
        }


class OutputPortTask(BaseTask):
    """Port de sortie - point de sortie d'un ProcessGroup."""

    TYPE = "outputPort"
    VERSION = "1.0.0"
    NAME = "Output Port"
    DESCRIPTION = "Port de sortie pour un Process Group"
    ICON = "log-out"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.port_name = self.config.get('port_name', 'output')

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        flowfile.set_attribute('port.name', self.port_name)
        return [flowfile]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'port_name': {
                'type': 'string', 'required': False, 'default': 'output',
                'description': "Nom du port de sortie",
            },
        }


TaskFactory.register(InputPortTask)
TaskFactory.register(OutputPortTask)
