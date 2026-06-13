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
import logging

import hashlib
import json
import os
import base64
import struct
import socket
import ssl
import sys
import threading
import time
import uuid


TOOL_RELAY_RETRY_ATTEMPTS = 5
TOOL_RELAY_RETRY_DELAY_SECONDS = 5.0

# Aliases for tool names a model may invent. Applied both to use_tool's
# `tool_name` and to direct (non-wrapper) MCP calls, which are rerouted through
# use_tool. We expose ONLY use_tool + get_tool_schema as MCP tools — these are
# name aliases, NOT additional exposed tools.
_BRIDGE_TOOL_ALIASES = {
    "run_command": "bash", "shell": "bash", "execute": "bash",
    "run": "bash", "terminal": "bash", "exec": "bash",
    "find_files": "glob", "list_files": "glob",
    "list_directory": "list_dir", "ls": "list_dir",
    "read_file": "read", "cat": "read", "view": "read", "open": "read",
    "create_file": "write", "save": "write",
    "replace": "edit", "patch": "edit", "modify": "edit",
    "web_fetch": "fetch", "http": "fetch",
    # Image/vision: route to `see` (view/analyze). `view` stays -> read
    # (text), so only the unambiguous image-* names map here.
    "image": "see", "image_view": "see", "view_image": "see",
    "Task": "Agent", "Brief": "SendUserMessage",
    "KillShell": "TaskStop",
    "AgentOutputTool": "TaskOutput", "BashOutputTool": "TaskOutput",
}


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
        self._send_lock = threading.Lock()
        self._pending = {}
        self._pending_lock = threading.Lock()
        self._reader_thread = None
        self._reader_error = None

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

        # Internal-auth cookie: the CC container has no user session;
        # the server mints a short-lived token into PAWFLOW_INTERNAL_TOKEN
        # on every MCP config write. Presented here, the main listener
        # bypasses the private-gateway + session checks for /ws/tools/*.
        internal = os.environ.get("PAWFLOW_INTERNAL_TOKEN", "")
        cookie_line = (
            f"Cookie: pawflow_internal={internal}\r\n" if internal else "")

        # WebSocket upgrade
        ws_key = base64.b64encode(os.urandom(16)).decode()
        upgrade = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {ws_key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"{cookie_line}"
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
        self._start_reader()
        _log(f"Connected to {self._url}, registered as user={self._user_id}")

    def _start_reader(self):
        self._reader_error = None
        def _reader():
            try:
                while self._sock:
                    raw = self._ws_recv()
                    msg = json.loads(raw)
                    if msg.get("type") == "ping":
                        with self._send_lock:
                            self._ws_send(json.dumps({"type": "pong"}))
                        continue
                    rid = msg.get("request_id")
                    if rid:
                        with self._pending_lock:
                            waiter = self._pending.get(rid)
                        if waiter:
                            waiter["msg"] = msg
                            waiter["event"].set()
                            continue
                    _log(f"Unexpected WS message: {raw[:200]}")
            except Exception as e:
                self._reader_error = e
                with self._pending_lock:
                    pending = list(self._pending.values())
                for waiter in pending:
                    waiter["error"] = e
                    waiter["event"].set()
        self._reader_thread = threading.Thread(
            target=_reader, daemon=True, name="mcp-bridge-ws-reader")
        self._reader_thread.start()

    def _ensure_connected(self):
        """Reconnect if the connection is dead."""
        if self._sock:
            try:
                # Quick liveness check
                self._sock.getpeername()
                if self._reader_error:
                    raise ConnectionError(f"reader stopped: {self._reader_error}")
                return
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        _log("Reconnecting to tool relay...")
        try:
            self.close()
            self.connect()
        except Exception as e:
            _log(f"Reconnect failed: {e}")
            raise

    def request(self, method: str, **kwargs) -> any:
        """Send a request and wait for the response.

        On connection failure: reconnect and retry with the same
        request_id. Server caches results, so retrying the same ID
        returns the already-computed result without re-executing.
        """
        import time as _time
        request_id = uuid.uuid4().hex[:12]
        for attempt in range(TOOL_RELAY_RETRY_ATTEMPTS):
            try:
                self._ensure_connected()
                return self._do_request(method, request_id, **kwargs)
            except (ConnectionError, OSError, BrokenPipeError) as e:
                _log(
                    f"Connection lost during request "
                    f"(attempt {attempt + 1}/{TOOL_RELAY_RETRY_ATTEMPTS}): {e}")
                # Close cleanly before reconnecting
                try:
                    self.close()
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                self._sock = None
                if attempt < TOOL_RELAY_RETRY_ATTEMPTS - 1:
                    _time.sleep(TOOL_RELAY_RETRY_DELAY_SECONDS)
                    continue
                raise ConnectionError(f"Tool relay unavailable: {e}") from e

    def _do_request(self, method: str, request_id: str, **kwargs) -> any:
        request_started = time.perf_counter()
        send_ms = 0.0
        payload = {"type": "request", "request_id": request_id,
                   "method": method, **kwargs}
        if method == "execute_tool":
            _log(f"→ RELAY {method} {kwargs.get('tool_name','?')} "
                 f"args={json.dumps(kwargs.get('arguments',''))[:300]} "
                 f"[req={request_id}]")
        waiter = {"event": threading.Event(), "msg": None, "error": None}
        with self._pending_lock:
            self._pending[request_id] = waiter
        try:
            with self._send_lock:
                send_started = time.perf_counter()
                self._ws_send(json.dumps(payload))
                send_ms = (time.perf_counter() - send_started) * 1000
            waiter["event"].wait()
            done_at = time.perf_counter()
            if waiter.get("error"):
                if method == "execute_tool":
                    _log(f"← RELAY {method} {kwargs.get('tool_name','?')} "
                         f"failed [req={request_id}] "
                         f"bridge_ms={(done_at - request_started) * 1000:.1f} "
                         f"send_ms={send_ms:.1f} err={waiter['error']}")
                raise ConnectionError(str(waiter["error"]))
            msg = waiter.get("msg") or {}
            if method == "execute_tool":
                data = msg.get("data")
                try:
                    result_len = len(data) if isinstance(data, str) else len(json.dumps(data, default=str))
                except Exception:
                    result_len = len(str(data))
                _log(f"← RELAY {method} {kwargs.get('tool_name','?')} "
                     f"[req={request_id}] "
                     f"bridge_ms={(done_at - request_started) * 1000:.1f} "
                     f"send_ms={send_ms:.1f} "
                     f"return_wait_ms={(done_at - request_started) * 1000 - send_ms:.1f} "
                     f"result_len={result_len} msg_type={msg.get('type', '?')}")
            if msg.get("type") == "error":
                return f"Error: {msg.get('error', 'unknown')}"
            return msg.get("data")
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)

    def close(self):
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            self._sock = None
        with self._pending_lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for waiter in pending:
            waiter["error"] = ConnectionError("Tool relay connection closed")
            waiter["event"].set()

    # ── WS frame helpers (thin wrappers around tools.ws_frame) ──

    def _ws_send(self, text: str):
        from pawflow_relay.ws_frame import ws_send
        ws_send(self._sock, text.encode("utf-8"), opcode=0x01)

    def _ws_recv(self) -> str:
        from pawflow_relay.ws_frame import ws_send, ws_recv
        opcode, payload = ws_recv(self._sock)
        if opcode == 0x09:  # ping → auto-pong, then read next frame
            ws_send(self._sock, payload, opcode=0x0A)
            return self._ws_recv()
        if opcode == 0x08:
            raise ConnectionError("Server closed WebSocket")
        return payload.decode("utf-8")


