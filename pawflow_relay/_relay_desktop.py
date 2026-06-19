"""Desktop (VNC/noVNC) WebSocket tunnel for the relay worker.

Extracted from `pawflow_relay/worker.py`'s `_ws_connect` closure (the
`desktop_ws_*` actions). The desktop lifecycle actions (start/stop/status
of the containerized X11+VNC stack and the host-screen local desktop)
still live in worker.py and will move here in a follow-up step.

Like the code-server tunnel, the backend reader forwards frames through
an injected ``send_frame(bytes)`` callback rather than writing to the
relay socket directly. State (the open backend WS sessions) lives in the
caller's ``RelayWorkerState`` and is passed in, so the per-connection
lifecycle is unchanged.

Differences from the code-server tunnel, preserved verbatim:
  - Browser/proxy headers ARE forwarded to the VNC backend handshake
    (minus the hop-by-hop WS handshake headers); code-server strips them.
  - The reader answers WS pings (0x09) with pongs (0x0A) locally.
  - Forwarded frames carry the real opcode (not -1) and only the
    unmasked payload (no raw frame).
  - cs uses opcode 1 (text) for sends; desktop defaults to 2 (binary/VNC).

Frame shapes (unchanged):
  {"type": "desktop_ws_data",  "session_id": <sid>, "data": <b64>, "opcode": <op>}
  {"type": "desktop_ws_close", "session_id": <sid>}
"""
import base64
import json
import logging
import os
import socket
import struct
import sys
import threading

_log = logging.getLogger(__name__)


