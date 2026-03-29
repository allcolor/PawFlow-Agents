"""HTTP Listener Service — shared HTTP server for multiple flows.

A singleton-per-port service that starts a threaded HTTP server and
dispatches incoming requests to registered flows/tasks based on
method + URL pattern matching.

Multiple flows can register routes on the same port as long as patterns
don't conflict.  When no route matches, 504/404 is returned directly.
"""

import asyncio
import json
import logging
import re
import socket
import ssl
import threading
import time
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field

from core.base_service import BaseService

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pending request — correlation between HTTP handler thread and flow
# ---------------------------------------------------------------------------

@dataclass
class PendingRequest:
    """Represents an in-flight HTTP request waiting for a flow response."""
    request_id: str
    method: str
    path: str
    headers: Dict[str, str]
    body: bytes
    query_string: str = ""
    path_params: Dict[str, str] = field(default_factory=dict)
    remote_addr: str = ""
    timestamp: float = field(default_factory=time.time)

    # Response fields (filled by handleHTTPResponse task)
    response_status: int = 200
    response_headers: Dict[str, str] = field(default_factory=dict)
    response_body: bytes = b""

    # Streaming response (alternative to response_body)
    response_stream: Any = None  # iterator yielding bytes chunks

    # Synchronization
    _event: threading.Event = field(default_factory=threading.Event)
    completed: bool = False

    def wait(self, timeout: float = 30.0) -> bool:
        """Block until response is ready or timeout."""
        return self._event.wait(timeout=timeout)

    def complete(self, status: int, headers: Dict[str, str], body: bytes):
        """Set the response and unblock the waiting HTTP handler."""
        self.response_status = status
        self.response_headers = headers
        self.response_body = body
        self.completed = True
        self._event.set()

    def complete_stream(self, status: int, headers: Dict[str, str], stream):
        """Set a streaming response and unblock the HTTP handler.

        Args:
            stream: An iterable yielding bytes chunks.
        """
        self.response_status = status
        self.response_headers = headers
        self.response_stream = stream
        self.completed = True
        self._event.set()


# ---------------------------------------------------------------------------
# Route registry
# ---------------------------------------------------------------------------

@dataclass
class RouteEntry:
    """A registered route pattern."""
    method: str          # GET, POST, etc.
    pattern: str         # e.g. /api/users/{id}
    regex: re.Pattern    # compiled regex with named groups
    owner_id: str        # flow_id or task_id that registered this route
    callback: Any        # callable(PendingRequest) -> None
    ws_handler: Any = None  # callable(socket, path_params) for WebSocket upgrades


class RouteConflictError(Exception):
    """Raised when two flows register overlapping routes."""
    pass


class RouteRegistry:
    """Thread-safe registry of method+pattern -> handler."""

    def __init__(self):
        self._routes: List[RouteEntry] = []
        self._lock = threading.Lock()

    def register(self, method: str, pattern: str, owner_id: str, callback,
                 ws_handler=None) -> RouteEntry:
        """Register a route.  Raises RouteConflictError on overlap."""
        method = method.upper()
        regex = self._compile_pattern(pattern)

        with self._lock:
            for existing in self._routes:
                if existing.method == method and existing.pattern == pattern:
                    if existing.owner_id == owner_id:
                        # Same owner, same route — idempotent update
                        existing.callback = callback
                        return existing
                    raise RouteConflictError(
                        f"Route {method} {pattern} already registered by '{existing.owner_id}'"
                    )

            entry = RouteEntry(
                method=method, pattern=pattern, regex=regex,
                owner_id=owner_id, callback=callback,
                ws_handler=ws_handler,
            )
            self._routes.append(entry)
            return entry

    def unregister(self, owner_id: str):
        """Remove all routes for a given owner."""
        with self._lock:
            self._routes = [r for r in self._routes if r.owner_id != owner_id]

    def match(self, method: str, path: str) -> Optional[Tuple[RouteEntry, Dict[str, str]]]:
        """Find matching route.  Returns (entry, path_params) or None."""
        method = method.upper()
        with self._lock:
            for entry in self._routes:
                if entry.method != method and entry.method != "*":
                    continue
                m = entry.regex.fullmatch(path)
                if m:
                    return entry, m.groupdict()
        return None

    def get_routes(self) -> List[Dict[str, str]]:
        """List all registered routes."""
        with self._lock:
            return [
                {"method": r.method, "pattern": r.pattern, "owner": r.owner_id}
                for r in self._routes
            ]

    @staticmethod
    def _compile_pattern(pattern: str) -> re.Pattern:
        """Convert /api/users/{id} to regex /api/users/(?P<id>[^/]+)."""
        # Escape regex special chars except { }
        escaped = ""
        i = 0
        while i < len(pattern):
            ch = pattern[i]
            if ch == '{':
                end = pattern.index('}', i)
                param_name = pattern[i+1:end]
                # {param+} matches one or more path segments (including /)
                if param_name.endswith('+'):
                    param_name = param_name[:-1]
                    escaped += f"(?P<{param_name}>.+)"
                else:
                    escaped += f"(?P<{param_name}>[^/]+)"
                i = end + 1
            elif ch in r'\.+*?[]()^$|':
                escaped += '\\' + ch
                i += 1
            else:
                escaped += ch
                i += 1
        return re.compile(escaped)


