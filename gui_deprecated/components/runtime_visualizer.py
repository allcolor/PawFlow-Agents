"""Runtime Flow Visualizer — live view of a running flow.

Shows task nodes colored by state (running/stopped/error/disabled),
edges colored by queue fill level, and live stats overlays.
Uses streamlit_flow like the Editor, but read-only.

Position model:
  Source of truth = deployment file layout.
  If no layout on disk → auto-layout → save to disk immediately.
  Frozen by default.  Unfreeze to drag or auto-layout.
  Save writes to disk + freeze.  Cancel reloads from disk + freeze.
"""

import streamlit as st
from streamlit_flow import streamlit_flow
from streamlit_flow.state import StreamlitFlowState
from streamlit_flow.elements import StreamlitFlowNode, StreamlitFlowEdge
from streamlit_flow.layouts import ManualLayout, LayeredLayout
from collections import defaultdict
from typing import Any, Dict, List, Optional

from engine.continuous_executor import ContinuousFlowExecutor
from gui.i18n import t
from gui.components.color_scheme import get_task_color


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

RELATION_COLORS = {
    "success": "#28a745",
    "failure": "#dc3545",
    "retry": "#fd7e14",
    "original": "#6c757d",
}


# ---------------------------------------------------------------------------
# Session-state key helpers  (shared across stopped / live views per instance)
# ---------------------------------------------------------------------------

def _pos_key(instance_id: str) -> str:
    """Current positions (may differ from disk after drag/auto-layout)."""
    return f"_rt_pos_{instance_id}"

def _disk_key(instance_id: str) -> str:
    """Snapshot of what's on disk (for dirty detection)."""
    return f"_rt_disk_pos_{instance_id}"

def _loaded_key(instance_id: str) -> str:
    return f"_rt_loaded_{instance_id}"

def _autosave_key(instance_id: str) -> str:
    return f"_rt_need_autosave_{instance_id}"


# ---------------------------------------------------------------------------
# Disk I/O
# ---------------------------------------------------------------------------

def _load_disk_positions(instance_id: str) -> dict:
    """Load saved layout positions from deployment registry."""
    try:
        from core.deployment_registry import DeploymentRegistry
        reg = DeploymentRegistry.get_instance()
        raw = reg.get_layout(instance_id)
        if not raw:
            return {}
        return {tid: (pos["x"], pos["y"]) for tid, pos in raw.items()
                if isinstance(pos, dict) and "x" in pos}
    except Exception:
        return {}


def save_layout_to_disk(instance_id: str, positions: dict):
    """Save layout positions to deployment registry."""
    try:
        from core.deployment_registry import DeploymentRegistry
        reg = DeploymentRegistry.get_instance()
        layout = {tid: {"x": pos[0], "y": pos[1]} for tid, pos in positions.items()}
        reg.save_layout(instance_id, layout)
    except Exception:
        pass


def reload_positions_from_disk(instance_id: str, view_suffix: str = ""):
    """Cancel: reload from disk, clear cached view state."""
    positions = _load_disk_positions(instance_id)
    st.session_state[_pos_key(instance_id)] = positions
    st.session_state[_disk_key(instance_id)] = dict(positions)
    # Clear view-specific cached state
    for k in [f"_rt_fp{view_suffix}", f"_rt_fp_struct{view_suffix}",
              f"_rt_state{view_suffix}"]:
        st.session_state.pop(k, None)


def is_layout_dirty(instance_id: str) -> bool:
    """Check if current positions differ from disk."""
    cur = st.session_state.get(_pos_key(instance_id), {})
    disk = st.session_state.get(_disk_key(instance_id), {})
    return bool(cur) and cur != disk


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_positions(result_state) -> dict:
    """Extract {task_id: (x, y)} from a result_state."""
    positions = {}
    if result_state and result_state.nodes:
        for node in result_state.nodes:
            if hasattr(node, "position") and node.position:
                positions[node.id] = (
                    node.position.get("x", 0),
                    node.position.get("y", 0),
                )
    return positions


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


