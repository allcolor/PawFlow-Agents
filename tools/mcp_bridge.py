#!/usr/bin/env python3
"""MCP Bridge — connects Claude Code to PawFlow tools via WebSocket relay.

Dual role:
  - MCP stdio server (for Claude Code) — exposes get_tool_schema + use_tool
  - WebSocket client (to PawFlow server) — executes tools via tool relay

Same pattern as the filesystem relay: connects to PawFlow server via
WebSocket with a shared API key. All tool execution happens server-side.

Env vars (set by claude-code LLM provider):
  PAWFLOW_TOOL_RELAY_URL: ws://host:port/ws/tools
  PAWFLOW_TOOL_RELAY_TOKEN: shared API key
  PAWFLOW_USER_ID: user ID for tool execution context
  PAWFLOW_CONVERSATION_ID: conversation ID for context
  PAWFLOW_AGENT_NAME: agent name

Uses JSON-RPC 2.0 over stdio (MCP standard).
"""

import hashlib
import json
import os
import base64
import struct
import socket
import ssl
import sys
import threading
import uuid


# ── WebSocket client to PawFlow server ──────────────────────────

class ToolRelayClient:
    """Minimal sync WebSocket client to PawFlow tool relay."""

    def __init__(self, url: str, token: str, user_id: str = "",
                 conversation_id: str = "", agent_name: str = ""):
        self._url = url
        self._token = token
        self._user_id = user_id
        self._conversation_id = conversation_id
        self._agent_name = agent_name
        self._sock = None
        self._lock = threading.Lock()

    def connect(self):
        """Connect and register with the tool relay service."""
        from urllib.parse import urlparse
        parsed = urlparse(self._url)
        host = parsed.hostname or "localhost"
        port = parsed.port or (443 if parsed.scheme == "wss" else 9091)
        path = parsed.path or "/ws/tools"
        use_tls = parsed.scheme == "wss"

        # TCP connect with keepalive
        sock = socket.create_connection((host, port), timeout=10)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        # Windows TCP keepalive: probe after 30s, interval 10s
        try:
            sock.ioctl(socket.SIO_KEEPALIVE_VALS, (1, 30000, 10000))
        except (AttributeError, OSError):
            pass  # non-Windows or unsupported
        if use_tls:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            sock = ctx.wrap_socket(sock, server_hostname=host)
        self._sock = sock

        # WebSocket upgrade
        ws_key = base64.b64encode(os.urandom(16)).decode()
        upgrade = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {ws_key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"\r\n"
        )
        sock.sendall(upgrade.encode("latin-1"))

        # Read HTTP response
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError("Server closed during WS upgrade")
            resp += chunk

        if b"101" not in resp.split(b"\r\n")[0]:
            raise ConnectionError(f"WS upgrade failed: {resp[:200].decode('latin-1', errors='replace')}")

        # Register
        self._ws_send(json.dumps({
            "type": "register",
            "token": self._token,
            "relay_id": f"mcp-bridge-{self._agent_name or 'default'}",
            "user_id": self._user_id,
            "conversation_id": self._conversation_id,
            "agent_name": self._agent_name,
        }))

        # Read registration response
        msg = json.loads(self._ws_recv())
        if msg.get("type") == "error":
            raise ConnectionError(f"Registration failed: {msg.get('message', '?')}")
        _log(f"Connected to {self._url}, registered as user={self._user_id}")

    def _ensure_connected(self):
        """Reconnect if the connection is dead."""
        if self._sock:
            try:
                # Quick liveness check
                self._sock.getpeername()
                return
            except Exception:
                pass
        _log("Reconnecting to tool relay...")
        try:
            self.close()
            self.connect()
        except Exception as e:
            _log(f"Reconnect failed: {e}")
            raise

    def request(self, method: str, **kwargs) -> any:
        """Send a request and wait for the response.

        Auto-reconnects on connection failure with backoff (up to 3 retries).
        The caller must never see transient connection errors.
        """
        import time as _time
        last_err = None
        for attempt in range(4):
            try:
                self._ensure_connected()
                return self._do_request(method, **kwargs)
            except (ConnectionError, OSError, BrokenPipeError) as e:
                last_err = e
                self._sock = None  # force reconnect
                if attempt < 3:
                    delay = min(1.0 * (2 ** attempt), 8.0)
                    _log(f"Connection lost (attempt {attempt + 1}/4), "
                         f"retrying in {delay}s: {e}")
                    _time.sleep(delay)
                    continue
                _log(f"Connection failed after 4 attempts: {e}")
                raise ConnectionError(
                    f"Tool relay unavailable after 4 attempts: {last_err}"
                ) from last_err

    def _do_request(self, method: str, **kwargs) -> any:
        request_id = uuid.uuid4().hex[:12]
        payload = {"type": "request", "request_id": request_id,
                   "method": method, **kwargs}
        with self._lock:
            self._ws_send(json.dumps(payload))
            while True:
                raw = self._ws_recv()
                msg = json.loads(raw)
                if msg.get("type") == "ping":
                    self._ws_send(json.dumps({"type": "pong"}))
                    continue
                if msg.get("request_id") == request_id:
                    if msg.get("type") == "error":
                        return f"Error: {msg.get('error', 'unknown')}"
                    return msg.get("data")
                _log(f"Unexpected WS message: {raw[:200]}")

    def close(self):
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    # ── WS frame helpers ──

    def _ws_send(self, text: str):
        data = text.encode("utf-8")
        # Client frames MUST be masked
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        length = len(data)
        frame = bytes([0x81])  # FIN + text opcode
        if length < 126:
            frame += bytes([0x80 | length])
        elif length < 65536:
            frame += bytes([0x80 | 126]) + struct.pack("!H", length)
        else:
            frame += bytes([0x80 | 127]) + struct.pack("!Q", length)
        frame += mask + masked
        self._sock.sendall(frame)

    def _ws_recv(self) -> str:
        hdr = self._recv_exact(2)
        opcode = hdr[0] & 0x0F
        length = hdr[1] & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._recv_exact(8))[0]
        data = self._recv_exact(length)
        if opcode == 0x09:  # ping
            # Auto-pong
            self._ws_send_raw(data, opcode=0x0A)
            return self._ws_recv()
        if opcode == 0x08:  # close
            raise ConnectionError("Server closed WebSocket")
        return data.decode("utf-8")

    def _ws_send_raw(self, data: bytes, opcode: int):
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        frame = bytes([0x80 | opcode])
        length = len(data)
        if length < 126:
            frame += bytes([0x80 | length])
        elif length < 65536:
            frame += bytes([0x80 | 126]) + struct.pack("!H", length)
        else:
            frame += bytes([0x80 | 127]) + struct.pack("!Q", length)
        frame += mask + masked
        self._sock.sendall(frame)

    def _recv_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("Server closed connection")
            buf += chunk
        return buf


