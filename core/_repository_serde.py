"""ScopedRepository serialization mixin: JSON / Markdown / directory
(theme, private_gateway_skin, skills) read & write.

Extracted from ``core.repository`` to keep that module <=800 lines. These
methods compose onto ``ScopedRepository`` via inheritance; they rely on
``self._write_lock`` (set in ScopedRepository.__init__) and call only each
other, so the split is behaviour-preserving.
"""

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional

from core.paths import _MARKDOWN_TYPES

logger = logging.getLogger(__name__)


class _RepositorySerdeMixin:
    """Read/write helpers for every repository storage format."""

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
    def _read_skill_package_files(path: Path) -> Dict[str, bytes]:
        """Return every file bundled with a skill, excluding SKILL.md.

        Content is returned verbatim as bytes so binary assets are preserved
        — nothing is dropped or lossily decoded. This is read on demand by
        skill export and review, not on every skill read (see _read_skill).
        Entries that resolve outside the skill root (symlink escape) are
        skipped.
        """
        if not path.exists():
            return {}
        try:
            base = path.resolve()
        except OSError:
            return {}
        files: Dict[str, bytes] = {}
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
                files[rel] = real.read_bytes()
            except OSError:
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
        # `review` is review-pipeline metadata, not part of the portable
        # SKILL.md; persisting it would leak a verdict into shared skills.
        meta = {k: v for k, v in data.items() if k not in (
            "instructions", "prompt", "name", "_scope", "skill_root", "package_files",
            "declared_allowed_tools", "review",
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
            # Binary assets are written verbatim; text content is encoded.
            if isinstance(content, bytes):
                target.write_bytes(content)
            else:
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
