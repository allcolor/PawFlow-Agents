"""pfp_package split module 6 (dependency level group)."""

from __future__ import annotations
import logging
from typing import Any, Dict, Iterable, Optional
from core.pfp_package._pp_base import (  # noqa: F401
    PfpError)
from core.pfp_package._pp_mod1 import (  # noqa: F401
    _aggregate_risk, _merge_record_secret_bindings, _missing_secret_bindings, _normalize_scope, _normalize_secret_bindings, _object_secret_bindings, _read_json_file, _selected_ids)
from core.pfp_package._pp_mod2 import (  # noqa: F401
    _aggregate_capabilities, _unavailable_secret_bindings)
from core.pfp_package._pp_mod3 import (  # noqa: F401
    _install_record_path, _remove_package_content_path, _review_object_for_install)
from core.pfp_package._pp_mod4 import (  # noqa: F401
    _package_update_diff, _record_is_locally_modified, _refresh_runtime, _selected_agent_missing_skills, _version_blocking_dependents, _write_install_record, _write_package_content_store)
from core.pfp_package._pp_mod5 import (  # noqa: F401
    _install_object, _load_package, _object_plan, _remove_obsolete_update_objects, _verify_pinned_developer_key, uninstall_pfp)

logger = logging.getLogger(__name__)


def inspect_pfp(path: str, *, user_id: str = "", conversation_id: str = "",
                scope: str = "user") -> Dict[str, Any]:
    """Verify a .pfp or inspect a local .pfpdir and return an install plan."""
    package = _load_package(path)
    manifest = package["manifest"]
    objects = []
    for obj in manifest.get("objects") or []:
        objects.append(_object_plan(obj, package, user_id, conversation_id, scope))
    update_diff = _package_update_diff(
        manifest, package, objects, user_id, conversation_id, scope)
    risks = [o["risk"] for o in objects]
    capabilities = _aggregate_capabilities(objects)
    return {
        "ok": True,
        "verified": package["verified"],
        "source_type": package["source_type"],
        "package": manifest["package"],
        "version": manifest["version"],
        "description": manifest.get("description", ""),
        "developer": manifest.get("developer", {}),
        "origin": manifest.get("origin", {}),
        "sha256": package.get("sha256", ""),
        "package_size": package.get("package_size", 0),
        "content_size": package.get("content_size", 0),
        "file_count": package.get("file_count", 0),
        "risk": _aggregate_risk(risks),
        "capabilities": capabilities,
        "update_diff": update_diff,
        "objects": objects,
    }


def install_pfp(path: str, *, user_id: str, conversation_id: str = "",
                scope: str = "user", include: Optional[Iterable[str]] = None,
                exclude: Optional[Iterable[str]] = None, force: bool = False,
                replace: bool = False, dry_run: bool = False,
                secret_bindings: Optional[Dict[str, str]] = None,
                agent_name: str = "") -> Dict[str, Any]:
    """Install selected package objects and write local package provenance."""
    if not user_id:
        raise PfpError("user_id is required")
    scope = _normalize_scope(scope, conversation_id)
    package = _load_package(path, require_verified=True)
    _verify_pinned_developer_key(package, user_id, conversation_id, scope, force=force)
    plan = inspect_pfp(path, user_id=user_id, conversation_id=conversation_id, scope=scope)
    selected = _selected_ids(plan["objects"], include, exclude)
    secret_bindings = _normalize_secret_bindings(secret_bindings or {})
    installed = []
    skipped = []
    errors = []
    records = []
    should_store_content = any(
        row["id"] in selected
        and row["status"] not in {"blocked", "missing_dependency", "unsupported_runtime"}
        and (row["status"] != "conflict" or replace)
        for row in plan["objects"]
    )
    if should_store_content and not dry_run:
        package["content_dir"] = str(_write_package_content_store(
            package, user_id, conversation_id, scope))
    for row in plan["objects"]:
        obj_id = row["id"]
        if obj_id not in selected:
            skipped.append({"id": obj_id, "reason": "not_selected"})
            continue
        if row["status"] in {"blocked", "missing_dependency", "unsupported_runtime"}:
            skipped.append({"id": obj_id, "reason": row["status"]})
            continue
        if row["status"] == "conflict" and not replace:
            skipped.append({"id": obj_id, "reason": "conflict"})
            continue
        selected_missing_skills = _selected_agent_missing_skills(
            row, package, selected, user_id, conversation_id, scope)
        if selected_missing_skills:
            skipped.append({
                "id": obj_id,
                "reason": "missing_dependency",
                "missing_assigned_skills": selected_missing_skills,
            })
            continue
        missing_secret_bindings = _missing_secret_bindings(row, secret_bindings)
        if missing_secret_bindings:
            skipped.append({
                "id": obj_id,
                "reason": "missing_secret_binding",
                "missing_secrets": missing_secret_bindings,
            })
            continue
        unavailable_secret_bindings = _unavailable_secret_bindings(
            row, secret_bindings, user_id, conversation_id)
        if unavailable_secret_bindings:
            skipped.append({
                "id": obj_id,
                "reason": "unavailable_secret_binding",
                "missing_secret_keys": unavailable_secret_bindings,
            })
            continue
        try:
            if dry_run:
                installed.append({"id": obj_id, "dry_run": True})
                continue
            _review_object_for_install(
                row, package, force, user_id, conversation_id,
                operation="pfp_install")
            object_secret_bindings = _object_secret_bindings(row, secret_bindings)
            record = _install_object(
                row["object"], package, user_id, conversation_id, scope, force,
                replace, object_secret_bindings, agent_name=agent_name)
            records.append(record)
            installed.append({"id": obj_id, **record})
        except Exception as exc:
            errors.append({"id": obj_id, "error": str(exc)})
    if not dry_run and records:
        _write_install_record(package, user_id, conversation_id, scope, records)
        _refresh_runtime(scope, user_id, conversation_id, records)
    elif should_store_content and not dry_run and package.get("content_dir"):
        _remove_package_content_path(package["content_dir"], user_id, conversation_id, scope)
    return {
        "ok": not errors,
        "package": plan["package"],
        "version": plan["version"],
        "scope": scope,
        "installed": installed,
        "skipped": skipped,
        "errors": errors,
    }


