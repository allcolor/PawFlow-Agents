# Validate JSON Task

"""
Tâche ValidateJSON - Valider que le contenu d'un FlowFile est un JSON valide.
"""

import json
from typing import Dict, Any, List
from core import FlowFile, TaskFactory
from core.base_task import BaseTask


class ValidateJSONTask(BaseTask):
    """Valider que le contenu d'un FlowFile est un JSON valide."""

    TYPE = "validateJSON"
    VERSION = "1.0.0"
    NAME = "Validate JSON"
    DESCRIPTION = "Valider que le contenu d'un FlowFile est un JSON valide"
    ICON = "check-circle"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.destination_attribute = self.config.get('destination_attribute', 'json.valid')
        self.route_to = self.config.get('route_to', '')

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        """Valider le contenu JSON du FlowFile."""
        content = flowfile.get_content()

        try:
            json.loads(content.decode('utf-8'))
            is_valid = True
        except (json.JSONDecodeError, UnicodeDecodeError):
            is_valid = False

        flowfile.set_attribute(self.destination_attribute, 'true' if is_valid else 'false')

        if self.route_to:
            flowfile.set_attribute('route', 'valid' if is_valid else 'invalid')

        return [flowfile]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'destination_attribute': {
                'type': 'string',
                'required': False,
                'description': "Nom de l'attribut pour le résultat de validation",
                'default': 'json.valid'
            },
            'route_to': {
                'type': 'select',
                'required': False,
                'description': 'Activer le routage selon le résultat',
                'options': ['', 'valid', 'invalid'],
                'default': ''
            }
        }


# Enregistrement dans la factory
TaskFactory.register(ValidateJSONTask)
