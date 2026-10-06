"""Stdlib-only WebSocket frame helpers for relay clients.

All client frames are masked per RFC 6455. Server frames MAY be unmasked.
Used by pawflow_relay/worker.py + tools/pawflow_relay.py launcher + mcp_bridge.py.
"""

import os
import socket
import struct

#: Bytes handed to one ``sock.send`` call. The socket timeout bounds each
#: call, so a frame fails only when the link makes no progress for that long,
#: never because a large frame takes longer than the timeout to upload.
_SEND_PIECE_BYTES = 64 * 1024


def ws_send(sock, data, opcode=0x01, on_progress=None):
    """Send a single masked WS frame. `data` must be bytes.

    ``on_progress`` is called after every piece the socket accepts, so a
    caller can count an upload in progress as link activity.

    ``sock.sendall`` was not usable here: the socket timeout bounded the
    WHOLE frame (the total ``sendall`` duration on a plain socket, one
    ``SSL_write`` of the whole buffer on TLS), so on a slow uplink a
    multi-megabyte result frame raised ``socket.timeout`` after a part of it
    was already on the wire ("The write operation timed out").

    A failed send shuts the socket down: the server is left waiting for the
    rest of a frame, and TLS refuses every later write that is not a retry of
    the failed one (``[SSL: BAD_LENGTH] bad length``). Each later result
    failed that way until the server closed the link; now the relay
    reconnects at once and its ledger serves the retried requests.
    """
    if isinstance(data, str):
        data = data.encode("utf-8")
    mask = os.urandom(4)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    frame = bytes([0x80 | opcode])
    length = len(data)
    if length < 126:
        frame += bytes([0x80 | length])
    elif length < 65536:
        frame += bytes([0x80 | 126]) + struct.pack("!H", length)
    else:
        frame += bytes([0x80 | 127]) + struct.pack("!Q", length)
    frame += mask + masked
    view = memoryview(frame)
    try:
        while view:
            sent = sock.send(view[:_SEND_PIECE_BYTES])
            view = view[sent:]
            if on_progress is not None:
                on_progress()
    except OSError:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        raise


def ws_recv(sock):
    """Receive one frame. Returns (opcode, payload_bytes).

    Handles both masked (client-to-server) and unmasked (server-to-client)
    frames. Raises ConnectionError if the socket closes mid-frame.
    """
    def _recv_exact(n):
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("WS connection closed")
            buf += chunk
        return buf

    hdr = _recv_exact(2)
    opcode = hdr[0] & 0x0F
    masked = bool(hdr[1] & 0x80)
    length = hdr[1] & 0x7F
    if length == 126:
        length = struct.unpack("!H", _recv_exact(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", _recv_exact(8))[0]
    if masked:
        mask = _recv_exact(4)
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(_recv_exact(length)))
    else:
        payload = _recv_exact(length)
    return opcode, payload