# ── MCP stdio server ────────────────────────────────────────────

_respond_lock = threading.Lock()
_active_call_threads = set()
_active_call_threads_lock = threading.Lock()


def _respond(req_id, result=None, error=None):
    resp = {"jsonrpc": "2.0", "id": req_id}
    if error:
        resp["error"] = error
    else:
        resp["result"] = result
    with _respond_lock:
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


def _track_tool_call_thread(thread: threading.Thread) -> None:
    with _active_call_threads_lock:
        _active_call_threads.add(thread)


def _untrack_tool_call_thread(thread: threading.Thread) -> None:
    with _active_call_threads_lock:
        _active_call_threads.discard(thread)


def _wait_for_active_tool_calls() -> None:
    while True:
        with _active_call_threads_lock:
            threads = [t for t in _active_call_threads if t.is_alive()]
            _active_call_threads.intersection_update(threads)
        if not threads:
            return
        _log(f"Waiting for {len(threads)} active MCP tool call(s) before shutdown")
        for thread in threads:
            thread.join(timeout=0.5)


_log_file = None

def _autoclose_truncated_json(s: str, max_appends: int = 4) -> str:
    """Append closing } / ] (and a closing " if needed) when a JSON
    string is EOF-truncated by a few chars.

    Narrow on purpose: only runs when json.loads raised at a position
    within a couple chars of len(s), i.e. the LLM forgot the final
    one-or-two closers. Counts balanced braces/brackets while tracking
    string literals + escapes; never rewrites content, only appends.
    Returns the original string if nothing looks fixable.

    Why targeted and not a json_repair wildcard: json_repair re-writes
    the whole stream and silently mangles valid patterns (the JS
    ternary incident). This helper ONLY adds trailing closers, so it
    can't corrupt valid content — the worst case is "already balanced,
    nothing appended" = caller retries, parser raises the same error.
    """
    stack = []
    in_string = False
    escape_next = False
    for c in s:
        if in_string:
            if escape_next:
                escape_next = False
            elif c == "\\":
                escape_next = True
            elif c == '"':
                in_string = False
        else:
            if c == '"':
                in_string = True
            elif c == "{":
                stack.append("}")
            elif c == "[":
                stack.append("]")
            elif c == "}" or c == "]":
                if stack and stack[-1] == c:
                    stack.pop()
    suffix = ""
    if in_string:
        suffix += '"'
    while stack and len(suffix) < max_appends:
        suffix += stack.pop()
    return s + suffix if suffix else s


