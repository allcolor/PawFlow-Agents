import importlib
import gzip
import json
import threading
import time

import pytest


@pytest.fixture(autouse=True)
def _reset_observed_emission_dedup():
    # The proxy dedups observed tool blocks at the source for the lifetime of
    # its process; tests reuse call ids across cases, so each starts clean.
    # The proxy imports the observers module as top-level cc_interactive_observers
    # when tools/ is on sys.path (standalone layout), so the suite can hold TWO
    # module instances with independent dedup sets — clear every loaded one.
    import sys
    from tools import cc_interactive_observers  # noqa: F401 — ensure loaded
    for name in ("tools.cc_interactive_observers", "cc_interactive_observers"):
        module = sys.modules.get(name)
        if module is not None:
            module._OBSERVED_EMITTED_USES.clear()
            module._OBSERVED_EMITTED_RESULTS.clear()
    yield


def test_observed_blocks_are_emitted_once_across_requests(monkeypatch):
    """Every request re-sends the whole history; only NEW blocks may emit.

    Without source dedup the proxy re-emitted every historical tool block on
    every /v1/messages request — event volume grew with the square of the
    turn count and delivery lagged the coordinator by more than a minute on
    a large-context session (the lost-final-answer incident).
    """
    monkeypatch.setenv("PAWFLOW_CCI_SESSION_TOKEN", "sess")
    proxy = importlib.import_module("tools.cc_interactive_proxy")
    events = []
    monkeypatch.setattr(proxy.EVENTS, "emit", events.append)
    body = json.dumps({"messages": [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu-dedup-1", "name": "bash",
             "input": {"command": "true"}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu-dedup-1",
             "content": "ok"}]},
    ]}).encode()
    proxy._emit_observed_tool_blocks("req-1", "/v1/messages?beta=true", body)
    assert [e["type"] for e in events] == ["tool_use", "tool_result"]
    # The next request replays the same history plus one new pair.
    body2 = json.dumps({"messages": [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu-dedup-1", "name": "bash",
             "input": {"command": "true"}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu-dedup-1",
             "content": "ok"}]},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu-dedup-2", "name": "read",
             "input": {"path": "/x"}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu-dedup-2",
             "content": "data"}]},
    ]}).encode()
    events.clear()
    proxy._emit_observed_tool_blocks("req-2", "/v1/messages?beta=true", body2)
    assert [(e["type"], e["tool_use_id"]) for e in events] == [
        ("tool_use", "toolu-dedup-2"), ("tool_result", "toolu-dedup-2")]


def test_proxy_observes_responses_function_calls_and_outputs(monkeypatch):
    monkeypatch.setenv("PAWFLOW_CCI_SESSION_TOKEN", "sess")
    proxy = importlib.import_module("tools.cc_interactive_proxy")
    events = []
    monkeypatch.setattr(proxy.EVENTS, "emit", events.append)
    body = json.dumps({
        "input": [
            {"type": "function_call", "call_id": "call-1",
             "name": "mcp__pawflow__use_tool",
             "arguments": json.dumps({
                 "tool_name": "read",
                 "arguments": {"path": "/workspace/a.py"}})},
            {"type": "function_call_output", "call_id": "call-1",
             "output": "file contents"},
        ]
    }).encode()

    proxy._emit_observed_tool_blocks(
        "request-1", "/backend-api/codex/responses", body)

    assert [event["type"] for event in events] == [
        "tool_use", "tool_result"]
    assert events[0]["name"] == "read"
    assert events[0]["arguments"] == {"path": "/workspace/a.py"}
    assert events[1]["content"] == "file contents"


def test_proxy_observes_the_native_call_items_too(monkeypatch):
    # The turn input replays Codex's own calls under their own item types.
    # Reading only `function_call` lost the shell and apply_patch results.
    monkeypatch.setenv("PAWFLOW_CCI_SESSION_TOKEN", "sess")
    proxy = importlib.import_module("tools.cc_interactive_proxy")
    events = []
    monkeypatch.setattr(proxy.EVENTS, "emit", events.append)
    body = json.dumps({
        "input": [
            {"type": "local_shell_call", "call_id": "call-1",
             "action": {"type": "exec", "command": ["bash", "-lc", "ls"]}},
            {"type": "local_shell_call_output", "call_id": "call-1",
             "output": "a.py"},
        ]
    }).encode()

    proxy._emit_observed_tool_blocks(
        "request-1", "/backend-api/codex/responses", body)

    assert [event["type"] for event in events] == ["tool_use", "tool_result"]
    assert events[0]["name"] == "local_shell"
    assert events[0]["arguments"] == {
        "type": "exec", "command": ["bash", "-lc", "ls"]}
    assert events[1]["content"] == "a.py"


