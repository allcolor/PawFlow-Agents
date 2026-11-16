"""Loopback endpoint that lets relay-side scripts call PawFlow tools.

A script the server starts on this relay (``execute_script``, a PFP package
runner) receives two environment variables:

- ``PAWFLOW_TOOL_ENDPOINT`` -- ``127.0.0.1:<port>`` of this bridge, set by the
  worker in its own environment so every command it executes inherits it;
- ``PAWFLOW_TOOL_CAP`` -- an opaque capability the server minted for that one
  execution (``core/script_capability.py``).

The SDK (``docker/pawflow_sdk/pawflow.py``) sends one JSON line per request:
``{"method": "call"|"schema"|"pfp_host_call", "cap", "tool_name",
"arguments", "invocation", "host_call", "timeout"}``.
The bridge forwards it as a ``tool.call`` / ``tool.schema`` relay request over
the worker's authenticated WebSocket and writes back one JSON line,
``{"data": ...}`` or ``{"error": ..., "message": ...}``. The bridge decides
nothing: the server validates the capability against the connection that
carried it and runs the tool through its normal governed path.

The listener is created once and survives WebSocket reconnects; each
connection swaps its ServerFsClient in, exactly like the FUSE mounts.

``local=true`` commands do not run beside the worker, so they get a bridge of
their own for the length of the command (``exec_bridge``):

- on the relay host, the host helper opens one and sends each request back to
  the worker as a ``tool_request`` line on the command's own TCP connection
  (``LineTunnel``); the worker answers it through its bridge
  (``worker_request``) with a ``tool_response`` line;
- on the server-local surface, the server opens one that answers directly
  (``services/relay_tool_bridge.py``).

Every path ends at the same server handler, with the same capability check.
"""

import json
import logging
import os
import socketserver
import threading
import uuid
from contextlib import contextmanager

from pawflow_relay.server_fs_client import SwappableServerFsClient

_log = logging.getLogger(__name__)

ENDPOINT_ENV = "PAWFLOW_TOOL_ENDPOINT"
CAP_ENV = "PAWFLOW_TOOL_CAP"
DEFAULT_TIMEOUT_SECONDS = 3600.0
MAX_TIMEOUT_SECONDS = 6 * 3600.0
_MAX_REQUEST_BYTES = 64 * 1024 * 1024
_METHODS = {"call": "tool.call", "schema": "tool.schema",
            "pfp_host_call": "tool.pfp_host_call"}


def _timeout(value) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_SECONDS
    if seconds <= 0:
        return DEFAULT_TIMEOUT_SECONDS
    return min(seconds, MAX_TIMEOUT_SECONDS)


def forward(client, request: dict) -> dict:
    """Translate one SDK request into a relay request and its reply."""
    if not isinstance(request, dict):
        return {"error": "EINVAL", "message": "request must be a JSON object"}
    method = _METHODS.get(str(request.get("method") or ""))
    if method is None:
        return {"error": "EINVAL",
                "message": f"unknown method {request.get('method')!r}"}
    args = {"cap": str(request.get("cap") or "")}
    if method == "tool.pfp_host_call":
        args["invocation"] = request.get("invocation")
        args["host_call"] = request.get("host_call")
    else:
        args["tool_name"] = str(request.get("tool_name") or "")
    if method == "tool.call":
        args["arguments"] = request.get("arguments") or {}
    reply = client.request(method, args, timeout=_timeout(request.get("timeout")))
    if "error" in reply:
        return {"error": reply.get("error"),
                "message": reply.get("message") or reply.get("error")}
    return {"data": reply.get("data")}


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        while True:
            line = self.rfile.readline(_MAX_REQUEST_BYTES)
            if not line:
                return
            try:
                request = json.loads(line.decode("utf-8"))
                reply = self.server.handle_request(request)
            # Any failure is reported to the script; it never kills the listener.
            except Exception as exc:  # noqa: BLE001
                reply = {"error": "EIO", "message": str(exc)}
            self.wfile.write(
                (json.dumps(reply, ensure_ascii=False) + "\n").encode("utf-8"))
            self.wfile.flush()


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


