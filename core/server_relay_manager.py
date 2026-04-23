"""Server-Side Relay Manager.

Spawns a Docker relay container per conversation, giving each conversation
a persistent workspace on the server without requiring a local install.

Each container:
- Runs tools/pawflow_relay.py in manual mode (env vars, not CLI args)
- Mounts a named Docker volume: pawflow_ws_{conv_id}
- Connects back to the main HTTPListenerService via ws(s)://host:<main>/ws/relay/<id>
- Has exec enabled (--allow-exec)

Metadata is stored in ConversationStore extra:
  key="server_relay" value={relay_id, container_id, token, user_id, ws_url, ...}

Max 1 server relay per conversation (enforced in spawn).
"""

import logging
import secrets
import subprocess
import threading
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ── Default values for server relay settings ────────────────────────────────
# These are read at runtime from data/config/global_parameters.json
# so they can be changed from the PawFlow UI without restarting.
#
# Keys in global_parameters.json:
#   server_relay_image      — Docker image (default: pawflow-relay-dev:latest)
#   server_relay_tools_dir  — Path to tools/ dir on the server (relative to CWD),
#                               mounted as /opt/pawflow/ so relay changes are live
#                               without image rebuild (default: tools)
#   server_relay_workspace  — Workspace dir in container (default: /workspace)
#   server_relay_cpus       — CPU limit (default: 2)
#   server_relay_memory     — Memory limit (default: 2g)

_DEFAULTS = {
    "server_relay_image":     "pawflow-relay-dev:latest",
    "server_relay_tools_dir": "tools",
    "server_relay_workspace": "/workspace",
    "server_relay_cpus":      "2",
    "server_relay_memory":    "2g",
}


def _cfg(key: str) -> str:
    """Read a server relay setting from global_parameters.json (live, no cache)."""
    try:
        from core.expression import _load_global_parameters
        return _load_global_parameters().get(key, _DEFAULTS[key])
    except Exception:
        return _DEFAULTS[key]


from core.docker_utils import docker_cmd, get_host_ip, to_host_path
from pawflow_relay.utils import find_free_port


def _volume_name(conv_id: str) -> str:
    return f"pawflow_ws_{conv_id}"


def _container_name(conv_id: str) -> str:
    return f"pawflow-relay-srv-{conv_id[:16]}"


def _relay_id_for_conv(conv_id: str) -> str:
    return f"srv_ws_{conv_id[:16]}"


