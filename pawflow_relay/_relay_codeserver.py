"""code-server lifecycle + WebSocket tunnel for the relay worker.

Extracted from `pawflow_relay/worker.py`'s `_ws_connect` closure. As with
the terminal manager, the socket output path is inverted: the backend
WS reader forwards frames through an injected ``send_frame(bytes)``
callback instead of writing to the relay socket directly. The worker
passes a callback that takes the send lock and writes to the live relay
socket (`ws_sock_ref[0]`); tests pass a capturing callback.

The long-lived state (code-server process handle/port/base path and the
open backend WS sessions) is owned by the caller's ``RelayWorkerState``
and passed in, so the per-connection lifecycle is unchanged by the
extraction — only the code moved out of the closure.

Frame shapes and the worker-protocol action contract are unchanged:
  {"type": "cs_ws_data",  "session_id": <sid>, "frame": <b64>, "data": <b64>, "opcode": -1, "fin": <bool>}
  {"type": "cs_ws_close", "session_id": <sid>}
"""
import base64
import json
import logging
import os
import socket
import subprocess  # nosec B404
import sys
import tempfile
import threading
import time
from pathlib import Path

from pawflow_relay._relay_ws_proto import encode_masked_frame, read_ws_frame

_log = logging.getLogger(__name__)


def start_code_server(state, msg, root_dir):
    """Start (or reuse) the code-server process. Returns a result dict.

    The public base path is passed to code-server only via
    ``--abs-proxy-base-path`` (stripped) — never as a positional arg — and
    the server runs with an isolated user-data/extensions dir and updates
    disabled.
    """
    import http.client
    _public_base_path = msg.get("base_path", "")
    _upstream_base_path = "/"
    _abs_proxy_base_path = _public_base_path.rstrip("/")
    if state.code_server_proc:
        p = state.code_server_proc
        if p.poll() is None:
            _running_base = state.code_server_base_path or ""
            if _running_base == _public_base_path:
                return {"ok": True, "data": {"port": state.code_server_port, "already_running": True}}
            p.terminate()
    _cs_port = msg.get("port", 0)
    if not _cs_port:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as _s:
            _s.bind(("", 0))
            _cs_port = _s.getsockname()[1]
    _cs_tmp_dir = Path(tempfile.gettempdir())
    _cs_user_data = _cs_tmp_dir / "pawflow-code-server-data"
    _cs_extensions = _cs_tmp_dir / "pawflow-code-server-extensions"
    _cs_settings = _cs_user_data / "User" / "settings.json"
    _cs_settings.parent.mkdir(parents=True, exist_ok=True)
    _cs_extensions.mkdir(parents=True, exist_ok=True)
    _cs_settings.write_text(json.dumps({
        "extensions.autoCheckUpdates": False,
        "extensions.autoUpdate": False,
        "extensions.ignoreRecommendations": True,
        "telemetry.telemetryLevel": "off",
        "workbench.enableExperiments": False,
        "github.gitAuthentication": False,
    }), encoding="utf-8")
    _cs_env = dict(os.environ)
    _cs_env["EXTENSIONS_GALLERY"] = "{}"
    _cs_env["VSCODE_DISABLE_TELEMETRY"] = "1"
    _cs_args = [
        "code-server",
        "--bind-addr", f"0.0.0.0:{_cs_port}",
        "--auth", "none",
        "--disable-telemetry",
        "--disable-workspace-trust",
        "--abs-proxy-base-path", _abs_proxy_base_path,
        "--user-data-dir", str(_cs_user_data),
        "--extensions-dir", str(_cs_extensions),
        str(Path(root_dir).resolve()),
    ]
    try:
        _cs_log = open("/tmp/code-server.log", "w")  # nosec B108 - relay-local service log.
        _cs_proc = subprocess.Popen(  # nosec B603
            _cs_args, stdout=_cs_log, stderr=_cs_log, env=_cs_env)
        _ready_path = _upstream_base_path
        _deadline = time.time() + 10
        _ready = False
        _last_err = ""
        while time.time() < _deadline:
            _rc = _cs_proc.poll()
            if _rc is not None:
                try:
                    _cs_log.flush()
                except Exception:
                    _log.debug("Ignored exception", exc_info=True)
                _tail = ""
                try:
                    with open(_cs_log.name, "r", encoding="utf-8", errors="replace") as _lf:
                        _tail = _lf.read()[-1200:]
                except Exception:
                    _log.debug("Ignored exception", exc_info=True)
                return {"ok": False, "error": f"code-server exited with status {_rc}: {_tail}"}
            try:
                _conn = http.client.HTTPConnection("127.0.0.1", _cs_port, timeout=0.5)
                _conn.request("GET", _ready_path)
                _resp = _conn.getresponse()
                _resp.read(1024)
                _conn.close()
                if _resp.status < 500:
                    _ready = True
                    break
            except Exception as _e:
                _last_err = str(_e)
            time.sleep(0.2)
        if not _ready:
            try:
                _cs_proc.terminate()
            except Exception:
                _log.debug("Ignored exception", exc_info=True)
            return {"ok": False, "error": f"code-server did not become ready on port {_cs_port}: {_last_err}"}
        state.code_server_proc = _cs_proc
        state.code_server_port = _cs_port
        state.code_server_base_path = _public_base_path
        sys.stderr.write(f"[FSRelay] code-server started on port {_cs_port} public_base_path={_public_base_path} upstream_base_path={_upstream_base_path}\n")
        return {"ok": True, "data": {"port": _cs_port, "pid": _cs_proc.pid, "upstream_base_path": _upstream_base_path}}
    except FileNotFoundError:
        return {"ok": False, "error": "code-server not installed"}
    except Exception as e:
        return {"ok": False, "error": f"Failed to start code-server: {e}"}


