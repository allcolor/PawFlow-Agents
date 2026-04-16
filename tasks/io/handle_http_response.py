"""handleHTTPResponse — sends an HTTP response back through the listener.

This task reads the FlowFile content as the response body and uses
FlowFile attributes to determine the status code and headers.

Config:
    service_id: str           — ID of the HTTPListenerService
    status_code: int          — default response status (default 200)
    content_type: str         — default Content-Type (default application/json)
    headers: dict             — default response headers (can be overridden per-FF)

FlowFile attributes that override defaults:
    http.response.status      — HTTP status code (e.g. "201", "404")
    http.response.header.*    — individual response headers
    http.response.body        — if set, used instead of FlowFile content
    http.request.id           — correlation ID (REQUIRED, set by httpReceiver)
"""

import json
import logging
from typing import Any, Dict, List, Optional

from core import FlowFile
from core.base_task import BaseTask

logger = logging.getLogger(__name__)


class HandleHTTPResponseTask(BaseTask):
    """Send an HTTP response for a pending request."""

    TYPE = "handleHTTPResponse"
    DESCRIPTION = "Send an HTTP response back through the HTTP listener"
    TAGS = ["http", "io", "response"]

    PARAMETERS = {
        "service_id": {
            "type": "string",
            "description": "ID of the HTTPListenerService",
            "required": True,
        },
        "status_code": {
            "type": "integer",
            "description": "Default HTTP status code",
            "required": False,
            "default": 200,
        },
        "content_type": {
            "type": "string",
            "description": "Default Content-Type header",
            "required": False,
            "default": "application/json",
        },
        "headers": {
            "type": "object",
            "description": "Default response headers",
            "required": False,
            "default": {},
        },
    }

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        # Skip if already responded (e.g. validateSessionAuth auto-responded)
        if flowfile.get_attribute("http.response.sent") == "true":
            return [flowfile]

        request_id = flowfile.get_attribute("http.request.id")
        if not request_id:
            raise RuntimeError("Missing http.request.id attribute — cannot send response")

        service_id = self.config.get("service_id", "")
        svc = self.get_service(service_id)
        if not svc:
            raise RuntimeError(f"HTTPListenerService '{service_id}' not found")

        # --- Status code ---
        status_attr = flowfile.get_attribute("http.response.status")
        if status_attr:
            status = int(status_attr)
        else:
            status = int(self.config.get("status_code", 200))

        # --- Headers ---
        # Start with config defaults
        headers: Dict[str, str] = dict(self.config.get("headers", {}))
        # Set Content-Type from config default
        if "Content-Type" not in headers:
            headers["Content-Type"] = self.config.get("content_type", "application/json")
        # Override with per-FlowFile header attributes
        for attr_key, attr_val in flowfile.get_attributes().items():
            if attr_key.startswith("http.response.header."):
                header_name = attr_key[len("http.response.header."):]
                headers[header_name] = attr_val

        # --- Body ---
        body_attr = flowfile.get_attribute("http.response.body")
        if body_attr is not None:
            body = body_attr.encode("utf-8") if isinstance(body_attr, str) else body_attr
        else:
            body = flowfile.get_content()

        # Submit response — streaming or regular
        sse_stream = getattr(flowfile, "_sse_stream", None)
        is_stream = flowfile.get_attribute("http.response.stream") == "true" and sse_stream
        if is_stream:
            success = svc.submit_stream_response(request_id, status, headers, sse_stream)
        else:
            success = svc.submit_response(request_id, status, headers, body)
        logger.info("[handleHTTPResponse] %s status=%d (req_id=%s, body=%db, ok=%s)",
                    "stream" if is_stream else "submit",
                    status, request_id[:8] if request_id else "?",
                    len(body) if body else 0, success)
        if not success:
            logger.warning(f"Response for request {request_id} failed (timed out or already sent)")

        # Pass FlowFile through (allows chaining, e.g. logging after response)
        flowfile.set_attribute("http.response.sent", "true")
        flowfile.set_attribute("http.response.status.sent", str(status))
        return [flowfile]


from core import TaskFactory
TaskFactory.register(HandleHTTPResponseTask)
