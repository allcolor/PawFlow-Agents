"""Codex 0.146 streams a turn over WebSocket, not over an SSE body.

The proxy forwards the bytes either way, so the CLI never noticed. The
coordinator did: after the 101 there was no HTTP left to observe, so it got
neither text nor tool calls and waited out its no-event timeout in silence.
"""

import importlib
import json
import zlib

import pytest

CRLF = "\r\n"


def _headers(lines) -> bytes:
    return (CRLF.join(lines) + CRLF + CRLF).encode()


UPGRADE_REQUEST = _headers([
    "GET /backend-api/codex/responses HTTP/1.1",
    "Host: chatgpt.com",
    "Connection: Upgrade",
    "Upgrade: websocket",
    "Sec-WebSocket-Version: 13",
    "sec-websocket-extensions: permessage-deflate; client_max_window_bits",
])


def _accept(extensions: str = "permessage-deflate") -> bytes:
    lines = [
        "HTTP/1.1 101 Switching Protocols",
        "Connection: upgrade",
        "upgrade: websocket",
        "sec-websocket-accept: Kw3pN0o5GbO197B3ltsbayJkIMg=",
    ]
    if extensions:
        lines.append("sec-websocket-extensions: " + extensions)
    return _headers(lines)


def _frame(payload: bytes, *, opcode: int = 0x1, fin: bool = True,
           rsv1: bool = False, mask: bytes = b"") -> bytes:
    first = (0x80 if fin else 0) | (0x40 if rsv1 else 0) | opcode
    flag = 0x80 if mask else 0
    length = len(payload)
    if length < 126:
        header = bytes([first, flag | length])
    elif length < 1 << 16:
        header = bytes([first, flag | 126]) + length.to_bytes(2, "big")
    else:
        header = bytes([first, flag | 127]) + length.to_bytes(8, "big")
    if mask:
        header += mask
        payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
    return header + payload


class _Deflater:
    """RFC 7692 sender: sync flush per message, trailer stripped."""

    def __init__(self):
        self.obj = zlib.compressobj(
            zlib.Z_DEFAULT_COMPRESSION, zlib.DEFLATED, -15)

    def __call__(self, payload: bytes) -> bytes:
        data = self.obj.compress(payload) + self.obj.flush(zlib.Z_SYNC_FLUSH)
        assert data.endswith(b"\x00\x00\xff\xff")
        return data[:-4]


@pytest.fixture()
def ws():
    return importlib.import_module("tools.cc_interactive_ws")


@pytest.fixture()
def observers(monkeypatch):
    monkeypatch.setenv("PAWFLOW_CCI_SESSION_TOKEN", "sess")
    return importlib.import_module("tools.cc_interactive_observers")


@pytest.fixture()
def events(observers, monkeypatch):
    collected = []
    monkeypatch.setattr(observers.EVENTS, "emit", collected.append)
    return collected


# -- extension negotiation --------------------------------------------


def test_bare_permessage_deflate_means_context_takeover(ws):
    params = ws.parse_negotiated_extensions("permessage-deflate")
    assert params["enabled"] is True
    assert params["server_no_context_takeover"] is False
    assert params["client_no_context_takeover"] is False


def test_no_context_takeover_is_honoured(ws):
    params = ws.parse_negotiated_extensions(
        "permessage-deflate; server_no_context_takeover; "
        "server_max_window_bits=12")
    assert params["server_no_context_takeover"] is True
    assert params["client_no_context_takeover"] is False


def test_an_extension_we_cannot_undo_is_refused(ws):
    with pytest.raises(ws.UnsupportedWebSocketExtension):
        ws.parse_negotiated_extensions("x-webkit-deflate-frame")


# -- framing ----------------------------------------------------------


def test_fragments_and_interleaved_control_frames_rebuild_one_message(ws):
    decoder = ws.WebSocketMessageDecoder()
    stream = (_frame(b'{"a":', fin=False)
              + _frame(b"", opcode=0x9)
              + _frame(b'1}', opcode=0x0))
    assert decoder.feed(stream) == [b'{"a":1}']


def test_a_message_split_across_reads_waits_for_its_last_byte(ws):
    decoder = ws.WebSocketMessageDecoder()
    stream = _frame(b"x" * 300)
    assert decoder.feed(stream[:10]) == []
    assert decoder.feed(stream[10:]) == [b"x" * 300]


def test_context_takeover_decodes_the_second_message(ws):
    # The reason the decompressor is per connection and not per message: the
    # second message back-references the first one's window.
    deflate = _Deflater()
    decoder = ws.WebSocketMessageDecoder(ws._Inflater())
    first = b'{"type":"response.output_text.delta","delta":"hello"}'
    second = b'{"type":"response.output_text.delta","delta":"hello again"}'
    assert decoder.feed(_frame(deflate(first), rsv1=True)) == [first]
    assert decoder.feed(_frame(deflate(second), rsv1=True)) == [second]


def test_masked_client_frames_are_unmasked(ws):
    decoder = ws.WebSocketMessageDecoder()
    payload = b'{"type":"response.create"}'
    mask = bytes([0x1E, 0x28, 0x1E, 0x1C])
    assert decoder.feed(_frame(payload, mask=mask)) == [payload]


