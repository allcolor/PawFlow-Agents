"""ScopedRepository — CRUD for repository definitions.

Each resource is a single JSON file stored under:
    data/repository/{rtype}/{scope_path}/{name}.json

Scopes:
    global                  → visible to all users
    user   (user_id)        → visible to this user
    conv   (user_id/conv_id)→ visible in this conversation only

Flows have special handling (packages, versions).
"""

import copy
import logging
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


import core.paths as _paths
from core.paths import (
    REPO_TYPES,
    _MARKDOWN_TYPES,
    repo_dir, repo_file,
    flow_package_dir, flow_dir, flow_latest_file, flow_version_file,
    parse_flow_fqn,
)
from core._repository_serde import _RepositorySerdeMixin

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


class ScopedRepository(_RepositorySerdeMixin):
    """Thread-safe CRUD for scoped repository definitions."""

    _instance: Optional["ScopedRepository"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._write_lock = threading.Lock()
        self._list_cache_lock = threading.Lock()
        self._list_cache: Dict[tuple, tuple] = {}

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

    def _invalidate_list_cache(self) -> None:
        with self._list_cache_lock:
            self._list_cache.clear()

    @staticmethod
    def _list_signature(rtype: str, directory: Path):
        try:
            if rtype in _DIRECTORY_TYPES:
                items = []
                for child in sorted(x for x in directory.iterdir() if x.is_dir()):
                    st = child.stat()
                    item = [child.name, st.st_mtime_ns, st.st_size]
                    for path in sorted(x for x in child.rglob("*") if x.is_file()):
                        try:
                            rel = path.relative_to(child).as_posix()
                            pst = path.stat()
                            item.extend([rel, pst.st_mtime_ns, pst.st_size])
                        except FileNotFoundError:
                            continue
                    items.append(tuple(item))
                return tuple(items)
            ext = "*.md" if rtype in _MARKDOWN_TYPES else "*.json"
            items = []
            for path in sorted(directory.glob(ext)):
                st = path.stat()
                items.append((path.name, st.st_mtime_ns, st.st_size))
            return tuple(items)
        except OSError:
            return None

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
            self._invalidate_list_cache()
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
        self._invalidate_list_cache()
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
            self._invalidate_list_cache()
            return self._read_directory_resource(rtype, path) or existing

        path = repo_file(rtype, name, scope, user_id, conv_id)
        existing = self._read(rtype, path)
        if existing is None:
            raise KeyError(f"{rtype}/{name} not found in scope {scope}")
        existing.update(data)
        existing["updated_at"] = time.time()
        self._write(rtype, path, existing)
        self._invalidate_list_cache()
        return existing

    def delete(self, rtype: str, name: str, scope: str,
               user_id: str = "", conv_id: str = "") -> bool:
        """Delete a definition. Returns True if deleted."""
        if rtype in _DIRECTORY_TYPES:
            path = repo_dir(rtype, scope, user_id, conv_id) / name
            if not path.exists():
                return False
            shutil.rmtree(path)
            self._invalidate_list_cache()
            return True

        path = repo_file(rtype, name, scope, user_id, conv_id)
        if not path.exists():
            return False
        path.unlink()
        self._invalidate_list_cache()
        return True

    def list(self, rtype: str, scope: str,
             user_id: str = "", conv_id: str = "") -> List[Dict[str, Any]]:
        """List definitions in a specific scope."""
        directory = repo_dir(rtype, scope, user_id, conv_id)
        if not directory.exists():
            return []
        cache_key = (rtype, scope, user_id or "", conv_id or "")
        signature = self._list_signature(rtype, directory)
        if signature is not None:
            with self._list_cache_lock:
                cached = self._list_cache.get(cache_key)
                if cached and cached[0] == signature:
                    return copy.deepcopy(cached[1])
        if rtype in _DIRECTORY_TYPES:
            results = []
            for p in sorted(x for x in directory.iterdir() if x.is_dir()):
                entry = self._read_directory_resource(rtype, p)
                if entry is not None:
                    entry["_scope"] = self._scope_label(scope, user_id, conv_id)
                    results.append(entry)
            if signature is not None:
                with self._list_cache_lock:
                    self._list_cache[cache_key] = (signature, copy.deepcopy(results))
            return results

        ext = "*.md" if rtype in _MARKDOWN_TYPES else "*.json"
        results = []
        for p in sorted(directory.glob(ext)):
            entry = self._read(rtype, p)
            if entry is not None:
                entry["_scope"] = self._scope_label(scope, user_id, conv_id)
                results.append(entry)
        if signature is not None:
            with self._list_cache_lock:
                self._list_cache[cache_key] = (signature, copy.deepcopy(results))
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

    def list_all_owners(self, rtype: str,
                        conv_pairs=None) -> List[Dict[str, Any]]:
        """Admin cross-user listing: every owner's definitions of one type.

        Read-only. Each entry is tagged (in addition to the usual ``_scope``)
        with ``_owner_id`` (the owning user, "" for global) and ``_conv_id``
        (set for conversation scope).

        User scope is enumerated from the filesystem. Conversation scope is
        enumerated only from ``conv_pairs`` -- an iterable of
        ``(owner_user_id, conv_id)`` supplied by the caller (the real
        conversation index). This avoids the directory-type ambiguity where a
        user-scope resource directory could be mistaken for a conversation id.
        """
        def _tag(entry, owner_id, conv_id):
            entry["_owner_id"] = owner_id
            entry["_conv_id"] = conv_id
            return entry

        results: List[Dict[str, Any]] = [
            _tag(e, "", "") for e in self.list(rtype, SCOPE_GLOBAL)]

        users_root = _paths.REPOSITORY_DIR / rtype / "users"
        if users_root.is_dir():
            for udir in sorted(x for x in users_root.iterdir() if x.is_dir()):
                uid = udir.name
                for e in self.list(rtype, SCOPE_USER, user_id=uid):
                    results.append(_tag(e, uid, ""))

        for pair in (conv_pairs or []):
            try:
                uid, cid = pair
            except (TypeError, ValueError):
                continue
            if not uid or not cid:
                continue
            for e in self.list(rtype, SCOPE_CONV, user_id=uid, conv_id=cid):
                results.append(_tag(e, uid, cid))
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
