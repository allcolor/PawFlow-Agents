"""Worker Server - HTTP server for remote task execution.

Lightweight HTTP server (stdlib only) that receives FlowFiles via
the binary streaming protocol, executes tasks, and returns results.
"""

import json
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional

from core import TaskFactory, FlowFile
from engine.worker_protocol import FlowFileSerializer

logger = logging.getLogger(__name__)


class _WorkerHandler(BaseHTTPRequestHandler):
    """HTTP request handler for worker endpoints."""

    def log_message(self, format, *args):
        logger.debug(format, *args)

    def _check_auth(self) -> bool:
        """Check API key authentication if configured."""
        api_key = getattr(self.server, "api_key", None)
        if not api_key:
            return True  # No auth configured
        auth_header = self.headers.get("Authorization", "")
        if auth_header == f"Bearer {api_key}":
            return True
        self._send_json(401, {"error": "unauthorized"})
        return False

    def do_POST(self):
        if not self._check_auth():
            return
        if self.path == "/execute":
            self._handle_execute()
        elif self.path == "/heartbeat":
            self._send_json(200, {"status": "ok"})
        else:
            self._send_json(404, {"error": "not found"})

    def do_GET(self):
        if not self._check_auth():
            return
        if self.path == "/status":
            self._handle_status()
        else:
            self._send_json(404, {"error": "not found"})

    def _handle_execute(self):
        """Receive a FlowFile, execute the task, return results."""
        try:
            # Read content length to create a limited reader
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length <= 0:
                self._send_json(400, {"error": "missing Content-Length"})
                return

            # Deserialize FlowFile from request body stream
            limited = _LimitedReader(self.rfile, content_length)
            flowfile, metadata = FlowFileSerializer.deserialize_from_stream(limited)

            task_type = metadata["task_type"]
            config = metadata["config"]
            task_id = metadata.get("task_id", "")

            # Execute the task
            task_class = TaskFactory.get(task_type)
            task = task_class(config)
            results = task.execute(flowfile)
            if results is None:
                results = []

            # Serialize results to a buffer (need Content-Length for response)
            import io
            buf = io.BytesIO()
            FlowFileSerializer.serialize_result_to_stream(
                results, buf, assignment_id=task_id
            )
            response_data = buf.getvalue()

            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(response_data)))
            self.end_headers()
            self.wfile.write(response_data)

        except Exception as e:
            logger.error(f"Execute error: {e}")
            # Send error result via protocol
            import io
            buf = io.BytesIO()
            FlowFileSerializer.serialize_result_to_stream(
                [], buf, assignment_id=metadata.get("task_id", "") if 'metadata' in dir() else "",
                error=str(e)
            )
            response_data = buf.getvalue()

            self.send_response(200)  # 200 because the error is in the protocol
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(response_data)))
            self.end_headers()
            self.wfile.write(response_data)

    def _handle_status(self):
        server = self.server
        info = {
            "worker_name": getattr(server, "worker_name", "unknown"),
            "status": "running",
        }
        self._send_json(200, info)

    def _send_json(self, code: int, data: dict):
        body = json.dumps(data).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _LimitedReader:
    """Read at most `limit` bytes from a stream."""

    def __init__(self, stream, limit: int):
        self._stream = stream
        self._remaining = limit

    def read(self, n: int = -1) -> bytes:
        if self._remaining <= 0:
            return b""
        if n < 0:
            n = self._remaining
        to_read = min(n, self._remaining)
        data = self._stream.read(to_read)
        self._remaining -= len(data)
        return data


class WorkerServer:
    """HTTP server for a remote worker.

    Usage:
        server = WorkerServer(port=8081)
        server.start()   # runs in daemon thread
        ...
        server.stop()
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 8081,  # nosec B104 - remote worker listens on configured interface.
                 worker_name: str = "remote-worker",
                 api_key: Optional[str] = None):
        self.host = host
        self.port = port
        self.worker_name = worker_name
        self.api_key = api_key
        self._httpd: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self):
        """Start the server in a daemon thread."""
        self._httpd = HTTPServer((self.host, self.port), _WorkerHandler)
        self._httpd.worker_name = self.worker_name
        self._httpd.api_key = self.api_key
        # Update port in case port=0 was used (ephemeral)
        self.port = self._httpd.server_address[1]

        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name=f"worker-{self.worker_name}",
            daemon=True,
        )
        self._thread.start()
        logger.info(f"WorkerServer '{self.worker_name}' started on {self.address}")

    def stop(self):
        """Stop the server."""
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        logger.info(f"WorkerServer '{self.worker_name}' stopped")

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"
