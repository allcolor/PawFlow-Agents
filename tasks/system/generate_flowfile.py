# Generate FlowFile Task

"""
Tâche GenerateFlowFile - Générer de nouveaux FlowFiles avec contenu configurable.

Works as a one-shot source task in ContinuousFlowExecutor:
fires once when the flow starts, then stays quiet.
Can also be used mid-flow (with incoming connections) to replace content.
"""

import threading
from typing import Dict, Any, List, Optional
from core import FlowFile, TaskFactory
from core.base_task import BaseTask


class GenerateFlowFileTask(BaseTask):
    """Générer de nouveaux FlowFiles avec contenu configurable."""

    TYPE = "generateFlowFile"
    VERSION = "1.0.0"
    NAME = "Generate FlowFile"
    DESCRIPTION = "Générer de nouveaux FlowFiles avec contenu configurable"
    ICON = "plus"
    TAGS = ["source", "generator"]

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.content = self.config.get('content', '')
        self.content_type = self.config.get('content_type', 'text/plain')
        self.count = self.config.get('count', 1)
        self.encoding = self.config.get('encoding', 'utf-8')
        self.custom_attributes = self.config.get('custom_attributes', {})
        self._fired = False
        self._lock = threading.Lock()

    def has_pending_input(self) -> bool:
        """Fire once when used as a root task (no incoming connections)."""
        with self._lock:
            return not self._fired

    def reset(self):
        """No-op: clearing queues must NOT re-arm a one-shot trigger."""
        pass

    def execute(self, flowfile: Optional[FlowFile] = None) -> List[FlowFile]:
        """Générer de nouveaux FlowFiles."""
        # Mark as fired so has_pending_input() returns False from now on
        with self._lock:
            self._fired = True

        content_bytes = self.content.encode(self.encoding)

        generated_files = []
        for i in range(self.count):
            attributes = flowfile.get_attributes().copy() if flowfile else {}
            attributes['mime.type'] = self.content_type
            attributes['fileSize'] = str(len(content_bytes))
            attributes['filename'] = f"generated_{i}.dat"

            for key, value in self.custom_attributes.items():
                resolved = self._resolve_string(str(value))
                attributes[key] = resolved

            new_flowfile = FlowFile(
                content=content_bytes,
                attributes=attributes
            )
            generated_files.append(new_flowfile)

        return generated_files

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'content': {
                'type': 'string',
                'required': False,
                'description': 'Contenu à écrire dans les FlowFiles générés',
                'default': ''
            },
            'content_type': {
                'type': 'string',
                'required': False,
                'description': 'Type MIME du contenu',
                'default': 'text/plain'
            },
            'count': {
                'type': 'integer',
                'required': False,
                'description': 'Nombre de FlowFiles à générer',
                'default': 1,
                'min': 1
            },
            'encoding': {
                'type': 'string',
                'required': False,
                'description': 'Encodage du contenu',
                'options': ['utf-8', 'ascii', 'latin-1'],
                'default': 'utf-8'
            },
            'custom_attributes': {
                'type': 'map',
                'required': False,
                'description': 'Attributs personnalisés à ajouter (clé → valeur)'
            }
        }


# Enregistrement dans la factory
TaskFactory.register(GenerateFlowFileTask)
