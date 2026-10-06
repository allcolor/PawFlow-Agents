"""Large relay frames on a slow link are neither cut nor desynchronised.

The Ultima7D relay dropped its connection mid-frame many times a day: its
watchdog ignored uploads in progress, its socket timeout bounded a whole
frame send, and the server cancelled a frame body that took longer than its
keepalive to arrive.
"""

import asyncio
import os
import socket
import struct
import threading
import time
from pathlib import Path

import pytest

from pawflow_relay.ws_frame import ws_recv, ws_send
from services._relay_ws import _ws_recv_frame


def _frame(payload, opcode=0x01):
    if len(payload) < 126:
        hdr = bytes([0x80 | opcode, len(payload)])
    elif len(payload) < 65536:
        hdr = bytes([0x80 | opcode, 126]) + struct.pack("!H", len(payload))
    else:
        hdr = bytes([0x80 | opcode, 127]) + struct.pack("!Q", len(payload))
    return hdr + payload


# ── server: services._relay_ws._ws_recv_frame ──────────────────────────

def test_idle_timeout_before_a_frame_consumes_nothing():
    async def run():
        reader = asyncio.StreamReader()
        with pytest.raises(asyncio.TimeoutError):
            await _ws_recv_frame(reader, idle_timeout=0.05)
        reader.feed_data(_frame(b"hello"))
        return await _ws_recv_frame(reader, idle_timeout=0.05)

    assert asyncio.run(run()) == (0x01, b"hello")


def test_slow_frame_body_longer_than_idle_timeout_arrives_intact():
    payload = os.urandom(300_000)
    data = _frame(payload)

    async def run():
        reader = asyncio.StreamReader()

        async def trickle():
            # 0.3 s in total, never more than 0.03 s without a byte.
            for start in range(0, len(data), 30_000):
                reader.feed_data(data[start:start + 30_000])
                await asyncio.sleep(0.03)
            reader.feed_data(_frame(b"next"))

        feeder = asyncio.create_task(trickle())
        first = await _ws_recv_frame(reader, idle_timeout=0.1)
        second = await _ws_recv_frame(reader, idle_timeout=0.1)
        await feeder
        return first, second

    first, second = asyncio.run(run())
    assert first == (0x01, payload)
    # The stream stays aligned on frame boundaries.
    assert second == (0x01, b"next")


def test_stall_inside_a_frame_closes_instead_of_resuming_mid_frame():
    data = _frame(os.urandom(100_000))

    async def run():
        reader = asyncio.StreamReader()
        reader.feed_data(data[:50_000])
        await _ws_recv_frame(reader, idle_timeout=0.05)

    # Not asyncio.TimeoutError: the main loop treats that as "no frame yet"
    # and would read again from the middle of this frame.
    with pytest.raises(ConnectionError, match="stalled"):
        asyncio.run(run())


def test_main_loops_bound_only_the_wait_for_a_frame():
    for path in ("services/_relay_conn.py", "services/tool_relay_service.py"):
        source = Path(path).read_text(encoding="utf-8")
        assert "_ws_recv_frame(reader), timeout=KEEPALIVE" not in source
        assert "_ws_recv_frame(\n" in source
        assert "reader, idle_timeout=KEEPALIVE)" in source


# ── relay: pawflow_relay.ws_frame.ws_send ──────────────────────────────

class _TrickleSocket:
    """Accepts at most ``limit`` bytes per send, like a full send buffer."""

    def __init__(self, limit):
        self.limit = limit
        self.data = bytearray()

    def send(self, view):
        accepted = bytes(view[:self.limit])
        self.data += accepted
        return len(accepted)

    def sendall(self, _data):  # pragma: no cover - must not be used
        raise AssertionError("ws_send must not use sendall")


class _BufferSocket:
    def __init__(self, data):
        self.data = bytes(data)

    def recv(self, n):
        chunk, self.data = self.data[:n], self.data[n:]
        return chunk


def test_ws_send_writes_the_whole_frame_through_partial_sends():
    payload = os.urandom(200_000)
    sock = _TrickleSocket(limit=7_000)
    progress = []

    ws_send(sock, payload, on_progress=lambda: progress.append(1))

    assert ws_recv(_BufferSocket(sock.data)) == (0x01, payload)
    # Every accepted piece is reported, so the watchdog sees the upload.
    assert len(progress) >= len(sock.data) // 7_000


def test_ws_send_longer_than_the_socket_timeout_succeeds_while_it_progresses():
    payload = os.urandom(4 * 1024 * 1024)
    sender, receiver = socket.socketpair()
    sender.settimeout(0.3)
    received = bytearray()

    def slow_reader():
        while True:
            chunk = receiver.recv(256 * 1024)
            if not chunk:
                return
            received.extend(chunk)
            time.sleep(0.05)

    reader = threading.Thread(target=slow_reader)
    reader.start()
    started = time.monotonic()
    try:
        ws_send(sender, payload)
        elapsed = time.monotonic() - started
    finally:
        sender.shutdown(socket.SHUT_WR)
        reader.join(timeout=30)
        sender.close()
        receiver.close()

    # sendall raised socket.timeout here: the frame took longer than the
    # 0.3 s socket timeout although the reader never stopped draining it.
    assert elapsed > 0.3
    assert ws_recv(_BufferSocket(received)) == (0x01, payload)


def test_relay_worker_counts_send_progress_as_watchdog_activity():
    source = Path("pawflow_relay/worker.py").read_text(encoding="utf-8")
    assert "import ws_send as _ws_send_raw" in source
    assert "_last_activity[0] = time.time()" in source.split(
        "def _mark_send_progress", 1)[1].split("def ", 1)[0]
    assert "on_progress=_mark_send_progress" in source
