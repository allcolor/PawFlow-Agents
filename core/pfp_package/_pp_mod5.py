"""pfp_package split module 5 (dependency level group)."""

from __future__ import annotations
import base64
import hmac
import logging
import os
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from core.pfp_package._pp_base import (  # noqa: F401
    FORMAT_VERSION, LOCK_FILE, MANIFEST_FILE, PfpError, SIGNATURE_FILE, _INSTALLABLE_TYPES, _RESERVED_SKILL_WORDS, _RESOURCE_NAME_RE, _RESOURCE_TYPES, _SKILL_NAME_RE)
from core.pfp_package._pp_mod1 import (  # noqa: F401
    _canonical_json, _file_sha256, _files_size, _format_dependency, _install_default_relay_id, _load_json_bytes, _make_lock, _name_from_id, _normalize_scope, _provenance, _public_key_text, _read_json_file, _register_flow_task_proxy, _safe_relpath, _validate_package_id, _validate_runtime_object, _verify_lock, _write_flow, _write_json_file, _write_resource, _write_service)
from core.pfp_package._pp_mod2 import (  # noqa: F401
    _collect_source_files, _existing_status_name, _load_private_key, _load_resource_data, _manifest_object_hash, _object_capabilities, _read_pfp_zip, _signature_payload, _validate_ui_extension_object, _validate_web_app_object)
from core.pfp_package._pp_mod3 import (  # noqa: F401
    _declared_secret_requirements, _inject_package_flow_task_relays, _install_record_path, _missing_agent_assigned_skills, _ui_extension_manifest, _uninstall_object, _verify_signature, _web_app_manifest)
from core.pfp_package._pp_mod4 import (  # noqa: F401
    _declared_package_dependencies, _dependent_packages, _existing_status, _missing_package_dependencies, _pinned_developer_key, _record_is_locally_modified, _refresh_runtime, _remove_package_content_store, _validate_allowed_refs, _validate_dependency_list)

logger = logging.getLogger(__name__)


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
    elif obj_type == "skill" and (not _SKILL_NAME_RE.match(name) or "--" in name
                                   or any(word in name for word in _RESERVED_SKILL_WORDS)):
        status, reason, installable = "blocked", "invalid Agent Skill name", False
    elif path and _safe_relpath(path) not in package["files"]:
        status, reason, installable = "blocked", f"missing package file: {path}", False
    elif obj_type == "ui_extension":
        _ui_err = _validate_ui_extension_object(obj, package)
        if _ui_err:
            status, reason, installable = "blocked", _ui_err, False
    elif obj_type == "web_app":
        _webapp_err = _validate_web_app_object(obj, package)
        if _webapp_err:
            status, reason, installable = "blocked", _webapp_err, False
    elif missing_dependencies:
        status = "missing_dependency"
        reason = "missing package dependency: " + ", ".join(
            _format_dependency(dep) for dep in missing_dependencies)
    if installable and status == "new" and obj_type == "skill":
        try:
            parsed = _load_resource_data(package, _safe_relpath(path), "skill", name)
            parsed_name = str(parsed.get("name") or "").strip()
            if parsed_name != name:
                status, reason, installable = "blocked", "SKILL.md name does not match package object name", False
            elif not str(parsed.get("description") or "").strip():
                status, reason, installable = "blocked", "SKILL.md frontmatter.description is required", False
        except Exception as exc:
            status, reason, installable = "blocked", str(exc), False
    if installable and status == "new" and obj_type == "agent":
        try:
            data = _load_resource_data(package, _safe_relpath(path), "agent", name)
            missing_skills = _missing_agent_assigned_skills(
                data, package, user_id, conversation_id, scope)
            if missing_skills:
                status = "missing_dependency"
                reason = "missing assigned skill: " + ", ".join(missing_skills)
        except Exception as exc:
            status, reason, installable = "blocked", str(exc), False
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