def cs_ws_open(state, msg, send_frame):
    """Open a backend WS to code-server and stream its frames via send_frame.

    Browser/proxy headers (Cookie, X-Forwarded-*, Origin) are deliberately
    NOT forwarded — code-server validates Origin and 403s proxied origins.
    """
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
        # The browser connects to PawFlow, not directly to code-server.
        # Do not forward browser/proxy headers such as Cookie,
        # X-Forwarded-*, or Origin: code-server validates Origin and
        # rejects proxied HTTPS origins with HTTP 403.
        _ws_protocol = (_ws_headers.get("Sec-WebSocket-Protocol")
                        or _ws_headers.get("sec-websocket-protocol"))
        if _ws_protocol:
            _hdr_lines.append(f"Sec-WebSocket-Protocol: {_ws_protocol}")
        _handshake = "\r\n".join(_hdr_lines) + "\r\n\r\n"
        sys.stderr.write(f"[FSRelay] cs_ws_open connecting to 127.0.0.1:{_ws_port} path={_ws_path[:80]}\n")
        _cs_sock = socket.create_connection(("127.0.0.1", _ws_port), timeout=10)
        _cs_sock.sendall(_handshake.encode())
        _resp = b""
        while b"\r\n\r\n" not in _resp:
            _chunk = _cs_sock.recv(4096)
            if not _chunk:
                raise ConnectionError("WS handshake failed")
            _resp += _chunk
        _status_line = _resp.split(b"\r\n")[0]
        if b"101" not in _status_line:
            sys.stderr.write(f"[FSRelay] cs_ws_open handshake rejected: {_resp[:500]}\n")
            _cs_sock.close()
            return {"ok": False, "error": f"WS handshake rejected: {_status_line.decode(errors='replace')}"}
        # Reader thread: code-server WS -> relay WS -> server -> browser
        state.cs_ws_sessions[_ws_sid] = {"sock": _cs_sock}

        def _forward_cs_ws_frame(_sid, _raw_frame, _op, _payload, _fin=True):
            sys.stderr.write(f"[FSRelay] cs_ws_data: sid={_sid} op={_op} len={len(_payload)}\n")
            _fwd = json.dumps({
                "type": "cs_ws_data",
                "session_id": _sid,
                "frame": base64.b64encode(_raw_frame).decode("ascii"),
                "data": base64.b64encode(_payload).decode("ascii"),
                "opcode": -1,
                "fin": _fin,
            })
            send_frame(_fwd.encode("utf-8"))
            sys.stderr.write("[FSRelay] cs_ws_data sent ok\n")

        def _cs_ws_reader(_sock, _sid):
            sys.stderr.write(f"[FSRelay] cs_ws_reader started for {_sid}\n")
            try:
                while True:
                    _frame = read_ws_frame(_sock)
                    if _frame is None:
                        break
                    # code-server tunnel forwards the raw on-wire frame
                    # (and the close frame too) up to the browser.
                    _forward_cs_ws_frame(
                        _sid, _frame.raw, _frame.op, _frame.payload, _frame.fin)
                    if _frame.op == 0x08:  # close
                        break
            except Exception:
                _log.debug("Ignored exception", exc_info=True)
            finally:
                try:
                    _sock.close()
                except Exception:
                    _log.debug("Ignored exception", exc_info=True)
                state.cs_ws_sessions.pop(_sid, None)
                try:
                    send_frame(json.dumps({"type": "cs_ws_close", "session_id": _sid}).encode("utf-8"))
                except Exception:
                    _log.debug("Ignored exception", exc_info=True)

        # Forward any leftover data after handshake
        _hdr_end = _resp.index(b"\r\n\r\n") + 4
        _leftover = _resp[_hdr_end:]
        if _leftover:
            _forward_cs_ws_frame(_ws_sid, _leftover, _leftover[0] & 0x0F, b"", bool(_leftover[0] & 0x80))
        _t = threading.Thread(target=_cs_ws_reader, args=(_cs_sock, _ws_sid), daemon=True)
        _t.start()
        state.cs_ws_sessions[_ws_sid]["reader"] = _t
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": f"cs_ws_open error: {e}"}


