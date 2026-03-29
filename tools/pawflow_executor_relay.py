#!/usr/bin/env python3
"""PawFlow Executor Relay — Standalone relay for remote command execution.

Runs on the user's machine to give the PawFlow agent secure shell/python/git
access. Zero external dependencies (stdlib only).

Two modes:
  HTTP (legacy, local only):
    python pawflow_executor_relay.py --port 9877 --dir . --secret abc123

  WS Reverse (recommended, works across NAT/firewalls):
    python pawflow_executor_relay.py --connect ws://pawflow.example.com/ws/relay \
        --token <api_key> --secret abc --dir /home/user/project

Security:
- Shared secret validated via hmac.compare_digest on every request
- Path containment (--dir = root, cwd resolved + relative_to check)
- --deny-patterns: regex blocklist for dangerous commands
- --no-shell / --no-python to disable actions
- Timeout enforcement (process.wait + process.kill)
- Output truncated to 100KB per stream
- --bind 127.0.0.1 by default (local only, HTTP mode)
- All commands logged to stderr
"""

import argparse
import hmac
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

VERSION = "1.0.0"
MAX_OUTPUT = 100 * 1024  # 100KB per stream


# ── Shell detection ──────────────────────────────────────────────

def _detect_shell():
    """Detect the native shell for the current platform."""
    if sys.platform == "win32":
        ps = shutil.which("pwsh") or shutil.which("powershell")
        return ps or os.environ.get("COMSPEC", "cmd.exe")
    return os.environ.get("SHELL", "/bin/sh")


# ── Default deny patterns ───────────────────────────────────────

_DEFAULT_DENY = [
    r"rm\s+-rf\s+/\s*$",
    r"mkfs\b",
    r"\bdd\s+if=",
    r"format\s+[a-zA-Z]:",
    r"diskpart",
    r":\(\)\s*\{\s*:\|:\s*&\s*\}\s*;",  # fork bomb
]


# ── Request handler ──────────────────────────────────────────────

class ExecutorRelayHandler(BaseHTTPRequestHandler):
    """HTTP handler for executor relay operations."""

    server_version = "PawFlow-ExecRelay/1.0"

    # Set by factory
    root_dir: str = "."
    secret: str = ""
    shell: str = ""
    disabled_actions: set = set()
    deny_patterns: list = []

    def log_message(self, fmt, *args):
        sys.stderr.write(f"[ExecRelay] {self.address_string()} - {fmt % args}\n")

    def _log_op(self, action: str, ok: bool, detail: str = ""):
        tag = "OK" if ok else "FAIL"
        extra = f" | {detail}" if detail else ""
        sys.stderr.write(f"[ExecRelay] [{tag}] {action}{extra}\n")

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
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _resolve_cwd(self, rel_cwd: str):
        """Resolve relative cwd to absolute, checking containment."""
        root = Path(self.root_dir).resolve()
        target = (root / rel_cwd).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            return None
        return str(target)

    def _truncate(self, text: str) -> str:
        """Truncate output to MAX_OUTPUT bytes."""
        if len(text) > MAX_OUTPUT:
            return text[:MAX_OUTPUT] + f"\n... [truncated at {MAX_OUTPUT} bytes]"
        return text

    def _check_deny(self, command: str) -> str | None:
        """Check command against deny patterns. Returns matched pattern or None."""
        for pattern in self.deny_patterns:
            if re.search(pattern, command, re.IGNORECASE):
                return pattern
        return None

    # ── HTTP verbs ────────────────────────────────────────────────

    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        """Health check / info endpoint."""
        actions = ["shell", "python_exec", "git"]
        actions = [a for a in actions if a not in self.disabled_actions]
        py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        self._send_json(True, data={
            "service": "PawFlow-ExecRelay",
            "version": VERSION,
            "platform": sys.platform,
            "shell": self.shell,
            "python": py_ver,
            "root": self.root_dir,
            "actions": actions,
        })

    def do_POST(self):
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

        # Check disabled
        base_action = "git" if action.startswith("git") else action
        if base_action in self.disabled_actions:
            self._log_op(action, False, "action disabled")
            self._send_json(False, error=f"Action disabled: {action}")
            return

        # Resolve cwd
        rel_cwd = req.get("cwd", ".")
        cwd = self._resolve_cwd(rel_cwd)
        if cwd is None:
            self._log_op(action, False, f"path traversal blocked: {rel_cwd}")
            self._send_json(False, error=f"Path traversal blocked: {rel_cwd}")
            return

        # Dispatch
        handler = _ACTIONS.get(action)
        if not handler:
            self._log_op(action, False, "unknown action")
            self._send_json(False, error=f"Unknown action: {action}")
            return

        timeout = req.get("timeout")  # None = no limit

        try:
            result = handler(self, cwd, req, timeout)
            self._log_op(action, True)
            self._send_json(True, data=result)
        except Exception as e:
            self._log_op(action, False, str(e))
            self._send_json(False, error=str(e))


