# Base64 Encode Task

"""
Tâche Base64Encode - Encoder ou décoder le contenu en Base64.
"""

import base64
from typing import Dict, Any, List
from core import FlowFile, TaskFactory
from core.base_task import BaseTask


class Base64EncodeTask(BaseTask):
    """Encoder ou décoder le contenu d'un FlowFile en Base64."""

    TYPE = "base64Encode"
    VERSION = "1.0.0"
    NAME = "Base64 Encode"
    DESCRIPTION = "Encoder ou décoder le contenu d'un FlowFile en Base64"
    ICON = "code"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.mode = self.config.get('mode', 'encode')

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        """Encoder ou décoder le contenu du FlowFile en Base64."""
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
                'description': "Mode d'opération",
                'options': ['encode', 'decode'],
                'default': 'encode'
            }
        }


# Enregistrement dans la factory
TaskFactory.register(Base64EncodeTask)
