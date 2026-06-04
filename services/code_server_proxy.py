"""Code-Server HTTP/WS Proxy — bridges browser to relay's code-server.

HTTP requests are proxied via the relay's `http_proxy` command action.
WebSocket connections are tunneled through the relay's WS connection.

Routes: /code/{path+}  where path starts with relay_id/...
The proxy strips /code/{relay_id} and forwards to code-server at root.
"""

import base64
import json
import logging
import struct
import threading
import time
import uuid

logger = logging.getLogger(__name__)

# —— Session registry ——
# session_id → {port, relay_service, relay_id, owner_user_id,
#               capability_token, cs_ws_sessions: {sid → {browser_sock, ...}}}
_sessions: dict = {}
_relay_to_session: dict = {}  # relay_id → session_id (for legacy lookups)
_lock = threading.Lock()

_VSDA_JS = b"""
(function () {
  class Validator {
    createNewMessage(value) { return value; }
    validate() { return "ok"; }
    free() {}
  }
  globalThis.vsda_web = {
    default: async function () {},
    validator: Validator,
    sign: function (value) { return value; }
  };
  if (typeof define === "function" && define.amd) {
    define([], function () { return globalThis.vsda_web; });
  }
}());
""".strip()

_EMPTY_WASM_MODULE = b"\x00asm\x01\x00\x00\x00"

_CODE_SERVER_CSP = (
    "default-src 'self' https://*.vscode-cdn.net https://vscode-cdn.net; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' blob: "
    "https://*.vscode-cdn.net https://vscode-cdn.net; "
    "script-src-elem 'self' 'unsafe-inline' 'unsafe-eval' blob: "
    "https://*.vscode-cdn.net https://vscode-cdn.net; "
    "style-src 'self' 'unsafe-inline' "
    "https://*.vscode-cdn.net https://vscode-cdn.net; "
    "style-src-elem 'self' 'unsafe-inline' "
    "https://*.vscode-cdn.net https://vscode-cdn.net; "
    "img-src 'self' data: blob: https://*.vscode-cdn.net https://vscode-cdn.net; "
    "font-src 'self' data: https://*.vscode-cdn.net https://vscode-cdn.net; "
    "media-src 'self' data: blob:; "
    "connect-src 'self' ws: wss: "
    "https://*.vscode-cdn.net https://vscode-cdn.net https://open-vsx.org; "
    "worker-src 'self' blob:; "
    "frame-src 'self' blob: https://*.vscode-cdn.net https://vscode-cdn.net; "
    "object-src 'none'; base-uri 'self'"
)


def _tokenless_code_server_asset_path(token: str, sub_path: str) -> str:
    """Return a VS Code static asset path when the browser omitted the token."""
    candidate = token.strip("/")
    rest = sub_path.strip("/")
    if rest:
        candidate = f"{candidate}/{rest}"
    if not candidate or candidate.startswith("/") or ".." in candidate.split("/"):
        return ""
    first = candidate.split("/", 1)[0]
    if first == "_i" or first.startswith("stable-"):
        return candidate
    if first in {"static", "out", "extensions"}:
        return candidate
    return ""


def _code_server_builtin_asset(sub_path: str):
    """Return small compatibility assets missing from OSS code-server builds."""
    asset_name = sub_path.rsplit("/", 1)[-1]
    if asset_name == "vsda.js":
        return "application/javascript", _VSDA_JS
    if asset_name == "vsda_bg.wasm":
        return "application/wasm", _EMPTY_WASM_MODULE
    if asset_name == "seti.woff":
        return "font/woff", b""
    return None


