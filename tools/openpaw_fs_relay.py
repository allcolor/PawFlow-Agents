#!/usr/bin/env python3
"""OpenPaw Filesystem Relay — Standalone relay for filesystem access.

Runs on the user's machine to give the OpenPaw server secure access to the
user's filesystem. Zero external dependencies (stdlib only).

Two modes:
  HTTP (legacy, local only):
    python openpaw_fs_relay.py --port 9876 --dir /home/user/data --secret abc123

  WS Reverse (recommended, works across NAT/firewalls):
    python openpaw_fs_relay.py --connect ws://openpaw.example.com/ws/relay \
        --token <api_key> --secret abc --dir /home/user/data

Security:
- Shared secret validated via hmac.compare_digest on every request
- Path traversal prevention (resolve + startswith check)
- --readonly flag rejects write/delete operations (defense-in-depth)
- --bind 127.0.0.1 by default (local only, HTTP mode)
- All operations logged to stderr
"""

import argparse
import base64
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


# ── Actions that require write access ─────────────────────────────

_WRITE_ACTIONS = frozenset({
    "write_file", "delete_file", "mkdir", "find_replace",
    "git_commit", "git_push",
})


# ── Request handler ───────────────────────────────────────────────

class FSRelayHandler(BaseHTTPRequestHandler):
    """HTTP POST handler for filesystem relay operations."""

    server_version = "OpenPaw-FSRelay/1.0"

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

    def _resolve(self, rel_path: str):
        """Resolve relative path to absolute, checking traversal.

        Returns absolute path string or None if blocked.
        """
        root = Path(self.root_dir).resolve()
        # Join with root, then resolve to handle .. etc.
        target = (root / rel_path).resolve()
        # Must be under root
        try:
            target.relative_to(root)
        except ValueError:
            return None
        return str(target)

    # ── HTTP verbs ────────────────────────────────────────────────

    def do_GET(self):
        self._send_json(True, data={"service": "OpenPaw-FSRelay", "version": "1.0"})

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
        rel_path = req.get("path", ".")

        # Readonly check
        if self.readonly and action in _WRITE_ACTIONS:
            self._log_op(action, rel_path, False, "readonly mode")
            self._send_json(False, error="Operation not allowed in readonly mode")
            return

        # Resolve path
        abs_path = self._resolve(rel_path)
        if abs_path is None:
            self._log_op(action, rel_path, False, "path traversal blocked")
            self._send_json(False, error=f"Path traversal blocked: {rel_path}")
            return

        # Dispatch
        handler = _ACTIONS.get(action)
        if not handler:
            self._log_op(action, rel_path, False, "unknown action")
            self._send_json(False, error=f"Unknown action: {action}")
            return

        try:
            result = handler(self, abs_path, req)
            self._log_op(action, rel_path, True)
            self._send_json(True, data=result)
        except Exception as e:
            self._log_op(action, rel_path, False, str(e))
            self._send_json(False, error=str(e))


# ── Action implementations ────────────────────────────────────────

def _rel(abs_path: str, root: str) -> str:
    """Convert absolute path back to relative for responses."""
    try:
        return str(Path(abs_path).relative_to(Path(root).resolve()))
    except ValueError:
        return abs_path


def _action_list_dir(handler, path, req):
    p = Path(path)
    root = Path(handler.root_dir).resolve()
    entries = []
    for entry in sorted(p.iterdir()):
        st = entry.stat()
        entries.append({
            "name": entry.name,
            "kind": "directory" if entry.is_dir() else "file",
            "size": st.st_size if entry.is_file() else 0,
            "modified": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
        })
    return entries


def _action_read_file(handler, path, req):
    content = Path(path).read_bytes()
    return {"content": base64.b64encode(content).decode("ascii"), "size": len(content)}


def _action_write_file(handler, path, req):
    content_b64 = req.get("content", "")
    content = base64.b64decode(content_b64)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    return {"size": len(content)}


def _action_delete_file(handler, path, req):
    p = Path(path)
    if p.is_file():
        p.unlink()
    elif p.is_dir():
        shutil.rmtree(p)
    else:
        raise FileNotFoundError(f"Not found: {path}")
    return {"deleted": True}


def _action_mkdir(handler, path, req):
    Path(path).mkdir(parents=True, exist_ok=True)
    return {"created": True}