def test_proxy_reports_a_code_mode_body_as_the_one_item_it_is(monkeypatch):
    # The observer describes the wire, and on the wire a code-mode body IS one
    # item: one call, one output. It does not read the script to guess what ran
    # inside it -- that is the relay's to report, and the provider drops this
    # item rather than render `exec(<javascript>)` in place of those tools.
    monkeypatch.setenv("PAWFLOW_CCI_SESSION_TOKEN", "sess")
    proxy = importlib.import_module("tools.cc_interactive_proxy")
    events = []
    monkeypatch.setattr(proxy.EVENTS, "emit", events.append)
    body = json.dumps({
        "input": [
            {"type": "custom_tool_call", "call_id": "call-1", "name": "exec",
             "input": 'const r=await tools.mcp__pawflow__use_tool('
                      '{tool_name:"read",arguments_json:'
                      '"{\\"path\\":\\"/workspace/a.py\\"}"});'
                      'await tools.exec_command({cmd:"ls"})'},
            {"type": "custom_tool_call_output", "call_id": "call-1",
             "output": "a.py"},
        ]
    }).encode()

    proxy._emit_observed_tool_blocks(
        "request-1", "/backend-api/codex/responses", body)

    assert [(event["type"], event["tool_use_id"]) for event in events] == [
        ("tool_use", "call-1"), ("tool_result", "call-1")]
    assert events[0]["name"] == "exec"
    assert events[1]["content"] == "a.py"


def test_proxy_reads_a_content_part_output_as_its_text(monkeypatch):
    # Codex returns its own tools' output as a list of content parts, not a
    # string. Serialised verbatim the user reads the JSON envelope instead of
    # what the command printed.
    monkeypatch.setenv("PAWFLOW_CCI_SESSION_TOKEN", "sess")
    proxy = importlib.import_module("tools.cc_interactive_proxy")
    events = []
    monkeypatch.setattr(proxy.EVENTS, "emit", events.append)
    body = json.dumps({
        "input": [
            {"type": "custom_tool_call", "call_id": "call-1", "id": "ctc-1",
             "name": "exec", "input": "const r = await tools.exec_command({})"},
            {"type": "custom_tool_call_output", "call_id": "call-1",
             "output": [{"type": "input_text", "text": "Script completed\n"},
                        {"type": "input_text", "text": "a.py\n"}]},
        ]
    }).encode()

    proxy._emit_observed_tool_blocks(
        "request-1", "/backend-api/codex/responses", body)

    assert [event["type"] for event in events] == ["tool_use", "tool_result"]
    # Both ids travel so the streamed observation of the same call, which may
    # quote either one, matches this row instead of opening a second.
    assert events[0]["alias_ids"] == ["call-1", "ctc-1"]
    assert events[1]["content"] == "Script completed\na.py\n"


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


def test_proxy_connects_to_clear_http_upstream_without_tls(monkeypatch):
    monkeypatch.setenv("PAWFLOW_CCI_SESSION_TOKEN", "sess")
    proxy = importlib.import_module("tools.cc_interactive_proxy")

    class RawSocket:
        def __init__(self):
            self.timeouts = []
            self.options = []

        def settimeout(self, value):
            self.timeouts.append(value)

        def setsockopt(self, *args):
            self.options.append(args)

    raw = RawSocket()
    wrapped = []

    monkeypatch.setattr(proxy, "UPSTREAM_HOST", "localhost")
    monkeypatch.setattr(proxy, "UPSTREAM_PORT", 11434)
    monkeypatch.setattr(proxy, "UPSTREAM_SCHEME", "http")
    monkeypatch.setenv("PAWFLOW_ANTHROPIC_UPSTREAM_IPS", "127.0.0.1")
    monkeypatch.setattr(proxy.socket, "create_connection", lambda address, timeout=0: raw)

    class Ctx:
        def wrap_socket(self, *args, **kwargs):
            wrapped.append((args, kwargs))
            raise AssertionError("HTTP upstream must not be TLS-wrapped")

    monkeypatch.setattr(proxy.ssl, "create_default_context", lambda: Ctx())

    assert proxy._connect_upstream() is raw
    assert wrapped == []


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


