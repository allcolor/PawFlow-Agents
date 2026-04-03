"""HTTP Listener Service — shared HTTP server for multiple flows.

A singleton-per-port service that starts a threaded HTTP server and
dispatches incoming requests to registered flows/tasks based on
method + URL pattern matching.

Multiple flows can register routes on the same port as long as patterns
don't conflict.  When no route matches, 504/404 is returned directly.
"""

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
        # Private gateway — must pass before anything else
        from services.private_gateway import check_request as _gw_check
        if _gw_check(self):
            return

        method = self.command
        path = self.path.split('?', 1)[0]
        query = self.path.split('?', 1)[1] if '?' in self.path else ""

        # WebSocket upgrades are intercepted in _HTTPServerWithRegistry.process_request
        # BEFORE reaching this handler — so no WS detection needed here.

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

    # Handle all HTTP methods
    do_GET = _handle
    do_POST = _handle
    do_PUT = _handle
    do_DELETE = _handle
    do_PATCH = _handle
    do_HEAD = _handle
    do_OPTIONS = _handle


class _PrefixedSocket:
    """Socket wrapper that prepends already-read data to recv().

    Used when we read HTTP headers to detect WS, but the connection is
    actually HTTP and needs to go through BaseHTTPRequestHandler which
    expects to read from the start.
    """

    def __init__(self, sock, prefix: bytes):
        self._sock = sock
        self._prefix = prefix
        # Copy attributes that BaseHTTPRequestHandler/socketserver need
        self.family = sock.family
        self.type = sock.type
        self.proto = getattr(sock, 'proto', 0)

    def recv(self, bufsize, flags=0):
        if flags:
            # MSG_PEEK etc. — can't handle with prefix, pass through
            return self._sock.recv(bufsize, flags)
        if self._prefix:
            data = self._prefix[:bufsize]
            self._prefix = self._prefix[bufsize:]
            return data
        return self._sock.recv(bufsize)

    def makefile(self, mode='r', buffering=-1, **kwargs):
        """Create a file-like wrapper — needed by BaseHTTPRequestHandler."""
        if self._prefix and 'r' in mode:
            import io
            raw = self._sock.makefile(mode, buffering=0, **kwargs)
            # Prepend our data to the raw stream
            prefixed = io.BytesIO(self._prefix)
            self._prefix = b""
            return io.BufferedReader(_ConcatReader(prefixed, raw), buffer_size=buffering if buffering > 0 else 8192)
        return self._sock.makefile(mode, buffering, **kwargs)

    def __getattr__(self, name):
        return getattr(self._sock, name)


class _ConcatReader:
    """Concatenate two readable streams — prefix + socket."""

    def __init__(self, first, second):
        self._first = first
        self._second = second
        self._first_done = False

    def read(self, n=-1):
        if not self._first_done:
            data = self._first.read(n)
            if data:
                return data
            self._first_done = True
        return self._second.read(n)

    def readinto(self, b):
        if not self._first_done:
            n = self._first.readinto(b)
            if n:
                return n
            self._first_done = True
        return self._second.readinto(b)

    def readable(self):
        return True

    def flush(self):
        pass

    def close(self):
        self._first.close()
        self._second.close()

    @property
    def closed(self):
        return self._second.closed


