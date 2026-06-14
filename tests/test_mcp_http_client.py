"""Tests for the Streamable HTTP MCP client (core.mcp_http_client).

Spins up a real localhost HTTP server that mimics an MCP Streamable HTTP
endpoint: it requires an ``initialize`` handshake, issues an
``Mcp-Session-Id``, answers ``tools/list`` over SSE and ``tools/call`` over
plain JSON, and can force a one-shot session expiry (HTTP 404) to exercise the
re-initialize-and-retry path.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from core.mcp_http_client import MCPHttpClient, flatten_tool_content


class _State:
    def __init__(self):
        self.session_counter = 0
        self.sessions = set()
        self.expire_once = False
        self.expired_done = False
        self.inits = 0


def _make_handler(state):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send_json(self, obj, status=200, session=None):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            if session:
                self.send_header("Mcp-Session-Id", session)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_sse(self, obj, session=None):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            if session:
                self.send_header("Mcp-Session-Id", session)
            self.end_headers()
            frame = f"event: message\ndata: {json.dumps(obj)}\n\n"
            self.wfile.write(frame.encode("utf-8"))

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b""
            try:
                rpc = json.loads(raw.decode("utf-8"))
            except Exception:
                self._send_json({"error": "bad json"}, status=400)
                return
            method = rpc.get("method")
            rid = rpc.get("id")
            sid = self.headers.get("Mcp-Session-Id")
            accept = self.headers.get("Accept", "")

            # Conformance: the client must negotiate both content types.
            assert "text/event-stream" in accept and "application/json" in accept

            if method == "initialize":
                state.inits += 1
                state.session_counter += 1
                new_sid = f"sess-{state.session_counter}"
                state.sessions.add(new_sid)
                self._send_json({
                    "jsonrpc": "2.0", "id": rid,
                    "result": {"protocolVersion": "2025-03-26",
                               "serverInfo": {"name": "fake"},
                               "capabilities": {}},
                }, session=new_sid)
                return

            if method == "notifications/initialized":
                self.send_response(202)
                self.end_headers()
                return

            # All other methods require a valid session.
            if not sid or sid not in state.sessions:
                self._send_json({"error": "no session"}, status=400)
                return

            # One-shot forced expiry to exercise re-init + retry.
            if state.expire_once and not state.expired_done:
                state.expired_done = True
                state.sessions.discard(sid)
                self.send_response(404)
                self.end_headers()
                return

            if method == "tools/list":
                # Answer over SSE to exercise the stream parser.
                self._send_sse({
                    "jsonrpc": "2.0", "id": rid,
                    "result": {"tools": [
                        {"name": "echo",
                         "description": "echo back",
                         "inputSchema": {"type": "object"}},
                    ]},
                })
                return

            if method == "tools/call":
                params = rpc.get("params", {})
                args = params.get("arguments", {})
                self._send_json({
                    "jsonrpc": "2.0", "id": rid,
                    "result": {"content": [
                        {"type": "text",
                         "text": f"got:{args.get('msg', '')}"}],
                              "isError": False},
                })
                return

            self._send_json({"jsonrpc": "2.0", "id": rid,
                             "error": {"message": "unknown method"}})

    return Handler


@pytest.fixture
def mcp_server():
    state = _State()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(state))
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    host, port = server.server_address
    url = f"http://{host}:{port}/mcp"
    try:
        yield url, state
    finally:
        server.shutdown()
        server.server_close()


def test_discover_lists_tools_over_sse(mcp_server):
    url, state = mcp_server
    client = MCPHttpClient(url, timeout=5)
    tools = client.list_tools()
    assert [t["name"] for t in tools] == ["echo"]
    assert tools[0]["description"] == "echo back"
    # initialize happened exactly once (lazy handshake).
    assert state.inits == 1


def test_call_tool_returns_flattened_text(mcp_server):
    url, _ = mcp_server
    client = MCPHttpClient(url, timeout=5)
    result = client.call_tool("echo", {"msg": "hi"})
    assert result.get("isError") is False
    assert flatten_tool_content(result) == "got:hi"


def test_session_is_reused_across_calls(mcp_server):
    url, state = mcp_server
    client = MCPHttpClient(url, timeout=5)
    client.list_tools()
    client.call_tool("echo", {"msg": "a"})
    client.call_tool("echo", {"msg": "b"})
    # Only one handshake for three operations.
    assert state.inits == 1


def test_expired_session_triggers_reinit_and_retry(mcp_server):
    url, state = mcp_server
    state.expire_once = True
    client = MCPHttpClient(url, timeout=5)
    # First call: server returns 404 once -> client re-initializes and retries.
    result = client.call_tool("echo", {"msg": "z"})
    assert flatten_tool_content(result) == "got:z"
    assert state.inits == 2


def test_discover_mcp_tools_entrypoint(mcp_server):
    url, _ = mcp_server
    from core.tool_registry import discover_mcp_tools
    tools = discover_mcp_tools(url, timeout=5)
    assert [t["name"] for t in tools] == ["echo"]


def test_mcp_tool_handler_http_execute(mcp_server):
    url, _ = mcp_server
    from core.handlers.agent_tools import MCPToolHandler
    h = MCPToolHandler(
        tool_name="echo", tool_description="", tool_parameters={},
        server_url=url, transport="http", server_id="fake")
    out = h.execute({"msg": "world"})
    assert out == "got:world"