try:
    from core.tool_json import autoclose_truncated_json as _shared_autoclose_truncated_json
except Exception:
    _shared_autoclose_truncated_json = _autoclose_truncated_json


def _log(msg):
    global _log_file
    sys.stderr.write(f"[mcp-bridge] {msg}\n")
    sys.stderr.flush()
    # Also write to file readable from host. Each CLI exposes its own
    # config dir env var (CC: CLAUDE_CONFIG_DIR, codex: CODEX_HOME,
    # gemini: HOME). Try them in order, fall back to cwd.
    try:
        if _log_file is None:
            import os
            _log_dir = (
                os.environ.get("CLAUDE_CONFIG_DIR")
                or os.environ.get("CODEX_HOME")
                or os.environ.get("HOME")
                or os.getcwd()
            )
            _log_file = open(os.path.join(_log_dir, "mcp_bridge.log"), "a", encoding="utf-8")
        _log_file.write(f"[mcp-bridge] {msg}\n")
        _log_file.flush()
    except Exception:
        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)


def main():
    relay_url = os.environ.get("PAWFLOW_TOOL_RELAY_URL", "")
    relay_token = os.environ.get("PAWFLOW_TOOL_RELAY_TOKEN", "")
    internal_token = os.environ.get("PAWFLOW_INTERNAL_TOKEN", "")
    user_id = os.environ.get("PAWFLOW_USER_ID", "")
    conv_id = os.environ.get("PAWFLOW_CONVERSATION_ID", "")
    agent_name = os.environ.get("PAWFLOW_AGENT_NAME", "")

    # Log presence (NOT value) of every PawFlow env var at boot — the
    # codex / gemini MCP integrations have a history of dropping `env =
    # {...}` from config.toml / settings.json silently when the bridge
    # subprocess inherits an empty env. With this log we can confirm at
    # a glance whether codex actually forwarded the env table or not.
    _log(f"env-check: relay_url={'set' if relay_url else 'MISSING'} "
         f"relay_token={'set' if relay_token else 'MISSING'} "
         f"internal_token={'set' if internal_token else 'MISSING'} "
         f"user_id={user_id or 'MISSING'} conv_id={conv_id or 'MISSING'} "
         f"agent={agent_name or 'MISSING'}")
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
                "Call get_tool_schema first to know the parameters.\n"
                "\n"
                "STRICT rules:\n"
                "  * `arguments_json` is a STRING containing a JSON object "
                "with the target tool's arguments, e.g. "
                "arguments_json='{\"path\":\"a.py\",\"content\":\"x = 1\\n\"}'. "
                "Use '{}' for a tool that takes no arguments. "
                "(A string field is used — not a free-form object — because "
                "some models otherwise emit an empty input.)\n"
                "  * Do NOT set tool_name to 'use_tool' and nest the call "
                "inside itself.\n"
                "  * Escape embedded newlines/quotes once (\\n, \\\"), "
                "not doubly."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "tool_name": {
                        "type": "string",
                        "description": (
                            "Name of the tool to execute (e.g. 'edit', "
                            "'read', 'bash'). NEVER set this to 'use_tool' "
                            "— use_tool is the wrapper itself and nesting "
                            "it inside itself is rejected."
                        ),
                    },
                    "arguments_json": {
                        "type": "string",
                        "description": (
                            "JSON object string with the target tool's "
                            "arguments, e.g. '{\"path\": \"/workspace\"}'. "
                            "Use '{}' for tools with no arguments."
                        ),
                    },
                },
                "required": ["tool_name", "arguments_json"],
            },
        },
    ]

    _log(f"MCP bridge ready: {len(mcp_tools)} tools, "
         f"relay={'connected' if client else 'unavailable'}")

    def _ensure_relay_client():
        """Return a connected relay client, retrying a failed initial connect."""
        nonlocal client
        if not relay_url or not relay_token:
            return None
        if client:
            try:
                client._ensure_connected()
                return client
            except Exception as e:
                _log(f"Existing tool relay client is unavailable: {e}")
                try:
                    client.close()
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                client = None
        last_error = None
        for attempt in range(TOOL_RELAY_RETRY_ATTEMPTS):
            try:
                _log(
                    f"Connecting to tool relay on demand "
                    f"(attempt {attempt + 1}/{TOOL_RELAY_RETRY_ATTEMPTS})...")
                new_client = ToolRelayClient(
                    relay_url, relay_token, user_id, conv_id, agent_name)
                new_client.connect()
                client = new_client
                return client
            except Exception as e:
                last_error = e
                _log(
                    f"On-demand tool relay connect failed "
                    f"(attempt {attempt + 1}/{TOOL_RELAY_RETRY_ATTEMPTS}): {e}")
                try:
                    new_client.close()
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                client = None
                if attempt < TOOL_RELAY_RETRY_ATTEMPTS - 1:
                    time.sleep(TOOL_RELAY_RETRY_DELAY_SECONDS)
        _log(f"Tool relay unavailable after retries: {last_error}")
        return None

    def _handle_tool_call(req_id, params, raw_line):
        call_started = time.perf_counter()
        name = params.get("name", "")
        args = params.get("arguments", {})
        if name == "use_tool" and isinstance(args, dict):
            _inner = args.get("arguments", {})
            if not _inner or _inner == {} or _inner == "{}":
                _log(f"EMPTY INNER ARGS! raw stdin line: {raw_line[:1000]}")
                _log(f"EMPTY INNER ARGS! parsed args: {json.dumps(args)[:500]}")
        for _ in range(3):
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except (json.JSONDecodeError, TypeError):
                    break
            else:
                break
        # Direct (non-wrapper) MCP call to an aliased name: we expose only
        # use_tool + get_tool_schema, so a model that calls e.g. `image` as a
        # top-level MCP tool would otherwise hit "unknown tool". Reroute it
        # through use_tool with the canonical name — purely an alias, no extra
        # tool is exposed in tools/list.
        if name not in ("use_tool", "get_tool_schema"):
            _canon = (_BRIDGE_TOOL_ALIASES.get(name)
                      or _BRIDGE_TOOL_ALIASES.get(str(name).lower()))
            if _canon:
                _log(f"DIRECT alias: {name}(...) -> use_tool(tool_name={_canon})")
                args = {"tool_name": _canon,
                        "arguments": args if isinstance(args, dict) else {}}
                name = "use_tool"

        _log(f"CALL {name}({json.dumps(args)[:300]})")

        relay_client = _ensure_relay_client()

        if not relay_client:
            result = "Error: tool relay not connected"
        elif name == "get_tool_schema":
            tool_name = args.get("tool_name", "") if isinstance(args, dict) else ""
            result = relay_client.request("get_tool_schema", tool_name=tool_name) if tool_name else relay_client.request("list_tools")
            result = json.dumps(result, indent=2, ensure_ascii=False) if isinstance(result, (dict, list)) else str(result)
        elif name == "use_tool":
            if not isinstance(args, dict):
                result = "Error: use_tool arguments must be a JSON object"
            else:
                tool_name = args.get("tool_name", "")
                # Unwrap double-wrapped calls: use_tool(tool_name='use_tool', arguments={tool_name:X, arguments:Y}).
                _unwrap_budget = 3
                while (tool_name in ("mcp__pawflow__use_tool", "mcp_pawflow_use_tool", "use_tool")
                       and _unwrap_budget > 0):
                    _inner_args = args.get("arguments", {})
                    if isinstance(_inner_args, str):
                        try:
                            _inner_args = json.loads(_inner_args)
                        except (json.JSONDecodeError, TypeError):
                            break
                    if not isinstance(_inner_args, dict) or "tool_name" not in _inner_args:
                        break
                    _log(f"USE_TOOL unwrap double-wrap: {tool_name} -> {_inner_args.get('tool_name')}")
                    args = _inner_args
                    tool_name = args.get("tool_name", "")
                    _unwrap_budget -= 1
                # Map hallucinated/legacy tool names to real PawFlow names.
                _TOOL_ALIASES = _BRIDGE_TOOL_ALIASES
                # Case-insensitive alias lookup for provider/native-style names.
                _alias_match = _TOOL_ALIASES.get(tool_name) or _TOOL_ALIASES.get(str(tool_name).lower())
                if _alias_match:
                    _log(f"USE_TOOL alias: {tool_name} -> {_alias_match}")
                    tool_name = _alias_match
                elif tool_name and tool_name[0].isupper() and tool_name.lower() != tool_name:
                    _lower = tool_name.lower()
                    _log(f"USE_TOOL lowering CC native: {tool_name} -> {_lower}")
                    tool_name = _lower
                if not tool_name or not str(tool_name).strip():
                    _other_keys = [k for k in args.keys() if k != "tool_name"]
                    result = (
                        "Error: missing required parameter 'tool_name'. "
                        "use_tool requires {\"tool_name\": \"<name>\", "
                        "\"arguments\": {...}}. Got keys: "
                        f"{sorted(args.keys()) or '[]'}."
                        + (f" (Did you forget to wrap your call -- the keys "
                           f"{_other_keys} look like tool arguments without "
                           f"a tool_name?)" if _other_keys else "")
                    )
                    _log(f"USE_TOOL REJECTED: empty tool_name, args_keys={sorted(args.keys())}")
                else:
                    # Payload source order: arguments_json (the advertised
                    # field — a STRING, so models don't collapse a free-form
                    # object to {}), then a literal `arguments` object
                    # (back-compat for clients that still send it), then flat
                    # sibling keys.
                    tool_args_raw = args.get("arguments_json")
                    if tool_args_raw is None or tool_args_raw == "":
                        tool_args_raw = args.get("arguments")
                    if tool_args_raw is None:
                        harvested = {k: v for k, v in args.items()
                                     if k not in ("tool_name", "arguments_json")}
                        if harvested:
                            _log(f"USE_TOOL {tool_name} harvested flat args (missing 'arguments'/'arguments_json' wrapper): keys={list(harvested.keys())}")
                        tool_args_raw = harvested
                    _log(f"USE_TOOL {tool_name} raw_type={type(tool_args_raw).__name__} raw={json.dumps(tool_args_raw, default=str)[:300]}")
                    tool_args = tool_args_raw
                    _decode_failed = False
                    _decode_err = None
                    _unwrap_passes = 0
                    for _ in range(3):
                        if not isinstance(tool_args, str):
                            break
                        try:
                            tool_args = json.loads(tool_args)
                            _unwrap_passes += 1
                        except json.JSONDecodeError as _je:
                            _decode_err = _je
                            if "Extra data" in str(_je):
                                try:
                                    tool_args, _ = json.JSONDecoder().raw_decode(tool_args)
                                    _unwrap_passes += 1
                                    _decode_err = None
                                    _log(f"USE_TOOL {tool_name} raw_decode OK (stripped trailing junk)")
                                    continue
                                except (json.JSONDecodeError, TypeError) as _je2:
                                    _decode_err = _je2
                                    _log(f"USE_TOOL {tool_name} raw_decode also FAILED: {_je2}")
                                    _decode_failed = True
                                    break
                            msg = str(_je)
                            trunc_like = (
                                "Expecting ',' delimiter" in msg
                                or "Expecting property name" in msg
                                or "Expecting value" in msg
                                or "Unterminated string" in msg
                            )
                            at_end = getattr(_je, "pos", -1) >= len(tool_args) - 4
                            if trunc_like and at_end:
                                patched = _shared_autoclose_truncated_json(tool_args)
                                if patched != tool_args:
                                    try:
                                        tool_args = json.loads(patched)
                                        _unwrap_passes += 1
                                        _decode_err = None
                                        _log(f"USE_TOOL {tool_name} truncation-repair OK")
                                        continue
                                    except (json.JSONDecodeError, TypeError) as _je3:
                                        _decode_err = _je3
                                        _log(f"USE_TOOL {tool_name} truncation-repair FAILED: {_je3}")
                            _log(f"USE_TOOL {tool_name} JSON decode FAILED: {_je} value={str(tool_args)[:200]}")
                            _decode_failed = True
                            break
                        except TypeError as _je:
                            _decode_err = _je
                            _log(f"USE_TOOL {tool_name} JSON decode TypeError: {_je}")
                            _decode_failed = True
                            break
                    if _unwrap_passes > 0:
                        _log(f"USE_TOOL {tool_name} unwrapped {_unwrap_passes} pass(es): {type(tool_args_raw).__name__} -> {type(tool_args).__name__}")
                    if _decode_failed and tool_args_raw and tool_args_raw != {} and tool_args_raw != "{}":
                        raw_str = tool_args_raw if isinstance(tool_args_raw, str) else str(tool_args_raw)
                        _log(f"USE_TOOL {tool_name} DECODE FAIL (forensic): raw_len={len(raw_str)} raw={raw_str!r}")
                        detail = str(_decode_err) if _decode_err else "unknown JSON error"
                        window = ""
                        pos = getattr(_decode_err, "pos", None)
                        if isinstance(pos, int) and 0 <= pos <= len(raw_str):
                            lo = max(0, pos - 120)
                            hi = min(len(raw_str), pos + 120)
                            prefix = "..." if lo > 0 else ""
                            suffix = "..." if hi < len(raw_str) else ""
                            window = f" Window around char {pos}: {prefix}{raw_str[lo:hi]!r}{suffix}"
                        result = (
                            f"Error: failed to decode arguments for {tool_name}. "
                            f"Arguments must be a JSON object (dict), not a JSON-encoded string. "
                            f"Parse error: {detail}.{window} Fix: resend with `arguments` "
                            f"as a literal dict, e.g. {{\"tool_name\": \"edit\", "
                            f"\"arguments\": {{\"path\": ..., \"old_string\": ...}}}} -- "
                            f"NOT a quoted string of JSON."
                        )
                    elif isinstance(tool_args, str):
                        _log(f"USE_TOOL {tool_name} args still string after unwrap: {tool_args[:200]}")
                        result = f"Error: arguments for {tool_name} must be a JSON object, got string: {tool_args[:200]}"
                    else:
                        _log(f"USE_TOOL {tool_name} final_type={type(tool_args).__name__} final={json.dumps(tool_args, default=str)[:300]}")
                        result = relay_client.request("execute_tool", tool_name=tool_name, arguments=tool_args)
                        if result is None or result == "":
                            result = "(no output)"
        else:
            result = f"Error: unknown tool '{name}'"

        try:
            result_len = len(result) if isinstance(result, str) else len(json.dumps(result, default=str))
        except Exception:
            result_len = len(str(result))
        _log(f"TIMING tools/call name={name} req={req_id} "
             f"total_ms={(time.perf_counter() - call_started) * 1000:.1f} "
             f"result_len={result_len}")
        _log(f"RESULT {name}: {str(result)[:100]}")
        if isinstance(result, list):
            content = result
            is_error = False
        else:
            result_str = str(result)
            try:
                from core.sanitization import sanitize_unicode
                result_str = sanitize_unicode(result_str)
            except ImportError:
                pass
            content = [{"type": "text", "text": result_str}]
            is_error = result_str.startswith("Error:")
        _respond(req_id, {"content": content, "isError": is_error})

    # MCP stdio loop
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as _line_err:
            # Full raw line dump (no [:200] cap) — forensic trail for
            # the "use_tool decoded as string" bug. Next time we hit it
            # we can inspect the actual bytes CC handed us.
            _log(f"STDIN JSON decode FAILED at char {getattr(_line_err, 'pos', '?')}: "
                 f"{_line_err}; raw_len={len(line)} raw={line!r}")
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
            def _run_tool_call():
                thread = threading.current_thread()
                try:
                    _handle_tool_call(req_id, params, line)
                finally:
                    _untrack_tool_call_thread(thread)

            thread = threading.Thread(
                target=_run_tool_call,
                daemon=False,
                name=f"mcp-call-{req_id}")
            _track_tool_call_thread(thread)
            thread.start()
            continue
        else:
            _respond(req_id, None,
                     error={"code": -32601, "message": f"Unknown method: {method}"})

    # Cleanup
    _wait_for_active_tool_calls()
    if client:
        client.close()


if __name__ == "__main__":
    main()
