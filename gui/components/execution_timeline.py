"""Execution timeline component.

Shows step-by-step task execution progress with timing,
status indicators, and performance insights.
"""

import logging
from typing import Any, Dict, List, Optional

import streamlit as st

from gui.i18n import t

logger = logging.getLogger(__name__)


def render_execution_timeline(
    execution_state: Any,
    task_stats: Optional[Dict[str, Dict[str, Any]]] = None,
) -> None:
    """Render an execution timeline showing per-task progress.

    Args:
        execution_state: ExecutionState with statistics and errors.
        task_stats: Optional dict {task_id: {runs, errors, ff_in, ff_out, bytes_in, bytes_out}}.
    """
    st.markdown(f"### {t('timeline.title')}")

    steps = _extract_steps(execution_state, task_stats)

    if not steps:
        st.info(t("timeline.no_steps"))
        return

    # Summary metrics
    _render_summary(steps, execution_state)

    st.markdown("---")

    # Timeline entries
    for i, step in enumerate(steps):
        _render_step(i, step, len(steps))


def _extract_steps(
    execution_state: Any,
    task_stats: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Extract timeline steps from execution state and task stats."""
    steps = []

    # Build error lookup
    error_map: Dict[str, str] = {}
    if hasattr(execution_state, "errors") and execution_state.errors:
        for err in execution_state.errors:
            if isinstance(err, dict):
                task_id = err.get("task_id", "")
                msg = err.get("error", str(err))
                if task_id:
                    error_map[task_id] = msg

    # From task_stats (continuous executor)
    if task_stats:
        for task_id, stats in task_stats.items():
            runs = stats.get("runs", 0)
            errors = stats.get("errors", 0)
            ff_in = stats.get("ff_in", 0)
            ff_out = stats.get("ff_out", 0)

            if errors > 0 and runs == errors:
                status = "failed"
            elif errors > 0:
                status = "partial"
            elif runs > 0:
                status = "success"
            else:
                status = "skipped"

            steps.append({
                "task_id": task_id,
                "task_type": stats.get("type", ""),
                "status": status,
                "runs": runs,
                "errors": errors,
                "ff_in": ff_in,
                "ff_out": ff_out,
                "error_msg": error_map.get(task_id, ""),
            })
        return steps

    # From execution_state statistics
    stats = {}
    if hasattr(execution_state, "statistics") and execution_state.statistics:
        stats = execution_state.statistics

    task_results = stats.get("task_results", {})
    if task_results:
        for task_id, result in task_results.items():
            status = result.get("status", "success")
            steps.append({
                "task_id": task_id,
                "task_type": result.get("type", ""),
                "status": status,
                "runs": 1 if status != "skipped" else 0,
                "errors": 1 if status == "failed" else 0,
                "ff_in": result.get("input_count", 0),
                "ff_out": result.get("output_count", 0),
                "duration_ms": result.get("duration_ms", 0),
                "error_msg": result.get("error", error_map.get(task_id, "")),
            })
        return steps

    # Fallback: create entries from errors only
    for task_id, msg in error_map.items():
        steps.append({
            "task_id": task_id,
            "task_type": "",
            "status": "failed",
            "runs": 1,
            "errors": 1,
            "ff_in": 0,
            "ff_out": 0,
            "error_msg": msg,
        })

    return steps


def _render_summary(steps: List[Dict[str, Any]], execution_state: Any) -> None:
    """Render summary metrics above the timeline."""
    total = len(steps)
    succeeded = sum(1 for s in steps if s["status"] == "success")
    failed = sum(1 for s in steps if s["status"] == "failed")
    total_errors = sum(s.get("errors", 0) for s in steps)

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        if hasattr(execution_state, "duration_ms") and execution_state.duration_ms:
            st.metric(t("timeline.total_duration"), f"{execution_state.duration_ms / 1000:.2f}s")
        else:
            st.metric(t("timeline.total_duration"), "N/A")

    with col2:
        st.metric(t("timeline.task"), f"{succeeded}/{total}")

    with col3:
        # Find slowest task
        durations = [(s["task_id"], s.get("duration_ms", 0)) for s in steps if s.get("duration_ms")]
        if durations:
            slowest = max(durations, key=lambda x: x[1])
            st.metric(t("timeline.slowest_task"), f"{slowest[0][:15]}", delta=f"{slowest[1]/1000:.2f}s")
        else:
            st.metric(t("timeline.slowest_task"), "-")

    with col4:
        st.metric(t("timeline.error_count"), total_errors)


def _render_step(index: int, step: Dict[str, Any], total: int) -> None:
    """Render a single timeline step."""
    status = step["status"]
    task_id = step["task_id"]
    task_type = step.get("task_type", "")

    # Status indicators
    status_config = {
        "success": ("✅", "green", t("timeline.step_success")),
        "failed": ("❌", "red", t("timeline.step_failed")),
        "skipped": ("⏭️", "gray", t("timeline.step_skipped")),
        "running": ("⏳", "blue", t("timeline.step_running")),
        "partial": ("⚠️", "orange", t("timeline.step_success")),
    }
    emoji, color, status_label = status_config.get(status, ("❓", "gray", status))

    # Build the step line
    connector = "│" if index < total - 1 else " "
    node = "●" if status == "success" else "○" if status == "skipped" else "◉"

    # Task label
    type_badge = f" `{task_type}`" if task_type else ""
    duration_str = ""
    if step.get("duration_ms"):
        duration_str = f" — {step['duration_ms']/1000:.3f}s"

    ff_str = ""
    if step.get("ff_in", 0) or step.get("ff_out", 0):
        ff_str = f" | {step['ff_in']}→{step['ff_out']} FF"

    runs_str = ""
    if step.get("runs", 0) > 1:
        runs_str = f" | {step['runs']} runs"
        if step.get("errors", 0):
            runs_str += f", {step['errors']} err"

    st.markdown(
        f":{color}[{emoji} **{task_id}**]{type_badge}{duration_str}{ff_str}{runs_str}"
    )

    # Show error detail if failed
    if step.get("error_msg"):
        st.caption(f"  └ {step['error_msg'][:200]}")

    # Connector line
    if index < total - 1:
        st.markdown(f"<span style='color:#888; margin-left:8px'>{connector}</span>", unsafe_allow_html=True)
