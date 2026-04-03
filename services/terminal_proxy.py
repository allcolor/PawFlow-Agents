"""Terminal WebSocket Proxy — bridges browser xterm.js to relay PTY.

Browser connects via WS to /terminal/{session_id}.
Messages are JSON: terminal_input, terminal_resize (browser→relay)
                  terminal_data, terminal_exit (relay→browser)

The proxy uses the RelayService WS connection to multiplex terminal
traffic alongside filesystem commands on the same relay channel.
"""

import json
import logging
import socket
import struct
import threading

logger = logging.getLogger(__name__)

# ── Session registry ──
# session_id → {relay_service_id, browser_sock, relay_service, ...}
_sessions: dict = {}
_lock = threading.Lock()


def register_terminal(session_id: str, relay_service_id: str, relay_service=None, **kwargs):
    with _lock:
        _sessions[session_id] = {
            "relay_service_id": relay_service_id,
            "relay_service": relay_service,
            "browser_sock": None,
            **kwargs,
        }


def unregister_terminal(session_id: str):
    with _lock:
        _sessions.pop(session_id, None)


def get_terminal(session_id: str):
    with _lock:
        return _sessions.get(session_id)


def dispatch_terminal_data(session_id: str, data_b64: str):
    """Called by RelayService when it receives terminal_data from relay."""
    with _lock:
        sess = _sessions.get(session_id)
    if not sess or not sess.get("browser_sock"):
        return
    try:
        msg = json.dumps({
            "type": "terminal_data",
            "session_id": session_id,
            "data": data_b64,
        }).encode("utf-8")
        _ws_send(sess["browser_sock"], msg)
    except Exception:
        pass


def dispatch_terminal_exit(session_id: str):
    """Called by RelayService when it receives terminal_exit from relay."""
    with _lock:
        sess = _sessions.get(session_id)
    if not sess or not sess.get("browser_sock"):
        return
    try:
        msg = json.dumps({
            "type": "terminal_exit",
            "session_id": session_id,
        }).encode("utf-8")
        _ws_send(sess["browser_sock"], msg)
    except Exception:
        pass


# ── WS handler (called by HTTPListenerService after handshake) ──

def terminal_ws_handler(client_sock, path_params: dict, meta: dict):
    """WebSocket handler for /terminal/{session_id}.

    Called by HTTPListenerService after the WS upgrade handshake.
    Receives terminal_input/terminal_resize from browser, forwards to relay.
    """
    session_id = path_params.get("session_id", "")
    if not session_id:
        _ws_close(client_sock, 4000, "Missing session_id")
        return

    with _lock:
        sess = _sessions.get(session_id)
    if not sess:
        _ws_close(client_sock, 4001, "Unknown terminal session")
        return

    # Direct WS mode: bridge browser WS ↔ host WS (local terminal)
    direct_ws = sess.get("direct_ws")
    if direct_ws:
        _terminal_ws_direct(client_sock, session_id, direct_ws)
        return

    relay_service = sess.get("relay_service")
    if not relay_service:
        _ws_close(client_sock, 4002, "Relay not available")
        return

    # Register browser socket for this session
    with _lock:
        if session_id in _sessions:
            _sessions[session_id]["browser_sock"] = client_sock

    logger.info("Terminal proxy: session %s connected (relay mode)", session_id)

    try:
        while True:
            opcode, payload = _ws_recv(client_sock)
            if opcode == 0x08:  # close
                break
            if opcode == 0x09:  # ping
                _ws_send(client_sock, payload, opcode=0x0A)
                continue
            if opcode != 0x01:  # text only
                continue

            msg = json.loads(payload.decode("utf-8"))
            msg_type = msg.get("type", "")

            if msg_type == "terminal_input":
                _send_command_to_relay(relay_service, {
                    "action": "write_terminal",
                    "session_id": session_id,
                    "data": msg.get("data", ""),
                })
            elif msg_type == "terminal_resize":
                _send_command_to_relay(relay_service, {
                    "action": "resize_terminal",
                    "session_id": session_id,
                    "cols": msg.get("cols", 80),
                    "rows": msg.get("rows", 24),
                })
    except Exception as e:
        if "0 bytes" not in str(e) and "Connection" not in str(e):
            logger.warning("Terminal proxy error: %s", e)
    finally:
        with _lock:
            if session_id in _sessions:
                _sessions[session_id]["browser_sock"] = None
        try:
            client_sock.close()
        except Exception:
            pass


