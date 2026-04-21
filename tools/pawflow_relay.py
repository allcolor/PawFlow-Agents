#!/usr/bin/env python3
"""PawFlow Relay — Connects to the PawFlow server to provide filesystem access.

Runs on the user's machine and connects TO the server (reverse WebSocket).
Works behind firewalls/NAT. Zero external dependencies (stdlib only).

Usage (auto — default, opens browser for OAuth login):
    python pawflow_relay.py --dir /path/to/share
    python pawflow_relay.py --dir /path/to/share --allow-exec --port 9091
    python pawflow_relay.py --dir /path/to/share --login-url http://host:9090

Usage (manual — legacy):
    python pawflow_relay.py --server ws://host:port/ws/relay \\
        --relay-id localFS --token abc123 --dir /path/to/share

The relay ID is auto-generated as fs_{username}_{hash8} from username + directory,
consistent with PawCode CLI and VSCode extension.

Security:
- OAuth browser login — no plaintext passwords
- Shared secret validated via hmac.compare_digest on every request
- Path traversal prevention (resolve + startswith check)
- --readonly flag rejects write/delete operations (defense-in-depth)
- --bind 127.0.0.1 by default (local only, HTTP mode)
- All operations logged to stderr
"""

import argparse
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


def _docker_cmd():
    if os.name == "nt":
        return ["wsl", "docker"]
    return ["docker"]


