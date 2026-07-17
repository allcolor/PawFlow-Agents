"""Worker-control WebSocket client — sidecar side.

Speaks the `services/_realtime_worker_protocol.py` contract against
PawFlow's `/ws/realtime-worker/{session_id}` endpoint. LiveKit-free on
purpose: this is the part of the worker that CI can test (aiohttp only).

Usage (inside the worker's asyncio loop):

    client = WorkerControlClient(control_url, session_id, worker_id)
    await client.connect()                 # hello/hello_ack handshake
    await client.send_event("realtime.session.ready", {})
    result = await client.call_tool("read", {"path": "x"})   # round-trip
    ...
    await client.close("session ended")

Incoming `context` and `shutdown` messages fire the registered callbacks.
"""

import asyncio
import json
import logging
import os
import time
import uuid

import aiohttp

logger = logging.getLogger(__name__)

_HANDSHAKE_TIMEOUT_S = 10.0
_TOOL_TIMEOUT_S = 630.0   # bridge hard timeout is 600s; small margin


def _make_message(msg_type: str, **payload) -> dict:
    """Same wire shape as services/_realtime_worker_protocol.make_message.

    Duplicated on purpose: the sidecar container ships without the PawFlow
    server package. tests/test_livekit_worker_client.py pins both sides to
    the same contract.
    """
    return {"id": str(uuid.uuid4()), "ts": time.time(), "type": msg_type,
            **payload}


class WorkerControlClient:
    """One control connection for one realtime session."""

    def __init__(self, url: str, session_id: str, worker_id: str,
                 *, sdk: str = "livekit-agents", on_context=None,
                 on_shutdown=None):
        self._url = url
        self._session_id = session_id
        self._worker_id = worker_id
        self._sdk = sdk
        self.on_context = on_context
        self.on_shutdown = on_shutdown
        self._http = None
        self._ws = None
        self._reader_task = None
        self._pending_tools = {}   # call_id -> Future
        self.closed = asyncio.Event()

    # -- lifecycle -------------------------------------------------------

    async def connect(self) -> None:
        self._http = aiohttp.ClientSession()
        # PAWFLOW_TLS_INSECURE=1: managed stack, loopback wss with the
        # default self-signed install certificate.
        insecure = os.environ.get("PAWFLOW_TLS_INSECURE", "") == "1"
        self._ws = await self._http.ws_connect(
            self._url, ssl=(False if insecure else None))
        await self._send(_make_message(
            "hello", session_id=self._session_id,
            worker_id=self._worker_id, sdk=self._sdk))
        msg = await asyncio.wait_for(self._ws.receive(),
                                     timeout=_HANDSHAKE_TIMEOUT_S)
        if msg.type != aiohttp.WSMsgType.TEXT:
            raise ConnectionError(
                f"worker-control handshake failed: {msg.type}")
        ack = json.loads(msg.data)
        if ack.get("type") == "shutdown":
            raise ConnectionError(
                f"worker-control rejected: {ack.get('reason', '')}")
        if ack.get("type") != "hello_ack" or \
                ack.get("session_id") != self._session_id:
            raise ConnectionError(f"unexpected handshake reply: {ack}")
        self._reader_task = asyncio.create_task(self._reader())

    async def close(self, reason: str = "bye") -> None:
        if self._ws is not None and not self._ws.closed:
            try:
                await self._send(_make_message("bye", reason=reason))
                await self._ws.close()
            except Exception:
                logger.debug("control close failed", exc_info=True)
        if self._reader_task is not None:
            self._reader_task.cancel()
        if self._http is not None:
            await self._http.close()
        self.closed.set()

    # -- outbound ----------------------------------------------------------

    async def _send(self, message: dict) -> None:
        await self._ws.send_str(json.dumps(message, ensure_ascii=False))

    async def send_event(self, name: str, data: dict) -> None:
        await self._send(_make_message("event", name=name, data=data or {}))

    async def call_tool(self, name: str, arguments: dict,
                        timeout: float = _TOOL_TIMEOUT_S) -> dict:
        """Forward a provider tool call to PawFlow; await the result dict."""
        call_id = str(uuid.uuid4())
        future = asyncio.get_running_loop().create_future()
        self._pending_tools[call_id] = future
        try:
            await self._send(_make_message(
                "tool_call", call_id=call_id, name=name,
                arguments=arguments or {}))
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending_tools.pop(call_id, None)

    # -- inbound -----------------------------------------------------------

    async def _reader(self) -> None:
        try:
            async for msg in self._ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                try:
                    message = json.loads(msg.data)
                except json.JSONDecodeError:
                    logger.warning("bad control message (not JSON)")
                    continue
                await self._dispatch(message)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("control reader failed", exc_info=True)
        finally:
            for future in self._pending_tools.values():
                if not future.done():
                    future.set_exception(
                        ConnectionError("worker-control connection closed"))
            self.closed.set()

    async def _dispatch(self, message: dict) -> None:
        msg_type = message.get("type")
        if msg_type == "tool_result":
            future = self._pending_tools.get(message.get("call_id", ""))
            if future is not None and not future.done():
                future.set_result({"ok": bool(message.get("ok")),
                                   "result": message.get("result")})
        elif msg_type == "context":
            if self.on_context is not None:
                await self.on_context(str(message.get("text", "")))
        elif msg_type == "shutdown":
            if self.on_shutdown is not None:
                await self.on_shutdown(str(message.get("reason", "")))
            self.closed.set()
        else:
            logger.warning("unexpected control message type: %s", msg_type)
