"""AgentLoopTask actions — agent resource"""

import json
import logging
import re
import time
import threading
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

_FLOW_TEMPLATES_TTL = 30.0
_FLOW_TEMPLATES_CACHE: Dict[str, Dict[str, Any]] = {}
_FLOW_TEMPLATES_REFRESHING: set[str] = set()
_FLOW_TEMPLATES_LOCK = threading.Lock()

# Sentinel: a cluster handler returns this when `action` is not one it owns.
_UNHANDLED = object()


def invalidate_flow_templates_cache(user_id: str = "") -> None:
    """Clear cached flow template listings after repository mutations."""
    keys = {user_id or "", ""}
    with _FLOW_TEMPLATES_LOCK:
        for key in keys:
            _FLOW_TEMPLATES_CACHE.pop(key, None)
            _FLOW_TEMPLATES_REFRESHING.discard(key)


# Cap on UI-supplied skill bundle uploads (sum of decoded asset bytes).
_SKILL_PACKAGE_FILES_MAX_BYTES = 2_000_000


def _safe_package_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.@+-]", "_", str(value or "")) or "default"


def _has_pfp_install_records(user_id: str, conversation_id: str = "",
                             scope: str = "user") -> bool:
    try:
        from core.paths import REPOSITORY_DIR
        root = REPOSITORY_DIR / "packages"
        if scope in {"conversation", "conv"} and conversation_id:
            conv_root = (root / "conversations" / _safe_package_component(user_id)
                         / _safe_package_component(conversation_id))
            if conv_root.exists() and any(conv_root.glob("*.json")):
                return True
        user_root = root / "users" / _safe_package_component(user_id)
        return user_root.exists() and any(user_root.glob("*.json"))
    except Exception:
        logger.debug("PFP install record fast check failed", exc_info=True)
        return True


def _decode_skill_package_files(raw) -> Dict[str, bytes]:
    """Decode UI-supplied skill bundle files to {relpath: bytes}.

    The UI sends {relpath: base64} so binary assets (e.g. images under
    assets/) survive the JSON transport. Unsafe paths and the reserved
    SKILL.md name are dropped; the total decoded size is capped.
    """
    import base64
    import binascii
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, bytes] = {}
    total = 0
    for rel, b64 in raw.items():
        clean = str(rel or "").replace("\\", "/").strip("/")
        parts = clean.split("/") if clean else []
        if not clean or clean == "SKILL.md" or any(
                p in (".", "..", "") for p in parts):
            continue
        try:
            content = base64.b64decode(str(b64 or ""), validate=True)
        except (binascii.Error, ValueError):
            continue
        total += len(content)
        if total > _SKILL_PACKAGE_FILES_MAX_BYTES:
            raise ValueError(
                "Skill bundle exceeds the "
                f"{_SKILL_PACKAGE_FILES_MAX_BYTES // 1000} KB upload cap")
        out[clean] = content
    return out


def _scan_flow_templates(user_id: str) -> List[Dict[str, Any]]:
    from core.paths import REPOSITORY_DIR

    templates = []
    roots = [("global", REPOSITORY_DIR / "flows" / "global")]
    if user_id:
        roots.append(("user", REPOSITORY_DIR / "flows" / "users" / user_id))
    for scope_label, root in roots:
        if not root.is_dir():
            continue
        for latest in root.rglob("latest.json"):
            try:
                tpl = _flow_template_from_latest(latest, root, scope_label)
            except Exception as exc:
                logger.debug("list_resources flow_templates: skip %s: %s", latest, exc)
                continue
            if tpl:
                templates.append(tpl)
    templates.sort(key=lambda t: (t["package"], t["name"], t["version"], t["scope"]))
    return templates


def _flow_template_from_latest(latest, root, scope_label) -> Dict[str, Any]:
    """Parse one repo flow-template pointer (latest.json) into a row, or None.

    Shared by the per-user scan (_scan_flow_templates) and the admin
    cross-user scan (_scan_all_flow_templates) so both stay in lock-step.
    """
    flow_dir = latest.parent
    rel_parts = flow_dir.relative_to(root).parts
    package = ".".join(rel_parts[:-1]) if len(rel_parts) > 1 else "default"
    ptr = json.loads(latest.read_text(encoding="utf-8"))
    version = (ptr.get("version") or "").strip()
    if not version:
        return None
    vfile = flow_dir / "versions" / f"{version}.json"
    if not vfile.is_file():
        return None
    raw = json.loads(vfile.read_text(encoding="utf-8"))
    return {
        "id": raw.get("id") or flow_dir.name,
        "name": raw.get("name") or flow_dir.name,
        "package": raw.get("package") or package,
        "version": version,
        "description": raw.get("description") or "",
        "scope": raw.get("scope") or scope_label,
        "tasks_count": len(raw.get("tasks", {}) or {}),
        "services_count": len(raw.get("services", {}) or {}),
    }


