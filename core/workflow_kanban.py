"""Derived Kanban projections and validated commands for WorkflowRuns.

The workflow definition, durable run row, immutable run events, and durable
interaction store remain authoritative.  This module never stores a lane or
task-board state.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from core.workflow_agent_contracts import WORKFLOW_TERMINAL_STATUSES
from core.workflow_run_inspector import (
    _safe_text,
    inspect_workflow_run,
    workflow_run_summary,
)

RUN_LANES = (
    {"id": "queued", "label": "Queued", "order": 10},
    {"id": "running", "label": "Running", "order": 20},
    {"id": "waiting", "label": "Waiting", "order": 30},
    {"id": "attention", "label": "Needs attention", "order": 40},
    {"id": "failed", "label": "Failed", "order": 50},
    {"id": "done", "label": "Done", "order": 60},
)
TASK_LANES = (
    {"id": "not_started", "label": "Not started", "order": 10},
    {"id": "ready", "label": "Ready", "order": 20},
    {"id": "running", "label": "Running", "order": 30},
    {"id": "waiting", "label": "Waiting for human", "order": 40},
    {"id": "blocked", "label": "Blocked", "order": 50},
    {"id": "failed", "label": "Failed", "order": 60},
    {"id": "done", "label": "Done", "order": 70},
    {"id": "unknown", "label": "Unknown", "order": 80},
)
RUN_STATUS_LANES = {
    "accepted": "queued",
    "running": "running",
    "committing": "running",
    "cancelling": "running",
    "waiting": "waiting",
    "retryable_failed": "attention",
    "failed": "failed",
    "timed_out": "failed",
    "budget_exceeded": "failed",
    "recovery_failed": "failed",
    "completed": "done",
    "cancelled": "done",
    "superseded": "done",
    "force_stopped": "done",
}
_ACTIVE_RUN_STATUSES = frozenset(
    {
        "accepted",
        "running",
        "waiting",
        "retryable_failed",
        "cancelling",
        "committing",
    }
)
_TASK_TERMINAL_STAGES = {
    "task_completed": "done",
    "completed": "done",
    "task_failed": "failed",
    "failed": "failed",
}
_REVIEW_DECISIONS = frozenset({"approved", "changes_requested", "reopened"})


@dataclass(frozen=True)
class CommandPlan:
    """Stable semantic result returned before a Kanban execution mutation."""

    run_id: str
    task_id: str
    source_lane: str
    target_lane: str
    command: str
    code: str
    message: str
    executable: bool
    mutates_execution: bool = False
    requires_confirmation: bool = False
    interaction: dict[str, Any] | None = None
    blocking_parents: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["blocking_parents"] = list(self.blocking_parents)
        return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _event_data(event: dict[str, Any]) -> dict[str, Any]:
    value = event.get("data")
    return value if isinstance(value, dict) else {}


def _same_task(event: dict[str, Any], task_id: str) -> bool:
    return str(_event_data(event).get("task_id") or "") == str(task_id or "")


def _event_timestamp(event: dict[str, Any], data: dict[str, Any]) -> str:
    return str(data.get("created_at") or event.get("timestamp") or "")


def _comment_rows(events: Iterable[dict[str, Any]], task_id: str) -> list[dict[str, Any]]:
    rows = []
    for event in events:
        if event.get("event_type") != "kanban_comment" or not _same_task(event, task_id):
            continue
        data = _event_data(event)
        rows.append(
            {
                "comment_id": str(data.get("comment_id") or event.get("event_id") or ""),
                "task_id": str(data.get("task_id") or ""),
                "author_label": _safe_text(
                    data.get("author_label") or data.get("author_user_id"), 160
                ),
                "body": _safe_text(data.get("body"), 4000),
                "created_at": _event_timestamp(event, data),
            }
        )
    return rows


def _attachment_rows(
    events: Iterable[dict[str, Any]],
    task_id: str,
    *,
    file_store,
    user_id: str,
    conversation_id: str,
) -> list[dict[str, Any]]:
    """Project only FileStore attachments the current actor can still access."""

    if file_store is None or not user_id or not conversation_id:
        return []
    removed = {
        str(_event_data(event).get("attachment_id") or "")
        for event in events
        if event.get("event_type") == "kanban_attachment_removed"
        and _same_task(event, task_id)
    }
    rows = []
    for event in events:
        if event.get("event_type") != "kanban_attachment_added" or not _same_task(
            event, task_id
        ):
            continue
        data = _event_data(event)
        attachment_id = str(
            data.get("attachment_id") or event.get("event_id") or ""
        )
        if attachment_id in removed:
            continue
        file_id = str(data.get("file_id") or "").strip()
        try:
            metadata = file_store.get_metadata_required(
                file_id, user_id=user_id, conversation_id=conversation_id
            )
        except (FileNotFoundError, TypeError, ValueError):
            continue
        rows.append(
            {
                "attachment_id": attachment_id,
                "file_id": file_id,
                "filename": _safe_text(metadata.get("filename"), 240),
                "content_type": _safe_text(metadata.get("content_type"), 160),
                "size": int(metadata.get("size") or 0),
                "label": _safe_text(
                    data.get("label") or metadata.get("filename"), 240
                ),
                "added_by_user_id": _safe_text(
                    data.get("added_by_user_id"), 160
                ),
                "created_at": _event_timestamp(event, data),
                "url": f"/files/{file_id}",
            }
        )
    return rows


def _review_rows(events: Iterable[dict[str, Any]], task_id: str) -> list[dict[str, Any]]:
    rows = []
    for event in events:
        if event.get("event_type") != "kanban_review" or not _same_task(event, task_id):
            continue
        data = _event_data(event)
        decision = str(data.get("decision") or "").strip()
        if decision not in _REVIEW_DECISIONS:
            continue
        rows.append(
            {
                "review_id": str(data.get("review_id") or event.get("event_id") or ""),
                "task_id": str(data.get("task_id") or ""),
                "decision": decision,
                "reviewer_user_id": _safe_text(data.get("reviewer_user_id"), 160),
                "comment": _safe_text(data.get("comment"), 4000),
                "created_at": _event_timestamp(event, data),
            }
        )
    return rows


def _project(flow: dict[str, Any]) -> dict[str, str]:
    name = _safe_text(flow.get("name"), 240)
    scope = _safe_text(flow.get("scope"), 160)
    identifier = ":".join(value for value in (scope, name) if value)
    return {"id": identifier, "label": name or identifier, "scope": scope}


def _worker_diagnostic(worker: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(worker, dict):
        return None
    return {
        "agent_name": _safe_text(worker.get("agent_name"), 160),
        "turn_id": _safe_text(worker.get("turn_id"), 160),
        "workflow_run_id": _safe_text(worker.get("workflow_run_id"), 160),
        "status": _safe_text(worker.get("status"), 160),
        "duration_s": max(0.0, float(worker.get("duration_s") or 0.0)),
        "runtime_kind": _safe_text(worker.get("runtime_kind"), 160),
        "termination_command": "force_stop",
    }


def _latest_assignment(
    events: Iterable[dict[str, Any]], task_id: str, *, fallback_to_run: bool = False
) -> str | None:
    rows = list(events)
    scopes = (task_id, "") if task_id and fallback_to_run else (task_id,)
    for scope in scopes:
        for event in reversed(rows):
            if event.get("event_type") != "kanban_assignment":
                continue
            if not _same_task(event, scope):
                continue
            value = _safe_text(_event_data(event).get("assignee"), 160)
            return value or None
    return None


def _wait_rows(waits: Iterable[dict[str, Any]], run_id: str) -> list[dict[str, Any]]:
    instance_id = f"workflow:{run_id}"
    rows = []
    for wait in waits:
        if (
            str(wait.get("instance_id") or "") != instance_id
            or str(wait.get("status") or "") != "waiting"
            or str(wait.get("kind") or "") != "signal"
        ):
            continue
        signal_id = str(wait.get("signal_id") or "")
        request_id = ""
        prefix, separator, suffix = signal_id.partition(":")
        if separator and prefix in {"confirmation", "interaction"}:
            request_id = suffix
        rows.append(
            {
                "wait_id": str(wait.get("wait_id") or ""),
                "request_id": request_id,
                "signal_id": signal_id,
                "task_id": str(wait.get("task_id") or ""),
                "created_at": wait.get("created_at"),
            }
        )
    return rows


def run_lane(status: Any) -> str:
    """Map every canonical WorkflowRun status to one operational lane."""

    return RUN_STATUS_LANES.get(str(status or ""), "failed")


def _badge(identifier: str, label: str, value: Any = None) -> dict[str, Any]:
    result = {"id": identifier, "label": label}
    if value not in (None, "", 0, False):
        result["value"] = value
    return result


def _run_allowed_commands(
    run: dict[str, Any], *, live: bool, safe_retry: bool, waits: Iterable[dict[str, Any]]
) -> list[str]:
    status = str(run.get("status") or "")
    commands = []
    if status == "retryable_failed" and safe_retry:
        commands.append("retry")
    if status == "waiting" and list(waits):
        commands.append("open_interaction")
    if status in _ACTIVE_RUN_STATUSES and live:
        if status != "cancelling":
            commands.append("cancel")
        commands.append("force_stop")
    return commands


def project_run_card(
    run: dict[str, Any],
    *,
    store,
    live_run_ids=(),
    events=(),
    waits=(),
    summary: dict[str, Any] | None = None,
    file_store=None,
    user_id: str = "",
    worker: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return one redacted card from a durable WorkflowRun row."""

    live = str(run.get("run_id") or "") in set(live_run_ids)
    summary = summary or workflow_run_summary(run, store=store, live_run_ids=live_run_ids)
    event_rows = list(events)
    wait_rows = _wait_rows(waits, summary["run_id"])
    comments = _comment_rows(event_rows, "")
    attachments = _attachment_rows(
        event_rows,
        "",
        file_store=file_store,
        user_id=user_id,
        conversation_id=str(run.get("conversation_id") or ""),
    )
    reviews = _review_rows(event_rows, "")
    review = reviews[-1] if reviews else None
    badges = []
    if live:
        badges.append(_badge("live", "Live"))
    if wait_rows:
        badges.append(_badge("human_wait", "Human wait", len(wait_rows)))
    if summary.get("safe_retry"):
        badges.append(_badge("safe_retry", "Safe retry"))
    if summary.get("recovery_count"):
        badges.append(_badge("recoveries", "Recoveries", summary["recovery_count"]))
    if summary.get("error"):
        badges.append(_badge("error", "Error", summary["error"].get("code") or "failed"))
    if summary.get("artifacts"):
        badges.append(_badge("artifacts", "Artifacts", len(summary["artifacts"])))
    if summary.get("usage"):
        badges.append(_badge("usage", "Usage"))
    if attachments:
        badges.append(_badge("attachments", "Attachments", len(attachments)))
    if review:
        badges.append(_badge("review", "Review", review["decision"]))
    flow = summary.get("flow") or {}
    project = _project(flow)
    current_generation = bool(store.is_current_generation(summary["run_id"]))
    return {
        "id": summary["run_id"],
        "run_id": summary["run_id"],
        "task_id": "",
        "lane": run_lane(summary.get("status")),
        "title": _safe_text(
            flow.get("name") or summary.get("agent_name") or summary["run_id"], 240
        ),
        "status": str(summary.get("status") or ""),
        "live": live,
        "assignee": _latest_assignment(event_rows, ""),
        "comments_count": len(comments),
        "comments": comments,
        "attachments_count": len(attachments),
        "attachments": attachments,
        "review": review,
        "review_history": reviews,
        "project": project,
        "created_at": summary.get("created_at"),
        "updated_at": summary.get("updated_at"),
        "badges": badges,
        "relations": {"parents": [], "children": []},
        "allowed_commands": _run_allowed_commands(
            run, live=live, safe_retry=bool(summary.get("safe_retry")), waits=wait_rows
        ),
        "allowed_actions": ["comment", "assign", "attach", "review"],
        "summary": {
            "agent_name": summary.get("agent_name"),
            "generation": summary.get("generation"),
            "flow": flow,
            "usage": summary.get("usage") or {},
            "artifacts": summary.get("artifacts") or [],
            "failure_reason": summary.get("failure_reason") or "",
            "error": summary.get("error"),
            "interaction": wait_rows[0] if wait_rows else None,
            "diagnostics": {
                "live": live,
                "current_generation": current_generation,
                "stale_generation": not current_generation,
                "worker": _worker_diagnostic(worker),
            },
        },
    }


