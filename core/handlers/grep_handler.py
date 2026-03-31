"""grep — Regex content search in files."""

from typing import Any, Dict
from core.handlers._fs_base import BaseFsHandler


class GrepHandler(BaseFsHandler):

    @property
    def name(self):
        return "grep"

    @property
    def description(self):
        return "Search file contents with a regex pattern. Returns path:line_number:line."

    @property
    def parameters_schema(self):
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern to search for"},
                "path": {"type": "string", "description": "Directory or file to search in (default: root)"},
                "recursive": {"type": "boolean", "description": "Search recursively (default: true)"},
                "limit": {"type": "integer", "description": "Max results (default: 250)"},
                "source": {"type": "string", "description": "Filesystem service name. Omit for default."},
            },
            "required": ["pattern"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        arguments = self._unwrap_json(arguments)
        arguments = self._resolve_expressions(arguments)
        pattern = arguments.get("pattern", "")
        if not pattern:
            return "Error: 'pattern' is required"
        path = arguments.get("path", ".")
        recursive = arguments.get("recursive", True)
        limit = int(arguments.get("limit", 250) or 250)
        source = arguments.get("source", "")

        _svc_name, path = self._parse_fs_url(path)
        if _svc_name:
            source = _svc_name

        svc, workdir = self._resolve(source)

        if svc == "filestore":
            return "Error: grep is not supported on FileStore"

        if workdir:
            return self._workdir_grep(pattern, path, recursive, limit)

        if svc is None:
            return self._no_target_error(source)

        try:
            results = svc.grep(path, pattern, recursive)
            lines = [f"{r['path']}:{r['line_number']}: {r['line']}" for r in results[:limit]]
            total = len(results)
            if total > limit:
                lines.append(f"... and {total - limit} more matches (use limit to see more)")
            return "\n".join(lines) if lines else "(no matches)"
        except Exception as e:
            return f"Error: {e}"
