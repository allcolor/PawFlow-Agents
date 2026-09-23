"""Relay-side supervisor of the combined server-fs FUSE mount.

The mount (one pyfuse3 session, three routed subtrees: see
``server_fs_mount._build_combined_operations_class``) is served by a child
process, ``pawflow_relay.fuse_responder`` -- never by the relay worker, whose
command threads read the mount; that module explains the kernel deadlock this
prevents. This supervisor answers the responder's backend requests with the
swappable WS clients, checks the responder stays alive and responsive,
restarts it if it dies, and tears everything down in order on stop().
"""

import errno
import logging
import os
import socket
import subprocess  # nosec B404
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from pawflow_relay.fuse_responder import recv_frame, send_frame

logger = logging.getLogger(__name__)


class CombinedServerFsMount:
    """Single FUSE mount at `mountpoint`/ exposing three routed subtrees.

    The mount's root contains exactly three synthetic directories,
    `cc_sessions`, `filestore` and `skills`, whose FS ops are answered by
    `sfs_client`, `ffs_client` and `skfs_client` respectively. The relay
    exposes those subtrees on the canonical /cc_sessions, /filestore and
    /skills paths.

    One mount, not three: pyfuse3 keeps a single global session per process,
    so separate mounts would race on `pyfuse3.init()` and orphan the loser.

    stop() tears down in order: refuse new work, detach the mount, end the
    responder, and only as a last resort abort its kernel connection.
    """

    _WORKERS = 32
    _READY_TIMEOUT = 15.0
    _PING_INTERVAL = 10.0
    _HANG_TIMEOUT = 30.0
    _EXIT_GRACE = 5.0

    def __init__(self, mountpoint: str, sfs_client, ffs_client, skfs_client,
                 allow_other: bool = False,
                 request_timeout: float = 30.0,
                 fsname: str = 'pawflow-combined-fs'):
        self._mountpoint = mountpoint
        self._routes = (('sfs.', sfs_client), ('ffs.', ffs_client),
                        ('skfs.', skfs_client))
        self._allow_other = allow_other
        self._timeout = request_timeout
        self._fsname = fsname
        self._lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self._sock: Optional[socket.socket] = None
        self._sock_lock = threading.Lock()
        self._connection: Optional[int] = None
        self._last_pong = 0.0
        self._loop_age = 0.0
        self._started = False
        self._stopping = threading.Event()
        self._pool: Optional[ThreadPoolExecutor] = None
        self._supervisor: Optional[threading.Thread] = None

    # ── Lifecycle ──────────────────────────────────────────────────

    def start(self) -> None:
        """Mount; raises RuntimeError when the responder cannot mount."""
        if self._started:
            raise RuntimeError('mount already started')
        self._started = True
        # Detach a leftover mount before touching the path: a dead one fails
        # every stat with ENOTCONN.
        self._try_unmount(silent=True)
        os.makedirs(self._mountpoint, exist_ok=True)
        self._pool = ThreadPoolExecutor(
            max_workers=self._WORKERS, thread_name_prefix='combined-fs-req')
        try:
            self._spawn()
        except BaseException:
            self._pool.shutdown(wait=False)
            raise
        self._supervisor = threading.Thread(
            target=self._supervise, name='combined-fs-supervisor', daemon=True)
        self._supervisor.start()
        threading.Thread(target=self._watch_health, name='combined-fs-health',
                         daemon=True).start()

    def stop(self) -> None:
        """Idempotent ordered teardown; safe on every exit path."""
        if self._stopping.is_set():
            return
        self._stopping.set()  # from here every backend request gets EIO
        with self._lock:
            proc, sock, connection = self._proc, self._sock, self._connection
        # Detach first: no new lookup can reach the mount any more.
        self._try_unmount(silent=False)
        # End of stream makes the responder stop its loop, unmount and exit.
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if proc is not None and not self._wait(proc, self._EXIT_GRACE):
            logger.warning('[combined-fs] responder pid=%s ignored end of '
                           'stream; killing it', proc.pid)
            self._kill(proc)
            if not self._wait(proc, self._EXIT_GRACE):
                self._abort_connection(connection)
        if self._supervisor is not None:
            self._supervisor.join(timeout=self._EXIT_GRACE)
        if sock is not None:
            sock.close()
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)

    # ── Responder process ──────────────────────────────────────────

    def _spawn(self) -> None:
        parent_sock, child_sock = socket.socketpair()
        pkg_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env = dict(os.environ)
        env['PYTHONPATH'] = os.pathsep.join(
            p for p in (pkg_root, env.get('PYTHONPATH', '')) if p)
        cmd = [sys.executable, '-m', 'pawflow_relay.fuse_responder',
               '--fd', str(child_sock.fileno()),
               '--mountpoint', self._mountpoint, '--fsname', self._fsname,
               '--request-timeout', str(self._timeout)]
        if self._allow_other:
            cmd.append('--allow-other')
        try:
            # Own session: terminal signals aimed at the relay must not end
            # the responder before the relay has detached the mount.
            proc = subprocess.Popen(  # nosec B603
                cmd, pass_fds=(child_sock.fileno(),), env=env,
                start_new_session=True)
        except BaseException:
            parent_sock.close()
            raise
        finally:
            child_sock.close()
        parent_sock.settimeout(self._READY_TIMEOUT)
        try:
            frame = recv_frame(parent_sock)
        except (OSError, ValueError) as exc:
            frame = {'type': 'failed', 'error': f'no ready signal: {exc}'}
        if not frame or frame.get('type') != 'ready':
            parent_sock.close()
            self._kill(proc)
            self._wait(proc, self._EXIT_GRACE)
            reason = ((frame or {}).get('error')
                      or f'responder exited rc={proc.poll()}')
            raise RuntimeError(
                f'combined-fs responder could not mount {self._mountpoint}: '
                f'{reason}')
        parent_sock.settimeout(None)
        with self._lock:
            if self._stopping.is_set():
                parent_sock.close()
                self._kill(proc)
                raise RuntimeError('combined-fs stopping')
            self._proc, self._sock = proc, parent_sock
            self._sock_lock = threading.Lock()
            self._connection = frame.get('connection')
            self._last_pong, self._loop_age = time.monotonic(), 0.0
        logger.info('[combined-fs] mounted at %s by responder pid=%s '
                    '(fuse connection %s)', self._mountpoint, proc.pid,
                    self._connection)

    def _supervise(self) -> None:
        """Serve the responder; restart it if it dies while not stopping."""
        backoff = 1.0
        while True:
            with self._lock:
                proc, sock, sock_lock = self._proc, self._sock, self._sock_lock
            self._serve(sock, sock_lock)
            rc = self._reap(proc)
            if self._stopping.is_set():
                return
            logger.error('[combined-fs] responder pid=%s exited (rc=%s) '
                         'while mounted; restarting it', proc.pid, rc)
            self._try_unmount(silent=True)
            while not self._stopping.wait(backoff):
                backoff = min(backoff * 2, 30.0)
                try:
                    self._spawn()
                    backoff = 1.0
                    break
                except Exception as exc:
                    logger.error('[combined-fs] restart failed: %s', exc)
            else:
                return

    def _serve(self, sock: socket.socket, sock_lock: threading.Lock) -> None:
        """Dispatch the responder's frames until end of stream."""
        try:
            while True:
                frame = recv_frame(sock)
                if frame is None:
                    return
                kind = frame.get('type')
                if kind == 'req':
                    self._pool.submit(self._answer, sock, sock_lock, frame)
                elif kind == 'pong':
                    self._last_pong = time.monotonic()
                    self._loop_age = float(frame.get('loop_age') or 0.0)
        except (OSError, ValueError, RuntimeError) as exc:
            if not self._stopping.is_set():
                logger.warning('[combined-fs] responder link lost: %s', exc)

    def _answer(self, sock, sock_lock, frame: dict) -> None:
        method = str(frame.get('method') or '')
        if self._stopping.is_set():
            reply = {'error': 'EIO', 'errno': errno.EIO,
                     'message': 'relay stopping'}
        else:
            reply = self._route(method, frame.get('args') or {},
                                frame.get('timeout'))
        try:
            send_frame(sock, sock_lock,
                       {'type': 'rep', 'id': frame.get('id'), 'reply': reply})
        except (OSError, ValueError):
            pass  # the responder is gone; it already failed the request

    def _route(self, method: str, args: dict, timeout) -> dict:
        for prefix, client in self._routes:
            if method.startswith(prefix) and client is not None:
                try:
                    return client.request(method, args, timeout)
                except Exception as exc:
                    return {'error': 'EIO', 'errno': errno.EIO,
                            'message': f'{method} failed: {exc}'}
        return {'error': 'EIO', 'errno': errno.EIO,
                'message': f'no backend for {method!r}'}

    def _watch_health(self) -> None:
        """Kill a responder that stops answering, so the kernel aborts its
        requests instead of leaving their callers blocked forever."""
        while not self._stopping.wait(self._PING_INTERVAL):
            with self._lock:
                proc, sock, sock_lock = self._proc, self._sock, self._sock_lock
            if proc is None or proc.poll() is not None:
                continue
            try:
                send_frame(sock, sock_lock, {'type': 'ping'})
            except (OSError, ValueError):
                continue
            silent = time.monotonic() - self._last_pong
            if silent > self._HANG_TIMEOUT or self._loop_age > self._HANG_TIMEOUT:
                logger.error('[combined-fs] responder pid=%s unresponsive '
                             '(no pong for %.0fs, loop stalled %.0fs); '
                             'killing it', proc.pid, silent, self._loop_age)
                self._kill(proc)

    @staticmethod
    def _wait(proc: subprocess.Popen, timeout: float) -> bool:
        try:
            proc.wait(timeout=timeout)
            return True
        except subprocess.TimeoutExpired:
            return False

    @staticmethod
    def _kill(proc: subprocess.Popen) -> None:
        try:
            proc.kill()
        except OSError:
            pass

    def _reap(self, proc: subprocess.Popen):
        if not self._wait(proc, self._EXIT_GRACE):
            self._kill(proc)
            self._wait(proc, self._EXIT_GRACE)
        return proc.poll()

    def _abort_connection(self, connection: Optional[int]) -> None:
        """Last resort for a responder that SIGKILL did not end.

        Only the connection id recorded when this mount came up is touched,
        and only while its responder is unreaped: the connection cannot have
        been freed, so its id cannot name another filesystem.
        """
        if connection is None:
            logger.error('[combined-fs] responder unkillable and its fuse '
                         'connection is unknown; cannot abort it')
            return
        path = f'/sys/fs/fuse/connections/{int(connection)}/abort'
        if not os.path.exists(path):
            logger.error('[combined-fs] cannot abort fuse connection %s: %s '
                         'is absent (fusectl not mounted)', connection, path)
            return
        try:
            with open(path, 'w', encoding='ascii') as fh:
                fh.write('1')
        except PermissionError:
            r = subprocess.run(['sudo', '-n', 'tee', path], input='1',  # nosec B603 B607
                               capture_output=True, text=True, timeout=5)
            if r.returncode != 0:
                logger.error('[combined-fs] abort of fuse connection %s '
                             'refused: %s', connection, r.stderr.strip())
                return
        except OSError as exc:
            logger.error('[combined-fs] abort of fuse connection %s failed: '
                         '%s', connection, exc)
            return
        logger.error('[combined-fs] aborted fuse connection %s', connection)

    def _try_unmount(self, silent: bool) -> None:
        """Lazily detach the mount: a busy mount must not block teardown.

        No stat of the mountpoint first -- that would be a FUSE request of its
        own, and fails with ENOTCONN on a dead mount.
        """
        for cmd in (['fusermount3', '-u', '-z', self._mountpoint],
                    ['fusermount', '-u', '-z', self._mountpoint],
                    ['umount', '-l', self._mountpoint]):
            try:
                r = subprocess.run(cmd, capture_output=True,  # nosec B603
                                   text=True, timeout=5)
                if r.returncode == 0:
                    return
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue
        if not silent:
            logger.warning('[combined-fs] unmount %s failed (all backends)',
                           self._mountpoint)
