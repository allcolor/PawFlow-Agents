"""Conversation-scoped filesystem bindings.

Filesystem bindings describe which filesystem services a conversation may use.
Rclone bindings are additionally materialized inside every relay linked to the
conversation. Native API-backed filesystem services are not mounted into relays;
they are simply made available to server-side filesystem tools.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

EXTRA_KEY = "remote_fs_bindings"
REMOTE_ROOT = "/remote"
_VALID_SCOPES = {"global", "user", "conv"}
_FILESYSTEM_SERVICE_TYPES = {"filesystem", "googleDrive", "oneDrive", "rcloneFilesystem"}
_RCLONE_COMPATIBLE_TYPES = {"rcloneFilesystem"}
_TOOL_COMPATIBLE_TYPES = _FILESYSTEM_SERVICE_TYPES - _RCLONE_COMPATIBLE_TYPES


def sanitize_mount_dir(service_id: str) -> str:
    """Derive a stable directory name from a service id."""
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", service_id.strip())
    name = name.strip("._-")
    if not name or name in {".", ".."}:
        raise ValueError(f"Filesystem service id {service_id!r} cannot be used as a mount directory")
    return name[:128]


def mount_path_for(service_id: str) -> str:
    return f"{REMOTE_ROOT}/{sanitize_mount_dir(service_id)}"


def is_rclone_service(service_type: str) -> bool:
    return service_type in _RCLONE_COMPATIBLE_TYPES


def get_bindings(cid: str) -> Dict[str, Any]:
    from core.conversation_store import ConversationStore
    raw = ConversationStore.instance().get_extra_cached(cid, EXTRA_KEY, default=None)
    if not isinstance(raw, dict):
        return {"linked": []}
    linked = raw.get("linked")
    if not isinstance(linked, list):
        raw["linked"] = []
    return raw


def list_linked(cid: str) -> List[Dict[str, Any]]:
    return list(get_bindings(cid).get("linked", []))


def _store_bindings(cid: str, bindings: Dict[str, Any]) -> None:
    from core.conversation_store import ConversationStore
    ConversationStore.instance().set_extra(cid, EXTRA_KEY, bindings)


def _normalize_scope(scope: str) -> str:
    scope = (scope or "").strip().lower()
    if scope == "conversation":
        scope = "conv"
    return scope


def _resolve_service_definition(user_id: str, cid: str, service_id: str, scope: str = ""):
    from core.service_registry import ServiceRegistry, SCOPE_CONV, SCOPE_GLOBAL, SCOPE_USER
    reg = ServiceRegistry.get_instance()
    scope = _normalize_scope(scope)
    if scope == SCOPE_GLOBAL:
        sdef = reg.get_definition(SCOPE_GLOBAL, "", service_id)
    elif scope == SCOPE_USER:
        sdef = reg.get_definition(SCOPE_USER, user_id, service_id)
    elif scope == SCOPE_CONV:
        sdef = reg.get_definition(SCOPE_CONV, cid, service_id)
    elif scope:
        raise ValueError(f"Unsupported filesystem service scope: {scope}")
    else:
        sdef = reg.resolve_definition(service_id, user_id=user_id, conv_id=cid)
    if sdef is None:
        raise ValueError(f"Filesystem service '{service_id}' not found")
    if sdef.scope not in _VALID_SCOPES:
        raise ValueError(f"Filesystem service '{service_id}' has unsupported scope '{sdef.scope}'")
    if sdef.service_type not in _FILESYSTEM_SERVICE_TYPES:
        raise ValueError(f"Filesystem service '{service_id}' not found")
    return sdef


def list_available(user_id: str, cid: str) -> List[Dict[str, Any]]:
    """List non-relay filesystem services visible to a conversation."""
    from core.service_registry import ServiceRegistry
    reg = ServiceRegistry.get_instance()
    result = []
    seen = set()
    for sdef in reg.resolve_all(user_id=user_id, conv_id=cid, enabled_only=True).values():
        if sdef.service_id in seen:
            continue
        if sdef.scope not in _VALID_SCOPES:
            continue
        if sdef.service_type not in _FILESYSTEM_SERVICE_TYPES:
            continue
        seen.add(sdef.service_id)
        is_rclone = is_rclone_service(sdef.service_type)
        result.append({
            "service_id": sdef.service_id,
            "service_type": sdef.service_type,
            "scope": sdef.scope,
            "mount_path": mount_path_for(sdef.service_id) if is_rclone else "",
            "access": "mounted" if is_rclone else "tools",
            "description": sdef.description,
        })
    return result


def _check_mount_collision(cid: str, service_id: str) -> None:
    target = sanitize_mount_dir(service_id)
    for item in list_linked(cid):
        existing_type = item.get("service_type", "")
        if existing_type and not is_rclone_service(existing_type):
            continue
        other = item.get("service_id", "")
        if other == service_id:
            continue
        if other and sanitize_mount_dir(other) == target:
            raise ValueError(
                f"Filesystem service '{service_id}' maps to {REMOTE_ROOT}/{target}, "
                f"already used by '{other}'")


def link_filesystem(cid: str, user_id: str, service_id: str,
                    scope: str = "", mode: str = "readwrite") -> Dict[str, Any]:
    if not cid:
        raise ValueError("conversation_id is required")
    if not user_id:
        raise ValueError("user_id is required")
    if not service_id:
        raise ValueError("service_id is required")
    sdef = _resolve_service_definition(user_id, cid, service_id, scope)
    is_rclone = is_rclone_service(sdef.service_type)
    if is_rclone:
        _check_mount_collision(cid, service_id)
    mode = mode if mode in {"read", "readwrite"} else "readwrite"
    bindings = get_bindings(cid)
    linked = bindings.setdefault("linked", [])
    entry = {
        "service_id": service_id,
        "scope": sdef.scope,
        "service_type": sdef.service_type,
        "mode": mode,
        "backend": "rclone" if is_rclone else "service",
        "access": "mounted" if is_rclone else "tools",
        "enabled": True,
    }
    if is_rclone:
        entry["mount_path"] = mount_path_for(service_id)
    for idx, existing in enumerate(linked):
        if existing.get("service_id") == service_id:
            linked[idx] = {**existing, **entry}
            _store_bindings(cid, bindings)
            notify_linked_relays(cid, user_id)
            return {**linked[idx], "mount_path": mount_path_for(service_id) if is_rclone else ""}
    linked.append(entry)
    _store_bindings(cid, bindings)
    notify_linked_relays(cid, user_id)
    return {**entry, "mount_path": mount_path_for(service_id) if is_rclone else ""}


def unlink_filesystem(cid: str, user_id: str, service_id: str) -> bool:
    bindings = get_bindings(cid)
    linked = bindings.setdefault("linked", [])
    before = len(linked)
    bindings["linked"] = [item for item in linked if item.get("service_id") != service_id]
    removed = len(bindings["linked"]) != before
    if removed:
        _store_bindings(cid, bindings)
        notify_linked_relays(cid, user_id)
    return removed


def _conversation_ids_for_user(user_id: str) -> Iterable[str]:
    from core.conversation_store import ConversationStore
    store = ConversationStore.instance()
    try:
        store._ensure_loaded()  # existing store has no public iterator yet
    except Exception:
        logger.debug("Conversation scan for remote FS mounts failed", exc_info=True)
    for cid, owner in list(getattr(store, "_cid_user", {}).items()):
        if owner == user_id:
            yield cid


def conversation_ids_for_relay(relay_id: str, user_id: str) -> List[str]:
    from core.relay_bindings import get_linked
    result = []
    for cid in _conversation_ids_for_user(user_id):
        try:
            if relay_id in get_linked(cid):
                result.append(cid)
        except Exception:
            logger.debug("Relay binding scan failed for %s", cid, exc_info=True)
    return result


def _rclone_config_for(user_id: str, sdef) -> Dict[str, str]:
    cfg = dict(sdef.config or {})
    if sdef.service_type == "rcloneFilesystem":
        if cfg.get("rclone_config"):
            return {"_raw": str(cfg["rclone_config"])}
        rclone_type = cfg.get("rclone_type") or cfg.get("type")
        if not rclone_type:
            raise ValueError(f"rcloneFilesystem service '{sdef.service_id}' is missing rclone_type")
        skip = {"rclone_type", "type", "mode", "allowed_paths", "denied_paths"}
        return {
            str(k): str(v)
            for k, v in cfg.items()
            if k not in skip and v not in (None, "")
        } | {"type": str(rclone_type)}
    raise ValueError(f"Unsupported remote filesystem service type: {sdef.service_type}")


def build_manifest_for_conversation(user_id: str, cid: str) -> Dict[str, Any]:
    mounts = []
    for item in list_linked(cid):
        if item.get("enabled") is False:
            continue
        if item.get("service_type") and item.get("service_type") not in _RCLONE_COMPATIBLE_TYPES:
            continue
        service_id = item.get("service_id", "")
        if not service_id:
            continue
        try:
            sdef = _resolve_service_definition(user_id, cid, service_id, item.get("scope", ""))
            mounts.append({
                "conversation_id": cid,
                "service_id": service_id,
                "service_type": sdef.service_type,
                "scope": sdef.scope,
                "remote_name": sanitize_mount_dir(service_id),
                "mount_path": mount_path_for(service_id),
                "mode": item.get("mode", "readwrite"),
                "rclone_config": _rclone_config_for(user_id, sdef),
            })
        except Exception as exc:
            mounts.append({
                "conversation_id": cid,
                "service_id": service_id,
                "remote_name": sanitize_mount_dir(service_id),
                "mount_path": mount_path_for(service_id),
                "error": str(exc),
            })
    return {"conversation_id": cid, "mounts": mounts}


def list_tool_filesystems(user_id: str, cid: str) -> List[Dict[str, Any]]:
    """Return linked native filesystem services usable by filesystem tools."""
    result = []
    for item in list_linked(cid):
        if item.get("enabled") is False:
            continue
        service_id = item.get("service_id", "")
        if not service_id:
            continue
        try:
            sdef = _resolve_service_definition(user_id, cid, service_id, item.get("scope", ""))
        except Exception:
            continue
        if sdef.service_type not in _TOOL_COMPATIBLE_TYPES:
            continue
        result.append({
            "id": sdef.service_id,
            "type": sdef.service_type,
            "scope": sdef.scope,
            "access": "tools",
        })
    return result


def build_manifest_for_relay(relay_id: str, user_id: str) -> Dict[str, Any]:
    merged: Dict[str, Dict[str, Any]] = {}
    errors = []
    for cid in conversation_ids_for_relay(relay_id, user_id):
        manifest = build_manifest_for_conversation(user_id, cid)
        for mount in manifest.get("mounts", []):
            name = mount.get("remote_name", "")
            if not name:
                continue
            existing = merged.get(name)
            if existing and existing.get("service_id") != mount.get("service_id"):
                errors.append({"mount": name, "error": "mount name collision"})
                continue
            merged[name] = mount
    return {
        "relay_id": relay_id,
        "root": REMOTE_ROOT,
        "mounts": list(merged.values()),
        "errors": errors,
    }


def notify_linked_relays(cid: str, user_id: str) -> None:
    try:
        from core.relay_bindings import get_linked
        from core.service_registry import ServiceRegistry
        reg = ServiceRegistry.get_instance()
        for relay_id in get_linked(cid):
            svc = reg.resolve(relay_id, user_id=user_id)
            push = getattr(svc, "push_remote_fs_manifest", None)
            if callable(push):
                push(user_id=user_id)
    except Exception:
        logger.debug("Remote FS relay notification failed", exc_info=True)


def summary(user_id: str, cid: str) -> Dict[str, Any]:
    linked = []
    for item in list_linked(cid):
        service_id = item.get("service_id", "")
        is_rclone = is_rclone_service(item.get("service_type", ""))
        linked.append({
            **item,
            "access": "mounted" if is_rclone else "tools",
            "mount_path": mount_path_for(service_id) if service_id and is_rclone else "",
        })
    return {
        "linked": linked,
        "available": list_available(user_id, cid),
    }
