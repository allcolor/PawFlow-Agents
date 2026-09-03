"""Message transports used by PawFlow's SDK-backed ACP connections."""

from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import struct
from typing import Any

DEFAULT_ACP_MESSAGE_BYTES = 16 * 1024 * 1024


class AcpWebSocketTransportError(ConnectionError):
    """Raised when an upgraded WebSocket carries an invalid ACP message."""


class RawWebSocketTransport:
    """ACP message transport over an HTTPListener upgraded raw socket.

    The official SDK owns JSON-RPC routing and validation. This adapter owns
    only RFC 6455 text-frame conversion because PawFlow's listener exposes an
    already-upgraded synchronous socket rather than an ASGI WebSocket object.
    """

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: Any,
        *,
        max_message_bytes: int = DEFAULT_ACP_MESSAGE_BYTES,
    ) -> None:
        if max_message_bytes <= 0:
            raise ValueError("max_message_bytes must be positive")
        self._reader = reader
        self._writer = writer
        self._max_message_bytes = max_message_bytes
        self._write_lock = asyncio.Lock()
        self._closed = False
        self._peer_closed = False

    async def send(self, message: dict[str, Any]) -> None:
        if self._closed or self._peer_closed:
            raise ConnectionError("ACP WebSocket transport is closed")
        if not isinstance(message, dict):
            raise TypeError("ACP transport messages must be objects")
        payload = json.dumps(
            message,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(payload) > self._max_message_bytes:
            raise AcpWebSocketTransportError(
                f"ACP message exceeds {self._max_message_bytes} bytes"
            )
        await self._send_frame(0x01, payload)

    async def receive(self) -> dict[str, Any] | None:
        message_opcode: int | None = None
        chunks: list[bytes] = []
        total = 0

        while not self._closed:
            try:
                fin, opcode, payload = await self._read_frame()
            except asyncio.IncompleteReadError:
                self._peer_closed = True
                return None

            if opcode == 0x08:
                self._peer_closed = True
                with contextlib.suppress(Exception):
                    await self._send_frame(0x08, payload)
                return None
            if opcode == 0x09:
                await self._send_frame(0x0A, payload)
                continue
            if opcode == 0x0A:
                continue

            if opcode in {0x01, 0x02}:
                if message_opcode is not None:
                    raise AcpWebSocketTransportError(
                        "new WebSocket message before fragmented message completed"
                    )
                message_opcode = opcode
                chunks = [payload]
                total = len(payload)
            elif opcode == 0x00:
                if message_opcode is None:
                    raise AcpWebSocketTransportError(
                        "unexpected WebSocket continuation frame"
                    )
                chunks.append(payload)
                total += len(payload)
            else:
                raise AcpWebSocketTransportError(
                    f"unsupported WebSocket opcode {opcode}"
                )

            if total > self._max_message_bytes:
                raise AcpWebSocketTransportError(
                    f"ACP message exceeds {self._max_message_bytes} bytes"
                )
            if not fin:
                continue

            complete_opcode = message_opcode
            data = b"".join(chunks)
            message_opcode = None
            chunks = []
            total = 0
            if complete_opcode == 0x02:
                continue

            try:
                decoded = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise AcpWebSocketTransportError(
                    "invalid ACP WebSocket JSON message"
                ) from exc
            if not isinstance(decoded, dict):
                raise AcpWebSocketTransportError(
                    "ACP WebSocket message must be a JSON object"
                )
            return decoded
        return None

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if not self._peer_closed:
            with contextlib.suppress(Exception):
                await self._send_frame(0x08, struct.pack("!H", 1000))
        with contextlib.suppress(Exception):
            self._writer.close()

    async def _read_frame(self) -> tuple[bool, int, bytes]:
        header = await self._reader.readexactly(2)
        fin = bool(header[0] & 0x80)
        if header[0] & 0x70:
            raise AcpWebSocketTransportError(
                "WebSocket extensions are not supported"
            )
        opcode = header[0] & 0x0F
        masked = bool(header[1] & 0x80)
        if not masked:
            raise AcpWebSocketTransportError(
                "client WebSocket frames must be masked"
            )

        length = header[1] & 0x7F
        if length == 126:
            length = struct.unpack("!H", await self._reader.readexactly(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", await self._reader.readexactly(8))[0]
        if opcode >= 0x08 and (not fin or length > 125):
            raise AcpWebSocketTransportError("invalid WebSocket control frame")
        if length > self._max_message_bytes:
            raise AcpWebSocketTransportError(
                f"ACP message exceeds {self._max_message_bytes} bytes"
            )

        mask = await self._reader.readexactly(4)
        encoded = await self._reader.readexactly(length)
        payload = bytes(
            value ^ mask[index % 4]
            for index, value in enumerate(encoded)
        )
        return fin, opcode, payload

    async def _send_frame(self, opcode: int, payload: bytes) -> None:
        async with self._write_lock:
            first = 0x80 | opcode
            length = len(payload)
            if length < 126:
                header = bytes((first, length))
            elif length < 65536:
                header = bytes((first, 126)) + struct.pack("!H", length)
            else:
                header = bytes((first, 127)) + struct.pack("!Q", length)
            self._writer.write(header + payload)
            await self._writer.drain()


class _MemoryTransport:
    def __init__(
        self,
        incoming: asyncio.Queue[dict[str, Any] | None],
        outgoing: asyncio.Queue[dict[str, Any] | None],
    ) -> None:
        self._incoming = incoming
        self._outgoing = outgoing
        self._closed = False

    async def send(self, message: dict[str, Any]) -> None:
        if self._closed:
            raise ConnectionError("ACP memory transport is closed")
        await self._outgoing.put(copy.deepcopy(message))

    async def receive(self) -> dict[str, Any] | None:
        return await self._incoming.get()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._outgoing.put(None)


def memory_transport_pair() -> tuple[_MemoryTransport, _MemoryTransport]:
    """Return linked message transports for deterministic SDK conformance tests."""
    left_to_right: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    right_to_left: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    return (
        _MemoryTransport(right_to_left, left_to_right),
        _MemoryTransport(left_to_right, right_to_left),
    )


def serve_agent_on_websocket(
    sock: Any,
    agent: Any,
    *,
    max_message_bytes: int = DEFAULT_ACP_MESSAGE_BYTES,
) -> None:
    """Run an official SDK Agent on one upgraded PawFlow listener socket."""
    from acp import run_agent
    from services.filesystem_service import _attach_sync_sock_to_loop

    loop = asyncio.new_event_loop()
    transport: RawWebSocketTransport | None = None
    try:
        asyncio.set_event_loop(loop)
        reader, writer = _attach_sync_sock_to_loop(sock, loop)
        transport = RawWebSocketTransport(
            reader,
            writer,
            max_message_bytes=max_message_bytes,
        )
        loop.run_until_complete(run_agent(agent, transport))
    finally:
        if transport is not None:
            with contextlib.suppress(Exception):
                loop.run_until_complete(transport.close())
        with contextlib.suppress(Exception):
            loop.run_until_complete(loop.shutdown_asyncgens())
        asyncio.set_event_loop(None)
        loop.close()
