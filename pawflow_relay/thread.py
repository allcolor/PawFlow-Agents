"""RelayThread — orchestrates a PawFlow relay connection.

Registers the service with the server, launches Docker or native worker,
manages host helper for Docker-to-host actions, handles auto-restart.
"""
import logging

import json
import os
import secrets
import socket
import subprocess  # nosec B404
import sys
import threading
import time
from pathlib import Path

from pawflow_relay.utils import (
    docker_cmd, translate_path, to_host_path, get_host_ip,
    find_free_port, generate_relay_id, api_call,
)


def _relay_container_prefix(relay_id: str) -> str:
    return f"pf-{relay_id[:12].replace('.', '-').replace('_', '-')}"


def _make_relay_container_name(relay_id: str, purpose: str) -> str:
    return f"{_relay_container_prefix(relay_id)}-{purpose}-{secrets.token_hex(4)}"


def _is_windows_drive_absolute_path(path: str) -> bool:
    raw = str(path or "").replace("\\", "/")
    return len(raw) >= 3 and raw[1] == ":" and raw[2] == "/"


def _is_host_absolute_path(path: str) -> bool:
    raw = str(path or "").replace("\\", "/")
    return raw.startswith("/") or raw.startswith("//") or _is_windows_drive_absolute_path(raw)


def _host_abs_path(raw_path: str, root_dir: str) -> str:
    raw = str(raw_path or ".").replace("\\", "/")
    if raw.startswith("fs://"):
        parts = raw[5:].split("/", 1)
        raw = parts[1] if len(parts) > 1 else "."
    root = Path(root_dir).resolve()
    if raw == "/workspace":
        target = root
    elif raw.startswith("/workspace/"):
        target = root / raw[len("/workspace/"):]
    elif _is_windows_drive_absolute_path(raw):
        return raw
    elif _is_host_absolute_path(raw):
        target = Path(raw).resolve()
    else:
        target = (root / raw).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            raise ValueError(f"Path traversal blocked: {raw_path}")
    return str(target)


def _kill_relay_containers(relay_id: str) -> int:
    prefix = _relay_container_prefix(relay_id)
    try:
        result = subprocess.run(  # nosec B603
            docker_cmd() + [
                "ps", "-a", "--filter", f"name={prefix}",
                "--format", "{{.ID}}\t{{.Names}}",
            ],
            capture_output=True, text=True, timeout=10)
    except Exception:
        return 0
    killed = 0
    for line in result.stdout.strip().splitlines():
        if not line.strip():
            continue
        container_id = line.split("\t", 1)[0]
        try:
            subprocess.run(docker_cmd() + ["rm", "-f", container_id],  # nosec B603
                           capture_output=True, timeout=10)
            killed += 1
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
    return killed


def cleanup_relay_containers(relay_id: str) -> int:
    """Remove Docker containers owned by one relay id."""
    return _kill_relay_containers(relay_id)


