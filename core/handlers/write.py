"""write — Write content to a file."""

import json
import logging
import re
from typing import Any, Dict

from core.handlers._fs_base import BaseFsHandler

logger = logging.getLogger(__name__)


class WriteHandler(BaseFsHandler):

    @property
    def name(self):
        return "write"

    @property
    def description(self):
        return (
            "Write content to a file, or copy a FileStore file to a filesystem path. "
            "Use destination parameter to specify the filesystem service."
        )

    @property
    def parameters_schema(self):
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path to write to",
                },
                "content": {
                    "type": "string",
                    "description": "Text content to write",
                },
                "file_id": {
                    "type": "string",
                    "description": "Copy this FileStore file to path instead of writing content",
                },
                "destination": {
                    "type": "string",
                    "description": "Filesystem service name. Omit for default.",
                },
            },
            "required": ["path"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        arguments = self._unwrap_json(arguments)
        arguments = self._resolve_expressions(arguments)
        path = arguments.get("path", "")
        if not path:
            return "Error: 'path' is required"
        dest = arguments.get("destination", "")

        _svc_name, path = self._parse_fs_url(path)
        if _svc_name:
            dest = _svc_name

        svc, workdir = self._resolve(dest)

        # Workdir
        if workdir:
            content = (arguments.get("content") or arguments.get("data")
                       or arguments.get("text") or "")
            if not content and not arguments.get("file_id"):
                return "Error: 'content' or 'file_id' is required"
            if arguments.get("file_id"):
                return self._write_from_filestore(arguments["file_id"], path, workdir=workdir)
            return self._workdir_write(path, content)

        if svc is None:
            return self._no_target_error(dest)

        if svc == "filestore":
            return "Error: cannot write to FileStore directly. Use copy or create_file instead."

        # Service
        try:
            file_id = arguments.get("file_id", "")
            if file_id:
                return self._write_from_filestore(file_id, path, svc=svc)

            content = (arguments.get("content") or arguments.get("command")
                       or arguments.get("data") or arguments.get("text") or "")
            if not content:
                return "Error: 'content' or 'file_id' is required"

            service_name = dest or getattr(svc, '_service_id', '')
            self._checkpoint_before(svc, path,
                                    content.encode("utf-8") if isinstance(content, str) else content,
                                    service_name=service_name)
            svc.write_file(path, content.encode("utf-8"))
            return f"Written {len(content)} chars to {path}"
        except Exception as e:
            return f"Error writing '{path}': {e}"

    def _write_from_filestore(self, file_id: str, path: str, svc=None, workdir: str = "") -> str:
        """Copy a file from FileStore to a service or workdir."""
        from core.file_store import FileStore
        store = FileStore.instance()
        # Extract file_id from URL
        url_match = re.search(r'/files/([^/]+)/', file_id)
        if url_match:
            file_id = url_match.group(1)
        entry = store.get(file_id)
        if not entry:
            found = store.find_by_name(file_id)
            if found:
                entry = store.get(found)
        if not entry:
            return f"Error: file_id '{file_id}' not found in FileStore"
        fname, data, _ct = entry
        if workdir:
            import os
            full = self._sandbox_path(path, workdir)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "wb") as f:
                f.write(data)
            return f"Copied {fname} ({len(data):,} bytes) to {path}"
        svc.write_file(path, data)
        return f"Copied {fname} ({len(data):,} bytes) to {path}"
