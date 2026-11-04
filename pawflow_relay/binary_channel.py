"""Raw byte streams on the relay WebSocket, as binary frames.

The relay link carries its control messages as JSON text frames. Byte streams
(desktop/VNC/voxl WebSockets, desktop audio, code-server WebSockets, HTTP
response bodies) used to ride the same JSON frames as base64 strings: every
packet grew by a third and was encoded, JSON-serialized, parsed and decoded
again on the way, under the server's GIL. They now travel as WebSocket binary
frames (opcode 0x02) holding the bytes themselves, behind a small header:

    byte 0      channel (CHANNEL_* below)
    byte 1      inner WebSocket opcode for WS tunnels, 0 otherwise
    byte 2      length N of the stream id (1..255)
    N bytes     stream id, ASCII (WS session id, audio session id, request id)
    rest        payload, untouched

Ordering: a stream's binary frames and its JSON control messages (open,
close, HTTP start/end) go out on the same socket from the same sender, so
they arrive in the order they were sent.

JSON messages that hold bytes (file contents, screenshots, FUSE reads and
writes, request bodies) use the same frames as attachments: each ``bytes``
value travels as one CHANNEL_ATTACHMENT frame, sent right before the JSON
message, and the JSON keeps a ``{"$pawflow_bin": <index>}`` reference plus a
top-level ``"$pawflow_att": <key>``. See ``pack_message`` and
``AttachmentAssembler``.

Stdlib only: imported by the relay worker and by the PawFlow server.
"""

import base64
import json
import os
import threading

CHANNEL_DESKTOP_WS = 1
"""Desktop viewer WebSocket (noVNC websockify or voxl): one WS message payload,
its opcode in byte 1. Both directions."""

CHANNEL_DESKTOP_AUDIO = 2
"""One Opus packet of desktop audio. Relay to server."""

CHANNEL_CODE_SERVER_WS = 3
"""One complete on-wire WebSocket frame of a code-server tunnel, forwarded as
is (fragmentation and masking preserved). Both directions."""

CHANNEL_HTTP_CHUNK = 4
"""One body chunk of a streamed ``http_proxy``/``http_fetch`` response; the
stream id is the command's request id. Relay to server."""

CHANNEL_ATTACHMENT = 5
"""One ``bytes`` value of the JSON message that follows; the stream id is
``<message key>.<index>``. Both directions."""

CHANNELS = frozenset({
    CHANNEL_DESKTOP_WS, CHANNEL_DESKTOP_AUDIO,
    CHANNEL_CODE_SERVER_WS, CHANNEL_HTTP_CHUNK, CHANNEL_ATTACHMENT,
})

_BIN_REF = "$pawflow_bin"
_ATT_KEY = "$pawflow_att"
_B64_REF = "$pawflow_b64"
_BYTES_TYPES = (bytes, bytearray, memoryview)


def encode(channel: int, stream_id: str, payload, opcode: int = 0) -> bytes:
    """Build the body of one binary relay frame."""
    if channel not in CHANNELS:
        raise ValueError(f"Unknown relay binary channel: {channel}")
    sid = str(stream_id or "").encode("ascii")
    if not 1 <= len(sid) <= 255:
        raise ValueError("Relay binary stream id must be 1..255 ASCII bytes")
    if not 0 <= int(opcode) <= 0x0F:
        raise ValueError(f"Invalid inner WebSocket opcode: {opcode}")
    return b"".join((bytes((channel, int(opcode), len(sid))), sid,
                     bytes(payload or b"")))


def decode(frame):
    """Split a binary relay frame into (channel, opcode, stream_id, payload).

    Raises ValueError on a malformed or unknown frame.
    """
    if len(frame) < 4:
        raise ValueError("Relay binary frame too short")
    channel, opcode, sid_len = frame[0], frame[1], frame[2]
    if channel not in CHANNELS:
        raise ValueError(f"Unknown relay binary channel: {channel}")
    if opcode > 0x0F:
        raise ValueError(f"Invalid inner WebSocket opcode: {opcode}")
    end = 3 + sid_len
    if sid_len == 0 or len(frame) < end:
        raise ValueError("Relay binary frame has a truncated stream id")
    try:
        stream_id = bytes(frame[3:end]).decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("Relay binary stream id is not ASCII") from exc
    return channel, opcode, stream_id, bytes(frame[end:])