class RelayThread:
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
        tools_dir = str(Path(__file__).resolve().parent.parent / "tools")
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)

        if self.docker_image:
            self._run_docker_relay(tools_dir)
        else:
            self._run_native_relay(tools_dir)

    def _run_native_relay(self, tools_dir):
        """Run the relay worker natively (no Docker)."""
        from pawflow_relay.worker import _ws_connect  # noqa: F401 — future direct invocation

    def _run_docker_relay(self, tools_dir):
        """Run the relay inside a Docker container with auto-restart."""
        # Start host helper (TCP server for host-level commands)
        host_helper_port = find_free_port()
        self._host_helper_thread = threading.Thread(
            target=self._run_host_helper, args=(host_helper_port,),
            daemon=True, name="pawflow-host-helper")
        self._host_helper_thread.start()

        import subprocess as _sp  # nosec B404

        restart_delay = 1
        max_restart_delay = 60

        while not self._stop_event.is_set():
            # Guard: every restart MUST carry a non-empty token. An empty
            # value has caused two distinct failure modes in the wild:
            # (a) the Windows wsl.exe wrapper silently drops "" args so
            #     the inner argparse sees `--token --relay-id …` and
            #     rejects with "expected one argument", (b) even if the
            #     "" reaches the container, pawflow_relay.py's mode
            #     selector (tools/pawflow_relay.py:2371) treats empty
            #     token as "enter auto-registration" and tries to open
            #     a browser from inside Docker, which has no display.
            # Better to stop the loop loud than to burn restart budget.
            if not self.ws_token:
                self._log(
                    "[Relay] FATAL: ws_token is empty at restart — "
                    "RelayThread.start() must run before _run_docker_relay. "
                    "Stopping restart loop.")
                break
            self._docker_container = _make_relay_container_name(self.relay_id, "relay")
            from urllib.parse import urlparse as _up
            _parsed = _up(self.server_url)
            _scheme = 'wss' if _parsed.scheme == 'https' else 'ws'
            _server_host = _parsed.hostname or 'localhost'
            _server_port = _parsed.port or (443 if _parsed.scheme == 'https' else 80)
            # Resolve the hostname via the host's name resolver (which honors
            # /etc/hosts or %WINDIR%\System32\drivers\etc\hosts) so custom
            # aliases for localhost (e.g. pawflow.allcolor.org → 127.0.0.1)
            # are caught here. The Docker container has its own DNS and
            # wouldn't see the host's hosts file, so resolving inside it
            # would pick the public IP and hit NAT hairpin.
            #
            # Fix: keep the original hostname in the URL (so TLS cert
            # validation works — the cert is issued for that hostname),
            # and add an /etc/hosts entry inside the container via
            # --add-host, pointing the hostname to the host-reachable IP.
            _ws_host_override = ""
            try:
                import socket as _socket
                _resolved_ip = _socket.gethostbyname(_server_host)
            except Exception:
                _resolved_ip = ""
            if _resolved_ip and _resolved_ip.startswith("127."):
                _host_ip = get_host_ip()
                _ws_host_override = f"{_server_host}:{_host_ip}"
                self._log(
                    f"[Relay] '{_server_host}' → {_resolved_ip} "
                    f"(loopback); adding --add-host "
                    f"{_server_host}:{_host_ip} so the container reaches "
                    f"the host without breaking TLS cert validation")
            elif not _resolved_ip and _server_host in ('localhost', '127.0.0.1'):
                _host_ip = get_host_ip()
                _ws_host_override = f"{_server_host}:{_host_ip}"
            ws_url = f"{_scheme}://{_server_host}:{_server_port}/ws/relay/{self.relay_id}"
            self._desktop_host_port = find_free_port() if self.allow_remote_desktop else 0
            self._audio_host_port = find_free_port() if self.allow_remote_desktop else 0
            _tok_masked = (self.ws_token[:4] + "****") if len(self.ws_token) > 4 else "****"
            self._log(
                f"[Relay] Docker launch: token={_tok_masked} "
                f"(len={len(self.ws_token)}), "
                f"session_token={'set' if self.session_token else 'EMPTY'}, "
                f"gateway_cookie={'set' if self.gateway_cookie else 'EMPTY'}, "
                f"ws_url={ws_url}")
            # In a source checkout, dev-mount relay scripts from the host so
            # /opt/pawflow reflects the current tree. In standalone desktop
            # installs those files are not next to the app, so the container
            # falls back to the scripts baked into the relay image.
            _project_root = os.path.dirname(
                os.path.dirname(os.path.abspath(__file__)))
            _tools_dir = os.path.join(_project_root, "tools")
            _sdk_dir = os.path.join(_project_root, "docker", "pawflow_sdk")
            _pkg_dir = os.path.join(_project_root, "pawflow_relay")
            _relay_script_mounts = []
            _mount_report = []
            # (source_dir, filename) pairs — all mount onto /opt/pawflow/.
            _src_files = [
                (_tools_dir, "pawflow_relay_launcher.py"),
                (_tools_dir, "fs_actions.py"),
                (_tools_dir, "fs_exec.py"),
                (_tools_dir, "fs_screen.py"),
                (_tools_dir, "fs_mcp.py"),
                (_tools_dir, "fs_common.py"),
                (_tools_dir, "fs_http.py"),
                (_tools_dir, "audio_capture.py"),
                (_tools_dir, "screen_actions.py"),
                (_sdk_dir, "pawflow.py"),
            ]
            for _src_dir, _rf in _src_files:
                _src = os.path.join(_src_dir, _rf)
                if os.path.exists(_src):
                    _translated = translate_path(to_host_path(_src))
                    _relay_script_mounts += [
                        "-v", f"{_translated}:/opt/pawflow/{_rf}:ro"]
                    _mount_report.append(f"{_rf}→{_translated}")
                else:
                    _mount_report.append(f"{_rf}:MISSING({_src})")
            # Mount the pawflow_relay/ package itself next to the script, so
            # the worker (stdlib-only today) can gradually migrate onto
            # `from pawflow_relay.* import ...` without losing the dev-mount
            # live-reload property. Python imports `pawflow_relay/` (package,
            # with __init__.py) before `pawflow_relay.py` (single-file
            # module) when both sit in the same directory.
            if os.path.isdir(_pkg_dir):
                _translated_pkg = translate_path(to_host_path(_pkg_dir))
                _relay_script_mounts += [
                    "-v", f"{_translated_pkg}:/opt/pawflow/pawflow_relay:ro"]
                _mount_report.append(f"pawflow_relay/→{_translated_pkg}")
            else:
                _mount_report.append(f"pawflow_relay/:MISSING({_pkg_dir})")
            self._log(
                f"[Relay] dev-mount scripts: {'; '.join(_mount_report)}")

            _extra_add_host = (
                ["--add-host", _ws_host_override] if _ws_host_override else [])

            # Gateway key + cookie may contain shell metacharacters
            # ((, ), !, ", ', …) that trip bash re-parsing when wsl.exe
            # forwards the command. Write them to a temp env-file instead.
            # Docker reads `--env-file` verbatim (KEY=VALUE lines, no shell
            # expansion), which sidesteps the quoting mess entirely.
            import tempfile as _tempfile
            _env_file_fd, _env_file_path = _tempfile.mkstemp(
                prefix="pf-relay-env-", suffix=".env")
            try:
                with os.fdopen(_env_file_fd, "w", encoding="utf-8") as _ef:
                    _ef.write(f"PAWFLOW_GATEWAY_KEY={self.gateway_key or ''}\n")
                    _ef.write(
                        f"PAWFLOW_GATEWAY_COOKIE="
                        f"{self.gateway_cookie if not self.gateway_key else ''}\n")
                try:
                    os.chmod(_env_file_path, 0o600)
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            except Exception:
                try:
                    os.close(_env_file_fd)
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                raise
            # Best-effort cleanup of any previous env-file from a prior
            # restart iteration (avoid leaking secrets in temp dir).
            _prev_env = getattr(self, "_env_file_path", "")
            if _prev_env and _prev_env != _env_file_path:
                try:
                    os.unlink(_prev_env)
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            self._env_file_path = _env_file_path
            _env_file_container = translate_path(to_host_path(_env_file_path))

            _desktop_publish_args = []
            if self.allow_remote_desktop:
                _desktop_publish_args = [
                    "--publish", f"{self._desktop_host_port}:6080",
                    "--publish", f"{self._audio_host_port}:6180",
                    "-e", "PAWFLOW_DESKTOP_NOVNC_PORT=6080",
                ]

            _relay_permission_args = []
            if self.allow_exec:
                _relay_permission_args.append("--allow-exec")
            if self.allow_remote_desktop:
                _relay_permission_args += ["--allow-automation", "--allow-local-screen"]

            docker_run_cmd = docker_cmd() + [
                "run", "--rm",
                "--name", self._docker_container,
                "--env-file", _env_file_container,
                "-v", f"{translate_path(to_host_path(self.directory))}:/workspace",
                "-v", f"pawflow_home_{self.relay_id}:/home/pawflow",
                *_relay_script_mounts,
                "--add-host", "host.docker.internal:host-gateway",
                *_extra_add_host,
                "--cpus", self.docker_cpus, "--memory", self.docker_memory,
                "--shm-size", "512m",
                # NOTE: do NOT pass --security-opt no-new-privileges here.
                # It disables the setuid bit on /usr/bin/fusermount3, which
                # the unprivileged pawflow user needs to mount the server-fs
                # FUSE endpoint at /cc_sessions.
                # FUSE (server-fs tunnel mount at /cc_sessions): SYS_ADMIN lets
                # pyfuse3 call mount() directly, /dev/fuse is the kernel char
                # device the FUSE lib opens, and apparmor:unconfined stops
                # Ubuntu's docker-default profile from blocking mount/umount.
                "--cap-add", "SYS_ADMIN",
                "--device", "/dev/fuse",
                "--security-opt", "apparmor:unconfined",
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
                "-e", f"PAWFLOW_SESSION_TOKEN={self.session_token}",
                *_desktop_publish_args,
                "-e", f"PAWFLOW_HOST_WORKDIR={self.directory.replace(chr(92), '/')}",
                "-e", "HOME=/home/pawflow",
                "-e", "USER=pawflow",
                "-e", "PATH=/home/pawflow/.cargo/bin:/home/pawflow/go/bin:/usr/local/go/bin:/opt/kotlinc/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                self.docker_image,
                "python3", "/opt/pawflow/pawflow_relay_launcher.py",
                "--server", ws_url,
                # token_urlsafe() can generate values beginning with "--".
                # Pass the token with --token=VALUE so argparse cannot
                # mistake a random token for another option during reconnect.
                f"--token={self.ws_token}",
                "--relay-id", self.relay_id,
                "--dir", "/workspace",
                *_relay_permission_args,
                "--server-mount", "/cc_sessions",
                "--filestore-mount", "/filestore",
            ] + (["--allow-local"] if self.allow_local else [])
            # One-shot diagnostic: dump the -v flags so we can confirm
            # the relay-script dev-mount lands on /opt/pawflow/*.py.
            _v_args = [a for i, a in enumerate(docker_run_cmd)
                       if i > 0 and docker_run_cmd[i - 1] == "-v"]
            self._log(
                f"[Relay] docker run -v flags ({len(_v_args)}): "
                f"{' | '.join(_v_args)}")
            _start_time = time.time()
            _full_reconnect_requested = threading.Event()
            try:
                # Merge stdout into stderr so we capture *everything* the
                # container emits in a single reader. Python's print()
                # defaults to stdout; anything written there would be lost
                # if we only drained stderr.
                self._docker_proc = _sp.Popen(  # nosec B603
                    docker_run_cmd, stdin=_sp.DEVNULL,
                    stdout=_sp.PIPE, stderr=_sp.STDOUT)

                def _read_relay_logs():
                    # Use the same sink as _log() — a file if log_file
                    # is configured (keeps pawflow_cli UI clean), else
                    # the unpatched stderr (so pawflow_cli's [FSRelay]-
                    # filter doesn't eat diagnostics).
                    try:
                        for line in self._docker_proc.stdout:
                            msg = line.decode("utf-8", errors="replace").rstrip()
                            if msg:
                                out = self._log_out()
                                out.write(f"[Relay] {msg}\n")
                                try:
                                    out.flush()
                                except Exception:
                                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                                if "HTTP/1.1 400 Bad Request" in msg:
                                    self._log(
                                        "[Relay] Relay handshake got 400; "
                                        "requesting full stop/start reconnect")
                                    _full_reconnect_requested.set()
                    except Exception:
                        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                threading.Thread(target=_read_relay_logs, daemon=True,
                                 name="relay-log-reader").start()

                _health_interval = 30  # check relay connectivity every 30s
                _last_health = time.time()
                _consecutive_fails = 0

                while not self._stop_event.is_set():
                    try:
                        self._docker_proc.wait(timeout=1)
                        break
                    except _sp.TimeoutExpired:
                        pass
                    if _full_reconnect_requested.is_set():
                        try:
                            self._docker_proc.kill()
                        except Exception:
                            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                        break
                    # Periodic health check: is the relay still connected to the server?
                    if time.time() - _last_health > _health_interval:
                        _last_health = time.time()
                        if self._check_relay_connected():
                            _consecutive_fails = 0
                        else:
                            _consecutive_fails += 1
                            self._log(
                                f"[Relay] Health: relay not connected "
                                f"({_consecutive_fails} consecutive)")
                            if _consecutive_fails >= 3:
                                self._log(
                                    "[Relay] Relay disconnected; requesting "
                                    "full stop/start reconnect")
                                _full_reconnect_requested.set()
                                try:
                                    self._docker_proc.kill()
                                except Exception:
                                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                                break

                if self._stop_event.is_set():
                    break

                if _full_reconnect_requested.is_set():
                    try:
                        self._restart_service_registration()
                        restart_delay = 1
                    except Exception as _fr_err:
                        self._log(f"[Relay] Full reconnect failed: {_fr_err}")

                rc = self._docker_proc.poll()
                if rc and rc != 0:
                    stderr = ""
                    try:
                        stderr = self._docker_proc.stderr.read().decode("utf-8", errors="replace")
                    except Exception:
                        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                    self._log(f"[Relay] Docker relay exited (code {rc}), restarting in {restart_delay}s")
                    if stderr:
                        self._log(f"[Relay] {stderr[:500]}")
                else:
                    self._log(f"[Relay] Docker relay exited (code 0), restarting in {restart_delay}s")
            except Exception as e:
                self._log(f"[Relay] Docker error: {e}, retrying in {restart_delay}s")
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
            self._log("[Relay] Restarting Docker relay container...")

    # ── Host helper (TCP server for Docker-to-host commands) ──────────

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

        tools_dir = str(Path(__file__).resolve().parent.parent / "tools")
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
        import shutil

        if hasattr(self, '_local_desktop_procs') and self._local_desktop_procs:
            alive = all(p.poll() is None for p in self._local_desktop_procs)
            if alive:
                return {"novnc_port": self._local_desktop_novnc_port, "already_running": True}
            for p in self._local_desktop_procs:
                try: p.kill()
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
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
                _sp.run([sys.executable, "-m", "websockify", "--help"],  # nosec B603
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
                try: p.wait(timeout=5)
                except Exception: p.kill()
            self._local_desktop_procs = None
            self._log("[Relay] Local desktop stopped")
            return {"ok": True}
        return {"was_running": False}

    def _host_screen_tool(self, req, action):
        """Forward screen automation actions to the host's screen tools."""
        tools_dir = str(Path(__file__).resolve().parent.parent / "tools")
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
                import pty as _pty_mod, fcntl, struct, termios
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
        import shutil

        code_server = shutil.which("code-server")
        if not code_server:
            return {"error": "code-server not installed on host. Install with: npm install -g code-server"}

        port = find_free_port()
        try:
            proc = _sp.Popen(  # nosec B603
                [code_server, "--port", str(port), "--auth", "none",
                 "--bind-addr", f"127.0.0.1:{port}", self.directory],
                stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
            if not hasattr(self, '_local_code_server'):
                self._local_code_server = {}
            self._local_code_server[port] = proc
            return {"port": port}
        except Exception as e:
            return {"error": f"Failed to start code-server: {e}"}