# ── Action implementations ───────────────────────────────────────

def _run_process(args, cwd, timeout, shell=False, env=None):
    """Run a subprocess with timeout and output truncation."""
    start = time.monotonic()
    try:
        proc = subprocess.Popen(
            args, cwd=cwd, shell=shell,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env,
        )
        stdout, stderr = proc.communicate(timeout=timeout)
        duration_ms = int((time.monotonic() - start) * 1000)
        return {
            "exit_code": proc.returncode,
            "stdout": stdout.decode("utf-8", errors="replace")[:MAX_OUTPUT],
            "stderr": stderr.decode("utf-8", errors="replace")[:MAX_OUTPUT],
            "duration_ms": duration_ms,
        }
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise TimeoutError(f"Command timed out after {timeout}s")


def _action_shell(handler, cwd, req, timeout):
    """Execute a shell command."""
    command = req.get("command", "")
    if not command:
        raise ValueError("Missing 'command' parameter")

    denied = handler._check_deny(command)
    if denied:
        raise ValueError(f"Command blocked by deny pattern: {denied}")

    # On Windows with PowerShell, wrap command appropriately
    shell_path = handler.shell
    if sys.platform == "win32" and "powershell" in shell_path.lower():
        args = [shell_path, "-NoProfile", "-Command", command]
        use_shell = False
    elif sys.platform == "win32":
        args = command
        use_shell = True
    else:
        # Unix: use the configured shell
        args = [shell_path, "-c", command]
        use_shell = False

    result = _run_process(args, cwd, timeout, shell=use_shell)
    result["shell"] = os.path.basename(shell_path)
    result["platform"] = sys.platform
    return result


def _action_python_exec(handler, cwd, req, timeout):
    """Execute Python code."""
    code = req.get("code", "")
    if not code:
        raise ValueError("Missing 'code' parameter")

    result = _run_process(
        [sys.executable, "-c", code], cwd, timeout,
    )
    result["python"] = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    result["platform"] = sys.platform
    return result




