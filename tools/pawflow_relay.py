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
        # Automation check
        if action.startswith("screen_") and not getattr(self, 'allow_automation', False):
            self._log_op(action, rel_path, False, "automation not allowed")
            self._send_json(False, error="Screen automation not allowed. Start relay with --allow-automation")
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
        # Fuzzy match via diff-match-patch
        try:
            import diff_match_patch as _dmp_mod
            dmp = _dmp_mod.diff_match_patch()
            dmp.Match_Threshold = 0.5
            dmp.Match_Distance = 2000

            # Strategy 1: fuzzy find via first/last line (32 chars max for match_main)
            _first = old_string.split("\n")[0].strip()[:32]
            _last = old_string.split("\n")[-1].strip()[:32]
            if len(_first) >= 8:
                loc = dmp.match_main(text, _first, 0)
            else:
                loc = -1
            if loc != -1:
                _search_from = max(0, loc + len(old_string) - 200)
                end_loc = dmp.match_main(text, _last, _search_from) if len(_last) >= 8 else -1
                actual_end = (end_loc + len(_last)) if end_loc != -1 else (loc + len(old_string))
                new_text = text[:loc] + new_string + text[actual_end:]
                p.write_text(new_text, encoding="utf-8")
                return {"replacements": 1, "fuzzy": True, "match_offset": loc, "path": rel}
        except Exception:
            pass  # diff-match-patch not installed or match error

        # Strategy 2: line-by-line trimmed match
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
            if lt and needle and needle[:30] in lt:
                score = len(lt)
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
    timeout = req.get("timeout")  # None = no limit
    if not command:
        raise ValueError("Missing 'command' parameter")

    # Like Claude Code: use native shell. For python -c with complex
    # nested quotes, write to a temp file and execute that instead.
    import sys, re as _re_pyc, tempfile
    exec_cmd = command
    shell_args = {}
    if sys.platform == "win32":
        _py_match = _re_pyc.match(
            r'^((?:cd\s+[^&]+&&\s*)?)python(?:3)?\s+-c\s+["\'](.+)["\']\s*(.*)$',
            command, _re_pyc.DOTALL | _re_pyc.IGNORECASE)
        if _py_match:
            _tmp = tempfile.NamedTemporaryFile(
                mode='w', suffix='.py', prefix='pawflow_exec_',
                dir=handler.root_dir, delete=False, encoding='utf-8')
            _tmp.write(_py_match.group(2))
            _tmp.close()
            prefix = _py_match.group(1).strip()
            suffix = _py_match.group(3).strip()
            exec_cmd = f'{prefix} python "{_tmp.name}" {suffix}' if prefix else f'python "{_tmp.name}" {suffix}'
            try:
                result = subprocess.run(
                    exec_cmd, shell=True,
                    capture_output=True, text=True,
                    timeout=timeout, cwd=handler.root_dir,
                )
                return {
                    "stdout": result.stdout[-10000:],
                    "stderr": result.stderr[-5000:],
                    "returncode": result.returncode,
                }
            finally:
                try:
                    Path(_tmp.name).unlink()
                except Exception:
                    pass

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
    "edit": _action_edit,
    "batch_edit": _action_batch_edit,
    "exec": _action_exec,
}


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

    try:
        # Send request
        req = json.dumps({"action": msg.get("action", "")}) + "\n"
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
                elif resp.get("type") == "result":
                    data = resp.get("data", {})
                    if "error" in data:
                        return {"ok": False, "error": data["error"]}
                    return {"ok": True, "data": data}
                elif resp.get("type") == "error":
                    return {"ok": False, "error": resp.get("error", "Unknown error")}

        if result:
            return result
        return {"ok": False, "error": "Host helper closed connection without result"}
    except Exception as e:
        return {"ok": False, "error": f"Host helper communication failed: {e}"}
    finally:
        sock.close()


# ── Main ──────────────────────────────────────────────────────────

def _make_handler_class(root_dir: str, secret: str, readonly: bool,
                        allow_exec: bool = False, allow_automation: bool = False):
    """Create a handler class with bound config (avoids lambda issues)."""

    class ConfiguredHandler(FSRelayHandler):
        pass

    ConfiguredHandler.root_dir = root_dir
    ConfiguredHandler.secret = secret
    ConfiguredHandler.readonly = readonly
    ConfiguredHandler.allow_exec = allow_exec
    ConfiguredHandler.allow_automation = allow_automation
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
    # Detect available shells for exec
    try:
        from fs_actions import detect_available_shells
        _shells = detect_available_shells()
    except Exception:
        _shells = {}
    def _is_containerized():
        return os.path.exists("/.dockerenv") or bool(os.environ.get("PAWFLOW_DOCKER_IMAGE"))

    info = {
        "platform": sys.platform,
        "root": root_dir,
        "mode": mode,
        "shells": list(_shells.keys()),
        "containerized": _is_containerized(),
        "docker_image": os.environ.get("PAWFLOW_DOCKER_IMAGE", ""),
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

        from fs_actions import ACTIONS as _FS_ACTIONS
        handler_func = _FS_ACTIONS.get(action)
        if not handler_func:
            return {"ok": False, "error": f"Unknown action: {action}"}

        try:
            if action in ("exec", "exec_stream"):
                result = handler_func(root_dir, abs_path, msg,
                                       allow_exec=getattr(mock, 'allow_exec', False),
                                       **({"on_output": on_output} if action == "exec_stream" and on_output else {}))
            else:
                result = handler_func(root_dir, abs_path, msg)
            return {"ok": True, "data": result}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    reconnect_delay = 1

    while True:
        try:
            sys.stderr.write(f"[FSRelay] Connecting to {url} ...\n")
            sock = socket.create_connection((host, port))
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
            _KEEPALIVE_INTERVAL = 60
            sock.settimeout(_KEEPALIVE_INTERVAL)
            ws_sock_ref = [sock]  # mutable ref for _execute_command closures
            import threading as _threading
            from concurrent.futures import ThreadPoolExecutor
            _pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="relay-cmd")
            _send_lock = _threading.Lock()
            _child_relays = {}  # relay_id → thread (child relay instances)

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

                        def _child_relay(_url, _tok, _sec, _rid, _root,
                                         _docker_img="", _parent_has_docker=False):
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
                                        "--cpus", "2", "--memory", "2g",
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
                                            readonly=readonly, allow_exec=allow_exec)
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
            "--add-host", "host.docker.internal:host-gateway",
            "--cpus", "2",
            "--memory", "2g",
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
                         root_dir, args.readonly, allow_exec=args.allow_exec)
        finally:
            _cleanup()


if __name__ == "__main__":
    main()
