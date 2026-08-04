"""Validation helpers for generic PFP-defined repository objects."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.extension_repository import (
    MAX_RESOURCE_ASSET_BYTES,
    MAX_RESOURCE_ASSETS,
    MAX_RESOURCE_ASSETS_BYTES,
    SAFE_RESOURCE_ASSET_EXTENSIONS,
    validate_document,
    validate_json_schema,
    validate_resource_name,
    validate_resource_type,
    validate_schema_version,
)


_SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9._@+\-/]+$")
_ASSET_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def safe_package_path(value: str) -> str:
    rel = str(value or "").replace("\\", "/").strip("/")
    if not rel or rel.startswith("/") or ".." in rel.split("/"):
        raise ValueError(f"unsafe package path: {value}")
    if not _SAFE_PATH_RE.fullmatch(rel):
        raise ValueError(f"unsafe package path characters: {value}")
    return rel


def repository_type_descriptor(obj: Dict[str, Any], package: Dict[str, Any]) -> Dict[str, Any]:
    resource_type = validate_resource_type(obj.get("resource_type", ""))
    schema_version = validate_schema_version(obj.get("schema_version", ""))
    schema_path = safe_package_path(obj.get("schema", ""))
    schema = validate_json_schema(
        _load_json_object(package, schema_path, "repository_type schema"))
    contributions = obj.get("contributions")
    if contributions not in {"owner", "dependencies"}:
        raise ValueError(
            "repository_type contributions must be owner or dependencies")
    if not isinstance(obj.get("mutable"), bool):
        raise ValueError("repository_type mutable must be a boolean")
    extensions = obj.get("asset_extensions")
    if not isinstance(extensions, list):
        raise ValueError("repository_type asset_extensions must be a list")
    normalized_extensions: List[str] = []
    for item in extensions:
        ext = str(item or "").strip().lower()
        if not ext.startswith("."):
            raise ValueError(
                "repository_type asset_extensions entries must start with a dot")
        if ext not in SAFE_RESOURCE_ASSET_EXTENSIONS:
            raise ValueError(
                f"repository_type asset extension is not allowed: {ext}")
        if ext not in normalized_extensions:
            normalized_extensions.append(ext)
    return {
        "resource_type": resource_type,
        "schema_version": schema_version,
        "schema_path": schema_path,
        "schema": schema,
        "contributions": contributions,
        "mutable": obj["mutable"],
        "asset_extensions": normalized_extensions,
        "title_key": str(obj.get("title_key") or ""),
        "owner_package": str(
            (package.get("manifest") or {}).get("package") or ""),
    }


def repository_resource_payload(
        obj: Dict[str, Any], package: Dict[str, Any],
        descriptor: Dict[str, Any]) -> Dict[str, Any]:
    resource_type = validate_resource_type(obj.get("resource_type", ""))
    if resource_type != descriptor.get("resource_type"):
        raise ValueError(
            "repository_resource resource_type does not match its descriptor")
    schema_version = validate_schema_version(obj.get("schema_version", ""))
    if schema_version != descriptor.get("schema_version"):
        raise ValueError(
            "repository_resource schema_version does not match its descriptor")
    name = validate_resource_name(obj.get("name", ""))
    document_path = safe_package_path(obj.get("path", ""))
    document = validate_document(
        _load_json_object(
            package, document_path, "repository_resource path"),
        descriptor["schema"])
    assets = _resource_assets(obj, package, descriptor)
    return {
        "resource_type": resource_type,
        "schema_version": schema_version,
        "name": name,
        "document_path": document_path,
        "document": document,
        "assets": assets,
    }


def package_repository_type(
        package: Dict[str, Any],
        resource_type: str) -> Optional[Dict[str, Any]]:
    matches = []
    manifest = package.get("manifest") or {}
    for obj in manifest.get("objects") or []:
        if (isinstance(obj, dict)
                and str(obj.get("type") or "") == "repository_type"
                and str(obj.get("resource_type") or "") == resource_type):
            matches.append(obj)
    if len(matches) > 1:
        raise ValueError(
            f"package declares repository_type more than once: {resource_type}")
    if not matches:
        return None
    descriptor = repository_type_descriptor(matches[0], package)
    descriptor["object_id"] = str(matches[0].get("id") or "")
    return descriptor


def repository_object_hash(
        obj: Dict[str, Any], package: Dict[str, Any]) -> str:
    paths: List[str] = []
    obj_type = str(obj.get("type") or "")
    if obj_type == "repository_type":
        paths.append(safe_package_path(obj.get("schema", "")))
    elif obj_type == "repository_resource":
        paths.append(safe_package_path(obj.get("path", "")))
        for item in obj.get("assets") or []:
            if isinstance(item, dict):
                paths.append(safe_package_path(item.get("path", "")))
    else:
        return ""
    lock_files = (package.get("lock") or {}).get("files") or {}
    if len(paths) == 1:
        return str(lock_files.get(paths[0]) or "")
    digest = hashlib.sha256()
    for rel in sorted(set(paths)):
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(lock_files.get(rel) or "").encode("utf-8"))
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def package_depends_on(
        manifest: Dict[str, Any], package_id: str,
        obj: Optional[Dict[str, Any]] = None) -> bool:
    values: List[Any] = list(manifest.get("dependencies") or [])
    if obj:
        values.extend(obj.get("requires") or [])
    for item in values:
        if isinstance(item, str):
            ref = (item.removeprefix("package:")
                   .split("@", 1)[0].split("/", 1)[0])
        elif isinstance(item, dict):
            ref = str(item.get("package") or "").split("@", 1)[0]
        else:
            continue
        if ref == package_id:
            return True
    return False


def _resource_assets(
        obj: Dict[str, Any], package: Dict[str, Any],
        descriptor: Dict[str, Any]) -> List[Dict[str, Any]]:
    values = obj.get("assets")
    if values is None:
        values = []
    if not isinstance(values, list):
        raise ValueError("repository_resource assets must be a list")
    if len(values) > MAX_RESOURCE_ASSETS:
        raise ValueError(
            "repository_resource assets exceed the count limit")
    allowed = set(descriptor.get("asset_extensions") or [])
    files = package.get("files") or {}
    seen_ids = set()
    seen_paths = set()
    rows = []
    total_size = 0
    for item in values:
        if not isinstance(item, dict):
            raise ValueError(
                "repository_resource asset entries must be objects")
        asset_id = str(item.get("id") or "").strip()
        if not _ASSET_ID_RE.fullmatch(asset_id):
            raise ValueError("repository_resource asset id is invalid")
        if asset_id in seen_ids:
            raise ValueError(
                f"repository_resource asset id is duplicated: {asset_id}")
        rel = safe_package_path(item.get("path", ""))
        if rel in seen_paths:
            raise ValueError(
                f"repository_resource asset path is duplicated: {rel}")
        if rel not in files:
            raise ValueError(
                f"repository_resource asset is missing: {rel}")
        ext = Path(rel).suffix.lower()
        if ext not in allowed:
            raise ValueError(
                f"repository_resource asset extension is not declared: {ext}")
        size = len(files[rel])
        if size > MAX_RESOURCE_ASSET_BYTES:
            raise ValueError(
                f"repository_resource asset exceeds the size limit: {rel}")
        total_size += size
        if total_size > MAX_RESOURCE_ASSETS_BYTES:
            raise ValueError(
                "repository_resource assets exceed the aggregate size limit")
        lock_hash = str(
            ((package.get("lock") or {}).get("files") or {}).get(rel) or "")
        rows.append({
            "id": asset_id,
            "path": rel,
            "sha256": lock_hash,
            "size": size,
            "extension": ext,
        })
        seen_ids.add(asset_id)
        seen_paths.add(rel)
    return rows


def _load_json_object(
        package: Dict[str, Any], rel: str, label: str) -> Dict[str, Any]:
    files = package.get("files") or {}
    if rel not in files:
        raise ValueError(f"{label} is missing in package: {rel}")
    raw = files[rel]
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"{label} must contain valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return value
