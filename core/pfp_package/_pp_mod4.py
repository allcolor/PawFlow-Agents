"""pfp_package split module 4 (dependency level group)."""

from __future__ import annotations
import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from core.pfp_package._pp_base import (  # noqa: F401
    LOCK_FILE, MANIFEST_FILE, PfpError, _RESOURCE_TYPES)
from core.pfp_package._pp_mod1 import (  # noqa: F401
    _canonical_json, _dedupe_dependencies, _normalize_scope, _read_json_file, _record_dependencies, _record_key, _safe_relpath, _write_bytes_file, _write_json_file)
from core.pfp_package._pp_mod2 import (  # noqa: F401
    _install_scope_dir, _load_resource_data, _manifest_object_hash, _record_depends_on_package, _version_change_kind)
from core.pfp_package._pp_mod3 import (  # noqa: F401
    _allowed_package, _dependency_package, _dependent_record_roots, _install_record_path, _installed_package_records, _missing_agent_assigned_skills, _package_content_dir, _remove_package_content_path, _version_satisfies, list_installed_ui_extensions, load_installed_package_tasks, resolve_installed_flow_task_runtime)

logger = logging.getLogger(__name__)


def list_installed_packages(*, user_id: str, conversation_id: str = "",
                            scope: str = "user") -> Dict[str, Any]:
    scope = _normalize_scope(scope, conversation_id)
    root = _install_scope_dir(user_id, conversation_id, scope)
    packages = []
    package_ids = set()
    if root.exists():
        for path in sorted(root.glob("*.json")):
            try:
                record = _read_json_file(path)
                package_id = str(record.get("package") or "")
                if package_id:
                    package_ids.add(package_id)
                packages.append(record)
            except (OSError, json.JSONDecodeError, PfpError) as exc:
                logger.debug("Skipping unreadable package install record %s: %s", path, exc)
    blocked_by = {pid: [] for pid in package_ids}
    blocked_keys = {pid: set() for pid in package_ids}
    if package_ids:
        for dep_root, record_scope, record_conversation_id in _dependent_record_roots(
                user_id, conversation_id, scope):
            if not dep_root.exists():
                continue
            for dep_path in sorted(dep_root.glob("*.json")):
                try:
                    dep_record = _read_json_file(dep_path)
                except Exception:
                    logging.getLogger(__name__).debug(
                        "Ignored exception", exc_info=True)
                    continue
                dependent_package = str(dep_record.get("package") or "")
                if not dependent_package:
                    continue
                for package_id in package_ids:
                    if dependent_package == package_id:
                        continue
                    if any(_record_depends_on_package(obj, package_id)
                           for obj in dep_record.get("objects") or []):
                        dep_key = (
                            f"{record_scope}:{record_conversation_id}:"
                            f"{dependent_package}")
                        if dep_key in blocked_keys.setdefault(package_id, set()):
                            continue
                        dependent = {
                            "package": dependent_package,
                            "version": str(dep_record.get("version") or ""),
                        }
                        if record_scope != scope:
                            dependent["scope"] = record_scope
                            if record_conversation_id:
                                dependent["conversation_id"] = record_conversation_id
                        blocked_keys.setdefault(package_id, set()).add(dep_key)
                        blocked_by.setdefault(package_id, []).append(dependent)
    for record in packages:
        package_id = str(record.get("package") or "")
        record["blocked_by"] = blocked_by.get(package_id, [])
    return {"ok": True, "scope": scope, "packages": packages}


def _selected_agent_missing_skills(row: Dict[str, Any], package: Dict[str, Any],
                                   selected_ids: set, user_id: str,
                                   conversation_id: str, scope: str) -> List[str]:
    if str(row.get("type") or "") != "agent":
        return []
    obj = row.get("object") or {}
    rel = _safe_relpath(str(obj.get("path") or ""))
    data = _load_resource_data(package, rel, "agent", row.get("name", ""))
    return _missing_agent_assigned_skills(
        data, package, user_id, conversation_id, scope, selected_ids)


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
                # Bindings are recorded per handler at install time. Without
                # this, a handler declaring a required secret would 502 at
                # invoke time because the runtime cannot resolve it.
                "secret_bindings": dict(handler.get("secret_bindings") or {}),
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


def _pinned_developer_key(package_id: str, user_id: str, conversation_id: str,
                          scope: str) -> str:
    """Return the developer.public_key pinned by a prior install of this
    package name in this scope, or "" if the package was never installed.

    Trust-on-first-use: the first signed install records the developer key;
    later updates MUST present the same key. The .pfp signature only proves
    the package is internally consistent with whatever key it carries, not
    that the key belongs to the original author — so without this pin a
    registry (or MITM) could ship an update signed by an attacker key and it
    would verify fine. The pin closes that gap.
    """
    path = _install_record_path(package_id, user_id, conversation_id, scope)
    if not path.exists():
        return ""
    try:
        record = _read_json_file(path)
    except Exception:
        return ""
    return str((record.get("developer") or {}).get("public_key") or "")


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
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
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
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
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
