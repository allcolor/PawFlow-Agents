"""stat — Get file metadata."""

import json
from typing import Any, Dict
from core.handlers._fs_base import BaseFsHandler


class StatHandler(BaseFsHandler):

    @property
    def name(self):
        return "stat"

    @property
    def description(self):
        return "Get file metadata (size, type, modification time)."

    @property
    def parameters_schema(self):
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path"},
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
            return self._filestore_stat(path)

        if workdir:
            return self._workdir_stat(path)

        if svc is None:
            return self._no_target_error(source)

        try:
            from dataclasses import asdict
            entry = svc.stat(path)
            return json.dumps(asdict(entry), default=str, indent=2)
        except Exception as e:
            return f"Error: {e}"
