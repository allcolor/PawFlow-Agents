# Extract Text Task

"""
Tâche ExtractText - Extraire du texte d'un FlowFile en utilisant des expressions régulières.
"""

import re
from typing import Dict, Any, List
from core import FlowFile, TaskFactory
from core.base_task import BaseTask


class ExtractTextTask(BaseTask):
    """Extraire du texte d'un FlowFile en utilisant des expressions régulières."""

    TYPE = "extractText"
    VERSION = "1.0.0"
    NAME = "Extract Text"
    DESCRIPTION = "Extraire du texte d'un FlowFile via expressions régulières"
    ICON = "search"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.pattern = self.config.get('pattern', '')
        self.attribute_name = self.config.get('attribute_name', 'extracted.text')
        self.group = self.config.get('group', 0)

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        """Extraire du texte en appliquant une expression régulière."""
        try:
            content = flowfile.get_content().decode('utf-8')
        except UnicodeDecodeError:
            content = flowfile.get_content().decode('latin-1')

        match = re.search(self.pattern, content)

        if match:
            extracted_value = match.group(self.group)
        else:
            extracted_value = ""

        flowfile.set_attribute(self.attribute_name, extracted_value)

        return [flowfile]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'pattern': {
                'type': 'string',
                'required': True,
                'description': 'Expression régulière à appliquer',
                'placeholder': r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
            },
            'attribute_name': {
                'type': 'string',
                'required': False,
                'description': "Nom de l'attribut pour stocker le texte extrait",
                'default': 'extracted.text'
            },
            'group': {
                'type': 'integer',
                'required': False,
                'description': 'Groupe de capture à extraire (0 = correspondance complète)',
                'default': 0,
                'min': 0
            }
        }


# Enregistrement dans la factory
TaskFactory.register(ExtractTextTask)
