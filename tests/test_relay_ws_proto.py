"""Tests for pawflow_relay._relay_ws_proto (shared WS framing).

Forge frames across every length class (7-bit, 16-bit, 64-bit), masked and
unmasked, control opcodes, fragmented recv, and EOF — and check read_ws_frame
against an independent reference decode, plus an encode/decode roundtrip.
"""
import struct

from pawflow_relay._relay_ws_proto import (
    WSFrame, encode_masked_frame, read_ws_frame,
)


class _ChunkSock:
    """Socket stub whose recv hands back data in fixed-size slices.

    chunk=None means "give everything asked for"; chunk=1 forces the most
    fragmented path (one byte per recv).
    """

    def __init__(self, data, chunk=None):
        self._data = data
        self._chunk = chunk

    def recv(self, n, _flags=0):
        if not self._data:
            return b""
        take = n if self._chunk is None else min(n, self._chunk)
        out = self._data[:take]
        self._data = self._data[take:]
        return out


def _build_frame(op, payload, masked, mask=b"\x00\x00\x00\x00", fin=True):
    """Reference WS frame builder (independent of the module under test)."""
    b0 = (0x80 if fin else 0) | op
    n = len(payload)
    out = bytes([b0])
    mbit = 0x80 if masked else 0
    if n < 126:
        out += bytes([mbit | n])
    elif n < 65536:
        out += bytes([mbit | 126]) + struct.pack("!H", n)
    else:
        out += bytes([mbit | 127]) + struct.pack("!Q", n)
    if masked:
        out += mask
        out += bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    else:
        out += payload
    return out


def test_read_unmasked_small():
    wire = _build_frame(0x02, b"hello", masked=False)
    f = read_ws_frame(_ChunkSock(wire))
    assert isinstance(f, WSFrame)
    assert f.op == 0x02 and f.fin is True
    assert f.payload == b"hello"
    assert f.raw == wire


def test_read_16bit_length():
    payload = bytes(range(256)) * 2  # 512 bytes -> 126 path
    wire = _build_frame(0x02, payload, masked=False)
    f = read_ws_frame(_ChunkSock(wire))
    assert f.payload == payload
    assert f.raw == wire


def test_read_64bit_length():
    payload = b"x" * 70000  # >65535 -> 127 path
    wire = _build_frame(0x02, payload, masked=False)
    f = read_ws_frame(_ChunkSock(wire))
    assert f.payload == payload
    assert len(f.raw) == len(wire)


def test_read_masked_unmasks_payload():
    mask = b"\x01\x02\x03\x04"
    wire = _build_frame(0x01, b"masked-data!", masked=True, mask=mask)
    f = read_ws_frame(_ChunkSock(wire))
    assert f.payload == b"masked-data!"   # unmasked
    assert f.raw == wire                  # raw keeps the masked bytes


def test_read_fragmented_recv_one_byte_at_a_time():
    mask = b"\x10\x20\x30\x40"
    payload = bytes(range(200))           # 126 path, masked
    wire = _build_frame(0x02, payload, masked=True, mask=mask)
    f = read_ws_frame(_ChunkSock(wire, chunk=1))
    assert f.payload == payload
    assert f.raw == wire


def test_read_control_opcodes():
    for op in (0x08, 0x09, 0x0A):
        wire = _build_frame(op, b"", masked=False)
        f = read_ws_frame(_ChunkSock(wire))
        assert f.op == op and f.payload == b""


def test_read_eof_in_header_returns_none():
    assert read_ws_frame(_ChunkSock(b"")) is None
    assert read_ws_frame(_ChunkSock(b"\x82")) is None  # only 1 of 2 header bytes


def test_encode_masked_frame_structure():
    frame = encode_masked_frame(0x01, b"hi")
    assert frame[0] == 0x81             # fin + text
    assert frame[1] == (0x80 | 2)       # mask bit + len
    assert frame[2:6] == b"\x00\x00\x00\x00"
    assert frame[6:] == b"hi"


def test_encode_masked_frame_16bit():
    payload = b"y" * 300
    frame = encode_masked_frame(0x02, payload)
    assert frame[1] == (0x80 | 126)
    assert frame[2:4] == struct.pack("!H", 300)


def test_encode_masked_frame_64bit():
    payload = b"z" * 70000
    frame = encode_masked_frame(0x02, payload)
    assert frame[1] == (0x80 | 127)
    assert frame[2:10] == struct.pack("!Q", 70000)


def test_encode_then_read_roundtrip():
    payload = b"roundtrip payload" * 50
    wire = encode_masked_frame(0x02, payload)
    f = read_ws_frame(_ChunkSock(wire))
    assert f.op == 0x02
    assert f.payload == payload         # zero-mask -> unmask is identity
