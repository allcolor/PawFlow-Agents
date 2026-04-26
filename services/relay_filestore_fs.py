"""RelayFileStoreFs — virtualized read-only FUSE proxy for the FileStore.

Maps the FileStore to a hierarchical FS layout the relay can mount via
FUSE, organised conversation-first so each linked conv is its own
sub-tree:

    /                                       → directory listing every
                                              conv_id that has at least
                                              one file owned by the
                                              relay's user.
    /<conv_id>                              → directory listing every
                                              file_id stored under that
                                              conv (and accessible to
                                              the user).
    /<conv_id>/<file_id>                    → directory containing one
                                              entry, the file's
                                              original filename.
    /<conv_id>/<file_id>/<filename>         → the file content
                                              (read-only).

The conv-first layout matches the on-disk FileStore layout
(`data/runtime/files/<user_id>/<conv_id>/...`) and makes it cheap for
downstream tools to bind-mount a single conv subtree if they only need
one. Cross-conv access is still possible by walking up to the root.

Write/create/unlink/rename/etc. all return EROFS — files are managed
via the FileStore HTTP/MCP APIs, not via the FUSE mount. This avoids
the ambiguity of "how do you cp into /<conv_id>/<NEW_id>/foo when the
id is assigned by FileStore.store()?".

Protocol (over the existing /ws/relay/<id> WebSocket, methods prefixed
with `ffs.` to disambiguate from the cc-sessions sfs.* protocol):

    relay  → server: {"type": "relay_request",
                     "request_id": "<id>",
                     "method": "ffs.<op>",
                     "args": {...}}

    server → relay: {"type": "relay_response",
                     "request_id": "<id>",
                     "data": {...}}     # success
                  or {"type": "relay_response",
                     "request_id": "<id>",
                     "error": "<code>",
                     "errno": <int>}

Methods: ffs.getattr, ffs.readdir, ffs.open, ffs.read, ffs.release,
ffs.statfs. Anything else (write side) returns EROFS or ENOSYS.
"""

import base64
import errno
import logging
import os
import stat as _stat
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from core.file_store import FileStore

logger = logging.getLogger(__name__)


_ERRNO_NAME = {v: k for k, v in errno.__dict__.items()
               if isinstance(v, int) and k.startswith("E")}


def _errno_response(eno: int, msg: str = "") -> Dict[str, Any]:
    name = _ERRNO_NAME.get(eno, f"E{eno}")
    if msg:
        return {"error": name, "errno": eno, "message": msg}
    return {"error": name, "errno": eno}