def test_request_observer_emits_tool_results_for_prefixed_anthropic_base(
        monkeypatch):
    """API-key providers keep their base path before Claude's /v1/messages."""
    monkeypatch.setenv("PAWFLOW_CCI_SESSION_TOKEN", "sess")
    proxy = importlib.import_module("tools.cc_interactive_proxy")
    events = []
    monkeypatch.setattr(proxy.EVENTS, "emit", events.append)
    body = json.dumps({
        "messages": [{
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": "toolu_zai",
                "content": "tool result received by GLM",
            }],
        }],
    }).encode()
    chunk = (
        b"POST /api/anthropic/v1/messages?beta=true HTTP/1.1\r\n"
        b"Host: api.z.ai\r\n"
        + f"Content-Length: {len(body)}\r\n\r\n".encode()
        + body
    )

    proxy.HTTPRequestObserver(proxy.HTTPExchangeTracker("zai")).feed(chunk)

    assert [event["type"] for event in events] == [
        "request_start", "tool_result"]
    assert events[1]["tool_use_id"] == "toolu_zai"
    assert events[1]["content"] == "tool result received by GLM"


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
        "tool_origin": "native",
    }
    assert events[2]["tool_use_id"] == "toolu_1"
    assert events[2]["content"] == "clean"


def test_request_observer_unwraps_observed_pawflow_use_tool(monkeypatch):
    monkeypatch.setenv("PAWFLOW_CCI_SESSION_TOKEN", "sess")
    proxy = importlib.import_module("tools.cc_interactive_proxy")
    events = []
    monkeypatch.setattr(proxy.EVENTS, "emit", events.append)
    body = json.dumps({
        "messages": [{
            "role": "assistant",
            "content": [{
                "type": "tool_use",
                "id": "toolu_1",
                "name": "mcp__pawflow__use_tool",
                "input": {
                    "tool_name": "bash",
                    "arguments": {"command": "git status"},
                },
            }],
        }],
    }).encode()
    chunk = (
        b"POST /v1/messages?beta=true HTTP/1.1\r\n"
        b"Host: api.anthropic.com\r\n"
        + f"Content-Length: {len(body)}\r\n\r\n".encode()
        + body
    )

    proxy.HTTPRequestObserver(proxy.HTTPExchangeTracker("r1")).feed(chunk)

    assert events[1] == {
        "type": "tool_use",
        "request_id": "r1",
        "path": "/v1/messages?beta=true",
        "tool_use_id": "toolu_1",
        "name": "bash",
        "arguments": {"command": "git status"},
        "tool_origin": "mcp",
    }


def test_request_observer_unwraps_use_tool_arguments_json_string(monkeypatch):
    # CCI advertises the payload as a string `arguments_json` (not a free-form
    # object). The observer must decode it so args render, not empty parens.
    monkeypatch.setenv("PAWFLOW_CCI_SESSION_TOKEN", "sess")
    proxy = importlib.import_module("tools.cc_interactive_proxy")
    events = []
    monkeypatch.setattr(proxy.EVENTS, "emit", events.append)
    body = json.dumps({
        "messages": [{
            "role": "assistant",
            "content": [{
                "type": "tool_use",
                "id": "toolu_2",
                "name": "mcp__pawflow__use_tool",
                "input": {
                    "tool_name": "bash",
                    "arguments_json": "{\"command\": \"git status\"}",
                },
            }],
        }],
    }).encode()
    chunk = (
        b"POST /v1/messages?beta=true HTTP/1.1\r\n"
        b"Host: api.anthropic.com\r\n"
        + f"Content-Length: {len(body)}\r\n\r\n".encode()
        + body
    )

    proxy.HTTPRequestObserver(proxy.HTTPExchangeTracker("r2")).feed(chunk)

    assert events[1] == {
        "type": "tool_use",
        "request_id": "r2",
        "path": "/v1/messages?beta=true",
        "tool_use_id": "toolu_2",
        "name": "bash",
        "arguments": {"command": "git status"},
        "tool_origin": "mcp",
    }


