import importlib
import gzip
import json


class _RecvSocket:
    def __init__(self, chunks):
        self.chunks = list(chunks)

    def recv(self, _n):
        return self.chunks.pop(0) if self.chunks else b""


class _SendSocket:
    def __init__(self):
        self.sent = []
        self.shutdowns = []

    def sendall(self, data):
        self.sent.append(data)

    def shutdown(self, how):
        self.shutdowns.append(how)


def test_proxy_forwards_each_received_chunk_without_rewriting(monkeypatch):
    monkeypatch.setenv("PAWFLOW_CCI_SESSION_TOKEN", "sess")
    proxy = importlib.import_module("tools.cc_interactive_proxy")
    src_chunks = [
        b"POST /v1/messages HTTP/1.1\r\nHost: api.anthropic.com\r\n",
        b"Connection: keep-alive\r\nContent-Length: 11\r\n\r\nhello",
        b" world",
    ]
    src = _RecvSocket(src_chunks)
    dst = _SendSocket()

    proxy._pipe_exact(src, dst)

    assert dst.sent == src_chunks
    assert dst.shutdowns == [proxy.socket.SHUT_WR]


def test_proxy_request_observer_does_not_modify_forwarded_bytes(monkeypatch):
    monkeypatch.setenv("PAWFLOW_CCI_SESSION_TOKEN", "sess")
    proxy = importlib.import_module("tools.cc_interactive_proxy")
    events = []
    monkeypatch.setattr(proxy.EVENTS, "emit", events.append)
    chunk = (
        b"POST /v1/messages HTTP/1.1\r\n"
        b"Host: api.anthropic.com\r\n"
        b"Connection: keep-alive\r\n"
        b"Content-Length: 5\r\n\r\nhello"
    )
    src = _RecvSocket([chunk])
    dst = _SendSocket()

    tracker = proxy.HTTPExchangeTracker("r1")
    proxy._pipe_exact(src, dst, proxy.HTTPRequestObserver(tracker))

    assert dst.sent == [chunk]
    assert events == [{
        "type": "request_start",
        "request_id": "r1",
        "method": "POST",
        "path": "/v1/messages",
        "body_sha256": proxy.hashlib.sha256(b"hello").hexdigest(),
        "body_bytes": 5,
        "ignore_reason": "",
    }]


def test_request_observer_emits_observed_tool_results(monkeypatch):
    monkeypatch.setenv("PAWFLOW_CCI_SESSION_TOKEN", "sess")
    proxy = importlib.import_module("tools.cc_interactive_proxy")
    events = []
    monkeypatch.setattr(proxy.EVENTS, "emit", events.append)
    body = json.dumps({
        "messages": [{
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": "toolu_1",
                "content": [{"type": "text", "text": "file body"}],
            }],
        }],
    }).encode()
    chunk = (
        b"POST /v1/messages?beta=true HTTP/1.1\r\n"
        b"Host: api.anthropic.com\r\n"
        + f"Content-Length: {len(body)}\r\n\r\n".encode()
        + body
    )

    tracker = proxy.HTTPExchangeTracker("r1")
    proxy.HTTPRequestObserver(tracker).feed(chunk)

    assert events[0]["type"] == "request_start"
    assert events[1] == {
        "type": "tool_result",
        "request_id": "r1",
        "path": "/v1/messages?beta=true",
        "tool_use_id": "toolu_1",
        "content": "file body",
        "is_error": False,
    }


