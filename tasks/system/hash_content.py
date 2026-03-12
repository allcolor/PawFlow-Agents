# Hash Content Task

"""
Tâche HashContent - Hacher le contenu d'un FlowFile.
"""

import hashlib
from typing import Dict, Any, List
from core import FlowFile, TaskFactory
from core.base_task import BaseTask


class HashContentTask(BaseTask):
    """Hacher le contenu d'un FlowFile avec différents algorithmes."""

    TYPE = "hashContent"
    VERSION = "1.0.0"
    NAME = "Hash Content"
    DESCRIPTION = "Hacher le contenu d'un FlowFile"
    ICON = "lock"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.algorithm = self.config.get('algorithm', 'sha256').lower()
        self.attribute_name = self.config.get('attribute_name', 'content.hash')

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        """Hacher le contenu du FlowFile."""
        content = flowfile.get_content()

        hasher = hashlib.new(self.algorithm) if self.algorithm in ('md5', 'sha1', 'sha256', 'sha512') else hashlib.sha256()
        hasher.update(content)
        flowfile.set_attribute(self.attribute_name, hasher.hexdigest())

        return [flowfile]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'algorithm': {
                'type': 'select',
                'required': False,
                'description': "Algorithme de hachage",
                'options': ['md5', 'sha1', 'sha256', 'sha512'],
                'default': 'sha256'
            },
            'attribute_name': {
                'type': 'string',
                'required': False,
                'description': "Nom de l'attribut pour stocker le hash",
                'default': 'content.hash'
            }
        }


# Enregistrement dans la factory
TaskFactory.register(HashContentTask)
