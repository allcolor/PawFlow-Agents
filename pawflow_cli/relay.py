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


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _api_call(server_url, method, path, body=None, session_token=""):
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
                 directory: str, allow_exec: bool = True,
                 docker_image: str = ""):
        self.server_url = server_url
        self.session_token = session_token
        self.username = username
        self.directory = str(Path(directory).resolve())
        self.allow_exec = allow_exec
        self.docker_image = docker_image
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
                      session_token=self.session_token)
        except Exception:
            pass

        # Create new service
        config_str = f"port={self.port},path=/ws/relay,token={self.ws_token},mode=readwrite"
        _api_call(self.server_url, "POST", "/api/agent",
                  body={
                      "action": "service_install",
                      "service_type": "relay",
                      "service_name": self.relay_id,
                      "config_str": config_str,
                  },
                  session_token=self.session_token)
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
                          session_token=self.session_token)
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

    def _run_relay(self):
        """Run the WS relay connection loop (stderr suppressed)."""
        # Add tools directory to path for fs_actions import
        tools_dir = str(Path(__file__).resolve().parent.parent / "tools")
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)

        import pawflow_relay as _relay_mod

        if self.docker_image:
            # Docker mode: launch relay INSIDE container
            # The container relay connects to the WS listener directly
            import subprocess as _sp
            import uuid as _uuid
            self._docker_container = f"pawflow-relay-{_uuid.uuid4().hex[:8]}"
            ws_url = f"wss://host.docker.internal:{self.port}/ws/relay"
            docker_cmd = _docker_cmd() + [
                "run", "--rm",
                "--name", self._docker_container,
                "-v", f"{_translate_path(self.directory)}:/workspace",
                "--add-host", "host.docker.internal:host-gateway",
                "--cpus", "2", "--memory", "2g",
                "--security-opt", "no-new-privileges",
                self.docker_image,
                "python3", "/opt/pawflow/pawflow_relay.py",
                "--server", ws_url,
                "--token", self.ws_token,
                "--relay-id", self.relay_id,
                "--dir", "/workspace",
                "--allow-exec",
            ]
            try:
                self._docker_proc = _sp.Popen(
                    docker_cmd, stdin=_sp.DEVNULL, stdout=_sp.PIPE, stderr=_sp.PIPE)
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
                    except Exception:
                        pass
            return

        # Direct mode: connect from this process
        # Filter [FSRelay] lines from stderr
        _real_write = sys.stderr.write
        def _filtered_write(s):
            if isinstance(s, str) and "[FSRelay]" in s:
                return len(s)
            return _real_write(s)
        sys.stderr.write = _filtered_write

        ws_url = f"wss://localhost:{self.port}/ws/relay"
        try:
            _relay_mod._ws_connect(ws_url, self.ws_token, self.ws_token, self.relay_id,
                                    self.directory, False, allow_exec=self.allow_exec)
        except Exception:
            if self._stop_event.is_set():
                return
            ws_url = f"ws://localhost:{self.port}/ws/relay"
            try:
                _relay_mod._ws_connect(ws_url, self.ws_token, self.ws_token, self.relay_id,
                                        self.directory, False, allow_exec=self.allow_exec)
            except Exception:
                pass
