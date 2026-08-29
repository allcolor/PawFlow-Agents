"""Bounded archive extraction actions for relay filesystem surfaces."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict

from _fs_paths import _rel, _resolve_tool_path


MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_ENTRY_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_FILES = 10_000
MAX_COMPRESSION_RATIO = 200
_COPY_CHUNK_BYTES = 1024 * 1024


def _bounded_int(
    req: Dict[str, Any], name: str, default: int, ceiling: int,
) -> int:
    value = req.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    if value > ceiling:
        raise ValueError(f"{name} exceeds the allowed ceiling")
    return value


def _safe_archive_path(value: str) -> PurePosixPath:
    raw = str(value or "")
    if not raw or "\x00" in raw or "\\" in raw or raw.startswith("/"):
        raise ValueError(f"unsafe archive path: {value}")
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"unsafe archive path: {value}")
    if path.parts and ":" in path.parts[0]:
        raise ValueError(f"unsafe archive path: {value}")
    return path


def _regular_entry(info: zipfile.ZipInfo) -> bool:
    mode = info.external_attr >> 16
    kind = stat.S_IFMT(mode)
    return kind in {0, stat.S_IFREG}


def _tree_digest(files: list[tuple[str, str]]) -> str:
    digest = hashlib.sha256()
    for relative, file_hash in sorted(files):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(file_hash))
    return digest.hexdigest()


def action_extract_zip_subtree(
    root_dir: str, path: str, req: Dict[str, Any],
) -> Dict[str, Any]:
    """Validate and atomically materialize one confined ZIP subtree."""

    archive_path = Path(path)
    if not archive_path.is_file():
        raise FileNotFoundError(f"Archive not found: {req.get('path', path)}")
    if archive_path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("template archive exceeds the 50 MiB size cap")
    raw_dest = str(req.get("dest_path") or "").strip()
    if not raw_dest:
        raise ValueError("dest_path is required")
    destination = _resolve_tool_path(
        root_dir, raw_dest, allow_host_absolute=bool(req.get("local")),
    )
    artifact_root = _safe_archive_path(str(req.get("artifact_root") or ""))
    max_entry = _bounded_int(req, "max_entry_bytes", MAX_ENTRY_BYTES, MAX_ENTRY_BYTES)
    max_total = _bounded_int(req, "max_total_bytes", MAX_TOTAL_BYTES, MAX_TOTAL_BYTES)
    max_files = _bounded_int(req, "max_files", MAX_ARCHIVE_FILES, MAX_ARCHIVE_FILES)
    max_ratio = _bounded_int(
        req, "max_compression_ratio", MAX_COMPRESSION_RATIO, MAX_COMPRESSION_RATIO,
    )

    selected: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
    seen: set[str] = set()
    total = 0
    with zipfile.ZipFile(archive_path, "r") as archive:
        for info in archive.infolist():
            member = _safe_archive_path(info.filename.rstrip("/"))
            if info.is_dir():
                continue
            if not _regular_entry(info):
                raise ValueError(f"archive symlink or special file is prohibited: {info.filename}")
            if info.file_size > max_entry:
                raise ValueError(f"archive entry exceeds size cap: {info.filename}")
            if info.file_size and (
                info.compress_size == 0 or info.file_size > info.compress_size * max_ratio
            ):
                raise ValueError(f"archive entry exceeds compression ratio: {info.filename}")
            try:
                relative = member.relative_to(artifact_root)
            except ValueError:
                continue
            relative_name = relative.as_posix()
            if relative_name.casefold() in seen:
                raise ValueError(f"archive contains a duplicate path: {relative_name}")
            seen.add(relative_name.casefold())
            selected.append((info, relative))
            if len(selected) > max_files:
                raise ValueError("archive artifact_root exceeds file count cap")
            total += info.file_size
            if total > max_total:
                raise ValueError("archive artifact_root exceeds total size cap")
        if not selected:
            raise ValueError("archive artifact_root is missing or empty")

        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(
            f".{destination.name}.extracting-{uuid.uuid4().hex}"
        )
        backup = destination.with_name(f".{destination.name}.backup-{uuid.uuid4().hex}")
        file_hashes: list[tuple[str, str]] = []
        replaced = False
        try:
            temporary.mkdir(parents=False)
            for info, relative in sorted(selected, key=lambda item: item[1].as_posix()):
                target = temporary.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                written = 0
                with archive.open(info, "r") as source, target.open("xb") as output:
                    while True:
                        chunk = source.read(_COPY_CHUNK_BYTES)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > info.file_size or written > max_entry:
                            raise ValueError(f"archive entry size changed: {info.filename}")
                        digest.update(chunk)
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                if written != info.file_size:
                    raise ValueError(f"archive entry size mismatch: {info.filename}")
                file_hashes.append((relative.as_posix(), digest.hexdigest()))
            if destination.exists():
                os.replace(destination, backup)
                replaced = True
            try:
                os.replace(temporary, destination)
            except Exception:
                if replaced:
                    os.replace(backup, destination)
                raise
            if replaced:
                shutil.rmtree(backup)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
            if backup.exists() and destination.exists():
                shutil.rmtree(backup)

    return {
        "path": _rel(str(destination), root_dir),
        "artifact_root": artifact_root.as_posix(),
        "files": len(file_hashes),
        "bytes": total,
        "sha256": _tree_digest(file_hashes),
    }