def _build_fingerprint(task_ids, queue_stats, frozen=False, task_states=None):
    edges = [(qs["source"], qs["target"], qs.get("queue_size", 0),
              qs.get("backpressured", False)) for qs in queue_stats]
    states = ""
    if task_states:
        states = str(sorted(
            (tid, ts.get("state", "?"), ts.get("flowfiles_in", 0),
             ts.get("flowfiles_out", 0), ts.get("error_count", 0),
             ts.get("in_flight", False))
            for tid, ts in task_states.items()
        ))
    return f"{sorted(task_ids)}|{edges}|{states}|frozen={frozen}"


def _structure_fingerprint(task_ids, queue_stats, frozen):
    return f"{sorted(task_ids)}|{[(qs['source'], qs['target']) for qs in queue_stats]}|frozen={frozen}"


# ---------------------------------------------------------------------------
# Position loading  (shared by both views)
# ---------------------------------------------------------------------------

def _ensure_positions_loaded(instance_id: str) -> tuple:
    """Load positions from disk once per session. Returns (positions, need_auto_layout).

    If deployment file has no layout → flags auto-layout + auto-save.
    """
    pk = _pos_key(instance_id)
    dk = _disk_key(instance_id)
    lk = _loaded_key(instance_id)

    if st.session_state.get(lk):
        return st.session_state.get(pk, {}), False

    st.session_state[lk] = True
    positions = _load_disk_positions(instance_id)
    st.session_state[pk] = positions
    st.session_state[dk] = dict(positions)

    if not positions:
        # No layout on disk → will auto-layout and auto-save result
        st.session_state[_autosave_key(instance_id)] = True
        return positions, True

    return positions, False


def _capture_react_result(result_state, state, instance_id, view_suffix):
    """After streamlit_flow(), capture positions from React result."""
    if result_state is state:
        return  # echo-back, ignore

    rkey = f"_rt_last_result{view_suffix}"
    st.session_state[rkey] = result_state

    new_positions = _extract_positions(result_state)
    if not new_positions:
        return

    pk = _pos_key(instance_id)
    dk = _disk_key(instance_id)
    st.session_state[pk] = new_positions

    # Auto-save: first load had no disk layout → save the auto-layout result
    if st.session_state.pop(_autosave_key(instance_id), False):
        save_layout_to_disk(instance_id, new_positions)
        st.session_state[dk] = dict(new_positions)


# ---------------------------------------------------------------------------
# Node / Edge builders
# ---------------------------------------------------------------------------

def _get_bulletin_errors(task_id: str) -> dict:
    try:
        from core.bulletin import BulletinBoard
        bb = BulletinBoard.get_instance()
        bulletins = bb.get_bulletins(source_id=task_id)
        errors = [b for b in bulletins if b.get("level") == "ERROR"]
        warnings = [b for b in bulletins if b.get("level") == "WARNING"]
        return {
            "errorCount": len(errors),
            "warningCount": len(warnings),
            "errorMessages": [b.get("message", "")[:80] for b in errors[:5]],
        }
    except Exception:
        return {}


def _compute_handle_counts(queue_stats):
    source_counts: Dict[str, int] = {}
    target_counts: Dict[str, int] = {}
    for qs in queue_stats:
        src, tgt = qs["source"], qs["target"]
        source_counts[src] = source_counts.get(src, 0) + 1
        target_counts[tgt] = target_counts.get(tgt, 0) + 1
    return {
        nid: {"source": source_counts.get(nid, 0), "target": target_counts.get(nid, 0)}
        for nid in set(list(source_counts) + list(target_counts))
    }


