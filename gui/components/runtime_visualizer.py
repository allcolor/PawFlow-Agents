"""Runtime Flow Visualizer — live view of a running flow.

Shows task nodes colored by state (running/stopped/error/disabled),
edges colored by queue fill level, and live stats overlays.
Uses streamlit_flow like the Editor, but read-only.
"""

import streamlit as st
from streamlit_flow import streamlit_flow
from streamlit_flow.state import StreamlitFlowState
from streamlit_flow.elements import StreamlitFlowNode, StreamlitFlowEdge
from streamlit_flow.layouts import ManualLayout
from typing import Any, Dict, List, Optional

from engine.continuous_executor import ContinuousFlowExecutor
from gui.i18n import t


STATE_COLORS = {
    "running": "#28a745",
    "stopped": "#6c757d",
    "error": "#dc3545",
    "disabled": "#343a40",
}

STATE_BORDERS = {
    "running": "#1e7e34",
    "stopped": "#495057",
    "error": "#a71d2a",
    "disabled": "#1d2124",
}

STATE_ICONS = {
    "running": "🟢",
    "stopped": "⏸️",
    "error": "🔥",
    "disabled": "⚫",
}


def _queue_color(fill_pct: float) -> str:
    if fill_pct >= 90:
        return "#dc3545"
    elif fill_pct >= 60:
        return "#fd7e14"
    elif fill_pct >= 30:
        return "#ffc107"
    elif fill_pct > 0:
        return "#28a745"
    return "#6c757d"


def _build_fingerprint(task_ids: List[str], queue_stats: List[dict]) -> str:
    """Fingerprint of flow structure only (not dynamic data like counts)."""
    edges = [(qs["source"], qs["target"]) for qs in queue_stats]
    return f"{sorted(task_ids)}|{edges}"


def render_runtime_flow(
    executor: ContinuousFlowExecutor,
    height: int = 500,
) -> Optional[str]:
    """Render a live, read-only view of the running flow."""
    task_states = executor.get_all_task_states()
    status = executor.get_status()
    queue_stats = status.get("queue_stats", [])

    task_ids = list(task_states.keys())
    if not task_ids:
        st.info(t("common.none"))
        return None

    # Only rebuild state when flow structure changes (not on every refresh)
    fingerprint = _build_fingerprint(task_ids, queue_stats)
    positions = st.session_state.get("_rt_node_positions", {})

    need_rebuild = (
        st.session_state.get("_rt_fingerprint") != fingerprint
        or "_rt_state" not in st.session_state
    )

    if need_rebuild:
        nodes = _build_nodes(task_states, positions)
        edges = _build_edges(queue_stats)
        state = StreamlitFlowState(nodes=nodes, edges=edges)
        st.session_state._rt_state = state
        st.session_state._rt_fingerprint = fingerprint
    else:
        state = st.session_state._rt_state

    result_state = streamlit_flow(
        "pyfi2_runtime_viz",
        state=state,
        fit_view=True,
        height=height,
        get_node_on_click=True,
        get_edge_on_click=False,
        hide_watermark=True,
        allow_new_edges=False,
        layout=ManualLayout(),
        pan_on_drag=True,
        allow_zoom=True,
    )

    # Persist positions from drag
    if result_state and result_state.nodes:
        for node in result_state.nodes:
            if hasattr(node, "position") and node.position:
                positions[node.id] = (
                    node.position.get("x", 0),
                    node.position.get("y", 0),
                )
        st.session_state._rt_node_positions = positions
        st.session_state._rt_state = result_state

    if result_state and result_state.selected_id:
        return result_state.selected_id
    return None


def _build_nodes(task_states: Dict[str, dict], positions: dict) -> List[StreamlitFlowNode]:
    nodes = []
    for i, (task_id, state) in enumerate(task_states.items()):
        task_type = state.get("task_type", "?")
        task_state = state.get("state", "stopped")
        icon = STATE_ICONS.get(task_state, "❓")
        ff_in = state.get("flowfiles_in", 0)
        ff_out = state.get("flowfiles_out", 0)
        error = state.get("error_message", "") or state.get("error", "")

        label = f"{icon} {task_type}\n{task_id}"
        if ff_in or ff_out:
            label += f"\nin:{ff_in} out:{ff_out}"
        if error:
            label += f"\n⚠ {error[:30]}"

        color = STATE_COLORS.get(task_state, "#6c757d")
        border = STATE_BORDERS.get(task_state, "#495057")
        pos = positions.get(task_id, (100 + i * 220, 100 + (i % 3) * 80))

        nodes.append(StreamlitFlowNode(
            id=task_id,
            pos=pos,
            data={"content": label},
            node_type="default",
            source_position="right",
            target_position="left",
            draggable=True,
            selectable=True,
            connectable=False,
            style={
                "background": color,
                "color": "white",
                "border": f"3px solid {border}",
                "borderRadius": "10px",
                "padding": "10px",
                "fontSize": "11px",
                "width": "170px",
                "whiteSpace": "pre-wrap",
                "textAlign": "center",
            },
        ))
    return nodes


def _build_edges(queue_stats: List[dict]) -> List[StreamlitFlowEdge]:
    edges = []
    for i, qs in enumerate(queue_stats):
        source = qs["source"]
        target = qs["target"]
        q_size = qs.get("queue_size", 0)
        max_size = qs.get("max_queue_size", 1) or 1
        fill_pct = (q_size / max_size) * 100

        color = _queue_color(fill_pct)
        width = max(2, min(6, int(fill_pct / 20) + 2))

        bp = qs.get("backpressured", False)
        edge_label = f"{q_size}/{max_size}"
        if bp:
            edge_label = f"🔴 {edge_label}"

        edges.append(StreamlitFlowEdge(
            id=f"rt_e{i}_{source}_{target}",
            source=source,
            target=target,
            animated=(q_size > 0),
            edge_type="smoothstep",
            style={"stroke": color, "strokeWidth": width},
            label=edge_label,
        ))
    return edges
