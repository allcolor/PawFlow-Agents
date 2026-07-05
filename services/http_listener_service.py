"""HTTP Listener Service — shared HTTP server for multiple flows.

A singleton-per-port service that starts a threaded HTTP server and
dispatches incoming requests to registered flows/tasks based on
method + URL pattern matching.

Multiple flows can register routes on the same port as long as patterns
don't conflict.  When no route matches, 504/404 is returned directly.

The request handler, threaded server and shared base types live in sibling
modules (_http_base, _http_request, _http_server) to keep files <=800 lines;
the public surface (HTTPListenerService, PendingRequest, RouteRegistry) is
re-exported here so the services.http_listener_service import path is unchanged.
"""

import json
import logging
import ssl
import threading
from typing import Any, Dict, List, Optional

from core.base_service import BaseService

from services._http_base import (  # noqa: F401
    PendingRequest,
    RouteConflictError,
    RouteEntry,
    RouteRegistry,
    _GlobalRateLimiter,
    _SECURITY_HEADERS,
    _emit_timing_summary,
    _rate_limit_policy,
    _request_action_label,
)
from services._http_request import _RequestHandler  # noqa: F401
from services._http_server import _HTTPServerWithRegistry  # noqa: F401

logger = logging.getLogger(__name__)


# Singleton registry: port -> service instance (in-process)
_instances: Dict[int, "HTTPListenerService"] = {}
_instances_lock = threading.Lock()