class _HTTPServerWithRegistry(ThreadingMixIn, HTTPServer):
    """Threaded HTTPServer with HTTP + WebSocket support.

    HTTP requests go through BaseHTTPRequestHandler as usual.
    WebSocket upgrades are intercepted in process_request via peek,
    handled directly on the raw socket (no BaseHTTPRequestHandler),
    and the socket is NOT closed by shutdown_request.
    """

    daemon_threads = True
    allow_reuse_address = True
    allow_reuse_port = True

    def __init__(self, server_address, handler_class, route_registry, request_timeout):
        self._route_registry = route_registry
        self._pending_requests: Dict[str, PendingRequest] = {}
        self._request_timeout = request_timeout
        self._ssl_ctx: Optional[ssl.SSLContext] = None
        self._sni_certs: Dict[str, ssl.SSLContext] = {}
        self._ws_sockets: set = set()  # sockets handed to WS handlers
        super().__init__(server_address, handler_class)

    def handle_error(self, request, client_address):
        import sys
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)

    def get_request(self):
        client_socket, client_address = self.socket.accept()
        if self._ssl_ctx:
            try:
                first_byte = client_socket.recv(1, socket.MSG_PEEK)
                if first_byte == b'\x16':
                    client_socket = self._ssl_ctx.wrap_socket(
                        client_socket, server_side=True)
            except (OSError, ssl.SSLError) as e:
                logger.debug("TLS auto-detect failed for %s: %s", client_address, e)
        return client_socket, client_address

    def process_request(self, request, client_address):
        """Route connections: WS goes to own thread, HTTP to BaseHTTPRequestHandler.

        Reads headers from the socket to detect WebSocket upgrade.
        For WS: runs handler in own thread on raw socket.
        For HTTP: wraps socket with pre-read data and delegates to normal path.
        """
        # Read headers to determine HTTP vs WS (64KB max to prevent abuse)
        try:
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = request.recv(4096)
                if not chunk:
                    request.close()
                    return
                data += chunk
                if len(data) > 65536:
                    request.close()
                    return
        except Exception:
            try:
                request.close()
            except Exception:
                pass
            return

        # Private gateway — reject banned IPs before any processing
        try:
            from services.private_gateway import is_banned, is_enabled
            if is_enabled() and client_address and is_banned(client_address[0]):
                request.close()
                return
        except Exception:
            pass

        if b"Upgrade: websocket" in data or b"upgrade: websocket" in data:
            # WS — handle directly, NOT through BaseHTTPRequestHandler
            self._ws_sockets.add(id(request))
            t = threading.Thread(
                target=self._handle_ws_connection_with_data,
                args=(request, client_address, data),
                daemon=True)
            t.start()
            return

        # HTTP — put the already-read data back via a wrapper socket
        request = _PrefixedSocket(request, data)
        super().process_request(request, client_address)

    def shutdown_request(self, request):
        """Don't close sockets that were handed to WS handlers."""
        if id(request) in self._ws_sockets:
            self._ws_sockets.discard(id(request))
            return  # WS handler will close it
        super().shutdown_request(request)

    def _handle_ws_connection_with_data(self, sock, client_address, data):
        """Handle a WebSocket connection directly on the raw socket.

        Args:
            data: Already-read HTTP request bytes.
        """
        import base64, hashlib

        try:
            header_end = data.index(b"\r\n\r\n") + 4
            header_text = data[:header_end].decode("latin-1", errors="replace")

            # Parse
            lines = header_text.split("\r\n")
            parts = lines[0].split()
            path = parts[1].split("?")[0] if len(parts) >= 2 else "/"
            query = parts[1].split("?", 1)[1] if len(parts) >= 2 and "?" in parts[1] else ""

            headers = {}
            for line in lines[1:]:
                if ":" in line:
                    k, v = line.split(":", 1)
                    headers[k.strip()] = v.strip()

            # Private gateway check for WebSocket connections
            try:
                from services.private_gateway import is_enabled, is_banned, _verify_cookie, _COOKIE_NAME
                if is_enabled():
                    ip = client_address[0] if client_address else "0.0.0.0"
                    if is_banned(ip):
                        sock.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\n")
                        sock.close()
                        return
                    cookie_header = headers.get("Cookie", "")
                    gw_ok = False
                    for part in cookie_header.split(";"):
                        part = part.strip()
                        if part.startswith(_COOKIE_NAME + "="):
                            cookie_val = part[len(_COOKIE_NAME) + 1:]
                            if _verify_cookie(cookie_val, ip):
                                gw_ok = True
                                break
                    if not gw_ok:
                        sock.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\n")
                        sock.close()
                        return
            except Exception:
                pass

            # Match route
            result = self._route_registry.match("GET", path)
            if result is None or not result[0].ws_handler:
                sock.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                sock.close()
                return

            entry, path_params = result

            # RFC 6455 handshake
            ws_key = headers.get("Sec-WebSocket-Key", "")
            if not ws_key:
                sock.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                sock.close()
                return

            _MAGIC = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
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
            sock.sendall(response.encode("latin-1"))

            # Run WS handler (blocking — in this thread)
            entry.ws_handler(sock, path_params, {
                "path": path,
                "query": query,
                "headers": headers,
                "remote_addr": client_address[0] if client_address else "",
            })

        except Exception as e:
            logger.debug("WS connection error: %s", e)
        finally:
            try:
                sock.close()
            except Exception:
                pass



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

        # Build default SSL context if configured
        self._default_ssl_ctx = self._build_ssl_context()

        self._server = _HTTPServerWithRegistry(
            (self._host, self._port),
            _RequestHandler,
            self._registry,
            self._request_timeout,
        )

        # Auto-detect TLS: don't wrap the server socket globally.
        # Instead, wrap per-connection in get_request() (see _AutoTLSServer).
        if self._default_ssl_ctx:
            self._server._ssl_ctx = self._default_ssl_ctx
            self._server._sni_certs = self._sni_certs
            proto = "HTTP+HTTPS"
        else:
            proto = "HTTP"

        self._server_thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
            name=f"http-listener-{self._port}",
        )
        self._server_thread.start()

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
            self._server.shutdown()
            self._server = None
        if self._server_thread:
            self._server_thread.join(timeout=5)
            self._server_thread = None

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
