"""PawCode — Agent API client with SSE streaming support."""

import json
import http.client
import queue
import ssl
import threading
import time
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse, urlencode


def acquire_gateway_cookie(server_url: str, gateway_key: str) -> str:
    """POST /_gateway with the access key, return the _pf_gw cookie value or empty string."""
    parsed = urlparse(server_url)
    use_ssl = parsed.scheme == "https"
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if use_ssl else 80)

    if use_ssl:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        conn = http.client.HTTPSConnection(host, port, context=ctx)
    else:
        conn = http.client.HTTPConnection(host, port)

    body = urlencode({"secret": gateway_key, "next": "/"})
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    conn.request("POST", "/_gateway", body=body, headers=headers)
    resp = conn.getresponse()

    # Follow 301/302 (HTTP→HTTPS redirect)
    if resp.status in (301, 302):
        resp.read()
        location = resp.getheader("Location", "")
        conn.close()
        if location:
            _rp = urlparse(location)
            _rssl = _rp.scheme == "https"
            _rh = _rp.hostname or host
            _rpt = _rp.port or (443 if _rssl else 80)
            if _rssl:
                _ctx2 = ssl.create_default_context()
                _ctx2.check_hostname = False
                _ctx2.verify_mode = ssl.CERT_NONE
                conn = http.client.HTTPSConnection(_rh, _rpt, context=_ctx2)
            else:
                conn = http.client.HTTPConnection(_rh, _rpt)
            conn.request("POST", _rp.path or "/_gateway", body=body, headers=headers)
            resp = conn.getresponse()

    resp.read()  # drain

    cookie_val = ""
    for hdr in resp.msg.get_all("Set-Cookie") or []:
        for part in hdr.split(";"):
            part = part.strip()
            if part.startswith("_pf_gw="):
                cookie_val = part[len("_pf_gw="):]
                break
        if cookie_val:
            break

    conn.close()
    return cookie_val


class SSEResultQueue:
    """Thread-safe queue for SSE command_result events, keyed by action name."""

    def __init__(self):
        self._waiters: Dict[str, threading.Event] = {}
        self._results: Dict[str, dict] = {}
        self._lock = threading.Lock()

    def get(self, action: str, timeout: float = 120) -> dict:
        """Wait for a command_result with matching action. Returns parsed result."""
        event = threading.Event()
        with self._lock:
            self._waiters[action] = event
        if not event.wait(timeout=timeout):
            with self._lock:
                self._waiters.pop(action, None)
            return {"error": f"Timeout waiting for {action} result"}
        with self._lock:
            self._waiters.pop(action, None)
            return self._results.pop(action, {})

    def push(self, action: str, data: dict):
        """Push a command_result. Wakes up any waiting get()."""
        with self._lock:
            result = data
            if isinstance(data.get("result"), str):
                try:
                    result = json.loads(data["result"])
                except (json.JSONDecodeError, TypeError):
                    result = data
            self._results[action] = result
            waiter = self._waiters.get(action)
        if waiter:
            waiter.set()


class AgentAPIClient:
    """HTTP client for the PawFlow agent API."""

    def __init__(self, server_url: str, session_token: str, gateway_cookie: str = ""):
        self.server_url = server_url.rstrip("/")
        self.session_token = session_token
        self.gateway_cookie = gateway_cookie
        self._parsed = urlparse(self.server_url)
        self._host = self._parsed.hostname or "localhost"
        self._port = self._parsed.port or (443 if self._parsed.scheme == "https" else 80)
        self._use_ssl = self._parsed.scheme == "https"
        self._sse_result_queue = SSEResultQueue()

    def _get_conn(self, timeout: int = 30):
        if self._use_ssl:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return http.client.HTTPSConnection(self._host, self._port, context=ctx, timeout=timeout)
        return http.client.HTTPConnection(self._host, self._port, timeout=timeout)

    def _headers(self):
        h = {"Content-Type": "application/json"}
        if self.session_token:
            h["Authorization"] = f"Bearer {self.session_token}"
        if self.gateway_cookie:
            h["Cookie"] = f"_pf_gw={self.gateway_cookie}"
        return h

    def send_action(self, action: str, **kwargs) -> dict:
        """Send an action and wait for the result via SSE command_result.

        The server runs all actions in background and returns {"status": "accepted"}.
        The real result arrives via SSE command_result event. This method blocks
        until that event arrives (or timeout).

        If no SSE client is connected, falls back to immediate response.
        """
        body = {"action": action}
        body.update(kwargs)
        resp = self._post("/api/agent", body)

        # If server returned data directly (no conversation_id = sync), use it
        if resp.get("status") != "accepted":
            return resp

        # Wait for SSE command_result with matching action
        if not self._sse_result_queue:
            return resp  # no SSE client — can't wait

        try:
            result = self._sse_result_queue.get(action, timeout=120)
            return result
        except Exception:
            return resp

    def send_action_fire(self, action: str, **kwargs) -> dict:
        """Fire-and-forget action — don't wait for result."""
        body = {"action": action}
        body.update(kwargs)
        return self._post("/api/agent", body)

    def send_message(self, message: str, conversation_id: str = None,
                     target_agent: str = "", attachments: list = None,
                     pending_agent: str = "", reply_to: dict = None) -> dict:
        """Send a chat message to the agent."""
        body = {"message": message}
        if conversation_id:
            body["conversation_id"] = conversation_id
        if target_agent:
            body["target_agent"] = target_agent
        if attachments:
            body["attachments"] = attachments
        if pending_agent:
            body["pending_agent"] = pending_agent
        if reply_to:
            body["reply_to"] = reply_to
        return self._post("/api/agent", body)

    def get(self, path: str) -> dict:
        """HTTP GET, return parsed JSON response."""
        conn = self._get_conn()
        try:
            conn.request("GET", f"/api{path}", headers=self._headers())
            resp = conn.getresponse()
            data = resp.read().decode("utf-8")
            if resp.status == 401:
                raise PermissionError("Session expired — re-authenticate")
            if resp.status >= 400:
                raise Exception(f"API error {resp.status}: {data[:500]}")
            return json.loads(data) if data else {}
        finally:
            conn.close()

    def _post(self, path: str, body: dict) -> dict:
        """HTTP POST with JSON body, return parsed response."""
        conn = self._get_conn()
        try:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            conn.request("POST", path, body=payload, headers=self._headers())
            resp = conn.getresponse()
            data = resp.read().decode("utf-8")
            if resp.status == 401:
                raise PermissionError("Session expired — re-authenticate")
            if resp.status >= 400:
                raise Exception(f"API error {resp.status}: {data[:500]}")
            return json.loads(data) if data else {}
        finally:
            conn.close()


