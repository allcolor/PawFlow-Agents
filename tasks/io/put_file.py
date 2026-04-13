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
        """Write FlowFile — via filesystem service if configured, else sandbox FileStore."""
        service_id = self.config.get('service_id')

        if service_id:
            return self._execute_via_service(service_id, flowfile)
        else:
            return self._execute_sandbox(flowfile)

    def _execute_via_service(self, service_id: str, flowfile: FlowFile) -> List[FlowFile]:
        """Write through a filesystem service."""
        svc = self.get_service(service_id)
        if svc is None:
            raise TaskError(f"Filesystem service not found: {service_id}")

        filename = flowfile.get_attribute('filename') or flowfile.process_id[:8]
        path = f"{self.output_directory}/{filename}".replace("\\", "/")

        if self.create_dirs:
            svc.mkdir(self.output_directory)

        # Conflict resolution via exists check
        if self.conflict_resolution != 'replace' and svc.exists(path):
            if self.conflict_resolution == 'fail':
                raise TaskError(f"File already exists: {path}")
            elif self.conflict_resolution == 'ignore':
                return [flowfile]

        svc.write_file(path, flowfile.get_content())
        flowfile.set_attribute('output.path', path)
        flowfile.set_attribute('output.filename', filename)
        return [flowfile]

    def _execute_sandbox(self, flowfile: FlowFile) -> List[FlowFile]:
        """Write to FileStore sandbox (no server disk access)."""
        from core.file_store import FileStore
        store = FileStore.instance()
        filename = flowfile.get_attribute('filename') or flowfile.process_id[:8]
        _uid = flowfile.get_attribute('user_id') or flowfile.get_attribute('http.auth.principal') or ''
        _cid = flowfile.get_attribute('conversation_id') or ''
        if not _uid or not _cid:
            raise ValueError(
                "putFile: user_id and conversation_id flowfile attributes required")
        file_id = store.store(filename, flowfile.get_content(),
                              user_id=_uid, conversation_id=_cid, ttl=3600)
        flowfile.set_attribute('output.file_id', file_id)
        flowfile.set_attribute('output.filename', filename)
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
            'service_id': {
                'type': 'string', 'required': False,
                'description': 'Filesystem service ID (without: uses sandbox FileStore)',
            },
        }


# Enregistrement dans la factory
TaskFactory.register(PutFileTask)