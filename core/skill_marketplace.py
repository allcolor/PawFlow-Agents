"""Search and import external Agent Skills marketplaces.

External skills are untrusted content. This module downloads bounded Agent
Skills packages, including binary assets, validates the package structure, and
returns PawFlow skill data for the caller to review before writing to
ResourceStore.

This is the public facade: it holds the public API and all network I/O
(GitHub/raw fetches, source backends). Pure validation/parsing/ranking helpers
live in :mod:`core._skill_marketplace_helpers` and are imported below. The
shared ``SkillMarketplaceError`` is re-exported from there for import
stability.
"""

from __future__ import annotations

import base64
import os
from typing import Any, Dict, Iterable, List
from urllib.parse import urlparse

import requests

from core._skill_marketplace_helpers import (
    SkillMarketplaceError,
    _awesome_openclaw_rows,
    _dedupe_results,
    _expand_sources,
    _infer_source,
    _is_github_tree_url,
    _join_plugin_path,
    _matches,
    _normalize_package,
    _parse_github_import_source,
    _parse_skill_md,
    _path_basename,
    _plugin_skill_paths,
    _public_skill_preview,
    _readme_skill_rows,
    _reject_unsafe_path,
    _result,
    _review_message,
    _search_rank,
    _skill_import_command,
    _validate_github_ref_part,
    _validate_skill_name,
)

__all__ = [
    "SkillMarketplaceError",
    "search_marketplace",
    "import_marketplace_skill",
    "resolve_skill_import_source",
    "fetch_skill_package",
]


_USER_AGENT = "PawFlow-skill-marketplace/1.0"
_MAX_RESULTS = 25
_MAX_PACKAGE_FILES = 80
_MAX_FILE_BYTES = 120_000
_MAX_TOTAL_BYTES = 500_000
_FETCH_TIMEOUT_SECONDS = 15
_KNOWN_CLAUDE_MARKETPLACES = [
    ("anthropics", "skills", "official"),
    ("daymade", "claude-code-skills", "community"),
]


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

    from core.review_bindings import attach_review_metadata, review_now
    review = review_now(
        skill_data, operation="import", user_id=user_id, conversation_id=conversation_id,
        package_files=package_files)
    blocked = not bool(review.get("allowed", False)) or review.get("risk") == "block"
    requires_human_review = bool(review.get("requires_human_review", False))
    # The user has the final word: force clears both a hard block and a
    # human-review request. Without force, return the findings so the
    # user can decide and rerun with force=true.
    if review_only or ((blocked or requires_human_review) and not force):
        confirmation_command = _skill_import_command(
            source, ref, name=name, scope=scope, force=True)
        message = _review_message(skill_name, blocked, requires_human_review, force)
        if not review_only:
            message = f"{message}\nReview the findings, then run `{confirmation_command}` to import anyway."
        return {
            "ok": not blocked,
            "imported": False,
            "review_only": review_only,
            "requires_human_review": requires_human_review,
            "requires_confirmation": (blocked or requires_human_review) and not review_only,
            "blocked": blocked,
            "skill": _public_skill_preview(skill_data),
            "package": package["package"],
            "review": review,
            "confirmation_command": confirmation_command,
            "message": message,
        }

    from core.package_review import review_hash, review_metadata
    review_meta = review_metadata(
        review,
        service_id=review.get("service_id", ""),
        llm_service=review.get("llm_service", ""),
        subject_hash=review_hash(skill_data, package_files),
    )
    if review_meta:
        skill_data = attach_review_metadata(skill_data, review_meta)

    from core.resource_store import GLOBAL_USER_ID, ResourceStore
    store = ResourceStore.instance()
    write_scope = (scope or "user").strip().lower()
    if write_scope == "conversation":
        if not conversation_id:
            raise SkillMarketplaceError("conversation scope requires conversation_id")
        store.create("skill", skill_name, user_id, skill_data,
                     conversation_id=conversation_id)
    elif write_scope == "user":
        store.create("skill", skill_name, user_id, skill_data)
    elif write_scope == "global":
        store.create("skill", skill_name, GLOBAL_USER_ID, skill_data)
    else:
        raise SkillMarketplaceError("scope must be global, user, or conversation")
    return {
        "ok": True,
        "imported": True,
        "name": skill_name,
        "scope": write_scope,
        "package": package["package"],
        "review": review,
        "message": f"Imported skill '{skill_name}' from {package['package']['source']}.",
    }


