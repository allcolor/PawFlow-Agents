"""FUSE3 filesystem that proxies every syscall to the PawFlow server.

Holds the pyfuse3 Operations class of the combined server-fs mount. It runs
only inside the out-of-process responder (`pawflow_relay.fuse_responder`);
the relay-side supervisor lives in `pawflow_relay.combined_fs`.

Depends on `pyfuse3` + `trio` + `libfuse3`. The imports are lazy so this
module can be loaded for inspection without the libs installed; only
building the Operations class requires them.
"""

import base64
import errno
import logging
import os
import stat as _stat
import sys
import threading
import time as _time

from pawflow_relay.server_fs_client import ServerFsClient

logger = logging.getLogger(__name__)

# Per-op diagnostic logging. Off by default — enable with
# `PAWFLOW_FUSE_TRACE=1` in the relay container's env to debug a
# stuck mount or a misrouted op. When on, we write to stderr (picked
# up by relay.log) and `/tmp/server_fs_mount.trace` (visible from any
# shell inside the relay container, survives docker stdout pipeline
# pressure). The earlier `/workspace/.fuse-trace.log` mirror was
# only useful while diagnosing the pyfuse3 single-mount bug — with
# that fixed, the mount lifecycle is stable and the host-visible
# mirror would just keep accumulating noise on the project tree.
_FUSE_TRACE = os.environ.get("PAWFLOW_FUSE_TRACE", "0") != "0"
_FUSE_TRACE_FILES = ("/tmp/server_fs_mount.trace",)  # nosec B108 - optional relay-local trace file.
_FUSE_TRACE_FHS: list = []
_FUSE_TRACE_LOCK = threading.Lock()


def _fuse_trace_emit(line: str) -> None:
    """Write a trace line to stderr + every available trace file.

    Files are line-buffered. The /workspace one is the authoritative
    post-mortem source because it sits on the host bind-mount and
    persists across container kills — so even after `docker rm -f`
    you can read what the FUSE daemon was doing right before it died.
    Never raises.
    """
    if not _FUSE_TRACE:
        return
    if not line.endswith("\n"):
        line = line + "\n"
    with _FUSE_TRACE_LOCK:
        try:
            sys.stderr.write(line)
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        if not _FUSE_TRACE_FHS:
            for path in _FUSE_TRACE_FILES:
                try:
                    fh = open(path, "a", encoding="utf-8", buffering=1)
                    _FUSE_TRACE_FHS.append(fh)
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        for fh in _FUSE_TRACE_FHS:
            try:
                fh.write(line)
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

# Module-load banner — confirms this exact build of the file is what
# the relay actually imported. Visible immediately in the trace files.
_fuse_trace_emit(
    f"[fuse-mount] module loaded pid={os.getpid()} "
    f"trace_files={_FUSE_TRACE_FILES} "
    f"src={os.path.abspath(__file__)}\n")

# Match the server's MAX_READ_CHUNK — if FUSE asks for more, we chunk.
_MAX_CHUNK = 1 * 1024 * 1024


def _to_fuse_error(reply: dict) -> int:
    """Map a server reply's `errno` field to a FUSE-compatible errno int."""
    return int(reply.get('errno') or errno.EIO)




# ─────────────────────────────────────────────────────────────────────
# Combined two-subtree mount
# ─────────────────────────────────────────────────────────────────────
#
# pyfuse3 keeps a single global session, so two mounts in the same process
# race for that global state — the second `init` wins and the first mount
# goes orphan in the kernel. The combined mount sidesteps this with ONE
# pyfuse3 mount whose root contains exactly three synthetic subdirectories:
# `cc_sessions` (sfs.*), `filestore` (ffs.*) and `skills` (skfs.*). The
# relay symlinks the canonical /cc_sessions, /filestore and /skills to
# those subtrees so all downstream consumers (CC config, file tools, etc.)
# keep their existing absolute paths.

