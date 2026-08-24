"""FlowAuthoringService — the domain layer of the Flow Editor.

One service, shared by the Web UI, agent tools and the CLI, in front of
``ScopedRepository``::

    Web UI ------\\
    Agent tools --+--> FlowAuthoringService --> ScopedRepository
    CLI ---------/

Rules (docs/flow_editor.md):
- published versions are IMMUTABLE: editing always goes through a draft,
  publishing always creates a new version (``publish_flow_version`` /
  ``create_flow`` refuse an existing version);
- drafts live outside the repository (``data/runtime/flow_editor_drafts/
  <user_id>/<draft_id>.json``) and carry a monotonic ``revision``;
- saving is OPTIMISTICALLY LOCKED: the caller sends the ``base_revision``
  it loaded; a mismatch raises ``DraftConflict`` (HTTP 409
  ``draft_changed_elsewhere``) — never last-writer-wins;
- the JSON definition is the source of truth: the service stores what it
  is given and never rebuilds a minimal document, so unknown/future
  fields survive a load → save round-trip untouched;
- static validation (``FlowDefinitionValidator``) never resolves secrets;
  publish adds a full ``FlowParser.parse`` on top.
"""

import copy
import json
import logging
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import core.paths as _paths
from core.flow_definition_validator import (
    FlowDefinitionValidator,
    normalize_relation,
    problem,
    relation_connection_id,
    static_service_schema,
    static_task_relationships,
    static_task_schema,
)

logger = logging.getLogger(__name__)

_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+([-.][0-9A-Za-z.-]+)?$")
_PACKAGE_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]*(\.[A-Za-z0-9_][A-Za-z0-9_-]*)*$")
_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]*$")
_DRAFT_ID_RE = re.compile(r"^d_[0-9a-f]{12}$")

# Repository-managed keys: rewritten on publish, never part of a diff.
_REPO_KEYS = {"fqn", "package", "created_at", "updated_at", "_scope"}
# Top-level collections diffed by member, everything else is metadata.
_COLLECTIONS = ("tasks", "services", "parameters", "groups")
_STRUCTURAL = set(_COLLECTIONS) | {"relations", "entries", "exits", "layout", "version"}


class DraftNotFound(KeyError):
    """No such draft for this user."""


class DraftConflict(Exception):
    """The draft was saved elsewhere since the caller loaded it."""

    code = "draft_changed_elsewhere"

    def __init__(self, draft_id: str, base_revision: int, current_revision: int):
        super().__init__(
            f"Draft {draft_id} is at revision {current_revision}, "
            f"you edited revision {base_revision}")
        self.draft_id = draft_id
        self.base_revision = base_revision
        self.current_revision = current_revision


class FlowValidationFailed(ValueError):
    """Publish refused: the definition has validation errors."""

    def __init__(self, report: Dict[str, Any]):
        super().__init__(f"{report.get('errors', 0)} validation error(s)")
        self.report = report


def normalize_scope(scope: str) -> str:
    """UI says ``conversation``, the repository says ``conv``."""
    scope = str(scope or "").strip().lower()
    if scope == "conversation":
        return "conv"
    if scope not in ("global", "user", "conv"):
        raise ValueError(f"Invalid scope: {scope!r}")
    return scope


def split_fqn(fqn: str):
    """``package.name[:version]`` → (qualified_name, version)."""
    fqn = str(fqn or "").strip()
    version = ""
    if ":" in fqn:
        fqn, version = fqn.rsplit(":", 1)
    if "." not in fqn:
        raise ValueError("Flow name must be qualified: package.name[:version]")
    return fqn, version


