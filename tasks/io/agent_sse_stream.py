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
import time
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
        # Extract conversation_id + replay flag from query params
        conversation_id = flowfile.get_attribute("http.query.conversation_id") or ""
        replay_param = flowfile.get_attribute("http.query.replay")
        client_id = flowfile.get_attribute("http.query.client_id") or ""
        if not conversation_id or replay_param is None or not client_id:
            query = flowfile.get_attribute("http.query") or ""
            import urllib.parse
            params = dict(urllib.parse.parse_qsl(query))
            if not conversation_id:
                conversation_id = params.get("conversation_id", "")
            if replay_param is None:
                replay_param = params.get("replay")
            if not client_id:
                client_id = params.get("client_id", "")

        if not conversation_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            flowfile.set_attribute("http.response.header.Content-Type", "application/json")
            return [flowfile]

        # replay defaults to True for backward compatibility (initial page
        # load, auto-reconnect). Explicit reload/switch in the UI passes
        # ?replay=false so buffered events are discarded, not replayed.
        replay = True
        if isinstance(replay_param, str) and replay_param.lower() in ("0", "false", "no"):
            replay = False

        # Subscribe to events
        bus = ConversationEventBus.instance()
        writer = bus.subscribe(conversation_id, replay=replay, client_id=client_id)

        # Set up SSE streaming response. The browser reconnects automatically;
        # a finite lifetime prevents half-open sockets from lingering forever.
        timeout = int(self.config.get("timeout", 600) or 600)

        def sse_iterator():
            """Yield SSE bytes from the writer."""
            started = time.monotonic()
            try:
                for chunk in writer.iterate(timeout=15.0):
                    # Deliver the freshly-dequeued chunk BEFORE checking the
                    # lifetime cap. Checking first dropped this chunk on the
                    # floor when an event landed on the same iteration the cap
                    # expired — and because send() already returned True, the
                    # event was never buffered for replay, so the reconnecting
                    # client could not recover it (lost message).
                    yield chunk
                    if timeout > 0 and time.monotonic() - started >= timeout:
                        logger.info("SSE stream lifetime reached for conv=%s client=%s",
                                    conversation_id[:8], client_id[:12])
                        # Flush anything else already queued (a burst at the
                        # lifetime boundary) — same reason: not in replay buffer.
                        for pending in writer.drain_nowait():
                            yield pending
                        yield ("event: sse_reconnect\n"
                               f"data: {json.dumps({'reason': 'lifetime', 'ts': time.time()})}\n\n").encode("utf-8")
                        writer.close()
                        break
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
