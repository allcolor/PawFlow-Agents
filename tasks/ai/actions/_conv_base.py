"""AgentLoopTask actions — conversation"""

import json
import logging
import shutil
import time
from pathlib import Path
from typing import Dict, Any, Optional


logger = logging.getLogger(__name__)


# Sentinel: a cluster handler returns this when `action` is not one it owns,
# so the facade dispatcher falls through to the next cluster.
_UNHANDLED = object()


def _zip_rel_path(name: str, prefix: str = "") -> Optional[Path]:
    """Return a safe archive-relative path, optionally stripping prefix."""
    if prefix:
        if name == prefix.rstrip("/"):
            return None
        if not name.startswith(prefix):
            return None
        name = name[len(prefix):]
    if not name or name.endswith("/"):
        return None
    rel = Path(name)
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError(f"Unsafe archive path: {name}")
    if rel.parts and rel.parts[0] in (".git", "filestore"):
        return None
    if str(rel) == "manifest.json":
        return None
    return rel


def _archive_manifest(zf) -> Dict[str, Any]:
    if "manifest.json" not in zf.namelist():
        return {}
    try:
        return json.loads(zf.read("manifest.json").decode("utf-8"))
    except Exception:
        return {}


def _extract_conversation_members(zf, conv_dir: Path) -> None:
    """Extract conversation files while excluding manifest/FileStore payloads."""
    from core.segmented_jsonl import SegmentedJsonl

    names = zf.namelist()
    prefix = "conversation/" if any(n.startswith("conversation/") for n in names) else ""
    for name in names:
        rel = _zip_rel_path(name, prefix=prefix)
        if rel is None:
            continue
        dest = conv_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if rel.suffix == ".jsonl":
            text = zf.read(name).decode("utf-8", errors="replace")
            SegmentedJsonl(dest).replace_lines(
                line + "\n" for line in text.splitlines() if line.strip())
            continue
        with zf.open(name) as src, dest.open("wb") as out:
            shutil.copyfileobj(src, out, length=1024 * 1024)


def _patch_json_identity(obj: Any, cid: str, user_id: str,
                         file_id_map: Dict[str, str]) -> Any:
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            if key == "conversation_id":
                out[key] = cid
            elif key in ("user_id", "_meta_user_id"):
                out[key] = user_id
            else:
                out[key] = _patch_json_identity(value, cid, user_id, file_id_map)
        return out
    if isinstance(obj, list):
        return [_patch_json_identity(v, cid, user_id, file_id_map) for v in obj]
    if isinstance(obj, str) and file_id_map:
        text = obj
        for old_id, new_id in file_id_map.items():
            text = text.replace(old_id, new_id)
        return text
    return obj


def _patch_conversation_files(conv_dir: Path, cid: str, user_id: str,
                              file_id_map: Dict[str, str]) -> None:
    """Patch imported JSON/JSONL identity fields without rebuilding caches."""
    for path in sorted(conv_dir.rglob("*")):
        if not path.is_file() or ".git" in path.parts:
            continue
        if path.suffix == ".json":
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                logger.debug("Skipped non-JSON archive file %s: %s", path, exc)
            else:
                patched = _patch_json_identity(data, cid, user_id, file_id_map)
                path.write_text(json.dumps(patched, ensure_ascii=False, indent=2), encoding="utf-8")
        elif path.suffix == ".jsonl":
            lines = []
            changed = False
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    lines.append(line)
                    continue
                patched = _patch_json_identity(row, cid, user_id, file_id_map)
                lines.append(json.dumps(patched, ensure_ascii=False))
                changed = True
            if changed:
                path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _ensure_import_summarizer_binding(cid: str, user_id: str) -> Dict[str, str]:
    """Bind an available summarizer on imported conversations.

    Archives can carry a summarizer_binding from another install. If that
    explicit binding is unavailable locally, summarizer resolution stops there
    instead of falling back to user/global services. Normalize the imported
    conversation to the first locally available summarizer.
    """
    try:
        from core.summarizer_bindings import list_available, set_binding, summary
        current = summary(user_id, cid)
        effective = current.get("effective") or {}
        if current.get("explicit") and effective.get("service_id"):
            return {
                "scope": effective.get("scope", ""),
                "service_id": effective.get("service_id", ""),
            }
        available = list_available(user_id, cid)
        if not available:
            return {}
        chosen = available[0]
        scope = chosen.get("scope", "")
        service_id = chosen.get("service_id", "")
        if scope and service_id:
            set_binding(cid, scope, service_id)
            return {"scope": scope, "service_id": service_id}
    except Exception:
        logger.debug("failed to bind summarizer for imported conversation %s", cid[:8], exc_info=True)
    return {}


