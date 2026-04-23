"""RelayServerFs — server-side handler for FS ops requested by a relay.

Normal direction: server → relay (server asks relay to read/write a host file).
This module adds the INVERSE: relay → server. The relay (typically its FUSE
proxy) asks the server to read/write a sandboxed file under the relay
owner's CLAUDE_SESSIONS_DIR slot. The relay's docker container can then
bind-mount the FUSE point and see the user's session files (CC spills,
etc.) at a canonical path identical to what CC itself uses.

Protocol (over the existing /ws/relay/<id> WebSocket):

    relay  → server: {"type": "relay_request",
                     "request_id": "<id>",
                     "method": "sfs.<op>",
                     "args": {...}}

    server → relay: {"type": "relay_response",
                     "request_id": "<id>",
                     "data": {...}}     # success
                  or {"type": "relay_response",
                     "request_id": "<id>",
                     "error": "<code>", # POSIX errno name ("ENOENT", ...)
                     "errno": <int>}

Phase 1a: read-only ops (`getattr`, `readdir`, `open`, `read`, `release`).
Write ops land in phase 1b.

Security invariants:
  1. Each relay is bound to a single owner user_id at registration time.
     All ops are scoped to `CLAUDE_SESSIONS_DIR / <user_id> /`.
  2. Path resolution uses Path.resolve() and re-checks containment after
     symlink expansion — a symlink pointing outside the slot is refused.
  3. Open file descriptors live in a per-relay-instance table; relay
     disconnect releases them.
  4. No write ops in this phase — even if the relay forges a method name,
     unknown methods return ENOSYS.
"""

import base64
import errno
import logging
import os
import stat as _stat
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from core.paths import CLAUDE_SESSIONS_DIR
from services import cc_memory_mirror

logger = logging.getLogger(__name__)


# Errno name lookup — we send strings on the wire so the relay can map
# them to its local errno without depending on a shared enum.
_ERRNO_NAME = {v: k for k, v in errno.__dict__.items()
               if isinstance(v, int) and k.startswith("E")}


def _errno_response(eno: int, msg: str = "") -> Dict[str, Any]:
    name = _ERRNO_NAME.get(eno, f"E{eno}")
    if msg:
        return {"error": name, "errno": eno, "message": msg}
    return {"error": name, "errno": eno}


class _PathEscape(Exception):
    """Raised when a relay tries to address a path outside its sandbox."""