def resolve_skill_import_source(ref: str = "", *, selected_ref: str = "",
                                path: str = "", limit: int = 40) -> Dict[str, Any]:
    """Resolve a GitHub repository into importable Agent Skill directories."""
    owner, repo, parsed_ref, parsed_path = _parse_github_import_source(ref)
    selected_ref = (selected_ref or parsed_ref or "").strip()
    root_path = (path or parsed_path or "").strip("/")
    repo_meta = _fetch_json(f"https://api.github.com/repos/{owner}/{repo}")
    default_ref = selected_ref or str(repo_meta.get("default_branch") or "main")
    branches = _github_ref_names(owner, repo, "branches")
    tags = _github_ref_names(owner, repo, "tags")
    paths = _find_github_skill_paths(owner, repo, default_ref, root_path, limit=limit)
    return {
        "ok": True,
        "repo": f"{owner}/{repo}",
        "selected_ref": default_ref,
        "default_ref": str(repo_meta.get("default_branch") or "main"),
        "refs": {"branches": branches, "tags": tags},
        "root_path": root_path,
        "paths": paths,
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
    elif source == "github":
        package = _fetch_github_repo_ref(ref, "github")
    else:
        raise SkillMarketplaceError(f"Unsupported marketplace source: {source}")
    return _normalize_package(package)


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
        _validate_github_ref_part(owner, "owner")
        _validate_github_ref_part(repo, "repo")
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


def _fetch_github_repo_ref(ref: str, source: str) -> Dict[str, Any]:
    if _is_github_tree_url(ref):
        return _fetch_github_tree_url(ref, source)
    repo_ref, path = (ref.split(":", 1) + [""])[:2] if ":" in ref else (ref, "")
    owner_repo, selected_ref = (repo_ref.split("@", 1) + [""])[:2] if "@" in repo_ref else (repo_ref, "")
    parts = [p for p in owner_repo.strip("/").split("/") if p]
    if len(parts) != 2:
        raise SkillMarketplaceError("GitHub import requires owner/repo@ref:path or a GitHub tree URL")
    owner, repo = parts
    _validate_github_ref_part(owner, "owner")
    _validate_github_ref_part(repo, "repo")
    if not selected_ref:
        selected_ref = str(_fetch_json(f"https://api.github.com/repos/{owner}/{repo}").get("default_branch") or "main")
    return _fetch_github_tree(owner, repo, selected_ref, path, source)


def _fetch_github_tree_url(url: str, source: str) -> Dict[str, Any]:
    parsed = urlparse(url)
    if parsed.netloc.lower() != "github.com":
        raise SkillMarketplaceError("Only github.com tree URLs are supported")
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 4 or parts[2] != "tree":
        raise SkillMarketplaceError("GitHub URL must point to a repository tree path")
    owner, repo, ref = parts[0], parts[1], parts[3]
    _validate_github_ref_part(owner, "owner")
    _validate_github_ref_part(repo, "repo")
    _validate_github_ref_part(ref, "ref")
    path = "/".join(parts[4:])
    return _fetch_github_tree(owner, repo, ref, path, source)


def _fetch_github_tree(owner: str, repo: str, ref: str, path: str,
                       source: str) -> Dict[str, Any]:
    _validate_github_ref_part(owner, "owner")
    _validate_github_ref_part(repo, "repo")
    _validate_github_ref_part(ref, "ref")
    path = path.strip("/")
    items = _github_contents(owner, repo, path, ref=ref)
    if isinstance(items, dict):
        raise SkillMarketplaceError("Skill ref must be a directory containing SKILL.md")
    files: Dict[str, bytes] = {}
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
                          items: Iterable[Dict[str, Any]], files: Dict[str, bytes]) -> None:
    total = sum(len(v) for v in files.values())
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
            total = sum(len(v) for v in files.values())
            continue
        if item_type != "file":
            continue
        _reject_unsafe_path(rel, is_dir=False)
        size = int(item.get("size") or 0)
        if size > _MAX_FILE_BYTES:
            raise SkillMarketplaceError(f"File too large for skill import: {rel}")
        download_url = item.get("download_url")
        if not download_url:
            content = _github_blob_bytes(owner, repo, item.get("sha", ""))
        else:
            content = _fetch_bytes(download_url)
        total += len(content)
        if total > _MAX_TOTAL_BYTES:
            raise SkillMarketplaceError("Skill package exceeds the total import size cap")
        files[rel] = content