# ---------------------------------------------------------------------------
# HTTP Request Handler
# ---------------------------------------------------------------------------

class _RequestHandler(BaseHTTPRequestHandler):
    """Handler dispatching to the RouteRegistry on the server."""

    # Silence default log output
    def log_message(self, format, *args):
        logger.debug(f"HTTP: {format % args}")

    def _handle(self):
        method = self.command
        path = self.path.split('?', 1)[0]
        query = self.path.split('?', 1)[1] if '?' in self.path else ""

        # WebSocket upgrade detection
        if (self.headers.get("Upgrade", "").lower() == "websocket" and
                "upgrade" in self.headers.get("Connection", "").lower()):
            return self._handle_websocket_upgrade(path, query)

        # Read body
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""

        # Collect headers
        headers = {k: v for k, v in self.headers.items()}

        # Match route
        registry: RouteRegistry = self.server._route_registry
        timeout: float = self.server._request_timeout
        result = registry.match(method, path)

        if result is None:
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "error": "Not Found",
                "message": f"No route matches {method} {path}",
            }).encode())
            return

        entry, path_params = result

        # Create pending request
        req = PendingRequest(
            request_id=uuid.uuid4().hex,
            method=method,
            path=path,
            headers=headers,
            body=body,
            query_string=query,
            path_params=path_params,
            remote_addr=self.client_address[0] if self.client_address else "",
        )

        # Store in server's pending map
        self.server._pending_requests[req.request_id] = req

        # Dispatch to flow (non-blocking — the callback enqueues a FlowFile)
        try:
            entry.callback(req)
        except Exception as e:
            logger.error(f"Route callback error for {method} {path}: {e}")
            self.server._pending_requests.pop(req.request_id, None)
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Internal Server Error"}).encode())
            return

        # Block until flow responds or timeout
        if not req.wait(timeout=timeout):
            self.server._pending_requests.pop(req.request_id, None)
            self.send_response(504)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "error": "Gateway Timeout",
                "message": f"Flow did not respond within {timeout}s",
            }).encode())
            return

        # Send the flow's response
        self.server._pending_requests.pop(req.request_id, None)
        self.send_response(req.response_status)
        for k, v in req.response_headers.items():
            self.send_header(k, v)
        if "Content-Type" not in req.response_headers:
            self.send_header("Content-Type", "application/octet-stream")
        self.end_headers()

        if req.response_stream is not None:
            # Streaming response — write chunks as they come
            try:
                for chunk in req.response_stream:
                    if chunk:
                        self.wfile.write(chunk)
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                logger.debug(f"Client disconnected during stream for {req.request_id}")
            except OSError as e:
                # WinError 10053/10054 and similar socket errors = client disconnect
                logger.debug(f"Client disconnected during stream for {req.request_id}: {e}")
            except Exception as e:
                logger.error(f"Stream error for {req.request_id}: {e}")
        elif req.response_body:
            self.wfile.write(req.response_body)

    def _handle_websocket_upgrade(self, path: str, query: str):
        """Handle a WebSocket upgrade request.

        Matches the route, verifies it has a ws_handler, performs the
        RFC 6455 handshake, then hands the raw socket to the handler.
        The handler runs in the current thread (thread-per-connection).
        """
        import base64
        import hashlib

        registry: RouteRegistry = self.server._route_registry
        result = registry.match("GET", path)

        if result is None or not result[0].ws_handler:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "error": "Bad Request",
                "message": f"WebSocket not supported on {path}",
            }).encode())
            return

        entry, path_params = result

        # RFC 6455 handshake
        ws_key = self.headers.get("Sec-WebSocket-Key", "")
        if not ws_key:
            self.send_response(400)
            self.end_headers()
            return

        _WS_MAGIC = b"258EAFA5-E914-47DA-95CA-5AB5ADF7254B"
        accept = base64.b64encode(
            hashlib.sha1(ws_key.encode() + _WS_MAGIC).digest()
        ).decode()

        # Send 101 — must be HTTP/1.1 for WebSocket (BaseHTTPHandler defaults to 1.0)
        self.protocol_version = "HTTP/1.1"
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        ws_protocol = self.headers.get("Sec-WebSocket-Protocol", "")
        if ws_protocol:
            self.send_header("Sec-WebSocket-Protocol",
                             ws_protocol.split(",")[0].strip())
        self.end_headers()
        self.wfile.flush()  # CRITICAL: flush the 101 to the browser NOW

        logger.info("[WS] 101 sent for %s", path)

        # Block in the WS handler in this HTTP thread.
        # Pass rfile/wfile for reading/writing (raw socket recv returns
        # EOF on Windows due to rfile BufferedReader consuming the fd).
        self.close_connection = True
        try:
            entry.ws_handler(self.request, path_params, {
                "path": path,
                "query": query,
                "headers": dict(self.headers),
                "remote_addr": self.client_address[0] if self.client_address else "",
                "_rfile": self.rfile,
                "_wfile": self.wfile,
            })
        except Exception as e:
            logger.debug(f"WebSocket handler error: {e}")

    # Handle all HTTP methods
    do_GET = _handle
    do_POST = _handle
    do_PUT = _handle
    do_DELETE = _handle
    do_PATCH = _handle
    do_HEAD = _handle
    do_OPTIONS = _handle


