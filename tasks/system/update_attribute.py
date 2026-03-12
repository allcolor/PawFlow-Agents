# UpdateAttribute Task

"""
Tâche UpdateAttribute - Modifier les attributs d'un FlowFile.
"""

from typing import Dict, Any, List
import re
from core import FlowFile, TaskFactory
from core.base_task import BaseTask


class UpdateAttributeTask(BaseTask):
    """Ajouter, modifier ou supprimer des attributs sur un FlowFile."""

    TYPE = "updateAttribute"
    VERSION = "1.0.0"
    NAME = "UpdateAttribute"
    DESCRIPTION = "Modifier les attributs d'un FlowFile"
    ICON = "edit"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        # Sauvegarder les valeurs originales avant résolution
        self._raw_attributes_to_set = config.get('set', {})
        self._raw_attributes_to_delete = config.get('delete', [])

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        """Modifier les attributs du FlowFile."""
        # Supprimer les attributs
        for key in self._raw_attributes_to_delete:
            flowfile.delete_attribute(key)

        # Ajouter/modifier les attributs avec résolution sur le flowfile
        for key, value in self._raw_attributes_to_set.items():
            resolved = self._resolve_attribute_value(flowfile, str(value))
            flowfile.set_attribute(key, resolved)

        return [flowfile]

    def _resolve_attribute_value(self, flowfile: FlowFile, value: str) -> str:
        """Résoudre les références à d'autres attributs dans la valeur."""
        if '${' not in value:
            return value

        def replace_ref(match):
            attr_name = match.group(1)
            return flowfile.get_attribute(attr_name) or match.group(0)

        # Boucler pour résoudre les références en cascade
        result = value
        max_iterations = 10
        for _ in range(max_iterations):
            new_result = re.sub(r'\$\{([^}]+)\}', replace_ref, result)
            if new_result == result:
                break
            result = new_result

        return result

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'set': {
                'type': 'map', 'required': False,
                'description': 'Attributs à ajouter/modifier (clé → valeur)',
            },
            'delete': {
                'type': 'list', 'required': False,
                'description': 'Attributs à supprimer',
            },
        }


# Enregistrement dans la factory
TaskFactory.register(UpdateAttributeTask)