def test_request_observer_emits_observed_tool_use_before_result(monkeypatch):
    monkeypatch.setenv("PAWFLOW_CCI_SESSION_TOKEN", "sess")
    proxy = importlib.import_module("tools.cc_interactive_proxy")
    events = []
    monkeypatch.setattr(proxy.EVENTS, "emit", events.append)
    body = json.dumps({
        "messages": [
            {"role": "assistant", "content": [{
                "type": "tool_use",
                "id": "toolu_1",
                "name": "Bash",
                "input": {"command": "git status"},
            }]},
            {"role": "user", "content": [{
                "type": "tool_result",
                "tool_use_id": "toolu_1",
                "content": "clean",
            }]},
        ],
    }).encode()
    chunk = (
        b"POST /v1/messages?beta=true HTTP/1.1\r\n"
        b"Host: api.anthropic.com\r\n"
        + f"Content-Length: {len(body)}\r\n\r\n".encode()
        + body
    )

    proxy.HTTPRequestObserver(proxy.HTTPExchangeTracker("r1")).feed(chunk)

    assert [event["type"] for event in events] == ["request_start", "tool_use", "tool_result"]
    assert events[1] == {
        "type": "tool_use",
        "request_id": "r1",
        "path": "/v1/messages?beta=true",
        "tool_use_id": "toolu_1",
        "name": "Bash",
        "arguments": {"command": "git status"},
    }
    assert events[2]["tool_use_id"] == "toolu_1"
    assert events[2]["content"] == "clean"


def test_request_observer_hides_bootstrap_native_tools(monkeypatch):
    monkeypatch.setenv("PAWFLOW_CCI_SESSION_TOKEN", "sess")
    proxy = importlib.import_module("tools.cc_interactive_proxy")
    events = []
    monkeypatch.setattr(proxy.EVENTS, "emit", events.append)
    body = json.dumps({
        "messages": [
            {"role": "assistant", "content": [{
                "type": "tool_use",
                "id": "toolu_1",
                "name": "Read",
                "input": {"file_path": "/cc_sessions/c/a/.pawflow_cci/initial_context.md"},
            }]},
            {"role": "user", "content": [{
                "type": "tool_result",
                "tool_use_id": "toolu_1",
                "content": "initial context",
            }]},
            {"role": "assistant", "content": [{
                "type": "tool_use",
                "id": "toolu_2",
                "name": "ToolSearch",
                "input": {"query": "Bash"},
            }]},
        ],
    }).encode()
    chunk = (
        b"POST /v1/messages?beta=true HTTP/1.1\r\n"
        b"Host: api.anthropic.com\r\n"
        + f"Content-Length: {len(body)}\r\n\r\n".encode()
        + body
    )

    proxy.HTTPRequestObserver(proxy.HTTPExchangeTracker("r1")).feed(chunk)

    assert [event["type"] for event in events] == ["request_start"]


def test_request_observer_ignores_title_prompt_requests(monkeypatch):
    monkeypatch.setenv("PAWFLOW_CCI_SESSION_TOKEN", "sess")
    proxy = importlib.import_module("tools.cc_interactive_proxy")
    events = []
    monkeypatch.setattr(proxy.EVENTS, "emit", events.append)
    body = json.dumps({
        "messages": [{
            "role": "user",
            "content": "Generate a JSON title for this conversation: Continue PawFlow session context",
        }],
    }).encode()
    chunk = (
        b"POST /v1/messages?beta=true HTTP/1.1\r\n"
        b"Host: api.anthropic.com\r\n"
        + f"Content-Length: {len(body)}\r\n\r\n".encode()
        + body
    )

    tracker = proxy.HTTPExchangeTracker("r1")
    proxy.HTTPRequestObserver(tracker).feed(chunk)

    ctx = tracker.pop()
    assert ctx["ignore_response"] is True
    assert ctx["ignore_reason"] == "title_prompt"
    assert events[0]["ignore_reason"] == "title_prompt"


def test_proxy_observer_errors_do_not_affect_forwarding(monkeypatch):
    monkeypatch.setenv("PAWFLOW_CCI_SESSION_TOKEN", "sess")
    proxy = importlib.import_module("tools.cc_interactive_proxy")

    class BadObserver:
        def feed(self, _data):
            raise ValueError("observer boom")

    chunks = [b"one", b"two"]
    src = _RecvSocket(chunks)
    dst = _SendSocket()

    proxy._pipe_exact(src, dst, BadObserver())

    assert dst.sent == chunks


