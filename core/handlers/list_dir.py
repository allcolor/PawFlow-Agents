"""list_dir — List directory contents."""

from typing import Any, Dict
from core.handlers._fs_base import BaseFsHandler


class ListDirHandler(BaseFsHandler):

    @property
    def name(self):
        return "list_dir"

    @property
    def description(self):
        return "List files and directories. Use source='filestore' to list server FileStore."

    @property
    def parameters_schema(self):
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path (default: root)"},
                "source": {"type": "string", "description": "Filesystem service name. Omit for default."},
            },
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        arguments = self._unwrap_json(arguments)
        arguments = self._resolve_expressions(arguments)
        path = arguments.get("path", ".")
        source = arguments.get("source", "")

        _svc_name, path = self._parse_fs_url(path)
        if _svc_name:
            source = _svc_name

        svc, workdir = self._resolve(source)

        if svc == "filestore":
            return self._filestore_list()

        if workdir:
            return self._workdir_list(path)

        if svc is None:
            return self._no_target_error(source)

        try:
            entries = svc.list_dir(path, local=bool(arguments.get("local", False)))
            _svc_id = source or getattr(svc, 'service_id', '') or 'fs'
            _base = f"fs://{_svc_id}/{path.rstrip('/')}/" if path != "." else f"fs://{_svc_id}/"
            lines = []
            for e in entries:
                kind = "📁" if e.kind == "directory" else "📄"
                size = f" ({e.size} bytes)" if e.kind == "file" else ""
                lines.append(f"{kind} {_base}{e.name}{size}")
            return "\n".join(lines) if lines else "(empty directory)"
        except Exception as e:
            return f"Error: {e}"
