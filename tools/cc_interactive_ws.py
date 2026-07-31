"""WebSocket reading for the CC interactive proxy (RFC 6455 + RFC 7692).

Codex 0.146 stopped POSTing a Responses body: it opens
``GET /backend-api/codex/responses`` with ``Upgrade: websocket`` (announced by
``openai-beta: responses_websockets=...``) and exchanges the very same Responses
events as compressed WebSocket messages. The proxy keeps forwarding every byte
untouched -- this module only reads the copy the observers already receive, so
nothing here can affect what the CLI sees.

The two directions of one connection are decoded by different threads but share
the handshake result, hence ``WebSocketExchange``: the client half cannot know
whether ``permessage-deflate`` was accepted until the server's 101 is parsed.
"""

from __future__ import annotations

import threading
import zlib

_CONTROL_OPCODES = frozenset({0x8, 0x9, 0xA})
_CONTINUATION_OPCODE = 0x0
# Always inflate with the largest window. A peer that compressed with a smaller
# one produced a stream a 15-bit window still decodes, while trusting its
# advertised value would break the moment the advertisement is wrong.
_WINDOW_BITS = 15
# One message carries the whole turn input, so the ceiling is generous; it only
# exists so a desynchronised stream cannot grow without bound.
MAX_MESSAGE_BYTES = 64 * 1024 * 1024
# Bytes the client half may hold while the 101 has not been parsed yet. In
# practice this stays empty -- a client does not send frames before the
# handshake completes -- so anything large here means the stream desynced.
MAX_PENDING_BYTES = 8 * 1024 * 1024


class UnsupportedWebSocketExtension(Exception):
    """A negotiated extension whose framing this module cannot undo."""


def parse_negotiated_extensions(value: str) -> dict:
    """Read a server ``Sec-WebSocket-Extensions`` header.

    Returns the ``permessage-deflate`` parameters with RFC 7692 defaults filled
    in, or ``enabled: False`` when the server negotiated nothing. Any other
    extension raises: it would change the payload in a way this module does not
    model, and a silent wrong decode is worse than a loud failure.
    """
    params = {
        "enabled": False,
        "server_no_context_takeover": False,
        "client_no_context_takeover": False,
    }
    for offer in (value or "").split(","):
        offer = offer.strip()
        if not offer:
            continue
        parts = [part.strip() for part in offer.split(";")]
        name = parts[0].lower()
        if name != "permessage-deflate":
            raise UnsupportedWebSocketExtension(name)
        params["enabled"] = True
        for part in parts[1:]:
            key = part.partition("=")[0].strip().lower()
            if key in ("server_no_context_takeover",
                       "client_no_context_takeover"):
                params[key] = True
    return params


class _Inflater:
    """Per-direction permessage-deflate reader.

    With context takeover -- what ChatGPT negotiates -- the compressor keeps its
    window across messages, so the reader must keep one decompressor for the
    whole connection and feed it the sync trailer RFC 7692 strips from every
    message.
    """

    def __init__(self, no_context_takeover: bool = False):
        self.no_context_takeover = no_context_takeover
        self._obj = None

    def decompress(self, payload: bytes) -> bytes:
        if self._obj is None or self.no_context_takeover:
            self._obj = zlib.decompressobj(-_WINDOW_BITS)
        return self._obj.decompress(payload + b"\x00\x00\xff\xff")


class WebSocketMessageDecoder:
    """Reassemble application messages from one direction of a WS stream."""

    def __init__(self, inflater=None):
        self.inflater = inflater
        self.buf = b""
        self._fragments = b""
        self._compressed = False
        self._started = False

    def feed(self, data: bytes) -> list:
        """Return every message completed by ``data`` (possibly none)."""
        self.buf += data
        messages = []
        while True:
            frame = self._next_frame()
            if frame is None:
                return messages
            fin, rsv1, opcode, payload = frame
            # Control frames may sit between the fragments of a message, so
            # they must not touch the fragment buffer.
            if opcode in _CONTROL_OPCODES:
                continue
            if opcode == _CONTINUATION_OPCODE:
                if not self._started:
                    continue
                self._fragments += payload
            else:
                # RSV1 is set on the first frame of a message only.
                self._fragments = payload
                self._compressed = bool(rsv1)
                self._started = True
            if len(self._fragments) > MAX_MESSAGE_BYTES:
                self._reset()
                continue
            if not fin:
                continue
            message, compressed = self._fragments, self._compressed
            self._reset()
            if compressed:
                if self.inflater is None:
                    continue
                message = self.inflater.decompress(message)
            messages.append(message)

    def _reset(self) -> None:
        self._fragments = b""
        self._compressed = False
        self._started = False

    def _next_frame(self):
        buf = self.buf
        if len(buf) < 2:
            return None
        first, second = buf[0], buf[1]
        length = second & 0x7F
        offset = 2
        if length == 126:
            if len(buf) < offset + 2:
                return None
            length = int.from_bytes(buf[offset:offset + 2], "big")
            offset += 2
        elif length == 127:
            if len(buf) < offset + 8:
                return None
            length = int.from_bytes(buf[offset:offset + 8], "big")
            offset += 8
        key = b""
        if second & 0x80:
            if len(buf) < offset + 4:
                return None
            key = buf[offset:offset + 4]
            offset += 4
        if len(buf) < offset + length:
            return None
        payload = buf[offset:offset + length]
        self.buf = buf[offset + length:]
        if key:
            unmasked = bytearray(payload)
            for index in range(len(unmasked)):
                unmasked[index] ^= key[index % 4]
            payload = bytes(unmasked)
        return first >> 7, (first >> 6) & 1, first & 0x0F, payload


class WebSocketExchange:
    """Handshake state shared by both directions of one connection.

    The client half starts reading frames as soon as it has seen the upgrade
    request, but it cannot build its decoder until the server's 101 says
    whether messages are deflated. It buffers until ``ready`` is set.
    """

    def __init__(self, request_id: str, path: str):
        self.request_id = request_id
        self.path = path
        self.ready = threading.Event()
        self.params = {}
        self.error = ""
        # Called once the handshake is settled. The buffered client half is
        # otherwise only flushed by its next inbound byte, and a turn whose
        # client sends a single message would hold it until the connection
        # closed.
        self.on_ready = None

    def accept(self, extensions_header: str) -> None:
        try:
            self.params = parse_negotiated_extensions(extensions_header)
        except UnsupportedWebSocketExtension as exc:
            self.params = {}
            self.error = f"unsupported websocket extension: {exc}"
        self._settle()

    def refuse(self, reason: str) -> None:
        self.params = {}
        self.error = reason
        self._settle()

    def _settle(self) -> None:
        self.ready.set()
        if self.on_ready is not None:
            self.on_ready()

    def inflater(self, *, peer: str):
        """Reader for messages sent by ``peer`` (``client`` or ``server``)."""
        if not self.params.get("enabled"):
            return None
        return _Inflater(bool(self.params.get(f"{peer}_no_context_takeover")))


def is_websocket_upgrade(headers) -> bool:
    """True when a parsed header list asks for (or grants) a WS upgrade."""
    return any(key.lower() == "upgrade" and "websocket" in value.lower()
               for key, value in headers)


def negotiated_extensions(headers) -> str:
    return "\n".join(value for key, value in headers
                     if key.lower() == "sec-websocket-extensions")
