"""P0 spike: fake PawFlow worker-control WebSocket server.

Stands in for the P1 `/ws/realtime-worker/{session_id}` endpoint. Speaks the
prototype protocol from control_protocol.py: acks hello, answers tool calls
with fake tools, logs events.

Run:  python spikes/livekit/spike_control_server.py [port]
Then: python spikes/livekit/spike_worker_control.py [url]
"""

import logging
import sys

from aiohttp import web, WSMsgType

from control_protocol import PawFlowControlStub, dumps, parse_message

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("spike-control-server")


async def worker_control(request: web.Request) -> web.WebSocketResponse:
    session_id = request.match_info["session_id"]
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    log.info("worker connected for session %s", session_id)
    stub = PawFlowControlStub()
    async for msg in ws:
        if msg.type != WSMsgType.TEXT:
            continue
        try:
            message = parse_message(msg.data)
            replies = stub.reply(message)
        except ValueError as e:
            log.warning("bad message: %s", e)
            continue
        log.info("<- %s", message["type"])
        for reply in replies:
            log.info("-> %s", reply["type"])
            await ws.send_str(dumps(reply))
        if message["type"] == "bye":
            break
    await ws.close()
    log.info("session %s closed (reason=%s, %d events)",
             session_id, stub.closed_reason, len(stub.events))
    return ws


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
    app = web.Application()
    app.router.add_get("/ws/realtime-worker/{session_id}", worker_control)
    log.info("listening on ws://127.0.0.1:%d/ws/realtime-worker/{session_id}", port)
    web.run_app(app, host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
