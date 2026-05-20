"""ScopedRepository — CRUD for repository definitions.

Each resource is a single JSON file stored under:
    data/repository/{rtype}/{scope_path}/{name}.json

Scopes:
    global                  → visible to all users
    user   (user_id)        → visible to this user
    conv   (user_id/conv_id)→ visible in this conversation only

Flows have special handling (packages, versions).
"""

import json
import logging
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import re

import core.paths as _paths
from core.paths import (
    REPO_TYPES,
    _MARKDOWN_TYPES,
    repo_dir, repo_file,
    flow_package_dir, flow_dir, flow_latest_file, flow_version_file,
    parse_flow_fqn,
)

logger = logging.getLogger(__name__)

SCOPE_GLOBAL = "global"
SCOPE_USER = "user"
SCOPE_CONV = "conv"
VALID_SCOPES = (SCOPE_GLOBAL, SCOPE_USER, SCOPE_CONV)
_DIRECTORY_TYPES = frozenset({"theme", "private_gateway_skin", "skills"})

# Promote direction: conv < user < global
_SCOPE_ORDER = {SCOPE_CONV: 0, SCOPE_USER: 1, SCOPE_GLOBAL: 2}


def _copytree_content(src: Path, dst: Path):
    """Copy directory tree (content only, no permission copy)."""
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        target = dst / item.name
        if item.is_dir():
            _copytree_content(item, target)
        else:
            shutil.copyfile(item, target)