# -- the observers ----------------------------------------------------


def _upgrade(observers):
    tracker = observers.HTTPExchangeTracker("conn")
    request = observers.HTTPRequestObserver(tracker)
    response = observers.HTTPResponseObserver(tracker)
    request.feed(UPGRADE_REQUEST)
    return request, response


def test_responses_events_reach_the_coordinator_as_sse(observers, events):
    _request, response = _upgrade(observers)
    deflate = _Deflater()
    stream = _accept()
    for payload in (
        {"type": "response.created", "response": {"model": "gpt-5.6-sol"}},
        {"type": "response.output_text.delta", "delta": "Bien "},
        {"type": "response.output_text.delta", "delta": "recu"},
        {"type": "response.completed",
         "response": {"usage": {"output_tokens": 9}}},
    ):
        stream += _frame(deflate(json.dumps(payload).encode()), rsv1=True)
    response.feed(stream)

    sse = [event for event in events if event["type"] == "sse"]
    assert [event["payload"]["type"] for event in sse] == [
        "response.created", "response.output_text.delta",
        "response.output_text.delta", "response.completed"]
    assert "".join(event["payload"].get("delta", "") for event in sse) == (
        "Bien recu")
    starts = [event for event in events if event["type"] == "response_start"]
    assert [event["status"] for event in starts] == ["101"]


def test_frames_are_not_parsed_as_the_next_http_response(observers, events):
    # The regression itself: through the HTTP path the frames are read as a
    # response header and every event after the handshake is lost.
    _request, response = _upgrade(observers)
    deflate = _Deflater()
    response.feed(_accept() + _frame(
        deflate(b'{"type":"response.completed","response":{}}'), rsv1=True))
    assert [event["type"] for event in events] == [
        "request_start", "response_start", "sse"]


def test_a_split_frame_is_held_until_the_next_read(observers, events):
    _request, response = _upgrade(observers)
    deflate = _Deflater()
    frame = _frame(
        deflate(b'{"type":"response.completed","response":{}}'), rsv1=True)
    response.feed(_accept() + frame[:3])
    assert [event["type"] for event in events] == [
        "request_start", "response_start"]
    response.feed(frame[3:])
    assert [event["type"] for event in events] == [
        "request_start", "response_start", "sse"]


def test_the_turn_input_still_yields_tool_use_and_tool_result(
        observers, events):
    request, response = _upgrade(observers)
    response.feed(_accept())
    deflate = _Deflater()
    create = json.dumps({
        "type": "response.create",
        "model": "gpt-5.6-sol",
        "input": [
            {"type": "function_call", "call_id": "call-1",
             "name": "mcp__pawflow__use_tool",
             "arguments": json.dumps({
                 "tool_name": "read",
                 "arguments": {"path": "/workspace/a.py"}})},
            {"type": "function_call_output", "call_id": "call-1",
             "output": "file contents"},
        ],
    }).encode()
    request.feed(_frame(deflate(create), rsv1=True, mask=bytes([1, 2, 3, 4])))

    observed = [event for event in events
                if event["type"] in ("tool_use", "tool_result")]
    assert [event["type"] for event in observed] == ["tool_use", "tool_result"]
    assert observed[0]["name"] == "read"
    assert observed[0]["arguments"] == {"path": "/workspace/a.py"}
    assert observed[1]["content"] == "file contents"


def test_a_client_message_sent_before_the_101_is_still_read(
        observers, events):
    # The client half buffers from the upgrade request on, and the two halves
    # run in different threads. Nothing else would flush that buffer if the
    # client went quiet after its single response.create.
    request, response = _upgrade(observers)
    deflate = _Deflater()
    create = json.dumps({
        "type": "response.create",
        "input": [{"type": "function_call", "call_id": "c1",
                   "name": "read", "arguments": "{}"}],
    }).encode()
    request.feed(_frame(deflate(create), rsv1=True, mask=bytes([9, 8, 7, 6])))
    assert [event["type"] for event in events] == ["request_start"]

    response.feed(_accept())
    assert [event["type"] for event in events] == [
        "request_start", "response_start", "tool_use"]


def test_only_a_101_switches_to_frames(observers, events):
    # A 426 names the upgrade it wants too, and its body is ordinary HTTP.
    _request, response = _upgrade(observers)
    body = b'{"error":"upgrade required"}'
    response.feed(_headers([
        "HTTP/1.1 426 Upgrade Required",
        "Upgrade: websocket",
        "Content-Type: application/json",
        "Content-Length: " + str(len(body)),
    ]) + body)
    starts = [event for event in events if event["type"] == "response_start"]
    assert [event["status"] for event in starts] == ["426"]
    assert not [event for event in events if event["type"] == "sse"]


def test_an_undecodable_upgrade_fails_the_turn_instead_of_hanging(
        observers, events):
    # A coordinator that receives request_start and nothing else waits out its
    # full no-event timeout and says nothing about why.
    _request, response = _upgrade(observers)
    response.feed(_accept("x-webkit-deflate-frame"))
    errors = [event for event in events if event["type"] == "request_error"]
    assert len(errors) == 1
    assert "x-webkit-deflate-frame" in errors[0]["error"]