def _get_host_ip():
    if os.name == "nt":
        import socket as _s
        try:
            s = _s.socket(_s.AF_INET, _s.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            pass
    return "host.docker.internal"

def _translate_path(p):
    if os.name != "nt":
        return p
    p = p.replace("\\", "/")
    if len(p) >= 2 and p[1] == ":":
        return f"/mnt/{p[0].lower()}{p[2:]}"
    return p


def _to_host_path(container_path):
    """Translate container path to host path for DinD volume mounts."""
    host_workdir = os.environ.get("PAWFLOW_HOST_WORKDIR")
    if not host_workdir:
        return container_path
    container_workdir = os.environ.get("PAWFLOW_WORKDIR", "/workspace")
    try:
        rel = os.path.relpath(container_path, container_workdir)
        if rel.startswith(".."):
            return container_path
        if rel == ".":
            return host_workdir
        return os.path.join(host_workdir, rel).replace("\\", "/")
    except ValueError:
        return container_path


def generate_relay_id(username: str, directory: str) -> str:
    """Generate a stable relay ID from username + directory.

    Format: fs_{username}_{sha256(username:normalized_dir)[:8]}
    Consistent across PawCode CLI, VSCode extension, and this standalone relay.
    """
    normalized = str(Path(directory).resolve())
    h = hashlib.sha256(f"{username}:{normalized}".encode()).hexdigest()[:8]
    return f"fs_{username}_{h}"


# ── Actions that require write access ─────────────────────────────

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

def _find_claude_binary():
    """Find the claude binary in known installation locations."""
    if sys.platform == "win32":
        home = os.environ.get("USERPROFILE", os.environ.get("HOME", ""))
        candidates = [
            os.path.join(home, ".local", "bin", "claude.exe"),
            os.path.join(home, "AppData", "Roaming", "npm", "claude.cmd"),
            os.path.join(home, "AppData", "Roaming", "npm", "claude"),
            os.path.join(home, ".npm-global", "bin", "claude.cmd"),
        ]
    else:
        home = os.path.expanduser("~")
        candidates = [
            os.path.join(home, ".local", "bin", "claude"),
            os.path.join(home, ".npm-global", "bin", "claude"),
            "/usr/local/bin/claude",
            "/usr/bin/claude",
        ]
    # Check known paths first
    for p in candidates:
        if os.path.isfile(p):
            return p
    # Fallback: which
    found = shutil.which("claude")
    if found:
        return found
    return None


def _claude_auth_login(req, *, send_progress=None):
    """Launch `claude auth login` on the HOST, intercept URL, return credentials."""
    claude_path = _find_claude_binary()
    if not claude_path:
        return {"error": "Claude binary not found. Install Claude Code first: npm install -g @anthropic-ai/claude-code"}

    sys.stderr.write(f"[Relay] claude auth login: {claude_path}\n")

    # Record launch time to detect credential updates
    _launch_time = time.time()

    try:
        proc = subprocess.Popen(
            [claude_path, "auth", "login"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True,
        )
    except FileNotFoundError:
        return {"error": f"Claude binary not found: {claude_path}"}
    except Exception as e:
        return {"error": f"Failed to start claude: {e}"}

    url_pattern = re.compile(r'https://claude\.ai/oauth/authorize\S+')
    url_found = None
    all_output = []

    for line in proc.stdout:
        line = line.rstrip()
        all_output.append(line)
        sys.stderr.write(f"[Relay] claude> {line}\n")
        m = url_pattern.search(line)
        if m and not url_found:
            url_found = m.group(0)
            sys.stderr.write(f"[Relay] Auth URL found\n")
            if send_progress:
                send_progress({"url": url_found})

    proc.wait()
    sys.stderr.write(f"[Relay] claude auth login exited: {proc.returncode}\n")

    if proc.returncode != 0 and not url_found:
        output = "\n".join(all_output[-10:])
        return {"error": f"claude auth login failed (exit {proc.returncode}):\n{output}"}

    # Read credentials
    if sys.platform == "win32":
        creds_path = os.path.join(
            os.environ.get("USERPROFILE", os.environ.get("HOME", "")),
            ".claude", ".credentials.json")
    else:
        creds_path = os.path.expanduser("~/.claude/.credentials.json")

    if not os.path.exists(creds_path):
        return {"error": f"Credentials file not found: {creds_path}"}

    # Wait for credentials to be updated (file mtime must be after launch)
    _max_wait = 180  # 3 minutes max
    _waited = 0
    while _waited < _max_wait:
        try:
            mtime = os.path.getmtime(creds_path)
            if mtime >= _launch_time:
                break
        except Exception:
            pass
        time.sleep(1)
        _waited += 1

    if _waited >= _max_wait:
        return {"error": "Timeout: credentials file was not updated after authorization"}

    try:
        with open(creds_path, "r", encoding="utf-8") as f:
            credentials = json.load(f)
    except Exception as e:
        return {"error": f"Failed to read credentials: {e}"}

    return {"credentials": credentials}


def _forward_to_host_helper(host_helper, msg, ws_sock, ws_send_fn):
    """Forward a command to the host helper (CLI process outside Docker).

    Connects via TCP, sends JSON request, reads progress + result.
    Progress messages are forwarded to the server via WebSocket.
    """
    import socket as _sock

    host, port_str = host_helper.rsplit(":", 1)
    port = int(port_str)
    request_id = msg.get("request_id", "")

    try:
        sock = _sock.create_connection((host, port), timeout=10)
    except Exception as e:
        return {"ok": False, "error": f"Cannot reach host helper at {host_helper}: {e}"}

    _sock_owned_by_bg = [False]
    try:
        # Send request (forward full message minus internal fields)
        _fwd_msg = {k: v for k, v in msg.items() if k not in ("type", "request_id")}
        req = json.dumps(_fwd_msg) + "\n"
        sock.sendall(req.encode("utf-8"))

        # Read responses (newline-delimited JSON)
        buf = b""
        result = None
        sock.settimeout(300)  # auth can take a while

        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                resp = json.loads(line)
                if resp.get("type") == "progress":
                    # Forward progress to server via WebSocket
                    if ws_sock:
                        progress = json.dumps({
                            "type": "progress",
                            "request_id": request_id,
                            "data": resp.get("data", {}),
                        }).encode("utf-8")
                        try:
                            ws_send_fn(ws_sock, progress)
                        except Exception:
                            pass
                elif resp.get("type") == "http_response":
                    # Streaming HTTP response chunks (host-side fetch).
                    # Forward verbatim with our request_id.
                    if ws_sock:
                        frame = json.dumps({
                            "type": "http_response",
                            "request_id": request_id,
                            "kind": resp.get("kind", ""),
                            "data": resp.get("data"),
                        }).encode("utf-8")
                        try:
                            ws_send_fn(ws_sock, frame)
                        except Exception:
                            pass
                elif resp.get("type") == "result":
                    data = resp.get("data", {})
                    if "error" in data:
                        return {"ok": False, "error": data["error"]}
                    # If this is a persistent stream (e.g. terminal),
                    # continue reading progress in a background thread
                    # instead of closing the socket.
                    _is_persistent = isinstance(data, dict) and (
                        data.get("session_id", "").startswith("local_term_"))
                    if _is_persistent:
                        _remaining = buf  # leftover data after result line

                        def _bg_progress_reader():
                            _buf = _remaining
                            try:
                                while True:
                                    chunk = sock.recv(4096)
                                    if not chunk:
                                        break
                                    _buf += chunk
                                    while b"\n" in _buf:
                                        line, _buf = _buf.split(b"\n", 1)
                                        r = json.loads(line)
                                        if r.get("type") == "progress" and ws_sock:
                                            p = json.dumps({
                                                "type": "progress",
                                                "request_id": request_id,
                                                "data": r.get("data", {}),
                                            }).encode("utf-8")
                                            try:
                                                ws_send_fn(ws_sock, p)
                                            except Exception:
                                                break
                            except Exception:
                                pass
                            finally:
                                try:
                                    sock.close()
                                except Exception:
                                    pass
                        import threading as _th
                        _sock_owned_by_bg[0] = True
                        _th.Thread(target=_bg_progress_reader, daemon=True,
                                   name=f"host-helper-stream-{request_id[:8]}").start()
                        return {"ok": True, "data": data}
                    return {"ok": True, "data": data}
                elif resp.get("type") == "error":
                    return {"ok": False, "error": resp.get("error", "Unknown error")}

        if result:
            return result
        return {"ok": False, "error": "Host helper closed connection without result"}
    except Exception as e:
        return {"ok": False, "error": f"Host helper communication failed: {e}"}
    finally:
        # Socket closed by bg reader for persistent streams,
        # or here for non-persistent commands
        if not _sock_owned_by_bg[0]:
            try:
                sock.close()
            except Exception:
                pass


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
                allow_automation=False, allow_local_screen=False, allow_local=False):
    """Connect to the PawFlow server via WebSocket and process filesystem commands."""
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

    def _ws_frame_send(sock, data_bytes, opcode=0x01):
        import secrets as _secrets
        mask_key = _secrets.token_bytes(4)
        masked = bytes(b ^ mask_key[i % 4] for i, b in enumerate(data_bytes))
        length = len(data_bytes)
        frame = bytes([0x80 | opcode])
        if length < 126:
            frame += bytes([0x80 | length])
        elif length < 65536:
            frame += bytes([0x80 | 126]) + struct.pack("!H", length)
        else:
            frame += bytes([0x80 | 127]) + struct.pack("!Q", length)
        frame += mask_key + masked
        sock.sendall(frame)

    def _ws_frame_recv(sock):
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
                                _pa_cmd.split(), env=_user_env, timeout=5, text=True)
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
            # Return hash of current relay scripts for version check
            _script_dir = os.path.dirname(os.path.abspath(__file__))
            _h = hashlib.sha256()
            for _sf in ["pawflow_relay.py", "fs_actions.py", "fs_exec.py", "fs_screen.py", "fs_mcp.py"]:
                _sp = os.path.join(_script_dir, _sf)
                if os.path.exists(_sp):
                    with open(_sp, "rb") as _f:
                        _h.update(_f.read())
            return {"ok": True, "data": {"hash": _h.hexdigest()[:16]}}

        if action == "update_scripts":
            # Receive updated relay scripts from server, write to script dir, hot-reload
            _scripts = msg.get("scripts", {})
            _new_hash = msg.get("script_hash", "")
            if not _scripts:
                return {"ok": False, "error": "No scripts provided"}
            _script_dir = os.path.dirname(os.path.abspath(__file__))
            _updated = []
            for _fname, _content_b64 in _scripts.items():
                if _fname not in ("pawflow_relay.py", "fs_actions.py", "fs_exec.py",
                                  "fs_screen.py", "fs_mcp.py"):
                    continue  # Only accept known relay files
                _dst = os.path.join(_script_dir, _fname)
                _data = base64.b64decode(_content_b64)
                with open(_dst, "wb") as _f:
                    _f.write(_data)
                _updated.append(_fname)
            # Hot-reload importable modules (not pawflow_relay.py itself)
            import importlib
            for _mod_name in ["fs_actions", "fs_exec", "fs_screen", "fs_mcp"]:
                if f"{_mod_name}.py" in _updated and _mod_name in sys.modules:
                    try:
                        importlib.reload(sys.modules[_mod_name])
                    except Exception as _e:
                        sys.stderr.write(f"[FSRelay] Failed to reload {_mod_name}: {_e}\n")
            _needs_restart = "pawflow_relay.py" in _updated
            sys.stderr.write(f"[FSRelay] Scripts updated: {_updated} hash={_new_hash}"
                             f"{' (restart needed)' if _needs_restart else ''}\n")
            return {"ok": True, "data": {"updated": _updated, "needs_restart": _needs_restart}}

        # Note: permission checks are enforced server-side by ToolApprovalGate.
        # (local_screen forwarding handled earlier, before desktop handlers)

        # Generic local=True forward: any action with local=true runs on the
        # user's host (via PawCode CLI helper), not in this relay container.
        # This is the equivalent of "exec on host" for all tools — used by
        # http_fetch (LLM proxy) and any other tool that needs the user's
        # actual localhost / host network.
        if msg.get("local"):
            _hh = os.environ.get("PAWFLOW_HOST_HELPER", "")
            if _hh and ws_sock_ref[0]:
                _fwd = {k: v for k, v in msg.items() if k != "local"}
                return _forward_to_host_helper(_hh, _fwd, ws_sock_ref[0], _ws_frame_send)

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
            if _gateway_cookie:
                _cookies.append(f'_pf_gw={_gateway_cookie}')
            if _session_token:
                _cookies.append(f'pawflow_token={_session_token}')
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
                if msg.get("type") == "spawn_relay":
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


