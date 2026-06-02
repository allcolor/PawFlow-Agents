"""Chat UI theme repository helpers.

Themes are repository resources stored as directories:
    data/repository/theme/{global|users/<uid>|users/<uid>/<conv_id>}/<name>/

Each theme directory contains theme.json metadata, one or more CSS files, and
optional assets referenced by those CSS files.
"""

from __future__ import annotations

import base64
import io
import json
import mimetypes
import posixpath
import re
import shutil
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

THEME_RTYPE = "theme"
THEME_META = "theme.json"
DEFAULT_THEME_REF = "global:pawflow_dark"


def _ref(scope: str, name: str) -> str:
    return f"{scope}:{name}"


def _repo_scope(scope: str) -> str:
    if scope == "conversation":
        return "conv"
    if scope in ("global", "user", "conv"):
        return scope
    raise ValueError(f"Invalid theme scope: {scope}")


def _public_scope(scope: str) -> str:
    return "conversation" if scope == "conv" else scope


def _scope_dir(scope: str, user_id: str = "", conversation_id: str = "") -> Path:
    from core.paths import repo_dir

    repo_scope = _repo_scope(scope)
    return repo_dir(THEME_RTYPE, repo_scope, user_id, conversation_id)


def _theme_dir(scope: str, name: str, user_id: str = "", conversation_id: str = "") -> Path:
    return _scope_dir(scope, user_id, conversation_id) / name


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def _write_json(path: Path, data: Dict[str, Any]):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _safe_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", (name or "").strip())
    cleaned = cleaned.strip("._")
    if not cleaned:
        raise ValueError("Missing theme name")
    return cleaned


def _safe_relpath(path: str) -> Optional[str]:
    clean = posixpath.normpath((path or "").replace("\\", "/")).lstrip("/")
    if not clean or clean == "." or clean.startswith("../") or "/../" in clean:
        return None
    if clean == THEME_META:
        return None
    return clean


def _decode_upload(upload: Dict[str, Any]) -> bytes:
    raw = upload.get("base64", "") if isinstance(upload, dict) else ""
    if "," in raw:
        raw = raw.split(",", 1)[1]
    return base64.b64decode(raw or "")


def _asset_data_url(path: str, data: bytes) -> str:
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _rewrite_css_urls(css: str, css_path: str, files: Dict[str, bytes]) -> str:
    base_dir = posixpath.dirname(css_path)

    def repl(match):
        raw = match.group(1).strip().strip('"\'')
        low = raw.lower()
        if not raw or low.startswith(("data:", "http://", "https://", "#")):
            return match.group(0)
        target = posixpath.normpath(posixpath.join(base_dir, raw))
        data = files.get(target)
        if data is None:
            data = files.get(raw)
        if data is None:
            return match.group(0)
        return "url(" + _asset_data_url(target, data) + ")"

    return re.sub(r"url\(([^)]+)\)", repl, css)


def _files_from_upload(upload: Dict[str, Any]) -> Dict[str, bytes]:
    if not upload:
        return {}
    filename = _safe_relpath((upload.get("filename") or "theme.css").strip()) or "theme.css"
    data = _decode_upload(upload)
    if not data:
        return {}
    if filename.lower().endswith(".zip"):
        out: Dict[str, bytes] = {}
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                path = _safe_relpath(info.filename)
                if path:
                    out[path] = zf.read(info)
        return out
    return {filename: data}


def css_from_upload(upload: Dict[str, Any]) -> str:
    """Return uploaded CSS with relative assets inlined for preview/tests."""
    files = _files_from_upload(upload)
    css_parts = []
    for path in sorted(p for p in files if p.lower().endswith(".css")):
        text = files[path].decode("utf-8", errors="replace")
        css_parts.append(f"/* {path} */\n" + _rewrite_css_urls(text, path, files))
    return "\n\n".join(css_parts)


def _css_from_theme_dir(theme_dir: Path) -> str:
    files: Dict[str, bytes] = {}
    for path in sorted(p for p in theme_dir.rglob("*") if p.is_file() and p.name != THEME_META):
        rel = path.relative_to(theme_dir).as_posix()
        files[rel] = path.read_bytes()
    css_parts = []
    for rel in sorted(p for p in files if p.lower().endswith(".css")):
        text = files[rel].decode("utf-8", errors="replace")
        css_parts.append(f"/* {rel} */\n" + _rewrite_css_urls(text, rel, files))
    return "\n\n".join(css_parts)


def _css_length_from_theme_dir(theme_dir: Path) -> int:
    total = 0
    for path in theme_dir.rglob("*.css"):
        if path.is_file():
            try:
                total += path.stat().st_size
            except OSError:
                pass
    return total