class RelayFileStoreFs:
    """Per-relay FileStore-as-FS handler.

    A single instance is attached to one RelayService. The relay's
    `user_id` is captured at construction; every op filters FileStore
    entries through `check_access(file_id, user_id)` so a forged path
    on the wire can't escalate scope.
    """

    ALLOWED_METHODS = frozenset({
        # Read-only ops — the only ones we serve.
        "ffs.getattr", "ffs.readdir",
        "ffs.open", "ffs.read", "ffs.release",
        "ffs.statfs",
        # Write-side ops are accepted by name so we can return EROFS
        # rather than ENOSYS (libc maps EROFS to a clearer user error).
        "ffs.create", "ffs.write", "ffs.truncate",
        "ffs.unlink", "ffs.mkdir", "ffs.rmdir",
        "ffs.rename", "ffs.chmod", "ffs.utimens",
    })

    _RO_METHODS = frozenset({
        "ffs.getattr", "ffs.readdir", "ffs.open",
        "ffs.read", "ffs.release", "ffs.statfs",
    })

    MAX_READ_CHUNK = 1 * 1024 * 1024  # 1 MB

    def __init__(self, user_id: str):
        if not user_id:
            raise ValueError("RelayFileStoreFs requires a non-empty user_id")
        self._user_id = user_id
        self._fd_lock = threading.Lock()
        self._fds: Dict[int, int] = {}  # fh → real fd
        self._next_fh = 1

    # ------------------------------------------------------------------
    # Path parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _split_path(path: str) -> Tuple[str, str, str]:
        """Return (conv_id, file_id, filename). Empty strings for upper levels.

        '/' → ('', '', '')                    root
        '/<conv>' → (conv, '', '')            conversation directory
        '/<conv>/<fid>' → (conv, fid, '')     file_id directory
        '/<conv>/<fid>/<name>' → (conv, fid, name)   the file
        Anything else → raises FileNotFoundError.
        """
        if path is None:
            raise FileNotFoundError("path is required")
        if not path or path == "/":
            return ("", "", "")
        parts = path.strip("/").split("/")
        if len(parts) == 1:
            return (parts[0], "", "")
        if len(parts) == 2:
            return (parts[0], parts[1], "")
        if len(parts) == 3:
            return (parts[0], parts[1], parts[2])
        raise FileNotFoundError(f"path too deep: {path!r}")

    def _entry_for(self, conv_id: str, file_id: str) -> Optional[Dict[str, Any]]:
        """Return metadata if file_id is accessible AND lives under conv_id.

        The conv check is what gives us per-conv isolation: a file_id
        from conv A surfaced through `/<convB>/<file_id>/...` returns
        ENOENT instead of leaking the bytes.
        """
        if not conv_id or not file_id:
            return None
        fs = FileStore.instance()
        meta = fs.get_metadata(file_id)
        if meta is None:
            return None
        if not fs.check_access(file_id, user_id=self._user_id):
            return None
        if meta.get("conversation_id", "") != conv_id:
            return None
        return meta

    def _list_user_convs(self) -> list:
        """Return sorted list of conv_ids that have at least one user file."""
        fs = FileStore.instance()
        rows = fs.list_files(user_id=self._user_id, include_internal=False)
        return sorted({r.get("conversation_id", "") for r in rows
                       if r.get("conversation_id", "")})

    def _list_files_in_conv(self, conv_id: str) -> list:
        """Return sorted list of file_ids in `conv_id` accessible to the user."""
        if not conv_id:
            return []
        fs = FileStore.instance()
        rows = fs.list_files(user_id=self._user_id,
                              conversation_id=conv_id,
                              include_internal=False)
        return sorted(r["file_id"] for r in rows)

    def _total_user_file_count(self) -> int:
        """Sum of files visible to the user, across every conv (for statfs)."""
        fs = FileStore.instance()
        rows = fs.list_files(user_id=self._user_id, include_internal=False)
        return sum(1 for r in rows if r.get("conversation_id", ""))

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Release every open fd. Call on relay disconnect."""
        with self._fd_lock:
            fds = list(self._fds.values())
            self._fds.clear()
        for fd in fds:
            try:
                os.close(fd)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def handle(self, method: str, args: Dict[str, Any]) -> Dict[str, Any]:
        if method not in self.ALLOWED_METHODS:
            return _errno_response(errno.ENOSYS, f"method {method!r} not allowed")
        if method not in self._RO_METHODS:
            return _errno_response(errno.EROFS,
                                    f"FileStore FUSE is read-only ({method})")
        try:
            handler = getattr(self, "_op_" + method.split(".", 1)[1])
        except AttributeError:
            return _errno_response(errno.ENOSYS, f"unimplemented {method!r}")
        try:
            return handler(args or {})
        except FileNotFoundError as e:
            return _errno_response(errno.ENOENT, str(e))
        except IsADirectoryError as e:
            return _errno_response(errno.EISDIR, str(e))
        except NotADirectoryError as e:
            return _errno_response(errno.ENOTDIR, str(e))
        except PermissionError as e:
            return _errno_response(errno.EACCES, str(e))
        except OSError as e:
            return _errno_response(e.errno or errno.EIO, str(e))
        except Exception as e:
            logger.exception("[filestore-fs] %s failed for user=%s",
                             method, self._user_id)
            return _errno_response(errno.EIO, str(e))

    # ------------------------------------------------------------------
    # Operations
    # ------------------------------------------------------------------

    def _dir_attrs(self, mtime: float = 0.0) -> Dict[str, Any]:
        if mtime <= 0:
            mtime = time.time()
        return {
            "st_mode": _stat.S_IFDIR | 0o555,
            "st_size": 0,
            "st_mtime": mtime,
            "st_atime": mtime,
            "st_ctime": mtime,
            "st_uid": os.getuid() if hasattr(os, "getuid") else 0,
            "st_gid": os.getgid() if hasattr(os, "getgid") else 0,
            "st_nlink": 2,
        }

    def _file_attrs(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        mtime = float(entry.get("created_at", 0) or time.time())
        return {
            "st_mode": _stat.S_IFREG | 0o444,
            "st_size": int(entry.get("size", 0)),
            "st_mtime": mtime,
            "st_atime": mtime,
            "st_ctime": mtime,
            "st_uid": os.getuid() if hasattr(os, "getuid") else 0,
            "st_gid": os.getgid() if hasattr(os, "getgid") else 0,
            "st_nlink": 1,
        }

    def _op_getattr(self, args: Dict[str, Any]) -> Dict[str, Any]:
        conv_id, file_id, filename = self._split_path(args.get("path", ""))
        if not conv_id:
            # root
            return {"data": self._dir_attrs()}
        if not file_id:
            # /<conv>
            if conv_id not in self._list_user_convs():
                raise FileNotFoundError(f"unknown conv_id={conv_id!r}")
            return {"data": self._dir_attrs()}
        entry = self._entry_for(conv_id, file_id)
        if entry is None:
            raise FileNotFoundError(
                f"unknown file_id={file_id!r} under conv={conv_id!r}")
        if not filename:
            # /<conv>/<file_id>
            return {"data": self._dir_attrs(
                mtime=float(entry.get("created_at", 0) or 0))}
        if filename != entry["filename"]:
            raise FileNotFoundError(
                f"filename mismatch under {conv_id}/{file_id}: "
                f"got {filename!r}, expected {entry['filename']!r}")
        return {"data": self._file_attrs(entry)}

    def _op_readdir(self, args: Dict[str, Any]) -> Dict[str, Any]:
        conv_id, file_id, filename = self._split_path(args.get("path", ""))
        if filename:
            raise NotADirectoryError(f"not a directory: {args.get('path')!r}")
        if not conv_id:
            # /
            return {"data": {"entries": self._list_user_convs()}}
        if not file_id:
            # /<conv>
            if conv_id not in self._list_user_convs():
                raise FileNotFoundError(f"unknown conv_id={conv_id!r}")
            return {"data": {"entries": self._list_files_in_conv(conv_id)}}
        # /<conv>/<file_id>
        entry = self._entry_for(conv_id, file_id)
        if entry is None:
            raise FileNotFoundError(
                f"unknown file_id={file_id!r} under conv={conv_id!r}")
        return {"data": {"entries": [entry["filename"]]}}

    def _op_open(self, args: Dict[str, Any]) -> Dict[str, Any]:
        flags = int(args.get("flags", os.O_RDONLY))
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC):
            return _errno_response(errno.EROFS,
                                    "FileStore FUSE is read-only")
        conv_id, file_id, filename = self._split_path(args.get("path", ""))
        if not conv_id or not file_id or not filename:
            raise FileNotFoundError(f"open: not a file path: {args.get('path')!r}")
        entry = self._entry_for(conv_id, file_id)
        if entry is None:
            raise FileNotFoundError(
                f"unknown file_id={file_id!r} under conv={conv_id!r}")
        if filename != entry["filename"]:
            raise FileNotFoundError(
                f"filename mismatch under {conv_id}/{file_id}: got {filename!r}")
        disk_path = FileStore.instance().get_disk_path(
            file_id, user_id=self._user_id)
        if disk_path is None:
            raise FileNotFoundError(
                f"file_id={file_id} index entry present but bytes missing")
        fd = os.open(disk_path, os.O_RDONLY)
        with self._fd_lock:
            fh = self._next_fh
            self._next_fh += 1
            self._fds[fh] = fd
        return {"data": {"fh": fh}}

    def _op_read(self, args: Dict[str, Any]) -> Dict[str, Any]:
        fh = int(args.get("fh", -1))
        offset = int(args.get("offset", 0))
        size = int(args.get("size", 0))
        if size <= 0:
            return {"data": {"data_b64": ""}}
        if size > self.MAX_READ_CHUNK:
            return _errno_response(errno.EINVAL,
                                    f"size {size} exceeds {self.MAX_READ_CHUNK}")
        with self._fd_lock:
            fd = self._fds.get(fh)
        if fd is None:
            return _errno_response(errno.EBADF, f"unknown fh {fh}")
        os.lseek(fd, offset, os.SEEK_SET)
        chunk = os.read(fd, size)
        return {"data": {"data_b64": base64.b64encode(chunk).decode("ascii")}}

    def _op_release(self, args: Dict[str, Any]) -> Dict[str, Any]:
        fh = int(args.get("fh", -1))
        with self._fd_lock:
            fd = self._fds.pop(fh, None)
        if fd is None:
            return _errno_response(errno.EBADF, f"unknown fh {fh}")
        try:
            os.close(fd)
        except OSError as e:
            return _errno_response(e.errno or errno.EIO, str(e))
        return {"data": {}}

    def _op_statfs(self, args: Dict[str, Any]) -> Dict[str, Any]:
        # FileStore is content-addressed, no real block info. Return
        # cosmetically-sane numbers so `df` doesn't blow up; the relay's
        # FUSE mount surfaces these directly to userspace tools.
        return {"data": {
            "f_bsize": 4096,
            "f_frsize": 4096,
            "f_blocks": 0,
            "f_bfree": 0,
            "f_bavail": 0,
            "f_files": self._total_user_file_count(),
            "f_ffree": 0,
            "f_favail": 0,
            "f_namemax": 255,
        }}
