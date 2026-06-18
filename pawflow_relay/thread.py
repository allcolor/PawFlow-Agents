"""RelayThread — orchestrates a PawFlow relay connection.

Registers the service with the server, launches Docker or native worker,
manages host helper for Docker-to-host actions, handles auto-restart.
"""
import logging

import os
import secrets
import sys
import threading
import time
from pathlib import Path

from pawflow_relay.utils import (
    docker_cmd, generate_relay_id, api_call,
)

from pawflow_relay._thread_base import (  # noqa: F401,E402
    _RELAY_APPARMOR_PROFILE, _relay_apparmor_resolved, _relay_apparmor_security_opts, _relay_container_prefix, _make_relay_container_name, _is_windows_drive_absolute_path, _is_host_absolute_path, _relay_runtime_root, _relay_tools_dir, _host_python_command, _host_abs_path, _kill_relay_containers, cleanup_relay_containers)
from pawflow_relay._thread_docker import _RelayDockerMixin  # noqa: E402
from pawflow_relay._thread_host import _RelayHostHelperMixin  # noqa: E402


class RelayThread(_RelayDockerMixin, _RelayHostHelperMixin):
    """Manages a background filesystem relay connection.

    Used by standalone client relays and server-managed relay launch paths.
    """

    def __init__(self, server_url: str, session_token: str, username: str,
                 directory: str, docker_image: str = "",
                 gateway_cookie: str = "",
                 gateway_key: str = "",
                 docker_cpus: str = "", docker_memory: str = "",
                 allow_exec: bool = True,
                 allow_remote_desktop: bool = True,
                 allow_local: bool = False,
                 on_token_refresh=None,
                 log_file: str = "",
                 relay_id: str = "",
                 read_only: bool = False):
        self.server_url = server_url
        self.session_token = session_token
        self.username = username
        self.directory = str(Path(directory).resolve())
        self.docker_image = docker_image
        self.gateway_cookie = gateway_cookie
        self.gateway_key = gateway_key
        self.log_file = log_file
        self.docker_cpus = docker_cpus or os.environ.get("PAWFLOW_RELAY_CPUS", "2")
        self.docker_memory = docker_memory or os.environ.get("PAWFLOW_RELAY_MEMORY", "4g")
        self.allow_exec = allow_exec
        self.allow_remote_desktop = allow_remote_desktop
        self.allow_local = allow_local
        self.read_only = read_only
        self.relay_id = relay_id or generate_relay_id(username, self.directory)
        self.port = 0
        self.ws_token = ""  # nosec B105
        self._thread = None
        self._stop_event = threading.Event()
        self._registered = False
        self._docker_container = None
        self._on_token_refresh = on_token_refresh
        # Lazily-opened log file handle for [Relay] lines when log_file
        # was configured. Writing to a file keeps pawflow_cli's UI clean
        # while still preserving the full relay log for diagnostics.
        self._log_fh = None

    def _log_out(self):
        """Return the writable sink for [Relay] diagnostics.

        Prefers the configured log_file (opened lazily, line-buffered).
        Falls back to sys.__stderr__ (unpatched — pawflow_cli's
        [FSRelay]-filter would otherwise eat the output).
        """
        if self.log_file:
            if self._log_fh is None:
                try:
                    _log_dir = os.path.dirname(self.log_file)
                    if _log_dir:
                        os.makedirs(_log_dir, exist_ok=True)
                    self._log_fh = open(self.log_file, "a",
                                        encoding="utf-8", buffering=1)
                except Exception:
                    # One-shot: stop trying, fall through to stderr.
                    self.log_file = ""
                    self._log_fh = None
            if self._log_fh is not None:
                return self._log_fh
        return getattr(sys, "__stderr__", None) or sys.stderr

    def _log(self, msg: str):
        """Write a single diagnostic line (already prefixed or not)."""
        try:
            out = self._log_out()
            out.write(msg if msg.endswith("\n") else msg + "\n")
            try:
                out.flush()
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

    def _api(self, method, path, body=None):
        """Convenience wrapper for api_call with this relay's credentials."""
        return api_call(
            self.server_url, method, path, body=body,
            session_token=self.session_token,
            gateway_cookie=self.gateway_cookie,
            on_token_refresh=self._handle_token_refresh,
        )

    def _api_retry(self, method, path, body=None, attempts=5):
        """Like _api but retries transient boot-time failures.

        The relay is often started right after pawflow; /api/ui may not
        be fully ready yet (non-JSON response, connection refused, 5xx).
        Backoff: 0.5s, 1s, 2s, 4s, 8s (capped).
        """
        delay = 0.5
        last_err = None
        for i in range(attempts):
            try:
                return self._api(method, path, body)
            except Exception as e:
                last_err = e
                if i == attempts - 1:
                    break
                self._log(
                    f"[Relay] {method} {path} failed (attempt {i+1}/{attempts}): "
                    f"{e} -- retrying in {delay:.1f}s")
                time.sleep(delay)
                delay = min(delay * 2, 8.0)
        raise last_err

    def _handle_token_refresh(self, new_token):
        """Internal handler for transparent token refresh."""
        self.session_token = new_token
        if self._on_token_refresh:
            self._on_token_refresh(new_token)

    def _check_relay_connected(self) -> bool:
        """Check if the relay is connected to the server."""
        try:
            data = self._api("POST", "/api/ui",
                             {"action": "relay_list_available"})
            for r in (data.get("relays") or []):
                if r.get("relay_id") == self.relay_id and r.get("connected"):
                    return True
            return False
        except Exception:
            return False

    def _service_config_str(self):
        _mode = "readonly" if self.read_only else "readwrite"
        config_str = f"port=0,path=/ws/relay,token={self.ws_token},mode={_mode}"
        if self.docker_image:
            config_str += f",docker_image={self.docker_image}"
        if self.allow_local:
            config_str += ",allow_local=true"
        return config_str

    def _install_service(self, *, retry=False):
        call = self._api_retry if retry else self._api
        call("POST", "/api/ui", {
            "action": "service_install",
            "service_type": "relay",
            "service_name": self.relay_id,
            "config_str": self._service_config_str(),
        })

    def _reregister_service(self):
        """Re-register the relay service on the server (keeps same port/token).

        Does NOT pre-uninstall: `service_install` is idempotent on the
        server (same config + already-live = no-op). The earlier
        uninstall-then-install pattern tore down the live WS pool on
        every re-register, which flipped is_connected() to False, which
        fired another re-register — self-reinforcing loop observed in
        the wild.
        """
        self._install_service()

    def _restart_service_registration(self):
        """Apply the same registration reset as a manual stop/start."""
        self._log("[Relay] Full reconnect: reinstalling relay service")
        try:
            self._api("POST", "/api/ui",
                      {"action": "service_uninstall", "service_id": self.relay_id})
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        self.ws_token = secrets.token_urlsafe(32)
        self._install_service(retry=True)
        self._registered = True

    def start(self):
        """Register the service and start the relay thread."""
        self._kill_docker()
        self.port = 0  # no longer used; route is on main HTTP listener
        self.ws_token = secrets.token_urlsafe(32)

        # Delete old service if exists (retry: absorbs server boot race)
        try:
            self._api_retry("POST", "/api/ui",
                      {"action": "service_uninstall", "service_id": self.relay_id})
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

        # Create new service. 'port' is kept for legacy config schema
        # compatibility; the server now registers on the main HTTP listener.
        self._install_service(retry=True)
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
                self._api("POST", "/api/ui",
                          {"action": "service_uninstall", "service_id": self.relay_id})
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            self._registered = False
        self._kill_docker()

    def wait(self):
        """Block until the relay thread finishes (for standalone mode)."""
        if self._thread:
            self._thread.join()

    def _kill_docker(self):
        """Kill this relay's Docker containers (current + orphans)."""
        if self._docker_container:
            import subprocess as _sp  # nosec B404
            try:
                _sp.run(docker_cmd() + ["rm", "-f", self._docker_container],  # nosec B603
                        capture_output=True, timeout=10)
                self._log(f"[Relay] Killed container: {self._docker_container}")
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            self._docker_container = None
        if hasattr(self, '_docker_proc') and self._docker_proc:
            try:
                self._docker_proc.kill()
            except (OSError, Exception):
                pass
            try:
                self._docker_proc.wait(timeout=2)
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            self._docker_proc = None
        # Kill orphans from this specific relay
        try:
            _killed = _kill_relay_containers(self.relay_id)
            if _killed:
                self._log(f"[Relay] Cleaned {_killed} orphan container(s)")
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

    def _run_relay(self):
        """Run the WS relay connection loop."""
        # Add tools directory to path for imports
        tools_dir = _relay_tools_dir()
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)

        if self.docker_image:
            self._run_docker_relay(tools_dir)
        else:
            self._run_native_relay(tools_dir)

    def _run_native_relay(self, tools_dir):
        """Run the relay worker natively (no Docker)."""
        from pawflow_relay.worker import _ws_connect  # noqa: F401 — future direct invocation