class ToolBridge:
    """A loopback listener answering SDK requests with ``handle_request``.

    Without ``handle_request`` it forwards over the worker's WebSocket through
    ``self.swap`` (the long-lived worker bridge).
    """

    def __init__(self, handle_request=None):
        self.swap = SwappableServerFsClient()
        self._server = _Server(("127.0.0.1", 0), _Handler)
        self._server.handle_request = handle_request or (
            lambda request: forward(self.swap, request))
        self.endpoint = f"127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="relay-tool-bridge",
            daemon=True)

    def start(self, publish_env: bool = True) -> "ToolBridge":
        self._thread.start()
        if publish_env:
            # Commands this worker executes copy os.environ (tools/fs_exec.py).
            os.environ[ENDPOINT_ENV] = self.endpoint
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if os.environ.get(ENDPOINT_ENV) == self.endpoint:
            os.environ.pop(ENDPOINT_ENV, None)


_worker_bridge = None


def start_tool_bridge():
    """Start the worker bridge; return None (and log) if it cannot listen."""
    global _worker_bridge
    try:
        _worker_bridge = ToolBridge().start()
    except OSError as exc:
        _log.warning("[tool-bridge] loopback listener failed: %s", exc)
        _worker_bridge = None
    return _worker_bridge


def worker_request(request: dict) -> dict:
    """Answer an SDK request through this worker's bridge connection."""
    bridge = _worker_bridge
    if bridge is None:
        return {"error": "EIO", "message": "relay tool bridge is not running"}
    return forward(bridge.swap, request)


@contextmanager
def exec_bridge(env: dict, handle_request):
    """Give one command a bridge of its own, if it carries a capability.

    Sets ``PAWFLOW_TOOL_ENDPOINT`` in ``env`` (the command's environment)
    for the duration of the block, then closes the listener. A command
    without a capability is left unchanged.
    """
    if not isinstance(env, dict) or not env.get(CAP_ENV):
        yield None
        return
    bridge = ToolBridge(handle_request).start(publish_env=False)
    env[ENDPOINT_ENV] = bridge.endpoint
    try:
        yield bridge
    finally:
        bridge.stop()


class LineTunnel:
    """Carry SDK requests over a JSON-lines connection to the worker.

    Used by the host helper while it runs a ``local=true`` command: the
    connection is the one the worker opened to forward that command.
    ``request`` writes ``{"type": "tool_request", "id", "request"}`` and
    waits for the matching ``tool_response`` line, which ``reader`` routes.
    """

    def __init__(self, conn, timeout: float = MAX_TIMEOUT_SECONDS):
        self._conn = conn
        self._timeout = timeout
        self._send_lock = threading.Lock()
        self._pending = {}
        self._pending_lock = threading.Lock()
        self._closed = False

    def request(self, request: dict) -> dict:
        call_id = uuid.uuid4().hex
        done = threading.Event()
        holder = {}
        with self._pending_lock:
            if self._closed:
                return {"error": "EIO", "message": "relay connection closed"}
            self._pending[call_id] = (done, holder)
        line = json.dumps({"type": "tool_request", "id": call_id,
                           "request": request}, ensure_ascii=False) + "\n"
        try:
            with self._send_lock:
                self._conn.sendall(line.encode("utf-8"))
        except OSError as exc:
            with self._pending_lock:
                self._pending.pop(call_id, None)
            return {"error": "EIO", "message": f"relay connection failed: {exc}"}
        if not done.wait(_timeout(request.get("timeout"))
                         if isinstance(request, dict) else self._timeout):
            with self._pending_lock:
                self._pending.pop(call_id, None)
            return {"error": "EIO", "message": "timed out waiting for the relay"}
        return holder.get("reply") or {"error": "EIO", "message": "no reply"}

    def reader(self) -> None:
        """Route ``tool_response`` lines until the connection closes."""
        buf = b""
        try:
            while True:
                chunk = self._conn.recv(65536)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if line.strip():
                        self._dispatch(json.loads(line.decode("utf-8")))
        except (OSError, ValueError):
            _log.debug("[tool-bridge] tunnel reader stopped", exc_info=True)
        finally:
            self.close()

    def _dispatch(self, msg: dict) -> None:
        if not isinstance(msg, dict) or msg.get("type") != "tool_response":
            return
        with self._pending_lock:
            entry = self._pending.pop(str(msg.get("id") or ""), None)
        if entry is not None:
            entry[1]["reply"] = msg.get("reply")
            entry[0].set()

    def close(self) -> None:
        with self._pending_lock:
            self._closed = True
            pending = list(self._pending.values())
            self._pending.clear()
        for done, holder in pending:
            holder["reply"] = {"error": "EIO", "message": "relay connection closed"}
            done.set()