def _terminal_ws_direct(browser_sock, session_id, host_addr):
    """Bridge browser WS ↔ host WS for local terminal.

    The host helper runs a WS server on host_addr (host:port).
    We connect to it and bidirectionally bridge all frames.
    """
    import hashlib as _hashlib
    import base64 as _b64

    host, port = host_addr.rsplit(":", 1)
    port = int(port)

    logger.info("Terminal proxy: session %s connecting to host WS %s:%d", session_id, host, port)

    # Connect to host WS
    host_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        host_sock.connect((host, port))
    except Exception as e:
        logger.error("Terminal proxy: failed to connect to host %s:%d: %s", host, port, e)
        _ws_close(browser_sock, 4003, f"Cannot connect to host terminal: {e}")
        return

    # WS handshake with host
    import secrets as _sec
    ws_key = _b64.b64encode(_sec.token_bytes(16)).decode()
    handshake = (f"GET / HTTP/1.1\r\nHost: {host}:{port}\r\n"
                 f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
                 f"Sec-WebSocket-Key: {ws_key}\r\n"
                 f"Sec-WebSocket-Version: 13\r\n\r\n")
    host_sock.sendall(handshake.encode())

    # Read handshake response
    resp = b""
    while b"\r\n\r\n" not in resp:
        chunk = host_sock.recv(4096)
        if not chunk:
            _ws_close(browser_sock, 4003, "Host WS handshake failed")
            host_sock.close()
            return
        resp += chunk

    if b"101" not in resp.split(b"\r\n")[0]:
        _ws_close(browser_sock, 4003, "Host WS handshake rejected")
        host_sock.close()
        return

    logger.info("Terminal proxy: session %s host WS connected", session_id)

    _stop = threading.Event()

    def _host_to_browser():
        """Forward host WS frames → browser WS (raw bytes → JSON terminal_data)."""
        import base64 as _b64h
        try:
            while not _stop.is_set():
                opcode, payload = _ws_recv(host_sock)
                if opcode == 0x08:
                    break
                if not payload:
                    continue
                # Convert raw PTY bytes to JSON terminal_data (same format as relay mode)
                msg = json.dumps({
                    "type": "terminal_data",
                    "session_id": session_id,
                    "data": _b64h.b64encode(payload).decode("ascii"),
                }).encode("utf-8")
                _ws_send(browser_sock, msg, opcode=0x01)  # text frame
        except Exception:
            pass
        finally:
            _stop.set()

    h2b = threading.Thread(target=_host_to_browser, daemon=True,
                           name=f"term-h2b-{session_id}")
    h2b.start()

    # Browser → host (JSON terminal_input → raw bytes to PTY)
    try:
        while not _stop.is_set():
            opcode, payload = _ws_recv(browser_sock)
            if opcode == 0x08:
                break
            if opcode == 0x09:  # ping from browser
                _ws_send(browser_sock, payload, opcode=0x0A)
                continue
            # Parse browser JSON message
            if opcode == 0x01:  # text
                try:
                    msg = json.loads(payload.decode("utf-8"))
                    msg_type = msg.get("type", "")
                    if msg_type == "terminal_input":
                        import base64 as _b64b
                        raw = _b64b.b64decode(msg.get("data", ""))
                        _ws_send_masked(host_sock, raw, opcode=0x02)  # binary to host
                        continue
                    elif msg_type == "terminal_resize":
                        # Forward resize as JSON to host
                        _ws_send_masked(host_sock, payload, opcode=0x01)
                        continue
                except (json.JSONDecodeError, Exception):
                    pass
            # Fallback: forward as-is
            _ws_send_masked(host_sock, payload, opcode=opcode)
    except Exception:
        pass
    finally:
        _stop.set()
        try:
            host_sock.close()
        except Exception:
            pass
        try:
            browser_sock.close()
        except Exception:
            pass
        with _lock:
            if session_id in _sessions:
                _sessions[session_id]["browser_sock"] = None
        logger.info("Terminal proxy: session %s disconnected", session_id)


def _ws_send_masked(sock, payload, opcode=0x02):
    """Send a masked WS frame (client→server frames must be masked)."""
    import secrets as _sec
    mask = _sec.token_bytes(4)
    length = len(payload)
    if length <= 125:
        header = bytes([0x80 | opcode, 0x80 | length])
    elif length <= 65535:
        header = bytes([0x80 | opcode, 0x80 | 126]) + length.to_bytes(2, 'big')
    else:
        header = bytes([0x80 | opcode, 0x80 | 127]) + length.to_bytes(8, 'big')
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    sock.sendall(header + mask + masked)


def _send_command_to_relay(relay_service, cmd: dict):
    """Send a command to the relay via the proven command pipeline."""
    import asyncio
    import uuid

    with relay_service._relay_pool_lock:
        pool = relay_service._relay_pool[:]
    if not pool:
        return

    # Wrap as a "command" type message — this goes through the relay's
    # _execute_command dispatch which is proven to work.
    request_id = uuid.uuid4().hex[:8]
    msg = {
        "type": "command",
        "request_id": request_id,
        **cmd,
    }
    payload = json.dumps(msg).encode("utf-8")
    conn = pool[0]
    writer, loop = conn["writer"], conn["loop"]

    async def _send(w=writer):
        listener = relay_service._connection
        if listener:
            await listener._ws_send(w, payload)

    try:
        asyncio.run_coroutine_threadsafe(_send(), loop).result(timeout=5)
    except Exception as e:
        logger.warning("Terminal command send error: %s", e)


# ── WS frame helpers ──

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


def _ws_close(sock, code=1000, reason=""):
    try:
        payload = struct.pack("!H", code) + reason.encode("utf-8")[:123]
        frame = bytes([0x88, len(payload)]) + payload
        sock.sendall(frame)
        sock.close()
    except Exception:
        pass