def _agent_workflow_starter(name: str) -> Dict[str, Any]:
    """Return a runnable, safe v1 agent-workflow editing starter."""
    return {
        "kind": "agent_workflow",
        "agent_contract": {
            "version": 1,
            "input": {"port": "agent_request"},
            "terminal": {"port": "agent_terminal"},
            "parameters": {},
            "supported_preempt_policies": ["queue", "checkpoint", "restart"],
            "allowed_effects": ["resource.read"],
        },
        "tasks": {
            "agent_request": {
                "type": "inputPort",
                "parameters": {"port_name": "agent_request"},
            },
            "validate_request": {
                "type": "agentWorkflowInput", "parameters": {},
            },
            "draft_response": {
                "type": "workflowFakeLLM",
                "parameters": {"response_prefix": f"{name}: "},
            },
            "complete_turn": {
                "type": "completeAgentTurn", "parameters": {},
            },
            "agent_terminal": {
                "type": "outputPort",
                "parameters": {"port_name": "agent_terminal"},
            },
        },
        "relations": [
            {"from": "agent_request", "to": "validate_request", "type": "success"},
            {"from": "validate_request", "to": "draft_response", "type": "success"},
            {"from": "draft_response", "to": "complete_turn", "type": "success"},
            {"from": "complete_turn", "to": "agent_terminal", "type": "success"},
        ],
        "entries": ["agent_request"],
        "exits": ["agent_terminal"],
        "layout": {"nodes": {
            "agent_request": {"x": 40, "y": 120},
            "validate_request": {"x": 260, "y": 120},
            "draft_response": {"x": 480, "y": 120},
            "complete_turn": {"x": 700, "y": 120},
            "agent_terminal": {"x": 920, "y": 120},
        }},
    }


