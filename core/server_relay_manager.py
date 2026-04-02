"""Server-Side Relay Manager.

Spawns a Docker relay container per conversation, giving each conversation
a persistent workspace on the server without requiring a local install.

Each container:
- Runs tools/pawflow_relay.py in manual mode (env vars, not CLI args)
- Mounts a named Docker volume: pawflow_ws_{conv_id}
- Connects back to the server's WSListener via wss://
- Has exec enabled (--allow-exec)

Metadata is stored in ConversationStore extra:
  key="server_relay" value={relay_id, container_id, port, token, user_id}

Max 1 server relay per conversation (enforced in spawn).
"""

import logging
import secrets
import socket
import subprocess
import threading
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ── Default values for server relay settings ────────────────────────────────
# These are read at runtime from config/global_parameters.json
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


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _docker_cmd() -> list:
    import os
    if os.name == "nt":
        return ["wsl", "docker"]
    return ["docker"]


def _get_host_ip() -> str:
    """IP that Docker containers can use to reach the host."""
    from core.docker_utils import get_host_ip
    return get_host_ip()


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

        Returns metadata dict: {relay_id, container_id, port, ws_url}.
        Raises if relay already exists or Docker unavailable.
        """
        from core.conversation_store import ConversationStore

        store = ConversationStore.get_instance()
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

        port = _find_free_port()
        desktop_host_port = _find_free_port()
        audio_host_port = _find_free_port()
        token = secrets.token_urlsafe(32)
        relay_id = _relay_id_for_conv(conv_id)
        path = f"/ws/relay/{relay_id}"
        container_name = _container_name(conv_id)
        volume = _volume_name(conv_id)
        host_ip = _get_host_ip()

        # Read config live from global_parameters.json
        relay_image = _cfg("server_relay_image")
        relay_tools_dir = _cfg("server_relay_tools_dir")  # relative to server CWD
        relay_workspace = _cfg("server_relay_workspace")
        relay_cpus = _cfg("server_relay_cpus")
        relay_memory = _cfg("server_relay_memory")

        # Resolve tools/ dir to absolute path (relative to server CWD)
        import os as _os
        from core.docker_utils import to_host_path, detect_exec_mode
        tools_abs = _os.path.abspath(relay_tools_dir)
        # In DinD, translate container path to host path for Docker daemon
        tools_host = to_host_path(tools_abs)
        # tools/ is mounted as /opt/pawflow/ — same location as in the image.
        # This means relay changes are live without rebuilding the image.
        _TOOLS_IN_CONTAINER = "/opt/pawflow"
        _SCRIPT_IN_CONTAINER = f"{_TOOLS_IN_CONTAINER}/pawflow_relay.py"

        # Register the WS listener on the server BEFORE spawning the container
        self._install_relay_service(user_id, relay_id, port, path, token)

        # Determine if WSListener uses TLS (if cryptography is installed)
        ws_scheme = self._detect_ws_scheme()
        ws_url_for_container = f"{ws_scheme}://{host_ip}:{port}{path}"

        # Spawn the Docker container
        # Mount tools/ → /opt/pawflow/ so all relay modules (fs_actions, fs_exec, …)
        # are live from the server filesystem — no image rebuild needed.
        docker_run_args = [
            "--rm",
            "--detach",
            "--name", container_name,
            "--volume", f"{volume}:{relay_workspace}",
            "--volume", f"{tools_host}:{_TOOLS_IN_CONTAINER}:ro",
            "--add-host", "host.docker.internal:host-gateway",
            "--cpus", relay_cpus,
            "--memory", relay_memory,
            "--env", f"PAWFLOW_RELAY_SERVER={ws_url_for_container}",
            "--env", f"PAWFLOW_RELAY_TOKEN={token}",
            "--env", f"PAWFLOW_RELAY_ID={relay_id}",
            "--env", f"PAWFLOW_RELAY_DIR={relay_workspace}",
            "--env", "PAWFLOW_RELAY_ALLOW_EXEC=1",
            "--publish", f"{desktop_host_port}:6080",
            "--publish", f"{audio_host_port}:6180",
            "--env", "PAWFLOW_DESKTOP_NOVNC_PORT=6080",
        ]

        # DinD: mount docker.sock so relay can spawn docker-* exec shells
        exec_mode = detect_exec_mode()
        if exec_mode == "docker" and _os.path.exists("/var/run/docker.sock"):
            docker_run_args.extend([
                "--volume", "/var/run/docker.sock:/var/run/docker.sock",
            ])
            # Propagate host path translation for nested container spawning
            host_workdir = _os.environ.get("PAWFLOW_HOST_WORKDIR", "")
            container_workdir = _os.environ.get("PAWFLOW_WORKDIR", "")
            if host_workdir:
                docker_run_args.extend([
                    "--env", f"PAWFLOW_HOST_WORKDIR={host_workdir}",
                ])
            if container_workdir:
                docker_run_args.extend([
                    "--env", f"PAWFLOW_WORKDIR={container_workdir}",
                ])

        docker_run_args.extend([
            relay_image,
            "python3", _SCRIPT_IN_CONTAINER,
        ])
        cmd = _docker_cmd() + ["run"] + docker_run_args
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
            "port": port,
            "path": path,
            "token": token,
            "user_id": user_id,
            "ws_url": ws_url_for_container,
            "volume": volume,
            "desktop_host_port": desktop_host_port,
            "audio_host_port": audio_host_port,
        }
        store.set_extra(conv_id, "server_relay", metadata)
        logger.info("Server relay spawned for conv %s: %s", conv_id, relay_id)
        return metadata

    def destroy(self, conv_id: str) -> bool:
        """Stop and remove the server relay for this conversation."""
        from core.conversation_store import ConversationStore

        store = ConversationStore.get_instance()
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
                    _docker_cmd() + ["volume", "rm", "-f", volume],
                    capture_output=True, timeout=15,
                )
            except Exception as e:
                logger.warning("Could not remove volume %s: %s", volume, e)

        # Uninstall the WS listener service
        if relay_id and user_id:
            self._uninstall_relay_service(user_id, relay_id)

        # Clear metadata
        store.set_extra(conv_id, "server_relay", None)
        logger.info("Server relay destroyed for conv %s", conv_id)
        return True

    def stop(self, conv_id: str) -> bool:
        """Stop the relay container without removing the volume."""
        from core.conversation_store import ConversationStore

        store = ConversationStore.get_instance()
        meta = store.get_extra(conv_id, "server_relay")
        if not meta or not isinstance(meta, dict):
            return False

        container_id = meta.get("container_id", "")
        self._cleanup_container(container_id, remove=False)
        return True

    def get_relay_id(self, conv_id: str) -> Optional[str]:
        """Return the relay_id for this conversation, or None."""
        from core.conversation_store import ConversationStore

        meta = ConversationStore.get_instance().get_extra(conv_id, "server_relay")
        if meta and isinstance(meta, dict):
            return meta.get("relay_id")
        return None

    def get_metadata(self, conv_id: str) -> Optional[Dict[str, Any]]:
        """Return full relay metadata for this conversation, or None."""
        from core.conversation_store import ConversationStore

        meta = ConversationStore.get_instance().get_extra(conv_id, "server_relay")
        if meta and isinstance(meta, dict) and meta.get("relay_id"):
            return meta
        return None

    def list_all(self) -> list:
        """List all conversations with a server relay (scans ConversationStore)."""
        from core.conversation_store import ConversationStore

        store = ConversationStore.get_instance()
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
                ConversationStore.get_instance().set_extra(conv_id, "server_relay", None)
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
                _docker_cmd() + ["inspect", "--format", "{{.State.Running}}", container_id],
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
                _docker_cmd() + ["stop", container_id],
                capture_output=True, timeout=15,
            )
        except Exception as e:
            logger.debug("Container stop error (%s): %s", container_id[:12], e)
        if remove:
            try:
                subprocess.run(
                    _docker_cmd() + ["rm", "-f", container_id],
                    capture_output=True, timeout=10,
                )
            except Exception as e:
                logger.debug("Container rm error (%s): %s", container_id[:12], e)

    def _install_relay_service(self, user_id: str, relay_id: str, port: int, path: str, token: str) -> None:
        from gui.services.user_service_registry import UserServiceRegistry
        registry = UserServiceRegistry.get_instance()
        registry.install(
            user_id=user_id,
            service_id=relay_id,
            service_type="relay",
            config={"port": port, "path": path, "token": token, "mode": "readwrite"},
            description=f"Server workspace relay (server-spawned)",
        )

    def _uninstall_relay_service(self, user_id: str, relay_id: str) -> None:
        try:
            from gui.services.user_service_registry import UserServiceRegistry
            UserServiceRegistry.get_instance().uninstall(user_id, relay_id)
        except Exception as e:
            logger.warning("Could not uninstall relay service %s: %s", relay_id, e)

    def _detect_ws_scheme(self) -> str:
        """Detect whether WSListener will use TLS (wss) or plain (ws)."""
        try:
            from cryptography import x509  # noqa: F401
            return "wss"
        except ImportError:
            return "ws"
