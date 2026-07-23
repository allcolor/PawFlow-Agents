"""pfp_package split module 3 (dependency level group)."""

from __future__ import annotations
import hashlib
import json
import logging
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from cryptography.exceptions import InvalidSignature
import core.paths as _paths
from core.pfp_package._pp_base import (  # noqa: F401
    PfpError, _PACKAGE_ID_RE, _RUNTIME_OBJECT_TYPES, _UI_API_VERSION, _WEBAPP_API_VERSION)
from core.pfp_package._pp_mod1 import (  # noqa: F401
    _agent_assigned_skill_names, _looks_like_package_ref, _normalize_scope, _read_json_file, _register_flow_task_proxy, _safe_component, _safe_relpath, _skill_bundled_files, _split_package_object_ref, _ui_extension_asset_list, _validate_version_ref, _version_tuple, _web_app_asset_list)
from core.pfp_package._pp_mod2 import (  # noqa: F401
    _find_replacement_flow_task_record, _install_scope_dir, _load_public_key, _load_resource_data, _normalize_secret_requirements, _package_content_root, _package_flow_task_types, _package_skill_names, _parse_package_version, _signature_payload, _uninstall_flow, _version_part_satisfies)

logger = logging.getLogger(__name__)


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
        for marker in ("::task::", "::task_verify::", "::delegate::", "::flash::"):
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


def _missing_agent_assigned_skills(data: Dict[str, Any], package: Dict[str, Any],
                                  user_id: str, conversation_id: str,
                                  scope: str, selected_ids: Optional[set] = None) -> List[str]:
    package_skills = _package_skill_names(package)
    missing = []
    from core.resource_store import ResourceStore
    store = ResourceStore.instance()
    for skill_name in _agent_assigned_skill_names(data):
        if selected_ids is None:
            if skill_name in package_skills:
                continue
        elif f"skill:{skill_name}" in selected_ids:
            continue
        conv = conversation_id if scope == "conversation" else ""
        if store.get_any("skill", skill_name, user_id, conversation_id=conv):
            continue
        missing.append(skill_name)
    return missing


def _verify_signature(manifest: Dict[str, Any], lock: Dict[str, Any], signature: bytes) -> None:
    public_text = str((manifest.get("developer") or {}).get("public_key") or "")
    if not public_text:
        raise PfpError("developer.public_key is required")
    public = _load_public_key(public_text)
    try:
        public.verify(signature, _signature_payload(manifest, lock))
    except InvalidSignature as exc:
        raise PfpError("Package signature verification failed") from exc


