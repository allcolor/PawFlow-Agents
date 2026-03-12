"""AgentSSEStream Task — SSE endpoint for streaming agent events.

Subscribes to the ConversationEventBus for a given conversation_id and
streams SSE events to the client.  The client opens this as an EventSource.

Flow pattern:
    httpReceiver (GET /api/agent/events?conversation_id=xxx) → agentSSEStream → handleHTTPResponse

The response is a streaming SSE body (text/event-stream).  The task sets
a special attribute ``http.response.stream`` with the SSE iterator so that
handleHTTPResponse can submit a streaming response.
"""

import json
import logging
from typing import Dict, Any, List

from core import FlowFile, TaskFactory
from core.base_task import BaseTask
from core.conversation_event_bus import ConversationEventBus

logger = logging.getLogger(__name__)


class AgentSSEStreamTask(BaseTask):
    """Stream agent events via Server-Sent Events."""

    TYPE = "agentSSEStream"
    VERSION = "1.0.0"
    NAME = "Agent SSE Stream"
    DESCRIPTION = "Stream agent events to the client via SSE"
    ICON = "stream"

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "timeout": {
                "type": "integer", "required": False, "default": 600,
                "description": "Max SSE connection time in seconds",
            },
        }

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        # Extract conversation_id from query params
        conversation_id = flowfile.get_attribute("http.query.conversation_id") or ""
        if not conversation_id:
            # Try parsing from query string
            query = flowfile.get_attribute("http.query") or ""
            import urllib.parse
            params = dict(urllib.parse.parse_qsl(query))
            conversation_id = params.get("conversation_id", "")

        if not conversation_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            flowfile.set_attribute("http.response.header.Content-Type", "application/json")
            return [flowfile]

        # Subscribe to events
        bus = ConversationEventBus.instance()
        writer = bus.subscribe(conversation_id)

        # Set up SSE streaming response
        timeout = int(self.config.get("timeout", 600))

        def sse_iterator():
            """Yield SSE bytes from the writer."""
            try:
                for chunk in writer.iterate(timeout=2.0):
                    yield chunk
            finally:
                bus.unsubscribe(conversation_id, writer)

        # Store the stream iterator for handleHTTPResponse to pick up
        flowfile.set_attribute("http.response.status", "200")
        flowfile.set_attribute("http.response.header.Content-Type", "text/event-stream")
        flowfile.set_attribute("http.response.header.Cache-Control", "no-cache")
        flowfile.set_attribute("http.response.header.Connection", "keep-alive")
        flowfile.set_attribute("http.response.header.X-Accel-Buffering", "no")
        flowfile.set_attribute("http.response.stream", "true")

        # Store the actual iterator on the flowfile (non-serializable, for in-process use)
        flowfile._sse_stream = sse_iterator()

        return [flowfile]


TaskFactory.register(AgentSSEStreamTask)
