"""VNC WebSocket Proxy — bidirectional relay between browser and Docker noVNC.

Used for the Claude Code server-side login flow. The browser connects
via WebSocket to PawFlow (authenticated), and this proxy relays frames
to the noVNC websockify running in a Docker container on localhost.

The proxy does not interpret frames — it relays raw bytes in both
directions until one side closes.
"""

import base64
import json
import logging
import socket
import struct
import threading
import time
import uuid

logger = logging.getLogger(__name__)

_WS_MAX_FRAME_BYTES = 64 * 1024 * 1024


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
    if session and session.get("relay_service") is not None:
        _vnc_ws_relay_proxy(client_sock, session_id, session, meta or {})
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
    import base64
    import os
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


def _vnc_ws_relay_proxy(client_sock, session_id: str, session: dict,
                        meta: dict):
    """Tunnel one browser VNC WebSocket through the relay connection."""
    relay_service = session["relay_service"]
    relay_id = session.get("relay_id", "")
    port = int(session.get("port") or 0)
    ws_session_id = uuid.uuid4().hex[:12]

    headers = {}
    for key, value in meta.get("headers", {}).items():
        if key.lower() in (
                "host", "upgrade", "connection", "sec-websocket-key",
                "sec-websocket-accept", "sec-websocket-version",
                "sec-websocket-extensions"):
            continue
        headers[key] = value
    headers["Host"] = f"127.0.0.1:{port}"
    headers["Sec-WebSocket-Protocol"] = "binary"

    with _lock:
        current = _sessions.get(session_id)
        if current is None:
            _ws_close(client_sock, 4001, "Unknown session")
            return
        current["vnc_ws_sessions"][ws_session_id] = {
            "browser_sock": client_sock,
        }

    try:
        result = relay_service._request(
            "desktop_ws_open",
            session_id=ws_session_id,
            port=port,
            ws_path="/websockify",
            headers=headers,
            local_screen=bool(session.get("local_screen")),
        )
        if not isinstance(result, dict) or not result.get("ok"):
            detail = (result.get("error", "Unknown")
                      if isinstance(result, dict) else str(result))
            with _lock:
                current = _sessions.get(session_id)
                if current:
                    current["vnc_ws_sessions"].pop(ws_session_id, None)
            _ws_close(client_sock, 4002, f"Failed: {detail}")
            return
    except Exception as exc:
        logger.warning("VNC relay tunnel open failed for %s: %s", relay_id, exc)
        with _lock:
            current = _sessions.get(session_id)
            if current:
                current["vnc_ws_sessions"].pop(ws_session_id, None)
        _ws_close(client_sock, 4002, f"Failed: {exc}")
        return

    logger.info("VNC relay tunnel connected: relay=%s session=%s port=%d",
                relay_id, ws_session_id, port)
    try:
        while True:
            opcode, payload = _ws_recv_frame(client_sock)
            if opcode == 0x08:
                break
            _send_command_to_relay(relay_service, {
                "action": "desktop_ws_send",
                "session_id": ws_session_id,
                "data": base64.b64encode(payload).decode("ascii"),
                "opcode": opcode,
            })
    except Exception as exc:
        logger.debug("VNC relay browser loop ended for %s: %s",
                     ws_session_id, exc)
    finally:
        _send_command_to_relay(relay_service, {
            "action": "desktop_ws_close",
            "session_id": ws_session_id,
        })
        with _lock:
            current = _sessions.get(session_id)
            if current:
                current["vnc_ws_sessions"].pop(ws_session_id, None)
        try:
            client_sock.close()
        except Exception:
            logger.debug("Ignored exception", exc_info=True)
        logger.info("VNC relay tunnel disconnected: relay=%s session=%s",
                    relay_id, ws_session_id)


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
        entry = {
            "port": port,
            "owner_user_id": owner_user_id,
            "conversation_id": conversation_id,
            "login_session_id": login_session_id,
            "capability_token": token,
            **kwargs,
        }
        entry.setdefault("vnc_ws_sessions", {})
        _sessions[session_id] = entry
    return token


