"""Static validation of PawFlow flow definitions (authoring layer).

The single domain validator shared by the Web Flow Editor, agent tools,
the CLI, PFP validation, publish and tests. It works on the JSON
definition — never on a parsed ``Flow`` — and is deliberately STATIC:

- it never resolves ``${...}`` expressions (secrets stay references);
- it never opens connections or starts services;
- it only instantiates task classes to read their parameter schema, and
  does so with expression-bearing values blanked out.

Publish-time validation (``FlowAuthoringService.publish``) adds a real
``FlowParser.parse`` on top of this report.

Every problem is structured::

    {"severity": "error" | "warning", "code": "missing_required_parameter",
     "message": "...", "entity_type": "task", "entity_id": "infer_ai",
     "field": "service"}

Relations are identified exactly like runtime queues:
``conn_<source>__<relationship>__<target>``.
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

ERROR = "error"
WARNING = "warning"


def problem(severity: str, code: str, message: str, *,
            entity_type: str = "flow", entity_id: str = "",
            field: str = "") -> Dict[str, str]:
    return {
        "severity": severity,
        "code": code,
        "message": message,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "field": field,
    }


def relation_connection_id(source: str, relationship: str, target: str) -> str:
    """Same identity as ``core.connection.Connection.connection_id``."""
    return f"conn_{source}__{relationship}__{target}"


def normalize_relation(rel: Dict[str, Any]) -> Dict[str, Any]:
    """Editor shape (from/to/type) from either editor or package shape
    (source/target/relationships) — mirrors ``FlowParser._normalize_relations``."""
    source = rel.get("from") or rel.get("source") or ""
    target = rel.get("to") or rel.get("target") or ""
    rel_type = rel.get("type")
    if rel_type is None:
        rels = rel.get("relationships")
        if isinstance(rels, list) and rels:
            rel_type = rels[0]
    return {"from": str(source), "to": str(target),
            "type": str(rel_type or "success")}


def static_task_schema(task_class, parameters: Optional[Dict[str, Any]] = None
                       ) -> Dict[str, Any]:
    """Parameter schema of ``task_class`` for the CURRENT configuration.

    Schemas may depend on ``self.config`` (e.g. the selected service), so
    the class is instantiated with the given parameters — with every
    expression-bearing value blanked so nothing is resolved.
    """
    safe = {}
    for key, value in (parameters or {}).items():
        safe[key] = "" if isinstance(value, str) and "${" in value else value
    try:
        return dict(task_class(safe).get_parameter_schema() or {})
    except Exception:
        logger.debug("schema via constructor failed for %s", task_class, exc_info=True)
    try:
        instance = task_class.__new__(task_class)
        instance.config = {}
        return dict(instance.get_parameter_schema() or {})
    except Exception:
        logger.debug("schema via __new__ failed for %s", task_class, exc_info=True)
        return {}


def _collect_task_ids(tasks: Dict[str, Any], groups: Dict[str, Any],
                      problems: List[Dict[str, str]]) -> set:
    """All task ids, root + inline groups (recursively). V1 rule: task ids
    are unique flow-wide, no namespacing."""
    seen = {}

    def _add(task_id: str, owner: str):
        if task_id in seen:
            problems.append(problem(
                ERROR, "duplicate_task_id",
                f"Task id '{task_id}' is used in '{seen[task_id]}' and '{owner}'; "
                "task ids must be unique across the whole flow",
                entity_type="task", entity_id=task_id))
            return
        seen[task_id] = owner

    for task_id in tasks:
        _add(str(task_id), "root")

    def _walk(group_id: str, group: Dict[str, Any]):
        if not isinstance(group, dict) or group.get("flow_ref"):
            return  # subflow internals belong to the referenced flow
        for task_id in (group.get("tasks") or {}):
            _add(str(task_id), f"group {group_id}")
        for child in (group.get("child_groups") or []):
            if isinstance(child, dict):
                _walk(str(child.get("id") or child.get("name") or "?"), child)

    for group_id, group in groups.items():
        _walk(str(group_id), group)
    return set(seen)


class FlowDefinitionValidator:
    """Static, side-effect-free validation of a flow JSON definition."""

    @classmethod
    def validate(cls, definition: Any, *,
                 task_types: Optional[set] = None,
                 service_types: Optional[set] = None) -> Dict[str, Any]:
        """Return ``{ok, errors, warnings, problems}``.

        ``task_types`` / ``service_types`` default to the registered
        factories; pass explicit sets to validate without a runtime.
        """
        problems: List[Dict[str, str]] = []
        if not isinstance(definition, dict):
            problems.append(problem(ERROR, "invalid_definition",
                                    "Flow definition must be a JSON object"))
            return cls._report(problems)

        if task_types is None or service_types is None:
            from core import TaskFactory, ServiceFactory
            if task_types is None:
                task_types = set(TaskFactory.list_types())
            if service_types is None:
                service_types = set(ServiceFactory.list_types())

        if not str(definition.get("name") or "").strip():
            problems.append(problem(WARNING, "missing_flow_name",
                                    "Flow has no name", field="name"))

        tasks = definition.get("tasks", {})
        if not isinstance(tasks, dict):
            problems.append(problem(ERROR, "invalid_field",
                                    "'tasks' must be an object", field="tasks"))
            tasks = {}
        services = definition.get("services", {}) or {}
        if not isinstance(services, dict):
            problems.append(problem(ERROR, "invalid_field",
                                    "'services' must be an object", field="services"))
            services = {}
        groups = definition.get("groups", {}) or {}
        if not isinstance(groups, dict):
            problems.append(problem(ERROR, "invalid_field",
                                    "'groups' must be an object", field="groups"))
            groups = {}
        relations = definition.get("relations", []) or []
        if not isinstance(relations, list):
            problems.append(problem(ERROR, "invalid_field",
                                    "'relations' must be a list", field="relations"))
            relations = []
        parameters = definition.get("parameters", {}) or {}
        if not isinstance(parameters, dict):
            problems.append(problem(ERROR, "invalid_field",
                                    "'parameters' must be an object", field="parameters"))

        all_task_ids = _collect_task_ids(tasks, groups, problems)
        # Subflow groups are addressable as relation endpoints (the parser
        # synthesizes an executeFlow task under the group id).
        endpoint_ids = set(all_task_ids) | {
            str(gid) for gid, g in groups.items()
            if isinstance(g, dict) and g.get("flow_ref")}

        cls._validate_services(services, service_types, problems)
        cls._validate_tasks(tasks, task_types, services, problems)

        connected = set()
        seen_connections = set()
        for index, rel in enumerate(relations):
            if not isinstance(rel, dict):
                problems.append(problem(ERROR, "invalid_relation",
                                        f"Relation #{index} must be an object",
                                        entity_type="relation", entity_id=str(index)))
                continue
            norm = normalize_relation(rel)
            conn_id = relation_connection_id(norm["from"], norm["type"], norm["to"])
            if not norm["from"] or norm["from"] not in endpoint_ids:
                problems.append(problem(
                    ERROR, "unknown_relation_source",
                    f"Relation points from unknown task '{norm['from']}'",
                    entity_type="relation", entity_id=conn_id, field="from"))
            if not norm["to"] or norm["to"] not in endpoint_ids:
                problems.append(problem(
                    ERROR, "unknown_relation_target",
                    f"Relation points to unknown task '{norm['to']}'",
                    entity_type="relation", entity_id=conn_id, field="to"))
            if conn_id in seen_connections:
                problems.append(problem(
                    WARNING, "duplicate_relation",
                    f"Relation {norm['from']} --{norm['type']}--> {norm['to']} "
                    "is declared more than once",
                    entity_type="relation", entity_id=conn_id))
            seen_connections.add(conn_id)
            connected.add(norm["from"])
            connected.add(norm["to"])

        for field, code in (("entries", "unknown_entry"), ("exits", "unknown_exit")):
            values = definition.get(field, []) or []
            if not isinstance(values, list):
                problems.append(problem(ERROR, "invalid_field",
                                        f"'{field}' must be a list", field=field))
                continue
            for task_id in values:
                if str(task_id) not in endpoint_ids:
                    problems.append(problem(
                        ERROR, code,
                        f"{field[:-1].capitalize()} task '{task_id}' does not exist",
                        entity_type="task", entity_id=str(task_id), field=field))

        if len(tasks) > 1:
            for task_id in tasks:
                if str(task_id) not in connected:
                    problems.append(problem(
                        WARNING, "task_disconnected",
                        f"Task '{task_id}' has no incoming or outgoing relation",
                        entity_type="task", entity_id=str(task_id)))

        return cls._report(problems)

    # ── parts ─────────────────────────────────────────────────────

    @classmethod
    def _validate_services(cls, services, service_types, problems):
        for service_id, service in services.items():
            sid = str(service_id)
            if not isinstance(service, dict):
                problems.append(problem(ERROR, "invalid_service",
                                        f"Service '{sid}' must be an object",
                                        entity_type="service", entity_id=sid))
                continue
            stype = service.get("type")
            if not stype:
                problems.append(problem(ERROR, "missing_service_type",
                                        f"Service '{sid}' has no type",
                                        entity_type="service", entity_id=sid,
                                        field="type"))
            elif stype not in service_types:
                problems.append(problem(ERROR, "unknown_service_type",
                                        f"Service '{sid}' has unknown type '{stype}'",
                                        entity_type="service", entity_id=sid,
                                        field="type"))

    @classmethod
    def _validate_tasks(cls, tasks, task_types, services, problems):
        from core import TaskFactory
        for task_id, task in tasks.items():
            tid = str(task_id)
            if not isinstance(task, dict):
                problems.append(problem(ERROR, "invalid_task",
                                        f"Task '{tid}' must be an object",
                                        entity_type="task", entity_id=tid))
                continue
            ttype = task.get("type")
            params = task.get("parameters", {})
            if params is None:
                params = {}
            if not isinstance(params, dict):
                problems.append(problem(ERROR, "invalid_parameters",
                                        f"Task '{tid}' parameters must be an object",
                                        entity_type="task", entity_id=tid,
                                        field="parameters"))
                params = {}
            if not ttype:
                problems.append(problem(ERROR, "missing_task_type",
                                        f"Task '{tid}' has no type",
                                        entity_type="task", entity_id=tid,
                                        field="type"))
                continue
            if ttype not in task_types:
                problems.append(problem(ERROR, "unknown_task_type",
                                        f"Task '{tid}' has unknown type '{ttype}'",
                                        entity_type="task", entity_id=tid,
                                        field="type"))
                continue
            try:
                task_class = TaskFactory.get(ttype)
            except Exception:
                task_class = None
            if task_class is not None:
                schema = static_task_schema(task_class, params)
                for name, spec in schema.items():
                    if not isinstance(spec, dict) or not spec.get("required"):
                        continue
                    value = params.get(name, None)
                    if value is None or value == "":
                        problems.append(problem(
                            ERROR, "missing_required_parameter",
                            f"Required parameter '{name}' is missing",
                            entity_type="task", entity_id=tid, field=name))
            service_ref = params.get("service")
            if (services and isinstance(service_ref, str) and service_ref
                    and "${" not in service_ref and service_ref not in services):
                problems.append(problem(
                    WARNING, "unknown_service_ref",
                    f"Task '{tid}' references service '{service_ref}' which is "
                    "not declared in this flow (it must exist as a user/global service)",
                    entity_type="task", entity_id=tid, field="service"))

    @staticmethod
    def _report(problems: List[Dict[str, str]]) -> Dict[str, Any]:
        errors = sum(1 for p in problems if p["severity"] == ERROR)
        warnings = sum(1 for p in problems if p["severity"] == WARNING)
        return {"ok": errors == 0, "errors": errors, "warnings": warnings,
                "problems": problems}


__all__ = ["FlowDefinitionValidator", "problem", "relation_connection_id",
           "normalize_relation", "static_task_schema", "ERROR", "WARNING"]
