"""glob — File pattern matching (like find)."""

from typing import Any, Dict
from core.handlers._fs_base import BaseFsHandler


class GlobHandler(BaseFsHandler):

    @property
    def name(self):
        return "glob"

    @property
    def description(self):
        return "Search for files matching a glob pattern (e.g. **/*.py)."

    @property
    def parameters_schema(self):
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern (e.g. *.py, src/**/*.ts)"},
                "path": {"type": "string", "description": "Directory to search in (default: root)"},
                "recursive": {"type": "boolean", "description": "Search recursively (default: true)"},
                "source": {"type": "string", "description": "Filesystem service name. Omit for default."},
            },
            "required": ["pattern"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        arguments = self._unwrap_json(arguments)
        arguments = self._resolve_expressions(arguments)
        pattern = arguments.get("pattern", "*")
        path = arguments.get("path", ".")
        recursive = arguments.get("recursive", True)
        source = arguments.get("source", "")

        _svc_name, path = self._parse_fs_url(path)
        if _svc_name:
            source = _svc_name

        svc, workdir = self._resolve(source)

        if svc == "filestore":
            return self._filestore_list()

        if workdir:
            return self._workdir_glob(pattern, path)

        if svc is None:
            return self._no_target_error(source)

        try:
            results = svc.search(path, pattern, recursive)
            return "\n".join(results) if results else "(no matches)"
        except Exception as e:
            return f"Error: {e}"