def register_code_server(relay_id: str, port: int, relay_service,
                          *, owner_user_id: str = "",
                          conversation_id: str = "",
                          login_session_id: str = "",
                          ttl_seconds: int = 86400,
                          **kwargs) -> tuple[str, str]:
    """Register a code-server session and mint its capability token.

    Returns (session_id, token). Caller embeds both in the URL handed
    to the user (`/code/<session_id>/<token>/...`); without the token
    the route handler rejects every request 401/403.

    `owner_user_id` is required for non-test callers.
    """
    if not owner_user_id:
        raise ValueError("register_code_server: owner_user_id is required")
    session_id = uuid.uuid4().hex[:16]
    from core.capability_routes import mint_route_token
    token = mint_route_token(
        "code_server", session_id, owner_user_id,
        conversation_id=conversation_id,
        session_id=login_session_id,
        ttl_seconds=ttl_seconds)
    with _lock:
        _sessions[session_id] = {
            "port": port,
            "relay_id": relay_id,
            "relay_service": relay_service,
            "owner_user_id": owner_user_id,
            "capability_token": token,
            "base_path": f"/code/{session_id}/{token}/",
            "upstream_base_path": "/",
            "cs_ws_sessions": {},
        }
        _relay_to_session[relay_id] = session_id
    logger.info(
        "Code-server registered: session=%s relay=%s port=%d owner=%s",
        session_id, relay_id, port, owner_user_id)
    return session_id, token


def update_code_server_port(session_id: str, port: int,
                            upstream_base_path: str | None = None) -> None:
    """Update the upstream port after a reserved code-server session starts."""
    with _lock:
        sess = _sessions.get(session_id)
        if sess is not None:
            sess["port"] = port
            if upstream_base_path is not None:
                sess["upstream_base_path"] = upstream_base_path


def _upstream_path(sess: dict, public_base_path: str, sub_path: str,
                   query: str = "") -> str:
    upstream_base = sess.get("upstream_base_path")
    if upstream_base is None:
        upstream_base = public_base_path
    upstream_base = str(upstream_base or "/")
    if not upstream_base.endswith("/"):
        upstream_base += "/"
    if upstream_base == "/":
        path = "/" + sub_path.lstrip("/")
    else:
        path = upstream_base + sub_path.lstrip("/")
    if query:
        path += "?" + query
    return path


def _proxy_result_payload(result):
    if isinstance(result, dict) and isinstance(result.get("data"), dict):
        return result["data"]
    return result


def _proxy_connection_refused(result) -> bool:
    if isinstance(result, dict):
        text = str(result.get("error") or result.get("detail") or result)
    else:
        text = str(result)
    return "connection refused" in text.lower() or "errno 111" in text.lower()


def _restart_code_server_session(session_id: str, relay_service, base_path: str):
    result = relay_service._request("start_code_server", base_path=base_path)
    data = _proxy_result_payload(result)
    if not isinstance(data, dict):
        return None
    port = data.get("port")
    if not port:
        return None
    update_code_server_port(
        session_id, port, upstream_base_path=data.get("upstream_base_path"))
    return port


def unregister_code_server(relay_id: str):
    with _lock:
        session_id = _relay_to_session.pop(relay_id, "")
        sess = _sessions.pop(session_id, None) if session_id else None
    if session_id:
        try:
            from core.capability_routes import revoke_route_tokens
            revoke_route_tokens(session_id)
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
    if sess:
        for ws_sess in sess.get("cs_ws_sessions", {}).values():
            sock = ws_sess.get("browser_sock")
            if sock:
                try:
                    sock.close()
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)


# —— HTTP proxy callback ——