def _scan_all_flow_templates() -> List[Dict[str, Any]]:
    """Admin cross-user flow-template catalog: global + every user's repo,
    each row owner-labelled. Read-only filesystem walk; mirrors
    _scan_flow_templates but across all owners (no per-user cache — the
    admin view-all path is rare and not on the hot render loop)."""
    from core.paths import REPOSITORY_DIR
    from core import admin_scope
    out: List[Dict[str, Any]] = []
    roots = []
    _g = REPOSITORY_DIR / "flows" / "global"
    if _g.is_dir():
        roots.append(("", "global", _g))
    _users = REPOSITORY_DIR / "flows" / "users"
    if _users.is_dir():
        for ud in sorted(_users.iterdir()):
            if ud.is_dir():
                roots.append((ud.name, "user", ud))
    for owner, scope_label, root in roots:
        for latest in root.rglob("latest.json"):
            try:
                tpl = _flow_template_from_latest(latest, root, scope_label)
            except Exception as exc:
                logger.debug(
                    "list_resources flow_templates(all): skip %s: %s", latest, exc)
                continue
            if not tpl:
                continue
            tpl["owner_id"] = owner
            tpl["owner_display"] = (
                admin_scope.display_name_for(owner) if owner else "")
            tpl["conv_id"] = ""
            tpl["conv_title"] = ""
            out.append(tpl)
    out.sort(key=lambda t: (t["package"], t["name"], t["version"], t["scope"]))
    return out


def _overlay_admin_view_all(result: Dict[str, Any], rs) -> None:
    """Replace the repo-backed catalog sections of a self-view list_resources
    `result` with admin cross-user rows (every owner, owner-labelled), in
    place. Leaves all other sections (deployed flows, relays, remote FS,
    summarizer, tasks, secrets, variables, live agents) untouched so the
    panel keeps the admin's own values instead of blanking. Secrets and
    variables are intentionally never enumerated cross-user.
    """
    from core import admin_scope
    cidx = admin_scope.conv_index()
    conv_pairs = [(v.get("owner", ""), cid)
                  for cid, v in cidx.items() if v.get("owner")]

    def _rows(rtype, mapper):
        out = []
        for e in rs.list_all_global(rtype, conv_pairs=conv_pairs):
            row = mapper(e)
            oid = e.get("_owner_id", "") or ""
            _cid = e.get("_conv_id", "") or ""
            row["scope"] = e.get("_scope", "")
            row["owner_id"] = oid
            row["owner_display"] = (
                admin_scope.display_name_for(oid) if oid else "")
            row["conv_id"] = _cid
            row["conv_title"] = cidx.get(_cid, {}).get("title", "")
            out.append(row)
        return out

    repo_agents_all = _rows("agent", lambda a: {
        "name": a.get("name", ""),
        "description": a.get("description", ""),
    })
    result["repo_agents"] = repo_agents_all
    result["repo_agent_count"] = len(repo_agents_all)
    result["skills"] = _rows("skill", lambda s: {
        "name": s.get("name", ""),
        "description": s.get("description", ""),
        "invalid": s.get("_invalid", ""),
        "assigned_to": [],
    })
    result["mcp_servers"] = _rows("mcp", lambda m: {
        "name": m.get("name", ""),
        "url": m.get("url", ""),
        "transport": m.get("transport", "http"),
        "enabled": False,
    })
    result["task_defs"] = _rows("task_def", lambda t: {
        "name": t.get("name", ""),
        "description": (t.get("description", "")
                        or t.get("prompt", "")[:60]),
        "default_interval": t.get("default_interval", "6/1m"),
    })
    result["prompts"] = _rows("prompt", lambda p: {
        "name": p.get("name", ""),
        "title": p.get("title", ""),
        "category": p.get("category", ""),
        "description": p.get("description", ""),
        "has_parameters": bool(p.get("parameters")),
    })
    result["agent_hooks"] = _rows("agent_hook", lambda h: {
        "name": h.get("name", ""),
        "description": h.get("description", ""),
        "events": h.get("events") or [],
        "tools": h.get("tools") or [],
        "fail_policy": h.get("fail_policy", "open"),
        "active": False,
    })
    result["flow_templates"] = _scan_all_flow_templates()


def _get_flow_templates_cached(user_id: str) -> List[Dict[str, Any]]:
    key = user_id or ""
    now = time.monotonic()
    with _FLOW_TEMPLATES_LOCK:
        entry = _FLOW_TEMPLATES_CACHE.get(key) or {}
        cached = list(entry.get("data") or [])
        if entry.get("expires", 0.0) > now:
            return cached
        if key in _FLOW_TEMPLATES_REFRESHING:
            return cached
        _FLOW_TEMPLATES_REFRESHING.add(key)

    if not cached:
        try:
            data = _scan_flow_templates(key)
            with _FLOW_TEMPLATES_LOCK:
                _FLOW_TEMPLATES_CACHE[key] = {
                    "data": data,
                    "expires": time.monotonic() + _FLOW_TEMPLATES_TTL,
                }
                _FLOW_TEMPLATES_REFRESHING.discard(key)
            return data
        except Exception as exc:
            logger.debug("list_resources flow_templates cold scan failed: %s", exc)
            with _FLOW_TEMPLATES_LOCK:
                _FLOW_TEMPLATES_REFRESHING.discard(key)
            return cached

    def _refresh() -> None:
        try:
            data = _scan_flow_templates(key)
            with _FLOW_TEMPLATES_LOCK:
                _FLOW_TEMPLATES_CACHE[key] = {
                    "data": data,
                    "expires": time.monotonic() + _FLOW_TEMPLATES_TTL,
                }
        except Exception as exc:
            logger.debug("list_resources flow_templates failed: %s", exc)
        finally:
            with _FLOW_TEMPLATES_LOCK:
                _FLOW_TEMPLATES_REFRESHING.discard(key)

    threading.Thread(
        target=_refresh, name=f"flow-template-cache-{key or 'global'}",
        daemon=True).start()
    return cached
