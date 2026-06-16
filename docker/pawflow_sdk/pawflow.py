"""PawFlow SDK — lightweight tool access for containerized scripts.

Pre-installed in Docker containers. Connects to the PawFlow tool relay
via WebSocket and exposes tools as simple Python function calls.

Usage:
    from pawflow import fs, tools

    # Filesystem operations
    content = fs.read_file("src/main.py")
    fs.write_file("output.txt", "hello world")
    result = fs.exec("ls -la")
    files = fs.list_dir(".")
    fs.grep("TODO", path="src/", recursive=True)

    # Any tool
    schema = tools.get_schema("generate_image")
    result = tools.call("generate_image", prompt="a cat", width=512)

Environment variables (set automatically by the container):
    PAWFLOW_TOOL_RELAY_URL  — WebSocket URL of the tool relay
    PAWFLOW_TOOL_RELAY_TOKEN — auth token
    PAWFLOW_USER_ID
    PAWFLOW_CONVERSATION_ID
    PAWFLOW_AGENT_NAME
    PAWFLOW_FS_SERVICE — default filesystem service name
"""

import json
import os
import socket
import ssl
import struct
import uuid
import base64
import sys
from urllib.parse import urlparse

_RELAY_URL = os.environ.get("PAWFLOW_TOOL_RELAY_URL", "")
_RELAY_TOKEN = os.environ.get("PAWFLOW_TOOL_RELAY_TOKEN", "")
_USER_ID = os.environ.get("PAWFLOW_USER_ID", "")
_CONV_ID = os.environ.get("PAWFLOW_CONVERSATION_ID", "")
_AGENT_NAME = os.environ.get("PAWFLOW_AGENT_NAME", "")
_FS_SERVICE = os.environ.get("PAWFLOW_FS_SERVICE", "")

_sock = None
_lock = None


def _ensure_connected():
    """Connect to the tool relay WebSocket."""
    global _sock, _lock
    import threading
    if _lock is None:
        _lock = threading.Lock()
    if _sock is not None:
        try:
            _sock.getpeername()
            return
        except Exception:
            _sock = None

    if not _RELAY_URL:
        raise ConnectionError(
            "PAWFLOW_TOOL_RELAY_URL not set. "
            "This SDK only works inside a PawFlow container.")

    parsed = urlparse(_RELAY_URL)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "wss" else 9091)
    path = parsed.path or "/ws/tools"
    use_tls = parsed.scheme == "wss"

    sock = socket.create_connection((host, port))
    if use_tls:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        sock = ctx.wrap_socket(sock, server_hostname=host)

    # WebSocket handshake
    ws_key = base64.b64encode(os.urandom(16)).decode()
    handshake = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {ws_key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n\r\n"
    )
    sock.sendall(handshake.encode())
    resp = b""
    while b"\r\n\r\n" not in resp:
        resp += sock.recv(4096)
    if b"101" not in resp.split(b"\r\n")[0]:
        raise ConnectionError(f"WS handshake failed: {resp[:200]}")

    # Register
    reg = json.dumps({
        "type": "register",
        "token": _RELAY_TOKEN,
        "secret": _RELAY_TOKEN,
        "relay_type": "tool_client",
        "user_id": _USER_ID,
        "conversation_id": _CONV_ID,
        "agent_name": _AGENT_NAME,
    })
    _ws_send(sock, reg.encode())
    _ws_recv(sock)  # registered response
    _sock = sock


def _ws_send(sock, data):
    """Send a WebSocket text frame (masked)."""
    if isinstance(data, str):
        data = data.encode()
    mask_key = os.urandom(4)
    masked = bytes(b ^ mask_key[i % 4] for i, b in enumerate(data))
    length = len(data)
    frame = bytes([0x81])  # text, fin
    if length < 126:
        frame += bytes([0x80 | length])
    elif length < 65536:
        frame += bytes([0x80 | 126]) + struct.pack("!H", length)
    else:
        frame += bytes([0x80 | 127]) + struct.pack("!Q", length)
    frame += mask_key + masked
    sock.sendall(frame)


