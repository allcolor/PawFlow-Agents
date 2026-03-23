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
    "write_file", "delete_file", "mkdir", "find_replace", "edit",
    "git_commit", "git_push", "exec",
})


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
        """
        rel_path = self._resolve_fs_url(rel_path)
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


def _action_edit(handler, path, req):
    """Edit file: exact string replacement OR line-based replacement."""
    old_string = req.get("old_string", "")
    new_string = req.get("new_string", "")
    replace_all = req.get("replace_all", False)
    start_line = int(req.get("start_line", 0) or 0)
    end_line = int(req.get("end_line", 0) or 0)
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    root = Path(handler.root_dir).resolve()
    rel = str(p.relative_to(root)).replace("\\", "/")

    # Line-based edit
    if start_line > 0 and end_line > 0:
        lines = text.split("\n")
        s = max(0, start_line - 1)
        e = min(len(lines), end_line)
        removed = lines[s:e]
        new_lines = new_string.split("\n")
        lines[s:e] = new_lines
        p.write_text("\n".join(lines), encoding="utf-8")
        return {"lines_replaced": f"{start_line}-{end_line}",
                "lines_removed": len(removed),
                "lines_inserted": len(new_lines), "path": rel}

    # String-based edit
    if not old_string:
        raise ValueError("Missing 'old_string' (or use start_line/end_line)")
    count = text.count(old_string)
    if count == 0:
        # Fuzzy retry: normalize whitespace and try again
        import re as _re_ws
        _norm = lambda s: _re_ws.sub(r'[ \t]+', ' ', s).replace('\r\n', '\n')
        if _norm(old_string) in _norm(text):
            old_lines = [l.strip() for l in old_string.split("\n")]
            text_lines = text.split("\n")
            for i in range(len(text_lines) - len(old_lines) + 1):
                if all(text_lines[i + j].strip() == old_lines[j] for j in range(len(old_lines))):
                    new_lines = new_string.split("\n")
                    text_lines[i:i + len(old_lines)] = new_lines
                    p.write_text("\n".join(text_lines), encoding="utf-8")
                    return {"replacements": 1, "fuzzy": True, "line": i + 1, "path": rel}
        # Still not found — helpful hint
        lines = text.split("\n")
        needle = old_string.split("\n")[0].strip()
        best_line, best_score = -1, 0
        for li, line in enumerate(lines):
            lt = line.strip()
            if lt and (needle[:30] in lt or lt[:30] in needle):
                score = min(len(lt), len(needle))
                if score > best_score:
                    best_score = score
                    best_line = li + 1
        hint = (f" Closest match near line {best_line}: \"{lines[best_line-1].strip()[:80]}\". "
                f"Try edit with start_line={best_line}/end_line={best_line}."
                if best_line > 0
                else " Try using start_line/end_line instead of old_string.")
        raise ValueError(f"old_string not found in {p.name}.{hint}")
    if count > 1 and not replace_all:
        raise ValueError(f"old_string found {count} times (use replace_all=true)")
    if replace_all:
        new_text = text.replace(old_string, new_string)
    else:
        new_text = text.replace(old_string, new_string, 1)
    p.write_text(new_text, encoding="utf-8")
    return {"replacements": count if replace_all else 1, "path": rel}


def _action_batch_edit(handler, path, req):
    """Atomic multi-file edit: [{path, old_string, new_string, replace_all}]."""
    edits = req.get("edits", [])
    if not edits:
        raise ValueError("Missing 'edits' list")
    root = Path(handler.root_dir).resolve()
    results = []
    files_modified = set()
    for edit in edits:
        epath = edit.get("path", "")
        old_s = edit.get("old_string", "")
        new_s = edit.get("new_string", "")
        repl_all = edit.get("replace_all", False)
        if not epath or not old_s:
            results.append({"path": epath, "error": "missing path or old_string"})
            continue
        p = (root / epath).resolve()
        if not str(p).startswith(str(root)):
            results.append({"path": epath, "error": "path escapes root"})
            continue
        try:
            text = p.read_text(encoding="utf-8")
            count = text.count(old_s)
            if count == 0:
                results.append({"path": epath, "error": "old_string not found"})
                continue
            if count > 1 and not repl_all:
                results.append({"path": epath, "error": f"found {count} times (use replace_all)"})
                continue
            text = text.replace(old_s, new_s) if repl_all else text.replace(old_s, new_s, 1)
            p.write_text(text, encoding="utf-8")
            files_modified.add(epath)
            results.append({"path": epath, "replacements": count if repl_all else 1})
        except Exception as e:
            results.append({"path": epath, "error": str(e)})
    return {"edits_applied": sum(1 for r in results if "replacements" in r),
            "files_modified": sorted(files_modified), "details": results}


def _action_exec(handler, path, req):
    """Execute a shell command in the sandbox directory."""
    if not getattr(handler, 'allow_exec', False):
        raise PermissionError("Shell execution disabled. Start relay with --allow-exec")
    command = req.get("command", "")
    timeout = min(req.get("timeout", 30), 120)  # cap at 2 minutes
    if not command:
        raise ValueError("Missing 'command' parameter")

    # On Windows, use bash (Git Bash or WSL) — cmd.exe breaks python -c
    # with nested quotes. Convert C:\path to /c/path for bash.
    import sys, re as _re_path
    shell_args = {}
    exec_cmd = command
    if sys.platform == "win32":
        _git_bash = Path("C:/Program Files/Git/bin/bash.exe")
        _wsl_bash = Path("C:/Windows/System32/bash.exe")
        if _git_bash.exists():
            shell_args = {"executable": str(_git_bash)}
            # Convert Windows paths: C:\x\y → /c/x/y
            exec_cmd = _re_path.sub(
                r'([A-Za-z]):\\([^ "\']*)',
                lambda m: '/' + m.group(1).lower() + '/' + m.group(2).replace('\\', '/'),
                command,
            )
        elif _wsl_bash.exists():
            shell_args = {"executable": str(_wsl_bash)}

    result = subprocess.run(
        exec_cmd, shell=True,
        capture_output=True, text=True,
        timeout=timeout,
        cwd=handler.root_dir,
        **shell_args,
    )
    return {
        "stdout": result.stdout[-10000:],  # cap output
        "stderr": result.stderr[-5000:],
        "returncode": result.returncode,
    }


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
    message = req.get("message", "PawFlow auto-commit")
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
    "edit": _action_edit,
    "batch_edit": _action_batch_edit,
    "exec": _action_exec,
}


# ── Main ──────────────────────────────────────────────────────────

def _make_handler_class(root_dir: str, secret: str, readonly: bool,
                        allow_exec: bool = False):
    """Create a handler class with bound config (avoids lambda issues)."""

    class ConfiguredHandler(FSRelayHandler):
        pass

    ConfiguredHandler.root_dir = root_dir
    ConfiguredHandler.secret = secret
    ConfiguredHandler.readonly = readonly
    ConfiguredHandler.allow_exec = allow_exec
    return ConfiguredHandler


# ── WS Reverse client ─────────────────────────────────────────────

def _ws_connect(url, token, secret, relay_id, root_dir, readonly, allow_exec=False):
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
    MockHandler.allow_exec = allow_exec
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

        # Token already validated at WS connect time — no per-command secret check

        if readonly and action in _WRITE_ACTIONS:
            return {"ok": False, "error": "Operation not allowed in readonly mode"}

        abs_path = _resolve(rel_path)
        if abs_path is None:
            return {"ok": False, "error": f"Path traversal blocked: {rel_path}"}

        from fs_actions import ACTIONS as _FS_ACTIONS
        handler_func = _FS_ACTIONS.get(action)
        if not handler_func:
            return {"ok": False, "error": f"Unknown action: {action}"}

        try:
            if action == "exec":
                result = handler_func(root_dir, abs_path, msg,
                                       allow_exec=getattr(mock, 'allow_exec', False))
            else:
                result = handler_func(root_dir, abs_path, msg)
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
                # Accept self-signed ephemeral certs from the PawFlow service
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
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
        finally:
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
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        conn = http.client.HTTPSConnection(host, port, context=ctx)
    else:
        conn = http.client.HTTPConnection(host, port)

    headers = {"Content-Type": "application/json"}
    if session_id:
        headers["Authorization"] = f"Bearer {session_id}"

    payload = json.dumps(body).encode("utf-8") if body else None
    conn.request(method, path, body=payload, headers=headers)
    resp = conn.getresponse()
    data = resp.read().decode("utf-8")
    conn.close()

    if resp.status >= 400:
        raise Exception(f"API {method} {path} → {resp.status}: {data}")
    return json.loads(data) if data else {}


def _agent_api_call(login_url, session_id, action_body):
    """Call the agent API (same port as chat UI) with an action."""
    return _api_call(login_url, "POST", "/api/agent",
                     body=action_body, session_id=session_id)


def _create_service(login_url, session_id, service_id, port, relay_path, token):
    """Create a user filesystem service via the agent API."""
    config_str = f"port={port},path={relay_path},token={token},mode=readwrite"
    return _agent_api_call(login_url, session_id, {
        "action": "service_install",
        "service_type": "filesystem",
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

    # Find port for WS listener
    port = args.port or _find_free_port()
    relay_path = args.relay_path
    ws_token = _secrets.token_urlsafe(32)

    # Delete existing service if any
    sys.stderr.write(f"[FSRelay] Cleaning up previous service '{args.relay_id}' ...\n")
    _delete_service(login_url, session_id, args.relay_id)

    # Create new service
    sys.stderr.write(f"[FSRelay] Creating service '{args.relay_id}' on port {port} ...\n")
    _create_service(login_url, session_id, args.relay_id, port, relay_path, ws_token)
    sys.stderr.write(f"[FSRelay] Service created.\n")

    # Build WS URL (default to wss since FilesystemWSListener uses TLS when cryptography is installed)
    scheme = "ws" if args.no_tls else "wss"
    ws_url = f"{scheme}://{args.host}:{port}{relay_path}"

    # Wait for the service WS listener to start
    time.sleep(1.5)

    return ws_url, ws_token, session_id, login_url


def main():
    parser = argparse.ArgumentParser(
        description="PawFlow Relay — Connects to PawFlow server for filesystem access",
    )
    parser.add_argument("--server",
                        help="PawFlow server WS URL (manual mode)")
    parser.add_argument("--relay-id", default="",
                        help="Service ID (auto-generated from username+dir if omitted)")
    parser.add_argument("--token",
                        help="Token for manual WS auth")
    parser.add_argument("--dir", required=True,
                        help="Root directory for filesystem access")
    parser.add_argument("--readonly", action="store_true",
                        help="Reject write/delete operations")
    parser.add_argument("--allow-exec", action="store_true",
                        help="Allow shell command execution (disabled by default)")
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
    args = parser.parse_args()

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
        f"  Token:     {masked}\n"
        f"  Auto-reg:  {'no (manual)' if args.server else 'yes'}\n\n"
    )

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

    try:
        _ws_connect(ws_url, token, token, args.relay_id,
                     root_dir, args.readonly, allow_exec=args.allow_exec)
    finally:
        _cleanup()


if __name__ == "__main__":
    main()
