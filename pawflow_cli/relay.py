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
    """Generate a stable relay ID from username + directory."""
    h = hashlib.sha256(f"{username}:{directory}".encode()).hexdigest()[:8]
    return f"cli_{username}_{h}"


class RelayThread:
    """Manages a background filesystem relay connection."""

    def __init__(self, server_url: str, session_token: str, username: str,
                 directory: str, allow_exec: bool = True):
        self.server_url = server_url
        self.session_token = session_token
        self.username = username
        self.directory = str(Path(directory).resolve())
        self.allow_exec = allow_exec
        self.relay_id = generate_relay_id(username, self.directory)
        self.port = 0
        self.ws_token = ""
        self._thread = None
        self._stop_event = threading.Event()
        self._registered = False

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
                      "service_type": "filesystem",
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
        """Stop the relay and cleanup the service."""
        self._stop_event.set()
        if self._registered:
            try:
                _api_call(self.server_url, "POST", "/api/agent",
                          body={"action": "service_uninstall", "service_id": self.relay_id},
                          session_token=self.session_token)
            except Exception:
                pass
            self._registered = False

    def _run_relay(self):
        """Run the WS relay connection loop (stderr suppressed)."""
        # Add tools directory to path for fs_actions import
        tools_dir = str(Path(__file__).resolve().parent.parent / "tools")
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)

        import pawflow_relay as _relay_mod
        import os as _os

        # Redirect fd 2 (stderr) to devnull — never restored (daemon thread)
        _devnull_fd = _os.open(_os.devnull, _os.O_WRONLY)
        _os.dup2(_devnull_fd, 2)
        _os.close(_devnull_fd)

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
        # Don't restore stderr — keep it silenced until process exits (daemon thread)
