"""PawFlow relay — worker-side HTTP/WS protocol, action dispatch, FSRelayHandler.

This module is the body of the relay worker. It runs either natively on
the user's host or inside the relay Docker container; in both cases
`pawflow_relay/` is on the Python path (mounted alongside the launcher
script in the container; importable via the source tree on the host).

Public entry points:
    _ws_connect(url, token, secret, relay_id, root_dir, readonly, ...)
    _make_handler_class(root_dir, secret, readonly, ...)
    FSRelayHandler
    _is_allowed_tmp_path(path)
    _WRITE_ACTIONS

Stdlib-only plus the in-tools sibling modules fs_common / fs_actions /
fs_exec / fs_screen / fs_mcp / fs_http, which are imported lazily where
needed so this module can be introspected without pulling in the whole
world.
"""

import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from fs_common import (
    _docker_cmd, _get_host_ip, _translate_path, _to_host_path,
)
from pawflow_relay.auth import (
    find_claude_binary as _find_claude_binary,
    claude_auth_login as _claude_auth_login,
    forward_to_host_helper as _forward_to_host_helper,
)


_WRITE_ACTIONS = frozenset({
    "write_file", "delete_file", "mkdir", "find_replace", "edit", "exec",
})


# ── Path allowlist (outside root_dir) ─────────────────────────────
#
# The relay is a filesystem sandbox bounded by --dir (root_dir). But
# a few absolute path prefixes are safe to allow through even when
# they don't live under the root: system temp dirs. They are local to
# the container/process, opaque to the host, and blocking them just
# breaks legitimate ephemeral writes (test fixtures, scratch files,
# commit message files, editor swap files).

def _tmp_allowlist():
    import tempfile
    dirs = ["/tmp", "/var/tmp"]
    try:
        dirs.append(tempfile.gettempdir())
    except Exception:
        pass
    # Resolve + dedup
    resolved = []
    seen = set()
    for d in dirs:
        try:
            rd = str(Path(d).resolve())
        except Exception:
            continue
        if rd not in seen:
            seen.add(rd)
            resolved.append(rd)
    return resolved


_TMP_ALLOWLIST = _tmp_allowlist()


# ── In-flight subprocess registry ───────────────────────────────────
# Lives in `pawflow_relay.proc_registry` to avoid a circular import
# with the action handlers (fs_actions/fs_exec/...). Re-exported here
# so call sites that already import from `worker` don't have to change.
from pawflow_relay.proc_registry import (
    register_inflight_proc,
    unregister_inflight_proc,
    kill_inflight_proc,
)


def _is_allowed_tmp_path(path: str) -> bool:
    """True when `path` is absolute and falls under a system temp dir."""
    if not path or not isinstance(path, str):
        return False
    p = Path(path)
    if not p.is_absolute():
        return False
    try:
        resolved = p.resolve()
    except Exception:
        return False
    for allowed in _TMP_ALLOWLIST:
        try:
            resolved.relative_to(allowed)
            return True
        except ValueError:
            continue
    return False


# ── Request handler ───────────────────────────────────────────────

