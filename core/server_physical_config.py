"""Canonical, atomic configuration for server-managed physical relay groups.

Logical ServiceDefs are projections. A retained deletion record suppresses stale
service-file projections after an interrupted save; it never deletes user data.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import secrets
import tempfile
import time
import uuid
from pathlib import Path

from core._service_defs import ServiceDef
from core._service_registry_io import _ServiceRegistryIOMixin

_CREDENTIALS = {"token", "internal_token", "server_internal_token"}


def normalize_scope(scope: str, scope_id: str) -> tuple[str, str]:
    if scope not in ("global", "user", "conv"):
        raise ValueError("A valid relay scope is required")
    if scope == "global":
        return scope, "__global__"
    if not isinstance(scope_id, str) or not scope_id.strip():
        raise ValueError("The relay scope_id is required")
    return scope, scope_id


def _root() -> Path:
    from core.paths import RUNTIME_DIR

    return RUNTIME_DIR / "relay_physicals"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def scope_directory(scope: str, scope_id: str) -> Path:
    scope, scope_id = normalize_scope(scope, scope_id)
    return _root() / _digest(json.dumps([scope, scope_id]))


def record_path(scope: str, scope_id: str, physical_id: str) -> Path:
    if not isinstance(physical_id, str) or not physical_id:
        raise ValueError("physical_id is required")
    return scope_directory(scope, scope_id) / (_digest(physical_id) + ".json")


def _credentials(record: dict, *, encrypt: bool) -> dict:
    result = copy.deepcopy(record)
    transform = (_ServiceRegistryIOMixin._encrypt_config if encrypt
                 else _ServiceRegistryIOMixin._decrypt_config)
    keys = _CREDENTIALS | _ServiceRegistryIOMixin._sensitive_keys("relay")
    for member in result["members"] + result.get("retired_members", []):
        member["config"] = transform(member["config"], keys)
        if not encrypt and any(
                str(member["config"].get(key, "")).startswith("enc:") for key in keys):
            raise ValueError("Unable to decrypt physical relay credentials")
    return result


def load_groups(scope: str, scope_id: str) -> list[dict]:
    scope, scope_id = normalize_scope(scope, scope_id)
    directory = scope_directory(scope, scope_id)
    if not directory.exists():
        return []
    records = []
    for filename in sorted(directory.glob("*.json")):
        record = json.loads(filename.read_text(encoding="utf-8"))
        if (record.get("version") != 1
                or (record.get("scope"), record.get("scope_id")) != (scope, scope_id)
                or filename != record_path(scope, scope_id, record["physical_id"])):
            raise ValueError("Invalid physical relay configuration ownership")
        records.append(_credentials(record, encrypt=False))
    return records


def stored_records():
    """Enumerate ownership metadata even when service projections are missing."""
    for filename in _root().glob("*/*.json"):
        record = json.loads(filename.read_text(encoding="utf-8"))
        if (record.get("version") != 1 or filename != record_path(
                record["scope"], record["scope_id"], record["physical_id"])):
            raise ValueError("Invalid physical relay configuration ownership")
        yield record


def save_group(record: dict) -> None:
    """Publish a complete group in one file replacement, or leave the old one."""
    filename = record_path(record["scope"], record["scope_id"], record["physical_id"])
    payload = json.dumps(_credentials(record, encrypt=True), ensure_ascii=False, indent=2)
    filename.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".physical-", dir=filename.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, filename)
    finally:
        Path(temporary).unlink(missing_ok=True)


def legacy_group(definition: ServiceDef) -> dict:
    """Retain every logical setting while adding a separately named parent."""
    from core.server_relay_manager import ServerRelayManager

    scope, scope_id = normalize_scope(definition.scope, definition.scope_id)
    member = copy.deepcopy(definition.to_dict())
    config = member["config"]
    user_id = config.get("server_user_id") or (scope_id if scope == "user" else "")
    runtime = ServerRelayManager.get_instance().service_relay_config(
        definition.service_id, scope=scope, scope_id=scope_id, user_id=user_id)
    for key, value in runtime.items():
        config.setdefault(key, value)
    record = {
        "version": 1, "physical_id": definition.service_id,
        "name": definition.service_id + " (physical)",
        "scope": scope, "scope_id": scope_id, "user_id": user_id,
        "kind": "workspace", "enabled": bool(definition.enabled), "deleted": False,
        "container_name": config["server_container_name"],
        "members": [member], "known_ids": [definition.service_id],
        "revision": 1, "created_at": time.time(), "updated_at": time.time(),
    }
    return record


def projected_definitions(record: dict) -> dict[str, ServiceDef]:
    if record["deleted"]:
        return {}
    definitions = {}
    for member in record["members"]:
        definition = ServiceDef.from_dict(copy.deepcopy(member))
        definition.scope, definition.scope_id = record["scope"], record["scope_id"]
        definition.enabled = record["enabled"]
        definition.config.update({
            "server_physical_id": record["physical_id"],
            "server_physical_name": record["name"],
            "server_physical_enabled": record["enabled"],
            "server_physical_revision": record["revision"],
            "server_container_name": record["container_name"],
            "server_scope": record["scope"], "server_scope_id": record["scope_id"],
            "server_user_id": record["user_id"],
        })
        definitions[definition.service_id] = definition
    return definitions


def overlay_definitions(definitions, scope, scope_id, records):
    """Rebuild logical projections before any service is allowed to connect."""
    definitions = dict(definitions)
    for record in records:
        if (record["scope"], record["scope_id"]) != (scope, scope_id):
            raise ValueError("Physical relay scope mismatch")
        for service_id in record["known_ids"]:
            existing = definitions.get(service_id)
            if existing:
                owner = existing.config.get("server_physical_id")
                if (existing.service_type != "relay" or not existing.config.get("server_managed")
                        or (owner and owner != record["physical_id"])):
                    raise ValueError(f"Logical relay '{service_id}' has a different owner")
            definitions.pop(service_id, None)
        definitions.update(projected_definitions(record))
    return definitions


def hydrate_scope(scope, scope_id, definitions):
    """Load/migrate while the registry holds its load lock; never run Docker."""
    scope, scope_id = normalize_scope(scope, scope_id)
    records = load_groups(scope, scope_id)
    known_ids = {name for record in records for name in record["known_ids"]}
    for definition in definitions.values():
        cfg = definition.config or {}
        if (definition.service_type != "relay" or not cfg.get("server_managed")
                or cfg.get("server_kind", "workspace") != "workspace"
                or definition.service_id in known_ids):
            continue
        if cfg.get("server_physical_id"):
            raise ValueError("Logical relay has no canonical physical configuration")
        record = legacy_group(definition)
        save_group(record)
        records.append(record)
    return overlay_definitions(definitions, scope, scope_id, records)


def project_scope(registry, scope: str, scope_id: str, records: list[dict]) -> None:
    """Overlay canonical groups on legacy files without touching live workers."""
    scope, scope_id = normalize_scope(scope, scope_id)
    with registry._data_lock:
        registry._definitions[scope_id] = overlay_definitions(
            registry._definitions.get(scope_id, {}), scope, scope_id, records)
        for record in records:
            for service_id, definition in projected_definitions(record).items():
                live = registry._live_instances.get(scope_id, {}).get(service_id)
                if live is not None:
                    live.config.update(definition.config)


def adopt_scope(registry, scope: str, scope_id: str) -> list[dict]:
    """Migrate legacy singleton configuration without changing its connection."""
    scope, scope_id = normalize_scope(scope, scope_id)
    if scope_id in registry._load_failed:
        raise ValueError("Cannot migrate a scope whose services failed to load")
    records = load_groups(scope, scope_id)
    known_ids = {name for record in records for name in record["known_ids"]}
    with registry._data_lock:
        definitions = list(registry._definitions.get(scope_id, {}).values())
    for definition in definitions:
        config = definition.config or {}
        if (definition.service_type != "relay" or not config.get("server_managed")
                or config.get("server_kind", "workspace") != "workspace"
                or definition.service_id in known_ids):
            continue
        if config.get("server_physical_id"):
            raise ValueError("Logical relay has no canonical physical configuration")
        record = legacy_group(definition)
        save_group(record)
        records.append(record)
    project_scope(registry, scope, scope_id, records)
    return records


def _logical_id(value) -> str:
    if (not isinstance(value, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value)
            or value.casefold() in {"filestore", "store", "server"}):
        raise ValueError("A unique path-safe logical relay name is required")
    return value


def prepare_group(registry, scope: str, scope_id: str, user_id: str,
                  body: dict, previous: dict | None) -> dict:
    """Validate a whole group before any persistence or container interruption."""
    from core._relay_naming import _relay_container_name, _relay_runtime_host_dir

    scope, scope_id = normalize_scope(scope, scope_id)
    if scope != "global" and not user_id:
        raise ValueError("The relay owner is required")
    name = body.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Physical relay name is required")
    workspaces = body.get("workspaces")
    if not isinstance(workspaces, list) or not workspaces:
        raise ValueError("A physical relay requires at least one logical relay")
    if previous and body.get("revision") != previous["revision"]:
        raise ValueError("Physical relay configuration changed; reload before saving")
    if previous and (previous["scope"], previous["scope_id"], previous["user_id"]) != (
            scope, scope_id, user_id):
        raise ValueError("Physical relay owner cannot change")
    physical_id = previous["physical_id"] if previous else str(uuid.uuid4())
    now = time.time()
    record = copy.deepcopy(previous) if previous else {
        "version": 1, "physical_id": physical_id, "scope": scope, "scope_id": scope_id,
        "user_id": user_id, "kind": "workspace", "enabled": False, "deleted": False,
        "container_name": _relay_container_name(physical_id),
        "known_ids": [], "revision": 0, "created_at": now,
    }
    retained = ((previous or {}).get("retired_members", [])
                + (previous or {}).get("members", []))
    old_members = {m["service_id"]: m for m in retained}
    old_names = {name.casefold(): name for name in old_members}
    members, names = [], set()
    for workspace in workspaces:
        if not isinstance(workspace, dict):
            raise ValueError("Each logical relay must be a configuration object")  # noqa: TRY004 -- API validation
        service_id = _logical_id(workspace.get("service_id"))
        retained_name = old_names.get(service_id.casefold())
        if retained_name is not None and retained_name != service_id:
            raise ValueError(f"Logical relay name must retain its original case: {retained_name}")
        if service_id.casefold() in names:
            raise ValueError("Logical relay names must be unique")
        names.add(service_id.casefold())
        if service_id in old_members:
            member = copy.deepcopy(old_members[service_id])
        else:
            directory = _root().parent / "relay_workspaces" / _digest(
                json.dumps([scope, scope_id, service_id]))
            member = ServiceDef(
                service_id, "relay", scope=scope, scope_id=scope_id,
                config={
                    "server_managed": True, "server_kind": "workspace",
                    "token": secrets.token_urlsafe(32),
                    "server_workspace_dir": str(directory),
                    "server_workspace_host_dir": _relay_runtime_host_dir(directory),
                    "server_home_volume": "pawflow_home_" + service_id,
                    "mode": "readwrite", "server_local_exec": False,
                    "allow_service_tunnels": False, "allow_exec": True,
                }).to_dict()
        config = member["config"]
        mode = workspace.get("mode", config.get("mode", "readwrite"))
        if mode not in ("readwrite", "readonly"):
            raise ValueError("Logical mode must be readwrite or readonly")
        config["mode"] = mode
        for key, default in (("server_local_exec", False), ("allow_service_tunnels", False),
                             ("allow_exec", True)):
            value = workspace.get(key, config.get(key, default))
            if not isinstance(value, bool):
                raise ValueError(f"{key} must be a boolean")  # noqa: TRY004 -- API validation
            config[key] = value
        config.update({
            "server_physical_id": physical_id, "server_scope": scope,
            "server_scope_id": scope_id, "server_user_id": user_id,
        })
        registry._check_relay_conflict(
            service_id, scope, scope_id, config,
            exclude_scope_id=scope_id,
            exclude_service_id=service_id if service_id in old_members else "",
            physical_group=True)
        members.append(member)
    for other in stored_records():
        same_scope = (other["scope"], other["scope_id"]) == (scope, scope_id)
        if same_scope and other["physical_id"] == physical_id:
            continue
        if (same_scope and other["name"].casefold() == name.strip().casefold()
                and not other["deleted"]):
            raise ValueError("Physical relay name already exists in this scope")
        if names & {value.casefold() for value in other["known_ids"]}:
            raise ValueError("Logical relay name belongs to another physical configuration")
    record.update({
        "name": name.strip(), "members": members, "deleted": False,
        "retired_members": [m for key, m in old_members.items() if key.casefold() not in names],
        "known_ids": sorted(set(record["known_ids"]) | {m["service_id"] for m in members}),
        "revision": record["revision"] + 1, "updated_at": now,
    })
    return record
