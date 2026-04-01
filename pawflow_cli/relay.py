"""Background filesystem relay for PawCode."""

import hashlib
import json
import os
import secrets
import socket
import sys
import threading
import time
from pathlib import Path


def _docker_cmd():
    if os.name == "nt":
        return ["wsl", "docker"]
    return ["docker"]


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


def _get_host_ip():
    """Get IP reachable from Docker containers."""
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


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _api_call(server_url, method, path, body=None, session_token="", gateway_cookie=""):
    """HTTP request to PawFlow agent API (stdlib only)."""
    import http.client
    from urllib.parse import urlparse

    parsed = urlparse(server_url)
    use_ssl = parsed.scheme == "https"
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if use_ssl else 80)

    if use_ssl:
        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        conn = http.client.HTTPSConnection(host, port, context=ctx, timeout=30)
    else:
        conn = http.client.HTTPConnection(host, port, timeout=30)

    headers = {"Content-Type": "application/json"}
    if session_token:
        headers["Authorization"] = f"Bearer {session_token}"
    if gateway_cookie:
        headers["Cookie"] = f"_pf_gw={gateway_cookie}"

    payload = json.dumps(body).encode("utf-8") if body else None
    conn.request(method, path, body=payload, headers=headers)
    resp = conn.getresponse()
    data = resp.read().decode("utf-8")
    conn.close()

    if resp.status >= 400:
        raise Exception(f"API {method} {path} -> {resp.status}: {data}")
    return json.loads(data) if data else {}


def generate_relay_id(username: str, directory: str) -> str:
    """Generate a stable relay ID from username + directory.

    Format: fs_{username}_{sha256(username:normalized_dir)[:8]}
    Consistent across PawCode CLI, VSCode extension, and Python relay.
    """
    normalized = str(Path(directory).resolve())
    h = hashlib.sha256(f"{username}:{normalized}".encode()).hexdigest()[:8]
    return f"fs_{username}_{h}"


