"""VNC WebSocket Proxy — bidirectional relay between browser and Docker noVNC.

Used for the Claude Code server-side login flow. The browser connects
via WebSocket to PawFlow (authenticated), and this proxy relays frames
to the noVNC websockify running in a Docker container on localhost.

The proxy does not interpret frames — it relays raw bytes in both
directions until one side closes.
"""

import json
import logging
import socket
import struct
import threading
import uuid

logger = logging.getLogger(__name__)


def vnc_ws_proxy(client_sock, path_params: dict, meta: dict):
    """WebSocket handler for /vnc/{session_id}/{token}/websockify.

    The browser connects to PawFlow with the capability token in the
    URL path (issued by `register_session`). We verify the token binds
    this session_id to the authenticated user before opening the
    backend connection — cross-user access is rejected at this point.
    """
    session_id = path_params.get("session_id", "")
    token = path_params.get("token", "")
    if not session_id:
        _ws_close(client_sock, 4000, "Missing session_id")
        return

    from core.capability_routes import verify_route_ws
    claims, err = verify_route_ws(
        meta or {}, "vnc", session_id, token, allow_bearer_only=True)
    if err is not None:
        try:
            client_sock.sendall(err)
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        try:
            client_sock.close()
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        return

    with _lock:
        session = _sessions.get(session_id)
    if session and session.get("use_relay_proxy"):
        _vnc_relay_ws_proxy(client_sock, session_id, session, meta or {})
        return

    # Look up the target host:port for this session
    target_host, target_port = _get_vnc_target(session_id)
    if not target_port:
        _ws_close(client_sock, 4001, "Unknown session")
        return

    # Connect to noVNC websockify (Docker container or local relay)
    import time
    backend_sock = None
    last_error = None
    deadline = time.time() + 8
    while time.time() < deadline:
        try:
            backend_sock = socket.create_connection((target_host, target_port), timeout=1)
            break
        except Exception as e:
            last_error = e
            time.sleep(0.2)
    if backend_sock is None:
        logger.warning("VNC proxy: cannot connect to %s:%d: %s", target_host, target_port, last_error)
        _ws_close(client_sock, 4002, "Backend unavailable")
        return
    # No timeout on the socket — VNC relay needs to stay open indefinitely
    backend_sock.settimeout(None)

    # Perform WS handshake with the backend (websockify expects a WS client)
    import base64, hashlib, os
    ws_key = base64.b64encode(os.urandom(16)).decode()
    handshake = (
        f"GET /websockify HTTP/1.1\r\n"
        f"Host: {target_host}:{target_port}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {ws_key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n"
        f"Sec-WebSocket-Protocol: binary\r\n"
        f"\r\n"
    )
    backend_sock.sendall(handshake.encode())

    # Read backend handshake response
    resp = b""
    while b"\r\n\r\n" not in resp:
        chunk = backend_sock.recv(4096)
        if not chunk:
            _ws_close(client_sock, 4003, "Backend handshake failed")
            backend_sock.close()
            return
        resp += chunk

    if b"101" not in resp.split(b"\r\n")[0]:
        logger.warning("VNC proxy: backend handshake failed: %s",
                        resp[:100].decode("latin-1", errors="replace"))
        _ws_close(client_sock, 4003, "Backend handshake failed")
        backend_sock.close()
        return

    # Check if there are leftover bytes after the handshake response
    _header_end = resp.index(b"\r\n\r\n") + 4
    _leftover = resp[_header_end:]
    if _leftover:
        # Forward leftover bytes from backend to client
        client_sock.sendall(_leftover)

    logger.info("VNC proxy: session %s connected (port %d, host=%s, leftover=%d bytes)",
                session_id, target_port, target_host, len(_leftover))

    stop = threading.Event()

    def _relay(src, dst, name):
        _bytes = 0
        try:
            while not stop.is_set():
                data = src.recv(65536)
                if not data:
                    logger.info("VNC proxy: %s EOF after %d bytes", name, _bytes)
                    break
                _bytes += len(data)
                dst.sendall(data)
        except Exception as _e:
            logger.info("VNC proxy: %s error after %d bytes: %s", name, _bytes, _e)
        finally:
            stop.set()

    t1 = threading.Thread(target=_relay, args=(client_sock, backend_sock, "browser->docker"), daemon=True)
    t2 = threading.Thread(target=_relay, args=(backend_sock, client_sock, "docker->browser"), daemon=True)
    t1.start()
    t2.start()

    # Wait until one side closes
    stop.wait()

    # Cleanup
    for s in (client_sock, backend_sock):
        try:
            s.close()
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

    logger.info("VNC proxy: session %s disconnected", session_id)


