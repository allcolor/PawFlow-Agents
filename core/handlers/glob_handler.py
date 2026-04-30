"""glob — File pattern matching (like find)."""

from typing import Any, Dict
from core.handlers._fs_base import BaseFsHandler


class GlobHandler(BaseFsHandler):

    @property
    def name(self):
        return "glob"

    @property
    def description(self):
        return (
            "Fast file pattern matching tool that works with any codebase size.\n\n"
            " - Supports glob patterns like '**/*.py' or 'src/**/*.ts'.\n"
            " - Returns matching file paths sorted by modification time.\n"
            " - Use this tool when you need to find files by name or extension pattern.\n"
            " - Use the source parameter to specify a non-default filesystem service.\n\n"
            "When to use glob vs grep:\n"
            " - Use glob to find files by name/path pattern (e.g. all .py files in src/).\n"
            " - Use grep to search for content inside files (e.g. a function name)."
        )

    @property
    def parameters_schema(self):
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern (e.g. *.py, src/**/*.ts)"},
                "path": {"type": "string", "description": "Directory to search in (default: root)"},
                "recursive": {"type": "boolean", "description": "Search recursively (default: true)"},
                "limit": {"type": "integer", "description": "Maximum number of results to return (default: 500)"},
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
        limit = self._parse_limit(arguments.get("limit", 500))
        source = arguments.get("source", "")

        _svc_name, path = self._parse_fs_url(path)
        if _svc_name:
            source = _svc_name

        svc, workdir = self._resolve(source)

        if svc == "filestore":
            return self._filestore_list()

        if workdir:
            return self._workdir_glob(pattern, path, limit=limit)

        if svc is None:
            return self._no_target_error(source)

        try:
            try:
                results = svc.search(path, pattern, recursive,
                                     local=bool(arguments.get("local", False)),
                                     limit=limit)
            except TypeError:
                results = svc.search(path, pattern, recursive,
                                     local=bool(arguments.get("local", False)))
            results = results[:limit]
            return "\n".join(results) if results else "(no matches)"
        except Exception as e:
            return f"Error: {e}"

    @staticmethod
    def _parse_limit(value: Any) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return 500
        if parsed <= 0:
            return 500
        return min(parsed, 5000)