def code_http_proxy(pending_req):
    """HTTP callback for /code/{session_id}/{token}/{path+}.

    Proxies to code-server via relay's http_proxy action. The
    capability token in the path binds the requester (auth_user) to
    this code-server session; cross-user access is rejected 403
    here, before any backend call.
    """
    session_id = pending_req.path_params.get("session_id", "")
    token = pending_req.path_params.get("token", "")
    with _lock:
        sess = _sessions.get(session_id)
    if not sess:
        pending_req.complete(404, {"Content-Type": "application/json"},
                             b'{"error": "Code server session not found"}')
        return

    sub_path = pending_req.path_params.get("path", "")
    from core.capability_routes import verify_route_request
    claims, err = verify_route_request(
        pending_req, "code_server", session_id, token,
        allow_bearer_only=True)
    if err is not None:
        tokenless_asset = ""
        if pending_req.method in ("GET", "HEAD"):
            tokenless_asset = _tokenless_code_server_asset_path(token, sub_path)
        if tokenless_asset:
            sub_path = tokenless_asset
        else:
            pending_req.complete(
                err["status"], err["headers"], err["body"].encode("utf-8"))
            return

    relay_id = sess["relay_id"]

    builtin_asset = _code_server_builtin_asset(sub_path)
    if pending_req.method == "GET" and builtin_asset is not None:
        content_type, body = builtin_asset
        pending_req.complete(
            200,
            {"Content-Type": content_type,
             "Content-Security-Policy": _CODE_SERVER_CSP},
            body)
        return

    base_path = sess.get("base_path") or f"/code/{session_id}/{token}/"
    proxied_path = _upstream_path(sess, base_path, sub_path,
                                  pending_req.query_string)

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
        def _proxy_once(target_port: int):
            fwd_headers["Host"] = f"127.0.0.1:{target_port}"
            return relay_service._request(
                "http_proxy",
                port=target_port,
                method=pending_req.method,
                req_path=proxied_path,
                req_headers=fwd_headers,
                req_body=base64.b64encode(pending_req.body).decode("ascii") if pending_req.body else "",
            )

        def _proxy_until_ready(target_port: int, wait_seconds: float = 8.0):
            deadline = time.time() + wait_seconds
            last_result = None
            while True:
                try:
                    last_result = _proxy_result_payload(_proxy_once(target_port))
                except Exception as exc:
                    if not _proxy_connection_refused(exc) or time.time() >= deadline:
                        raise
                    time.sleep(0.2)
                    continue
                if not _proxy_connection_refused(last_result) or time.time() >= deadline:
                    return last_result
                time.sleep(0.2)

        try:
            result = _proxy_result_payload(_proxy_once(port))
        except Exception as first_error:
            if not _proxy_connection_refused(first_error):
                raise
            restarted_port = _restart_code_server_session(
                session_id, relay_service, base_path)
            if not restarted_port:
                raise
            port = restarted_port
            result = _proxy_until_ready(port)

        if _proxy_connection_refused(result):
            restarted_port = _restart_code_server_session(
                session_id, relay_service, base_path)
            if restarted_port:
                port = restarted_port
                result = _proxy_until_ready(port)

        if not isinstance(result, dict) or "status" not in result:
            pending_req.complete(502, {"Content-Type": "text/plain"},
                                 f"Bad proxy response: {result}".encode())
            return

        status = result["status"]
        resp_headers = result.get("headers", {})
        resp_body = base64.b64decode(result.get("body", "")) if result.get("body") else b""

        for k in list(resp_headers):
            if k.lower() in ("transfer-encoding", "connection", "keep-alive",
                             "content-length"):
                del resp_headers[k]
        resp_headers["Content-Security-Policy"] = _CODE_SERVER_CSP
        resp_headers["Content-Length"] = str(len(resp_body))

        pending_req.complete(status, resp_headers, resp_body)
    except Exception as e:
        logger.warning("Code proxy HTTP error for %s: %s", relay_id, e)
        pending_req.complete(502, {"Content-Type": "application/json"},
                             json.dumps({"error": str(e)}).encode())


# —— WebSocket proxy handler ——

