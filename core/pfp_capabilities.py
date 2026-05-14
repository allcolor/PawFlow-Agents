"""Capability checks for PawFlow Package runtime calls."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict

import core.paths as _paths


_PACKAGE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,120}[a-z0-9]$")


class PackageCapabilityError(PermissionError):
    """Raised when a package runtime call is not covered by install grants."""


class PackageCapabilityBroker:
    """Authorizes package tool/service calls from installed PFP objects."""

    def __init__(self, *, user_id: str, conversation_id: str = "", scope: str = "user"):
        self.user_id = user_id
        self.conversation_id = conversation_id
        self.scope = "conversation" if scope in {"conv", "conversation"} else "user"

    def authorize_tool_call(self, caller_runtime: Dict[str, Any], tool_ref: str) -> Dict[str, Any]:
        return self._authorize(caller_runtime, tool_ref, "tool", "allowed_tools")

    def authorize_service_call(self, caller_runtime: Dict[str, Any], service_ref: str) -> Dict[str, Any]:
        return self._authorize(caller_runtime, service_ref, "service", "allowed_services")

    def _authorize(self, caller_runtime: Dict[str, Any], target_ref: str,
                   target_kind: str, grant_field: str) -> Dict[str, Any]:
        caller_package = str((caller_runtime or {}).get("package") or "")
        caller_object = str((caller_runtime or {}).get("object_id") or "")
        if not caller_package or not caller_object:
            raise PackageCapabilityError("caller package runtime identity is required")
        target = _parse_target_ref(target_ref, target_kind)
        grants = caller_runtime.get(grant_field) or []
        for grant in grants:
            parsed_grant = _parse_grant(grant, target_kind)
            if _grant_matches(parsed_grant, target):
                authorized_target = dict(target)
                if target.get("package"):
                    version_constraint = target.get("version", "") or parsed_grant.get("version", "")
                    self._require_installed_package(
                        target["package"], version_constraint,
                        f"{target['kind']}:{target['name']}")
                    if version_constraint:
                        authorized_target["version"] = version_constraint
                return {
                    "ok": True,
                    "caller_package": caller_package,
                    "caller_object": caller_object,
                    "target": authorized_target,
                    "grant": grant,
                }
        raise PackageCapabilityError(
            f"{caller_package}:{caller_object} is not allowed to call {target_ref}")

    def _require_installed_package(self, package_id: str, version: str = "",
                                   object_id: str = "") -> None:
        installed = _installed_package_records(
            self.user_id, self.conversation_id, self.scope)
        record = installed.get(package_id, {})
        installed_version = str(record.get("version") or "")
        if not installed_version:
            raise PackageCapabilityError(f"package dependency is not installed: {package_id}")
        if version and not _version_satisfies(installed_version, version):
            raise PackageCapabilityError(
                f"package dependency version mismatch: {package_id}@{version}")
        installed_objects = set(record.get("objects") or [])
        accepted_objects = {object_id}
        if object_id.startswith("service:"):
            accepted_objects.add("service_provider:" + object_id.split(":", 1)[1])
        if object_id and not accepted_objects.intersection(installed_objects):
            raise PackageCapabilityError(
                f"package dependency object is not installed: {package_id}/{object_id}")
        if version and object_id:
            object_versions = record.get("object_versions") or {}
            matching_versions = [
                str(object_versions.get(obj_id) or "")
                for obj_id in accepted_objects
                if obj_id in installed_objects
            ]
            if matching_versions and not any(
                    _version_satisfies(candidate, version)
                    for candidate in matching_versions):
                raise PackageCapabilityError(
                    f"package dependency object version mismatch: {package_id}@{version}/{object_id}")


def _parse_target_ref(ref: str, expected_kind: str) -> Dict[str, str]:
    value = str(ref or "").strip()
    if not value:
        raise PackageCapabilityError("target reference is required")
    if "/" not in value:
        return {"kind": expected_kind, "name": value, "package": "", "version": ""}
    package_ref, object_ref = value.split("/", 1)
    package_ref = package_ref.removeprefix("package:")
    package_id, version = _split_package_ref(package_ref)
    kind, name = _split_object_ref(object_ref)
    if kind != expected_kind:
        raise PackageCapabilityError(f"expected {expected_kind} reference, got {kind}")
    return {"kind": kind, "name": name, "package": package_id, "version": version}


def _grant_matches(parsed: Dict[str, str], target: Dict[str, str]) -> bool:
    if not parsed:
        return False
    for key in ("kind", "name", "package"):
        if parsed.get(key, "") != target.get(key, ""):
            return False
    if target.get("version") and parsed.get("version"):
        return _version_satisfies(target["version"], parsed["version"])
    return True


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


def _version_part_satisfies(version_tuple: tuple[int, ...], constraint: str) -> bool:
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


def _version_tuple(value: str) -> tuple[int, ...] | None:
    raw = str(value or "").strip().removeprefix("v")
    if not raw:
        return None
    raw = re.split(r"[-+]", raw, 1)[0]
    parts = raw.split(".")
    if not all(part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def _compare_versions(left: tuple[int, ...], right: tuple[int, ...]) -> int:
    width = max(len(left), len(right), 3)
    a = left + (0,) * (width - len(left))
    b = right + (0,) * (width - len(right))
    return (a > b) - (a < b)


def _caret_upper_bound(base: tuple[int, ...]) -> tuple[int, ...]:
    major = base[0] if len(base) > 0 else 0
    minor = base[1] if len(base) > 1 else 0
    patch = base[2] if len(base) > 2 else 0
    if major > 0:
        return (major + 1, 0, 0)
    if minor > 0:
        return (0, minor + 1, 0)
    return (0, 0, patch + 1)


def _tilde_upper_bound(base: tuple[int, ...]) -> tuple[int, ...]:
    major = base[0] if len(base) > 0 else 0
    minor = base[1] if len(base) > 1 else 0
    if len(base) == 1:
        return (major + 1, 0, 0)
    return (major, minor + 1, 0)


def _parse_grant(grant: Any, expected_kind: str) -> Dict[str, str]:
    if isinstance(grant, str):
        value = grant.strip()
        if not value:
            return {}
        return _parse_target_ref(value, expected_kind)
    if not isinstance(grant, dict):
        return {}
    package_id = str(grant.get("package") or "").strip()
    object_ref = str(grant.get("object") or "").strip()
    name = str(grant.get("name") or "").strip()
    version = str(grant.get("version") or grant.get("constraint") or "").strip()
    if package_id:
        package_id, inline_version = _split_package_ref(package_id)
        version = version or inline_version
        kind, obj_name = _split_object_ref(object_ref)
        if kind != expected_kind:
            return {}
        return {"kind": kind, "name": obj_name, "package": package_id, "version": version}
    if name:
        return {"kind": expected_kind, "name": name, "package": "", "version": ""}
    return {}


def _split_package_ref(value: str) -> tuple[str, str]:
    if "@" in value:
        package_id, version = value.split("@", 1)
    else:
        package_id, version = value, ""
    if not _PACKAGE_ID_RE.match(package_id):
        raise PackageCapabilityError(f"invalid package reference: {value}")
    return package_id, version


def _split_object_ref(value: str) -> tuple[str, str]:
    if ":" not in value:
        raise PackageCapabilityError("package capability object must be type:name")
    kind, name = value.split(":", 1)
    if not kind or not name:
        raise PackageCapabilityError("package capability object must be type:name")
    return kind, name


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
                record = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
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


def _install_scope_dir(user_id: str, conversation_id: str, scope: str) -> Path:
    root = _paths.REPOSITORY_DIR / "packages"
    if scope == "conversation":
        return root / "conversations" / _safe_component(user_id) / _safe_component(conversation_id)
    return root / "users" / _safe_component(user_id)


def _safe_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.@+-]", "_", str(value or "")) or "default"