def _graph_maps(graph: dict[str, Any]) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    task_ids = {
        str(task.get("id") or "")
        for task in graph.get("tasks", [])
        if isinstance(task, dict) and str(task.get("id") or "")
    }
    parents = {task_id: [] for task_id in task_ids}
    children = {task_id: [] for task_id in task_ids}
    for relation in graph.get("relations", []):
        if not isinstance(relation, dict):
            continue
        source = str(relation.get("from") or "")
        target = str(relation.get("to") or "")
        if source not in task_ids or target not in task_ids:
            continue
        if source not in parents[target]:
            parents[target].append(source)
        if target not in children[source]:
            children[source].append(target)
    return parents, children


def _task_event_lane(
    task_id: str, events: Iterable[dict[str, Any]], *, retryable_task_id: str, waiting: bool
) -> tuple[str | None, str]:
    lane = None
    evidence = ""
    for event in events:
        if not _same_task(event, task_id):
            continue
        event_type = str(event.get("event_type") or "")
        data = _event_data(event)
        if event_type == "authorization":
            decision = str(data.get("decision") or "").casefold()
            if decision == "execute":
                lane, evidence = "running", "authorization"
            elif decision:
                lane, evidence = "blocked", "authorization"
            continue
        if event_type not in {
            "progress",
            "retrying",
            "error",
            "waiting",
            "stage_started",
            "stage_completed",
            "agent_message",
            "tool_call",
            "tool_result",
        }:
            continue
        stage = str(data.get("stage") or "").casefold()
        outcome = str(data.get("outcome") or "").casefold()
        state = _TASK_TERMINAL_STAGES.get(stage) or _TASK_TERMINAL_STAGES.get(outcome)
        if state:
            lane, evidence = state, stage or outcome
        elif event_type == "error":
            lane, evidence = "failed", "error"
        elif event_type == "retrying":
            lane, evidence = "running", "retrying"
        elif event_type in {"agent_message", "tool_call", "tool_result"}:
            lane, evidence = "running", event_type
        elif "queued" in {stage, outcome}:
            lane, evidence = "ready", stage or outcome
        else:
            lane, evidence = "running", stage or event_type
    if waiting:
        return "waiting", "durable_wait"
    if lane == "failed" and task_id == retryable_task_id:
        return "blocked", "retryable_failure"
    return lane, evidence


