# PutFile Task

"""
Tâche PutFile - Écrire un FlowFile sur le système de fichiers.
"""

import os
from typing import Dict, Any, List
from core import FlowFile, TaskFactory, TaskError
from core.base_task import BaseTask


class PutFileTask(BaseTask):
    """Écrire le contenu d'un FlowFile dans un fichier."""

    TYPE = "putFile"
    VERSION = "1.0.0"
    NAME = "PutFile"
    DESCRIPTION = "Écrire un FlowFile sur le système de fichiers"
    ICON = "file-output"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.output_directory = self.config.get('output_directory', '.')
        self.conflict_resolution = self.config.get('conflict_resolution', 'replace')
        self.create_dirs = self.config.get('create_dirs', True)

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        """Écrire le FlowFile sur disque."""
        filename = flowfile.get_attribute('filename') or flowfile.process_id[:8]

        if self.create_dirs:
            os.makedirs(self.output_directory, exist_ok=True)

        filepath = os.path.join(self.output_directory, filename)

        # Gestion des conflits
        if os.path.exists(filepath):
            if self.conflict_resolution == 'fail':
                raise TaskError(f"Fichier existe déjà: {filepath}")
            elif self.conflict_resolution == 'ignore':
                return [flowfile]
            elif self.conflict_resolution == 'rename':
                base, ext = os.path.splitext(filepath)
                counter = 1
                while os.path.exists(filepath):
                    filepath = f"{base}_{counter}{ext}"
                    counter += 1

        with open(filepath, 'wb') as f:
            f.write(flowfile.get_content())

        # Mettre à jour les attributs
        flowfile.set_attribute('output.path', os.path.abspath(filepath))
        flowfile.set_attribute('output.filename', os.path.basename(filepath))

        return [flowfile]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'output_directory': {
                'type': 'string', 'required': True,
                'description': 'Répertoire de destination',
            },
            'conflict_resolution': {
                'type': 'select', 'required': False, 'default': 'replace',
                'options': ['replace', 'fail', 'ignore', 'rename'],
                'description': 'Stratégie en cas de fichier existant',
            },
            'create_dirs': {
                'type': 'boolean', 'required': False, 'default': True,
                'description': 'Créer le répertoire si inexistant',
            },
        }


# Enregistrement dans la factory
TaskFactory.register(PutFileTask)