class _AsyncHTTPServer:
    """Asyncio-based HTTP+WS server.

    Handles both HTTP and WebSocket on the same port:
    - HTTP: reads request, dispatches to route callback via thread pool
    - WS: detects Upgrade header, does RFC 6455 handshake, passes
      asyncio reader/writer to ws_handler

    Auto-detects TLS (peek first byte for 0x16 ClientHello).
    """

    def __init__(self, host: str, port: int, route_registry: RouteRegistry,
                 request_timeout: float, ssl_ctx=None):
        self._host = host
        self._port = port
        self._route_registry = route_registry
        self._request_timeout = request_timeout
        self._ssl_ctx = ssl_ctx
        self._pending_requests: Dict[str, PendingRequest] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._server = None
        self._thread: Optional[threading.Thread] = None

    def start(self):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run, daemon=True,
            name=f"http-listener-{self._port}")
        self._thread.start()
        # Wait for server ready
        for _ in range(50):
            if self._server:
                break
            time.sleep(0.1)

    def _run(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._serve())

    async def _serve(self):
        self._server = await asyncio.start_server(
            self._handle_connection, self._host, self._port,
            reuse_address=True)
        logger.info("Async HTTP+WS server started on %s:%d", self._host, self._port)
        async with self._server:
            await self._server.serve_forever()

    def stop(self):
        if self._server and self._loop:
            self._loop.call_soon_threadsafe(self._server.close)
        if self._thread:
            self._thread.join(timeout=5)

    async def _handle_connection(self, reader: asyncio.StreamReader,
                                  writer: asyncio.StreamWriter):
        """Handle a single TCP connection — HTTP or WS."""
        import base64, hashlib
        addr = writer.get_extra_info("peername")

        try:
            # Read HTTP request headers
            request = b""
            while b"\r\n\r\n" not in request:
                chunk = await asyncio.wait_for(reader.read(4096), timeout=30)
                if not chunk:
                    return
                request += chunk

            # Separate headers from any extra data
            header_end = request.index(b"\r\n\r\n") + 4
            extra = request[header_end:]
            header_bytes = request[:header_end]

            # Parse first line
            first_line = header_bytes.split(b"\r\n")[0].decode("latin-1", errors="replace")
            parts = first_line.split()
            method = parts[0] if len(parts) >= 1 else "GET"
            full_path = parts[1] if len(parts) >= 2 else "/"
            path = full_path.split("?")[0]
            query = full_path.split("?", 1)[1] if "?" in full_path else ""

            # Parse headers
            headers = {}
            for line in header_bytes.decode("latin-1", errors="replace").split("\r\n")[1:]:
                if ":" in line:
                    k, v = line.split(":", 1)
                    headers[k.strip()] = v.strip()

            # Detect WebSocket upgrade
            if (headers.get("Upgrade", "").lower() == "websocket" and
                    "upgrade" in headers.get("Connection", "").lower()):
                await self._handle_ws(reader, writer, path, query, headers, addr)
                return

            # HTTP request — handle in thread pool
            await self._handle_http(reader, writer, method, path, query,
                                     headers, header_bytes, extra, addr)

        except asyncio.TimeoutError:
            pass
        except (ConnectionError, OSError):
            pass
        except Exception as e:
            logger.debug("Connection handler error: %s", e)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _handle_ws(self, reader, writer, path, query, headers, addr):
        """Handle WebSocket upgrade on the asyncio stream."""
        import base64, hashlib

        result = self._route_registry.match("GET", path)
        if result is None or not result[0].ws_handler:
            writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\nWebSocket not supported")
            await writer.drain()
            return

        entry, path_params = result

        ws_key = headers.get("Sec-WebSocket-Key", "")
        if not ws_key:
            writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            await writer.drain()
            return

        _MAGIC = b"258EAFA5-E914-47DA-95CA-5AB5ADF7254B"
        accept = base64.b64encode(
            hashlib.sha1(ws_key.encode() + _MAGIC).digest()
        ).decode()

        response = (
            f"HTTP/1.1 101 Switching Protocols\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n"
        )
        ws_protocol = headers.get("Sec-WebSocket-Protocol", "")
        if ws_protocol:
            response += f"Sec-WebSocket-Protocol: {ws_protocol.split(',')[0].strip()}\r\n"
        response += "\r\n"
        writer.write(response.encode("latin-1"))
        await writer.drain()

        logger.info("[WS] 101 sent for %s", path)

        # Get the raw socket from the asyncio transport
        transport = writer.transport
        raw_sock = transport.get_extra_info("socket")
        if raw_sock is None:
            # Fallback: detach from asyncio and use transport directly
            logger.warning("[WS] Cannot get raw socket from transport")
            return

        # Detach from asyncio — we'll use the raw socket directly
        # (asyncio reader/writer no longer usable after this)
        transport.pause_reading()

        # The WS handler runs in a thread (blocking I/O on the raw socket)
        def _ws_thread():
            try:
                entry.ws_handler(raw_sock, path_params, {
                    "path": path,
                    "query": query,
                    "headers": headers,
                    "remote_addr": addr[0] if addr else "",
                })
            except Exception as e:
                logger.debug("WS handler error: %s", e)
            finally:
                try:
                    raw_sock.close()
                except Exception:
                    pass

        # Run in thread and wait
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _ws_thread)

    async def _handle_http(self, reader, writer, method, path, query,
                            headers, header_bytes, extra, addr):
        """Handle HTTP request — dispatch to route callback."""
        result = self._route_registry.match(method.upper(), path)

        if result is None:
            resp = (
                b"HTTP/1.1 404 Not Found\r\n"
                b"Content-Type: application/json\r\n\r\n"
                + json.dumps({"error": "Not Found",
                              "message": f"No route matches {method} {path}"}).encode()
            )
            writer.write(resp)
            await writer.drain()
            return

        entry, path_params = result

        # Read body if Content-Length present
        content_length = int(headers.get("Content-Length", 0))
        body = extra[:content_length] if extra else b""
        remaining = content_length - len(body)
        if remaining > 0:
            body += await asyncio.wait_for(reader.readexactly(remaining), timeout=30)

        # Create pending request
        req = PendingRequest(
            request_id=uuid.uuid4().hex,
            method=method,
            path=path,
            headers=headers,
            body=body,
            query_string=query,
            path_params=path_params,
            remote_addr=addr[0] if addr else "",
        )
        self._pending_requests[req.request_id] = req

        # Dispatch (non-blocking callback)
        try:
            entry.callback(req)
        except Exception as e:
            logger.error("Route callback error: %s", e)
            self._pending_requests.pop(req.request_id, None)
            writer.write(b"HTTP/1.1 500 Internal Server Error\r\n\r\n")
            await writer.drain()
            return

        # Wait for response (in thread to not block event loop)
        loop = asyncio.get_event_loop()
        responded = await loop.run_in_executor(
            None, req.wait, self._request_timeout)

        self._pending_requests.pop(req.request_id, None)

        if not responded:
            resp = (
                b"HTTP/1.1 504 Gateway Timeout\r\n"
                b"Content-Type: application/json\r\n\r\n"
                + json.dumps({"error": "Gateway Timeout"}).encode()
            )
            writer.write(resp)
            await writer.drain()
            return

        # Send response
        status_line = f"HTTP/1.1 {req.response_status} OK\r\n"
        header_lines = "".join(
            f"{k}: {v}\r\n" for k, v in req.response_headers.items())
        if "Content-Type" not in req.response_headers:
            header_lines += "Content-Type: application/octet-stream\r\n"

        writer.write(f"{status_line}{header_lines}\r\n".encode("latin-1"))

        if req.response_stream is not None:
            try:
                for chunk in req.response_stream:
                    if chunk:
                        writer.write(chunk)
                        await writer.drain()
            except (ConnectionError, OSError):
                pass
        elif req.response_body:
            writer.write(req.response_body)

        await writer.drain()



