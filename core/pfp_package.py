"""PawFlow Package (.pfp) build, verify, inspect, install, and uninstall.

PFP packages are untrusted distribution artifacts. A .pfp is a zip containing
pfp.json, pfp.lock.json, signature.ed25519, and content files. The signature
covers canonical JSON for the manifest and lock, and the lock covers every
package file hash. Installing a package always goes through an install plan;
code-bearing tools/services/tasks execute only through the relay package runtime.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import shutil
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
    load_pem_private_key,
    load_pem_public_key,
)

import yaml

import core.paths as _paths


logger = logging.getLogger(__name__)


FORMAT_VERSION = "pawflow.package.v1"
LOCK_VERSION = "pawflow.package.lock.v1"
SIGNATURE_FILE = "signature.ed25519"
MANIFEST_FILE = "pfp.json"
LOCK_FILE = "pfp.lock.json"

_SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9._/@:+-]+$")
_PACKAGE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,120}[a-z0-9]$")
_RESOURCE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,127}$")
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)
_VERSION_REF_RE = re.compile(r"^[A-Za-z0-9._+*<>=!~,^ -]{1,80}$")

_RESOURCE_TYPES = {
    "agent": "agent",
    "prompt": "prompt",
    "skill": "skill",
    "theme": "theme",
    "task": "task_def",
    "task_def": "task_def",
}
_INSTALLABLE_TYPES = set(_RESOURCE_TYPES) | {"flow", "service", "service_definition"}
_INSTALLABLE_TYPES.update({"tool", "service_provider", "flow_task", "task_provider"})
_INSTALLABLE_TYPES.add("ui_extension")

_RUNTIME_OBJECT_TYPES = {"tool", "service_provider", "flow_task", "task_provider"}
_SUPPORTED_RUNTIME_RUNNERS = {"python"}

# Slot and hook names accepted by the browser-side `ui.v1` contract.
# Adding a new slot / hook is additive; removing or renaming bumps to ui.v2
# and packages declaring `version_compat: "ui.v1"` must fail install.
_UI_API_VERSION = "ui.v1"
_UI_KNOWN_SLOTS = {
    "action_menu", "gear_menu", "resources_panel",
    "sidebar_top", "sidebar_bottom",
    "header_actions", "tab_bar",
}
_UI_KNOWN_HOOKS = {
    "boot", "shutdown",
    "conversation_changed", "conversation_created", "conversation_deleted",
    "message_appended", "message_streaming",
    "tool_call_started", "tool_call_completed",
    "command_submitted", "command_result",
    "before_send",
    "agent_changed", "theme_changed",
    "tab_switched", "permission_mode_changed",
    "sse_event",
}
_UI_ASSET_EXTENSIONS = {".js", ".css", ".json", ".html", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".woff", ".woff2"}


class PfpError(ValueError):
    """Raised for invalid, unsafe, or unsupported PFP operations."""


def build_pfp(source_dir: str, output_path: str = "", *,
              private_key: str = "", private_key_env: str = "") -> Dict[str, Any]:
    """Build and sign a .pfp from a .pfpdir source directory."""
    root = Path(source_dir).expanduser().resolve()
    if not root.is_dir():
        raise PfpError("source_dir must be a package directory")
    manifest = _read_json_file(root / MANIFEST_FILE)
    _validate_manifest(manifest)
    key_material = private_key
    if private_key_env:
        key_material = os.environ.get(private_key_env, "")
        if not key_material:
            raise PfpError(f"Environment variable '{private_key_env}' is not set")
    private = _load_private_key(key_material)
    public_text = _public_key_text(private.public_key())
    declared_key = str((manifest.get("developer") or {}).get("public_key") or "")
    if declared_key and declared_key != public_text:
        raise PfpError("developer.public_key does not match signing key")
    manifest.setdefault("developer", {})["public_key"] = public_text

    files = _collect_source_files(root)
    files[MANIFEST_FILE] = _canonical_json(manifest)
    lock = _make_lock(files)
    payload = _signature_payload(manifest, lock)
    signature = private.sign(payload)

    package = manifest["package"]
    version = manifest["version"]
    out = Path(output_path).expanduser() if output_path else root / "dist" / f"{package}-{version}.pfp"
    out = out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(MANIFEST_FILE, files[MANIFEST_FILE])
        zf.writestr(LOCK_FILE, _canonical_json(lock))
        zf.writestr(SIGNATURE_FILE, base64.b64encode(signature).decode("ascii"))
        for rel in sorted(p for p in files if p != MANIFEST_FILE):
            zf.writestr(rel, files[rel])
    return {
        "ok": True,
        "path": str(out),
        "package": package,
        "version": version,
        "sha256": _file_sha256(out),
        "files": len(files),
        "content_size": _files_size(files),
        "package_size": out.stat().st_size,
    }


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


def format_inspection_display(plan: Dict[str, Any]) -> str:
    """Render a compact human review for a PFP install plan."""
    caps = plan.get("capabilities") or {}
    objects = plan.get("objects") or []
    lines = [
        f"PFP {plan.get('package', '')}@{plan.get('version', '')}",
        f"Verified: {bool(plan.get('verified'))} | Risk: {plan.get('risk', 'low')}",
        (
            f"Size: {_format_bytes(int(plan.get('package_size') or 0))} package | "
            f"{_format_bytes(int(plan.get('content_size') or 0))} content | "
            f"{int(plan.get('file_count') or 0)} files"
        ),
    ]
    update_diff = plan.get("update_diff") or {}
    if update_diff.get("installed"):
        object_changes = [
            f"{item.get('change', '')}:{item.get('id', '')}"
            for item in update_diff.get("objects") or []
            if item.get("change") != "unchanged"
        ]
        summary = ", ".join(object_changes) if object_changes else "no object changes"
        lines.append(
            f"Update: {update_diff.get('from_version', '')} -> {update_diff.get('to_version', '')} "
            f"({update_diff.get('version_change', 'unknown')}); {summary}")
    lines.extend(["", "Objects:"])
    for row in objects:
        selected = "selected" if row.get("selected") else "not selected"
        reason = f" - {row.get('reason')}" if row.get("reason") else ""
        lines.append(
            f"- {row.get('id', '')} ({row.get('type', '')}, {row.get('status', '')}, {selected}, risk={row.get('risk', 'low')}){reason}")
    lines.extend(["", "Capabilities:"])
    _append_display_list(lines, "runtime objects", caps.get("runtime_objects") or [])
    _append_display_list(lines, "brokered tools", [item.get("ref", "") for item in caps.get("allowed_tools") or []])
    _append_display_list(lines, "brokered services", [item.get("ref", "") for item in caps.get("allowed_services") or []])
    _append_display_list(lines, "dependencies", [_format_dependency(dep) for dep in caps.get("dependencies") or []])
    _append_display_list(lines, "provided capabilities", caps.get("provides") or [])
    secret_names = [
        f"{item.get('name', '')}->{item.get('env', '')}"
        for item in caps.get("secrets") or []
    ]
    _append_display_list(lines, "secrets", secret_names)
    return "\n".join(lines)


def _append_display_list(lines: List[str], label: str, values: List[str]) -> None:
    clean = [str(value) for value in values if str(value or "")]
    if clean:
        lines.append(f"- {label}: " + ", ".join(clean))
    else:
        lines.append(f"- {label}: none")


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


def dev_unload_pfp(package_id: str, *, user_id: str, conversation_id: str = "",
                   scope: str = "conversation", force: bool = True) -> Dict[str, Any]:
    """Unload a development package loaded from .pfpdir."""
    if scope in {"", "conversation", "conv"} and not conversation_id:
        scope = "user"
    return uninstall_pfp(package_id, user_id=user_id,
                         conversation_id=conversation_id, scope=scope, force=force)


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


def uninstall_pfp(package_id: str, *, user_id: str, conversation_id: str = "",
                  scope: str = "user", force: bool = False) -> Dict[str, Any]:
    """Uninstall objects recorded for a previously installed package."""
    if not package_id:
        raise PfpError("package_id is required")
    scope = _normalize_scope(scope, conversation_id)
    record_path = _install_record_path(package_id, user_id, conversation_id, scope)
    record = _read_json_file(record_path) if record_path.exists() else None
    if not record:
        raise PfpError(f"Package '{package_id}' is not installed in scope {scope}")
    dependents = _dependent_packages(package_id, user_id, conversation_id, scope)
    if dependents and not force:
        return {
            "ok": False,
            "package": package_id,
            "removed": [],
            "kept": record.get("objects") or [],
            "blocked_by": dependents,
        }
    removed = []
    kept = []
    for obj in record.get("objects") or []:
        try:
            if _uninstall_object(obj, user_id, conversation_id, scope, force):
                removed.append(obj)
            else:
                kept.append({**obj, "reason": "not_found_or_modified"})
        except Exception as exc:
            kept.append({**obj, "reason": str(exc)})
    if removed and not kept:
        record_path.unlink(missing_ok=True)
        _remove_package_content_store(record, user_id, conversation_id, scope)
    else:
        record["objects"] = kept
        record["updated_at"] = time.time()
        _write_json_file(record_path, record)
    _refresh_runtime(scope, user_id, conversation_id, removed)
    return {"ok": not kept, "package": package_id, "removed": removed, "kept": kept}


def list_installed_packages(*, user_id: str, conversation_id: str = "",
                            scope: str = "user") -> Dict[str, Any]:
    scope = _normalize_scope(scope, conversation_id)
    root = _install_scope_dir(user_id, conversation_id, scope)
    packages = []
    if root.exists():
        for path in sorted(root.glob("*.json")):
            try:
                record = _read_json_file(path)
                package_id = str(record.get("package") or "")
                record["blocked_by"] = _dependent_packages(
                    package_id, user_id, conversation_id, scope) if package_id else []
                packages.append(record)
            except (OSError, json.JSONDecodeError, PfpError) as exc:
                logger.debug("Skipping unreadable package install record %s: %s", path, exc)
    return {"ok": True, "scope": scope, "packages": packages}


def load_installed_package_tasks(*, user_id: str, conversation_id: str = "",
                                 scope: str = "user") -> Dict[str, Any]:
    """Reload installed PFP flow task proxies into TaskFactory."""
    if not user_id:
        raise PfpError("user_id is required")
    scope = _normalize_scope(scope, conversation_id)
    root = _install_scope_dir(user_id, conversation_id, scope)
    loaded = []
    errors = []
    if not root.exists():
        return {"ok": True, "scope": scope, "loaded": loaded, "errors": errors}
    for path in sorted(root.glob("*.json")):
        try:
            record = _read_json_file(path)
        except Exception as exc:
            errors.append({"path": str(path), "error": str(exc)})
            continue
        for obj in record.get("objects") or []:
            if obj.get("kind") != "flow_task":
                continue
            try:
                _register_flow_task_proxy(obj)
                loaded.append({
                    "package": record.get("package", ""),
                    "object_id": obj.get("object_id", ""),
                    "task_type": obj.get("task_type", ""),
                })
            except Exception as exc:
                errors.append({
                    "package": record.get("package", ""),
                    "object_id": obj.get("object_id", ""),
                    "error": str(exc),
                })
    return {"ok": not errors, "scope": scope, "loaded": loaded, "errors": errors}


def load_all_installed_package_tasks() -> Dict[str, Any]:
    """Reload every installed PFP flow task proxy into TaskFactory."""
    loaded = []
    errors = []
    for path, scope, user_id, conversation_id in _iter_install_record_paths():
        try:
            record = _read_json_file(path)
        except Exception as exc:
            errors.append({"path": str(path), "error": str(exc)})
            continue
        for obj in record.get("objects") or []:
            if obj.get("kind") != "flow_task":
                continue
            try:
                _register_flow_task_proxy(obj)
                loaded.append({
                    "package": record.get("package", ""),
                    "object_id": obj.get("object_id", ""),
                    "task_type": obj.get("task_type", ""),
                    "scope": scope,
                    "user_id": user_id,
                    "conversation_id": conversation_id,
                })
            except Exception as exc:
                errors.append({
                    "path": str(path),
                    "package": record.get("package", ""),
                    "object_id": obj.get("object_id", ""),
                    "error": str(exc),
                })
    return {"ok": not errors, "loaded": loaded, "errors": errors}


def resolve_installed_flow_task_runtime(task_type: str, *, user_id: str,
                                        conversation_id: str = "",
                                        scope: str = "conversation") -> Dict[str, Any]:
    """Resolve the installed PFP flow task runtime for this execution scope."""
    task_type = str(task_type or "").strip()
    if not task_type:
        raise PfpError("task_type is required")
    if not user_id:
        raise PfpError("user_id is required")
    def _conversation_scope_ids(cid: str) -> list[str]:
        cid = str(cid or "")
        ids = [cid] if cid else []
        for marker in ("::task::", "::task_verify::", "::delegate::"):
            if cid and marker in cid:
                parent = cid.split(marker, 1)[0]
                if parent and parent not in ids:
                    ids.append(parent)
                break
        return ids

    scopes = []
    if scope in {"conversation", "conv"}:
        if not conversation_id:
            raise PfpError("conversation_id is required for conversation-scoped PFP flow tasks")
        for cid in _conversation_scope_ids(conversation_id):
            scopes.append(("conversation", cid))
    scopes.append(("user", ""))

    for candidate_scope, scope_id in scopes:
        root = _install_scope_dir(user_id, scope_id, candidate_scope)
        matches = []
        if root.exists():
            for path in sorted(root.glob("*.json")):
                record = _read_json_file(path)
                for obj in record.get("objects") or []:
                    if obj.get("kind") == "flow_task" and obj.get("task_type") == task_type:
                        matches.append(obj)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise PfpError(f"PFP flow task type is ambiguous in {candidate_scope} scope: {task_type}")
    raise PfpError(f"PFP flow task is not installed for this scope: {task_type}")


def export_pfpdir(package_id: str, version: str, include: Iterable[str], *,
                  output_dir: str, user_id: str, conversation_id: str = "") -> Dict[str, Any]:
    """Export existing ResourceStore objects into a .pfpdir source tree."""
    _validate_package_id(package_id)
    if not version:
        raise PfpError("version is required")
    out = Path(output_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    objects = []
    from core.resource_store import ResourceStore
    store = ResourceStore.instance()
    for spec in include or []:
        rtype, name = _split_object_ref(str(spec))
        if rtype == "task":
            rtype = "task_def"
        if rtype not in _RESOURCE_TYPES.values():
            raise PfpError(f"Export does not support resource type: {rtype}")
        item = store.get_any(rtype, name, user_id, conversation_id=conversation_id)
        if not item:
            raise PfpError(f"{rtype}:{name} not found")
        clean = {k: v for k, v in item.items() if not str(k).startswith("_")}
        clean.pop("created_at", None)
        clean.pop("updated_at", None)
        path = f"content/{rtype}s/{name}.json"
        target = out / path
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_json_file(target, clean)
        objects.append({
            "id": f"{rtype}:{name}",
            "type": rtype,
            "name": name,
            "path": path,
        })
    manifest = {
        "format": FORMAT_VERSION,
        "package": package_id,
        "version": version,
        "description": "",
        "developer": {"email": "", "public_key": ""},
        "origin": {},
        "objects": objects,
    }
    _write_json_file(out / MANIFEST_FILE, manifest)
    return {"ok": True, "path": str(out), "objects": objects}


def create_signing_key() -> Dict[str, str]:
    """Create an Ed25519 key pair for package development."""
    private = Ed25519PrivateKey.generate()
    private_raw = private.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    return {
        "private_key": "ed25519:" + base64.b64encode(private_raw).decode("ascii"),
        "public_key": _public_key_text(private.public_key()),
    }


def _load_package(path: str, require_verified: bool = False) -> Dict[str, Any]:
    source = Path(path).expanduser().resolve()
    if not source.exists():
        raise PfpError(f"Package path not found: {path}")
    if source.is_dir():
        manifest = _read_json_file(source / MANIFEST_FILE)
        _validate_manifest(manifest)
        files = _collect_source_files(source)
        files[MANIFEST_FILE] = _canonical_json(manifest)
        lock = _make_lock(files)
        if require_verified:
            raise PfpError("Installing requires a signed .pfp file, not an unsigned .pfpdir")
        return {
            "source_type": "pfpdir",
            "path": str(source),
            "manifest": manifest,
            "lock": lock,
            "files": files,
            "verified": False,
            "sha256": "",
            "package_size": 0,
            "content_size": _files_size(files),
            "file_count": len(files),
        }
    if source.suffix != ".pfp":
        raise PfpError("Package file must use .pfp extension")
    manifest, lock, files, signature = _read_pfp_zip(source)
    _validate_manifest(manifest)
    _verify_lock(lock, files)
    _verify_signature(manifest, lock, signature)
    return {
        "source_type": "pfp",
        "path": str(source),
        "manifest": manifest,
        "lock": lock,
        "files": files,
        "verified": True,
        "sha256": _file_sha256(source),
        "package_size": source.stat().st_size,
        "content_size": _files_size(files),
        "file_count": len(files),
    }


def _read_pfp_zip(path: Path) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, bytes], bytes]:
    files: Dict[str, bytes] = {}
    with zipfile.ZipFile(path, "r") as zf:
        names = zf.namelist()
        for name in names:
            rel = _safe_relpath(name)
            data = zf.read(name)
            files[rel] = data
    for required in (MANIFEST_FILE, LOCK_FILE, SIGNATURE_FILE):
        if required not in files:
            raise PfpError(f"Package missing {required}")
    manifest = json.loads(files.pop(MANIFEST_FILE).decode("utf-8"))
    lock = json.loads(files.pop(LOCK_FILE).decode("utf-8"))
    signature_text = files.pop(SIGNATURE_FILE).decode("ascii").strip()
    signature = _decode_key_bytes(signature_text)
    files[MANIFEST_FILE] = _canonical_json(manifest)
    return manifest, lock, files, signature


def _collect_source_files(root: Path) -> Dict[str, bytes]:
    files: Dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = _safe_relpath(path.relative_to(root).as_posix())
        if rel in {MANIFEST_FILE, LOCK_FILE, SIGNATURE_FILE} or rel.startswith("dist/"):
            continue
        data = path.read_bytes()
        files[rel] = data
    return files


def _files_size(files: Dict[str, bytes]) -> int:
    return sum(len(data) for data in files.values())


def _format_bytes(size: int) -> str:
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    value = float(max(size, 0))
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024


def _make_lock(files: Dict[str, bytes]) -> Dict[str, Any]:
    return {
        "format": LOCK_VERSION,
        "generated_at": int(time.time()),
        "files": {
            rel: "sha256:" + hashlib.sha256(data).hexdigest()
            for rel, data in sorted(files.items())
        },
    }


def _verify_lock(lock: Dict[str, Any], files: Dict[str, bytes]) -> None:
    if lock.get("format") != LOCK_VERSION:
        raise PfpError("Unsupported lock format")
    expected = lock.get("files") or {}
    actual = {
        rel: "sha256:" + hashlib.sha256(data).hexdigest()
        for rel, data in sorted(files.items())
    }
    if expected != actual:
        raise PfpError("Package file hashes do not match pfp.lock.json")


def _verify_signature(manifest: Dict[str, Any], lock: Dict[str, Any], signature: bytes) -> None:
    public_text = str((manifest.get("developer") or {}).get("public_key") or "")
    if not public_text:
        raise PfpError("developer.public_key is required")
    public = _load_public_key(public_text)
    try:
        public.verify(signature, _signature_payload(manifest, lock))
    except InvalidSignature as exc:
        raise PfpError("Package signature verification failed") from exc


def _object_plan(obj: Dict[str, Any], package: Dict[str, Any], user_id: str,
                 conversation_id: str, scope: str) -> Dict[str, Any]:
    obj = dict(obj or {})
    manifest = package["manifest"]
    obj_id = str(obj.get("id") or "").strip()
    obj_type = str(obj.get("type") or "").strip()
    name = str(obj.get("name") or _name_from_id(obj_id) or "").strip()
    if obj_type in {"flow_task", "task_provider"}:
        name = str(obj.get("task_type") or obj.get("type_name") or name).strip()
    path = str(obj.get("path") or "").strip()
    dependencies = _declared_package_dependencies(manifest, obj)
    missing_dependencies = _missing_package_dependencies(
        str(manifest.get("package") or ""), dependencies, user_id, conversation_id, scope)
    status = "new"
    risk = "low"
    installable = obj_type in _INSTALLABLE_TYPES
    reason = ""
    if not obj_id or ":" not in obj_id:
        status, reason = "blocked", "object id must be type:name"
    elif obj_type not in _INSTALLABLE_TYPES:
        status, reason, installable = "blocked", f"unsupported object type: {obj_type}", False
    elif not name or not _RESOURCE_NAME_RE.match(name):
        status, reason, installable = "blocked", "invalid object name", False
    elif path and _safe_relpath(path) not in package["files"]:
        status, reason, installable = "blocked", f"missing package file: {path}", False
    elif obj_type == "ui_extension":
        _ui_err = _validate_ui_extension_object(obj, package)
        if _ui_err:
            status, reason, installable = "blocked", _ui_err, False
    elif missing_dependencies:
        status = "missing_dependency"
        reason = "missing package dependency: " + ", ".join(
            _format_dependency(dep) for dep in missing_dependencies)
    if installable and status == "new":
        status = _existing_status(
            obj_type, _existing_status_name(obj_type, obj, package, path, name),
            user_id, conversation_id, scope)
    if obj_type in {"service", "service_definition"}:
        risk = "medium"
    if obj_type in {"tool", "service_provider", "flow_task", "task_provider"}:
        risk = "high"
    if obj.get("permissions") or obj.get("allowed_tools") or obj.get("allowed_services"):
        risk = "high"
    secrets = _declared_secret_requirements(manifest, obj)
    if secrets:
        risk = "high"
    capabilities = _object_capabilities(obj_type, obj, dependencies, secrets)
    return {
        "id": obj_id,
        "type": obj_type,
        "name": name,
        "path": path,
        "hash": _manifest_object_hash(obj, package),
        "selected": installable and status not in {"blocked", "missing_dependency", "unsupported_runtime"},
        "installable": installable,
        "status": status,
        "risk": risk,
        "reason": reason,
        "requires": obj.get("requires", []),
        "dependencies": dependencies,
        "missing_dependencies": missing_dependencies,
        "provides": obj.get("provides", []),
        "permissions": obj.get("permissions", {}),
        "allowed_tools": obj.get("allowed_tools", []),
        "allowed_services": obj.get("allowed_services", []),
        "secrets": secrets,
        "capabilities": capabilities,
        "object": obj,
    }


def _existing_status_name(obj_type: str, obj: Dict[str, Any], package: Dict[str, Any],
                          path: str, name: str) -> str:
    if obj_type == "service_provider":
        service_id = str(obj.get("service_id") or "").strip()
        if service_id:
            return service_id
        if path and path.endswith(".json"):
            try:
                metadata = _load_json_bytes(package["files"][_safe_relpath(path)])
                service_id = str(metadata.get("service_id") or "").strip()
                if service_id:
                    return service_id
            except Exception:
                pass
    return name


def _package_update_diff(manifest: Dict[str, Any], package: Dict[str, Any],
                         objects: List[Dict[str, Any]], user_id: str,
                         conversation_id: str, scope: str) -> Dict[str, Any]:
    if not user_id:
        return {"installed": False, "objects": []}
    scope = _normalize_scope(scope, conversation_id)
    record_path = _install_record_path(
        str(manifest.get("package") or ""), user_id, conversation_id, scope)
    if not record_path.exists():
        return {
            "installed": False,
            "from_version": "",
            "to_version": str(manifest.get("version") or ""),
            "version_change": "new",
            "objects": [],
        }
    try:
        record = _read_json_file(record_path)
    except Exception:
        return {"installed": False, "objects": []}
    old_by_key = {_record_key(item): item for item in record.get("objects") or []}
    diffs: List[Dict[str, Any]] = []
    for row in objects:
        key = str(row.get("id") or "")
        old = old_by_key.pop(key, None)
        new_hash = _manifest_object_hash(row.get("object") or {}, package)
        old_hash = str((old or {}).get("hash") or "")
        change = "add" if old is None else "unchanged"
        if old is not None and old_hash != new_hash:
            change = "update"
        diff = {
            "id": key,
            "type": row.get("type", ""),
            "change": change,
            "from_hash": old_hash,
            "to_hash": new_hash,
        }
        row["update_diff"] = diff
        diffs.append(diff)
    for old in old_by_key.values():
        diffs.append({
            "id": old.get("object_id", ""),
            "type": old.get("kind", ""),
            "change": "remove",
            "from_hash": old.get("hash", ""),
            "to_hash": "",
        })
    from_version = str(record.get("version") or "")
    to_version = str(manifest.get("version") or "")
    return {
        "installed": True,
        "from_version": from_version,
        "to_version": to_version,
        "version_change": _version_change_kind(from_version, to_version),
        "objects": diffs,
    }


def _manifest_object_hash(obj: Dict[str, Any], package: Dict[str, Any]) -> str:
    rel = str(obj.get("path") or "").strip()
    if not rel:
        return ""
    rel = _safe_relpath(rel)
    return str(((package.get("lock") or {}).get("files") or {}).get(rel) or "")


def _version_change_kind(from_version: str, to_version: str) -> str:
    if from_version == to_version:
        return "same"
    left = _version_tuple(from_version)
    right = _version_tuple(to_version)
    if left is None or right is None:
        return "change"
    return "upgrade" if _compare_versions(right, left) > 0 else "downgrade"


def _object_capabilities(obj_type: str, obj: Dict[str, Any],
                         dependencies: List[Dict[str, str]],
                         secrets: List[Dict[str, Any]]) -> Dict[str, Any]:
    caps = {
        "runtime": obj_type in {"tool", "service_provider", "flow_task", "task_provider"},
        "allowed_tools": _capability_refs(obj.get("allowed_tools") or [], "tool"),
        "allowed_services": _capability_refs(obj.get("allowed_services") or [], "service"),
        "dependencies": dependencies,
        "provides": [str(item) for item in obj.get("provides", [])],
        "secrets": secrets,
        "permissions": obj.get("permissions", {}) if isinstance(obj.get("permissions"), dict) else {},
    }
    if obj_type == "ui_extension":
        assets = _ui_extension_asset_list(obj)
        caps["ui_extension"] = {
            "version_compat": str(obj.get("version_compat") or ""),
            "slots": [
                {"slot": str(s.get("slot") or ""), "id": str(s.get("id") or "")}
                for s in (obj.get("slots") or []) if isinstance(s, dict)
            ],
            "hooks": [str(h) for h in (obj.get("hooks") or [])],
            "asset_count": len(assets),
        }
    return caps


def _aggregate_capabilities(objects: List[Dict[str, Any]]) -> Dict[str, Any]:
    runtime_objects = []
    allowed_tools = []
    allowed_services = []
    dependencies = []
    secrets = []
    provides = []
    permissions = []
    for row in objects:
        caps = row.get("capabilities") or {}
        if caps.get("runtime"):
            runtime_objects.append(row.get("id", ""))
        allowed_tools.extend(caps.get("allowed_tools") or [])
        allowed_services.extend(caps.get("allowed_services") or [])
        dependencies.extend(caps.get("dependencies") or [])
        secrets.extend(caps.get("secrets") or [])
        provides.extend(caps.get("provides") or [])
        if caps.get("permissions"):
            permissions.append({"object": row.get("id", ""), "permissions": caps.get("permissions")})
    return {
        "runtime_objects": [item for item in runtime_objects if item],
        "allowed_tools": _dedupe_dicts(allowed_tools, ("ref", "package", "object", "name")),
        "allowed_services": _dedupe_dicts(allowed_services, ("ref", "package", "object", "name")),
        "dependencies": _dedupe_dependencies(dependencies),
        "secrets": _dedupe_dicts(secrets, ("name", "env", "required")),
        "provides": sorted({item for item in provides if item}),
        "permissions": permissions,
    }


def _capability_refs(values: Any, expected_kind: str) -> List[Dict[str, str]]:
    if not isinstance(values, list):
        return []
    refs = []
    for item in values:
        if isinstance(item, str):
            ref = item.strip()
            if not ref:
                continue
            package = ""
            object_ref = ""
            name = ref
            if "/" in ref:
                package_ref, object_ref = ref.removeprefix("package:").split("/", 1)
                package = package_ref
                name = object_ref.split(":", 1)[1] if ":" in object_ref else object_ref
            refs.append({"kind": expected_kind, "ref": ref, "package": package, "object": object_ref, "name": name})
            continue
        if not isinstance(item, dict):
            continue
        package = str(item.get("package") or "").strip()
        object_ref = str(item.get("object") or "").strip()
        name = str(item.get("name") or "").strip()
        version = str(item.get("version") or item.get("constraint") or "").strip()
        package_ref = f"{package}@{version}" if package and version else package
        ref = f"{package_ref}/{object_ref}" if package_ref and object_ref else name
        if not ref:
            continue
        refs.append({
            "kind": expected_kind,
            "ref": ref,
            "package": package_ref,
            "object": object_ref,
            "name": name or (object_ref.split(":", 1)[1] if ":" in object_ref else object_ref),
        })
    return refs


def _declared_secret_requirements(manifest: Dict[str, Any],
                                  obj: Dict[str, Any]) -> List[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    for item in _normalize_secret_requirements(manifest.get("secrets", [])):
        merged[item["name"]] = item
    for item in _normalize_secret_requirements(obj.get("secrets", [])):
        merged[item["name"]] = item
    return list(merged.values())


def _normalize_secret_requirements(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        if isinstance(item, str):
            name = item.strip()
            env_name = _secret_env_name(name)
            required = True
        elif isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            env_name = str(item.get("env") or _secret_env_name(name)).strip()
            required = bool(item.get("required", True))
        else:
            continue
        if not name or not _RESOURCE_NAME_RE.match(name):
            continue
        result.append({"name": name, "env": env_name, "required": required})
    return result


def _secret_env_name(name: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_]", "_", str(name or "")).upper().strip("_")
    return f"PFP_SECRET_{clean or 'VALUE'}"


def _normalize_secret_bindings(bindings: Dict[str, str]) -> Dict[str, str]:
    if not isinstance(bindings, dict):
        return {}
    result = {}
    for name, key in bindings.items():
        secret_name = str(name or "").strip()
        secret_key = str(key or "").strip()
        if secret_name and secret_key:
            result[secret_name] = secret_key
    return result


def _missing_secret_bindings(row: Dict[str, Any], bindings: Dict[str, str]) -> List[str]:
    missing = []
    for item in row.get("secrets", []):
        if item.get("required", True) and not bindings.get(item.get("name")):
            missing.append(str(item.get("name") or ""))
    return [name for name in missing if name]


def _object_secret_bindings(row: Dict[str, Any], bindings: Dict[str, str]) -> Dict[str, str]:
    names = {str(item.get("name") or "") for item in row.get("secrets", [])}
    return {name: bindings[name] for name in names if name and bindings.get(name)}


def _unavailable_secret_bindings(row: Dict[str, Any], bindings: Dict[str, str],
                                 user_id: str, conversation_id: str) -> List[str]:
    missing = []
    for item in row.get("secrets", []):
        name = str(item.get("name") or "")
        bound_key = bindings.get(name)
        if bound_key and not _secret_key_exists(bound_key, user_id, conversation_id):
            missing.append(bound_key)
    return missing


def _secret_key_exists(secret_key: str, user_id: str, conversation_id: str) -> bool:
    key = str(secret_key or "").strip()
    if not key:
        return False
    if conversation_id:
        try:
            from core.conversation_store import ConversationStore
            conv_secrets = ConversationStore.instance().get_extra(
                conversation_id, "conv_secrets") or {}
            if key in conv_secrets:
                return True
        except Exception:
            pass
    if user_id:
        from core.config_store import ConfigStore
        if key in ConfigStore.load_secrets_raw(_paths.user_secrets_path(user_id)):
            return True
    from core.config_store import ConfigStore
    return key in ConfigStore.load_secrets_raw(_paths.GLOBAL_SECRETS_FILE)


def _merge_record_secret_bindings(record: Dict[str, Any], selected: set,
                                  overrides: Dict[str, str]) -> Dict[str, str]:
    merged: Dict[str, str] = {}
    for obj in record.get("objects") or []:
        if str(obj.get("object_id") or "") not in selected:
            continue
        runtime = obj.get("package_runtime") or {}
        bindings = runtime.get("secret_bindings") or {}
        if isinstance(bindings, dict):
            for name, key in bindings.items():
                if str(name or "") and str(key or ""):
                    merged[str(name)] = str(key)
    merged.update(overrides or {})
    return merged


def _install_object(obj: Dict[str, Any], package: Dict[str, Any], user_id: str,
                    conversation_id: str, scope: str, force: bool,
                    replace: bool,
                    secret_bindings: Optional[Dict[str, str]] = None,
                    agent_name: str = "") -> Dict[str, Any]:
    obj_type = obj["type"]
    obj_id = obj["id"]
    name = obj.get("name") or _name_from_id(obj_id)
    path_str = str(obj.get("path") or "")
    # ui_extension uses an `assets` list instead of a single `path`; allow
    # an empty top-level path here and let the install branch read the
    # asset list directly.
    rel = _safe_relpath(path_str) if path_str else ""
    provenance = _provenance(package, obj_id, rel)
    dependencies = _declared_package_dependencies(package["manifest"], obj)
    if obj_type == "tool":
        data = _load_tool_proxy_data(obj, package, rel, provenance, secret_bindings)
        _write_resource("tool", name, data, user_id, conversation_id, scope, replace)
        return {
            "kind": "resource",
            "object_id": obj_id,
            "resource_type": "tool",
            "name": name,
            "hash": provenance["hash"],
            "dependencies": dependencies,
            "package_runtime": data["package_runtime"],
        }
    if obj_type in _RESOURCE_TYPES:
        rtype = _RESOURCE_TYPES[obj_type]
        data = _load_resource_data(package, rel, rtype, name)
        data["installed_from"] = provenance
        if rtype == "skill" and obj.get("_review"):
            from core.review_bindings import attach_review_metadata
            data = attach_review_metadata(data, obj["_review"])
        _write_resource(rtype, name, data, user_id, conversation_id, scope, replace)
        return {
            "kind": "resource",
            "object_id": obj_id,
            "resource_type": rtype,
            "name": name,
            "hash": provenance["hash"],
            "dependencies": dependencies,
        }
    if obj_type == "flow":
        data = _load_json_bytes(package["files"][rel])
        _inject_package_flow_task_relays(
            data, package, _install_default_relay_id(conversation_id, agent_name))
        data["installed_from"] = provenance
        fqn = str(obj.get("fqn") or data.get("fqn") or name)
        _write_flow(fqn, data, user_id, conversation_id, scope, replace)
        return {
            "kind": "flow",
            "object_id": obj_id,
            "fqn": fqn,
            "name": name,
            "hash": provenance["hash"],
            "dependencies": dependencies,
        }
    if obj_type == "service_provider":
        data = _load_service_provider_proxy_data(
            obj, package, rel, provenance, name, secret_bindings)
        _write_service(data, user_id, conversation_id, scope, replace)
        return {
            "kind": "service",
            "object_id": obj_id,
            "service_id": data.get("service_id") or name,
            "hash": provenance["hash"],
            "dependencies": dependencies,
            "package_runtime": data["config"]["package_runtime"],
        }
    if obj_type in {"flow_task", "task_provider"}:
        data = _load_flow_task_proxy_data(
            obj, package, rel, provenance, name, secret_bindings)
        _register_flow_task_proxy(data)
        return {
            "kind": "flow_task",
            "object_id": obj_id,
            "task_type": data["task_type"],
            "name": data["name"],
            "version": data["version"],
            "description": data["description"],
            "icon": data["icon"],
            "parameters": data["parameters"],
            "installed_from": provenance,
            "package_runtime": data["package_runtime"],
            "dependencies": dependencies,
            "hash": provenance["hash"],
        }
    if obj_type in {"service", "service_definition"}:
        data = _load_json_bytes(package["files"][rel])
        data.setdefault("description", obj.get("description", ""))
        data["installed_from"] = provenance
        data["package_capabilities"] = {
            "dependencies": _declared_package_dependencies(package["manifest"], obj),
            "allowed_tools": obj.get("allowed_tools", []),
            "allowed_services": obj.get("allowed_services", []),
        }
        _write_service(data, user_id, conversation_id, scope, replace)
        return {
            "kind": "service",
            "object_id": obj_id,
            "service_id": data.get("service_id") or name,
            "hash": provenance["hash"],
            "dependencies": dependencies,
        }
    if obj_type == "ui_extension":
        manifest_obj = _ui_extension_manifest(obj, package)
        return {
            "kind": "ui_extension",
            "object_id": obj_id,
            "name": name,
            "version_compat": manifest_obj["version_compat"],
            "assets": manifest_obj["assets"],
            "slots": manifest_obj["slots"],
            "hooks": manifest_obj["hooks"],
            "i18n": manifest_obj["i18n"],
            "handlers": manifest_obj["handlers"],
            "allowed_tools": list(obj.get("allowed_tools") or []),
            "allowed_services": list(obj.get("allowed_services") or []),
            "installed_from": provenance,
            "dependencies": dependencies,
            "hash": provenance["hash"],
        }
    raise PfpError(f"Unsupported object type: {obj_type}")


def _load_tool_proxy_data(obj: Dict[str, Any], package: Dict[str, Any], rel: str,
                          provenance: Dict[str, Any],
                          secret_bindings: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    manifest = package["manifest"]
    metadata: Dict[str, Any] = {}
    if rel.endswith(".json"):
        metadata = _load_json_bytes(package["files"][rel])
    description = str(obj.get("description") or metadata.get("description") or "")
    parameters = obj.get("parameters") or metadata.get("parameters") or {}
    if not isinstance(parameters, dict):
        raise PfpError("tool parameters must be a JSON object")
    return {
        "source": "",
        "description": description,
        "parameters": parameters,
        "installed_from": provenance,
        "package_runtime": {
            "package": manifest["package"],
            "version": manifest["version"],
            "object_id": obj["id"],
            "entrypoint": rel,
            "content_dir": package.get("content_dir", ""),
            "runtime": str(obj.get("runtime") or metadata.get("runtime") or "python"),
            "runner": str(obj.get("runner") or metadata.get("runner") or ""),
            "dependencies": _declared_package_dependencies(manifest, obj),
            "allowed_tools": obj.get("allowed_tools", []),
            "allowed_services": obj.get("allowed_services", []),
            "secrets": _declared_secret_requirements(manifest, obj),
            "secret_bindings": dict(secret_bindings or {}),
            "dev": bool(package.get("dev")),
            "review": obj.get("_review", {}),
        },
    }


def _load_service_provider_proxy_data(obj: Dict[str, Any], package: Dict[str, Any],
                                      rel: str, provenance: Dict[str, Any],
                                      name: str,
                                      secret_bindings: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    manifest = package["manifest"]
    metadata: Dict[str, Any] = {}
    if rel.endswith(".json"):
        metadata = _load_json_bytes(package["files"][rel])
    service_id = str(obj.get("service_id") or metadata.get("service_id") or name)
    operations = obj.get("operations", metadata.get("operations", {}))
    if not isinstance(operations, (dict, list)):
        operations = {}
    package_runtime = {
        "package": manifest["package"],
        "version": manifest["version"],
        "object_id": obj["id"],
        "entrypoint": rel,
        "content_dir": package.get("content_dir", ""),
        "runtime": str(obj.get("runtime") or metadata.get("runtime") or "python"),
        "runner": str(obj.get("runner") or metadata.get("runner") or ""),
        "provides": obj.get("provides", metadata.get("provides", [])),
        "dependencies": _declared_package_dependencies(manifest, obj),
        "allowed_tools": obj.get("allowed_tools", []),
        "allowed_services": obj.get("allowed_services", []),
        "secrets": _declared_secret_requirements(manifest, obj),
        "secret_bindings": dict(secret_bindings or {}),
        "dev": bool(package.get("dev")),
        "review": obj.get("_review", {}),
    }
    return {
        "service_id": service_id,
        "service_type": "packageRuntime",
        "description": str(obj.get("description") or metadata.get("description") or ""),
        "enabled": bool(obj.get("enabled", metadata.get("enabled", True))),
        "config": {
            "package_runtime": package_runtime,
            "installed_from": provenance,
            "operations": operations,
            "package_capabilities": {
                "dependencies": package_runtime["dependencies"],
                "allowed_tools": package_runtime["allowed_tools"],
                "allowed_services": package_runtime["allowed_services"],
            },
        },
        "installed_from": provenance,
    }


def _load_flow_task_proxy_data(obj: Dict[str, Any], package: Dict[str, Any],
                               rel: str, provenance: Dict[str, Any],
                               name: str,
                               secret_bindings: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    manifest = package["manifest"]
    metadata: Dict[str, Any] = {}
    if rel.endswith(".json"):
        metadata = _load_json_bytes(package["files"][rel])
    task_type = str(obj.get("task_type") or obj.get("type_name")
                    or metadata.get("task_type") or metadata.get("type") or name)
    if not task_type or not _RESOURCE_NAME_RE.match(task_type):
        raise PfpError("flow task task_type is invalid")
    parameters = obj.get("parameters") or metadata.get("parameters") or {}
    if not isinstance(parameters, dict):
        raise PfpError("flow task parameters must be a JSON object")
    package_runtime = {
        "package": manifest["package"],
        "version": manifest["version"],
        "object_id": obj["id"],
        "entrypoint": rel,
        "content_dir": package.get("content_dir", ""),
        "runtime": str(obj.get("runtime") or metadata.get("runtime") or "python"),
        "runner": str(obj.get("runner") or metadata.get("runner") or ""),
        "dependencies": _declared_package_dependencies(manifest, obj),
        "allowed_tools": obj.get("allowed_tools", []),
        "allowed_services": obj.get("allowed_services", []),
        "secrets": _declared_secret_requirements(manifest, obj),
        "secret_bindings": dict(secret_bindings or {}),
        "dev": bool(package.get("dev")),
        "review": obj.get("_review", {}),
    }
    return {
        "task_type": task_type,
        "name": str(obj.get("display_name") or metadata.get("name") or name),
        "version": str(obj.get("version") or metadata.get("version") or manifest["version"]),
        "description": str(obj.get("description") or metadata.get("description") or ""),
        "icon": str(obj.get("icon") or metadata.get("icon") or "package"),
        "parameters": parameters,
        "installed_from": provenance,
        "package_runtime": package_runtime,
    }


def _register_flow_task_proxy(data: Dict[str, Any]) -> None:
    from core.pfp_task_runtime import register_package_task_proxy
    register_package_task_proxy(data["task_type"], data)


def _install_default_relay_id(conversation_id: str, agent_name: str = "") -> str:
    if not conversation_id:
        return ""
    from core.relay_bindings import get_default
    return str(get_default(conversation_id, agent=agent_name) or "")


def _package_flow_task_types(package: Dict[str, Any]) -> set[str]:
    manifest = package["manifest"]
    task_types = set()
    for obj in manifest.get("objects") or []:
        if str(obj.get("type") or "") not in {"flow_task", "task_provider"}:
            continue
        metadata = {}
        rel = str(obj.get("path") or "")
        if rel.endswith(".json"):
            try:
                metadata = _load_json_bytes(package["files"][_safe_relpath(rel)])
            except Exception:
                metadata = {}
        task_type = str(
            obj.get("task_type") or obj.get("type_name")
            or metadata.get("task_type") or metadata.get("type")
            or obj.get("name") or ""
        ).strip()
        if task_type:
            task_types.add(task_type)
    return task_types


def _inject_package_flow_task_relays(data: Dict[str, Any], package: Dict[str, Any], relay_id: str) -> None:
    if not relay_id:
        return
    task_types = _package_flow_task_types(package)
    if not task_types:
        return
    tasks = data.get("tasks") or {}
    if not isinstance(tasks, dict):
        return
    for task in tasks.values():
        if not isinstance(task, dict) or str(task.get("type") or "") not in task_types:
            continue
        parameters = task.setdefault("parameters", {})
        if isinstance(parameters, dict):
            parameters.setdefault("relay", relay_id)


def _load_resource_data(package: Dict[str, Any], rel: str, rtype: str, name: str) -> Dict[str, Any]:
    data = package["files"][rel]
    if rtype == "skill" and rel.endswith("SKILL.md"):
        return _parse_skill_md(data.decode("utf-8"), default_name=name)
    loaded = _load_json_bytes(data)
    if not isinstance(loaded, dict):
        raise PfpError(f"{rel} must contain a JSON object")
    loaded.pop("name", None)
    return loaded


def _ui_extension_asset_list(obj: Dict[str, Any]) -> List[Dict[str, str]]:
    """Flatten the assets object into [{kind, path}, ...]."""
    raw = obj.get("assets") if isinstance(obj.get("assets"), dict) else {}
    rows = []
    for kind in ("scripts", "styles"):
        for item in raw.get(kind) or []:
            path = str(item or "").strip()
            if path:
                rows.append({"kind": kind[:-1], "path": path})
    # i18n catalogs live next to scripts/styles under the same root.
    i18n = raw.get("i18n") if isinstance(raw.get("i18n"), dict) else {}
    for lang, path in i18n.items():
        spath = str(path or "").strip()
        if not isinstance(lang, str) or not spath:
            continue
        rows.append({"kind": "i18n", "path": spath, "lang": lang})
    return rows


_UI_HANDLER_ACTION_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")


def _validate_ui_extension_object(obj: Dict[str, Any], package: Dict[str, Any]) -> str:
    """Return an empty string when the ui_extension is structurally valid, else a reason."""
    if str(obj.get("version_compat") or "") != _UI_API_VERSION:
        return f"ui_extension requires version_compat == {_UI_API_VERSION!r}"
    assets = obj.get("assets")
    if not isinstance(assets, dict):
        return "ui_extension.assets must be an object with scripts/styles"
    rows = _ui_extension_asset_list(obj)
    if not rows:
        return "ui_extension must declare at least one script"
    files = package.get("files") or {}
    for row in rows:
        rel = _safe_relpath(row["path"])
        if rel not in files:
            return f"ui_extension asset is missing in package: {row['path']}"
        ext = Path(rel).suffix.lower()
        if ext not in _UI_ASSET_EXTENSIONS:
            return f"ui_extension asset extension is not allowed: {row['path']}"
    slots = obj.get("slots") if isinstance(obj.get("slots"), list) else []
    seen_ids = set()
    for slot in slots:
        if not isinstance(slot, dict):
            return "ui_extension.slots entries must be objects"
        slot_name = str(slot.get("slot") or "")
        slot_id = str(slot.get("id") or "")
        if slot_name not in _UI_KNOWN_SLOTS:
            return f"ui_extension.slots: unknown slot {slot_name!r}"
        if not slot_id:
            return "ui_extension.slots entries require a non-empty id"
        key = (slot_name, slot_id)
        if key in seen_ids:
            return f"ui_extension.slots: duplicate id {slot_id!r} in slot {slot_name!r}"
        seen_ids.add(key)
    hooks = obj.get("hooks") if isinstance(obj.get("hooks"), list) else []
    for hook in hooks:
        if str(hook) not in _UI_KNOWN_HOOKS:
            return f"ui_extension.hooks: unknown hook {hook!r}"
    # Server-side handlers: triggered by `pfp.call(action, body)` from the
    # browser. Each handler runs in the relay subprocess sandbox — same
    # isolation as PFP tools — with broker-authorized host calls.
    handlers = obj.get("handlers") if isinstance(obj.get("handlers"), list) else []
    seen_actions = set()
    for entry in handlers:
        if not isinstance(entry, dict):
            return "ui_extension.handlers entries must be objects"
        act = str(entry.get("action") or "").strip()
        if not act or not _UI_HANDLER_ACTION_RE.match(act):
            return f"ui_extension.handlers: invalid action {act!r}"
        if act in seen_actions:
            return f"ui_extension.handlers: duplicate action {act!r}"
        seen_actions.add(act)
        runner = str(entry.get("runner") or "")
        if runner != "python":
            return f"ui_extension.handlers: only 'python' runner is supported (got {runner!r})"
        path = str(entry.get("path") or "").strip()
        if not path:
            return f"ui_extension.handlers[{act}]: path is required"
        rel = _safe_relpath(path)
        if rel not in files:
            return f"ui_extension.handlers[{act}]: missing package file {path!r}"
        if Path(rel).suffix.lower() != ".py":
            return f"ui_extension.handlers[{act}]: handler must be a .py file"
    return ""


def _ui_extension_manifest(obj: Dict[str, Any], package: Dict[str, Any]) -> Dict[str, Any]:
    """Build the install-record manifest (assets with sha + size, slots, hooks, handlers)."""
    import hashlib
    rows = []
    for entry in _ui_extension_asset_list(obj):
        rel = _safe_relpath(entry["path"])
        data = package["files"][rel]
        digest = hashlib.sha256(data).hexdigest()
        record = {
            "kind": entry["kind"],
            "path": rel,
            "sha256": "sha256:" + digest,
            "size": len(data),
        }
        if entry.get("lang"):
            record["lang"] = entry["lang"]
        rows.append(record)
    slots = []
    for slot in (obj.get("slots") or []):
        slots.append({
            "slot": str(slot.get("slot") or ""),
            "id": str(slot.get("id") or ""),
            "icon": str(slot.get("icon") or ""),
            "label_key": str(slot.get("label_key") or ""),
        })
    hooks = [str(h) for h in (obj.get("hooks") or [])]
    i18n = {}
    for row in rows:
        if row["kind"] == "i18n" and row.get("lang"):
            i18n[row["lang"]] = row["path"]
    handlers = []
    for entry in (obj.get("handlers") or []):
        rel = _safe_relpath(str(entry.get("path") or ""))
        data = package["files"][rel]
        digest = hashlib.sha256(data).hexdigest()
        handlers.append({
            "action": str(entry.get("action") or ""),
            "path": rel,
            "sha256": "sha256:" + digest,
            "runner": "python",
            "allowed_tools": list(entry.get("allowed_tools") or []),
            "allowed_services": list(entry.get("allowed_services") or []),
            "secrets": _normalize_secret_requirements(entry.get("secrets")),
            "description": str(entry.get("description") or ""),
        })
    return {
        "version_compat": _UI_API_VERSION,
        "assets": rows,
        "slots": slots,
        "hooks": hooks,
        "i18n": i18n,
        "handlers": handlers,
    }


def list_installed_ui_extensions(*, user_id: str, conversation_id: str = "",
                                 scope: str = "user") -> List[Dict[str, Any]]:
    """Return the asset manifest for every installed ui_extension in the scope.

    Each entry has: package, version, content_dir, version_compat, assets,
    slots, hooks, i18n. Conversation scope inherits user-scope packages.
    """
    seen = set()
    out = []
    scopes = ["conversation", "user"] if scope in {"conversation", "conv"} else ["user"]
    for sc in scopes:
        try:
            root = _install_scope_dir(user_id, conversation_id, sc)
        except PfpError:
            continue
        if not root.exists():
            continue
        for path in sorted(root.glob("*.json")):
            try:
                record = _read_json_file(path)
            except (OSError, json.JSONDecodeError, PfpError):
                continue
            package_id = str(record.get("package") or "")
            if not package_id or package_id in seen:
                continue
            content_dir = str(record.get("content_dir") or "")
            if not content_dir or not Path(content_dir).is_dir():
                continue
            objects = record.get("objects") or []
            ui_objects = [obj for obj in objects if obj.get("kind") == "ui_extension"]
            if not ui_objects:
                continue
            seen.add(package_id)
            for ui in ui_objects:
                out.append({
                    "package": package_id,
                    "version": str(record.get("version") or ""),
                    "scope": sc,
                    "content_dir": content_dir,
                    "object_id": str(ui.get("object_id") or ""),
                    "version_compat": str(ui.get("version_compat") or _UI_API_VERSION),
                    "assets": list(ui.get("assets") or []),
                    "slots": list(ui.get("slots") or []),
                    "hooks": list(ui.get("hooks") or []),
                    "i18n": dict(ui.get("i18n") or {}),
                    "handlers": list(ui.get("handlers") or []),
                    "allowed_tools": list(ui.get("allowed_tools") or []),
                    "allowed_services": list(ui.get("allowed_services") or []),
                    "installed_from": dict(ui.get("installed_from") or {}),
                })
    return out


def resolve_ui_handler(package_id: str, action: str, *,
                       user_id: str, conversation_id: str = "",
                       scope: str = "user") -> Optional[Dict[str, Any]]:
    """Return runtime info for a UI handler, or None when no match exists.

    The returned dict carries the fields needed to call `pfp_runtime.invoke_ui_handler`:
    `package_runtime`, `installed_from`, plus the matching handler's secrets,
    grants, and entrypoint path. Conversation scope inherits user-scope packages.
    """
    if not package_id or not action:
        return None
    records = list_installed_ui_extensions(
        user_id=user_id, conversation_id=conversation_id, scope=scope)
    for rec in records:
        if rec.get("package") != package_id:
            continue
        for handler in rec.get("handlers") or []:
            if str(handler.get("action") or "") != action:
                continue
            installed_from = dict(rec.get("installed_from") or {})
            # Use the handler's own hash so the runtime entrypoint check
            # validates THIS file rather than the ui_extension manifest hash.
            installed_from["hash"] = str(handler.get("sha256") or installed_from.get("hash") or "")
            installed_from["file"] = str(handler.get("path") or "")
            package_runtime = {
                "package": package_id,
                "version": rec.get("version", ""),
                "object_id": rec.get("object_id", ""),
                "runtime": "python",
                "runner": "python",
                "entrypoint": str(handler.get("path") or ""),
                "hash": str(handler.get("sha256") or ""),
                "content_dir": rec.get("content_dir", ""),
                "allowed_tools": list(handler.get("allowed_tools")
                                       or rec.get("allowed_tools") or []),
                "allowed_services": list(handler.get("allowed_services")
                                          or rec.get("allowed_services") or []),
                "secrets": list(handler.get("secrets") or []),
                "secret_bindings": {},
                "provides": [],
                "dependencies": [],
            }
            return {
                "package_runtime": package_runtime,
                "installed_from": installed_from,
                "scope": rec.get("scope", scope),
                "description": str(handler.get("description") or ""),
            }
    return None


def _parse_skill_md(text: str, default_name: str = "") -> Dict[str, Any]:
    match = _FRONTMATTER_RE.match(text or "")
    if not match:
        raise PfpError("SKILL.md must contain YAML frontmatter")
    meta = yaml.safe_load(match.group(1)) or {}
    if not isinstance(meta, dict):
        raise PfpError("SKILL.md frontmatter must be a mapping")
    body = match.group(2).strip()
    if not body:
        raise PfpError("SKILL.md body is required")
    return {
        "prompt": body,
        "description": str(meta.get("description", "") or ""),
        "parameters": meta.get("parameters", {}) if isinstance(meta.get("parameters", {}), dict) else {},
        "extends": str(meta.get("extends", "") or ""),
        "template_engine": str(meta.get("template_engine", "") or ""),
        "name": str(meta.get("name") or default_name),
    }


def _review_object_for_install(row: Dict[str, Any], package: Dict[str, Any],
                               force: bool, user_id: str,
                               conversation_id: str,
                               operation: str) -> None:
    obj = row["object"]
    obj_type = str(obj.get("type") or "")
    if obj_type == "skill":
        rel = _safe_relpath(str(obj.get("path") or ""))
        data = _load_resource_data(package, rel, "skill", row.get("name", ""))
        package_files = {
            rel_path: content.decode("utf-8", errors="replace")
            for rel_path, content in package["files"].items()
            if rel_path not in {MANIFEST_FILE} and rel_path.startswith("content/")
        }
        from core.package_review import (
            assert_installable_review, review_hash, review_metadata,
            review_skill_content,
        )
        review = review_skill_content(
            data,
            operation=operation,
            user_id=user_id,
            conversation_id=conversation_id,
            package_files=package_files,
        )
        assert_installable_review(review, force=force, label="Skill")
        obj["_review"] = review_metadata(
            review,
            service_id=review.get("service_id", ""),
            llm_service=review.get("llm_service", ""),
            subject_hash=review_hash(data, package_files),
        )
        return
    if obj_type in _RUNTIME_OBJECT_TYPES:
        from core.package_review import (
            assert_installable_review, review_hash, review_metadata,
            review_package_object,
        )
        review = review_package_object(
            package,
            obj,
            operation=operation,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        assert_installable_review(review, force=force, label="Package object")
        obj["_review"] = review_metadata(
            review,
            service_id=review.get("service_id", ""),
            llm_service=review.get("llm_service", ""),
            subject_hash=review_hash(obj, package.get("lock", {}).get("files", {})),
        )


def _write_resource(rtype: str, name: str, data: Dict[str, Any], user_id: str,
                    conversation_id: str, scope: str, replace: bool) -> None:
    from core.resource_store import ResourceStore
    store = ResourceStore.instance()
    if replace and store.get(rtype, name, user_id, conversation_id if scope == "conversation" else ""):
        store.update(rtype, name, user_id, data, conversation_id if scope == "conversation" else "")
        return
    if scope == "conversation":
        store.create(rtype, name, user_id, data, conversation_id=conversation_id)
    else:
        store.create(rtype, name, user_id, data)


def _write_flow(fqn: str, data: Dict[str, Any], user_id: str, conversation_id: str,
                scope: str, replace: bool) -> None:
    from core.repository import ScopedRepository
    repo = ScopedRepository.instance()
    repo_scope = "conv" if scope == "conversation" else scope
    existing = repo.get_flow(fqn, repo_scope, user_id=user_id, conv_id=conversation_id)
    if existing and not replace:
        raise PfpError(f"Flow {fqn} already exists")
    if existing and replace:
        raise PfpError("Replacing existing flow versions is not supported; publish a new version")
    repo.create_flow(fqn, repo_scope, data, user_id=user_id, conv_id=conversation_id)


def _uninstall_flow(record: Dict[str, Any], user_id: str,
                    conversation_id: str, scope: str, force: bool) -> bool:
    from core.paths import flow_dir, flow_latest_file, flow_version_file, parse_flow_fqn
    from core.repository import ScopedRepository
    fqn = str(record.get("fqn") or "")
    if not fqn:
        return False
    repo_scope = "conv" if scope == "conversation" else scope
    current = ScopedRepository.instance().get_flow(
        fqn, repo_scope, user_id=user_id, conv_id=conversation_id)
    if not current:
        return False
    installed_from = current.get("installed_from") or {}
    if not force and installed_from.get("hash") != record.get("hash"):
        return False
    package, flowname, version = parse_flow_fqn(fqn)
    if not version:
        return False
    version_path = flow_version_file(
        package, flowname, version, repo_scope, user_id, conversation_id)
    if not version_path.exists():
        return False
    version_path.unlink()
    flow_path = flow_dir(package, flowname, repo_scope, user_id, conversation_id)
    versions_dir = flow_path / "versions"
    remaining = sorted(versions_dir.glob("*.json")) if versions_dir.exists() else []
    if remaining:
        _write_json_file(flow_latest_file(
            package, flowname, repo_scope, user_id, conversation_id), {
                "version": remaining[-1].stem,
            })
    else:
        shutil.rmtree(flow_path, ignore_errors=True)
    return True


def _write_service(data: Dict[str, Any], user_id: str, conversation_id: str,
                   scope: str, replace: bool) -> None:
    from core.service_registry import ServiceRegistry, SCOPE_CONV, SCOPE_USER
    service_id = str(data.get("service_id") or "")
    service_type = str(data.get("service_type") or data.get("type") or "")
    if not service_id or not service_type:
        raise PfpError("service_id and service_type are required")
    if service_type == "packageRuntime":
        import services.package_runtime_service  # noqa: F401
    reg_scope = SCOPE_CONV if scope == "conversation" else SCOPE_USER
    scope_id = conversation_id if scope == "conversation" else user_id
    reg = ServiceRegistry.get_instance()
    existing = reg.get_definition(reg_scope, scope_id, service_id)
    if existing and not replace:
        raise PfpError(f"Service {service_id} already exists")
    if existing:
        reg.uninstall(reg_scope, scope_id, service_id)
    config = dict(data.get("config") or {})
    config["installed_from"] = data.get("installed_from", {})
    config["package_capabilities"] = data.get("package_capabilities", {})
    if service_type == "packageRuntime":
        config["package_runtime_context"] = {
            "user_id": user_id,
            "conversation_id": conversation_id if scope == "conversation" else "",
            "scope": scope,
        }
    reg.install(
        reg_scope, scope_id, service_id, service_type,
        config=config,
        description=str(data.get("description") or ""),
        enabled=bool(data.get("enabled", True)),
    )


def _uninstall_object(record: Dict[str, Any], user_id: str, conversation_id: str,
                      scope: str, force: bool) -> bool:
    kind = record.get("kind")
    if kind == "resource":
        from core.resource_store import ResourceStore
        store = ResourceStore.instance()
        rtype = record["resource_type"]
        name = record["name"]
        conv = conversation_id if scope == "conversation" else ""
        current = store.get(rtype, name, user_id, conv)
        if current and not force:
            installed_from = current.get("installed_from") or {}
            if installed_from.get("hash") != record.get("hash"):
                return False
        return store.delete(rtype, name, user_id, conv)
    if kind == "service":
        from core.service_registry import ServiceRegistry, SCOPE_CONV, SCOPE_USER
        reg_scope = SCOPE_CONV if scope == "conversation" else SCOPE_USER
        scope_id = conversation_id if scope == "conversation" else user_id
        reg = ServiceRegistry.get_instance()
        current = reg.get_definition(reg_scope, scope_id, record["service_id"])
        if current and not force:
            installed_from = (current.config or {}).get("installed_from") or {}
            if installed_from.get("hash") != record.get("hash"):
                return False
        reg.uninstall(reg_scope, scope_id, record["service_id"])
        return True
    if kind == "flow_task":
        from core import TaskFactory
        task_type = str(record.get("task_type") or "")
        current = TaskFactory.get(task_type) if task_type in TaskFactory.list_types() else None
        current_matches = False
        if current:
            runtime = getattr(current, "PACKAGE_RUNTIME", {}) or {}
            installed_from = getattr(current, "INSTALLED_FROM", {}) or {}
            current_matches = (
                runtime.get("object_id") == record.get("object_id")
                and installed_from.get("hash") == record.get("hash")
            )
        if task_type:
            if current_matches:
                replacement = _find_replacement_flow_task_record(task_type, record)
                if replacement:
                    _register_flow_task_proxy(replacement)
                else:
                    TaskFactory._tasks.pop(task_type, None)
            return True
        return False
    if kind == "flow":
        return _uninstall_flow(record, user_id, conversation_id, scope, force)
    if kind == "ui_extension":
        # ui_extension assets live entirely in the package content store;
        # removing the install record is enough. The shared content_dir
        # is cleaned up by uninstall_pfp when no objects remain.
        return True
    return False


def _record_is_locally_modified(record: Dict[str, Any], user_id: str,
                                conversation_id: str, scope: str) -> bool:
    kind = record.get("kind")
    if kind == "resource":
        from core.resource_store import ResourceStore
        conv = conversation_id if scope == "conversation" else ""
        current = ResourceStore.instance().get(
            record.get("resource_type", ""), record.get("name", ""), user_id, conv)
        if not current:
            return False
        installed_from = current.get("installed_from") or {}
        return installed_from.get("hash") != record.get("hash")
    if kind == "service":
        from core.service_registry import ServiceRegistry, SCOPE_CONV, SCOPE_USER
        reg_scope = SCOPE_CONV if scope == "conversation" else SCOPE_USER
        scope_id = conversation_id if scope == "conversation" else user_id
        sdef = ServiceRegistry.get_instance().get_definition(
            reg_scope, scope_id, record.get("service_id", ""))
        if not sdef:
            return False
        installed_from = (sdef.config or {}).get("installed_from") or {}
        return installed_from.get("hash") != record.get("hash")
    if kind == "flow_task":
        task_type = str(record.get("task_type") or "")
        if not task_type:
            return False
        try:
            current = resolve_installed_flow_task_runtime(
                task_type, user_id=user_id,
                conversation_id=conversation_id, scope=scope)
        except Exception:
            return False
        runtime = current.get("package_runtime") or {}
        installed_from = current.get("installed_from") or {}
        return (runtime.get("object_id") != record.get("object_id")
                or installed_from.get("hash") != record.get("hash"))
    return False


def _find_replacement_flow_task_record(task_type: str,
                                       removed: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    removed_object_id = str(removed.get("object_id") or "")
    removed_package = str((removed.get("installed_from") or {}).get("package") or "")
    for path, _scope, _user_id, _conversation_id in _iter_install_record_paths():
        try:
            record = _read_json_file(path)
        except Exception:
            continue
        for obj in record.get("objects") or []:
            if obj.get("kind") != "flow_task" or obj.get("task_type") != task_type:
                continue
            obj_package = str((obj.get("installed_from") or {}).get("package") or "")
            if (str(obj.get("object_id") or "") == removed_object_id
                    and obj_package == removed_package):
                continue
            return obj
    return None


def _write_install_record(package: Dict[str, Any], user_id: str, conversation_id: str,
                          scope: str, records: List[Dict[str, Any]]) -> None:
    manifest = package["manifest"]
    path = _install_record_path(manifest["package"], user_id, conversation_id, scope)
    existing = _read_json_file(path) if path.exists() else {}
    prior = existing.get("objects") or []
    merged_by_key = {
        _record_key(item): item
        for item in prior
    }
    for item in records:
        merged_by_key[_record_key(item)] = item
    merged = list(merged_by_key.values())
    _write_json_file(path, {
        "package": manifest["package"],
        "version": manifest["version"],
        "developer": manifest.get("developer", {}),
        "origin": manifest.get("origin", {}),
        "scope": scope,
        "installed_at": existing.get("installed_at") or time.time(),
        "updated_at": time.time(),
        "package_sha256": package.get("sha256", ""),
        "content_dir": package.get("content_dir", ""),
        "source_dir": package.get("path", "") if package.get("dev") else "",
        "source_type": package.get("source_type", ""),
        "verified": bool(package.get("verified")),
        "dev": bool(package.get("dev")),
        "objects": merged,
    })


def _remove_obsolete_update_objects(record: Dict[str, Any], manifest: Dict[str, Any],
                                    user_id: str, conversation_id: str, scope: str,
                                    selected: set, force: bool,
                                    dry_run: bool) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    current_ids = {
        str(obj.get("id") or "")
        for obj in manifest.get("objects") or []
        if str(obj.get("id") or "")
    }
    selected_ids = {str(item) for item in selected or set() if str(item)}
    removed = []
    skipped = []
    removed_ids = set()
    for obj in record.get("objects") or []:
        obj_id = str(obj.get("object_id") or "")
        if not obj_id or obj_id in current_ids or obj_id not in selected_ids:
            continue
        if _record_is_locally_modified(obj, user_id, conversation_id, scope) and not force:
            skipped.append({"id": obj_id, "reason": "local_modified"})
            continue
        if dry_run:
            removed.append({"id": obj_id, **obj})
            removed_ids.add(obj_id)
            continue
        try:
            if _uninstall_object(obj, user_id, conversation_id, scope, force):
                removed.append({"id": obj_id, **obj})
                removed_ids.add(obj_id)
            else:
                skipped.append({"id": obj_id, "reason": "not_found_or_modified"})
        except Exception as exc:
            skipped.append({"id": obj_id, "reason": str(exc)})
    if removed_ids and not dry_run:
        _drop_install_record_objects(manifest, user_id, conversation_id, scope, removed_ids)
    return removed, skipped


def _drop_install_record_objects(manifest: Dict[str, Any], user_id: str,
                                 conversation_id: str, scope: str,
                                 object_ids: set) -> None:
    path = _install_record_path(str(manifest.get("package") or ""), user_id, conversation_id, scope)
    if not path.exists():
        return
    record = _read_json_file(path)
    kept = [
        obj for obj in record.get("objects") or []
        if str(obj.get("object_id") or "") not in object_ids
    ]
    if kept:
        record["objects"] = kept
        record["version"] = str(manifest.get("version") or record.get("version") or "")
        record["updated_at"] = time.time()
        _write_json_file(path, record)
    else:
        path.unlink(missing_ok=True)
        _remove_package_content_store(record, user_id, conversation_id, scope)


def _dependent_packages(package_id: str, user_id: str, conversation_id: str,
                        scope: str) -> List[Dict[str, str]]:
    dependents: Dict[str, Dict[str, str]] = {}
    for root, record_scope, record_conversation_id in _dependent_record_roots(
            user_id, conversation_id, scope):
        if not root.exists():
            continue
        for path in sorted(root.glob("*.json")):
            try:
                record = _read_json_file(path)
            except Exception:
                continue
            dependent_package = str(record.get("package") or "")
            if not dependent_package or dependent_package == package_id:
                continue
            for obj in record.get("objects") or []:
                if _record_depends_on_package(obj, package_id):
                    key = f"{record_scope}:{record_conversation_id}:{dependent_package}"
                    dependent = {
                        "package": dependent_package,
                        "version": str(record.get("version") or ""),
                    }
                    if record_scope != scope:
                        dependent["scope"] = record_scope
                        if record_conversation_id:
                            dependent["conversation_id"] = record_conversation_id
                    dependents[key] = dependent
                    break
    return list(dependents.values())


def _version_blocking_dependents(package_id: str, new_version: str, user_id: str,
                                 conversation_id: str, scope: str) -> List[Dict[str, str]]:
    blockers: Dict[Tuple[str, str, str, str], Dict[str, str]] = {}
    for root, record_scope, record_conversation_id in _dependent_record_roots(
            user_id, conversation_id, scope):
        if not root.exists():
            continue
        for path in sorted(root.glob("*.json")):
            try:
                record = _read_json_file(path)
            except Exception:
                continue
            dependent_package = str(record.get("package") or "")
            if not dependent_package or dependent_package == package_id:
                continue
            for obj in record.get("objects") or []:
                for dep in _record_dependencies(obj):
                    required_version = str(dep.get("version") or "") if isinstance(dep, dict) else ""
                    if (isinstance(dep, dict)
                            and str(dep.get("package") or "") == package_id
                            and required_version
                            and not _version_satisfies(new_version, required_version)):
                        key = (record_scope, record_conversation_id, dependent_package, required_version)
                        blocker = {
                            "package": dependent_package,
                            "version": str(record.get("version") or ""),
                            "required_version": required_version,
                        }
                        if dep.get("object"):
                            blocker["object"] = str(dep.get("object") or "")
                        if record_scope != scope:
                            blocker["scope"] = record_scope
                            if record_conversation_id:
                                blocker["conversation_id"] = record_conversation_id
                        blockers[key] = blocker
    return list(blockers.values())


def _dependent_record_roots(user_id: str, conversation_id: str,
                            scope: str) -> List[Tuple[Path, str, str]]:
    roots = [(_install_scope_dir(user_id, conversation_id, scope), scope, conversation_id)]
    if scope != "user":
        return roots
    conversations_root = _paths.REPOSITORY_DIR / "packages" / "conversations" / _safe_component(user_id)
    if conversations_root.exists():
        for conv_dir in sorted(path for path in conversations_root.iterdir() if path.is_dir()):
            roots.append((conv_dir, "conversation", conv_dir.name))
    return roots


def _record_depends_on_package(record: Dict[str, Any], package_id: str) -> bool:
    for dep in _record_dependencies(record):
        if isinstance(dep, dict) and str(dep.get("package") or "") == package_id:
            return True
    return False


def _record_dependencies(record: Dict[str, Any]) -> List[Any]:
    runtime = record.get("package_runtime") or {}
    dependencies: List[Any] = []
    dependencies.extend(record.get("dependencies") or [])
    dependencies.extend(runtime.get("dependencies") or [])
    return dependencies


def _write_package_content_store(package: Dict[str, Any], user_id: str,
                                 conversation_id: str, scope: str) -> Path:
    manifest = package["manifest"]
    target = _package_content_dir(
        manifest["package"], manifest["version"], user_id, conversation_id, scope)
    tmp = target.with_name(target.name + ".tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    _write_bytes_file(tmp / MANIFEST_FILE, _canonical_json(manifest))
    _write_bytes_file(tmp / LOCK_FILE, _canonical_json(package["lock"]))
    for rel, content in sorted(package["files"].items()):
        if rel == MANIFEST_FILE:
            continue
        _write_bytes_file(tmp / rel, content)
    if target.exists():
        shutil.rmtree(target)
    os.replace(tmp, target)
    return target


def _remove_package_content_store(record: Dict[str, Any], user_id: str,
                                  conversation_id: str, scope: str) -> None:
    content_dir = str(record.get("content_dir") or "")
    if not content_dir:
        content_dir = str(_package_content_dir(
            str(record.get("package") or ""), str(record.get("version") or ""),
            user_id, conversation_id, scope))
    _remove_package_content_path(content_dir, user_id, conversation_id, scope)


def _remove_package_content_path(content_dir: str, user_id: str,
                                 conversation_id: str, scope: str) -> None:
    root = _package_content_root(user_id, conversation_id, scope).resolve()
    target = Path(content_dir).expanduser().resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return
    if target.exists():
        shutil.rmtree(target)


def _write_bytes_file(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(content)
    os.replace(tmp, path)


def _record_key(item: Dict[str, Any]) -> str:
    if item.get("object_id"):
        return str(item.get("object_id"))
    if item.get("kind") == "resource":
        return f"resource:{item.get('resource_type')}:{item.get('name')}"
    if item.get("kind") == "service":
        return f"service:{item.get('service_id')}"
    if item.get("kind") == "flow":
        return f"flow:{item.get('fqn') or item.get('name')}"
    if item.get("kind") == "flow_task":
        return f"flow_task:{item.get('task_type')}"
    return json.dumps(item, sort_keys=True)


def _refresh_runtime(scope: str, user_id: str, conversation_id: str,
                     records: List[Dict[str, Any]]) -> None:
    if not records:
        return
    try:
        from core.resource_store import ResourceStore
        ResourceStore.instance().reload()
    except Exception as exc:
        logger.debug("PFP resource refresh failed: %s", exc)
    try:
        from core.service_registry import ServiceRegistry, SCOPE_CONV, SCOPE_USER
        reg_scope = SCOPE_CONV if scope == "conversation" else SCOPE_USER
        scope_id = conversation_id if scope == "conversation" else user_id
        ServiceRegistry.get_instance().reload_scope(reg_scope, scope_id)
    except Exception as exc:
        logger.debug("PFP service refresh failed: %s", exc)
    try:
        load_installed_package_tasks(
            user_id=user_id, conversation_id=conversation_id, scope=scope)
    except Exception as exc:
        logger.debug("PFP flow task refresh failed: %s", exc)


def _existing_status(obj_type: str, name: str, user_id: str,
                     conversation_id: str, scope: str) -> str:
    try:
        if obj_type in _RESOURCE_TYPES:
            from core.resource_store import ResourceStore
            rtype = _RESOURCE_TYPES[obj_type]
            conv = conversation_id if scope == "conversation" else ""
            return "conflict" if ResourceStore.instance().get(rtype, name, user_id, conv) else "new"
        if obj_type == "tool":
            from core.resource_store import ResourceStore
            conv = conversation_id if scope == "conversation" else ""
            return "conflict" if ResourceStore.instance().get("tool", name, user_id, conv) else "new"
        if obj_type == "flow":
            from core.repository import ScopedRepository
            repo_scope = "conv" if scope == "conversation" else scope
            return "conflict" if ScopedRepository.instance().get_flow(name, repo_scope, user_id=user_id, conv_id=conversation_id) else "new"
        if obj_type in {"flow_task", "task_provider"}:
            from core import TaskFactory
            task_type = name
            current = TaskFactory.get(task_type) if task_type in TaskFactory.list_types() else None
            if current and not getattr(current, "PACKAGE_RUNTIME", None):
                return "conflict"
            try:
                resolve_installed_flow_task_runtime(
                    task_type, user_id=user_id,
                    conversation_id=conversation_id, scope=scope)
                return "conflict"
            except Exception:
                return "new"
        if obj_type in {"service", "service_definition"}:
            from core.service_registry import ServiceRegistry, SCOPE_CONV, SCOPE_USER
            reg_scope = SCOPE_CONV if scope == "conversation" else SCOPE_USER
            scope_id = conversation_id if scope == "conversation" else user_id
            return "conflict" if ServiceRegistry.get_instance().get_definition(reg_scope, scope_id, name) else "new"
        if obj_type == "service_provider":
            from core.service_registry import ServiceRegistry, SCOPE_CONV, SCOPE_USER
            reg_scope = SCOPE_CONV if scope == "conversation" else SCOPE_USER
            scope_id = conversation_id if scope == "conversation" else user_id
            return "conflict" if ServiceRegistry.get_instance().get_definition(reg_scope, scope_id, name) else "new"
    except Exception:
        return "unknown"
    return "new"


def _selected_ids(objects: List[Dict[str, Any]], include: Optional[Iterable[str]],
                  exclude: Optional[Iterable[str]]) -> set:
    include_set = {str(x) for x in include or [] if str(x)}
    exclude_set = {str(x) for x in exclude or [] if str(x)}
    if include_set:
        ids = {o["id"] for o in objects if o.get("id") in include_set}
    else:
        ids = {o["id"] for o in objects if o.get("selected")}
    return ids.difference(exclude_set)


def _validate_manifest(manifest: Dict[str, Any]) -> None:
    if not isinstance(manifest, dict):
        raise PfpError("pfp.json must be an object")
    if manifest.get("format") != FORMAT_VERSION:
        raise PfpError("Unsupported PFP format")
    _validate_package_id(str(manifest.get("package") or ""))
    if not str(manifest.get("version") or ""):
        raise PfpError("version is required")
    _validate_dependency_list(manifest.get("dependencies") or [], "dependencies")
    objects = manifest.get("objects")
    if not isinstance(objects, list):
        raise PfpError("objects must be a list")
    for obj in objects:
        if not isinstance(obj, dict):
            raise PfpError("objects must contain JSON objects")
        _validate_runtime_object(obj)
        _validate_dependency_list(obj.get("requires") or [], "object requires")
        _validate_allowed_refs(obj.get("allowed_tools") or [], "allowed_tools")
        _validate_allowed_refs(obj.get("allowed_services") or [], "allowed_services")


def _validate_runtime_object(obj: Dict[str, Any]) -> None:
    obj_type = str(obj.get("type") or "").strip()
    if obj_type not in _RUNTIME_OBJECT_TYPES:
        return
    if not str(obj.get("path") or "").strip():
        raise PfpError(f"{obj_type} runtime objects require path")
    runtime = str(obj.get("runtime") or "python").strip()
    if runtime != "python":
        raise PfpError(f"Unsupported PFP runtime: {runtime}")
    runner = str(obj.get("runner") or "").strip()
    if runner not in _SUPPORTED_RUNTIME_RUNNERS:
        raise PfpError(f"Unsupported PFP runtime runner: {runner}")


def _validate_dependency_list(values: Any, field: str) -> None:
    if not isinstance(values, list):
        raise PfpError(f"{field} must be a list")
    for item in values:
        _dependency_package(item, strict=(field == "dependencies"))


def _validate_allowed_refs(values: Any, field: str) -> None:
    if not isinstance(values, list):
        raise PfpError(f"{field} must be a list")
    for item in values:
        _allowed_package(item)


def _declared_package_dependencies(manifest: Dict[str, Any], obj: Dict[str, Any]) -> List[Dict[str, str]]:
    deps: List[Dict[str, str]] = []
    for item in manifest.get("dependencies") or []:
        dep = _dependency_package(item, strict=True)
        if dep:
            deps.append(dep)
    for item in obj.get("requires") or []:
        dep = _dependency_package(item, strict=False)
        if dep:
            deps.append(dep)
    for item in obj.get("allowed_tools") or []:
        dep = _allowed_package(item)
        if dep:
            deps.append(dep)
    for item in obj.get("allowed_services") or []:
        dep = _allowed_package(item)
        if dep:
            deps.append(dep)
    return _dedupe_dependencies(deps)


def _dependency_package(item: Any, *, strict: bool) -> Dict[str, str]:
    if isinstance(item, str):
        value = item.strip()
        if not value:
            raise PfpError("empty package dependency")
        if value.startswith("package:"):
            return _parse_package_version(value.split(":", 1)[1])
        if strict:
            return _parse_package_version(value)
        if _looks_like_package_ref(value):
            return _parse_package_version(value)
        return {}
    if isinstance(item, dict):
        dep_type = str(item.get("type") or "").strip()
        package_id = str(item.get("package") or "").strip()
        if not package_id:
            if strict or dep_type == "package":
                raise PfpError("package dependency requires package")
            return {}
        dep = _parse_package_version(package_id)
        version = str(item.get("version") or item.get("constraint") or dep.get("version") or "").strip()
        if version:
            _validate_version_ref(version)
            dep["version"] = version
        object_ref = str(item.get("object") or "").strip()
        if object_ref and ":" not in object_ref:
            raise PfpError("package dependency object must be type:name")
        if object_ref:
            dep["object"] = object_ref
        return dep
    raise PfpError("package dependency entries must be strings or objects")


def _allowed_package(item: Any) -> Dict[str, str]:
    if isinstance(item, str):
        value = item.strip()
        if not value:
            raise PfpError("empty allowed capability")
        if value.startswith("package:"):
            package_ref, object_ref = _split_package_object_ref(value.split(":", 1)[1])
            dep = _parse_package_version(package_ref)
            if object_ref:
                dep["object"] = object_ref
            return dep
        if "/" in value:
            package_ref, object_ref = value.split("/", 1)
            if _PACKAGE_ID_RE.match(package_ref) and ":" in object_ref:
                dep = _parse_package_version(package_ref)
                dep["object"] = object_ref
                return dep
        return {}
    if isinstance(item, dict):
        package_id = str(item.get("package") or "").strip()
        if not package_id:
            return {}
        dep = _parse_package_version(package_id)
        version = str(item.get("version") or item.get("constraint") or dep.get("version") or "").strip()
        if version:
            _validate_version_ref(version)
            dep["version"] = version
        object_ref = str(item.get("object") or "").strip()
        if object_ref and ":" not in object_ref:
            raise PfpError("package allowed capability object must be type:name")
        if object_ref:
            dep["object"] = object_ref
        return dep
    raise PfpError("allowed capability entries must be strings or objects")


def _parse_package_version(value: str) -> Dict[str, str]:
    raw = str(value or "").strip()
    if not raw:
        raise PfpError("package dependency is required")
    if "@" in raw:
        package_id, version = raw.split("@", 1)
    else:
        package_id, version = raw, ""
    _validate_package_id(package_id)
    if version:
        _validate_version_ref(version)
    return {"package": package_id, "version": version}


def _split_package_object_ref(value: str) -> Tuple[str, str]:
    raw = str(value or "").strip()
    if "/" not in raw:
        return raw, ""
    package_ref, object_ref = raw.split("/", 1)
    if object_ref and ":" not in object_ref:
        raise PfpError("package allowed capability object must be type:name")
    return package_ref, object_ref


def _looks_like_package_ref(value: str) -> bool:
    package_id = value.split("@", 1)[0]
    return bool(_PACKAGE_ID_RE.match(package_id))


def _validate_version_ref(value: str) -> None:
    if not _VERSION_REF_RE.match(value):
        raise PfpError("invalid package dependency version")


def _version_satisfies(version: str, constraint: str) -> bool:
    constraint = str(constraint or "").strip()
    if not constraint or constraint == "*":
        return True
    version_tuple = _version_tuple(version)
    if version_tuple is None:
        return False
    for part in [item.strip() for item in constraint.split(",") if item.strip()]:
        if not _version_part_satisfies(version_tuple, part):
            return False
    return True


def _version_part_satisfies(version_tuple: Tuple[int, ...], constraint: str) -> bool:
    if constraint.startswith("^"):
        base = _version_tuple(constraint[1:])
        if base is None:
            return False
        return _compare_versions(version_tuple, base) >= 0 and _compare_versions(version_tuple, _caret_upper_bound(base)) < 0
    if constraint.startswith("~"):
        base = _version_tuple(constraint[1:])
        if base is None:
            return False
        return _compare_versions(version_tuple, base) >= 0 and _compare_versions(version_tuple, _tilde_upper_bound(base)) < 0
    for op in (">=", "<=", "==", "!=", ">", "<"):
        if constraint.startswith(op):
            target = _version_tuple(constraint[len(op):])
            if target is None:
                return False
            cmp = _compare_versions(version_tuple, target)
            return {
                ">=": cmp >= 0,
                "<=": cmp <= 0,
                "==": cmp == 0,
                "!=": cmp != 0,
                ">": cmp > 0,
                "<": cmp < 0,
            }[op]
    target = _version_tuple(constraint)
    return target is not None and _compare_versions(version_tuple, target) == 0


def _version_tuple(value: str) -> Optional[Tuple[int, ...]]:
    raw = str(value or "").strip().removeprefix("v")
    if not raw:
        return None
    raw = re.split(r"[-+]", raw, 1)[0]
    parts = raw.split(".")
    if not all(part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def _compare_versions(left: Tuple[int, ...], right: Tuple[int, ...]) -> int:
    width = max(len(left), len(right), 3)
    a = left + (0,) * (width - len(left))
    b = right + (0,) * (width - len(right))
    return (a > b) - (a < b)


def _caret_upper_bound(base: Tuple[int, ...]) -> Tuple[int, ...]:
    major = base[0] if len(base) > 0 else 0
    minor = base[1] if len(base) > 1 else 0
    patch = base[2] if len(base) > 2 else 0
    if major > 0:
        return (major + 1, 0, 0)
    if minor > 0:
        return (0, minor + 1, 0)
    return (0, 0, patch + 1)


def _tilde_upper_bound(base: Tuple[int, ...]) -> Tuple[int, ...]:
    major = base[0] if len(base) > 0 else 0
    minor = base[1] if len(base) > 1 else 0
    if len(base) == 1:
        return (major + 1, 0, 0)
    return (major, minor + 1, 0)


def _dedupe_dependencies(deps: List[Dict[str, str]]) -> List[Dict[str, str]]:
    seen = set()
    result = []
    for dep in deps:
        key = (dep.get("package", ""), dep.get("version", ""), dep.get("object", ""))
        if key not in seen:
            seen.add(key)
            result.append(dep)
    return result


def _dedupe_dicts(items: List[Dict[str, Any]], keys: Tuple[str, ...]) -> List[Dict[str, Any]]:
    seen = set()
    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        key = tuple(str(item.get(name, "")) for name in keys)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _missing_package_dependencies(package_id: str, dependencies: List[Dict[str, str]],
                                  user_id: str, conversation_id: str,
                                  scope: str) -> List[Dict[str, str]]:
    installed = _installed_package_records(user_id, conversation_id, scope)
    missing = []
    for dep in dependencies:
        dep_package = dep.get("package", "")
        dep_version = dep.get("version", "")
        dep_object = dep.get("object", "")
        if not dep_package or dep_package == package_id:
            continue
        installed_record = installed.get(dep_package, {})
        installed_version = str(installed_record.get("version") or "")
        if not installed_version or not _version_satisfies(installed_version, dep_version):
            missing.append(dep)
            continue
        installed_objects = set(installed_record.get("objects") or [])
        accepted_objects = {dep_object}
        if dep_object.startswith("service:"):
            accepted_objects.add("service_provider:" + dep_object.split(":", 1)[1])
        if dep_object and not accepted_objects.intersection(installed_objects):
            missing.append(dep)
            continue
        if dep_object and dep_version:
            object_versions = installed_record.get("object_versions") or {}
            matching_versions = [
                str(object_versions.get(obj_id) or "")
                for obj_id in accepted_objects
                if obj_id in installed_objects
            ]
            if matching_versions and not any(
                    _version_satisfies(version, dep_version)
                    for version in matching_versions):
                missing.append(dep)
    return missing


def _installed_package_versions(user_id: str, conversation_id: str, scope: str) -> Dict[str, str]:
    return {
        package_id: str(record.get("version") or "")
        for package_id, record in _installed_package_records(user_id, conversation_id, scope).items()
    }


def _installed_package_records(user_id: str, conversation_id: str, scope: str) -> Dict[str, Dict[str, Any]]:
    roots = [_install_scope_dir(user_id, "", "user")]
    if scope == "conversation":
        conv_ids = []
        for marker in ("::task::", "::task_verify::", "::delegate::"):
            if conversation_id and marker in conversation_id:
                parent = conversation_id.split(marker, 1)[0]
                if parent:
                    conv_ids.append(parent)
                break
        if conversation_id:
            conv_ids.append(conversation_id)
        for conv_id in conv_ids:
            roots.append(_install_scope_dir(user_id, conv_id, "conversation"))
    packages: Dict[str, Dict[str, Any]] = {}
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.glob("*.json")):
            try:
                record = _read_json_file(path)
            except (OSError, json.JSONDecodeError, PfpError):
                continue
            package_id = str(record.get("package") or "")
            version = str(record.get("version") or "")
            if package_id and version:
                objects = [
                    str(obj.get("object_id") or "")
                    for obj in record.get("objects") or []
                    if obj.get("object_id")
                ]
                object_versions = {}
                for obj in record.get("objects") or []:
                    object_id = str(obj.get("object_id") or "")
                    if not object_id:
                        continue
                    runtime = obj.get("package_runtime") or {}
                    object_versions[object_id] = str(runtime.get("version") or version)
                packages[package_id] = {
                    "version": version,
                    "objects": objects,
                    "object_versions": object_versions,
                }
    return packages


def _iter_install_record_paths() -> Iterable[Tuple[Path, str, str, str]]:
    root = _paths.REPOSITORY_DIR / "packages"
    users_root = root / "users"
    if users_root.exists():
        for user_dir in sorted(path for path in users_root.iterdir() if path.is_dir()):
            for record_path in sorted(user_dir.glob("*.json")):
                yield record_path, "user", user_dir.name, ""
    conversations_root = root / "conversations"
    if conversations_root.exists():
        for user_dir in sorted(path for path in conversations_root.iterdir() if path.is_dir()):
            for conv_dir in sorted(path for path in user_dir.iterdir() if path.is_dir()):
                for record_path in sorted(conv_dir.glob("*.json")):
                    yield record_path, "conversation", user_dir.name, conv_dir.name


def _format_dependency(dep: Dict[str, str]) -> str:
    version = dep.get("version", "")
    package_ref = f"{dep.get('package', '')}@{version}" if version else dep.get("package", "")
    return f"{package_ref}/{dep.get('object', '')}" if dep.get("object") else package_ref


def _validate_package_id(package_id: str) -> None:
    if not package_id or not _PACKAGE_ID_RE.match(package_id):
        raise PfpError("package must be a lowercase dotted id")


def _safe_relpath(path: str) -> str:
    rel = str(path or "").replace("\\", "/").strip("/")
    if not rel or rel.startswith("/") or ".." in rel.split("/"):
        raise PfpError(f"Unsafe package path: {path}")
    if not _SAFE_PATH_RE.match(rel):
        raise PfpError(f"Unsafe package path characters: {path}")
    return rel


def _split_object_ref(spec: str) -> Tuple[str, str]:
    if ":" not in spec:
        raise PfpError("Object refs must be type:name")
    left, right = spec.split(":", 1)
    return left.strip(), right.strip()


def _name_from_id(obj_id: str) -> str:
    return obj_id.split(":", 1)[1] if ":" in obj_id else ""


def _normalize_scope(scope: str, conversation_id: str) -> str:
    value = (scope or "user").strip().lower()
    if value in {"conv", "conversation"}:
        if not conversation_id:
            raise PfpError("conversation scope requires conversation_id")
        return "conversation"
    if value == "user":
        return "user"
    raise PfpError("scope must be user or conversation")


def _provenance(package: Dict[str, Any], obj_id: str, rel: str) -> Dict[str, Any]:
    manifest = package["manifest"]
    return {
        "package": manifest["package"],
        "version": manifest["version"],
        "object_id": obj_id,
        "file": rel,
        "hash": package["lock"]["files"].get(rel, ""),
        "package_sha256": package.get("sha256", ""),
        "content_dir": package.get("content_dir", ""),
        "source_dir": package.get("path", "") if package.get("dev") else "",
        "source_type": package.get("source_type", ""),
        "verified": bool(package.get("verified")),
        "dev": bool(package.get("dev")),
        "developer_key": (manifest.get("developer") or {}).get("public_key", ""),
    }


def _install_scope_dir(user_id: str, conversation_id: str, scope: str) -> Path:
    root = _paths.REPOSITORY_DIR / "packages"
    if scope == "conversation":
        return root / "conversations" / _safe_component(user_id) / _safe_component(conversation_id)
    return root / "users" / _safe_component(user_id)


def _package_content_root(user_id: str, conversation_id: str, scope: str) -> Path:
    root = _paths.REPOSITORY_DIR / "packages" / "content"
    if scope == "conversation":
        return root / "conversations" / _safe_component(user_id) / _safe_component(conversation_id)
    return root / "users" / _safe_component(user_id)


def _package_content_dir(package_id: str, version: str, user_id: str,
                         conversation_id: str, scope: str) -> Path:
    return (_package_content_root(user_id, conversation_id, scope)
            / _safe_component(package_id) / _safe_component(version))


def _install_record_path(package_id: str, user_id: str, conversation_id: str,
                         scope: str) -> Path:
    return _install_scope_dir(user_id, conversation_id, scope) / f"{_safe_component(package_id)}.json"


def _safe_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.@+-]", "_", str(value or "")) or "default"


def _aggregate_risk(risks: List[str]) -> str:
    order = {"low": 0, "medium": 1, "high": 2, "block": 3}
    if not risks:
        return "low"
    return max(risks, key=lambda item: order.get(item, 0))


def _signature_payload(manifest: Dict[str, Any], lock: Dict[str, Any]) -> bytes:
    return _canonical_json({"manifest": manifest, "lock": lock})


def _canonical_json(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _load_private_key(value: str) -> Ed25519PrivateKey:
    if not value:
        raise PfpError("private_key is required to build a signed package")
    text = value.strip()
    if text.startswith("-----BEGIN"):
        key = load_pem_private_key(text.encode("utf-8"), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise PfpError("private_key must be an Ed25519 key")
        return key
    raw = _decode_key_bytes(text)
    if len(raw) != 32:
        raise PfpError("Ed25519 private key must be 32 raw bytes")
    return Ed25519PrivateKey.from_private_bytes(raw)


def _load_public_key(value: str) -> Ed25519PublicKey:
    text = value.strip()
    if text.startswith("-----BEGIN"):
        key = load_pem_public_key(text.encode("utf-8"))
        if not isinstance(key, Ed25519PublicKey):
            raise PfpError("developer.public_key must be an Ed25519 key")
        return key
    raw = _decode_key_bytes(text)
    if len(raw) != 32:
        raise PfpError("Ed25519 public key must be 32 raw bytes")
    return Ed25519PublicKey.from_public_bytes(raw)


def _decode_key_bytes(value: str) -> bytes:
    text = value.strip()
    if text.startswith("ed25519:"):
        text = text.split(":", 1)[1]
    try:
        if re.fullmatch(r"[0-9a-fA-F]{64}", text):
            return bytes.fromhex(text)
        return base64.b64decode(text, validate=True)
    except Exception as exc:
        raise PfpError("Invalid Ed25519 key/signature encoding") from exc


def _public_key_text(public: Ed25519PublicKey) -> str:
    raw = public.public_bytes(Encoding.Raw, PublicFormat.Raw)
    return "ed25519:" + base64.b64encode(raw).decode("ascii")


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def _load_json_bytes(data: bytes) -> Dict[str, Any]:
    value = json.loads(data.decode("utf-8"))
    if not isinstance(value, dict):
        raise PfpError("JSON file must contain an object")
    return value


def _read_json_file(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise PfpError(f"{path.name} must contain a JSON object")
    return data


def _write_json_file(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)