def _build_nodes(task_states, positions, handle_counts=None, frozen=False):
    nodes = []
    for i, (task_id, state) in enumerate(task_states.items()):
        task_type = state.get("task_type", "?")
        task_state = state.get("state", "stopped")
        icon = STATE_ICONS.get(task_state, "❓")
        error = state.get("error_message", "") or state.get("error", "")
        ff_in = state.get("flowfiles_in", 0)
        ff_out = state.get("flowfiles_out", 0)

        label = f"{icon} {task_type}\n{task_id}"
        if error:
            label += f"\n⚠ {error[:30]}"

        color = STATE_COLORS.get(task_state, "#6c757d")
        border = STATE_BORDERS.get(task_state, "#495057")
        task_color = get_task_color(task_type)
        pos = positions.get(task_id, (100 + i * 220, 100 + (i % 3) * 80))

        bulletin_data = _get_bulletin_errors(task_id)
        from gui.components.color_scheme import get_task_category, CATEGORY_ICONS
        category = get_task_category(task_type)

        hc = handle_counts.get(task_id, {}) if handle_counts else {}
        node_data = {
            "content": label,
            "taskType": task_type,
            "taskId": task_id,
            "category": category,
            "icon": CATEGORY_ICONS.get(category, ""),
            "stats": {"in": ff_in, "out": ff_out},
            "handleCounts": {
                "source": max(1, hc.get("source", 1)),
                "target": max(1, hc.get("target", 1)),
            },
            **bulletin_data,
        }
        if state.get("backpressured"):
            node_data["backpressured"] = True

        pulse_class = ""
        if state.get("in_flight"):
            pulse_class = "running-pulse"
        elif task_state == "error":
            pulse_class = "error-pulse"

        nodes.append(StreamlitFlowNode(
            id=task_id,
            pos=pos,
            data=node_data,
            node_type="default",
            source_position="right",
            target_position="left",
            draggable=not frozen,
            selectable=True,
            connectable=False,
            className=pulse_class,
            style={
                "background": color,
                "color": "white",
                "border": f"3px solid {border}",
                "borderLeft": f"6px solid {task_color}",
                "borderRadius": "10px",
                "padding": "10px",
                "fontSize": "11px",
                "width": "170px",
                "whiteSpace": "pre-wrap",
                "textAlign": "center",
            },
        ))
    return nodes


def _build_edges(queue_stats, positions=None):
    """Build edges with handle indices sorted by y-position to minimize crossings."""
    pos = positions or {}

    outgoing: Dict[str, List[int]] = defaultdict(list)
    incoming: Dict[str, List[int]] = defaultdict(list)

    for i, qs in enumerate(queue_stats):
        outgoing[qs["source"]].append(i)
        incoming[qs["target"]].append(i)

    source_handle: Dict[int, int] = {}
    target_handle: Dict[int, int] = {}

    for node_id, indices in outgoing.items():
        sorted_idx = sorted(indices, key=lambda i: pos.get(
            queue_stats[i]["target"], (0, 0))[1])
        for h, qi in enumerate(sorted_idx):
            source_handle[qi] = h

    for node_id, indices in incoming.items():
        sorted_idx = sorted(indices, key=lambda i: pos.get(
            queue_stats[i]["source"], (0, 0))[1])
        for h, qi in enumerate(sorted_idx):
            target_handle[qi] = h

    edges = []
    for i, qs in enumerate(queue_stats):
        source, target = qs["source"], qs["target"]
        q_size = qs.get("queue_size", 0)
        max_size = qs.get("max_queue_size", 1) or 1
        fill_pct = (q_size / max_size) * 100
        relationship = qs.get("relationship", qs.get("type", "success"))

        color = _queue_color(fill_pct) if q_size > 0 else RELATION_COLORS.get(relationship, "#6c757d")
        width = max(2, min(6, int(fill_pct / 20) + 2))

        edge_label = relationship
        if q_size > 0 or max_size < 10000:
            edge_label = f"{relationship} ({q_size}/{max_size})"
        if qs.get("backpressured"):
            edge_label = f"🔴 {edge_label}"

        edges.append(StreamlitFlowEdge(
            id=f"rt_e{i}_{source}_{target}",
            source=source,
            target=target,
            animated=(q_size > 0),
            edge_type="smoothstep",
            style={"stroke": color, "strokeWidth": width},
            label=edge_label,
            sourceHandle=f"source-{source_handle.get(i, 0)}",
            targetHandle=f"target-{target_handle.get(i, 0)}",
        ))
    return edges


# ---------------------------------------------------------------------------
# Public render functions
# ---------------------------------------------------------------------------

