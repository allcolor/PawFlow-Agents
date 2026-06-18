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
import shutil
from pathlib import Path
from typing import Any, Dict

from core.docker_utils import to_host_path

logger = logging.getLogger("core.server_relay_manager")  # canonical name preserved across split

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


