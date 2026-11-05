"""voxl desktop viewer behind PawFlow's /vnc/<session>/<token>/voxl/ route.

voxl (pawflow_relay/voxl.py) serves its own page, scripts, a WebSocket at
``/ws`` and a few APIs (``/files/*``, ``/screenshot``, ``/wait``). Its web
client only uses relative URLs, so it runs unchanged under
``/vnc/<session>/<token>/voxl/``.

The capability token in the path authorizes the browser (checked by the
/vnc routes before this module is reached). voxl's own password never leaves
the server: every proxied request carries ``Authorization: Bearer
<password>``. Browser cookies, ``Origin`` and ``Referer`` are not forwarded:
voxl answers only its own origin, and PawFlow already decided the request is
allowed.

Transports, chosen from the VNC session registered by the desktop action:

* relay (default): HTTP through the relay's streaming ``http_proxy`` (or
  ``http_fetch`` with ``local=True`` for a host-screen desktop behind the
  host helper) and the WebSocket through the relay's ``desktop_ws_open``
  tunnel, as raw binary frames.
* direct (``voxl_host``): a desktop running next to the server, reached
  over TCP.
"""

import base64
import http.client
import json
import logging
import os
import socket
import threading

logger = logging.getLogger(__name__)

_HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade", "content-length",
})

#: Browser request headers voxl may use; everything else is dropped.
_FORWARDED_REQUEST_HEADERS = frozenset({
    "accept", "accept-language", "content-type", "range", "if-range",
    "if-none-match", "if-modified-since", "cache-control",
})


def voxl_available(session) -> bool:
    return bool(session and int(session.get("voxl_port") or 0))


def _backend_headers(session, browser_headers=None) -> dict:
    port = int(session["voxl_port"])
    headers = {}
    for key, value in (browser_headers or {}).items():
        if key.lower() in _FORWARDED_REQUEST_HEADERS:
            headers[key] = value
    headers["Host"] = f"127.0.0.1:{port}"
    headers["Authorization"] = f"Bearer {session.get('voxl_password', '')}"
    return headers


#: The chat page is cross-origin isolated (COOP/COEP require-corp, see
#: serve_chat_ui); an embedded document without matching headers is blocked
#: by the browser ("refused to connect"), exactly like the noVNC viewer.
_ISOLATION_HEADERS = {
    "Cross-Origin-Resource-Policy": "same-origin",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Embedder-Policy": "require-corp",
}


def _response_headers(headers) -> dict:
    isolation = {k.lower() for k in _ISOLATION_HEADERS}
    out = {str(k): str(v) for k, v in dict(headers or {}).items()
           if str(k).lower() not in _HOP_BY_HOP
           and str(k).lower() not in isolation}
    out.update(_ISOLATION_HEADERS)
    return out


# ── HTTP ──────────────────────────────────────────────────────────────


def voxl_http_proxy(pending_req, session_id: str, session: dict,
                    sub_path: str) -> None:
    """Proxy one request under ``voxl/`` (``sub_path`` is what follows)."""
    if not voxl_available(session):
        pending_req.complete(404, {"Content-Type": "application/json"},
                             b'{"error": "voxl is not running for this desktop"}')
        return
    path = "/" + str(sub_path or "").lstrip("/")
    query = getattr(pending_req, "query_string", "") or ""
    if query:
        path += "?" + query
    method = getattr(pending_req, "method", "GET") or "GET"
    body = getattr(pending_req, "body", b"") or b""
    headers = _backend_headers(session, getattr(pending_req, "headers", {}))
    try:
        if session.get("voxl_host"):
            _direct_http(pending_req, session, method, path, headers, body)
        else:
            _relay_http(pending_req, session_id, session, method, path,
                        headers, body)
    except Exception as exc:
        logger.warning("voxl HTTP proxy error for %s: %s", session_id, exc)
        pending_req.complete(502, {"Content-Type": "application/json"},
                             json.dumps({"error": str(exc)}).encode())


