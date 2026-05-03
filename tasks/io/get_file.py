# GetFile Task

"""
Task GetFile - Read a file from the filesystem.
"""

import os
import glob
from typing import Dict, Any, List
from core import FlowFile, TaskFactory, TaskError
from core.base_task import BaseTask


class GetFileTask(BaseTask):
    """Read a file and create a FlowFile with its content."""

    TYPE = "getFile"
    VERSION = "1.0.0"
    NAME = "GetFile"
    DESCRIPTION = "Read a file from the filesystem"
    ICON = "file-input"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.input_directory = self.config.get('input_directory', '.')
        self.file_filter = self.config.get('file_filter', '*')
        self.recursive = self.config.get('recursive', False)
        self.keep_source = self.config.get('keep_source', True)

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        """Read files — via filesystem service if configured, else sandbox FileStore."""
        service_id = self.config.get('service_id')

        if service_id:
            # ── Filesystem service mode ──
            svc = self.get_service(service_id)
            if svc is None:
                raise TaskError(f"Filesystem service not found: {service_id}")
            return self._execute_via_service(svc, flowfile)
        else:
            # ── Sandbox mode (FileStore, no server disk access) ──
            return self._execute_sandbox(flowfile)

    def _execute_via_service(self, svc, flowfile: FlowFile) -> List[FlowFile]:
        """Read files through a filesystem service."""
        import fnmatch as fnmod
        entries = svc.list_dir(self.input_directory)
        results = []
        for entry in entries:
            if entry.kind != "file":
                if self.recursive and entry.kind == "directory":
                    # TODO: recursive service listing
                    pass
                continue
            if not fnmod.fnmatch(entry.name, self.file_filter):
                continue
            path = f"{self.input_directory}/{entry.name}".replace("\\", "/")
            content = svc.read_file(path)
            ff = self.create_flowfile(
                content=content,
                attributes={
                    'filename': entry.name,
                    'path': self.input_directory,
                    'fileSize': str(len(content)),
                },
                parent_flowfile=flowfile,
            )
            results.append(ff)
            if not self.keep_source:
                svc.delete_file(path)
        return results if results else [flowfile]

    def _execute_sandbox(self, flowfile: FlowFile) -> List[FlowFile]:
        """Read files from FileStore sandbox (no server disk access)."""
        import fnmatch as fnmod
        from core.file_store import FileStore
        store = FileStore.instance()
        results = []
        for f in store.list_files():
            if not fnmod.fnmatch(f["filename"], self.file_filter):
                continue
            result = store.get(f["file_id"])
            if result:
                ff = self.create_flowfile(
                    content=result[1],
                    attributes={
                        'filename': f["filename"],
                        'fileSize': str(len(result[1])),
                        'file_id': f["file_id"],
                    },
                    parent_flowfile=flowfile,
                )
                results.append(ff)
        return results if results else [flowfile]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'input_directory': {
                'type': 'string', 'required': True,
                'description': 'Source directory',
            },
            'file_filter': {
                'type': 'string', 'required': False, 'default': '*',
                'description': 'Filtre de fichiers (glob pattern)',
            },
            'recursive': {
                'type': 'boolean', 'required': False, 'default': False,
                'description': 'Traverse subdirectories',
            },
            'keep_source': {
                'type': 'boolean', 'required': False, 'default': True,
                'description': 'Keep the source file after reading',
            },
            'service_id': {
                'type': 'string', 'required': False,
                'description': 'Filesystem service ID (without: uses sandbox FileStore)',
            },
        }


# Register in the factory
TaskFactory.register(GetFileTask)