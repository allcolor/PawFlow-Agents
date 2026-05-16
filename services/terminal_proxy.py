"""Terminal WebSocket Proxy — bridges browser xterm.js to relay PTY.

Browser connects via WS to /terminal/{session_id}.
Messages are JSON: terminal_input, terminal_resize (browser→relay)
                  terminal_data, terminal_exit (relay→browser)

The proxy uses the RelayService WS connection to multiplex terminal
traffic alongside filesystem commands on the same relay channel.
"""

import base64
import json
import logging
import socket
import struct
import subprocess
import threading

logger = logging.getLogger(__name__)

# ── Session registry ──
# session_id → {relay_service_id, browser_sock, relay_service, ...}
_sessions: dict = {}
_lock = threading.Lock()


def register_terminal(session_id: str, relay_service_id: str, relay_service=None,
                       *, owner_user_id: str = "",
                       conversation_id: str = "",
                       login_session_id: str = "",
                       ttl_seconds: int = 86400,
                       **kwargs) -> str:
    """Register a terminal session and mint its capability token.

    Returns the token (URL-safe). Caller embeds it in the WS URL
    (`/terminal/<session>/<token>`); without it the WS handler
    rejects every connection 401/403.

    `owner_user_id` is required for non-test callers.
    """
    if not owner_user_id:
        raise ValueError("register_terminal: owner_user_id is required")
    from core.capability_routes import mint_route_token
    token = mint_route_token(
        "terminal", session_id, owner_user_id,
        conversation_id=conversation_id,
        session_id=login_session_id,
        ttl_seconds=ttl_seconds)
    with _lock:
        _sessions[session_id] = {
            "relay_service_id": relay_service_id,
            "relay_service": relay_service,
            "browser_sock": None,
            "owner_user_id": owner_user_id,
            "capability_token": token,
            **kwargs,
        }
    return token


def get_terminal_token(session_id: str) -> str:
    """Return the capability token for a terminal session, or empty
    string if the session is unknown."""
    with _lock:
        return (_sessions.get(session_id) or {}).get(
            "capability_token", "") or ""


def unregister_terminal(session_id: str):
    with _lock:
        sess = _sessions.pop(session_id, None)
    proc = (sess or {}).get("server_pipe_process")
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    try:
        from core.capability_routes import revoke_route_tokens
        revoke_route_tokens(session_id)
    except Exception:
        pass


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
    """WebSocket handler for /terminal/{session_id}/{token}.

    Called by HTTPListenerService after the WS upgrade handshake.
    Receives terminal_input/terminal_resize from browser, forwards to
    relay. The capability token in the URL binds the requester
    (auth_user) to this terminal; cross-user access is rejected here.
    """
    session_id = path_params.get("session_id", "")
    token = path_params.get("token", "")
    if not session_id:
        _ws_close(client_sock, 4000, "Missing session_id")
        return

    from core.capability_routes import verify_route_ws
    claims, err = verify_route_ws(meta or {}, "terminal", session_id, token)
    if err is not None:
        try:
            client_sock.sendall(err)
        except Exception:
            pass
        try:
            client_sock.close()
        except Exception:
            pass
        return

    with _lock:
        sess = _sessions.get(session_id)
    if not sess:
        _ws_close(client_sock, 4001, "Unknown terminal session")
        return

    # Register browser socket for this session
    with _lock:
        if session_id in _sessions:
            _sessions[session_id]["browser_sock"] = client_sock

    if sess.get("server_pipe_command"):
        logger.info("Terminal proxy: session %s connected (server pipe mode)", session_id)
        _server_pipe_ws_loop(client_sock, session_id, sess)
        return

    relay_service = sess.get("relay_service")
    if not relay_service:
        _ws_close(client_sock, 4002, "Relay not available")
        return

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

def _server_pipe_ws_loop(client_sock, session_id: str, sess: dict):
    """Bridge a browser terminal to a server-side subprocess pipe.

    For CC interactive debugging, the subprocess is `docker exec -i` running a
    Linux-side PTY bridge inside the provider container. The PawFlow server only
    handles ordinary pipes, so this stays portable on Windows.
    """
    cmd = sess.get("server_pipe_command") or []
    if not cmd:
        _ws_close(client_sock, 4003, "Missing server terminal command")
        return

    proc = None
    stop = threading.Event()
    send_lock = threading.Lock()

    def _send_json(payload: dict):
        with send_lock:
            _ws_send(client_sock, json.dumps(payload).encode("utf-8"))

    def _send_data(data: bytes):
        if data:
            _send_json({
                "type": "terminal_data",
                "session_id": session_id,
                "data": base64.b64encode(data).decode("ascii"),
            })

    def _reader():
        try:
            while not stop.is_set():
                try:
                    if not proc or not proc.stdout:
                        data = b""
                    elif hasattr(proc.stdout, "read1"):
                        data = proc.stdout.read1(4096)
                    else:
                        data = proc.stdout.read(1)
                except Exception:
                    break
                if not data:
                    break
                _send_data(data)
        except Exception as exc:
            if not stop.is_set():
                logger.debug("Server terminal reader stopped: %s", exc)
        finally:
            try:
                _send_json({"type": "terminal_exit", "session_id": session_id})
            except Exception:
                pass

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            close_fds=True,
        )
        with _lock:
            if session_id in _sessions:
                _sessions[session_id]["server_pipe_process"] = proc

        reader = threading.Thread(target=_reader, name=f"server-terminal-{session_id}", daemon=True)
        reader.start()

        while True:
            opcode, payload = _ws_recv(client_sock)
            if opcode == 0x08:
                break
            if opcode == 0x09:
                with send_lock:
                    _ws_send(client_sock, payload, opcode=0x0A)
                continue
            if opcode != 0x01:
                continue
            msg = json.loads(payload.decode("utf-8"))
            msg_type = msg.get("type", "")
            if msg_type == "terminal_input":
                data = base64.b64decode(msg.get("data", ""))
                if data and proc.stdin:
                    proc.stdin.write(data)
                    proc.stdin.flush()
            elif msg_type == "terminal_resize":
                pass
    except Exception as exc:
        if "Connection" not in str(exc):
            logger.warning("Server terminal proxy error: %s", exc)
    finally:
        stop.set()
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        with _lock:
            if session_id in _sessions:
                _sessions[session_id]["browser_sock"] = None
                _sessions[session_id].pop("server_pipe_process", None)
        try:
            client_sock.close()
        except Exception:
            pass


def _send_command_to_relay(relay_service, cmd: dict):
    """Send a command (terminal_input / terminal_resize) to the relay.

    Uses _ws_send_frame from filesystem_service — the previous code
    called listener._ws_send(...) which never existed on
    HTTPListenerService, so every keystroke and resize hit
    AttributeError, the except branch logged it, and the terminal
    appeared frozen (open, but unreceptive).

    Pool ordering matches RelayService._send_to_pool: most-recently-
    connected first, failover backward. The pool only ever holds
    more than one entry during a reconnect overlap.
    """
    import asyncio
    import uuid
    from services.filesystem_service import _ws_send_frame

    with relay_service._relay_pool_lock:
        pool = relay_service._relay_pool[:]
    if not pool:
        return

    request_id = uuid.uuid4().hex[:8]
    msg = {
        "type": "command",
        "request_id": request_id,
        **cmd,
    }
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
        logger.warning("Terminal command send error: %s", last_err)


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
