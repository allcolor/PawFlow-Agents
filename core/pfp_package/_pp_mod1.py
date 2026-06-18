"""pfp_package split module 1 (dependency level group)."""

from __future__ import annotations
import base64
import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
)
import yaml
import core.paths as _paths
from core.pfp_package._pp_base import (  # noqa: F401
    LOCK_VERSION, PfpError, _FRONTMATTER_RE, _PACKAGE_ID_RE, _RESERVED_SKILL_WORDS, _RUNTIME_OBJECT_TYPES, _SAFE_PATH_RE, _SKILL_NAME_RE, _SUPPORTED_RUNTIME_RUNNERS, _VERSION_REF_RE)

logger = logging.getLogger(__name__)


def _append_display_list(lines: List[str], label: str, values: List[str]) -> None:
    clean = [str(value) for value in values if str(value or "")]
    if clean:
        lines.append(f"- {label}: " + ", ".join(clean))
    else:
        lines.append(f"- {label}: none")


def _agent_assigned_skill_names(data: Dict[str, Any]) -> List[str]:
    from core.skill_resolver import normalize_skill_entry
    names = []
    seen = set()
    for entry in list((data or {}).get("assigned_skills") or []):
        name, _params, _condition = normalize_skill_entry(entry)
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


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
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
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


def _register_flow_task_proxy(data: Dict[str, Any]) -> None:
    from core.pfp_task_runtime import register_package_task_proxy
    register_package_task_proxy(data["task_type"], data)


def _install_default_relay_id(conversation_id: str, agent_name: str = "") -> str:
    if not conversation_id:
        return ""
    from core.relay_bindings import get_default
    return str(get_default(conversation_id, agent=agent_name) or "")


def _skill_bundled_files(package: Dict[str, Any], rel: str) -> Dict[str, bytes]:
    """Return {skill-relative path: bytes} for files bundled with a skill.

    A PFP skill object points at content/skills/<name>/SKILL.md; any sibling
    file under that directory is a bundled asset (scripts/, references/...).
    Content is returned verbatim as bytes so binary assets are preserved —
    nothing is dropped or lossily decoded.
    """
    if not rel.endswith("SKILL.md"):
        return {}
    skill_dir = rel[:-len("SKILL.md")]
    if not skill_dir:
        return {}
    out: Dict[str, bytes] = {}
    for fpath, content in (package.get("files") or {}).items():
        if fpath == rel or not fpath.startswith(skill_dir):
            continue
        sub = fpath[len(skill_dir):]
        if sub:
            out[sub] = content
    return out


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
    name = str(meta.get("name") or default_name)
    if (not _SKILL_NAME_RE.match(name) or "--" in name
            or any(word in name for word in _RESERVED_SKILL_WORDS)):
        raise PfpError("Skill name must follow Agent Skills spec: lowercase letters, numbers, single hyphens")
    return {
        "instructions": body,
        "description": str(meta.get("description", "") or ""),
        "name": name,
        # template_engine is dropped: PawFlow-specific dynamic templating is
        # not supported (portability — see agentskills.io conformance).
        **{k: v for k, v in meta.items()
           if k not in {"name", "description", "template_engine"}},
    }


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


def _record_dependencies(record: Dict[str, Any]) -> List[Any]:
    runtime = record.get("package_runtime") or {}
    dependencies: List[Any] = []
    dependencies.extend(record.get("dependencies") or [])
    dependencies.extend(runtime.get("dependencies") or [])
    return dependencies


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


def _selected_ids(objects: List[Dict[str, Any]], include: Optional[Iterable[str]],
                  exclude: Optional[Iterable[str]]) -> set:
    include_set = {str(x) for x in include or [] if str(x)}
    exclude_set = {str(x) for x in exclude or [] if str(x)}
    if include_set:
        ids = {o["id"] for o in objects if o.get("id") in include_set}
    else:
        ids = {o["id"] for o in objects if o.get("selected")}
    return ids.difference(exclude_set)


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


def _provenance(package: Dict[str, Any], obj_id: str, rel: str,
                extra_rels: Optional[List[str]] = None) -> Dict[str, Any]:
    manifest = package["manifest"]
    lock_files = package["lock"]["files"]
    file_hash = lock_files.get(rel, "")
    if extra_rels:
        # Fold bundled-asset hashes in so drift in a skill's scripts/ or
        # references/ is reflected in the object hash (see _pfp update).
        import hashlib
        parts = [file_hash] + [
            f"{r}:{lock_files.get(r, '')}" for r in sorted(extra_rels)]
        file_hash = hashlib.sha256(
            "\n".join(parts).encode("utf-8")).hexdigest()
    return {
        "package": manifest["package"],
        "version": manifest["version"],
        "object_id": obj_id,
        "file": rel,
        "hash": file_hash,
        "package_sha256": package.get("sha256", ""),
        "content_dir": package.get("content_dir", ""),
        "source_dir": package.get("path", "") if package.get("dev") else "",
        "source_type": package.get("source_type", ""),
        "verified": bool(package.get("verified")),
        "dev": bool(package.get("dev")),
        "developer_key": (manifest.get("developer") or {}).get("public_key", ""),
    }


def _safe_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.@+-]", "_", str(value or "")) or "default"


def _aggregate_risk(risks: List[str]) -> str:
    order = {"low": 0, "medium": 1, "high": 2, "block": 3}
    if not risks:
        return "low"
    return max(risks, key=lambda item: order.get(item, 0))


def _canonical_json(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


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