def code_ws_proxy(client_sock, path_params: dict, meta: dict):
    """WS handler for /code/{session_id}/{token}/{path+}.

    Tunnels browser WS <-> relay <-> code-server WS. The capability
    token in the path binds the requester to this code-server
    session; cross-user access is rejected before any tunnel is
    opened.
    """
    session_id = path_params.get("session_id", "")
    token = path_params.get("token", "")
    from core.capability_routes import verify_route_ws
    claims, err = verify_route_ws(
        meta or {}, "code_server", session_id, token,
        allow_bearer_only=True)
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
        sess = _sessions.get(session_id)
    if not sess:
        _ws_close(client_sock, 4001, "Code server session not found")
        return
    relay_id = sess["relay_id"]

    sub_path = path_params.get("path", "")
    base_path = sess.get("base_path") or f"/code/{session_id}/{token}/"
    proxied_path = _upstream_path(sess, base_path, sub_path,
                                  meta.get("query", ""))

    relay_service = sess["relay_service"]
    port = sess["port"]

    ws_session_id = uuid.uuid4().hex[:12]

    fwd_headers = {}
    for k, v in meta.get("headers", {}).items():
        kl = k.lower()
        if kl in ("sec-websocket-key", "sec-websocket-accept",
                  "sec-websocket-extensions", "upgrade", "connection"):
            continue
        fwd_headers[k] = v
    fwd_headers["Host"] = f"127.0.0.1:{port}"
    fwd_headers["Origin"] = f"http://127.0.0.1:{port}"

    # Register browser socket BEFORE opening relay WS. _sessions is keyed
    # by code-server session_id, while _relay_to_session maps relay_id ->
    # session_id for backend dispatch.
    with _lock:
        if session_id in _sessions:
            _sessions[session_id]["cs_ws_sessions"][ws_session_id] = {
                "browser_sock": client_sock,
            }

    logger.debug("Code WS proxy: opening relay WS session=%s path=%s", ws_session_id, proxied_path)

    try:
        deadline = time.time() + 8.0
        result = None
        while True:
            result = relay_service._request(
                "cs_ws_open",
                session_id=ws_session_id,
                port=port,
                ws_path=proxied_path,
                headers=fwd_headers,
            )
            if isinstance(result, dict) and result.get("ok"):
                break
            if not _proxy_connection_refused(result) or time.time() >= deadline:
                break
            time.sleep(0.2)
        if not isinstance(result, dict) or not result.get("ok"):
            err = result.get("error", "Unknown") if isinstance(result, dict) else str(result)
            with _lock:
                if session_id in _sessions:
                    _sessions[session_id]["cs_ws_sessions"].pop(ws_session_id, None)
            _ws_close(client_sock, 4002, f"Failed: {err}")
            return
    except Exception as e:
        logger.warning("Code WS proxy: cs_ws_open failed: %s", e)
        with _lock:
            if session_id in _sessions:
                _sessions[session_id]["cs_ws_sessions"].pop(ws_session_id, None)
        _ws_close(client_sock, 4002, f"Failed: {e}")
        return

    logger.debug("Code WS proxy: relay=%s session=%s connected", relay_id, ws_session_id)

    try:
        while True:
            received = _ws_recv(client_sock)
            if len(received) == 3:
                opcode, payload, frame = received
            else:
                opcode, payload = received
                frame = _ws_build_frame(payload, opcode=opcode)
            if opcode == 0x08:  # close
                _send_cs_ws_command_to_relay(relay_service, {
                    "action": "cs_ws_send",
                    "session_id": ws_session_id,
                    "frame": base64.b64encode(frame).decode("ascii"),
                    "opcode": opcode,
                })
                break
            if opcode == 0x09:  # ping
                # Browser-to-code-server frames are already masked exactly as
                # the upstream expects. Preserve them instead of rebuilding.
                pass

            _send_cs_ws_command_to_relay(relay_service, {
                "action": "cs_ws_send",
                "session_id": ws_session_id,
                "frame": base64.b64encode(frame).decode("ascii"),
                "data": base64.b64encode(payload).decode("ascii"),
                "opcode": opcode,
            })
    except Exception as e:
        logger.debug("Code WS proxy read loop ended: %s (session=%s)", e, ws_session_id)
    finally:
        try:
            _send_cs_ws_command_to_relay(relay_service, {
                "action": "cs_ws_close",
                "session_id": ws_session_id,
            })
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

        with _lock:
            if session_id in _sessions:
                _sessions[session_id]["cs_ws_sessions"].pop(ws_session_id, None)
        try:
            client_sock.close()
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        logger.debug("Code WS proxy: session=%s disconnected", ws_session_id)


