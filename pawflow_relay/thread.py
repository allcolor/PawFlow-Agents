"""RelayThread — orchestrates a PawFlow relay connection.

Registers the service with the server, launches Docker or native worker,
manages host helper for Docker-to-host actions, handles auto-restart.
"""

import json
import os
import secrets
import socket
import sys
import threading
import time
from pathlib import Path

from pawflow_relay.utils import (
    docker_cmd, translate_path, to_host_path, get_host_ip,
    find_free_port, generate_relay_id, api_call,
)


class RelayThread:
    """Manages a background filesystem relay connection.

    Used by PawCode CLI (as background thread), standalone relay, and
    VS Code extension (as subprocess).
    """

    def __init__(self, server_url: str, session_token: str, username: str,
                 directory: str, docker_image: str = "",
                 gateway_cookie: str = "",
                 docker_cpus: str = "", docker_memory: str = "",
                 allow_local: bool = False,
                 on_token_refresh=None):
        self.server_url = server_url
        self.session_token = session_token
        self.username = username
        self.directory = str(Path(directory).resolve())
        self.docker_image = docker_image
        self.gateway_cookie = gateway_cookie
        self.docker_cpus = docker_cpus or os.environ.get("PAWFLOW_RELAY_CPUS", "2")
        self.docker_memory = docker_memory or os.environ.get("PAWFLOW_RELAY_MEMORY", "4g")
        self.allow_local = allow_local
        self.relay_id = generate_relay_id(username, self.directory)
        self.port = 0
        self.ws_token = ""
        self._thread = None
        self._stop_event = threading.Event()
        self._registered = False
        self._docker_container = None
        self._on_token_refresh = on_token_refresh

    def _api(self, method, path, body=None):
        """Convenience wrapper for api_call with this relay's credentials."""
        return api_call(
            self.server_url, method, path, body=body,
            session_token=self.session_token,
            gateway_cookie=self.gateway_cookie,
            on_token_refresh=self._handle_token_refresh,
        )

    def _handle_token_refresh(self, new_token):
        """Internal handler for transparent token refresh."""
        self.session_token = new_token
        if self._on_token_refresh:
            self._on_token_refresh(new_token)

    def start(self):
        """Register the service and start the relay thread."""
        self._kill_docker()
        self.port = find_free_port()
        self.ws_token = secrets.token_urlsafe(32)

        # Delete old service if exists
        try:
            self._api("POST", "/api/agent",
                      {"action": "service_uninstall", "service_id": self.relay_id})
        except Exception:
            pass

        # Create new service
        config_str = f"port={self.port},path=/ws/relay,token={self.ws_token},mode=readwrite"
        if self.docker_image:
            config_str += f",docker_image={self.docker_image}"
        if self.allow_local:
            config_str += ",allow_local=true"
        self._api("POST", "/api/agent", {
            "action": "service_install",
            "service_type": "relay",
            "service_name": self.relay_id,
            "config_str": config_str,
        })
        self._registered = True

        # Wait for WS listener to start
        time.sleep(1.5)

        # Start background relay thread
        self._thread = threading.Thread(
            target=self._run_relay, daemon=True, name="pawflow-relay")
        self._thread.start()

    def stop(self):
        """Stop the relay and cleanup the service + Docker container."""
        self._stop_event.set()
        if self._registered:
            try:
                self._api("POST", "/api/agent",
                          {"action": "service_uninstall", "service_id": self.relay_id})
            except Exception:
                pass
            self._registered = False
        self._kill_docker()

    def wait(self):
        """Block until the relay thread finishes (for standalone mode)."""
        if self._thread:
            self._thread.join()

    def _kill_docker(self):
        """Kill this relay's Docker containers (current + orphans)."""
        if self._docker_container:
            import subprocess as _sp
            try:
                _sp.run(docker_cmd() + ["rm", "-f", self._docker_container],
                        capture_output=True, timeout=10)
                sys.stderr.write(f"[Relay] Killed container: {self._docker_container}\n")
            except Exception:
                pass
            self._docker_container = None
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
        # Kill orphans from this specific relay
        try:
            from core.docker_utils import kill_containers
            _killed = kill_containers(self.relay_id)
            if _killed:
                sys.stderr.write(f"[Relay] Cleaned {_killed} orphan container(s)\n")
        except Exception:
            pass

    def _run_relay(self):
        """Run the WS relay connection loop."""
        # Add tools directory to path for imports
        tools_dir = str(Path(__file__).resolve().parent.parent / "tools")
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)

        if self.docker_image:
            self._run_docker_relay(tools_dir)
        else:
            self._run_native_relay(tools_dir)

    def _run_native_relay(self, tools_dir):
        """Run the relay worker natively (no Docker)."""
        import pawflow_relay as _relay_mod  # noqa: this is tools/pawflow_relay.py via sys.path
        # The native relay is just _ws_connect from the worker module
        # For now, defer to the existing import mechanism
        # TODO: when tools/pawflow_relay.py is cleaned up, call _ws_connect directly

    def _run_docker_relay(self, tools_dir):
        """Run the relay inside a Docker container with auto-restart."""
        # Start host helper (TCP server for host-level commands)
        host_helper_port = find_free_port()
        self._host_helper_thread = threading.Thread(
            target=self._run_host_helper, args=(host_helper_port,),
            daemon=True, name="pawflow-host-helper")
        self._host_helper_thread.start()

        import subprocess as _sp
        from core.docker_utils import make_container_name

        restart_delay = 1
        max_restart_delay = 60

        while not self._stop_event.is_set():
            self._docker_container = make_container_name(self.relay_id, "relay")
            ws_url = f"wss://{get_host_ip()}:{self.port}/ws/relay"
            self._desktop_host_port = find_free_port()
            self._audio_host_port = find_free_port()
            docker_run_cmd = docker_cmd() + [
                "run", "--rm",
                "--name", self._docker_container,
                "-v", f"{translate_path(to_host_path(self.directory))}:/workspace",
                "--add-host", "host.docker.internal:host-gateway",
                "--cpus", self.docker_cpus, "--memory", self.docker_memory,
                "--shm-size", "512m",
                "--security-opt", "no-new-privileges",
                "-e", "GIT_CONFIG_COUNT=4",
                "-e", "GIT_CONFIG_KEY_0=safe.directory",
                "-e", "GIT_CONFIG_VALUE_0=/workspace",
                "-e", "GIT_CONFIG_KEY_1=core.preloadIndex",
                "-e", "GIT_CONFIG_VALUE_1=true",
                "-e", "GIT_CONFIG_KEY_2=core.fsmonitor",
                "-e", "GIT_CONFIG_VALUE_2=false",
                "-e", "GIT_CONFIG_KEY_3=core.untrackedCache",
                "-e", "GIT_CONFIG_VALUE_3=false",
                "-e", f"PAWFLOW_HOST_HELPER={get_host_ip()}:{host_helper_port}",
                "--publish", f"{self._desktop_host_port}:6080",
                "--publish", f"{self._audio_host_port}:6180",
                "-e", "PAWFLOW_DESKTOP_NOVNC_PORT=6080",
                "-e", f"PAWFLOW_HOST_WORKDIR={self.directory}",
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
            _start_time = time.time()
            try:
                self._docker_proc = _sp.Popen(
                    docker_run_cmd, stdin=_sp.DEVNULL, stdout=_sp.PIPE, stderr=_sp.PIPE)

                def _read_relay_logs():
                    try:
                        for line in self._docker_proc.stderr:
                            msg = line.decode("utf-8", errors="replace").rstrip()
                            if msg and "[FSRelay]" in msg:
                                if any(k in msg for k in ("connect", "disconnect", "error", "Reconnect")):
                                    sys.stderr.write(f"[Relay] {msg}\n")
                    except Exception:
                        pass
                threading.Thread(target=_read_relay_logs, daemon=True,
                                 name="relay-log-reader").start()

                while not self._stop_event.is_set():
                    try:
                        self._docker_proc.wait(timeout=1)
                        break
                    except _sp.TimeoutExpired:
                        continue

                if self._stop_event.is_set():
                    break

                rc = self._docker_proc.poll()
                if rc and rc != 0:
                    stderr = ""
                    try:
                        stderr = self._docker_proc.stderr.read().decode("utf-8", errors="replace")
                    except Exception:
                        pass
                    sys.stderr.write(f"[Relay] Docker relay exited (code {rc}), restarting in {restart_delay}s\n")
                    if stderr:
                        sys.stderr.write(f"[Relay] {stderr[:500]}\n")
                else:
                    sys.stderr.write(f"[Relay] Docker relay exited (code 0), restarting in {restart_delay}s\n")
            except Exception as e:
                sys.stderr.write(f"[Relay] Docker error: {e}, retrying in {restart_delay}s\n")
            finally:
                if hasattr(self, '_docker_proc') and self._docker_proc:
                    try:
                        self._docker_proc.kill()
                    except (OSError, Exception):
                        pass
                    self._docker_proc = None

            if time.time() - _start_time > 30:
                restart_delay = 1
            self._stop_event.wait(restart_delay)
            if self._stop_event.is_set():
                break
            restart_delay = min(restart_delay * 2, max_restart_delay)
            self._kill_docker()
            sys.stderr.write(f"[Relay] Restarting Docker relay container...\n")

    # ── Host helper (TCP server for Docker-to-host commands) ──────────

    def _run_host_helper(self, port: int):
        """TCP server on the host for commands that must run outside Docker."""
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", port))
        srv.listen(1)
        srv.settimeout(2)
        sys.stderr.write(f"[Relay] Host helper listening on port {port}\n")

        while not self._stop_event.is_set():
            try:
                conn, addr = srv.accept()
            except socket.timeout:
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
            self._handle_host_screen_action(conn, req, action)

        else:
            resp = json.dumps({"type": "error", "error": f"Unknown action: {action}"}) + "\n"
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
        import subprocess as _sp
        import shutil

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

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", 0)); vnc_port = s.getsockname()[1]
        if not novnc_port:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("", 0)); novnc_port = s.getsockname()[1]

        procs = []

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
                    pass
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

        time.sleep(0.5)

        _ws_cmd = _ws_base + [str(novnc_port), f"localhost:{vnc_port}"]
        p_ws = _sp.Popen(_ws_cmd, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
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
