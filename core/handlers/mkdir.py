"""mkdir — Create a directory."""

from typing import Any, Dict
from core.handlers._fs_base import BaseFsHandler


class MkdirHandler(BaseFsHandler):

    @property
    def name(self):
        return "mkdir"

    @property
    def description(self):
        return "Create a directory (and parent directories if needed)."

    @property
    def parameters_schema(self):
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path to create"},
                "filesystem": {"type": "string", "description": "Filesystem service name. Omit for default."},
            },
            "required": ["path"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        arguments = self._unwrap_json(arguments)
        arguments = self._resolve_expressions(arguments)
        path = arguments.get("path", "")
        if not path:
            return "Error: 'path' is required"
        fs = arguments.get("filesystem", "")

        _svc_name, path = self._parse_fs_url(path)
        if _svc_name:
            fs = _svc_name

        svc, workdir = self._resolve(fs)

        if svc == "filestore":
            return "Error: mkdir not supported on FileStore"

        if workdir:
            return self._workdir_mkdir(path)

        if svc is None:
            return self._no_target_error(fs)

        try:
            svc.mkdir(path)
            return f"Created directory: {path}"
        except Exception as e:
            return f"Error: {e}"