class FSRelayHandler(BaseHTTPRequestHandler):
    """HTTP POST handler for filesystem relay operations."""

    server_version = "PawFlow-FSRelay/1.0"

    # Set by the factory function
    root_dir: str = "."
    secret: str = ""
    readonly: bool = False

    def log_message(self, fmt, *args):
        sys.stderr.write(f"[FSRelay] {self.address_string()} - {fmt % args}\n")

    def _log_op(self, action: str, path: str, ok: bool, detail: str = ""):
        tag = "OK" if ok else "FAIL"
        extra = f" | {detail}" if detail else ""
        sys.stderr.write(f"[FSRelay] [{tag}] {action} path={path}{extra}\n")

    def _send_json(self, ok: bool, data=None, error=None):
        resp = {"ok": ok}
        if ok and data is not None:
            resp["data"] = data
        elif not ok and error:
            resp["error"] = error
        body = json.dumps(resp, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _resolve_fs_url(self, value: str) -> str:
        """Resolve fs://relay_id/path → relative path."""
        if not value or not value.startswith("fs://"):
            return value
        # fs://any_relay_id/some/path → some/path
        import re as _re_fs
        m = _re_fs.match(r'fs://[^/]+/(.*)', value)
        return m.group(1) if m else value

    def _resolve(self, rel_path: str):
        """Resolve relative path to absolute, checking traversal.

        Returns absolute path string or None if blocked.

        Absolute paths under an allowlisted system temp dir (/tmp,
        /var/tmp, tempfile.gettempdir()) are passed through unchanged —
        they are sandboxed to the container/process and never escape to
        host state, so blocking them just breaks legitimate "write to
        tmp" use cases (test artifacts, editor swap files, etc.).
        """
        rel_path = self._resolve_fs_url(rel_path)
        if _is_allowed_tmp_path(rel_path):
            return str(Path(rel_path).resolve())
        root = Path(self.root_dir).resolve()
        target = (root / rel_path).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            return None
        return str(target)

    # ── HTTP verbs ────────────────────────────────────────────────

    def do_GET(self):
        self._send_json(True, data={"service": "PawFlow-FSRelay", "version": "1.0"})

    def do_POST(self):
        # Read body
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            req = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            self._send_json(False, error=f"Invalid JSON: {e}")
            return

        # Validate secret
        if not hmac.compare_digest(req.get("secret", ""), self.secret):
            self._send_json(False, error="Invalid secret")
            return

        action = req.get("action", "")
        rel_path = self._resolve_fs_url(req.get("path", "."))
        # Also resolve fs:// in other path-like fields
        for _fk in ("source_path", "dest_path"):
            if _fk in req and isinstance(req[_fk], str):
                req[_fk] = self._resolve_fs_url(req[_fk])

        # Readonly check
        from fs_actions import WRITE_ACTIONS
        if self.readonly and action in WRITE_ACTIONS:
            self._log_op(action, rel_path, False, "readonly mode")
            self._send_json(False, error="Operation not allowed in readonly mode")
            return
        # Note: permission checks are enforced server-side by ToolApprovalGate.
        # The relay is a transport — it executes whatever the server sends.

        # Resolve path
        abs_path = self._resolve(rel_path)
        if abs_path is None:
            self._log_op(action, rel_path, False, "path traversal blocked")
            self._send_json(False, error=f"Path traversal blocked: {rel_path}")
            return

        # Dispatch via shared fs_actions module
        from fs_actions import ACTIONS as _FS_ACTIONS, WRITE_ACTIONS
        handler_fn = _FS_ACTIONS.get(action)
        if not handler_fn:
            self._log_op(action, rel_path, False, "unknown action")
            self._send_json(False, error=f"Unknown action: {action}")
            return

        try:
            if action == "exec":
                result = handler_fn(self.root_dir, abs_path, req,
                                     allow_exec=getattr(self, 'allow_exec', False))
            else:
                result = handler_fn(self.root_dir, abs_path, req)
            self._log_op(action, rel_path, True)
            self._send_json(True, data=result)
        except Exception as e:
            self._log_op(action, rel_path, False, str(e))
            self._send_json(False, error=str(e))


# Action implementations live in tools/fs_actions.py and are dispatched
# via `fs_actions.ACTIONS` (see the HTTP handler above and the WS handler
# further down). A duplicate set of `_action_*` handlers used to live
# here as a legacy fallback — removed as dead code.


# ── Claude auth login (host action) ──────────────────────────────



# ── Main ──────────────────────────────────────────────────────────

def _make_handler_class(root_dir: str, secret: str, readonly: bool,
                        allow_exec: bool = False, allow_automation: bool = False,
                        allow_local_screen: bool = False, allow_local: bool = False):
    """Create a handler class with bound config (avoids lambda issues)."""

    class ConfiguredHandler(FSRelayHandler):
        pass

    ConfiguredHandler.root_dir = root_dir
    ConfiguredHandler.secret = secret
    ConfiguredHandler.readonly = readonly
    ConfiguredHandler.allow_exec = allow_exec
    ConfiguredHandler.allow_automation = allow_automation
    ConfiguredHandler.allow_local_screen = allow_local_screen
    ConfiguredHandler.allow_local = allow_local
    return ConfiguredHandler


# ── WS Reverse client ─────────────────────────────────────────────

def _ws_connect(url, token, secret, relay_id, root_dir, readonly, allow_exec=False,
                allow_automation=False, allow_local_screen=False, allow_local=False,
                gateway_cookie="", session_token="", server_mount="",
                filestore_mount=""):
    """Connect to the PawFlow server via WebSocket and process filesystem commands.

    server_mount: if set, mount a FUSE proxy at this local path that
    forwards each syscall to the server's RelayServerFs handler over
    the same WS tunnel. Read-only in this phase. The path is bind-mounted
    by the operator into any docker container that needs to see the
    user's CLAUDE_SESSIONS_DIR slot.

    filestore_mount: if set, mount a second FUSE proxy at this local
    path that exposes the server FileStore as a virtualized hierarchy
    (/<file_id>/<filename>). Read-only — writes go through the
    HTTP/MCP FileStore APIs, not the FUSE mount.
    """
    import ssl
    import base64 as b64
    from urllib.parse import urlparse

    parsed = urlparse(url)
    use_ssl = parsed.scheme in ("wss", "https")
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if use_ssl else 80)
    path = parsed.path or "/ws/relay"

    mode = "read" if readonly else "readwrite"
    # Detect available shells for exec
    try:
        from fs_actions import detect_available_shells
        _shells = detect_available_shells()
    except Exception:
        _shells = {}
    def _is_containerized():
        return os.path.exists("/.dockerenv") or bool(os.environ.get("PAWFLOW_DOCKER_IMAGE"))

    # host_root: the original path on the user's machine (before Docker mount)
    # Always use forward slashes (Windows backslashes break JSON display)
    _host_root = os.environ.get("PAWFLOW_HOST_WORKDIR", "")
    if not _host_root and not _is_containerized():
        _host_root = root_dir
    _host_root = _host_root.replace("\\", "/")

    info = {
        "platform": sys.platform,
        "root": root_dir,
        "host_root": _host_root,
        "mode": mode,
        "shells": list(_shells.keys()),
        "containerized": _is_containerized(),
        "docker_image": os.environ.get("PAWFLOW_DOCKER_IMAGE", ""),
        "container_id": socket.gethostname() if _is_containerized() else "",
        "allow_exec": allow_exec,
        "allow_automation": allow_automation,
        "allow_local_screen": allow_local_screen,
        "allow_local": allow_local,
    }

    def _resolve(rel_path):
        if _is_allowed_tmp_path(rel_path):
            return str(Path(rel_path).resolve())
        root = Path(root_dir).resolve()
        target = (root / rel_path).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            return None
        return str(target)

    class MockHandler:
        pass
    MockHandler.root_dir = root_dir
    MockHandler.secret = secret
    MockHandler.readonly = readonly
    MockHandler.allow_exec = allow_exec
    MockHandler.allow_automation = allow_automation
    MockHandler.allow_local_screen = allow_local_screen
    MockHandler.allow_local = allow_local
    mock = MockHandler()

    from pawflow_relay.ws_frame import ws_send as _ws_frame_send, ws_recv as _ws_frame_recv

    def _execute_command(msg, on_output=None):
        action = msg.get("action", "")
        rel_path = msg.get("path", ".")

        # Token already validated at WS connect time — no per-command secret check

        if readonly and action in _WRITE_ACTIONS:
            return {"ok": False, "error": "Operation not allowed in readonly mode"}

        abs_path = _resolve(rel_path)
        if abs_path is None:
            return {"ok": False, "error": f"Path traversal blocked: {rel_path}"}

        # Host-level action: claude auth login
        # If in Docker → forward to host helper; if native → run directly
        if action == "claude_auth_login":
            host_helper = os.environ.get("PAWFLOW_HOST_HELPER", "")
            if host_helper:
                # Forward to host helper (CLI process on the host machine)
                return _forward_to_host_helper(host_helper, msg, ws_sock_ref[0], _ws_frame_send)
            else:
                # Native relay (no Docker) → run directly
                def _send_progress(data):
                    if ws_sock_ref[0]:
                        progress = json.dumps({
                            "type": "progress",
                            "request_id": msg.get("request_id", ""),
                            "data": data,
                        }).encode("utf-8")
                        try:
                            _ws_frame_send(ws_sock_ref[0], progress)
                        except Exception:
                            pass

                try:
                    result = _claude_auth_login(msg, send_progress=_send_progress)
                    if "error" in result:
                        return {"ok": False, "error": result["error"]}
                    return {"ok": True, "data": result}
                except Exception as e:
                    return {"ok": False, "error": str(e)}

        # Terminal actions (handled here, not in fs_actions)
        if action == "open_terminal":
            if not allow_exec:
                return {"ok": False, "error": "Exec not allowed"}
            try:
                _sid = _open_terminal(
                    cols=msg.get("cols", 80),
                    rows=msg.get("rows", 24),
                    shell=msg.get("shell"),
                )
                return {"ok": True, "data": {"session_id": _sid}}
            except Exception as e:
                return {"ok": False, "error": f"Failed to open terminal: {e}"}

        if action == "close_terminal":
            _sid = msg.get("session_id", "")
            if not _sid:
                return {"ok": False, "error": "Missing session_id"}
            if _sid.startswith("local_term_"):
                _hh = os.environ.get("PAWFLOW_HOST_HELPER", "")
                if _hh:
                    return _forward_to_host_helper(_hh, msg, ws_sock_ref[0], _ws_frame_send)
            ok = _close_terminal(_sid)
            return {"ok": ok, "error": "" if ok else "Session not found"}

        if action == "write_terminal":
            _sid = msg.get("session_id", "")
            if _sid.startswith("local_term_"):
                _hh = os.environ.get("PAWFLOW_HOST_HELPER", "")
                if _hh:
                    return _forward_to_host_helper(_hh, msg, ws_sock_ref[0], _ws_frame_send)
            _tsess = _terminal_sessions.get(_sid)
            if not _tsess:
                return {"ok": False, "error": f"Terminal session not found: {_sid}"}
            try:
                _raw = base64.b64decode(msg.get("data", ""))
                os.write(_tsess["master_fd"], _raw)
                return {"ok": True}
            except OSError as e:
                return {"ok": False, "error": str(e)}

        if action == "resize_terminal":
            _sid = msg.get("session_id", "")
            if _sid.startswith("local_term_"):
                _hh = os.environ.get("PAWFLOW_HOST_HELPER", "")
                if _hh:
                    return _forward_to_host_helper(_hh, msg, ws_sock_ref[0], _ws_frame_send)
            _tsess = _terminal_sessions.get(_sid)
            if not _tsess:
                return {"ok": False, "error": f"Terminal session not found: {_sid}"}
            try:
                import fcntl as _fcntl_rt
                import termios as _termios_rt
                import array as _array_rt
                _c = msg.get("cols", 80)
                _r = msg.get("rows", 24)
                _ws = _array_rt.array("H", [_r, _c, 0, 0])
                _fcntl_rt.ioctl(_tsess["master_fd"], _termios_rt.TIOCSWINSZ, _ws)
                return {"ok": True}
            except Exception as e:
                return {"ok": False, "error": str(e)}

        if action == "list_terminals":
            return {"ok": True, "data": {
                "sessions": [
                    {"session_id": sid, "shell": s["shell"]}
                    for sid, s in _terminal_sessions.items()
                ]
            }}

        if action == "http_proxy":
            if not allow_exec:
                return {"ok": False, "error": "Exec not allowed"}
            import http.client
            _target_port = msg.get("port", 0)
            _method = msg.get("method", "GET")
            _req_path = msg.get("req_path", "/")
            _req_headers = msg.get("req_headers", {})
            _req_body = msg.get("req_body", "")  # base64
            if not _target_port:
                return {"ok": False, "error": "Missing port"}
            try:
                conn = http.client.HTTPConnection("127.0.0.1", _target_port, timeout=30)
                _body_bytes = base64.b64decode(_req_body) if _req_body else None
                conn.request(_method, _req_path, body=_body_bytes, headers=_req_headers)
                resp = conn.getresponse()
                _resp_body = resp.read()
                _resp_headers = dict(resp.getheaders())
                conn.close()
                return {"ok": True, "data": {
                    "status": resp.status,
                    "reason": resp.reason,
                    "headers": _resp_headers,
                    "body": base64.b64encode(_resp_body).decode("ascii"),
                }}
            except Exception as e:
                return {"ok": False, "error": f"Proxy error: {e}"}

        if action == "start_code_server":
            if not allow_exec:
                return {"ok": False, "error": "Exec not allowed"}
            if hasattr(_execute_command, '_code_server_proc') and _execute_command._code_server_proc:
                p = _execute_command._code_server_proc
                if p.poll() is None:
                    return {"ok": True, "data": {"port": _execute_command._code_server_port, "already_running": True}}
            _cs_port = msg.get("port", 0)
            _base_path = msg.get("base_path", "")
            if not _cs_port:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as _s:
                    _s.bind(("", 0))
                    _cs_port = _s.getsockname()[1]
            _cs_args = [
                "code-server",
                "--bind-addr", f"0.0.0.0:{_cs_port}",
                "--auth", "none",
                "--disable-telemetry",
            ]
            _cs_args.append(root_dir)
            try:
                _cs_log = open("/tmp/code-server.log", "w")
                _cs_proc = subprocess.Popen(
                    _cs_args, stdout=_cs_log, stderr=_cs_log)
                _execute_command._code_server_proc = _cs_proc
                _execute_command._code_server_port = _cs_port
                sys.stderr.write(f"[FSRelay] code-server started on port {_cs_port} base_path={_base_path}\n")
                return {"ok": True, "data": {"port": _cs_port, "pid": _cs_proc.pid}}
            except FileNotFoundError:
                return {"ok": False, "error": "code-server not installed"}
            except Exception as e:
                return {"ok": False, "error": f"Failed to start code-server: {e}"}

        # -- Code-server WS tunnel --
        if action == "cs_ws_open":
            if not allow_exec:
                return {"ok": False, "error": "Exec not allowed"}
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
                if not hasattr(_execute_command, '_cs_ws_sessions'):
                    _execute_command._cs_ws_sessions = {}
                _execute_command._cs_ws_sessions[_ws_sid] = {"sock": _cs_sock}

                def _cs_ws_reader(_sock, _sid):
                    sys.stderr.write(f"[FSRelay] cs_ws_reader started for {_sid}\n")
                    try:
                        while True:
                            _data = b""
                            # Read WS frame header
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
                                    if not _c: break
                                    _lb += _c
                                _plen = struct.unpack("!H", _lb)[0]
                            elif _plen == 127:
                                _lb = b""
                                while len(_lb) < 8:
                                    _c = _sock.recv(8 - len(_lb))
                                    if not _c: break
                                    _lb += _c
                                _plen = struct.unpack("!Q", _lb)[0]
                            if _masked:
                                _mask = b""
                                while len(_mask) < 4:
                                    _c = _sock.recv(4 - len(_mask))
                                    if not _c: break
                                    _mask += _c
                            _payload = b""
                            while len(_payload) < _plen:
                                _c = _sock.recv(min(65536, _plen - len(_payload)))
                                if not _c: break
                                _payload += _c
                            if _masked:
                                _payload = bytes(b ^ _mask[i % 4] for i, b in enumerate(_payload))
                            if _op == 0x08:  # close
                                break
                            if _op == 0x09:  # ping -> pong
                                _pong = bytes([0x80 | 0x0A])
                                if len(_payload) < 126:
                                    _pong += bytes([len(_payload)])
                                _pong += _payload
                                try:
                                    _sock.sendall(_pong)
                                except Exception:
                                    break
                                continue
                            # Forward to server
                            sys.stderr.write(f"[FSRelay] cs_ws_data: sid={_sid} op={_op} len={len(_payload)}\n")
                            _fwd = json.dumps({
                                "type": "cs_ws_data",
                                "session_id": _sid,
                                "data": base64.b64encode(_payload).decode("ascii"),
                                "opcode": _op,
                            })
                            with _send_lock:
                                _ws_frame_send(ws_sock_ref[0], _fwd.encode("utf-8"))
                            sys.stderr.write(f"[FSRelay] cs_ws_data sent ok\n")
                    except Exception:
                        pass
                    finally:
                        try:
                            _sock.close()
                        except Exception:
                            pass
                        if hasattr(_execute_command, '_cs_ws_sessions'):
                            _execute_command._cs_ws_sessions.pop(_sid, None)
                        try:
                            with _send_lock:
                                _ws_frame_send(ws_sock_ref[0], json.dumps({"type": "cs_ws_close", "session_id": _sid}).encode("utf-8"))
                        except Exception:
                            pass

                _t = _threading.Thread(target=_cs_ws_reader, args=(_cs_sock, _ws_sid), daemon=True)
                _t.start()
                _execute_command._cs_ws_sessions[_ws_sid]["reader"] = _t
                # Forward any leftover data after handshake
                _hdr_end = _resp.index(b"\r\n\r\n") + 4
                _leftover = _resp[_hdr_end:]
                if _leftover:
                    pass  # Leftover bytes will be read by the reader thread
                return {"ok": True}
            except Exception as e:
                return {"ok": False, "error": f"cs_ws_open error: {e}"}

        if action == "cs_ws_send":
            _ws_sid = msg.get("session_id", "")
            _ws_data = msg.get("data", "")
            _ws_op = msg.get("opcode", 1)
            if not hasattr(_execute_command, '_cs_ws_sessions'):
                return {"ok": False, "error": "No WS sessions"}
            _ws_sess = _execute_command._cs_ws_sessions.get(_ws_sid)
            if not _ws_sess:
                return {"ok": False, "error": f"WS session not found: {_ws_sid}"}
            try:
                _raw = base64.b64decode(_ws_data)
                sys.stderr.write(f"[FSRelay] cs_ws_send: sid={_ws_sid} op={_ws_op} len={len(_raw)}\n")
                # Build WS frame (masked, client->server)
                _frame = bytes([0x80 | _ws_op])
                if len(_raw) < 126:
                    _frame += bytes([0x80 | len(_raw)])  # masked (client->server)
                elif len(_raw) < 65536:
                    _frame += bytes([0x80 | 126]) + struct.pack("!H", len(_raw))
                else:
                    _frame += bytes([0x80 | 127]) + struct.pack("!Q", len(_raw))
                # Mask with zeros (simplest valid mask)
                _frame += b"\x00\x00\x00\x00" + _raw
                _ws_sess["sock"].sendall(_frame)
                return {"ok": True}
            except Exception as e:
                return {"ok": False, "error": str(e)}

        if action == "cs_ws_close":
            _ws_sid = msg.get("session_id", "")
            if hasattr(_execute_command, '_cs_ws_sessions'):
                _ws_sess = _execute_command._cs_ws_sessions.pop(_ws_sid, None)
                if _ws_sess and _ws_sess.get("sock"):
                    try:
                        _ws_sess["sock"].close()
                    except Exception:
                        pass
            return {"ok": True}

        if action == "stop_code_server":
            if hasattr(_execute_command, '_code_server_proc') and _execute_command._code_server_proc:
                p = _execute_command._code_server_proc
                if p.poll() is None:
                    p.terminate()
                    try:
                        p.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        p.kill()
                _execute_command._code_server_proc = None
                _execute_command._code_server_port = None
                sys.stderr.write("[FSRelay] code-server stopped\n")
                return {"ok": True}
            return {"ok": True, "data": {"was_running": False}}

        # ── Forward local screen/desktop to host helper if in Docker ────
        _explicitly_local = action in (
            "start_local_desktop", "stop_local_desktop", "local_screen_check",
            "open_local_terminal", "start_local_code_server")
        # NOTE: write_terminal/resize_terminal/close_terminal for local_term_*
        # are forwarded inline in the terminal action handlers above.
        _screen_with_flag = action.startswith("screen_") and msg.get("local", False)
        _host_helper = os.environ.get("PAWFLOW_HOST_HELPER", "")
        if (_explicitly_local or _screen_with_flag) and _host_helper:
            _fwd = {k: v for k, v in msg.items() if k != "local"}
            return _forward_to_host_helper(_host_helper, _fwd, ws_sock_ref[0], _ws_frame_send)

        # ── Desktop VNC (singleton) ──────────────────────────────────────
        if action == "start_desktop":
            if not allow_exec:
                return {"ok": False, "error": "Exec not allowed"}
            # Idempotent: if already running, return existing info
            if hasattr(_execute_command, '_desktop_procs') and _execute_command._desktop_procs:
                _essential = getattr(_execute_command, '_desktop_essential_procs', None) or _execute_command._desktop_procs
                _alive = all(p.poll() is None for p in _essential)
                if _alive:
                    return {"ok": True, "data": {
                        "vnc_port": _execute_command._desktop_vnc_port,
                        "novnc_port": _execute_command._desktop_novnc_port,
                        "display": _execute_command._desktop_display,
                        "already_running": True
                    }}
                else:
                    for p in _execute_command._desktop_procs:
                        try: p.kill()
                        except: pass
                    _execute_command._desktop_procs = None

            _resolution = msg.get("resolution", "1280x800")
            _depth = msg.get("depth", 24)
            _display_num = msg.get("display", 99)
            _display = f":{_display_num}"
            _vnc_port = msg.get("vnc_port", 0)
            # Use fixed port from env (Docker published) or find a free one
            _novnc_port = int(os.environ.get("PAWFLOW_DESKTOP_NOVNC_PORT", 0)) or msg.get("novnc_port", 0)
            if not _vnc_port:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as _s:
                    _s.bind(("", 0)); _vnc_port = _s.getsockname()[1]
            if not _novnc_port:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as _s:
                    _s.bind(("", 0)); _novnc_port = _s.getsockname()[1]
            try:
                import time as _time_mod
                _log_d = open("/tmp/desktop.log", "w")
                _procs = []

                # Desktop runs as current user (pawflow via Dockerfile USER)
                _desktop_user = os.environ.get("USER", "pawflow")
                _desktop_home = os.environ.get("HOME", "/home/pawflow")

                _user_env = {
                    **os.environ,
                    "DISPLAY": _display,
                    "HOME": _desktop_home,
                    "USER": _desktop_user,
                    "DBUS_SESSION_BUS_ADDRESS": f"unix:path=/tmp/dbus-desktop",
                    "XDG_RUNTIME_DIR": f"/tmp/xdg-{_desktop_user}",
                }
                os.makedirs(_user_env["XDG_RUNTIME_DIR"], mode=0o700, exist_ok=True)

                # 1. Xvfb
                _p_xvfb = subprocess.Popen(
                    ["Xvfb", _display, "-screen", "0", f"{_resolution}x{_depth}",
                     "-ac", "+extension", "GLX", "+render", "-noreset"],
                    stdout=_log_d, stderr=_log_d)
                _procs.append(_p_xvfb)
                os.environ["DISPLAY"] = _display
                _time_mod.sleep(0.5)

                # 2. D-Bus session (needed by XFCE)
                _p_dbus = subprocess.Popen(
                    ["dbus-daemon", "--session", "--nofork",
                     f"--address=unix:path=/tmp/dbus-desktop"],
                    env=_user_env,
                    stdout=_log_d, stderr=_log_d)
                _procs.append(_p_dbus)
                _time_mod.sleep(0.3)

                # 3. PulseAudio (BEFORE XFCE — so desktop apps find PA already running)
                import shutil as _shutil
                _audio_port = 0
                if _shutil.which("pulseaudio"):
                    _pa_conf_dir = Path(_desktop_home) / ".config" / "pulse"
                    _pa_conf_dir.mkdir(parents=True, exist_ok=True)
                    (_pa_conf_dir / "daemon.conf").write_text(
                        "default-sample-rate = 48000\n"
                        "alternate-sample-rate = 48000\n"
                    )
                    if _desktop_user:
                        subprocess.run(["chown", "-R", _desktop_user,
                                        str(_pa_conf_dir)], check=False)
                    subprocess.run(["pulseaudio", "--kill"], env=_user_env,
                                   stdout=_log_d, stderr=_log_d, timeout=5)
                    _time_mod.sleep(0.3)
                    _p_pulse = subprocess.Popen(
                        ["pulseaudio", "--start", "--exit-idle-time=-1",
                         "--load=module-null-sink sink_name=virtual_out rate=48000",
                         "--load=module-always-sink"],
                        env=_user_env, stdout=_log_d, stderr=_log_d)
                    _procs.append(_p_pulse)
                    _time_mod.sleep(0.5)
                    for _pa_cmd, _pa_label in [
                        (["pactl", "info"], "PA info"),
                        (["pactl", "list", "short", "sinks"], "PA sinks"),
                    ]:
                        try:
                            _pa_out = subprocess.check_output(
                                _pa_cmd, env=_user_env, timeout=5, text=True)
                            sys.stderr.write(f"[FSRelay] {_pa_label}:\n{_pa_out.strip()}\n")
                        except Exception as _pa_err:
                            sys.stderr.write(f"[FSRelay] {_pa_label} failed: {_pa_err}\n")
                    _audio_port = _novnc_port + 100
                    _audio_script = Path("/opt/pawflow/audio_capture.py")
                    if _audio_script.exists():
                        _p_audio = subprocess.Popen(
                            [sys.executable, str(_audio_script),
                             "--port", str(_audio_port), "--source", "pulse"],
                            env=_user_env, stdout=_log_d, stderr=_log_d)
                        _procs.append(_p_audio)
                        sys.stderr.write(f"[FSRelay] Audio capture on port {_audio_port}\n")
                    else:
                        _audio_port = 0

                # 4. XFCE desktop session (PA already running — no plugin conflict)
                _p_wm = subprocess.Popen(
                    ["startxfce4"], env=_user_env,
                    stdout=_log_d, stderr=_log_d)
                _procs.append(_p_wm)
                _time_mod.sleep(1)

                # 5. x11vnc
                _p_vnc = subprocess.Popen(
                    ["x11vnc", "-display", _display, "-forever", "-nopw",
                     "-rfbport", str(_vnc_port), "-shared", "-noxdamage",
                     "-defer", "33"],
                    stdout=_log_d, stderr=_log_d)
                _procs.append(_p_vnc)

                # 6. websockify (noVNC)
                _novnc_web = "/usr/share/novnc"
                _p_novnc = subprocess.Popen(
                    ["websockify", "--web", _novnc_web,
                     "--heartbeat", "30",
                     str(_novnc_port), f"localhost:{_vnc_port}"],
                    stdout=_log_d, stderr=_log_d)
                _procs.append(_p_novnc)

                _execute_command._desktop_procs = _procs
                _execute_command._desktop_essential_procs = [_p_xvfb, _p_vnc, _p_novnc]
                _execute_command._desktop_vnc_port = _vnc_port
                _execute_command._desktop_novnc_port = _novnc_port
                _execute_command._desktop_display = _display
                sys.stderr.write(f"[FSRelay] Desktop started: display={_display} vnc={_vnc_port} novnc={_novnc_port} audio={_audio_port} res={_resolution}\n")
                return {"ok": True, "data": {
                    "vnc_port": _vnc_port, "novnc_port": _novnc_port,
                    "audio_port": _audio_port,
                    "display": _display, "resolution": _resolution
                }}
            except FileNotFoundError as e:
                return {"ok": False, "error": f"Desktop dependency not installed: {e}"}
            except Exception as e:
                return {"ok": False, "error": f"Failed to start desktop: {e}"}

        if action == "stop_desktop":
            if hasattr(_execute_command, '_desktop_procs') and _execute_command._desktop_procs:
                for p in _execute_command._desktop_procs:
                    if p.poll() is None:
                        p.terminate()
                for p in _execute_command._desktop_procs:
                    try: p.wait(timeout=5)
                    except subprocess.TimeoutExpired: p.kill()
                _execute_command._desktop_procs = None
                _execute_command._desktop_vnc_port = None
                _execute_command._desktop_novnc_port = None
                _execute_command._desktop_display = None
                if "DISPLAY" in os.environ:
                    del os.environ["DISPLAY"]
                sys.stderr.write("[FSRelay] Desktop stopped\n")
                return {"ok": True}
            return {"ok": True, "data": {"was_running": False}}

        if action == "desktop_status":
            _running = False
            if hasattr(_execute_command, '_desktop_procs') and _execute_command._desktop_procs:
                # Check only essential processes (Xvfb, x11vnc, websockify)
                # startxfce4 and dbus may exit normally after spawning children
                _essential = getattr(_execute_command, '_desktop_essential_procs', None)
                if _essential:
                    _running = all(p.poll() is None for p in _essential)
                else:
                    _running = any(p.poll() is None for p in _execute_command._desktop_procs)
            _local_running = False
            if hasattr(_execute_command, '_local_desktop_procs') and _execute_command._local_desktop_procs:
                _local_running = all(p.poll() is None for p in _execute_command._local_desktop_procs)
            _novnc = getattr(_execute_command, '_desktop_novnc_port', None)
            return {"ok": True, "data": {
                "running": _running,
                "display": getattr(_execute_command, '_desktop_display', None),
                "vnc_port": getattr(_execute_command, '_desktop_vnc_port', None),
                "novnc_port": _novnc,
                "audio_port": (_novnc + 100) if _novnc and _running else 0,
                "local_screen_running": _local_running,
                "local_screen_novnc_port": getattr(_execute_command, '_local_desktop_novnc_port', None),
            }}

        # NOTE: local action forwarding is handled by the main dispatch
        # block at the top of _execute_command (line ~1230). No duplicate here.

        if action == "start_local_desktop":
            # Idempotent
            if hasattr(_execute_command, '_local_desktop_procs') and _execute_command._local_desktop_procs:
                _alive = all(p.poll() is None for p in _execute_command._local_desktop_procs)
                if _alive:
                    return {"ok": True, "data": {
                        "novnc_port": _execute_command._local_desktop_novnc_port,
                        "already_running": True
                    }}
                else:
                    for p in _execute_command._local_desktop_procs:
                        try: p.kill()
                        except: pass
                    _execute_command._local_desktop_procs = None

            # Detect available VNC server
            _vnc_cmd = None
            _platform = sys.platform
            _vnc_port = 0
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as _s:
                _s.bind(("", 0)); _vnc_port = _s.getsockname()[1]
            _novnc_port = int(msg.get("novnc_port", 0))
            if not _novnc_port:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as _s:
                    _s.bind(("", 0)); _novnc_port = _s.getsockname()[1]

            try:
                import shutil
                _procs = []
                _log_d = open("/tmp/local_desktop.log", "w") if _platform != "win32" else open(os.path.join(os.environ.get("TEMP", "."), "local_desktop.log"), "w")

                if _platform == "linux":
                    # Linux: use x11vnc to share the real display :0
                    _display = os.environ.get("DISPLAY", ":0")
                    if not shutil.which("x11vnc"):
                        return {"ok": False, "error": "x11vnc not installed. Install with: apt install x11vnc"}
                    if not shutil.which("websockify"):
                        return {"ok": False, "error": "websockify not installed. Install with: pip install websockify"}
                    _p_vnc = subprocess.Popen(
                        ["x11vnc", "-display", _display, "-forever", "-nopw",
                         "-rfbport", str(_vnc_port), "-shared", "-noxdamage",
                         "-defer", "33"],
                        stdout=_log_d, stderr=_log_d)
                    _procs.append(_p_vnc)

                elif _platform == "win32":
                    # Windows: use TightVNC or UltraVNC via WinVNC if available,
                    # else try built-in Windows VNC (Remote Desktop) — but for noVNC we need a VNC server.
                    # Check for common VNC servers
                    _winvnc = None
                    for _candidate in [
                        r"C:\Program Files\TightVNC\tvnserver.exe",
                        r"C:\Program Files\uvnc bvba\UltraVNC\winvnc.exe",
                        r"C:\Program Files (x86)\TightVNC\tvnserver.exe",
                    ]:
                        if os.path.exists(_candidate):
                            _winvnc = _candidate
                            break
                    if not _winvnc:
                        _winvnc = shutil.which("tvnserver") or shutil.which("winvnc")
                    if not _winvnc:
                        return {"ok": False, "error": "No VNC server found on Windows. Install TightVNC or UltraVNC."}
                    _websockify = shutil.which("websockify")
                    if not _websockify:
                        return {"ok": False, "error": "websockify not installed. Install with: pip install websockify"}
                    # Start VNC server on the specified port
                    _p_vnc = subprocess.Popen(
                        [_winvnc, "-rfbport", str(_vnc_port), "-localhost"],
                        stdout=_log_d, stderr=_log_d)
                    _procs.append(_p_vnc)

                elif _platform == "darwin":
                    # macOS: built-in VNC server (Screen Sharing)
                    # Enable via: System Preferences → Sharing → Screen Sharing
                    # Or start with: /System/Library/CoreServices/RemoteManagement/ARDAgent.app/...
                    if not shutil.which("websockify"):
                        return {"ok": False, "error": "websockify not installed. Install with: pip install websockify"}
                    # macOS VNC server usually runs on port 5900
                    _vnc_port = 5900
                    # Just check it's accessible
                    try:
                        _test = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        _test.settimeout(2)
                        _test.connect(("localhost", 5900))
                        _test.close()
                    except Exception:
                        return {"ok": False, "error": "macOS Screen Sharing not enabled. Enable in System Preferences → Sharing → Screen Sharing."}

                else:
                    return {"ok": False, "error": f"Unsupported platform for local screen: {_platform}"}

                # Start websockify (noVNC)
                import time as _time_mod
                _time_mod.sleep(0.5)
                _novnc_web = "/usr/share/novnc"
                if _platform == "win32":
                    _novnc_web = os.path.join(os.environ.get("PROGRAMFILES", "C:\\Program Files"), "noVNC")
                    if not os.path.isdir(_novnc_web):
                        _novnc_web = ""
                elif _platform == "darwin":
                    _novnc_web = "/usr/local/share/novnc"
                    if not os.path.isdir(_novnc_web):
                        _novnc_web = ""

                _ws_args = ["websockify", str(_novnc_port), f"localhost:{_vnc_port}"]
                if _novnc_web and os.path.isdir(_novnc_web):
                    _ws_args = ["websockify", "--web", _novnc_web, str(_novnc_port), f"localhost:{_vnc_port}"]
                _p_novnc = subprocess.Popen(_ws_args, stdout=_log_d, stderr=_log_d)
                _procs.append(_p_novnc)

                _execute_command._local_desktop_procs = _procs
                _execute_command._local_desktop_vnc_port = _vnc_port
                _execute_command._local_desktop_novnc_port = _novnc_port
                sys.stderr.write(f"[FSRelay] Local desktop started: vnc={_vnc_port} novnc={_novnc_port} platform={_platform}\n")
                return {"ok": True, "data": {
                    "vnc_port": _vnc_port, "novnc_port": _novnc_port,
                    "platform": _platform, "local_screen": True
                }}
            except FileNotFoundError as e:
                return {"ok": False, "error": f"Local desktop dependency not installed: {e}"}
            except Exception as e:
                return {"ok": False, "error": f"Failed to start local desktop: {e}"}

        if action == "stop_local_desktop":
            if hasattr(_execute_command, '_local_desktop_procs') and _execute_command._local_desktop_procs:
                for p in _execute_command._local_desktop_procs:
                    if p.poll() is None:
                        p.terminate()
                for p in _execute_command._local_desktop_procs:
                    try: p.wait(timeout=5)
                    except subprocess.TimeoutExpired: p.kill()
                _execute_command._local_desktop_procs = None
                _execute_command._local_desktop_vnc_port = None
                _execute_command._local_desktop_novnc_port = None
                sys.stderr.write("[FSRelay] Local desktop stopped\n")
                return {"ok": True}
            return {"ok": True, "data": {"was_running": False}}

        if action == "local_screen_check":
            # Check if local screen VNC dependencies are available
            import shutil
            _checks = {}
            _platform = sys.platform
            _checks["platform"] = _platform
            _checks["allow_local_screen"] = allow_local_screen
            if _platform == "linux":
                _checks["x11vnc"] = bool(shutil.which("x11vnc"))
                _checks["websockify"] = bool(shutil.which("websockify"))
                _checks["display"] = os.environ.get("DISPLAY", "")
                _checks["ready"] = _checks["x11vnc"] and _checks["websockify"] and bool(_checks["display"])
            elif _platform == "win32":
                _has_vnc = False
                for _c in [r"C:\Program Files\TightVNC\tvnserver.exe",
                           r"C:\Program Files\uvnc bvba\UltraVNC\winvnc.exe",
                           r"C:\Program Files (x86)\TightVNC\tvnserver.exe"]:
                    if os.path.exists(_c):
                        _has_vnc = True; break
                _has_vnc = _has_vnc or bool(shutil.which("tvnserver")) or bool(shutil.which("winvnc"))
                _checks["vnc_server"] = _has_vnc
                _checks["websockify"] = bool(shutil.which("websockify"))
                _checks["ready"] = _has_vnc and _checks["websockify"]
            elif _platform == "darwin":
                _checks["websockify"] = bool(shutil.which("websockify"))
                try:
                    _test = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    _test.settimeout(2)
                    _test.connect(("localhost", 5900))
                    _test.close()
                    _checks["screen_sharing"] = True
                except Exception:
                    _checks["screen_sharing"] = False
                _checks["ready"] = _checks["websockify"] and _checks["screen_sharing"]
            else:
                _checks["ready"] = False
            return {"ok": True, "data": _checks}

        # ── Desktop VNC WS tunnel (same pattern as cs_ws_*) ────────────────
        if action == "desktop_ws_open":
            if not allow_exec:
                return {"ok": False, "error": "Exec not allowed"}
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
                if not hasattr(_execute_command, '_desktop_ws_sessions'):
                    _execute_command._desktop_ws_sessions = {}
                _execute_command._desktop_ws_sessions[_ws_sid] = {"sock": _vnc_sock}

                def _desktop_ws_reader(_sock, _sid):
                    sys.stderr.write(f"[FSRelay] desktop_ws_reader started for {_sid}\n")
                    try:
                        while True:
                            _hdr2 = b""
                            while len(_hdr2) < 2:
                                _c = _sock.recv(2 - len(_hdr2))
                                if not _c: break
                                _hdr2 += _c
                            if len(_hdr2) < 2: break
                            _op = _hdr2[0] & 0x0F
                            _masked = bool(_hdr2[1] & 0x80)
                            _plen = _hdr2[1] & 0x7F
                            if _plen == 126:
                                _lb = b""
                                while len(_lb) < 2:
                                    _c = _sock.recv(2 - len(_lb))
                                    if not _c: break
                                    _lb += _c
                                _plen = struct.unpack("!H", _lb)[0]
                            elif _plen == 127:
                                _lb = b""
                                while len(_lb) < 8:
                                    _c = _sock.recv(8 - len(_lb))
                                    if not _c: break
                                    _lb += _c
                                _plen = struct.unpack("!Q", _lb)[0]
                            if _masked:
                                _mask = b""
                                while len(_mask) < 4:
                                    _c = _sock.recv(4 - len(_mask))
                                    if not _c: break
                                    _mask += _c
                            _payload = b""
                            while len(_payload) < _plen:
                                _c = _sock.recv(min(65536, _plen - len(_payload)))
                                if not _c: break
                                _payload += _c
                            if _masked:
                                _payload = bytes(b ^ _mask[i % 4] for i, b in enumerate(_payload))
                            if _op == 0x08: break
                            if _op == 0x09:
                                _pong = bytes([0x80 | 0x0A])
                                if len(_payload) < 126:
                                    _pong += bytes([len(_payload)])
                                _pong += _payload
                                try: _sock.sendall(_pong)
                                except: break
                                continue
                            _fwd = json.dumps({
                                "type": "desktop_ws_data",
                                "session_id": _sid,
                                "data": base64.b64encode(_payload).decode("ascii"),
                                "opcode": _op,
                            })
                            with _send_lock:
                                _ws_frame_send(ws_sock_ref[0], _fwd.encode("utf-8"))
                    except Exception:
                        pass
                    finally:
                        try: _sock.close()
                        except: pass
                        if hasattr(_execute_command, '_desktop_ws_sessions'):
                            _execute_command._desktop_ws_sessions.pop(_sid, None)
                        try:
                            with _send_lock:
                                _ws_frame_send(ws_sock_ref[0], json.dumps({"type": "desktop_ws_close", "session_id": _sid}).encode("utf-8"))
                        except: pass

                _t = _threading.Thread(target=_desktop_ws_reader, args=(_vnc_sock, _ws_sid), daemon=True)
                _t.start()
                _execute_command._desktop_ws_sessions[_ws_sid]["reader"] = _t
                return {"ok": True}
            except Exception as e:
                return {"ok": False, "error": f"desktop_ws_open error: {e}"}

        if action == "desktop_ws_send":
            _ws_sid = msg.get("session_id", "")
            _ws_data = msg.get("data", "")
            _ws_op = msg.get("opcode", 2)  # binary by default for VNC
            if not hasattr(_execute_command, '_desktop_ws_sessions'):
                return {"ok": False, "error": "No desktop WS sessions"}
            _ws_sess = _execute_command._desktop_ws_sessions.get(_ws_sid)
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

        if action == "desktop_ws_close":
            _ws_sid = msg.get("session_id", "")
            if hasattr(_execute_command, '_desktop_ws_sessions'):
                _ws_sess = _execute_command._desktop_ws_sessions.pop(_ws_sid, None)
                if _ws_sess and _ws_sess.get("sock"):
                    try: _ws_sess["sock"].close()
                    except: pass
            return {"ok": True}

        if action == "script_hash":
            # Return hash of current relay scripts for version check.
            # Scripts live at /opt/pawflow/*.py (bind-mounted from the
            # host's tools/ in dev setups, or written there in legacy
            # sync setups). __file__ points at the pawflow_relay
            # PACKAGE dir (/opt/pawflow/pawflow_relay/), so the scripts
            # are one level up. Using __file__'s own dir misses them
            # all, returns an empty hash, triggers update_scripts which
            # then hits EROFS on the read-only bind-mount.
            _script_dir = os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))
            _h = hashlib.sha256()
            for _sf in ["pawflow_relay_launcher.py", "fs_actions.py", "fs_exec.py", "fs_screen.py", "fs_mcp.py"]:
                _sp = os.path.join(_script_dir, _sf)
                if os.path.exists(_sp):
                    with open(_sp, "rb") as _f:
                        _h.update(_f.read())
            return {"ok": True, "data": {"hash": _h.hexdigest()[:16]}}

        if action == "update_scripts":
            # Receive updated relay scripts from server, write to script dir, hot-reload.
            # Same path correction as script_hash: scripts live at
            # /opt/pawflow/, not inside the pawflow_relay/ package.
            _scripts = msg.get("scripts", {})
            _new_hash = msg.get("script_hash", "")
            if not _scripts:
                return {"ok": False, "error": "No scripts provided"}
            _script_dir = os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))
            _updated = []
            _readonly_skipped = []
            for _fname, _content_b64 in _scripts.items():
                if _fname not in ("pawflow_relay_launcher.py", "fs_actions.py", "fs_exec.py",
                                  "fs_screen.py", "fs_mcp.py"):
                    continue  # Only accept known relay files
                _dst = os.path.join(_script_dir, _fname)
                _data = base64.b64decode(_content_b64)
                try:
                    with open(_dst, "wb") as _f:
                        _f.write(_data)
                    _updated.append(_fname)
                except OSError as _e:
                    # EROFS (errno 30): file is bind-mounted read-only
                    # from the host in dev setups. The mount IS the
                    # "update" — host edits are already visible. Skip
                    # silently instead of failing the whole sync.
                    if getattr(_e, "errno", 0) == 30:
                        _readonly_skipped.append(_fname)
                    else:
                        raise
            # Hot-reload importable modules (not pawflow_relay.py itself)
            import importlib
            for _mod_name in ["fs_actions", "fs_exec", "fs_screen", "fs_mcp"]:
                if f"{_mod_name}.py" in _updated and _mod_name in sys.modules:
                    try:
                        importlib.reload(sys.modules[_mod_name])
                    except Exception as _e:
                        sys.stderr.write(f"[FSRelay] Failed to reload {_mod_name}: {_e}\n")
            _needs_restart = "pawflow_relay_launcher.py" in _updated
            if _updated or _readonly_skipped:
                sys.stderr.write(
                    f"[FSRelay] Scripts updated={_updated} "
                    f"readonly_skipped={_readonly_skipped} hash={_new_hash}"
                    f"{' (restart needed)' if _needs_restart else ''}\n")
            return {"ok": True, "data": {
                "updated": _updated,
                "readonly_skipped": _readonly_skipped,
                "needs_restart": _needs_restart}}

        # Note: permission checks are enforced server-side by ToolApprovalGate.
        # (local_screen forwarding handled earlier, before desktop handlers)

        # Generic local=True forward: any action with local=true runs on the
        # user's host (via PawCode CLI helper), not in this relay container.
        # This is the equivalent of "exec on host" for all tools — used by
        # http_fetch (LLM proxy) and any other tool that needs the user's
        # actual localhost / host network.
        #
        # STRICT: local=True is a contract, not a hint. If we can't honour
        # it, we MUST fail loud. The previous fallthrough silently ran the
        # action inside the relay container — which means
        # `http_fetch("http://localhost:8080/")` hit the container's
        # network namespace instead of the user's host. Repro: CC gets
        # HTTP 200 with an empty/malformed body (whatever happens to
        # listen on :8080 INSIDE the container, or an immediate EOF from
        # the in-container proxy), qwen on the user's host sees zero
        # requests, and the operator spends an afternoon hunting a ghost.
        # Fail explicitly so the error surfaces as "host helper
        # unavailable" rather than a misleading upstream error.
        if msg.get("local"):
            _hh = os.environ.get("PAWFLOW_HOST_HELPER", "")
            if not _hh:
                return {
                    "ok": False,
                    "error": (
                        "local=True requested but PAWFLOW_HOST_HELPER is "
                        "not configured on the relay container. "
                        "Host-forwarding is required for this action "
                        "(e.g. http_fetch to the user's localhost). "
                        "Restart the relay via the managed path so the "
                        "host-helper thread starts and the env var is "
                        "propagated."),
                }
            if not ws_sock_ref[0]:
                return {
                    "ok": False,
                    "error": (
                        "local=True requested but the relay's WS to the "
                        "server is not alive — cannot stream progress "
                        "back from the host helper."),
                }
            _fwd = {k: v for k, v in msg.items() if k != "local"}
            return _forward_to_host_helper(
                _hh, _fwd, ws_sock_ref[0], _ws_frame_send)

        from fs_actions import ACTIONS as _FS_ACTIONS
        handler_func = _FS_ACTIONS.get(action)
        if not handler_func:
            return {"ok": False, "error": f"Unknown action: {action}"}

        try:
            if action in ("exec", "exec_stream"):
                result = handler_func(root_dir, abs_path, msg,
                                       allow_exec=getattr(mock, 'allow_exec', False),
                                       **({"on_output": on_output} if action == "exec_stream" and on_output else {}))
            elif action == "http_fetch":
                # http_fetch: stream chunks when the caller wired
                # on_output (LLM proxy, SSE relay), else run in sync
                # mode so the action returns {status, headers, body}
                # inline (Pixazo polling, generic GET).
                if on_output:
                    def _on_chunk(kind, data):
                        on_output(kind, data)
                    result = handler_func(root_dir, abs_path, msg,
                                           on_chunk=_on_chunk)
                else:
                    result = handler_func(root_dir, abs_path, msg)
            else:
                result = handler_func(root_dir, abs_path, msg)
            return {"ok": True, "data": result}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── FUSE mounts (one-shot, survive WS reconnects) ────────────────
    # Create the FUSE mounts BEFORE entering the reconnect loop and
    # tear them down only on final exit. Each WS reconnect swaps the
    # underlying ServerFsClient via SwappableServerFsClient.set_inner;
    # the kernel-side mount and inode allocations stay stable, so
    # bind-mounts of /cc_sessions and /filestore in downstream
    # containers (notably CC) keep working across relay reconnects.
    _server_fs_swap = None
    _server_fs_mount = None
    _filestore_fs_swap = None
    if server_mount or filestore_mount:
        from pawflow_relay.server_fs_client import SwappableServerFsClient
        from pawflow_relay.server_fs_mount import CombinedServerFsMount
        # ONE pyfuse3 mount at /pawflow_fs serving both cc_sessions
        # (sfs.*) and filestore (ffs.*) subtrees. Required because
        # pyfuse3 keeps a single global session per process — two
        # separate mounts race on `pyfuse3.init()`, the second wins,
        # the first goes orphan, and any syscall on the orphan blocks
        # forever (no userspace daemon answers the kernel). The
        # canonical paths /cc_sessions and /filestore are restored
        # via `mount --bind` against the routed subtrees.
        _server_fs_swap = SwappableServerFsClient()
        _filestore_fs_swap = SwappableServerFsClient()
        # Mountpoint under /tmp because it's tmpfs (always writable
        # by the relay user, no Dockerfile change required to grant
        # ownership). The bind-mounts that follow expose the canonical
        # /cc_sessions and /filestore paths so downstream consumers
        # don't see the temp location.
        _combined_root = "/tmp/pf_combined_fs"
        try:
            _server_fs_mount = CombinedServerFsMount(
                _combined_root, _server_fs_swap, _filestore_fs_swap)
            _server_fs_mount.start()
            sys.stderr.write(
                f"[FSRelay] combined-fs mounted at {_combined_root}\n")
            # Expose each canonical path as a symlink to the routed
            # subtree of the combined FUSE mount. Symlinks rather than
            # `mount --bind` because they're cheaper and survive any
            # future restructuring; we route every filesystem op
            # through `sudo` because the canonical paths live in `/`
            # (root-owned) and pawflow can't rmdir entries there
            # without escalating privileges. The Dockerfile grants
            # pawflow NOPASSWD sudo precisely to enable this.
            _aliases = []
            if server_mount:
                _aliases.append((f"{_combined_root}/cc_sessions", server_mount))
            if filestore_mount:
                _aliases.append((f"{_combined_root}/filestore", filestore_mount))

            def _sudo_run(argv: list, _what: str):
                _rc = subprocess.run(
                    ["sudo", "-n"] + argv,
                    capture_output=True, text=True, timeout=5)
                if _rc.returncode != 0:
                    sys.stderr.write(
                        f"[FSRelay] {_what} FAILED rc={_rc.returncode} "
                        f"stdout={_rc.stdout.strip()!r} "
                        f"stderr={_rc.stderr.strip()!r}\n")
                return _rc.returncode == 0

            for _src, _dst in _aliases:
                try:
                    # Wipe whatever's at the canonical path (empty dir
                    # from the Dockerfile, leftover symlink from a
                    # previous run, …). `rm -rf` covers all cases.
                    if not _sudo_run(["rm", "-rf", _dst],
                                     f"sudo rm -rf {_dst}"):
                        continue
                    # Re-create the parent dir if `rm -rf` removed it
                    # (it shouldn't for top-level paths like /cc_sessions
                    # but be defensive).
                    _parent = os.path.dirname(_dst) or "/"
                    if not os.path.isdir(_parent):
                        _sudo_run(["mkdir", "-p", _parent],
                                  f"sudo mkdir -p {_parent}")
                    if not _sudo_run(["ln", "-s", _src, _dst],
                                     f"sudo ln -s {_src} {_dst}"):
                        continue
                    sys.stderr.write(
                        f"[FSRelay] symlinked {_dst} → {_src}\n")
                except Exception as _serr:
                    sys.stderr.write(
                        f"[FSRelay] symlink {_dst} → {_src} "
                        f"FAILED: {_serr}\n")
        except Exception as _smerr:
            import traceback as _tb
            _full_tb = _tb.format_exc()
            sys.stderr.write(
                f"[FSRelay] combined-fs mount FAILED: {_smerr}\n"
                "  Likely cause: missing pyfuse3 / libfuse3, or no "
                "CAP_SYS_ADMIN. Continuing without combined-fs.\n"
                f"  full traceback follows:\n{_full_tb}")
            # Also write the traceback into the FUSE trace file so
            # users diagnosing a mount failure don't have to dig
            # through relay.log to find the cause.
            try:
                from pawflow_relay.server_fs_mount import _fuse_trace_emit
                _fuse_trace_emit(
                    f"[FSRelay] combined-fs mount FAILED err={_smerr}\n"
                    f"--- traceback ---\n{_full_tb}--- end ---")
            except Exception:
                pass
            _server_fs_mount = None
            _server_fs_swap = None
            _filestore_fs_swap = None

    reconnect_delay = 1

    while True:
        try:
            sys.stderr.write(f"[FSRelay] Connecting to {url} ...\n")
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

            ws_key = b64.b64encode(os.urandom(16)).decode()
            _cookies = []
            if gateway_cookie:
                _cookies.append(f'_pf_gw={gateway_cookie}')
            if session_token:
                _cookies.append(f'pawflow_token={session_token}')
            _extra_hdrs = ''
            if _cookies:
                _extra_hdrs = 'Cookie: ' + '; '.join(_cookies) + '\r\n'
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
                        data = _buf.pop(0)
                        return data[:n]  # may need to re-buffer if data > n
                    return _orig_recv(n)
                sock.recv = _patched_recv

            sys.stderr.write(f"[FSRelay] Connected to {url}\n")

            reg_msg = json.dumps({
                "type": "register",
                "token": token,
                "secret": secret,
                "relay_type": "relay",
                "relay_id": relay_id,
                "info": info,
            }).encode("utf-8")
            _ws_frame_send(sock, reg_msg)

            opcode, payload = _ws_frame_recv(sock)
            if opcode == 0x01:
                reg_resp = json.loads(payload.decode("utf-8"))
                if reg_resp.get("type") == "registered":
                    sys.stderr.write(f"[FSRelay] Registered as '{reg_resp.get('relay_id')}'\n")

            reconnect_delay = 1
            _KEEPALIVE_INTERVAL = 30
            _DEAD_TIMEOUT = 90  # force reconnect if no data for this long
            sock.settimeout(_KEEPALIVE_INTERVAL)
            ws_sock_ref = [sock]  # mutable ref for _execute_command closures
            _last_activity = [time.time()]  # updated on any recv
            import threading as _threading
            from concurrent.futures import ThreadPoolExecutor
            _pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="relay-cmd")

            # Watchdog: force-close socket if no activity for _DEAD_TIMEOUT
            _watchdog_stop = _threading.Event()
            def _watchdog():
                while not _watchdog_stop.is_set():
                    _watchdog_stop.wait(15)
                    if _watchdog_stop.is_set():
                        break
                    idle = time.time() - _last_activity[0]
                    if idle > _DEAD_TIMEOUT:
                        sys.stderr.write(f"[FSRelay] Watchdog: no activity for {idle:.0f}s, forcing reconnect\n")
                        try:
                            sock.close()
                        except Exception:
                            pass
                        break
            _wd_thread = _threading.Thread(target=_watchdog, daemon=True, name="relay-watchdog")
            _wd_thread.start()
            _send_lock = _threading.Lock()
            _child_relays = {}  # relay_id → thread (child relay instances)
            _terminal_sessions = {}  # session_id → {master_fd, pid, reader}

            # ── Per-WS-connection FUSE clients ──────────────────────────
            # The FUSE mounts themselves were created once before the
            # reconnect loop; here we just build a fresh ServerFsClient
            # bound to THIS sock and swap it into the swappable handle
            # the mount is holding. The kernel-side mount stays live
            # across WS reconnects so downstream container bind-mounts
            # of /cc_sessions and /filestore remain valid.
            from pawflow_relay.server_fs_client import ServerFsClient
            _server_fs_client = None
            if _server_fs_swap is not None:
                _server_fs_client = ServerFsClient(
                    send_callable=lambda b: _ws_frame_send(sock, b),
                    send_lock=_send_lock)
                _server_fs_swap.set_inner(_server_fs_client)

            _filestore_fs_client = None
            if _filestore_fs_swap is not None:
                _filestore_fs_client = ServerFsClient(
                    send_callable=lambda b: _ws_frame_send(sock, b),
                    send_lock=_send_lock)
                _filestore_fs_swap.set_inner(_filestore_fs_client)

            def _open_terminal(cols=80, rows=24, shell=None):
                import uuid as _uuid_term
                import fcntl
                import termios
                import array

                _sid = _uuid_term.uuid4().hex[:12]
                _shell = shell or os.environ.get("SHELL", "/bin/bash")

                pid, master_fd = os.forkpty()
                if pid == 0:
                    os.chdir(root_dir)
                    env = os.environ.copy()
                    env["TERM"] = "xterm-256color"
                    env["COLUMNS"] = str(cols)
                    env["LINES"] = str(rows)
                    os.execvpe(_shell, [_shell], env)

                # Set terminal size
                try:
                    winsize = array.array("H", [rows, cols, 0, 0])
                    fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)
                except Exception:
                    pass

                # Reader thread: PTY fd → WS
                def _pty_reader(_fd, _sid):
                    try:
                        while True:
                            data = os.read(_fd, 4096)
                            if not data:
                                break
                            frame = json.dumps({
                                "type": "terminal_data",
                                "session_id": _sid,
                                "data": base64.b64encode(data).decode("ascii"),
                            }).encode("utf-8")
                            with _send_lock:
                                _ws_frame_send(sock, frame)
                    except OSError:
                        pass
                    finally:
                        try:
                            frame = json.dumps({
                                "type": "terminal_exit",
                                "session_id": _sid,
                            }).encode("utf-8")
                            with _send_lock:
                                _ws_frame_send(sock, frame)
                        except Exception:
                            pass

                reader = _threading.Thread(
                    target=_pty_reader, args=(master_fd, _sid),
                    daemon=True, name=f"pty-reader-{_sid}")
                reader.start()

                _terminal_sessions[_sid] = {
                    "master_fd": master_fd,
                    "pid": pid,
                    "reader": reader,
                    "shell": _shell,
                }
                sys.stderr.write(f"[FSRelay] Terminal opened: {_sid} (shell={_shell})\n")
                return _sid

            def _close_terminal(session_id):
                sess = _terminal_sessions.pop(session_id, None)
                if not sess:
                    return False
                try:
                    os.close(sess["master_fd"])
                except OSError:
                    pass
                try:
                    os.kill(sess["pid"], 9)
                    os.waitpid(sess["pid"], os.WNOHANG)
                except (OSError, ChildProcessError):
                    pass
                sys.stderr.write(f"[FSRelay] Terminal closed: {session_id}\n")
                return True

            def _close_all_terminals():
                for sid in list(_terminal_sessions):
                    _close_terminal(sid)

            while True:
                try:
                    opcode, payload = _ws_frame_recv(sock)
                    _last_activity[0] = time.time()
                except socket.timeout:
                    # Send app-level ping to keep connection alive.
                    # MUST hold _send_lock — worker threads from _pool also send
                    # on this socket with the lock; concurrent writes on an SSL
                    # socket interleave bytes mid-record and the server sees
                    # WRONG_VERSION_NUMBER (ssl is not thread-safe for writes).
                    try:
                        with _send_lock:
                            _ws_frame_send(sock, json.dumps({"type": "ping"}).encode("utf-8"))
                        _last_activity[0] = time.time()  # successful send = connection alive
                    except Exception:
                        break  # send failed → connection dead
                    continue

                if opcode == 0x08:
                    break
                elif opcode == 0x09:
                    # Same reasoning as the ping above: SSL writes must be
                    # serialized with worker-thread sends.
                    with _send_lock:
                        _ws_frame_send(sock, payload, opcode=0x0A)
                    continue
                elif opcode != 0x01:
                    continue

                msg = json.loads(payload.decode("utf-8"))
                _mtype = msg.get("type")
                if _mtype == "relay_response":
                    # Inverse-direction reply for a relay→server FS op.
                    # Wake the FUSE callback waiting on this request_id.
                    # Try each client in turn — request_ids are uuids so
                    # only one will own a given response.
                    _delivered = False
                    for _fsc in (_server_fs_client, _filestore_fs_client):
                        if _fsc is not None and _fsc.dispatch_response(msg):
                            _delivered = True
                            break
                    if not _delivered and (_server_fs_client is not None
                                            or _filestore_fs_client is not None):
                        sys.stderr.write(
                            f"[FSRelay] orphan relay_response: {msg.get('request_id', '?')}\n")
                    continue
                if _mtype == "cancel_request":
                    # Server-initiated kill: a tool action that spawned a
                    # Popen and registered it via register_inflight_proc()
                    # gets terminated. After this returns, the action's
                    # blocked `proc.wait()` unblocks and the action exits
                    # — the original tool caller server-side has already
                    # given up on the result, so we don't need to send a
                    # response here.
                    _rid = msg.get("request_id", "")
                    if _rid:
                        _ok = kill_inflight_proc(_rid)
                        sys.stderr.write(
                            f"[FSRelay] cancel_request rid={_rid} "
                            f"hit={'yes' if _ok else 'no-such-proc'}\n")
                    continue
                if _mtype == "spawn_relay":
                    # Server asks us to create a child relay for a different root
                    _sr_root = msg.get("root", "")
                    _sr_id = msg.get("relay_id", "")
                    _sr_token = msg.get("token", token)
                    _sr_secret = msg.get("secret", secret)
                    _sr_rid = msg.get("request_id", "")
                    if not _sr_root or not os.path.isdir(_sr_root):
                        _resp = json.dumps({"type": "result", "request_id": _sr_rid,
                            "data": {"ok": False, "error": f"Directory not found: {_sr_root}"}}).encode("utf-8")
                        with _send_lock:
                            _ws_frame_send(sock, _resp)
                    else:
                        sys.stderr.write(f"[FSRelay] Spawning child relay: {_sr_id} -> {_sr_root}\n")
                        # If parent uses Docker, child starts its own container
                        _parent_docker = globals().get('_DOCKER_EXEC_CONTAINER') or \
                                         getattr(__import__('fs_actions'), '_DOCKER_EXEC_CONTAINER', None) \
                                         if 'fs_actions' in sys.modules else None
                        _child_docker_image = msg.get("docker_image", "")

                        _docker_cpus = getattr(globals().get('args', None), 'docker_cpus', '2')
                        _docker_memory = getattr(globals().get('args', None), 'docker_memory', '4g')
                        def _child_relay(_url, _tok, _sec, _rid, _root,
                                         _docker_img="", _parent_has_docker=False,
                                         _cpus=_docker_cpus, _mem=_docker_memory):
                            _child_container = None
                            try:
                                # Start child Docker container if parent uses Docker
                                if _docker_img or _parent_has_docker:
                                    import uuid as _uuid_child
                                    _img = _docker_img or "pawflow-relay-dev:latest"
                                    _child_container = f"pawflow-relay-child-{_uuid_child.uuid4().hex[:8]}"
                                    _dr = subprocess.run(_docker_cmd() + [
                                        "run", "-d",
                                        "--name", _child_container,
                                        "-v", f"{_translate_path(_to_host_path(_root))}:/workspace",
                                        "-w", "/workspace",
                                        "--cpus", _cpus, "--memory", _mem,
                                        "--security-opt", "no-new-privileges",
                                        _img, "tail", "-f", "/dev/null",
                                    ], capture_output=True, text=True)
                                    if _dr.returncode == 0:
                                        # Register container for this root dir
                                        import fs_actions as _fsa
                                        if not hasattr(_fsa, '_DOCKER_CONTAINERS'):
                                            _fsa._DOCKER_CONTAINERS = {}
                                        _fsa._DOCKER_CONTAINERS[str(Path(_root).resolve())] = _child_container
                                        sys.stderr.write(f"[FSRelay] Child container: {_child_container}\n")
                                    else:
                                        _child_container = None
                                _ws_connect(_url, _tok, _sec, _rid, _root,
                                            readonly=readonly, allow_exec=allow_exec,
                                            allow_automation=allow_automation,
                                            allow_local_screen=allow_local_screen,
                                            allow_local=allow_local)
                            except Exception as _ce:
                                sys.stderr.write(f"[FSRelay] Child {_rid} died: {_ce}\n")
                            finally:
                                if _child_container:
                                    # Unregister from dict
                                    try:
                                        import fs_actions as _fsa2
                                        _fsa2._DOCKER_CONTAINERS.pop(str(Path(_root).resolve()), None)
                                    except Exception:
                                        pass
                                    try:
                                        subprocess.run(_docker_cmd() + ["rm", "-f", _child_container],
                                                       capture_output=True, timeout=10)
                                    except Exception:
                                        pass
                        _child_thread = _threading.Thread(
                            target=_child_relay,
                            args=(url, _sr_token, _sr_secret, _sr_id, _sr_root,
                                  _child_docker_image, bool(_parent_docker)),
                            daemon=True, name=f"relay-child-{_sr_id}")
                        _child_thread.start()
                        _child_relays[_sr_id] = _child_thread
                        _resp = json.dumps({"type": "result", "request_id": _sr_rid,
                            "data": {"ok": True, "relay_id": _sr_id, "root": _sr_root}}).encode("utf-8")
                        with _send_lock:
                            _ws_frame_send(sock, _resp)

                elif msg.get("type") == "stop_relay":
                    # Server asks us to stop a child relay
                    _stop_id = msg.get("relay_id", "")
                    _stop_rid = msg.get("request_id", "")
                    _child = _child_relays.pop(_stop_id, None)
                    # Child relays run _ws_connect which reconnects forever.
                    # Signal them to stop by removing from tracking.
                    # The child will die on next reconnect failure or be cleaned up.
                    sys.stderr.write(f"[FSRelay] Stopping child relay: {_stop_id}\n")
                    _resp = json.dumps({"type": "result", "request_id": _stop_rid,
                        "data": {"ok": True, "stopped": _stop_id}}).encode("utf-8")
                    with _send_lock:
                        _ws_frame_send(sock, _resp)

                elif msg.get("type") == "terminal_input":
                    _tid = msg.get("session_id", "")
                    _tsess = _terminal_sessions.get(_tid)
                    if _tsess:
                        try:
                            _raw = base64.b64decode(msg.get("data", ""))
                            os.write(_tsess["master_fd"], _raw)
                        except OSError as _oe:
                            sys.stderr.write(f"[FSRelay] terminal write error: {_oe}\n")

                elif msg.get("type") == "terminal_resize":
                    _tid = msg.get("session_id", "")
                    _tsess = _terminal_sessions.get(_tid)
                    if _tsess:
                        try:
                            import fcntl as _fcntl_r
                            import termios as _termios_r
                            import array as _array_r
                            _c = msg.get("cols", 80)
                            _r = msg.get("rows", 24)
                            _ws = _array_r.array("H", [_r, _c, 0, 0])
                            _fcntl_r.ioctl(_tsess["master_fd"], _termios_r.TIOCSWINSZ, _ws)
                        except Exception:
                            pass

                elif msg.get("type") == "command":
                    request_id = msg.get("request_id", "")
                    sys.stderr.write(f"[FSRelay] Command: {msg.get('action', '?')}\n")
                    # Execute in thread pool for parallel command handling
                    def _run_cmd(_msg, _rid, _sock, _send_fn):
                        # Streaming callback for exec_stream
                        _on_output = None
                        if _msg.get("action") == "exec_stream":
                            def _on_output(stream, data):
                                _frame = json.dumps({
                                    "type": "exec_output",
                                    "request_id": _rid,
                                    "stream": stream,
                                    "data": data,
                                }).encode("utf-8")
                                with _send_lock:
                                    _send_fn(_sock, _frame)
                        try:
                            _result = _execute_command(_msg, on_output=_on_output)
                            _resp = json.dumps({
                                "type": "result",
                                "request_id": _rid,
                                "data": _result.get("data", _result),
                            }).encode("utf-8")
                        except Exception as _e:
                            _resp = json.dumps({
                                "type": "result",
                                "request_id": _rid,
                                "data": {"ok": False, "error": str(_e)},
                            }).encode("utf-8")
                        with _send_lock:
                            _send_fn(_sock, _resp)
                    _pool.submit(_run_cmd, msg, request_id, sock, _ws_frame_send)

        except KeyboardInterrupt:
            sys.stderr.write("\n[FSRelay] Shutting down.\n")
            _close_all_terminals()
            # Final exit — unmount the combined FUSE filesystem before
            # returning so we don't leave a dangling pyfuse3 mount
            # pointing at a dead WS.
            if _server_fs_mount is not None:
                try:
                    _server_fs_mount.stop()
                except Exception as _se:
                    sys.stderr.write(f"[FSRelay] combined-fs stop: {_se}\n")
            try:
                sock.close()
            except Exception:
                pass
            return
        except Exception as e:
            sys.stderr.write(f"[FSRelay] Connection error: {e}\n")
        finally:
            # Guard: on early connect errors, _close_all_terminals may not
            # be defined yet (its definition sits past the handshake).
            _ct = locals().get('_close_all_terminals')
            if _ct:
                _ct()
            # Detach the per-WS ServerFsClient from the FUSE mount and
            # cancel its pending requests with EIO so the kernel doesn't
            # hang on the dead socket. The FUSE mount itself stays up
            # across reconnects — see the one-shot setup before this loop.
            for _swap in (_server_fs_swap, _filestore_fs_swap):
                if _swap is not None:
                    try:
                        _swap.clear_inner()
                    except Exception:
                        pass
            for _name in ('_server_fs_client', '_filestore_fs_client'):
                _c = locals().get(_name)
                if _c is not None:
                    try:
                        _c.cancel_all('relay disconnected')
                    except Exception:
                        pass
            # Stop watchdog
            try:
                _watchdog_stop.set()
            except Exception:
                pass
            # Always close socket before reconnecting — prevents socket leak
            try:
                sock.close()
            except Exception:
                pass

        sys.stderr.write(f"[FSRelay] Reconnecting in {reconnect_delay}s ...\n")
        time.sleep(reconnect_delay)
        # Exponential backoff: 1s → 2s → 4s → 8s → 16s → 30s → 60s
        reconnect_delay = min(reconnect_delay * 2, 60)