def render_runtime_flow_static(
    task_states: Dict[str, dict],
    queue_stats: List[dict],
    height: int = 500,
    key_suffix: str = "_stopped",
    use_auto_layout: bool = False,
    instance_id: Optional[str] = None,
    frozen: bool = False,
) -> Optional[str]:
    """Render a flow view from static data (e.g. stopped flow / checkpoint)."""
    task_ids = list(task_states.keys())
    if not task_ids:
        st.info(t("common.none"))
        return None

    # --- 1. Positions from disk ---
    positions, need_auto = _ensure_positions_loaded(instance_id) if instance_id else ({}, False)
    if need_auto:
        use_auto_layout = True
    positions = st.session_state.get(_pos_key(instance_id), positions)

    # --- 2. Build state if needed ---
    fkey = f"_rt_fp{key_suffix}"
    skey = f"_rt_state{key_suffix}"
    fingerprint = _build_fingerprint(task_ids, queue_stats, frozen=frozen,
                                     task_states=task_states)
    struct_fp = _structure_fingerprint(task_ids, queue_stats, frozen)
    prev_struct = st.session_state.get(f"{fkey}_struct")

    if not frozen:
        need_rebuild = (use_auto_layout or prev_struct != struct_fp
                        or skey not in st.session_state)
    else:
        need_rebuild = (use_auto_layout
                        or st.session_state.get(fkey) != fingerprint
                        or skey not in st.session_state)

    if need_rebuild:
        hc = _compute_handle_counts(queue_stats)
        nodes = _build_nodes(task_states, positions, hc, frozen=frozen)
        edges = _build_edges(queue_stats, positions)
        state = StreamlitFlowState(nodes=nodes, edges=edges)
        st.session_state[skey] = state
        st.session_state[fkey] = fingerprint
        st.session_state[f"{fkey}_struct"] = struct_fp
    else:
        state = st.session_state[skey]

    # --- 3. Render ---
    layout = LayeredLayout(direction="right") if use_auto_layout else ManualLayout()

    result_state = streamlit_flow(
        f"pawflow_runtime_viz{key_suffix}",
        state=state,
        fit_view=True,
        height=height,
        get_node_on_click=True,
        get_edge_on_click=False,
        hide_watermark=True,
        allow_new_edges=False,
        layout=layout,
        pan_on_drag=True,
        allow_zoom=True,
    )

    # --- 4. Capture result ---
    if instance_id:
        _capture_react_result(result_state, state, instance_id, key_suffix)

    if result_state and result_state.selected_id:
        return result_state.selected_id
    return None


def render_runtime_flow(
    executor: ContinuousFlowExecutor,
    height: int = 500,
    use_auto_layout: bool = False,
    frozen: bool = False,
    instance_id: Optional[str] = None,
) -> Optional[str]:
    """Render a live, read-only view of the running flow.

    Unlike render_runtime_flow_static, the live view ALWAYS rebuilds nodes/edges
    on each call (every fragment refresh ≈ 3s) to guarantee the component
    receives fresh args.  The overhead is negligible for typical flow sizes.
    """
    task_states = executor.get_all_task_states()
    status = executor.get_status()
    queue_stats = status.get("queue_stats", [])
    if not instance_id:
        flow_obj = getattr(executor, '_flow', None)
        instance_id = getattr(flow_obj, 'id', None) if flow_obj else None

    task_ids = list(task_states.keys())
    if not task_ids:
        st.info(t("common.none"))
        return None

    # --- 1. Positions from disk ---
    positions, need_auto = _ensure_positions_loaded(instance_id) if instance_id else ({}, False)
    if need_auto:
        use_auto_layout = True
    positions = st.session_state.get(_pos_key(instance_id), positions)

    # --- 2. Always rebuild — React side diffs to avoid flicker ---
    view_suffix = "_live"
    skey = f"_rt_state{view_suffix}"

    hc = _compute_handle_counts(queue_stats)
    nodes = _build_nodes(task_states, positions, hc, frozen=frozen)
    edges = _build_edges(queue_stats, positions)
    state = StreamlitFlowState(nodes=nodes, edges=edges)
    st.session_state[skey] = state

    # --- 3. Render ---
    layout = LayeredLayout(direction="right") if use_auto_layout else ManualLayout()

    result_state = streamlit_flow(
        "pawflow_runtime_viz",
        state=state,
        fit_view=True,
        height=height,
        get_node_on_click=True,
        get_edge_on_click=False,
        hide_watermark=True,
        allow_new_edges=False,
        layout=layout,
        pan_on_drag=True,
        allow_zoom=True,
    )

    # --- 4. Capture result ---
    if instance_id:
        _capture_react_result(result_state, state, instance_id, view_suffix)

    if result_state and result_state.selected_id:
        return result_state.selected_id
    return None