def _build_combined_operations_class():
    """Build the routing pyfuse3 Operations subclass.

    Lazy-imports pyfuse3 + trio so this module can still be imported
    on systems where they aren't installed (e.g. Windows host that
    only invokes the relay code via tests).
    """
    import pyfuse3
    import trio

    SFS_INO = pyfuse3.ROOT_INODE + 1   # /cc_sessions
    FFS_INO = pyfuse3.ROOT_INODE + 2   # /filestore
    SKFS_INO = pyfuse3.ROOT_INODE + 3  # /skills
    SFS_NAME = 'cc_sessions'
    FFS_NAME = 'filestore'
    SKFS_NAME = 'skills'
    # Mount-root subtrees, in fh-tag index order (0=sfs, 1=ffs, 2=skfs).
    SUBTREES = ((SFS_NAME, SFS_INO, 'sfs'),
                (FFS_NAME, FFS_INO, 'ffs'),
                (SKFS_NAME, SKFS_INO, 'skfs'))
    TAG_BY_INDEX = ('sfs', 'ffs', 'skfs')

    class CombinedRouterOperations(pyfuse3.Operations):
        """Routes FUSE callbacks between three ServerFsClient backends.

        Inode bookkeeping is namespaced by the backend tag so
        cc_sessions, filestore and skills can have entries with the
        same relative path without colliding.
        """

        _ATTR_TIMEOUT = 1.0
        _ENTRY_TIMEOUT = 1.0
        # fh tag occupies the top 2 bits (index 0=sfs, 1=ffs, 2=skfs);
        # the backend fh sits in the low 30 bits. Backend handlers issue
        # small sequential fhs, so 30 bits is ample headroom.
        _FH_TAG_SHIFT = 30
        _FH_MASK = (1 << _FH_TAG_SHIFT) - 1

        def __init__(self, sfs_client: ServerFsClient,
                     ffs_client: ServerFsClient,
                     skfs_client: ServerFsClient,
                     request_timeout: float = 30.0):
            super().__init__()
            self._sfs = sfs_client
            self._ffs = ffs_client
            self._skfs = skfs_client
            self._timeout = min(request_timeout, 5.0)
            self._next_ino = SKFS_INO + 1
            # inode → (tag, path-in-that-subtree). 'root' is the mount
            # root itself (no real backend); 'sfs'/'ffs'/'skfs' route to
            # the respective client. The three anchor inodes are
            # pre-registered so lookup of the mount root's children is
            # in-memory (fast, no WS roundtrip).
            self._ino_meta: dict = {
                pyfuse3.ROOT_INODE: ('root', '/'),
                SFS_INO: ('sfs', '/'),
                FFS_INO: ('ffs', '/'),
                SKFS_INO: ('skfs', '/'),
            }
            self._meta_ino: dict = {v: k for k, v in self._ino_meta.items()}
            self._lock = threading.Lock()
            _fuse_trace_emit(
                f"[fuse-mount] CombinedOperations init "
                f"timeout={self._timeout}s sfs_ino={SFS_INO} "
                f"ffs_ino={FFS_INO} skfs_ino={SKFS_INO}")

        def _ino_for(self, tag: str, path: str) -> int:
            with self._lock:
                key = (tag, path)
                ino = self._meta_ino.get(key)
                if ino is None:
                    ino = self._next_ino
                    self._next_ino += 1
                    self._ino_meta[ino] = key
                    self._meta_ino[key] = ino
                return ino

        def _meta_for(self, inode: int):
            with self._lock:
                return self._ino_meta.get(inode)

        def _drop_path(self, tag: str, path: str) -> None:
            with self._lock:
                key = (tag, path)
                ino = self._meta_ino.pop(key, None)
                if ino is not None:
                    self._ino_meta.pop(ino, None)

        def _client_for(self, tag: str):
            if tag == 'sfs':
                return self._sfs, 'sfs.'
            if tag == 'ffs':
                return self._ffs, 'ffs.'
            if tag == 'skfs':
                return self._skfs, 'skfs.'
            return None, ''

        @classmethod
        def _tag_fh(cls, tag: str, backend_fh: int) -> int:
            """Pack the backend tag into the top bits of a FUSE fh."""
            return ((backend_fh & cls._FH_MASK)
                    | (TAG_BY_INDEX.index(tag) << cls._FH_TAG_SHIFT))

        async def _req(self, tag: str, op_short: str, args: dict) -> dict:
            cli, prefix = self._client_for(tag)
            if cli is None:
                return {'error': 'EIO', 'errno': errno.EIO,
                        'message': f'no client for tag {tag!r}'}
            timeout = self._timeout
            return await trio.to_thread.run_sync(
                lambda: cli.request(prefix + op_short, args, timeout))

        # ── Helpers ────────────────────────────────────────────────

        @staticmethod
        def _decode(name):
            return name.decode('utf-8') if isinstance(name, bytes) else name

        def _synthetic_dir_attrs(self, inode: int):
            now = _time.time()
            entry = pyfuse3.EntryAttributes()
            entry.st_mode = _stat.S_IFDIR | 0o755
            entry.st_size = 0
            entry.st_nlink = 2
            entry.st_uid = os.getuid() if hasattr(os, 'getuid') else 0
            entry.st_gid = os.getgid() if hasattr(os, 'getgid') else 0
            entry.st_atime_ns = int(now * 1e9)
            entry.st_mtime_ns = int(now * 1e9)
            entry.st_ctime_ns = int(now * 1e9)
            entry.st_ino = inode
            entry.generation = 0
            entry.entry_timeout = self._ENTRY_TIMEOUT
            entry.attr_timeout = self._ATTR_TIMEOUT
            return entry

        def _attrs_from_server(self, d: dict, ino: int):
            entry = pyfuse3.EntryAttributes()
            entry.st_mode = int(d['st_mode'])
            entry.st_size = int(d['st_size'])
            entry.st_nlink = int(d['st_nlink'])
            entry.st_uid = int(d['st_uid'])
            entry.st_gid = int(d['st_gid'])
            entry.st_atime_ns = int(float(d['st_atime']) * 1e9)
            entry.st_mtime_ns = int(float(d['st_mtime']) * 1e9)
            entry.st_ctime_ns = int(float(d['st_ctime']) * 1e9)
            entry.st_ino = ino
            entry.generation = 0
            entry.entry_timeout = self._ENTRY_TIMEOUT
            entry.attr_timeout = self._ATTR_TIMEOUT
            return entry

        # ── Metadata ───────────────────────────────────────────────

        async def getattr(self, inode, ctx=None):
            meta = self._meta_for(inode)
            if meta is None:
                raise pyfuse3.FUSEError(errno.ESTALE)
            tag, path = meta
            if tag == 'root' or path == '/':
                return self._synthetic_dir_attrs(inode)
            r = await self._req(tag, 'getattr', {'path': path})
            if 'error' in r:
                raise pyfuse3.FUSEError(_to_fuse_error(r))
            return self._attrs_from_server(r['data'], inode)

        async def lookup(self, parent_inode, name, ctx=None):
            name_s = self._decode(name)
            pmeta = self._meta_for(parent_inode)
            if pmeta is None:
                raise pyfuse3.FUSEError(errno.ENOENT)
            ptag, ppath = pmeta
            if ptag == 'root':
                # Only the synthetic subtree directories at the mount root.
                for _name, _ino, _tag in SUBTREES:
                    if name_s == _name:
                        return self._synthetic_dir_attrs(_ino)
                raise pyfuse3.FUSEError(errno.ENOENT)
            child_path = os.path.normpath(os.path.join(ppath, name_s))
            r = await self._req(ptag, 'getattr', {'path': child_path})
            if 'error' in r:
                raise pyfuse3.FUSEError(_to_fuse_error(r))
            ino = self._ino_for(ptag, child_path)
            return self._attrs_from_server(r['data'], ino)

        async def statfs(self, ctx=None):
            # Aggregate from both backends — sane defaults if either fails.
            sf = pyfuse3.StatvfsData()
            sf.f_bsize = 4096
            sf.f_frsize = 4096
            sf.f_blocks = 0
            sf.f_bfree = 0
            sf.f_bavail = 0
            sf.f_files = 0
            sf.f_ffree = 0
            sf.f_favail = 0
            sf.f_namemax = 255
            return sf

        # ── Directory ops ──────────────────────────────────────────

        async def opendir(self, inode, ctx=None):
            return inode

        async def readdir(self, fh, start_id, token):
            meta = self._meta_for(fh)
            if meta is None:
                raise pyfuse3.FUSEError(errno.ENOTDIR)
            tag, path = meta
            if tag == 'root':
                # Synthetic subtree children, no WS call.
                fixed = [(n, i) for n, i, _t in SUBTREES]
                i = int(start_id)
                while i < len(fixed):
                    name, ino = fixed[i]
                    attrs = self._synthetic_dir_attrs(ino)
                    next_id = i + 1
                    if not pyfuse3.readdir_reply(
                            token, name.encode('utf-8'), attrs, next_id):
                        return
                    i = next_id
                return
            r = await self._req(tag, 'readdir', {'path': path})
            if 'error' in r:
                raise pyfuse3.FUSEError(_to_fuse_error(r))
            entries = list(r['data']['entries'])
            i = int(start_id)
            while i < len(entries):
                name = entries[i]
                child_path = os.path.normpath(os.path.join(path, name))
                gr = await self._req(tag, 'getattr', {'path': child_path})
                if 'error' in gr:
                    i += 1
                    continue
                child_ino = self._ino_for(tag, child_path)
                attrs = self._attrs_from_server(gr['data'], child_ino)
                next_id = i + 1
                if not pyfuse3.readdir_reply(
                        token, name.encode('utf-8'), attrs, next_id):
                    return
                i = next_id

        async def releasedir(self, fh):
            return

        # ── File I/O ───────────────────────────────────────────────

        async def open(self, inode, flags, ctx=None):
            meta = self._meta_for(inode)
            if meta is None or meta[0] == 'root':
                raise pyfuse3.FUSEError(errno.EISDIR)
            tag, path = meta
            r = await self._req(tag, 'open',
                                 {'path': path, 'flags': int(flags)})
            if 'error' in r:
                raise pyfuse3.FUSEError(_to_fuse_error(r))
            # Pack tag into the FUSE fh so read/write/release can route
            # back to the right backend without re-looking up the inode.
            backend_fh = int(r['data']['fh'])
            return pyfuse3.FileInfo(fh=self._tag_fh(tag, backend_fh))

        @classmethod
        def _untag_fh(cls, fh: int):
            idx = (int(fh) >> cls._FH_TAG_SHIFT) & 0x3
            return TAG_BY_INDEX[idx], int(fh) & cls._FH_MASK

        async def read(self, fh, offset, size):
            tag, real_fh = self._untag_fh(int(fh))
            out = bytearray()
            cur = int(offset)
            remaining = int(size)
            while remaining > 0:
                chunk_size = min(remaining, _MAX_CHUNK)
                r = await self._req(tag, 'read', {
                    'fh': int(real_fh), 'offset': cur, 'size': chunk_size,
                })
                if 'error' in r:
                    raise pyfuse3.FUSEError(_to_fuse_error(r))
                chunk = base64.b64decode(r['data']['data_b64'])
                if not chunk:
                    break
                out.extend(chunk)
                cur += len(chunk)
                remaining -= len(chunk)
            return bytes(out)

        async def write(self, fh, offset, buf):
            tag, real_fh = self._untag_fh(int(fh))
            total = 0
            cur = int(offset)
            view = memoryview(buf)
            while view:
                chunk = bytes(view[:_MAX_CHUNK])
                r = await self._req(tag, 'write', {
                    'fh': int(real_fh), 'offset': cur,
                    'data_b64': base64.b64encode(chunk).decode('ascii'),
                })
                if 'error' in r:
                    raise pyfuse3.FUSEError(_to_fuse_error(r))
                n = int(r['data'].get('bytes_written', 0))
                if n <= 0:
                    break
                total += n
                cur += n
                view = view[n:]
            return total

        async def release(self, fh):
            tag, real_fh = self._untag_fh(int(fh))
            r = await self._req(tag, 'release', {'fh': int(real_fh)})
            if 'error' in r:
                logger.warning(
                    '[combined-fs] release(fh=%s tag=%s) error: %s',
                    real_fh, tag, r.get('message', r.get('error')))

        # ── Create / delete ────────────────────────────────────────

        async def create(self, parent_inode, name, mode, flags, ctx=None):
            pmeta = self._meta_for(parent_inode)
            if pmeta is None or pmeta[0] == 'root':
                raise pyfuse3.FUSEError(errno.EROFS)
            tag, ppath = pmeta
            path = os.path.normpath(os.path.join(ppath, self._decode(name)))
            r = await self._req(tag, 'create',
                                 {'path': path, 'mode': int(mode)})
            if 'error' in r:
                raise pyfuse3.FUSEError(_to_fuse_error(r))
            backend_fh = int(r['data']['fh'])
            tagged = self._tag_fh(tag, backend_fh)
            ar = await self._req(tag, 'getattr', {'path': path})
            if 'error' in ar:
                raise pyfuse3.FUSEError(_to_fuse_error(ar))
            ino = self._ino_for(tag, path)
            return (pyfuse3.FileInfo(fh=tagged),
                    self._attrs_from_server(ar['data'], ino))

        async def unlink(self, parent_inode, name, ctx=None):
            pmeta = self._meta_for(parent_inode)
            if pmeta is None or pmeta[0] == 'root':
                raise pyfuse3.FUSEError(errno.EROFS)
            tag, ppath = pmeta
            path = os.path.normpath(os.path.join(ppath, self._decode(name)))
            r = await self._req(tag, 'unlink', {'path': path})
            if 'error' in r:
                raise pyfuse3.FUSEError(_to_fuse_error(r))
            self._drop_path(tag, path)

        async def mkdir(self, parent_inode, name, mode, ctx=None):
            pmeta = self._meta_for(parent_inode)
            if pmeta is None or pmeta[0] == 'root':
                raise pyfuse3.FUSEError(errno.EROFS)
            tag, ppath = pmeta
            path = os.path.normpath(os.path.join(ppath, self._decode(name)))
            r = await self._req(tag, 'mkdir',
                                 {'path': path, 'mode': int(mode)})
            if 'error' in r:
                raise pyfuse3.FUSEError(_to_fuse_error(r))
            ar = await self._req(tag, 'getattr', {'path': path})
            if 'error' in ar:
                raise pyfuse3.FUSEError(_to_fuse_error(ar))
            ino = self._ino_for(tag, path)
            return self._attrs_from_server(ar['data'], ino)

        async def rmdir(self, parent_inode, name, ctx=None):
            pmeta = self._meta_for(parent_inode)
            if pmeta is None or pmeta[0] == 'root':
                raise pyfuse3.FUSEError(errno.EROFS)
            tag, ppath = pmeta
            path = os.path.normpath(os.path.join(ppath, self._decode(name)))
            r = await self._req(tag, 'rmdir', {'path': path})
            if 'error' in r:
                raise pyfuse3.FUSEError(_to_fuse_error(r))
            self._drop_path(tag, path)

        async def rename(self, parent_inode_old, name_old,
                         parent_inode_new, name_new, flags, ctx=None):
            ometa = self._meta_for(parent_inode_old)
            nmeta = self._meta_for(parent_inode_new)
            if (ometa is None or nmeta is None
                    or ometa[0] == 'root' or nmeta[0] == 'root'
                    or ometa[0] != nmeta[0]):
                # Cross-subtree rename isn't representable through one
                # backend protocol; refuse it.
                raise pyfuse3.FUSEError(errno.EXDEV)
            tag = ometa[0]
            old = os.path.normpath(os.path.join(ometa[1], self._decode(name_old)))
            new = os.path.normpath(os.path.join(nmeta[1], self._decode(name_new)))
            r = await self._req(tag, 'rename', {'old': old, 'new': new})
            if 'error' in r:
                raise pyfuse3.FUSEError(_to_fuse_error(r))
            with self._lock:
                ino = self._meta_ino.pop((tag, old), None)
                if ino is not None:
                    self._meta_ino[(tag, new)] = ino
                    self._ino_meta[ino] = (tag, new)

        async def setattr(self, inode, attr, fields, fh, ctx=None):
            meta = self._meta_for(inode)
            if meta is None or meta[0] == 'root':
                raise pyfuse3.FUSEError(errno.EPERM)
            tag, path = meta
            if fields.update_mode:
                r = await self._req(tag, 'chmod',
                                     {'path': path,
                                      'mode': int(attr.st_mode)})
                if 'error' in r:
                    raise pyfuse3.FUSEError(_to_fuse_error(r))
            if fields.update_size:
                args = {'length': int(attr.st_size)}
                if fh is not None:
                    _, real_fh = self._untag_fh(int(fh))
                    args['fh'] = int(real_fh)
                else:
                    args['path'] = path
                r = await self._req(tag, 'truncate', args)
                if 'error' in r:
                    raise pyfuse3.FUSEError(_to_fuse_error(r))
            if fields.update_atime or fields.update_mtime:
                args = {'path': path}
                now = _time.time()
                if fields.update_atime:
                    args['atime'] = (
                        now if getattr(fields, 'update_atime_now', False)
                        else attr.st_atime_ns / 1e9)
                if fields.update_mtime:
                    args['mtime'] = (
                        now if getattr(fields, 'update_mtime_now', False)
                        else attr.st_mtime_ns / 1e9)
                if 'atime' not in args or 'mtime' not in args:
                    d0 = await self._req(tag, 'getattr', {'path': path})
                    if 'error' not in d0:
                        args.setdefault('atime', float(d0['data']['st_atime']))
                        args.setdefault('mtime', float(d0['data']['st_mtime']))
                r = await self._req(tag, 'utimens', args)
                if 'error' in r:
                    raise pyfuse3.FUSEError(_to_fuse_error(r))
            ar = await self._req(tag, 'getattr', {'path': path})
            if 'error' in ar:
                raise pyfuse3.FUSEError(_to_fuse_error(ar))
            return self._attrs_from_server(ar['data'], inode)

    return CombinedRouterOperations
