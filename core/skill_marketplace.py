"""Search and import external Agent Skills marketplaces.

External skills are untrusted content. This module downloads only bounded text
packages, validates the Agent Skills structure, and returns PawFlow skill data
for the caller to review before writing to ResourceStore.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from typing import Any, Dict, Iterable, List, Tuple
from urllib.parse import urlparse

import requests
import yaml


_USER_AGENT = "PawFlow-skill-marketplace/1.0"
_MAX_RESULTS = 25
_MAX_PACKAGE_FILES = 80
_MAX_FILE_BYTES = 120_000
_MAX_TOTAL_BYTES = 500_000
_SKILL_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)
_SAFE_TEXT_EXTENSIONS = {
    ".css", ".csv", ".html", ".js", ".json", ".md", ".mjs", ".ps1",
    ".py", ".sh", ".svg", ".toml", ".ts", ".txt", ".yaml", ".yml",
}
_KNOWN_CLAUDE_MARKETPLACES = [
    ("anthropics", "skills", "official"),
    ("daymade", "claude-code-skills", "community"),
]


class SkillMarketplaceError(ValueError):
    """Raised for invalid source, unsafe package content, or fetch failures."""


def search_marketplace(source: str = "all", query: str = "",
                       limit: int = 10) -> Dict[str, Any]:
    """Search known skill marketplaces and return normalized result rows."""
    source = (source or "all").strip().lower()
    query = (query or "").strip().lower()
    limit = max(1, min(int(limit or 10), _MAX_RESULTS))
    sources = _expand_sources(source)
    results: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    for src in sources:
        try:
            if src == "codex":
                rows = _search_codex(query, limit)
            elif src == "claude":
                rows = _search_claude(query, limit)
            elif src == "hermes":
                rows = _search_hermes(query, limit)
            elif src == "openclaw":
                rows = _search_openclaw(query, limit)
            else:
                rows = []
            results.extend(rows)
        except Exception as exc:
            errors.append({"source": src, "error": str(exc)})
    results = _dedupe_results(results)
    if query:
        results.sort(key=lambda row: _search_rank(row, query), reverse=True)
    return {
        "source": source,
        "query": query,
        "count": min(len(results), limit),
        "results": results[:limit],
        "errors": errors,
    }


def import_marketplace_skill(source: str = "", ref: str = "", *,
                             name: str = "", user_id: str = "",
                             conversation_id: str = "",
                             review_only: bool = False,
                             force: bool = False,
                             scope: str = "user") -> Dict[str, Any]:
    """Fetch, review, and optionally create a PawFlow skill from a marketplace."""
    if not ref:
        raise SkillMarketplaceError("ref is required")
    source = (source or _infer_source(ref)).strip().lower()
    package = fetch_skill_package(source, ref)
    skill_data = package["skill"]
    package_files = package.get("package_files") or {}
    if name:
        _validate_skill_name(name)
        skill_data["name"] = name
    skill_name = skill_data["name"]

    from core.review_bindings import (
        attach_review_metadata, review_for_write, review_now,
    )
    review = review_now(
        skill_data, operation="import", user_id=user_id, conversation_id=conversation_id,
        package_files=package_files)
    blocked = not bool(review.get("allowed", False)) or review.get("risk") == "block"
    requires_human_review = bool(review.get("requires_human_review", False))
    if review_only or blocked or (requires_human_review and not force):
        return {
            "ok": not blocked,
            "imported": False,
            "review_only": review_only,
            "requires_human_review": requires_human_review,
            "blocked": blocked,
            "skill": _public_skill_preview(skill_data),
            "package": package["package"],
            "review": review,
            "message": _review_message(skill_name, blocked, requires_human_review, force),
        }

    review_meta = review_for_write(
        skill_data, operation="import", user_id=user_id,
        conversation_id=conversation_id, package_files=package_files)
    if review_meta:
        skill_data = attach_review_metadata(skill_data, review_meta)

    from core.resource_store import ResourceStore
    store = ResourceStore.instance()
    write_scope = (scope or "user").strip().lower()
    if write_scope == "conversation":
        if not conversation_id:
            raise SkillMarketplaceError("conversation scope requires conversation_id")
        store.create("skill", skill_name, user_id, skill_data,
                     conversation_id=conversation_id)
    elif write_scope == "user":
        store.create("skill", skill_name, user_id, skill_data)
    else:
        raise SkillMarketplaceError("scope must be user or conversation")
    return {
        "ok": True,
        "imported": True,
        "name": skill_name,
        "scope": write_scope,
        "package": package["package"],
        "review": review,
        "message": f"Imported skill '{skill_name}' from {package['package']['source']}.",
    }


def fetch_skill_package(source: str, ref: str) -> Dict[str, Any]:
    source = (source or _infer_source(ref)).strip().lower()
    if _is_github_tree_url(ref):
        package = _fetch_github_tree_url(ref, source or "github")
    elif source == "codex":
        package = _fetch_codex_ref(ref)
    elif source == "claude":
        package = _fetch_claude_ref(ref)
    elif source == "hermes":
        package = _fetch_github_tree(
            "amanning3390", "hermeshub", "main", f"skills/{ref}", "hermes")
    elif source == "openclaw":
        if "/" not in ref:
            raise SkillMarketplaceError(
                "OpenClaw import requires a GitHub tree URL or repo path ref")
        package = _fetch_openclaw_ref(ref)
    else:
        raise SkillMarketplaceError(f"Unsupported marketplace source: {source}")
    return _normalize_package(package)


def _expand_sources(source: str) -> List[str]:
    if source in ("", "all"):
        return ["codex", "claude", "hermes", "openclaw"]
    if source not in {"codex", "claude", "hermes", "openclaw"}:
        raise SkillMarketplaceError(f"Unsupported marketplace source: {source}")
    return [source]


def _search_codex(query: str, limit: int) -> List[Dict[str, Any]]:
    rows = []
    for group, trust in ((".curated", "official"), (".system", "official-system")):
        for item in _github_contents("openai", "skills", f"skills/{group}"):
            if item.get("type") != "dir":
                continue
            name = item.get("name", "")
            desc = _github_skill_description("openai", "skills", item.get("path", ""))
            if _matches(query, name, desc):
                rows.append(_result(
                    source="codex", name=name, description=desc,
                    ref=name, url=item.get("html_url", ""), trust=trust,
                    import_supported=True,
                ))
            if len(rows) >= limit:
                return rows
    return rows


def _search_claude(query: str, limit: int) -> List[Dict[str, Any]]:
    rows = []
    for owner, repo, trust in _KNOWN_CLAUDE_MARKETPLACES:
        market = _fetch_json(
            f"https://raw.githubusercontent.com/{owner}/{repo}/main/.claude-plugin/marketplace.json")
        for plugin in market.get("plugins") or []:
            for skill_path in _plugin_skill_paths(plugin):
                name = _path_basename(skill_path) or plugin.get("name", "")
                desc = str(plugin.get("description", "") or "")
                haystack = " ".join([
                    name, desc, " ".join(plugin.get("keywords") or []),
                    plugin.get("category", ""),
                ])
                if _matches(query, haystack):
                    clean_path = _join_plugin_path(plugin.get("source", ""), skill_path)
                    url = f"https://github.com/{owner}/{repo}/tree/main/{clean_path}"
                    rows.append(_result(
                        source="claude", name=name, description=desc,
                        ref=url, url=url, trust=trust, import_supported=True,
                    ))
                if len(rows) >= limit:
                    return rows
    return rows


def _search_hermes(query: str, limit: int) -> List[Dict[str, Any]]:
    text = _fetch_text("https://raw.githubusercontent.com/amanning3390/hermeshub/main/README.md")
    rows = []
    for name, desc in _readme_skill_rows(text):
        if _matches(query, name, desc):
            url = f"https://github.com/amanning3390/hermeshub/tree/main/skills/{name}"
            rows.append(_result(
                source="hermes", name=name, description=desc, ref=name,
                url=url, trust="community-scanned", import_supported=True,
            ))
        if len(rows) >= limit:
            return rows
    return rows


def _search_openclaw(query: str, limit: int) -> List[Dict[str, Any]]:
    text = _fetch_text(
        "https://raw.githubusercontent.com/VoltAgent/awesome-openclaw-skills/main/README.md")
    rows = []
    for name, desc, url in _awesome_openclaw_rows(text):
        if _matches(query, name, desc):
            rows.append(_result(
                source="openclaw", name=name, description=desc, ref=url,
                url=url, trust="community-curated-not-audited",
                import_supported=False,
                note="Import requires a github.com/openclaw/skills tree URL.",
            ))
        if len(rows) >= limit:
            return rows
    return rows


def _fetch_codex_ref(ref: str) -> Dict[str, Any]:
    ref = ref.strip().strip("/")
    if ref.startswith("skills/"):
        return _fetch_github_tree("openai", "skills", "main", ref, "codex")
    for group in (".curated", ".system"):
        path = f"skills/{group}/{ref}"
        if _github_path_exists("openai", "skills", path):
            return _fetch_github_tree("openai", "skills", "main", path, "codex")
    raise SkillMarketplaceError(f"Codex skill '{ref}' not found")


def _fetch_claude_ref(ref: str) -> Dict[str, Any]:
    ref = ref.strip()
    if _is_github_tree_url(ref):
        return _fetch_github_tree_url(ref, "claude")
    if ":" in ref and "/" in ref.split(":", 1)[0]:
        repo_ref, skill_ref = ref.split(":", 1)
        owner, repo = repo_ref.split("/", 1)
        market = _fetch_json(
            f"https://raw.githubusercontent.com/{owner}/{repo}/main/.claude-plugin/marketplace.json")
        for plugin in market.get("plugins") or []:
            if plugin.get("name") == skill_ref:
                skill_path = _plugin_skill_paths(plugin)[0]
                clean_path = _join_plugin_path(plugin.get("source", ""), skill_path)
                return _fetch_github_tree(owner, repo, "main", clean_path, "claude")
        raise SkillMarketplaceError(f"Claude plugin '{skill_ref}' not found in {repo_ref}")
    matches = search_marketplace("claude", ref, limit=1).get("results") or []
    if not matches:
        raise SkillMarketplaceError(f"Claude skill '{ref}' not found")
    return _fetch_github_tree_url(matches[0]["ref"], "claude")


def _fetch_openclaw_ref(ref: str) -> Dict[str, Any]:
    if _is_github_tree_url(ref):
        return _fetch_github_tree_url(ref, "openclaw")
    if ref.startswith("openclaw/skills/"):
        path = ref[len("openclaw/skills/"):].strip("/")
        return _fetch_github_tree("openclaw", "skills", "main", path, "openclaw")
    raise SkillMarketplaceError(
        "OpenClaw import requires a GitHub tree URL or openclaw/skills/<path>")


def _normalize_package(package: Dict[str, Any]) -> Dict[str, Any]:
    files = package["files"]
    skill_md = files.get("SKILL.md")
    if not skill_md:
        raise SkillMarketplaceError("Skill package must contain SKILL.md at its root")
    frontmatter, body = _parse_skill_md(skill_md)
    skill_name = str(frontmatter.get("name", "") or "").strip()
    _validate_skill_name(skill_name)
    description = str(frontmatter.get("description", "") or "").strip()
    if not description:
        raise SkillMarketplaceError("SKILL.md frontmatter.description is required")
    package_files = {k: v for k, v in files.items() if k != "SKILL.md"}
    digest = _package_hash(files)
    skill = {
        "prompt": body.strip(),
        "description": description,
        "parameters": {},
        "extends": "",
        "imported_from": package["provenance"],
        "package_hash": digest,
    }
    for optional in ("license", "compatibility", "metadata"):
        if optional in frontmatter:
            skill[optional] = frontmatter[optional]
    if "allowed-tools" in frontmatter:
        skill["declared_allowed_tools"] = str(frontmatter.get("allowed-tools") or "")
    if package_files:
        skill["package_files"] = package_files
    skill["name"] = skill_name
    return {
        "skill": skill,
        "package_files": package_files,
        "package": {
            **package["provenance"],
            "skill_name": skill_name,
            "files_count": len(files),
            "package_hash": digest,
            "package_files_count": len(package_files),
        },
    }


def _fetch_github_tree_url(url: str, source: str) -> Dict[str, Any]:
    parsed = urlparse(url)
    if parsed.netloc.lower() != "github.com":
        raise SkillMarketplaceError("Only github.com tree URLs are supported")
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 5 or parts[2] != "tree":
        raise SkillMarketplaceError("GitHub URL must point to a repository tree path")
    owner, repo, ref = parts[0], parts[1], parts[3]
    path = "/".join(parts[4:])
    return _fetch_github_tree(owner, repo, ref, path, source)


def _fetch_github_tree(owner: str, repo: str, ref: str, path: str,
                       source: str) -> Dict[str, Any]:
    path = path.strip("/")
    items = _github_contents(owner, repo, path, ref=ref)
    if isinstance(items, dict):
        raise SkillMarketplaceError("Skill ref must be a directory containing SKILL.md")
    files: Dict[str, str] = {}
    _collect_github_files(owner, repo, ref, path, items, files)
    return {
        "files": files,
        "provenance": {
            "source": source,
            "repo": f"{owner}/{repo}",
            "ref": ref,
            "path": path,
            "url": f"https://github.com/{owner}/{repo}/tree/{ref}/{path}",
        },
    }


def _collect_github_files(owner: str, repo: str, ref: str, root_path: str,
                          items: Iterable[Dict[str, Any]], files: Dict[str, str]) -> None:
    total = sum(len(v.encode("utf-8")) for v in files.values())
    for item in items:
        if len(files) >= _MAX_PACKAGE_FILES:
            raise SkillMarketplaceError("Skill package has too many files")
        item_type = item.get("type")
        item_path = item.get("path", "")
        rel = item_path[len(root_path):].strip("/") if root_path else item_path
        if item_type == "dir":
            _reject_unsafe_path(rel or item.get("name", ""), is_dir=True)
            children = _github_contents(owner, repo, item_path, ref=ref)
            _collect_github_files(owner, repo, ref, root_path, children, files)
            total = sum(len(v.encode("utf-8")) for v in files.values())
            continue
        if item_type != "file":
            continue
        if _unsupported_file_extension(rel):
            if rel.startswith("assets/"):
                omitted = files.get(".pawflow-omitted-assets.txt", "")
                files[".pawflow-omitted-assets.txt"] = (
                    omitted + f"Skipped non-text asset during import: {rel}\n")
                continue
            raise SkillMarketplaceError(f"Unsupported package file type: {rel}")
        _reject_unsafe_path(rel, is_dir=False)
        size = int(item.get("size") or 0)
        if size > _MAX_FILE_BYTES:
            raise SkillMarketplaceError(f"File too large for skill import: {rel}")
        download_url = item.get("download_url")
        if not download_url:
            content = _github_blob_text(owner, repo, item.get("sha", ""))
        else:
            content = _fetch_text(download_url)
        encoded_len = len(content.encode("utf-8"))
        total += encoded_len
        if total > _MAX_TOTAL_BYTES:
            raise SkillMarketplaceError("Skill package exceeds the total import size cap")
        files[rel] = content


def _reject_unsafe_path(path: str, *, is_dir: bool) -> None:
    clean = (path or "").replace("\\", "/").strip("/")
    parts = [p for p in clean.split("/") if p]
    if not clean or any(p in (".", "..") for p in parts):
        raise SkillMarketplaceError(f"Unsafe package path: {path}")
    blocked_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv"}
    if any(p in blocked_dirs for p in parts):
        raise SkillMarketplaceError(f"Blocked package directory: {path}")
    if not is_dir:
        if _unsupported_file_extension(clean):
            raise SkillMarketplaceError(f"Unsupported package file type: {path}")


def _unsupported_file_extension(path: str) -> bool:
    clean = (path or "").replace("\\", "/").strip("/")
    if clean in {"LICENSE", "NOTICE"}:
        return False
    ext = "." + clean.rsplit(".", 1)[-1].lower() if "." in clean else ""
    return clean != "SKILL.md" and ext not in _SAFE_TEXT_EXTENSIONS


def _parse_skill_md(text: str) -> Tuple[Dict[str, Any], str]:
    match = _FRONTMATTER_RE.match(text or "")
    if not match:
        raise SkillMarketplaceError("SKILL.md must contain YAML frontmatter")
    frontmatter = yaml.safe_load(match.group(1)) or {}
    if not isinstance(frontmatter, dict):
        raise SkillMarketplaceError("SKILL.md frontmatter must be a mapping")
    body = match.group(2).strip()
    if not body:
        raise SkillMarketplaceError("SKILL.md body is required")
    return frontmatter, body


def _validate_skill_name(name: str) -> None:
    if not name:
        raise SkillMarketplaceError("SKILL.md frontmatter.name is required")
    if not _SKILL_NAME_RE.match(name) or "--" in name:
        raise SkillMarketplaceError(
            "Skill name must follow Agent Skills spec: lowercase letters, numbers, single hyphens")


def _github_contents(owner: str, repo: str, path: str, ref: str = "main"):
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path.strip('/')}?ref={ref}"
    return _fetch_json(url)


def _github_path_exists(owner: str, repo: str, path: str, ref: str = "main") -> bool:
    try:
        _github_contents(owner, repo, path, ref=ref)
        return True
    except Exception:
        return False


def _github_blob_text(owner: str, repo: str, sha: str) -> str:
    blob = _fetch_json(f"https://api.github.com/repos/{owner}/{repo}/git/blobs/{sha}")
    if blob.get("encoding") != "base64":
        raise SkillMarketplaceError("Unsupported GitHub blob encoding")
    raw = base64.b64decode(blob.get("content", ""))
    if len(raw) > _MAX_FILE_BYTES:
        raise SkillMarketplaceError("GitHub blob exceeds file size cap")
    return raw.decode("utf-8")


def _github_skill_description(owner: str, repo: str, path: str) -> str:
    try:
        text = _fetch_text(
            f"https://raw.githubusercontent.com/{owner}/{repo}/main/{path.strip('/')}/SKILL.md")
        frontmatter, _body = _parse_skill_md(text)
        return str(frontmatter.get("description", "") or "")
    except Exception:
        return ""


def _fetch_json(url: str) -> Any:
    response = requests.get(url, headers={"User-Agent": _USER_AGENT})
    if response.status_code >= 400:
        raise SkillMarketplaceError(f"Fetch failed {response.status_code}: {url}")
    return response.json()


def _fetch_text(url: str) -> str:
    response = requests.get(url, headers={"User-Agent": _USER_AGENT})
    if response.status_code >= 400:
        raise SkillMarketplaceError(f"Fetch failed {response.status_code}: {url}")
    data = response.content
    if len(data) > _MAX_FILE_BYTES * 2 and "README.md" not in url:
        raise SkillMarketplaceError(f"Fetched text exceeds import cap: {url}")
    return data.decode("utf-8")


def _plugin_skill_paths(plugin: Dict[str, Any]) -> List[str]:
    skills = plugin.get("skills")
    if isinstance(skills, list) and skills:
        return [str(s) for s in skills]
    source = str(plugin.get("source", "") or "")
    return [source] if source and source != "./" else []


def _join_plugin_path(source: str, skill_path: str) -> str:
    source = str(source or "").strip()
    skill_path = str(skill_path or "").strip()
    if skill_path.startswith("./"):
        skill_path = skill_path[2:]
    if source.startswith("./"):
        source = source[2:]
    if not source or source == ".":
        return skill_path.strip("/")
    if skill_path and skill_path not in (source, "."):
        if skill_path.startswith(source.rstrip("/") + "/"):
            return skill_path.strip("/")
        return f"{source.rstrip('/')}/{skill_path}".strip("/")
    return source.strip("/")


def _path_basename(path: str) -> str:
    return str(path or "").rstrip("/").split("/")[-1]


def _readme_skill_rows(text: str) -> Iterable[Tuple[str, str]]:
    for line in (text or "").splitlines():
        if not line or line.startswith("#") or "|" in line:
            continue
        match = re.match(r"^([a-z0-9][a-z0-9-]{0,80})\s+(.+)$", line.strip())
        if match:
            yield match.group(1), match.group(2).strip()


def _awesome_openclaw_rows(text: str) -> Iterable[Tuple[str, str, str]]:
    pattern = re.compile(r"^- \[([^\]]+)\]\((https://clawskills\.sh/skills/[^)]+)\) - (.+)$")
    for line in (text or "").splitlines():
        match = pattern.match(line.strip())
        if match:
            yield match.group(1).strip(), match.group(3).strip(), match.group(2).strip()


def _matches(query: str, *parts: str) -> bool:
    if not query:
        return True
    haystack = " ".join(str(p or "") for p in parts).lower()
    return all(term in haystack for term in query.split())


def _search_rank(row: Dict[str, Any], query: str) -> int:
    name = str(row.get("name", "") or "").lower()
    desc = str(row.get("description", "") or "").lower()
    score = 0
    if query == name:
        score += 100
    if query in name:
        score += 50
    if query in desc:
        score += 10
    if row.get("trust") in ("official", "official-system"):
        score += 5
    return score


def _result(**kwargs) -> Dict[str, Any]:
    return {k: v for k, v in kwargs.items() if v not in (None, "")}


def _dedupe_results(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for row in rows:
        key = (row.get("source"), row.get("ref") or row.get("url"), row.get("name"))
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _package_hash(files: Dict[str, str]) -> str:
    raw = json.dumps(files, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _public_skill_preview(skill: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": skill.get("name", ""),
        "description": skill.get("description", ""),
        "package_hash": skill.get("package_hash", ""),
        "package_files_count": len(skill.get("package_files") or {}),
        "provenance": skill.get("imported_from") or {},
    }


def _review_message(skill_name: str, blocked: bool,
                    requires_human_review: bool, force: bool) -> str:
    if blocked:
        return f"Import blocked by skill review for '{skill_name}'."
    if requires_human_review and not force:
        return (
            f"Skill '{skill_name}' requires human review. "
            "Re-run with force=true after reviewing the package to import it."
        )
    return f"Reviewed skill '{skill_name}' without importing it."


def _infer_source(ref: str) -> str:
    if "github.com/openai/skills" in ref:
        return "codex"
    if "github.com/anthropics/skills" in ref or "github.com/daymade/claude-code-skills" in ref:
        return "claude"
    if "github.com/amanning3390/hermeshub" in ref:
        return "hermes"
    if "github.com/openclaw/skills" in ref or "clawskills.sh" in ref:
        return "openclaw"
    return "github" if _is_github_tree_url(ref) else ""


def _is_github_tree_url(ref: str) -> bool:
    parsed = urlparse(ref or "")
    return parsed.netloc.lower() == "github.com" and "/tree/" in parsed.path

