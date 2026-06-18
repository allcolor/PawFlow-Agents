"""RelayThread host helper socket + local desktop/terminal/code-server."""

import logging

import json
import os
import shutil
import socket
import sys
import threading
import time
from pathlib import Path

from pawflow_relay.utils import (
    find_free_port,
)

# Split out of pawflow_relay/thread.py for the <=800-line rule; composed back
# into RelayThread (invariant 2: MRO/shared state). Whole pkg is vendored via copytree.

from pawflow_relay._thread_base import _host_abs_path, _host_python_command, _relay_tools_dir  # noqa: F401,E402


class _RelayHostHelperMixin:
    """host helper socket + local desktop/terminal/code-server."""

    def _run_host_helper(self, port: int):
        """TCP server on the host for commands that must run outside Docker."""
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", port))  # nosec B104 - host helper must be reachable from relay container.
        srv.listen(5)
        srv.settimeout(2)
        self._log(f"[Relay] Host helper listening on port {port}")

        while not self._stop_event.is_set():
            try:
                conn, addr = srv.accept()
            except socket.timeout:
                continue
            except Exception:
                break
            # Handle each connection in its own thread (terminal sessions are persistent)
            threading.Thread(
                target=self._handle_host_helper_conn_safe, args=(conn,),
                daemon=True, name="host-helper-conn").start()
        srv.close()

    def _handle_host_helper_conn_safe(self, conn):
        """Wrapper that closes conn unless the handler takes ownership."""
        _close_conn = True
        try:
            _close_conn = self._handle_host_helper_conn(conn)
        except Exception as e:
            self._log(f"[Relay] Host helper error: {e}")
        finally:
            if _close_conn is not False:
                try:
                    conn.close()
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

    def _handle_host_helper_conn(self, conn):
        """Handle a single host helper connection."""
        buf = b""
        while b"\n" not in buf:
            chunk = conn.recv(4096)
            if not chunk:
                return
            buf += chunk

        req = json.loads(buf.split(b"\n")[0])
        action = req.get("action", "")

        tools_dir = _relay_tools_dir()
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)

        if action in ("claude_auth_login", "codex_auth_login", "gemini_auth_login"):
            from pawflow_relay.auth import (
                claude_auth_login as _claude_auth_login,
                codex_auth_login as _codex_auth_login,
                gemini_auth_login as _gemini_auth_login,
            )
            _login_fn = {
                "claude_auth_login": _claude_auth_login,
                "codex_auth_login": _codex_auth_login,
                "gemini_auth_login": _gemini_auth_login,
            }[action]

            def _send_progress(data):
                try:
                    msg = json.dumps({"type": "progress", "data": data}) + "\n"
                    conn.sendall(msg.encode("utf-8"))
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

            result = _login_fn(req, send_progress=_send_progress)
            resp = json.dumps({"type": "result", "data": result}) + "\n"
            conn.sendall(resp.encode("utf-8"))

        elif action in ("start_local_desktop", "stop_local_desktop",
                        "local_screen_check") or action.startswith("screen_"):
            self._handle_host_screen_action(conn, req, action)

        elif action == "open_local_terminal":
            # Open PTY, send result, then stream terminal_data as progress
            # The relay's _forward_to_host_helper will forward progress to the server WS
            self._host_terminal_persistent(conn, req)
            return False  # conn managed by _host_terminal_persistent, don't close

        elif action in ("write_terminal", "resize_terminal", "close_terminal"):
            sid = req.get("session_id", "")
            if hasattr(self, '_local_terminals') and sid in self._local_terminals:
                t = self._local_terminals[sid]
                if action == "write_terminal":
                    import base64 as _b64w
                    raw = _b64w.b64decode(req.get("data", ""))
                    t["write"](raw)
                elif action == "resize_terminal":
                    t["resize"](req.get("cols", 80), req.get("rows", 24))
                elif action == "close_terminal":
                    t["kill"]()
                    self._local_terminals.pop(sid, None)
                resp = json.dumps({"type": "result", "data": {"ok": True}}) + "\n"
                conn.sendall(resp.encode("utf-8"))
            else:
                resp = json.dumps({"type": "error", "error": f"Terminal session {sid} not found"}) + "\n"
                conn.sendall(resp.encode("utf-8"))

        elif action == "start_local_code_server":
            result = self._host_start_local_code_server(req)
            resp = json.dumps({"type": "result", "data": result}) + "\n"
            conn.sendall(resp.encode("utf-8"))

        elif action == "http_fetch":
            # Run the fetch on the host (where 'localhost' = real localhost).
            # Stream chunks back as http_response events; the relay forwards
            # them to PawFlow via WebSocket.
            from fs_http import action_http_fetch as _http_fetch
            _chunk_stats = {"bytes": 0, "chunks": 0, "status": None}

            def _on_chunk(kind, data):
                if kind == "start":
                    _chunk_stats["status"] = data.get("status") if isinstance(data, dict) else None
                elif kind == "chunk":
                    try:
                        import base64 as _b64
                        _chunk_stats["bytes"] += len(_b64.b64decode(data)) if isinstance(data, str) else len(data or b"")
                    except Exception:
                        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                    _chunk_stats["chunks"] += 1
                try:
                    msg = json.dumps({"type": "http_response", "kind": kind,
                                       "data": data}) + "\n"
                    conn.sendall(msg.encode("utf-8"))
                except Exception as _se:
                    self._log(
                        f"[HostHelper] http_fetch sendall({kind}) failed: {_se}")
            try:
                result = _http_fetch(".", ".", req, on_chunk=_on_chunk)
                # Only log abnormal outcomes: error statuses, zero-
                # byte responses, or failed results. Happy path (200
                # + bytes) stays quiet.
                _ok = result.get("ok") if isinstance(result, dict) else False
                _status = _chunk_stats["status"]
                if (not _ok or (_status and _status >= 400)
                        or _chunk_stats["bytes"] == 0):
                    self._log(
                        f"[HostHelper] http_fetch status={_status} "
                        f"bytes={_chunk_stats['bytes']} "
                        f"chunks={_chunk_stats['chunks']} ok={_ok}")
                resp = json.dumps({"type": "result", "data": result}) + "\n"
            except Exception as e:
                self._log(f"[HostHelper] http_fetch EXCEPTION: {e}")
                resp = json.dumps({"type": "error", "error": str(e)}) + "\n"
            conn.sendall(resp.encode("utf-8"))

        else:
            try:
                if not self.allow_local:
                    raise PermissionError(
                        "Local execution disabled. Start relay with --allow-local")
                from fs_actions import ACTIONS as _FS_ACTIONS
                handler = _FS_ACTIONS.get(action)
                if not handler:
                    raise ValueError(f"Unknown action: {action}")

                abs_path = _host_abs_path(req.get("path", "."), self.directory)
                if action == "exec":
                    result = handler(self.directory, abs_path, req, allow_exec=True)
                else:
                    result = handler(self.directory, abs_path, req)
                resp = json.dumps({"type": "result", "data": result}) + "\n"
            except Exception as e:
                resp = json.dumps({"type": "error", "error": str(e)}) + "\n"
            conn.sendall(resp.encode("utf-8"))

    def _handle_host_screen_action(self, conn, req, action):
        """Handle screen/desktop actions on the host machine."""
        try:
            if action == "start_local_desktop":
                result = self._host_start_local_desktop(req)
            elif action == "stop_local_desktop":
                result = self._host_stop_local_desktop()
            elif action.startswith("screen_"):
                result = self._host_screen_tool(req, action)
            else:
                result = {"error": f"Unsupported host action: {action}"}

            if "error" in result:
                resp = json.dumps({"type": "error", "error": result["error"]}) + "\n"
            else:
                resp = json.dumps({"type": "result", "data": result}) + "\n"
        except Exception as e:
            resp = json.dumps({"type": "error", "error": str(e)}) + "\n"

        conn.sendall(resp.encode("utf-8"))

    def _host_start_local_desktop(self, req):
        """Start VNC + websockify on the host to share the local screen."""
        import subprocess as _sp  # nosec B404

        if hasattr(self, '_local_desktop_procs') and self._local_desktop_procs:
            alive = all(p.poll() is None for p in self._local_desktop_procs)
            if alive:
                return {"novnc_port": self._local_desktop_novnc_port, "already_running": True}
            for p in self._local_desktop_procs:
                try:
                    p.kill()
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            self._local_desktop_procs = None

        _platform = sys.platform
        vnc_port = 0
        novnc_port = int(req.get("novnc_port", 0))

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", 0))
            vnc_port = s.getsockname()[1]
        if not novnc_port:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("", 0))
                novnc_port = s.getsockname()[1]

        procs = []

        websockify_cmd = shutil.which("websockify")
        if websockify_cmd:
            _ws_base = [websockify_cmd]
        else:
            _python_cmd = _host_python_command()
            if not _python_cmd:
                return {"error": "websockify not installed. Install with: pip install websockify"}
            try:
                _sp.run([_python_cmd, "-m", "websockify", "--help"],  # nosec B603
                        capture_output=True, timeout=5)
                _ws_base = [_python_cmd, "-m", "websockify"]
            except Exception:
                return {"error": "websockify not installed. Install with: pip install websockify"}

        if _platform == "win32":
            _existing = False
            for _tp in [5900, 5901]:
                try:
                    _ts = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    _ts.settimeout(1)
                    _ts.connect(("localhost", _tp))
                    _ts.close()
                    vnc_port = _tp
                    _existing = True
                    break
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            if not _existing:
                winvnc = None
                for candidate in [
                    r"C:\Program Files\TightVNC\tvnserver.exe",
                    r"C:\Program Files\uvnc bvba\UltraVNC\winvnc.exe",
                    r"C:\Program Files (x86)\TightVNC\tvnserver.exe",
                ]:
                    if os.path.exists(candidate):
                        winvnc = candidate
                        break
                if not winvnc:
                    winvnc = shutil.which("tvnserver") or shutil.which("winvnc")
                if not winvnc:
                    return {"error": "No VNC server found. Install TightVNC or UltraVNC."}
                p_vnc = _sp.Popen([winvnc, "-rfbport", str(vnc_port), "-localhost"],  # nosec B603
                                  stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
                procs.append(p_vnc)

        elif _platform == "linux":
            display = os.environ.get("DISPLAY", ":0")
            if not shutil.which("x11vnc"):
                return {"error": "x11vnc not installed"}
            p_vnc = _sp.Popen(  # nosec B603, B607
                ["x11vnc", "-display", display, "-forever", "-nopw",
                 "-rfbport", str(vnc_port), "-shared", "-noxdamage"],
                stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
            procs.append(p_vnc)

        elif _platform == "darwin":
            vnc_port = 5900
        else:
            return {"error": f"Unsupported platform: {_platform}"}

        time.sleep(0.5)

        _ws_cmd = _ws_base + [str(novnc_port), f"localhost:{vnc_port}"]
        p_ws = _sp.Popen(_ws_cmd, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)  # nosec B603
        procs.append(p_ws)

        self._local_desktop_procs = procs
        self._local_desktop_vnc_port = vnc_port
        self._local_desktop_novnc_port = novnc_port

        return {"vnc_port": vnc_port, "novnc_port": novnc_port, "local_screen": True}

    def _host_stop_local_desktop(self):
        if hasattr(self, '_local_desktop_procs') and self._local_desktop_procs:
            for p in self._local_desktop_procs:
                if p.poll() is None:
                    p.terminate()
            for p in self._local_desktop_procs:
                try:
                    p.wait(timeout=5)
                except Exception:
                    p.kill()
            self._local_desktop_procs = None
            self._log("[Relay] Local desktop stopped")
            return {"ok": True}
        return {"was_running": False}

    def _host_screen_tool(self, req, action):
        """Forward screen automation actions to the host's screen tools."""
        tools_dir = _relay_tools_dir()
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)
        from screen_actions import handle_screen_action
        return handle_screen_action(action, req)

    def _host_terminal_persistent(self, conn, req):
        """Open a PTY on the host, stream terminal_data as progress on the TCP conn.

        Same mechanism as the Docker relay terminal — the PTY reader sends
        terminal_data messages, and write_terminal/resize_terminal come as
        separate host helper calls on new TCP connections.

        The relay's _forward_to_host_helper forwards progress messages
        to the server WS, where dispatch_terminal_data sends them to
        the browser — exactly like the Docker terminal path.
        """
        import subprocess as _sp  # nosec B404
        import shutil
        import uuid as _uuid
        import base64

        cols = req.get("cols", 80)
        rows = req.get("rows", 24)
        shell = req.get("shell")

        if not shell:
            if sys.platform == "win32":
                shell = (shutil.which("pwsh")
                         or shutil.which("powershell")
                         or shutil.which("git-bash")
                         or shutil.which("bash")
                         or "cmd.exe")
            else:
                shell = os.environ.get("SHELL", "/bin/bash")

        session_id = f"local_term_{_uuid.uuid4().hex[:8]}"

        try:
            if sys.platform != "win32":
                import pty as _pty_mod
                import fcntl
                import struct
                import termios
                master, slave = _pty_mod.openpty()
                winsize = struct.pack("HHHH", rows, cols, 0, 0)
                fcntl.ioctl(slave, termios.TIOCSWINSZ, winsize)
                env = os.environ.copy()
                env["TERM"] = "xterm-256color"
                proc = _sp.Popen(  # nosec B603
                    [shell], stdin=slave, stdout=slave, stderr=slave,
                    cwd=self.directory, preexec_fn=os.setsid,
                    close_fds=True, env=env)
                os.close(slave)

                def _read():
                    return os.read(master, 4096)

                def _write(data):
                    os.write(master, data)

                def _resize(c, r):
                    ws = struct.pack("HHHH", r, c, 0, 0)
                    fcntl.ioctl(master, termios.TIOCSWINSZ, ws)

                def _kill():
                    proc.kill()
            else:
                from winpty import PtyProcess
                pty_proc = PtyProcess.spawn([shell], cwd=self.directory,
                                            dimensions=(rows, cols))

                def _read():
                    pty_proc.fileobj.settimeout(0.1)
                    try:
                        data = pty_proc.fileobj.recv(4096)
                        if not data:
                            raise EOFError
                        return data
                    except socket.timeout:
                        return b""
                    except OSError:
                        raise EOFError

                def _write(data):
                    pty_proc.write(data.decode("utf-8", errors="replace")
                                   if isinstance(data, bytes) else data)

                def _resize(c, r):
                    pty_proc.setwinsize(r, c)

                def _kill():
                    try:
                        import signal as _sig
                        pty_proc.kill(_sig.SIGTERM)
                    except Exception:
                        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

            # Store session for write/resize/close from separate TCP calls
            if not hasattr(self, '_local_terminals'):
                self._local_terminals = {}
            self._local_terminals[session_id] = {
                "write": _write, "resize": _resize, "kill": _kill,
            }

            # Send result immediately
            result_msg = json.dumps({
                "type": "result",
                "data": {"session_id": session_id},
            }) + "\n"
            conn.sendall(result_msg.encode("utf-8"))

            # Stream terminal_data as progress messages on this TCP connection.
            # The relay's _forward_to_host_helper reads these and forwards
            # them to the server WS → dispatch_terminal_data → browser.
            self._log(f"[Relay] Local terminal {session_id} opened ({shell})")
            try:
                while True:
                    try:
                        data = _read()
                    except EOFError:
                        break
                    if not data:
                        continue
                    progress = json.dumps({
                        "type": "progress",
                        "data": {
                            "type": "terminal_data",
                            "session_id": session_id,
                            "data": base64.b64encode(data).decode("ascii"),
                        },
                    }) + "\n"
                    try:
                        conn.sendall(progress.encode("utf-8"))
                    except (BrokenPipeError, OSError):
                        break
            finally:
                _kill()
                self._local_terminals.pop(session_id, None)
                # Send terminal_exit
                try:
                    exit_msg = json.dumps({
                        "type": "progress",
                        "data": {
                            "type": "terminal_exit",
                            "session_id": session_id,
                        },
                    }) + "\n"
                    conn.sendall(exit_msg.encode("utf-8"))
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                try:
                    conn.close()
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                self._log(f"[Relay] Local terminal {session_id} closed")

        except Exception as e:
            resp = json.dumps({"type": "error", "error": f"Failed: {e}"}) + "\n"
            try:
                conn.sendall(resp.encode("utf-8"))
                conn.close()
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

    def _host_start_local_code_server(self, req):
        """Start code-server on the host machine."""
        import subprocess as _sp  # nosec B404
        import http.client
        import shutil

        code_server = shutil.which("code-server")
        if not code_server:
            return {"error": "code-server not installed on host. Install with: npm install -g code-server"}

        port = find_free_port()
        base_path = req.get("base_path", "")
        abs_proxy_base_path = base_path.rstrip("/")
        if hasattr(self, '_local_code_server'):
            for old_port, old_proc in list(self._local_code_server.items()):
                if old_proc.poll() is None:
                    old_base = getattr(self, '_local_code_server_base_path', {}).get(old_port, "")
                    if old_base != base_path:
                        old_proc.terminate()
                        self._local_code_server.pop(old_port, None)
        args = [
            code_server,
            "--port", str(port),
            "--auth", "none",
            "--disable-telemetry",
            "--disable-workspace-trust",
            "--abs-proxy-base-path", abs_proxy_base_path,
            "--bind-addr", f"127.0.0.1:{port}",
            str(Path(self.directory).resolve()),
        ]
        try:
            proc = _sp.Popen(  # nosec B603
                args,
                stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
            ready_path = "/"
            deadline = time.time() + 10
            ready = False
            last_err = ""
            while time.time() < deadline:
                rc = proc.poll()
                if rc is not None:
                    return {"error": f"code-server exited with status {rc}"}
                try:
                    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=0.5)
                    conn.request("GET", ready_path)
                    resp = conn.getresponse()
                    resp.read(1024)
                    conn.close()
                    if resp.status < 500:
                        ready = True
                        break
                except Exception as e:
                    last_err = str(e)
                time.sleep(0.2)
            if not ready:
                try:
                    proc.terminate()
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                return {"error": f"code-server did not become ready on port {port}: {last_err}"}
            if not hasattr(self, '_local_code_server'):
                self._local_code_server = {}
            self._local_code_server[port] = proc
            if not hasattr(self, '_local_code_server_base_path'):
                self._local_code_server_base_path = {}
            self._local_code_server_base_path[port] = base_path
            return {"port": port, "upstream_base_path": "/"}
        except Exception as e:
            return {"error": f"Failed to start code-server: {e}"}
