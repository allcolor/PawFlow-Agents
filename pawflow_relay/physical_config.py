"""Physical relay configuration over the existing workspace records.

A physical relay always contains at least one logical workspace. Legacy shares
are normalized in memory and persisted with the next configuration mutation,
without changing identities, permissions, credentials or HOME volume names.
"""

from __future__ import annotations

from pathlib import Path

from pawflow_relay.manager import _workspace_config_lock
from pawflow_relay.physical_plan import plan_physical_relay


def load_workspaces() -> dict:
    from pawflow_relay import manager

    records = manager._load_json(manager._WORKSPACES_FILE)
    for name, share in records.items():
        if "physical_name" not in share and "physical_id" not in share:
            share["physical_name"] = name
            share["physical_id"] = share["relay_id"]
        if not share.get("physical_name") or not share.get("physical_id"):
            raise ValueError(f"Workspace '{name}' has incomplete physical relay ownership")
    return records


def _groups(records: dict) -> list[dict]:
    from pawflow_relay import manager

    groups = {}
    for share in records.values():
        name = share["physical_name"]
        groups.setdefault(name, []).append(share)
    result = []
    ids = set()
    for name, members in groups.items():
        physical_id = members[0]["physical_id"]
        if physical_id in ids or any(s["physical_id"] != physical_id for s in members):
            raise ValueError("Physical relay identities must be distinct and consistent")
        ids.add(physical_id)
        plan = plan_physical_relay(physical_id, members)
        running = is_running(physical_id)
        result.append({
            "name": name,
            "physical_id": physical_id,
            "server": plan.server,
            "docker_image": plan.docker_image,
            "revision": plan.revision,
            "workspaces": sorted(members, key=lambda share: share["name"]),
            "running": running,
            "cleanup_pending": not running and manager._workspace_runtime_lock_path(physical_id).exists(),
        })
    return result


def list_physicals() -> list[dict]:
    return _groups(load_workspaces())


def get_physical(name: str) -> dict:
    for physical in list_physicals():
        if physical["name"] == name:
            return physical
    raise ValueError(f"Unknown physical relay '{name}'")


def is_running(physical_id: str) -> bool:
    from pawflow_relay import manager

    lock = manager._read_runtime_lock(
        manager._workspace_runtime_lock_path(physical_id))
    return manager._process_is_running(int(lock.get("pid") or 0))


def require_stopped(physical: dict) -> None:
    from pawflow_relay import manager

    if is_running(physical["physical_id"]):
        raise ValueError(
            f"Stop physical relay '{physical['name']}' before changing its directories; "
            "restart it afterwards to reconnect the complete group")
    if manager._workspace_runtime_lock_path(physical["physical_id"]).exists():
        raise ValueError(f"Clean up physical relay '{physical['name']}' before changing its directories")


@_workspace_config_lock()
def save_physical(name: str, server: str, docker_image: str,
                  workspaces: list[dict], *, validate_only: bool = False,
                  physical_id: str | None = None) -> dict:
    """Replace one stopped physical relay's complete directory configuration."""
    from pawflow_relay import manager

    if not isinstance(name, str) or not name.strip():
        raise ValueError("Physical relay name is required")
    if not isinstance(workspaces, list) or not workspaces:
        raise ValueError("A physical relay requires at least one logical workspace")
    manager.get_server(server)
    records = load_workspaces()
    physicals = _groups(records)
    if physical_id is not None:
        if not isinstance(physical_id, str) or not physical_id:
            raise ValueError("An existing physical_id is required when renaming a physical relay")
        existing = next((p for p in physicals if p["physical_id"] == physical_id), None)
        if existing is None:
            raise ValueError(f"Unknown physical relay identity '{physical_id}'")
        if any(p["name"] == name and p["physical_id"] != physical_id for p in physicals):
            raise ValueError(f"Physical relay name '{name}' is already in use")
    else:
        existing = next((p for p in physicals if p["name"] == name), None)
    source_name = existing["name"] if existing else name
    if existing and not validate_only:
        require_stopped(existing)
    now = manager._now()
    members = []
    requested_names = {
        entry.get("name") for entry in workspaces
        if isinstance(entry, dict) and isinstance(entry.get("name"), str)
    }
    removed = [
        share for share in records.values()
        if share["physical_name"] == source_name and share["name"] not in requested_names
    ]
    for entry in workspaces:
        if not isinstance(entry, dict):
            raise ValueError("Each workspace must be a configuration object")  # noqa: TRY004 - config validation
        logical_name = entry.get("name")
        if not isinstance(logical_name, str) or not logical_name.strip():
            raise ValueError("Logical relay name is required")
        previous = records.get(logical_name, {})
        if not previous and entry.get("relay_id"):
            previous = next((
                share for share in removed if share["relay_id"] == entry["relay_id"]
            ), {})
        if previous and previous["physical_name"] != source_name:
            raise ValueError(f"Workspace '{logical_name}' belongs to another physical relay")
        relay_id = entry.get("relay_id") or previous.get("relay_id") or logical_name
        if previous and relay_id != previous["relay_id"]:
            raise ValueError("Existing logical relay identities must be retained")
        raw_path = entry.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError("Workspace path is required")
        directory = Path(raw_path).expanduser().resolve()
        if not directory.is_dir():
            raise ValueError(f"Workspace '{logical_name}' is not an existing directory")
        if not previous and not entry.get("relay_id") and any(
            Path(share["path"]).expanduser().resolve() == directory for share in removed
        ):
            raise ValueError(
                "Reusing a removed workspace's path requires an explicit relay_id; "
                "retain its identity to rename it, or remove it in a separate save"
            )
        share = {
            **previous,
            "name": logical_name,
            "server": server,
            "path": str(directory),
            "docker_image": docker_image,
            "relay_id": relay_id,
            "created_at": previous.get("created_at", now),
            "updated_at": now,
            "physical_name": name,
        }
        for field, default in (
            ("mode", "rw"), ("allow_exec", True), ("allow_remote_desktop", True),
            ("allow_local", False), ("allow_service_tunnels", False),
        ):
            share[field] = entry.get(field, previous.get(field, default))
        members.append(share)
    physical_id = existing["physical_id"] if existing else members[0]["relay_id"]
    for share in members:
        share["physical_id"] = physical_id
    plan_physical_relay(physical_id, members)
    updated = {
        key: share for key, share in records.items()
        if share["physical_name"] != source_name
    }
    updated.update({share["name"]: share for share in members})
    physicals = _groups(updated)
    all_ids = [share["relay_id"].casefold() for share in updated.values()]
    if len(all_ids) != len(set(all_ids)):
        raise ValueError("Logical relay identities must be unique across physical relays")
    if not validate_only:
        manager._save_json(manager._WORKSPACES_FILE, updated)
    return next(p for p in physicals if p["name"] == name)


@_workspace_config_lock()
def delete_physical(name: str) -> dict:
    """Remove a stopped group's configuration, retaining its data and HOME."""
    from pawflow_relay import manager

    records = load_workspaces()
    physical = next((p for p in _groups(records) if p["name"] == name), None)
    if physical is None:
        raise ValueError(f"Unknown physical relay '{name}'")
    require_stopped(physical)
    manager._save_json(manager._WORKSPACES_FILE, {
        key: share for key, share in records.items()
        if share["physical_name"] != name
    })
    return physical