def cs_ws_send(state, msg):
    """Send a frame (raw passthrough or masked-from-data) to a backend WS."""
    _ws_sid = msg.get("session_id", "")
    _ws_data = msg.get("data", "")
    _ws_op = msg.get("opcode", 1)
    _ws_sess = state.cs_ws_sessions.get(_ws_sid)
    if not _ws_sess:
        return {"ok": False, "error": f"WS session not found: {_ws_sid}"}
    try:
        _ws_frame = msg.get("frame", "")
        if _ws_frame:
            _frame = base64.b64decode(_ws_frame)
            sys.stderr.write(f"[FSRelay] cs_ws_send frame: sid={_ws_sid} len={len(_frame)}\n")
            _ws_sess["sock"].sendall(_frame)
            return {"ok": True}
        _raw = base64.b64decode(_ws_data)
        sys.stderr.write(f"[FSRelay] cs_ws_send: sid={_ws_sid} op={_ws_op} len={len(_raw)}\n")
        # Build WS frame (masked, client->server)
        _frame = encode_masked_frame(_ws_op, _raw)
        _ws_sess["sock"].sendall(_frame)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def cs_ws_close(state, msg):
    """Close one backend WS session."""
    _ws_sid = msg.get("session_id", "")
    _ws_sess = state.cs_ws_sessions.pop(_ws_sid, None)
    if _ws_sess and _ws_sess.get("sock"):
        try:
            _ws_sess["sock"].close()
        except Exception:
            _log.debug("Ignored exception", exc_info=True)
    return {"ok": True}


def stop_code_server(state):
    """Terminate the code-server process if running."""
    if state.code_server_proc:
        p = state.code_server_proc
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        state.code_server_proc = None
        state.code_server_port = None
        sys.stderr.write("[FSRelay] code-server stopped\n")
        return {"ok": True}
    return {"ok": True, "data": {"was_running": False}}