def _find_free_port():
    """Find a free TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _acquire_gateway_cookie(api_url, gateway_key):
    """POST /_gateway with the access key, return the _pf_gw cookie value or empty string."""
    import http.client
    from urllib.parse import urlparse, urlencode

    parsed = urlparse(api_url)
    use_ssl = parsed.scheme == "https"
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if use_ssl else 80)

    if use_ssl:
        import ssl
        ctx = ssl.create_default_context()
        if os.environ.get('PAWFLOW_RELAY_INSECURE') == '1':
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        conn = http.client.HTTPSConnection(host, port, context=ctx)
    else:
        conn = http.client.HTTPConnection(host, port)

    body = urlencode({"secret": gateway_key, "next": "/"})
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    conn.request("POST", "/_gateway", body=body, headers=headers)
    resp = conn.getresponse()
    resp.read()  # drain

    # Extract _pf_gw from Set-Cookie header
    cookie_val = ""
    for hdr in resp.msg.get_all("Set-Cookie") or []:
        for part in hdr.split(";"):
            part = part.strip()
            if part.startswith("_pf_gw="):
                cookie_val = part[len("_pf_gw="):]
                break
        if cookie_val:
            break

    conn.close()
    if cookie_val:
        sys.stderr.write(f"[FSRelay] Gateway cookie acquired.\n")
    else:
        sys.stderr.write(f"[FSRelay] Warning: gateway POST returned no _pf_gw cookie.\n")
    return cookie_val


# Module-level gateway cookie + session token — set once in main(), used by _api_call and _ws_connect
_gateway_cookie = ""
_session_token = ""


def _api_call(api_url, method, path, body=None, session_id=""):
    """Make an HTTP request to the PawFlow API (stdlib only)."""
    import http.client
    from urllib.parse import urlparse

    parsed = urlparse(api_url)
    use_ssl = parsed.scheme == "https"
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if use_ssl else 80)

    if use_ssl:
        import ssl
        ctx = ssl.create_default_context()
        if os.environ.get('PAWFLOW_RELAY_INSECURE') == '1':
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        conn = http.client.HTTPSConnection(host, port, context=ctx)
    else:
        conn = http.client.HTTPConnection(host, port)

    headers = {"Content-Type": "application/json"}
    if session_id:
        headers["Authorization"] = f"Bearer {session_id}"
    if _gateway_cookie:
        headers["Cookie"] = f"_pf_gw={_gateway_cookie}"

    payload = json.dumps(body).encode("utf-8") if body else None
    conn.request(method, path, body=payload, headers=headers)
    resp = conn.getresponse()
    data = resp.read().decode("utf-8")
    conn.close()

    if resp.status >= 400:
        raise Exception(f"API {method} {path} → {resp.status}: {data}")
    return json.loads(data) if data else {}


def _agent_api_call(login_url, session_id, action_body):
    """Call the UI action endpoint with an action payload.

    UI actions (service_install, service_uninstall, relay_list_available, …)
    are dispatched via agentActions on /api/ui — not the agent pipeline at
    /api/agent, which is reserved for real user↔agent messages.
    """
    return _api_call(login_url, "POST", "/api/ui",
                     body=action_body, session_id=session_id)


def _create_service(login_url, session_id, service_id, port, relay_path, token):
    """Create a user filesystem service via the agent API."""
    config_str = f"port={port},path={relay_path},token={token},mode=readwrite"
    return _agent_api_call(login_url, session_id, {
        "action": "service_install",
        "service_type": "relay",
        "service_name": service_id,
        "config_str": config_str,
    })


def _delete_service(login_url, session_id, service_id):
    """Delete a user filesystem service via the agent API."""
    try:
        _agent_api_call(login_url, session_id, {
            "action": "service_uninstall",
            "service_id": service_id,
        })
    except Exception:
        pass  # May not exist, that's OK


def _start_callback_server():
    """Start a tiny HTTP server to receive the OAuth callback token."""
    from http.server import HTTPServer as _HTTPServer, BaseHTTPRequestHandler
    import threading
    from urllib.parse import urlparse, parse_qs

    result = {"token": None, "username": None}
    ready = threading.Event()

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            result["token"] = params.get("token", [None])[0]
            result["username"] = params.get("username", [None])[0]

            # Serve a simple "you can close this" page
            html = (
                '<!DOCTYPE html><html><body style="font-family:sans-serif;text-align:center;'
                'padding:60px;background:#1a1a2e;color:#e0e0e0">'
                '<h2>&#10004; Relay authenticated</h2>'
                '<p>You can close this window. The relay is now connected.</p>'
                '</body></html>'
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode())
            ready.set()

        def log_message(self, *args):
            pass  # suppress logs

    server = _HTTPServer(("127.0.0.1", 0), CallbackHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    return port, result, ready, server


def _auto_register(args):
    """Auto-register: browser login, create service, return (ws_url, token, session_id, api_url)."""
    import secrets as _secrets
    import webbrowser
    from urllib.parse import quote

    login_url = args.login_url.rstrip("/")

    # Start callback server
    cb_port, cb_result, cb_ready, cb_server = _start_callback_server()
    callback_url = f"http://127.0.0.1:{cb_port}/callback"

    # Open browser to login page with relay_callback
    auth_url = f"{login_url}/auth/login?relay_callback={quote(callback_url)}"
    sys.stderr.write(f"[FSRelay] Opening browser for login: {auth_url}\n")
    sys.stderr.write(f"[FSRelay] Waiting for authentication...\n")
    webbrowser.open(auth_url)

    # Wait for callback (timeout 120s)
    if not cb_ready.wait(timeout=120):
        cb_server.server_close()
        sys.stderr.write("[FSRelay] Error: authentication timed out (120s)\n")
        sys.exit(1)

    cb_server.server_close()
    session_id = cb_result.get("token")
    username = cb_result.get("username", "?")

    if not session_id:
        sys.stderr.write("[FSRelay] Error: no token received from login\n")
        sys.exit(1)

    sys.stderr.write(f"[FSRelay] Authenticated as '{username}'.\n")

    # Auto-generate relay_id if not provided
    root_dir = str(Path(args.dir).resolve())
    if not args.relay_id:
        args.relay_id = generate_relay_id(username, root_dir)
        sys.stderr.write(f"[FSRelay] Auto-generated relay ID: {args.relay_id}\n")

    ws_token = _secrets.token_urlsafe(32)

    # Delete existing service if any
    sys.stderr.write(f"[FSRelay] Cleaning up previous service '{args.relay_id}' ...\n")
    _delete_service(login_url, session_id, args.relay_id)

    # Create new service (port kept for legacy config schema; server ignores it)
    sys.stderr.write(f"[FSRelay] Creating service '{args.relay_id}' ...\n")
    _create_service(login_url, session_id, args.relay_id, 0, args.relay_path, ws_token)
    sys.stderr.write(f"[FSRelay] Service created.\n")

    # Build WS URL from the main listener URL (login_url). The route is
    # registered server-side on HTTPListenerService at /ws/relay/<service_id>.
    from urllib.parse import urlparse as _up
    _parsed = _up(login_url)
    _scheme = 'wss' if _parsed.scheme == 'https' else 'ws'
    _host = _parsed.hostname or args.host
    _port = _parsed.port or (443 if _parsed.scheme == 'https' else 80)
    ws_url = f"{_scheme}://{_host}:{_port}/ws/relay/{args.relay_id}"

    return ws_url, ws_token, session_id, login_url


def main():
    # Env var fallback — used when running as a server-spawned container
    _env_server = os.environ.get("PAWFLOW_RELAY_SERVER", "")
    _env_token = os.environ.get("PAWFLOW_RELAY_TOKEN", "")
    _env_relay_id = os.environ.get("PAWFLOW_RELAY_ID", "")
    _env_dir = os.environ.get("PAWFLOW_RELAY_DIR", "")
    _env_allow_exec = os.environ.get("PAWFLOW_RELAY_ALLOW_EXEC", "").lower() in ("1", "true", "yes")

    parser = argparse.ArgumentParser(
        description="PawFlow Relay — Connects to PawFlow server for filesystem access",
    )
    parser.add_argument("--server", default=_env_server,
                        help="PawFlow server WS URL (manual mode)")
    parser.add_argument("--relay-id", default=_env_relay_id,
                        help="Service ID (auto-generated from username+dir if omitted)")
    parser.add_argument("--token", default=_env_token,
                        help="Token for manual WS auth")
    parser.add_argument("--dir", required=not bool(_env_dir), default=_env_dir,
                        help="Root directory for filesystem access")
    parser.add_argument("--readonly", action="store_true",
                        help="Reject write/delete operations")
    parser.add_argument("--allow-exec", action="store_true",
                        help="Allow shell command execution (disabled by default)")
    parser.add_argument("--allow-automation", action="store_true",
                        help="Allow screen automation (screenshot, click, type — disabled by default)")
    parser.add_argument("--allow-local-screen", action="store_true",
                        help="Allow local screen access — actions execute on this machine's display (disabled by default)")
    parser.add_argument("--allow-local", action="store_true",
                        help="Allow local exec — commands run on the host, not in Docker (disabled by default)")
    # Auto-registration params
    parser.add_argument("--login-url", default="http://localhost:9090",
                        help="PawFlow chat UI URL for OAuth login (default: http://localhost:9090)")
    parser.add_argument("--host", default="localhost",
                        help="Host the WS listener binds to (default: localhost)")
    parser.add_argument("--port", type=int, default=0,
                        help="Port for WS listener (0 = auto-select free port)")
    parser.add_argument("--relay-path", default="/ws/relay",
                        help="WS endpoint path (default: /ws/relay)")
    parser.add_argument("--no-tls", action="store_true",
                        help="Use ws:// instead of wss:// (default is wss with self-signed cert)")
    parser.add_argument("--docker-image", default="",
                        help="Run exec/git commands inside this Docker image (mounts --dir as /workspace)")
    parser.add_argument("--docker-cpus", default=os.environ.get("PAWFLOW_RELAY_CPUS", "2"),
                        help="CPU limit for Docker containers (default: 2, env: PAWFLOW_RELAY_CPUS)")
    parser.add_argument("--docker-memory", default=os.environ.get("PAWFLOW_RELAY_MEMORY", "4g"),
                        help="Memory limit for Docker containers (default: 4g, env: PAWFLOW_RELAY_MEMORY)")
    parser.add_argument("--gateway-key", default=os.environ.get("PAWFLOW_GATEWAY_KEY", ""),
                        help="Private gateway access key (env: PAWFLOW_GATEWAY_KEY)")
    parser.add_argument("--gateway-cookie", default=os.environ.get("PAWFLOW_GATEWAY_COOKIE", ""),
                        help="Pre-acquired _pf_gw cookie value (env: PAWFLOW_GATEWAY_COOKIE)")
    parser.add_argument("--session-token", default=os.environ.get("PAWFLOW_SESSION_TOKEN", ""),
                        help="User session token / pawflow_token cookie (env: PAWFLOW_SESSION_TOKEN)")
    args = parser.parse_args()
    # Apply env var defaults that argparse store_true can't handle natively
    if _env_allow_exec:
        args.allow_exec = True

    root_dir = str(Path(args.dir).resolve())
    if not Path(root_dir).is_dir():
        sys.stderr.write(f"[Relay] Error: not a directory: {root_dir}\n")
        sys.exit(1)

    mode = "readonly" if args.readonly else "readwrite"
    session_id = ""
    login_url = ""
    _cleaned_up = False

    if args.server and args.token:
        # Manual mode (legacy) — relay_id required
        if not args.relay_id:
            sys.stderr.write("[Relay] Error: --relay-id is required in manual mode\n")
            sys.exit(1)
        ws_url = args.server
        token = args.token
        masked = token[:2] + "*" * max(0, len(token) - 2)
    else:
        # Auto-registration mode (default — opens browser for OAuth login)
        ws_url, token, session_id, login_url = _auto_register(args)
        masked = token[:4] + "****"

    sys.stderr.write(
        f"\n  PawFlow Relay\n"
        f"  ─────────────\n"
        f"  Server:    {ws_url}\n"
        f"  Relay ID:  {args.relay_id}\n"
        f"  Directory: {root_dir}\n"
        f"  Mode:      {mode}\n"
        f"  Exec:      {'enabled' if args.allow_exec else 'disabled'}\n"
        f"  Automation:{'enabled' if args.allow_automation else 'disabled'}\n"
        f"  Local scr: {'enabled' if args.allow_local_screen else 'disabled'}\n"
        f"  Local exec:{'enabled' if args.allow_local else 'disabled'}\n"
        f"  Token:     {masked}\n"
        f"  Auto-reg:  {'no (manual)' if args.server else 'yes'}\n"
        f"  Gateway:   {'key provided' if args.gateway_key else 'none'}\n\n"
    )

    # Acquire / set gateway cookie and session token
    global _gateway_cookie, _session_token
    if args.gateway_cookie:
        _gateway_cookie = args.gateway_cookie
    elif args.gateway_key:
        _gw_url = login_url or args.login_url.rstrip("/")
        if not _gw_url:
            from urllib.parse import urlparse as _gw_parse
            _gw_parsed = _gw_parse(ws_url)
            _gw_scheme = "https" if _gw_parsed.scheme in ("wss", "https") else "http"
            _gw_url = f"{_gw_scheme}://{_gw_parsed.hostname}:{_gw_parsed.port or 80}"
        _gateway_cookie = _acquire_gateway_cookie(_gw_url, args.gateway_key)
    if args.session_token:
        _session_token = args.session_token
    elif session_id:
        _session_token = session_id

    # Cleanup on exit (auto-registration only)
    def _cleanup():
        nonlocal _cleaned_up
        if _cleaned_up:
            return
        if session_id and login_url:
            _cleaned_up = True
            sys.stderr.write(f"[FSRelay] Cleaning up service '{args.relay_id}' ...\n")
            _delete_service(login_url, session_id, args.relay_id)
            sys.stderr.write(f"[FSRelay] Service deleted.\n")

    import atexit
    import signal
    atexit.register(_cleanup)

    def _signal_handler(sig, frame):
        sys.stderr.write("\n[FSRelay] Shutting down (signal).\n")
        _cleanup()
        sys.exit(0)

    signal.signal(signal.SIGINT, _signal_handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _signal_handler)

    if args.docker_image:
        # Docker mode: launch the relay INSIDE the container.
        # The container relay connects directly to the PawFlow server.
        # The host process just manages the container lifecycle.
        import uuid as _uuid_docker
        _docker_container = f"pawflow-relay-{_uuid_docker.uuid4().hex[:8]}"
        sys.stderr.write(f"[FSRelay] Starting Docker relay: {_docker_container}\n")

        # The container runs the relay Python script connecting to the server
        docker_run_args = [
            "--rm",
            "--name", _docker_container,
            "-v", f"{_translate_path(_to_host_path(root_dir))}:/workspace",
        ]
        # Dev mount: bind relay scripts from host so changes take effect without rebuild
        _tools_dir = os.path.dirname(os.path.abspath(__file__))
        for _relay_file in ["pawflow_relay.py", "fs_actions.py", "fs_exec.py", "fs_screen.py", "fs_mcp.py"]:
            _src = os.path.join(_tools_dir, _relay_file)
            if os.path.exists(_src):
                docker_run_args.extend(["-v", f"{_translate_path(_to_host_path(_src))}:/opt/pawflow/{_relay_file}:ro"])
        # Propagate auth cookies to the container via env (not argv, to stay out of `ps`)
        if _gateway_cookie:
            docker_run_args += ["-e", f"PAWFLOW_GATEWAY_COOKIE={_gateway_cookie}"]
        if _session_token:
            docker_run_args += ["-e", f"PAWFLOW_SESSION_TOKEN={_session_token}"]
        if os.environ.get('PAWFLOW_RELAY_INSECURE') == '1':
            docker_run_args += ["-e", "PAWFLOW_RELAY_INSECURE=1"]
        docker_run_args += [
            "--add-host", "host.docker.internal:host-gateway",
            "--cpus", args.docker_cpus,
            "--memory", args.docker_memory,
            "--security-opt", "no-new-privileges",
            args.docker_image,
            "python3", "/opt/pawflow/pawflow_relay.py",
            "--server", ws_url.replace("localhost", _get_host_ip())
                               .replace("127.0.0.1", _get_host_ip()),
            "--token", token,
            "--relay-id", args.relay_id,
            "--dir", "/workspace",
        ]
        docker_cmd = _docker_cmd() + ["run"] + docker_run_args
        if args.allow_exec:
            docker_cmd.append("--allow-exec")
        if args.allow_automation:
            docker_cmd.append("--allow-automation")
        if args.readonly:
            docker_cmd.append("--readonly")

        sys.stderr.write(f"[FSRelay] Container relay connecting to server...\n")
        try:
            # Run in foreground — blocks until container exits
            proc = subprocess.Popen(docker_cmd, stdout=sys.stdout, stderr=sys.stderr)

            def _cleanup_docker():
                try:
                    subprocess.run(_docker_cmd() + ["rm", "-f", _docker_container],
                                   capture_output=True, timeout=10)
                except Exception:
                    pass
            atexit.register(_cleanup_docker)

            proc.wait()
        except KeyboardInterrupt:
            sys.stderr.write(f"\n[FSRelay] Stopping container: {_docker_container}\n")
            subprocess.run(_docker_cmd() + ["rm", "-f", _docker_container],
                           capture_output=True, timeout=10)
        finally:
            _cleanup()
    else:
        # Direct mode: connect to server from this process
        try:
            _ws_connect(ws_url, token, token, args.relay_id,
                         root_dir, args.readonly, allow_exec=args.allow_exec,
                         allow_automation=args.allow_automation,
                         allow_local_screen=args.allow_local_screen,
                         allow_local=args.allow_local)
        finally:
            _cleanup()


if __name__ == "__main__":
    main()
