"""RelayThread docker relay run loop."""

import logging

import os
import threading
import time

from pawflow_relay.utils import (
    docker_cmd, translate_path, to_host_path, get_host_ip,
    find_free_port,
)

# Split out of pawflow_relay/thread.py for the <=800-line rule; composed back
# into RelayThread (invariant 2: MRO/shared state). Whole pkg is vendored via copytree.

from pawflow_relay._thread_base import _make_relay_container_name, _relay_apparmor_security_opts, _relay_runtime_root  # noqa: F401,E402


class _RelayDockerMixin:
    """docker relay run loop."""

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
            _project_root = str(_relay_runtime_root())
            _tools_dir = os.path.join(_project_root, "tools")
            _sdk_dir = os.path.join(_project_root, "docker", "pawflow_sdk")
            _pkg_dir = os.path.join(_project_root, "pawflow_relay")
            _relay_script_mounts = []
            _mount_report = []
            # (source_dir, filename) pairs — all mount onto /opt/pawflow/.
            _src_files = [
                (_tools_dir, "pawflow_relay_launcher.py"),
                (_tools_dir, "fs_actions.py"),
                (_tools_dir, "_fs_paths.py"),
                (_tools_dir, "_fs_read.py"),
                (_tools_dir, "_fs_grep.py"),
                (_tools_dir, "_fs_edit.py"),
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
                    # Relay key for conversation auto-unlock (set by
                    # `start --unlock-key`). Empty unless unlocked; lives only
                    # in the container's RAM, gone when the container stops.
                    _ef.write(
                        f"PAWFLOW_RELAY_PRIVKEY_B64="
                        f"{os.environ.get('PAWFLOW_RELAY_PRIVKEY_B64', '')}\n")
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
                "--init",
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
                # FUSE (server-fs tunnel mount at /cc_sessions): SYS_ADMIN +
                # /dev/fuse let the mount come up. AppArmor: the
                # pawflow-relay profile when loaded on the host (FUSE mounts
                # allowed only under /tmp/pf_combined_fs and /remote),
                # apparmor=unconfined fallback otherwise — docker-default
                # would block mount/umount entirely.
                "--cap-add", "SYS_ADMIN",
                "--device", "/dev/fuse",
                *_relay_apparmor_security_opts(self.docker_image),
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
                "-e", "CARGO_HOME=/opt/local/rust/cargo",
                "-e", "RUSTUP_HOME=/opt/local/rust/rustup",
                "-e", "GOPATH=/opt/local/go-path",
                "-e", "GOBIN=/opt/local/bin",
                "-e", "GOCACHE=/tmp/pawflow-go-build",
                "-e", "GOMODCACHE=/tmp/pawflow-go-mod",
                "-e", "XDG_CACHE_HOME=/tmp/pawflow-cache",
                "-e", "HF_HOME=/tmp/pawflow-cache/huggingface",
                "-e", "HUGGINGFACE_HUB_CACHE=/tmp/pawflow-cache/huggingface/hub",
                "-e", "SENTENCE_TRANSFORMERS_HOME=/tmp/pawflow-cache/sentence-transformers",
                "-e", "TRANSFORMERS_CACHE=/tmp/pawflow-cache/huggingface/transformers",
                "-e", "PATH=/opt/local/bin:/opt/local/rust/cargo/bin:/usr/local/go/bin:/opt/kotlinc/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                self.docker_image,
                "python3", "-u", "/opt/pawflow/pawflow_relay_launcher.py",
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
                "--skills-mount", "/skills",
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
            _service_reregister_requested = threading.Event()
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
                                    if self._stop_event.is_set():
                                        continue
                                    self._log(
                                        "[Relay] Relay handshake got 400; "
                                        "re-registering service without stopping Docker relay")
                                    _service_reregister_requested.set()
                    except Exception:
                        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                threading.Thread(target=_read_relay_logs, daemon=True,
                                 name="relay-log-reader").start()

                _health_interval = 30  # check relay connectivity every 30s
                _last_health = time.time()
                _consecutive_fails = 0
                _stale_disconnect_fails = 0

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
                    if _service_reregister_requested.is_set():
                        _service_reregister_requested.clear()
                        if self._stop_event.is_set():
                            break
                        try:
                            self._reregister_service()
                            _consecutive_fails = 0
                        except Exception as _rr_err:
                            self._log(f"[Relay] Service re-register failed: {_rr_err}")
                    # Periodic health check: is the relay still connected to the server?
                    if time.time() - _last_health > _health_interval:
                        _last_health = time.time()
                        if self._check_relay_connected():
                            _consecutive_fails = 0
                            _stale_disconnect_fails = 0
                        else:
                            _consecutive_fails += 1
                            _stale_disconnect_fails += 1
                            self._log(
                                f"[Relay] Health: relay not connected "
                                f"({_consecutive_fails} consecutive)")
                            if _stale_disconnect_fails >= 10:
                                self._log(
                                    "[Relay] Relay disconnected for an extended "
                                    "period; restarting Docker relay container")
                                _full_reconnect_requested.set()
                                continue
                            if _consecutive_fails >= 3:
                                if self._stop_event.is_set():
                                    break
                                self._log(
                                    "[Relay] Relay health still false; "
                                    "re-registering service without stopping Docker relay")
                                try:
                                    self._reregister_service()
                                    _consecutive_fails = 0
                                except Exception as _rr_err:
                                    self._log(f"[Relay] Service re-register failed: {_rr_err}")

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
