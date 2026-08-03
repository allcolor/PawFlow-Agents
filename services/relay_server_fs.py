"""RelayServerFs — server-side handler for FS ops requested by a relay.

Normal direction: server → relay (server asks relay to read/write a host file).
This module adds the INVERSE: relay → server. The relay (typically its FUSE
proxy) asks the server to read/write a sandboxed file under the relay
owner's Claude, Codex, and Gemini CLI session slots. The relay's docker
container can then bind-mount the FUSE point and see every provider's
session files at the canonical path each CLI uses.

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
     All ops are scoped to that user's slots below the Claude, Codex, and
     Gemini CLI session roots.
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
import time as _time
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from core import paths as _paths
from services import cc_memory_mirror

logger = logging.getLogger(__name__)

# Last time we logged a layout-drift warning per user, to avoid spamming
# logs when every turn writes a non-memory .md (e.g. a generated README).
# Process-local; rate-limit window is 1 hour.
_LAYOUT_DRIFT_SEEN: Dict[str, float] = {}


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

    def __init__(self, user_id: str, root_dir: Optional[Path] = None,
                 root_dirs: Optional[Mapping[str, Path]] = None):
        if not user_id:
            raise ValueError("RelayServerFs requires a non-empty user_id")
        self._user_id = user_id
        if root_dir is not None and root_dirs is not None:
            raise ValueError("root_dir and root_dirs are mutually exclusive")
        if root_dirs is None:
            if root_dir is not None:
                roots = {"claude": Path(root_dir)}
            else:
                roots = {
                    "claude": _paths.CLAUDE_SESSIONS_DIR,
                    "codex": _paths.CODEX_SESSIONS_DIR,
                    "gemini": _paths.GEMINI_SESSIONS_DIR,
                }
        else:
            roots = {str(name): Path(path) for name, path in root_dirs.items()}
        if not roots:
            raise ValueError("RelayServerFs requires at least one session root")

        # The canonical /cc_sessions mount is a union of the provider-specific
        # runtime trees. Keep the provider name with each resolved user slot so
        # writes and Claude-only memory hooks remain routed to their owner.
        self._roots: Dict[str, Tuple[Path, Path]] = {}
        for provider, base in roots.items():
            slot = Path(base) / user_id
            slot.mkdir(parents=True, exist_ok=True)
            self._roots[provider] = (slot, slot.resolve())
        # Retained as the default write target for a completely new path and
        # for compatibility with the single-root test/injection surface.
        self._root, self._root_resolved = next(iter(self._roots.values()))
        self._fd_lock = threading.Lock()
        self._fds: Dict[int, int] = {}  # fh → real fd
        # fh → (rel_path, dirty, provider). We track the relay-supplied path so
        # post-release mirrors can re-read the finished Claude file without
        # the relay having to re-send it.
        self._open_meta: Dict[int, Tuple[str, bool, str]] = {}
        self._next_fh = 1

    # ------------------------------------------------------------------
    # Path resolution
    # ------------------------------------------------------------------

    def _candidate_paths(self, rel_path: str):
        """Return sandboxed provider candidates for a canonical path."""
        if rel_path is None:
            raise _PathEscape("path is required")
        rel = str(rel_path).lstrip("/\\")
        candidates = []
        for provider, (root, root_resolved) in self._roots.items():
            candidate = (root / rel).resolve()
            try:
                candidate.relative_to(root_resolved)
            except ValueError:
                raise _PathEscape(f"escape: {rel_path!r} → {candidate}")
            candidates.append((provider, candidate, root_resolved))
        return candidates

    @staticmethod
    def _provider_hint(rel_path: str) -> str:
        parts = set(str(rel_path).replace("\\", "/").split("/"))
        if ".claude" in parts:
            return "claude"
        if ".codex" in parts:
            return "codex"
        if ".gemini" in parts or ".agents" in parts:
            return "gemini"
        return ""

    @staticmethod
    def _mtime_ns(path: Path) -> int:
        try:
            return path.stat().st_mtime_ns
        except OSError:
            return -1

    def _resolve_entry(self, rel_path: str, *, for_write: bool = False):
        """Resolve a canonical path and retain its provider ownership.

        Existing paths win. If several providers contain the same canonical
        entry, a provider-specific dot-directory wins first, otherwise the
        most recently modified entry does. A new write follows the provider
        owning its deepest existing ancestor.
        """
        candidates = self._candidate_paths(rel_path)
        hint = self._provider_hint(rel_path)
        existing = [entry for entry in candidates if entry[1].exists()]
        if existing:
            indexed = {provider: idx for idx, provider in enumerate(self._roots)}
            return max(
                existing,
                key=lambda entry: (
                    int(entry[0] == hint),
                    self._mtime_ns(entry[1]),
                    -indexed[entry[0]],
                ),
            )
        if not for_write:
            # Return a contained candidate and let the filesystem operation
            # raise its normal ENOENT.
            return candidates[0]

        indexed = {provider: idx for idx, provider in enumerate(self._roots)}

        def _write_score(entry):
            provider, target, root_resolved = entry
            ancestor = target.parent
            depth = 0
            while ancestor != root_resolved and not ancestor.exists():
                ancestor = ancestor.parent
                depth += 1
            existing_depth = len(target.parts) - len(root_resolved.parts) - depth
            return (
                int(provider == hint),
                existing_depth,
                self._mtime_ns(ancestor),
                -indexed[provider],
            )

        return max(candidates, key=_write_score)

    def _resolve(self, rel_path: str) -> Path:
        """Resolve an existing canonical path inside one provider slot."""
        return self._resolve_entry(rel_path)[1]

    def _resolve_for_provider(self, provider: str, rel_path: str) -> Path:
        for name, candidate, _root in self._candidate_paths(rel_path):
            if name == provider:
                return candidate
        raise _PathEscape(f"unknown session provider: {provider!r}")

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
        _provider, target, root_resolved = self._resolve_entry(args.get("path", ""))
        st = os.lstat(target)
        # Refuse symlinks pointing outside the slot. lstat doesn't follow,
        # so handle the symlink case explicitly.
        if _stat.S_ISLNK(st.st_mode):
            link_target = os.readlink(target)
            link_abs = (target.parent / link_target).resolve()
            try:
                link_abs.relative_to(root_resolved)
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
        candidates = self._candidate_paths(args.get("path", ""))
        existing = [target for _provider, target, _root in candidates
                    if target.exists()]
        directories = [target for target in existing if target.is_dir()]
        if not directories:
            if existing:
                raise NotADirectoryError(str(existing[0]))
            raise FileNotFoundError(str(candidates[0][1]))
        entries = sorted({entry for target in directories
                          for entry in os.listdir(target)})
        return {"data": {"entries": entries}}

    def _op_open(self, args: Dict[str, Any]) -> Dict[str, Any]:
        flags = int(args.get("flags", os.O_RDONLY))
        provider, target, _root = self._resolve_entry(args.get("path", ""))
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
            self._open_meta[fh] = (args.get("path", ""), False, provider)
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
            rel_path, dirty, provider = meta
            if dirty:
                self._maybe_mirror_write(rel_path, provider)
        return {"data": {}}

    def _op_statfs(self, args: Dict[str, Any]) -> Dict[str, Any]:
        _provider, target, _root = self._resolve_entry(args.get("path", ""))
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
        provider, target, _root = self._resolve_entry(
            args.get("path", ""), for_write=True)
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
            self._open_meta[fh] = (args.get("path", ""), False, provider)
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
                self._open_meta[fh] = (meta[0], True, meta[2])
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
                    self._open_meta[fh] = (meta[0], True, meta[2])
            os.ftruncate(fd, length)
        else:
            rel = args.get("path", "")
            provider, target, _root = self._resolve_entry(rel)
            os.truncate(target, length)
            self._maybe_mirror_write(rel, provider)
        return {"data": {}}

    def _op_unlink(self, args: Dict[str, Any]) -> Dict[str, Any]:
        rel = args.get("path", "")
        provider, target, _root = self._resolve_entry(rel)
        os.unlink(target)
        if provider == "claude":
            try:
                cc_memory_mirror.mirror_unlink(self._user_id, rel)
            except Exception:
                logger.exception("[server-fs] mirror_unlink hook failed")
        return {"data": {}}

    def _op_mkdir(self, args: Dict[str, Any]) -> Dict[str, Any]:
        _provider, target, _root = self._resolve_entry(
            args.get("path", ""), for_write=True)
        mode = int(args.get("mode", 0o700)) & 0o777
        os.mkdir(target, mode)
        return {"data": {}}

    def _op_rmdir(self, args: Dict[str, Any]) -> Dict[str, Any]:
        _provider, target, _root = self._resolve_entry(args.get("path", ""))
        os.rmdir(target)
        return {"data": {}}

    def _op_rename(self, args: Dict[str, Any]) -> Dict[str, Any]:
        # BOTH paths must resolve inside the slot — a rename can't be
        # used to escape the sandbox in either direction.
        old_rel = args.get("old", "")
        new_rel = args.get("new", "")
        provider, old, _root = self._resolve_entry(old_rel)
        new = self._resolve_for_provider(provider, new_rel)
        os.rename(old, new)
        new_data: Optional[bytes] = None
        if provider == "claude" and cc_memory_mirror.match_memory_path(new_rel):
            try:
                new_data = new.read_bytes()
            except OSError:
                new_data = None
        if provider == "claude":
            try:
                cc_memory_mirror.mirror_rename(self._user_id, old_rel, new_rel,
                                                new_data)
            except Exception:
                logger.exception("[server-fs] mirror_rename hook failed")
        return {"data": {}}

    def _op_chmod(self, args: Dict[str, Any]) -> Dict[str, Any]:
        _provider, target, _root = self._resolve_entry(args.get("path", ""))
        # Mask out setuid/setgid/sticky — these have no business in a
        # session slot and could be used to harden a foothold.
        mode = int(args.get("mode", 0o600)) & 0o777
        os.chmod(target, mode)
        return {"data": {}}

    def _op_utimens(self, args: Dict[str, Any]) -> Dict[str, Any]:
        _provider, target, _root = self._resolve_entry(args.get("path", ""))
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

    def _maybe_mirror_write(self, rel_path: str, provider: str = "claude") -> None:
        """If `rel_path` is a mirrorable CC memory file, re-read it from
        disk and forward the bytes to the mirror. Best-effort — errors
        are logged and swallowed so a failed mirror never breaks the FS
        op that triggered it.

        If the path looks like a CC memory file (.md under the slot)
        but doesn't fit `match_memory_path`, log a rate-limited warning
        so an operator notices a layout drift (CC may have changed its
        memory-skill file convention between versions, breaking the
        mirror without any other surface signal).
        """
        if provider != "claude":
            return
        if not cc_memory_mirror.match_memory_path(rel_path):
            self._maybe_log_layout_drift(rel_path)
            return
        try:
            data = self._resolve_for_provider(provider, rel_path).read_bytes()
        except OSError:
            logger.debug("[server-fs] mirror read failed for %s", rel_path,
                         exc_info=True)
            return
        try:
            cc_memory_mirror.mirror_write(self._user_id, rel_path, data)
        except Exception:
            logger.exception("[server-fs] mirror_write hook failed")

    def _maybe_log_layout_drift(self, rel_path: str) -> None:
        """Warn once per hour per user when an .md write doesn't match
        the mirrorable layout. Avoids log spam if every CC turn writes
        a non-memory .md (e.g. a generated README) while still surfacing
        a real layout drift within an hour.
        """
        if not rel_path.lower().endswith(".md"):
            return
        # Skip the index file (we already filter it in match_memory_path)
        # and dotfiles — only signal on plausible memory-shaped paths.
        name = rel_path.rsplit("/", 1)[-1]
        if name == cc_memory_mirror._INDEX_FILE or name.startswith("."):
            return
        now = _time.monotonic()
        last = _LAYOUT_DRIFT_SEEN.get(self._user_id, 0.0)
        if now - last < 3600:
            return
        _LAYOUT_DRIFT_SEEN[self._user_id] = now
        logger.warning(
            "[server-fs] CC wrote %r under user=%s slot but path doesn't "
            "match the cc-memory mirror layout (<conv>/<agent>/.../memory/<slug>.md). "
            "If this is a memory file, the CC skill version may have "
            "changed and cc_memory_mirror.match_memory_path needs updating.",
            rel_path, self._user_id)