def _write_filestore_archive(zf, conv_id: str, user_id: str) -> Dict[str, Any]:
    """Write FileStore files for a conversation into an archive."""
    from core.file_store import FileStore
    fs = FileStore.instance()
    objects = []
    total_size = 0
    with fs._store_lock:  # FileStore has no export API yet.
        fs._ensure_loaded()
        entries = [(fid, dict(entry)) for fid, entry in fs._entries.items()
                   if entry.get("conversation_id") == conv_id
                   and entry.get("user_id") == user_id]
    for fid, entry in entries:
        src = Path(entry.get("path", ""))
        if not src.is_file():
            continue
        filename = Path(entry.get("filename") or src.name).name or "file"
        arc = f"filestore/objects/{fid}_{filename}"
        size = src.stat().st_size
        zf.write(src, arc)
        total_size += size
        objects.append({
            "file_id": fid,
            "filename": filename,
            "content_type": entry.get("content_type", "application/octet-stream"),
            "size": size,
            "created_at": entry.get("created_at", 0),
            "access": entry.get("access", "private"),
            "shared_with": entry.get("shared_with", []),
            "ttl": entry.get("ttl", 0),
            "agent_name": entry.get("agent_name", ""),
            "category": entry.get("category", ""),
            "path": f"objects/{fid}_{filename}",
        })
    zf.writestr("filestore/index.json", json.dumps(objects, ensure_ascii=False, indent=2))
    return {"included": True, "count": len(objects), "bytes": total_size}


def _restore_filestore_archive(zf, cid: str, user_id: str,
                               restore: bool,
                               file_id_policy: str = "preserve_or_remap"
                               ) -> Dict[str, Any]:
    """Restore archived FileStore entries and return an old->new id map."""
    if "filestore/index.json" not in zf.namelist():
        return {"restored": 0, "bytes": 0, "file_id_map": {}}
    if not restore:
        return {"restored": 0, "bytes": 0, "file_id_map": {}}
    import uuid as _uuid
    from core.file_store import FileStore
    try:
        entries = json.loads(zf.read("filestore/index.json").decode("utf-8"))
    except Exception as e:
        raise ValueError(f"Invalid FileStore index: {e}")
    if file_id_policy not in ("preserve", "remap", "preserve_or_remap"):
        raise ValueError("Invalid file_id_policy")
    fs = FileStore.instance()
    id_map: Dict[str, str] = {}
    total_size = 0
    restored = 0
    with fs._store_lock:
        fs._ensure_loaded()
        used = set(fs._entries.keys())
        for meta in entries:
            old_id = str(meta.get("file_id", ""))
            if not old_id:
                continue
            collision = old_id in used
            if file_id_policy == "preserve" and collision:
                raise ValueError(f"FileStore file_id collision: {old_id}")
            if file_id_policy == "remap" or collision:
                new_id = _uuid.uuid4().hex[:12]
                while new_id in used:
                    new_id = _uuid.uuid4().hex[:12]
            else:
                new_id = old_id
            used.add(new_id)
            if new_id != old_id:
                id_map[old_id] = new_id

            rel_obj = str(meta.get("path", ""))
            if not rel_obj or rel_obj.startswith("/") or ".." in Path(rel_obj).parts:
                raise ValueError(f"Unsafe FileStore object path: {rel_obj}")
            arc = "filestore/" + rel_obj
            if arc not in zf.namelist():
                raise ValueError(f"Missing FileStore object: {rel_obj}")
            filename = Path(meta.get("filename") or "file").name or "file"
            scope_dir = fs._scope_dir(user_id, cid)
            bucket = fs._pick_bucket(scope_dir)
            bucket_dir = scope_dir / bucket
            bucket_dir.mkdir(parents=True, exist_ok=True)
            disk_path = bucket_dir / f"{new_id}_{filename}"
            with zf.open(arc) as src, disk_path.open("wb") as out:
                shutil.copyfileobj(src, out, length=1024 * 1024)
            size = disk_path.stat().st_size
            total_size += size
            restored += 1
            fs._entries[new_id] = {
                "filename": filename,
                "path": str(disk_path),
                "content_type": meta.get("content_type", "application/octet-stream"),
                "size": size,
                "created_at": meta.get("created_at", time.time()),
                "conversation_id": cid,
                "user_id": user_id,
                "access": meta.get("access", "private"),
                "shared_with": meta.get("shared_with", []),
                "ttl": meta.get("ttl", 0),
                "agent_name": meta.get("agent_name", ""),
                "category": meta.get("category", ""),
            }
        fs._save_index()
    return {"restored": restored, "bytes": total_size, "file_id_map": id_map}