class HTTPListenerService(BaseService):
    """Shared HTTP listener service — global, one per port.

    Multiple flows can share the same service (= same port).
    Each flow registers its routes; the service dispatches incoming
    requests to the right flow based on method + URL pattern.

    Singleton per port via _instances dict. Also registered in
    ServiceRegistry for discoverability by code outside flows.

    Features:
    - Auto-detect TLS: peek first byte, if 0x16 → SSL, else plain HTTP
    - Multi-cert SNI: register_cert(hostname, certfile, keyfile)
    - WebSocket upgrade support on any route with ws_handler

    Config:
        host: str = "0.0.0.0"
        port: int
        ssl_certfile: str = ""  (path to default PEM certificate)
        ssl_keyfile: str = ""   (path to default PEM private key)
        ssl_keyfile_password: str = "" (optional key password)
    """

    TYPE = "httpListener"
    PARAMETERS = {
        "host": {
            "type": "string", "required": False, "default": "0.0.0.0",
            "description": "Bind address",
        },
        "port": {
            "type": "integer", "required": True,
            "description": "Listen port",
        },
        "max_dispatch_threads": {
            "type": "integer", "required": False, "default": 128,
            "description": "Maximum concurrent HTTP/WebSocket dispatch threads",
        },
        "header_read_timeout": {
            "type": "float", "required": False, "default": 3.0,
            "description": "Seconds to wait for request headers before closing slow or half-open clients",
        },
        "ssl_certfile": {
            "type": "string", "required": False, "default": "",
            "description": "Path to SSL certificate (PEM)",
        },
        "ssl_keyfile": {
            "type": "string", "required": False, "default": "",
            "description": "Path to SSL private key (PEM)",
        },
        "ssl_keyfile_password": {
            "type": "string", "required": False, "default": "",
            "description": "Password for encrypted key",
        },
        "ssl_service_id": {
            "type": "string", "required": False, "default": "",
            "description": "SSLContextService ID (alternative to certfile/keyfile)",
        },
        "private_gateway_service_id": {
            "type": "service_ref", "service_type": "privateGateway",
            "required": False, "default": "",
            "description": "PrivateGateway service protecting this listener. Leave empty for no listener-level private gateway.",
        },
    }

    @classmethod
    def get_for_port(cls, port: int) -> Optional["HTTPListenerService"]:
        """Find the HTTPListenerService for a port."""
        with _instances_lock:
            return _instances.get(port)

    @classmethod
    def all_instances(cls) -> Dict[int, "HTTPListenerService"]:
        """Snapshot of every running listener keyed by port."""
        with _instances_lock:
            return dict(_instances)

    def __new__(cls, config=None):
        if config is None:
            config = {}
        from core.expression import LazyResolveDict
        if not isinstance(config, LazyResolveDict):
            config = LazyResolveDict(config or {})
        if "port" not in config or config.get("port") in {None, ""}:
            raise ValueError("HTTPListenerService requires port")
        port = int(config.get("port"))
        with _instances_lock:
            if port in _instances:
                return _instances[port]
        return super().__new__(cls)

    def __init__(self, config: Dict[str, Any]):
        if hasattr(self, '_port'):
            self._update_runtime_config(config or {})
            return  # already initialized (singleton)
        super().__init__(config)
        self._host = self.config.get("host", "0.0.0.0")  # nosec B104 - listener bind is explicit configuration.
        if "port" not in self.config or self.config.get("port") in {None, ""}:
            raise ValueError("HTTPListenerService requires port")
        self._port = int(self.config.get("port"))
        self._max_dispatch_threads = self.config.get("max_dispatch_threads")
        self._header_read_timeout = self.config.get("header_read_timeout")
        self._private_gateway_service_id = self.config.get("private_gateway_service_id", "")

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

    def _update_runtime_config(self, config: Dict[str, Any]) -> None:
        """Apply load-bearing config when another flow reuses this port."""
        if not isinstance(config, dict):
            return
        old_ssl = (
            self._ssl_certfile,
            self._ssl_keyfile,
            self._ssl_keyfile_password,
        )
        self.config.update(config)
        self._max_dispatch_threads = self.config.get("max_dispatch_threads", self._max_dispatch_threads)
        self._header_read_timeout = self.config.get("header_read_timeout", self._header_read_timeout)
        self._private_gateway_service_id = self.config.get("private_gateway_service_id", self._private_gateway_service_id)
        self._ssl_certfile = self.config.get("ssl_certfile", self._ssl_certfile)
        self._ssl_keyfile = self.config.get("ssl_keyfile", self._ssl_keyfile)
        self._ssl_keyfile_password = self.config.get("ssl_keyfile_password", self._ssl_keyfile_password)
        if self._server is not None:
            self._server._private_gateway = self._resolve_private_gateway()
            new_ssl = (
                self._ssl_certfile,
                self._ssl_keyfile,
                self._ssl_keyfile_password,
            )
            if new_ssl != old_ssl:
                self._default_ssl_ctx = self._build_ssl_context()
                self._server._ssl_ctx = self._default_ssl_ctx
                self._server._sni_certs = self._sni_certs

    def get_parameter_schema(self) -> Dict[str, Any]:
        return self.PARAMETERS

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

        Registers self in ServiceRegistry as _http_listener_{port}.
        Uses auto-detect TLS: if SSL is configured, accepts both plain
        and TLS connections on the same port (peek first byte).
        """
        with self._ref_lock:
            self._ref_count += 1
            if self._server is not None:
                return self._server  # already running

        # Build default SSL context if configured
        self._default_ssl_ctx = self._build_ssl_context()
        private_gateway = self._resolve_private_gateway()

        self._server = _HTTPServerWithRegistry(
            (self._host, self._port),
            _RequestHandler,
            self._registry,
            max_dispatch_threads=self._max_dispatch_threads,
            header_read_timeout=self._header_read_timeout,
            private_gateway=private_gateway,
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

        logger.info(f"HTTPListenerService started on {proto} {self._host}:{self._port}")
        return self._server

    def _resolve_private_gateway(self):
        service_id = str(self._private_gateway_service_id or "").strip()
        if not service_id:
            return None
        try:
            from core.service_registry import ServiceRegistry
            svc = ServiceRegistry.get_instance().resolve(service_id)
            if svc and hasattr(svc, "check_request") and hasattr(svc, "check_ws"):
                return svc
            logger.warning("Private gateway service '%s' was not found or is invalid", service_id)
        except Exception as exc:
            logger.warning("Failed to resolve private gateway service '%s': %s", service_id, exc)
        return None

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

    def register_cert(self, hostname: str, certfile: str, keyfile: str = "",  # nosec B107
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

    @property
    def public_hostname(self) -> str:
        """Best-effort hostname that matches the configured cert.

        Used by components that need to hand an HTTPS URL to clients who
        verify the cert (e.g. the relay-proxy URL embedded into CC's
        ANTHROPIC_BASE_URL). Resolution order:
          1. explicit `public_hostname` config key
          2. first SNI-registered hostname (register_cert mapped it)
          3. parse the Subject CN / first SAN of the default cert file
          4. "" (caller falls back to LAN IP + skips cert verify)

        Returning "" is a signal to the caller that no hostname binds
        to the cert chain — it should then decide whether a bare IP URL
        + cert-skip is acceptable for its context.
        """
        # 1. Explicit config override
        _cfg = self.config.get("public_hostname", "") or ""
        if _cfg:
            return _cfg
        # 2. First SNI-registered hostname
        for _h in self._sni_certs.keys():
            if _h:
                return _h
        # 3. Parse Subject CN / SAN from the default cert
        if self._ssl_certfile:
            try:
                import ssl as _ssl_mod
                _info = _ssl_mod._ssl._test_decode_cert(self._ssl_certfile)
                # Try SAN first (modern certs prefer SAN over CN)
                for _san_tuple in (_info.get("subjectAltName") or ()):
                    if len(_san_tuple) == 2 and _san_tuple[0] == "DNS":
                        return _san_tuple[1]
                # Fall back to CN in Subject
                for _rdn in (_info.get("subject") or ()):
                    for _oid, _val in _rdn:
                        if _oid == "commonName":
                            return _val
            except Exception:
                logger.debug(
                    "public_hostname cert parse failed", exc_info=True)
        return ""

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

        with _instances_lock:
            _instances.pop(self._port, None)

        logger.info(f"HTTPListenerService stopped on port {self._port}")

    # -- Route management --

    def register_route(self, method: str, pattern: str, owner_id: str, callback,
                       ws_handler=None, public: bool = False,
                       private_only: bool = False,
                       gateway_exempt: bool = False) -> RouteEntry:
        """Register a route for a flow/task.

        Args:
            ws_handler: Optional WebSocket handler callable(socket, path_params, meta).
                        If set, WebSocket upgrade requests on this route are accepted
                        and the handler is called with the raw socket after handshake.
            public: if True, session auth is skipped for this route; the
                    callback is responsible for its own auth. Private gateway
                    still applies unless private_only is also true.
            private_only: if True, only clients with private IPs (RFC 1918 /
                    localhost) are accepted — even if the route is public.
            gateway_exempt: if True, the private gateway challenge is skipped
                    for this route while public IPs are still accepted — use
                    for provider callbacks (media webhooks) whose URL carries
                    its own unguessable token credential.
        """
        return self._registry.register(method, pattern, owner_id, callback,
                                        ws_handler=ws_handler,
                                        public=public, private_only=private_only,
                                        gateway_exempt=gateway_exempt)

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
            logger.warning(f"Request {request_id} not found (already responded or listener stopped)")
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


# Auto-register (deliberate late import to avoid a circular import at module load)
from core import ServiceFactory  # noqa: E402
ServiceFactory.register(HTTPListenerService)