def _action_stat(handler, path, req):
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Not found: {path}")
    st = p.stat()
    return {
        "name": p.name,
        "kind": "directory" if p.is_dir() else "file",
        "size": st.st_size if p.is_file() else 0,
        "modified": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
    }


def _action_exists(handler, path, req):
    return {"exists": Path(path).exists()}


def _action_search(handler, path, req):
    pattern = req.get("pattern", "*")
    recursive = req.get("recursive", True)
    p = Path(path)
    root = Path(handler.root_dir).resolve()
    matches = p.rglob(pattern) if recursive else p.glob(pattern)
    results = []
    for m in sorted(matches):
        try:
            rel = str(m.relative_to(root))
        except ValueError:
            rel = str(m)
        results.append(rel.replace("\\", "/"))
    return results


def _action_grep(handler, path, req):
    regex_str = req.get("regex", "")
    recursive = req.get("recursive", True)
    if not regex_str:
        raise ValueError("Missing 'regex' parameter")
    compiled = re.compile(regex_str)
    p = Path(path)
    root = Path(handler.root_dir).resolve()
    results = []
    files = p.rglob("*") if recursive else p.glob("*")
    for fp in sorted(files):
        if not fp.is_file():
            continue
        try:
            text = fp.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            m = compiled.search(line)
            if m:
                try:
                    rel = str(fp.relative_to(root))
                except ValueError:
                    rel = str(fp)
                results.append({
                    "path": rel.replace("\\", "/"),
                    "line_number": i,
                    "line": line,
                    "match": m.group(),
                })
    return results


def _action_find_replace(handler, path, req):
    pattern = req.get("pattern", "")
    replacement = req.get("replacement", "")
    if not pattern:
        raise ValueError("Missing 'pattern' parameter")
    compiled = re.compile(pattern)
    p = Path(path)
    text = p.read_text(encoding="utf-8", errors="replace")
    new_text, count = compiled.subn(replacement, text)
    if count > 0:
        p.write_text(new_text, encoding="utf-8")
    root = Path(handler.root_dir).resolve()
    try:
        rel = str(p.relative_to(root))
    except ValueError:
        rel = str(p)
    return {"replacements": count, "path": rel.replace("\\", "/")}


# ── Git actions ───────────────────────────────────────────────────

def _git_run(cwd, args, timeout=30):
    """Run a git command and return CompletedProcess."""
    return subprocess.run(
        ["git"] + args, cwd=cwd,
        capture_output=True, text=True, timeout=timeout,
    )


def _action_git_status(handler, path, req):
    # Get branch
    br = _git_run(path, ["branch", "--show-current"])
    branch = br.stdout.strip() or "HEAD"

    # Get porcelain status
    st = _git_run(path, ["status", "--porcelain"])
    staged, modified, untracked = [], [], []
    for line in st.stdout.splitlines():
        if len(line) < 3:
            continue
        x, y = line[0], line[1]
        name = line[3:]
        if x == "?":
            untracked.append(name)
        elif x != " " and x != "?":
            staged.append(name)
        if y != " " and y != "?":
            modified.append(name)

    return {
        "branch": branch,
        "clean": not staged and not modified and not untracked,
        "staged": staged,
        "modified": modified,
        "untracked": untracked,
    }


def _action_git_log(handler, path, req):
    count = req.get("count", 10)
    r = _git_run(path, [
        "log", f"-n{count}",
        "--pretty=format:%H%x00%an%x00%aI%x00%s",
    ])
    entries = []
    for line in r.stdout.splitlines():
        parts = line.split("\x00", 3)
        if len(parts) == 4:
            entries.append({
                "hash": parts[0], "author": parts[1],
                "date": parts[2], "message": parts[3],
            })
    return entries


def _action_git_diff(handler, path, req):
    ref = req.get("ref", "")
    cmd = ["diff", ref] if ref else ["diff"]
    r = _git_run(path, cmd)
    return r.stdout


def _action_git_commit(handler, path, req):
    message = req.get("message", "OpenPaw auto-commit")
    _git_run(path, ["add", "-A"])
    _git_run(path, ["commit", "-m", message])
    h = _git_run(path, ["rev-parse", "HEAD"])
    return {"hash": h.stdout.strip(), "message": message}