class RelayThread:
    """Manages a background filesystem relay connection."""

    def __init__(self, server_url: str, session_token: str, username: str,
                 directory: str, docker_image: str = "",
                 gateway_cookie: str = ""):
        self.server_url = server_url
        self.session_token = session_token
        self.username = username
        self.directory = str(Path(directory).resolve())
        self.docker_image = docker_image
        self.gateway_cookie = gateway_cookie
        self.relay_id = generate_relay_id(username, self.directory)
        self.port = 0
        self.ws_token = ""
        self._thread = None
        self._stop_event = threading.Event()
        self._registered = False
        self._docker_container = None

    def start(self):
        """Register the service and start the relay thread."""
        self.port = find_free_port()
        self.ws_token = secrets.token_urlsafe(32)

        # Delete old service if exists
        try:
            _api_call(self.server_url, "POST", "/api/agent",
                      body={"action": "service_uninstall", "service_id": self.relay_id},
                      session_token=self.session_token, gateway_cookie=self.gateway_cookie)
        except Exception:
            pass

        # Create new service
        config_str = f"port={self.port},path=/ws/relay,token={self.ws_token},mode=readwrite"
        if self.docker_image:
            config_str += f",docker_image={self.docker_image}"
        _api_call(self.server_url, "POST", "/api/agent",
                  body={
                      "action": "service_install",
                      "service_type": "relay",
                      "service_name": self.relay_id,
                      "config_str": config_str,
                  },
                  session_token=self.session_token, gateway_cookie=self.gateway_cookie)
        self._registered = True

        # Wait for WS listener to start
        time.sleep(1.5)

        # Start background relay thread
        self._thread = threading.Thread(target=self._run_relay, daemon=True,
                                         name="pawflow-cli-relay")
        self._thread.start()

    def stop(self):
        """Stop the relay and cleanup the service + Docker container."""
        self._stop_event.set()
        if self._registered:
            try:
                _api_call(self.server_url, "POST", "/api/agent",
                          body={"action": "service_uninstall", "service_id": self.relay_id},
                          session_token=self.session_token, gateway_cookie=self.gateway_cookie)
            except Exception:
                pass
            self._registered = False
        if self._docker_container:
            import subprocess as _sp
            try:
                _sp.run(_docker_cmd() + ["rm", "-f", self._docker_container],
                        capture_output=True, timeout=10)
            except Exception:
                pass
            self._docker_container = None
        # Kill Docker Popen to prevent WinError 6 on GC
        if hasattr(self, '_docker_proc') and self._docker_proc:
            try:
                self._docker_proc.kill()
            except (OSError, Exception):
                pass
            try:
                self._docker_proc.wait(timeout=2)
            except Exception:
                pass
            self._docker_proc = None

    def _run_relay(self):
        """Run the WS relay connection loop (stderr suppressed)."""
        # Add tools directory to path for fs_actions import
        tools_dir = str(Path(__file__).resolve().parent.parent / "tools")
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)

        import pawflow_relay as _relay_mod

        if self.docker_image:
            # Docker mode: launch relay INSIDE container
            # Start host helper (TCP server for host-level commands like claude_auth_login)
            host_helper_port = find_free_port()
            self._host_helper_thread = threading.Thread(
                target=self._run_host_helper, args=(host_helper_port,),
                daemon=True, name="pawflow-host-helper")
            self._host_helper_thread.start()

            import subprocess as _sp
            import uuid as _uuid
            self._docker_container = f"pawflow-relay-{_uuid.uuid4().hex[:8]}"
            ws_url = f"wss://{_get_host_ip()}:{self.port}/ws/relay"
            self._desktop_host_port = find_free_port()
            docker_cmd = _docker_cmd() + [
                "run", "--rm",
                "--name", self._docker_container,
                "-v", f"{_translate_path(_to_host_path(self.directory))}:/workspace",
                "--add-host", "host.docker.internal:host-gateway",
                "--cpus", "2", "--memory", "2g",
                "--security-opt", "no-new-privileges",
                "-e", "GIT_CONFIG_COUNT=1",
                "-e", "GIT_CONFIG_KEY_0=safe.directory",
                "-e", "GIT_CONFIG_VALUE_0=/workspace",
                "-e", f"PAWFLOW_HOST_HELPER={_get_host_ip()}:{host_helper_port}",
                "--publish", f"{self._desktop_host_port}:6080",
                "-e", "PAWFLOW_DESKTOP_NOVNC_PORT=6080",
                self.docker_image,
                "python3", "/opt/pawflow/pawflow_relay.py",
                "--server", ws_url,
                "--token", self.ws_token,
                "--relay-id", self.relay_id,
                "--dir", "/workspace",
                "--allow-exec",
                "--allow-automation",
                "--allow-local-screen",
            ]
            try:
                self._docker_proc = _sp.Popen(
                    docker_cmd, stdin=_sp.DEVNULL, stdout=_sp.PIPE, stderr=_sp.PIPE)
                # Background thread to read relay logs from container stderr
                def _read_relay_logs():
                    try:
                        for line in self._docker_proc.stderr:
                            msg = line.decode("utf-8", errors="replace").rstrip()
                            if msg and "[FSRelay]" in msg:
                                # Show important relay events to user
                                if any(k in msg for k in ("connect", "disconnect", "error", "Reconnect")):
                                    sys.stderr.write(f"[Relay] {msg}\n")
                    except Exception:
                        pass
                threading.Thread(target=_read_relay_logs, daemon=True,
                                 name="relay-log-reader").start()
                # Wait for container to exit or stop event
                while not self._stop_event.is_set():
                    try:
                        self._docker_proc.wait(timeout=1)
                        break  # container exited
                    except _sp.TimeoutExpired:
                        continue
                # Check exit code
                rc = self._docker_proc.poll()
                if rc and rc != 0:
                    stderr = ""
                    try:
                        stderr = self._docker_proc.stderr.read().decode("utf-8", errors="replace")
                    except Exception:
                        pass
                    sys.stderr.write(f"[Relay] Docker relay failed (exit {rc}):\n")
                    sys.stderr.write(f"[Relay] {stderr[:500]}\n")
            except Exception as e:
                sys.stderr.write(f"[Relay] Docker error: {e}\n")
            finally:
                if hasattr(self, '_docker_proc') and self._docker_proc:
                    try:
                        self._docker_proc.kill()
                    except (OSError, Exception):
                        pass
                    self._docker_proc = None
            return

    def _run_host_helper(self, port: int):
        """TCP server on the host for commands that must run outside Docker.

        The relay in Docker connects to host.docker.internal:{port} to
        forward host-level commands (e.g. claude_auth_login).

        Protocol: newline-delimited JSON.
          → {"action": "claude_auth_login"}
          ← {"type": "progress", "data": {"url": "https://..."}}
          ← {"type": "result", "data": {"credentials": {...}}}
        """
        import socket as _sock

        srv = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
        srv.setsockopt(_sock.SOL_SOCKET, _sock.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", port))
        srv.listen(1)
        srv.settimeout(2)
        sys.stderr.write(f"[Relay] Host helper listening on port {port}\n")

        while not self._stop_event.is_set():
            try:
                conn, addr = srv.accept()
            except _sock.timeout:
                continue
            except Exception:
                break

            try:
                self._handle_host_helper_conn(conn)
            except Exception as e:
                sys.stderr.write(f"[Relay] Host helper error: {e}\n")
            finally:
                conn.close()

        srv.close()

    def _handle_host_helper_conn(self, conn):
        """Handle a single host helper connection."""
        # Read request (newline-delimited JSON)
        buf = b""
        while b"\n" not in buf:
            chunk = conn.recv(4096)
            if not chunk:
                return
            buf += chunk

        req = json.loads(buf.split(b"\n")[0])
        action = req.get("action", "")

        tools_dir = str(Path(__file__).resolve().parent.parent / "tools")
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)

        if action == "claude_auth_login":
            from pawflow_relay import _claude_auth_login

            def _send_progress(data):
                try:
                    msg = json.dumps({"type": "progress", "data": data}) + "\n"
                    conn.sendall(msg.encode("utf-8"))
                except Exception:
                    pass

            result = _claude_auth_login(req, send_progress=_send_progress)
            resp = json.dumps({"type": "result", "data": result}) + "\n"
            conn.sendall(resp.encode("utf-8"))

        elif action in ("start_local_desktop", "stop_local_desktop",
                        "local_screen_check") or action.startswith("screen_"):
            # Run local screen/desktop actions natively on host
            self._handle_host_screen_action(conn, req, action)

        else:
            resp = json.dumps({"type": "error", "error": f"Unknown action: {action}"}) + "\n"
            conn.sendall(resp.encode("utf-8"))

    def _handle_host_screen_action(self, conn, req, action):
        """Handle screen/desktop actions on the host machine.

        Delegates to pawflow_relay's _execute_command logic by running
        the action in a temporary local context.
        """
        tools_dir = str(Path(__file__).resolve().parent.parent / "tools")
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)

        # Reuse the relay's local desktop / screen handlers
        # We build a minimal _execute_command-like dispatch
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
        import subprocess as _sp
        import shutil

        # Idempotent
        if hasattr(self, '_local_desktop_procs') and self._local_desktop_procs:
            alive = all(p.poll() is None for p in self._local_desktop_procs)
            if alive:
                return {"novnc_port": self._local_desktop_novnc_port, "already_running": True}
            for p in self._local_desktop_procs:
                try: p.kill()
                except Exception: pass
            self._local_desktop_procs = None

        _platform = sys.platform
        vnc_port = 0
        novnc_port = int(req.get("novnc_port", 0))

        # Find free ports
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", 0)); vnc_port = s.getsockname()[1]
        if not novnc_port:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("", 0)); novnc_port = s.getsockname()[1]

        procs = []

        # Detect websockify once (binary or python module)
        websockify_cmd = shutil.which("websockify")
        if websockify_cmd:
            _ws_base = [websockify_cmd]
        else:
            try:
                _sp.run([sys.executable, "-m", "websockify", "--help"],
                        capture_output=True, timeout=5)
                _ws_base = [sys.executable, "-m", "websockify"]
            except Exception:
                return {"error": "websockify not installed. Install with: pip install websockify"}

        if _platform == "win32":
            # Windows: use TightVNC
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
            p_vnc = _sp.Popen([winvnc, "-rfbport", str(vnc_port), "-localhost"],
                              stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
            procs.append(p_vnc)

        elif _platform == "linux":
            display = os.environ.get("DISPLAY", ":0")
            if not shutil.which("x11vnc"):
                return {"error": "x11vnc not installed"}
            p_vnc = _sp.Popen(
                ["x11vnc", "-display", display, "-forever", "-nopw",
                 "-rfbport", str(vnc_port), "-shared", "-noxdamage"],
                stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
            procs.append(p_vnc)

        elif _platform == "darwin":
            vnc_port = 5900
        else:
            return {"error": f"Unsupported platform: {_platform}"}

        import time as _t
        _t.sleep(0.5)

        p_ws = _sp.Popen(_ws_base + [str(novnc_port), f"localhost:{vnc_port}"],
                         stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
        procs.append(p_ws)

        self._local_desktop_procs = procs
        self._local_desktop_vnc_port = vnc_port
        self._local_desktop_novnc_port = novnc_port
        sys.stderr.write(f"[Relay] Local desktop started: vnc={vnc_port} novnc={novnc_port}\n")
        return {"vnc_port": vnc_port, "novnc_port": novnc_port, "local_screen": True}

    def _host_stop_local_desktop(self):
        if hasattr(self, '_local_desktop_procs') and self._local_desktop_procs:
            for p in self._local_desktop_procs:
                if p.poll() is None:
                    p.terminate()
            for p in self._local_desktop_procs:
                try: p.wait(timeout=5)
                except Exception: p.kill()
            self._local_desktop_procs = None
            sys.stderr.write("[Relay] Local desktop stopped\n")
            return {"ok": True}
        return {"was_running": False}

    def _host_screen_tool(self, req, action):
        """Forward screen automation actions to the host's screen tools."""
        tools_dir = str(Path(__file__).resolve().parent.parent / "tools")
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)
        from screen_actions import handle_screen_action
        return handle_screen_action(action, req)