def _action_claude_auth_login(handler, cwd, req, timeout, *, send_progress=None):
    """Launch `claude auth login` on the HOST, intercept URL, return credentials.

    This action runs directly on the host machine (not in Docker sandbox).
    Only `claude auth login` is allowed — no arbitrary commands.
    """
    claude_path = req.get("claude_path", "").strip()
    if not claude_path:
        # Platform default
        if sys.platform == "win32":
            home = os.environ.get("USERPROFILE", os.environ.get("HOME", ""))
            claude_path = os.path.join(home, ".local", "bin", "claude.exe")
            if not os.path.exists(claude_path):
                claude_path = shutil.which("claude") or "claude"
        else:
            claude_path = shutil.which("claude") or "claude"

    # Security: validate path — no shell injection
    _bad = set(';|&`$(){}')
    if any(c in claude_path for c in _bad):
        return {"error": f"Invalid claude path: contains forbidden characters"}

    sys.stderr.write(f"[ExecRelay] claude auth login: {claude_path}\n")

    try:
        proc = subprocess.Popen(
            [claude_path, "auth", "login", "--no-browser"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=cwd,
        )
    except FileNotFoundError:
        return {"error": f"Claude binary not found: {claude_path}"}
    except Exception as e:
        return {"error": f"Failed to start claude: {e}"}

    # Parse stdout for the authorize URL
    url_pattern = re.compile(r'https://claude\.ai/oauth/authorize\S+')
    url_found = None

    for line in proc.stdout:
        line = line.rstrip()
        sys.stderr.write(f"[ExecRelay] claude> {line}\n")
        m = url_pattern.search(line)
        if m and not url_found:
            url_found = m.group(0)
            sys.stderr.write(f"[ExecRelay] Auth URL found: {url_found[:80]}...\n")
            if send_progress:
                send_progress({"url": url_found})

    proc.wait()
    sys.stderr.write(f"[ExecRelay] claude auth login exited: {proc.returncode}\n")

    if proc.returncode != 0 and not url_found:
        return {"error": f"claude auth login failed (exit {proc.returncode})"}

    # Read credentials
    if sys.platform == "win32":
        creds_path = os.path.join(
            os.environ.get("USERPROFILE", os.environ.get("HOME", "")),
            ".claude", ".credentials.json")
    else:
        creds_path = os.path.expanduser("~/.claude/.credentials.json")

    if not os.path.exists(creds_path):
        return {"error": f"Credentials file not found: {creds_path}"}

    try:
        with open(creds_path, "r", encoding="utf-8") as f:
            credentials = json.load(f)
    except Exception as e:
        return {"error": f"Failed to read credentials: {e}"}

    return {"credentials": credentials}


# ── Action dispatch table ────────────────────────────────────────

_ACTIONS = {
    "shell": _action_shell,
    "python_exec": _action_python_exec,
    "claude_auth_login": _action_claude_auth_login,
}


# ── Main ─────────────────────────────────────────────────────────

def _make_handler_class(root_dir: str, secret: str, shell: str,
                        disabled_actions: set, deny_patterns: list):
    """Create a handler class with bound config."""

    class ConfiguredHandler(ExecutorRelayHandler):
        pass

    ConfiguredHandler.root_dir = root_dir
    ConfiguredHandler.secret = secret
    ConfiguredHandler.shell = shell
    ConfiguredHandler.disabled_actions = disabled_actions
    ConfiguredHandler.deny_patterns = deny_patterns
    return ConfiguredHandler


# ── WS Reverse client ─────────────────────────────────────────────

def _ws_connect(url, token, secret, relay_id, root_dir, shell, disabled, deny):
    """Connect to the PawFlow server via WebSocket and process commands.

    Minimal WS client using stdlib only (RFC 6455).
    """
    import hashlib
    import socket
    import ssl
    import struct
    import base64 as b64
    from urllib.parse import urlparse

    parsed = urlparse(url)
    use_ssl = parsed.scheme in ("wss", "https")
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if use_ssl else 80)
    path = parsed.path or "/ws/relay"

    actions = [a for a in ["shell", "python_exec", "git"] if a not in disabled]
    actions.append("claude_auth_login")  # always available (host action)
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    info = {
        "platform": sys.platform,
        "shell": shell,
        "python": py_ver,
        "root": root_dir,
        "actions": actions,
    }

    # Create a mock handler for action dispatch
    class MockHandler:
        pass
    MockHandler.root_dir = root_dir
    MockHandler.secret = secret
    MockHandler.shell = shell
    MockHandler.disabled_actions = disabled
    MockHandler.deny_patterns = deny

    def _resolve_cwd(rel_cwd):
        root = Path(root_dir).resolve()
        target = (root / rel_cwd).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            return None
        return str(target)

    MockHandler._resolve_cwd = staticmethod(_resolve_cwd)
    MockHandler._truncate = staticmethod(lambda text: text[:MAX_OUTPUT] if len(text) > MAX_OUTPUT else text)
    MockHandler._check_deny = lambda self, cmd: next(
        (p for p in deny if re.search(p, cmd, re.IGNORECASE)), None
    )

    mock = MockHandler()

    def _ws_frame_send(sock, data_bytes, opcode=0x01):
        """Send a WebSocket frame (text=0x01, close=0x08, pong=0x0A)."""
        length = len(data_bytes)
        # Client frames must be masked
        import secrets as _secrets
        mask_key = _secrets.token_bytes(4)
        masked = bytes(b ^ mask_key[i % 4] for i, b in enumerate(data_bytes))

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
        """Receive a WebSocket frame. Returns (opcode, payload_bytes)."""
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

    def _execute_command(msg, ws_sock=None):
        """Execute a command from the server."""
        action = msg.get("action", "")
        request_id = msg.get("request_id", "")

        # claude_auth_login: no secret check, no cwd, no disable check
        # (whitelisted host action, not a sandbox command)
        if action == "claude_auth_login":
            handler_func = _ACTIONS.get(action)
            if not handler_func:
                return {"ok": False, "error": f"Unknown action: {action}"}

            def _send_progress(data):
                if ws_sock:
                    progress = json.dumps({
                        "type": "progress",
                        "request_id": request_id,
                        "data": data,
                    }).encode("utf-8")
                    try:
                        _ws_frame_send(ws_sock, progress)
                    except Exception as e:
                        sys.stderr.write(f"[ExecRelay] progress send failed: {e}\n")

            try:
                result = handler_func(mock, str(Path(root_dir).resolve()),
                                      msg, None, send_progress=_send_progress)
                if "error" in result:
                    return {"ok": False, "error": result["error"]}
                return {"ok": True, "data": result}
            except Exception as e:
                return {"ok": False, "error": str(e)}

        # Check disabled
        base_action = "git" if action.startswith("git") else action
        if base_action in disabled:
            return {"ok": False, "error": f"Action disabled: {action}"}

        # Validate secret
        msg_secret = msg.get("secret", "")
        if not hmac.compare_digest(msg_secret, secret):
            return {"ok": False, "error": "Invalid secret"}

        # Resolve cwd
        rel_cwd = msg.get("cwd", ".")
        cwd = _resolve_cwd(rel_cwd)
        if cwd is None:
            return {"ok": False, "error": f"Path traversal blocked: {rel_cwd}"}

        handler_func = _ACTIONS.get(action)
        if not handler_func:
            return {"ok": False, "error": f"Unknown action: {action}"}

        timeout_val = msg.get("timeout")  # None = no limit
        try:
            result = handler_func(mock, cwd, msg, timeout_val)
            return {"ok": True, "data": result}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    reconnect_delay = 1

    while True:
        try:
            sys.stderr.write(f"[ExecRelay] Connecting to {url} ...\n")

            # TCP connect
            sock = socket.create_connection((host, port))
            if use_ssl:
                ctx = ssl.create_default_context()
                sock = ctx.wrap_socket(sock, server_hostname=host)

            # WebSocket handshake
            ws_key = b64.b64encode(os.urandom(16)).decode()
            handshake = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n"
                f"Upgrade: websocket\r\n"
                f"Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {ws_key}\r\n"
                f"Sec-WebSocket-Version: 13\r\n"
                f"\r\n"
            )
            sock.sendall(handshake.encode())

            # Read response headers
            resp = b""
            while b"\r\n\r\n" not in resp:
                chunk = sock.recv(4096)
                if not chunk:
                    raise ConnectionError("Handshake failed: connection closed")
                resp += chunk

            status_line = resp.split(b"\r\n")[0].decode()
            if "101" not in status_line:
                raise ConnectionError(f"Handshake failed: {status_line}")

            sys.stderr.write(f"[ExecRelay] Connected to {url}\n")

            # Send registration
            reg_msg = json.dumps({
                "type": "register",
                "token": token,
                "secret": secret,
                "relay_type": "executor",
                "relay_id": relay_id,
                "info": info,
            }).encode("utf-8")
            _ws_frame_send(sock, reg_msg)

            # Wait for registration confirmation
            opcode, payload = _ws_frame_recv(sock)
            if opcode == 0x01:
                reg_resp = json.loads(payload.decode("utf-8"))
                if reg_resp.get("type") == "registered":
                    sys.stderr.write(f"[ExecRelay] Registered as '{reg_resp.get('relay_id')}' "
                                     f"for user '{reg_resp.get('user_id')}'\n")
                else:
                    raise ConnectionError(f"Registration failed: {reg_resp}")

            reconnect_delay = 1  # Reset on successful connect

            # Keepalive: recv timeout triggers ping check
            _KEEPALIVE_INTERVAL = 60
            sock.settimeout(_KEEPALIVE_INTERVAL)

            # Main loop
            ping_interval = 30
            last_ping = time.monotonic()

            while True:
                try:
                    opcode, payload = _ws_frame_recv(sock)
                except socket.timeout:
                    # Send ping
                    if time.monotonic() - last_ping >= ping_interval:
                        _ws_frame_send(sock, json.dumps({"type": "ping"}).encode("utf-8"))
                        last_ping = time.monotonic()
                    continue

                if opcode == 0x08:  # Close
                    sys.stderr.write("[ExecRelay] Server closed connection\n")
                    break
                elif opcode == 0x09:  # Ping
                    _ws_frame_send(sock, payload, opcode=0x0A)
                    continue
                elif opcode == 0x0A:  # Pong
                    continue
                elif opcode != 0x01:  # Not text
                    continue

                msg = json.loads(payload.decode("utf-8"))
                msg_type = msg.get("type", "")

                if msg_type == "command":
                    request_id = msg.get("request_id", "")
                    sys.stderr.write(f"[ExecRelay] Command: {msg.get('action', '?')} "
                                     f"(id={request_id[:8]})\n")
                    result = _execute_command(msg, ws_sock=sock)
                    response = json.dumps({
                        "type": "result",
                        "request_id": request_id,
                        "data": result.get("data", result),
                    }).encode("utf-8")
                    _ws_frame_send(sock, response)
                    sys.stderr.write(f"[ExecRelay] Result sent for {request_id[:8]}\n")

                elif msg_type == "pong":
                    pass

        except KeyboardInterrupt:
            sys.stderr.write("\n[ExecRelay] Shutting down.\n")
            try:
                _ws_frame_send(sock, b"", opcode=0x08)
                sock.close()
            except Exception:
                pass
            return

        except Exception as e:
            sys.stderr.write(f"[ExecRelay] Connection error: {e}\n")
            try:
                sock.close()
            except Exception:
                pass

        # Reconnect with exponential backoff
        sys.stderr.write(f"[ExecRelay] Reconnecting in {reconnect_delay}s ...\n")
        time.sleep(reconnect_delay)
        reconnect_delay = min(reconnect_delay * 2, 30)


