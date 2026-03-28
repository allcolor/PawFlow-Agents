"""delete — Delete a file or directory."""

from typing import Any, Dict
from core.handlers._fs_base import BaseFsHandler


class DeleteHandler(BaseFsHandler):

    @property
    def name(self):
        return "delete"

    @property
    def description(self):
        return "Delete a file or directory. Use filesystem='filestore' with file_id to delete from server store."

    @property
    def parameters_schema(self):
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File or directory path to delete"},
                "file_id": {"type": "string", "description": "FileStore file ID (alternative to path for FileStore)"},
                "filesystem": {"type": "string", "description": "Filesystem service name. Omit for default."},
            },
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        arguments = self._unwrap_json(arguments)
        arguments = self._resolve_expressions(arguments)
        path = arguments.get("path", "")
        file_id = arguments.get("file_id", "")
        fs = arguments.get("filesystem", "")

        if path:
            _svc_name, path = self._parse_fs_url(path)
            if _svc_name:
                fs = _svc_name

        svc, workdir = self._resolve(fs)

        if svc == "filestore":
            return self._filestore_delete(path, file_id)

        if workdir:
            if not path:
                return "Error: 'path' is required"
            return self._workdir_delete(path)

        if svc is None:
            return self._no_target_error(fs)

        if not path:
            return "Error: 'path' is required"

        try:
            service_name = fs or getattr(svc, '_service_id', '')
            self._checkpoint_before(svc, path, is_delete=True, service_name=service_name)
            svc.delete_file(path)
            return f"Deleted: {path}"
        except Exception as e:
            return f"Error deleting '{path}': {e}"