def dev_load_pfp(source_dir: str, *, user_id: str, conversation_id: str = "",
                 scope: str = "conversation", include: Optional[Iterable[str]] = None,
                 exclude: Optional[Iterable[str]] = None, force: bool = True,
                 replace: bool = True, dry_run: bool = False,
                 secret_bindings: Optional[Dict[str, str]] = None,
                 agent_name: str = "") -> Dict[str, Any]:
    """Load an unsigned .pfpdir directly for local package development."""
    if not user_id:
        raise PfpError("user_id is required")
    if scope in {"", "conversation", "conv"} and not conversation_id:
        scope = "user"
    scope = _normalize_scope(scope, conversation_id)
    package = _load_package(source_dir, require_verified=False)
    if package["source_type"] != "pfpdir":
        raise PfpError("dev_load requires an unsigned .pfpdir source directory")
    package["dev"] = True
    package["content_dir"] = package["path"]
    manifest = package["manifest"]
    if replace and not dry_run:
        record_path = _install_record_path(manifest["package"], user_id, conversation_id, scope)
        if record_path.exists():
            uninstall_pfp(manifest["package"], user_id=user_id,
                          conversation_id=conversation_id, scope=scope, force=True)
    plan = inspect_pfp(source_dir, user_id=user_id, conversation_id=conversation_id, scope=scope)
    selected = _selected_ids(plan["objects"], include, exclude)
    secret_bindings = _normalize_secret_bindings(secret_bindings or {})
    installed = []
    skipped = []
    errors = []
    records = []
    for row in plan["objects"]:
        obj_id = row["id"]
        if obj_id not in selected:
            skipped.append({"id": obj_id, "reason": "not_selected"})
            continue
        if row["status"] in {"blocked", "missing_dependency", "unsupported_runtime"}:
            skipped.append({"id": obj_id, "reason": row["status"]})
            continue
        if row["status"] == "conflict" and not replace:
            skipped.append({"id": obj_id, "reason": "conflict"})
            continue
        selected_missing_skills = _selected_agent_missing_skills(
            row, package, selected, user_id, conversation_id, scope)
        if selected_missing_skills:
            skipped.append({
                "id": obj_id,
                "reason": "missing_dependency",
                "missing_assigned_skills": selected_missing_skills,
            })
            continue
        missing_secret_bindings = _missing_secret_bindings(row, secret_bindings)
        if missing_secret_bindings:
            skipped.append({
                "id": obj_id,
                "reason": "missing_secret_binding",
                "missing_secrets": missing_secret_bindings,
            })
            continue
        unavailable_secret_bindings = _unavailable_secret_bindings(
            row, secret_bindings, user_id, conversation_id)
        if unavailable_secret_bindings:
            skipped.append({
                "id": obj_id,
                "reason": "unavailable_secret_binding",
                "missing_secret_keys": unavailable_secret_bindings,
            })
            continue
        try:
            if dry_run:
                installed.append({"id": obj_id, "dry_run": True, "dev": True})
                continue
            _review_object_for_install(
                row, package, force, user_id, conversation_id,
                operation="pfp_dev_load")
            object_secret_bindings = _object_secret_bindings(row, secret_bindings)
            record = _install_object(
                row["object"], package, user_id, conversation_id, scope, force,
                replace, object_secret_bindings, agent_name=agent_name)
            record["dev"] = True
            records.append(record)
            installed.append({"id": obj_id, **record})
        except Exception as exc:
            errors.append({"id": obj_id, "error": str(exc)})
    if not dry_run and records:
        _write_install_record(package, user_id, conversation_id, scope, records)
        _refresh_runtime(scope, user_id, conversation_id, records)
    return {
        "ok": not errors,
        "dev": True,
        "package": manifest["package"],
        "version": manifest["version"],
        "scope": scope,
        "source_dir": package["path"],
        "installed": installed,
        "skipped": skipped,
        "errors": errors,
    }