def test_proxy_scrubs_large_payload_values(monkeypatch):
    monkeypatch.setenv("PAWFLOW_CCI_SESSION_TOKEN", "sess")
    proxy = importlib.import_module("tools.cc_interactive_proxy")

    scrubbed = proxy._scrub({"source": "x" * 600, "ok": "short"})

    assert scrubbed["ok"] == "short"
    assert scrubbed["source"]["length"] == 600
    assert len(scrubbed["source"]["sha256"]) == 64


def test_event_client_preserves_provider_payload_but_scrubs_wire(monkeypatch):
    monkeypatch.setenv("PAWFLOW_CCI_SESSION_TOKEN", "sess")
    proxy = importlib.import_module("tools.cc_interactive_proxy")
    sent = []
    client = proxy.EventClient("", "", "sess")
    client.sock = object()
    monkeypatch.setattr(client, "_send", sent.append)

    large = "x" * 800
    client.emit({"type": "tool_result", "content": large})
    client.emit({"type": "wire", "content": large})

    assert sent[0]["event"]["content"] == large
    assert sent[1]["event"]["content"]["length"] == len(large)
    assert len(sent[1]["event"]["content"]["sha256"]) == 64


def test_wire_logger_emits_full_body_with_redacted_sensitive_headers(monkeypatch):
    monkeypatch.setenv("PAWFLOW_CCI_SESSION_TOKEN", "sess")
    proxy = importlib.import_module("tools.cc_interactive_proxy")
    events = []
    logs = []
    monkeypatch.setattr(proxy.EVENTS, "emit", events.append)
    monkeypatch.setattr(proxy, "_log", logs.append)
    monkeypatch.setattr(proxy, "WIRE_LOG_ENABLED", True)

    wire = proxy.WireLogger("r1", "client_to_upstream", {})
    wire.log("in", (
        b"POST /v1/messages?beta=true HTTP/1.1\r\n"
        b"Authorization: Bearer secret-token\r\n"
        b"Cookie: session=secret-cookie\r\n"
        b"Content-Length: 11\r\n\r\n"
        b"hello world"
    ))
    wire.log("out", (
        b"POST /v1/messages?beta=true HTTP/1.1\r\n"
        b"Authorization: Bearer secret-token\r\n"
        b"Cookie: session=secret-cookie\r\n"
        b"Content-Length: 11\r\n\r\n"
        b"hello world"
    ))

    assert len(events) == 2
    assert {event["stage"] for event in events} == {"in", "out"}
    for event in events:
        decoded = proxy.base64.b64decode(event["data_b64"])
        assert b"hello world" in decoded
        assert b"secret-token" not in decoded
        assert b"secret-cookie" not in decoded
        assert b"Authorization: <redacted:" in decoded
        assert b"Cookie: <redacted:" in decoded
        assert "hello world" in event["text_repr"]
    assert logs


def test_wire_logger_is_disabled_by_default_but_tracks_upstream_bytes(monkeypatch):
    monkeypatch.setenv("PAWFLOW_CCI_SESSION_TOKEN", "sess")
    proxy = importlib.import_module("tools.cc_interactive_proxy")
    events = []
    logs = []
    context = {}
    monkeypatch.setattr(proxy.EVENTS, "emit", events.append)
    monkeypatch.setattr(proxy, "_log", logs.append)
    monkeypatch.setattr(proxy, "WIRE_LOG_ENABLED", False)

    wire = proxy.WireLogger("r1", "upstream_to_client", context)
    wire.log("out", b"HTTP/1.1 200 OK\r\n\r\nhello")

    assert events == []
    assert logs == []
    assert context["upstream_to_client_bytes"] == 24


def test_wire_logger_skips_non_model_paths_by_default(monkeypatch):
    monkeypatch.setenv("PAWFLOW_CCI_SESSION_TOKEN", "sess")
    proxy = importlib.import_module("tools.cc_interactive_proxy")
    events = []
    monkeypatch.setattr(proxy.EVENTS, "emit", events.append)

    wire = proxy.WireLogger("r1", "client_to_upstream", {})
    wire.log("in", (
        b"POST /api/event_logging/v2/batch HTTP/1.1\r\n"
        b"Authorization: Bearer secret-token\r\n"
        b"Content-Length: 11\r\n\r\n"
        b"hello world"
    ))
    wire.log("in", b"more telemetry")

    assert events == []


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


