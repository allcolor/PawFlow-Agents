"""Shared WebSocket framing for the relay worker's backend tunnels.

The code-server and desktop/VNC tunnels both hand-roll the same WS framing:
read a frame header, resolve the 7/16/64-bit length, read+unmask the payload
(reader side) and build a masked client->server frame (sender side). This
module holds those two primitives so the fiddly binary lives in one place.

The primitives are policy-free on purpose — each tunnel keeps its own
behaviour on top of them (the code-server reader forwards the raw on-wire
frame and forwards close frames; the desktop reader answers pings locally,
breaks on close before forwarding, and sends only the unmasked payload). The
byte-level read/encode is identical to the previous inline versions.
"""
import struct
from dataclasses import dataclass


@dataclass
class WSFrame:
    """One decoded WS frame.

    fin/op come from the first header byte (op is the low nibble, matching the
    inline `_hdr2[0] & 0x0F`). payload is the UNMASKED application payload.
    raw is the exact on-wire byte sequence (header + extended length + mask +
    masked payload) — the code-server tunnel forwards it verbatim.
    """
    fin: bool
    op: int
    payload: bytes
    raw: bytes


def _recv_exactly(sock, n):
    """Read exactly n bytes, or return None if the peer closes first."""
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def read_ws_frame(sock):
    """Read one WS frame from a blocking socket.

    Returns a WSFrame, or None when the socket closes before/within the
    header (the reader loop treats None as end-of-stream). A truncated
    payload (EOF mid-body) is returned as-is, mirroring the inline readers
    which forwarded whatever bytes arrived.
    """
    hdr = _recv_exactly(sock, 2)
    if hdr is None:
        return None
    fin = bool(hdr[0] & 0x80)
    op = hdr[0] & 0x0F
    masked = bool(hdr[1] & 0x80)
    plen = hdr[1] & 0x7F
    parts = [hdr]
    if plen == 126:
        lb = _recv_exactly(sock, 2)
        if lb is None:
            return None
        parts.append(lb)
        plen = struct.unpack("!H", lb)[0]
    elif plen == 127:
        lb = _recv_exactly(sock, 8)
        if lb is None:
            return None
        parts.append(lb)
        plen = struct.unpack("!Q", lb)[0]
    mask = b""
    if masked:
        mask = _recv_exactly(sock, 4)
        if mask is None:
            return None
        parts.append(mask)
    payload = b""
    while len(payload) < plen:
        chunk = sock.recv(min(65536, plen - len(payload)))
        if not chunk:
            break
        payload += chunk
    parts.append(payload)
    raw = b"".join(parts)
    if masked:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return WSFrame(fin=fin, op=op, payload=payload, raw=raw)


def encode_masked_frame(op, payload):
    """Build a masked client->server WS frame (zero mask, like the inline code)."""
    frame = bytes([0x80 | op])
    n = len(payload)
    if n < 126:
        frame += bytes([0x80 | n])
    elif n < 65536:
        frame += bytes([0x80 | 126]) + struct.pack("!H", n)
    else:
        frame += bytes([0x80 | 127]) + struct.pack("!Q", n)
    frame += b"\x00\x00\x00\x00" + payload
    return frame