# ── MCP stdio server ────────────────────────────────────────────

def _respond(req_id, result=None, error=None):
    resp = {"jsonrpc": "2.0", "id": req_id}
    if error:
        resp["error"] = error
    else:
        resp["result"] = result
    sys.stdout.write(json.dumps(resp) + "\n")
    sys.stdout.flush()


def _log(msg):
    sys.stderr.write(f"[mcp-bridge] {msg}\n")
    sys.stderr.flush()


def main():
    relay_url = os.environ.get("PAWFLOW_TOOL_RELAY_URL", "")
    relay_token = os.environ.get("PAWFLOW_TOOL_RELAY_TOKEN", "")
    user_id = os.environ.get("PAWFLOW_USER_ID", "")
    conv_id = os.environ.get("PAWFLOW_CONVERSATION_ID", "")
    agent_name = os.environ.get("PAWFLOW_AGENT_NAME", "")

    _log(f"Starting MCP bridge: relay={relay_url}, user={user_id}, "
         f"conv={conv_id}, agent={agent_name}")

    # Connect to PawFlow tool relay
    client = None
    if relay_url and relay_token:
        try:
            client = ToolRelayClient(
                relay_url, relay_token, user_id, conv_id, agent_name)
            client.connect()
        except Exception as e:
            _log(f"Failed to connect to tool relay: {e}")
            client = None
    else:
        _log("No PAWFLOW_TOOL_RELAY_URL/TOKEN — tools unavailable")

    # MCP tools: get_tool_schema + use_tool (same lazy tools pattern)
    mcp_tools = [
        {
            "name": "get_tool_schema",
            "description": (
                "Get the full parameter schema for a tool, or list all available tools. "
                "Call with tool_name to get the schema. Call without to list all tools."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "tool_name": {"type": "string",
                                  "description": "Name of the tool to inspect (omit to list all)"},
                },
            },
        },
        {
            "name": "use_tool",
            "description": (
                "Execute a tool by name with the given arguments. "
                "Call get_tool_schema first to know the parameters."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "tool_name": {"type": "string",
                                  "description": "Name of the tool to execute"},
                    "arguments": {"type": "object",
                                  "description": "Arguments to pass to the tool"},
                },
                "required": ["tool_name", "arguments"],
            },
        },
    ]

    _log(f"MCP bridge ready: {len(mcp_tools)} tools, "
         f"relay={'connected' if client else 'unavailable'}")

    # MCP stdio loop
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = request.get("method", "")
        req_id = request.get("id")
        params = request.get("params", {})

        _log(f"<< {method} (id={req_id})")

        if method == "initialize":
            _respond(req_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "pawflow-mcp-bridge", "version": "2.0"},
            })
        elif method == "notifications/initialized":
            _log("MCP initialized")
        elif method == "tools/list":
            _respond(req_id, {"tools": mcp_tools})
        elif method == "tools/call":
            name = params.get("name", "")
            args = params.get("arguments", {})
            # Robust: LLM sometimes double-encodes arguments as a JSON string
            for _ in range(3):
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except (json.JSONDecodeError, TypeError):
                        break
                else:
                    break
            _log(f"CALL {name}({json.dumps(args)[:200]})")

            if not client:
                result = "Error: tool relay not connected"
            elif name == "get_tool_schema":
                tool_name = args.get("tool_name", "")
                if tool_name:
                    result = client.request("get_tool_schema",
                                            tool_name=tool_name)
                else:
                    result = client.request("list_tools")
                if isinstance(result, (dict, list)):
                    result = json.dumps(result, indent=2, ensure_ascii=False)
                else:
                    result = str(result)
            elif name == "use_tool":
                tool_name = args.get("tool_name", "")
                tool_args = args.get("arguments", {})
                for _ in range(3):
                    if not isinstance(tool_args, str):
                        break
                    try:
                        tool_args = json.loads(tool_args)
                    except (json.JSONDecodeError, TypeError):
                        break
                result = client.request("execute_tool",
                                        tool_name=tool_name,
                                        arguments=tool_args)
                result = str(result) if result else "(no output)"
            else:
                result = f"Error: unknown tool '{name}'"

            _log(f"RESULT {name}: {str(result)[:100]}")
            _respond(req_id, {
                "content": [{"type": "text", "text": str(result)}],
                "isError": str(result).startswith("Error:"),
            })
        else:
            _respond(req_id, None,
                     error={"code": -32601, "message": f"Unknown method: {method}"})

    # Cleanup
    if client:
        client.close()


if __name__ == "__main__":
    main()