# ── Attachments: bytes inside JSON messages ───────────────────────────


def pack_message(msg: dict):
    """Serialize ``msg`` into the frames that carry it, in send order.

    Returns a list of ``(payload, opcode)``: one binary attachment frame per
    ``bytes`` value found anywhere in ``msg``, then the JSON text frame.
    A message without bytes is a single JSON frame, exactly as before. The
    frames of one message must be sent back to back on one socket.
    """
    parts = []

    def _walk(value):
        if isinstance(value, _BYTES_TYPES):
            parts.append(bytes(value))
            return {_BIN_REF: len(parts) - 1}
        if isinstance(value, dict):
            return {k: _walk(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_walk(v) for v in value]
        return value

    body = _walk(msg)
    if not parts:
        return [(json.dumps(msg).encode("utf-8"), 0x01)]
    key = os.urandom(8).hex()
    body[_ATT_KEY] = key
    frames = [(encode(CHANNEL_ATTACHMENT, f"{key}.{index}", part), 0x02)
              for index, part in enumerate(parts)]
    frames.append((json.dumps(body).encode("utf-8"), 0x01))
    return frames


class AttachmentAssembler:
    """Hold attachment frames until their JSON message arrives.

    One per connection: a message's attachments are sent right before it on
    the same socket, so whatever is still held when the connection ends was
    never completed and is dropped with it. ``max_bytes`` bounds what a peer
    can make the receiver hold.
    """

    def __init__(self, max_bytes: int = 512 * 1024 * 1024):
        self._pending = {}
        self._bytes = 0
        self._max = int(max_bytes)
        self._lock = threading.Lock()

    def add(self, stream_id: str, payload: bytes) -> None:
        key, _, index = stream_id.rpartition(".")
        if not key or not index.isdigit():
            raise ValueError(f"Invalid attachment id: {stream_id!r}")
        with self._lock:
            if self._bytes + len(payload) > self._max:
                raise ValueError("Too many pending attachment bytes")
            self._pending.setdefault(key, {})[int(index)] = payload
            self._bytes += len(payload)

    def resolve(self, msg):
        """Put the held bytes back into ``msg``; a no-op without attachments."""
        if not isinstance(msg, dict) or _ATT_KEY not in msg:
            return msg
        key = msg.pop(_ATT_KEY)
        with self._lock:
            parts = self._pending.pop(key, {})
            self._bytes -= sum(len(p) for p in parts.values())

        def _walk(value):
            if isinstance(value, dict):
                if len(value) == 1 and _BIN_REF in value:
                    index = value[_BIN_REF]
                    if index not in parts:
                        raise ValueError(
                            f"Attachment {key}.{index} never arrived")
                    return parts[index]
                return {k: _walk(v) for k, v in value.items()}
            if isinstance(value, list):
                return [_walk(v) for v in value]
            return value

        return _walk(msg)


def json_line_dumps(obj) -> str:
    """JSON for text-only channels (the host helper's line protocol):
    ``bytes`` values become ``{"$pawflow_b64": "..."}``."""
    def _default(value):
        if isinstance(value, _BYTES_TYPES):
            return {_B64_REF: base64.b64encode(bytes(value)).decode("ascii")}
        raise TypeError(
            f"Object of type {type(value).__name__} is not JSON serializable")
    return json.dumps(obj, default=_default)


def json_line_loads(text):
    """Inverse of ``json_line_dumps``: base64 references become bytes."""
    def _hook(value):
        if len(value) == 1 and _B64_REF in value:
            return base64.b64decode(value[_B64_REF])
        return value
    return json.loads(text, object_hook=_hook)