def _vnc_relay_ws_proxy(client_sock, session_id: str, session: dict, meta: dict):
    """Tunnel browser noVNC WS through the relay command channel."""
    import base64

    relay_service = session.get("relay_service")
    port = session.get("port")
    if not relay_service or not port:
        _ws_close(client_sock, 4002, "Relay backend unavailable")
        return

    ws_session_id = uuid.uuid4().hex[:12]
    fwd_headers = {}
    for k, v in meta.get("headers", {}).items():
        kl = k.lower()
        if kl in ("sec-websocket-key", "sec-websocket-accept",
                  "sec-websocket-extensions", "upgrade", "connection"):
            continue
        fwd_headers[k] = v

    with _lock:
        if session_id in _sessions:
            _sessions[session_id].setdefault("desktop_ws_sessions", {})[ws_session_id] = {
                "browser_sock": client_sock,
            }

    try:
        result = relay_service._request(
            "desktop_ws_open",
            session_id=ws_session_id,
            port=port,
            ws_path="/websockify",
            headers=fwd_headers,
        )
        if isinstance(result, dict) and isinstance(result.get("data"), dict):
            result = result["data"]
        if not isinstance(result, dict) or not result.get("ok", True):
            err = result.get("error", "Unknown") if isinstance(result, dict) else str(result)
            _ws_close(client_sock, 4002, f"Failed: {err}")
            return
    except Exception as e:
        _ws_close(client_sock, 4002, f"Failed: {e}")
        return

    try:
        while True:
            opcode, payload = _ws_recv(client_sock)
            if opcode == 0x08:
                break
            if opcode == 0x09:
                _ws_send(client_sock, payload, opcode=0x0A)
                continue
            _send_command_to_relay(relay_service, {
                "action": "desktop_ws_send",
                "session_id": ws_session_id,
                "data": base64.b64encode(payload).decode("ascii"),
                "opcode": opcode,
            })
    except Exception as e:
        logger.debug("VNC relay WS loop ended: %s (session=%s)", e, ws_session_id)
    finally:
        try:
            _send_command_to_relay(relay_service, {
                "action": "desktop_ws_close",
                "session_id": ws_session_id,
            })
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        with _lock:
            if session_id in _sessions:
                _sessions[session_id].setdefault("desktop_ws_sessions", {}).pop(ws_session_id, None)
        try:
            client_sock.close()
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)


def dispatch_desktop_ws_data(relay_id: str, ws_session_id: str, data_b64: str, opcode: int = 2):
    import base64
    with _lock:
        match = None
        for sess in _sessions.values():
            if sess.get("relay_id") == relay_id:
                match = sess.get("desktop_ws_sessions", {}).get(ws_session_id)
                if match:
                    break
    if not match or not match.get("browser_sock"):
        return
    try:
        _ws_send(match["browser_sock"], base64.b64decode(data_b64), opcode=opcode)
    except Exception as e:
        logger.warning("desktop_ws_data send error: %s", e)


def dispatch_desktop_ws_close(relay_id: str, ws_session_id: str):
    with _lock:
        match = None
        for sess in _sessions.values():
            if sess.get("relay_id") == relay_id:
                match = sess.get("desktop_ws_sessions", {}).pop(ws_session_id, None)
                if match:
                    break
    if match and match.get("browser_sock"):
        try:
            _ws_close(match["browser_sock"], 1000, "Backend closed")
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)


