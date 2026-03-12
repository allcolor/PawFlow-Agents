# GetFile Task

"""
Tâche GetFile - Lire un fichier depuis le système de fichiers.
"""

import os
import glob
from typing import Dict, Any, List
from core import FlowFile, TaskFactory, TaskError
from core.base_task import BaseTask


class GetFileTask(BaseTask):
    """Lire un fichier et créer un FlowFile avec son contenu."""

    TYPE = "getFile"
    VERSION = "1.0.0"
    NAME = "GetFile"
    DESCRIPTION = "Lire un fichier depuis le système de fichiers"
    ICON = "file-input"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.input_directory = self.config.get('input_directory', '.')
        self.file_filter = self.config.get('file_filter', '*')
        self.recursive = self.config.get('recursive', False)
        self.keep_source = self.config.get('keep_source', True)

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        """Lire les fichiers du répertoire et créer des FlowFiles."""
        pattern = os.path.join(self.input_directory, '**' if self.recursive else '', self.file_filter)
        files = glob.glob(pattern, recursive=self.recursive)

        results = []
        for filepath in files:
            if not os.path.isfile(filepath):
                continue

            with open(filepath, 'rb') as f:
                content = f.read()

            ff = self.create_flowfile(
                content=content,
                attributes={
                    'filename': os.path.basename(filepath),
                    'absolute.path': os.path.abspath(filepath),
                    'path': os.path.dirname(filepath),
                    'fileSize': str(len(content)),
                },
                parent_flowfile=flowfile
            )
            results.append(ff)

            if not self.keep_source:
                os.remove(filepath)

        return results if results else [flowfile]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'input_directory': {
                'type': 'string', 'required': True,
                'description': 'Répertoire source',
            },
            'file_filter': {
                'type': 'string', 'required': False, 'default': '*',
                'description': 'Filtre de fichiers (glob pattern)',
            },
            'recursive': {
                'type': 'boolean', 'required': False, 'default': False,
                'description': 'Parcourir les sous-répertoires',
            },
            'keep_source': {
                'type': 'boolean', 'required': False, 'default': True,
                'description': 'Conserver le fichier source après lecture',
            },
        }


# Enregistrement dans la factory
TaskFactory.register(GetFileTask)