def update_pfp(path: str, *, user_id: str, conversation_id: str = "",
               scope: str = "user", include: Optional[Iterable[str]] = None,
               exclude: Optional[Iterable[str]] = None, force: bool = False,
               dry_run: bool = False,
               secret_bindings: Optional[Dict[str, str]] = None,
               agent_name: str = "") -> Dict[str, Any]:
    """Update objects previously installed from the same package.

    By default only objects already recorded for this package are updated.
    New objects can be explicitly selected with include. Locally modified
    resources are skipped unless force is true.
    """
    if not user_id:
        raise PfpError("user_id is required")
    scope = _normalize_scope(scope, conversation_id)
    package = _load_package(path, require_verified=True)
    manifest = package["manifest"]
    record_path = _install_record_path(manifest["package"], user_id, conversation_id, scope)
    record = _read_json_file(record_path) if record_path.exists() else None
    if not record:
        raise PfpError(f"Package '{manifest['package']}' is not installed in scope {scope}")
    update_secret_bindings = _normalize_secret_bindings(secret_bindings or {})
    version_blockers = _version_blocking_dependents(
        manifest["package"], manifest["version"], user_id, conversation_id, scope)
    if version_blockers and not force:
        return {
            "ok": False,
            "package": manifest["package"],
            "version": manifest["version"],
            "scope": scope,
            "updated": [],
            "skipped": [],
            "errors": [],
            "reason": "dependent_version_conflict",
            "blocked_by": version_blockers,
        }

    installed_ids = {
        str(obj.get("object_id") or "")
        for obj in record.get("objects") or []
        if obj.get("object_id")
    }
    include_set = {str(x) for x in include or [] if str(x)}
    selected = include_set or installed_ids
    selected = selected.difference({str(x) for x in exclude or [] if str(x)})
    modified = []
    allowed = set()
    by_id = {
        str(obj.get("object_id") or ""): obj
        for obj in record.get("objects") or []
        if obj.get("object_id")
    }
    for obj_id in selected:
        old = by_id.get(obj_id)
        if old and _record_is_locally_modified(old, user_id, conversation_id, scope):
            if not force:
                modified.append({"id": obj_id, "reason": "local_modified"})
                continue
        allowed.add(obj_id)
    if not allowed:
        return {
            "ok": not modified,
            "package": manifest["package"],
            "version": manifest["version"],
            "scope": scope,
            "updated": [],
            "skipped": modified,
            "errors": [],
        }
    result = install_pfp(
        path,
        user_id=user_id,
        conversation_id=conversation_id,
        scope=scope,
        include=sorted(allowed),
        force=force,
        replace=True,
        dry_run=dry_run,
        agent_name=agent_name,
        secret_bindings=_merge_record_secret_bindings(
            record, allowed, update_secret_bindings),
    )
    result["updated"] = result.pop("installed", [])
    removed, removal_skips = _remove_obsolete_update_objects(
        record, manifest, user_id, conversation_id, scope, allowed, force, dry_run)
    if removed:
        result["removed"] = removed
    else:
        result.setdefault("removed", [])
    result["skipped"] = modified + result.get("skipped", [])
    if removal_skips:
        result["skipped"] = result.get("skipped", []) + removal_skips
        result["ok"] = False
    result["update"] = True
    return result
