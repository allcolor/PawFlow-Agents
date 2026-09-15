"""Immutable startup plans for a fixed set of logical workspace relays.

The complete mount set belongs to one physical container revision. Changing
that set requires replacing the container; this module performs no runtime I/O.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath, PureWindowsPath


def _identity(value: str, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value):
        raise ValueError(f"{label} must be a nonempty path-safe identity")
    return value


@dataclass(frozen=True)
class WorkspaceLaunch:
    name: str
    relay_id: str
    root_source: str
    mode: str
    allow_exec: bool
    allow_remote_desktop: bool
    allow_local: bool
    allow_service_tunnels: bool

    @property
    def workspace_target(self) -> str:
        return "/workspace"

    @property
    def home_target(self) -> str:
        return "/home/pawflow"

    @property
    def home_volume(self) -> str:
        return f"pawflow_home_{self.relay_id}"

    @property
    def root_mount(self) -> str:
        digest = hashlib.sha256(self.relay_id.encode("utf-8")).hexdigest()
        return f"/run/pawflow-physical/{digest}/workspace"

    @property
    def home_mount(self) -> str:
        digest = hashlib.sha256(self.relay_id.encode("utf-8")).hexdigest()
        return f"/run/pawflow-physical/{digest}/home"


@dataclass(frozen=True)
class PhysicalRelayPlan:
    physical_id: str
    server: str
    docker_image: str
    exports: tuple[WorkspaceLaunch, ...]

    @property
    def revision(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def plan_physical_relay(physical_id: str, workspaces: list[dict]) -> PhysicalRelayPlan:
    """Snapshot explicit compatible shares without retaining credential records."""
    _identity(physical_id, "physical_id")
    if not isinstance(workspaces, (list, tuple)) or not workspaces:
        raise ValueError("At least one workspace is required")
    exports = []
    names, relay_ids, servers, images = set(), set(), set(), set()
    for share in workspaces:
        if not isinstance(share, dict):
            raise ValueError("Each workspace must be a workspace record")  # noqa: TRY004 - config validation
        name = share.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("Workspace name is required")
        relay_id = _identity(share.get("relay_id"), "relay_id")
        if name in names or relay_id.casefold() in relay_ids:
            raise ValueError("Workspace names and relay identities must be distinct")
        names.add(name)
        relay_ids.add(relay_id.casefold())
        server = share.get("server")
        if not isinstance(server, str) or not server:
            raise ValueError("Workspace server is required")
        servers.add(server)
        image = share.get("docker_image", "")
        if not isinstance(image, str):
            raise ValueError("Workspace Docker image must be a string")  # noqa: TRY004 - config validation
        image = image or "pawflow-relay-dev:latest"
        images.add(image)
        root = share.get("path")
        if (not isinstance(root, str) or not root or chr(0) in root
                or not (PurePosixPath(root).is_absolute() or PureWindowsPath(root).is_absolute())):
            raise ValueError("Workspace path must be an absolute directory path")
        mode = share.get("mode", "rw")
        if mode not in ("rw", "ro"):
            raise ValueError("Workspace mode must be 'rw' or 'ro'")
        permissions = {}
        for key, default in (("allow_exec", True), ("allow_remote_desktop", True),
                             ("allow_local", False), ("allow_service_tunnels", False)):
            value = share.get(key, default)
            if not isinstance(value, bool):
                raise ValueError(f"Workspace {key} must be a boolean")  # noqa: TRY004 - config validation
            permissions[key] = value
        exports.append(WorkspaceLaunch(name, relay_id, root, mode, **permissions))
    if len(servers) != 1 or len(images) != 1:
        raise ValueError("A physical relay requires one server profile and Docker image")
    return PhysicalRelayPlan(
        physical_id, next(iter(servers)), next(iter(images)),
        tuple(sorted(exports, key=lambda export: export.relay_id)),
    )
