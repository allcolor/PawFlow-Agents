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

    # Look up the Docker port for this session
    target_port = _get_vnc_port(session_id)
    if not target_port:
        _ws_close(client_sock, 4001, "Unknown session")
        return

    # Connect to Docker noVNC websockify
    try:
        backend_sock = socket.create_connection(("127.0.0.1", target_port), timeout=5)
    except Exception as e:
        logger.warning("VNC proxy: cannot connect to Docker port %d: %s", target_port, e)
        _ws_close(client_sock, 4002, "Backend unavailable")
        return

    # Perform WS handshake with the backend (websockify expects a WS client)
    import base64, hashlib, os
    ws_key = base64.b64encode(os.urandom(16)).decode()
    handshake = (
        f"GET /websockify HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{target_port}\r\n"
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

    logger.info("VNC proxy: session %s connected (port %d)", session_id, target_port)

    # Use rfile/wfile from HTTP handler for the browser side
    # (raw socket recv() returns EOF on Windows due to buffering)
    client_rfile = meta.get("_rfile")
    client_wfile = meta.get("_wfile")

    # Bidirectional relay — 2 threads
    stop = threading.Event()

    def _relay_read_write(reader, writer, name):
        """Relay using file-like read/write."""
        logger.info("VNC relay %s: started", name)
        try:
            while not stop.is_set():
                data = reader.read(65536)
                if not data:
                    logger.info("VNC relay %s: EOF", name)
                    break
                writer.write(data)
                writer.flush()
        except Exception as e:
            logger.info("VNC relay %s: error %s: %s", name, type(e).__name__, e)
        finally:
            stop.set()

    def _relay_sock_to_file(src_sock, dst_file, name):
        """Relay from socket to file-like."""
        logger.info("VNC relay %s: started", name)
        try:
            while not stop.is_set():
                data = src_sock.recv(65536)
                if not data:
                    logger.info("VNC relay %s: EOF", name)
                    break
                dst_file.write(data)
                dst_file.flush()
        except Exception as e:
            logger.info("VNC relay %s: error %s: %s", name, type(e).__name__, e)
        finally:
            stop.set()

    def _relay_file_to_sock(src_file, dst_sock, name):
        """Relay from file-like to socket."""
        logger.info("VNC relay %s: started", name)
        try:
            while not stop.is_set():
                data = src_file.read1(65536)  # read1 = non-blocking read
                if not data:
                    logger.info("VNC relay %s: EOF", name)
                    break
                dst_sock.sendall(data)
        except Exception as e:
            logger.info("VNC relay %s: error %s: %s", name, type(e).__name__, e)
        finally:
            stop.set()

    if client_rfile and client_wfile:
        # browser→docker: read from rfile, send to backend socket
        t1 = threading.Thread(target=_relay_file_to_sock,
                              args=(client_rfile, backend_sock, "browser→docker"), daemon=True)
        # docker→browser: recv from backend socket, write to wfile
        t2 = threading.Thread(target=_relay_sock_to_file,
                              args=(backend_sock, client_wfile, "docker→browser"), daemon=True)
    else:
        # Fallback: raw socket relay (for non-HTTP WS handlers)
        def _relay_raw(src, dst, name):
            logger.info("VNC relay %s: started (raw)", name)
            try:
                while not stop.is_set():
                    data = src.recv(65536)
                    if not data:
                        break
                    dst.sendall(data)
            except Exception as e:
                logger.info("VNC relay %s: %s", name, e)
            finally:
                stop.set()
        t1 = threading.Thread(target=_relay_raw, args=(client_sock, backend_sock, "browser→docker"), daemon=True)
        t2 = threading.Thread(target=_relay_raw, args=(backend_sock, client_sock, "docker→browser"), daemon=True)

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


def _get_vnc_port(session_id: str) -> int:
    with _lock:
        entry = _sessions.get(session_id)
    return entry["port"] if entry else 0


def vnc_http_proxy(pending_req):
    """HTTP proxy callback for noVNC static files.

    Proxies GET requests to the Docker container's noVNC HTTP server.
    Route pattern: /vnc/{session_id}/{path}
    """
    import urllib.request
    import urllib.error

    session_id = pending_req.path_params.get("session_id", "")
    logger.info("[vnc-proxy] HTTP request: session=%s path=%s, known sessions: %s",
                session_id, pending_req.path_params.get("path", ""),
                list(_sessions.keys()))
    sub_path = pending_req.path_params.get("path", "")

    port = _get_vnc_port(session_id)
    if not port:
        pending_req.complete(404, {"Content-Type": "application/json"},
                             b'{"error": "Unknown VNC session"}')
        return

    # Proxy to Docker container
    target = f"http://127.0.0.1:{port}/{sub_path}"
    try:
        req = urllib.request.Request(target, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read()
            content_type = resp.headers.get("Content-Type", "application/octet-stream")
            pending_req.complete(200, {"Content-Type": content_type}, body)
    except urllib.error.HTTPError as e:
        pending_req.complete(e.code, {"Content-Type": "text/plain"},
                             e.read()[:500])
    except Exception as e:
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