class ServerRelayManager:
    """Manages server-side relay containers (one per conversation)."""

    _instance: Optional["ServerRelayManager"] = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "ServerRelayManager":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def spawn(
        self,
        conv_id: str,
        user_id: str,
    ) -> Dict[str, Any]:
        """Spawn a server relay for this conversation.

        Returns metadata dict: {relay_id, container_id, ws_url, ...}.
        Raises if relay already exists or Docker unavailable.
        """
        from core.conversation_store import ConversationStore

        store = ConversationStore.instance()
        existing = store.get_extra(conv_id, "server_relay")
        if existing and isinstance(existing, dict) and existing.get("relay_id"):
            # Check if the container is actually running
            cid = existing.get("container_id", "")
            if cid and self._is_container_running(cid):
                raise ValueError(
                    f"A server workspace already exists for this conversation: "
                    f"{existing['relay_id']}"
                )
            # Container is dead — clean up and re-spawn
            logger.info("Dead server relay found for conv %s — re-spawning", conv_id)
            self._cleanup_container(existing.get("container_id", ""), remove=True)

        # VNC / audio host ports are real Docker publish ports — must be free.
        desktop_host_port = find_free_port()
        audio_host_port = find_free_port()
        token = secrets.token_urlsafe(32)
        relay_id = _relay_id_for_conv(conv_id)
        path = f"/ws/relay/{relay_id}"
        container_name = _container_name(conv_id)
        volume = _volume_name(conv_id)
        host_ip = get_host_ip()

        # Resolve the REAL main HTTPListenerService port + TLS state. The
        # relay container registers its WS route on that listener, so the URL
        # we hand it must point at the main listener — not a random free port.
        from services.http_listener_service import HTTPListenerService
        _listeners = HTTPListenerService.all_instances()
        if not _listeners:
            raise RuntimeError(
                "Cannot spawn server relay: no HTTPListenerService running. "
                "Start the main listener first.")
        _main_listener = next(iter(_listeners.values()))
        main_port = _main_listener._port
        ws_scheme = "wss" if _main_listener.is_ssl else "ws"

        # Read config live from global_parameters.json
        relay_image = _cfg("server_relay_image")
        relay_tools_dir = _cfg("server_relay_tools_dir")  # relative to server CWD
        relay_workspace = _cfg("server_relay_workspace")
        relay_cpus = _cfg("server_relay_cpus")
        relay_memory = _cfg("server_relay_memory")

        # Resolve tools/ dir to absolute path (relative to server CWD)
        import os as _os
        tools_abs = _os.path.abspath(relay_tools_dir)
        # In DinD, translate container path to host path for Docker daemon
        tools_host = to_host_path(tools_abs)
        # tools/ is mounted as /opt/pawflow/ — same location as in the image.
        # This means relay changes are live without rebuilding the image.
        _TOOLS_IN_CONTAINER = "/opt/pawflow"
        _SCRIPT_IN_CONTAINER = f"{_TOOLS_IN_CONTAINER}/pawflow_relay_launcher.py"

        # Also mount pawflow_relay/ (the package) inside /opt/pawflow so the
        # worker script can `from pawflow_relay.* import ...` as code moves
        # out of the monolithic tools/pawflow_relay.py. Python's file finder
        # prefers a package dir over a single-file module with the same name.
        _pkg_abs = _os.path.abspath(
            _os.path.join(_os.path.dirname(tools_abs), "pawflow_relay"))
        _pkg_host = to_host_path(_pkg_abs) if _os.path.isdir(_pkg_abs) else ""

        # Register the relay service on the server BEFORE spawning the container.
        # RelayService.connect() registers /ws/relay/<service_id> on the main
        # HTTPListenerService — no separate port or path to configure.
        self._install_relay_service(user_id, relay_id, token)

        ws_url_for_container = f"{ws_scheme}://{host_ip}:{main_port}{path}"

        # Spawn the Docker container
        # Mount tools/ → /opt/pawflow/ so all relay modules (fs_actions, fs_exec, …)
        # are live from the server filesystem — no image rebuild needed.
        docker_run_args = [
            "--rm",
            "--detach",
            "--name", container_name,
            "--volume", f"{volume}:{relay_workspace}",
            "--volume", f"pawflow_home_{relay_id}:/home/pawflow",
            "--volume", f"{tools_host}:{_TOOLS_IN_CONTAINER}:ro",
            *(["--volume", f"{_pkg_host}:{_TOOLS_IN_CONTAINER}/pawflow_relay:ro"]
              if _pkg_host else []),
            "--add-host", "host.docker.internal:host-gateway",
            "--cpus", relay_cpus,
            "--memory", relay_memory,
            # FUSE (server-fs tunnel mount at /cc_sessions): SYS_ADMIN lets
            # pyfuse3 call mount() directly, /dev/fuse is the kernel char
            # device the FUSE lib opens, and apparmor:unconfined stops
            # Ubuntu's docker-default profile from blocking mount/umount.
            "--cap-add", "SYS_ADMIN",
            "--device", "/dev/fuse",
            "--security-opt", "apparmor:unconfined",
            "--env", f"PAWFLOW_RELAY_SERVER={ws_url_for_container}",
            "--env", f"PAWFLOW_RELAY_TOKEN={token}",
            "--env", f"PAWFLOW_RELAY_ID={relay_id}",
            "--env", f"PAWFLOW_RELAY_DIR={relay_workspace}",
            "--env", "PAWFLOW_RELAY_ALLOW_EXEC=1",
            "--env", "PAWFLOW_SERVER_MOUNT=/cc_sessions",
            "--publish", f"{desktop_host_port}:6080",
            "--publish", f"{audio_host_port}:6180",
            "--env", "PAWFLOW_DESKTOP_NOVNC_PORT=6080",
            "--env", "HOME=/home/pawflow",
            "--env", "USER=pawflow",
            "--env", "PATH=/home/pawflow/.cargo/bin:/home/pawflow/go/bin:/usr/local/go/bin:/opt/kotlinc/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        ]

        # NOTE: no docker.sock bind-mount. The server-spawned relay lives in
        # an isolated named volume (pawflow_data_<conv_id>); it has no host
        # path to bind-mount into a nested container, so spawn_relay / child-
        # relay features are meaningless here. Exposing docker.sock would
        # grant root-on-host via `docker run -v /:/host --privileged` for no
        # functional gain.

        docker_run_args.extend([
            relay_image,
            "python3", _SCRIPT_IN_CONTAINER,
        ])
        cmd = docker_cmd() + ["run"] + docker_run_args
        logger.info("Spawning server relay container: %s  cmd=%s", container_name, cmd)
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            # Cleanup the service we just installed
            self._uninstall_relay_service(user_id, relay_id)
            raise RuntimeError(
                f"Failed to start relay container: {result.stderr.strip()}"
            )

        container_id = result.stdout.strip()
        logger.info("Server relay container started: %s (%s)", container_name, container_id[:12])

        metadata = {
            "relay_id": relay_id,
            "container_id": container_id,
            "container_name": container_name,
            "token": token,
            "user_id": user_id,
            "ws_url": ws_url_for_container,
            "volume": volume,
            "desktop_host_port": desktop_host_port,
            "audio_host_port": audio_host_port,
        }
        store.set_extra(conv_id, "server_relay", metadata)
        # Auto-link this relay to the conversation
        try:
            from core.relay_bindings import link_relay
            link_relay(conv_id, relay_id)
        except Exception as e:
            logger.warning("Failed to auto-link relay %s to conv %s: %s", relay_id, conv_id, e)
        logger.info("Server relay spawned for conv %s: %s", conv_id, relay_id)
        return metadata

    def destroy(self, conv_id: str) -> bool:
        """Stop and remove the server relay for this conversation."""
        from core.conversation_store import ConversationStore

        store = ConversationStore.instance()
        meta = store.get_extra(conv_id, "server_relay")
        if not meta or not isinstance(meta, dict):
            return False

        relay_id = meta.get("relay_id", "")
        container_id = meta.get("container_id", "")
        user_id = meta.get("user_id", "")
        volume = meta.get("volume", _volume_name(conv_id))

        # Stop + remove container
        self._cleanup_container(container_id, remove=True)

        # Remove Docker volume
        if volume:
            try:
                subprocess.run(
                    docker_cmd() + ["volume", "rm", "-f", volume],
                    capture_output=True, timeout=15,
                )
            except Exception as e:
                logger.warning("Could not remove volume %s: %s", volume, e)

        # Uninstall the WS listener service
        if relay_id and user_id:
            self._uninstall_relay_service(user_id, relay_id)

        # Unlink relay from conversation bindings
        if relay_id:
            try:
                from core.relay_bindings import unlink_relay
                unlink_relay(conv_id, relay_id)
            except Exception:
                pass
        # Clear metadata
        store.set_extra(conv_id, "server_relay", None)
        logger.info("Server relay destroyed for conv %s", conv_id)
        return True

    def stop(self, conv_id: str) -> bool:
        """Stop the relay container without removing the volume."""
        from core.conversation_store import ConversationStore

        store = ConversationStore.instance()
        meta = store.get_extra(conv_id, "server_relay")
        if not meta or not isinstance(meta, dict):
            return False

        container_id = meta.get("container_id", "")
        self._cleanup_container(container_id, remove=False)
        return True

    def get_relay_id(self, conv_id: str) -> Optional[str]:
        """Return the relay_id for this conversation, or None."""
        from core.conversation_store import ConversationStore

        meta = ConversationStore.instance().get_extra(conv_id, "server_relay")
        if meta and isinstance(meta, dict):
            return meta.get("relay_id")
        return None

    def get_metadata(self, conv_id: str) -> Optional[Dict[str, Any]]:
        """Return full relay metadata for this conversation, or None."""
        from core.conversation_store import ConversationStore

        meta = ConversationStore.instance().get_extra(conv_id, "server_relay")
        if meta and isinstance(meta, dict) and meta.get("relay_id"):
            return meta
        return None

    def list_all(self) -> list:
        """List all conversations with a server relay (scans ConversationStore)."""
        from core.conversation_store import ConversationStore

        store = ConversationStore.instance()
        result = []
        try:
            for conv in store.list_conversations():
                cid = conv["conversation_id"]
                meta = store.get_extra(cid, "server_relay")
                if meta and isinstance(meta, dict) and meta.get("relay_id"):
                    result.append({"conv_id": cid, **meta})
        except Exception as e:
            logger.warning("list_all error: %s", e)
        return result

    def restart_orphans(self) -> int:
        """Re-spawn relay containers that died during a server restart.

        Called once at server startup. Returns number of relays restarted.
        """
        restarted = 0
        for entry in self.list_all():
            conv_id = entry["conv_id"]
            container_id = entry.get("container_id", "")
            if self._is_container_running(container_id):
                logger.info("Server relay still alive for conv %s — skipping", conv_id)
                continue
            # Re-spawn
            user_id = entry.get("user_id", "")
            if not user_id:
                logger.warning("No user_id in relay metadata for conv %s — skipping", conv_id)
                continue
            try:
                # Clear stale metadata so spawn() doesn't treat it as alive
                from core.conversation_store import ConversationStore
                ConversationStore.instance().set_extra(conv_id, "server_relay", None)
                self.spawn(conv_id, user_id)
                restarted += 1
                logger.info("Restarted server relay for conv %s", conv_id)
            except Exception as e:
                logger.error("Failed to restart server relay for conv %s: %s", conv_id, e)
        return restarted

    # ── Helpers ──────────────────────────────────────────────────────

    def _is_container_running(self, container_id: str) -> bool:
        if not container_id:
            return False
        try:
            result = subprocess.run(
                docker_cmd() + ["inspect", "--format", "{{.State.Running}}", container_id],
                capture_output=True, text=True, timeout=5,
            )
            return result.returncode == 0 and result.stdout.strip() == "true"
        except Exception:
            return False

    def _cleanup_container(self, container_id: str, remove: bool = True) -> None:
        if not container_id:
            return
        try:
            subprocess.run(
                docker_cmd() + ["stop", container_id],
                capture_output=True, timeout=15,
            )
        except Exception as e:
            logger.debug("Container stop error (%s): %s", container_id[:12], e)
        if remove:
            try:
                subprocess.run(
                    docker_cmd() + ["rm", "-f", container_id],
                    capture_output=True, timeout=10,
                )
            except Exception as e:
                logger.debug("Container rm error (%s): %s", container_id[:12], e)

    def _install_relay_service(self, user_id: str, relay_id: str, token: str) -> None:
        from core.service_registry import ServiceRegistry
        registry = ServiceRegistry.get_instance()
        registry.install(
            scope="user",
            scope_id=user_id,
            service_id=relay_id,
            service_type="relay",
            config={"token": token, "mode": "readwrite"},
            description="Server workspace relay (server-spawned)",
        )

    def _uninstall_relay_service(self, user_id: str, relay_id: str) -> None:
        try:
            from core.service_registry import ServiceRegistry
            ServiceRegistry.get_instance().uninstall("user", user_id, relay_id)
        except Exception as e:
            logger.warning("Could not uninstall relay service %s: %s", relay_id, e)
