"""Server-Side Relay Manager.

Spawns Docker relay containers per conversation without requiring a local
install. PawFlow uses two explicit server relay kinds:

- workspace: persistent full workspace relay for interactive/server files
- minimal: protected execution relay for flow tasks and ExecuteScript

Each container:
- Runs tools/pawflow_relay.py in manual mode (env vars, not CLI args)
- Mounts a named Docker volume: pawflow_ws_{conv_id}
- Connects back to the main HTTPListenerService via ws(s)://host:<main>/ws/relay/<id>
- Has exec enabled (--allow-exec)

Metadata is stored in ConversationStore extra:
  key="server_relay" value={relay_id, container_id, token, user_id, ws_url, ...}
  key="server_minimal_relay" value={relay_id, container_id, token, user_id, ws_url, ...}

Max 1 server relay of each kind per conversation (enforced in spawn).
"""

import hashlib
import json
import logging
import os
import secrets
import shutil
import subprocess  # nosec B404
import threading
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ── Default values for server relay settings ────────────────────────────────
# These are read at runtime from data/config/global_parameters.json
# so they can be changed from the PawFlow UI without restarting.
#
# Keys in global_parameters.json:
#   server_relay_image      — Docker image (default: pawflow-relay-dev:latest)
#   server_relay_workspace  — Workspace dir in container (default: /workspace)
#   server_relay_cpus       — CPU limit (default: 2)
#   server_relay_memory     — Memory limit (default: 2g)
#   server_relay_minimal_image  — Docker image for protected execution relays
#   server_relay_minimal_cpus   — CPU limit for protected execution relays
#   server_relay_minimal_memory — Memory limit for protected execution relays

_DEFAULTS = {
    "server_relay_image":     "pawflow-relay-dev:latest",
    "server_relay_minimal_image": "pawflow-relay-minimal:latest",
    "server_relay_workspace": "/workspace",
    "server_relay_cpus":      "2",
    "server_relay_memory":    "2g",
    "server_relay_minimal_cpus": "1",
    "server_relay_minimal_memory": "512m",
}

_KIND_WORKSPACE = "workspace"
_KIND_MINIMAL = "minimal"


def _cfg(key: str) -> str:
    """Read a server relay setting from global_parameters.json (live, no cache)."""
    env_key = "PAWFLOW_" + key.upper()
    env_value = os.environ.get(env_key)
    if env_value:
        return env_value
    try:
        from core.expression import _load_global_parameters
        return _load_global_parameters().get(key, _DEFAULTS[key])
    except Exception:
        return _DEFAULTS[key]


def _host_run_uid_gid() -> tuple[int, int] | None:
    """Return the host UID/GID that should own managed relay files."""
    try:
        uid = int(os.environ.get("PAWFLOW_RUN_UID", "").strip())
        gid = int(os.environ.get("PAWFLOW_RUN_GID", "").strip())
    except (TypeError, ValueError):
        return None
    if uid < 0 or gid < 0:
        return None
    return uid, gid


def _chown_for_host_runner(path: Path) -> None:
    """Keep bind-mounted relay runtime paths owned by the PawFlow host user."""
    owner = _host_run_uid_gid()
    if owner is None:
        return
    uid, gid = owner
    try:
        for root, dirs, files in os.walk(path):
            os.chown(root, uid, gid)
            for name in dirs:
                os.chown(os.path.join(root, name), uid, gid)
            for name in files:
                os.chown(os.path.join(root, name), uid, gid)
    except PermissionError:
        logger.warning("Could not chown relay runtime path %s to %s:%s", path, uid, gid)


from core.apparmor import relay_apparmor_security_opts
from core.docker_utils import docker_cmd, get_host_ip, to_host_path


def _validate_kind(kind: str) -> str:
    if kind not in {_KIND_WORKSPACE, _KIND_MINIMAL}:
        raise ValueError(f"Unknown server relay kind: {kind}")
    return kind


def _metadata_key(kind: str) -> str:
    kind = _validate_kind(kind)
    return "server_minimal_relay" if kind == _KIND_MINIMAL else "server_relay"


def _volume_name(conv_id: str, kind: str = _KIND_WORKSPACE) -> str:
    kind = _validate_kind(kind)
    prefix = "pawflow_exec" if kind == _KIND_MINIMAL else "pawflow_ws"
    return f"{prefix}_{conv_id}"


