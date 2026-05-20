"""RelaySkillsFs — virtualized read-only FUSE proxy for the skills repository.

Exposes the server-side Agent Skills repository to a relay container so
non-CLI providers (whose tools execute inside the relay) can reach the
asset files referenced by a skill's instructions — e.g.
``${CLAUDE_SKILL_DIR}/scripts/foo.py``.

The in-container layout mirrors the on-disk repository and the paths
produced by ``core.skill_resolver.skill_mount_dir``:

    /                        → directory listing 'global' and 'users'
    /global/<skill>/...      → global skill directories
    /users/<uid>/<skill>/... → this relay user's skill tree
                               (which nests conversation-scoped skills)

The relay's ``user_id`` is captured at construction. Only ``global`` and
the relay user's own ``users/<uid>`` subtree are reachable; any other
``users/<other>`` path returns ENOENT so one user's relay cannot read
another user's skills.

Write/create/unlink/rename/etc. all return EROFS — skills are managed
via the resource APIs, not the FUSE mount.

Protocol (over the existing /ws/relay/<id> WebSocket, methods prefixed
with ``skfs.`` to disambiguate from the cc-sessions ``sfs.*`` and
filestore ``ffs.*`` protocols):

    relay  → server: {"type": "relay_request", "request_id": "<id>",
                      "method": "skfs.<op>", "args": {...}}
    server → relay: {"type": "relay_response", "request_id": "<id>",
                      "data": {...}}   # or {"error": ..., "errno": ...}

Methods: skfs.getattr, skfs.readdir, skfs.open, skfs.read, skfs.release,
skfs.statfs. Anything else (write side) returns EROFS or ENOSYS.
"""

import base64
import errno
import logging
import os
import stat as _stat
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)


_ERRNO_NAME = {v: k for k, v in errno.__dict__.items()
               if isinstance(v, int) and k.startswith("E")}


def _errno_response(eno: int, msg: str = "") -> Dict[str, Any]:
    name = _ERRNO_NAME.get(eno, f"E{eno}")
    if msg:
        return {"error": name, "errno": eno, "message": msg}
    return {"error": name, "errno": eno}


