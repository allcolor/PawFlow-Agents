"""P0 spike: sidecar side of the worker-control WebSocket.

Connects to the fake PawFlow server (spike_control_server.py), performs the
hello handshake, streams a couple of realtime.* events, then runs one fake
tool call round-trip and prints its latency. Exit code 0 = round-trip OK.

Run:  python spikes/livekit/spike_worker_control.py [ws_url]
Default url: ws://127.0.0.1:8899/ws/realtime-worker/spike-session
"""

import asyncio
import sys
import time

import aiohttp

from control_protocol import dumps, make_message, parse_message

DEFAULT_URL = "ws://127.0.0.1:8899/ws/realtime-worker/spike-session"


async def recv_typed(ws, expected_type: str, timeout: float = 5.0) -> dict:
    msg = await ws.receive(timeout=timeout)
    if msg.type != aiohttp.WSMsgType.TEXT:
        raise RuntimeError(f"Expected text frame, got {msg.type}")
    message = parse_message(msg.data)
    if message["type"] != expected_type:
        raise RuntimeError(
            f"Expected '{expected_type}', got '{message['type']}'")
    return message


async def run(url: str) -> None:
    async with aiohttp.ClientSession() as http:
        async with http.ws_connect(url) as ws:
            await ws.send_str(dumps(make_message(
                "hello", session_id="spike-session",
                worker_id="spike-worker-1", sdk="livekit-agents")))
            ack = await recv_typed(ws, "hello_ack")
            print(f"handshake OK (session {ack['session_id']})")

            for name, data in [
                ("realtime.session.ready", {}),
                ("realtime.user.transcript.final",
                 {"text": "What time is it?"}),
            ]:
                await ws.send_str(dumps(make_message(
                    "event", name=name, data=data)))

            started = time.perf_counter()
            await ws.send_str(dumps(make_message(
                "tool_call", call_id="call-1", name="get_time",
                arguments={})))
            result = await recv_typed(ws, "tool_result")
            elapsed_ms = (time.perf_counter() - started) * 1000
            if result["call_id"] != "call-1" or not result["ok"]:
                raise RuntimeError(f"Bad tool result: {result}")
            print(f"tool round-trip OK in {elapsed_ms:.1f} ms: {result['result']}")

            await ws.send_str(dumps(make_message("bye", reason="spike done")))


def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    asyncio.run(run(url))
    print("worker-control spike PASSED")


if __name__ == "__main__":
    main()