def _github_contents(owner: str, repo: str, path: str, ref: str = "main"):
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path.strip('/')}?ref={ref}"
    return _fetch_json(url)


def _github_ref_names(owner: str, repo: str, kind: str) -> List[str]:
    rows = _fetch_json(f"https://api.github.com/repos/{owner}/{repo}/{kind}?per_page=100")
    if not isinstance(rows, list):
        return []
    return [str(row.get("name") or "") for row in rows if row.get("name")]


def _find_github_skill_paths(owner: str, repo: str, ref: str, root_path: str,
                             *, limit: int) -> List[Dict[str, Any]]:
    limit = max(1, min(int(limit or 40), _MAX_RESULTS * 2))
    queue = [root_path.strip("/")]
    seen = set()
    matches: List[Dict[str, Any]] = []
    while queue and len(matches) < limit:
        current = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)
        items = _github_contents(owner, repo, current, ref=ref)
        if isinstance(items, dict):
            continue
        names = {str(item.get("name") or "") for item in items}
        if "SKILL.md" in names:
            matches.append({
                "name": _path_basename(current) or repo,
                "path": current,
                "ref": ref,
                "url": f"https://github.com/{owner}/{repo}/tree/{ref}/{current}" if current else f"https://github.com/{owner}/{repo}/tree/{ref}",
                "import_ref": f"{owner}/{repo}@{ref}:{current}",
            })
            continue
        for item in items:
            if item.get("type") != "dir":
                continue
            item_path = str(item.get("path") or "").strip("/")
            try:
                _reject_unsafe_path(item_path or item.get("name", ""), is_dir=True)
            except SkillMarketplaceError:
                continue
            if len(seen) + len(queue) < 160:
                queue.append(item_path)
    return matches


def _request_headers() -> Dict[str, str]:
    headers = {"User-Agent": _USER_AGENT}
    token = os.environ.get("GITHUB_TOKEN", "") or os.environ.get("GH_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _github_path_exists(owner: str, repo: str, path: str, ref: str = "main") -> bool:
    try:
        _github_contents(owner, repo, path, ref=ref)
        return True
    except Exception:
        return False


def _github_blob_bytes(owner: str, repo: str, sha: str) -> bytes:
    blob = _fetch_json(f"https://api.github.com/repos/{owner}/{repo}/git/blobs/{sha}")
    if blob.get("encoding") != "base64":
        raise SkillMarketplaceError("Unsupported GitHub blob encoding")
    raw = base64.b64decode(blob.get("content", ""))
    if len(raw) > _MAX_FILE_BYTES:
        raise SkillMarketplaceError("GitHub blob exceeds file size cap")
    return raw


def _github_skill_description(owner: str, repo: str, path: str) -> str:
    try:
        text = _fetch_text(
            f"https://raw.githubusercontent.com/{owner}/{repo}/main/{path.strip('/')}/SKILL.md")
        frontmatter, _body = _parse_skill_md(text)
        return str(frontmatter.get("description", "") or "")
    except Exception:
        return ""


def _fetch_json(url: str) -> Any:
    response = requests.get(
        url, headers=_request_headers(), timeout=_FETCH_TIMEOUT_SECONDS)
    if response.status_code >= 400:
        raise SkillMarketplaceError(f"Fetch failed {response.status_code}: {url}")
    return response.json()


def _fetch_text(url: str) -> str:
    return _fetch_bytes(url, readme="README.md" in url).decode("utf-8")


def _fetch_bytes(url: str, *, readme: bool = False) -> bytes:
    response = requests.get(
        url, headers=_request_headers(), timeout=_FETCH_TIMEOUT_SECONDS,
        stream=True)
    if response.status_code >= 400:
        raise SkillMarketplaceError(f"Fetch failed {response.status_code}: {url}")
    cap = _MAX_TOTAL_BYTES if readme else _MAX_FILE_BYTES
    chunks = []
    total = 0
    for chunk in response.iter_content(chunk_size=16384):
        if not chunk:
            continue
        total += len(chunk)
        if total > cap:
            raise SkillMarketplaceError(f"Fetched file exceeds import cap: {url}")
        chunks.append(chunk)
    return b"".join(chunks)
