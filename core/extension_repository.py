"""Namespaced repository storage for resource types declared by PFP packages.

This module deliberately does not extend ``ResourceStore`` or ``REPO_TYPES``.
Built-in PawFlow resources keep their closed schemas and behavior, while PFP
packages receive an isolated JSON-only repository whose logical types are
owned by signed ``repository_type`` objects.
"""

from __future__ import annotations

import copy
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import jsonschema

import core.paths as _paths


RESOURCE_TYPE_RE = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+){1,15}$")
RESOURCE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@+-]{0,127}$")
SCHEMA_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,31}$")

MAX_SCHEMA_BYTES = 256 * 1024
MAX_DOCUMENT_BYTES = 1024 * 1024
MAX_RESOURCE_ASSETS = 128
MAX_RESOURCE_ASSET_BYTES = 100 * 1024 * 1024
MAX_RESOURCE_ASSETS_BYTES = 512 * 1024 * 1024

# Repository assets are inert data. Executable/browser-active formats belong
# to a reviewed runtime or ui_extension object, never a repository document.
SAFE_RESOURCE_ASSET_EXTENSIONS = frozenset({
    ".bin", ".glb", ".gltf", ".vrm",
    ".png", ".jpg", ".jpeg", ".webp",
    ".mp3", ".wav", ".ogg", ".flac",
    ".ktx2", ".hdr", ".json",
})


def validate_resource_type(value: str) -> str:
    resource_type = str(value or "").strip()
    if not RESOURCE_TYPE_RE.fullmatch(resource_type):
        raise ValueError(
            "resource_type must be a lowercase dotted or dashed identifier")
    return resource_type


def validate_resource_name(value: str) -> str:
    name = str(value or "").strip()
    if not RESOURCE_NAME_RE.fullmatch(name):
        raise ValueError("extension resource name is invalid")
    return name


def validate_schema_version(value: str) -> str:
    version = str(value or "").strip()
    if not SCHEMA_VERSION_RE.fullmatch(version):
        raise ValueError("schema_version is required and invalid")
    return version


def validate_json_schema(schema: Any) -> Dict[str, Any]:
    if not isinstance(schema, dict):
        raise ValueError("repository_type schema must contain a JSON object")
    if len(_canonical_json(schema)) > MAX_SCHEMA_BYTES:
        raise ValueError("repository_type schema exceeds the size limit")
    pending = [schema]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            ref = value.get("$ref")
            if ref is not None and (
                    not isinstance(ref, str) or not ref.startswith("#")):
                raise ValueError(
                    "repository_type schema references must be package-local fragments")
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    try:
        jsonschema.Draft202012Validator.check_schema(schema)
    except jsonschema.SchemaError as exc:
        raise ValueError(f"repository_type schema is invalid: {exc.message}") from exc
    return copy.deepcopy(schema)


def validate_document(document: Any, schema: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(document, dict):
        raise ValueError("repository_resource path must contain a JSON object")
    if len(_canonical_json(document)) > MAX_DOCUMENT_BYTES:
        raise ValueError("repository_resource document exceeds the size limit")
    try:
        jsonschema.Draft202012Validator(schema).validate(document)
    except jsonschema.ValidationError as exc:
        location = ".".join(str(part) for part in exc.absolute_path)
        prefix = f" at {location}" if location else ""
        raise ValueError(
            f"repository_resource document does not match its schema{prefix}: "
            f"{exc.message}") from exc
    return copy.deepcopy(document)


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, allow_nan=False,
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("extension repository values must be JSON-serializable") from exc


def _safe_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.@+-]", "_", str(value or "")) or "default"