def _send_command_to_relay(relay_service, cmd: dict):
    """Send a desktop WS command to the relay command channel."""
    import asyncio
    from services.filesystem_service import _ws_send_frame

    with relay_service._relay_pool_lock:
        pool = relay_service._relay_pool[:]
    if not pool:
        return

    request_id = uuid.uuid4().hex[:8]
    msg = {"type": "command", "request_id": request_id, **cmd}
    payload = json.dumps(msg).encode("utf-8")

    last_err = None
    for conn in reversed(pool):
        writer, loop = conn["writer"], conn["loop"]

        async def _send(w=writer):
            await _ws_send_frame(w, payload)

        try:
            asyncio.run_coroutine_threadsafe(_send(), loop).result(timeout=5)
            return
        except Exception as e:
            last_err = e
            continue
    if last_err is not None:
        logger.warning("VNC relay command send error: %s", last_err)


# -- Session registry (maps session_id → Docker port) --

_sessions: dict = {}  # session_id → {"port": int, ...}
_lock = threading.Lock()


def register_session(session_id: str, port: int, *,
                     owner_user_id: str = "",
                     conversation_id: str = "",
                     login_session_id: str = "",
                     ttl_seconds: int = 86400,
                     **kwargs) -> str:
    """Register a VNC session and mint its capability token.

    Returns the token (URL-safe). Caller MUST embed it in the URL
    handed to the user (`/vnc/<session_id>/<token>/...`); without the
    token in the path the route handler rejects every request 401/403.

    `owner_user_id` is required for non-test callers — every VNC
    session belongs to exactly one PawFlow user. `login_session_id`
    binds the token to the user's login session so logout revokes it.
    """
    if not owner_user_id:
        raise ValueError("register_session: owner_user_id is required")
    from core.capability_routes import mint_route_token
    token = mint_route_token(
        "vnc", session_id, owner_user_id,
        conversation_id=conversation_id,
        session_id=login_session_id,
        ttl_seconds=ttl_seconds)
    with _lock:
        _sessions[session_id] = {
            "port": port,
            "owner_user_id": owner_user_id,
            "conversation_id": conversation_id,
            "login_session_id": login_session_id,
            "capability_token": token,
            **kwargs,
        }
    return token


def unregister_session(session_id: str):
    with _lock:
        _sessions.pop(session_id, None)
    try:
        from core.capability_routes import revoke_route_tokens
        revoke_route_tokens(session_id)
    except Exception:
        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)


def get_session_token(session_id: str) -> str:
    """Return the capability token for a session (used by URL builders
    that issue the user-facing URL after register_session). Returns
    empty string if the session is unknown."""
    with _lock:
        entry = _sessions.get(session_id)
        return (entry or {}).get("capability_token", "") or ""


def update_session_ready(session_id: str):
    with _lock:
        if session_id in _sessions:
            _sessions[session_id]["ready"] = True


def update_session_error(session_id: str, error: str):
    with _lock:
        if session_id in _sessions:
            _sessions[session_id]["error"] = error


def cleanup_user_login_sessions(user_id: str):
    """Kill all login containers for a specific user."""
    import subprocess  # nosec B404
    with _lock:
        to_remove = [sid for sid, s in _sessions.items()
                     if s.get("user_id") == user_id]
    for sid in to_remove:
        session = _sessions.get(sid)
        if not session:
            continue
        container = session.get("container", "")
        if container:
            try:
                from core.server_relay_manager import _docker_cmd
                subprocess.run(_docker_cmd() + ["rm", "-f", container],  # nosec B603
                               capture_output=True, timeout=10)
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        unregister_session(sid)
    if to_remove:
        logger.info("Cleaned up %d login container(s) for user %s", len(to_remove), user_id)


def _get_vnc_port(session_id: str) -> int:
    with _lock:
        entry = _sessions.get(session_id)
    return entry["port"] if entry else 0