def test_request_observer_emits_bootstrap_native_tools(monkeypatch):
    """Every observed tool call reaches the transcript -- native ones included.

    The proxy used to drop Claude Code's own bootstrap/discovery calls (the
    `Read` of `.pawflow_cci/initial_context.md`, `ToolSearch`, `GetSchema`) on
    the grounds that they were noise. They are not: a turn that opens by
    reading its context showed an empty technical-details block, and the user
    could not tell a suppressed call from a lost one. Nothing is filtered now
    -- what the agent did is what the transcript shows.
    """
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

    assert [event["type"] for event in events] == [
        "request_start", "tool_use", "tool_result", "tool_use"]
    # The bootstrap Read is a first-class tool call, badged native.
    assert events[1]["name"] == "Read"
    assert events[1]["tool_origin"] == "native"
    assert events[1]["arguments"] == {
        "file_path": "/cc_sessions/c/a/.pawflow_cci/initial_context.md"}
    # Its result must survive too: suppressing the call used to suppress the
    # result with it, via the hidden-id set.
    assert events[2]["tool_use_id"] == "toolu_1"
    assert events[2]["content"] == "initial context"
    assert events[3]["name"] == "ToolSearch"


def test_observed_tool_origin_classifies_mcp_wrappers_vs_native():
    from tools.cc_interactive_filters import observed_tool_origin

    # PawFlow MCP bridge wrappers -> mcp badge.
    assert observed_tool_origin("mcp__pawflow__use_tool") == "mcp"
    assert observed_tool_origin("use_tool") == "mcp"
    assert observed_tool_origin("mcp__pawflow__get_tool_schema") == "mcp"
    assert observed_tool_origin("get_tool_schema") == "mcp"
    # Claude Code's own built-in tools -> native badge.
    assert observed_tool_origin("Bash") == "native"
    assert observed_tool_origin("Edit") == "native"
    assert observed_tool_origin("TaskCreate") == "native"
    assert observed_tool_origin("") == "native"


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


def test_proxy_observer_work_cannot_block_forwarding(monkeypatch):
    monkeypatch.setenv("PAWFLOW_CCI_SESSION_TOKEN", "sess")
    proxy = importlib.import_module("tools.cc_interactive_proxy")
    started = threading.Event()
    release = threading.Event()

    class BlockingObserver:
        def feed(self, _data):
            started.set()
            release.wait(timeout=5)

    chunks = [b"one", b"two"]
    src = _RecvSocket(chunks)
    dst = _SendSocket()
    thread = threading.Thread(
        target=proxy._pipe_exact,
        args=(src, dst, BlockingObserver()),
        daemon=True)

    thread.start()
    deadline = time.time() + 2
    while time.time() < deadline and dst.sent != chunks:
        time.sleep(0.01)

    assert started.wait(timeout=1) is True
    assert dst.sent == chunks
    release.set()
    thread.join(timeout=2)
    assert thread.is_alive() is False


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


def test_event_client_retries_same_event_after_send_failure(monkeypatch):
    monkeypatch.setenv("PAWFLOW_CCI_SESSION_TOKEN", "sess")
    proxy = importlib.import_module("tools.cc_interactive_proxy")
    client = proxy.EventClient("wss://events", "tok", "sess")
    client.sock = object()
    sends = []
    connects = []

    def fake_send(obj):
        if not sends:
            sends.append(("failed", obj))
            raise ConnectionError("stale socket")
        sends.append(("sent", obj))

    def fake_connect():
        connects.append(True)
        client.sock = object()

    monkeypatch.setattr(client, "_send", fake_send)
    monkeypatch.setattr(client, "connect", fake_connect)

    client.emit({"type": "sse", "event": "content_block_delta"})

    assert connects == [True]
    assert sends[0][1] == sends[1][1]
    assert sends[1][0] == "sent"
    assert sends[1][1]["type"] == "event"
    assert sends[1][1]["event"]["type"] == "sse"
    assert sends[1][1]["event"]["event"] == "content_block_delta"
    assert sends[1][1]["event"]["session_token"] == "sess"
    assert sends[1][1]["event"]["timestamp"] > 0


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


