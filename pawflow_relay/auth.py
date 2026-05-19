"""PawFlow relay — host-side CLI authentication + host-helper bridge.

Exported:
    find_claude_binary()
    claude_auth_login(req, *, send_progress=None)
    find_codex_binary()
    codex_auth_login(req, *, send_progress=None)
    find_gemini_binary()
    gemini_auth_login(req, *, send_progress=None)
    forward_to_host_helper(host_helper, msg, ws_sock, ws_send_fn)

All stdlib-only so the module loads inside the relay container.
"""
import logging

import json
import os
import re
import shutil
import subprocess  # nosec B404
import sys
import threading
import time


def find_claude_binary():
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
    for p in candidates:
        if os.path.isfile(p):
            return p
    found = shutil.which("claude")
    if found:
        return found
    return None


def claude_auth_login(req, *, send_progress=None):
    """Launch `claude auth login` on the host, intercept URL, return credentials.

    Streams the auth URL via send_progress({"url": ...}) as soon as it's
    printed on stdout, then waits for the user to complete the flow and
    returns the resulting credentials dict (or {"error": ...}).
    """
    claude_path = find_claude_binary()
    if not claude_path:
        return {"error": "Claude binary not found. Install Claude Code first: "
                         "npm install -g @anthropic-ai/claude-code"}

    sys.stderr.write(f"[Relay] claude auth login: {claude_path}\n")
    _launch_time = time.time()

    try:
        proc = subprocess.Popen(  # nosec B603
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
            sys.stderr.write("[Relay] Auth URL found\n")
            if send_progress:
                send_progress({"url": url_found})

    proc.wait()
    sys.stderr.write(f"[Relay] claude auth login exited: {proc.returncode}\n")

    if proc.returncode != 0 and not url_found:
        output = "\n".join(all_output[-10:])
        return {"error": f"claude auth login failed (exit {proc.returncode}):\n{output}"}

    if sys.platform == "win32":
        creds_path = os.path.join(
            os.environ.get("USERPROFILE", os.environ.get("HOME", "")),
            ".claude", ".credentials.json")
    else:
        creds_path = os.path.expanduser("~/.claude/.credentials.json")

    if not os.path.exists(creds_path):
        return {"error": f"Credentials file not found: {creds_path}"}

    # Wait up to 3 min for the credentials file to be rewritten after auth.
    _max_wait = 180
    _waited = 0
    while _waited < _max_wait:
        try:
            if os.path.getmtime(creds_path) >= _launch_time:
                break
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
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


def find_codex_binary():
    """Find the codex binary in known installation locations."""
    if sys.platform == "win32":
        home = os.environ.get("USERPROFILE", os.environ.get("HOME", ""))
        candidates = [
            os.path.join(home, ".local", "bin", "codex.exe"),
            os.path.join(home, "AppData", "Roaming", "npm", "codex.cmd"),
            os.path.join(home, "AppData", "Roaming", "npm", "codex"),
            os.path.join(home, ".npm-global", "bin", "codex.cmd"),
        ]
    else:
        home = os.path.expanduser("~")
        candidates = [
            os.path.join(home, ".local", "bin", "codex"),
            os.path.join(home, ".npm-global", "bin", "codex"),
            "/usr/local/bin/codex",
            "/usr/bin/codex",
        ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    found = shutil.which("codex")
    if found:
        return found
    return None


def codex_auth_login(req, *, send_progress=None):
    r"""Launch `codex login` on the host, intercept URL, return ~/.codex/auth.json.

    Mirror of claude_auth_login but for codex CLI. The OAuth PKCE URL on
    auth.openai.com matches /^https:\/\/auth\.openai\.com\/.*$/, captured
    from stdout and forwarded via send_progress so the user can open it.
    Once the flow completes, ~/.codex/auth.json is read and returned.
    """
    codex_path = find_codex_binary()
    if not codex_path:
        return {"error": "Codex binary not found. Install Codex CLI first: "
                         "npm install -g @openai/codex"}

    sys.stderr.write(f"[Relay] codex login: {codex_path}\n")
    _launch_time = time.time()

    try:
        proc = subprocess.Popen(  # nosec B603
            [codex_path, "login"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True,
        )
    except FileNotFoundError:
        return {"error": f"Codex binary not found: {codex_path}"}
    except Exception as e:
        return {"error": f"Failed to start codex: {e}"}

    url_pattern = re.compile(r'https://auth\.openai\.com\S+')
    url_found = None
    all_output = []

    for line in proc.stdout:
        line = line.rstrip()
        all_output.append(line)
        sys.stderr.write(f"[Relay] codex> {line}\n")
        m = url_pattern.search(line)
        if m and not url_found:
            url_found = m.group(0)
            sys.stderr.write("[Relay] Codex auth URL found\n")
            if send_progress:
                send_progress({"url": url_found})

    proc.wait()
    sys.stderr.write(f"[Relay] codex login exited: {proc.returncode}\n")

    if proc.returncode != 0 and not url_found:
        output = "\n".join(all_output[-10:])
        return {"error": f"codex login failed (exit {proc.returncode}):\n{output}"}

    if sys.platform == "win32":
        creds_path = os.path.join(
            os.environ.get("USERPROFILE", os.environ.get("HOME", "")),
            ".codex", "auth.json")
    else:
        creds_path = os.path.expanduser("~/.codex/auth.json")

    if not os.path.exists(creds_path):
        return {"error": f"Codex credentials file not found: {creds_path}"}

    _max_wait = 180
    _waited = 0
    while _waited < _max_wait:
        try:
            if os.path.getmtime(creds_path) >= _launch_time:
                break
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        time.sleep(1)
        _waited += 1

    if _waited >= _max_wait:
        return {"error": "Timeout: codex auth.json was not updated after authorization"}

    try:
        with open(creds_path, "r", encoding="utf-8") as f:
            credentials = json.load(f)
    except Exception as e:
        return {"error": f"Failed to read codex credentials: {e}"}

    return {"credentials": credentials}


def find_gemini_binary():
    """Find the gemini binary in known installation locations."""
    if sys.platform == "win32":
        home = os.environ.get("USERPROFILE", os.environ.get("HOME", ""))
        candidates = [
            os.path.join(home, ".local", "bin", "gemini.exe"),
            os.path.join(home, "AppData", "Roaming", "npm", "gemini.cmd"),
            os.path.join(home, "AppData", "Roaming", "npm", "gemini"),
            os.path.join(home, ".npm-global", "bin", "gemini.cmd"),
        ]
    else:
        home = os.path.expanduser("~")
        candidates = [
            os.path.join(home, ".local", "bin", "gemini"),
            os.path.join(home, ".npm-global", "bin", "gemini"),
            "/usr/local/bin/gemini",
            "/usr/bin/gemini",
        ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    found = shutil.which("gemini")
    if found:
        return found
    return None


def gemini_auth_login(req, *, send_progress=None):
    """Launch interactive `gemini` on the host, intercept the OAuth URL,
    return ~/.gemini/oauth_creds.json.

    Gemini does not have a dedicated `login` subcommand: launching the CLI
    interactively with selectedAuthType=oauth-personal triggers the Google
    OAuth dance. We seed ~/.gemini/settings.json before spawning so the
    auth-type prompt doesn't block the headless host helper.
    """
    gemini_path = find_gemini_binary()
    if not gemini_path:
        return {"error": "Gemini binary not found. Install Gemini CLI first: "
                         "npm install -g @google/gemini-cli"}

    if sys.platform == "win32":
        gemini_dir = os.path.join(
            os.environ.get("USERPROFILE", os.environ.get("HOME", "")),
            ".gemini")
    else:
        gemini_dir = os.path.expanduser("~/.gemini")
    os.makedirs(gemini_dir, exist_ok=True)
    settings_path = os.path.join(gemini_dir, "settings.json")
    if not os.path.exists(settings_path):
        try:
            with open(settings_path, "w", encoding="utf-8") as f:
                json.dump({"theme": "Default", "selectedAuthType": "oauth-personal"}, f)
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

    sys.stderr.write(f"[Relay] gemini login: {gemini_path}\n")
    _launch_time = time.time()

    try:
        proc = subprocess.Popen(  # nosec B603
            [gemini_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except FileNotFoundError:
        return {"error": f"Gemini binary not found: {gemini_path}"}
    except Exception as e:
        return {"error": f"Failed to start gemini: {e}"}

    # Send /exit to break out of the TUI as soon as the OAuth dance lands.
    try:
        proc.stdin.write("/exit\n")
        proc.stdin.flush()
    except Exception:
        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

    url_pattern = re.compile(r'https://accounts\.google\.com/o/oauth2/\S+')
    url_found = None
    all_output = []

    for line in proc.stdout:
        line = line.rstrip()
        all_output.append(line)
        sys.stderr.write(f"[Relay] gemini> {line}\n")
        m = url_pattern.search(line)
        if m and not url_found:
            url_found = m.group(0)
            sys.stderr.write("[Relay] Gemini auth URL found\n")
            if send_progress:
                send_progress({"url": url_found})

    proc.wait()
    sys.stderr.write(f"[Relay] gemini login exited: {proc.returncode}\n")

    creds_path = os.path.join(gemini_dir, "oauth_creds.json")
    if not os.path.exists(creds_path):
        return {"error": f"Gemini credentials file not found: {creds_path}"}

    _max_wait = 180
    _waited = 0
    while _waited < _max_wait:
        try:
            if os.path.getmtime(creds_path) >= _launch_time:
                break
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        time.sleep(1)
        _waited += 1

    if _waited >= _max_wait:
        return {"error": "Timeout: gemini oauth_creds.json was not updated"}

    try:
        with open(creds_path, "r", encoding="utf-8") as f:
            credentials = json.load(f)
        # Also try to bundle google_accounts.json so the server can record
        # the Google account label alongside the access tokens.
        accounts = {}
        accounts_path = os.path.join(gemini_dir, "google_accounts.json")
        if os.path.exists(accounts_path):
            try:
                with open(accounts_path, "r", encoding="utf-8") as f:
                    accounts = json.load(f)
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
    except Exception as e:
        return {"error": f"Failed to read gemini credentials: {e}"}

    return {"credentials": credentials, "accounts": accounts}


def forward_to_host_helper(host_helper, msg, ws_sock, ws_send_fn):
    """Forward a command to the host helper (CLI process outside Docker).

    Connects via TCP, sends JSON request, streams progress + result.
    Progress messages are forwarded to the server via WebSocket.
    For persistent streams (e.g. terminals) a background thread keeps
    pumping progress frames after the initial result is returned.
    """
    import socket as _sock
    import sys as _sys

    host, port_str = host_helper.rsplit(":", 1)
    port = int(port_str)
    request_id = msg.get("request_id", "")
    _action = msg.get("action", "?")

    try:
        sock = _sock.create_connection((host, port), timeout=10)
    except Exception as e:
        _sys.stderr.write(
            f"[FSRelay] fwd→host action={_action} "
            f"FAILED to connect {host_helper}: {e}\n")
        return {"ok": False, "error": f"Cannot reach host helper at {host_helper}: {e}"}

    _sock_owned_by_bg = [False]
    try:
        _fwd_msg = {k: v for k, v in msg.items() if k not in ("type", "request_id")}
        req = json.dumps(_fwd_msg) + "\n"
        sock.sendall(req.encode("utf-8"))

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
                    if ws_sock:
                        progress = json.dumps({
                            "type": "progress",
                            "request_id": request_id,
                            "data": resp.get("data", {}),
                        }).encode("utf-8")
                        try:
                            ws_send_fn(ws_sock, progress)
                        except Exception:
                            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                elif resp.get("type") == "http_response":
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
                            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                elif resp.get("type") == "result":
                    data = resp.get("data", {})
                    if "error" in data:
                        return {"ok": False, "error": data["error"]}
                    _is_persistent = isinstance(data, dict) and (
                        data.get("session_id", "").startswith("local_term_"))
                    if _is_persistent:
                        _remaining = buf

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
                                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                            finally:
                                try:
                                    sock.close()
                                except Exception:
                                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

                        _sock_owned_by_bg[0] = True
                        threading.Thread(
                            target=_bg_progress_reader, daemon=True,
                            name=f"host-helper-stream-{request_id[:8]}",
                        ).start()
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
        if not _sock_owned_by_bg[0]:
            try:
                sock.close()
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