class SSEClient:
    """Server-Sent Events client that runs in a background thread.

    Parses the SSE wire format and dispatches events to a callback.
    """

    def __init__(self, server_url: str, session_token: str, gateway_cookie: str = ""):
        self.server_url = server_url.rstrip("/")
        self.session_token = session_token
        self.gateway_cookie = gateway_cookie
        self._parsed = urlparse(self.server_url)
        self._host = self._parsed.hostname or "localhost"
        self._port = self._parsed.port or (443 if self._parsed.scheme == "https" else 80)
        self._use_ssl = self._parsed.scheme == "https"
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._conn = None
        self.events: queue.Queue = queue.Queue()
        self.connected = False

    def connect(self, conversation_id: str):
        """Start SSE connection in background thread."""
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, args=(conversation_id,),
            daemon=True, name="pawflow-cli-sse",
        )
        self._thread.start()

    def disconnect(self):
        """Stop the SSE connection."""
        self._stop.set()
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass

    def _run(self, conversation_id: str):
        """SSE connection loop with auto-reconnect."""
        retry_delay = 1
        while not self._stop.is_set():
            try:
                self._stream(conversation_id)
                retry_delay = 1  # reset on successful connection
            except Exception as e:
                if self._stop.is_set():
                    return
                self.events.put({"event": "_sse_error", "data": {"error": str(e)}})
                self._stop.wait(retry_delay)
                # Exponential backoff: 1s → 2s → 4s → 8s → 16s → 30s → 60s
                retry_delay = min(retry_delay * 2, 60)

    def _stream(self, conversation_id: str):
        """Open SSE connection and parse events."""
        # Close previous connection before opening a new one — prevents socket leak
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

        path = f"/api/agent/events?conversation_id={conversation_id}"
        if self.session_token:
            path += f"&token={self.session_token}"

        if self._use_ssl:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            conn = http.client.HTTPSConnection(self._host, self._port, context=ctx, timeout=120)
        else:
            conn = http.client.HTTPConnection(self._host, self._port, timeout=120)

        self._conn = conn
        headers = {"Accept": "text/event-stream", "Cache-Control": "no-cache"}
        if self.session_token:
            headers["Authorization"] = f"Bearer {self.session_token}"
        if self.gateway_cookie:
            headers["Cookie"] = f"_pf_gw={self.gateway_cookie}"

        conn.request("GET", path, headers=headers)
        resp = conn.getresponse()

        if resp.status != 200:
            raise Exception(f"SSE connection failed: {resp.status}")

        self.connected = True
        self.events.put({"event": "_sse_connected", "data": {}})

        # Parse SSE stream line-by-line (low latency — each event delivered immediately)
        event_type = ""
        data_lines = []

        # Use the raw socket for line-by-line reading
        # HTTPResponse supports iteration and readline
        while not self._stop.is_set():
            try:
                raw_line = resp.readline()
                if not raw_line:
                    break
                line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")

                if line.startswith("event:"):
                    event_type = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].strip())
                elif line == "":
                    # End of event — dispatch
                    if event_type or data_lines:
                        raw_data = "\n".join(data_lines)
                        try:
                            parsed = json.loads(raw_data) if raw_data else {}
                        except json.JSONDecodeError:
                            parsed = {"raw": raw_data}
                        self.events.put({
                            "event": event_type or "message",
                            "data": parsed,
                        })
                    event_type = ""
                    data_lines = []
            except Exception:
                if self._stop.is_set():
                    return
                raise

        self.connected = False
        conn.close()
