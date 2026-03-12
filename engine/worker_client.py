"""Worker Client - HTTP client for sending tasks to remote workers.

Streams FlowFiles to a remote WorkerServer using the binary protocol,
receives results back without loading everything into memory at once.
"""

import io
import json
import logging
from http.client import HTTPConnection
from typing import List, Tuple, Optional, Dict, Any

from core import FlowFile
from engine.worker_protocol import FlowFileSerializer

logger = logging.getLogger(__name__)


class WorkerClient:
    """Client for communicating with a remote WorkerServer.

    Usage:
        client = WorkerClient("192.168.1.10", 8081)
        results, meta = client.execute_task(flowfile, "t1", "log", {"message": "hi", "level": "INFO"})
    """

    def __init__(self, host: str, port: int, timeout: int = 30):
        self.host = host
        self.port = port
        self.timeout = timeout

    def execute_task(self, flowfile: FlowFile, task_id: str,
                     task_type: str, config: dict) -> Tuple[List[FlowFile], dict]:
        """Send a FlowFile to the remote worker for execution.

        Serializes the FlowFile + task metadata, sends via HTTP POST,
        and deserializes the result FlowFiles.

        Returns:
            (list_of_result_flowfiles, result_metadata_dict)
        """
        # Serialize to buffer (need Content-Length for HTTP)
        buf = io.BytesIO()
        FlowFileSerializer.serialize_to_stream(
            flowfile, buf, task_id=task_id, task_type=task_type, config=config
        )
        body = buf.getvalue()

        conn = HTTPConnection(self.host, self.port, timeout=self.timeout)
        try:
            conn.request(
                "POST", "/execute", body=body,
                headers={
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(len(body)),
                },
            )
            response = conn.getresponse()

            if response.status != 200:
                error_body = response.read().decode("utf-8", errors="replace")
                raise RuntimeError(
                    f"Worker returned HTTP {response.status}: {error_body}"
                )

            # Deserialize result from response stream
            flowfiles, metadata = FlowFileSerializer.deserialize_result_from_stream(
                response
            )
            return flowfiles, metadata

        finally:
            conn.close()

    def heartbeat(self) -> bool:
        """Send a heartbeat ping. Returns True if worker is alive."""
        conn = HTTPConnection(self.host, self.port, timeout=self.timeout)
        try:
            conn.request("POST", "/heartbeat", body=b"",
                         headers={"Content-Length": "0"})
            response = conn.getresponse()
            response.read()  # drain
            return response.status == 200
        except Exception:
            return False
        finally:
            conn.close()

    def get_status(self) -> dict:
        """Get the worker's status."""
        conn = HTTPConnection(self.host, self.port, timeout=self.timeout)
        try:
            conn.request("GET", "/status")
            response = conn.getresponse()
            body = response.read()
            return json.loads(body.decode("utf-8"))
        finally:
            conn.close()
