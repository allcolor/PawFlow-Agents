"""Fail-closed capability metadata and authorization for workflow tasks."""

from __future__ import annotations

import hashlib
import json
import posixpath
from pathlib import PurePosixPath
from typing import Any

from core.agent_contracts import (
    DESTRUCTIVE_EFFECTS,
    READ_ONLY_EFFECTS,
    CapabilityEffect,
    CapabilityMetadata,
    IdempotencyClass,
)


class WorkflowTaskSafetyError(RuntimeError):
    """A workflow task is unsafe, out of scope, or not authorized."""

    retryable = False


def workflow_task_metadata(task_or_class: Any) -> CapabilityMetadata:
    """Return one validated declaration, rejecting missing package metadata."""
    task_class = task_or_class if isinstance(task_or_class, type) else type(task_or_class)
    task_type = str(getattr(task_class, "TYPE", "") or "")
    if getattr(task_class, "AGENT_WORKFLOW_SAFE", None) is not True:
        raise WorkflowTaskSafetyError(
            f"task type '{task_type}' is not workflow-safe")
    raw_effects = getattr(task_class, "EFFECTS", None)
    if not isinstance(raw_effects, (tuple, list, set, frozenset)) or not raw_effects:
        raise WorkflowTaskSafetyError("workflow-safe task must declare effects")
    try:
        effects = tuple(
            value if isinstance(value, CapabilityEffect) else CapabilityEffect(value)
            for value in raw_effects)
        idempotency = getattr(task_class, "IDEMPOTENCY", None)
        if not isinstance(idempotency, IdempotencyClass):
            idempotency = IdempotencyClass(idempotency)
        effect_set = frozenset(effects)
        metadata = CapabilityMetadata(
            effects=effects,
            read_only=effect_set <= READ_ONLY_EFFECTS,
            destructive=bool(effect_set & DESTRUCTIVE_EFFECTS),
            idempotency=idempotency,
            open_world=bool(getattr(task_class, "OPEN_WORLD", False)),
            authorization_target_kind=str(
                getattr(task_class, "AUTHORIZATION_TARGET_KIND", "") or ""),
            workflow_safe=True,
            group_safe=bool(getattr(task_class, "GROUP_SAFE", False)),
        )
    except (TypeError, ValueError) as exc:
        raise WorkflowTaskSafetyError(
            f"task type '{task_type}' has invalid capability metadata: {exc}") from exc

    package_runtime = getattr(task_class, "PACKAGE_RUNTIME", None)
    if package_runtime:
        declaration = package_runtime.get("workflow_capabilities")
        if not isinstance(declaration, dict):
            raise WorkflowTaskSafetyError(
                "PFP workflow task has no package capability declaration")
        try:
            package_metadata = CapabilityMetadata.from_dict(declaration)
        except (TypeError, ValueError) as exc:
            raise WorkflowTaskSafetyError(
                f"PFP workflow task capability declaration is invalid: {exc}") from exc
        if package_metadata != metadata:
            raise WorkflowTaskSafetyError(
                "PFP runtime metadata differs from its package declaration")
    return metadata


def validate_workflow_task_class(
    task_class: type, allowed_effects: tuple[CapabilityEffect, ...]
) -> CapabilityMetadata:
    """Validate task metadata against the flow/instance effect ceiling."""
    metadata = workflow_task_metadata(task_class)
    excess = frozenset(metadata.effects) - frozenset(allowed_effects)
    if excess:
        values = ", ".join(sorted(value.value for value in excess))
        raise WorkflowTaskSafetyError(
            f"task effects exceed the workflow declaration: {values}")
    return metadata


def _target(task: Any, flowfile: Any) -> dict[str, Any]:
    resolver = getattr(task, "workflow_authorization_target", None)
    if not callable(resolver):
        return {}
    value = resolver(flowfile)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise WorkflowTaskSafetyError(
            "workflow_authorization_target must return an object")
    allowed = {
        "user_id", "conversation_id", "relay_id", "service_id", "scope",
        "scope_id", "resource_paths", "target_fingerprint",
    }
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise WorkflowTaskSafetyError(
            "workflow task target has unknown fields: " + ", ".join(unknown))
    return dict(value)


