"""exists — Check if a file or directory exists."""

from typing import Any, Dict
from core.handlers._fs_base import BaseFsHandler


class ExistsHandler(BaseFsHandler):

    @property
    def name(self):
        return "exists"

    @property
    def description(self):
        return "Check if a file or directory exists."

    @property
    def parameters_schema(self):
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to check"},
                "source": {"type": "string", "description": "Filesystem service name. Omit for default."},
            },
            "required": ["path"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        arguments = self._unwrap_json(arguments)
        arguments = self._resolve_expressions(arguments)
        path = arguments.get("path", "")
        if not path:
            return "Error: 'path' is required"
        source = arguments.get("source", "")

        _svc_name, path = self._parse_fs_url(path)
        if _svc_name:
            source = _svc_name

        svc, workdir = self._resolve(source)

        if svc == "filestore":
            return self._filestore_exists(path)

        if workdir:
            return self._workdir_exists(path)

        if svc is None:
            return self._no_target_error(source)

        try:
            result = svc.exists(path, local=bool(arguments.get("local", False)))
            return f"{'Exists' if result else 'Does not exist'}: {path}"
        except Exception as e:
            return f"Error: {e}"