class ScopedRepository:
    """Thread-safe CRUD for scoped repository definitions."""

    _instance: Optional["ScopedRepository"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._write_lock = threading.Lock()

    @classmethod
    def instance(cls) -> "ScopedRepository":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls):
        with cls._lock:
            cls._instance = None

    # ── CRUD ───────────────────────────────────────────────────────

    def create(self, rtype: str, name: str, scope: str,
              data: Dict[str, Any],
              user_id: str = "", conv_id: str = "") -> Dict[str, Any]:
        """Create a definition. Raises ValueError if it already exists."""
        self._validate(rtype, scope, user_id, conv_id)
        if rtype in _DIRECTORY_TYPES:
            path = repo_dir(rtype, scope, user_id, conv_id) / name
            if path.exists():
                raise ValueError(
                    f"{rtype}/{name} already exists in scope {scope}")
            entry = dict(data)
            entry["name"] = name
            entry.setdefault("created_at", time.time())
            entry["updated_at"] = time.time()
            self._write_directory_resource(rtype, path, entry)
            return self._read_directory_resource(rtype, path) or entry

        path = repo_file(rtype, name, scope, user_id, conv_id)
        if path.exists():
            raise ValueError(
                f"{rtype}/{name} already exists in scope {scope}")

        entry = dict(data)
        entry["name"] = name
        entry.setdefault("created_at", time.time())
        entry["updated_at"] = time.time()

        self._write(rtype, path, entry)
        return entry

    def get(self, rtype: str, name: str, scope: str,
            user_id: str = "", conv_id: str = "") -> Optional[Dict[str, Any]]:
        """Read a definition from a specific scope."""
        if rtype in _DIRECTORY_TYPES:
            return self._read_directory_resource(
                rtype, repo_dir(rtype, scope, user_id, conv_id) / name)
        path = repo_file(rtype, name, scope, user_id, conv_id)
        return self._read(rtype, path)

    def update(self, rtype: str, name: str, scope: str,
               data: Dict[str, Any],
               user_id: str = "", conv_id: str = "") -> Dict[str, Any]:
        """Update a definition. Raises KeyError if not found."""
        if rtype in _DIRECTORY_TYPES:
            path = repo_dir(rtype, scope, user_id, conv_id) / name
            existing = self._read_directory_resource(rtype, path)
            if existing is None:
                raise KeyError(f"{rtype}/{name} not found in scope {scope}")
            existing.update(data)
            existing["updated_at"] = time.time()
            self._write_directory_resource(rtype, path, existing)
            return self._read_directory_resource(rtype, path) or existing

        path = repo_file(rtype, name, scope, user_id, conv_id)
        existing = self._read(rtype, path)
        if existing is None:
            raise KeyError(f"{rtype}/{name} not found in scope {scope}")
        existing.update(data)
        existing["updated_at"] = time.time()
        self._write(rtype, path, existing)
        return existing

    def delete(self, rtype: str, name: str, scope: str,
               user_id: str = "", conv_id: str = "") -> bool:
        """Delete a definition. Returns True if deleted."""
        if rtype in _DIRECTORY_TYPES:
            path = repo_dir(rtype, scope, user_id, conv_id) / name
            if not path.exists():
                return False
            shutil.rmtree(path)
            return True

        path = repo_file(rtype, name, scope, user_id, conv_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    def list(self, rtype: str, scope: str,
             user_id: str = "", conv_id: str = "") -> List[Dict[str, Any]]:
        """List definitions in a specific scope."""
        directory = repo_dir(rtype, scope, user_id, conv_id)
        if not directory.exists():
            return []
        if rtype in _DIRECTORY_TYPES:
            results = []
            for p in sorted(x for x in directory.iterdir() if x.is_dir()):
                entry = self._read_directory_resource(rtype, p)
                if entry is not None:
                    entry["_scope"] = self._scope_label(scope, user_id, conv_id)
                    results.append(entry)
            return results

        ext = "*.md" if rtype in _MARKDOWN_TYPES else "*.json"
        results = []
        for p in sorted(directory.glob(ext)):
            entry = self._read(rtype, p)
            if entry is not None:
                entry["_scope"] = self._scope_label(scope, user_id, conv_id)
                results.append(entry)
        return results

    def list_available(self, rtype: str,
                       user_id: str,
                       conv_id: str = "") -> List[Dict[str, Any]]:
        """List ALL definitions accessible to this user in this conv.

        Returns entries from all 3 scopes, each tagged with _scope.
        No dedup, no override — distinct definitions.
        """
        results = []
        results.extend(self.list(rtype, SCOPE_GLOBAL))
        if user_id:
            results.extend(self.list(rtype, SCOPE_USER, user_id))
        if user_id and conv_id:
            results.extend(self.list(rtype, SCOPE_CONV, user_id, conv_id))
        return results

    # ── Promote / Demote ─────────────────────────────────────────

    def promote(self, rtype: str, name: str,
                from_scope: str, to_scope: str,
                user_id: str = "", conv_id: str = "",
                move: bool = False) -> Path:
        """Copy a definition to a broader scope.

        Raises ValueError if to_scope is not broader than from_scope.
        Raises ValueError if target already exists.
        """
        if _SCOPE_ORDER.get(to_scope, -1) <= _SCOPE_ORDER.get(from_scope, -1):
            raise ValueError(
                f"Cannot promote from {from_scope} to {to_scope} "
                f"(must be broader)")
        return self._move_or_copy(
            rtype, name, from_scope, to_scope,
            user_id, conv_id, move)

    def demote(self, rtype: str, name: str,
               from_scope: str, to_scope: str,
               user_id: str = "", conv_id: str = "",
               move: bool = False) -> Path:
        """Copy a definition to a narrower scope.

        Raises ValueError if to_scope is not narrower than from_scope.
        Raises ValueError if target already exists.
        """
        if _SCOPE_ORDER.get(to_scope, -1) >= _SCOPE_ORDER.get(from_scope, -1):
            raise ValueError(
                f"Cannot demote from {from_scope} to {to_scope} "
                f"(must be narrower)")
        return self._move_or_copy(
            rtype, name, from_scope, to_scope,
            user_id, conv_id, move)

    def _move_or_copy(self, rtype: str, name: str,
                      from_scope: str, to_scope: str,
                      user_id: str, conv_id: str,
                      move: bool) -> Path:
        if rtype == "flows":
            return self._move_or_copy_flow(
                name, from_scope, to_scope, user_id, conv_id, move)
        if rtype in _DIRECTORY_TYPES:
            src = repo_dir(rtype, from_scope, user_id, conv_id) / name
            dst = repo_dir(rtype, to_scope, user_id, conv_id) / name
            if not src.exists():
                raise KeyError(f"{rtype}/{name} not found in scope {from_scope}")
            if dst.exists():
                raise ValueError(
                    f"{rtype}/{name} already exists in scope {to_scope}")
            dst.parent.mkdir(parents=True, exist_ok=True)
            if move:
                shutil.move(str(src), str(dst))
            else:
                _copytree_content(src, dst)
            return dst

        src = repo_file(rtype, name, from_scope, user_id, conv_id)
        dst = repo_file(rtype, name, to_scope, user_id, conv_id)
        if not src.exists():
            raise KeyError(f"{rtype}/{name} not found in scope {from_scope}")
        if dst.exists():
            raise ValueError(
                f"{rtype}/{name} already exists in scope {to_scope}")

        dst.parent.mkdir(parents=True, exist_ok=True)
        if move:
            src.rename(dst)
        else:
            shutil.copyfile(src, dst)

        logger.info("%s %s/%s: %s → %s",
                     "Moved" if move else "Copied",
                     rtype, name, from_scope, to_scope)
        return dst

    def _move_or_copy_flow(self, qualified_name: str,
                           from_scope: str, to_scope: str,
                           user_id: str, conv_id: str,
                           move: bool) -> Path:
        """Promote/demote a flow: copy the entire flow dir (latest + versions)."""
        package, flowname, _ = parse_flow_fqn(qualified_name)
        src_dir = flow_dir(package, flowname, from_scope, user_id, conv_id)
        dst_dir = flow_dir(package, flowname, to_scope, user_id, conv_id)

        if not src_dir.exists():
            raise KeyError(
                f"Flow {qualified_name} not found in scope {from_scope}")
        if dst_dir.exists():
            raise ValueError(
                f"Flow {qualified_name} already exists in scope {to_scope}")

        dst_dir.parent.mkdir(parents=True, exist_ok=True)
        if move:
            shutil.move(str(src_dir), str(dst_dir))
        else:
            _copytree_content(src_dir, dst_dir)

        # Ensure package.json exists at destination
        pkg_dir = flow_package_dir(package, to_scope, user_id, conv_id)
        pkg_file = pkg_dir / "package.json"
        if not pkg_file.exists():
            self._write_json(pkg_file, {
                "name": package,
                "description": "",
                "author": "",
            })

        logger.info("%s flow %s: %s → %s",
                     "Moved" if move else "Copied",
                     qualified_name, from_scope, to_scope)
        return dst_dir

    # ── Flow operations ──────────────────────────────────────────

    def create_flow(self, fqn: str, scope: str,
                    data: Dict[str, Any],
                    user_id: str = "", conv_id: str = "") -> Dict[str, Any]:
        """Create a versioned flow. Creates package if absent."""
        package, flowname, version = parse_flow_fqn(fqn)
        if not version:
            raise ValueError("Flow FQN must include version: package.name:1.0.0")

        ver_file = flow_version_file(
            package, flowname, version, scope, user_id, conv_id)
        if ver_file.exists():
            raise ValueError(f"Flow {fqn} already exists in scope {scope}")

        # Ensure package.json
        pkg_dir = flow_package_dir(package, scope, user_id, conv_id)
        pkg_file = pkg_dir / "package.json"
        if not pkg_file.exists():
            self._write_json(pkg_file, {
                "name": package,
                "description": "",
                "author": "",
            })

        # Write version
        entry = dict(data)
        entry["fqn"] = fqn
        entry["package"] = package
        entry["name"] = flowname
        entry["version"] = version
        entry.setdefault("created_at", time.time())
        self._write_json(ver_file, entry)

        # Write/update latest.json
        latest_file = flow_latest_file(
            package, flowname, scope, user_id, conv_id)
        self._write_json(latest_file, {"version": version})

        return entry

    def publish_flow_version(self, fqn: str, scope: str,
                             data: Dict[str, Any],
                             user_id: str = "",
                             conv_id: str = "") -> Dict[str, Any]:
        """Add a new version and update latest."""
        package, flowname, version = parse_flow_fqn(fqn)
        if not version:
            raise ValueError("Flow FQN must include version")

        fdir = flow_dir(package, flowname, scope, user_id, conv_id)
        if not fdir.exists():
            raise KeyError(
                f"Flow {package}.{flowname} not found in scope {scope}. "
                f"Use create_flow first.")

        ver_file = flow_version_file(
            package, flowname, version, scope, user_id, conv_id)
        if ver_file.exists():
            raise ValueError(f"Version {version} already exists")

        entry = dict(data)
        entry["fqn"] = fqn
        entry["package"] = package
        entry["name"] = flowname
        entry["version"] = version
        entry.setdefault("created_at", time.time())
        self._write_json(ver_file, entry)

        latest_file = flow_latest_file(
            package, flowname, scope, user_id, conv_id)
        self._write_json(latest_file, {"version": version})

        return entry

    def get_flow(self, fqn: str, scope: str,
                 user_id: str = "", conv_id: str = "") -> Optional[Dict[str, Any]]:
        """Get a flow version. If no version in fqn, returns latest."""
        package, flowname, version = parse_flow_fqn(fqn)
        if not version:
            latest_file = flow_latest_file(
                package, flowname, scope, user_id, conv_id)
            latest = self._read_json(latest_file)
            if not latest:
                return None
            version = latest["version"]

        ver_file = flow_version_file(
            package, flowname, version, scope, user_id, conv_id)
        return self._read_json(ver_file)

    def list_flow_versions(self, qualified_name: str, scope: str,
                           user_id: str = "",
                           conv_id: str = "") -> List[str]:
        """List available versions for a flow."""
        package, flowname, _ = parse_flow_fqn(qualified_name)
        versions_dir = flow_dir(
            package, flowname, scope, user_id, conv_id) / "versions"
        if not versions_dir.exists():
            return []
        return sorted(
            [p.stem for p in versions_dir.glob("*.json")],
            key=lambda v: [int(x) if x.isdigit() else x
                           for x in v.replace("-", ".").split(".")])

    def rollback_flow(self, qualified_name: str, version: str,
                      scope: str,
                      user_id: str = "",
                      conv_id: str = "") -> Dict[str, Any]:
        """Set latest to a previous version."""
        package, flowname, _ = parse_flow_fqn(qualified_name)
        ver_file = flow_version_file(
            package, flowname, version, scope, user_id, conv_id)
        if not ver_file.exists():
            raise KeyError(f"Version {version} not found")

        latest_file = flow_latest_file(
            package, flowname, scope, user_id, conv_id)
        self._write_json(latest_file, {"version": version})

        return self._read_json(ver_file)

    # ── Internal ───────────────────────────────────────────────────

    def _validate(self, rtype: str, scope: str,
                  user_id: str, conv_id: str):
        if rtype not in REPO_TYPES:
            raise ValueError(f"Invalid resource type: {rtype}")
        if scope not in VALID_SCOPES:
            raise ValueError(f"Invalid scope: {scope}")
        if scope in (SCOPE_USER, SCOPE_CONV) and not user_id:
            raise ValueError(f"user_id required for scope {scope}")
        if scope == SCOPE_CONV and not conv_id:
            raise ValueError("conv_id required for scope conv")

    @staticmethod
    def _scope_label(scope: str, user_id: str, conv_id: str) -> str:
        if scope == SCOPE_GLOBAL:
            return "global"
        if scope == SCOPE_USER:
            return f"user:{user_id}"
        return f"conv:{user_id}/{conv_id}"

    def _read(self, rtype: str, path: Path) -> Optional[Dict[str, Any]]:
        """Read a resource definition. Dispatches to JSON or Markdown."""
        if rtype in _MARKDOWN_TYPES:
            return self._read_md(path)
        return self._read_json(path)

    def _write(self, rtype: str, path: Path, data: Dict[str, Any]):
        """Write a resource definition. Dispatches to JSON or Markdown."""
        if rtype in _MARKDOWN_TYPES:
            self._write_md(path, data)
        else:
            self._write_json(path, data)

    def _read_theme(self, path: Path) -> Optional[Dict[str, Any]]:
        """Read a directory-based theme resource."""
        meta_file = path / "theme.json"
        if not meta_file.exists():
            return None
        meta = self._read_json(meta_file) or {}
        css_parts = []
        for css_file in sorted(path.rglob("*.css")):
            rel = css_file.relative_to(path).as_posix()
            css_parts.append(f"/* {rel} */\n" + css_file.read_text(encoding="utf-8", errors="replace"))
        entry = dict(meta)
        entry.setdefault("name", path.name)
        entry.setdefault("title", entry.get("name", path.name))
        entry.setdefault("description", "")
        entry["css"] = "\n\n".join(css_parts)
        entry["css_length"] = len(entry["css"])
        return entry

    def _write_theme(self, path: Path, data: Dict[str, Any]):
        """Write a directory-based theme resource."""
        css = data.get("css", "")
        meta = {k: v for k, v in data.items() if k not in ("css", "css_length")}
        path.mkdir(parents=True, exist_ok=True)
        self._write_json(path / "theme.json", meta)
        if css:
            (path / "theme.css").write_text(css, encoding="utf-8")

    def _read_private_gateway_skin(self, path: Path) -> Optional[Dict[str, Any]]:
        """Read a directory-based private gateway skin resource."""
        template_file = path / "template.html"
        if not template_file.exists():
            return None
        meta = self._read_json(path / "skin.json") or {}
        entry = dict(meta)
        entry.setdefault("name", path.name)
        entry.setdefault("title", entry.get("name", path.name))
        entry.setdefault("description", "")
        entry["template"] = template_file.read_text(
            encoding="utf-8", errors="replace")
        entry["template_length"] = len(entry["template"])
        return entry

    def _write_private_gateway_skin(self, path: Path, data: Dict[str, Any]):
        """Write a directory-based private gateway skin resource."""
        template = data.get("template", "")
        meta = {k: v for k, v in data.items()
                if k not in ("template", "template_length")}
        path.mkdir(parents=True, exist_ok=True)
        self._write_json(path / "skin.json", meta)
        if template:
            (path / "template.html").write_text(template, encoding="utf-8")

    @staticmethod
    def _invalid_skill_stub(path: Path, reason: str) -> Dict[str, Any]:
        """Return a placeholder for a malformed skill directory.

        A skill that fails validation must not silently vanish from listings
        and assignments. The stub carries `_invalid` so the UI can surface the
        problem and resolvers can skip it explicitly (see skill_resolver).
        """
        logger.warning("Skill %s is invalid: %s", path, reason)
        return {
            "name": path.name,
            "description": "",
            "instructions": "",
            "_invalid": reason,
        }

    @staticmethod
    def _read_skill_package_files(path: Path) -> Dict[str, str]:
        """Return bundled text files next to SKILL.md, relative to the skill root."""
        if not path.exists():
            return {}
        try:
            base = path.resolve()
        except OSError:
            return {}
        files: Dict[str, str] = {}
        for child in sorted(path.rglob("*")):
            if not child.is_file():
                continue
            try:
                real = child.resolve()
                rel = real.relative_to(base).as_posix()
            except (OSError, ValueError):
                continue
            if rel == "SKILL.md":
                continue
            try:
                files[rel] = real.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
        return files

    def _read_skill(self, path: Path) -> Optional[Dict[str, Any]]:
        """Read a standard Agent Skills directory resource.

        Returns None only when the directory is not a skill at all (no
        SKILL.md). A directory that has a SKILL.md but fails validation is
        returned as an `_invalid` stub so the failure is visible.
        """
        skill_md = path / "SKILL.md"
        if not skill_md.exists():
            return None
        try:
            text = skill_md.read_text(encoding="utf-8")
            m = self._FRONTMATTER_RE.match(text)
            if not m:
                return self._invalid_skill_stub(
                    path, "SKILL.md is missing YAML frontmatter")
            import yaml
            try:
                meta = yaml.safe_load(m.group(1)) or {}
            except yaml.YAMLError as e:
                return self._invalid_skill_stub(
                    path, f"SKILL.md frontmatter is not valid YAML: {e}")
            if not isinstance(meta, dict):
                return self._invalid_skill_stub(
                    path, "SKILL.md frontmatter is not a mapping")
            declared_name = str(meta.get("name") or "").strip()
            if declared_name != path.name:
                return self._invalid_skill_stub(
                    path,
                    f"name mismatch: frontmatter={declared_name!r} "
                    f"directory={path.name!r}")
            body = m.group(2).strip()
            if not body:
                return self._invalid_skill_stub(
                    path, "SKILL.md body is empty")
            entry = dict(meta)
            entry["name"] = path.name
            entry["instructions"] = body
            # Internal compatibility while runtime code moves to instructions.
            entry["prompt"] = body
            entry["skill_root"] = str(path)
            package_files = self._read_skill_package_files(path)
            if package_files:
                entry["package_files"] = package_files
            if "allowed-tools" in entry:
                entry["declared_allowed_tools"] = entry.get("allowed-tools")
            return entry
        except Exception as e:
            return self._invalid_skill_stub(path, f"failed to read SKILL.md: {e}")

    def _write_skill(self, path: Path, data: Dict[str, Any]):
        """Write a standard Agent Skills directory resource."""
        instructions = str(data.get("instructions") or "").strip()
        if not instructions:
            raise ValueError("Skill instructions are required")
        description = str(data.get("description") or "").strip()
        if not description:
            raise ValueError("Skill description is required")
        import re
        if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", path.name):
            raise ValueError(
                "Skill name must be lowercase letters, digits and single hyphens "
                f"(got {path.name!r})")
        # Agent Skills spec conformity: name <= 64 chars, no reserved words.
        if len(path.name) > 64:
            raise ValueError(
                f"Skill name must be at most 64 characters (got {len(path.name)})")
        if "anthropic" in path.name or "claude" in path.name:
            raise ValueError(
                "Skill name must not contain the reserved words "
                "'anthropic' or 'claude'")
        if len(description) > 1024:
            raise ValueError(
                "Skill description must be at most 1024 characters "
                f"(got {len(description)})")
        path.mkdir(parents=True, exist_ok=True)
        # World-readable so the uid-1000 CLI container can read mounted skills.
        try:
            path.chmod(0o755)
        except OSError:
            pass
        import yaml
        # `declared_allowed_tools` is a read-time alias of `allowed-tools`
        # synthesised by _read_skill; writing it back would duplicate the
        # field in the SKILL.md frontmatter.
        meta = {k: v for k, v in data.items() if k not in (
            "instructions", "prompt", "name", "_scope", "skill_root", "package_files",
            "declared_allowed_tools",
        ) and not str(k).startswith("_")}
        meta["name"] = path.name
        meta["description"] = description
        parts = [
            "---",
            yaml.dump(meta, default_flow_style=False, allow_unicode=True, sort_keys=False).rstrip(),
            "---",
            "",
            instructions,
            "",
        ]
        def _world_readable(p: Path, mode: int) -> None:
            try:
                p.chmod(mode)
            except OSError:
                pass
        skill_md = path / "SKILL.md"
        skill_md.write_text("\n".join(parts), encoding="utf-8")
        _world_readable(skill_md, 0o644)
        for rel, content in (data.get("package_files") or {}).items():
            clean = str(rel or "").replace("\\", "/").strip("/")
            if not clean or clean == "SKILL.md" or any(p in (".", "..") for p in clean.split("/")):
                continue
            target = path / clean
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(content or ""), encoding="utf-8")
            _world_readable(target, 0o644)
            # Subdirectories must be traversable by the uid-1000 container.
            sub = path
            for part in clean.split("/")[:-1]:
                sub = sub / part
                _world_readable(sub, 0o755)

    def _read_directory_resource(self, rtype: str, path: Path) -> Optional[Dict[str, Any]]:
        if rtype == "skills":
            return self._read_skill(path)
        if rtype == "theme":
            return self._read_theme(path)
        if rtype == "private_gateway_skin":
            return self._read_private_gateway_skin(path)
        raise ValueError(f"Invalid directory resource type: {rtype}")

    def _write_directory_resource(self, rtype: str, path: Path, data: Dict[str, Any]):
        if rtype == "skills":
            self._write_skill(path, data)
            return
        if rtype == "theme":
            self._write_theme(path, data)
            return
        if rtype == "private_gateway_skin":
            self._write_private_gateway_skin(path, data)
            return
        raise ValueError(f"Invalid directory resource type: {rtype}")

    def _read_json(self, path: Path) -> Optional[Dict[str, Any]]:
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("Failed to read %s: %s", path, e)
            return None

    def _write_json(self, path: Path, data: Dict[str, Any]):
        with self._write_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            try:
                tmp.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8")
                tmp.replace(path)
            except Exception as e:
                logger.error("Failed to write %s: %s", path, e)
                try:
                    tmp.unlink(missing_ok=True)
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                raise

    _FRONTMATTER_RE = re.compile(
        r'\A---\s*\n(.*?)\n---\s*\n(.*)',
        re.DOTALL,
    )

    def _read_md(self, path: Path) -> Optional[Dict[str, Any]]:
        """Read a markdown definition with YAML frontmatter.

        Format:
            ---
            description: Short description
            ---

            Body text (= prompt for markdown-backed resources)
        """
        if not path.exists():
            return None
        try:
            text = path.read_text(encoding="utf-8")
            m = self._FRONTMATTER_RE.match(text)
            if m:
                import yaml
                meta = yaml.safe_load(m.group(1)) or {}
                body = m.group(2).strip()
            else:
                # No frontmatter — entire file is the prompt
                meta = {}
                body = text.strip()
            entry = dict(meta)
            entry["name"] = path.stem
            entry["prompt"] = body
            return entry
        except Exception as e:
            logger.warning("Failed to read %s: %s", path, e)
            return None

    def _write_md(self, path: Path, data: Dict[str, Any]):
        """Write a markdown definition with YAML frontmatter."""
        with self._write_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            import yaml
            # Separate prompt (body) from metadata (frontmatter)
            body = data.get("prompt", "")
            meta = {k: v for k, v in data.items()
                    if k not in ("prompt", "name", "_scope")}
            # Only write frontmatter if there's metadata
            parts = []
            if meta:
                parts.append("---")
                parts.append(yaml.dump(
                    meta, default_flow_style=False,
                    allow_unicode=True).rstrip())
                parts.append("---")
                parts.append("")
            parts.append(body)
            content = "\n".join(parts)

            tmp = path.with_suffix(".tmp")
            try:
                tmp.write_text(content, encoding="utf-8")
                tmp.replace(path)
            except Exception as e:
                logger.error("Failed to write %s: %s", path, e)
                try:
                    tmp.unlink(missing_ok=True)
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                raise
