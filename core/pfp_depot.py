"""Permanent per-user storage for signed PawFlow Package artifacts.

The depot is distinct from installed package records: adding a package only
makes the verified artifact available for later inspection and installation.
Bundled artifacts are listed alongside user uploads but remain read-only.
"""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List

import core.paths as _paths

MAX_DEPOT_PACKAGE_BYTES = 100 * 1024 * 1024
_DEPOT_REF_PREFIX = "depot:"
_SAFE_COMPONENT_RE = re.compile(r"[^A-Za-z0-9_.@+-]")


class PfpDepotError(ValueError):
    """Raised for invalid or unauthorized PFP depot operations."""


def list_packages(*, user_id: str) -> Dict[str, Any]:
    """List bundled packages and valid artifacts uploaded by user_id."""
    _require_user_id(user_id)
    from core import pfp_package, pfp_registry

    packages: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []

    try:
        for row in pfp_registry.list_bundled_packages():
            packages.append({
                **row,
                "source": "bundled",
                "deletable": False,
            })
    except Exception as exc:
        errors.append({"source": "bundled", "error": str(exc)})

    root = _depot_dir(user_id)
    if root.exists():
        for path in sorted(root.glob("*.pfp")):
            try:
                plan = pfp_package.inspect_pfp(str(path), user_id=user_id)
                packages.append(_uploaded_row(path, plan))
            except Exception as exc:
                errors.append({"source": "uploaded", "name": path.name,
                               "error": str(exc)})

    return {
        "ok": True,
        "packages": packages,
        "errors": errors,
    }


def add_upload(file_id: str, *, user_id: str) -> Dict[str, Any]:
    """Validate one FileStore upload and atomically add it to the user depot."""
    _require_user_id(user_id)
    file_id = str(file_id or "").strip()
    if not file_id:
        raise PfpDepotError("file_id is required")

    from core.file_store import FileStore
    uploaded = FileStore.instance().get(file_id, user_id=user_id)
    if uploaded is None:
        raise PfpDepotError("Uploaded file not found")
    filename, content, _content_type = uploaded
    if Path(filename).suffix.lower() != ".pfp":
        raise PfpDepotError("Depot uploads must use the .pfp extension")
    if not content:
        raise PfpDepotError("Package upload is empty")
    if len(content) > MAX_DEPOT_PACKAGE_BYTES:
        raise PfpDepotError(
            f"Package exceeds the {MAX_DEPOT_PACKAGE_BYTES}-byte depot limit")

    from core import pfp_package

    root = _depot_dir(user_id)
    root.mkdir(parents=True, exist_ok=True)
    temporary = root / f".upload-{uuid.uuid4().hex}.pfp"
    try:
        temporary.write_bytes(content)
        try:
            plan = pfp_package.inspect_pfp(str(temporary), user_id=user_id)
        except Exception as exc:
            raise PfpDepotError(f"Invalid PFP package: {exc}") from exc
        digest = str(plan.get("sha256") or "").removeprefix("sha256:")
        package = _safe_component(plan.get("package") or "package")
        version = _safe_component(plan.get("version") or "version")
        target = root / f"{package}-{version}-{digest[:16]}.pfp"

        for existing in root.glob("*.pfp"):
            if existing == temporary:
                continue
            try:
                existing_plan = pfp_package.inspect_pfp(
                    str(existing), user_id=user_id)
            except Exception:
                continue
            if (existing_plan.get("package"), existing_plan.get("version")) != (
                    plan.get("package"), plan.get("version")):
                continue
            if existing_plan.get("sha256") == plan.get("sha256"):
                return {
                    "ok": True,
                    "already_present": True,
                    "package": _uploaded_row(existing, existing_plan),
                }
            raise PfpDepotError(
                f"{plan['package']}@{plan['version']} already exists in the depot "
                "with different contents")

        os.replace(temporary, target)
        return {
            "ok": True,
            "already_present": False,
            "package": _uploaded_row(target, plan),
        }
    finally:
        temporary.unlink(missing_ok=True)


def delete_package(depot_id: str, *, user_id: str) -> Dict[str, Any]:
    """Delete one artifact from the authenticated user upload depot."""
    _require_user_id(user_id)
    name = _depot_name(depot_id)
    target = _depot_dir(user_id) / name
    if not target.is_file():
        raise PfpDepotError("Depot package not found")
    target.unlink()
    return {"ok": True, "deleted": True, "depot_id": name}


def resolve_ref(ref: str, *, user_id: str) -> Dict[str, Any]:
    """Resolve an opaque user-depot ref to a verified local artifact."""
    value = str(ref or "").strip()
    if not value.startswith(_DEPOT_REF_PREFIX):
        return {}
    _require_user_id(user_id)
    name = _depot_name(value)
    path = _depot_dir(user_id) / name
    if not path.is_file():
        raise PfpDepotError("Depot package not found")

    from core import pfp_package
    plan = pfp_package.inspect_pfp(str(path), user_id=user_id)
    return {
        "path": str(path),
        "downloaded": False,
        "sha256": plan.get("sha256", ""),
        "url": "",
        "source": "depot",
    }


def _uploaded_row(path: Path, plan: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "depot_id": path.name,
        "ref": _DEPOT_REF_PREFIX + path.name,
        "source": "uploaded",
        "deletable": True,
        "package": plan.get("package", ""),
        "version": plan.get("version", ""),
        "description": plan.get("description", ""),
        "sha256": plan.get("sha256", ""),
        "package_size": plan.get("package_size", 0),
        "content_size": plan.get("content_size", 0),
        "file_count": plan.get("file_count", 0),
        "verified": bool(plan.get("verified")),
        "risk": plan.get("risk", "low"),
        "objects": [
            {"id": row.get("id", ""), "type": row.get("type", "")}
            for row in plan.get("objects") or []
        ],
    }


def _depot_name(value: str) -> str:
    text = str(value or "").strip()
    if text.startswith(_DEPOT_REF_PREFIX):
        text = text[len(_DEPOT_REF_PREFIX):]
    if not text or Path(text).name != text or not text.endswith(".pfp"):
        raise PfpDepotError("Invalid depot package identifier")
    return text


def _depot_dir(user_id: str) -> Path:
    return (
        _paths.REPOSITORY_DIR
        / "packages"
        / "depot"
        / _safe_component(user_id)
    )


def _safe_component(value: Any) -> str:
    return _SAFE_COMPONENT_RE.sub("_", str(value or "")) or "default"


def _require_user_id(user_id: str) -> None:
    if not str(user_id or "").strip():
        raise PfpDepotError("user_id is required")