def desktop_ws_open(state, msg, send_frame):
    """Open a backend WS to the VNC/noVNC server and stream frames out."""
    _ws_sid = msg.get("session_id", "")
    _ws_port = msg.get("port", 0)
    _ws_path = msg.get("ws_path", "/")
    _ws_headers = msg.get("headers", {})
    if not _ws_sid or not _ws_port:
        return {"ok": False, "error": "Missing session_id or port"}
    try:
        _ws_key = base64.b64encode(os.urandom(16)).decode()
        _hdr_lines = [
            f"GET {_ws_path} HTTP/1.1",
            f"Host: 127.0.0.1:{_ws_port}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {_ws_key}",
            "Sec-WebSocket-Version: 13",
        ]
        for _hk, _hv in _ws_headers.items():
            _hkl = _hk.lower()
            if _hkl not in ("host", "upgrade", "connection",
                            "sec-websocket-key", "sec-websocket-version"):
                _hdr_lines.append(f"{_hk}: {_hv}")
        _handshake = "\r\n".join(_hdr_lines) + "\r\n\r\n"
        sys.stderr.write(f"[FSRelay] desktop_ws_open connecting to 127.0.0.1:{_ws_port} path={_ws_path[:80]}\n")
        _vnc_sock = socket.create_connection(("127.0.0.1", _ws_port), timeout=10)
        _vnc_sock.sendall(_handshake.encode())
        _resp = b""
        while b"\r\n\r\n" not in _resp:
            _chunk = _vnc_sock.recv(4096)
            if not _chunk:
                raise ConnectionError("WS handshake failed")
            _resp += _chunk
        _status_line = _resp.split(b"\r\n")[0]
        if b"101" not in _status_line:
            sys.stderr.write(f"[FSRelay] desktop_ws_open handshake rejected: {_resp[:500]}\n")
            _vnc_sock.close()
            return {"ok": False, "error": f"WS handshake rejected: {_status_line.decode(errors='replace')}"}
        state.desktop_ws_sessions[_ws_sid] = {"sock": _vnc_sock}

        def _desktop_ws_reader(_sock, _sid):
            sys.stderr.write(f"[FSRelay] desktop_ws_reader started for {_sid}\n")
            try:
                while True:
                    _hdr2 = b""
                    while len(_hdr2) < 2:
                        _c = _sock.recv(2 - len(_hdr2))
                        if not _c:
                            break
                        _hdr2 += _c
                    if len(_hdr2) < 2:
                        break
                    _op = _hdr2[0] & 0x0F
                    _masked = bool(_hdr2[1] & 0x80)
                    _plen = _hdr2[1] & 0x7F
                    if _plen == 126:
                        _lb = b""
                        while len(_lb) < 2:
                            _c = _sock.recv(2 - len(_lb))
                            if not _c:
                                break
                            _lb += _c
                        _plen = struct.unpack("!H", _lb)[0]
                    elif _plen == 127:
                        _lb = b""
                        while len(_lb) < 8:
                            _c = _sock.recv(8 - len(_lb))
                            if not _c:
                                break
                            _lb += _c
                        _plen = struct.unpack("!Q", _lb)[0]
                    if _masked:
                        _mask = b""
                        while len(_mask) < 4:
                            _c = _sock.recv(4 - len(_mask))
                            if not _c:
                                break
                            _mask += _c
                    _payload = b""
                    while len(_payload) < _plen:
                        _c = _sock.recv(min(65536, _plen - len(_payload)))
                        if not _c:
                            break
                        _payload += _c
                    if _masked:
                        _payload = bytes(b ^ _mask[i % 4] for i, b in enumerate(_payload))
                    if _op == 0x08:
                        break
                    if _op == 0x09:
                        _pong = bytes([0x80 | 0x0A])
                        if len(_payload) < 126:
                            _pong += bytes([len(_payload)])
                        _pong += _payload
                        try:
                            _sock.sendall(_pong)
                        except Exception:
                            break
                        continue
                    _fwd = json.dumps({
                        "type": "desktop_ws_data",
                        "session_id": _sid,
                        "data": base64.b64encode(_payload).decode("ascii"),
                        "opcode": _op,
                    })
                    send_frame(_fwd.encode("utf-8"))
            except Exception:
                _log.debug("Ignored exception", exc_info=True)
            finally:
                try:
                    _sock.close()
                except Exception:
                    _log.debug("Ignored exception", exc_info=True)
                state.desktop_ws_sessions.pop(_sid, None)
                try:
                    send_frame(json.dumps({"type": "desktop_ws_close", "session_id": _sid}).encode("utf-8"))
                except Exception:
                    _log.debug("Ignored exception", exc_info=True)

        _t = threading.Thread(target=_desktop_ws_reader, args=(_vnc_sock, _ws_sid), daemon=True)
        _t.start()
        state.desktop_ws_sessions[_ws_sid]["reader"] = _t
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": f"desktop_ws_open error: {e}"}


def desktop_ws_send(state, msg):
    """Send a masked frame (binary by default) to a backend VNC WS."""
    _ws_sid = msg.get("session_id", "")
    _ws_data = msg.get("data", "")
    _ws_op = msg.get("opcode", 2)  # binary by default for VNC
    _ws_sess = state.desktop_ws_sessions.get(_ws_sid)
    if not _ws_sess:
        return {"ok": False, "error": f"Desktop WS session not found: {_ws_sid}"}
    try:
        _raw = base64.b64decode(_ws_data)
        _frame = bytes([0x80 | _ws_op])
        if len(_raw) < 126:
            _frame += bytes([0x80 | len(_raw)])
        elif len(_raw) < 65536:
            _frame += bytes([0x80 | 126]) + struct.pack("!H", len(_raw))
        else:
            _frame += bytes([0x80 | 127]) + struct.pack("!Q", len(_raw))
        _frame += b"\x00\x00\x00\x00" + _raw
        _ws_sess["sock"].sendall(_frame)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def desktop_ws_close(state, msg):
    """Close one backend VNC WS session."""
    _ws_sid = msg.get("session_id", "")
    _ws_sess = state.desktop_ws_sessions.pop(_ws_sid, None)
    if _ws_sess and _ws_sess.get("sock"):
        try:
            _ws_sess["sock"].close()
        except Exception:
            _log.debug("Ignored exception", exc_info=True)
    return {"ok": True}
