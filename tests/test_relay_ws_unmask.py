"""Relay WebSocket frame unmasking matches the RFC 6455 per-byte XOR."""

import asyncio
import os
import struct

import pytest

from services._relay_ws import _ws_recv_frame, _ws_unmask


def _reference(data, mask):
    return bytes(b ^ mask[i % 4] for i, b in enumerate(data))


@pytest.mark.parametrize("size", [0, 1, 3, 4, 5, 125, 126, 4096, 70001])
def test_unmask_matches_reference(size):
    data = os.urandom(size)
    mask = os.urandom(4)
    assert _ws_unmask(data, mask) == _reference(data, mask)


def test_unmask_keeps_leading_zero_bytes():
    mask = b"\x01\x02\x03\x04"
    data = _reference(b"\x00\x00\x00ab", mask)
    assert _ws_unmask(data, mask) == b"\x00\x00\x00ab"


def test_recv_frame_unmasks_extended_length_payload():
    payload = os.urandom(70001)
    mask = os.urandom(4)
    frame = (bytes([0x82, 0x80 | 127]) + struct.pack("!Q", len(payload))
             + mask + _reference(payload, mask))

    async def run():
        reader = asyncio.StreamReader()
        reader.feed_data(frame)
        reader.feed_eof()
        return await _ws_recv_frame(reader)

    opcode, got = asyncio.run(run())
    assert opcode == 0x2
    assert got == payload