def _validate_target(target: dict[str, Any], context: Any,
                     runtime_context: dict[str, Any]) -> None:
    user_id = str(target.get("user_id") or "")
    if user_id and user_id != context.user_id:
        raise WorkflowTaskSafetyError("workflow task targets another user")
    conversation_id = str(target.get("conversation_id") or "")
    if conversation_id and conversation_id != context.conversation_id:
        raise WorkflowTaskSafetyError("workflow task targets another conversation")

    scope = str(target.get("scope") or "")
    scope_id = str(target.get("scope_id") or "")
    if scope == "user" and scope_id != context.user_id:
        raise WorkflowTaskSafetyError("workflow task targets another user scope")
    if scope in {"conversation", "conv"} and scope_id != context.conversation_id:
        raise WorkflowTaskSafetyError(
            "workflow task targets another conversation scope")
    if scope_id and not scope:
        raise WorkflowTaskSafetyError("workflow task scope_id requires scope")

    service_id = str(target.get("service_id") or "")
    if service_id:
        services = dict((context.service_snapshot or {}).get("services") or {})
        entry = next((dict(value) for key, value in services.items()
                      if str(key).casefold() == service_id.casefold()
                      or str((value or {}).get("service_id") or "").casefold()
                      == service_id.casefold()), None)
        if entry is None:
            raise WorkflowTaskSafetyError(
                "workflow task service is outside the run snapshot")
        scope = str(entry.get("scope") or "")
        scope_id = str(entry.get("scope_id") or "")
        if scope == "user" and scope_id != context.user_id:
            raise WorkflowTaskSafetyError("workflow task targets another user's service")
        if scope in {"conversation", "conv"} and scope_id != context.conversation_id:
            raise WorkflowTaskSafetyError(
                "workflow task targets another conversation's service")

    relay_id = str(target.get("relay_id") or "")
    if relay_id:
        allowed_relays = {
            str(value) for value in
            (runtime_context.get("workflow_allowed_relay_ids") or ()) if str(value)
        }
        if relay_id not in allowed_relays:
            raise WorkflowTaskSafetyError(
                "workflow task relay is outside the conversation binding")

    paths = target.get("resource_paths") or ()
    if isinstance(paths, str) or not isinstance(paths, (tuple, list)):
        raise WorkflowTaskSafetyError("workflow task resource_paths must be a list")
    roots = tuple(str(value) for value in (
        runtime_context.get("workflow_resource_roots") or ()) if str(value))
    for raw in paths:
        path = posixpath.normpath(str(raw or ""))
        if not path or not path.startswith("/"):
            raise WorkflowTaskSafetyError("workflow task resource path must be absolute")
        if not any(path == root or path.startswith(root.rstrip("/") + "/")
                   for root in roots):
            raise WorkflowTaskSafetyError(
                "workflow task resource path is outside the bound roots")
        if ".." in PurePosixPath(str(raw)).parts:
            raise WorkflowTaskSafetyError(
                "workflow task resource path contains traversal")


def authorize_workflow_task(task: Any, task_id: str, flowfile: Any,
                            runtime_context: dict[str, Any], attempt: int) -> Any:
    """Authorize one task attempt immediately before execution."""
    context = runtime_context.get("workflow_run_context")
    if context is None:
        return None
    try:
        allowed = tuple(
            value if isinstance(value, CapabilityEffect) else CapabilityEffect(value)
            for value in runtime_context["workflow_allowed_effects"])
    except (KeyError, TypeError, ValueError) as exc:
        raise WorkflowTaskSafetyError(
            "workflow runtime has no valid effect ceiling") from exc
    metadata = validate_workflow_task_class(type(task), allowed)
    target = _target(task, flowfile)
    _validate_target(target, context, runtime_context)

    from core.authorization_context import AuthorizationRef, active_authority_ref
    active_ref = active_authority_ref(context.conversation_id, context.agent_name)
    initial_ref = AuthorizationRef.from_dict(context.authorization_ref.to_dict())
    if initial_ref is None:
        raise WorkflowTaskSafetyError("workflow authorization reference is invalid")
    authorization_ref = initial_ref
    if active_ref is not None:
        same_lineage = (
            active_ref.context_id == initial_ref.context_id
            and active_ref.root_turn_id == initial_ref.root_turn_id
        )
        if same_lineage:
            if active_ref.revision < initial_ref.revision:
                raise WorkflowTaskSafetyError("active authorization revision is stale")
            authorization_ref = active_ref

    run_store = runtime_context.get("workflow_run_store")
    if run_store is not None:
        run_store.set_authorization_ref(context.run_id, authorization_ref.to_dict())

    task_type = str(getattr(task, "TYPE", "") or "")
    safe_target = {
        key: target[key] for key in (
            "relay_id", "service_id", "scope", "scope_id",
            "target_fingerprint") if target.get(key)
    }
    target_digest = hashlib.sha256(json.dumps(
        target, ensure_ascii=False, sort_keys=True, default=str,
        separators=(",", ":")).encode("utf-8")).hexdigest()
    from core.tool_authorization import authorize_tool_call
    result = authorize_tool_call(
        tool_name=f"workflow.{task_type}",
        arguments={"task_id": task_id, "task_type": task_type, **safe_target},
        user_id=context.user_id,
        conversation_id=context.conversation_id,
        agent_name=context.agent_name,
        turn_id=context.root_turn_id,
        call_id=f"workflow:{context.run_id}:{task_id}:{attempt}",
        permission_mode=context.permission_mode,
        capability_effects=metadata.effects,
        authorization_ref=authorization_ref,
    )
    decision = "execute" if result.decision == "legacy" else result.decision
    if run_store is not None:
        run_store.append_event(context.run_id, "authorization", {
            "task_id": task_id,
            "task_type": task_type,
            "attempt": attempt,
            "effects": [value.value for value in metadata.effects],
            "idempotency": metadata.idempotency.value,
            "target_kind": metadata.authorization_target_kind,
            "target_digest": target_digest,
            "authorization_revision": authorization_ref.revision,
            "decision": decision,
            "reason": str(result.reason or "")[:240],
        })
    if decision != "execute":
        raise WorkflowTaskSafetyError(
            f"workflow task authorization {decision}: {result.reason or 'not allowed'}")
    return result


__all__ = [
    "WorkflowTaskSafetyError", "authorize_workflow_task",
    "validate_workflow_task_class", "workflow_task_metadata",
]