def _load_agent_hook_proxy_data(obj: Dict[str, Any], package: Dict[str, Any], rel: str,
                                provenance: Dict[str, Any],
                                secret_bindings: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    manifest = package["manifest"]
    metadata: Dict[str, Any] = {}
    if rel.endswith(".json"):
        metadata = _load_json_bytes(package["files"][rel])
    events = obj.get("events") or metadata.get("events") or []
    if isinstance(events, str):
        events = [events]
    if not isinstance(events, list):
        raise PfpError("agent_hook events must be a list")
    tools = obj.get("tools") or metadata.get("tools") or []
    if isinstance(tools, str):
        tools = [tools]
    if not isinstance(tools, list):
        raise PfpError("agent_hook tools must be a list")
    return {
        "source": "",
        "description": str(obj.get("description") or metadata.get("description") or ""),
        "events": events,
        "tools": tools,
        "fail_policy": str(obj.get("fail_policy") or metadata.get("fail_policy") or "open"),
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


def _verify_pinned_developer_key(package: Dict[str, Any], user_id: str,
                                 conversation_id: str, scope: str,
                                 force: bool = False) -> None:
    """Enforce trust-on-first-use on the package's developer key.

    Raises PfpError if a prior install of the same package name pinned a
    different developer.public_key. `force=True` overrides the pin (the
    operator explicitly accepts the key change), mirroring how `force`
    overrides conflict/local-modification gates elsewhere.
    """
    manifest = package.get("manifest") or {}
    package_id = str(manifest.get("package") or "")
    if not package_id:
        return
    new_key = str((manifest.get("developer") or {}).get("public_key") or "")
    pinned = _pinned_developer_key(package_id, user_id, conversation_id, scope)
    if not pinned or not new_key:
        return
    if not hmac.compare_digest(pinned, new_key):
        if force:
            logger.warning(
                "[pfp] developer key for %s changed from pinned %s... to %s... "
                "— accepted because force=True",
                package_id, pinned[:16], new_key[:16])
            return
        raise PfpError(
            f"Developer key mismatch for package '{package_id}': this update "
            f"is signed by a different key than the one pinned at first "
            f"install. Refusing to install (the package may have been "
            f"compromised or hijacked at its registry). Re-run with force=True "
            f"only if you trust the new signer.")


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


def dev_unload_pfp(package_id: str, *, user_id: str, conversation_id: str = "",
                   scope: str = "conversation", force: bool = True) -> Dict[str, Any]:
    """Unload a development package loaded from .pfpdir."""
    if scope in {"", "conversation", "conv"} and not conversation_id:
        scope = "user"
    return uninstall_pfp(package_id, user_id=user_id,
                         conversation_id=conversation_id, scope=scope, force=force)


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
    _extra_rels = None
    if obj_type == "skill" and rel.endswith("SKILL.md"):
        _skill_dir = rel[:-len("SKILL.md")]
        if _skill_dir:
            _extra_rels = [f for f in (package.get("files") or {})
                           if f != rel and f.startswith(_skill_dir)]
    provenance = _provenance(package, obj_id, rel, extra_rels=_extra_rels)
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
    if obj_type == "agent_hook":
        data = _load_agent_hook_proxy_data(
            obj, package, rel, provenance, secret_bindings)
        _write_resource("agent_hook", name, data, user_id, conversation_id, scope, replace)
        return {
            "kind": "resource",
            "object_id": obj_id,
            "resource_type": "agent_hook",
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
        existing_skill = None
        if rtype == "skill":
            from core.resource_store import ResourceStore
            existing_skill = ResourceStore.instance().get(
                "skill", name, user_id,
                conversation_id if scope == "conversation" else "")
        _write_resource(rtype, name, data, user_id, conversation_id, scope, replace)
        if rtype == "skill" and existing_skill and conversation_id:
            from core.skill_lifecycle import notify_skill_updated
            notify_skill_updated(name, data, user_id, conversation_id)
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
        manifest_obj = _ui_extension_manifest(
            obj, package, secret_bindings=secret_bindings)
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
    if obj_type == "web_app":
        manifest_obj = _web_app_manifest(obj, package)
        package_id = str(package["manifest"].get("package") or "")
        return {
            "kind": "web_app",
            "object_id": obj_id,
            "name": name,
            "version_compat": manifest_obj["version_compat"],
            "entry": manifest_obj["entry"],
            "assets": manifest_obj["assets"],
            "url": f"/apps/{package_id}/{name}/",
            "installed_from": provenance,
            "dependencies": dependencies,
            "hash": provenance["hash"],
        }
    raise PfpError(f"Unsupported object type: {obj_type}")


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
