"""Code-Server HTTP/WS Proxy — bridges browser to relay's code-server.

HTTP requests are proxied via the relay's `http_proxy` command action.
WebSocket connections are tunneled through the relay's WS connection
(multiplexed alongside filesystem commands and terminal traffic).

Routes: /code/{path+}  where path starts with relay_id/...
code-server is started with --base-path /code/{relay_id} so all
generated URLs include the correct prefix.
"""

import base64
import json
import logging
import struct
import threading
import uuid

logger = logging.getLogger(__name__)

# —— Session registry ——
# relay_id → {port, relay_service, base_path, cs_ws_sessions: {sid → {browser_sock, ...}}}
_sessions: dict = {}
_lock = threading.Lock()


def register_code_server(relay_id: str, port: int, relay_service, base_path: str):
    with _lock:
        _sessions[relay_id] = {
            "port": port,
            "relay_service": relay_service,
            "base_path": base_path,
            "cs_ws_sessions": {},
        }


def unregister_code_server(relay_id: str):
    with _lock:
        sess = _sessions.pop(relay_id, None)
    if sess:
        for ws_sess in sess.get("cs_ws_sessions", {}).values():
            sock = ws_sess.get("browser_sock")
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass


def get_code_server(relay_id: str):
    with _lock:
        return _sessions.get(relay_id)


# —— HTTP proxy callback ——

def code_http_proxy(pending_req):
    """HTTP callback for /code/{path+}.

    Extracts relay_id from the path, proxies to code-server via relay.
    """
    full_path = pending_req.path_params.get("path", "")
    relay_id = full_path.split("/", 1)[0]
    original_path = pending_req.path
    query = pending_req.query_string
    if query:
        original_path += "?" + query

    with _lock:
        sess = _sessions.get(relay_id)
    if not sess:
        pending_req.complete(404, {"Content-Type": "application/json"},
                             b'{"error": "Code server not running for this relay"}')
        return

    relay_service = sess["relay_service"]
    port = sess["port"]

    # Forward headers (strip hop-by-hop)
    fwd_headers = {}
    for k, v in pending_req.headers.items():
        kl = k.lower()
        if kl in ("host", "connection", "upgrade", "sec-websocket-key",
                  "sec-websocket-version", "sec-websocket-extensions"):
            continue
        fwd_headers[k] = v
    fwd_headers["Host"] = f"127.0.0.1:{port}"

    try:
        result = relay_service._request(
            "http_proxy",
            port=port,
            method=pending_req.method,
            req_path=original_path,
            req_headers=fwd_headers,
            req_body=base64.b64encode(pending_req.body).decode("ascii") if pending_req.body else "",
        )
        if not isinstance(result, dict) or "status" not in result:
            pending_req.complete(502, {"Content-Type": "text/plain"},
                                 f"Bad proxy response: {result}".encode())
            return

        status = result["status"]
        resp_headers = result.get("headers", {})
        resp_body = base64.b64decode(result.get("body", "")) if result.get("body") else b""

        # Remove hop-by-hop headers
        for k in list(resp_headers):
            if k.lower() in ("transfer-encoding", "connection", "keep-alive"):
                del resp_headers[k]

        pending_req.complete(status, resp_headers, resp_body)
    except Exception as e:
        logger.warning("Code proxy HTTP error for %s: %s", relay_id, e)
        pending_req.complete(502, {"Content-Type": "application/json"},
                             json.dumps({"error": str(e)}).encode())


# —— WebSocket proxy handler ——