def _ws_recv(sock):
    """Receive a WebSocket text frame."""
    def _recv_exact(n):
        data = b""
        while len(data) < n:
            chunk = sock.recv(n - len(data))
            if not chunk:
                raise ConnectionError("WS connection closed")
            data += chunk
        return data

    hdr = _recv_exact(2)
    opcode = hdr[0] & 0x0F
    masked = bool(hdr[1] & 0x80)
    length = hdr[1] & 0x7F
    if length == 126:
        length = struct.unpack("!H", _recv_exact(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", _recv_exact(8))[0]
    if masked:
        mask = _recv_exact(4)
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(_recv_exact(length)))
    else:
        payload = _recv_exact(length)
    # Auto-respond to pings
    if opcode == 0x09:
        _ws_send(sock, payload)
        return _ws_recv(sock)
    return payload.decode("utf-8")


def _request(method, **kwargs):
    """Send a request to the tool relay and return the result."""
    _ensure_connected()
    request_id = uuid.uuid4().hex[:12]
    payload = json.dumps({
        "type": "request",
        "request_id": request_id,
        "method": method,
        **kwargs,
    })
    with _lock:
        _ws_send(_sock, payload.encode())
        while True:
            raw = _ws_recv(_sock)
            msg = json.loads(raw)
            if msg.get("type") == "ping":
                _ws_send(_sock, json.dumps({"type": "pong"}).encode())
                continue
            if msg.get("request_id") == request_id:
                if msg.get("type") == "error":
                    raise RuntimeError(f"Tool error: {msg.get('error', 'unknown')}")
                return msg.get("data")


# ── Tools API ────────────────────────────────────────────────────────

class _Tools:
    """Generic tool access."""

    def get_schema(self, tool_name: str) -> dict:
        """Get the JSON schema for a tool."""
        result = _request("get_tool_schema", tool_name=tool_name)
        if isinstance(result, str):
            try:
                return json.loads(result)
            except (json.JSONDecodeError, TypeError):
                pass
        return result or {}

    def call(self, tool_name: str, **arguments) -> str:
        """Call a tool and return the result."""
        result = _request("execute_tool",
                          tool_name=tool_name, arguments=arguments)
        return str(result) if result is not None else ""


class _Filesystem:
    """Filesystem tool shortcuts."""

    def __init__(self):
        self._service = _FS_SERVICE

    def _call(self, action: str, **kwargs) -> str:
        kwargs["action"] = action
        if self._service:
            kwargs.setdefault("service", self._service)
        return tools.call("filesystem", **kwargs)

    def read_file(self, path: str, **kwargs) -> str:
        return self._call("read_file", path=path, **kwargs)

    def write_file(self, path: str, content: str, **kwargs) -> str:
        return self._call("write_file", path=path, content=content, **kwargs)

    def list_dir(self, path: str = ".", **kwargs):
        result = self._call("list_dir", path=path, **kwargs)
        if isinstance(result, str):
            try:
                return json.loads(result)
            except (json.JSONDecodeError, TypeError):
                pass
        return result

    def exec(self, command: str, **kwargs) -> str:
        return self._call("exec", command=command, **kwargs)

    def grep(self, pattern: str, path: str = ".", **kwargs) -> str:
        return self._call("grep", path=path, regex=pattern, **kwargs)

    def stat(self, path: str, **kwargs):
        result = self._call("stat", path=path, **kwargs)
        if isinstance(result, str):
            try:
                return json.loads(result)
            except (json.JSONDecodeError, TypeError):
                pass
        return result

    def exists(self, path: str, **kwargs) -> bool:
        result = self._call("exists", path=path, **kwargs)
        return bool(result) and "true" in str(result).lower()

    def delete_file(self, path: str, **kwargs) -> str:
        return self._call("delete_file", path=path, **kwargs)

    def mkdir(self, path: str, **kwargs) -> str:
        return self._call("mkdir", path=path, **kwargs)

    def edit(self, path: str, old_string: str, new_string: str, **kwargs) -> str:
        return self._call("edit", path=path,
                          old_string=old_string, new_string=new_string, **kwargs)

    def git_status(self, path: str = ".", **kwargs) -> str:
        return self._call("git_status", path=path, **kwargs)

    def git_commit(self, path: str = ".", message: str = "", **kwargs) -> str:
        return self._call("git_commit", path=path, message=message, **kwargs)


_PFP_INVOKE_FORMAT = "pawflow.package.runtime.invoke.v1"
_PFP_RESULT_FORMAT = "pawflow.package.runtime.result.v1"
_PFP_HOST_CALL_FORMAT = "pawflow.package.runtime.host_call.v1"


class _PfpRuntime:
    """Small runtime helper for PawFlow Package entrypoints."""

    def __init__(self):
        self._request = None
        # Bind the protocol streams ONCE, at import time, before any wrapper
        # redirects sys.stdout to capture user print() output. The host-call
        # protocol (and the final result envelope) must always travel on the
        # real stdout/stdin even while the user script's stdout is buffered.
        self._out = sys.stdout
        self._in = sys.stdin

    def input(self) -> dict:
        """Read and cache the invocation envelope from stdin."""
        if self._request is not None:
            return self._request
        line = self._in.readline()
        if not line:
            raise RuntimeError("missing PFP invocation envelope")
        request = json.loads(line)
        if not isinstance(request, dict) or request.get("format") != _PFP_INVOKE_FORMAT:
            raise RuntimeError("invalid PFP invocation envelope")
        self._request = request
        return request

    @property
    def kind(self) -> str:
        return str(self.input().get("kind") or "")

    @property
    def payload(self) -> dict:
        payload = self.input().get("payload") or {}
        return payload if isinstance(payload, dict) else {}

    @property
    def package(self) -> dict:
        package = self.input().get("package") or {}
        return package if isinstance(package, dict) else {}

    @property
    def context(self) -> dict:
        context = self.input().get("context") or {}
        return context if isinstance(context, dict) else {}

    def result(self, value=None, *, flowfiles=None) -> None:
        envelope = {"format": _PFP_RESULT_FORMAT, "ok": True}
        if flowfiles is not None:
            envelope["flowfiles"] = flowfiles
        else:
            envelope["result"] = value
        self._emit(envelope)

    def error(self, message: str) -> None:
        self._emit({"format": _PFP_RESULT_FORMAT, "ok": False, "error": str(message)})

    def call_tool(self, tool_name: str, **arguments):
        return self._host_call("tool", tool_name, arguments=arguments)

    def call_service(self, service_name: str, operation: str, **arguments):
        return self._host_call("service", service_name, operation=operation, arguments=arguments)

    def flowfile(self, content=b"", attributes=None) -> dict:
        if isinstance(content, str):
            content = content.encode("utf-8")
        if content is None:
            content = b""
        rel_path = f".pawflow/flowfiles/results/result-{uuid.uuid4().hex}.bin"
        os.makedirs(os.path.dirname(rel_path), exist_ok=True)
        with open(rel_path, "wb") as handle:
            handle.write(bytes(content))
        return {
            "content_path": rel_path,
            "attributes": {str(k): str(v) for k, v in (attributes or {}).items()},
        }

    def artifact(self, kind: str, path: str, content_type: str = "",
                 filename: str = "") -> dict:
        data = {
            "kind": str(kind or ""),
            "path": str(path or ""),
        }
        if content_type:
            data["content_type"] = str(content_type)
        if filename:
            data["filename"] = str(filename)
        return {"artifact": data}

    def _host_call(self, kind: str, target: str, *, operation: str = "",
                   args=None, arguments=None):
        request = {
            "format": _PFP_HOST_CALL_FORMAT,
            "kind": kind,
            "target": target,
            "arguments": arguments or {},
        }
        if args:
            request["args"] = list(args)
        if operation:
            request["operation"] = operation
        self._emit(request)
        line = self._in.readline()
        if not line:
            raise RuntimeError("missing PFP host-call response")
        response = json.loads(line)
        if not isinstance(response, dict) or response.get("format") != _PFP_RESULT_FORMAT:
            raise RuntimeError("invalid PFP host-call response")
        if not response.get("ok", True):
            raise RuntimeError(str(response.get("error") or "PFP host-call failed"))
        return response.get("result")

    def _emit(self, envelope: dict) -> None:
        self._out.write(json.dumps(envelope, ensure_ascii=False) + "\n")
        self._out.flush()


# ── executeScript parity proxies ─────────────────────────────────────
# These give a containerized executeScript script the SAME names and call
# style as the in-process path: get_service(id), pawflow, flowfile. Each call
# is forwarded to the host over the pfp host-call protocol (stdin/stdout) and
# resolved there against THIS flow's declared services / scope-bounded API /
# live FlowFile. Only JSON-serializable arguments and results cross.

# Bytes cross the JSON boundary base64-encoded under this marker so binary
# FlowFile content round-trips losslessly (drop-in with the in-process path).
_BYTES_KEY = "__bytes_b64__"


class _ServiceProxy:
    """Proxy for a host-side flow service reached via get_service(id)."""

    def __init__(self, service_id: str):
        self.__dict__["_service_id"] = service_id

    def __getattr__(self, operation: str):
        # Refuse only dunders: they are never real service operations and would
        # otherwise let copy/pickle/repr machinery trigger host-calls. Single
        # underscore stays allowed for true parity with the raw object.
        if operation.startswith("__"):
            raise AttributeError(operation)

        def _call(*args, **arguments):
            return pfp._host_call(
                "service", self._service_id,
                operation=operation, args=args, arguments=arguments)
        return _call


def get_service(service_id: str) -> _ServiceProxy:
    """Return a proxy for a service declared in this flow (host-resolved)."""
    return _ServiceProxy(str(service_id))


class _PawflowProxy:
    """Proxy for the scope-bounded host ``pawflow`` API facade."""

    def __getattr__(self, operation: str):
        if operation.startswith("__"):
            raise AttributeError(operation)

        def _call(*args, **arguments):
            return pfp._host_call(
                "pawflow_api", "",
                operation=operation, args=args, arguments=arguments)
        return _call


class _FlowFileProxy:
    """Proxy for the live host FlowFile; mutations apply on the host.

    Mirrors core.FlowFile: get_content() returns bytes, set_content() preserves
    bytes vs str, get_attribute(key, default=None) returns Optional[str].
    """

    def get_content(self) -> bytes:
        data = pfp._host_call("flowfile", "", operation="get_content")
        if isinstance(data, dict) and _BYTES_KEY in data:
            return base64.b64decode(data[_BYTES_KEY])
        if isinstance(data, str):
            return data.encode("utf-8")
        return b"" if data is None else bytes(data)

    def set_content(self, content) -> None:
        if isinstance(content, (bytes, bytearray)):
            payload = {_BYTES_KEY: base64.b64encode(bytes(content)).decode("ascii")}
        else:
            payload = content
        pfp._host_call("flowfile", "", operation="set_content",
                       arguments={"content": payload})

    def get_attribute(self, key, default=None):
        return pfp._host_call("flowfile", "", operation="get_attribute",
                              arguments={"key": str(key), "default": default})

    def set_attribute(self, key, value) -> None:
        pfp._host_call("flowfile", "", operation="set_attribute",
                       arguments={"key": str(key), "value": value})

    def get_attributes(self) -> dict:
        return pfp._host_call("flowfile", "", operation="get_attributes") or {}


# Module-level singletons
tools = _Tools()
fs = _Filesystem()
pfp = _PfpRuntime()
script_pawflow = _PawflowProxy()
script_flowfile = _FlowFileProxy()
