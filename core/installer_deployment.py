"""Where an installer-deployed server lives on its host.

The installer does not use Docker Compose. ``scripts/install-pawflow.sh`` ends
on ``scripts/run-pawflow-docker.sh``, a plain ``docker run``. Compose stamps
the project's host path onto every container it creates; a ``docker run``
stamps nothing, so the self-update had nothing to work from and refused every
installer deployment -- which is most of them.

Everything it needs is recoverable from the container itself:

* ``org.pawflow.*`` labels, written by ``run-pawflow-docker.sh``. Authoritative,
  but only on containers created by a version that writes them.
* ``PAWFLOW_HOST_APP_DIR`` and ``PAWFLOW_HOST_DATA_DIR``, injected into the
  environment long before those labels existed. This is what makes an
  *already running* install updatable, instead of only the next one.
* ``docker inspect`` of ourselves for the image, the network mode and the port
  -- the port is a command-line argument (``cli.py start --port N``), never an
  environment variable.

The result describes how to run ``run-pawflow-docker.sh`` again. That script is
the source of truth for how a PawFlow container is started, it already recreates
an existing container in place (``PAWFLOW_RECREATE_CONTAINER`` defaults to 1),
and reproducing its ``docker run`` by hand from an inspect dump would be a
second source of truth that drifts.
"""

from __future__ import annotations

import logging
import posixpath
import threading
from typing import Any, Dict, List, Optional, Tuple

from core.compose_deployment import inspect_container, self_container_id

logger = logging.getLogger(__name__)

#: Files ``scripts/install-pawflow.sh`` copies out of the server image onto the
#: host (``extract_image_artifacts``). They are what the host runs *outside* the
#: container -- the start script itself, the relay runtime, the AppArmor
#: profiles, the relay image catalog -- so an update that refreshes only the
#: image leaves them frozen at whatever version was installed from the command
#: line. Kept in the installer's own order; it is the source of truth and this
#: list mirrors it.
IMAGE_ARTIFACTS: Tuple[str, ...] = (
    "scripts/run-pawflow-docker.sh",
    "scripts/install-pawflow.sh",
    "scripts/doctor-pawflow.sh",
    "scripts/doctor-pawflow.ps1",
    "config/relay_image_catalog.json",
    "docker/claude-code",
    "docker/pawflow_sdk",
    "tools/mcp_bridge.py",
    "core/tool_json.py",
    "pawflow_relay",
)

#: Artifacts an older image may simply not carry. The installer warns and
#: continues for exactly these; so does the update.
OPTIONAL_IMAGE_ARTIFACTS: Tuple[str, ...] = (
    "scripts/install-pawflow.ps1",
    "scripts/test_apparmor_profile.sh",
    "scripts/test_apparmor_relay_profile.sh",
    "docker/apparmor",
)


def image_tag_dirname(image: str) -> str:
    """The directory name ``install-pawflow.sh`` derives from an image tag.

    Mirrors its ``image_runtime_dir``: the tag, with everything outside
    ``[A-Za-z0-9._-]`` replaced by ``_``. A digest-pinned or tagless image has
    no tag to speak of and yields ``latest``, exactly as the installer does.
    """
    ref = (image or "").split("@", 1)[0]
    tag = ref.rsplit(":", 1)[-1] if ":" in ref.rsplit("/", 1)[-1] else ""
    tag = tag or "latest"
    return "".join(c if (c.isalnum() or c in "._-") else "_" for c in tag)


def artifact_dir_for_update(host_app_dir: str, current_image: str,
                            target_image: str) -> str:
    """Where the update should extract the new image's host artifacts.

    The installer lays them out per version, in ``~/.pawflow/runtime/<tag>``, so
    an update moves to the *new* tag's directory and leaves the old one intact:
    the previous version stays on disk to fall back to, and the directory never
    claims a version it does not hold.

    That only holds when the directory actually follows the installer's naming.
    An operator who passed ``--runtime-dir`` chose their own path, and a sibling
    named after a tag would be a directory PawFlow invented behind their back;
    those are refreshed in place instead. Returns an empty string when there is
    nothing to work from.
    """
    if not host_app_dir or not target_image:
        return ""
    parent = posixpath.dirname(host_app_dir.rstrip("/"))
    current = posixpath.basename(host_app_dir.rstrip("/"))
    if not parent or current != image_tag_dirname(current_image):
        return host_app_dir
    return posixpath.join(parent, image_tag_dirname(target_image))

#: Written by ``scripts/run-pawflow-docker.sh`` onto the server container.
LABEL_DEPLOYMENT = "org.pawflow.deployment"
LABEL_APP_DIR = "org.pawflow.host-app-dir"
LABEL_HOME = "org.pawflow.home"
LABEL_PORT = "org.pawflow.port"
LABEL_NETWORK = "org.pawflow.network-mode"

DEPLOYMENT_INSTALLER = "installer"

#: Path the installer bind-mounts the persistent data directory to.
DATA_MOUNT = "/app/data"

#: Re-running the start script must not replay the *first-run* flags. A fresh
#: install may have been started with the bootstrap reset on; replaying it on
#: every update would wipe the installer state of a working server.
RESET_ON_UPDATE = {"PAWFLOW_BOOTSTRAP_RESET": ""}

_cache: Optional[Dict[str, Any]] = None
_cache_lock = threading.Lock()


def _env_map(raw: Dict[str, Any]) -> Dict[str, str]:
    """The container's ``PAWFLOW_*`` environment, as a dict.

    Only our own variables: the rest belongs to the image and is re-applied by
    the image itself when the new container starts.
    """
    env: Dict[str, str] = {}
    for item in ((raw.get("Config") or {}).get("Env") or []):
        name, sep, value = str(item).partition("=")
        if sep and name.startswith("PAWFLOW_"):
            env[name] = value
    return env