def code_ws_proxy(client_sock, path_params: dict, meta: dict):
    """WS handler for /code/{path+}.

    Tunnels browser WS <-> relay <-> code-server WS.
    """
    full_path = path_params.get("path", "")
    relay_id = full_path.split("/", 1)[0]
    original_path = meta.get("path", "/")
    query = meta.get("query", "")
    if query:
        original_path += "?" + query

    with _lock:
        sess = _sessions.get(relay_id)
    if not sess:
        _ws_close(client_sock, 4001, "Code server not running")
        return

    relay_service = sess["relay_service"]
    port = sess["port"]

    # Open a WS connection on the relay to code-server
    ws_session_id = uuid.uuid4().hex[:12]

    fwd_headers = {}
    for k, v in meta.get("headers", {}).items():
        kl = k.lower()
        if kl in ("sec-websocket-key", "sec-websocket-accept",
                  "upgrade", "connection"):
            continue
        fwd_headers[k] = v
    fwd_headers["Host"] = f"127.0.0.1:{port}"

    try:
        result = relay_service._request(
            "cs_ws_open",
            session_id=ws_session_id,
            port=port,
            path=original_path,
            headers=fwd_headers,
        )
        if not isinstance(result, dict) or not result.get("ok"):
            err = result.get("error", "Unknown") if isinstance(result, dict) else str(result)
            _ws_close(client_sock, 4002, f"Failed: {err}")
            return
    except Exception as e:
        _ws_close(client_sock, 4002, f"Failed: {e}")
        return

    # Register browser socket
    with _lock:
        if relay_id in _sessions:
            _sessions[relay_id]["cs_ws_sessions"][ws_session_id] = {
                "browser_sock": client_sock,
            }

    logger.info("Code WS proxy: relay=%s session=%s connected", relay_id, ws_session_id)

    try:
        while True:
            opcode, payload = _ws_recv(client_sock)
            if opcode == 0x08:  # close
                break
            if opcode == 0x09:  # ping
                _ws_send(client_sock, payload, opcode=0x0A)
                continue

            _send_command_to_relay(relay_service, {
                "action": "cs_ws_send",
                "session_id": ws_session_id,
                "data": base64.b64encode(payload).decode("ascii"),
                "opcode": opcode,
            })
    except Exception as e:
        if "0 bytes" not in str(e) and "Connection" not in str(e):
            logger.debug("Code WS proxy error: %s", e)
    finally:
        try:
            _send_command_to_relay(relay_service, {
                "action": "cs_ws_close",
                "session_id": ws_session_id,
            })
        except Exception:
            pass

        with _lock:
            if relay_id in _sessions:
                _sessions[relay_id]["cs_ws_sessions"].pop(ws_session_id, None)
        try:
            client_sock.close()
        except Exception:
            pass
        logger.info("Code WS proxy: session=%s disconnected", ws_session_id)


def dispatch_cs_ws_data(relay_id: str, ws_session_id: str, data_b64: str, opcode: int = 1):
    """Called when relay sends cs_ws_data — forward to browser."""
    with _lock:
        sess = _sessions.get(relay_id)
    if not sess:
        return
    ws_sess = sess["cs_ws_sessions"].get(ws_session_id)
    if not ws_sess or not ws_sess.get("browser_sock"):
        return
    try:
        payload = base64.b64decode(data_b64)
        _ws_send(ws_sess["browser_sock"], payload, opcode=opcode)
    except Exception:
        pass


def dispatch_cs_ws_close(relay_id: str, ws_session_id: str):
    """Called when relay's code-server WS closes."""
    with _lock:
        sess = _sessions.get(relay_id)
    if not sess:
        return
    ws_sess = sess["cs_ws_sessions"].pop(ws_session_id, None)
    if ws_sess and ws_sess.get("browser_sock"):
        try:
            _ws_close(ws_sess["browser_sock"], 1000, "Backend closed")
        except Exception:
            pass


def _send_command_to_relay(relay_service, cmd: dict):
    """Send a command to the relay via the command pipeline."""
    import asyncio

    with relay_service._relay_pool_lock:
        pool = relay_service._relay_pool[:]
    if not pool:
        return

    request_id = uuid.uuid4().hex[:8]
    msg = {"type": "command", "request_id": request_id, **cmd}
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
        logger.warning("Code proxy command send error: %s", e)


# —— WS frame helpers ——

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
                raise ConnectionError("WS connection closed (0 bytes)")
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
