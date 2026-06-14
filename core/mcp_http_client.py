"""Streamable HTTP MCP client.

A spec-conformant client for MCP servers exposed over the Model Context
Protocol **Streamable HTTP** transport (rev 2025-03-26): a single endpoint
that requires an ``initialize`` handshake, issues a session id via the
``Mcp-Session-Id`` response header that must be echoed on every subsequent
request, and may answer either with a plain JSON body
(``application/json``) or with a Server-Sent-Events stream
(``text/event-stream``).

The previous client (a single bare POST with ``Accept: application/json`` and
no handshake/session/SSE handling) only interoperated with a non-standard
"sessionless JSON-RPC over one POST" dialect that virtually no real MCP server
(FastMCP, the official SDK servers, ...) speaks. This module implements the
real transport so any conformant HTTP MCP server works, including when the URL
is a PawFlow relay-proxy URL (the relay proxy already streams SSE end-to-end
and forwards the ``Mcp-Session-Id`` header in both directions).

Stdlib only (``http.client``) to stay dependency-free and consistent with the
rest of the tool layer.
"""

import http.client
import json
import logging
import ssl
import uuid as _uuid
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# MCP protocol revision we advertise on initialize.
PROTOCOL_VERSION = "2025-03-26"


class MCPSessionExpired(Exception):
    """Raised when the server reports the session is gone (HTTP 404)."""


class MCPHttpError(Exception):
    """Transport- or protocol-level error talking to the MCP server."""


def _parse_sse_messages(resp) -> List[dict]:
    """Read an ``text/event-stream`` body incrementally and return the JSON
    payloads of every ``data:`` event that parses as a JSON object.

    MCP frames each JSON-RPC message as one SSE event whose ``data`` field is
    the JSON. We read line by line (no fixed delimiter) and flush an event on
    each blank line, stopping as soon as we have at least one JSON-RPC message
    with an ``id`` (the response to our request) so we do not block on a
    server that keeps the stream open.
    """
    messages: List[dict] = []
    data_lines: List[str] = []
    while True:
        raw = resp.readline()
        if not raw:
            break
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if line == "":
            # End of one SSE event — assemble its data payload.
            if data_lines:
                payload = "\n".join(data_lines)
                data_lines = []
                try:
                    obj = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    messages.append(obj)
                    if obj.get("id") is not None:
                        break
            continue
        if line.startswith(":"):
            continue  # SSE comment / keepalive
        if line.startswith("data:"):
            data_lines.append(line[len("data:"):].lstrip())
        # other fields (event:, id:, retry:) are ignored
    # Flush a trailing event with no terminating blank line.
    if data_lines:
        try:
            obj = json.loads("\n".join(data_lines))
            if isinstance(obj, dict):
                messages.append(obj)
        except json.JSONDecodeError:
            pass
    return messages


