"""Stdlib-only WebSocket frame helpers for relay clients.

All client frames are masked per RFC 6455. Server frames MAY be unmasked.
Used by pawflow_relay.py, pawflow_executor_relay.py, mcp_bridge.py.
"""

import os
import struct


def ws_send(sock, data, opcode=0x01):
    """Send a single masked WS frame. `data` must be bytes."""
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
    sock.sendall(frame)


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