class ExtensionRepository:
    """Atomic scoped CRUD for PFP-defined JSON resources."""

    _instance: Optional["ExtensionRepository"] = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._write_lock = threading.RLock()

    @classmethod
    def instance(cls) -> "ExtensionRepository":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        with cls._instance_lock:
            cls._instance = None

    def create(self, resource_type: str, name: str, *, user_id: str,
               scope: str, conversation_id: str = "", document: Dict[str, Any],
               schema_version: str, owner_package: str,
               contributor_package: str, assets: Optional[List[Dict[str, Any]]] = None,
               installed_from: Optional[Dict[str, Any]] = None,
               source: str = "package") -> Dict[str, Any]:
        path = self._resource_path(
            resource_type, name, user_id, scope, conversation_id)
        if path.exists():
            raise ValueError(
                f"extension resource {resource_type}/{name} already exists")
        now = time.time()
        entry = self._entry(
            resource_type, name, document, schema_version, owner_package,
            contributor_package, assets or [], installed_from or {}, source,
            created_at=now, updated_at=now)
        self._write(path, entry)
        return copy.deepcopy(entry)

    def get(self, resource_type: str, name: str, *, user_id: str,
            scope: str, conversation_id: str = "") -> Optional[Dict[str, Any]]:
        path = self._resource_path(
            resource_type, name, user_id, scope, conversation_id)
        return self._read(path)

    def list(self, resource_type: str, *, user_id: str, scope: str,
             conversation_id: str = "") -> List[Dict[str, Any]]:
        directory = self._type_dir(
            resource_type, user_id, scope, conversation_id)
        if not directory.exists():
            return []
        rows = []
        for path in sorted(directory.glob("*.json")):
            entry = self._read(path)
            if entry is not None:
                rows.append(entry)
        return rows

    def get_available(
            self, resource_type: str, name: str, *, user_id: str,
            conversation_id: str = "") -> Optional[Dict[str, Any]]:
        """Resolve conversation scope first, then the owning user's scope."""
        if conversation_id:
            result = self.get(
                resource_type, name, user_id=user_id, scope="conversation",
                conversation_id=conversation_id)
            if result is not None:
                result["_scope"] = "conversation"
                return result
        result = self.get(
            resource_type, name, user_id=user_id, scope="user")
        if result is not None:
            result["_scope"] = "user"
        return result

    def list_available(
            self, resource_type: str, *, user_id: str,
            conversation_id: str = "") -> List[Dict[str, Any]]:
        """List accessible resources without collapsing distinct scopes."""
        rows = []
        if conversation_id:
            for item in self.list(
                    resource_type, user_id=user_id, scope="conversation",
                    conversation_id=conversation_id):
                item["_scope"] = "conversation"
                rows.append(item)
        for item in self.list(
                resource_type, user_id=user_id, scope="user"):
            item["_scope"] = "user"
            rows.append(item)
        return rows

    def update(self, resource_type: str, name: str, *, user_id: str,
               scope: str, conversation_id: str = "",
               document: Dict[str, Any], schema_version: str,
               owner_package: str, contributor_package: str,
               assets: Optional[List[Dict[str, Any]]] = None,
               installed_from: Optional[Dict[str, Any]] = None,
               source: str = "user") -> Dict[str, Any]:
        path = self._resource_path(
            resource_type, name, user_id, scope, conversation_id)
        existing = self._read(path)
        if existing is None:
            raise KeyError(f"extension resource {resource_type}/{name} not found")
        entry = self._entry(
            resource_type, name, document, schema_version, owner_package,
            contributor_package,
            assets if assets is not None else list(existing.get("assets") or []),
            installed_from or {}, source,
            created_at=float(existing.get("created_at") or time.time()),
            updated_at=time.time())
        self._write(path, entry)
        return copy.deepcopy(entry)

    def delete(self, resource_type: str, name: str, *, user_id: str,
               scope: str, conversation_id: str = "") -> bool:
        path = self._resource_path(
            resource_type, name, user_id, scope, conversation_id)
        with self._write_lock:
            if not path.exists():
                return False
            path.unlink()
        return True

    def _entry(self, resource_type: str, name: str, document: Dict[str, Any],
               schema_version: str, owner_package: str,
               contributor_package: str, assets: List[Dict[str, Any]],
               installed_from: Dict[str, Any], source: str, *,
               created_at: float, updated_at: float) -> Dict[str, Any]:
        resource_type = validate_resource_type(resource_type)
        name = validate_resource_name(name)
        schema_version = validate_schema_version(schema_version)
        if not isinstance(document, dict):
            raise ValueError("extension resource document must be an object")
        _canonical_json(document)
        if len(_canonical_json(document)) > MAX_DOCUMENT_BYTES:
            raise ValueError("extension resource document exceeds the size limit")
        if source not in {"package", "user"}:
            raise ValueError("extension resource source must be package or user")
        if not owner_package or not contributor_package:
            raise ValueError("extension resource package ownership is required")
        if not isinstance(assets, list) or len(assets) > MAX_RESOURCE_ASSETS:
            raise ValueError("extension resource assets exceed the count limit")
        _canonical_json(assets)
        return {
            "format": "pawflow.extension-resource.v1",
            "resource_type": resource_type,
            "schema_version": schema_version,
            "name": name,
            "document": copy.deepcopy(document),
            "assets": copy.deepcopy(assets),
            "owner_package": str(owner_package),
            "contributor_package": str(contributor_package),
            "source": source,
            "installed_from": copy.deepcopy(installed_from),
            "created_at": created_at,
            "updated_at": updated_at,
        }

    def _type_dir(self, resource_type: str, user_id: str, scope: str,
                  conversation_id: str) -> Path:
        resource_type = validate_resource_type(resource_type)
        if not user_id:
            raise ValueError("user_id is required for extension resources")
        root = _paths.REPOSITORY_DIR / "extensions"
        if scope in {"conversation", "conv"}:
            if not conversation_id:
                raise ValueError(
                    "conversation_id is required for conversation extension resources")
            return (root / "conversations" / _safe_component(user_id)
                    / _safe_component(conversation_id) / resource_type)
        if scope != "user":
            raise ValueError("extension resource scope must be user or conversation")
        return root / "users" / _safe_component(user_id) / resource_type

    def _resource_path(self, resource_type: str, name: str, user_id: str,
                       scope: str, conversation_id: str) -> Path:
        return self._type_dir(
            resource_type, user_id, scope, conversation_id) / (
                validate_resource_name(name) + ".json")

    @staticmethod
    def _read(path: Path) -> Optional[Dict[str, Any]]:
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def _write(self, path: Path, value: Dict[str, Any]) -> None:
        data = _canonical_json(value) + b"\n"
        with self._write_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, path)
