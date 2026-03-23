"""StorageResolver — unified file I/O across FileStore and filesystem services.

Provides a single interface for reading/writing files regardless of the
storage backend. Tools use this instead of hardcoding FileStore.

Targets:
    "filestore" (default) — temporary FileStore (TTL-based)
    "fs:<service_name>"   — filesystem relay service
    "<service_name>"      — filesystem relay service (shorthand)
"""

import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Aliases that resolve to FileStore
_FILESTORE_ALIASES = {"filestore", "store", "server", ""}


class StorageResolver:
    """Resolve storage targets and perform I/O operations."""

    def __init__(self, user_id: str = "", fs_resolver=None):
        """
        Args:
            user_id: Current user ID (for FileStore ownership)
            fs_resolver: Callable(service_name, user_id) -> filesystem service instance
        """
        self._user_id = user_id
        self._fs_resolver = fs_resolver

    def write(self, destination: str, path: str, data: bytes,
              content_type: str = "") -> Dict[str, Any]:
        """Write data to a destination.

        Args:
            destination: "filestore", "fs:service_name", or "service_name"
            path: Filename or path
            data: File content as bytes
            content_type: MIME type (optional)

        Returns:
            {"file_id": ..., "url": ..., "path": ..., "destination": ...}
        """
        target = self._normalize(destination)

        if target in _FILESTORE_ALIASES:
            return self._write_filestore(path, data, content_type)
        else:
            return self._write_filesystem(target, path, data)

    def read(self, source: str, path: str) -> Tuple[bytes, str]:
        """Read data from a source.

        Args:
            source: "filestore", "fs:service_name", or "service_name"
            path: Filename, file_id, or path

        Returns:
            (data, content_type)
        """
        target = self._normalize(source)

        if target in _FILESTORE_ALIASES:
            return self._read_filestore(path)
        else:
            return self._read_filesystem(target, path)

    def _normalize(self, target: str) -> str:
        """Normalize target name."""
        if not target:
            return ""
        t = target.strip().lower()
        if t in _FILESTORE_ALIASES:
            return ""
        if t.startswith("fs:"):
            return t[3:]
        return t

    def _write_filestore(self, filename: str, data: bytes,
                          content_type: str = "") -> Dict[str, Any]:
        """Write to FileStore."""
        from core.file_store import FileStore
        store = FileStore.instance()
        file_id = store.store(filename, data,
                              content_type=content_type,
                              user_id=self._user_id)
        base_url = store.get_base_url() if hasattr(store, 'get_base_url') else ""
        url = f"{base_url}/files/{file_id}/{filename}" if base_url else f"/files/{file_id}/{filename}"
        return {
            "file_id": file_id,
            "url": url,
            "path": filename,
            "destination": "filestore",
        }

    def _read_filestore(self, path: str) -> Tuple[bytes, str]:
        """Read from FileStore by file_id or filename."""
        import os
        from core.file_store import FileStore
        store = FileStore.instance()
        name = os.path.basename(path) or path

        # Try direct file_id lookup
        result = store.get(name, user_id=self._user_id)
        if result:
            return result[1], result[2] if len(result) > 2 else ""

        # Try filename match
        for f in store.list_files():
            if f.get("filename") == name:
                result = store.get(f["file_id"])
                if result:
                    return result[1], result[2] if len(result) > 2 else ""

        raise FileNotFoundError(f"Not found in FileStore: {path}")

    def _write_filesystem(self, service_name: str, path: str,
                           data: bytes) -> Dict[str, Any]:
        """Write to a filesystem service."""
        svc = self._resolve_fs(service_name)
        if not svc:
            raise ValueError(f"Filesystem service '{service_name}' not found")

        # Prefer direct write_file (FilesystemService unified relay)
        if hasattr(svc, 'write_file'):
            svc.write_file(path, data)
        elif hasattr(svc, 'execute_command'):
            import base64
            svc.execute_command({
                "action": "write",
                "path": path,
                "content_b64": base64.b64encode(data).decode(),
            })
        else:
            raise TypeError(f"Service '{service_name}' has no write method")

        return {
            "path": path,
            "destination": service_name,
            "service": service_name,
        }

    def _read_filesystem(self, service_name: str, path: str) -> Tuple[bytes, str]:
        """Read from a filesystem service."""
        svc = self._resolve_fs(service_name)
        if not svc:
            raise ValueError(f"Filesystem service '{service_name}' not found")

        # Prefer direct read_file (FilesystemService unified relay)
        if hasattr(svc, 'read_file'):
            data = svc.read_file(path)
            return data, ""
        elif hasattr(svc, 'execute_command'):
            import base64
            result = svc.execute_command({
                "action": "read",
                "path": path,
            })
            if isinstance(result, dict) and result.get("content_b64"):
                data = base64.b64decode(result["content_b64"])
                return data, result.get("content_type", "")
            elif isinstance(result, dict) and result.get("content"):
                return result["content"].encode("utf-8"), "text/plain"

        raise FileNotFoundError(f"Not found on '{service_name}': {path}")

    # Aliases that auto-resolve to the first available filesystem service
    _FS_AUTO_ALIASES = {"workspace", "ws", "local"}

    # Filesystem service types (checked in order for auto-detection)
    _FS_TYPES = ("filesystem", "browserFilesystem", "serverFilesystem",
                 "googleDrive", "oneDrive")

    def _resolve_fs(self, service_name: str):
        """Resolve a filesystem service by name.

        "workspace", "ws", "local" → auto-detect first available FS.
        Named service → look up by exact ID.
        Fallback: if only one FS exists, use it regardless of name.
        """
        # Aliases → auto-detect
        if service_name.lower() in self._FS_AUTO_ALIASES:
            return self._find_first_fs()

        # Try exact match via resolver or registries
        if self._fs_resolver:
            svc = self._fs_resolver(service_name, self._user_id)
            if svc:
                return svc

        svc = self._lookup_service(service_name)
        if svc:
            return svc

        # Fallback: if the name is unknown but only one FS exists, use it
        only = self._find_first_fs()
        if only:
            logger.info("Service '%s' not found, using only available FS", service_name)
            return only

        return None

    def _lookup_service(self, service_name: str):
        """Look up a service by exact ID in registries."""
        try:
            from gui.services.global_service_registry import GlobalServiceRegistry
            svc = GlobalServiceRegistry.get_instance().get_live_instance(service_name)
            if svc:
                return svc
        except Exception:
            pass
        try:
            from gui.services.user_service_registry import UserServiceRegistry
            svc = UserServiceRegistry.get_instance().get_live_instance(
                self._user_id, service_name)
            if svc:
                return svc
        except Exception:
            pass
        return None

    def _find_first_fs(self):
        """Auto-detect the first available filesystem service."""
        # Global services
        try:
            from gui.services.global_service_registry import GlobalServiceRegistry
            greg = GlobalServiceRegistry.get_instance()
            for sid, sdef in greg.get_all_definitions().items():
                if not getattr(sdef, "enabled", True):
                    continue
                if getattr(sdef, "service_type", "") in self._FS_TYPES:
                    svc = greg.get_live_instance(sid)
                    if svc:
                        return svc
        except Exception:
            pass
        # User services
        if self._user_id:
            try:
                from gui.services.user_service_registry import UserServiceRegistry
                ureg = UserServiceRegistry.get_instance()
                for fs_type in self._FS_TYPES:
                    compatible = ureg.get_compatible(fs_type, self._user_id)
                    for sdef in compatible:
                        if sdef.enabled:
                            svc = ureg.get_live_instance(self._user_id, sdef.service_id)
                            if svc:
                                return svc
            except Exception:
                pass
        return None
