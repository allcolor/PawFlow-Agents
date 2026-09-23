"""Out-of-process responder for the combined server-fs FUSE mount.

Why a separate process: the kernel answers a FUSE request only when the
userspace responder replies or the connection is aborted, and it aborts the
connection only when the last reference to the ``/dev/fuse`` file goes away.
Once the responder has read a request, the thread that issued it waits in
``request_wait_answer`` uninterruptibly -- SIGKILL included. When the responder
lived as a thread of the relay worker, a relay that exited while its own
command threads were enumerating ``/cc_sessions`` (``readdir``) could never
finish: the waiting threads kept the process's fd table -- and with it the
``/dev/fuse`` file -- alive, so the connection was never aborted and the
requests were never answered. The process stayed ``<defunct>`` with threads in
``D`` state, and its PID namespace outlived the container.

This module runs the responder in a child process that never accesses its own
mount. Every request it takes from the kernel is answered within a bounded
time, whatever happens to the relay, and its death -- orderly or SIGKILL --
closes the only ``/dev/fuse`` reference, which aborts every pending request.

The child speaks to the relay over an inherited ``AF_UNIX`` stream socket with
length-prefixed JSON frames:

- child -> relay: ``ready`` / ``failed`` once, then ``req`` and ``pong``;
- relay -> child: ``rep`` and ``ping``.

End of stream means the relay is gone: the child fails every pending request,
stops the FUSE loop, unmounts and exits.
"""

import argparse
import errno
import itertools
import json
import socket
import struct
import sys
import threading
import time
from typing import Any, Dict, Optional

_HEADER = struct.Struct("!I")
#: A write chunk is 1 MiB, i.e. ~1.4 MB once base64 + JSON encoded.
MAX_FRAME = 16 * 1024 * 1024
#: Extra time the child waits for the relay's reply beyond the op timeout.
#: The relay enforces the op timeout itself; this only bounds a relay that
#: stopped answering without closing the socket (frozen or dying).
REPLY_GRACE = 2.0


def send_frame(sock: socket.socket, lock: threading.Lock, obj: Dict[str, Any]) -> None:
    """Write one length-prefixed JSON frame; the lock serializes writers."""
    data = json.dumps(obj).encode("utf-8")
    if len(data) > MAX_FRAME:
        raise ValueError(f"frame of {len(data)} bytes exceeds {MAX_FRAME}")
    with lock:
        sock.sendall(_HEADER.pack(len(data)) + data)