def _container_name(conv_id: str, kind: str = _KIND_WORKSPACE) -> str:
    kind = _validate_kind(kind)
    prefix = "pawflow-relay-min" if kind == _KIND_MINIMAL else "pawflow-relay-srv"
    return f"{prefix}-{conv_id[:16]}"


def _relay_id_for_conv(conv_id: str, kind: str = _KIND_WORKSPACE) -> str:
    kind = _validate_kind(kind)
    prefix = "srv_min" if kind == _KIND_MINIMAL else "srv_ws"
    return f"{prefix}_{conv_id[:16]}"


def _safe_path_part(value: str) -> str:
    text = str(value or "").strip() or "global"
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in text)


def _relay_runtime_dir(user_id: str, conv_id: str, kind: str = _KIND_WORKSPACE) -> Path:
    return _relay_runtime_dir_for_scope("conv", user_id, conv_id, kind)


def _relay_runtime_dir_for_scope(
    scope: str,
    user_id: str,
    scope_id: str,
    kind: str = _KIND_WORKSPACE,
) -> Path:
    kind = _validate_kind(kind)
    base = Path(os.environ.get("PAWFLOW_DATA_DIR") or "data") / "runtime" / "relay"
    if scope == "global":
        path = base / "global"
    elif scope == "user":
        path = base / _safe_path_part(scope_id or user_id)
    else:
        path = base / _safe_path_part(user_id or "global") / _safe_path_part(scope_id)
    if kind == _KIND_MINIMAL:
        path = path / "minimal"
    return path


def _relay_container_name(relay_id: str, kind: str = _KIND_WORKSPACE) -> str:
    kind = _validate_kind(kind)
    prefix = "pawflow-relay-min" if kind == _KIND_MINIMAL else "pawflow-relay-srv"
    return f"{prefix}-{_safe_path_part(relay_id)[:48]}"


def _relay_volume_name(relay_id: str, kind: str = _KIND_WORKSPACE) -> str:
    kind = _validate_kind(kind)
    prefix = "pawflow_exec" if kind == _KIND_MINIMAL else "pawflow_ws"
    return f"{prefix}_{_safe_path_part(relay_id)}"


def _relay_runtime_host_dir(runtime_dir: Path) -> str:
    data_dir = Path(os.environ.get("PAWFLOW_DATA_DIR") or "data").resolve()
    host_data_dir = os.environ.get("PAWFLOW_HOST_DATA_DIR", "").strip()
    runtime_abs = runtime_dir.resolve()
    if host_data_dir:
        try:
            rel = runtime_abs.relative_to(data_dir)
            return str((Path(host_data_dir) / rel).resolve())
        except ValueError:
            pass
    return to_host_path(str(runtime_abs))