class MCPHttpClient:
    """Minimal Streamable HTTP MCP client with lazy session handshake.

    One instance is bound to one server URL. The session id is captured on
    ``initialize`` and replayed on every request; an HTTP 404 transparently
    triggers one re-initialize + retry.
    """

    def __init__(self, server_url: str,
                 headers: Optional[Dict[str, str]] = None,
                 timeout: int = 30,
                 client_name: str = "pawflow",
                 client_version: str = "1.0"):
        self._server_url = server_url
        self._extra_headers = dict(headers or {})
        self._timeout = timeout
        self._client_name = client_name
        self._client_version = client_version
        self._session_id: Optional[str] = None
        self._initialized = False

    # ── low-level transport ──────────────────────────────────────────
    def _connect(self):
        parsed = urlparse(self._server_url)
        host = parsed.hostname
        port = parsed.port
        scheme = parsed.scheme or "https"
        if scheme == "https":
            ctx = ssl.create_default_context()
            return http.client.HTTPSConnection(
                host, port, timeout=self._timeout, context=ctx), parsed
        return http.client.HTTPConnection(
            host, port, timeout=self._timeout), parsed

    def _post(self, rpc: dict) -> List[dict]:
        """POST one JSON-RPC message and return the JSON-RPC messages received.

        Handles both ``application/json`` (single body) and
        ``text/event-stream`` (SSE) responses, captures the session id, and
        maps HTTP 404 to :class:`MCPSessionExpired`. A notification (no ``id``)
        that the server acks with 202 and an empty body returns ``[]``.
        """
        conn, parsed = self._connect()
        body = json.dumps(rpc).encode("utf-8")
        req_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Content-Length": str(len(body)),
        }
        if self._session_id:
            req_headers["Mcp-Session-Id"] = self._session_id
        req_headers.update(self._extra_headers)
        path = parsed.path or "/"
        if parsed.query:
            path = path + "?" + parsed.query
        try:
            conn.request("POST", path, body=body, headers=req_headers)
            resp = conn.getresponse()
            sid = resp.getheader("Mcp-Session-Id")
            if sid:
                self._session_id = sid
            status = resp.status
            if status == 404:
                resp.read()
                raise MCPSessionExpired("MCP session expired (HTTP 404)")
            if status == 202:
                resp.read()
                return []
            ctype = (resp.getheader("Content-Type") or "").lower()
            if status >= 400:
                err_body = resp.read().decode("utf-8", errors="replace")
                raise MCPHttpError(f"HTTP {status}: {err_body[:500]}")
            if "text/event-stream" in ctype:
                return _parse_sse_messages(resp)
            data = resp.read().decode("utf-8", errors="replace")
            if not data.strip():
                return []
            obj = json.loads(data)
            return obj if isinstance(obj, list) else [obj]
        finally:
            try:
                conn.close()
            except Exception:
                logger.debug("Ignored MCP conn close error", exc_info=True)

    @staticmethod
    def _result_of(messages: List[dict], request_id: str) -> dict:
        """Pick the JSON-RPC response matching ``request_id`` and return its
        ``result`` (raising on a JSON-RPC error)."""
        chosen = None
        for m in messages:
            if m.get("id") == request_id:
                chosen = m
                break
        if chosen is None:
            # Fall back to the first message carrying result/error.
            for m in messages:
                if "result" in m or "error" in m:
                    chosen = m
                    break
        if chosen is None:
            raise MCPHttpError("no JSON-RPC response received")
        if "error" in chosen:
            err = chosen["error"]
            msg = err.get("message", err) if isinstance(err, dict) else err
            raise MCPHttpError(f"MCP error: {msg}")
        return chosen.get("result", {}) or {}

    def _rpc(self, method: str, params: Optional[dict] = None) -> dict:
        request_id = _uuid.uuid4().hex[:12]
        rpc = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            rpc["params"] = params
        return self._result_of(self._post(rpc), request_id)

    def _notify(self, method: str, params: Optional[dict] = None) -> None:
        rpc = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            rpc["params"] = params
        self._post(rpc)

    # ── handshake ────────────────────────────────────────────────────
    def _initialize(self) -> None:
        self._session_id = None
        self._rpc("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {
                "name": self._client_name,
                "version": self._client_version,
            },
        })
        try:
            self._notify("notifications/initialized")
        except Exception:
            logger.debug("initialized notification failed", exc_info=True)
        self._initialized = True

    def _ensure_initialized(self) -> None:
        if not self._initialized:
            self._initialize()

    def _call_with_session_retry(self, method: str, params: dict) -> dict:
        self._ensure_initialized()
        try:
            return self._rpc(method, params)
        except MCPSessionExpired:
            logger.info("MCP session expired, re-initializing %s", self._server_url)
            self._initialized = False
            self._ensure_initialized()
            return self._rpc(method, params)

    # ── public API ───────────────────────────────────────────────────
    def list_tools(self) -> List[Dict[str, Any]]:
        result = self._call_with_session_retry("tools/list", {})
        tools = result.get("tools", []) if isinstance(result, dict) else []
        return [
            {
                "name": t.get("name", ""),
                "description": t.get("description", ""),
                "inputSchema": t.get("inputSchema", {}),
            }
            for t in tools
        ]

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> dict:
        """Call a tool and return the raw MCP ``tools/call`` result dict
        (with ``content`` array and ``isError``)."""
        return self._call_with_session_retry(
            "tools/call", {"name": name, "arguments": arguments})


def flatten_tool_content(result: dict) -> str:
    """Flatten an MCP ``tools/call`` result's ``content`` array into text,
    mirroring the relay stdio proxy's behaviour."""
    content = result.get("content", []) if isinstance(result, dict) else []
    parts: List[str] = []
    for item in content:
        if not isinstance(item, dict):
            parts.append(str(item))
            continue
        if item.get("type") == "text":
            parts.append(item.get("text", ""))
        elif item.get("type") == "image":
            parts.append(f"[image: {item.get('mimeType', 'image/*')}]")
        else:
            parts.append(json.dumps(item))
    return "\n".join(parts) if parts else json.dumps(result)
