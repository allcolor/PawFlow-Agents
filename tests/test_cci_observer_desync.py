"""CCI proxy observers: HTTP framing must survive leaf-observer failures.

Regression tests for the lost-final-answer incident (beta.192): brotli JSON
responses (count_tokens, event_logging) made JSONResponseObserver raise inside
the parse chain, corrupting HTTPResponseObserver state. From then on the
connection emitted phantom empty-status responses, request/response pairing
shifted, and the turn's final /v1/messages response was only captured during
the NEXT turn — the webchat received the final message and its `done` a full
turn late.  A second, timing-only desync: a terminating `0\r\n` whose closing
CRLF arrived in the next TCP segment fell into the data-chunk branch and
parsed the next response's bytes as body.
"""
import importlib
import json

import pytest


@pytest.fixture(autouse=True)
def _alias_observers_module():
    # The proxy imports the observers module as top-level cc_interactive_observers
    import sys
    from tools import cc_interactive_observers  # noqa: F401 — ensure loaded
    sys.modules.setdefault(
        "cc_interactive_observers", sys.modules["tools.cc_interactive_observers"])
    yield


def _chunk(data: bytes) -> bytes:
    return f"{len(data):x}".encode() + b"\r\n" + data + b"\r\n"


def _observers(monkeypatch, events):
    monkeypatch.setenv("PAWFLOW_CCI_SESSION_TOKEN", "sess")
    observers = importlib.import_module("tools.cc_interactive_observers")
    monkeypatch.setattr(observers.EVENTS, "emit", events.append)
    return observers


def test_undecodable_json_response_does_not_desync_pairing(monkeypatch):
    """A brotli JSON response must not shift pairing for later responses."""
    events = []
    observers = _observers(monkeypatch, events)
    tracker = observers.HTTPExchangeTracker("conn")
    from tools.cc_interactive_proxy import HTTPRequestObserver, HTTPResponseObserver
    req_obs = HTTPRequestObserver(tracker)
    resp_obs = HTTPResponseObserver(tracker)
    body = json.dumps({"messages": []}).encode()
    head = ("POST %s HTTP/1.1\r\nContent-Length: %d\r\n\r\n" % (
        "/v1/messages/count_tokens?beta=true", len(body))).encode()
    req_obs.feed(head + body)
    head2 = ("POST %s HTTP/1.1\r\nContent-Length: %d\r\n\r\n" % (
        "/v1/messages?beta=true", len(body))).encode()
    req_obs.feed(head2 + body)

    resp1 = (b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
             b"Content-Encoding: br\r\nTransfer-Encoding: chunked\r\n\r\n"
             + _chunk(b"\x1b\x2e\x00\xa4brotli-junk") + b"0\r\n\r\n")
    sse_body = b"event: message_start\ndata: {\"type\": \"message_start\"}\n\n"
    resp2 = (b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
             b"Transfer-Encoding: chunked\r\n\r\n" + _chunk(sse_body) + b"0\r\n\r\n")
    resp_obs.feed(resp1)
    resp_obs.feed(resp2)

    starts = [e for e in events if e["type"] == "response_start"]
    assert [s["request_id"] for s in starts] == ["conn", "conn-2"]
    assert all(s["status"] == "200" for s in starts)
    ignored = [e for e in events if e["type"] == "response_ignored"]
    assert any(e.get("reason") == "unsupported_content_encoding" for e in ignored)
    sse = [e for e in events
           if e["type"] == "sse" and e.get("event") == "message_start"]
    assert sse and sse[0]["request_id"] == "conn-2"


def test_split_terminating_chunk_finishes_and_keeps_next_response(monkeypatch):
    """`0\\r\\n` then `\\r\\n` in a later feed must still terminate the body."""
    events = []
    observers = _observers(monkeypatch, events)
    finished = []

    class Leaf:
        def feed(self, data):
            pass

        def finish(self):
            finished.append(True)

    obs = observers.ChunkedBodyObserver(Leaf())
    assert obs.feed(_chunk(b"data") + b"0\r\n") is None
    leftover = obs.feed(b"\r\nHTTP/1.1 200 OK\r\n")
    assert finished == [True]
    assert obs.done
    assert leftover == b"HTTP/1.1 200 OK\r\n"


def test_leaf_observer_exception_keeps_chunk_framing(monkeypatch):
    events = []
    observers = _observers(monkeypatch, events)

    class Boom:
        def feed(self, data):
            raise RuntimeError("boom")

        def finish(self):
            raise RuntimeError("boom-finish")

    obs = observers.ChunkedBodyObserver(Boom())
    leftover = obs.feed(_chunk(b"payload") + b"0\r\n\r\nNEXT")
    assert obs.done
    assert leftover == b"NEXT"


def test_json_response_observer_ignores_undecodable_payloads(monkeypatch):
    events = []
    observers = _observers(monkeypatch, events)
    j = observers.JSONResponseObserver(
        {"type": "sse", "request_id": "r"}, content_length=0, encoding="br")
    j.feed(b"\x00\x01junk")
    j.finish()
    assert events and events[-1]["type"] == "response_ignored"
    assert events[-1]["reason"] == "unsupported_content_encoding"
    events.clear()
    j2 = observers.JSONResponseObserver(
        {"type": "sse", "request_id": "r2"}, content_length=0, encoding="")
    j2.feed(b"not-json")
    j2.finish()
    assert events and events[-1]["type"] == "response_ignored"
    assert events[-1]["reason"] == "json_undecodable"
