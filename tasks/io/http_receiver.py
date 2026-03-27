"""httpReceiver — self-triggering source task for HTTP Listener flows.

This task registers route patterns on a shared HTTPListenerService and
converts incoming HTTP requests into FlowFiles with relationship-based
routing.

Config:
    service_id: str          — ID of the HTTPListenerService in the flow
    routes: list[dict]       — route definitions, each with:
        method: str          — HTTP method (GET, POST, PUT, DELETE, etc.)
        pattern: str         — URL pattern (e.g. /api/users/{id})
        relationship: str    — optional, defaults to "METHOD:/pattern"

The task sets these FlowFile attributes:
    http.request.id       — correlation ID for response
    http.method           — GET, POST, etc.
    http.path             — request path
    http.query            — query string
    http.header.*         — request headers (lowered keys)
    http.path.*           — path parameters from URL pattern
    http.remote.addr      — client IP
    route.relationship    — determines which connection the FF is routed to
"""

import json
import logging
import queue
import threading
from typing import Any, Dict, List, Optional

from core import FlowFile
from core.base_task import BaseTask

logger = logging.getLogger(__name__)


class HTTPReceiverTask(BaseTask):
    """Self-triggering source task that receives HTTP requests."""

    TYPE = "httpReceiver"
    DESCRIPTION = "Receive HTTP requests from a shared HTTP listener service"
    TAGS = ["http", "io", "source", "listener"]

    PARAMETERS = {
        "service_id": {
            "type": "string",
            "description": "ID of the HTTPListenerService",
            "required": True,
        },
        "routes": {
            "type": "array",
            "description": "Route definitions: [{method, pattern, relationship?}]",
            "required": True,
        },
    }

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._queue: queue.Queue = queue.Queue(maxsize=1000)
        self._registered = False
        self._owner_id: Optional[str] = None

    def initialize(self):
        """Register routes on startup (called by executor after services connect)."""
        self._ensure_routes_registered()

    def has_pending_input(self) -> bool:
        """Protocol method: tells the scheduler this task has self-generated input."""
        return not self._queue.empty()

    @property
    def is_persistent_source(self) -> bool:
        return True

    def _ensure_routes_registered(self):
        """Register routes on the HTTP listener service (lazy, once)."""
        if self._registered:
            return

        service_id = self.config.get("service_id", "")
        svc = self.get_service(service_id)
        if not svc:
            raise RuntimeError(f"HTTPListenerService '{service_id}' not found")

        # Ensure service is connected
        svc.ensure_connected()

        routes = self.config.get("routes", [])
        flow_id = self.config.get("_flow_id", id(self))
        self._owner_id = f"httpReceiver_{flow_id}"

        for route_def in routes:
            method = route_def.get("method", "GET").upper()
            pattern = route_def.get("pattern", "/")
            relationship = route_def.get("relationship", f"{method}:{pattern}")

            def make_callback(rel):
                def callback(pending_req):
                    self._on_request(pending_req, rel)
                return callback

            svc.register_route(method, pattern, self._owner_id, make_callback(relationship))
            logger.info(f"httpReceiver registered {method} {pattern} -> {relationship}")

        self._registered = True

    def _on_request(self, pending_req, relationship: str):
        """Called by the HTTP server thread when a request arrives."""
        ff = FlowFile(content=pending_req.body or b"")

        # Core attributes
        ff.set_attribute("http.request.id", pending_req.request_id)
        ff.set_attribute("http.method", pending_req.method)
        ff.set_attribute("http.path", pending_req.path)
        ff.set_attribute("http.query", pending_req.query_string)
        ff.set_attribute("http.remote.addr", pending_req.remote_addr)

        # Headers
        for k, v in pending_req.headers.items():
            ff.set_attribute(f"http.header.{k.lower()}", v)

        # Path parameters
        for k, v in pending_req.path_params.items():
            ff.set_attribute(f"http.path.{k}", v)

        # Relationship routing
        ff.set_attribute("route.relationship", relationship)

        # POST agent: respond immediately with ack (async processing)
        if pending_req.method == "POST" and "/api/agent" in pending_req.path:
            service_id = self.config.get("service_id", "")
            svc = self.get_service(service_id)
            if svc:
                svc.submit_response(
                    pending_req.request_id, 200,
                    {"Content-Type": "application/json"},
                    json.dumps({"status": "accepted"}).encode())

        # Priority: commands and technical actions get high priority
        if pending_req.body:
            try:
                _body = json.loads(pending_req.body)
                if isinstance(_body, dict) and _body.get("action"):
                    ff.set_attribute("priority", "10")
            except (json.JSONDecodeError, TypeError):
                pass

        # Enqueue for pickup by execute()
        try:
            self._queue.put_nowait(ff)
        except queue.Full:
            logger.warning("httpReceiver queue full, rejecting request")
            # Auto-respond 503
            service_id = self.config.get("service_id", "")
            svc = self.get_service(service_id)
            if svc:
                svc.submit_response(
                    pending_req.request_id, 503,
                    {"Content-Type": "application/json"},
                    json.dumps({"error": "Service Unavailable", "message": "Queue full"}).encode(),
                )

    def execute(self, flowfile: Optional[FlowFile] = None) -> List[FlowFile]:
        """Consume one request from the internal queue.

        The scheduler calls this when has_pending_input() is True.
        The `flowfile` argument is ignored (self-triggering).
        """
        self._ensure_routes_registered()

        try:
            ff = self._queue.get_nowait()
            return [ff]
        except queue.Empty:
            return []

    def cleanup(self):
        """Unregister routes when the task/flow stops."""
        if self._registered and self._owner_id:
            service_id = self.config.get("service_id", "")
            svc = self.get_service(service_id)
            if svc:
                svc.unregister_routes(self._owner_id)
            self._registered = False


from core import TaskFactory
TaskFactory.register(HTTPReceiverTask)