def unregister_session(session_id: str):
    with _lock:
        session = _sessions.pop(session_id, None)
    if session:
        relay_service = session.get("relay_service")
        for ws_session_id, ws_session in list(
                session.get("vnc_ws_sessions", {}).items()):
            if relay_service is not None:
                _send_command_to_relay(relay_service, {
                    "action": "desktop_ws_close",
                    "session_id": ws_session_id,
                })
            browser_sock = ws_session.get("browser_sock")
            if browser_sock:
                try:
                    browser_sock.close()
                except Exception:
                    logger.debug("Ignored exception", exc_info=True)
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


def update_session_target(session_id: str, host: str, port: int):
    with _lock:
        if session_id in _sessions:
            _sessions[session_id]["host"] = host
            _sessions[session_id]["port"] = port


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


def dispatch_vnc_ws_data(relay_id: str, ws_session_id: str,
                         data_b64: str, opcode: int = 2):
    """Forward a VNC frame received from a relay to its browser socket."""
    with _lock:
        ws_session = None
        for session in _sessions.values():
            if session.get("relay_id") != relay_id:
                continue
            ws_session = session.get("vnc_ws_sessions", {}).get(ws_session_id)
            if ws_session:
                break
    if not ws_session or not ws_session.get("browser_sock"):
        logger.debug("desktop_ws_data: no browser for relay=%s session=%s",
                     relay_id, ws_session_id)
        return
    try:
        payload = base64.b64decode(data_b64)
        ws_session["browser_sock"].sendall(
            _ws_build_frame(payload, opcode=opcode))
    except Exception as exc:
        logger.warning("desktop_ws_data send failed: %s", exc)


def dispatch_vnc_ws_close(relay_id: str, ws_session_id: str):
    """Close the browser socket when the relay-side VNC socket closes."""
    with _lock:
        ws_session = None
        for session in _sessions.values():
            if session.get("relay_id") != relay_id:
                continue
            ws_session = session.get("vnc_ws_sessions", {}).pop(
                ws_session_id, None)
            if ws_session:
                break
    if ws_session and ws_session.get("browser_sock"):
        _ws_close(ws_session["browser_sock"], 1000, "Backend closed")


def _send_command_to_relay(relay_service, command: dict):
    """Send a fire-and-forget desktop command through a connected relay."""
    import asyncio
    from services.filesystem_service import _ws_send_frame

    with relay_service._relay_pool_lock:
        pool = relay_service._relay_pool[:]
    if not pool:
        return
    payload = json.dumps({
        "type": "command",
        "request_id": uuid.uuid4().hex[:8],
        **command,
    }).encode("utf-8")
    last_error = None
    for connection in reversed(pool):
        writer = connection["writer"]
        loop = connection["loop"]
        send_lock = connection.get("send_lock")

        async def _send(w=writer, lock=send_lock):
            if lock is not None:
                async with lock:
                    await _ws_send_frame(w, payload)
            else:
                await _ws_send_frame(w, payload)

        try:
            asyncio.run_coroutine_threadsafe(_send(), loop).result(timeout=5)
            return
        except Exception as exc:
            last_error = exc
    if last_error is not None:
        logger.warning("VNC relay command send failed: %s", last_error)


def _ws_build_frame(data: bytes, opcode: int = 0x02) -> bytes:
    frame = bytes([0x80 | opcode])
    length = len(data)
    if length < 126:
        return frame + bytes([length]) + data
    if length < 65536:
        return frame + bytes([126]) + struct.pack("!H", length) + data
    return frame + bytes([127]) + struct.pack("!Q", length) + data