class FlowAuthoringService:
    """Draft lifecycle, validation, diff, versioning and catalogs."""

    _instance = None
    _instance_lock = threading.Lock()

    def __init__(self, repository=None, drafts_dir: Optional[Path] = None):
        self._repo = repository
        self._drafts_dir = Path(drafts_dir) if drafts_dir else None
        self._lock = threading.RLock()

    @classmethod
    def instance(cls) -> "FlowAuthoringService":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def reset(cls):
        with cls._instance_lock:
            cls._instance = None

    # ── plumbing ─────────────────────────────────────────────────

    @property
    def repo(self):
        if self._repo is None:
            from core.repository import ScopedRepository
            return ScopedRepository.instance()
        return self._repo

    @property
    def drafts_dir(self) -> Path:
        return self._drafts_dir or _paths.flow_editor_drafts_dir()

    def _draft_path(self, user_id: str, draft_id: str) -> Path:
        if not user_id:
            raise ValueError("user_id is required")
        if not _DRAFT_ID_RE.match(str(draft_id or "")):
            raise DraftNotFound(draft_id)
        return self.drafts_dir / str(user_id) / f"{draft_id}.json"

    def _read_draft(self, user_id: str, draft_id: str) -> Dict[str, Any]:
        path = self._draft_path(user_id, draft_id)
        if not path.exists():
            raise DraftNotFound(draft_id)
        with open(path, "r", encoding="utf-8") as fh:
            draft = json.load(fh)
        if draft.get("user_id") != user_id:
            raise DraftNotFound(draft_id)
        return draft

    def _write_draft(self, draft: Dict[str, Any]) -> None:
        path = self._draft_path(draft["user_id"], draft["draft_id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(draft, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

    def _new_draft(self, *, user_id: str, flow: str, scope: str, conv_id: str,
                   base_version: str, definition: Dict[str, Any],
                   extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        now = time.time()
        draft = {
            "draft_id": "d_" + uuid.uuid4().hex[:12],
            "user_id": user_id,
            "flow": flow,
            "scope": scope,
            "conv_id": conv_id or "",
            "base_version": base_version or "",
            "revision": 0,
            "created_at": now,
            "updated_at": now,
            "definition": definition,
        }
        if extra:
            draft.update(extra)
        with self._lock:
            self._write_draft(draft)
        return copy.deepcopy(draft)

    @staticmethod
    def _check_identifiers(package: str, name: str, version: str) -> None:
        if not _PACKAGE_RE.match(str(package or "")):
            raise ValueError(f"Invalid package name: {package!r}")
        if not _NAME_RE.match(str(name or "")):
            raise ValueError(f"Invalid flow name: {name!r}")
        if not _VERSION_RE.match(str(version or "")):
            raise ValueError(f"Invalid version (expected MAJOR.MINOR.PATCH): {version!r}")

    def _flow_exists(self, flow: str, scope: str, user_id: str, conv_id: str) -> bool:
        return bool(self.repo.list_flow_versions(
            flow, scope, user_id=user_id, conv_id=conv_id))

    # ── published flows ──────────────────────────────────────────

    def load(self, fqn: str, scope: str, user_id: str = "",
             conv_id: str = "") -> Dict[str, Any]:
        """A deep copy of a published version (latest when unversioned)."""
        scope = normalize_scope(scope)
        raw = self.repo.get_flow(fqn, scope, user_id=user_id, conv_id=conv_id)
        if raw is None:
            raise KeyError(f"Flow {fqn} not found in scope {scope}")
        return copy.deepcopy(raw)

    def versions(self, fqn: str, scope: str, user_id: str = "",
                 conv_id: str = "") -> Dict[str, Any]:
        scope = normalize_scope(scope)
        flow, _ = split_fqn(fqn)
        listed = self.repo.list_flow_versions(flow, scope, user_id=user_id, conv_id=conv_id)
        latest = self.repo.get_flow(flow, scope, user_id=user_id, conv_id=conv_id) or {}
        return {"flow": flow, "scope": scope, "versions": listed,
                "latest": latest.get("version", "")}

    def delete_version(self, fqn: str, scope: str, user_id: str = "",
                       conv_id: str = "") -> Dict[str, Any]:
        """Delete one published version (``package.name:version``).

        Versions are never edited, only added or deleted. The repository
        refuses the last remaining version and re-points ``latest`` when the
        latest one goes.
        """
        scope = normalize_scope(scope)
        flow, version = split_fqn(fqn)
        if not version:
            raise ValueError("Flow name must include the version to delete: "
                             "package.name:version")
        return self.repo.delete_flow_version(
            f"{flow}:{version}", scope, user_id=user_id, conv_id=conv_id)

    def new(self, package: str, name: str, version: str, scope: str,
            user_id: str, conv_id: str = "", description: str = "",
            template_kind: str = "standard") -> Dict[str, Any]:
        """A draft for a flow that does not exist yet (nothing is published)."""
        scope = normalize_scope(scope)
        self._check_identifiers(package, name, version)
        flow = f"{package}.{name}"
        if self._flow_exists(flow, scope, user_id, conv_id):
            raise ValueError(f"Flow {flow} already exists in scope {scope}; "
                             "open a draft of it instead")
        definition = {
            "id": name, "name": name, "version": version,
            "description": description or "",
            "parameters": {}, "tasks": {}, "services": {}, "groups": {},
            "relations": [], "entries": [], "exits": [], "layout": {},
        }
        if template_kind == "agent_workflow":
            definition.update(_agent_workflow_starter(name))
        elif template_kind != "standard":
            raise ValueError("template_kind must be standard or agent_workflow")
        return self._new_draft(user_id=user_id, flow=flow, scope=scope,
                               conv_id=conv_id, base_version="",
                               definition=definition)

    def fork(self, source_fqn: str, source_scope: str, package: str, name: str,
             version: str, scope: str, user_id: str, conv_id: str = "",
             source_user_id: str = "", source_conv_id: str = "") -> Dict[str, Any]:
        """Copy a (possibly read-only) flow into a new draft the user owns."""
        scope = normalize_scope(scope)
        self._check_identifiers(package, name, version)
        source = self.load(source_fqn, source_scope,
                           user_id=source_user_id or user_id,
                           conv_id=source_conv_id or conv_id)
        flow = f"{package}.{name}"
        if self._flow_exists(flow, scope, user_id, conv_id):
            raise ValueError(f"Flow {flow} already exists in scope {scope}")
        definition = {k: v for k, v in source.items() if k not in _REPO_KEYS}
        definition["id"] = name
        definition["name"] = name
        definition["version"] = version
        return self._new_draft(
            user_id=user_id, flow=flow, scope=scope, conv_id=conv_id,
            base_version="", definition=definition,
            extra={"forked_from": {"fqn": source.get("fqn") or source_fqn,
                                   "scope": normalize_scope(source_scope)}})

    # ── drafts ───────────────────────────────────────────────────

    def create_draft(self, fqn: str, scope: str, user_id: str, conv_id: str = "",
                     reuse_existing: bool = True) -> Dict[str, Any]:
        """Open a draft of a published version (the latest when unversioned).

        With ``reuse_existing`` an existing draft of the same flow/scope/conv
        is returned (``reused: True``) instead of silently starting a second
        working copy.
        """
        scope = normalize_scope(scope)
        flow, _ = split_fqn(fqn)
        if reuse_existing:
            for existing in self.list_drafts(user_id):
                if (existing["flow"] == flow and existing["scope"] == scope
                        and (existing.get("conv_id") or "") == (conv_id or "")):
                    draft = self.load_draft(existing["draft_id"], user_id)
                    draft["reused"] = True
                    return draft
        published = self.load(fqn, scope, user_id=user_id, conv_id=conv_id)
        base_version = str(published.get("version") or "")
        definition = {k: v for k, v in published.items() if k not in _REPO_KEYS}
        draft = self._new_draft(user_id=user_id, flow=flow, scope=scope,
                                conv_id=conv_id, base_version=base_version,
                                definition=definition)
        draft["reused"] = False
        return draft

    def load_draft(self, draft_id: str, user_id: str) -> Dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._read_draft(user_id, draft_id))

    def list_drafts(self, user_id: str) -> List[Dict[str, Any]]:
        """Summaries (no definition) of the user's drafts, newest first."""
        if not user_id:
            raise ValueError("user_id is required")
        directory = self.drafts_dir / str(user_id)
        if not directory.exists():
            return []
        rows = []
        for path in directory.glob("d_*.json"):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    draft = json.load(fh)
            except (OSError, ValueError):
                logger.warning("unreadable flow draft %s", path)
                continue
            definition = draft.get("definition") or {}
            rows.append({
                "draft_id": draft.get("draft_id", path.stem),
                "flow": draft.get("flow", ""),
                "scope": draft.get("scope", ""),
                "conv_id": draft.get("conv_id", ""),
                "base_version": draft.get("base_version", ""),
                "target_version": str(definition.get("version") or ""),
                "revision": int(draft.get("revision", 0) or 0),
                "created_at": draft.get("created_at", 0),
                "updated_at": draft.get("updated_at", 0),
                "task_count": len(definition.get("tasks") or {}),
                "forked_from": draft.get("forked_from"),
            })
        rows.sort(key=lambda r: r["updated_at"], reverse=True)
        return rows

    def save_draft(self, draft_id: str, user_id: str, definition: Dict[str, Any],
                   base_revision: int) -> Dict[str, Any]:
        """Store ``definition`` verbatim. Optimistic locking on ``base_revision``."""
        if not isinstance(definition, dict):
            raise ValueError("definition must be a JSON object")
        try:
            base_revision = int(base_revision)
        except (TypeError, ValueError):
            raise ValueError("base_revision is required")
        with self._lock:
            draft = self._read_draft(user_id, draft_id)
            current = int(draft.get("revision", 0) or 0)
            if base_revision != current:
                raise DraftConflict(draft_id, base_revision, current)
            draft["definition"] = copy.deepcopy(definition)
            draft["revision"] = current + 1
            draft["updated_at"] = time.time()
            self._write_draft(draft)
            return copy.deepcopy(draft)

    def discard_draft(self, draft_id: str, user_id: str) -> bool:
        with self._lock:
            try:
                path = self._draft_path(user_id, draft_id)
            except DraftNotFound:
                return False
            if not path.exists():
                return False
            path.unlink()
            return True

    # ── validation / diff ────────────────────────────────────────

    @staticmethod
    def validate(definition: Dict[str, Any]) -> Dict[str, Any]:
        """Static validation — never resolves secrets or opens connections."""
        if definition.get("kind") == "agent_workflow":
            from core.workflow_agent_resources import (
                validate_agent_workflow_definition,
            )
            return validate_agent_workflow_definition(definition)
        return FlowDefinitionValidator.validate(definition)

    def validate_draft(self, draft_id: str, user_id: str) -> Dict[str, Any]:
        return self.validate(self.load_draft(draft_id, user_id)["definition"])

    @staticmethod
    def diff(base: Optional[Dict[str, Any]], definition: Dict[str, Any]
             ) -> Dict[str, Any]:
        """Structured changes ``base → definition``.

        Each change: ``{op: added|removed|changed, kind, id, runtime_impact}``
        (``runtime_impact`` is False for layout/metadata-only changes: they
        never require a hot-swap). Relations are keyed by ``connection_id``.
        """
        base = base or {}
        changes: List[Dict[str, Any]] = []

        def _emit(op, kind, ident, impact=True, **extra):
            row = {"op": op, "kind": kind, "id": ident, "runtime_impact": impact}
            row.update(extra)
            changes.append(row)

        for kind in _COLLECTIONS:
            before = base.get(kind) or {}
            after = definition.get(kind) or {}
            if not isinstance(before, dict):
                before = {}
            if not isinstance(after, dict):
                after = {}
            for ident in sorted(set(before) | set(after)):
                if ident not in before:
                    _emit("added", kind[:-1], ident)
                elif ident not in after:
                    _emit("removed", kind[:-1], ident)
                elif before[ident] != after[ident]:
                    fields = FlowAuthoringService._changed_fields(before[ident], after[ident])
                    _emit("changed", kind[:-1], ident, fields=fields)

        def _rel_map(rels):
            out = {}
            for rel in rels or []:
                if isinstance(rel, dict):
                    norm = normalize_relation(rel)
                    out[relation_connection_id(norm["from"], norm["type"], norm["to"])] = rel
            return out

        before_rel, after_rel = _rel_map(base.get("relations")), _rel_map(definition.get("relations"))
        for ident in sorted(set(before_rel) | set(after_rel)):
            if ident not in before_rel:
                _emit("added", "relation", ident)
            elif ident not in after_rel:
                _emit("removed", "relation", ident)
            elif before_rel[ident] != after_rel[ident]:
                _emit("changed", "relation", ident,
                      fields=FlowAuthoringService._changed_fields(before_rel[ident], after_rel[ident]))

        for kind in ("entries", "exits"):
            before = set(map(str, base.get(kind) or []))
            after = set(map(str, definition.get(kind) or []))
            for ident in sorted(after - before):
                _emit("added", kind[:-1], ident)
            for ident in sorted(before - after):
                _emit("removed", kind[:-1], ident)

        if (base.get("layout") or {}) != (definition.get("layout") or {}):
            _emit("changed", "layout", "layout", impact=False)

        meta_keys = (set(base) | set(definition)) - _STRUCTURAL - _REPO_KEYS
        for key in sorted(meta_keys):
            if base.get(key) != definition.get(key):
                op = ("added" if key not in base
                      else "removed" if key not in definition else "changed")
                _emit(op, "metadata", key, impact=key not in ("name", "description"))

        return {"changes": changes, "count": len(changes),
                "runtime_impact": any(c["runtime_impact"] for c in changes)}

    @staticmethod
    def _changed_fields(before: Any, after: Any, prefix: str = "") -> List[str]:
        if not isinstance(before, dict) or not isinstance(after, dict):
            return [prefix.rstrip(".")] if prefix else ["value"]
        fields = []
        for key in sorted(set(before) | set(after)):
            if before.get(key) == after.get(key):
                continue
            if isinstance(before.get(key), dict) and isinstance(after.get(key), dict):
                fields.extend(FlowAuthoringService._changed_fields(
                    before[key], after[key], f"{prefix}{key}."))
            else:
                fields.append(f"{prefix}{key}")
        return fields

    def diff_draft(self, draft_id: str, user_id: str) -> Dict[str, Any]:
        """Draft vs the version it was created from (everything is ``added``
        for a new or forked flow)."""
        draft = self.load_draft(draft_id, user_id)
        base = None
        if draft.get("base_version"):
            try:
                base = self.load(f"{draft['flow']}:{draft['base_version']}",
                                 draft["scope"], user_id=user_id,
                                 conv_id=draft.get("conv_id", ""))
            except KeyError:
                base = None
        result = self.diff(base, draft["definition"])
        result["base_version"] = draft.get("base_version", "")
        return result

    # ── publish ──────────────────────────────────────────────────

    def publish(self, draft_id: str, user_id: str, version: str = "",
                *, parse: bool = True, keep_draft: bool = False) -> Dict[str, Any]:
        """Publish the draft as a NEW immutable version.

        Static validation errors and (when ``parse``) a failing
        ``FlowParser.parse`` raise ``FlowValidationFailed``; an existing
        version is refused by the repository (``ValueError``).
        """
        with self._lock:
            draft = self._read_draft(user_id, draft_id)
        definition = copy.deepcopy(draft["definition"])
        version = str(version or definition.get("version") or "").strip()
        if not _VERSION_RE.match(version):
            raise ValueError(f"Invalid version (expected MAJOR.MINOR.PATCH): {version!r}")
        flow, scope, conv_id = draft["flow"], draft["scope"], draft.get("conv_id", "")
        package, name = flow.rsplit(".", 1)

        definition["version"] = version
        definition.setdefault("id", name)
        definition.setdefault("name", name)
        definition["fqn"] = f"{flow}:{version}"
        if definition.get("kind") == "agent_workflow":
            from core.workflow_agent_resources import (
                validate_agent_workflow_definition,
            )
            report = validate_agent_workflow_definition(definition)
        else:
            report = self.validate(definition)
        if not report["ok"]:
            raise FlowValidationFailed(report)
        if parse:
            parse_problem = self._publish_parse(definition)
            if parse_problem is not None:
                report["problems"].append(parse_problem)
                report["errors"] += 1
                report["ok"] = False
                raise FlowValidationFailed(report)

        for key in _REPO_KEYS:
            definition.pop(key, None)
        fqn = f"{flow}:{version}"
        if self._flow_exists(flow, scope, user_id, conv_id):
            entry = self.repo.publish_flow_version(
                fqn, scope, definition, user_id=user_id, conv_id=conv_id)
        else:
            entry = self.repo.create_flow(
                fqn, scope, definition, user_id=user_id, conv_id=conv_id)
        if not keep_draft:
            self.discard_draft(draft_id, user_id)
        return {"fqn": fqn, "flow": flow, "version": version, "scope": scope,
                "base_version": draft.get("base_version", ""),
                "draft_discarded": not keep_draft, "entry": entry}

    @staticmethod
    def _publish_parse(definition: Dict[str, Any]):
        """Publish-time validation: a real parse (may resolve expressions)."""
        try:
            from engine.parser import FlowParser
            FlowParser.parse(copy.deepcopy(definition))
        except Exception as exc:  # the parser raises many concrete types
            return problem("error", "parse_error", f"Flow does not parse: {exc}")
        return None

    # ── catalogs / schemas ───────────────────────────────────────

    @staticmethod
    def task_catalog() -> List[Dict[str, Any]]:
        """Palette entries derived from TaskFactory + TASK_CATEGORIES."""
        from core import TaskFactory
        from core.task_categories import TASK_CATEGORIES
        rows = []
        for task_type in TaskFactory.list_types():
            cls = TaskFactory.get(task_type)
            rows.append({
                "type": getattr(cls, "TYPE", task_type),
                "name": getattr(cls, "NAME", task_type),
                "description": getattr(cls, "DESCRIPTION", ""),
                "icon": getattr(cls, "ICON", ""),
                "category": TASK_CATEGORIES.get(task_type, "Plugins"),
            })
        rows.sort(key=lambda r: (r["category"], r["name"].lower()))
        return rows

    @staticmethod
    def task_schema(task_type: str, parameters: Optional[Dict[str, Any]] = None
                    ) -> Dict[str, Any]:
        """Schema for the CURRENT configuration (schemas may depend on it)."""
        from core import TaskFactory
        if not task_type or task_type not in TaskFactory.list_types():
            raise KeyError(f"Unknown task type: {task_type}")
        cls = TaskFactory.get(task_type)
        current = parameters or {}
        return {"type": task_type,
                "schema": static_task_schema(cls, current),
                "relationships": static_task_relationships(cls, current)}

    @staticmethod
    def service_catalog() -> List[Dict[str, Any]]:
        from core import ServiceFactory
        from tasks.ai.actions.service_flow import _service_category, _service_type_sort_key
        rows = []
        for svc_type in ServiceFactory.list_types():
            cls = ServiceFactory.get(svc_type)
            rows.append({
                "type": getattr(cls, "TYPE", svc_type),
                "name": getattr(cls, "NAME", svc_type),
                "description": getattr(cls, "DESCRIPTION", ""),
                "category": _service_category(svc_type, cls),
            })
        rows.sort(key=_service_type_sort_key)
        return rows

    @staticmethod
    def service_schema(service_type: str,
                       parameters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        from core import ServiceFactory
        if not service_type or service_type not in ServiceFactory.list_types():
            raise KeyError(f"Unknown service type: {service_type}")
        cls = ServiceFactory.get(service_type)
        schema = static_service_schema(cls, parameters)
        from core.service_parameter_helpers import apply_service_parameter_helpers
        schema = apply_service_parameter_helpers(service_type, schema)
        return {"type": service_type, "schema": schema}


__all__ = ["FlowAuthoringService", "DraftConflict", "DraftNotFound",
           "FlowValidationFailed", "normalize_scope", "split_fqn"]