def test_response_observer_reads_chunked_sse_without_rechunking(monkeypatch):
    monkeypatch.setenv("PAWFLOW_CCI_SESSION_TOKEN", "sess")
    proxy = importlib.import_module("tools.cc_interactive_proxy")
    events = []
    monkeypatch.setattr(proxy.EVENTS, "emit", events.append)
    sse = (
        b"event: content_block_delta\n"
        + b"data: "
        + json.dumps({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hi"}}).encode()
        + b"\n\n"
    )
    response_chunks = [
        b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n",
        (hex(len(sse))[2:].encode() + b"\r\n" + sse[:12]),
        sse[12:] + b"\r\n0\r\n\r\n",
    ]
    src = _RecvSocket(response_chunks)
    dst = _SendSocket()

    tracker = proxy.HTTPExchangeTracker("r1")
    tracker.push({"request_id": "r1", "path": "/v1/messages", "ignore_response": False})
    proxy._pipe_exact(src, dst, proxy.HTTPResponseObserver(tracker))

    assert dst.sent == response_chunks
    assert events == [
        {
            "type": "response_start",
            "request_id": "r1",
            "path": "/v1/messages",
            "status": "200",
            "content_type": "text/event-stream",
            "content_length": 0,
            "content_encoding": "",
            "chunked": True,
        },
        {
            "type": "sse",
            "request_id": "r1",
            "event": "content_block_delta",
            "payload": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hi"}},
        },
    ]


def test_response_observer_decompresses_chunked_gzip_sse(monkeypatch):
    monkeypatch.setenv("PAWFLOW_CCI_SESSION_TOKEN", "sess")
    proxy = importlib.import_module("tools.cc_interactive_proxy")
    events = []
    monkeypatch.setattr(proxy.EVENTS, "emit", events.append)
    sse = (
        b"event: content_block_delta\n"
        + b"data: "
        + json.dumps({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "bonjour"}}).encode()
        + b"\n\n"
    )
    compressed = gzip.compress(sse)
    response = (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: text/event-stream; charset=utf-8\r\n"
        b"Content-Encoding: gzip\r\n"
        b"Transfer-Encoding: chunked\r\n\r\n"
        + hex(len(compressed)).encode()
        + b"\r\n"
        + compressed
        + b"\r\n0\r\n\r\n"
    )

    tracker = proxy.HTTPExchangeTracker("r-gzip")
    tracker.push({"request_id": "r-gzip", "path": "/v1/messages", "ignore_response": False})
    proxy.HTTPResponseObserver(tracker).feed(response)

    assert events == [
        {
            "type": "response_start",
            "request_id": "r-gzip",
            "path": "/v1/messages",
            "status": "200",
            "content_type": "text/event-stream; charset=utf-8",
            "content_length": 0,
            "content_encoding": "gzip",
            "chunked": True,
        },
        {
            "type": "sse",
            "request_id": "r-gzip",
            "event": "content_block_delta",
            "payload": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "bonjour"}},
        },
    ]