class RelaySkillsFs:
    """Per-relay skills-repository-as-FS handler.

    A single instance is attached to one RelayService. The relay's
    ``user_id`` is captured at construction; every op resolves the
    virtual path against the repository root and rejects anything
    outside ``global`` or this user's own ``users/<uid>`` subtree.
    """

    ALLOWED_METHODS = frozenset({
        # Read-only ops — the only ones we serve.
        "skfs.getattr", "skfs.readdir",
        "skfs.open", "skfs.read", "skfs.release",
        "skfs.statfs",
        # Write-side ops are accepted by name so we can return EROFS
        # rather than ENOSYS (libc maps EROFS to a clearer user error).
        "skfs.create", "skfs.write", "skfs.truncate",
        "skfs.unlink", "skfs.mkdir", "skfs.rmdir",
        "skfs.rename", "skfs.chmod", "skfs.utimens",
    })

    _RO_METHODS = frozenset({
        "skfs.getattr", "skfs.readdir", "skfs.open",
        "skfs.read", "skfs.release", "skfs.statfs",
    })

    MAX_READ_CHUNK = 1 * 1024 * 1024  # 1 MB

    def __init__(self, user_id: str):
        if not user_id:
            raise ValueError("RelaySkillsFs requires a non-empty user_id")
        self._user_id = user_id
        self._fd_lock = threading.Lock()
        self._fds: Dict[int, int] = {}  # fh → real fd
        self._next_fh = 1

    # ------------------------------------------------------------------
    # Path resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _skills_base() -> Path:
        from core.paths import REPOSITORY_DIR
        return (Path(REPOSITORY_DIR) / "skills").resolve()

    @staticmethod
    def _split_path(path: str) -> List[str]:
        """Return the cleaned, non-empty path components."""
        if path is None:
            raise FileNotFoundError("path is required")
        parts = [p for p in str(path).replace("\\", "/").split("/") if p]
        if any(p in (".", "..") for p in parts):
            raise FileNotFoundError(f"unsafe path: {path!r}")
        return parts

    def _real_path(self, path: str) -> Tuple[Path, List[str]]:
        """Map a virtual path to a real repository path, enforcing scope.

        Raises FileNotFoundError for paths outside the allowed scopes.
        Returns (real_path, parts). The root maps to the repository base.
        """
        parts = self._split_path(path)
        base = self._skills_base()
        if not parts:
            return base, parts
        scope = parts[0]
        if scope == "global":
            pass
        elif scope == "users":
            # /users itself is browsable; below it only this user's tree.
            if len(parts) >= 2 and parts[1] != self._user_id:
                raise FileNotFoundError(
                    f"users/{parts[1]} is not visible to this relay")
        else:
            raise FileNotFoundError(f"unknown skills scope: {scope!r}")
        candidate = base.joinpath(*parts)
        # Symlink-escape guard: the resolved path must stay under base.
        resolved = Path(os.path.realpath(candidate))
        if resolved != base and base not in resolved.parents:
            raise FileNotFoundError(f"path escapes skills repository: {path!r}")
        return candidate, parts

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
                                   f"skills FUSE is read-only ({method})")
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
            logger.exception("[skills-fs] %s failed for user=%s",
                             method, self._user_id)
            return _errno_response(errno.EIO, str(e))

    # ------------------------------------------------------------------
    # Attributes
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

    def _file_attrs(self, real: Path) -> Dict[str, Any]:
        st = real.stat()
        return {
            "st_mode": _stat.S_IFREG | 0o444,
            "st_size": int(st.st_size),
            "st_mtime": st.st_mtime,
            "st_atime": st.st_atime,
            "st_ctime": st.st_ctime,
            "st_uid": os.getuid() if hasattr(os, "getuid") else 0,
            "st_gid": os.getgid() if hasattr(os, "getgid") else 0,
            "st_nlink": 1,
        }

    # ------------------------------------------------------------------
    # Operations
    # ------------------------------------------------------------------

    def _op_getattr(self, args: Dict[str, Any]) -> Dict[str, Any]:
        real, parts = self._real_path(args.get("path", ""))
        if not parts:
            return {"data": self._dir_attrs()}
        # `users` is a synthetic directory even before the user has skills.
        if parts == ["users"]:
            return {"data": self._dir_attrs()}
        if not real.exists():
            raise FileNotFoundError(f"no such skill path: {args.get('path')!r}")
        if real.is_dir():
            return {"data": self._dir_attrs(mtime=real.stat().st_mtime)}
        return {"data": self._file_attrs(real)}

    def _op_readdir(self, args: Dict[str, Any]) -> Dict[str, Any]:
        real, parts = self._real_path(args.get("path", ""))
        if not parts:
            # Root: only the two scope directories that exist on disk.
            base = self._skills_base()
            entries = [d for d in ("global", "users")
                       if (base / d).is_dir()]
            return {"data": {"entries": entries}}
        if parts == ["users"]:
            # Only this relay user's own subtree is listed.
            udir = self._skills_base() / "users" / self._user_id
            return {"data": {"entries":
                              [self._user_id] if udir.is_dir() else []}}
        if not real.is_dir():
            raise NotADirectoryError(f"not a directory: {args.get('path')!r}")
        return {"data": {"entries": sorted(os.listdir(real))}}

    def _op_open(self, args: Dict[str, Any]) -> Dict[str, Any]:
        flags = int(args.get("flags", os.O_RDONLY))
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC):
            return _errno_response(errno.EROFS, "skills FUSE is read-only")
        real, parts = self._real_path(args.get("path", ""))
        if not parts or not real.is_file():
            raise FileNotFoundError(f"open: not a file: {args.get('path')!r}")
        fd = os.open(real, os.O_RDONLY)
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
        return {"data": {
            "f_bsize": 4096,
            "f_frsize": 4096,
            "f_blocks": 0,
            "f_bfree": 0,
            "f_bavail": 0,
            "f_files": 0,
            "f_ffree": 0,
            "f_favail": 0,
            "f_namemax": 255,
        }}