def _cmd_value(cmd: List[str], flag: str) -> str:
    try:
        return str(cmd[cmd.index(flag) + 1])
    except (ValueError, IndexError):
        return ""


def _extra_args(cmd: List[str]) -> str:
    """Whatever the operator appended after the flags the script itself passes.

    ``run-pawflow-docker.sh`` builds ``cli.py start --host H --port P $EXTRA``,
    so anything past those two flags is ``PAWFLOW_EXTRA_ARGS`` and must survive
    the update.
    """
    tokens = [str(c) for c in cmd]
    tail = 0
    for flag in ("--host", "--port"):
        if flag in tokens:
            tail = max(tail, tokens.index(flag) + 2)
    return " ".join(tokens[tail:]) if tail else ""


def _home_from_mounts(raw: Dict[str, Any]) -> str:
    """Host ``PAWFLOW_HOME``, read off the data bind mount.

    ``-v $PAWFLOW_HOME/data:/app/data`` is the one mount the server cannot run
    without, so its host side is the most reliable anchor for the rest.
    """
    for mount in (raw.get("Mounts") or []):
        if str(mount.get("Destination") or "") == DATA_MOUNT:
            source = str(mount.get("Source") or "")
            return source[:-len("/data")] if source.endswith("/data") else source
    return ""


def _image_of(raw: Dict[str, Any]) -> str:
    return str((raw.get("Config") or {}).get("Image") or "")


def installer_info(refresh: bool = False) -> Dict[str, Any]:
    """Installer identity of this server, or ``{}`` when it is not one.

    Cached like the compose lookup: none of it can change without the container
    being recreated, and by then this process is gone.
    """
    global _cache
    with _cache_lock:
        if _cache is not None and not refresh:
            return dict(_cache)

    info: Dict[str, Any] = {}
    container_id = self_container_id()
    if container_id:
        raw = inspect_container(container_id) or {}
        labels = (raw.get("Config") or {}).get("Labels") or {}
        env = _env_map(raw)
        cmd = [str(c) for c in ((raw.get("Config") or {}).get("Cmd") or [])]

        # Labels win when present; the environment is what makes a container
        # started before the labels existed updatable at all.
        app_dir = labels.get(LABEL_APP_DIR) or env.get("PAWFLOW_HOST_APP_DIR", "")
        home = (labels.get(LABEL_HOME) or _home_from_mounts(raw)
                or (env.get("PAWFLOW_HOST_DATA_DIR", "")[:-len("/data")]
                    if env.get("PAWFLOW_HOST_DATA_DIR", "").endswith("/data")
                    else ""))
        if app_dir:
            info = {
                "container_id": container_id,
                "container_name": (raw.get("Name", "") or "").lstrip("/"),
                "host_app_dir": app_dir,
                "pawflow_home": home,
                "image": _image_of(raw),
                "port": labels.get(LABEL_PORT) or _cmd_value(cmd, "--port"),
                "container_host": _cmd_value(cmd, "--host"),
                "extra_args": _extra_args(cmd),
                "network_mode": (labels.get(LABEL_NETWORK)
                                 or str((raw.get("HostConfig") or {})
                                        .get("NetworkMode") or "")),
                "env": env,
                "labelled": labels.get(LABEL_DEPLOYMENT) == DEPLOYMENT_INSTALLER,
            }
    if not info:
        logger.info("[installer] no installer markers on this container — "
                    "this is not a run-pawflow-docker.sh deployment")

    with _cache_lock:
        _cache = info
    return dict(info)


def start_script_env(info: Dict[str, Any], image: str,
                     source_dir: str = "") -> Dict[str, str]:
    """Environment that makes ``run-pawflow-docker.sh`` reproduce this server.

    Everything the running container carries is replayed, so a deployment keeps
    its bootstrap key, its uid/gid and its relay images across an update. Only
    what the update itself decides is overridden: the new image, and the
    first-run flags that must not be replayed.

    ``source_dir`` moves the deployment to a new host directory. The start
    script stamps it onto the container as ``org.pawflow.host-app-dir`` and
    ``PAWFLOW_HOST_APP_DIR``, which is how the *next* update finds the
    artifacts this one extracted; without it the label would keep pointing at
    the directory of the version that has just been replaced.
    """
    env = dict(info.get("env") or {})
    env.update({
        "PAWFLOW_IMAGE": image,
        "PAWFLOW_CONTAINER": info.get("container_name", ""),
        "PAWFLOW_HOME": info.get("pawflow_home", ""),
        "PAWFLOW_SOURCE_DIR": source_dir or info.get("host_app_dir", ""),
        "PAWFLOW_RECREATE_CONTAINER": "1",
    })
    for key, value in (("PAWFLOW_PORT", info.get("port", "")),
                       ("PAWFLOW_CONTAINER_HOST", info.get("container_host", "")),
                       ("PAWFLOW_NETWORK_MODE", info.get("network_mode", "")),
                       ("PAWFLOW_EXTRA_ARGS", info.get("extra_args", ""))):
        if value:
            env[key] = str(value)
    env.update(RESET_ON_UPDATE)
    return {k: v for k, v in env.items() if k not in (
        # Paths *inside* the container: the new one sets its own.
        "PAWFLOW_APP_DIR", "PAWFLOW_DATA_DIR",
        "PAWFLOW_HOST_APP_DIR", "PAWFLOW_HOST_DATA_DIR")}


def reset_cache() -> None:
    """Drop the cached lookup (tests, and an explicit refresh from the UI)."""
    global _cache
    with _cache_lock:
        _cache = None
