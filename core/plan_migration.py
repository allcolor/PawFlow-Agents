"""Read-only WP9 preflight for migrating legacy PlanStore records.

The preflight inventories one-file-per-plan JSON records, classifies their
lifecycle without mutating source data, resolves exact agent adapters, and
produces a deterministic activation report. Conversion and activation consume
this report; importing this module never migrates data.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from enum import Enum
from pathlib import Path
from typing import Any, Callable


class LegacyPlanClass(str, Enum):
    """Migration classes required by the declarative workflow cutover."""

    COMPLETED = "completed"
    CANCELLED = "cancelled"
    PENDING_APPROVAL = "pending_approval"
    APPROVED_NOT_STARTED = "approved_not_started"
    IN_PROGRESS = "in_progress"
    WAITING_VERIFICATION = "waiting_verification"
    FAILED = "failed"


_TERMINAL_CLASSES = frozenset({
    LegacyPlanClass.COMPLETED,
    LegacyPlanClass.CANCELLED,
    LegacyPlanClass.FAILED,
})
_STEP_STATUSES = frozenset({
    "pending", "in_progress", "pending_verification", "done", "error", "skipped",
})
_PLAN_STATUSES = frozenset({
    "pending_approval", "approved", "active", "in_progress",
    "completed", "cancelled", "failed",
})


def _required_text(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} is required")
    return result


def _validated_steps(plan: dict[str, Any]) -> list[dict[str, Any]]:
    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("steps must be a non-empty array")
    seen: set[int] = set()
    result: list[dict[str, Any]] = []
    for position, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            raise ValueError(f"steps[{position}] must be an object")
        try:
            index = int(step.get("index", position))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"steps[{position}].index must be an integer") from exc
        if index < 1 or index in seen:
            raise ValueError("step indexes must be unique positive integers")
        seen.add(index)
        _required_text(step.get("description"), f"steps[{position}].description")
        status = _required_text(step.get("status"), f"steps[{position}].status")
        if status not in _STEP_STATUSES:
            raise ValueError(f"unknown legacy step status: {status}")
        result.append(step)
    return result


def classify_legacy_plan(plan: dict[str, Any]) -> LegacyPlanClass:
    """Classify one valid legacy record or reject ambiguous state."""

    if not isinstance(plan, dict):
        raise ValueError("plan must be an object")
    _required_text(plan.get("id"), "id")
    _required_text(plan.get("title"), "title")
    status = _required_text(plan.get("status"), "status")
    if status not in _PLAN_STATUSES:
        raise ValueError(f"unknown legacy plan status: {status}")
    steps = _validated_steps(plan)
    step_statuses = [str(step["status"]) for step in steps]

    if sum(value == "in_progress" for value in step_statuses) > 1:
        raise ValueError("multiple in-progress steps cannot be resumed safely")
    if status == "completed":
        if not all(value in {"done", "skipped"} for value in step_statuses):
            raise ValueError("completed plan contains non-terminal steps")
        return LegacyPlanClass.COMPLETED
    if status == "cancelled":
        return LegacyPlanClass.CANCELLED
    if status == "pending_approval":
        if any(value != "pending" for value in step_statuses):
            raise ValueError("pending-approval plan contains progressed steps")
        return LegacyPlanClass.PENDING_APPROVAL
    if status == "failed" or "error" in step_statuses:
        return LegacyPlanClass.FAILED
    if "pending_verification" in step_statuses:
        if "in_progress" in step_statuses:
            raise ValueError("verification and execution cannot both be active")
        return LegacyPlanClass.WAITING_VERIFICATION
    if "in_progress" in step_statuses:
        return LegacyPlanClass.IN_PROGRESS
    if status in {"approved", "active", "in_progress"}:
        if all(value == "pending" for value in step_statuses):
            return LegacyPlanClass.APPROVED_NOT_STARTED
        raise ValueError("active plan has no representable continuation state")
    raise ValueError(f"unrepresentable legacy plan status: {status}")


def legacy_plan_digest(plan: dict[str, Any]) -> str:
    """Return the canonical SHA-256 digest of one decoded source record."""

    if not isinstance(plan, dict):
        raise TypeError("plan must be an object")
    payload = json.dumps(
        plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def resolve_legacy_agent_adapter(
    name: str,
    user_id: str,
    conversation_id: str,
    *,
    resource_store=None,
    workflow_resolver=None,
) -> dict[str, Any] | None:
    """Resolve only agents already backed by the durable Workflow Agent path."""

    from core.agent_group_resources import resolve_agent_resource

    try:
        resolved = resolve_agent_resource(
            name, user_id, conversation_id, resource_store=resource_store)
    except ValueError:
        return None
    defaults = resolved.definition.get("runtime_defaults") or {}
    if not isinstance(defaults, dict) or defaults.get("kind") != "workflow":
        return None
    workflow = defaults.get("workflow")
    if not isinstance(workflow, dict):
        return None
    flow_fqn = str(workflow.get("flow_fqn") or "").strip()
    if not flow_fqn:
        return None
    if workflow_resolver is None:
        from core.workflow_agent_resources import resolve_exact_agent_workflow

        workflow_resolver = resolve_exact_agent_workflow
    try:
        resolved_flow = workflow_resolver(flow_fqn, user_id, conversation_id)
    except (KeyError, TypeError, ValueError):
        return None
    if isinstance(resolved_flow, dict):
        flow_ref = resolved_flow.get("flow_ref")
    else:
        flow_ref = getattr(resolved_flow, "ref", None)
        if flow_ref is not None:
            flow_ref = flow_ref.to_dict()
    if not isinstance(flow_ref, dict) or not flow_ref:
        return None
    return {
        "adapter": "invokeWorkflowAgent",
        "runtime_kind": "workflow",
        "agent_ref": resolved.ref.to_dict(),
        "flow_ref": flow_ref,
        "workflow": json.loads(json.dumps(workflow, ensure_ascii=False)),
    }


def resolve_legacy_plan_checkpoint(
    plan: dict[str, Any],
    schedules: list[dict[str, Any]],
    *,
    conversation_id: str = "",
) -> dict[str, Any] | None:
    """Map only the durable verification wake; executing turns cannot resume."""

    if classify_legacy_plan(plan) is not LegacyPlanClass.WAITING_VERIFICATION:
        return None
    plan_id = _required_text(plan.get("id"), "id")
    conversation_id = str(
        conversation_id or plan.get("conversation_id") or "").strip()
    if not conversation_id or not isinstance(schedules, list):
        return None
    step = next(
        value for value in _validated_steps(plan)
        if value.get("status") == "pending_verification")
    step_index = int(step.get("index"))
    verifier = str(step.get("verifier") or plan.get("verifier") or "").strip()
    if not verifier:
        return None
    key = (
        f"{conversation_id}::plan::{plan_id}::"
        f"verify{step_index}::{verifier}")
    candidates = [
        value for value in schedules
        if isinstance(value, dict)
        and value.get("conversation_id") == conversation_id
        and value.get("key") == key
    ]
    if len(candidates) != 1:
        return None
    schedule = candidates[0]
    match = re.fullmatch(
        rf"\[plan_verify:{re.escape(plan_id)}:{step_index}:([\w.-]+)\] "
        rf"\({re.escape(verifier)}\)",
        str(schedule.get("reason") or ""),
    )
    if match is None:
        return None
    return {
        "schema_version": 1,
        "kind": "legacy_plan_verification",
        "plan_id": plan_id,
        "step": step_index,
        "executor": match.group(1),
        "verifier": verifier,
        "schedule": json.loads(json.dumps(schedule, ensure_ascii=False)),
    }


def _agent_names(plan: dict[str, Any]) -> list[str]:
    names: set[str] = set()
    assigned = plan.get("assigned_to", [])
    if isinstance(assigned, str):
        assigned = [assigned]
    if isinstance(assigned, list):
        names.update(str(value).strip() for value in assigned if str(value).strip())
    for field in ("verifier", "verified_by"):
        value = plan.get(field)
        if isinstance(value, str) and value.strip():
            names.add(value.strip())
    for step in plan.get("steps") or []:
        for field in ("assigned_to", "verifier", "verified_by"):
            value = step.get(field)
            if isinstance(value, str) and value.strip():
                names.add(value.strip())
        verification = step.get("verification")
        if isinstance(verification, dict):
            for field in ("agent", "assigned_to", "verifier"):
                value = verification.get(field)
                if isinstance(value, str) and value.strip():
                    names.add(value.strip())
    created_by = str(plan.get("created_by") or "").strip()
    if created_by and created_by != "user" and any(
        not str(step.get("assigned_to") or "").strip()
        for step in plan.get("steps") or []
    ):
        names.add(created_by)
    return sorted(names)


_PROPOSAL_STATUS = {
    LegacyPlanClass.COMPLETED: "completed",
    LegacyPlanClass.CANCELLED: "cancelled",
    LegacyPlanClass.PENDING_APPROVAL: "user_review",
    LegacyPlanClass.APPROVED_NOT_STARTED: "accepted",
    LegacyPlanClass.IN_PROGRESS: "running",
    LegacyPlanClass.WAITING_VERIFICATION: "running",
    LegacyPlanClass.FAILED: "failed",
}
_RUN_STATUS = {
    LegacyPlanClass.COMPLETED: "completed",
    LegacyPlanClass.CANCELLED: "cancelled",
    LegacyPlanClass.PENDING_APPROVAL: None,
    LegacyPlanClass.APPROVED_NOT_STARTED: "created",
    LegacyPlanClass.IN_PROGRESS: "running",
    LegacyPlanClass.WAITING_VERIFICATION: "waiting",
    LegacyPlanClass.FAILED: "failed",
}


def build_legacy_conversion_plan(
    record: dict[str, Any],
    plan: dict[str, Any],
) -> dict[str, Any]:
    """Build a deterministic, non-mutating conversion plan for one record."""

    if not isinstance(record, dict):
        raise TypeError("record must be an object")
    if legacy_plan_digest(plan) != _required_text(
        record.get("source_digest"), "source_digest"
    ):
        raise ValueError("legacy plan changed after preflight")
    plan_id = _required_text(plan.get("id"), "id")
    if plan_id != _required_text(record.get("plan_id"), "plan_id"):
        raise ValueError("preflight plan identity does not match source")
    classification = LegacyPlanClass(
        _required_text(record.get("classification"), "classification"))
    if classify_legacy_plan(plan) is not classification:
        raise ValueError("legacy plan classification changed after preflight")
    user_id = _required_text(record.get("user_id"), "user_id")
    conversation_id = _required_text(
        record.get("conversation_id"), "conversation_id")
    if str(plan.get("conversation_id") or conversation_id) != conversation_id:
        raise ValueError("legacy plan conversation changed after preflight")
    adapters = record.get("agent_adapters") or {}
    if not isinstance(adapters, dict):
        raise TypeError("agent_adapters must be an object")

    default_executor = str(plan.get("created_by") or "").strip()
    default_verifier = str(plan.get("verifier") or "").strip()
    steps = []
    for step in sorted(_validated_steps(plan), key=lambda value: int(
        value.get("index", 0))):
        executor = str(step.get("assigned_to") or default_executor).strip()
        if executor == "user":
            executor = ""
        verifier = str(step.get("verifier") or default_verifier).strip()
        steps.append({
            "index": int(step.get("index")),
            "description": str(step.get("description")),
            "status": str(step.get("status")),
            "paused": bool(step.get("paused", False)),
            "note": str(step.get("note") or ""),
            "legacy_task_id": str(step.get("task_id") or ""),
            "executor": executor,
            "executor_adapter": adapters.get(executor) if executor else None,
            "verifier": verifier,
            "verifier_adapter": adapters.get(verifier) if verifier else None,
        })

    digest = str(record["source_digest"])
    identity = digest[:20]
    mode = (
        "archive" if classification in _TERMINAL_CLASSES
        else "review" if classification is LegacyPlanClass.PENDING_APPROVAL
        else "resume")
    run_status = _RUN_STATUS[classification]
    run = None
    if run_status is not None:
        run = {
            "run_id": f"fr_legacy_{identity}",
            "status": run_status,
            "checkpoint": record.get("checkpoint"),
            "terminal": (
                {
                    "imported": True,
                    "legacy_plan_id": plan_id,
                    "step_statuses": [
                        {"index": step["index"], "status": step["status"],
                         "note": step["note"]}
                        for step in steps
                    ],
                }
                if classification in _TERMINAL_CLASSES else None
            ),
        }
    required_agents = {
        value
        for step in steps
        for value in (step["executor"], step["verifier"])
        if value
    }
    replayable = bool(required_agents) and required_agents.issubset(adapters)
    return {
        "schema_version": 1,
        "mode": mode,
        "source_digest": digest,
        "flow": {
            "fqn": f"legacy_plans.plan_{digest[:16]}:1.0.0",
            "scope": "conversation",
            "owner_id": conversation_id,
            "replayable": replayable,
        },
        "proposal": {
            "proposal_id": f"wp_legacy_{identity}",
            "status": _PROPOSAL_STATUS[classification],
        },
        "run": run,
        "steps": steps,
        "imported_plan": {
            "source_id": plan_id,
            "source_path": _required_text(
                record.get("source_path"), "source_path"),
            "user_id": user_id,
            "conversation_id": conversation_id,
            "title": str(plan.get("title")),
            "created_by": str(plan.get("created_by") or ""),
            "created_at": plan.get("created_at"),
            "updated_at": plan.get("updated_at"),
            "classification": classification.value,
        },
    }


class LegacyPlanMigrationPreflight:
    """Inventory legacy plan files and produce a deterministic cutover report."""

    def __init__(
        self,
        *,
        plans_dir: Path,
        resolve_agent_adapter: Callable[
            [str, str, str], dict[str, Any] | None],
        resolve_active_checkpoint: (
            Callable[
                [dict[str, Any], str, str], dict[str, Any] | None] | None
        ) = None,
    ) -> None:
        self.plans_dir = Path(plans_dir)
        if not callable(resolve_agent_adapter):
            raise TypeError("resolve_agent_adapter must be callable")
        self.resolve_agent_adapter = resolve_agent_adapter
        if resolve_active_checkpoint is not None and not callable(
            resolve_active_checkpoint
        ):
            raise TypeError("resolve_active_checkpoint must be callable")
        self.resolve_active_checkpoint = resolve_active_checkpoint

    @staticmethod
    def _issue(
        code: str,
        source_path: str,
        *,
        plan_id: str = "",
        agent: str = "",
        detail: str = "",
    ) -> dict[str, str]:
        return {
            "code": code,
            "source_path": source_path,
            "plan_id": plan_id,
            "agent": agent,
            "detail": detail,
        }

    def run(self) -> dict[str, Any]:
        records: list[dict[str, Any]] = []
        blockers: list[dict[str, str]] = []
        warnings: list[dict[str, str]] = []
        paths = (
            sorted(self.plans_dir.glob("*/*/*.json"))
            if self.plans_dir.exists() else []
        )
        for path in paths:
            relative = path.relative_to(self.plans_dir).as_posix()
            user_id = path.parent.parent.name
            conversation_id = path.parent.name
            try:
                plan = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                blockers.append(self._issue(
                    "corrupt_plan", relative, detail=str(exc)))
                continue
            if not isinstance(plan, dict):
                blockers.append(self._issue(
                    "invalid_plan", relative, detail="plan must be an object"))
                continue
            plan_id = str(plan.get("id") or "")
            if path.stem != plan_id:
                blockers.append(self._issue(
                    "plan_id_mismatch", relative, plan_id=plan_id,
                    detail=f"filename identifies {path.stem}"))
                continue
            embedded_conversation = str(plan.get("conversation_id") or "")
            if embedded_conversation and embedded_conversation != conversation_id:
                blockers.append(self._issue(
                    "conversation_scope_mismatch", relative, plan_id=plan_id,
                    detail=f"record identifies {embedded_conversation}"))
                continue
            try:
                classification = classify_legacy_plan(plan)
            except ValueError as exc:
                blockers.append(self._issue(
                    "unclassifiable_plan", relative, plan_id=plan_id,
                    detail=str(exc)))
                continue

            agents = _agent_names(plan)
            adapters: dict[str, dict[str, Any]] = {}
            for agent in agents:
                adapter = self.resolve_agent_adapter(
                    agent, user_id, conversation_id)
                if isinstance(adapter, dict) and adapter:
                    adapters[agent] = adapter
                    continue
                issue = self._issue(
                    "missing_exact_agent_adapter"
                    if classification not in _TERMINAL_CLASSES
                    else "unresolved_terminal_assignment",
                    relative, plan_id=plan_id, agent=agent,
                )
                if classification in _TERMINAL_CLASSES:
                    warnings.append(issue)
                else:
                    blockers.append(issue)

            checkpoint = None
            if classification in {
                LegacyPlanClass.IN_PROGRESS,
                LegacyPlanClass.WAITING_VERIFICATION,
            }:
                try:
                    checkpoint = (
                        self.resolve_active_checkpoint(
                            plan, user_id, conversation_id)
                        if self.resolve_active_checkpoint is not None else None)
                except Exception as exc:  # preflight records adapter failure
                    blockers.append(self._issue(
                        "active_checkpoint_adapter_failed",
                        relative, plan_id=plan_id, detail=str(exc)))
                else:
                    if not isinstance(checkpoint, dict) or not checkpoint:
                        checkpoint = None
                        blockers.append(self._issue(
                            "missing_active_checkpoint_adapter",
                            relative, plan_id=plan_id))

            records.append({
                "source_path": relative,
                "source_digest": legacy_plan_digest(plan),
                "user_id": user_id,
                "conversation_id": conversation_id,
                "plan_id": plan_id,
                "classification": classification.value,
                "assigned_agents": agents,
                "agent_adapters": adapters,
                "checkpoint": checkpoint,
                "created_at": plan.get("created_at"),
                "updated_at": plan.get("updated_at"),
            })

        records.sort(key=lambda item: (
            item["user_id"], item["conversation_id"], item["plan_id"]))
        blockers.sort(key=lambda item: (
            item["source_path"], item["code"], item["agent"]))
        warnings.sort(key=lambda item: (
            item["source_path"], item["code"], item["agent"]))
        counts = dict(sorted(Counter(
            item["classification"] for item in records).items()))
        return {
            "schema_version": 1,
            "source_root": self.plans_dir.as_posix(),
            "record_count": len(records),
            "counts": counts,
            "records": records,
            "blockers": blockers,
            "warnings": warnings,
            "activation_allowed": not blockers,
        }


__all__ = [
    "LegacyPlanClass",
    "LegacyPlanMigrationPreflight",
    "build_legacy_conversion_plan",
    "classify_legacy_plan",
    "legacy_plan_digest",
    "resolve_legacy_agent_adapter",
    "resolve_legacy_plan_checkpoint",
]
