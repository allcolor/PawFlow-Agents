"""Where this server actually lives on its host.

The server runs inside a container: it sees ``/app``, never the directory the
user ran ``docker compose up`` from. Anything that has to drive compose from
outside — the self-update in :mod:`core.update_manager` — needs that host path,
and asking the operator to type it into a settings field is one more thing to
get wrong.

Compose already records it. Every container it creates carries labels:

* ``com.docker.compose.project``            — project name
* ``com.docker.compose.project.working_dir`` — **host** path of the project
* ``com.docker.compose.project.config_files``— host paths of the compose files
* ``com.docker.compose.service``            — this container's service

So the job is to find our own container id, read its labels, and cache the
result. A deployment that carries no such labels is simply not a compose
deployment, and callers refuse rather than guess.
"""

from __future__ import annotations

import json
import logging
import re
import socket
import subprocess  # nosec B404
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.docker_utils import docker_cmd

logger = logging.getLogger(__name__)

_CONTAINER_ID_RE = re.compile(r"[0-9a-f]{64}")

LABEL_PROJECT = "com.docker.compose.project"
LABEL_WORKING_DIR = "com.docker.compose.project.working_dir"
LABEL_CONFIG_FILES = "com.docker.compose.project.config_files"
LABEL_SERVICE = "com.docker.compose.service"

_cache: Optional[Dict[str, Any]] = None
_cache_lock = threading.Lock()


def self_container_id() -> str:
    """Best-effort id of the container this process runs in.

    ``/proc/self/mountinfo`` is the reliable source — Docker bind-mounts
    ``/etc/hosts`` and friends from ``/var/lib/docker/containers/<id>/``, so the
    id appears verbatim. cgroup v1 layouts expose it too. The hostname is the
    last resort: Docker defaults it to the short id, but an explicit
    ``hostname:`` in the compose file overrides that, so it is never trusted
    first.
    """
    for path in ("/proc/self/mountinfo", "/proc/self/cgroup"):
        try:
            text = Path(path).read_text(encoding="utf-8", errors="ignore")
        except Exception:
            logger.debug("Could not read %s", path, exc_info=True)
            continue
        match = _CONTAINER_ID_RE.search(text)
        if match:
            return match.group(0)
    try:
        host = socket.gethostname().strip()
    except Exception:
        return ""
    return host if re.fullmatch(r"[0-9a-f]{12,64}", host or "") else ""


def inspect_container(container: str) -> Dict[str, Any]:
    """``docker inspect`` of one container, or ``{}`` when it cannot be read."""
    try:
        result = subprocess.run(  # nosec B603
            docker_cmd() + ["inspect", container],
            capture_output=True, text=True, timeout=20)
    except Exception:
        logger.debug("docker inspect %s failed", container, exc_info=True)
        return {}
    if result.returncode != 0:
        return {}
    try:
        data = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return {}
    return data[0] if isinstance(data, list) and data else {}


def _config_files(raw: str) -> List[str]:
    return [p.strip() for p in (raw or "").split(",") if p.strip()]


def compose_info(refresh: bool = False) -> Dict[str, Any]:
    """Compose identity of this server, or ``{}`` when it is not one.

    Resolved once and cached: it cannot change without the container being
    recreated, at which point the process is gone anyway.
    """
    global _cache
    with _cache_lock:
        if _cache is not None and not refresh:
            return dict(_cache)

    info: Dict[str, Any] = {}
    container_id = self_container_id()
    if container_id:
        raw = inspect_container(container_id)
        labels = ((raw.get("Config") or {}).get("Labels") or {}) if raw else {}
        working_dir = labels.get(LABEL_WORKING_DIR, "")
        if labels.get(LABEL_PROJECT) and working_dir:
            info = {
                "container_id": container_id,
                "container_name": (raw.get("Name", "") or "").lstrip("/"),
                "project": labels.get(LABEL_PROJECT, ""),
                "service": labels.get(LABEL_SERVICE, ""),
                "working_dir": working_dir,
                "config_files": _config_files(labels.get(LABEL_CONFIG_FILES, "")),
            }
    if not info:
        logger.info("[compose] no compose labels on this container — "
                    "self-update is unavailable")

    with _cache_lock:
        _cache = info
    return dict(info)


def reset_cache() -> None:
    """Drop the cached lookup (tests, and an explicit refresh from the UI)."""
    global _cache
    with _cache_lock:
        _cache = None
