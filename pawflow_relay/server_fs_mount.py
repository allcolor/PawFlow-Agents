"""FUSE3 filesystem that proxies every syscall to the PawFlow server.

Mounts a directory on the relay host (typically `/var/lib/pawflow/server-mount`
or a per-relay tmpdir). The relay docker bind-mounts that path at
`/cc_sessions` so containerized tools see the user's session files via
canonical paths identical to what CC itself uses.

Depends on `pyfuse3` + `trio` + `libfuse3`. The imports are lazy so this
module can be loaded for inspection without the libs installed; only
`mount()` requires them.
"""

import base64
import errno
import logging
import os
import subprocess
import threading
import time as _time
from typing import Optional

from pawflow_relay.server_fs_client import ServerFsClient

logger = logging.getLogger(__name__)

# Match the server's MAX_READ_CHUNK — if FUSE asks for more, we chunk.
_MAX_CHUNK = 1 * 1024 * 1024


def _to_fuse_error(reply: dict) -> int:
    """Map a server reply's `errno` field to a FUSE-compatible errno int."""
    return int(reply.get('errno') or errno.EIO)


def _build_operations_class():
    """Build the pyfuse3 Operations subclass.

    Done lazily so that importing this module doesn't require pyfuse3.
    pyfuse3 is inode-based, but the server speaks paths; we maintain a
    monotonic inode<->path map populated by lookup/readdir/create.
    """
    import pyfuse3
    import trio

    class ServerFsOperations(pyfuse3.Operations):
        """Read-write pyfuse3 proxy to the PawFlow server session slot."""

        _ATTR_TIMEOUT = 1.0
        _ENTRY_TIMEOUT = 1.0

        def __init__(self, client: ServerFsClient,
                     request_timeout: float = 30.0):
            super().__init__()
            self._cli = client
            self._timeout = request_timeout
            self._next_ino = pyfuse3.ROOT_INODE + 1
            self._ino2path: dict = {pyfuse3.ROOT_INODE: '/'}
            self._path2ino: dict = {'/': pyfuse3.ROOT_INODE}
            self._lock = threading.Lock()

        # ── Inode bookkeeping ──────────────────────────────────────────

        def _ino_for_path(self, path: str) -> int:
            with self._lock:
                ino = self._path2ino.get(path)
                if ino is None:
                    ino = self._next_ino
                    self._next_ino += 1
                    self._path2ino[path] = ino
                    self._ino2path[ino] = path
                return ino

        def _path_for_ino(self, ino: int) -> str:
            with self._lock:
                p = self._ino2path.get(ino)
                if p is None:
                    raise pyfuse3.FUSEError(errno.ESTALE)
                return p

        def _drop(self, path: str) -> None:
            with self._lock:
                ino = self._path2ino.pop(path, None)
                if ino is not None:
                    self._ino2path.pop(ino, None)

        # ── Server call helper ─────────────────────────────────────────
        # ServerFsClient.request() is synchronous (blocks on a threading
        # Event). Running it directly would block the trio event loop for
        # every other FUSE request. Always dispatch to a worker thread.

        async def _req(self, op: str, args: dict) -> dict:
            timeout = self._timeout
            cli = self._cli
            return await trio.to_thread.run_sync(
                lambda: cli.request(op, args, timeout))

        # ── Attribute helpers ──────────────────────────────────────────

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

        async def _server_getattr(self, path: str) -> dict:
            r = await self._req('sfs.getattr', {'path': path})
            if 'error' in r:
                raise pyfuse3.FUSEError(_to_fuse_error(r))
            return r['data']

        @staticmethod
        def _decode(name):
            return name.decode('utf-8') if isinstance(name, bytes) else name

        # ── Metadata ───────────────────────────────────────────────────

        async def getattr(self, inode, ctx=None):
            path = self._path_for_ino(inode)
            d = await self._server_getattr(path)
            return self._attrs_from_server(d, inode)

        async def lookup(self, parent_inode, name, ctx=None):
            parent = self._path_for_ino(parent_inode)
            name_s = self._decode(name)
            path = os.path.normpath(os.path.join(parent, name_s))
            r = await self._req('sfs.getattr', {'path': path})
            if 'error' in r:
                raise pyfuse3.FUSEError(_to_fuse_error(r))
            ino = self._ino_for_path(path)
            return self._attrs_from_server(r['data'], ino)

        async def statfs(self, ctx=None):
            r = await self._req('sfs.statfs', {'path': '/'})
            if 'error' in r:
                raise pyfuse3.FUSEError(_to_fuse_error(r))
            d = r['data']
            sf = pyfuse3.StatvfsData()
            sf.f_bsize = int(d.get('f_bsize', 4096))
            sf.f_frsize = int(d.get('f_frsize', sf.f_bsize))
            sf.f_blocks = int(d.get('f_blocks', 0))
            sf.f_bfree = int(d.get('f_bfree', 0))
            sf.f_bavail = int(d.get('f_bavail', 0))
            sf.f_files = int(d.get('f_files', 0))
            sf.f_ffree = int(d.get('f_ffree', 0))
            sf.f_favail = int(d.get('f_favail', 0))
            sf.f_namemax = int(d.get('f_namemax', 255))
            return sf

        # ── Directory ops ──────────────────────────────────────────────

        async def opendir(self, inode, ctx=None):
            return inode  # reuse inode as the dir fh

        async def readdir(self, fh, start_id, token):
            path = self._path_for_ino(fh)
            r = await self._req('sfs.readdir', {'path': path})
            if 'error' in r:
                raise pyfuse3.FUSEError(_to_fuse_error(r))
            entries = list(r['data']['entries'])
            i = int(start_id)
            while i < len(entries):
                name = entries[i]
                child_path = os.path.normpath(os.path.join(path, name))
                try:
                    d = await self._server_getattr(child_path)
                except pyfuse3.FUSEError:
                    i += 1
                    continue
                child_ino = self._ino_for_path(child_path)
                attrs = self._attrs_from_server(d, child_ino)
                next_id = i + 1
                if not pyfuse3.readdir_reply(
                        token, name.encode('utf-8'), attrs, next_id):
                    return
                i = next_id

        async def releasedir(self, fh):
            return

        # ── File I/O ───────────────────────────────────────────────────

        async def open(self, inode, flags, ctx=None):
            path = self._path_for_ino(inode)
            r = await self._req('sfs.open',
                                 {'path': path, 'flags': int(flags)})
            if 'error' in r:
                raise pyfuse3.FUSEError(_to_fuse_error(r))
            return pyfuse3.FileInfo(fh=int(r['data']['fh']))

        async def read(self, fh, offset, size):
            out = bytearray()
            cur = int(offset)
            remaining = int(size)
            while remaining > 0:
                chunk_size = min(remaining, _MAX_CHUNK)
                r = await self._req('sfs.read', {
                    'fh': int(fh), 'offset': cur, 'size': chunk_size,
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
            total = 0
            cur = int(offset)
            view = memoryview(buf)
            while view:
                chunk = bytes(view[:_MAX_CHUNK])
                r = await self._req('sfs.write', {
                    'fh': int(fh), 'offset': cur,
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
            r = await self._req('sfs.release', {'fh': int(fh)})
            if 'error' in r:
                logger.warning('[server-fs] release(fh=%s) error: %s',
                               fh, r.get('message', r.get('error')))

        # ── Create / delete ────────────────────────────────────────────

        async def create(self, parent_inode, name, mode, flags, ctx=None):
            parent = self._path_for_ino(parent_inode)
            path = os.path.normpath(os.path.join(
                parent, self._decode(name)))
            r = await self._req('sfs.create',
                                 {'path': path, 'mode': int(mode)})
            if 'error' in r:
                raise pyfuse3.FUSEError(_to_fuse_error(r))
            fh = int(r['data']['fh'])
            d = await self._server_getattr(path)
            ino = self._ino_for_path(path)
            return pyfuse3.FileInfo(fh=fh), self._attrs_from_server(d, ino)

        async def unlink(self, parent_inode, name, ctx=None):
            parent = self._path_for_ino(parent_inode)
            path = os.path.normpath(os.path.join(
                parent, self._decode(name)))
            r = await self._req('sfs.unlink', {'path': path})
            if 'error' in r:
                raise pyfuse3.FUSEError(_to_fuse_error(r))
            self._drop(path)

        async def mkdir(self, parent_inode, name, mode, ctx=None):
            parent = self._path_for_ino(parent_inode)
            path = os.path.normpath(os.path.join(
                parent, self._decode(name)))
            r = await self._req('sfs.mkdir',
                                 {'path': path, 'mode': int(mode)})
            if 'error' in r:
                raise pyfuse3.FUSEError(_to_fuse_error(r))
            d = await self._server_getattr(path)
            ino = self._ino_for_path(path)
            return self._attrs_from_server(d, ino)

        async def rmdir(self, parent_inode, name, ctx=None):
            parent = self._path_for_ino(parent_inode)
            path = os.path.normpath(os.path.join(
                parent, self._decode(name)))
            r = await self._req('sfs.rmdir', {'path': path})
            if 'error' in r:
                raise pyfuse3.FUSEError(_to_fuse_error(r))
            self._drop(path)

        async def rename(self, parent_inode_old, name_old,
                         parent_inode_new, name_new, flags, ctx=None):
            p_old = self._path_for_ino(parent_inode_old)
            p_new = self._path_for_ino(parent_inode_new)
            old = os.path.normpath(os.path.join(
                p_old, self._decode(name_old)))
            new = os.path.normpath(os.path.join(
                p_new, self._decode(name_new)))
            r = await self._req('sfs.rename', {'old': old, 'new': new})
            if 'error' in r:
                raise pyfuse3.FUSEError(_to_fuse_error(r))
            with self._lock:
                ino = self._path2ino.pop(old, None)
                if ino is not None:
                    self._path2ino[new] = ino
                    self._ino2path[ino] = new

        # ── setattr — replaces chmod / truncate / utimens ──────────────

        async def setattr(self, inode, attr, fields, fh, ctx=None):
            path = self._path_for_ino(inode)
            if fields.update_mode:
                r = await self._req('sfs.chmod',
                                     {'path': path,
                                      'mode': int(attr.st_mode)})
                if 'error' in r:
                    raise pyfuse3.FUSEError(_to_fuse_error(r))
            if fields.update_size:
                args = {'length': int(attr.st_size)}
                if fh is not None:
                    args['fh'] = int(fh)
                else:
                    args['path'] = path
                r = await self._req('sfs.truncate', args)
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
                # Server sfs.utimens needs both — fill missing half with
                # the current on-disk value so we don't accidentally
                # clobber it.
                if 'atime' not in args or 'mtime' not in args:
                    d0 = await self._server_getattr(path)
                    args.setdefault('atime', float(d0['st_atime']))
                    args.setdefault('mtime', float(d0['st_mtime']))
                r = await self._req('sfs.utimens', args)
                if 'error' in r:
                    raise pyfuse3.FUSEError(_to_fuse_error(r))
            d = await self._server_getattr(path)
            return self._attrs_from_server(d, inode)

    return ServerFsOperations


class ServerFsMount:
    """Mount lifecycle wrapper.

    Usage:
        client = ServerFsClient(send_callable=..., send_lock=...)
        mount = ServerFsMount(client, mountpoint='/cc_sessions')
        mount.start()
        # ... relay runs ...
        mount.stop()
    """

    def __init__(self, client: ServerFsClient, mountpoint: str,
                 allow_other: bool = False, request_timeout: float = 30.0):
        self._cli = client
        self._mountpoint = mountpoint
        self._allow_other = allow_other
        self._timeout = request_timeout
        self._thread: Optional[threading.Thread] = None
        self._started = threading.Event()

    def start(self) -> None:
        """Mount the FUSE filesystem in a daemon thread.

        Blocks until the mount is up (or fails). Raises if the FUSE thread
        exits before the mount becomes visible.
        """
        if self._thread is not None:
            raise RuntimeError('mount already started')
        os.makedirs(self._mountpoint, exist_ok=True)
        self._try_unmount(silent=True)

        Operations = _build_operations_class()
        import pyfuse3
        import trio

        ops = Operations(self._cli, request_timeout=self._timeout)

        fuse_opts = set(pyfuse3.default_options)
        fuse_opts.add('fsname=pawflow-server-fs')
        if self._allow_other:
            fuse_opts.add('allow_other')

        def _run():
            try:
                pyfuse3.init(ops, self._mountpoint, fuse_opts)
            except Exception as e:
                logger.error('[server-fs] pyfuse3.init failed: %s',
                             e, exc_info=True)
                self._started.set()
                return
            try:
                trio.run(pyfuse3.main)
            except Exception as e:
                logger.error('[server-fs] pyfuse3 main exited: %s',
                             e, exc_info=True)
            finally:
                try:
                    pyfuse3.close(unmount=True)
                except Exception:
                    pass
                self._started.set()

        self._thread = threading.Thread(target=_run, daemon=True,
                                         name='server-fs-fuse')
        self._thread.start()
        deadline = _time.time() + 3.0
        while _time.time() < deadline:
            if self._is_mounted():
                self._started.set()
                logger.info('[server-fs] mounted at %s', self._mountpoint)
                return
            _time.sleep(0.05)
        if not self._thread.is_alive():
            raise RuntimeError(
                f'FUSE thread died before mount became visible at {self._mountpoint}')
        logger.warning(
            '[server-fs] mount at %s not visible after 3s but thread alive',
            self._mountpoint)

    def stop(self) -> None:
        """Unmount and join the FUSE thread. Safe to call multiple times."""
        self._try_unmount(silent=False)
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        self._thread = None

    # ------------------------------------------------------------------

    def _is_mounted(self) -> bool:
        try:
            with open('/proc/self/mountinfo', 'r') as f:
                for line in f:
                    if self._mountpoint in line:
                        return True
        except OSError:
            pass
        return False

    def _try_unmount(self, silent: bool) -> None:
        if not os.path.exists(self._mountpoint):
            return
        for cmd in (['fusermount3', '-u', self._mountpoint],
                    ['fusermount', '-u', self._mountpoint],
                    ['umount', self._mountpoint]):
            try:
                r = subprocess.run(cmd, capture_output=True,
                                   text=True, timeout=5)
                if r.returncode == 0:
                    return
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue
        if not silent:
            logger.warning('[server-fs] unmount %s failed (all backends)',
                           self._mountpoint)