class RelayServerFs:
    """Per-relay-instance FS handler. Maintains an open-fd table.

    A single instance is attached to one RelayService and serves every
    `relay_request` message arriving on its WebSocket. The relay's
    `user_id` is captured at construction — every op resolves paths
    under the user's slot and never reads it from the wire (so a
    forged user_id in the message can't escalate scope).
    """

    # Methods the relay is allowed to call. Anything else returns ENOSYS,
    # even if it exists as a Python method on this class.
    ALLOWED_METHODS = frozenset({
        # Read-only
        "sfs.getattr", "sfs.readdir",
        "sfs.open", "sfs.read", "sfs.release",
        "sfs.statfs",
        # Read-write
        "sfs.create", "sfs.write", "sfs.truncate",
        "sfs.unlink", "sfs.mkdir", "sfs.rmdir",
        "sfs.rename", "sfs.chmod", "sfs.utimens",
    })

    # Cap on a single read or write payload to prevent a malicious or
    # buggy relay from asking for an absurd chunk and OOM'ing the server.
    MAX_READ_CHUNK = 1 * 1024 * 1024  # 1 MB
    MAX_WRITE_CHUNK = 1 * 1024 * 1024  # 1 MB

    def __init__(self, user_id: str, root_dir: Optional[Path] = None):
        if not user_id:
            raise ValueError("RelayServerFs requires a non-empty user_id")
        self._user_id = user_id
        # Lazy mkdir so the slot exists even if the user has no CC session yet
        self._root = (Path(root_dir) if root_dir else CLAUDE_SESSIONS_DIR) / user_id
        self._root.mkdir(parents=True, exist_ok=True)
        self._root_resolved = self._root.resolve()
        self._fd_lock = threading.Lock()
        self._fds: Dict[int, int] = {}  # fh → real fd
        # fh → (rel_path, dirty). We track the relay-supplied path so that
        # post-release mirrors (cc_memory_mirror) can re-read the finished
        # file without the relay having to re-send it.
        self._open_meta: Dict[int, Tuple[str, bool]] = {}
        self._next_fh = 1

    # ------------------------------------------------------------------
    # Path resolution
    # ------------------------------------------------------------------

    def _resolve(self, rel_path: str) -> Path:
        """Resolve a relay-supplied path to an absolute path inside the slot.

        Accepts paths with or without a leading slash. Refuses absolute
        paths that don't fall under the slot, refuses traversal via `..`,
        and refuses symlinks pointing outside the slot.
        """
        if rel_path is None:
            raise _PathEscape("path is required")
        # Strip leading slash so it joins as relative
        rel = rel_path.lstrip("/\\")
        candidate = (self._root / rel).resolve()
        try:
            candidate.relative_to(self._root_resolved)
        except ValueError:
            raise _PathEscape(f"escape: {rel_path!r} → {candidate}")
        return candidate

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Release every open fd. Call on relay disconnect."""
        with self._fd_lock:
            fds = list(self._fds.values())
            self._fds.clear()
            self._open_meta.clear()
        for fd in fds:
            try:
                os.close(fd)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def handle(self, method: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """Run one relay-side request. Returns the `data`/`error` payload.

        The caller wraps this in a `relay_response` envelope. Any unknown
        method returns ENOSYS — callers must NOT discover server-side
        methods reflectively.
        """
        if method not in self.ALLOWED_METHODS:
            return _errno_response(errno.ENOSYS, f"method {method!r} not allowed")
        try:
            handler = getattr(self, "_op_" + method.split(".", 1)[1])
        except AttributeError:
            return _errno_response(errno.ENOSYS, f"unimplemented {method!r}")
        try:
            return handler(args or {})
        except _PathEscape as e:
            logger.warning("[server-fs] path escape by user=%s: %s", self._user_id, e)
            return _errno_response(errno.EACCES, str(e))
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
            logger.exception("[server-fs] %s failed for user=%s", method, self._user_id)
            return _errno_response(errno.EIO, str(e))

    # ------------------------------------------------------------------
    # Operations — read-only set
    # ------------------------------------------------------------------

    def _op_getattr(self, args: Dict[str, Any]) -> Dict[str, Any]:
        target = self._resolve(args.get("path", ""))
        st = os.lstat(target)
        # Refuse symlinks pointing outside the slot. lstat doesn't follow,
        # so handle the symlink case explicitly.
        if _stat.S_ISLNK(st.st_mode):
            link_target = os.readlink(target)
            link_abs = (target.parent / link_target).resolve()
            try:
                link_abs.relative_to(self._root_resolved)
            except ValueError:
                raise _PathEscape(f"symlink escapes: {target} → {link_abs}")
            st = os.stat(target)
        return {"data": {
            "st_mode": st.st_mode,
            "st_size": st.st_size,
            "st_mtime": st.st_mtime,
            "st_atime": st.st_atime,
            "st_ctime": st.st_ctime,
            "st_uid": st.st_uid,
            "st_gid": st.st_gid,
            "st_nlink": st.st_nlink,
        }}

    def _op_readdir(self, args: Dict[str, Any]) -> Dict[str, Any]:
        target = self._resolve(args.get("path", ""))
        if not target.is_dir():
            raise NotADirectoryError(str(target))
        entries = sorted(os.listdir(target))
        return {"data": {"entries": entries}}

    def _op_open(self, args: Dict[str, Any]) -> Dict[str, Any]:
        flags = int(args.get("flags", os.O_RDONLY))
        target = self._resolve(args.get("path", ""))
        if target.is_dir():
            raise IsADirectoryError(str(target))
        # Refuse O_CREAT — callers must use sfs.create explicitly so the
        # creation mode is required; this also prevents accidental file
        # creation from a careless O_WRONLY|O_CREAT.
        if flags & os.O_CREAT:
            return _errno_response(errno.EINVAL,
                                    "use sfs.create for file creation")
        fd = os.open(target, flags)
        with self._fd_lock:
            fh = self._next_fh
            self._next_fh += 1
            self._fds[fh] = fd
            self._open_meta[fh] = (args.get("path", ""), False)
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
            meta = self._open_meta.pop(fh, None)
        if fd is None:
            return _errno_response(errno.EBADF, f"unknown fh {fh}")
        try:
            os.close(fd)
        except OSError as e:
            return _errno_response(e.errno or errno.EIO, str(e))
        if meta is not None:
            rel_path, dirty = meta
            if dirty:
                self._maybe_mirror_write(rel_path)
        return {"data": {}}

    def _op_statfs(self, args: Dict[str, Any]) -> Dict[str, Any]:
        target = self._resolve(args.get("path", ""))
        st = os.statvfs(target)
        return {"data": {
            "f_bsize": st.f_bsize,
            "f_frsize": st.f_frsize,
            "f_blocks": st.f_blocks,
            "f_bfree": st.f_bfree,
            "f_bavail": st.f_bavail,
            "f_files": st.f_files,
            "f_ffree": st.f_ffree,
            "f_favail": st.f_favail,
            "f_namemax": st.f_namemax,
        }}

    # ------------------------------------------------------------------
    # Operations — read-write set
    # ------------------------------------------------------------------

    def _op_create(self, args: Dict[str, Any]) -> Dict[str, Any]:
        target = self._resolve(args.get("path", ""))
        # Standard create: O_WRONLY|O_CREAT|O_TRUNC, mode 0o600 by default
        flags = int(args.get("flags", os.O_WRONLY | os.O_CREAT | os.O_TRUNC))
        mode = int(args.get("mode", 0o600)) & 0o777
        if not (flags & os.O_CREAT):
            flags |= os.O_CREAT
        fd = os.open(target, flags, mode)
        with self._fd_lock:
            fh = self._next_fh
            self._next_fh += 1
            self._fds[fh] = fd
            self._open_meta[fh] = (args.get("path", ""), False)
        return {"data": {"fh": fh}}

    def _op_write(self, args: Dict[str, Any]) -> Dict[str, Any]:
        fh = int(args.get("fh", -1))
        offset = int(args.get("offset", 0))
        data_b64 = args.get("data_b64", "")
        try:
            data = base64.b64decode(data_b64)
        except (ValueError, TypeError) as e:
            return _errno_response(errno.EINVAL, f"bad base64: {e}")
        if len(data) > self.MAX_WRITE_CHUNK:
            return _errno_response(errno.EINVAL,
                                    f"write chunk {len(data)} exceeds {self.MAX_WRITE_CHUNK}")
        with self._fd_lock:
            fd = self._fds.get(fh)
            if fd is None:
                return _errno_response(errno.EBADF, f"unknown fh {fh}")
            # Mark the fh dirty while we still hold the fd lock so that a
            # racing release can't pop the meta entry before we record it.
            meta = self._open_meta.get(fh)
            if meta is not None:
                self._open_meta[fh] = (meta[0], True)
        os.lseek(fd, offset, os.SEEK_SET)
        n = os.write(fd, data)
        return {"data": {"bytes_written": n}}

    def _op_truncate(self, args: Dict[str, Any]) -> Dict[str, Any]:
        length = int(args.get("length", 0))
        # Either path-based (POSIX truncate) or fh-based (ftruncate).
        if args.get("fh") is not None:
            fh = int(args["fh"])
            with self._fd_lock:
                fd = self._fds.get(fh)
                if fd is None:
                    return _errno_response(errno.EBADF, f"unknown fh {fh}")
                meta = self._open_meta.get(fh)
                if meta is not None:
                    self._open_meta[fh] = (meta[0], True)
            os.ftruncate(fd, length)
        else:
            rel = args.get("path", "")
            target = self._resolve(rel)
            os.truncate(target, length)
            self._maybe_mirror_write(rel)
        return {"data": {}}

    def _op_unlink(self, args: Dict[str, Any]) -> Dict[str, Any]:
        rel = args.get("path", "")
        target = self._resolve(rel)
        os.unlink(target)
        try:
            cc_memory_mirror.mirror_unlink(self._user_id, rel)
        except Exception:
            logger.exception("[server-fs] mirror_unlink hook failed")
        return {"data": {}}

    def _op_mkdir(self, args: Dict[str, Any]) -> Dict[str, Any]:
        target = self._resolve(args.get("path", ""))
        mode = int(args.get("mode", 0o700)) & 0o777
        os.mkdir(target, mode)
        return {"data": {}}

    def _op_rmdir(self, args: Dict[str, Any]) -> Dict[str, Any]:
        target = self._resolve(args.get("path", ""))
        os.rmdir(target)
        return {"data": {}}

    def _op_rename(self, args: Dict[str, Any]) -> Dict[str, Any]:
        # BOTH paths must resolve inside the slot — a rename can't be
        # used to escape the sandbox in either direction.
        old_rel = args.get("old", "")
        new_rel = args.get("new", "")
        old = self._resolve(old_rel)
        new = self._resolve(new_rel)
        os.rename(old, new)
        new_data: Optional[bytes] = None
        if cc_memory_mirror.match_memory_path(new_rel):
            try:
                new_data = new.read_bytes()
            except OSError:
                new_data = None
        try:
            cc_memory_mirror.mirror_rename(self._user_id, old_rel, new_rel,
                                            new_data)
        except Exception:
            logger.exception("[server-fs] mirror_rename hook failed")
        return {"data": {}}

    def _op_chmod(self, args: Dict[str, Any]) -> Dict[str, Any]:
        target = self._resolve(args.get("path", ""))
        # Mask out setuid/setgid/sticky — these have no business in a
        # session slot and could be used to harden a foothold.
        mode = int(args.get("mode", 0o600)) & 0o777
        os.chmod(target, mode)
        return {"data": {}}

    def _op_utimens(self, args: Dict[str, Any]) -> Dict[str, Any]:
        target = self._resolve(args.get("path", ""))
        atime = args.get("atime")
        mtime = args.get("mtime")
        if atime is None or mtime is None:
            os.utime(target, None)
        else:
            os.utime(target, (float(atime), float(mtime)))
        return {"data": {}}

    # ------------------------------------------------------------------
    # Mirror hooks
    # ------------------------------------------------------------------

    def _maybe_mirror_write(self, rel_path: str) -> None:
        """If `rel_path` is a mirrorable CC memory file, re-read it from
        disk and forward the bytes to the mirror. Best-effort — errors
        are logged and swallowed so a failed mirror never breaks the FS
        op that triggered it.
        """
        if not cc_memory_mirror.match_memory_path(rel_path):
            return
        try:
            data = self._resolve(rel_path).read_bytes()
        except OSError:
            logger.debug("[server-fs] mirror read failed for %s", rel_path,
                         exc_info=True)
            return
        try:
            cc_memory_mirror.mirror_write(self._user_id, rel_path, data)
        except Exception:
            logger.exception("[server-fs] mirror_write hook failed")
