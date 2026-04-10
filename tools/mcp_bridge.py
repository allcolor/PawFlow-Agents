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
        sock = socket.create_connection((host, port))
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

        On connection failure: reconnect ONCE and retry with the same
        request_id. Server caches results, so retrying the same ID
        returns the already-computed result without re-executing.
        """
        import time as _time
        request_id = uuid.uuid4().hex[:12]
        for attempt in range(3):
            try:
                self._ensure_connected()
                return self._do_request(method, request_id, **kwargs)
            except (ConnectionError, OSError, BrokenPipeError) as e:
                _log(f"Connection lost during request (attempt {attempt + 1}/3): {e}")
                # Close cleanly before reconnecting
                try:
                    self.close()
                except Exception:
                    pass
                self._sock = None
                if attempt < 2:
                    _time.sleep(2)
                    continue
                raise ConnectionError(f"Tool relay unavailable: {e}") from e

    def _do_request(self, method: str, request_id: str, **kwargs) -> any:
        payload = {"type": "request", "request_id": request_id,
                   "method": method, **kwargs}
        if method == "execute_tool":
            _log(f"→ RELAY {method} {kwargs.get('tool_name','?')} "
                 f"args={json.dumps(kwargs.get('arguments',''))[:300]} "
                 f"[req={request_id}]")
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


_log_file = None

def _log(msg):
    global _log_file
    sys.stderr.write(f"[mcp-bridge] {msg}\n")
    sys.stderr.flush()
    # Also write to file on workspace (readable from host)
    try:
        if _log_file is None:
            import os
            _log_dir = os.environ.get("CLAUDE_CONFIG_DIR", "/workspace")
            _log_file = open(os.path.join(_log_dir, "mcp_bridge.log"), "a", encoding="utf-8")
        _log_file.write(f"[mcp-bridge] {msg}\n")
        _log_file.flush()
    except Exception:
        pass


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

    # Preload tool schemas — cache required params for phantom call detection
    _required_cache = {}  # tool_name → set of required param names
    if client:
        try:
            all_tools = client.request("list_tools")
            if isinstance(all_tools, str):
                all_tools = json.loads(all_tools)
            if isinstance(all_tools, list):
                for tool_info in all_tools:
                    tname = tool_info.get("name", "")
                    if not tname:
                        continue
                    try:
                        schema = client.request("get_tool_schema", tool_name=tname)
                        if isinstance(schema, str):
                            schema = json.loads(schema)
                        if isinstance(schema, dict):
                            required = set(schema.get("parameters", {}).get("required", []))
                            _required_cache[tname] = required
                    except Exception:
                        pass
                _log(f"Preloaded schemas: {len(_required_cache)} tools, "
                     f"{sum(1 for r in _required_cache.values() if r)} with required args")
        except Exception as e:
            _log(f"Schema preload failed: {e}")

    _log(f"MCP bridge ready: {len(mcp_tools)} tools, "
         f"relay={'connected' if client else 'unavailable'}")

    def _has_required_args(tool_name):
        """Check if a tool has required parameters (from preloaded cache)."""
        return bool(_required_cache.get(tool_name))

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
            # Log the RAW stdin line for diagnosis of empty args
            if name == "use_tool" and isinstance(args, dict):
                _inner = args.get("arguments", {})
                if not _inner or _inner == {} or _inner == "{}":
                    _log(f"EMPTY INNER ARGS! raw stdin line: {line[:1000]}")
                    _log(f"EMPTY INNER ARGS! parsed args: {json.dumps(args)[:500]}")
            # Robust: LLM sometimes double-encodes arguments as a JSON string
            for _ in range(3):
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except (json.JSONDecodeError, TypeError):
                        break
                else:
                    break
            _log(f"CALL {name}({json.dumps(args)[:300]})")

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
                # Map hallucinated/legacy tool names to real PawFlow names
                _TOOL_ALIASES = {
                    # CC hallucinations (common LLM mistakes)
                    "run_command": "bash", "shell": "bash", "execute": "bash",
                    "run": "bash", "terminal": "bash", "exec": "bash",
                    "search": "grep", "find_files": "glob", "list_files": "glob",
                    "cat": "read", "view": "read", "open": "read",
                    "create_file": "write", "save": "write",
                    "replace": "edit", "patch": "edit", "modify": "edit",
                    "web_fetch": "fetch", "http": "fetch",
                    # CC official legacy aliases
                    "Task": "Agent", "Brief": "SendUserMessage",
                    "KillShell": "TaskStop",
                    "AgentOutputTool": "TaskOutput", "BashOutputTool": "TaskOutput",
                }
                if tool_name in _TOOL_ALIASES:
                    _real = _TOOL_ALIASES[tool_name]
                    _log(f"USE_TOOL alias: {tool_name} → {_real}")
                    tool_name = _real
                tool_args_raw = args.get("arguments", {})
                _log(f"USE_TOOL {tool_name} raw_type={type(tool_args_raw).__name__} raw={json.dumps(tool_args_raw, default=str)[:300]}")
                # Unwrap JSON string arguments (CC sometimes double/triple-encodes)
                tool_args = tool_args_raw
                _decode_failed = False
                _unwrap_passes = 0
                for _ in range(3):
                    if not isinstance(tool_args, str):
                        break
                    try:
                        _prev = tool_args
                        tool_args = json.loads(tool_args)
                        _unwrap_passes += 1
                    except json.JSONDecodeError as _je:
                        # "Extra data" = valid JSON followed by junk (e.g. CC appends </invoke>)
                        # Use raw_decode to parse only the first JSON object
                        if "Extra data" in str(_je):
                            try:
                                tool_args, _ = json.JSONDecoder().raw_decode(tool_args)
                                _unwrap_passes += 1
                                _log(f"USE_TOOL {tool_name} raw_decode OK (stripped trailing junk)")
                            except (json.JSONDecodeError, TypeError) as _je2:
                                _log(f"USE_TOOL {tool_name} raw_decode also FAILED: {_je2}")
                                _decode_failed = True
                                break
                        else:
                            # Last resort: json-repair for structurally broken JSON
                            # (e.g. unescaped quotes in bash commands)
                            try:
                                from json_repair import repair_json
                                _repaired = repair_json(tool_args)
                                tool_args = json.loads(_repaired)
                                _unwrap_passes += 1
                                _log(f"USE_TOOL {tool_name} json-repair OK (fixed malformed JSON)")
                            except Exception as _je3:
                                _log(f"USE_TOOL {tool_name} ALL decode attempts FAILED: "
                                     f"json.loads={_je} json-repair={_je3} value={str(tool_args)[:200]}")
                                _decode_failed = True
                                break
                    except TypeError as _je:
                        _log(f"USE_TOOL {tool_name} JSON decode TypeError: {_je}")
                        _decode_failed = True
                        break
                if _unwrap_passes > 0:
                    _log(f"USE_TOOL {tool_name} unwrapped {_unwrap_passes} pass(es): {type(tool_args_raw).__name__} → {type(tool_args).__name__}")
                # Decode failed on non-empty input → error (don't silently send {})
                if _decode_failed and tool_args_raw and tool_args_raw != {} and tool_args_raw != "{}":
                    result = (f"Error: failed to decode arguments for {tool_name}. "
                              f"Arguments must be a JSON object, got: {str(tool_args_raw)[:200]}")
                # Still a string after unwrap → same problem
                elif isinstance(tool_args, str):
                    _log(f"USE_TOOL {tool_name} args still string after unwrap: {tool_args[:200]}")
                    result = (f"Error: arguments for {tool_name} must be a JSON object, "
                              f"got string: {tool_args[:200]}")
                else:
                    _log(f"USE_TOOL {tool_name} final_type={type(tool_args).__name__} final={json.dumps(tool_args, default=str)[:300]}")
                    result = client.request("execute_tool",
                                            tool_name=tool_name,
                                            arguments=tool_args)
                    result = str(result) if result else "(no output)"
            else:
                result = f"Error: unknown tool '{name}'"

            _log(f"RESULT {name}: {str(result)[:100]}")
            # Sanitize tool result (strip invisible/malicious unicode)
            try:
                from core.sanitization import sanitize_unicode
                result = sanitize_unicode(str(result)) if result else result
            except ImportError:
                pass  # core.sanitization not available in Docker container
            # Convert __image_data__ markers to MCP image content blocks
            result_str = str(result)
            if "__image_data__:" in result_str:
                content = []
                for rline in result_str.split("\n"):
                    if rline.startswith("__image_data__:"):
                        parts = rline.split(":", 2)
                        if len(parts) == 3:
                            mime, b64 = parts[1], parts[2]
                            content.append({
                                "type": "image",
                                "data": b64,
                                "mimeType": mime,
                            })
                    elif rline.strip():
                        content.append({"type": "text", "text": rline})
                if not content:
                    content = [{"type": "text", "text": result_str}]
            else:
                content = [{"type": "text", "text": result_str}]
            _respond(req_id, {
                "content": content,
                "isError": result_str.startswith("Error:"),
            })
        else:
            _respond(req_id, None,
                     error={"code": -32601, "message": f"Unknown method: {method}"})

    # Cleanup
    if client:
        client.close()


if __name__ == "__main__":
    main()
