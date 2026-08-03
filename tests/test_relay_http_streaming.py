"""Relay HTTP responses stay chunked and disk-backed end to end."""

import base64
import os

import pytest

from pawflow_relay import _relay_actions
from services._relay_http_response import RelayHttpResponseStream


def test_relay_http_proxy_rejects_the_buffering_inline_contract():
    result = _relay_actions.http_proxy({"port": 8765})

    assert result["ok"] is False
    assert "streaming" in result["error"]


def test_relay_http_proxy_reads_and_emits_bounded_chunks(monkeypatch):
    reads = []
    emitted = []

    class _Response:
        status = 200
        reason = "OK"

        def getheaders(self):
            return [("Content-Type", "video/mp4")]

        def read(self, size):
            reads.append(size)
            return b"video-part" if len(reads) == 1 else b""

    class _Connection:
        def __init__(self, host, port, timeout):
            assert (host, port, timeout) == ("127.0.0.1", 8765, 300)

        def request(self, method, path, body=None, headers=None):
            assert (method, path, body) == ("GET", "/output.mp4", None)

        def getresponse(self):
            return _Response()

        def close(self):
            return None

    monkeypatch.setattr("http.client.HTTPConnection", _Connection)

    result = _relay_actions.http_proxy(
        {"port": 8765, "method": "GET", "req_path": "/output.mp4"},
        on_output=lambda kind, data: emitted.append((kind, data)))

    assert result["ok"] is True
    assert reads == [64 * 1024, 64 * 1024]
    assert emitted[0][0] == "start"
    assert base64.b64decode(emitted[1][1]) == b"video-part"
    assert emitted[2] == ("end", None)


def test_server_stream_spools_chunks_to_disk_and_removes_the_file():
    payload = b"0123456789" * 1000

    class _Relay:
        def http_proxy_stream(self, *, on_output, **_kwargs):
            on_output("start", {
                "status": 200,
                "headers": {
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(len(payload)),
                },
            })
            for offset in range(0, len(payload), 97):
                on_output(
                    "chunk",
                    base64.b64encode(payload[offset:offset + 97]).decode("ascii"))
            on_output("end", None)
            return {"ok": True}

    stream = RelayHttpResponseStream.for_local_port(
        _Relay(), port=8765, method="GET", req_path="/large.bin",
        headers={}, body=b"").start()
    stream.wait_ready()
    spool_path = stream.spool_path

    assert os.path.exists(spool_path)
    assert "Content-Length" not in stream.headers
    assert b"".join(stream.iter_bytes(chunk_size=113)) == payload
    assert not os.path.exists(spool_path)


def test_server_stream_surfaces_failure_after_headers_and_removes_spool():
    class _Relay:
        def http_proxy_stream(self, *, on_output, **_kwargs):
            on_output("start", {"status": 200, "headers": {}})
            on_output("chunk", base64.b64encode(b"partial").decode("ascii"))
            return {"ok": False, "error": "upstream connection broke"}

    stream = RelayHttpResponseStream.for_local_port(
        _Relay(), port=8765, method="GET", req_path="/large.bin",
        headers={}, body=b"").start()
    stream.wait_ready()
    spool_path = stream.spool_path

    with pytest.raises(RuntimeError, match="upstream connection broke"):
        list(stream.iter_bytes())
    assert not os.path.exists(spool_path)
