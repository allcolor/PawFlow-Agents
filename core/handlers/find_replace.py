"""find_replace — Regex find and replace in a file."""

from typing import Any, Dict
from core.handlers._fs_base import BaseFsHandler


class FindReplaceHandler(BaseFsHandler):

    @property
    def name(self):
        return "find_replace"

    @property
    def description(self):
        return "Find and replace using a regex pattern in a file."

    @property
    def parameters_schema(self):
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path"},
                "pattern": {"type": "string", "description": "Regex pattern to find"},
                "replacement": {"type": "string", "description": "Replacement string"},
                "filesystem": {"type": "string", "description": "Filesystem service name. Omit for default."},
            },
            "required": ["path", "pattern", "replacement"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        arguments = self._unwrap_json(arguments)
        arguments = self._resolve_expressions(arguments)
        path = arguments.get("path", "")
        pattern = arguments.get("pattern", "")
        replacement = arguments.get("replacement", "")
        if not path or not pattern:
            return "Error: 'path' and 'pattern' are required"
        fs = arguments.get("filesystem", "")

        _svc_name, path = self._parse_fs_url(path)
        if _svc_name:
            fs = _svc_name

        svc, workdir = self._resolve(fs)

        if svc == "filestore":
            return "Error: find_replace not supported on FileStore"

        if workdir:
            return self._workdir_find_replace(path, pattern, replacement)

        if svc is None:
            return self._no_target_error(fs)

        try:
            service_name = fs or getattr(svc, '_service_id', '')
            self._checkpoint_before(svc, path, service_name=service_name)
            result = svc.find_replace(path, pattern, replacement)
            return f"Replaced {result.get('replacements', 0)} occurrences in {result.get('path', path)}"
        except Exception as e:
            return f"Error: {e}"
