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

from core.paths import (
    REPO_TYPES, REPOSITORY_DIR, DEFAULTS_DIR,
    repo_dir, repo_file,
    flow_package_dir, flow_dir, flow_latest_file, flow_version_file,
    parse_flow_fqn,
)

logger = logging.getLogger(__name__)

SCOPE_GLOBAL = "global"
SCOPE_USER = "user"
SCOPE_CONV = "conv"
VALID_SCOPES = (SCOPE_GLOBAL, SCOPE_USER, SCOPE_CONV)

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
        path = repo_file(rtype, name, scope, user_id, conv_id)
        if path.exists():
            raise ValueError(
                f"{rtype}/{name} already exists in scope {scope}")

        entry = dict(data)
        entry["name"] = name
        entry.setdefault("created_at", time.time())
        entry["updated_at"] = time.time()

        self._write_json(path, entry)
        return entry

    def get(self, rtype: str, name: str, scope: str,
            user_id: str = "", conv_id: str = "") -> Optional[Dict[str, Any]]:
        """Read a definition from a specific scope."""
        path = repo_file(rtype, name, scope, user_id, conv_id)
        return self._read_json(path)

    def update(self, rtype: str, name: str, scope: str,
               data: Dict[str, Any],
               user_id: str = "", conv_id: str = "") -> Dict[str, Any]:
        """Update a definition. Raises KeyError if not found."""
        path = repo_file(rtype, name, scope, user_id, conv_id)
        existing = self._read_json(path)
        if existing is None:
            raise KeyError(f"{rtype}/{name} not found in scope {scope}")
        existing.update(data)
        existing["updated_at"] = time.time()
        self._write_json(path, existing)
        return existing

    def delete(self, rtype: str, name: str, scope: str,
               user_id: str = "", conv_id: str = "") -> bool:
        """Delete a definition. Returns True if deleted."""
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
        results = []
        for p in sorted(directory.glob("*.json")):
            entry = self._read_json(p)
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

    # ── Seed ───────────────────────────────────────────────────────

    def seed_from_defaults(self, rtype: str):
        """Seed global scope from defaults/ directory if empty."""
        global_dir = repo_dir(rtype, SCOPE_GLOBAL)
        if global_dir.exists() and any(global_dir.glob("*.json")):
            return
        seed_dir = DEFAULTS_DIR / rtype
        if not seed_dir.exists():
            return
        global_dir.mkdir(parents=True, exist_ok=True)
        for src in seed_dir.glob("*.json"):
            shutil.copy2(src, global_dir / src.name)
            logger.info("Seeded %s/global/%s from defaults", rtype, src.name)

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
                    pass
                raise