def _declared_secret_requirements(manifest: Dict[str, Any],
                                  obj: Dict[str, Any]) -> List[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    for item in _normalize_secret_requirements(manifest.get("secrets", [])):
        merged[item["name"]] = item
    for item in _normalize_secret_requirements(obj.get("secrets", [])):
        merged[item["name"]] = item
    # ui_extension nests per-handler secret requirements one level deeper.
    # Surface them at the object level so the install plan, capability
    # aggregator, and `--secret name=key` matcher see them like any other.
    if str(obj.get("type") or "") == "ui_extension":
        for handler in obj.get("handlers") or []:
            if not isinstance(handler, dict):
                continue
            for item in _normalize_secret_requirements(handler.get("secrets")):
                merged.setdefault(item["name"], item)
    return list(merged.values())


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


def _ui_extension_manifest(obj: Dict[str, Any], package: Dict[str, Any],
                            secret_bindings: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Build the install-record manifest (assets with sha + size, slots, hooks, handlers)."""
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
    secret_bindings = secret_bindings or {}
    for entry in (obj.get("handlers") or []):
        rel = _safe_relpath(str(entry.get("path") or ""))
        data = package["files"][rel]
        digest = hashlib.sha256(data).hexdigest()
        handler_secrets = _normalize_secret_requirements(entry.get("secrets"))
        # Bindings are install-time `--secret name=stored_key` pairs that
        # apply to the whole package. Per-handler `secret_bindings` is the
        # subset of those that this handler actually requested.
        handler_bindings = {
            req["name"]: secret_bindings[req["name"]]
            for req in handler_secrets
            if req["name"] in secret_bindings
        }
        handlers.append({
            "action": str(entry.get("action") or ""),
            "path": rel,
            "sha256": "sha256:" + digest,
            "runner": "python",
            "allowed_tools": list(entry.get("allowed_tools") or []),
            "allowed_services": list(entry.get("allowed_services") or []),
            "secrets": handler_secrets,
            "secret_bindings": handler_bindings,
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


def _web_app_manifest(obj: Dict[str, Any], package: Dict[str, Any]) -> Dict[str, Any]:
    """Build the install-record manifest (assets with sha + size, entry path)."""
    rows = []
    for item in _web_app_asset_list(obj):
        rel = _safe_relpath(item)
        data = package["files"][rel]
        digest = hashlib.sha256(data).hexdigest()
        rows.append({
            "path": rel,
            "sha256": "sha256:" + digest,
            "size": len(data),
        })
    entry = _safe_relpath(str(obj.get("entry") or ""))
    return {
        "version_compat": _WEBAPP_API_VERSION,
        "entry": entry,
        "assets": rows,
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


def list_installed_web_apps(*, user_id: str, conversation_id: str = "",
                           scope: str = "user") -> List[Dict[str, Any]]:
    """Return the asset manifest for every installed web_app object in the scope.

    Each entry has: package, version, content_dir, object_id, name,
    version_compat, entry, assets, url. Conversation scope inherits
    user-scope packages. Unlike ui_extension (one bundle per package),
    a package may ship several web_app objects, each served at its own
    `/apps/<package>/<name>/` route.
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
            if not package_id:
                continue
            content_dir = str(record.get("content_dir") or "")
            if not content_dir or not Path(content_dir).is_dir():
                continue
            objects = record.get("objects") or []
            web_objects = [obj for obj in objects if obj.get("kind") == "web_app"]
            for web in web_objects:
                object_id = str(web.get("object_id") or "")
                dedupe_key = (package_id, object_id)
                if not object_id or dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                name = str(web.get("name") or "")
                out.append({
                    "package": package_id,
                    "version": str(record.get("version") or ""),
                    "scope": sc,
                    "content_dir": content_dir,
                    "object_id": object_id,
                    "name": name,
                    "version_compat": str(web.get("version_compat") or _WEBAPP_API_VERSION),
                    "entry": str(web.get("entry") or ""),
                    "assets": list(web.get("assets") or []),
                    "url": f"/apps/{package_id}/{name}/",
                    "installed_from": dict(web.get("installed_from") or {}),
                })
    return out


def _review_object_for_install(row: Dict[str, Any], package: Dict[str, Any],
                               force: bool, user_id: str,
                               conversation_id: str,
                               operation: str) -> None:
    obj = row["object"]
    obj_type = str(obj.get("type") or "")
    if obj_type == "skill":
        rel = _safe_relpath(str(obj.get("path") or ""))
        data = _load_resource_data(package, rel, "skill", row.get("name", ""))
        # Only the skill's own bundled assets — not every object in the package.
        package_files = _skill_bundled_files(package, rel)
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
        return
    if obj_type == "ui_extension":
        # Scan all declared ui_extension files (scripts, styles, i18n,
        # handlers) through the static+LLM pipeline. Browser-side .js/.css
        # assets are matched against `_JS_STATIC_PATTERNS`; the .py handlers
        # fall through to the python pattern set automatically.
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
        assert_installable_review(review, force=force, label="UI extension")
        obj["_review"] = review_metadata(
            review,
            service_id=review.get("service_id", ""),
            llm_service=review.get("llm_service", ""),
            subject_hash=review_hash(obj, package.get("lock", {}).get("files", {})),
        )
        return
    if obj_type == "web_app":
        # Same static+LLM pipeline as ui_extension; the .html entry is
        # matched against `_JS_STATIC_PATTERNS` like .js/.css already are.
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
        assert_installable_review(review, force=force, label="Web app")
        obj["_review"] = review_metadata(
            review,
            service_id=review.get("service_id", ""),
            llm_service=review.get("llm_service", ""),
            subject_hash=review_hash(obj, package.get("lock", {}).get("files", {})),
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
        deleted = store.delete(rtype, name, user_id, conv)
        if deleted and rtype == "skill":
            from core.skill_lifecycle import remove_skill_assignments
            remove_skill_assignments(
                name, user_id, conversation_id,
                resource_store=store, source="skill_delete")
        return deleted
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
    if kind == "web_app":
        # Same as ui_extension: static assets live in the package content
        # store, removed by uninstall_pfp when no objects remain.
        return True
    return False


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


def _installed_package_records(user_id: str, conversation_id: str, scope: str) -> Dict[str, Dict[str, Any]]:
    roots = [_install_scope_dir(user_id, "", "user")]
    if scope == "conversation":
        conv_ids = []
        for marker in ("::task::", "::task_verify::", "::delegate::", "::flash::"):
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


def _package_content_dir(package_id: str, version: str, user_id: str,
                         conversation_id: str, scope: str) -> Path:
    return (_package_content_root(user_id, conversation_id, scope)
            / _safe_component(package_id) / _safe_component(version))


def _install_record_path(package_id: str, user_id: str, conversation_id: str,
                         scope: str) -> Path:
    return _install_scope_dir(user_id, conversation_id, scope) / f"{_safe_component(package_id)}.json"
