"""copy — Stream files between filesystem services, FileStore, and workdirs."""

import mimetypes
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, Iterator

from core.handlers._fs_base import BaseFsHandler


_COPY_CHUNK_SIZE = 4 * 1024 * 1024


def _iter_path(path: Path) -> Iterator[bytes]:
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(_COPY_CHUNK_SIZE)
            if not chunk:
                return
            yield chunk


def _copy_path_atomic(source: Path, target: Path) -> int:
    """Copy one disk file with bounded memory and atomic publication."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(
        f".{target.name}.pawflow-copy-{uuid.uuid4().hex}.part")
    try:
        with source.open("rb") as inp, temporary.open("wb") as out:
            shutil.copyfileobj(inp, out, length=_COPY_CHUNK_SIZE)
        size = temporary.stat().st_size
        os.replace(temporary, target)
        return size
    finally:
        temporary.unlink(missing_ok=True)


class CopyHandler(BaseFsHandler):

    @property
    def name(self):
        return "copy"

    @property
    def description(self):
        return (
            "Copy a file between filesystem services and FileStore. "
            "Transfers are streamed with bounded memory; copies within one "
            "relay or host execute directly on that filesystem. "
            "source_service/dest_service: relay name, 'filestore', or omit "
            "for default. Use this to upload into FileStore from a relay path."
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
        source_path = str(arguments.get("source_path") or "")
        dest_path = str(arguments.get("dest_path") or source_path)
        if not source_path:
            return "Error: 'source_path' is required"

        src_name = str(arguments.get("source_service") or "")
        dst_name = str(arguments.get("dest_service") or "")
        src_svc, src_workdir = self._resolve(src_name)
        dst_svc, dst_workdir = self._resolve(dst_name)
        if src_svc is None and src_workdir is None:
            return self._no_target_error(src_name)
        if dst_svc is None and dst_workdir is None:
            return self._no_target_error(dst_name)

        local = self._resolve_local(arguments)
        try:
            if (src_svc not in (None, "filestore")
                    and src_svc is dst_svc
                    and hasattr(src_svc, "copy_file")):
                result = src_svc.copy_file(
                    source_path, dest_path, local=local)
                size = int((result or {}).get("size", 0))
                return self._success(source_path, dest_path, size)

            source_disk = self._source_disk_path(
                src_svc, src_workdir, source_path)
            if isinstance(source_disk, str):
                return source_disk

            if source_disk is not None:
                return self._copy_from_disk(
                    source_disk, dst_svc, dst_workdir, source_path,
                    dest_path, local)

            if not hasattr(src_svc, "copy_file_to_local"):
                return (
                    "Error copying: source filesystem does not support "
                    "streaming downloads")

            if dst_workdir:
                target = Path(self._sandbox_path(dest_path, dst_workdir))
                result = src_svc.copy_file_to_local(
                    source_path, str(target), local=local)
                return self._success(
                    source_path, dest_path, int(result.get("written", 0)))

            with tempfile.TemporaryDirectory(
                    prefix="pawflow-copy-") as temporary:
                staged = Path(temporary) / "payload"
                result = src_svc.copy_file_to_local(
                    source_path, str(staged), local=local)
                written = int(result.get("written", staged.stat().st_size))
                if dst_svc == "filestore":
                    file_id = self._store_path_in_filestore(staged, dest_path)
                    return (
                        f"Copied {self._name(source_path)} ({written:,} bytes) "
                        f"to FileStore: {file_id}")
                self._stream_path_to_service(
                    dst_svc, staged, dest_path, local)
                return self._success(source_path, dest_path, written)
        except Exception as exc:
            return f"Error copying: {exc}"

    def _source_disk_path(self, svc, workdir, path):
        if svc == "filestore":
            return self._filestore_disk_path(path)
        if workdir:
            full = Path(self._sandbox_path(path, workdir))
            if not full.is_file():
                return f"Error: '{path}' not found in workspace"
            return full
        return None

    def _copy_from_disk(self, source: Path, dst_svc, dst_workdir,
                        source_path: str, dest_path: str,
                        local: bool) -> str:
        size = source.stat().st_size
        if dst_svc == "filestore":
            file_id = self._store_path_in_filestore(source, dest_path)
            return (
                f"Copied {self._name(source_path)} ({size:,} bytes) "
                f"to FileStore: {file_id}")
        if dst_workdir:
            target = Path(self._sandbox_path(dest_path, dst_workdir))
            size = _copy_path_atomic(source, target)
        else:
            self._stream_path_to_service(
                dst_svc, source, dest_path, local)
        return self._success(source_path, dest_path, size)

    @staticmethod
    def _stream_path_to_service(svc, source: Path, dest_path: str,
                                local: bool) -> None:
        writer = getattr(svc, "write_file_stream", None)
        if not callable(writer):
            raise RuntimeError(
                "destination filesystem does not support streaming uploads")
        writer(
            dest_path, _iter_path(source),
            expected_size=source.stat().st_size, local=local)

    @staticmethod
    def _name(path: str) -> str:
        return path.replace("\\", "/").rsplit("/", 1)[-1]

    @classmethod
    def _success(cls, source_path: str, dest_path: str, size: int) -> str:
        return (
            f"Copied {cls._name(source_path)} ({size:,} bytes): "
            f"{source_path} → {dest_path}")

    def _store_path_in_filestore(self, source_path: Path,
                                  dest_path: str) -> str:
        from core.file_store import FileStore

        filename = self._name(dest_path)
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        return FileStore.instance().store_file(
            filename, str(source_path), mime,
            user_id=self._user_id,
            conversation_id=self._conversation_id)

    def _filestore_disk_path(self, path):
        import re
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
        return Path(disk_path)
