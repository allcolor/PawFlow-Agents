"""Redacted operational projections for workflow-agent run inspection."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

_USAGE_KEYS = (
    "llm_calls", "tokens_in", "tokens_out", "cache_read", "cache_write",
    "duration_ms", "cost_usd", "virtual_cost_usd",
)
_EVENT_KEYS = {
    "started": {"recovery_count"},
    "progress": {"task_id", "stage", "label", "service_id", "usage"},
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
}
_EVENT_TEXT_KEYS = {
    "reason", "label", "stage", "service_id", "task_id", "task_type",
    "group_run_id", "group_name", "member_id", "disposition", "stop_reason",
    "run_id", "turn_id", "tool_call_id", "tool_name", "phase",
}
_EVENT_NUMERIC_KEYS = {
    "recovery_count", "attempt", "authorization_revision", "member_count",
    "max_rounds", "max_tokens", "round", "rounds", "participant_calls",
    "llm_calls", "tool_calls", "tokens", "cost", "confidence",
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
            value = _safe_text(value, 8000)
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


def workflow_run_summary(run: dict[str, Any], *, store=None) -> dict[str, Any]:
    if store is None:
        from core.workflow_run_store import WorkflowRunStore
        store = WorkflowRunStore.instance()
    status = str(run.get("status") or "")
    recoverable = status in {"accepted", "running", "committing"}
    return {
        "run_id": str(run.get("run_id") or ""),
        "root_turn_id": str(run.get("root_turn_id") or ""),
        "agent_name": str(run.get("agent_name") or ""),
        "generation": int(run.get("run_generation") or 0),
        "status": status,
        "failure_reason": _safe_text(run.get("reason")),
        "flow": _flow_ref(run.get("flow_ref")),
        "invocation_mode": str(run.get("invocation_mode") or ""),
        "permission_mode": str(run.get("permission_mode") or ""),
        "usage": _usage(run.get("usage")),
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
        "safe_retry": recoverable and store.is_current_generation(run["run_id"]),
    }


def list_workflow_runs(conversation_id: str, agent_name: str = "",
                       limit: int = 50, *, store=None) -> list[dict[str, Any]]:
    if store is None:
        from core.workflow_run_store import WorkflowRunStore
        store = WorkflowRunStore.instance()
    return [workflow_run_summary(row, store=store) for row in store.list_runs(
        conversation_id, agent_name, limit)]


def inspect_workflow_run(run_id: str, *, store=None) -> dict[str, Any] | None:
    if store is None:
        from core.workflow_run_store import WorkflowRunStore
        store = WorkflowRunStore.instance()
    run = store.get_run(run_id)
    if run is None:
        return None
    result = workflow_run_summary(run, store=store)
    result["events"] = [
        _event_projection(event) for event in store.list_events(run_id)]
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
        if (status in {"accepted", "running", "committing"}
                and _timestamp(run.get("deadline_at")) <= timestamp):
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