def project_task_cards(
    run: dict[str, Any],
    graph: dict[str, Any],
    events: Iterable[dict[str, Any]],
    *,
    waits=(),
    safe_retry: bool = False,
    live: bool = False,
    file_store=None,
    user_id: str = "",
    worker: dict[str, Any] | None = None,
    current_generation: bool = True,
) -> list[dict[str, Any]]:
    """Derive task cards while retaining exact branch/join relationships."""

    event_rows = list(events)
    run_id = str(run.get("run_id") or "")
    wait_rows = _wait_rows(waits, run_id)
    waits_by_task = {
        str(wait.get("task_id") or ""): wait for wait in wait_rows if str(wait.get("task_id") or "")
    }
    parents, children = _graph_maps(graph)
    review_by_task = {
        task_id: rows[-1]
        for task_id in parents
        if (rows := _review_rows(event_rows, task_id))
    }
    raw_error = run.get("error") if isinstance(run.get("error"), dict) else {}
    retryable_task_id = str(run.get("resume_task_id") or raw_error.get("task_id") or "")
    lane_by_task: dict[str, str] = {}
    evidence_by_task: dict[str, str] = {}
    for task in graph.get("tasks", []):
        if not isinstance(task, dict):
            continue
        task_id = str(task.get("id") or "")
        if not task_id:
            continue
        lane, evidence = _task_event_lane(
            task_id,
            event_rows,
            retryable_task_id=retryable_task_id,
            waiting=task_id in waits_by_task,
        )
        graph_status = str(task.get("status") or "").casefold()
        if task_id in waits_by_task:
            lane, evidence = "waiting", "durable_wait"
        elif graph_status == "completed":
            lane, evidence = "done", "graph_completed"
        elif graph_status == "failed":
            lane = "blocked" if task_id == retryable_task_id else "failed"
            evidence = "graph_failed"
        elif graph_status == "running":
            lane, evidence = "running", "graph_running"
        elif graph_status not in {"", "pending"} and lane is None:
            lane, evidence = "unknown", "unknown_graph_status"
        if lane:
            lane_by_task[task_id] = lane
            evidence_by_task[task_id] = evidence

    for task_id in parents:
        if task_id in lane_by_task:
            continue
        task_parents = parents[task_id]
        parent_lanes = [lane_by_task.get(parent) for parent in task_parents]
        if task_parents and all(value == "done" for value in parent_lanes):
            lane_by_task[task_id] = "ready"
            evidence_by_task[task_id] = "parents_completed"
        elif task_parents and any(value in {"blocked", "failed"} for value in parent_lanes):
            lane_by_task[task_id] = "blocked"
            evidence_by_task[task_id] = "blocked_parent"
        else:
            lane_by_task[task_id] = "not_started"
            evidence_by_task[task_id] = "no_start_event"

    result = []
    for task in graph.get("tasks", []):
        if not isinstance(task, dict):
            continue
        task_id = str(task.get("id") or "")
        if not task_id:
            continue
        lane = lane_by_task.get(task_id, "unknown")
        task_comments = _comment_rows(event_rows, task_id)
        task_attachments = _attachment_rows(
            event_rows,
            task_id,
            file_store=file_store,
            user_id=user_id,
            conversation_id=str(run.get("conversation_id") or ""),
        )
        task_reviews = _review_rows(event_rows, task_id)
        task_review = task_reviews[-1] if task_reviews else None
        task_wait = waits_by_task.get(task_id)
        blocking_parents = [
            parent for parent in parents.get(task_id, []) if lane_by_task.get(parent) != "done"
        ]
        review_dependency_warnings = [
            parent
            for parent in parents.get(task_id, [])
            if (review_by_task.get(parent) or {}).get("decision")
            in {"changes_requested", "reopened"}
        ]
        badges = []
        if len(children.get(task_id, [])) > 1:
            badges.append(_badge("branch", "Branch", len(children.get(task_id, []))))
        if len(parents.get(task_id, [])) > 1:
            badges.append(_badge("join", "Join", len(parents.get(task_id, []))))
        if parents.get(task_id):
            badges.append(_badge("dependencies", "Dependencies", len(parents[task_id])))
        if task_wait:
            badges.append(_badge("human_wait", "Human wait"))
        if task_id == retryable_task_id and safe_retry:
            badges.append(_badge("safe_retry", "Safe retry"))
        if lane == "unknown":
            badges.append(_badge("diagnostic", "Unknown evidence"))
        if task_attachments:
            badges.append(_badge("attachments", "Attachments", len(task_attachments)))
        if task_review:
            badges.append(_badge("review", "Review", task_review["decision"]))
        if review_dependency_warnings:
            badges.append(
                _badge(
                    "review_dependencies",
                    "Review dependency",
                    len(review_dependency_warnings),
                )
            )
        allowed = ["open_graph"]
        if task_wait:
            allowed.append("open_interaction")
        if task_id == retryable_task_id and safe_retry:
            allowed.append("retry")
        scoped_timestamps = [
            _event_timestamp(event, _event_data(event))
            for event in event_rows
            if _same_task(event, task_id)
        ]
        result.append(
            {
                "id": f"{run_id}:{task_id}",
                "run_id": run_id,
                "task_id": task_id,
                "lane": lane,
                "title": _safe_text(task.get("label") or task_id, 240),
                "status": lane,
                "live": live,
                "assignee": _latest_assignment(event_rows, task_id, fallback_to_run=True),
                "comments_count": len(task_comments),
                "comments": task_comments,
                "attachments_count": len(task_attachments),
                "attachments": task_attachments,
                "review": task_review,
                "review_history": task_reviews,
                "project": _project(run.get("flow_ref") or {}),
                "created_at": run.get("created_at"),
                "updated_at": (
                    scoped_timestamps[-1] if scoped_timestamps else run.get("updated_at")
                ),
                "badges": badges,
                "relations": {
                    "parents": list(parents.get(task_id, [])),
                    "children": list(children.get(task_id, [])),
                },
                "allowed_commands": allowed,
                "allowed_actions": ["comment", "assign", "attach", "review"],
                "summary": {
                    "type": _safe_text(task.get("type"), 160),
                    "description": _safe_text(task.get("description"), 500),
                    "evidence": evidence_by_task.get(task_id, "unknown"),
                    "blocking_parents": blocking_parents,
                    "review_dependency_warnings": review_dependency_warnings,
                    "interaction": task_wait,
                    "generation": int(run.get("run_generation") or 0),
                    "diagnostics": {
                        "live": live,
                        "current_generation": current_generation,
                        "stale_generation": not current_generation,
                        "worker": _worker_diagnostic(worker),
                    },
                },
            }
        )
    return result