def _relay_http(pending_req, session_id, session, method, path, headers, body):
    from services._relay_http_response import RelayHttpResponseStream

    relay_service = session["voxl_relay_service"]
    port = int(session["voxl_port"])
    label = f"voxl-http-{session_id[:8]}"
    if session.get("voxl_local"):
        # Host-screen voxl runs on the user's machine: local=True executes
        # the request through the relay host helper, like the WebSocket.
        stream = RelayHttpResponseStream.for_fetch(
            relay_service, url=f"http://127.0.0.1:{port}{path}",
            method=method, headers=headers, body=body, local=True,
            timeout=300, label=label).start()
    else:
        stream = RelayHttpResponseStream.for_local_port(
            relay_service, port=port, method=method, req_path=path,
            headers=headers, body=body, timeout=300, label=label).start()
    stream.wait_ready()
    if stream.error:
        detail = stream.error
        stream.discard()
        pending_req.complete(502, {"Content-Type": "application/json"},
                             json.dumps({"error": detail}).encode())
        return
    pending_req.complete_stream(int(stream.status),
                                _response_headers(stream.headers),
                                stream.iter_bytes())


def _direct_http(pending_req, session, method, path, headers, body):
    conn = http.client.HTTPConnection(
        session["voxl_host"], int(session["voxl_port"]), timeout=300)
    conn.request(method, path, body=body or None, headers=headers)
    resp = conn.getresponse()

    def _chunks():
        try:
            while True:
                chunk = resp.read1(64 * 1024)
                if not chunk:
                    return
                yield chunk
        finally:
            conn.close()

    pending_req.complete_stream(resp.status,
                                _response_headers(dict(resp.getheaders())),
                                _chunks())


# ── WebSocket ─────────────────────────────────────────────────────────


def voxl_ws_proxy(client_sock, path_params: dict, meta: dict):
    """WebSocket handler for /vnc/{session_id}/{token}/voxl/ws."""
    from services import vnc_proxy

    session_id = path_params.get("session_id", "")
    token = path_params.get("token", "")
    from core.capability_routes import verify_route_ws
    _claims, err = verify_route_ws(
        meta or {}, "vnc", session_id, token, allow_bearer_only=True)
    if err is not None:
        try:
            client_sock.sendall(err)
        finally:
            client_sock.close()
        return
    with vnc_proxy._lock:
        session = vnc_proxy._sessions.get(session_id)
    if not voxl_available(session):
        vnc_proxy._ws_close(client_sock, 4001, "voxl is not running")
        return
    query = (meta or {}).get("query", "") or ""
    ws_path = "/ws" + (f"?{query}" if query else "")
    headers = _backend_headers(session)
    if session.get("voxl_host"):
        _direct_ws(client_sock, session, ws_path, headers)
        return
    vnc_proxy._vnc_ws_relay_proxy(
        client_sock, session_id, session, meta or {},
        port=int(session["voxl_port"]), ws_path=ws_path, headers=headers,
        relay_service=session["voxl_relay_service"],
        local_screen=bool(session.get("voxl_local")))


def _direct_ws(client_sock, session, ws_path, headers):
    """Byte-level bridge to a voxl reachable over TCP from the server."""
    from services import vnc_proxy

    try:
        backend = socket.create_connection(
            (session["voxl_host"], int(session["voxl_port"])), timeout=10)
    except OSError as exc:
        logger.warning("voxl direct WS connect failed: %s", exc)
        vnc_proxy._ws_close(client_sock, 4002, "Backend unavailable")
        return
    key = base64.b64encode(os.urandom(16)).decode()
    lines = [f"GET {ws_path} HTTP/1.1", "Upgrade: websocket",
             "Connection: Upgrade", f"Sec-WebSocket-Key: {key}",
             "Sec-WebSocket-Version: 13"]
    lines += [f"{k}: {v}" for k, v in headers.items()]
    backend.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
    response = b""
    while b"\r\n\r\n" not in response:
        chunk = backend.recv(4096)
        if not chunk:
            break
        response += chunk
    if b" 101 " not in response.split(b"\r\n", 1)[0]:
        backend.close()
        vnc_proxy._ws_close(client_sock, 4003, "Backend handshake failed")
        return
    backend.settimeout(None)
    leftover = response[response.index(b"\r\n\r\n") + 4:]
    if leftover:
        client_sock.sendall(leftover)
    stop = threading.Event()

    def _pump(src, dst):
        try:
            while not stop.is_set():
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)
        except OSError:
            pass
        finally:
            stop.set()

    threading.Thread(target=_pump, args=(client_sock, backend), daemon=True).start()
    _pump(backend, client_sock)
    for sock in (client_sock, backend):
        try:
            sock.close()
        except OSError:
            pass
