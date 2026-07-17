"""P0 spike shim — the protocol was promoted to production in P1.

Canonical module: services/_realtime_worker_protocol.py. This file keeps
the spike scripts working unchanged and hosts the fake-PawFlow stub used
by the spike server and the CI tests.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services._realtime_worker_protocol import (  # noqa: E402,F401
    MESSAGE_FIELDS, WORKER_TO_PAWFLOW, dumps, make_message, parse_message,
)


class PawFlowControlStub:
    """PawFlow-side message handler for the spike server and unit tests.

    Fakes the production endpoint behavior: acks the hello, answers tool
    calls with a canned fake-tool result, records events, and ends on bye.
    ``reply(msg)`` returns the messages to send back to the worker.
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
