"""WS connection + upgrade handshake for the relay worker.

Extracted verbatim from _ws_connect's reconnect loop: opens the TCP socket
(with keepalive), wraps it in TLS for wss, performs the WebSocket upgrade
handshake (forwarding gateway cookie / session token / internal token /
gateway key headers the same way), validates the 101 response, and rebuffers
any bytes that arrived after the handshake via a recv wrapper. Returns the
ready (possibly TLS-wrapped) socket.
"""
import base64
import logging
import os
import socket
import ssl

_log = logging.getLogger(__name__)


def connect_and_handshake(host, port, path, use_ssl, gateway_cookie,
                          session_token, gateway_key):
    sock = socket.create_connection((host, port), timeout=10)
    # TCP keepalive: detect dead connections at OS level
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    try:
        # Linux: start probing after 30s idle, every 10s, fail after 3 misses
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 30)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
    except (AttributeError, OSError):
        pass  # not available on all platforms
    if use_ssl:
        ctx = ssl.create_default_context()
        if os.environ.get('PAWFLOW_RELAY_INSECURE') == '1':
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        sock = ctx.wrap_socket(sock, server_hostname=host)

    ws_key = base64.b64encode(os.urandom(16)).decode()
    _cookies = []
    if gateway_cookie:
        _cookies.append(f'_pf_gw={gateway_cookie}')
    if session_token:
        _cookies.append(f'pawflow_token={session_token}')
    internal_token = os.environ.get('PAWFLOW_INTERNAL_TOKEN', '')
    if internal_token:
        _cookies.append(f'pawflow_internal={internal_token}')
    _extra_hdrs = ''
    if _cookies:
        _extra_hdrs = 'Cookie: ' + '; '.join(_cookies) + '\r\n'
    if gateway_key:
        _extra_hdrs += f'X-PawFlow-Gateway-Key: {gateway_key}\r\n'
    handshake = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {ws_key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n"
        f"{_extra_hdrs}"
        f"\r\n"
    )
    sock.sendall(handshake.encode())

    resp = b""
    while b"\r\n\r\n" not in resp:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("Handshake failed")
        resp += chunk

    if b"101" not in resp.split(b"\r\n")[0]:
        _status_line = resp.split(b"\r\n")[0]
        raise ConnectionError(f"Handshake failed: {_status_line}")

    # Any bytes after \r\n\r\n are the start of the first WS frame
    # — push them back into the socket buffer via a wrapper
    _header_end = resp.index(b"\r\n\r\n") + 4
    _leftover = resp[_header_end:]
    if _leftover:
        _orig_recv = sock.recv
        _buf = [_leftover]
        def _patched_recv(n, _flags=0):
            if _buf:
                data = _buf[0]
                if len(data) <= n:
                    _buf.pop(0)
                    return data
                # Caller asked for fewer bytes than buffered: hand back
                # the first n and keep the remainder for the next recv.
                # Without this the tail (data[n:]) was silently dropped,
                # corrupting the first WS frame whenever the server
                # coalesced the 101 response with >n frame bytes.
                _buf[0] = data[n:]
                return data[:n]
            return _orig_recv(n)
        sock.recv = _patched_recv
    return sock