def _action_git_pull(handler, path, req):
    r = _git_run(path, ["pull"], timeout=60)
    return {
        "updated": r.returncode == 0,
        "conflicts": "conflict" in r.stdout.lower() or r.returncode != 0,
    }


def _action_git_push(handler, path, req):
    r = _git_run(path, ["push"], timeout=120)
    return {"pushed": r.returncode == 0, "remote": "origin"}


def _action_git_checkout(handler, path, req):
    ref = req.get("ref", "main")
    _git_run(path, ["checkout", ref])
    br = _git_run(path, ["branch", "--show-current"])
    return {"branch": br.stdout.strip() or ref}


# ── Action dispatch table ─────────────────────────────────────────

_ACTIONS = {
    "list_dir": _action_list_dir,
    "read_file": _action_read_file,
    "write_file": _action_write_file,
    "delete_file": _action_delete_file,
    "mkdir": _action_mkdir,
    "stat": _action_stat,
    "exists": _action_exists,
    "search": _action_search,
    "grep": _action_grep,
    "find_replace": _action_find_replace,
    "git_status": _action_git_status,
    "git_log": _action_git_log,
    "git_diff": _action_git_diff,
    "git_commit": _action_git_commit,
    "git_pull": _action_git_pull,
    "git_push": _action_git_push,
    "git_checkout": _action_git_checkout,
}


# ── Main ──────────────────────────────────────────────────────────

def _make_handler_class(root_dir: str, secret: str, readonly: bool):
    """Create a handler class with bound config (avoids lambda issues)."""

    class ConfiguredHandler(FSRelayHandler):
        pass

    ConfiguredHandler.root_dir = root_dir
    ConfiguredHandler.secret = secret
    ConfiguredHandler.readonly = readonly
    return ConfiguredHandler


# ── WS Reverse client ─────────────────────────────────────────────

