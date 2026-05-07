"""HTTP Listener Service — shared HTTP server for multiple flows.

A singleton-per-port service that starts a threaded HTTP server and
dispatches incoming requests to registered flows/tasks based on
method + URL pattern matching.

Multiple flows can register routes on the same port as long as patterns
don't conflict.  When no route matches, 504/404 is returned directly.
"""

import json
import logging
import os
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


_SECURITY_HEADERS = {
    # The chat UI pulls rxjs from jsDelivr and highlight.js (script + theme
    # CSS) from cdnjs. Both CDN origins are whitelisted explicitly so the
    # default 'self'-only policy doesn't break the page on first load.
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' "
        "https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
        "style-src 'self' 'unsafe-inline' "
        "https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
        "img-src 'self' data: blob:; "
        "media-src 'self' data: blob:; "
        "connect-src 'self' ws: wss:; "
        "frame-src 'self' blob: http: https:; "
        "frame-ancestors 'self'; "
        "object-src 'none'; base-uri 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


class _GlobalRateLimiter:
    """Small in-memory sliding-window limiter for login and API routes."""

    def __init__(self):
        self._lock = threading.Lock()
        self._hits: Dict[Tuple[str, str], List[float]] = {}

    def allow(self, key: str, bucket: str, limit: int, window_s: float) -> Tuple[bool, float]:
        now = time.time()
        cutoff = now - window_s
        with self._lock:
            hits = [ts for ts in self._hits.get((key, bucket), []) if ts > cutoff]
            if len(hits) >= limit:
                retry_after = max(1.0, window_s - (now - hits[0]))
                self._hits[(key, bucket)] = hits
                return False, retry_after
            hits.append(now)
            self._hits[(key, bucket)] = hits
            return True, 0.0

    def reset(self):
        with self._lock:
            self._hits.clear()


_GLOBAL_RATE_LIMITER = _GlobalRateLimiter()
_DEFAULT_MAX_DISPATCH_THREADS = 128
_DEFAULT_HEADER_READ_TIMEOUT = 3.0


def _rate_limit_policy(path: str) -> Optional[Tuple[str, int, float]]:
    if path.startswith("/auth") or path == "/_gateway":
        return ("login", 30, 60.0)
    if path.startswith("/api/"):
        return ("api", 600, 60.0)
    return None

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

    # Authenticated identity, populated by the HTTP handler AFTER the session
    # auth check passes. Routes downstream consume these to check ownership
    # against capability tokens (see core.capability_auth.verify_capability).
    # Empty when the request is on a public route (auth was skipped).
    auth_user_id: str = ""
    auth_role: str = ""
    auth_session_id: str = ""
    auth_is_api_key: bool = False

    # Response fields (filled by handleHTTPResponse task)
    response_status: int = 200
    response_headers: Dict[str, str] = field(default_factory=dict)
    response_body: bytes = b""

    # Streaming response (alternative to response_body)
    response_stream: Any = None  # iterator yielding bytes chunks

    # Synchronization
    _event: threading.Event = field(default_factory=threading.Event)
    completed: bool = False

    # Per-request timing — keyed milestone → monotonic seconds.
    # Filled in via mark(); the listener's emit_timing_summary() walks
    # the keys to produce a single grep-friendly log line per request.
    timing: Dict[str, float] = field(default_factory=dict)

    def mark(self, name: str) -> None:
        """Record a monotonic-clock milestone for this request.

        Idempotent: subsequent calls with the same name overwrite. Used
        by the HTTP listener and downstream tasks (httpReceiver, the
        response-emit path) so we can attribute every millisecond of a
        slow request without having to grep across multiple log lines.
        """
        self.timing[name] = time.monotonic()

    def wait(self) -> None:
        """Block until response is ready. NO TIMEOUT — project rule.

        If the flow never responds, this blocks forever. That's a real
        bug we want to surface (not mask with a 504), and the HTTP
        worker thread is a daemon — Ctrl+C kills it cleanly.
        """
        self._event.wait()

    def complete(self, status: int, headers: Dict[str, str], body: bytes):
        """Set the response and unblock the waiting HTTP handler."""
        self.mark("respond")
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
        self.mark("respond")
        self.response_status = status
        self.response_headers = headers
        self.response_stream = stream
        self.completed = True
        self._event.set()


_TIMING_SEGMENTS = (
    # (label, start_mark, end_mark) — order is wall-clock order. Each
    # segment shows wall-time spent in that phase. Missing marks (e.g.
    # streaming responses that never get a `send` mark on this thread,
    # or `enqueue`/`dequeue` only present for routes that go through
    # httpReceiver) are silently skipped — the summary then just omits
    # that segment rather than printing 0ms or NaN.
    ("recv→dispatch", "recv", "dispatch"),
    ("dispatch→enqueue", "dispatch", "enqueue"),
    ("enqueue→dequeue", "enqueue", "dequeue"),
    ("dequeue→respond", "dequeue", "respond"),
    ("respond→send", "respond", "send"),
)


def _request_action_label(req: "PendingRequest") -> str:
    if req.path != "/api/ui" or not req.body:
        return ""
    try:
        data = json.loads(req.body.decode("utf-8", errors="replace"))
        action = data.get("action", "") if isinstance(data, dict) else ""
        return str(action or "")[:80]
    except Exception:
        return ""


def _is_long_lived_stream_path(path: str) -> bool:
    return path == "/api/agent/events" or path.startswith("/relay-proxy/")


def _emit_timing_summary(req: "PendingRequest") -> None:
    """Emit one grep-friendly per-request timing line."""
    t = req.timing
    if not t or "recv" not in t:
        return
    is_long_stream = _is_long_lived_stream_path(req.path)
    segments = []
    for label, a, b in _TIMING_SEGMENTS:
        if is_long_stream and label == "respond→send":
            continue
        if a in t and b in t:
            segments.append(f"{label}={round((t[b] - t[a]) * 1000)}ms")
    last = (t.get("respond") if is_long_stream else None) or t.get("send") or t.get("respond") or t.get("dispatch")
    total = round((last - t["recv"]) * 1000) if last else 0
    action = _request_action_label(req)
    msg = "[http-timing] req_id=%s %s %s%s total=%dms %s status=%d"
    args = (
        req.request_id[:8], req.method, req.path,
        f" action={action}" if action else "",
        total, " ".join(segments), req.response_status,
    )
    if total > 5000 and not is_long_stream:
        logger.info(msg, *args)
    else:
        logger.debug(msg, *args)


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
    public: bool = False    # if True: skip session auth + private gateway
    private_only: bool = False  # if True: only accept private-IP clients


class RouteConflictError(Exception):
    """Raised when two flows register overlapping routes."""
    pass


class RouteRegistry:
    """Thread-safe registry of method+pattern -> handler."""

    def __init__(self):
        self._routes: List[RouteEntry] = []
        self._lock = threading.Lock()

    def register(self, method: str, pattern: str, owner_id: str, callback,
                 ws_handler=None, public: bool = False,
                 private_only: bool = False) -> RouteEntry:
        """Register a route.  Raises RouteConflictError on overlap.

        public=True skips session auth and private gateway checks (use for
        login pages, callbacks, proxy endpoints with their own token auth).
        private_only=True rejects non-RFC1918 clients even if public=True
        (use for proxy endpoints leaked URLs must not allow external abuse).
        """
        method = method.upper()
        regex = self._compile_pattern(pattern)

        with self._lock:
            for existing in self._routes:
                if existing.method == method and existing.pattern == pattern:
                    if existing.owner_id == owner_id:
                        # Same owner, same route — idempotent update
                        existing.callback = callback
                        existing.public = public
                        existing.private_only = private_only
                        return existing
                    raise RouteConflictError(
                        f"Route {method} {pattern} already registered by '{existing.owner_id}'"
                    )

            entry = RouteEntry(
                method=method, pattern=pattern, regex=regex,
                owner_id=owner_id, callback=callback,
                ws_handler=ws_handler,
                public=public, private_only=private_only,
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

    def _header_sent(self, name: str) -> bool:
        prefix = (name.lower() + ":").encode("latin-1")
        return any(bytes(line).lower().startswith(prefix)
                   for line in getattr(self, "_headers_buffer", []))

    def end_headers(self):
        for name, value in _SECURITY_HEADERS.items():
            if not self._header_sent(name):
                self.send_header(name, value)
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

        # Private gateway — checks ALL routes (own _EXEMPT_PATHS handles exclusions)
        from services.private_gateway import check_request as _gw_check
        if _gw_check(self):
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
        # LLM watchdog has a timeout, nowhere else. If a request stalls
        # forever, that's a backend bug we want to surface (not paper over
        # with a 504). The slow-response log below catches anything > 5s,
        # but skips paths that are legitimately long-lived (LLM streaming
        # through /relay-proxy/ can take 60+ s per turn).
        req.wait()
        # `respond` is marked from inside complete()/complete_stream(),
        # capturing when the flow actually handed us the response — not
        # when this thread woke up from the event.
        _waited = time.monotonic() - req.timing.get(
            "recv", req.timing.get("dispatch", time.monotonic()))
        _is_streaming_path = _is_long_lived_stream_path(path)
        if _waited > 5.0 and not _is_streaming_path:
            _action = _request_action_label(req)
            logger.warning("[http] slow response — %s %s%s took %.1fs "
                            "(request_id=%s, status=%d)",
                            method, path,
                            f" action={_action}" if _action else "",
                            _waited, req.request_id[:8],
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

        # Extract optional conversation_id from form fields
        conv_id = ""
        for part in msg.walk():
            if part.get_content_disposition() == "form-data" and not part.get_filename():
                name = part.get_param("name", header="content-disposition") or ""
                if name == "conversation_id":
                    conv_id = (part.get_payload(decode=True) or b"").decode().strip()
                    break

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

    def __init__(self, server_address, handler_class, route_registry, request_timeout,
                 max_dispatch_threads=None, header_read_timeout=None):
        self._route_registry = route_registry
        self._pending_requests: Dict[str, PendingRequest] = {}
        self._request_timeout = request_timeout
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
                pass
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
            pass
        finally:
            try:
                sock.close()
            except Exception:
                pass

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
                pass
            return

        with self._dispatch_lock:
            self._dispatch_active += 1

        def _run_dispatch():
            try:
                self._dispatch_request(request, client_address)
            finally:
                with self._dispatch_lock:
                    self._dispatch_active = max(0, self._dispatch_active - 1)
                self._dispatch_slots.release()

        t = threading.Thread(
            target=_run_dispatch,
            daemon=True,
            name="http-dispatch")
        t.start()

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
                pass
            return
        finally:
            try:
                request.settimeout(None)
            except Exception:
                pass

        # Private gateway — reject banned IPs before any processing
        try:
            from services.private_gateway import is_banned, is_enabled
            if is_enabled() and client_address and is_banned(client_address[0]):
                request.close()
                return
        except Exception:
            pass

        if b"Upgrade: websocket" in data or b"upgrade: websocket" in data:
            # WS — handle directly on raw socket.
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

            _remote = client_address[0] if client_address else "?"

            # Internal-auth bypass for server-spawned components (CC
            # container MCP bridge, server-side relays). A valid
            # `pawflow_internal` cookie on a /ws/tools/* route skips
            # gateway + session checks. Route-level register-step token
            # auth (inside the tool relay register message) still runs.
            # Tokens are minted fresh per MCP config write and held
            # in-memory only.
            _internal_ok = False
            if path.startswith("/ws/tools/"):
                try:
                    from core.internal_auth import validate_token
                    _ch = headers.get("Cookie", "")
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
                from services.private_gateway import is_enabled, is_banned, _verify_cookie, _COOKIE_NAME
                if is_enabled() and not _internal_ok:
                    ip = client_address[0] if client_address else "0.0.0.0"
                    if is_banned(ip):
                        logger.warning(
                            "[ws] rejected %s on %s: private gateway banned IP",
                            _remote, path)
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
                        logger.warning(
                            "[ws] rejected %s on %s: private gateway enabled "
                            "but %s cookie missing/invalid (cookie_header "
                            "has %d parts)",
                            _remote, path, _COOKIE_NAME,
                            len([p for p in cookie_header.split(";") if p.strip()]))
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
            if not _internal_ok:
                try:
                    from core.security import SecurityManager
                    sm = SecurityManager.get_instance()
                    ws_token = None
                    cookie_header = headers.get("Cookie", "")
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
    ServiceRegistry for discoverability by code outside flows.

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

    @classmethod
    def all_instances(cls) -> Dict[int, "HTTPListenerService"]:
        """Snapshot of every running listener keyed by port."""
        with _instances_lock:
            return dict(_instances)

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
        self._max_dispatch_threads = self.config.get("max_dispatch_threads")
        self._header_read_timeout = self.config.get("header_read_timeout")

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
            "max_dispatch_threads": {"type": "integer", "required": False, "default": 128, "description": "Maximum concurrent HTTP/WebSocket dispatch threads"},
            "header_read_timeout": {"type": "float", "required": False, "default": 3.0, "description": "Seconds to wait for request headers before closing slow or half-open clients"},
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

        self._server = _HTTPServerWithRegistry(
            (self._host, self._port),
            _RequestHandler,
            self._registry,
            self._request_timeout,
            max_dispatch_threads=self._max_dispatch_threads,
            header_read_timeout=self._header_read_timeout,
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
                       private_only: bool = False) -> RouteEntry:
        """Register a route for a flow/task.

        Args:
            ws_handler: Optional WebSocket handler callable(socket, path_params, meta).
                        If set, WebSocket upgrade requests on this route are accepted
                        and the handler is called with the raw socket after handshake.
            public: if True, session auth and private-gateway checks are skipped
                    for this route — the callback is responsible for its own auth
                    (login pages, OAuth callbacks, proxy endpoints with tokens…).
            private_only: if True, only clients with private IPs (RFC 1918 /
                    localhost) are accepted — even if the route is public.
        """
        return self._registry.register(method, pattern, owner_id, callback,
                                        ws_handler=ws_handler,
                                        public=public, private_only=private_only)

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
