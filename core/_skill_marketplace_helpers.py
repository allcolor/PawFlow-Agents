"""Pure helpers for the skill marketplace (no network I/O).

This module holds the validation, parsing, ranking, and formatting helpers
used by :mod:`core.skill_marketplace`. It performs no HTTP and imports nothing
from the facade module, so the dependency is strictly one-directional
(``skill_marketplace`` -> ``_skill_marketplace_helpers``). The shared error
type and the structural regexes live here because the pure helpers raise/use
them; the facade re-exports ``SkillMarketplaceError`` for import stability.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, Iterable, List, Tuple
from urllib.parse import urlparse

import yaml


_GITHUB_REF_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_SKILL_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)
_RESERVED_SKILL_WORDS = ("anthropic", "claude")


class SkillMarketplaceError(ValueError):
    """Raised for invalid source, unsafe package content, or fetch failures."""


def _expand_sources(source: str) -> List[str]:
    if source in ("", "all"):
        return ["codex", "claude", "hermes", "openclaw"]
    if source not in {"codex", "claude", "hermes", "openclaw"}:
        raise SkillMarketplaceError(f"Unsupported marketplace source: {source}")
    return [source]


def _normalize_package(package: Dict[str, Any]) -> Dict[str, Any]:
    files = package["files"]
    skill_md = files.get("SKILL.md")
    if not skill_md:
        raise SkillMarketplaceError("Skill package must contain SKILL.md at its root")
    frontmatter, body = _parse_skill_md(_decode_text_file(skill_md, "SKILL.md"))
    skill_name = str(frontmatter.get("name", "") or "").strip()
    _validate_skill_name(skill_name)
    description = str(frontmatter.get("description", "") or "").strip()
    if not description:
        raise SkillMarketplaceError("SKILL.md frontmatter.description is required")
    package_files = {k: v for k, v in files.items() if k != "SKILL.md"}
    digest = _package_hash(files)
    skill = {
        "instructions": body.strip(),
        "description": description,
        "imported_from": package["provenance"],
        "package_hash": digest,
    }
    for optional in ("license", "compatibility", "metadata"):
        if optional in frontmatter:
            skill[optional] = frontmatter[optional]
    if "allowed-tools" in frontmatter:
        skill["allowed-tools"] = frontmatter.get("allowed-tools") or ""
        skill["declared_allowed_tools"] = skill["allowed-tools"]
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


def _parse_github_import_source(ref: str) -> Tuple[str, str, str, str]:
    ref = (ref or "").strip()
    if not ref:
        raise SkillMarketplaceError("GitHub repository is required")
    parsed = urlparse(ref)
    if parsed.scheme in {"http", "https"}:
        if parsed.netloc.lower() != "github.com":
            raise SkillMarketplaceError("Only github.com repositories are supported")
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) < 2:
            raise SkillMarketplaceError("GitHub URL must include owner and repo")
        owner, repo = parts[0], parts[1]
        if len(parts) >= 4 and parts[2] == "tree":
            selected_ref = parts[3]
            path = "/".join(parts[4:])
        else:
            selected_ref = ""
            path = ""
    else:
        repo_ref, path = (ref.split(":", 1) + [""])[:2] if ":" in ref else (ref, "")
        parts = [p for p in repo_ref.strip("/").split("/") if p]
        if len(parts) != 2:
            raise SkillMarketplaceError("Use owner/repo or https://github.com/owner/repo")
        owner, repo = parts
        selected_ref = ""
    _validate_github_ref_part(owner, "owner")
    _validate_github_ref_part(repo, "repo")
    if selected_ref:
        _validate_github_ref_part(selected_ref, "ref")
    return owner, repo, selected_ref, path.strip("/")


def _reject_unsafe_path(path: str, *, is_dir: bool) -> None:
    clean = (path or "").replace("\\", "/").strip("/")
    parts = [p for p in clean.split("/") if p]
    if not clean or any(p in (".", "..") for p in parts):
        raise SkillMarketplaceError(f"Unsafe package path: {path}")
    blocked_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv"}
    if any(p in blocked_dirs for p in parts):
        raise SkillMarketplaceError(f"Blocked package directory: {path}")
    if not is_dir and clean == "SKILL.md":
        return


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
    if (not _SKILL_NAME_RE.match(name) or "--" in name
            or any(word in name for word in _RESERVED_SKILL_WORDS)):
        raise SkillMarketplaceError(
            "Skill name must follow Agent Skills spec: lowercase letters, numbers, single hyphens")


def _validate_github_ref_part(value: str, label: str) -> None:
    if label == "ref":
        parts = [p for p in str(value or "").split("/") if p]
        if (not value or not re.match(r"^[A-Za-z0-9_./-]+$", value)
                or any(p in {".", ".."} for p in parts)):
            raise SkillMarketplaceError(f"Invalid GitHub {label}: {value}")
        return
    if not value or not _GITHUB_REF_RE.match(value) or value in {".", ".."}:
        raise SkillMarketplaceError(f"Invalid GitHub {label}: {value}")


def _decode_text_file(content: Any, path: str) -> str:
    if isinstance(content, bytes):
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SkillMarketplaceError(f"{path} must be UTF-8 text") from exc
    return str(content or "")


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


def _package_hash(files: Dict[str, Any]) -> str:
    canonical = {}
    for rel, content in sorted((files or {}).items()):
        data = content if isinstance(content, bytes) else str(content or "").encode("utf-8")
        canonical[rel] = {
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    raw = json.dumps(canonical, ensure_ascii=False, sort_keys=True).encode("utf-8")
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
    if blocked and not force:
        return (
            f"Skill review flagged '{skill_name}' as high risk. "
            "Review the findings and rerun with force=true to import it anyway."
        )
    if requires_human_review and not force:
        return (
            f"Skill '{skill_name}' needs human review. "
            "Review the findings and rerun with force=true to import it."
        )
    return f"Reviewed skill '{skill_name}' without importing it."


def _skill_import_command(source: str, ref: str, *, name: str = "",
                          scope: str = "user", force: bool = False) -> str:
    parts = ["/skill", "import"]
    if source:
        parts.extend(["--source", source])
    if force:
        parts.append("--force")
    if scope and scope != "user":
        parts.extend(["--scope", scope])
    if name:
        parts.extend(["--name", name])
    parts.append(ref)
    return " ".join(_quote_command_part(part) for part in parts)


def _quote_command_part(value: str) -> str:
    value = str(value or "")
    if not value:
        return "''"
    if re.search(r"[\s'\"\\]", value):
        return "'" + value.replace("'", "'\\''") + "'"
    return value


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