def test_wire_path_filter_accepts_prefixed_messages_endpoint(monkeypatch):
    monkeypatch.setattr(
        "tools.cc_interactive_common.WIRE_LOG_PATHS", ("/v1/messages",))
    monkeypatch.setattr("tools.cc_interactive_common.WIRE_LOG_ALL", False)

    from tools.cc_interactive_common import _wire_path_allowed

    assert _wire_path_allowed(
        "/api/anthropic/v1/messages?beta=true") is True
    assert _wire_path_allowed("/api/event_logging/v2/batch") is False


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


def test_response_observer_emits_json_text_message(monkeypatch):
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

    assert [event.get("event") for event in events] == [
        None,
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
        "message_stop",
    ]
    assert events[1]["payload"] == {
        "type": "content_block_start",
        "index": 0,
        "content_block": {"type": "text"},
    }
    assert events[2]["payload"]["delta"] == {
        "type": "text_delta",
        "text": json.dumps({"title": "Continue PawFlow session context"}),
    }


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
            self.sockopts = []

        def settimeout(self, value):
            self.timeout = value
            calls.append(value)

        def setsockopt(self, level, opt, value):
            self.sockopts.append((level, opt, value))

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
    # Nagle disabled so SSE chunks are forwarded without delayed-ACK bursts.
    assert (proxy.socket.IPPROTO_TCP, proxy.socket.TCP_NODELAY, 1) in raw.sockopts


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
    assert "consumed_at" in marker.read_text(encoding="utf-8")

    duplicate = hook._compact_input({
        "hook_event_name": "UserPromptSubmit",
        "prompt": prompt,
        "cwd": "/workspace",
    })

    assert duplicate["pawflow_injected_prompt"] is True
    assert "prompt" not in duplicate


def test_hook_matches_injected_prompt_when_claude_strips_trailing_newline(tmp_path, monkeypatch):
    hook = importlib.import_module("tools.cc_interactive_hook")
    prompt = "Read this PawFlow initial context file before answering:\n@/cc_sessions/c/a/.pawflow_cci/initial_context.md"
    sent = prompt + "\n"
    marker = tmp_path / "injected_prompts.jsonl"
    marker.write_text(json.dumps({
        "sha256": hook.hashlib.sha256(sent.encode("utf-8")).hexdigest(),
        "length": len(sent),
    }) + "\n", encoding="utf-8")
    monkeypatch.setenv("PAWFLOW_CCI_INJECTED_PROMPTS", str(marker))

    compact = hook._compact_input({
        "hook_event_name": "UserPromptSubmit",
        "prompt": prompt,
        "cwd": "/workspace",
    })

    assert compact["pawflow_injected_prompt"] is True
    assert "prompt" not in compact
    assert "consumed_at" in marker.read_text(encoding="utf-8")


def test_hook_consumes_one_fragment_from_durable_injected_prompt(tmp_path, monkeypatch):
    hook = importlib.import_module("tools.cc_interactive_hook")
    full = "PawFlow cold-session bootstrap. Read the whole context first."
    fragment = "PawFlow cold-session bootstrap."
    marker = tmp_path / "injected_prompts.jsonl"
    marker.write_text(json.dumps({
        "sha256": hook.hashlib.sha256(full.encode("utf-8")).hexdigest(),
        "length": len(full),
        "ts": hook.time.time(),
        "remaining": full,
    }) + "\n", encoding="utf-8")
    monkeypatch.setenv("PAWFLOW_CCI_INJECTED_PROMPTS", str(marker))

    compact = hook._compact_input({
        "hook_event_name": "UserPromptSubmit",
        "prompt": fragment,
    })
    duplicate = hook._compact_input({
        "hook_event_name": "UserPromptSubmit",
        "prompt": fragment,
    })

    assert compact["pawflow_injected_prompt"] is True
    assert "prompt" not in compact
    assert duplicate["pawflow_injected_prompt"] is False
    assert duplicate["prompt"] == fragment
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert fragment not in payload["remaining"]
    assert payload["fragment_seen_at"] > 0


def test_pool_marker_persists_normalized_fragment_state(tmp_path):
    pool_module = importlib.import_module("core.claude_code_interactive_pool")
    state = type("State", (), {"workdir": str(tmp_path)})()
    prompt = "PawFlow cold-session bootstrap.\nRead   the whole context first.\n"

    pool_module.InteractiveClaudeCodePool._remember_injected_prompt(
        state, prompt)

    marker = tmp_path / ".pawflow_cci" / "injected_prompts.jsonl"
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["remaining"] == (
        "PawFlow cold-session bootstrap. Read the whole context first.")
    assert payload["sha256"] == pool_module.hashlib.sha256(
        prompt.encode("utf-8")).hexdigest()


