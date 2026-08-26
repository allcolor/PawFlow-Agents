"""Deterministic Flow compilation and publication for legacy PlanStore records."""

from __future__ import annotations

import copy
from typing import Any

from core.flow_authoring import FlowAuthoringService
from core.flow_layout_contracts import relation_id_seed
from core.resource_identity import ResourceRef
from core.workflow_proposal_store import definition_digest


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not value:
        raise ValueError(f"{name} must be a non-empty object")
    return value


def _text(value: Any, name: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{name} is required")
    return result


def _agent_task(
    adapter: Any,
    *,
    message: str,
    legacy_agent: str,
) -> dict[str, Any]:
    if not isinstance(adapter, dict):
        return {
            "type": "log",
            "parameters": {
                "message": (
                    f"Legacy step for {legacy_agent or 'manual action'} is "
                    f"historical and not replayable: {message}"),
            },
            "migration": {"replayable": False, "legacy_agent": legacy_agent},
        }
    if adapter.get("adapter") != "invokeWorkflowAgent":
        raise ValueError("legacy agent adapter must be invokeWorkflowAgent")
    ref = ResourceRef.from_dict(adapter.get("agent_ref") or {})
    if ref.resource_type != "agent":
        raise ValueError("legacy agent adapter must contain an exact agent_ref")
    return {
        "type": "invokeWorkflowAgent",
        "parameters": {
            "agent_ref": ref.to_dict(),
            "message": message,
            "attachments": [],
            "parameters": {},
            "await_terminal": True,
            "publish_to_conversation": False,
            "cancellation_policy": "propagate",
        },
        "migration": {"replayable": True, "legacy_agent": legacy_agent},
    }


def build_legacy_flow_definition(
    conversion: dict[str, Any],
) -> dict[str, Any]:
    """Compile one deterministic conversion plan to a canonical Flow definition."""

    conversion = _object(conversion, "conversion")
    if conversion.get("schema_version") != 1:
        raise ValueError("conversion schema_version must be 1")
    source_digest = _text(conversion.get("source_digest"), "source_digest")
    if (
        len(source_digest) != 64
        or any(value not in "0123456789abcdef" for value in source_digest)
    ):
        raise ValueError("source_digest must be a lowercase SHA-256")
    flow = _object(conversion.get("flow"), "flow")
    imported = _object(conversion.get("imported_plan"), "imported_plan")
    fqn = _text(flow.get("fqn"), "flow.fqn")
    try:
        flow_name, version = fqn.rsplit(":", 1)
        _package, name = flow_name.rsplit(".", 1)
    except ValueError as exc:
        raise ValueError("flow.fqn must be package.name:version") from exc
    if version != "1.0.0":
        raise ValueError("legacy imported flow version must be 1.0.0")

    steps = conversion.get("steps")
    if not isinstance(steps, list):
        raise ValueError("steps must be an array")
    tasks: dict[str, dict[str, Any]] = {}
    relations: list[dict[str, str]] = []
    ordered_ids: list[str] = []
    relationship_by_task: dict[str, str] = {}
    step_by_index: dict[int, dict[str, Any]] = {}
    for step in steps:
        step = _object(step, "step")
        index = step.get("index")
        if isinstance(index, bool) or not isinstance(index, int) or index < 1:
            raise ValueError("step.index must be an integer >= 1")
        if index in step_by_index:
            raise ValueError("legacy step indexes must be unique")
        step_by_index[index] = step
        description = _text(step.get("description"), "step.description")
        executor = str(step.get("executor") or "").strip()
        execute_id = f"step_{index}_execute"
        tasks[execute_id] = _agent_task(
            step.get("executor_adapter"),
            message=description,
            legacy_agent=executor,
        )
        ordered_ids.append(execute_id)
        relationship_by_task[execute_id] = (
            "completed"
            if tasks[execute_id]["type"] == "invokeWorkflowAgent"
            else "success")
        verifier = str(step.get("verifier") or "").strip()
        if verifier:
            verify_id = f"step_{index}_verify"
            tasks[verify_id] = _agent_task(
                step.get("verifier_adapter"),
                message=(
                    f"Verify legacy plan step {index}: {description}. "
                    f"Executor note: {str(step.get('note') or '')}"),
                legacy_agent=verifier,
            )
            ordered_ids.append(verify_id)
            relationship_by_task[verify_id] = (
                "completed"
                if tasks[verify_id]["type"] == "invokeWorkflowAgent"
                else "success")

    tasks["complete"] = {
        "type": "completeFlowRun",
        "parameters": {"summary": _text(imported.get("title"), "title")},
    }
    for source, target in zip(ordered_ids, [*ordered_ids[1:], "complete"]):
        relation = {
            "from": source,
            "to": target,
            "type": relationship_by_task[source],
        }
        relation["relation_id"] = relation_id_seed(relation)
        relations.append(relation)

    checkpoint = (conversion.get("run") or {}).get("checkpoint")
    resume_task_id = ordered_ids[0] if ordered_ids else "complete"
    if checkpoint is not None:
        checkpoint = _object(checkpoint, "run.checkpoint")
        if checkpoint.get("kind") != "legacy_plan_verification":
            raise ValueError("unsupported legacy checkpoint kind")
        checkpoint_step = checkpoint.get("step")
        step = step_by_index.get(checkpoint_step)
        if step is None:
            raise ValueError("checkpoint step does not exist")
        verifier = str(step.get("verifier") or "").strip()
        if str(checkpoint.get("verifier") or "") != verifier:
            raise ValueError("checkpoint verifier does not match converted step")
        resume_task_id = f"step_{checkpoint_step}_verify"
        if resume_task_id not in tasks or tasks[resume_task_id]["type"] != (
                "invokeWorkflowAgent"):
            raise ValueError("checkpoint verifier has no exact runnable adapter")

    return {
        "id": name,
        "name": name,
        "version": version,
        "description": _text(imported.get("title"), "title"),
        "execution_mode": "durable_one_shot",
        "run_contract": {"mode": "durable_one_shot"},
        "parameters": {},
        "services": {},
        "groups": {},
        "tasks": tasks,
        "relations": relations,
        "entries": [ordered_ids[0] if ordered_ids else "complete"],
        "exits": ["complete"],
        "layout": {
            "nodes": {
                task_id: {"x": 80 + position * 240, "y": 120}
                for position, task_id in enumerate([*ordered_ids, "complete"])
            },
        },
        "migration": {
            "schema_version": 1,
            "source_type": "legacy_plan",
            "source_id": _text(imported.get("source_id"), "source_id"),
            "source_path": _text(imported.get("source_path"), "source_path"),
            "source_digest": source_digest,
            "classification": _text(
                imported.get("classification"), "classification"),
            "resume_task_id": resume_task_id,
            "replayable": bool(flow.get("replayable")),
            "original_timestamps": {
                "created_at": imported.get("created_at"),
                "updated_at": imported.get("updated_at"),
            },
        },
    }


def _published_ref(
    *,
    fqn: str,
    version: str,
    user_id: str,
    definition: dict[str, Any],
) -> dict[str, Any]:
    return ResourceRef(
        resource_type="flow",
        name=fqn,
        scope="conversation",
        owner_id=user_id,
        version=version,
        content_digest=definition_digest(definition),
        source_id=f"repository:conversation:{fqn}",
    ).to_dict()


def publish_legacy_flow(
    conversion: dict[str, Any],
    *,
    authoring: FlowAuthoringService | None = None,
) -> dict[str, Any]:
    """Publish one immutable imported flow, idempotent for the exact source."""

    definition = build_legacy_flow_definition(conversion)
    imported = _object(conversion.get("imported_plan"), "imported_plan")
    flow = _object(conversion.get("flow"), "flow")
    fqn = _text(flow.get("fqn"), "flow.fqn")
    flow_name, version = fqn.rsplit(":", 1)
    package, name = flow_name.rsplit(".", 1)
    user_id = _text(imported.get("user_id"), "user_id")
    conversation_id = _text(
        imported.get("conversation_id"), "conversation_id")
    authoring = authoring or FlowAuthoringService.instance()

    try:
        published_definition = authoring.load(
            fqn, "conv", user_id=user_id, conv_id=conversation_id)
    except KeyError:
        draft = authoring.new_from_definition(
            package, name, version, "conv", user_id,
            copy.deepcopy(definition), conv_id=conversation_id)
        authoring.publish(draft["draft_id"], user_id, version)
        published_definition = authoring.load(
            fqn, "conv", user_id=user_id, conv_id=conversation_id)
    else:
        migration = published_definition.get("migration") or {}
        if migration.get("source_digest") != conversion.get("source_digest"):
            raise ValueError(
                "published flow FQN belongs to a different legacy source")

    return _published_ref(
        fqn=fqn,
        version=version,
        user_id=user_id,
        definition=published_definition,
    )


__all__ = ["build_legacy_flow_definition", "publish_legacy_flow"]