def dispatch_cs_ws_data(relay_id: str, ws_session_id: str, data_b64: str, opcode: int = 1):
    """Called when relay sends cs_ws_data — forward to browser."""
    with _lock:
        session_id = _relay_to_session.get(relay_id, "")
        sess = _sessions.get(session_id) if session_id else None
    if not sess:
        logger.debug("cs_ws_data: no session for relay %s", relay_id)
        return
    ws_sess = sess["cs_ws_sessions"].get(ws_session_id)
    if not ws_sess or not ws_sess.get("browser_sock"):
        logger.debug("cs_ws_data: no browser sock for ws_session %s", ws_session_id)
        return
    try:
        frame_b64 = data_b64 if opcode == -1 else ""
        if frame_b64:
            ws_sess["browser_sock"].sendall(base64.b64decode(frame_b64))
        else:
            payload = base64.b64decode(data_b64)
            _ws_send(ws_sess["browser_sock"], payload, opcode=opcode)
    except Exception as e:
        logger.warning("cs_ws_data: send error: %s", e)


def dispatch_cs_ws_close(relay_id: str, ws_session_id: str):
    """Called when relay's code-server WS closes."""
    with _lock:
        session_id = _relay_to_session.get(relay_id, "")
        sess = _sessions.get(session_id) if session_id else None
    if not sess:
        return
    ws_sess = sess["cs_ws_sessions"].pop(ws_session_id, None)
    if ws_sess and ws_sess.get("browser_sock"):
        try:
            _ws_close(ws_sess["browser_sock"], 1000, "Backend closed")
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)


def _send_command_to_relay(relay_service, cmd: dict):
    """Send a command to the relay via the command pipeline (fire-and-forget)."""
    request_id = uuid.uuid4().hex[:8]
    _send_relay_message(relay_service, {"type": "command", "request_id": request_id, **cmd})


def _send_cs_ws_command_to_relay(relay_service, cmd: dict):
    """Send code-server WS traffic without generating per-frame results."""
    _send_relay_message(relay_service, {"type": "cs_ws_command", **cmd})


def _send_relay_message(relay_service, msg: dict):
    """Send a JSON message to the relay over its command WebSocket."""
    import asyncio
    from services.filesystem_service import _ws_send_frame

    with relay_service._relay_pool_lock:
        pool = relay_service._relay_pool[:]
    if not pool:
        return

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
        logger.warning("Code proxy command send error: %s", last_err)


# —— WS frame helpers ——

def _ws_send(sock, data: bytes, opcode=0x01):
    sock.sendall(_ws_build_frame(data, opcode=opcode))


def _ws_build_frame(data: bytes, opcode=0x01) -> bytes:
    length = len(data)
    frame = bytes([0x80 | opcode])
    if length < 126:
        frame += bytes([length])
    elif length < 65536:
        frame += bytes([126]) + struct.pack("!H", length)
    else:
        frame += bytes([127]) + struct.pack("!Q", length)
    frame += data
    return frame


def _ws_recv(sock):
    return _ws_recv_frame(sock)


def _ws_recv_frame(sock):
    def _recv_exact(n):
        data = b""
        while len(data) < n:
            chunk = sock.recv(n - len(data))
            if not chunk:
                raise ConnectionError("WS connection closed (0 bytes)")
            data += chunk
        return data

    parts = []
    hdr = _recv_exact(2)
    parts.append(hdr)
    opcode = hdr[0] & 0x0F
    masked = bool(hdr[1] & 0x80)
    length = hdr[1] & 0x7F
    if length == 126:
        ext = _recv_exact(2)
        parts.append(ext)
        length = struct.unpack("!H", ext)[0]
    elif length == 127:
        ext = _recv_exact(8)
        parts.append(ext)
        length = struct.unpack("!Q", ext)[0]
    if masked:
        mask = _recv_exact(4)
        parts.append(mask)
        masked_payload = _recv_exact(length)
        parts.append(masked_payload)
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(masked_payload))
    else:
        payload = _recv_exact(length)
        parts.append(payload)
    return opcode, payload, b"".join(parts)


def _ws_close(sock, code=1000, reason=""):
    try:
        payload = struct.pack("!H", code) + reason.encode("utf-8")[:123]
        frame = bytes([0x88, len(payload)]) + payload
        sock.sendall(frame)
        sock.close()
    except Exception:
        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
