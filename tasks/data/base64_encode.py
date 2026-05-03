# Base64 Encode Task

"""
Base64Encode task - Encode or decode content as Base64.
"""

import base64
from typing import Dict, Any, List
from core import FlowFile, TaskFactory
from core.base_task import BaseTask


class Base64EncodeTask(BaseTask):
    """Encode or decode FlowFile content as Base64."""

    TYPE = "base64Encode"
    VERSION = "1.0.0"
    NAME = "Base64 Encode"
    DESCRIPTION = "Encode or decode FlowFile content as Base64"
    ICON = "code"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.mode = self.config.get('mode', 'encode')

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        """Encode or decode the FlowFile content as Base64."""
        content = flowfile.get_content()

        if self.mode == 'encode':
            result = base64.b64encode(content)
        else:
            result = base64.b64decode(content)

        flowfile.set_content(result)
        return [flowfile]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'mode': {
                'type': 'select',
                'required': False,
                'description': "Operation mode",
                'options': ['encode', 'decode'],
                'default': 'encode'
            }
        }


# Register in the factory
TaskFactory.register(Base64EncodeTask)