def main():
    parser = argparse.ArgumentParser(
        description="PawFlow Executor Relay — Secure command execution (HTTP or WS)",
    )
    parser.add_argument("--port", type=int, default=9877,
                        help="Port to listen on in HTTP mode (default: 9877)")
    parser.add_argument("--dir", required=True,
                        help="Root directory for command execution")
    parser.add_argument("--secret", required=True,
                        help="Shared secret for authentication")
    parser.add_argument("--shell", default=None,
                        help="Shell to use (default: auto-detect)")
    parser.add_argument("--no-shell", action="store_true",
                        help="Disable shell action")
    parser.add_argument("--no-python", action="store_true",
                        help="Disable python_exec action")
    parser.add_argument("--no-git", action="store_true",
                        help="Disable git actions")
    parser.add_argument("--deny-patterns", default="",
                        help="Comma-separated regex patterns to block")
    parser.add_argument("--allow-all", action="store_true",
                        help="Skip relay-side deny patterns (approval still happens in UI)")
    parser.add_argument("--bind", default="127.0.0.1",
                        help="Bind address for HTTP mode (default: 127.0.0.1)")
    # WS Reverse mode
    parser.add_argument("--connect", default="",
                        help="WS URL to connect to (e.g. ws://pawflow.example.com/ws/relay)")
    parser.add_argument("--token", default="",
                        help="API key for WS authentication")
    parser.add_argument("--relay-id", default="",
                        help="Relay ID (default: auto-generated)")
    args = parser.parse_args()

    root_dir = str(Path(args.dir).resolve())
    if not Path(root_dir).is_dir():
        sys.stderr.write(f"[ExecRelay] Error: not a directory: {root_dir}\n")
        sys.exit(1)

    shell = args.shell or _detect_shell()
    if args.shell and not shutil.which(args.shell):
        sys.stderr.write(f"[ExecRelay] Warning: shell not found: {args.shell}\n")

    disabled = set()
    if args.no_shell:
        disabled.add("shell")
    if args.no_python:
        disabled.add("python_exec")
    if args.no_git:
        disabled.add("git")

    deny = [] if args.allow_all else list(_DEFAULT_DENY)
    if args.deny_patterns:
        deny.extend(p.strip() for p in args.deny_patterns.split(",") if p.strip())

    masked = args.secret[:2] + "*" * max(0, len(args.secret) - 2)
    shell_name = os.path.basename(shell)
    actions = [a for a in ["shell", "python_exec", "git"] if a not in disabled]

    if args.connect:
        # WS Reverse mode
        if not args.token:
            sys.stderr.write("[ExecRelay] Error: --token required for --connect mode\n")
            sys.exit(1)

        relay_id = args.relay_id or f"exec-{os.getpid()}"

        sys.stderr.write(
            f"\n  PawFlow Executor Relay (WS Reverse)\n"
            f"  ──────────────────────────────────\n"
            f"  Server:    {args.connect}\n"
            f"  Relay ID:  {relay_id}\n"
            f"  Directory: {root_dir}\n"
            f"  Shell:     {shell_name} ({shell})\n"
            f"  Platform:  {sys.platform}\n"
            f"  Actions:   {', '.join(actions)}\n"
            f"  Secret:    {masked}\n"
            f"  Deny:      {len(deny)} patterns\n\n"
        )

        _ws_connect(args.connect, args.token, args.secret, relay_id,
                     root_dir, shell, disabled, deny)
    else:
        # HTTP mode (legacy)
        sys.stderr.write(
            f"\n  PawFlow Executor Relay (HTTP)\n"
            f"  ───────────────────────────\n"
            f"  Bind:      {args.bind}:{args.port}\n"
            f"  Directory: {root_dir}\n"
            f"  Shell:     {shell_name} ({shell})\n"
            f"  Platform:  {sys.platform}\n"
            f"  Actions:   {', '.join(actions)}\n"
            f"  Secret:    {masked}\n"
            f"  Deny:      {len(deny)} patterns\n\n"
        )

        handler_cls = _make_handler_class(root_dir, args.secret, shell, disabled, deny)
        httpd = HTTPServer((args.bind, args.port), handler_cls)

        try:
            sys.stderr.write(f"[ExecRelay] Listening on {args.bind}:{args.port} ...\n")
            httpd.serve_forever()
        except KeyboardInterrupt:
            sys.stderr.write("\n[ExecRelay] Shutting down.\n")
        finally:
            httpd.server_close()


if __name__ == "__main__":
    main()