def test_hook_never_claims_short_fragment_of_injected_prompt(tmp_path, monkeypatch):
    hook = importlib.import_module("tools.cc_interactive_hook")
    full = "PawFlow cold-session bootstrap. Read the whole context first."
    marker = tmp_path / "injected_prompts.jsonl"
    marker.write_text(json.dumps({
        "sha256": hook.hashlib.sha256(full.encode("utf-8")).hexdigest(),
        "length": len(full),
        "ts": hook.time.time(),
        "remaining": full,
    }) + "\n", encoding="utf-8")
    monkeypatch.setenv("PAWFLOW_CCI_INJECTED_PROMPTS", str(marker))

    compact = hook._compact_input({
        "hook_event_name": "UserPromptSubmit",
        "prompt": "PawFlow",
    })

    assert compact["pawflow_injected_prompt"] is False
    assert compact["prompt"] == "PawFlow"


def test_hook_does_not_claim_fragment_after_its_burst(tmp_path, monkeypatch):
    hook = importlib.import_module("tools.cc_interactive_hook")
    full = "PawFlow cold-session bootstrap. Read the whole context first."
    fragment = "PawFlow cold-session bootstrap."
    marker = tmp_path / "injected_prompts.jsonl"
    marker.write_text(json.dumps({
        "sha256": hook.hashlib.sha256(full.encode("utf-8")).hexdigest(),
        "length": len(full),
        "ts": hook.time.time() - hook._INJECTED_FRAGMENT_BURST_SECONDS - 1,
        "remaining": full,
    }) + "\n", encoding="utf-8")
    monkeypatch.setenv("PAWFLOW_CCI_INJECTED_PROMPTS", str(marker))

    compact = hook._compact_input({
        "hook_event_name": "UserPromptSubmit",
        "prompt": fragment,
    })

    assert compact["pawflow_injected_prompt"] is False
    assert compact["prompt"] == fragment


def test_exact_hook_match_spends_fragment_state_but_stays_idempotent(tmp_path, monkeypatch):
    hook = importlib.import_module("tools.cc_interactive_hook")
    full = "PawFlow cold-session bootstrap. Read the whole context first."
    marker = tmp_path / "injected_prompts.jsonl"
    marker.write_text(json.dumps({
        "sha256": hook.hashlib.sha256(full.encode("utf-8")).hexdigest(),
        "length": len(full),
        "ts": hook.time.time(),
        "remaining": full,
    }) + "\n", encoding="utf-8")
    monkeypatch.setenv("PAWFLOW_CCI_INJECTED_PROMPTS", str(marker))

    exact = hook._compact_input({
        "hook_event_name": "UserPromptSubmit",
        "prompt": full,
    })
    fragment = hook._compact_input({
        "hook_event_name": "UserPromptSubmit",
        "prompt": "PawFlow cold-session bootstrap.",
    })
    duplicate_exact = hook._compact_input({
        "hook_event_name": "UserPromptSubmit",
        "prompt": full,
    })

    assert exact["pawflow_injected_prompt"] is True
    assert fragment["pawflow_injected_prompt"] is False
    assert fragment["prompt"] == "PawFlow cold-session bootstrap."
    assert duplicate_exact["pawflow_injected_prompt"] is True


def test_hook_keeps_manual_user_prompt(monkeypatch):
    hook = importlib.import_module("tools.cc_interactive_hook")
    monkeypatch.delenv("PAWFLOW_CCI_INJECTED_PROMPTS", raising=False)

    compact = hook._compact_input({
        "hook_event_name": "UserPromptSubmit",
        "prompt": "Manual tmux prompt",
    })

    assert compact["pawflow_injected_prompt"] is False
    assert compact["prompt"] == "Manual tmux prompt"


def test_hook_keeps_manual_prompt_when_marker_is_missing(monkeypatch):
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
    assert "pawflow_managed_prompt" not in compact
    assert "pawflow_injected_prompt_missing" not in compact
    assert compact["prompt_sha256"] == hook.hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    assert compact["prompt"] == prompt
