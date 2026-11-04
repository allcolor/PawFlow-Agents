"""Raw binary stream frames on the relay link (pawflow_relay.binary_channel).

Desktop, audio, code-server and HTTP body bytes used to cross the relay link
as base64 inside JSON. These tests pin the binary path end to end: the frame
format, the relay message loop (server -> relay frames, HTTP chunks out), the
server dispatcher (relay -> server frames) and the fast WebSocket masking.
"""
import json
import os
import socket
import struct
import threading
import time
import types

import pytest

from pawflow_relay import _relay_msg_loop as ml
from pawflow_relay import binary_channel as bc
from pawflow_relay._relay_msg_loop import ConnContext, ConnSession
from pawflow_relay.command_ledger import CommandLedger
from pawflow_relay.ws_frame import ws_recv, ws_send, xor_mask


CLOSE = (0x08, struct.pack("!H", 1000))


def _wait(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


# ── Frame format ──────────────────────────────────────────────────────


@pytest.mark.parametrize("channel", sorted(bc.CHANNELS))
def test_encode_decode_round_trip_keeps_bytes_untouched(channel):
    payload = bytes(range(256)) * 3
    frame = bc.encode(channel, "stream-1", payload, opcode=2)
    assert frame[:3] == bytes((channel, 2, len("stream-1")))
    assert bc.decode(frame) == (channel, 2, "stream-1", payload)


def test_encoded_frame_has_no_base64_overhead():
    payload = os.urandom(100_000)
    frame = bc.encode(bc.CHANNEL_DESKTOP_WS, "ws-1", payload, opcode=2)
    assert len(frame) == 3 + len("ws-1") + len(payload)


@pytest.mark.parametrize("kwargs, message", [
    (dict(channel=99, stream_id="s"), "Unknown"),
    (dict(channel=bc.CHANNEL_DESKTOP_WS, stream_id=""), "stream id"),
    (dict(channel=bc.CHANNEL_DESKTOP_WS, stream_id="x" * 256), "stream id"),
    (dict(channel=bc.CHANNEL_DESKTOP_WS, stream_id="s", opcode=16), "opcode"),
])
def test_encode_rejects_invalid_input(kwargs, message):
    with pytest.raises(ValueError, match=message):
        bc.encode(payload=b"x", **kwargs)


@pytest.mark.parametrize("frame", [
    b"",
    b"\x01\x02",
    bytes((99, 0, 1)) + b"s",          # unknown channel
    bytes((1, 0x20, 1)) + b"s",        # opcode out of range
    bytes((1, 0, 0)) + b"payload",     # empty stream id
    bytes((1, 0, 9)) + b"short",       # truncated stream id
    bytes((1, 0, 2)) + b"\xff\xfe",    # non-ASCII stream id
])
def test_decode_rejects_malformed_frames(frame):
    with pytest.raises(ValueError):
        bc.decode(frame)


# ── Masking ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("size", [0, 1, 3, 4, 5, 125, 65_537])
def test_xor_mask_matches_rfc6455_per_byte_definition(size):
    data = os.urandom(size)
    mask = os.urandom(4)
    expected = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    assert xor_mask(data, mask) == expected
    assert xor_mask(xor_mask(data, mask), mask) == data


def test_ws_send_and_recv_round_trip_large_binary_frame():
    a, b = socket.socketpair()
    payload = os.urandom(300_000)
    try:
        sender = threading.Thread(target=ws_send, args=(a, payload, 0x02))
        sender.start()
        opcode, received = ws_recv(b)
        sender.join(5)
    finally:
        a.close()
        b.close()
    assert opcode == 0x02
    assert received == payload


# ── Relay message loop ───────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _fresh_ledger(monkeypatch):
    monkeypatch.setattr(ml, "LEDGER", CommandLedger())


def _ctx(frames, executed, sends):
    it = iter(frames)
    return ConnContext(
        sock=object(),
        send_lock=threading.Lock(),
        ws_frame_send=lambda _s, f, opcode=0x01: sends.append((f, opcode)),
        ws_frame_recv=lambda _sock: next(it),
        socket_diag={},
        last_activity=[0.0],
        pool=None,
        execute_command=lambda m, on_output=None: (
            executed.append(m) or {"ok": True}),
        term_mgr=types.SimpleNamespace(sessions={}),
        children=types.SimpleNamespace(
            handle_spawn=lambda *a: None, handle_stop=lambda *a: None),
        child_cfg=object(),
        term_send=lambda _f: None,
        fuse_clients=(None, None, None),
        remote_mount_mgr=None,
        resolve_spawn_docker_env=lambda: object(),
    )


def test_relay_routes_server_binary_frames_in_order_without_replies():
    executed, sends = [], []
    close_cmd = json.dumps({
        "type": "command", "action": "desktop_ws_close",
        "request_id": "c1", "session_id": "ws-1"}).encode()
    frames = [
        (0x02, bc.encode(bc.CHANNEL_DESKTOP_WS, "ws-1", b"key-down", 2)),
        (0x02, bc.encode(bc.CHANNEL_DESKTOP_WS, "ws-1", b"key-up", 1)),
        (0x02, bc.encode(bc.CHANNEL_CODE_SERVER_WS, "cs-1", b"\x81\x00")),
        (0x02, b"\x63garbage"),  # malformed: dropped, loop keeps going
        (0x01, close_cmd),
        CLOSE,
    ]
    ConnSession(_ctx(frames, executed, sends)).run()

    assert _wait(lambda: len(executed) == 4)
    desktop = [m for m in executed if m["session_id"] == "ws-1"]
    assert desktop == [
        {"action": "desktop_ws_send", "session_id": "ws-1",
         "payload": b"key-down", "opcode": 2},
        {"action": "desktop_ws_send", "session_id": "ws-1",
         "payload": b"key-up", "opcode": 1},
        {"type": "command", "action": "desktop_ws_close",
         "request_id": "c1", "session_id": "ws-1"},
    ]
    assert {"action": "cs_ws_send", "session_id": "cs-1",
            "frame": b"\x81\x00"} in executed
    # Only the JSON close command is answered; binary frames get no reply.
    assert _wait(lambda: len(sends) == 1)
    assert json.loads(sends[0][0])["request_id"] == "c1"


def test_relay_refuses_relay_only_channels_from_the_server():
    executed, sends = [], []
    frames = [
        (0x02, bc.encode(bc.CHANNEL_HTTP_CHUNK, "r1", b"body")),
        (0x02, bc.encode(bc.CHANNEL_DESKTOP_AUDIO, "a1", b"opus")),
        CLOSE,
    ]
    ConnSession(_ctx(frames, executed, sends)).run()
    time.sleep(0.1)
    assert executed == [] and sends == []


@pytest.mark.parametrize("chunk", [b"\x00raw-bytes\xff", "AHJhdy1ieXRlc/8="])
def test_http_proxy_chunks_leave_the_relay_as_binary_frames(chunk):
    """Raw chunks and host-helper base64 chunks both go out as raw bytes."""
    sends = []

    def _exec(msg, on_output=None):
        on_output("start", {"status": 200, "headers": {}})
        on_output("chunk", chunk)
        on_output("end", None)
        return {"ok": True}

    session = ConnSession(_ctx([CLOSE], [], sends))
    session.execute_command = _exec
    session._run_command({"action": "http_proxy"}, "rid-1",
                         session.sock, session.ws_frame_send)

    kinds = []
    for frame, opcode in sends:
        if opcode == 0x02:
            kinds.append(("binary", bc.decode(frame)))
        else:
            kinds.append(("text", json.loads(frame)))
    assert kinds[0][1]["kind"] == "start"
    assert kinds[1] == ("binary", (bc.CHANNEL_HTTP_CHUNK, 0, "rid-1",
                                   b"\x00raw-bytes\xff"))
    assert kinds[2][1]["kind"] == "end"
    assert kinds[3][1]["type"] == "result"


# ── Server dispatcher ────────────────────────────────────────────────


def _dispatch(frame, monkeypatch):
    from services._relay_conn import _RelayConnMixin
    calls = []
    monkeypatch.setattr("services.vnc_proxy.dispatch_vnc_ws_data",
                        lambda *a: calls.append(("desktop",) + a))
    monkeypatch.setattr("services.audio_proxy.dispatch_audio_data",
                        lambda *a: calls.append(("audio",) + a))
    monkeypatch.setattr("services.code_server_proxy.dispatch_cs_ws_frame",
                        lambda *a: calls.append(("code",) + a))
    service = types.SimpleNamespace(
        _service_id="relay-1",
        _dispatch_http_response=lambda msg: calls.append(("http", msg)))
    _RelayConnMixin._dispatch_relay_binary(None, frame, service)
    return calls


@pytest.mark.parametrize("frame, expected", [
    (bc.encode(bc.CHANNEL_DESKTOP_WS, "ws-1", b"rfb", 2),
     ("desktop", "relay-1", "ws-1", b"rfb", 2)),
    (bc.encode(bc.CHANNEL_DESKTOP_AUDIO, "au-1", b"opus"),
     ("audio", "relay-1", "au-1", b"opus")),
    (bc.encode(bc.CHANNEL_CODE_SERVER_WS, "cs-1", b"\x81\x01x"),
     ("code", "relay-1", "cs-1", b"\x81\x01x")),
    (bc.encode(bc.CHANNEL_HTTP_CHUNK, "req-9", b"<html>"),
     ("http", {"request_id": "req-9", "kind": "chunk", "data": b"<html>"})),
])
def test_server_routes_binary_frames_to_their_stream(frame, expected, monkeypatch):
    assert _dispatch(frame, monkeypatch) == [expected]


def test_server_ignores_malformed_binary_frame(monkeypatch):
    assert _dispatch(b"\x07junk", monkeypatch) == []


def test_http_response_stream_spools_raw_chunks_and_rejects_text():
    from services._relay_http_response import RelayHttpResponseStream

    def _runner(on_output):
        on_output("start", {"status": 200, "headers": {"X-A": "1"}})
        on_output("chunk", b"hello ")
        on_output("chunk", bytearray(b"world"))
        on_output("end", None)
        return {"ok": True}

    stream = RelayHttpResponseStream(_runner).start()
    stream.wait_ready()
    assert b"".join(stream.iter_bytes()) == b"hello world"

    def _base64_runner(on_output):
        on_output("start", {"status": 200, "headers": {}})
        on_output("chunk", "aGVsbG8=")
        return {"ok": True}

    bad = RelayHttpResponseStream(_base64_runner).start()
    bad.wait_ready()
    with pytest.raises(RuntimeError, match="not raw bytes"):
        b"".join(bad.iter_bytes())


def test_send_to_relay_writes_binary_opcode_under_the_connection_lock(monkeypatch):
    import asyncio
    import services._relay_ws as relay_ws

    written = []

    async def fake_send(writer, payload, opcode=0x01):
        written.append((writer, payload, opcode))

    monkeypatch.setattr(relay_ws, "_ws_send_frame", fake_send)
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        lock = asyncio.Lock()
        relay = types.SimpleNamespace(
            _relay_pool_lock=threading.Lock(),
            _relay_pool=[{"writer": "w", "loop": loop, "send_lock": lock}])
        frame = bc.encode(bc.CHANNEL_DESKTOP_WS, "ws-1", b"x", 2)
        relay_ws.send_to_relay(relay, frame, 0x02)
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(2)
        loop.close()
    assert written == [("w", frame, 0x02)]
