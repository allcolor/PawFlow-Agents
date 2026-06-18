"""HTTP request handler + socket helpers for the HTTP listener service.

Extracted from http_listener_service.py to keep files <=800 lines. Depends
downward on services._http_base.
"""

import json
import logging
import mimetypes
import threading
import time
import uuid
from pathlib import Path
from http.server import BaseHTTPRequestHandler
from typing import Dict, Tuple
from urllib.parse import parse_qs

from services._http_base import (
    PendingRequest,
    RouteRegistry,
    _GLOBAL_RATE_LIMITER,
    _HTTP_TIMING_DIAG_MS,
    _SECURITY_HEADERS,
    _emit_timing_summary,
    _is_long_lived_stream_path,
    _rate_limit_policy,
    _request_action_label,
)

logger = logging.getLogger("services.http_listener_service")  # canonical name preserved across the module split


class _RequestHandler(BaseHTTPRequestHandler):
    """Handler dispatching to the RouteRegistry on the server."""

    _chat_js_cache_lock = threading.RLock()
    _chat_js_cache: Dict[str, Tuple[int, int, bytes]] = {}

    # Silence default log output
    def log_message(self, format, *args):
        logger.debug(f"HTTP: {format % args}")

    def _header_sent(self, name: str) -> bool:
        prefix = (name.lower() + ":").encode("latin-1")
        return any(bytes(line).lower().startswith(prefix)
                   for line in getattr(self, "_headers_buffer", []))

    def end_headers(self):
        path = getattr(self, "path", "").split("?", 1)[0]
        for name, value in _SECURITY_HEADERS.items():
            if path.startswith("/code/") and name.lower() == "x-frame-options":
                continue
            if not self._header_sent(name):
                self.send_header(name, value)
        if path.startswith("/code/"):
            if not self._header_sent("Cross-Origin-Embedder-Policy"):
                self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
            if not self._header_sent("Cross-Origin-Opener-Policy"):
                self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        super().end_headers()

    def _check_global_rate_limit(self, path: str) -> bool:
        policy = _rate_limit_policy(path)
        if not policy:
            return True
        bucket, limit, window_s = policy
        ip = self.client_address[0] if self.client_address else "unknown"
        ok, retry_after = _GLOBAL_RATE_LIMITER.allow(ip, bucket, limit, window_s)
        if ok:
            return True
        self.send_response(429)
        self.send_header("Content-Type", "application/json")
        self.send_header("Retry-After", str(int(retry_after)))
        self.end_headers()
        self.wfile.write(b'{"error": "Too Many Requests"}')
        return False

    def _handle_chat_js_asset(self, path: str) -> bool:
        """Serve built-in chat JS assets without going through the flow DAG."""
        if not path.startswith("/chat/js/"):
            return False

        from pathlib import Path
        from urllib.parse import unquote
        import mimetypes

        rel = unquote(path[len("/chat/js/"):]).replace("\\", "/")
        if not rel or rel.startswith("/") or ".." in rel.split("/"):
            body = b'{"error": "Invalid path"}'
            self.send_response(403)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return True

        root = Path(__file__).resolve().parent.parent / "tasks" / "io" / "chat_ui"
        target = (root / rel).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError:
            body = b'{"error": "Invalid path"}'
            self.send_response(403)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return True

        if not target.is_file():
            body = b'{"error": "Not found"}'
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return True

        stat = target.stat()
        cache_key = str(target)
        cache_sig = (stat.st_mtime_ns, stat.st_size)
        with self._chat_js_cache_lock:
            cached = self._chat_js_cache.get(cache_key)
            if cached and cached[:2] == cache_sig:
                body = cached[2]
            else:
                body = target.read_bytes()
                self._chat_js_cache[cache_key] = (*cache_sig, body)

        mime_type, _ = mimetypes.guess_type(str(target))
        self.send_response(200)
        self.send_header("Content-Type", mime_type or "application/octet-stream")
        self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        self.send_header("Content-Length", str(len(body)))
        if hasattr(self, '_renew_cookie') and self._renew_cookie:
            self.send_header("Set-Cookie", self._renew_cookie)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        return True

    def _send_json_error(self, status: int, message: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(message)))
        if hasattr(self, '_renew_cookie') and self._renew_cookie:
            self.send_header("Set-Cookie", self._renew_cookie)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(message)

    @staticmethod
    def _filestore_request_is_public(path: str, query: str) -> bool:
        """True when a /files/<id> GET self-authenticates (no session needed).

        A public file is reachable by anyone; a gateway_key file is reachable
        by anyone presenting the matching ?k=. Mirrors the bypass already in
        services.private_gateway and tasks.io.validate_session_auth so the
        inline session gate doesn't 401 a valid keyed request.
        """
        file_id = path.split("/")[2] if len(path.split("/")) >= 3 else ""
        if not file_id:
            return False
        try:
            from core.file_store import (
                FileStore, ACCESS_PUBLIC, ACCESS_GATEWAY_KEY)
            store = FileStore.instance()
            level = store.get_access_level(file_id)
            if level == ACCESS_PUBLIC:
                return True
            if level == ACCESS_GATEWAY_KEY:
                key = (parse_qs(query or "").get("k") or [""])[0]
                return bool(key and store.check_access(
                    file_id, gateway_key=key))
        except Exception:
            logger.debug("filestore public-access probe failed", exc_info=True)
        return False

    def _handle_filestore_download(self, path_params: Dict[str, str],
                                   query: str, session) -> bool:
        """Stream FileStore downloads directly from disk.

        Large exports must not travel through FlowFile content queues: the
        default queue byte threshold is 100 MB, so a 180 MB archive can be
        produced successfully and then stall forever before send_response.
        """
        file_id = path_params.get("file_id", "")
        if not file_id:
            self._send_json_error(400, b'{"error": "No file ID provided"}')
            return True

        from core.file_store import FileStore

        user_id = ""
        if session and session is not True:
            user_id = getattr(session, "username", "") or ""

        query_params = parse_qs(query or "")
        gateway_key = (query_params.get("k") or [""])[0]

        store = FileStore.instance()
        if not store.exists(file_id):
            self._send_json_error(404, b'{"error": "File not found or expired"}')
            return True
        if not store.check_access(file_id, user_id=user_id,
                                  gateway_key=gateway_key):
            self._send_json_error(403, b'{"error": "Access denied"}')
            return True

        path = store.get_disk_path(file_id, user_id=user_id,
                                   gateway_key=gateway_key)
        metadata = store.get_metadata(file_id)
        if path is None or metadata is None:
            self._send_json_error(404, b'{"error": "File not found or expired"}')
            return True

        filename = metadata.get("filename", path.name)
        content_type = (metadata.get("content_type")
                        or mimetypes.guess_type(filename)[0]
                        or "application/octet-stream")
        size = path.stat().st_size

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition",
                         f'inline; filename="{Path(filename).name}"')
        self.send_header("Content-Length", str(size))
        if hasattr(self, '_renew_cookie') and self._renew_cookie:
            self.send_header("Set-Cookie", self._renew_cookie)
        self.end_headers()
        if self.command == "HEAD":
            return True

        try:
            with path.open("rb") as f:
                while True:
                    chunk = f.read(1024 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            logger.debug("Client disconnected during FileStore download %s",
                         file_id)
        except OSError as e:
            logger.debug("Client disconnected during FileStore download %s: %s",
                         file_id, e)
        return True

    def _handle(self):
        method = self.command
        path = self.path.split('?', 1)[0]
        query = self.path.split('?', 1)[1] if '?' in self.path else ""

        # Built-in health endpoint for container/orchestrator checks. Keep it
        # before auth/gateway routing; it returns no sensitive state.
        if method == "GET" and path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
            return

        if not self._check_global_rate_limit(path):
            return

        # Match route upfront to know if it's public (skip auth/gateway)
        # and/or private-only (reject external IPs).
        _match = self.server._route_registry.match(method, path)
        _matched = _match[0] if _match else None
        _is_public = bool(_matched and _matched.public)
        _is_private_only = bool(_matched and _matched.private_only)

        # FileStore downloads carry their own access control: a public file,
        # or a gateway_key file fetched with a valid ?k=, authenticates
        # without a session cookie. _handle_filestore_download still calls
        # check_access, so let these through the session gate instead of
        # 401-ing an unauthenticated (e.g. media-provider asset-proxy)
        # request that holds a valid key.
        if (not _is_public and method in ("GET", "HEAD")
                and path.startswith("/files/")):
            _is_public = self._filestore_request_is_public(path, query)

        # Private-only routes: reject public IPs immediately
        if _is_private_only:
            from core.relay_proxy_auth import is_private_ip
            _src_ip = self.client_address[0] if self.client_address else ""
            if not is_private_ip(_src_ip):
                self.send_response(403)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error": "Forbidden: external IP"}')
                return

        # Private gateway - checks all routes when this listener references one.
        _gateway = getattr(self.server, "_private_gateway", None)
        if _gateway is not None and _gateway.check_request(self):
            return

        # Session auth — skipped for public routes
        session = None
        if not _is_public:
            try:
                from core.security import SecurityManager
                sm = SecurityManager.get_instance()
                token = None
                cookie_header = self.headers.get("Cookie", "")
                if cookie_header:
                    for part in cookie_header.split(";"):
                        part = part.strip()
                        if part.startswith("pawflow_token="):
                            token = part[len("pawflow_token="):]
                            break
                if not token:
                    auth_header = self.headers.get("Authorization", "")
                    if auth_header and auth_header.lower().startswith("bearer "):
                        token = auth_header[7:].strip()
                session = sm.get_session(token) if token else None
                if (session is not None and session is not True
                        and getattr(session, "is_expired", False)):
                    logger.info("Expired session rejected for %s", path)
                    try:
                        sm.logout(token)
                    except Exception:
                        logger.debug("expired session cleanup failed", exc_info=True)
                    session = None
                if not session and token:
                    session = True if sm.validate_api_key(token) else None
                if not session:
                    # Browser requests → redirect to login; API requests → 401 JSON
                    _accept = self.headers.get("Accept", "")
                    if "text/html" in _accept:
                        self.send_response(302)
                        self.send_header("Location", "/auth/login")
                        self.send_header("Cache-Control", "no-store")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                    else:
                        self.send_response(401)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(b'{"error": "Unauthorized"}')
                    return
                # Renew cookie to extend browser-side expiry (sliding window)
                if token and cookie_header and session is not True:
                    self._renew_cookie = f"pawflow_token={token}; Path=/; Max-Age={int(sm._session_ttl)}; HttpOnly; SameSite=Lax"
            except Exception as e:
                logger.error("Session auth check failed: %s", e, exc_info=True)
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error": "Internal Server Error"}')
                return

        if method == "GET" and path.startswith("/chat/js/"):
            if self._handle_chat_js_asset(path):
                return

        # WebSocket upgrades are intercepted in _HTTPServerWithRegistry.process_request
        # BEFORE reaching this handler — so no WS detection needed here.

        # Read body
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""

        # ── Fast-path: /api/upload (multipart file upload → FileStore) ──
        if method == "POST" and path == "/api/upload":
            self._handle_upload(body, session)
            return

        # Collect headers
        headers = {k: v for k, v in self.headers.items()}

        # Inject scheme hint when connection is TLS (auto-detect)
        import ssl as _ssl_mod
        _sock = getattr(self, 'request', None) or getattr(self, 'connection', None)
        # Unwrap _PrefixedSocket to check the real socket
        _raw_sock = getattr(_sock, '_sock', _sock)
        _is_tls = isinstance(_raw_sock, _ssl_mod.SSLSocket)
        if _is_tls:
            if not any(k.lower() == 'x-forwarded-proto' for k in headers):
                headers['x-forwarded-proto'] = 'https'
        logger.debug("TLS detect: sock_type=%s is_tls=%s path=%s", type(_sock).__name__, _is_tls, path)

        # Match route
        registry: RouteRegistry = self.server._route_registry
        result = registry.match(method, path)

        if result is None:
            # Only the bare root redirects to /chat for authenticated users.
            # Any other unmatched path must 404 — never mask a missing route
            # with a redirect, it hides real bugs (e.g. unregistered VNC/proxy
            # routes would silently show the chat UI instead of failing).
            # Unauthenticated access is already handled earlier by the private
            # gateway / session-auth checks above.
            if method == "GET" and path == "/":
                _scheme = "https" if headers.get('x-forwarded-proto') == 'https' else "http"
                _host = headers.get('host') or headers.get('Host') or 'localhost'
                self.send_response(302)
                self.send_header("Location", f"{_scheme}://{_host}/chat")
                self.end_headers()
                return
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "error": "Not Found",
                "message": f"No route matches {method} {path}",
            }).encode())
            return

        entry, path_params = result

        if method in ("GET", "HEAD") and path.startswith("/files/"):
            if self._handle_filestore_download(path_params, query, session):
                return

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
        req.mark("recv")

        # Stamp the authenticated identity onto the request so downstream
        # routes (capability checks, ownership filters, etc.) can resolve
        # the requester without re-parsing cookies. `session` is set above
        # by the auth block: a Session object for cookie/bearer login,
        # the literal `True` for an API key (no Session object available),
        # or None for a public route (auth was skipped).
        if session is True:
            req.auth_is_api_key = True
        elif session is not None:
            req.auth_user_id = getattr(session, "username", "") or ""
            _role = getattr(session, "role", None)
            # Role can be an enum or a plain string depending on the auth
            # backend; serialise to lowercase string either way.
            req.auth_role = (getattr(_role, "value", None)
                              or str(_role) if _role else "").lower()
            req.auth_session_id = getattr(session, "session_id", "") or ""
            req.auth_is_api_key = False

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
        req.mark("dispatch")

        # Block until flow responds. NO TIMEOUT — project rule: only the
        # LLM watchdog has a timeout, nowhere else. While waiting for the
        # flow, move this request off the short dispatch pool so one stuck
        # flow response cannot starve unrelated UI/API requests.
        if not req.completed:
            if not self.server.transfer_current_dispatch_to_long_lived(
                    f"flow response wait {method} {path}"):
                self.server._pending_requests.pop(req.request_id, None)
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "error": "HTTP response wait capacity exceeded",
                    "request_id": req.request_id,
                    "path": path,
                }).encode("utf-8"))
                req.mark("send")
                return
        req.wait()
        # `respond` is marked from inside complete()/complete_stream(),
        # capturing when the flow actually handed us the response — not
        # when this thread woke up from the event.
        _waited = time.monotonic() - req.timing.get(
            "recv", req.timing.get("dispatch", time.monotonic()))
        _is_streaming_path = _is_long_lived_stream_path(path)
        _waited_ms = _waited * 1000.0
        if _waited_ms > _HTTP_TIMING_DIAG_MS and not _is_streaming_path:
            _action = _request_action_label(req)
            logger.warning("[http] slow response — %s %s%s took %.0fms "
                            "(request_id=%s, status=%d)",
                            method, path,
                            f" action={_action}" if _action else "",
                            _waited_ms, req.request_id[:8],
                            req.response_status)

        # Send the flow's response
        self.server._pending_requests.pop(req.request_id, None)
        self.send_response(req.response_status)
        for k, v in req.response_headers.items():
            # Set-Cookie requires separate headers per cookie (RFC 6265)
            if k == "Set-Cookie" and "\n" in v:
                for cv in v.split("\n"):
                    if cv.strip():
                        self.send_header(k, cv.strip())
            else:
                self.send_header(k, v)
        if "Content-Type" not in req.response_headers:
            self.send_header("Content-Type", "application/octet-stream")
        # Renew session cookie (sliding window)
        if hasattr(self, '_renew_cookie') and self._renew_cookie:
            self.send_header("Set-Cookie", self._renew_cookie)
        self.end_headers()

        if req.response_stream is not None:
            # Streaming response — write chunks as they come
            if _is_long_lived_stream_path(path):
                self.server.transfer_current_dispatch_to_long_lived(
                    f"stream {path}", self.connection)
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
            finally:
                close_stream = getattr(req.response_stream, "close", None)
                if callable(close_stream):
                    try:
                        close_stream()
                    except Exception:
                        logger.debug("exception suppressed", exc_info=True)
        elif req.response_body:
            self.wfile.write(req.response_body)
        req.mark("send")
        _emit_timing_summary(req)

    def _handle_upload(self, body: bytes, session):
        """Fast-path handler for POST /api/upload.

        Parses multipart/form-data, stores each file in FileStore,
        returns JSON with file IDs. No FlowFile pipeline needed.
        """
        from email.parser import BytesParser
        from core.file_store import FileStore
        from core.file_ttl import resolve_ttl_seconds

        user_id = ""
        if session and session is not True:
            user_id = getattr(session, "username", "") or ""

        ct = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ct:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error": "Expected multipart/form-data"}')
            return

        # Parse multipart without the removed cgi module
        header = f"Content-Type: {ct}\r\n\r\n".encode()
        msg = BytesParser().parsebytes(header + body)

        # Extract optional conversation_id and explicit TTL from form fields
        conv_id = ""
        ttl = 0
        for part in msg.walk():
            if part.get_content_disposition() == "form-data" and not part.get_filename():
                name = part.get_param("name", header="content-disposition") or ""
                if name == "conversation_id":
                    conv_id = (part.get_payload(decode=True) or b"").decode().strip()
                elif name == "ttl":
                    try:
                        ttl = int((part.get_payload(decode=True) or b"").decode().strip() or "0")
                    except ValueError:
                        ttl = 0
        if ttl <= 0:
            ttl = resolve_ttl_seconds(
                conversation_id=conv_id,
                conv_keys=("webchat_upload_ttl_seconds", "attachment_ttl_seconds"),
                env_key="PAWFLOW_WEBCHAT_UPLOAD_TTL_SECONDS",
                default=3600,
            )
        else:
            ttl = max(60, ttl)

        store = FileStore.instance()
        results = []
        for part in msg.walk():
            disp = part.get_content_disposition()
            if disp != "form-data":
                continue
            filename = part.get_filename()
            if not filename:
                continue
            raw = part.get_payload(decode=True)
            if raw is None:
                continue
            mime = part.get_content_type() or "application/octet-stream"
            fid = store.store(
                filename, raw, mime,
                user_id=user_id or "_anonymous",
                conversation_id=conv_id or "_upload",
                ttl=ttl,
                category="upload",
            )
            results.append({
                "file_id": fid,
                "filename": filename,
                "mime_type": mime,
                "size": len(raw),
                "url": f"/files/{fid}/{filename}",
            })
            logger.info("Upload: %s (%s, %d bytes) -> %s",
                        filename, mime, len(raw), fid)

        resp = json.dumps({"ok": True, "files": results}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        if hasattr(self, "_renew_cookie") and self._renew_cookie:
            self.send_header("Set-Cookie", self._renew_cookie)
        self.end_headers()
        self.wfile.write(resp)

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