def _relay_runtime_source_hash(tools_dir: Path, relay_pkg: Path, sdk_file: Path) -> str:
    digest = hashlib.sha256()
    sources = (("tools", tools_dir), ("pawflow_relay", relay_pkg))
    for label, directory in sources:
        for path in sorted(p for p in directory.rglob("*") if p.is_file()):
            rel = f"{label}/{path.relative_to(directory).as_posix()}".encode("utf-8")
            digest.update(rel + b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    digest.update(b"pawflow.py\0")
    digest.update(sdk_file.read_bytes())
    return digest.hexdigest()


def _prepare_relay_code_dir(runtime_dir: Path) -> Path:
    """Stage relay runtime code from this PawFlow server image for bind-mounting."""
    root = Path(__file__).resolve().parents[1]
    tools_dir = root / "tools"
    relay_pkg = root / "pawflow_relay"
    sdk_file = root / "docker" / "pawflow_sdk" / "pawflow.py"
    for required in (tools_dir, relay_pkg, sdk_file):
        if not required.exists():
            raise RuntimeError(f"Missing relay runtime source: {required}")

    source_hash = _relay_runtime_source_hash(tools_dir, relay_pkg, sdk_file)
    code_dir = runtime_dir / ".pawflow-runtime"
    marker_file = code_dir / ".pawflow-runtime-source.json"
    if code_dir.exists():
        try:
            marker = json.loads(marker_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            marker = {}
        if (
            marker.get("source_hash") == source_hash
            and (code_dir / "pawflow_relay").exists()
            and (code_dir / "pawflow.py").exists()
            and (code_dir / "pawflow_relay_launcher.py").exists()
        ):
            return code_dir
        shutil.rmtree(code_dir)
    shutil.copytree(tools_dir, code_dir)
    shutil.copytree(relay_pkg, code_dir / "pawflow_relay")
    shutil.copy2(sdk_file, code_dir / "pawflow.py")
    marker_file.write_text(
        json.dumps({"source": str(root), "source_hash": source_hash}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _chown_for_host_runner(code_dir)
    return code_dir


def _relay_kind_config(kind: str) -> Dict[str, Any]:
    kind = _validate_kind(kind)
    if kind == _KIND_MINIMAL:
        return {
            "kind": _KIND_MINIMAL,
            "label": "server minimal execution relay",
            "image": _cfg("server_relay_minimal_image"),
            "cpus": _cfg("server_relay_minimal_cpus"),
            "memory": _cfg("server_relay_minimal_memory"),
            "publish_desktop": False,
            "description": "Server minimal execution relay (server-spawned)",
        }
    return {
        "kind": _KIND_WORKSPACE,
        "label": "server workspace relay",
        "image": _cfg("server_relay_image"),
        "cpus": _cfg("server_relay_cpus"),
        "memory": _cfg("server_relay_memory"),
        "publish_desktop": True,
        "description": "Server workspace relay (server-spawned)",
    }


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
        *,
        kind: str = _KIND_WORKSPACE,
    ) -> Dict[str, Any]:
        """Spawn a server relay for this conversation.

        Returns metadata dict: {relay_id, container_id, ws_url, ...}.
        Raises if relay already exists or Docker unavailable.
        """
        from core.conversation_store import ConversationStore

        kind = _validate_kind(kind)
        kind_cfg = _relay_kind_config(kind)
        metadata_key = _metadata_key(kind)
        store = ConversationStore.instance()
        existing = store.get_extra(conv_id, metadata_key)
        if existing and isinstance(existing, dict) and existing.get("relay_id"):
            # Check if the container is actually running
            cid = existing.get("container_id", "")
            if cid and self._is_container_running(cid):
                raise ValueError(
                    f"A {kind_cfg['label']} already exists for this conversation: "
                    f"{existing['relay_id']}"
                )
            # Container is dead — clean up and re-spawn
            logger.info("Dead %s found for conv %s — re-spawning", kind_cfg["label"], conv_id)
            self._cleanup_container(existing.get("container_id", ""), remove=True)

        token = secrets.token_urlsafe(32)
        from core.internal_auth import mint_token
        internal_token = mint_token()
        relay_id = _relay_id_for_conv(conv_id, kind)
        path = f"/ws/relay/{relay_id}"
        container_name = _container_name(conv_id, kind)
        volume = _volume_name(conv_id, kind)
        runtime_dir = _relay_runtime_dir(user_id, conv_id, kind)
        runtime_dir.mkdir(parents=True, exist_ok=True)
        _chown_for_host_runner(runtime_dir)
        runtime_host_dir = _relay_runtime_host_dir(runtime_dir)
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
        relay_image = kind_cfg["image"]
        relay_workspace = _cfg("server_relay_workspace")
        relay_cpus = kind_cfg["cpus"]
        relay_memory = kind_cfg["memory"]

        _TOOLS_IN_CONTAINER = "/opt/pawflow"
        _SCRIPT_IN_CONTAINER = f"{_TOOLS_IN_CONTAINER}/pawflow_relay_launcher.py"
        code_dir = _prepare_relay_code_dir(runtime_dir)
        code_host_dir = _relay_runtime_host_dir(code_dir)

        # Register the relay service on the server BEFORE spawning the container.
        # RelayService.connect() registers /ws/relay/<service_id> on the main
        # HTTPListenerService — no separate port or path to configure.
        self._install_relay_service(user_id, relay_id, token, kind=kind)

        ws_url_for_container = f"{ws_scheme}://{host_ip}:{main_port}{path}"

        # Mount relay runtime code staged from the PawFlow server image so relay
        # images stay clean dependency/runtime images instead of embedding code.
        # Encrypted workspace (phase 6): when enabled+unlocked, bind the host
        # dir as the CryFS cipher-store and hand the relay the DEK (RAM-only) to
        # mount a plaintext view at the workspace path. Plaintext workspaces are
        # byte-for-byte unchanged. See workspace_encryption.spawn_env.
        from core import workspace_encryption as _we
        _ws_mount_target, _ws_extra_env = _we.spawn_env(
            store, conv_id, is_workspace_kind=(kind == _KIND_WORKSPACE),
            relay_workspace=relay_workspace)

        docker_run_args = [
            "--rm",
            "--detach",
            "--name", container_name,
            "--init",
            "--volume", f"{runtime_host_dir}:{_ws_mount_target}",
            "--volume", f"pawflow_home_{relay_id}:/home/pawflow",
            "--volume", f"{code_host_dir}:{_TOOLS_IN_CONTAINER}:ro",
            "--add-host", "host.docker.internal:host-gateway",
            "--cpus", relay_cpus,
            "--memory", relay_memory,
            # FUSE mounts inside the relay container:
            #   /cc_sessions — server-fs sister-protocol (sfs.*), the
            #     conversation's CLAUDE_SESSIONS_DIR slot.
            #   /filestore — FileStore sister-protocol (ffs.*), the
            #     virtualized RO view of files visible to this user.
            #   /skills — Agent Skills sister-protocol (skfs.*), the
            #     virtualized RO view of the skills repository.
            # SYS_ADMIN + /dev/fuse let the FUSE mounts come up. AppArmor:
            # the pawflow-relay profile when loaded on the host (FUSE
            # mounts allowed only under /tmp/pf_combined_fs and /remote),
            # apparmor=unconfined fallback otherwise — docker-default
            # would block mount/umount entirely.
            "--cap-add", "SYS_ADMIN",
            "--device", "/dev/fuse",
            *relay_apparmor_security_opts(relay_image),
            "--env", f"PAWFLOW_RELAY_SERVER={ws_url_for_container}",
            "--env", f"PAWFLOW_RELAY_TOKEN={token}",
            "--env", f"PAWFLOW_RELAY_ID={relay_id}",
            "--env", f"PAWFLOW_RELAY_DIR={relay_workspace}",
            "--env", f"PAWFLOW_RUN_UID={os.environ.get('PAWFLOW_RUN_UID', '')}",
            "--env", f"PAWFLOW_RUN_GID={os.environ.get('PAWFLOW_RUN_GID', '')}",
            "--env", "PAWFLOW_RELAY_ALLOW_EXEC=1",
            "--env", "PAWFLOW_RELAY_INSECURE=1",
            "--env", f"PAWFLOW_INTERNAL_TOKEN={internal_token}",
            "--env", "PAWFLOW_SERVER_MOUNT=/cc_sessions",
            "--env", "PAWFLOW_FILESTORE_MOUNT=/filestore",
            "--env", "PAWFLOW_SKILLS_MOUNT=/skills",
            "--env", "HOME=/home/pawflow",
            "--env", "USER=pawflow",
            "--env", "CARGO_HOME=/opt/local/rust/cargo",
            "--env", "RUSTUP_HOME=/opt/local/rust/rustup",
            "--env", "GOPATH=/opt/local/go-path",
            "--env", "GOBIN=/opt/local/bin",
            "--env", "GOCACHE=/tmp/pawflow-go-build",
            "--env", "GOMODCACHE=/tmp/pawflow-go-mod",
            "--env", "XDG_CACHE_HOME=/tmp/pawflow-cache",
            "--env", "HF_HOME=/tmp/pawflow-cache/huggingface",
            "--env", "HUGGINGFACE_HUB_CACHE=/tmp/pawflow-cache/huggingface/hub",
            "--env", "SENTENCE_TRANSFORMERS_HOME=/tmp/pawflow-cache/sentence-transformers",
            "--env", "TRANSFORMERS_CACHE=/tmp/pawflow-cache/huggingface/transformers",
            "--env", "PATH=/opt/local/bin:/opt/local/rust/cargo/bin:/usr/local/go/bin:/opt/kotlinc/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        ]
        if kind_cfg["publish_desktop"]:
            docker_run_args.extend([
                "--env", "PAWFLOW_DESKTOP_NOVNC_PORT=6080",
            ])

        # NOTE: no docker.sock bind-mount. The server-spawned relay lives in
        # an isolated kind-specific named volume; it has no host
        # path to bind-mount into a nested container, so spawn_relay / child-
        # relay features are meaningless here. Exposing docker.sock would
        # grant root-on-host via `docker run -v /:/host --privileged` for no
        # functional gain.

        if _ws_extra_env:
            docker_run_args.extend(_ws_extra_env)

        docker_run_args.extend([
            relay_image,
            "python3", _SCRIPT_IN_CONTAINER,
        ])
        cmd = docker_cmd() + ["run"] + docker_run_args
        logger.info("Spawning server relay container: %s  cmd=%s", container_name, cmd)
        result = subprocess.run(  # nosec B603
            cmd, capture_output=True, text=True,
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
            "workspace_dir": str(runtime_dir),
            "workspace_host_dir": runtime_host_dir,
            "code_dir": str(code_dir),
            "code_host_dir": code_host_dir,
            "kind": kind,
            "image": relay_image,
            "cpus": relay_cpus,
            "memory": relay_memory,
            "internal_token": internal_token,
        }
        store.set_extra(conv_id, metadata_key, metadata)
        # Auto-link this relay to the conversation
        try:
            from core.relay_bindings import link_relay
            link_relay(conv_id, relay_id, user_id=user_id)
        except Exception as e:
            logger.warning("Failed to auto-link relay %s to conv %s: %s", relay_id, conv_id, e)
        logger.info("%s spawned for conv %s: %s", kind_cfg["label"], conv_id, relay_id)
        return metadata

    def spawn_minimal(self, conv_id: str, user_id: str) -> Dict[str, Any]:
        """Spawn the protected minimal execution relay for this conversation."""
        return self.spawn(conv_id, user_id, kind=_KIND_MINIMAL)

    def service_relay_config(
        self,
        relay_id: str,
        *,
        scope: str,
        scope_id: str,
        user_id: str,
        kind: str = _KIND_WORKSPACE,
    ) -> Dict[str, Any]:
        """Return deterministic managed-runtime config for a relay service."""
        kind = _validate_kind(kind)
        runtime_dir = _relay_runtime_dir_for_scope(scope, user_id, scope_id, kind)
        return {
            "server_container_name": _relay_container_name(relay_id, kind),
            "server_workspace_dir": str(runtime_dir),
            "server_workspace_host_dir": _relay_runtime_host_dir(runtime_dir),
            "server_home_volume": f"pawflow_home_{relay_id}",
            "server_scope": scope,
            "server_scope_id": scope_id,
            "server_user_id": user_id,
        }

    def spawn_service_relay(
        self,
        relay_id: str,
        token: str,
        *,
        scope: str,
        scope_id: str,
        user_id: str,
        kind: str = _KIND_WORKSPACE,
        internal_token: str = "",
    ) -> Dict[str, Any]:
        """Spawn a managed server relay container for an installed relay service."""
        kind = _validate_kind(kind)
        kind_cfg = _relay_kind_config(kind)
        if not relay_id:
            raise ValueError("Missing relay_id")
        if not token:
            raise ValueError("Missing relay token")
        if not internal_token:
            from core.internal_auth import mint_token
            internal_token = mint_token()

        path = f"/ws/relay/{relay_id}"
        container_name = _relay_container_name(relay_id, kind)
        home_volume = f"pawflow_home_{relay_id}"
        volume = _relay_volume_name(relay_id, kind)
        runtime_dir = _relay_runtime_dir_for_scope(scope, user_id, scope_id, kind)
        runtime_dir.mkdir(parents=True, exist_ok=True)
        _chown_for_host_runner(runtime_dir)
        runtime_host_dir = _relay_runtime_host_dir(runtime_dir)
        host_ip = get_host_ip()

        from services.http_listener_service import HTTPListenerService
        _listeners = HTTPListenerService.all_instances()
        if not _listeners:
            raise RuntimeError(
                "Cannot spawn server relay: no HTTPListenerService running. "
                "Start the main listener first.")
        _main_listener = next(iter(_listeners.values()))
        main_port = _main_listener._port
        ws_scheme = "wss" if _main_listener.is_ssl else "ws"

        relay_image = kind_cfg["image"]
        relay_workspace = _cfg("server_relay_workspace")
        relay_cpus = kind_cfg["cpus"]
        relay_memory = kind_cfg["memory"]

        _TOOLS_IN_CONTAINER = "/opt/pawflow"
        _SCRIPT_IN_CONTAINER = f"{_TOOLS_IN_CONTAINER}/pawflow_relay_launcher.py"
        code_dir = _prepare_relay_code_dir(runtime_dir)
        code_host_dir = _relay_runtime_host_dir(code_dir)

        self._cleanup_container(container_name, remove=True)
        ws_url_for_container = f"{ws_scheme}://{host_ip}:{main_port}{path}"
        docker_run_args = [
            "--rm",
            "--detach",
            "--name", container_name,
            "--init",
            "--volume", f"{runtime_host_dir}:{relay_workspace}",
            "--volume", f"{home_volume}:/home/pawflow",
            "--volume", f"{code_host_dir}:{_TOOLS_IN_CONTAINER}:ro",
            "--add-host", "host.docker.internal:host-gateway",
            "--cpus", relay_cpus,
            "--memory", relay_memory,
            "--cap-add", "SYS_ADMIN",
            "--device", "/dev/fuse",
            *relay_apparmor_security_opts(relay_image),
            "--env", f"PAWFLOW_RELAY_SERVER={ws_url_for_container}",
            "--env", f"PAWFLOW_RELAY_TOKEN={token}",
            "--env", f"PAWFLOW_RELAY_ID={relay_id}",
            "--env", f"PAWFLOW_RELAY_DIR={relay_workspace}",
            "--env", f"PAWFLOW_RUN_UID={os.environ.get('PAWFLOW_RUN_UID', '')}",
            "--env", f"PAWFLOW_RUN_GID={os.environ.get('PAWFLOW_RUN_GID', '')}",
            "--env", "PAWFLOW_RELAY_ALLOW_EXEC=1",
            "--env", "PAWFLOW_RELAY_INSECURE=1",
            "--env", f"PAWFLOW_INTERNAL_TOKEN={internal_token}",
            "--env", "PAWFLOW_SERVER_MOUNT=/cc_sessions",
            "--env", "PAWFLOW_FILESTORE_MOUNT=/filestore",
            "--env", "PAWFLOW_SKILLS_MOUNT=/skills",
            "--env", "HOME=/home/pawflow",
            "--env", "USER=pawflow",
            "--env", "CARGO_HOME=/opt/local/rust/cargo",
            "--env", "RUSTUP_HOME=/opt/local/rust/rustup",
            "--env", "GOPATH=/opt/local/go-path",
            "--env", "GOBIN=/opt/local/bin",
            "--env", "GOCACHE=/tmp/pawflow-go-build",
            "--env", "GOMODCACHE=/tmp/pawflow-go-mod",
            "--env", "XDG_CACHE_HOME=/tmp/pawflow-cache",
            "--env", "HF_HOME=/tmp/pawflow-cache/huggingface",
            "--env", "HUGGINGFACE_HUB_CACHE=/tmp/pawflow-cache/huggingface/hub",
            "--env", "SENTENCE_TRANSFORMERS_HOME=/tmp/pawflow-cache/sentence-transformers",
            "--env", "TRANSFORMERS_CACHE=/tmp/pawflow-cache/huggingface/transformers",
            "--env", "PATH=/opt/local/bin:/opt/local/rust/cargo/bin:/usr/local/go/bin:/opt/kotlinc/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        ]
        if kind_cfg["publish_desktop"]:
            docker_run_args.extend([
                "--env", "PAWFLOW_DESKTOP_NOVNC_PORT=6080",
            ])
        docker_run_args.extend([
            relay_image,
            "python3", _SCRIPT_IN_CONTAINER,
        ])
        cmd = docker_cmd() + ["run"] + docker_run_args
        logger.info("Spawning managed server relay service: %s  cmd=%s", container_name, cmd)
        result = subprocess.run(  # nosec B603
            cmd, capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to start relay container: {result.stderr.strip()}"
            )

        container_id = result.stdout.strip()
        metadata = {
            "relay_id": relay_id,
            "container_id": container_id,
            "container_name": container_name,
            "user_id": user_id,
            "scope": scope,
            "scope_id": scope_id,
            "ws_url": ws_url_for_container,
            "volume": volume,
            "workspace_dir": str(runtime_dir),
            "workspace_host_dir": runtime_host_dir,
            "home_volume": home_volume,
            "code_dir": str(code_dir),
            "code_host_dir": code_host_dir,
            "kind": kind,
            "image": relay_image,
            "cpus": relay_cpus,
            "memory": relay_memory,
            "internal_token": internal_token,
        }
        logger.info("Managed server relay service spawned: %s", relay_id)
        return metadata

    def cleanup_service_relay(self, config: Dict[str, Any]) -> bool:
        """Stop a managed relay container and delete its managed runtime dir."""
        if not config or not config.get("server_managed"):
            return False
        container_id = str(config.get("server_container_id") or "")
        container_name = str(config.get("server_container_name") or "")
        workspace_dir = str(config.get("server_workspace_dir") or "")
        home_volume = str(config.get("server_home_volume") or "")
        internal_token = str(config.get("server_internal_token") or config.get("internal_token") or "")

        self._cleanup_container(container_id or container_name, remove=True)
        if internal_token:
            try:
                from core.internal_auth import revoke_token
                revoke_token(internal_token)
            except Exception:
                logger.debug("Ignored exception", exc_info=True)
        if home_volume:
            try:
                subprocess.run(  # nosec B603
                    docker_cmd() + ["volume", "rm", "-f", home_volume],
                    capture_output=True,
                )
            except Exception as e:
                logger.warning("Could not remove relay home volume %s: %s", home_volume, e)
        if workspace_dir:
            try:
                shutil.rmtree(workspace_dir, ignore_errors=True)
            except Exception as e:
                logger.warning("Could not remove relay workspace %s: %s", workspace_dir, e)
        return True

    def ensure(
        self,
        conv_id: str,
        user_id: str,
        *,
        kind: str = _KIND_WORKSPACE,
    ) -> Dict[str, Any]:
        """Return a running server relay, spawning it when missing or dead."""
        kind = _validate_kind(kind)
        meta = self.get_metadata(conv_id, kind=kind)
        if meta and self._is_container_running(meta.get("container_id", "")):
            return meta
        return self.spawn(conv_id, user_id, kind=kind)

    def ensure_minimal(self, conv_id: str, user_id: str) -> Dict[str, Any]:
        """Return the protected minimal execution relay, spawning it if needed."""
        return self.ensure(conv_id, user_id, kind=_KIND_MINIMAL)

    def destroy(self, conv_id: str, *, kind: str = _KIND_WORKSPACE) -> bool:
        """Stop and remove the server relay for this conversation."""
        from core.conversation_store import ConversationStore

        kind = _validate_kind(kind)
        metadata_key = _metadata_key(kind)
        store = ConversationStore.instance()
        meta = store.get_extra(conv_id, metadata_key)
        if not meta or not isinstance(meta, dict):
            return False

        relay_id = meta.get("relay_id", "")
        container_id = meta.get("container_id", "")
        user_id = meta.get("user_id", "")
        volume = meta.get("volume", _volume_name(conv_id, kind))
        workspace_dir = meta.get("workspace_dir", "")

        # Stop + remove container
        self._cleanup_container(container_id, remove=True)

        # Remove Docker volume
        if volume:
            try:
                subprocess.run(  # nosec B603
                    docker_cmd() + ["volume", "rm", "-f", volume],
                    capture_output=True,
                )
            except Exception as e:
                logger.warning("Could not remove volume %s: %s", volume, e)

        if workspace_dir:
            try:
                shutil.rmtree(workspace_dir, ignore_errors=True)
            except Exception as e:
                logger.warning("Could not remove relay workspace %s: %s", workspace_dir, e)

        # Uninstall the WS listener service
        if relay_id and user_id:
            self._uninstall_relay_service(user_id, relay_id)

        # Unlink relay from conversation bindings
        if relay_id:
            try:
                from core.relay_bindings import unlink_relay
                unlink_relay(conv_id, relay_id)
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        # Clear metadata
        store.set_extra(conv_id, metadata_key, None)
        logger.info("Server %s relay destroyed for conv %s", kind, conv_id)
        return True

    def destroy_minimal(self, conv_id: str) -> bool:
        """Stop and remove the protected minimal execution relay."""
        return self.destroy(conv_id, kind=_KIND_MINIMAL)

    def stop(self, conv_id: str, *, kind: str = _KIND_WORKSPACE) -> bool:
        """Stop the relay container without removing the volume."""
        from core.conversation_store import ConversationStore

        kind = _validate_kind(kind)
        store = ConversationStore.instance()
        meta = store.get_extra(conv_id, _metadata_key(kind))
        if not meta or not isinstance(meta, dict):
            return False

        container_id = meta.get("container_id", "")
        self._cleanup_container(container_id, remove=False)
        return True

    def get_relay_id(self, conv_id: str, *, kind: str = _KIND_WORKSPACE) -> Optional[str]:
        """Return the relay_id for this conversation, or None."""
        from core.conversation_store import ConversationStore

        kind = _validate_kind(kind)
        meta = ConversationStore.instance().get_extra(conv_id, _metadata_key(kind))
        if meta and isinstance(meta, dict):
            return meta.get("relay_id")
        return None

    def get_metadata(self, conv_id: str, *, kind: str = _KIND_WORKSPACE) -> Optional[Dict[str, Any]]:
        """Return full relay metadata for this conversation, or None."""
        from core.conversation_store import ConversationStore

        kind = _validate_kind(kind)
        meta = ConversationStore.instance().get_extra(conv_id, _metadata_key(kind))
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
                for kind in (_KIND_WORKSPACE, _KIND_MINIMAL):
                    meta = store.get_extra(cid, _metadata_key(kind))
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
            kind = entry.get("kind") or _KIND_WORKSPACE
            if not user_id:
                logger.warning("No user_id in relay metadata for conv %s — skipping", conv_id)
                continue
            try:
                # Clear stale metadata so spawn() doesn't treat it as alive
                from core.conversation_store import ConversationStore
                ConversationStore.instance().set_extra(conv_id, _metadata_key(kind), None)
                self.spawn(conv_id, user_id, kind=kind)
                restarted += 1
                logger.info("Restarted server %s relay for conv %s", kind, conv_id)
            except Exception as e:
                logger.error("Failed to restart server relay for conv %s: %s", conv_id, e)
        return restarted

    # ── Helpers ──────────────────────────────────────────────────────

    def _is_container_running(self, container_id: str) -> bool:
        if not container_id:
            return False
        try:
            result = subprocess.run(  # nosec B603
                docker_cmd() + ["inspect", "--format", "{{.State.Running}}", container_id],
                capture_output=True, text=True,
            )
            return result.returncode == 0 and result.stdout.strip() == "true"
        except Exception:
            return False

    def _cleanup_container(self, container_id: str, remove: bool = True) -> None:
        if not container_id:
            return
        try:
            subprocess.run(  # nosec B603
                docker_cmd() + ["stop", container_id],
                capture_output=True,
            )
        except Exception as e:
            logger.debug("Container stop error (%s): %s", container_id[:12], e)
        if remove:
            try:
                subprocess.run(  # nosec B603
                    docker_cmd() + ["rm", "-f", container_id],
                    capture_output=True,
                )
            except Exception as e:
                logger.debug("Container rm error (%s): %s", container_id[:12], e)

    def _install_relay_service(self, user_id: str, relay_id: str, token: str,
                               *, kind: str = _KIND_WORKSPACE) -> None:
        from core.service_registry import ServiceRegistry
        kind = _validate_kind(kind)
        kind_cfg = _relay_kind_config(kind)
        registry = ServiceRegistry.get_instance()
        registry.install(
            scope="user",
            scope_id=user_id,
            service_id=relay_id,
            service_type="relay",
            config={"token": token, "mode": "readwrite", "server_kind": kind},
            description=kind_cfg["description"],
        )

    def _uninstall_relay_service(self, user_id: str, relay_id: str) -> None:
        try:
            from core.service_registry import ServiceRegistry
            ServiceRegistry.get_instance().uninstall("user", user_id, relay_id)
        except Exception as e:
            logger.warning("Could not uninstall relay service %s: %s", relay_id, e)