def _ws_connect(url, token, secret, relay_id, root_dir, readonly):
    """Connect to the OpenPaw server via WebSocket and process filesystem commands."""
    import ssl
    import base64 as b64
    from urllib.parse import urlparse

    parsed = urlparse(url)
    use_ssl = parsed.scheme in ("wss", "https")
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if use_ssl else 80)
    path = parsed.path or "/ws/relay"

    mode = "read" if readonly else "readwrite"
    info = {
        "platform": sys.platform,
        "root": root_dir,
        "mode": mode,
    }

    def _resolve(rel_path):
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

    def _execute_command(msg):
        action = msg.get("action", "")
        rel_path = msg.get("path", ".")

        msg_secret = msg.get("secret", "")
        if not hmac.compare_digest(msg_secret, secret):
            return {"ok": False, "error": "Invalid secret"}

        if readonly and action in _WRITE_ACTIONS:
            return {"ok": False, "error": "Operation not allowed in readonly mode"}

        abs_path = _resolve(rel_path)
        if abs_path is None:
            return {"ok": False, "error": f"Path traversal blocked: {rel_path}"}

        handler_func = _ACTIONS.get(action)
        if not handler_func:
            return {"ok": False, "error": f"Unknown action: {action}"}

        try:
            result = handler_func(mock, abs_path, msg)
            return {"ok": True, "data": result}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    reconnect_delay = 1

    while True:
        try:
            sys.stderr.write(f"[FSRelay] Connecting to {url} ...\n")
            sock = socket.create_connection((host, port), timeout=30)
            if use_ssl:
                ctx = ssl.create_default_context()
                sock = ctx.wrap_socket(sock, server_hostname=host)

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

            resp = b""
            while b"\r\n\r\n" not in resp:
                chunk = sock.recv(4096)
                if not chunk:
                    raise ConnectionError("Handshake failed")
                resp += chunk

            if b"101" not in resp.split(b"\r\n")[0]:
                raise ConnectionError(f"Handshake failed: {resp.split(b'\\r\\n')[0]}")

            sys.stderr.write(f"[FSRelay] Connected to {url}\n")

            reg_msg = json.dumps({
                "type": "register",
                "token": token,
                "secret": secret,
                "relay_type": "filesystem",
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
            sock.settimeout(60)

            while True:
                try:
                    opcode, payload = _ws_frame_recv(sock)
                except socket.timeout:
                    _ws_frame_send(sock, json.dumps({"type": "ping"}).encode("utf-8"))
                    continue

                if opcode == 0x08:
                    break
                elif opcode == 0x09:
                    _ws_frame_send(sock, payload, opcode=0x0A)
                    continue
                elif opcode != 0x01:
                    continue

                msg = json.loads(payload.decode("utf-8"))
                if msg.get("type") == "command":
                    request_id = msg.get("request_id", "")
                    sys.stderr.write(f"[FSRelay] Command: {msg.get('action', '?')}\n")
                    result = _execute_command(msg)
                    response = json.dumps({
                        "type": "result",
                        "request_id": request_id,
                        "data": result.get("data", result),
                    }).encode("utf-8")
                    _ws_frame_send(sock, response)

        except KeyboardInterrupt:
            sys.stderr.write("\n[FSRelay] Shutting down.\n")
            try:
                sock.close()
            except Exception:
                pass
            return
        except Exception as e:
            sys.stderr.write(f"[FSRelay] Connection error: {e}\n")
            try:
                sock.close()
            except Exception:
                pass

        sys.stderr.write(f"[FSRelay] Reconnecting in {reconnect_delay}s ...\n")
        time.sleep(reconnect_delay)
        reconnect_delay = min(reconnect_delay * 2, 30)


def main():
    parser = argparse.ArgumentParser(
        description="OpenPaw Filesystem Relay — Secure filesystem access (HTTP or WS)",
    )
    parser.add_argument("--port", type=int, default=9876,
                        help="Port to listen on in HTTP mode (default: 9876)")
    parser.add_argument("--dir", required=True,
                        help="Root directory for filesystem access")
    parser.add_argument("--secret", required=True,
                        help="Shared secret for authentication")
    parser.add_argument("--readonly", action="store_true",
                        help="Reject write/delete operations")
    parser.add_argument("--bind", default="127.0.0.1",
                        help="Bind address for HTTP mode (default: 127.0.0.1)")
    # WS Reverse mode
    parser.add_argument("--connect", default="",
                        help="WS URL to connect to (e.g. ws://openpaw.example.com/ws/relay)")
    parser.add_argument("--token", default="",
                        help="API key for WS authentication")
    parser.add_argument("--relay-id", default="",
                        help="Relay ID (default: auto-generated)")
    args = parser.parse_args()

    root_dir = str(Path(args.dir).resolve())
    if not Path(root_dir).is_dir():
        sys.stderr.write(f"[FSRelay] Error: not a directory: {root_dir}\n")
        sys.exit(1)

    mode = "readonly" if args.readonly else "readwrite"
    masked = args.secret[:2] + "*" * max(0, len(args.secret) - 2)

    if args.connect:
        # WS Reverse mode
        if not args.token:
            sys.stderr.write("[FSRelay] Error: --token required for --connect mode\n")
            sys.exit(1)

        relay_id = args.relay_id or f"fs-{os.getpid()}"

        sys.stderr.write(
            f"\n  OpenPaw Filesystem Relay (WS Reverse)\n"
            f"  ────────────────────────────────────\n"
            f"  Server:    {args.connect}\n"
            f"  Relay ID:  {relay_id}\n"
            f"  Directory: {root_dir}\n"
            f"  Mode:      {mode}\n"
            f"  Secret:    {masked}\n\n"
        )

        _ws_connect(args.connect, args.token, args.secret, relay_id,
                     root_dir, args.readonly)
    else:
        # HTTP mode (legacy)
        sys.stderr.write(
            f"\n  OpenPaw Filesystem Relay (HTTP)\n"
            f"  ─────────────────────────────\n"
            f"  Bind:      {args.bind}:{args.port}\n"
            f"  Directory: {root_dir}\n"
            f"  Mode:      {mode}\n"
            f"  Secret:    {masked}\n\n"
        )

        handler_cls = _make_handler_class(root_dir, args.secret, args.readonly)
        httpd = HTTPServer((args.bind, args.port), handler_cls)

        try:
            sys.stderr.write(f"[FSRelay] Listening on {args.bind}:{args.port} ...\n")
            httpd.serve_forever()
        except KeyboardInterrupt:
            sys.stderr.write("\n[FSRelay] Shutting down.\n")
        finally:
            httpd.server_close()


if __name__ == "__main__":
    main()
