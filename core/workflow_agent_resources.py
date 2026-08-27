"""WP2 resolution and validation for immutable agent workflow bindings."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from core.flow_definition_validator import ERROR, FlowDefinitionValidator, problem
from core.paths import parse_flow_fqn, repo_dir
from core.resource_identity import ResourceRef
from core.workflow_agent_contracts import (
    AgentDefinitionRuntimeDefaults,
    AgentWorkflowDefinition,
    WorkflowInstanceConfig,
    _parameter_value_matches,
)

FORBIDDEN_AGENT_WORKFLOW_TASKS = frozenset({
    "agentLoop", "cronTrigger", "executeFlow", "executeScript",
    "generateFlowFile", "httpReceiver", "installBootstrap", "shutdownTrigger",
})
_LLM_SERVICE_TYPES = frozenset({
    "llmConnection", "llmAggregator", "llmRouter",
})


def _service_matches_capability(service_type: str, capability: str) -> bool:
    return (
        service_type == capability
        or (capability == "llm" and service_type in _LLM_SERVICE_TYPES)
        or (
            capability == "llm_resolvable"
            and (
                service_type in _LLM_SERVICE_TYPES
                or service_type == "summarizer"
            )
        )
    )


@dataclass(frozen=True)
class ResolvedAgentWorkflow:
    definition: dict[str, Any]
    ref: ResourceRef


def _canonical_digest(value: dict[str, Any]) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_agent_definition_data(data: dict[str, Any]) -> dict[str, Any]:
    """Validate workflow defaults while leaving prompt-only agents unchanged."""
    if not isinstance(data, dict):
        raise TypeError("agent definition must be an object")
    defaults = data.get("runtime_defaults")
    if defaults is not None:
        from core.agent_feature_flags import validate_agent_runtime_kind

        parsed = AgentDefinitionRuntimeDefaults.from_dict(defaults)
        validate_agent_runtime_kind(parsed.kind)
    automation_roles = data.get("automation_roles")
    if automation_roles is not None:
        if (not isinstance(automation_roles, list)
                or not all(isinstance(role, str) and role.strip()
                           for role in automation_roles)
                or len(set(automation_roles)) != len(automation_roles)):
            raise ValueError("automation_roles must be a unique list of names")
    return data


def validate_pfp_workflow_agent_dependency(
        data: dict[str, Any], obj: dict[str, Any],
        package: dict[str, Any]) -> str:
    """Return the required in-package flow object id for a workflow agent."""
    validate_agent_definition_data(data)
    defaults = data.get("runtime_defaults") or {}
    if defaults.get("kind") != "workflow":
        return ""
    flow_fqn = str((defaults.get("workflow") or {}).get("flow_fqn") or "")
    flow_object = next((
        item for item in package.get("manifest", {}).get("objects", [])
        if isinstance(item, dict) and item.get("type") == "flow"
        and str(item.get("fqn") or "") == flow_fqn
    ), None)
    if flow_object is None:
        raise ValueError(
            f"workflow agent requires an in-package exact flow: {flow_fqn}")
    object_id = str(flow_object.get("id") or "")
    requires = obj.get("requires") or []
    declared = any(
        item == object_id
        or (isinstance(item, dict) and item.get("object") == object_id)
        for item in requires
    )
    if not declared:
        raise ValueError(
            f"workflow agent must declare object dependency: {object_id}")
    return object_id


def _scope_candidates(user_id: str, conversation_id: str):
    if conversation_id:
        from core.resource_store import _conv_scope_user

        owner = _conv_scope_user(conversation_id, user_id)
        yield "conversation", "conv", owner, conversation_id
    if user_id:
        yield "user", "user", user_id, ""
    yield "global", "global", "", ""


def resolve_exact_agent_workflow(
    flow_fqn: str,
    user_id: str,
    conversation_id: str,
    *,
    repository=None,
) -> ResolvedAgentWorkflow:
    """Resolve one visible exact flow version and pin its canonical digest."""
    try:
        _package, _name, version = parse_flow_fqn(str(flow_fqn or ""))
    except (AttributeError, ValueError):
        version = ""
    if not version:
        raise ValueError("workflow binding requires an exact flow version")

    if repository is None:
        from core.repository import ScopedRepository

        repository = ScopedRepository.instance()
    for public_scope, repo_scope, owner, conv_id in _scope_candidates(
            user_id, conversation_id):
        definition = repository.get_flow(
            flow_fqn, repo_scope, user_id=owner, conv_id=conv_id)
        if definition is None:
            continue
        installed = definition.get("installed_from") or {}
        ref = ResourceRef(
            schema_version=1,
            resource_type="flow",
            name=flow_fqn,
            scope=public_scope,
            owner_id=None if public_scope == "global" else owner,
            package_id=str(installed.get("package") or "") or None,
            package_version=str(installed.get("version") or "") or None,
            version=version,
            content_digest=_canonical_digest(definition),
            source_id=f"repository:{public_scope}:{flow_fqn}",
        )
        return ResolvedAgentWorkflow(definition=dict(definition), ref=ref)
    raise ValueError(f"workflow flow is not visible: {flow_fqn}")


def list_compatible_agent_workflows(
    user_id: str,
    conversation_id: str,
) -> list[dict[str, Any]]:
    """Return redacted metadata for every visible valid exact workflow version.

    Scope precedence matches :func:`resolve_exact_agent_workflow`: when the
    same exact FQN exists in more than one visible scope, only the nearest
    conversation/user/global definition is returned. Source bodies, task
    parameters, service definitions, prompts, and installed package metadata
    never cross this UI boundary.
    """
    from tasks import register_all_tasks

    register_all_tasks()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for public_scope, repo_scope, owner, conv_id in _scope_candidates(
            user_id, conversation_id):
        root = repo_dir("flows", repo_scope, owner, conv_id)
        if not root.is_dir():
            continue
        for version_file in sorted(root.rglob("versions/*.json")):
            try:
                definition = json.loads(version_file.read_text(encoding="utf-8"))
                flow_fqn = str(definition.get("fqn") or "").strip()
                package, flow_name, version = parse_flow_fqn(flow_fqn)
                if not version or flow_fqn in seen:
                    continue
                if definition.get("kind") != "agent_workflow":
                    continue
                report = validate_agent_workflow_definition(definition)
                if not report["ok"]:
                    continue
                parsed = AgentWorkflowDefinition.from_dict({
                    key: definition.get(key)
                    for key in ("id", "name", "version", "kind", "agent_contract")
                })
                if parsed.id != flow_name or parsed.version != version:
                    continue
                contract = parsed.agent_contract
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
            seen.add(flow_fqn)
            rows.append({
                "flow_fqn": flow_fqn,
                "name": parsed.name,
                "description": str(definition.get("description") or ""),
                "package": package,
                "version": version,
                "scope": public_scope,
                "input_port": contract.input.port,
                "terminal_port": contract.terminal.port,
                "parameters": {
                    name: spec.to_dict()
                    for name, spec in contract.parameters.items()
                },
                "supported_preempt_policies": list(
                    contract.supported_preempt_policies),
                "allowed_effects": [
                    effect.value for effect in contract.allowed_effects
                ],
            })
    rows.sort(key=lambda row: (
        row["package"], row["name"], row["version"], row["scope"]))
    return rows


def _graph_problems(
        definition: dict[str, Any], terminal: str) -> list[dict[str, str]]:
    tasks = definition.get("tasks") or {}
    graph = {str(task_id): set() for task_id in tasks}
    bounded_edges = set()
    explicit_loop_edges = set()
    for rel in definition.get("relations") or []:
        if not isinstance(rel, dict):
            continue
        source = str(rel.get("from") or rel.get("source") or "")
        target = str(rel.get("to") or rel.get("target") or "")
        if source in graph and target in graph:
            graph[source].add(target)
            bound = rel.get("max_visits", rel.get("max_iterations"))
            if isinstance(bound, int) and not isinstance(bound, bool) and bound > 0:
                bounded_edges.add((source, target))
            if rel.get("explicit_loop") is True:
                explicit_loop_edges.add((source, target))

    problems = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        cycle = any(
            child in visiting
            and (node, child) not in bounded_edges
            and (node, child) not in explicit_loop_edges
            or child not in visiting and visit(child)
            for child in graph[node]
        )
        visiting.remove(node)
        visited.add(node)
        return cycle

    if any(visit(node) for node in graph if node not in visited):
        problems.append(problem(
            ERROR, "agent_workflow_cycle",
            "Agent workflows cannot contain unbounded cycles", field="relations"))

    reverse = {node: set() for node in graph}
    for source, targets in graph.items():
        for target in targets:
            reverse[target].add(source)
    reaches_terminal = {terminal}
    pending = [terminal]
    while pending:
        target = pending.pop()
        for source in reverse.get(target, ()):
            if source not in reaches_terminal:
                reaches_terminal.add(source)
                pending.append(source)
    for task_id, task in tasks.items():
        if task_id not in reaches_terminal and task.get("type") != "stopFlow":
            problems.append(problem(
                ERROR, "agent_workflow_terminal_unreachable",
                f"Task '{task_id}' cannot reach the terminal port",
                entity_type="task", entity_id=str(task_id)))
    return problems


def validate_agent_workflow_definition(
        definition: dict[str, Any]) -> dict[str, Any]:
    """Apply ordinary flow checks plus the fail-closed agent workflow contract."""
    report = FlowDefinitionValidator.validate(definition)
    problems = list(report["problems"])
    if not isinstance(definition, dict) or definition.get("kind") != "agent_workflow":
        return report
    try:
        parsed = AgentWorkflowDefinition.from_dict({
            key: definition.get(key)
            for key in ("id", "name", "version", "kind", "agent_contract")
        })
    except ValueError as exc:
        problems.append(problem(
            ERROR, "agent_workflow_contract_invalid", str(exc),
            field="agent_contract"))
        return FlowDefinitionValidator._report(problems)

    tasks = definition.get("tasks") or {}
    input_id = parsed.agent_contract.input.port
    terminal_id = parsed.agent_contract.terminal.port
    if (tasks.get(input_id) or {}).get("type") != "inputPort":
        problems.append(problem(
            ERROR, "agent_workflow_input_port_invalid",
            "Declared input port must be an inputPort task",
            entity_type="task", entity_id=input_id))
    if (tasks.get(terminal_id) or {}).get("type") != "outputPort":
        problems.append(problem(
            ERROR, "agent_workflow_terminal_port_invalid",
            "Declared terminal port must be an outputPort task",
            entity_type="task", entity_id=terminal_id))
    input_ports = [
        task_id for task_id, task in tasks.items()
        if (task or {}).get("type") == "inputPort"
    ]
    terminal_ports = [
        task_id for task_id, task in tasks.items()
        if (task or {}).get("type") == "outputPort"
    ]
    if input_ports != [input_id]:
        problems.append(problem(
            ERROR, "agent_workflow_input_port_count",
            "Agent workflows must contain exactly one declared inputPort",
            field="tasks"))
    if terminal_ports != [terminal_id]:
        problems.append(problem(
            ERROR, "agent_workflow_terminal_port_count",
            "Agent workflows must contain exactly one declared outputPort",
            field="tasks"))
    flow_fqn = str(definition.get("fqn") or "")
    if flow_fqn:
        try:
            _package, flow_name, flow_version = parse_flow_fqn(flow_fqn)
        except ValueError:
            flow_name, flow_version = "", ""
        if parsed.id != flow_name or parsed.version != flow_version:
            problems.append(problem(
                ERROR, "agent_workflow_identity_mismatch",
                "Agent workflow id/version must match its exact FQN",
                field="fqn"))
    for task_id, task in tasks.items():
        task_type = str((task or {}).get("type") or "")
        if task_type in FORBIDDEN_AGENT_WORKFLOW_TASKS:
            problems.append(problem(
                ERROR, "workflow_task_forbidden",
                f"Task type '{task_type}' is forbidden in agent workflows",
                entity_type="task", entity_id=str(task_id), field="type"))
        if task_type and task_type not in FORBIDDEN_AGENT_WORKFLOW_TASKS:
            try:
                from core import TaskFactory
                from core.workflow_task_safety import validate_workflow_task_class
                task_class = TaskFactory.get(task_type)
                validate_workflow_task_class(
                    task_class, parsed.agent_contract.allowed_effects)
            except Exception as exc:
                problems.append(problem(
                    ERROR, "workflow_task_unsafe",
                    f"Task type '{task_type}' is unsafe: {exc}",
                    entity_type="task", entity_id=str(task_id), field="type"))
    problems.extend(_graph_problems(definition, terminal_id))
    return FlowDefinitionValidator._report(problems)


def bind_agent_workflow(
    workflow: dict[str, Any], user_id: str, conversation_id: str,
    *, repository=None,
) -> dict[str, Any]:
    """Validate, resolve, and serialize an immutable conversation binding."""
    # Binding is a standalone API (imports/tests need not have booted a flow
    # service first), so its ordinary task-type validation must be deterministic.
    from tasks import register_all_tasks
    register_all_tasks()
    candidate = dict(workflow or {})
    resolved = resolve_exact_agent_workflow(
        str(candidate.get("flow_fqn") or ""), user_id, conversation_id,
        repository=repository)
    report = validate_agent_workflow_definition(resolved.definition)
    if not report["ok"]:
        codes = ", ".join(
            p["code"] for p in report["problems"] if p["severity"] == ERROR)
        raise ValueError(f"workflow contract is invalid: {codes}")
    contract = AgentWorkflowDefinition.from_dict({
        key: resolved.definition.get(key)
        for key in ("id", "name", "version", "kind", "agent_contract")
    }).agent_contract
    candidate.setdefault(
        "allowed_effects",
        [effect.value for effect in contract.allowed_effects])
    candidate.setdefault("input_port", contract.input.port)
    candidate.setdefault("terminal_port", contract.terminal.port)
    candidate["flow_scope"] = resolved.ref.scope
    candidate["flow_ref"] = resolved.ref.to_dict()
    parsed = WorkflowInstanceConfig.from_dict(candidate)
    if not set(parsed.allowed_effects) <= set(contract.allowed_effects):
        raise ValueError("workflow instance effects exceed the flow contract")
    if parsed.input_port != contract.input.port:
        raise ValueError("workflow input_port does not match the flow contract")
    if parsed.terminal_port != contract.terminal.port:
        raise ValueError("workflow terminal_port does not match the flow contract")
    if parsed.preempt_policy not in contract.supported_preempt_policies:
        raise ValueError("workflow preempt_policy is not supported by the flow")
    values = parsed.parameters
    for name, spec in contract.parameters.items():
        if spec.required and name not in values:
            raise ValueError(f"workflow parameter is required: {name}")
        value = values.get(name)
        if not spec.required and (value is None or value == ""):
            values.pop(name, None)
        if name not in values and spec.default is not None:
            values[name] = spec.default
        value = values.get(name, spec.default)
        if value is not None and not _parameter_value_matches(spec.type, value):
            raise ValueError(
                f"workflow parameter '{name}' does not match type {spec.type}")
    unknown = sorted(set(values) - set(contract.parameters))
    if unknown:
        raise ValueError("unknown workflow parameters: " + ", ".join(unknown))
    service_defs = None
    for name, spec in contract.parameters.items():
        if spec.type != "service_ref" or name not in values:
            continue
        if service_defs is None:
            from core.service_registry import ServiceRegistry
            service_defs = ServiceRegistry.get_instance().resolve_all(
                user_id=user_id, conv_id=conversation_id, enabled_only=True)
        from core.identifier import resolve_identifier
        canonical = resolve_identifier(service_defs, values[name])
        service = service_defs.get(canonical) if canonical else None
        if service is None:
            raise ValueError(f"workflow service is not visible: {values[name]}")
        service_type = str(getattr(service, "service_type", "") or "")
        if not _service_matches_capability(service_type, spec.capability):
            raise ValueError(
                f"workflow service '{values[name]}' lacks capability "
                f"{spec.capability}")
    return parsed.to_dict()


def snapshot_agent_workflow_services(
    binding: WorkflowInstanceConfig,
    definition: dict[str, Any],
    user_id: str,
    conversation_id: str,
    *,
    registry=None,
    agent_name: str = "",
) -> dict[str, Any]:
    """Freeze secret-free revisions for every bound service parameter.

    A Summarizer binding is an authoring-time selector. Its configured LLM is
    resolved at run acceptance and becomes the execution binding, so workflow
    tasks never call the Summarizer service itself.
    """
    contract = AgentWorkflowDefinition.from_dict({
        key: definition.get(key)
        for key in ("id", "name", "version", "kind", "agent_contract")
    }).agent_contract
    service_parameters = {
        name: spec for name, spec in contract.parameters.items()
        if spec.type == "service_ref"
    }
    requested: dict[str, tuple[Any, str]] = {
        name: (binding.parameters.get(name, spec.default), spec.capability)
        for name, spec in service_parameters.items()
    }
    for task_id, task in (definition.get("tasks") or {}).items():
        if not isinstance(task, dict) or task.get("type") != "agentLLMCall":
            continue
        parameters = task.get("parameters") or task.get("config") or {}
        raw = parameters.get("service") if isinstance(parameters, dict) else None
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(
                f"agentLLMCall task '{task_id}' requires a service")
        value = raw.strip()
        parameter_name = ""
        if value.startswith("${") and value.endswith("}"):
            parameter_name = value[2:-1].strip()
        elif value in service_parameters:
            parameter_name = value
        if parameter_name:
            spec = service_parameters.get(parameter_name)
            if spec is None:
                raise ValueError(
                    f"agentLLMCall task '{task_id}' references a non-service "
                    f"parameter: {parameter_name}")
            requested[parameter_name] = (
                binding.parameters.get(parameter_name, spec.default),
                spec.capability)
        else:
            requested[f"task:{task_id}"] = (value, "llm")
    if registry is None:
        from core.service_registry import ServiceRegistry
        registry = ServiceRegistry.get_instance()
    definitions = registry.resolve_all(
        user_id=user_id, conv_id=conversation_id, enabled_only=True)
    from core.identifier import resolve_identifier
    from core.service_definition_revision import (
        compute_service_definition_revision,
    )

    bindings: dict[str, str] = {}
    services: dict[str, dict[str, Any]] = {}
    for name, (raw, capability) in requested.items():
        if raw in (None, ""):
            spec = service_parameters.get(name)
            if spec is not None and spec.required:
                raise ValueError(f"workflow service is required: {name}")
            continue
        canonical = resolve_identifier(definitions, str(raw))
        service = definitions.get(canonical) if canonical else None
        if service is None:
            raise ValueError(f"workflow service is not visible: {raw}")
        if not _service_matches_capability(service.service_type, capability):
            raise ValueError(
                f"workflow service '{raw}' lacks capability {capability}")
        execution_service = service
        if (
            capability in {"summarizer", "llm_resolvable"}
            and service.service_type == "summarizer"
        ):
            linked = str((service.config or {}).get("llm_service") or "").strip()
            linked_canonical = resolve_identifier(definitions, linked)
            execution_service = (
                definitions.get(linked_canonical) if linked_canonical else None)
            if execution_service is None:
                raise ValueError(
                    f"workflow summarizer '{raw}' has no visible LLM service")
            if str(execution_service.service_type) not in _LLM_SERVICE_TYPES:
                raise ValueError(
                    f"workflow summarizer '{raw}' resolves to a non-LLM service")
            selector_id = str(service.service_id)
            services[selector_id] = {
                "service_id": selector_id,
                "service_type": str(service.service_type),
                "scope": str(service.scope),
                "scope_id": str(service.scope_id),
                "definition_revision": compute_service_definition_revision(service),
                "resolved_llm_service": str(execution_service.service_id),
            }
        service_id = str(execution_service.service_id)
        bindings[name] = service_id
        services[service_id] = {
            "service_id": service_id,
            "service_type": str(execution_service.service_type),
            "scope": str(execution_service.scope),
            "scope_id": str(execution_service.scope_id),
            "definition_revision": compute_service_definition_revision(
                execution_service),
        }
    discovers_media = any(
        isinstance(task, dict) and task.get("type") == "snapshotMediaCapabilities"
        for task in (definition.get("tasks") or {}).values())
    if discovers_media:
        from core import ServiceFactory
        from core.media_capability_discovery import _definition_capabilities

        for service in definitions.values():
            if not _definition_capabilities(
                    service, service_factory=ServiceFactory):
                continue
            service_id = str(service.service_id)
            services[service_id] = {
                "service_id": service_id,
                "service_type": str(service.service_type),
                "scope": str(service.scope),
                "scope_id": str(service.scope_id),
                "definition_revision": compute_service_definition_revision(service),
            }
    snapshot = {"bindings": bindings, "services": services}
    if "relay" in contract.parameters:
        from core.relay_bindings import (
            get_default, get_default_local, get_linked,
        )

        relay_agent = str(agent_name or definition.get("name") or "")
        candidates = get_linked(conversation_id, relay_agent)
        requested_relay = str(binding.parameters.get("relay") or "").strip()
        default_relay = str(get_default(
            conversation_id, relay_agent) or "").strip()
        selected_id = ""
        source = ""
        if requested_relay:
            selected_id = next((
                item for item in candidates
                if item.casefold() == requested_relay.casefold()
            ), "")
            if not selected_id:
                raise ValueError(
                    f"Relay '{requested_relay}' is not linked to this conversation")
            source = "parameter"
        elif default_relay:
            selected_id = next((
                item for item in candidates
                if item.casefold() == default_relay.casefold()
            ), "")
            source = "default" if selected_id else ""
        elif len(candidates) == 1:
            selected_id = candidates[0]
            source = "unique"
        if selected_id and registry.resolve(
                selected_id, user_id=user_id, conv_id=conversation_id) is None:
            raise ValueError(f"Relay '{selected_id}' is linked but not connected")
        snapshot["relay"] = {
            "selected_id": selected_id,
            "candidates": list(candidates),
            "selection_required": not bool(selected_id),
            "source": source,
            "local": (
                get_default_local(
                    conversation_id, selected_id,
                    agent=relay_agent)
                if selected_id else None),
        }
    return snapshot


def assert_flow_version_unreferenced(
    flow_fqn: str, scope: str, user_id: str = "", conv_id: str = "",
    *, conversation_store=None,
) -> None:
    """Refuse deleting an exact flow version bound by a live agent instance."""
    if conversation_store is None:
        from core.conversation_store import ConversationStore
        conversation_store = ConversationStore.instance()
    public_scope = "conversation" if scope == "conv" else scope
    references = []
    for row in conversation_store.list_conversations():
        cid = str(row.get("conversation_id") or "")
        owner = str(row.get("user_id") or "")
        if public_scope == "user" and owner != user_id:
            continue
        if public_scope == "conversation" and (
                cid != conv_id or owner != user_id):
            continue
        configs = conversation_store.get_extra(cid, "conv_agents") or {}
        for agent_name, config in configs.items():
            workflow = (config or {}).get("workflow") or {}
            if (config or {}).get("runtime_kind") == "workflow" and (
                    workflow.get("flow_fqn") == flow_fqn
                    and workflow.get("flow_scope") == public_scope):
                references.append(f"{cid}/{agent_name}")
    if references:
        raise ValueError(
            f"Flow {flow_fqn} is referenced by workflow agent instances: "
            + ", ".join(sorted(references)))