def _task_relation_projection(run_id: str, graph: dict[str, Any]) -> list[dict[str, str]]:
    result = []
    for relation in graph.get("relations", []):
        if not isinstance(relation, dict):
            continue
        source = str(relation.get("from") or "")
        target = str(relation.get("to") or "")
        if not source or not target:
            continue
        result.append(
            {
                "from": f"{run_id}:{source}",
                "to": f"{run_id}:{target}",
                "type": _safe_text(relation.get("type"), 160),
            }
        )
    return result


def _parse_cursor(cursor: Any) -> int:
    value = str(cursor or "").strip()
    if not value:
        return 0
    if not value.isdecimal():
        raise ValueError("cursor is invalid")
    return int(value)


def workflow_kanban_snapshot(
    conversation_id: str,
    agent_name: str = "",
    run_id: str = "",
    limit: int = 100,
    *,
    cursor: str = "",
    store=None,
    live_run_ids=(),
    waits=None,
    user_id: str = "",
    file_store=None,
    workers=(),
) -> dict[str, Any]:
    """Build a redacted run board or exact single-run task board."""

    conversation_id = str(conversation_id or "").strip()
    if not conversation_id:
        raise ValueError("conversation_id is required")
    count = int(limit)
    if count < 1:
        raise ValueError("limit must be positive")
    if store is None:
        from core.workflow_run_store import WorkflowRunStore

        store = WorkflowRunStore.instance()
    wait_store = None
    wait_rows = list(waits) if waits is not None else []
    if waits is None:
        from core.confirmation_store import ConfirmationStore

        wait_store = ConfirmationStore.instance()
    run_id = str(run_id or "").strip()
    worker_by_run = {
        str(worker.get("workflow_run_id") or ""): worker
        for worker in workers
        if isinstance(worker, dict) and str(worker.get("workflow_run_id") or "")
    }
    if run_id:
        run = store.get_run(run_id)
        if run is None or run.get("conversation_id") != conversation_id:
            raise KeyError("workflow run not found")
        if (
            str(agent_name or "").strip()
            and str(run.get("agent_name") or "").casefold() != str(agent_name).casefold()
        ):
            raise KeyError("workflow run not found")
        projection = inspect_workflow_run(run_id, store=store, live_run_ids=live_run_ids)
        if projection is None:
            raise KeyError("workflow run not found")
        events = list(store.list_events(run_id))
        if wait_store is not None:
            wait_rows = wait_store.list_waits_for_instances([f"workflow:{run_id}"])
        graph = projection.get("flow_graph") or {}
        live = run_id in set(live_run_ids)
        cards = project_task_cards(
            run,
            graph,
            events,
            waits=wait_rows,
            safe_retry=bool(projection.get("safe_retry")),
            live=live,
            file_store=file_store,
            user_id=user_id,
            worker=worker_by_run.get(run_id),
            current_generation=bool(store.is_current_generation(run_id)),
        )
        project = _project(projection.get("flow") or {})
        return {
            "version": 2,
            "generated_at": _utc_now(),
            "conversation_id": conversation_id,
            "agent_name": str(run.get("agent_name") or ""),
            "mode": "tasks",
            "run": projection,
            "lanes": [dict(lane) for lane in TASK_LANES],
            "cards": cards,
            "relations": _task_relation_projection(run_id, graph),
            "projects": [project] if project.get("id") else [],
            "filters": {"limit": count, "run_id": run_id},
            "cursor": None,
        }

    offset = _parse_cursor(cursor)
    rows = list(store.list_runs(conversation_id, agent_name, count + 1, offset=offset))
    has_more = len(rows) > count
    rows = rows[:count]
    if wait_store is not None:
        wait_rows = wait_store.list_waits_for_instances(
            [f"workflow:{run.get('run_id') or ''}" for run in rows]
        )
    cards = []
    for run in rows:
        run_events = store.list_events(str(run.get("run_id") or ""))
        cards.append(
            project_run_card(
                run,
                store=store,
                live_run_ids=live_run_ids,
                events=run_events,
                waits=wait_rows,
                file_store=file_store,
                user_id=user_id,
                worker=worker_by_run.get(str(run.get("run_id") or "")),
            )
        )
    projects = {
        card["project"]["id"]: card["project"]
        for card in cards
        if (card.get("project") or {}).get("id")
    }
    return {
        "version": 2,
        "generated_at": _utc_now(),
        "conversation_id": conversation_id,
        "agent_name": str(agent_name or ""),
        "mode": "runs",
        "lanes": [dict(lane) for lane in RUN_LANES],
        "cards": cards,
        "relations": [],
        "projects": [projects[key] for key in sorted(projects)],
        "filters": {"limit": count, "offset": offset},
        "cursor": str(offset + count) if has_more else None,
    }