def _get_vnc_target(session_id: str) -> tuple:
    """Return (host, port) for a session. Host defaults to 127.0.0.1."""
    with _lock:
        entry = _sessions.get(session_id)
    if not entry:
        return ("127.0.0.1", 0)
    return (entry.get("host", "127.0.0.1"), entry["port"])


# noVNC local fallback directories (checked in order)
import os as _os
_NOVNC_LOCAL_DIRS = [
    _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "static", "novnc"),
    "/usr/share/novnc",
    "/usr/local/share/novnc",
]

_MIME_TYPES = {
    ".html": "text/html",
    ".js": "application/javascript",
    ".css": "text/css",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".json": "application/json",
}


def _serve_novnc_local(pending_req, sub_path: str) -> bool:
    """Serve noVNC static file from local filesystem. Returns True if served."""
    import os
    safe_path = os.path.normpath(sub_path).lstrip(os.sep).lstrip("/")
    if ".." in safe_path:
        return False
    for base_dir in _NOVNC_LOCAL_DIRS:
        full_path = os.path.join(base_dir, safe_path)
        if os.path.isfile(full_path):
            try:
                with open(full_path, "rb") as f:
                    body = f.read()
                ext = os.path.splitext(full_path)[1].lower()
                content_type = _MIME_TYPES.get(ext, "application/octet-stream")
                pending_req.complete(200, {
                "Content-Type": content_type,
                "Cross-Origin-Resource-Policy": "same-origin",
                "Cross-Origin-Opener-Policy": "same-origin",
                "Cross-Origin-Embedder-Policy": "require-corp",
            }, body)
                return True
            except Exception:
                return False
    return False


def _check_http_session_auth(pending_req) -> bool:
    """Check session auth for direct HTTP callbacks (not flow-based).

    Returns True if authenticated, False otherwise (sends 401).
    """
    try:
        from core.security import SecurityManager
        sm = SecurityManager.get_instance()
        token = None
        cookie_header = pending_req.headers.get("Cookie", "") or pending_req.headers.get("cookie", "")
        for part in cookie_header.split(";"):
            part = part.strip()
            if part.startswith("pawflow_token="):
                token = part[len("pawflow_token="):]
                break
        if not token:
            auth_header = pending_req.headers.get("Authorization", "") or pending_req.headers.get("authorization", "")
            if auth_header.lower().startswith("bearer "):
                token = auth_header[7:].strip()
        if not token or (not sm.get_session(token) and not sm.validate_api_key(token)):
            pending_req.complete(401, {"Content-Type": "application/json"},
                                 b'{"error": "Unauthorized"}')
            return False
    except Exception as e:
        logger.error("VNC session auth check failed: %s", e, exc_info=True)
        pending_req.complete(500, {"Content-Type": "application/json"},
                             b'{"error": "Internal Server Error"}')
        return False


