"""Worker-control WebSocket protocol — sidecar worker <-> PawFlow.

Wire contract of `/ws/realtime-worker/{session_id}` (P1 of
docs/REALTIME_MULTIMODAL_LIVEKIT_PLAN.md), promoted from the P0 spike
(spikes/livekit/control_protocol.py re-exports this module). JSON text
frames; every message carries a UUID ``id`` and creation ``ts`` (project
convention).

Worker -> PawFlow types:
  hello      {session_id, worker_id, sdk}
  event      {name, data}          name uses the realtime.* namespace
  tool_call  {call_id, name, arguments}
  bye        {reason}

PawFlow -> worker types:
  hello_ack   {session_id}
  tool_result {call_id, ok, result}
  context     {text}
  shutdown    {reason}
"""

import json
import time
import uuid

# type -> required payload fields
MESSAGE_FIELDS = {
    "hello": ("session_id", "worker_id", "sdk"),
    "event": ("name", "data"),
    "tool_call": ("call_id", "name", "arguments"),
    "bye": ("reason",),
    "hello_ack": ("session_id",),
    "tool_result": ("call_id", "ok", "result"),
    "context": ("text",),
    "shutdown": ("reason",),
}

# Messages PawFlow accepts from a worker (server side of the endpoint).
WORKER_TO_PAWFLOW = ("hello", "event", "tool_call", "bye")


def make_message(msg_type: str, **payload) -> dict:
    """Build a message dict with UUID + timestamp; validates required fields."""
    fields = MESSAGE_FIELDS.get(msg_type)
    if fields is None:
        raise ValueError(f"Unknown worker-control message type: {msg_type}")
    missing = [f for f in fields if f not in payload]
    if missing:
        raise ValueError(
            f"Message '{msg_type}' missing required fields: {', '.join(missing)}")
    return {
        "id": str(uuid.uuid4()),
        "ts": time.time(),
        "type": msg_type,
        **payload,
    }


def dumps(message: dict) -> str:
    return json.dumps(message, ensure_ascii=False)


def parse_message(raw: str) -> dict:
    """Parse and validate one wire message. Raises ValueError on bad input."""
    try:
        message = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Worker-control message is not valid JSON: {e}")
    if not isinstance(message, dict):
        raise ValueError("Worker-control message must be a JSON object")
    msg_type = message.get("type")
    fields = MESSAGE_FIELDS.get(msg_type)
    if fields is None:
        raise ValueError(f"Unknown worker-control message type: {msg_type}")
    for key in ("id", "ts"):
        if key not in message:
            raise ValueError(f"Message '{msg_type}' missing '{key}'")
    missing = [f for f in fields if f not in message]
    if missing:
        raise ValueError(
            f"Message '{msg_type}' missing required fields: {', '.join(missing)}")
    return message
