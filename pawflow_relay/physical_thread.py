"""One Docker owner for a static group of logical relay connections."""

from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess  # nosec B404
import threading
from urllib.parse import quote

from pawflow_relay._physical_runtime import SECCOMP_PROFILE
from pawflow_relay._thread_base import _relay_runtime_root
from pawflow_relay.physical_plan import plan_physical_relay
from pawflow_relay.thread import RelayThread
from pawflow_relay.utils import (
    find_free_port,
    get_host_ip,
    to_host_path,
    translate_path,
)


class PhysicalRelayThread(RelayThread):
    """Reuse the existing Docker retry loop without registering a physical service."""

    def __init__(self, physical: dict, server: dict):
        self.plan = plan_physical_relay(physical["physical_id"], physical["workspaces"])
        self.physical_name = physical["name"]
        first = self.plan.exports[0]
        common = {
            "server_url": server["url"], "session_token": server["session_token"],
            "username": server["username"], "docker_image": self.plan.docker_image,
            "gateway_cookie": server.get("gateway_cookie", ""),
            "gateway_key": server.get("gateway_key", ""),
        }
        super().__init__(directory=first.root_source, relay_id=first.relay_id, **common)
        self._physical_id = self.plan.physical_id
        self._helper_lifecycle_lock = threading.RLock()
        self.members = []
        for export in self.plan.exports:
            member = RelayThread(
                directory=export.root_source, relay_id=export.relay_id,
                read_only=export.mode == "ro", allow_exec=export.allow_exec,
                allow_remote_desktop=export.allow_remote_desktop,
                allow_local=export.allow_local,
                allow_service_tunnels=export.allow_service_tunnels, **common)
            member._stop_event = self._stop_event
            member._log = lambda message, rid=export.relay_id: self._log(f"[{rid}] {message}")
            self.members.append(member)
        self.allow_exec = first.allow_exec
        self.allow_remote_desktop = first.allow_remote_desktop
        self.allow_local = first.allow_local
        self.allow_service_tunnels = first.allow_service_tunnels
        self.read_only = first.mode == "ro"

    def start(self):
        self._kill_docker()
        try:
            self._restart_service_registration()
            self._thread = threading.Thread(
                target=self._run_relay, daemon=True, name="pawflow-physical-relay")
            self._thread.start()
        except BaseException:
            self.stop()
            raise

    def _restart_service_registration(self):
        for member in self.members:
            member._restart_service_registration()
        self.ws_token = self.members[0].ws_token

    def _reregister_service(self):
        for member in self.members:
            member._reregister_service()

    def _check_relay_connected(self):
        try:
            data = self._api("POST", "/api/ui", {"action": "relay_list_available"})
            connected = {
                item["relay_id"] for item in data.get("relays", [])
                if item.get("connected")
            }
            return all(member.relay_id in connected for member in self.members)
        except Exception:  # noqa: BLE001 - transport health follows RelayThread's boundary.
            return False

    def stop(self):
        self._stop_event.set()
        self._kill_docker()
        for member in self.members:
            if member._registered:
                try:
                    member._api("POST", "/api/ui", {
                        "action": "service_uninstall", "service_id": member.relay_id})
                except Exception:  # noqa: BLE001 - continue unregistering the rest of the group.
                    self._log(f"[Relay] Could not unregister {member.relay_id}")
                member._registered = False

    def _kill_docker(self):
        with self._helper_lifecycle_lock:
            helpers = [self, *self.members[1:]]
            for helper in helpers:
                event = getattr(helper, "_host_helper_stop_event", None)
                if event is not None:
                    event.set()
            super()._kill_docker()
            for helper in helpers:
                helper._stop_windows_host_bridge()
                thread = getattr(helper, "_host_helper_thread", None)
                if thread is not None:
                    if thread.is_alive():
                        thread.join(timeout=3)
                    if thread.is_alive():
                        raise RuntimeError(f"Host helper did not stop for {helper.relay_id}")
                    helper._host_helper_thread = None
                for conn in tuple(getattr(helper, "_host_helper_connections", ())):
                    try:
                        conn.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    conn.close()
                for terminal in tuple(getattr(helper, "_local_terminals", {}).values()):
                    try:
                        terminal["kill"]()
                    except OSError:
                        pass
            env_file = getattr(self, "_env_file_path", "")
            if env_file:
                try:
                    os.unlink(env_file)
                except FileNotFoundError:
                    pass
                self._env_file_path = ""

    def _prepare_docker_helpers(self, host_helper_port):
        with self._helper_lifecycle_lock:
            for helper in [self, *self.members[1:]]:
                if self._stop_event.is_set():
                    return
                helper._host_helper_stop_event = threading.Event()
                helper._host_helper_connections = set()
                if helper is self:
                    super()._prepare_docker_helpers(host_helper_port)
                else:
                    helper._helper_port = find_free_port()
                    helper._host_helper_token = secrets.token_urlsafe(32)
                    helper._prepare_docker_helpers(helper._helper_port)
            self._check_docker_helpers()

    def _check_docker_helpers(self):
        with self._helper_lifecycle_lock:
            if self._stop_event.is_set():
                return
            for helper in [self, *self.members[1:]]:
                thread = getattr(helper, "_host_helper_thread", None)
                bridge = helper._host_bridge_proc
                if (thread is None or not thread.is_alive()
                        or helper._host_helper_error is not None
                        or (bridge is not None and bridge.poll() is not None)):
                    raise RuntimeError(f"Host helper stopped for {helper.relay_id}")

    def _cleanup_docker_helpers(self):
        self._kill_docker()

    def _group_launch(self, original: list[str]) -> tuple[list[str], dict]:
        """Retain common image/runtime options and provide private mounts/config."""
        image_index = original.index(self.docker_image)
        worker_command = original[image_index + 1:]
        ws_url = worker_command[worker_command.index("--server") + 1]
        common, environment = [], {}
        index = 0
        while index < image_index:
            option = original[index]
            if option in ("-v", "--env-file", "--publish", "-e", "--security-opt"):
                value = original[index + 1]
                index += 2
                if option == "-e":
                    key, _, setting = value.partition("=")
                    environment[key] = setting
                    if key.startswith("PAWFLOW_"):
                        continue
                if option in ("--env-file", "--publish"):
                    continue
                if option == "-v" and value.endswith((":/workspace", ":/home/pawflow")):
                    continue
                if option == "--security-opt" and value.startswith("apparmor="):
                    continue
                common.extend((option, value))
            else:
                common.append(option)
                index += 1
        common.extend([
            "--interactive", "--workdir", "/", "--entrypoint", "/usr/bin/tini",
            "--volume", "/run/pawflow-rootfs", "--cap-add", "NET_ADMIN",
            "--device", "/dev/net/tun", "--security-opt", "apparmor=unconfined",
            "--security-opt", "seccomp=" + translate_path(str(SECCOMP_PROFILE)),
        ])
        exports = []
        for index, (export, member) in enumerate(zip(self.plan.exports, self.members)):
            common.extend([
                "--volume", f"{translate_path(to_host_path(export.root_source))}:{export.root_mount}:{export.mode}",
                "--volume", f"{export.home_volume}:{export.home_mount}",
            ])
            if index == 0:
                helper = environment["PAWFLOW_HOST_HELPER"]
                helper_token = self._host_helper_token
            else:
                helper_port = member._helper_port
                if os.name == "nt":
                    bridge_port = find_free_port()
                    member._start_windows_host_bridge(
                        str(_relay_runtime_root()), bridge_port, helper_port, subprocess)
                    helper_port = bridge_port
                helper = f"host.docker.internal:{helper_port}"
                helper_token = member._host_helper_token
            command = [
                "python3", "-u", "/opt/pawflow/pawflow_relay_launcher.py",
                "--server", ws_url.rsplit("/", 1)[0] + "/" + quote(member.relay_id, safe=""),
                f"--token={member.ws_token}", "--relay-id", member.relay_id,
                "--dir", "/workspace", "--server-mount", "/cc_sessions",
                "--filestore-mount", "/filestore", "--skills-mount", "/skills",
            ]
            for enabled, flags in (
                (export.allow_exec, ["--allow-exec"]),
                (export.allow_remote_desktop, ["--allow-automation", "--allow-local-screen"]),
                (export.allow_local, ["--allow-local"]),
                (export.allow_service_tunnels, ["--allow-service-tunnels"]),
            ):
                if enabled:
                    command.extend(flags)
            exports.append({
                "relay_id": export.relay_id, "root_mount": export.root_mount,
                "home_mount": export.home_mount, "mode": export.mode, "command": command,
                "environment": {
                    "PAWFLOW_GATEWAY_KEY": member.gateway_key,
                    "PAWFLOW_GATEWAY_COOKIE": "" if member.gateway_key else member.gateway_cookie,
                    "PAWFLOW_SESSION_TOKEN": member.session_token,
                    "PAWFLOW_RELAY_PRIVKEY_B64": os.environ.get("PAWFLOW_RELAY_PRIVKEY_B64", ""),
                    "PAWFLOW_HOST_HELPER": helper,
                    "PAWFLOW_HOST_HELPER_TOKEN": helper_token,
                    "PAWFLOW_WINDOWS_HOST_IP": get_host_ip(),
                    "PAWFLOW_HOST_WORKDIR": export.root_source.replace(chr(92), "/"),
                    "PAWFLOW_DESKTOP_NOVNC_PORT": "6080",
                },
            })
        common.extend([
            self.docker_image, "--", "python3", "-I", "-u",
            "/opt/pawflow/pawflow_relay/_physical_runtime.py",
        ])
        return common, {"exports": exports}

    def _spawn_docker_process(self, command):
        with self._helper_lifecycle_lock:
            if self._stop_event.is_set():
                raise RuntimeError("Physical relay stopped before launch")
            return self._spawn_group_process(command)

    def _spawn_group_process(self, command):
        command, config = self._group_launch(command)
        if command[:2] == ["wsl", "docker"]:
            self._load_wsl_tun_module()
        env_file = getattr(self, "_env_file_path", "")
        if env_file:
            os.unlink(env_file)
            self._env_file_path = ""
        process = subprocess.Popen(  # nosec B603
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self._docker_proc = process
        try:
            process.stdin.write(json.dumps(config).encode("utf-8"))
            process.stdin.close()
        except BaseException:
            process.kill()
            process.wait(timeout=10)
            raise
        return process

    def _load_wsl_tun_module(self):
        """Load ``tun`` in the WSL kernel before slirp4netns needs it.

        WSL 6.x kernels build it as a module (``CONFIG_TUN=m``) and nothing
        loads it on demand from inside a container, so ``/dev/net/tun`` exists
        but opening it fails with ENODEV. A failure is only logged: the
        container's own preflight then reports the exact remedy.
        """
        try:
            result = subprocess.run(  # nosec B603 B607
                ["wsl", "-u", "root", "--", "modprobe", "tun"],
                capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as exc:
            self._log(f"[Relay] modprobe tun in WSL failed: {exc}")
            return
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            self._log(f"[Relay] modprobe tun in WSL failed ({result.returncode}): {detail}")
