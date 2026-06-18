"""pfp_package split module 2 (dependency level group)."""

from __future__ import annotations
import base64
import json
import logging
import shutil
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    load_pem_private_key,
    load_pem_public_key,
)
import yaml
import core.paths as _paths
from core.pfp_package._pp_base import (  # noqa: F401
    FORMAT_VERSION, LOCK_FILE, MANIFEST_FILE, PfpError, SIGNATURE_FILE, _RESOURCE_NAME_RE, _RESOURCE_TYPES, _UI_API_VERSION, _UI_ASSET_EXTENSIONS, _UI_HANDLER_ACTION_RE, _UI_KNOWN_HOOKS, _UI_KNOWN_SLOTS)
from core.pfp_package._pp_mod1 import (  # noqa: F401
    _agent_assigned_skill_names, _append_display_list, _canonical_json, _capability_refs, _caret_upper_bound, _compare_versions, _decode_key_bytes, _dedupe_dependencies, _dedupe_dicts, _format_bytes, _format_dependency, _iter_install_record_paths, _load_json_bytes, _name_from_id, _parse_skill_md, _public_key_text, _read_json_file, _record_dependencies, _register_flow_task_proxy, _safe_component, _safe_relpath, _secret_env_name, _secret_key_exists, _skill_bundled_files, _split_object_ref, _tilde_upper_bound, _ui_extension_asset_list, _validate_package_id, _validate_version_ref, _version_tuple, _write_json_file)

logger = logging.getLogger(__name__)


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
    pending = [str(spec) for spec in (include or [])]
    seen_refs = set()
    index = 0
    while index < len(pending):
        spec = pending[index]
        index += 1
        rtype, name = _split_object_ref(str(spec))
        if rtype == "task":
            rtype = "task_def"
        ref_key = f"{rtype}:{name}"
        if ref_key in seen_refs:
            continue
        seen_refs.add(ref_key)
        if rtype not in _RESOURCE_TYPES.values():
            raise PfpError(f"Export does not support resource type: {rtype}")
        item = store.get_any(rtype, name, user_id, conversation_id=conversation_id)
        if not item:
            raise PfpError(f"{rtype}:{name} not found")
        clean = {k: v for k, v in item.items() if not str(k).startswith("_")}
        clean.pop("created_at", None)
        clean.pop("updated_at", None)
        if rtype == "skill":
            path = f"content/skills/{name}/SKILL.md"
            target = out / path
            target.parent.mkdir(parents=True, exist_ok=True)
            instructions = str(clean.pop("instructions", "") or clean.pop("prompt", "") or "").strip()
            if not instructions:
                raise PfpError(f"skill:{name} has no instructions")
            skill_root = str(clean.pop("skill_root", "") or "")
            clean.pop("package_files", None)
            clean.pop("prompt", None)
            clean.pop("name", None)
            # Internal/derived fields must not leak into the portable SKILL.md.
            clean.pop("declared_allowed_tools", None)
            clean.pop("installed_from", None)
            clean.pop("imported_from", None)
            clean.pop("package_hash", None)
            clean.pop("review", None)
            meta = {"name": name}
            meta.update(clean)
            if not meta.get("description"):
                raise PfpError(f"skill:{name} has no description")
            skill_text = (
                "---\n"
                + yaml.dump(meta, default_flow_style=False, allow_unicode=True, sort_keys=False)
                + "---\n\n"
                + instructions
                + "\n"
            )
            target.write_text(skill_text, encoding="utf-8")
            # Copy every bundled asset verbatim, straight from the skill
            # directory on disk — binary files included, nothing dropped.
            if skill_root:
                from core.repository import ScopedRepository
                for rel, content in ScopedRepository._read_skill_package_files(
                        Path(skill_root)).items():
                    safe_rel = _safe_relpath(rel)
                    if safe_rel == "SKILL.md":
                        continue
                    asset_target = target.parent / safe_rel
                    asset_target.parent.mkdir(parents=True, exist_ok=True)
                    asset_target.write_bytes(content)
        else:
            path = f"content/{rtype}s/{name}.json"
            target = out / path
            target.parent.mkdir(parents=True, exist_ok=True)
            _write_json_file(target, clean)
            if rtype == "agent":
                for skill_name in _agent_assigned_skill_names(clean):
                    if not store.get_any(
                            "skill", skill_name, user_id,
                            conversation_id=conversation_id):
                        raise PfpError(
                            f"agent:{name} assigned skill:{skill_name} not found")
                    skill_ref = f"skill:{skill_name}"
                    if skill_ref not in seen_refs:
                        pending.append(skill_ref)
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


def _package_skill_names(package: Dict[str, Any]) -> set:
    names = set()
    for obj in (package.get("manifest") or {}).get("objects") or []:
        if not isinstance(obj, dict) or str(obj.get("type") or "") != "skill":
            continue
        name = str(obj.get("name") or _name_from_id(str(obj.get("id") or "")) or "").strip()
        if name:
            names.add(name)
    return names


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
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
    return name


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


def _unavailable_secret_bindings(row: Dict[str, Any], bindings: Dict[str, str],
                                 user_id: str, conversation_id: str) -> List[str]:
    missing = []
    for item in row.get("secrets", []):
        name = str(item.get("name") or "")
        bound_key = bindings.get(name)
        if bound_key and not _secret_key_exists(bound_key, user_id, conversation_id):
            missing.append(bound_key)
    return missing


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


def _load_resource_data(package: Dict[str, Any], rel: str, rtype: str, name: str) -> Dict[str, Any]:
    data = package["files"][rel]
    if rtype == "skill" and rel.endswith("SKILL.md"):
        parsed = _parse_skill_md(data.decode("utf-8"), default_name=name)
        bundled = _skill_bundled_files(package, rel)
        if bundled:
            parsed["package_files"] = bundled
        return parsed
    loaded = _load_json_bytes(data)
    if not isinstance(loaded, dict):
        raise PfpError(f"{rel} must contain a JSON object")
    loaded.pop("name", None)
    return loaded


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


def _find_replacement_flow_task_record(task_type: str,
                                       removed: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    removed_object_id = str(removed.get("object_id") or "")
    removed_package = str((removed.get("installed_from") or {}).get("package") or "")
    for path, _scope, _user_id, _conversation_id in _iter_install_record_paths():
        try:
            record = _read_json_file(path)
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
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


def _record_depends_on_package(record: Dict[str, Any], package_id: str) -> bool:
    for dep in _record_dependencies(record):
        if isinstance(dep, dict) and str(dep.get("package") or "") == package_id:
            return True
    return False


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


def _signature_payload(manifest: Dict[str, Any], lock: Dict[str, Any]) -> bytes:
    return _canonical_json({"manifest": manifest, "lock": lock})


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
