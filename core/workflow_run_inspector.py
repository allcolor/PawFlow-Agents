"""Redacted operational projections for workflow-agent run inspection."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from core.workflow_agent_contracts import WORKFLOW_TERMINAL_STATUSES

_USAGE_KEYS = (
    "llm_calls", "tokens_in", "tokens_out", "cache_read", "cache_write",
    "duration_ms", "cost_usd", "virtual_cost_usd",
)
_EVENT_KEYS = {
    "started": {"recovery_count"},
    "progress": {
        "task_id", "stage", "label", "service_id", "usage", "phase",
        "iteration", "tool_name", "outcome", "task_type", "attempt",
        "duration_ms",
    },
    "authorization": {
        "task_id", "task_type", "attempt", "effects", "idempotency",
        "target_kind", "authorization_revision", "decision", "reason",
    },
    "group_run_started": {
        "group_run_id", "group_name", "member_count", "max_rounds", "max_tokens",
    },
    "group_participant_failed": {
        "group_run_id", "round", "member_id", "required",
    },
    "group_participant_post": {
        "group_run_id", "round", "member_id", "disposition", "content",
        "citations", "confidence", "token_usage",
    },
    "group_rounds_completed": {
        "group_run_id", "rounds", "participant_calls", "stop_reason", "tokens",
        "llm_calls", "tool_calls", "cost",
    },
    "group_synthesis_completed": {
        "group_run_id", "rounds", "participant_calls", "stop_reason", "tokens",
        "llm_calls", "tool_calls", "cost",
    },
    "group_tool_lifecycle": {
        "group_run_id", "run_id", "round", "member_id", "turn_id",
        "tool_call_id", "tool_name", "phase", "reason",
    },
    "error": {"code", "message", "retryable", "task_id", "error_id"},
    "retrying": {"task_id", "recovery_count"},
    "agent_message": {
        "task_id", "phase", "iteration", "role", "content",
        "structured_content", "model",
    },
    "tool_call": {
        "task_id", "phase", "iteration", "tool_call_id", "tool_name",
        "arguments",
    },
    "tool_result": {
        "task_id", "phase", "iteration", "tool_call_id", "tool_name",
        "content", "outcome",
    },
    "kanban_comment": {
        "comment_id", "task_id", "author_label", "body", "created_at",
    },
    "kanban_assignment": {
        "assignment_id", "task_id", "assignee", "assigned_by_user_id",
        "created_at",
    },
    "kanban_attachment_added": {
        "attachment_id", "task_id", "file_id", "label", "added_by_user_id",
        "created_at",
    },
    "kanban_attachment_removed": {
        "attachment_id", "task_id", "removed_by_user_id", "created_at",
    },
    "kanban_review": {
        "review_id", "task_id", "decision", "reviewer_user_id", "comment",
        "created_at",
    },
    "kanban_command_requested": {
        "command_id", "command", "result_code", "source_lane", "target_lane",
        "task_id", "actor_user_id", "created_at",
    },
    "kanban_command_succeeded": {
        "command_id", "command", "result_code", "source_lane", "target_lane",
        "task_id", "actor_user_id", "created_at",
    },
    "kanban_command_rejected": {
        "command_id", "command", "result_code", "source_lane", "target_lane",
        "task_id", "actor_user_id", "created_at",
    },
}
_EVENT_TEXT_KEYS = {
    "reason", "label", "stage", "service_id", "task_id", "task_type",
    "group_run_id", "group_name", "member_id", "disposition", "stop_reason",
    "run_id", "turn_id", "tool_call_id", "tool_name", "phase", "code",
    "message", "error_id", "outcome",
    "role", "model", "comment_id", "author_label", "created_at",
    "assignment_id", "assignee", "command_id", "command", "result_code",
    "source_lane", "target_lane", "actor_user_id",
    "assigned_by_user_id", "attachment_id", "file_id", "label",
    "added_by_user_id", "removed_by_user_id", "review_id", "decision",
    "reviewer_user_id", "comment",
}
_EVENT_NUMERIC_KEYS = {
    "recovery_count", "attempt", "authorization_revision", "member_count",
    "max_rounds", "max_tokens", "round", "rounds", "participant_calls",
    "llm_calls", "tool_calls", "tokens", "cost", "confidence", "iteration",
    "duration_ms",
}
_SECRET_PATTERN = re.compile(
    r"(?i)(api[_ -]?key|token|secret|password|authorization)\s*[:=]\s*\S+")


def _utc(value: Any) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(float(value), timezone.utc).isoformat()


def _timestamp(value: Any) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value or "")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return 0.0


def _safe_text(value: Any, maximum: int = 240) -> str:
    text = str(value or "").replace("\x00", "").strip()[:maximum]
    return _SECRET_PATTERN.sub(r"\1=[redacted]", text)


def _usage(value: Any) -> dict[str, int | float]:
    raw = value if isinstance(value, dict) else {}
    return {
        key: raw[key] for key in _USAGE_KEYS
        if isinstance(raw.get(key), (int, float)) and not isinstance(raw.get(key), bool)
    }


def _flow_ref(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    return {
        key: raw.get(key) for key in (
            "name", "scope", "version", "content_digest",
            "package_id", "package_version",
        ) if raw.get(key) not in (None, "")
    }


def _citations(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, (list, tuple)):
        return []
    result = []
    for item in value[:20]:
        if not isinstance(item, dict):
            continue
        row = {
            key: _safe_text(item.get(key), 500)
            for key in ("label", "title", "url")
            if item.get(key) not in (None, "")
        }
        if row:
            result.append(row)
    return result


def _artifacts(value: Any) -> list[dict[str, str]]:
    raw = value if isinstance(value, dict) else {}
    artifacts = raw.get("artifacts")
    if not isinstance(artifacts, (list, tuple)):
        return []
    result = []
    for item in artifacts[:100]:
        if not isinstance(item, dict):
            continue
        row = {
            key: _safe_text(item.get(key), 500)
            for key in ("kind", "id", "label")
            if item.get(key) not in (None, "")
        }
        if row:
            result.append(row)
    return result


def _event_projection(event: dict[str, Any]) -> dict[str, Any]:
    event_type = str(event.get("event_type") or "")
    raw = event.get("data") if isinstance(event.get("data"), dict) else {}
    data = {}
    for key in _EVENT_KEYS.get(event_type, set()):
        value = raw.get(key)
        if key in {"usage", "token_usage"}:
            value = _usage(value)
        elif key == "citations":
            value = _citations(value)
        elif key == "content":
            structured = None
            if event_type == "agent_message" and isinstance(value, str):
                try:
                    candidate = json.loads(value)
                except (TypeError, ValueError):
                    candidate = None
                if isinstance(candidate, (dict, list)):
                    from core.gating_policy import redact_arguments
                    structured = redact_arguments(
                        candidate, max_string=2000, max_items=64, max_depth=6)
            if structured not in (None, "", [], {}):
                data["structured_content"] = structured
                value = None
            elif event_type == "agent_message" and str(value or "").lstrip().startswith(
                ("{", "[")
            ):
                value = "Structured response incomplete."
            else:
                value = _safe_text(value, 8000)
        elif key in {"arguments", "structured_content"}:
            from core.gating_policy import redact_arguments
            value = redact_arguments(
                value,
                max_string=2000 if key == "structured_content" else 800,
                max_items=64 if key == "structured_content" else 32,
                max_depth=6,
            )
        elif key in {"body", "comment"}:
            value = _safe_text(value, 4000)
        elif key in _EVENT_TEXT_KEYS:
            value = _safe_text(value, 160 if key != "reason" else 240)
        elif key in _EVENT_NUMERIC_KEYS:
            value = (
                value if isinstance(value, (int, float))
                and not isinstance(value, bool) else None
            )
        elif key == "required":
            value = value if isinstance(value, bool) else None
        if value not in (None, "", [], {}):
            data[key] = value
    return {
        "sequence": int(event.get("sequence") or 0),
        "event_type": event_type,
        "timestamp": str(event.get("timestamp") or ""),
        "data": data,
    }


def workflow_run_summary(
        run: dict[str, Any], *, store=None, live_run_ids=()) -> dict[str, Any]:
    if store is None:
        from core.workflow_run_store import WorkflowRunStore
        store = WorkflowRunStore.instance()
    status = str(run.get("status") or "")
    recoverable = status in {
        "accepted", "running", "committing", "retryable_failed"}
    raw_error = run.get("error") if isinstance(run.get("error"), dict) else {}
    error = {
        "error_id": str(raw_error.get("error_id") or ""),
        "code": str(raw_error.get("code") or ""),
        "message": _safe_text(raw_error.get("message")),
        "retryable": bool(raw_error.get("retryable")),
        "task_id": str(raw_error.get("task_id") or ""),
        "created_at": str(raw_error.get("created_at") or ""),
    } if raw_error else None
    return {
        "run_id": str(run.get("run_id") or ""),
        "root_turn_id": str(run.get("root_turn_id") or ""),
        "agent_name": str(run.get("agent_name") or ""),
        "generation": int(run.get("run_generation") or 0),
        "status": status,
        "failure_reason": _safe_text(run.get("reason")),
        "error": error,
        "flow": _flow_ref(run.get("flow_ref")),
        "invocation_mode": str(run.get("invocation_mode") or ""),
        "permission_mode": str(run.get("permission_mode") or ""),
        "usage": _usage(run.get("usage")),
        "artifacts": _artifacts(run.get("staged_result")),
        "claimed_count": len(run.get("claimed_ids") or []),
        "terminal_commit": {
            "message_committed": bool(run.get("message_committed")),
            "inbox_acknowledged": bool(run.get("inbox_acknowledged")),
            "outbox_enqueued": bool(run.get("outbox_enqueued")),
        },
        "recovery_count": int(run.get("recovery_count") or 0),
        "created_at": _utc(run.get("created_at")),
        "updated_at": _utc(run.get("updated_at")),
        "terminal_at": _utc(run.get("terminal_at")),
        "safe_retry": (
            recoverable and store.is_current_generation(run["run_id"])
            and run["run_id"] not in set(live_run_ids)
            and (status != "retryable_failed" or bool(
                run.get("resume_task_id") and run.get("resume_flowfile_json")))),
        "can_delete": (
            status in WORKFLOW_TERMINAL_STATUSES
            and run["run_id"] not in set(live_run_ids)),
    }


def list_workflow_runs(conversation_id: str, agent_name: str = "",
                       limit: int = 50, *, store=None,
                       live_run_ids=()) -> list[dict[str, Any]]:
    if store is None:
        from core.workflow_run_store import WorkflowRunStore
        store = WorkflowRunStore.instance()
    return [workflow_run_summary(
        row, store=store, live_run_ids=live_run_ids) for row in store.list_runs(
        conversation_id, agent_name, limit)]


def _flow_graph(run: dict[str, Any], events=()) -> dict[str, Any]:
    empty = {"tasks": [], "relations": [], "direction": "LR"}
    flow_ref = run.get("flow_ref") if isinstance(run.get("flow_ref"), dict) else {}
    flow_fqn = str(flow_ref.get("name") or "")
    if not flow_fqn:
        return empty
    try:
        from core.workflow_agent_resources import resolve_exact_agent_workflow
        resolved = resolve_exact_agent_workflow(
            flow_fqn,
            str(run.get("user_id") or ""),
            str(run.get("conversation_id") or ""),
        )
    except (FileNotFoundError, KeyError, TypeError, ValueError):
        return empty
    if resolved.ref.to_dict() != flow_ref:
        return empty
    definition = resolved.definition
    layouts = definition.get("layouts") if isinstance(definition, dict) else {}
    layout_id = str(definition.get("default_layout_id") or "")
    layout = layouts.get(layout_id) if isinstance(layouts, dict) else {}
    layout = layout if isinstance(layout, dict) else {}
    layout_nodes = layout.get("nodes") if isinstance(layout.get("nodes"), dict) else {}
    direction = str(layout.get("direction") or "LR").upper()
    if direction not in {"LR", "RL", "TB", "BT"}:
        direction = "LR"

    raw_tasks = definition.get("tasks") if isinstance(definition, dict) else {}
    raw_tasks = raw_tasks if isinstance(raw_tasks, dict) else {}
    task_ids = {str(task_id) for task_id in raw_tasks}
    task_states: dict[str, str] = {}
    active_task_id = ""
    for event in events:
        if not isinstance(event, dict):
            continue
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        task_id = str(data.get("task_id") or "")
        if not task_id or task_id not in task_ids:
            continue
        if event.get("event_type") == "authorization":
            if str(data.get("decision") or "").casefold() != "execute":
                continue
            if (active_task_id and active_task_id != task_id
                    and task_states.get(active_task_id) == "running"):
                task_states[active_task_id] = "completed"
            task_states[task_id] = "running"
            active_task_id = task_id
            continue
        if event.get("event_type") != "progress":
            continue
        stage = str(data.get("stage") or "").casefold()
        outcome = str(data.get("outcome") or "").casefold()
        if stage == "task_completed" or outcome == "completed":
            task_states[task_id] = "completed"
            if active_task_id == task_id:
                active_task_id = ""
        elif stage == "task_failed" or outcome == "failed":
            task_states[task_id] = "failed"
            if active_task_id == task_id:
                active_task_id = ""
        else:
            task_states[task_id] = "running"
            active_task_id = task_id

    raw_error = run.get("error") if isinstance(run.get("error"), dict) else {}
    failed_task_id = str(raw_error.get("task_id") or "")
    if str(run.get("status") or "") == "failed" and failed_task_id in task_ids:
        task_states[failed_task_id] = "failed"
    elif str(run.get("status") or "") == "completed" and active_task_id:
        task_states[active_task_id] = "completed"

    def geometry(value: Any, fallback: float) -> float:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(max(-100000, min(100000, value)))
        return fallback

    tasks = []
    for task_id, raw in list(raw_tasks.items())[:300]:
        task = raw if isinstance(raw, dict) else {}
        node = layout_nodes.get(task_id)
        node = node if isinstance(node, dict) else {}
        tasks.append({
            "id": _safe_text(task_id, 160),
            "type": _safe_text(task.get("type"), 160),
            "label": _safe_text(task.get("label") or task_id, 240),
            "description": _safe_text(task.get("description"), 500),
            "status": task_states.get(str(task_id), "pending"),
            "x": geometry(node.get("x"), 0.0),
            "y": geometry(node.get("y"), 0.0),
            "width": max(80.0, geometry(node.get("width"), 200.0)),
            "height": max(48.0, geometry(node.get("height"), 72.0)),
        })
    relations = []
    raw_relations = definition.get("relations") if isinstance(definition, dict) else []
    if isinstance(raw_relations, list):
        for raw in raw_relations[:500]:
            if not isinstance(raw, dict):
                continue
            relation = {
                key: _safe_text(raw.get(key), 160)
                for key in ("from", "to", "type")
            }
            if relation["from"] and relation["to"]:
                relations.append(relation)
    return {"tasks": tasks, "relations": relations, "direction": direction}


def inspect_workflow_run(
        run_id: str, *, store=None, live_run_ids=()) -> dict[str, Any] | None:
    if store is None:
        from core.workflow_run_store import WorkflowRunStore
        store = WorkflowRunStore.instance()
    run = store.get_run(run_id)
    if run is None:
        return None
    result = workflow_run_summary(
        run, store=store, live_run_ids=live_run_ids)
    result["events"] = [
        _event_projection(event) for event in store.list_events(run_id)]
    result["flow_graph"] = _flow_graph(run, result["events"])
    return result


def workflow_operational_summary(
        conversation_id: str, agent_name: str = "", *,
        store=None, inbox=None, now: float | None = None,
        backlog_alert: int = 100) -> dict[str, Any]:
    """Return redacted scoped counters and deterministic operational alerts."""
    if store is None:
        from core.workflow_run_store import WorkflowRunStore
        store = WorkflowRunStore.instance()
    if inbox is None:
        from core.agent_inbox_store import AgentInboxStore
        inbox = AgentInboxStore.instance()
    timestamp = (
        datetime.now(timezone.utc).timestamp() if now is None else float(now))
    runs = list(store.list_runs(conversation_id, agent_name, 200))
    statuses: dict[str, int] = {}
    modes: dict[str, int] = {}
    usage: dict[str, int | float] = {}
    failed_ids, overdue_ids, recovery_ids = [], [], []
    agents = {
        str(run.get("agent_name") or "").casefold()
        for run in runs if str(run.get("agent_name") or "")}
    if str(agent_name or "").strip():
        agents = {str(agent_name).casefold()}
    else:
        agents.update(inbox.list_agent_keys(conversation_id))
    for run in runs:
        status = str(run.get("status") or "")
        mode = str(run.get("invocation_mode") or "")
        statuses[status] = statuses.get(status, 0) + 1
        modes[mode] = modes.get(mode, 0) + 1
        for key, value in _usage(run.get("usage")).items():
            usage[key] = usage.get(key, 0) + value
        run_id = str(run.get("run_id") or "")
        if status == "failed":
            failed_ids.append(run_id)
        deadline_at = run.get("deadline_at")
        if (status in {"accepted", "running", "committing"}
                and deadline_at
                and _timestamp(deadline_at) <= timestamp):
            overdue_ids.append(run_id)
        if int(run.get("recovery_count") or 0) >= 3:
            recovery_ids.append(run_id)

    inbox_counts: dict[str, int] = {}
    expired_claims = 0
    for agent in sorted(agents):
        for item in inbox.list_items(conversation_id, agent, limit=10000):
            inbox_counts[item.state] = inbox_counts.get(item.state, 0) + 1
            if (item.state == "claimed"
                    and _timestamp(item.lease_expires_at) <= timestamp):
                expired_claims += 1

    alerts = []

    def alert(code: str, severity: str, count: int, run_ids=()) -> None:
        if count:
            row = {"code": code, "severity": severity, "count": int(count)}
            if run_ids:
                row["run_ids"] = list(run_ids)[:20]
            alerts.append(row)

    alert("workflow_failed_runs", "warning", len(failed_ids), failed_ids)
    alert("workflow_overdue_runs", "critical", len(overdue_ids), overdue_ids)
    alert("workflow_recovery_churn", "warning", len(recovery_ids), recovery_ids)
    alert(
        "workflow_inbox_backlog", "warning",
        inbox_counts.get("pending", 0)
        if inbox_counts.get("pending", 0) >= max(1, int(backlog_alert)) else 0)
    alert("workflow_expired_claims", "critical", expired_claims)
    health = (
        "critical" if any(row["severity"] == "critical" for row in alerts)
        else "warning" if alerts else "ok")
    return {
        "generated_at": _utc(timestamp),
        "conversation_id": conversation_id,
        "agent_name": str(agent_name or ""),
        "health": health,
        "runs": {
            "total": len(runs),
            "status": dict(sorted(statuses.items())),
            "invocation_mode": dict(sorted(modes.items())),
            "recoveries": sum(int(run.get("recovery_count") or 0) for run in runs),
            "usage": usage,
        },
        "inbox": {**dict(sorted(inbox_counts.items())),
                  "expired_claims": expired_claims},
        "alerts": alerts,
    }


__all__ = [
    "inspect_workflow_run", "list_workflow_runs", "workflow_operational_summary",
    "workflow_run_summary",
]
