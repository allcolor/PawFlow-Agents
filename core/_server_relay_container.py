"""Atomic Docker lifecycle for PawFlow-managed relay service containers."""

import json
import logging
import subprocess  # nosec B404
import threading
import time
from typing import Dict, Optional, Tuple

from core.docker_utils import (
    PAWFLOW_SERVER_LABEL,
    PAWFLOW_SPAWNED_LABEL,
    docker_cmd,
    get_server_id,
)


logger = logging.getLogger(__name__)

_KIND_LABEL = "org.pawflow.kind"
_MANAGED_RELAY_KIND = "relay-managed"
_LOCKS: Dict[str, threading.Lock] = {}
_GENERATIONS: Dict[str, int] = {}
_LOCKS_GUARD = threading.Lock()
_REMOVAL_WAIT_SECONDS = 10.0
_REMOVAL_POLL_SECONDS = 0.05


def _container_claim(container_name: str) -> Tuple[threading.Lock, int]:
    """Return the name lock and the spawn generation observed by this caller."""
    with _LOCKS_GUARD:
        return (
            _LOCKS.setdefault(container_name, threading.Lock()),
            _GENERATIONS.get(container_name, 0),
        )


def _inspect_container(container_name: str) -> Optional[dict]:
    """Return identity, running state and labels, or None when absent."""
    result = subprocess.run(  # nosec B603
        docker_cmd() + [
            "inspect", "--format",
            "{{.Id}}\t{{.State.Running}}\t{{json .Config.Labels}}",
            container_name,
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return None
    parts = result.stdout.strip().split("\t", 2)
    if len(parts) != 3:
        raise RuntimeError(
            f"Docker returned invalid metadata for relay container '{container_name}'")
    try:
        labels = json.loads(parts[2]) or {}
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Docker returned invalid labels for relay container '{container_name}'") from exc
    return {"id": parts[0], "running": parts[1] == "true", "labels": labels}


def _wait_until_container_gone(container_name: str) -> bool:
    """Wait briefly for Docker's asynchronous removal to finish."""
    deadline = time.monotonic() + _REMOVAL_WAIT_SECONDS
    while _inspect_container(container_name) is not None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(_REMOVAL_POLL_SECONDS, remaining))
    return True


def start_managed_relay_container(
    container_name: str,
    command: list,
    *,
    replace: bool = False,
) -> Tuple[str, bool]:
    """Start once per name, reusing an overlapping healthy managed start.

    Returns ``(container_id, reused)``. Only a container carrying this server's
    ownership labels and the managed-relay kind may be reused or replaced.
    """
    lock, observed_generation = _container_claim(container_name)
    with lock:
        with _LOCKS_GUARD:
            current_generation = _GENERATIONS.get(container_name, 0)
        existing = _inspect_container(container_name)
        if existing:
            labels = existing["labels"]
            owned = (
                labels.get(PAWFLOW_SPAWNED_LABEL) == "1"
                and labels.get(PAWFLOW_SERVER_LABEL) == get_server_id()
                and labels.get(_KIND_LABEL) == _MANAGED_RELAY_KIND
            )
            if not owned:
                raise RuntimeError(
                    f"Refusing to replace Docker container '{container_name}': "
                    "it is not a managed relay owned by this PawFlow server")
            overlapping_start_won = current_generation != observed_generation
            if existing["running"] and not replace and overlapping_start_won:
                logger.info("Reusing managed relay container: %s", container_name)
                return str(existing["id"]), True
            removed = subprocess.run(  # nosec B603
                docker_cmd() + ["rm", "-f", str(existing["id"])],
                capture_output=True, text=True,
            )
            if removed.returncode != 0:
                reason = removed.stderr.strip()
                removal_in_progress = (
                    "removal of container" in reason.lower()
                    and "already in progress" in reason.lower()
                )
                if not removal_in_progress or not _wait_until_container_gone(
                        container_name):
                    raise RuntimeError(
                        f"Failed to remove managed relay container '{container_name}': "
                        f"{reason or 'Docker gave no reason'}")
                logger.info(
                    "Managed relay container %s finished its asynchronous removal",
                    container_name,
                )

        result = subprocess.run(  # nosec B603
            command, capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to start relay container: {result.stderr.strip()}")
        container_id = result.stdout.strip()
        if not container_id:
            raise RuntimeError(
                f"Failed to start relay container '{container_name}': "
                "Docker returned no container id")
        with _LOCKS_GUARD:
            _GENERATIONS[container_name] = current_generation + 1
        return container_id, False