def _read_theme(theme_dir: Path, scope: str, *, include_css: bool = True) -> Optional[Dict[str, Any]]:
    if not theme_dir.is_dir():
        return None
    if not (theme_dir / THEME_META).is_file():
        return None
    meta = _read_json(theme_dir / THEME_META)
    name = meta.get("name") or theme_dir.name
    css = _css_from_theme_dir(theme_dir) if include_css else ""
    out = dict(meta)
    out.update({
        "name": name,
        "title": meta.get("title") or name,
        "description": meta.get("description", ""),
        "scope": _public_scope(scope),
        "ref": _ref(_public_scope(scope), name),
        "builtin": False,
        "css": css,
        "css_length": len(css) if include_css else _css_length_from_theme_dir(theme_dir),
    })
    return out


def _list_scope(scope: str, user_id: str = "", conversation_id: str = "", *,
                include_css: bool = True) -> List[Dict[str, Any]]:
    directory = _scope_dir(scope, user_id, conversation_id)
    if not directory.exists():
        return []
    items = []
    for theme_path in sorted(p for p in directory.iterdir() if p.is_dir()):
        item = _read_theme(theme_path, _repo_scope(scope), include_css=include_css)
        if item:
            item["_scope"] = item["scope"]
            items.append(item)
    return items


def list_themes(user_id: str, conversation_id: str = "", *,
                include_css: bool = True) -> List[Dict[str, Any]]:
    if user_id == "__global__":
        themes = _list_scope("global", include_css=include_css)
    else:
        themes = _list_scope("user", user_id, include_css=include_css)
        seen = {t.get("name") for t in themes}
        for item in _list_scope("global", include_css=include_css):
            if item.get("name") not in seen:
                themes.append(item)
                seen.add(item.get("name"))
        if conversation_id:
            for item in _list_scope("conversation", user_id, conversation_id,
                                    include_css=include_css):
                if item.get("name") not in seen:
                    themes.append(item)
                    seen.add(item.get("name"))
    themes.sort(key=lambda t: (t.get("scope", ""), t.get("title", "").lower()))
    return themes


def _scope_lookup(scope: str, name: str, user_id: str, conversation_id: str) -> Optional[Dict[str, Any]]:
    if scope == "global":
        return _read_theme(_theme_dir("global", name), "global")
    if scope == "user":
        return _read_theme(_theme_dir("user", name, user_id), "user")
    if scope == "conversation":
        return _read_theme(_theme_dir("conversation", name, user_id, conversation_id), "conv")
    return None


def resolve_theme(ref: str, user_id: str, conversation_id: str = "") -> Optional[Dict[str, Any]]:
    if not ref:
        ref = DEFAULT_THEME_REF
    if ":" in ref:
        scope, name = ref.split(":", 1)
    else:
        scope, name = "global", ref
    if scope == "conversation" and not conversation_id:
        return None
    return _scope_lookup(scope, name, user_id, conversation_id)


def create_theme(name: str, scope: str, user_id: str, conversation_id: str,
                 title: str = "", description: str = "", css: str = "",
                 upload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    safe_name = _safe_name(name)
    target_dir = _theme_dir(scope, safe_name, user_id, conversation_id)
    if target_dir.exists():
        raise ValueError(f"theme/{safe_name} already exists in scope {scope}")
    files = _files_from_upload(upload or {})
    if css.strip():
        files.setdefault("theme.css", css.encode("utf-8"))
    if not any(path.lower().endswith(".css") for path in files):
        raise ValueError("Theme CSS or CSS/ZIP upload is required")
    target_dir.mkdir(parents=True, exist_ok=False)
    for rel, data in files.items():
        safe_rel = _safe_relpath(rel)
        if not safe_rel:
            continue
        out = target_dir / safe_rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
    now = time.time()
    meta = {
        "name": safe_name,
        "title": title or safe_name,
        "description": description,
        "source_filename": (upload or {}).get("filename", ""),
        "created_at": now,
        "updated_at": now,
    }
    _write_json(target_dir / THEME_META, meta)
    created = _read_theme(target_dir, _repo_scope(scope))
    if not created:
        raise ValueError("Theme creation failed")
    return created


def delete_theme(ref: str, user_id: str, conversation_id: str = "") -> bool:
    if not ref or ":" not in ref:
        raise ValueError("theme ref is required")
    scope, name = ref.split(":", 1)
    if scope == "global":
        target = _theme_dir("global", name)
    elif scope == "user":
        target = _theme_dir("user", name, user_id)
    elif scope == "conversation":
        target = _theme_dir("conversation", name, user_id, conversation_id)
    else:
        raise ValueError(f"Invalid theme scope: {scope}")
    if not target.exists():
        return False
    shutil.rmtree(target)
    return True


def theme_json(theme: Dict[str, Any]) -> str:
    return json.dumps(theme, ensure_ascii=False)