def plan_workflow_kanban_command(
    run: dict[str, Any],
    task_id: str,
    target_lane: str,
    events: Iterable[dict[str, Any]],
    *,
    graph=None,
    live: bool = False,
    safe_retry: bool = False,
    waits=(),
) -> CommandPlan:
    """Map a requested move to an existing runtime command or reject it."""

    run_id = str(run.get("run_id") or "")
    task_id = str(task_id or "").strip()
    target_lane = str(target_lane or "").strip().casefold()
    event_rows = list(events)
    wait_rows = _wait_rows(waits, run_id)
    if task_id:
        graph = graph if isinstance(graph, dict) else {}
        cards = project_task_cards(
            run, graph, event_rows, waits=waits, safe_retry=safe_retry, live=live
        )
        card = next((value for value in cards if value["task_id"] == task_id), None)
        if card is None:
            return CommandPlan(
                run_id,
                task_id,
                "unknown",
                target_lane,
                "",
                "unknown_task",
                "The task does not exist in this run graph.",
                False,
            )
        source = card["lane"]
        if target_lane == source:
            return CommandPlan(
                run_id,
                task_id,
                source,
                target_lane,
                "",
                "no_change",
                "The task is already in this lane.",
                False,
            )
        if source == "done":
            return CommandPlan(
                run_id,
                task_id,
                source,
                target_lane,
                "",
                "task_immutable",
                "Completed workflow tasks cannot be moved.",
                False,
            )
        if target_lane != "running":
            return CommandPlan(
                run_id,
                task_id,
                source,
                target_lane,
                "",
                "direct_status_forbidden",
                "Kanban lanes are derived and cannot be written directly.",
                False,
            )
        interaction = card["summary"].get("interaction")
        if source == "waiting" and interaction:
            return CommandPlan(
                run_id,
                task_id,
                source,
                target_lane,
                "open_interaction",
                "interaction_required",
                "Open the exact durable interaction to resume this task.",
                True,
                interaction=interaction,
            )
        if task_id == str(run.get("resume_task_id") or "") or task_id == str(
            (run.get("error") or {}).get("task_id") or ""
        ):
            if safe_retry:
                return CommandPlan(
                    run_id,
                    task_id,
                    source,
                    target_lane,
                    "retry",
                    "retry",
                    "Retry this task from its durable checkpoint.",
                    True,
                    True,
                    True,
                )
            return CommandPlan(
                run_id,
                task_id,
                source,
                target_lane,
                "",
                "unsafe_retry",
                "This task has no safe durable retry checkpoint.",
                False,
            )
        blocking = tuple(card["summary"].get("blocking_parents") or ())
        if blocking:
            return CommandPlan(
                run_id,
                task_id,
                source,
                target_lane,
                "",
                "blocked_by_dependencies",
                "The task cannot start until its parent tasks complete.",
                False,
                blocking_parents=blocking,
            )
        return CommandPlan(
            run_id,
            task_id,
            source,
            target_lane,
            "",
            "task_start_informational",
            "The runtime has no reviewed manual task-start signal.",
            False,
        )

    status = str(run.get("status") or "")
    source = run_lane(status)
    if target_lane == source:
        return CommandPlan(
            run_id,
            "",
            source,
            target_lane,
            "",
            "no_change",
            "The run is already in this lane.",
            False,
        )
    if status in WORKFLOW_TERMINAL_STATUSES:
        return CommandPlan(
            run_id,
            "",
            source,
            target_lane,
            "",
            "terminal_immutable",
            "Terminal workflow runs are immutable.",
            False,
        )
    if status == "retryable_failed" and target_lane == "running":
        if safe_retry:
            return CommandPlan(
                run_id,
                "",
                source,
                target_lane,
                "retry",
                "retry",
                "Retry this run from its durable checkpoint.",
                True,
                True,
                True,
            )
        return CommandPlan(
            run_id,
            "",
            source,
            target_lane,
            "",
            "unsafe_retry",
            "This run has no safe durable retry checkpoint.",
            False,
        )
    if status == "waiting" and target_lane == "running":
        interaction = wait_rows[0] if wait_rows else None
        if interaction:
            return CommandPlan(
                run_id,
                "",
                source,
                target_lane,
                "open_interaction",
                "interaction_required",
                "Open the exact durable interaction to resume this run.",
                True,
                interaction=interaction,
            )
        return CommandPlan(
            run_id,
            "",
            source,
            target_lane,
            "",
            "interaction_not_found",
            "No pending durable interaction was found for this run.",
            False,
        )
    if target_lane == "done" and status in _ACTIVE_RUN_STATUSES:
        if status == "cancelling":
            return CommandPlan(
                run_id,
                "",
                source,
                target_lane,
                "",
                "cancel_already_requested",
                "A graceful cancellation is already in progress.",
                False,
            )
        if not live:
            return CommandPlan(
                run_id,
                "",
                source,
                target_lane,
                "",
                "run_not_live",
                "The run is not owned by a live runtime process.",
                False,
            )
        return CommandPlan(
            run_id,
            "",
            source,
            target_lane,
            "cancel",
            "cancel",
            "Request graceful cancellation. This does not mark the run complete.",
            True,
            True,
            True,
        )
    if target_lane == "force_stopped" and status in _ACTIVE_RUN_STATUSES:
        if not live:
            return CommandPlan(
                run_id,
                "",
                source,
                target_lane,
                "",
                "run_not_live",
                "The run is not owned by a live runtime process.",
                False,
            )
        return CommandPlan(
            run_id,
            "",
            source,
            target_lane,
            "force_stop",
            "force_stop",
            "Force-stop the live run immediately.",
            True,
            True,
            True,
        )
    return CommandPlan(
        run_id,
        "",
        source,
        target_lane,
        "",
        "direct_status_forbidden",
        "Kanban lanes are derived and cannot be written directly.",
        False,
    )


def validate_command_id(value: Any) -> str:
    command_id = str(value or "").strip()
    try:
        parsed = uuid.UUID(command_id)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("idempotency_key must be a UUID") from exc
    return str(parsed)


def publish_workflow_kanban_update(
    run: dict[str, Any], event: dict[str, Any], task_id: str = ""
) -> None:
    """Publish a state invalidation containing no workflow payload."""

    from core.conversation_event_bus import ConversationEventBus

    ConversationEventBus.instance().publish_event(
        str(run.get("conversation_id") or ""),
        "workflow.kanban.updated",
        {
            "conversation_id": str(run.get("conversation_id") or ""),
            "run_id": str(run.get("run_id") or ""),
            "task_id": str(task_id or ""),
            "event_id": str(event.get("event_id") or ""),
            "timestamp": str(event.get("timestamp") or ""),
        },
    )


__all__ = [
    "RUN_LANES",
    "TASK_LANES",
    "CommandPlan",
    "plan_workflow_kanban_command",
    "project_run_card",
    "project_task_cards",
    "publish_workflow_kanban_update",
    "run_lane",
    "validate_command_id",
    "workflow_kanban_snapshot",
]
