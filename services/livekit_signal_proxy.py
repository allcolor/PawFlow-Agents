"""LiveKit signal WebSocket proxy — same-origin path for managed stacks.

Browsers on an HTTPS PawFlow page cannot open a cleartext ws:// connection
to the managed livekit-server (mixed content). This proxy exposes the
LiveKit signal WebSocket on PawFlow's own listener (`/livekit/rtc?...`),
so the browser connects same-origin (wss when PawFlow is TLS) and the
bytes are relayed verbatim to the local livekit-server. Only the signal
path transits here — WebRTC media flows over UDP/ICE directly between the
browser and livekit-server.

The proxy is a dumb pipe: authentication is the LiveKit access token in
the query string, verified by livekit-server itself (managed deployments
use a generated API secret). The route only relays when the managed stack
has been provisioned — external-LiveKit deployments never enable it.
"""

import base64
import logging
import os
import socket
import threading

logger = logging.getLogger(__name__)

_BACKEND_HOST = "127.0.0.1"


def livekit_signal_ws_proxy(client_sock, path_params: dict, meta: dict):
    """WebSocket handler for GET /livekit/{path+} (public route).

    Relays the browser's LiveKit signal WebSocket to the managed
    livekit-server on localhost. Refuses when no managed stack exists.
    """
    from core.realtime_stack_manager import (RealtimeStackManager,
                                             SIGNAL_PORT)
    mgr = RealtimeStackManager.get_instance()
    if not mgr.has_state():
        _ws_close(client_sock, 4404, "No managed realtime stack")
        return

    sub_path = "/" + (path_params.get("path", "") or "").lstrip("/")
    query = meta.get("query", "") or ""
    target = sub_path + ("?" + query if query else "")

    try:
        backend = socket.create_connection((_BACKEND_HOST, SIGNAL_PORT),
                                           timeout=5)
    except OSError as e:
        logger.warning("[livekit-proxy] backend unavailable: %s", e)
        _ws_close(client_sock, 4002, "LiveKit backend unavailable")
        return
    backend.settimeout(None)

    ws_key = base64.b64encode(os.urandom(16)).decode()
    handshake = (
        f"GET {target} HTTP/1.1\r\n"
        f"Host: {_BACKEND_HOST}:{SIGNAL_PORT}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {ws_key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n"
        f"\r\n"
    )
    try:
        backend.sendall(handshake.encode("latin-1"))
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = backend.recv(4096)
            if not chunk:
                raise OSError("backend closed during handshake")
            resp += chunk
    except OSError as e:
        logger.warning("[livekit-proxy] backend handshake failed: %s", e)
        _ws_close(client_sock, 4003, "Backend handshake failed")
        backend.close()
        return

    if b" 101 " not in resp.split(b"\r\n", 1)[0] + b" ":
        logger.warning("[livekit-proxy] backend refused upgrade: %s",
                       resp[:120].decode("latin-1", errors="replace"))
        _ws_close(client_sock, 4003, "Backend refused upgrade")
        backend.close()
        return

    leftover = resp[resp.index(b"\r\n\r\n") + 4:]
    if leftover:
        client_sock.sendall(leftover)

    stop = threading.Event()

    def _relay(src, dst, name):
        try:
            while not stop.is_set():
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)
        except OSError:
            logger.debug("[livekit-proxy] %s relay ended", name,
                         exc_info=True)
        finally:
            stop.set()

    t1 = threading.Thread(target=_relay,
                          args=(client_sock, backend, "browser->livekit"),
                          daemon=True)
    t2 = threading.Thread(target=_relay,
                          args=(backend, client_sock, "livekit->browser"),
                          daemon=True)
    t1.start()
    t2.start()
    stop.wait()
    for s in (client_sock, backend):
        try:
            s.close()
        except OSError:
            logger.debug("[livekit-proxy] close failed", exc_info=True)


def _ws_close(sock, code: int, reason: str):
    """Send a WS close frame (server→client, unmasked) and close."""
    try:
        payload = code.to_bytes(2, "big") + reason.encode()[:120]
        frame = bytes([0x88, len(payload)]) + payload
        sock.sendall(frame)
    except OSError:
        logger.debug("[livekit-proxy] close frame failed", exc_info=True)
    try:
        sock.close()
    except OSError:
        logger.debug("[livekit-proxy] socket close failed", exc_info=True)
