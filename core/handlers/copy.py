"""copy — Copy files between filesystem services, FileStore, and agent workdir."""

import logging
import mimetypes
import os
import re
import shutil
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
            "source_service/dest_service: relay name, 'filestore', or omit "
            "for default. Use this to UPLOAD into the FileStore from a relay "
            "path — the read-only `/filestore` FUSE mount can't accept writes "
            "directly (`cp ... /filestore/...` returns EROFS), so call `copy` "
            "with `dest_service=\"filestore\"` instead. The new file_id is "
            "returned in the result and the file becomes visible at "
            "`/filestore/<conv_id>/<file_id>/<filename>` immediately afterwards."
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
            _local = bool(arguments.get("local", False))
            if dst_svc == "filestore" and src_svc is None and src_workdir:
                full = self._sandbox_path(source_path, src_workdir)
                if not os.path.exists(full):
                    return f"Error: '{source_path}' not found in workspace"
                fid = self._store_path_in_filestore(full, dest_path)
                return f"Copied {os.path.basename(source_path)} to FileStore: {fid}"

            if src_svc == "filestore" and dst_svc is None and dst_workdir:
                src_disk = self._filestore_disk_path(source_path)
                if isinstance(src_disk, str):
                    return src_disk
                full = self._sandbox_path(dest_path, dst_workdir)
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(src_disk, "rb") as inp, open(full, "wb") as out:
                    shutil.copyfileobj(inp, out, length=1024 * 1024)
                return f"Copied {source_path} → {dest_path}"

            # Read from source
            data = self._read_bytes(src_svc, src_workdir, source_path, local=_local)
            if isinstance(data, str):
                return data  # error message

            # Write to dest
            result = self._write_bytes(dst_svc, dst_workdir, dest_path, data, local=_local)
            fname = source_path.rsplit("/", 1)[-1] if "/" in source_path else source_path
            return f"Copied {fname} ({len(data):,} bytes): {source_path} → {dest_path}"
        except Exception as e:
            return f"Error copying: {e}"

    def _read_bytes(self, svc, workdir, path, local: bool = False):
        """Read raw bytes from source."""
        if svc == "filestore":
            disk_path = self._filestore_disk_path(path)
            if isinstance(disk_path, str):
                return disk_path
            with open(disk_path, "rb") as f:
                return f.read()

        if workdir:
            full = self._sandbox_path(path, workdir)
            if not os.path.exists(full):
                return f"Error: '{path}' not found in workspace"
            with open(full, "rb") as f:
                return f.read()

        return svc.read_file(path, local=local)

    def _write_bytes(self, svc, workdir, path, data, local: bool = False):
        """Write raw bytes to dest."""
        if svc == "filestore":
            from core.file_store import FileStore
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

        svc.write_file(path, data, local=local)
        return path

    def _store_path_in_filestore(self, source_path: str, dest_path: str) -> str:
        from core.file_store import FileStore

        fname = dest_path.rsplit("/", 1)[-1] if "/" in dest_path else dest_path
        mime = mimetypes.guess_type(fname)[0] or "application/octet-stream"
        return FileStore.instance().store_file(
            fname, source_path, mime,
            user_id=self._user_id,
            conversation_id=getattr(self, '_conversation_id', '') or '')

    def _filestore_disk_path(self, path):
        from core.file_store import FileStore

        store = FileStore.instance()
        match = re.search(r'/?(?:files/)?([a-f0-9]{12})(?:/|$)', path)
        file_id = match.group(1) if match else path.split("/")[0]
        disk_path = store.get_disk_path(file_id, user_id=self._user_id)
        if disk_path is None:
            found = store.find_by_name(file_id, user_id=self._user_id)
            if found:
                disk_path = store.get_disk_path(found, user_id=self._user_id)
        if disk_path is None:
            return f"Error: '{file_id}' not found in FileStore"
        return disk_path
