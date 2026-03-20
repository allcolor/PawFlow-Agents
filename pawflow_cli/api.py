"""PawCode — Agent API client with SSE streaming support."""

import json
import http.client
import queue
import ssl
import threading
import time
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse


class AgentAPIClient:
    """HTTP client for the PawFlow agent API."""

    def __init__(self, server_url: str, session_token: str):
        self.server_url = server_url.rstrip("/")
        self.session_token = session_token
        self._parsed = urlparse(self.server_url)
        self._host = self._parsed.hostname or "localhost"
        self._port = self._parsed.port or (443 if self._parsed.scheme == "https" else 80)
        self._use_ssl = self._parsed.scheme == "https"

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
        return h

    def send_action(self, action: str, **kwargs) -> dict:
        """Send an action to the agent API. Returns parsed JSON response."""
        body = {"action": action}
        body.update(kwargs)
        return self._post("/api/agent", body)

    def send_message(self, message: str, conversation_id: str = None,
                     target_agent: str = "", attachments: list = None,
                     pending_agent: str = "") -> dict:
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
        return self._post("/api/agent", body)

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

    def __init__(self, server_url: str, session_token: str):
        self.server_url = server_url.rstrip("/")
        self.session_token = session_token
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
                retry_delay = min(retry_delay * 2, 15)

    def _stream(self, conversation_id: str):
        """Open SSE connection and parse events."""
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
