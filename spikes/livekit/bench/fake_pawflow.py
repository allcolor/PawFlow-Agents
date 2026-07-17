"""Bench tier 1: fake PawFlow control plane for the real sidecar worker.

Serves the bootstrap endpoint + worker-control WS with the REAL wire
protocol module from the repo, records everything, exposes /events for
the driver's assertions. No provider key needed to validate the glue.
"""

import json
import os
import sys
import time

sys.path.insert(0, "/workspace")

from aiohttp import web, WSMsgType

from services._realtime_worker_protocol import (  # noqa: E402
    dumps, make_message, parse_message,
)

SECRET = "benchsecret"
STATE = {"bootstrap_calls": [], "messages": [], "hello": None}


async def bootstrap(request):
    if request.headers.get("X-PawFlow-Worker-Secret") != SECRET:
        return web.json_response({"error": "bad worker secret"}, status=403)
    body = await request.json()
    STATE["bootstrap_calls"].append({"room": body.get("room"),
                                     "ts": time.time()})
    return web.json_response({
        "session_id": "bench-1",
        "room_name": body.get("room", ""),
        "livekit_url": "ws://127.0.0.1:7880",
        "agent_room_token": "",
        "control_token": "bench-control-token",
        "conversation_id": "bench-conv",
        "agent_name": "claude",
        "provider": "openai",
        "model": "gpt-realtime",
        "voice": "alloy",
        "modalities": ["audio", "text"],
        "video_input": False,
        "video_fps_active": 1.0,
        "video_fps_idle": 0.33,
        "local_pipeline": {},
        "turn_detection": "provider_default",
        "max_session_seconds": 60,
        "instructions": "You are the bench agent.",
        "tools": [{"name": "echo", "description": "echo back",
                   "parameters": {"type": "object", "properties": {
                       "text": {"type": "string"}}}}],
        "credentials": {"source": "llm_service", "provider": "openai",
                        "api_key": os.environ.get("OPENAI_API_KEY",
                                                  "sk-invalid-bench"),
                        "base_url": "", "default_model": ""},
    })


async def control_ws(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    token = request.query.get("token", "")
    sid = request.match_info["session_id"]
    STATE["messages"].append(("_ws_open", {"sid": sid, "token": token}))
    async for msg in ws:
        if msg.type != WSMsgType.TEXT:
            continue
        try:
            message = parse_message(msg.data)
        except ValueError as e:
            STATE["messages"].append(("_bad", {"error": str(e)}))
            continue
        STATE["messages"].append((message["type"], {
            k: v for k, v in message.items()
            if k not in ("id", "ts", "type")}))
        if message["type"] == "hello":
            STATE["hello"] = message
            await ws.send_str(dumps(make_message(
                "hello_ack", session_id=message["session_id"])))
        elif message["type"] == "tool_call":
            await ws.send_str(dumps(make_message(
                "tool_result", call_id=message["call_id"], ok=True,
                result={"text": "bench echo: "
                        + json.dumps(message["arguments"])})))
        elif message["type"] == "bye":
            break
    await ws.close()
    STATE["messages"].append(("_ws_closed", {}))
    return ws


async def events(request):
    return web.json_response(STATE)


app = web.Application()
app.router.add_post("/api/realtime/livekit/worker/bootstrap", bootstrap)
app.router.add_get("/ws/realtime-worker/{session_id}", control_ws)
app.router.add_get("/events", events)

if __name__ == "__main__":
    web.run_app(app, host="127.0.0.1", port=8898, print=None)
