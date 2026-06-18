"""Threaded HTTP server with route registry for the HTTP listener service.

Extracted from http_listener_service.py to keep files <=800 lines. Depends
downward on services._http_base and services._http_request.
"""

import logging
import os
import socket
import ssl
import threading
from http.server import HTTPServer
from socketserver import ThreadingMixIn
from typing import Dict, Optional

from services._http_base import (
    PendingRequest,
    _DEFAULT_HEADER_READ_TIMEOUT,
    _DEFAULT_MAX_DISPATCH_THREADS,
    _DEFAULT_MAX_LONG_DISPATCH_THREADS,
)
from services._http_request import (
    _PrefixedSocket,
)

logger = logging.getLogger("services.http_listener_service")  # canonical name preserved across the module split


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

    def __init__(self, server_address, handler_class, route_registry,
                 max_dispatch_threads=None, header_read_timeout=None,
                 private_gateway=None):
        self._route_registry = route_registry
        self._private_gateway = private_gateway
        self._pending_requests: Dict[str, PendingRequest] = {}
        self._ssl_ctx: Optional[ssl.SSLContext] = None
        self._sni_certs: Dict[str, ssl.SSLContext] = {}
        self._ws_sockets: set = set()  # sockets handed to WS handlers
        self._max_dispatch_threads = self._resolve_int_config(
            max_dispatch_threads,
            "PAWFLOW_HTTP_MAX_DISPATCH_THREADS",
            _DEFAULT_MAX_DISPATCH_THREADS,
            minimum=1)
        self._header_read_timeout = self._resolve_float_config(
            header_read_timeout,
            "PAWFLOW_HTTP_HEADER_TIMEOUT",
            _DEFAULT_HEADER_READ_TIMEOUT,
            minimum=0.1)
        self._dispatch_slots = threading.BoundedSemaphore(self._max_dispatch_threads)
        self._dispatch_active = 0
        self._dispatch_rejected = 0
        self._dispatch_lock = threading.Lock()
        self._dispatch_context = threading.local()
        self._max_long_dispatch_threads = self._resolve_int_config(
            None,
            "PAWFLOW_HTTP_MAX_LONG_DISPATCH_THREADS",
            _DEFAULT_MAX_LONG_DISPATCH_THREADS,
            minimum=1)
        self._long_dispatch_slots = threading.BoundedSemaphore(
            self._max_long_dispatch_threads)
        self._long_dispatch_active = 0
        self._long_dispatch_rejected = 0
        super().__init__(server_address, handler_class)

    @staticmethod
    def _resolve_int_config(config_value, env_name: str, default: int,
                            minimum: int = 1) -> int:
        raw = config_value if config_value is not None else os.getenv(env_name, "")
        try:
            value = int(raw or default)
        except (TypeError, ValueError):
            value = default
        return max(minimum, value)

    @staticmethod
    def _resolve_float_config(config_value, env_name: str, default: float,
                              minimum: float = 0.1) -> float:
        raw = config_value if config_value is not None else os.getenv(env_name, "")
        try:
            value = float(raw or default)
        except (TypeError, ValueError):
            value = default
        return max(minimum, value)

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
                else:
                    # Plain HTTP on an HTTPS port. By default redirect to
                    # HTTPS, but allow public + private_only routes (proxy
                    # endpoints with token auth) to pass through unencrypted
                    # since they're LAN-only and TLS cert may not match.
                    if self._http_route_allows_plain(client_socket, client_address):
                        return client_socket, client_address
                    self._redirect_to_https(client_socket)
                    raise OSError("HTTP→HTTPS redirect sent")
            except (ssl.SSLError,) as e:
                logger.debug("TLS auto-detect failed for %s: %s", client_address, e)
            except OSError:
                raise  # re-raise redirect OSError
        return client_socket, client_address

    def _http_route_allows_plain(self, sock, client_address):
        """Peek at the request to decide if plain HTTP is allowed.

        Returns True if the matched route is public AND private_only,
        leaving the socket with the consumed bytes re-injected via a
        wrapper. The caller should dispatch normally.
        """
        try:
            # Peek the request line + Host header (~256 bytes is enough)
            sock.settimeout(2)
            data = b""
            while b"\r\n\r\n" not in data and len(data) < 8192:
                chunk = sock.recv(4096, socket.MSG_PEEK)
                if not chunk or len(chunk) <= len(data):
                    break
                data = chunk
            if b"\r\n" not in data:
                return False
            request_line = data.split(b"\r\n")[0].decode("latin-1", errors="replace")
            parts = request_line.split()
            if len(parts) < 2:
                return False
            method, path = parts[0], parts[1].split('?', 1)[0]
            _match = self._route_registry.match(method, path)
            if not _match:
                return False
            entry = _match[0]
            if not (getattr(entry, "public", False)
                    and getattr(entry, "private_only", False)):
                return False
            # Reset to no-timeout so the dispatcher controls it
            try:
                sock.settimeout(None)
            except Exception:
                logger.debug("Ignored exception", exc_info=True)
            return True
        except Exception:
            return False

    def _redirect_to_https(self, sock):
        """Send a 301 redirect from HTTP to HTTPS — caller checked already
        that the route is not exempt from redirection."""
        try:
            # Read the HTTP request line to get the path
            data = b""
            sock.settimeout(2)
            while b"\r\n" not in data and len(data) < 4096:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
            request_line = data.split(b"\r\n")[0].decode("latin-1", errors="replace")
            parts = request_line.split()
            path = parts[1] if len(parts) >= 2 else "/"
            # Extract Host header
            host = ""
            for line in data.decode("latin-1", errors="replace").split("\r\n")[1:]:
                if line.lower().startswith("host:"):
                    host = line.split(":", 1)[1].strip()
                    break
            if not host:
                host = sock.getsockname()[0]
                port = sock.getsockname()[1]
                if port != 443:
                    host = f"{host}:{port}"
            redirect_url = f"https://{host}{path}"
            response = (
                f"HTTP/1.1 301 Moved Permanently\r\n"
                f"Location: {redirect_url}\r\n"
                f"Content-Length: 0\r\n"
                f"Connection: close\r\n\r\n"
            )
            sock.sendall(response.encode("latin-1"))
        except Exception:
            logger.debug("Ignored exception", exc_info=True)
        finally:
            try:
                sock.close()
            except Exception:
                logger.debug("Ignored exception", exc_info=True)

    def process_request(self, request, client_address):
        """Spawn a bounded dispatch thread without blocking the accept loop."""
        if not self._dispatch_slots.acquire(blocking=False):
            with self._dispatch_lock:
                self._dispatch_rejected += 1
                rejected = self._dispatch_rejected
            if rejected == 1 or rejected % 100 == 0:
                logger.warning(
                    "HTTP dispatch saturated: active=%d max=%d rejected=%d; closing %s",
                    self._dispatch_active, self._max_dispatch_threads,
                    rejected, client_address)
            try:
                request.close()
            except Exception:
                logger.debug("Ignored exception", exc_info=True)
            return

        with self._dispatch_lock:
            self._dispatch_active += 1

        def _run_dispatch():
            self._dispatch_context.short_slot_released = False
            self._dispatch_context.long_slot_acquired = False
            try:
                self._dispatch_request(request, client_address)
            finally:
                if getattr(self._dispatch_context, "long_slot_acquired", False):
                    self._release_long_dispatch_slot()
                if not getattr(self._dispatch_context, "short_slot_released", False):
                    self._release_short_dispatch_slot()
                self._dispatch_context.short_slot_released = False
                self._dispatch_context.long_slot_acquired = False

        t = threading.Thread(
            target=_run_dispatch,
            daemon=True,
            name="http-dispatch")
        t.start()

    def _release_short_dispatch_slot(self):
        if getattr(self._dispatch_context, "short_slot_released", False):
            return
        with self._dispatch_lock:
            self._dispatch_active = max(0, self._dispatch_active - 1)
        self._dispatch_slots.release()
        self._dispatch_context.short_slot_released = True

    def _release_long_dispatch_slot(self):
        if not getattr(self._dispatch_context, "long_slot_acquired", False):
            return
        with self._dispatch_lock:
            self._long_dispatch_active = max(0, self._long_dispatch_active - 1)
        self._long_dispatch_slots.release()
        self._dispatch_context.long_slot_acquired = False

    def transfer_current_dispatch_to_long_lived(self, reason: str,
                                                request=None) -> bool:
        """Move this connection off the short HTTP dispatch pool.

        Long-lived WebSocket and streaming responses must not occupy the same
        bounded pool as short API requests. Otherwise a few stuck browser tabs,
        VNC sessions, or relay streams can make /api/ui look like a 502 outage
        to the reverse proxy even though the process is still alive.
        """
        if getattr(self._dispatch_context, "long_slot_acquired", False):
            return True
        if not self._long_dispatch_slots.acquire(blocking=False):
            with self._dispatch_lock:
                self._long_dispatch_rejected += 1
                rejected = self._long_dispatch_rejected
            if rejected == 1 or rejected % 100 == 0:
                logger.warning(
                    "HTTP long dispatch saturated: active=%d max=%d "
                    "rejected=%d reason=%s",
                    self._long_dispatch_active, self._max_long_dispatch_threads,
                    rejected, reason)
            if request is not None:
                try:
                    request.close()
                except Exception:
                    logger.debug("Ignored exception", exc_info=True)
            return False
        with self._dispatch_lock:
            self._long_dispatch_active += 1
        self._dispatch_context.long_slot_acquired = True
        self._release_short_dispatch_slot()
        return True

    def _dispatch_request(self, request, client_address):
        """Route connections: read headers, detect WS vs HTTP, handle.

        Runs in its own thread so the accept loop is never blocked by
        slow clients, half-open TCP, or browser preflight connections.
        """
        # Read headers to determine HTTP vs WS (64KB max to prevent abuse)
        try:
            request.settimeout(self._header_read_timeout)
        except OSError:
            # Socket already closed by the client — bail out cleanly
            return
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
                logger.debug("Ignored exception", exc_info=True)
            return
        finally:
            try:
                request.settimeout(None)
            except Exception:
                logger.debug("Ignored exception", exc_info=True)

        # Private gateway - reject banned IPs before any processing.
        try:
            _gateway = getattr(self, "_private_gateway", None)
            if (_gateway is not None and _gateway.is_enabled()
                    and client_address and _gateway.is_banned(client_address[0])):
                request.close()
                return
        except Exception:
            logger.debug("Ignored exception", exc_info=True)

        if b"Upgrade: websocket" in data or b"upgrade: websocket" in data:
            # WS — handle directly on raw socket.
            if not self.transfer_current_dispatch_to_long_lived("websocket", request):
                return
            sock_id = id(request)
            self._ws_sockets.add(sock_id)
            try:
                self._handle_ws_connection_with_data(request, client_address, data)
            finally:
                self._ws_sockets.discard(sock_id)
            return

        # HTTP — put the already-read data back via a wrapper socket
        request = _PrefixedSocket(request, data)
        try:
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)

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
        import base64
        import hashlib

        try:
            header_end = data.index(b"\r\n\r\n") + 4
            header_text = data[:header_end].decode("latin-1", errors="replace")

            # Parse
            lines = header_text.split("\r\n")
            parts = lines[0].split()
            path = parts[1].split("?")[0] if len(parts) >= 2 else "/"
            query = parts[1].split("?", 1)[1] if len(parts) >= 2 and "?" in parts[1] else ""

            headers = {}
            headers_lc = {}
            for line in lines[1:]:
                if ":" in line:
                    k, v = line.split(":", 1)
                    key = k.strip()
                    value = v.strip()
                    headers[key] = value
                    headers_lc[key.lower()] = value

            _remote = client_address[0] if client_address else "?"

            result = self._route_registry.match("GET", path)
            entry = result[0] if result else None
            path_params = result[1] if result else {}
            _is_public = bool(entry and entry.public)
            _is_private_only = bool(entry and entry.private_only)

            if _is_private_only:
                from core.relay_proxy_auth import is_private_ip
                if not is_private_ip(_remote):
                    sock.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\n")
                    sock.close()
                    return

            # Internal-auth bypass for server-spawned components (CC
            # container MCP bridge, server-side relays). A valid
            # `pawflow_internal` cookie on server-spawned WebSocket routes skips
            # gateway + session checks. Route-level register-step token
            # auth (inside the tool relay register message) still runs.
            # Tokens are minted fresh per MCP config write and held
            # in-memory only.
            _internal_ok = False
            if (path.startswith("/ws/tools/")
                    or path.startswith("/ws/relay/")
                    or path.startswith("/ws/cc-interactive/events/")):
                try:
                    from core.internal_auth import validate_token
                    _ch = headers_lc.get("cookie", "")
                    for _p in _ch.split(";"):
                        _p = _p.strip()
                        if _p.startswith("pawflow_internal="):
                            if validate_token(_p[len("pawflow_internal="):]):
                                _internal_ok = True
                            break
                except Exception as _ie:
                    logger.error("internal-auth check failed: %s", _ie,
                                 exc_info=True)

            # Private gateway check for WebSocket connections
            try:
                _gateway = getattr(self, "_private_gateway", None)
                if (not _is_public
                        and not _internal_ok
                        and _gateway is not None
                        and _gateway.check_ws(path, headers, client_address, _internal_ok)):
                    logger.warning(
                        "[ws] rejected %s on %s: private gateway check failed",
                        _remote, path)
                    sock.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\n")
                    sock.close()
                    return
            except Exception as e:
                logger.error("WS gateway check failed: %s", e, exc_info=True)
                sock.sendall(b"HTTP/1.1 500 Internal Server Error\r\n\r\n")
                sock.close()
                return

            # Session auth check for WebSocket connections
            # (skipped when internal-auth already cleared the request —
            # CC container / server-side relays have no user session).
            # On success, captures the resolved identity into
            # `_ws_auth_user_id` / `_ws_auth_role` / `_ws_auth_session_id` /
            # `_ws_auth_is_api_key` so we can pass it through the WS
            # handler's `meta` dict (capability checks downstream consume
            # these the same way HTTP routes consume req.auth_*).
            _ws_auth_user_id = ""
            _ws_auth_role = ""
            _ws_auth_session_id = ""
            _ws_auth_is_api_key = False
            if not _internal_ok and not _is_public:
                try:
                    from core.security import SecurityManager
                    sm = SecurityManager.get_instance()
                    ws_token = None
                    cookie_header = headers_lc.get("cookie", "")
                    for part in cookie_header.split(";"):
                        part = part.strip()
                        if part.startswith("pawflow_token="):
                            ws_token = part[len("pawflow_token="):]
                            break
                    if not ws_token and "token=" in query:
                        from urllib.parse import parse_qs
                        ws_token = parse_qs(query).get("token", [""])[0]
                    if not ws_token:
                        logger.warning(
                            "[ws] rejected %s on %s: no session token "
                            "(expected pawflow_token cookie or ?token= query)",
                            _remote, path)
                        sock.sendall(b"HTTP/1.1 401 Unauthorized\r\n\r\n")
                        sock.close()
                        return
                    _ws_session = sm.get_session(ws_token)
                    if (_ws_session is not None
                            and getattr(_ws_session, "is_expired", False)):
                        logger.info("[ws] rejected %s on %s: expired session",
                                    _remote, path)
                        try:
                            sm.logout(ws_token)
                        except Exception:
                            logger.debug("expired WS session cleanup failed", exc_info=True)
                        sock.sendall(b"HTTP/1.1 401 Unauthorized\r\n\r\n")
                        sock.close()
                        return
                    if _ws_session is not None:
                        _ws_auth_user_id = getattr(_ws_session, "username", "") or ""
                        _r = getattr(_ws_session, "role", None)
                        _ws_auth_role = ((getattr(_r, "value", None)
                                           or str(_r)) if _r else "").lower()
                        _ws_auth_session_id = getattr(_ws_session, "session_id", "") or ""
                        _ws_auth_is_api_key = False
                    else:
                        _apikey_ok = bool(sm.validate_api_key(ws_token))
                        if not _apikey_ok:
                            logger.warning(
                                "[ws] rejected %s on %s: session token present but "
                                "invalid (not a live session nor API key)",
                                _remote, path)
                            sock.sendall(b"HTTP/1.1 401 Unauthorized\r\n\r\n")
                            sock.close()
                            return
                        _ws_auth_is_api_key = True
                except Exception as e:
                    logger.error("WS session auth check failed: %s", e, exc_info=True)
                    sock.sendall(b"HTTP/1.1 500 Internal Server Error\r\n\r\n")
                    sock.close()
                    return

            if entry is None or not entry.ws_handler:
                sock.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                sock.close()
                return

            # RFC 6455 handshake
            ws_key = headers_lc.get("sec-websocket-key", "")
            if not ws_key:
                sock.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                sock.close()
                return

            _MAGIC = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
            accept = base64.b64encode(
                hashlib.sha1(ws_key.encode() + _MAGIC, usedforsecurity=False).digest()
            ).decode()

            response = (
                f"HTTP/1.1 101 Switching Protocols\r\n"
                f"Upgrade: websocket\r\n"
                f"Connection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept}\r\n"
            )
            ws_protocol = headers_lc.get("sec-websocket-protocol", "")
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
                # Authenticated identity (mirrors PendingRequest.auth_*
                # for HTTP). Empty when the upgrade went through the
                # internal-auth bypass (CC container / server-spawned
                # relays — no user session attached).
                "auth_user_id": _ws_auth_user_id,
                "auth_role": _ws_auth_role,
                "auth_session_id": _ws_auth_session_id,
                "auth_is_api_key": _ws_auth_is_api_key,
                "auth_internal": _internal_ok,
            })

        except Exception as e:
            logger.debug("WS connection error: %s", e)
        finally:
            try:
                sock.close()
            except Exception:
                logger.debug("Ignored exception", exc_info=True)
