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

logger = logging.getLogger(__name__)


def vnc_ws_proxy(client_sock, path_params: dict, meta: dict):
    """WebSocket handler for /vnc/{session_id}/websockify.

    Called by HTTPListenerService after the WS handshake with the browser.
    Connects to the Docker noVNC websockify and relays frames bidirectionally.

    Args:
        client_sock: Raw socket to the browser (already past WS handshake).
        path_params: {"session_id": "abc123"} from URL pattern.
        meta: {"path", "query", "headers", "remote_addr"}.
    """
    session_id = path_params.get("session_id", "")
    if not session_id:
        _ws_close(client_sock, 4000, "Missing session_id")
        return

    # Look up the target host:port for this session
    target_host, target_port = _get_vnc_target(session_id)
    if not target_port:
        _ws_close(client_sock, 4001, "Unknown session")
        return

    # Connect to noVNC websockify (Docker container or local relay)
    try:
        backend_sock = socket.create_connection((target_host, target_port))
        # No timeout on the socket — VNC relay needs to stay open indefinitely
    except Exception as e:
        logger.warning("VNC proxy: cannot connect to %s:%d: %s", target_host, target_port, e)
        _ws_close(client_sock, 4002, "Backend unavailable")
        return

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
                    logger.debug("VNC proxy: %s EOF after %d bytes", name, _bytes)
                    break
                _bytes += len(data)
                dst.sendall(data)
        except Exception as _e:
            logger.debug("VNC proxy: %s error after %d bytes: %s", name, _bytes, _e)
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
            pass

    logger.info("VNC proxy: session %s disconnected", session_id)


# -- Session registry (maps session_id → Docker port) --

_sessions: dict = {}  # session_id → {"port": int, ...}
_lock = threading.Lock()


def register_session(session_id: str, port: int, **kwargs):
    with _lock:
        _sessions[session_id] = {"port": port, **kwargs}


def unregister_session(session_id: str):
    with _lock:
        _sessions.pop(session_id, None)


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
    import subprocess
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
                subprocess.run(_docker_cmd() + ["rm", "-f", container],
                               capture_output=True, timeout=10)
            except Exception:
                pass
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


def vnc_http_proxy(pending_req):
    """HTTP proxy callback for noVNC static files.

    Proxies GET requests to the backend noVNC HTTP server.
    Falls back to serving from local noVNC files if the backend
    returns 405 (websockify without --web) or is unreachable.
    Route pattern: /vnc/{session_id}/{path}
    """
    import urllib.request
    import urllib.error

    session_id = pending_req.path_params.get("session_id", "")
    sub_path = pending_req.path_params.get("path", "")

    host, port = _get_vnc_target(session_id)
    if not port:
        pending_req.complete(404, {"Content-Type": "application/json"},
                             b'{"error": "Unknown VNC session"}')
        return

    # Proxy to backend (Docker container or local relay)
    target = f"http://{host}:{port}/{sub_path}"
    try:
        req = urllib.request.Request(target, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read()
            content_type = resp.headers.get("Content-Type", "application/octet-stream")
            pending_req.complete(200, {
                "Content-Type": content_type,
                "Cross-Origin-Resource-Policy": "same-origin",
                "Cross-Origin-Opener-Policy": "same-origin",
                "Cross-Origin-Embedder-Policy": "require-corp",
            }, body)
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


def _ws_close(sock, code: int, reason: str):
    """Send a WebSocket close frame."""
    payload = struct.pack("!H", code) + reason.encode("utf-8")[:123]
    frame = bytes([0x88, len(payload)]) + payload
    try:
        sock.sendall(frame)
        sock.close()
    except Exception:
        pass
