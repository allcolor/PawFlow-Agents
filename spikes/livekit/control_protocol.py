"""Worker-control WebSocket protocol — P0 spike version.

Message schema prototype for the sidecar->PawFlow control channel described
in docs/REALTIME_MULTIMODAL_LIVEKIT_PLAN.md (`/ws/realtime-worker/{session_id}`).
Dependency-free on purpose: the schema logic is unit-tested in CI
(tests/test_livekit_spike_control.py) without LiveKit or network access,
and the P1 production endpoint will start from this contract.

Every message carries a UUID ``id`` and creation ``ts`` (project convention).

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


class PawFlowControlStub:
    """PawFlow-side message handler for the spike server and unit tests.

    Fakes the P1 server behavior: acks the hello, answers tool calls with a
    canned fake-tool result, records events, and ends on bye. ``reply(msg)``
    returns the messages to send back to the worker (possibly empty).
    """

    FAKE_TOOLS = {
        "get_time": lambda arguments: {"now": time.strftime("%Y-%m-%d %H:%M:%S")},
        "echo": lambda arguments: dict(arguments),
    }

    def __init__(self):
        self.session_id = None
        self.events = []
        self.closed_reason = None

    def reply(self, message: dict) -> list:
        msg_type = message["type"]
        if msg_type == "hello":
            self.session_id = message["session_id"]
            return [make_message("hello_ack", session_id=self.session_id)]
        if msg_type == "event":
            self.events.append((message["name"], message["data"]))
            return []
        if msg_type == "tool_call":
            tool = self.FAKE_TOOLS.get(message["name"])
            if tool is None:
                return [make_message(
                    "tool_result", call_id=message["call_id"], ok=False,
                    result={"error": f"Unknown fake tool: {message['name']}"})]
            return [make_message(
                "tool_result", call_id=message["call_id"], ok=True,
                result=tool(message["arguments"]))]
        if msg_type == "bye":
            self.closed_reason = message["reason"]
            return []
        # hello_ack/tool_result/context/shutdown are PawFlow->worker only
        raise ValueError(
            f"Unexpected worker->PawFlow message type: {msg_type}")