# ---------------------------------------------------------------------------
# HTTPListenerService — the shared service
# ---------------------------------------------------------------------------

# Singleton registry: port → service instance (in-process)
_instances: Dict[int, "HTTPListenerService"] = {}
_instances_lock = threading.Lock()


class HTTPListenerService(BaseService):
    """Shared HTTP listener service — global, one per port.

    Multiple flows can share the same service (= same port).
    Each flow registers its routes; the service dispatches incoming
    requests to the right flow based on method + URL pattern.

    Singleton per port via _instances dict. Also registered in
    GlobalServiceRegistry for discoverability by code outside flows.

    Features:
    - Auto-detect TLS: peek first byte, if 0x16 → SSL, else plain HTTP
    - Multi-cert SNI: register_cert(hostname, certfile, keyfile)
    - WebSocket upgrade support on any route with ws_handler

    Config:
        host: str = "0.0.0.0"
        port: int = 9090
        request_timeout: float = 30.0  (seconds before 504)
        ssl_certfile: str = ""  (path to default PEM certificate)
        ssl_keyfile: str = ""   (path to default PEM private key)
        ssl_keyfile_password: str = "" (optional key password)
    """

    TYPE = "httpListener"

    @classmethod
    def get_for_port(cls, port: int) -> Optional["HTTPListenerService"]:
        """Find the HTTPListenerService for a port."""
        with _instances_lock:
            return _instances.get(port)

    def __new__(cls, config=None):
        if config is None:
            config = {}
        port = int(config.get("port", 9090))
        with _instances_lock:
            if port in _instances:
                return _instances[port]
        return super().__new__(cls)

    def __init__(self, config: Dict[str, Any]):
        if hasattr(self, '_port'):
            return  # already initialized (singleton)
        super().__init__(config)
        self._host = self.config.get("host", "0.0.0.0")
        self._port = int(self.config.get("port", 9090))
        self._request_timeout = float(self.config.get("request_timeout", 120.0))

        self._ssl_certfile = self.config.get("ssl_certfile", "")
        self._ssl_keyfile = self.config.get("ssl_keyfile", "")
        self._ssl_keyfile_password = self.config.get("ssl_keyfile_password", "")

        self._registry = RouteRegistry()
        self._server: Optional[_HTTPServerWithRegistry] = None
        self._server_thread: Optional[threading.Thread] = None
        self._ref_count = 0
        self._ref_lock = threading.Lock()

        # SNI multi-cert: hostname → SSLContext
        self._sni_certs: Dict[str, ssl.SSLContext] = {}
        self._default_ssl_ctx: Optional[ssl.SSLContext] = None

        # Register singleton
        with _instances_lock:
            _instances[self._port] = self

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "host": {"type": "string", "required": False, "default": "0.0.0.0", "description": "Bind address"},
            "port": {"type": "integer", "required": True, "default": 9090, "description": "Listen port"},
            "request_timeout": {"type": "float", "required": False, "default": 30.0, "description": "Request timeout (seconds)"},
            "ssl_certfile": {"type": "string", "required": False, "default": "", "description": "Path to SSL certificate (PEM)"},
            "ssl_keyfile": {"type": "string", "required": False, "default": "", "description": "Path to SSL private key (PEM)"},
            "ssl_keyfile_password": {"type": "string", "required": False, "default": "", "description": "Password for encrypted key"},
            "ssl_service_id": {"type": "string", "required": False, "default": "", "description": "SSLContextService ID (alternative to certfile/keyfile)"},
        }

    @property
    def port(self) -> int:
        return self._port

    @property
    def registry(self) -> RouteRegistry:
        return self._registry

    # -- Service lifecycle --

    def disconnect(self):
        """Override BaseService.disconnect to support ref-counted shared instances.

        BaseService.disconnect() sets _connection = None after the first call,
        which prevents subsequent disconnect() calls from reaching _close_connection().
        We override to always go through the ref-counting logic.
        """
        try:
            self._close_connection()
        except Exception as e:
            logger.error(f"Error during HTTPListenerService disconnect: {e}")
        # Only clear base state when truly stopped
        with self._ref_lock:
            if self._ref_count <= 0:
                self._connection = None
                self._initialized = False

    def _create_connection(self):
        """Start the HTTP server (only on first connect).

        Registers self in GlobalServiceRegistry as _http_listener_{port}.
        Uses auto-detect TLS: if SSL is configured, accepts both plain
        and TLS connections on the same port (peek first byte).
        """
        with self._ref_lock:
            self._ref_count += 1
            if self._server is not None:
                return self._server  # already running

        self._default_ssl_ctx = self._build_ssl_context()

        self._server = _AsyncHTTPServer(
            self._host, self._port, self._registry,
            self._request_timeout, ssl_ctx=self._default_ssl_ctx,
        )
        self._server.start()

        # Register in GlobalServiceRegistry for discoverability
        svc_id = f"_http_listener_{self._port}"
        try:
            from gui.services.global_service_registry import GlobalServiceRegistry
            greg = GlobalServiceRegistry.get_instance()
            if not greg.get_definition(svc_id):
                # install with enabled=True — _connect_one will be called
                # but __new__ returns this same singleton, so no duplicate
                greg.install(svc_id, self.TYPE, self.config,
                             description=f"HTTP listener on port {self._port}")
            # Ensure we're the live instance (in case install skipped _connect_one
            # because we're already in _live_instances from install's own connect)
            greg._live_instances[svc_id] = self
        except Exception as e:
            logger.debug("Failed to register in GlobalServiceRegistry: %s", e)

        logger.info(f"HTTPListenerService started on {proto} {self._host}:{self._port}")
        return self._server

    def _build_ssl_context(self) -> Optional[ssl.SSLContext]:
        """Build default SSL context from config."""
        if not self._ssl_certfile:
            return None
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(
            certfile=self._ssl_certfile,
            keyfile=self._ssl_keyfile or None,
            password=self._ssl_keyfile_password or None,
        )

        # SNI callback for multi-cert support
        def _sni_callback(ssl_socket, server_name, ssl_context):
            if server_name and server_name in self._sni_certs:
                ssl_socket.context = self._sni_certs[server_name]
        ctx.sni_callback = _sni_callback

        return ctx

    def register_cert(self, hostname: str, certfile: str, keyfile: str = "",
                      password: str = ""):
        """Register an SSL certificate for a specific hostname (SNI).

        If no default SSL is configured, this also enables TLS on the port
        using this cert as the default.
        """
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile, keyfile or None, password or None)
        self._sni_certs[hostname] = ctx

        # If no default SSL yet, use this as default and enable TLS
        if not self._default_ssl_ctx:
            self._default_ssl_ctx = ctx
            # Propagate to running server
            if self._server:
                self._server._ssl_ctx = ctx
                self._server._sni_certs = self._sni_certs

        logger.info("Registered SSL cert for hostname '%s' on port %d", hostname, self._port)

    @property
    def is_ssl(self) -> bool:
        """Whether the listener has SSL configured (accepts both plain+TLS)."""
        return self._default_ssl_ctx is not None

    def _close_connection(self):
        """Stop the HTTP server (only when ref count reaches 0)."""
        with self._ref_lock:
            self._ref_count = max(0, self._ref_count - 1)
            if self._ref_count > 0:
                return  # other flows still using it

        if self._server:
            for req_id, req in list(self._server._pending_requests.items()):
                req.complete(503, {"Content-Type": "application/json"},
                             json.dumps({"error": "Service Unavailable"}).encode())
            self._server.stop()
            self._server = None

        # Deregister
        with _instances_lock:
            _instances.pop(self._port, None)
        try:
            from gui.services.global_service_registry import GlobalServiceRegistry
            greg = GlobalServiceRegistry.get_instance()
            greg._live_instances.pop(f"_http_listener_{self._port}", None)
        except Exception:
            pass

        logger.info(f"HTTPListenerService stopped on port {self._port}")

    # -- Route management --

    def register_route(self, method: str, pattern: str, owner_id: str, callback,
                       ws_handler=None) -> RouteEntry:
        """Register a route for a flow/task.

        Args:
            ws_handler: Optional WebSocket handler callable(socket, path_params, meta).
                        If set, WebSocket upgrade requests on this route are accepted
                        and the handler is called with the raw socket after handshake.
        """
        return self._registry.register(method, pattern, owner_id, callback,
                                        ws_handler=ws_handler)

    def unregister_routes(self, owner_id: str):
        """Remove all routes for a flow/task."""
        self._registry.unregister(owner_id)

    def get_routes(self) -> List[Dict[str, str]]:
        """List all registered routes."""
        return self._registry.get_routes()

    # -- Response submission --

    def submit_response(self, request_id: str, status: int = 200,
                        headers: Optional[Dict[str, str]] = None,
                        body: bytes = b"") -> bool:
        """Submit a response for a pending request.

        Called by handleHTTPResponse task to complete the HTTP cycle.
        """
        if not self._server:
            logger.error("Cannot submit response: server not running")
            return False

        req = self._server._pending_requests.get(request_id)
        if not req:
            logger.warning(f"Request {request_id} not found (timed out or already responded)")
            return False

        req.complete(status, headers or {}, body)
        return True

    def submit_stream_response(self, request_id: str, status: int = 200,
                               headers: Optional[Dict[str, str]] = None,
                               stream=None) -> bool:
        """Submit a streaming response for a pending request.

        The stream should be an iterable yielding bytes chunks.
        """
        if not self._server:
            logger.error("Cannot submit stream response: server not running")
            return False

        req = self._server._pending_requests.get(request_id)
        if not req:
            logger.warning(f"Request {request_id} not found for stream response")
            return False

        req.complete_stream(status, headers or {}, stream)
        return True

    def get_pending_count(self) -> int:
        """Number of in-flight requests waiting for flow responses."""
        if not self._server:
            return 0
        return len(self._server._pending_requests)


# Auto-register
from core import ServiceFactory
ServiceFactory.register(HTTPListenerService)
