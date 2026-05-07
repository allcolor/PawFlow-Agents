"""Repository-backed private gateway skins.

Private gateway skins are directory resources stored as:
    data/repository/private_gateway_skin/{global|users/<uid>|users/<uid>/<conv_id>}/<name>/

Each skin directory contains skin.json metadata and template.html. Optional
asset files may be added later by plugins; this module currently renders the
HTML template directly with the private gateway context.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

SKIN_RTYPE = "private_gateway_skin"
SKIN_META = "skin.json"
SKIN_TEMPLATE = "template.html"
DEFAULT_SKIN = "matrix"
FALLBACK_SKIN = "default"


_LEGACY_PLACEHOLDERS = {
    "%(next_url)s": "next_url",
    "%(error)s": "error",
    "%(cooldown)d": "cooldown",
    "%(cooldown)s": "cooldown",
}
_BRACE_PLACEHOLDER_RE = re.compile(r"\{\{\s*(next_url|error|cooldown)\s*\}\}")


def _repo_scope(scope: str) -> str:
    if scope == "conversation":
        return "conv"
    if scope in ("global", "user", "conv"):
        return scope
    raise ValueError(f"Invalid private gateway skin scope: {scope}")


def _public_scope(scope: str) -> str:
    return "conversation" if scope == "conv" else scope


def _scope_dir(scope: str, user_id: str = "", conversation_id: str = "") -> Path:
    from core.paths import repo_dir

    return repo_dir(SKIN_RTYPE, _repo_scope(scope), user_id, conversation_id)


def _skin_dir(scope: str, name: str, user_id: str = "", conversation_id: str = "") -> Path:
    return _scope_dir(scope, user_id, conversation_id) / name


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def _read_skin(path: Path, scope: str) -> Optional[Dict[str, Any]]:
    if not path.is_dir():
        return None
    template_path = path / SKIN_TEMPLATE
    if not template_path.is_file():
        return None
    meta = _read_json(path / SKIN_META)
    name = str(meta.get("name") or path.name)
    template = template_path.read_text(encoding="utf-8", errors="replace")
    out = dict(meta)
    out.update({
        "name": name,
        "title": meta.get("title") or name,
        "description": meta.get("description", ""),
        "scope": _public_scope(scope),
        "ref": f"{_public_scope(scope)}:{name}",
        "template": template,
        "template_length": len(template),
    })
    return out


def _list_scope(scope: str, user_id: str = "", conversation_id: str = "") -> List[Dict[str, Any]]:
    directory = _scope_dir(scope, user_id, conversation_id)
    if not directory.exists():
        return []
    skins = []
    for skin_path in sorted(p for p in directory.iterdir() if p.is_dir()):
        item = _read_skin(skin_path, _repo_scope(scope))
        if item:
            item["_scope"] = item["scope"]
            skins.append(item)
    return skins


def list_skins(user_id: str = "", conversation_id: str = "") -> List[Dict[str, Any]]:
    skins = _list_scope("global")
    seen = {skin.get("name") for skin in skins}
    if user_id:
        for item in _list_scope("user", user_id):
            if item.get("name") not in seen:
                skins.append(item)
                seen.add(item.get("name"))
    if user_id and conversation_id:
        for item in _list_scope("conversation", user_id, conversation_id):
            if item.get("name") not in seen:
                skins.append(item)
                seen.add(item.get("name"))
    skins.sort(key=lambda skin: (skin.get("scope", ""), skin.get("title", "").lower()))
    return skins


def _scope_lookup(scope: str, name: str, user_id: str = "", conversation_id: str = "") -> Optional[Dict[str, Any]]:
    if scope == "global":
        return _read_skin(_skin_dir("global", name), "global")
    if scope == "user":
        return _read_skin(_skin_dir("user", name, user_id), "user")
    if scope == "conversation":
        return _read_skin(_skin_dir("conversation", name, user_id, conversation_id), "conv")
    return None


def resolve_skin(ref: str, user_id: str = "", conversation_id: str = "") -> Optional[Dict[str, Any]]:
    ref = (ref or DEFAULT_SKIN).strip().lower()
    if not ref:
        ref = DEFAULT_SKIN
    if ":" in ref:
        scope, name = ref.split(":", 1)
        return _scope_lookup(scope, name, user_id, conversation_id)

    if user_id and conversation_id:
        skin = _scope_lookup("conversation", ref, user_id, conversation_id)
        if skin:
            return skin
    if user_id:
        skin = _scope_lookup("user", ref, user_id, conversation_id)
        if skin:
            return skin
    return _scope_lookup("global", ref, user_id, conversation_id)


def _context(error: str = "", cooldown: int = 0, next_url: str = "/") -> Dict[str, str]:
    return {
        "error": html.escape(str(error or ""), quote=True),
        "cooldown": str(max(0, int(cooldown or 0))),
        "next_url": html.escape(str(next_url or "/"), quote=True),
    }


def render_template(template: str, error: str = "", cooldown: int = 0, next_url: str = "/") -> str:
    ctx = _context(error=error, cooldown=cooldown, next_url=next_url)
    out = template
    for placeholder, key in _LEGACY_PLACEHOLDERS.items():
        out = out.replace(placeholder, ctx[key])
    return _BRACE_PLACEHOLDER_RE.sub(lambda m: ctx[m.group(1)], out)


def render_skin(ref: str, error: str = "", cooldown: int = 0, next_url: str = "/",
                user_id: str = "", conversation_id: str = "") -> str:
    skin = resolve_skin(ref, user_id=user_id, conversation_id=conversation_id)
    if skin is None and ref != FALLBACK_SKIN:
        skin = resolve_skin(FALLBACK_SKIN, user_id=user_id, conversation_id=conversation_id)
    if skin is None:
        raise FileNotFoundError("No private gateway skin template found")
    return render_template(
        skin.get("template", ""),
        error=error,
        cooldown=cooldown,
        next_url=next_url,
    )


def failure_redirect(ref: str, submitted: str, user_id: str = "",
                     conversation_id: str = "") -> str:
    skin = resolve_skin(ref, user_id=user_id, conversation_id=conversation_id)
    mode = str((skin or {}).get("failure_redirect") or "").strip()
    if not mode:
        return ""
    if mode == "google_search":
        from urllib.parse import quote
        return f"https://www.google.com/search?q={quote(submitted)}"
    if mode == "bing_search":
        from urllib.parse import quote
        return f"https://www.bing.com/search?q={quote(submitted)}"
    if mode.startswith(("http://", "https://", "/")):
        return mode
    return ""
