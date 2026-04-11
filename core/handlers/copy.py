"""copy — Copy files between filesystem services, FileStore, and agent workdir."""

import logging
from typing import Any, Dict

from core.handlers._fs_base import BaseFsHandler

logger = logging.getLogger(__name__)


class CopyHandler(BaseFsHandler):

    @property
    def name(self):
        return "copy"

    @property
    def description(self):
        return (
            "Copy a file between filesystem services and FileStore. "
            "source_service/dest_service: relay name, 'filestore', or omit for default."
        )

    @property
    def parameters_schema(self):
        return {
            "type": "object",
            "properties": {
                "source_service": {
                    "type": "string",
                    "description": "Source filesystem service (omit for default)",
                },
                "source_path": {
                    "type": "string",
                    "description": "Path on source",
                },
                "dest_service": {
                    "type": "string",
                    "description": "Destination filesystem service (omit for default)",
                },
                "dest_path": {
                    "type": "string",
                    "description": "Path on destination",
                },
            },
            "required": ["source_path"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        arguments = self._unwrap_json(arguments)
        arguments = self._resolve_expressions(arguments)
        source_path = arguments.get("source_path", "")
        dest_path = arguments.get("dest_path", "") or source_path
        if not source_path:
            return "Error: 'source_path' is required"

        src_svc_name = arguments.get("source_service", "")
        dst_svc_name = arguments.get("dest_service", "")

        # Resolve source
        src_svc, src_workdir = self._resolve(src_svc_name)
        # Resolve dest
        dst_svc, dst_workdir = self._resolve(dst_svc_name)

        if src_svc is None and src_workdir is None:
            return self._no_target_error(src_svc_name)
        if dst_svc is None and dst_workdir is None:
            return self._no_target_error(dst_svc_name)

        try:
            # Read from source
            data = self._read_bytes(src_svc, src_workdir, source_path)
            if isinstance(data, str):
                return data  # error message

            # Write to dest
            result = self._write_bytes(dst_svc, dst_workdir, dest_path, data)
            fname = source_path.rsplit("/", 1)[-1] if "/" in source_path else source_path
            return f"Copied {fname} ({len(data):,} bytes): {source_path} → {dest_path}"
        except Exception as e:
            return f"Error copying: {e}"

    def _read_bytes(self, svc, workdir, path):
        """Read raw bytes from source."""
        import os
        import re

        if svc == "filestore":
            from core.file_store import FileStore
            store = FileStore.instance()
            _fid_match = re.search(r'/?(?:files/)?([a-f0-9]{12})(?:/|$)', path)
            file_id = _fid_match.group(1) if _fid_match else path.split("/")[0]
            entry = store.get(file_id)
            if not entry:
                found = store.find_by_name(file_id)
                if found:
                    entry = store.get(found)
            if not entry:
                return f"Error: '{file_id}' not found in FileStore"
            return entry[1]  # data bytes

        if workdir:
            full = self._sandbox_path(path, workdir)
            if not os.path.exists(full):
                return f"Error: '{path}' not found in workspace"
            with open(full, "rb") as f:
                return f.read()

        return svc.read_file(path)

    def _write_bytes(self, svc, workdir, path, data):
        """Write raw bytes to dest."""
        import os

        if svc == "filestore":
            from core.file_store import FileStore
            import mimetypes
            fname = path.rsplit("/", 1)[-1] if "/" in path else path
            mime = mimetypes.guess_type(fname)[0] or "application/octet-stream"
            fid = FileStore.instance().store(
                fname, data, mime,
                user_id=self._user_id,
                conversation_id=getattr(self, '_conversation_id', '') or '')
            return fid

        if workdir:
            full = self._sandbox_path(path, workdir)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "wb") as f:
                f.write(data)
            return path

        svc.write_file(path, data)
        return path
