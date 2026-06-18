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
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field


logger = logging.getLogger("services.http_listener_service")  # canonical name preserved across the module split
_HTTP_TIMING_DIAG_MS = float(os.getenv("PAWFLOW_HTTP_TIMING_DIAG_MS", "100") or "100")


_SECURITY_HEADERS = {
    # The chat UI pulls rxjs from jsDelivr, highlight.js (script + theme CSS)
    # from cdnjs, and the embedded flow graph's ESM imports from esm.sh. These
    # CDN origins are whitelisted explicitly so the default 'self'-only policy
    # doesn't break the page on first load.
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' blob: "
        "https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://esm.sh https://telegram.org; "
        "style-src 'self' 'unsafe-inline' "
        "https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
        "img-src 'self' data: blob:; "
        "media-src 'self' data: blob:; "
        "connect-src 'self' ws: wss: https://cdn.jsdelivr.net https://esm.sh; "
        "frame-src 'self' blob: http: https:; "
        "frame-ancestors 'self'; "
        "object-src 'none'; base-uri 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(self), geolocation=()",
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
_DEFAULT_MAX_LONG_DISPATCH_THREADS = 1024
_DEFAULT_HEADER_READ_TIMEOUT = 3.0


def _rate_limit_policy(path: str) -> Optional[Tuple[str, int, float]]:
    if path == "/auth/login" or path.startswith("/auth/login/"):
        return None
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
        bug we want to surface (not mask with a 503), and the HTTP
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


def _request_action_meta(req: "PendingRequest") -> str:
    if req.path != "/api/ui" or not req.body:
        return ""
    try:
        data = json.loads(req.body.decode("utf-8", errors="replace"))
        if not isinstance(data, dict):
            return ""
        if data.get("_reply_conversation_id"):
            reply = "bus"
        elif data.get("conversation_id"):
            reply = "conv"
        else:
            reply = "inline"
    except Exception:
        return ""
    mode = ""
    try:
        body = req.response_body or b""
        if body:
            payload = json.loads(body[:512].decode("utf-8", errors="replace"))
            if isinstance(payload, dict):
                mode = "accepted" if payload.get("status") == "accepted" else "inline"
    except Exception:
        mode = ""
    return f" reply={reply}" + (f" mode={mode}" if mode else "")


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
    meta = _request_action_meta(req)
    msg = "[http-timing] req_id=%s %s %s%s%s total=%dms %s status=%d"
    args = (
        req.request_id[:8], req.method, req.path,
        f" action={action}" if action else "", meta,
        total, " ".join(segments), req.response_status,
    )
    if total > _HTTP_TIMING_DIAG_MS and not is_long_stream:
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
    public: bool = False    # if True: skip session auth; gateway still applies
    private_only: bool = False  # if True: only accept private-IP clients
    gateway_exempt: bool = False  # if True: bypass the private gateway challenge


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
                 private_only: bool = False,
                 gateway_exempt: bool = False) -> RouteEntry:
        """Register a route.  Raises RouteConflictError on overlap.

        public=True skips session auth (use for login pages, callbacks, proxy
        endpoints with their own token auth). Private gateway still applies
        unless the route is also private_only.
        private_only=True rejects non-RFC1918 clients even if public=True
        (use for proxy endpoints leaked URLs must not allow external abuse).
        gateway_exempt=True bypasses the private gateway challenge while still
        accepting public IPs — use for provider callbacks (media webhooks)
        whose URL carries its own unguessable token credential. Unlike
        private_only, it does not reject internet clients, which is required
        for callbacks delivered from a provider's public egress.
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
                        existing.gateway_exempt = gateway_exempt
                        return existing
                    raise RouteConflictError(
                        f"Route {method} {pattern} already registered by '{existing.owner_id}'"
                    )

            entry = RouteEntry(
                method=method, pattern=pattern, regex=regex,
                owner_id=owner_id, callback=callback,
                ws_handler=ws_handler,
                public=public, private_only=private_only,
                gateway_exempt=gateway_exempt,
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