def test_response_observer_converts_json_message_to_stream_events(monkeypatch):
    monkeypatch.setenv("PAWFLOW_CCI_SESSION_TOKEN", "sess")
    proxy = importlib.import_module("tools.cc_interactive_proxy")
    events = []
    monkeypatch.setattr(proxy.EVENTS, "emit", events.append)
    body = json.dumps({
        "type": "message",
        "content": [{"type": "text", "text": "Bonjour Quentin !"}],
        "usage": {"input_tokens": 3, "output_tokens": 4},
    }).encode()
    response = (
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
        + str(len(body)).encode()
        + b"\r\n\r\n"
        + body
    )

    tracker = proxy.HTTPExchangeTracker("r-json")
    tracker.push({"request_id": "r-json", "path": "/v1/messages", "ignore_response": False})
    obs = proxy.HTTPResponseObserver(tracker)
    obs.feed(response)

    assert events == [
        {
            "type": "response_start",
            "request_id": "r-json",
            "path": "/v1/messages",
            "status": "200",
            "content_type": "application/json",
            "content_length": len(body),
            "content_encoding": "",
            "chunked": False,
        },
        {
            "type": "sse",
            "request_id": "r-json",
            "event": "content_block_start",
            "payload": {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text"},
            },
        },
        {
            "type": "sse",
            "request_id": "r-json",
            "event": "content_block_delta",
            "payload": {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "Bonjour Quentin !"},
            },
        },
        {
            "type": "sse",
            "request_id": "r-json",
            "event": "content_block_stop",
            "payload": {"type": "content_block_stop", "index": 0},
        },
        {
            "type": "sse",
            "request_id": "r-json",
            "event": "message_delta",
            "payload": {
                "type": "message_delta",
                "usage": {"input_tokens": 3, "output_tokens": 4},
            },
        },
        {
            "type": "sse",
            "request_id": "r-json",
            "event": "message_stop",
            "payload": {"type": "message_stop"},
        },
    ]


def test_response_observer_ignores_json_title_message(monkeypatch):
    monkeypatch.setenv("PAWFLOW_CCI_SESSION_TOKEN", "sess")
    proxy = importlib.import_module("tools.cc_interactive_proxy")
    events = []
    monkeypatch.setattr(proxy.EVENTS, "emit", events.append)
    body = json.dumps({
        "type": "message",
        "content": [{"type": "text", "text": json.dumps({"title": "Continue PawFlow session context"})}],
    }).encode()
    response = (
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
        + str(len(body)).encode()
        + b"\r\n\r\n"
        + body
    )

    tracker = proxy.HTTPExchangeTracker("r-title")
    tracker.push({"request_id": "r-title", "path": "/v1/messages", "ignore_response": False})
    proxy.HTTPResponseObserver(tracker).feed(response)

    assert events == [
        {
            "type": "response_start",
            "request_id": "r-title",
            "path": "/v1/messages",
            "status": "200",
            "content_type": "application/json",
            "content_length": len(body),
            "content_encoding": "",
            "chunked": False,
        },
        {
            "type": "response_ignored",
            "request_id": "r-title",
            "reason": "title_json_message",
            "payload_type": "message",
        },
    ]


def test_keep_alive_quota_probe_response_is_ignored_before_real_response(monkeypatch):
    monkeypatch.setenv("PAWFLOW_CCI_SESSION_TOKEN", "sess")
    proxy = importlib.import_module("tools.cc_interactive_proxy")
    events = []
    monkeypatch.setattr(proxy.EVENTS, "emit", events.append)

    tracker = proxy.HTTPExchangeTracker("conn")
    req_observer = proxy.HTTPRequestObserver(tracker)
    quota_body = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "quota"}],
    }).encode()
    real_body = json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": "Bonjour"}],
    }).encode()
    req_observer.feed(
        b"POST /v1/messages?beta=true HTTP/1.1\r\nContent-Length: "
        + str(len(quota_body)).encode() + b"\r\n\r\n" + quota_body
        + b"POST /v1/messages?beta=true HTTP/1.1\r\nContent-Length: "
        + str(len(real_body)).encode() + b"\r\n\r\n" + real_body
    )

    response_observer = proxy.HTTPResponseObserver(tracker)
    quota_response = json.dumps({
        "type": "message",
        "content": [{"type": "text", "text": "#"}],
    }).encode()
    real_response = json.dumps({
        "type": "message",
        "content": [{"type": "text", "text": "Bonjour Quentin !"}],
    }).encode()
    response_observer.feed(
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
        + str(len(quota_response)).encode() + b"\r\n\r\n" + quota_response
        + b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
        + str(len(real_response)).encode() + b"\r\n\r\n" + real_response
    )

    assert events[0]["type"] == "request_start"
    assert events[0]["request_id"] == "conn"
    assert events[0]["ignore_reason"] == "quota_probe"
    assert events[1]["type"] == "request_start"
    assert events[1]["request_id"] == "conn-2"
    assert events[1]["ignore_reason"] == ""
    assert any(
        event.get("type") == "response_ignored"
        and event.get("request_id") == "conn"
        and event.get("reason") == "quota_probe"
        for event in events
    )
    text_events = [
        event for event in events
        if event.get("type") == "sse"
        and event.get("event") == "content_block_delta"
    ]
    assert [event["payload"]["delta"]["text"] for event in text_events] == ["Bonjour Quentin !"]