def _recv_exact(sock: socket.socket, size: int) -> Optional[bytes]:
    buf = bytearray()
    while len(buf) < size:
        chunk = sock.recv(size - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def recv_frame(sock: socket.socket) -> Optional[Dict[str, Any]]:
    """Read one frame; None on a clean or truncated end of stream."""
    header = _recv_exact(sock, _HEADER.size)
    if header is None:
        return None
    (size,) = _HEADER.unpack(header)
    if size > MAX_FRAME:
        raise ValueError(f"frame of {size} bytes exceeds {MAX_FRAME}")
    body = _recv_exact(sock, size)
    if body is None:
        return None
    return json.loads(body.decode("utf-8"))


def fuse_connection_id(mountpoint: str, fsname: str,
                       mountinfo_path: str = "/proc/self/mountinfo") -> Optional[int]:
    """Kernel FUSE connection id of the mount at ``mountpoint`` named ``fsname``.

    The id is the minor of the anonymous device (``0:<id>``) and names the
    ``/sys/fs/fuse/connections/<id>`` directory. Only a FUSE entry whose mount
    point AND source both match is accepted, so the id positively identifies
    our own connection. None when there is no such mount.
    """
    try:
        with open(mountinfo_path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return None
    found = None
    for line in lines:
        head, sep, tail = line.partition(" - ")
        if not sep:
            continue
        fields, extra = head.split(), tail.split()
        if len(fields) < 5 or len(extra) < 2:
            continue
        if fields[4] != mountpoint:
            continue
        if not extra[0].startswith("fuse") or extra[1] != fsname:
            continue
        major, _, minor = fields[2].partition(":")
        if major == "0" and minor.isdigit():
            found = int(minor)  # the last entry is the one on top
    return found


class ParentLink:
    """Child side of the socket: forwards FUSE backend requests to the relay.

    Implements the ``request(method, args, timeout)`` contract of
    ``ServerFsClient`` so the routing Operations class is unchanged. Every
    request returns within ``timeout + REPLY_GRACE``; after end of stream every
    request fails with EIO at once.
    """

    def __init__(self, sock: socket.socket):
        self._sock = sock
        self._send_lock = threading.Lock()
        self._pending: Dict[int, tuple] = {}
        self._pending_lock = threading.Lock()
        self._ids = itertools.count(1)
        self.eof = threading.Event()
        #: Monotonic time of the FUSE loop's last tick (set by the loop).
        self.loop_tick = time.monotonic()

    def send(self, obj: Dict[str, Any]) -> None:
        send_frame(self._sock, self._send_lock, obj)

    def request(self, method: str, args: Dict[str, Any],
                timeout: Optional[float] = None) -> Dict[str, Any]:
        if self.eof.is_set():
            return {"error": "EIO", "errno": errno.EIO, "message": "relay gone"}
        op_timeout = 5.0 if timeout is None else float(timeout)
        req_id = next(self._ids)
        evt = threading.Event()
        holder: Dict[str, Any] = {}
        with self._pending_lock:
            self._pending[req_id] = (evt, holder)
        try:
            self.send({"type": "req", "id": req_id, "method": method,
                       "args": args or {}, "timeout": op_timeout})
        except (OSError, ValueError) as exc:
            with self._pending_lock:
                self._pending.pop(req_id, None)
            return {"error": "EIO", "errno": errno.EIO,
                    "message": f"send to relay failed: {exc}"}
        if not evt.wait(op_timeout + REPLY_GRACE):
            with self._pending_lock:
                self._pending.pop(req_id, None)
            return {"error": "EIO", "errno": errno.EIO,
                    "message": f"relay did not answer {method}"}
        return dict(holder)

    def serve(self) -> None:
        """Read frames from the relay until end of stream (blocking)."""
        try:
            while True:
                frame = recv_frame(self._sock)
                if frame is None:
                    break
                kind = frame.get("type")
                if kind == "rep":
                    with self._pending_lock:
                        entry = self._pending.pop(frame.get("id"), None)
                    if entry is not None:
                        entry[1].update(frame.get("reply") or {})
                        entry[0].set()
                elif kind == "ping":
                    self.send({"type": "pong",
                               "loop_age": time.monotonic() - self.loop_tick})
        except (OSError, ValueError):
            pass
        finally:
            self.close()

    def close(self) -> None:
        """Mark end of stream and fail every pending request with EIO."""
        self.eof.set()
        with self._pending_lock:
            entries = list(self._pending.values())
            self._pending.clear()
        for evt, holder in entries:
            holder.update({"error": "EIO", "errno": errno.EIO,
                           "message": "relay gone"})
            evt.set()


def run_responder(sock: socket.socket, mountpoint: str, fsname: str,
                  allow_other: bool, request_timeout: float) -> int:
    """Mount, serve until the relay closes the socket, unmount. Returns rc."""
    import pyfuse3
    import trio

    from pawflow_relay.server_fs_mount import _build_combined_operations_class

    link = ParentLink(sock)
    ops = _build_combined_operations_class()(
        link, link, link, request_timeout=request_timeout)
    options = set(pyfuse3.default_options)
    options.add(f"fsname={fsname}")
    if allow_other:
        options.add("allow_other")
    try:
        pyfuse3.init(ops, mountpoint, options)
    except Exception as exc:
        link.send({"type": "failed", "error": f"{type(exc).__name__}: {exc}"})
        return 1
    threading.Thread(target=link.serve, name="fuse-parent-link",
                     daemon=True).start()

    async def _watch_link():
        # Ticks prove the loop is alive (reported in pongs) and end the loop
        # as soon as the relay is gone.
        while not link.eof.is_set():
            link.loop_tick = time.monotonic()
            await trio.sleep(0.5)
        pyfuse3.terminate()

    async def _main():
        async with trio.open_nursery() as nursery:
            nursery.start_soon(_watch_link)
            await pyfuse3.main()
            nursery.cancel_scope.cancel()

    try:
        link.send({"type": "ready",
                   "connection": fuse_connection_id(mountpoint, fsname)})
        trio.run(_main)
    finally:
        link.close()
        # Closing the session releases /dev/fuse: any request still pending
        # in the kernel is aborted rather than left waiting.
        pyfuse3.close(unmount=True)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fd", type=int, required=True)
    parser.add_argument("--mountpoint", required=True)
    parser.add_argument("--fsname", required=True)
    parser.add_argument("--allow-other", action="store_true")
    parser.add_argument("--request-timeout", type=float, required=True)
    args = parser.parse_args(argv)
    sock = socket.socket(fileno=args.fd)
    return run_responder(sock, args.mountpoint, args.fsname,
                         args.allow_other, args.request_timeout)


if __name__ == "__main__":
    sys.exit(main())
