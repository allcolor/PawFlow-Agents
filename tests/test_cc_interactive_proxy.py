import importlib
import json


def test_proxy_rewrites_hop_by_hop_headers_and_host(monkeypatch):
    monkeypatch.setenv("PAWFLOW_CCI_SESSION_TOKEN", "sess")
    proxy = importlib.import_module("tools.cc_interactive_proxy")

    raw = proxy._rewrite_request(
        "POST /v1/messages HTTP/1.1",
        [
            ("Host", "127.0.0.1"),
            ("Proxy-Connection", "keep-alive"),
            ("Connection", "keep-alive"),
            ("Content-Type", "application/json"),
        ],
    ).decode("latin-1")

    assert "Host: api.anthropic.com\r\n" in raw
    assert "Proxy-Connection" not in raw
    assert "Connection: keep-alive" not in raw
    assert "Content-Type: application/json" in raw


def test_proxy_scrubs_large_payload_values(monkeypatch):
    monkeypatch.setenv("PAWFLOW_CCI_SESSION_TOKEN", "sess")
    proxy = importlib.import_module("tools.cc_interactive_proxy")

    scrubbed = proxy._scrub({"source": "x" * 600, "ok": "short"})

    assert scrubbed["ok"] == "short"
    assert scrubbed["source"]["length"] == 600
    assert len(scrubbed["source"]["sha256"]) == 64


def test_sse_observer_emits_json_events(monkeypatch):
    monkeypatch.setenv("PAWFLOW_CCI_SESSION_TOKEN", "sess")
    proxy = importlib.import_module("tools.cc_interactive_proxy")
    events = []
    monkeypatch.setattr(proxy.EVENTS, "emit", events.append)

    obs = proxy.SSEObserver({"type": "sse", "request_id": "r1"})
    obs.feed(
        b"event: content_block_delta\n"
        + b"data: "
        + json.dumps({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hi"}}).encode()
        + b"\n\n"
    )

    assert events == [{
        "type": "sse",
        "request_id": "r1",
        "event": "content_block_delta",
        "payload": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hi"}},
    }]


def test_hook_compacts_lifecycle_input():
    hook = importlib.import_module("tools.cc_interactive_hook")

    compact = hook._compact_input({
        "hook_event_name": "PreCompact",
        "trigger": "auto",
        "cwd": "/workspace",
        "transcript_path": "/tmp/secret.jsonl",
        "large": "x" * 1000,
    })

    assert compact == {
        "hook_event_name": "PreCompact",
        "trigger": "auto",
        "cwd": "/workspace",
    }