def vnc_http_proxy(pending_req):
    """HTTP proxy callback for noVNC static files.

    Route pattern: /vnc/{session_id}/{token}/{path}. The capability
    token in the path binds the requester (auth_user) to this VNC
    session; cross-user access is rejected 403 here, before any
    backend connection. Falls back to serving from local noVNC files
    if the backend returns 405 (websockify without --web) or is
    unreachable.
    """
    import urllib.request
    import urllib.error

    session_id = pending_req.path_params.get("session_id", "")
    token = pending_req.path_params.get("token", "")
    sub_path = pending_req.path_params.get("path", "")

    from core.capability_routes import verify_route_request
    claims, err = verify_route_request(
        pending_req, "vnc", session_id, token, allow_bearer_only=True)
    if err is not None:
        pending_req.complete(
            err["status"], err["headers"], err["body"].encode("utf-8"))
        return

    host, port = _get_vnc_target(session_id)
    if not port:
        pending_req.complete(404, {"Content-Type": "application/json"},
                             b'{"error": "Unknown VNC session"}')
        return

    with _lock:
        session = _sessions.get(session_id)
    if session and session.get("use_relay_proxy"):
        if _serve_novnc_local(pending_req, sub_path):
            return
        relay_service = session.get("relay_service")
        if not relay_service:
            pending_req.complete(502, {"Content-Type": "application/json"},
                                 b'{"error": "Relay backend unavailable"}')
            return
        try:
            result = relay_service._request(
                "http_proxy",
                port=port,
                method="GET",
                req_path="/" + sub_path.lstrip("/"),
                req_headers={},
                req_body="",
            )
            if isinstance(result, dict) and isinstance(result.get("data"), dict):
                result = result["data"]
            if not isinstance(result, dict) or "status" not in result:
                pending_req.complete(502, {"Content-Type": "text/plain"},
                                     f"Bad proxy response: {result}".encode())
                return
            import base64
            body_b64 = result.get("body", "")
            body = base64.b64decode(body_b64) if body_b64 else b""
            headers = result.get("headers", {}) or {}
            content_type = headers.get("Content-Type") or headers.get("content-type") or "application/octet-stream"
            pending_req.complete(result["status"], {"Content-Type": content_type}, body)
            return
        except Exception as e:
            pending_req.complete(502, {"Content-Type": "application/json"},
                                 json.dumps({"error": str(e)}).encode())
            return

    # Proxy to backend (Docker container or local relay)
    target = f"http://{host}:{port}/{sub_path}"
    try:
        import time
        last_error = None
        deadline = time.time() + 8
        while True:
            try:
                req = urllib.request.Request(target, method="GET")
                with urllib.request.urlopen(req, timeout=2) as resp:  # nosec B310 - internal noVNC asset proxy target.
                    body = resp.read()
                    content_type = resp.headers.get("Content-Type", "application/octet-stream")
                    pending_req.complete(200, {
                        "Content-Type": content_type,
                        "Cross-Origin-Resource-Policy": "same-origin",
                        "Cross-Origin-Opener-Policy": "same-origin",
                        "Cross-Origin-Embedder-Policy": "require-corp",
                    }, body)
                    return
            except urllib.error.HTTPError:
                raise
            except Exception as e:
                last_error = e
                if time.time() >= deadline:
                    raise last_error
                time.sleep(0.2)
    except urllib.error.HTTPError as e:
        if e.code == 405 and _serve_novnc_local(pending_req, sub_path):
            return
        pending_req.complete(e.code, {"Content-Type": "text/plain"},
                             e.read()[:500])
    except Exception as e:
        if _serve_novnc_local(pending_req, sub_path):
            return
        pending_req.complete(502, {"Content-Type": "application/json"},
                             json.dumps({"error": str(e)}).encode())


def _ws_send(sock, data: bytes, opcode=0x01):
    length = len(data)
    frame = bytes([0x80 | opcode])
    if length < 126:
        frame += bytes([length])
    elif length < 65536:
        frame += bytes([126]) + struct.pack("!H", length)
    else:
        frame += bytes([127]) + struct.pack("!Q", length)
    frame += data
    sock.sendall(frame)


def _ws_recv(sock):
    def _recv_exact(n):
        data = b""
        while len(data) < n:
            chunk = sock.recv(n - len(data))
            if not chunk:
                raise ConnectionError("WS connection closed")
            data += chunk
        return data

    hdr = _recv_exact(2)
    opcode = hdr[0] & 0x0F
    masked = bool(hdr[1] & 0x80)
    length = hdr[1] & 0x7F
    if length == 126:
        length = struct.unpack("!H", _recv_exact(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", _recv_exact(8))[0]
    if masked:
        mask = _recv_exact(4)
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(_recv_exact(length)))
    else:
        payload = _recv_exact(length)
    return opcode, payload


def _ws_close(sock, code: int, reason: str):
    """Send a WebSocket close frame."""
    payload = struct.pack("!H", code) + reason.encode("utf-8")[:123]
    frame = bytes([0x88, len(payload)]) + payload
    try:
        sock.sendall(frame)
        sock.close()
    except Exception:
        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