def test_upstream_socket_is_blocking_after_connect(monkeypatch):
    monkeypatch.setenv("PAWFLOW_CCI_SESSION_TOKEN", "sess")
    proxy = importlib.import_module("tools.cc_interactive_proxy")
    calls = []

    class _Socket:
        def __init__(self):
            self.timeout = "connect-timeout"

        def settimeout(self, value):
            self.timeout = value
            calls.append(value)

    class _Context:
        def wrap_socket(self, raw, server_hostname=None):
            assert server_hostname == proxy.UPSTREAM_HOST
            return raw

    raw = _Socket()
    monkeypatch.setattr(proxy.socket, "create_connection", lambda *a, **k: raw)
    monkeypatch.setattr(proxy.ssl, "create_default_context", lambda: _Context())
    monkeypatch.setenv("PAWFLOW_ANTHROPIC_UPSTREAM_IPS", "203.0.113.10")

    assert proxy._connect_upstream() is raw
    assert calls == [None, None]
    assert raw.timeout is None


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


def test_hook_marks_pawflow_injected_prompts_without_forwarding_text(tmp_path, monkeypatch):
    hook = importlib.import_module("tools.cc_interactive_hook")
    prompt = "PawFlow injected prompt"
    marker = tmp_path / "injected_prompts.jsonl"
    marker.write_text(json.dumps({
        "sha256": hook.hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "length": len(prompt),
    }) + "\n", encoding="utf-8")
    monkeypatch.setenv("PAWFLOW_CCI_INJECTED_PROMPTS", str(marker))

    compact = hook._compact_input({
        "hook_event_name": "UserPromptSubmit",
        "prompt": prompt,
        "cwd": "/workspace",
    })

    assert compact["hook_event_name"] == "UserPromptSubmit"
    assert compact["pawflow_injected_prompt"] is True
    assert compact["prompt_len"] == len(prompt)
    assert "prompt" not in compact
    assert marker.read_text(encoding="utf-8") == ""


def test_hook_keeps_manual_user_prompt(monkeypatch):
    hook = importlib.import_module("tools.cc_interactive_hook")
    monkeypatch.delenv("PAWFLOW_CCI_INJECTED_PROMPTS", raising=False)

    compact = hook._compact_input({
        "hook_event_name": "UserPromptSubmit",
        "prompt": "Manual tmux prompt",
    })

    assert compact["pawflow_injected_prompt"] is False
    assert compact["prompt"] == "Manual tmux prompt"


def test_hook_masks_pawflow_prompt_when_marker_is_missing(monkeypatch):
    hook = importlib.import_module("tools.cc_interactive_hook")
    monkeypatch.setenv("PAWFLOW_CCI_INJECTED_PROMPTS", "/tmp/missing-pawflow-cci-marker.jsonl")
    prompt = (
        "Read this PawFlow initial context file before answering:\n"
        "@/cc_sessions/c/a/.pawflow_cci/initial_context.md\n\n"
        "It contains the compacted conversation summary/context."
    )

    compact = hook._compact_input({
        "hook_event_name": "UserPromptSubmit",
        "prompt": prompt,
    })

    assert compact["pawflow_injected_prompt"] is False
    assert compact["pawflow_managed_prompt"] is True
    assert compact["pawflow_injected_prompt_missing"] is True
    assert compact["prompt_sha256"] == hook.hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    assert "prompt" not in compact
