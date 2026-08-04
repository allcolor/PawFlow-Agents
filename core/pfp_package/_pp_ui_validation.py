"""Validation for installable PFP UI extension objects."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from core.pfp_package._pp_base import (
    _UI_API_VERSION,
    _UI_ASSET_EXTENSIONS,
    _UI_ASSET_ID_RE,
    _UI_EXECUTABLE_ASSET_MAX_BYTES,
    _UI_EXTENSION_ASSET_MAX_BYTES,
    _UI_EXTENSION_ASSET_MAX_COUNT,
    _UI_HANDLER_ACTION_RE,
    _UI_INERT_ASSET_EXTENSIONS,
    _UI_INERT_ASSET_MAX_BYTES,
    _UI_KNOWN_HOOKS,
    _UI_KNOWN_SLOTS,
)
from core.pfp_package._pp_mod1 import _safe_relpath, _ui_extension_asset_list


def _validate_ui_extension_object(
        obj: Dict[str, Any], package: Dict[str, Any]) -> str:
    """Return an empty string when the UI extension is structurally valid."""
    if str(obj.get("version_compat") or "") != _UI_API_VERSION:
        return f"ui_extension requires version_compat == {_UI_API_VERSION!r}"
    assets = obj.get("assets")
    if not isinstance(assets, dict):
        return "ui_extension.assets must be an object with scripts/styles"
    for key in ("scripts", "styles"):
        if key in assets and not isinstance(assets[key], list):
            return f"ui_extension.assets.{key} must be an array"
    if "i18n" in assets and not isinstance(assets["i18n"], dict):
        return "ui_extension.assets.i18n must be an object"
    raw_files = assets.get("files", [])
    if not isinstance(raw_files, list):
        return "ui_extension.assets.files must be an array"
    for entry in raw_files:
        if isinstance(entry, str):
            if not entry.strip():
                return "ui_extension.assets.files paths must not be empty"
            continue
        if not isinstance(entry, dict):
            return "ui_extension.assets.files entries must be paths or objects"
        if not str(entry.get("id") or "").strip():
            return "ui_extension.assets.files object entries require an id"
        if not str(entry.get("path") or "").strip():
            return "ui_extension.assets.files object entries require a path"
    rows = _ui_extension_asset_list(obj)
    if not rows or not any(row["kind"] == "script" for row in rows):
        return "ui_extension must declare at least one script"
    if len(rows) > _UI_EXTENSION_ASSET_MAX_COUNT:
        return ("ui_extension declares too many assets: "
                f"{len(rows)} > {_UI_EXTENSION_ASSET_MAX_COUNT}")

    files = package.get("files") or {}
    seen_paths = set()
    seen_file_ids = set()
    total_size = 0
    for row in rows:
        rel = _safe_relpath(row["path"])
        if rel in seen_paths:
            return f"ui_extension.assets: duplicate path {row['path']!r}"
        seen_paths.add(rel)
        if rel not in files:
            return f"ui_extension asset is missing in package: {row['path']}"
        ext = Path(rel).suffix.lower()
        kind = row["kind"]
        if kind == "script" and ext != ".js":
            return f"ui_extension script must be a .js file: {row['path']}"
        if kind == "style" and ext != ".css":
            return f"ui_extension style must be a .css file: {row['path']}"
        if kind == "i18n" and ext != ".json":
            return f"ui_extension i18n catalog must be a .json file: {row['path']}"
        if kind == "file" and ext not in _UI_INERT_ASSET_EXTENSIONS:
            return f"ui_extension asset extension is not allowed: {row['path']}"
        if ext not in _UI_ASSET_EXTENSIONS:
            return f"ui_extension asset extension is not allowed: {row['path']}"
        if kind == "file":
            asset_id = str(row.get("id") or "")
            if asset_id:
                if not _UI_ASSET_ID_RE.fullmatch(asset_id):
                    return f"ui_extension file asset has invalid id: {asset_id!r}"
                if asset_id in seen_file_ids:
                    return f"ui_extension file asset has duplicate id: {asset_id!r}"
                seen_file_ids.add(asset_id)
        size = len(files[rel])
        max_size = (_UI_INERT_ASSET_MAX_BYTES if kind == "file"
                    else _UI_EXECUTABLE_ASSET_MAX_BYTES)
        if size > max_size:
            return (f"ui_extension asset is too large: {row['path']} "
                    f"({size} > {max_size} bytes)")
        total_size += size
    if total_size > _UI_EXTENSION_ASSET_MAX_BYTES:
        return ("ui_extension assets are too large in total: "
                f"{total_size} > {_UI_EXTENSION_ASSET_MAX_BYTES} bytes")

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
            return (f"ui_extension.slots: duplicate id {slot_id!r} "
                    f"in slot {slot_name!r}")
        seen_ids.add(key)
    hooks = obj.get("hooks") if isinstance(obj.get("hooks"), list) else []
    for hook in hooks:
        if str(hook) not in _UI_KNOWN_HOOKS:
            return f"ui_extension.hooks: unknown hook {hook!r}"

    handlers = obj.get("handlers") if isinstance(obj.get("handlers"), list) else []
    seen_actions = set()
    for entry in handlers:
        if not isinstance(entry, dict):
            return "ui_extension.handlers entries must be objects"
        action = str(entry.get("action") or "").strip()
        if not action or not _UI_HANDLER_ACTION_RE.match(action):
            return f"ui_extension.handlers: invalid action {action!r}"
        if action in seen_actions:
            return f"ui_extension.handlers: duplicate action {action!r}"
        seen_actions.add(action)
        runner = str(entry.get("runner") or "")
        if runner != "python":
            return ("ui_extension.handlers: only 'python' runner is supported "
                    f"(got {runner!r})")
        path = str(entry.get("path") or "").strip()
        if not path:
            return f"ui_extension.handlers[{action}]: path is required"
        rel = _safe_relpath(path)
        if rel not in files:
            return f"ui_extension.handlers[{action}]: missing package file {path!r}"
        if Path(rel).suffix.lower() != ".py":
            return f"ui_extension.handlers[{action}]: handler must be a .py file"
    return ""