def _ws_recv_frame(sock) -> tuple[int, bytes]:
    def _recv_exact(length: int) -> bytes:
        data = b""
        while len(data) < length:
            chunk = sock.recv(length - len(data))
            if not chunk:
                raise ConnectionError("WS connection closed")
            data += chunk
        return data

    header = _recv_exact(2)
    opcode = header[0] & 0x0F
    masked = bool(header[1] & 0x80)
    length = header[1] & 0x7F
    if length == 126:
        length = struct.unpack("!H", _recv_exact(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", _recv_exact(8))[0]
    if length > _WS_MAX_FRAME_BYTES:
        raise ConnectionError(f"WS frame too large: {length} bytes")
    mask = _recv_exact(4) if masked else b""
    payload = _recv_exact(length)
    if masked:
        payload = bytes(byte ^ mask[index % 4]
                        for index, byte in enumerate(payload))
    return opcode, payload


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
    ".oga": "audio/ogg",
    ".ogg": "audio/ogg",
    ".mp3": "audio/mpeg",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".json": "application/json",
}
_NOVNC_CACHE_CONTROL = "private, max-age=3600"


_PAWFLOW_NOVNC_CLIENT_SCRIPT = b"""
<script>
(function() {
  'use strict';
  function isResizeObserverLoopError(event) {
    const message = String(
      (event && event.message)
      || (event && event.error && event.error.message)
      || ''
    );
    return message === 'ResizeObserver loop limit exceeded'
      || message === 'ResizeObserver loop completed with undelivered notifications.';
  }

  function ignoreResizeObserverLoopError(event) {
    if (!isResizeObserverLoopError(event)) return;
    event.preventDefault();
    event.stopImmediatePropagation();
  }

  // Chromium reports deferred ResizeObserver work through window.error even
  // though noVNC keeps rendering normally. Register before the deferred
  // app/ui.js module so its fatal-status handler never turns that benign
  // browser notification into a permanent red overlay.
  window.addEventListener('error', ignoreResizeObserverLoopError, true);

  const repeatKeysyms = {
    Backspace: 0xff08,
    Tab: 0xff09,
    Enter: 0xff0d,
    Escape: 0xff1b,
    Delete: 0xffff,
    Insert: 0xff63,
    Home: 0xff50,
    End: 0xff57,
    PageUp: 0xff55,
    PageDown: 0xff56,
    ArrowLeft: 0xff51,
    ArrowUp: 0xff52,
    ArrowRight: 0xff53,
    ArrowDown: 0xff54,
  };
  let rfb = null;

  function canUseClipboard() {
    return window.isSecureContext && navigator.clipboard;
  }

  function sendCtrlV() {
    if (!rfb) return;
    rfb.sendKey(0xffe3, 'ControlLeft', true);
    rfb.sendKey(0x0076, 'KeyV');
    rfb.sendKey(0xffe3, 'ControlLeft', false);
  }

  function pasteHostClipboard() {
    if (!rfb || !canUseClipboard() || !navigator.clipboard.readText) {
      sendCtrlV();
      return;
    }
    navigator.clipboard.readText().then((text) => {
      rfb.clipboardPasteFrom(text || '');
      sendCtrlV();
    }).catch(() => sendCtrlV());
  }

  function onKeyDown(event) {
    if (!rfb) return;
    const pasteShortcut = (event.ctrlKey || event.metaKey) && !event.altKey &&
      !event.shiftKey && String(event.key || '').toLowerCase() === 'v';
    if (pasteShortcut) {
      event.preventDefault();
      event.stopImmediatePropagation();
      pasteHostClipboard();
      return;
    }
    const keysym = repeatKeysyms[event.key];
    if (!event.repeat || !keysym) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    rfb.sendKey(keysym, event.code || event.key);
  }

  function onRemoteClipboard(event) {
    if (!canUseClipboard() || !navigator.clipboard.writeText) return;
    const text = event && event.detail ? event.detail.text : '';
    navigator.clipboard.writeText(text || '').catch(() => {});
  }

  window.PawFlowNoVNC = {
    attach(nextRfb) {
      if (rfb === nextRfb) return;
      if (rfb) rfb.removeEventListener('clipboard', onRemoteClipboard);
      rfb = nextRfb;
      if (rfb) rfb.addEventListener('clipboard', onRemoteClipboard);
    }
  };
  document.addEventListener('keydown', onKeyDown, true);
})();
</script>
"""


def _patch_novnc_static_body(sub_path: str, body: bytes) -> bytes:
    """Inject PawFlow desktop behavior without vendoring noVNC files."""
    safe_path = _os.path.normpath(str(sub_path or "")).lstrip(_os.sep).lstrip("/")
    if safe_path in {"vnc.html", "vnc_lite.html"}:
        marker = b'<script type="module" crossorigin="anonymous" src="app/ui.js"></script>'
        if marker in body and b"PawFlowNoVNC" not in body:
            return body.replace(marker, marker + b"\n" + _PAWFLOW_NOVNC_CLIENT_SCRIPT, 1)
        return body
    if safe_path == "app/ui.js" and b"PawFlowNoVNC.attach" not in body:
        marker = b'UI.rfb.addEventListener("clipboard", UI.clipboardReceive);'
        if marker in body:
            patch = marker + b"\n        if (window.PawFlowNoVNC) { window.PawFlowNoVNC.attach(UI.rfb); }"
            return body.replace(marker, patch, 1)
    return body


def _is_novnc_static_path(sub_path: str) -> bool:
    """Return True for noVNC UI files that can be served locally."""
    safe_path = _os.path.normpath(str(sub_path or "")).lstrip(_os.sep).lstrip("/")
    if not safe_path or ".." in safe_path:
        return False
    if safe_path in {"vnc.html", "vnc_lite.html"}:
        return True
    return safe_path.startswith(("app/", "core/", "vendor/", "include/"))


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
                body = _patch_novnc_static_body(safe_path, body)
                if getattr(pending_req, "method", "GET") == "HEAD":
                    body = b""
                pending_req.complete(200, {
                    "Content-Type": content_type,
                    "Cache-Control": _NOVNC_CACHE_CONTROL,
                    "Cross-Origin-Resource-Policy": "same-origin",
                    "Cross-Origin-Opener-Policy": "same-origin",
                    "Cross-Origin-Embedder-Policy": "require-corp",
                }, body)
                return True
            except Exception:
                return False
    return False


def _serve_novnc_from_relay(pending_req, relay_service,
                            sub_path: str) -> bool:
    """Serve a noVNC UI asset from the relay runtime, not the host helper."""
    import base64

    safe_path = _os.path.normpath(str(sub_path or "")).lstrip(
        _os.sep).lstrip("/")
    try:
        result = relay_service._request("novnc_asset", path=safe_path)
        body_b64 = result.get("body", "") if isinstance(result, dict) else ""
        if not body_b64:
            return False
        body = base64.b64decode(body_b64, validate=True)
        body = _patch_novnc_static_body(safe_path, body)
        if getattr(pending_req, "method", "GET") == "HEAD":
            body = b""
        pending_req.complete(200, {
            "Content-Type": result.get(
                "content_type", "application/octet-stream"),
            "Cache-Control": _NOVNC_CACHE_CONTROL,
            "Cross-Origin-Resource-Policy": "same-origin",
            "Cross-Origin-Opener-Policy": "same-origin",
            "Cross-Origin-Embedder-Policy": "require-corp",
        }, body)
        return True
    except Exception:
        logger.debug("Relay noVNC asset fetch failed for %s",
                     safe_path, exc_info=True)
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


def _vnc_http_relay_proxy(pending_req, session_id: str, session: dict,
                          sub_path: str) -> None:
    """Fetch one noVNC HTTP asset through the session's outbound relay."""
    relay_service = session["relay_service"]
    relay_id = session.get("relay_id", "")
    port = int(session.get("port") or 0)
    proxied_path = "/" + str(sub_path or "").lstrip("/")
    query = getattr(pending_req, "query_string", "") or ""
    if query:
        proxied_path += "?" + query

    # noVNC does not need the PawFlow browser cookies.  Keep the upstream
    # request deliberately small and avoid forwarding Accept-Encoding so the
    # two PawFlow noVNC patches always see the uncompressed asset body.
    headers = {"Host": f"127.0.0.1:{port}"}

    try:
        from services._relay_http_response import RelayHttpResponseStream

        if (session.get("local_screen")
                and _is_novnc_static_path(sub_path)
                and _serve_novnc_from_relay(
                    pending_req, relay_service, sub_path)):
            return

        def _open_stream():
            label = f"vnc-http-{session_id[:8]}"
            if session.get("local_screen"):
                # Host-screen websockify runs on the user's machine, outside
                # the relay container.  local=True routes localhost through
                # the relay host helper, just like desktop_ws_open does.
                return RelayHttpResponseStream.for_fetch(
                    relay_service,
                    url=f"http://127.0.0.1:{port}{proxied_path}",
                    method="GET",
                    headers=headers,
                    body=b"",
                    local=True,
                    timeout=10,
                    label=label,
                ).start()
            # Containerized desktop websockify is in the relay runtime's
            # localhost namespace.
            return RelayHttpResponseStream.for_local_port(
                relay_service,
                port=port,
                method="GET",
                req_path=proxied_path,
                headers=headers,
                body=b"",
                timeout=10,
                label=label,
            ).start()

        deadline = time.time() + 8
        while True:
            stream = _open_stream()
            stream.wait_ready()
            if not stream.error or time.time() >= deadline:
                break
            stream.discard()
            time.sleep(0.2)

        if stream.error:
            detail = stream.error
            stream.discard()
            if (_is_novnc_static_path(sub_path)
                    and _serve_novnc_local(pending_req, sub_path)):
                return
            pending_req.complete(502, {"Content-Type": "application/json"},
                                 json.dumps({"error": detail}).encode())
            return

        status = int(stream.status)
        body = b"".join(stream.iter_bytes())
        if status >= 400 and _is_novnc_static_path(sub_path):
            if _serve_novnc_local(pending_req, sub_path):
                return

        body = _patch_novnc_static_body(sub_path, body)
        response_headers = dict(stream.headers)
        content_type = next((
            value for key, value in response_headers.items()
            if key.lower() == "content-type"
        ), "application/octet-stream")
        response_headers = {
            key: value for key, value in response_headers.items()
            if key.lower() not in {"content-type", "cache-control"}
        }
        response_headers["Content-Type"] = content_type
        response_headers.update({
            "Cache-Control": _NOVNC_CACHE_CONTROL,
            "Cross-Origin-Resource-Policy": "same-origin",
            "Cross-Origin-Opener-Policy": "same-origin",
            "Cross-Origin-Embedder-Policy": "require-corp",
        })
        if getattr(pending_req, "method", "GET") == "HEAD":
            body = b""
        pending_req.complete(status, response_headers, body)
    except Exception as exc:
        logger.warning("VNC relay HTTP error for %s (relay=%s port=%d): %s",
                       session_id, relay_id, port, exc)
        pending_req.complete(502, {"Content-Type": "application/json"},
                             json.dumps({"error": str(exc)}).encode())


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

    with _lock:
        session = _sessions.get(session_id)
    host, port = _get_vnc_target(session_id)
    if not port:
        pending_req.complete(404, {"Content-Type": "application/json"},
                             b'{"error": "Unknown VNC session"}')
        return

    # noVNC is application UI, not session state.  The server image bundles a
    # complete noVNC distribution, so serve it before
    # opening a relay stream.  The relay/backend paths remain fallbacks for
    # non-Docker installations that do not have a local noVNC tree.
    if (_is_novnc_static_path(sub_path)
            and _serve_novnc_local(pending_req, sub_path)):
        return

    if session and session.get("relay_service") is not None:
        _vnc_http_relay_proxy(pending_req, session_id, session, sub_path)
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
                    body = _patch_novnc_static_body(sub_path, body)
                    pending_req.complete(200, {
                        "Content-Type": content_type,
                        "Cache-Control": _NOVNC_CACHE_CONTROL,
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
        if _is_novnc_static_path(sub_path) and _serve_novnc_local(pending_req, sub_path):
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
        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
