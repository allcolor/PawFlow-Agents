# Flow Canvas Component

"""
Canvas visuel interactif pour l'édition de flux.
Utilise streamlit-flow-component v1.6+ (React Flow wrapper, forked).

Key insight: streamlit_flow uses the state's timestamp to decide whether
to reset the component. We must persist the returned state and only
create a new state (new timestamp) when the flow structure changes.

Drag & drop: The task palette is rendered INSIDE the React component
(DragPalette) to avoid cross-iframe limitations. Tasks are dragged
from the palette onto the canvas, just like Apache NiFi.
"""

import streamlit as st
from streamlit_flow import streamlit_flow
from streamlit_flow.state import StreamlitFlowState
from streamlit_flow.elements import StreamlitFlowNode, StreamlitFlowEdge
from streamlit_flow.layouts import (
    ManualLayout, LayeredLayout, TreeLayout, ForceLayout,
    HierarchicalLayeredLayout, CompactLayout, PipelineLayout,
)
from typing import Dict, Any, List, Optional, Tuple, Set
from gui.components.color_scheme import (
    get_task_color, get_task_category, get_category_base_color,
    CATEGORY_PALETTES, TASK_CATEGORIES, CATEGORY_ICONS,
)
from gui.components.group_helpers import (
    compute_group_bounds, absolute_to_relative,
    collapse_nodes_edges, GROUP_HEADER_HEIGHT,
)


# Task categories for the in-canvas drag palette
TASK_PALETTE_CATEGORIES = {
    "System": {
        "tasks": ["log", "updateAttribute", "replace_text", "wait", "fail",
                  "generateFlowFile", "hashContent", "listFiles", "executeScript"],
    },
    "IO": {
        "tasks": ["getFile", "putFile", "fetchHTTP", "listenHTTP",
                  "getSFTP", "putSFTP", "listSFTP", "getFTP", "putFTP",
                  "httpReceiver", "handleHTTPResponse", "validateHTTPAuth"],
    },
    "Cloud": {
        "tasks": ["putS3", "getS3", "putGCS", "getGCS", "putAzureBlob", "getAzureBlob"],
    },
    "Data": {
        "tasks": ["transformJSON", "evaluateJSONPath", "extractText",
                  "compressContent", "validateJSON", "convertCharset",
                  "filterContent", "base64Encode", "countText",
                  "convertCSVToJSON", "convertJSONToCSV",
                  "executeSQL", "putSQL", "putCache", "getCache",
                  "fetchDistributedMapCache", "putDistributedMapCache",
                  "detectDuplicate", "attributesToJSON", "splitJSON"],
    },
    "Control": {
        "tasks": ["routeOnAttribute", "splitContent", "mergeContent", "duplicateContent",
                  "funnel", "inputPort", "outputPort", "controlRate"],
    },
    "Messaging": {
        "tasks": ["publishKafka", "consumeKafka", "publishMQTT", "consumeMQTT",
                  "sendEmail", "notifySlack"],
    },
    "Sync": {
        "tasks": ["waitForSignal", "notify"],
    },
    "Monitoring": {
        "tasks": ["reporting"],
    },
    "AI": {
        "tasks": ["inferLLM", "agentLoop"],
    },
}


def _build_task_types_for_palette(available_types: set) -> List[Dict[str, str]]:
    """Build the task types list for the React DragPalette component."""
    result = []
    known = set()
    for cat_name, cat_info in TASK_PALETTE_CATEGORIES.items():
        for task_type in cat_info["tasks"]:
            if task_type in available_types:
                result.append({
                    "type": task_type,
                    "color": get_task_color(task_type, cat_name),
                    "category": cat_name,
                })
                known.add(task_type)

    # Plugin/unknown tasks
    for task_type in sorted(available_types - known):
        result.append({
            "type": task_type,
            "color": get_task_color(task_type),
            "category": "Plugins",
        })

    return result


DEFAULT_X_SPACING = 250
DEFAULT_Y_START = 100


def _flow_fingerprint(flow_dict: Dict[str, Any]) -> str:
    """Generate a fingerprint of the flow structure to detect changes."""
    tasks = sorted(flow_dict.get('tasks', {}).keys())
    rels = [(r['from'], r['to']) for r in flow_dict.get('relations', [])]
    annots = sorted(flow_dict.get('annotations', {}).items())
    # Include group state (collapsed, members) in fingerprint
    collapsed = sorted(st.session_state.get("collapsed_groups", {}).items())
    groups = sorted(
        (k, str(_get_group_member_ids(v)), _get_group_color(v))
        for k, v in flow_dict.get('groups', {}).items()
    )
    return f"{tasks}|{rels}|{annots}|{groups}|{collapsed}"


def _get_group_member_ids(group_data) -> List[str]:
    """Extract member task IDs from a group (ProcessGroup or legacy dict)."""
    if hasattr(group_data, 'get_member_task_ids'):
        return group_data.get_member_task_ids()
    # Dict-based group
    tasks = group_data.get("tasks", {})
    if isinstance(tasks, list):
        return tasks
    return list(tasks.keys())


def _get_group_color(group_data) -> str:
    """Get color from a group (ProcessGroup or dict)."""
    if hasattr(group_data, 'color'):
        return group_data.color
    return group_data.get("color", "#4285f4")


def _is_group_collapsed(group_id: str) -> bool:
    """Check if a group is collapsed."""
    return st.session_state.get("collapsed_groups", {}).get(group_id, False)


class FlowCanvas:
    """Composant canvas pour l'édition visuelle de flux."""

    def __init__(self):
        if 'node_positions' not in st.session_state:
            st.session_state.node_positions = {}
        if 'collapsed_groups' not in st.session_state:
            st.session_state.collapsed_groups = {}

    def render(self, flow_dict: Dict[str, Any], height: int = 600,
               enable_drag_palette: bool = False,
               available_task_types: Optional[set] = None) -> Optional[str]:
        """
        Rendre le canvas interactif.
        Persists state between renders so dragging and connections work.

        Args:
            flow_dict: Flow structure dict
            height: Canvas height in pixels
            enable_drag_palette: Show the in-canvas drag palette (NiFi-style)
            available_task_types: Set of available task type names for the palette

        Returns:
            ID de l'élément sélectionné (node ou edge) ou None
        """
        fingerprint = _flow_fingerprint(flow_dict)

        # Only rebuild state if flow structure changed
        if (st.session_state.get('_canvas_fingerprint') != fingerprint
                or '_canvas_state' not in st.session_state):
            handle_counts = self._compute_handle_counts(flow_dict)
            nodes = self._build_nodes(flow_dict, handle_counts)
            edges = self._build_edges(flow_dict)
            state = StreamlitFlowState(nodes=nodes, edges=edges)
            st.session_state._canvas_state = state
            st.session_state._canvas_fingerprint = fingerprint
        else:
            state = st.session_state._canvas_state

        show_minimap = st.session_state.get("canvas_show_minimap", False)
        show_controls = st.session_state.get("canvas_show_controls", True)

        layout_name = st.session_state.get("canvas_layout", "manual")
        has_groups = bool(flow_dict.get("groups"))
        layout_map = {
            "manual": ManualLayout(),
            "layered": LayeredLayout(direction="right"),
            "hierarchical": HierarchicalLayeredLayout(direction="right"),
            "compact": CompactLayout(direction="right"),
            "pipeline": PipelineLayout(direction="right"),
            "tree": TreeLayout(direction="right"),
            "force": ForceLayout(),
        }
        # Default to hierarchical if groups exist and manual is selected
        if layout_name == "manual" and has_groups:
            layout = layout_map.get("manual")
        else:
            layout = layout_map.get(layout_name, ManualLayout())

        # Build task types for the drag palette
        task_types = []
        if enable_drag_palette and available_task_types:
            task_types = _build_task_types_for_palette(available_task_types)

        result_state = streamlit_flow(
            "openpaw_canvas",
            state=state,
            fit_view=True,
            height=height,
            get_node_on_click=True,
            get_edge_on_click=True,
            hide_watermark=True,
            allow_new_edges=True,
            animate_new_edges=True,
            layout=layout,
            pan_on_drag=True,
            allow_zoom=True,
            show_minimap=show_minimap,
            show_controls=show_controls,
            enable_pane_menu=True,
            enable_node_menu=True,
            enable_edge_menu=True,
            enable_drag_palette=enable_drag_palette,
            task_types=task_types,
        )

        # Only update persisted state when user actually interacted
        if result_state:
            # Handle group collapse toggle
            if hasattr(result_state, 'toggle_group_collapse') and result_state.toggle_group_collapse:
                group_id = result_state.toggle_group_collapse
                collapsed = st.session_state.collapsed_groups
                collapsed[group_id] = not collapsed.get(group_id, False)
                st.session_state.collapsed_groups = collapsed
                # Force rebuild
                st.session_state.pop('_canvas_state', None)
                st.session_state.pop('_canvas_fingerprint', None)
                st.rerun()

            # Handle drag-and-drop: new node was dropped on canvas
            if hasattr(result_state, 'new_node_request') and result_state.new_node_request:
                req = result_state.new_node_request
                task_type = req.get('nodeType', 'log')
                pos = req.get('position', {'x': 100, 'y': 100})
                # Generate unique task ID
                base_id = task_type
                existing = set(flow_dict.get('tasks', {}).keys())
                task_id = base_id
                counter = 1
                while task_id in existing:
                    task_id = f"{base_id}_{counter}"
                    counter += 1
                # Add to flow
                self.add_task(flow_dict, task_id, task_type,
                              position=(pos.get('x', 100), pos.get('y', 100)))
                st.rerun()

            # Sync positions back from the component
            if result_state.nodes:
                for node in result_state.nodes:
                    if hasattr(node, 'position') and node.position:
                        pos = node.position
                        st.session_state.node_positions[node.id] = (
                            pos.get('x', 0), pos.get('y', 0)
                        )

            # Detect new edges added by user dragging connectors
            if result_state.edges:
                current_pairs = {(r['from'], r['to']) for r in flow_dict.get('relations', [])}
                new_edge_added = False
                for edge in result_state.edges:
                    pair = (edge.source, edge.target)
                    if pair not in current_pairs:
                        flow_dict.setdefault('relations', []).append({
                            'from': edge.source,
                            'to': edge.target,
                            'type': 'success',
                        })
                        new_edge_added = True
                if new_edge_added:
                    st.session_state._canvas_fingerprint = _flow_fingerprint(flow_dict)
                    st.session_state._canvas_state = result_state

            # Return selected element ID
            if result_state.selected_id:
                return result_state.selected_id

        return None

    @staticmethod
    def _compute_handle_counts(flow_dict: Dict[str, Any]) -> Dict[str, Dict[str, int]]:
        """Count incoming/outgoing connections per node for dynamic handles."""
        source_counts: Dict[str, int] = {}
        target_counts: Dict[str, int] = {}
        for rel in flow_dict.get("relations", []):
            src = rel["from"]
            tgt = rel["to"]
            source_counts[src] = source_counts.get(src, 0) + 1
            target_counts[tgt] = target_counts.get(tgt, 0) + 1
        all_ids = set(list(source_counts.keys()) + list(target_counts.keys()))
        return {
            nid: {
                "source": source_counts.get(nid, 0),
                "target": target_counts.get(nid, 0),
            }
            for nid in all_ids
        }

    def _build_nodes(self, flow_dict: Dict[str, Any],
                     handle_counts: Optional[Dict[str, Dict[str, int]]] = None) -> List[StreamlitFlowNode]:
        """Convertir les tasks du flow en nodes pour le canvas.

        Handles Process Groups:
        - Expanded group → GroupNode container + child nodes with parentNode
        - Collapsed group → single summary node
        """
        nodes = []
        tasks = flow_dict.get('tasks', {})
        groups = flow_dict.get('groups', {})
        validation_errors = st.session_state.get("_validation_errors", {})

        # Build group membership map: task_id → group_id
        task_to_group: Dict[str, str] = {}
        for gid, gdata in groups.items():
            for tid in _get_group_member_ids(gdata):
                task_to_group[tid] = gid

        # Track which tasks are in collapsed groups (skip them individually)
        collapsed_task_ids: Set[str] = set()

        # 1. Build group container nodes (or summary nodes for collapsed)
        for gid, gdata in groups.items():
            member_ids = _get_group_member_ids(gdata)
            color = _get_group_color(gdata)
            group_name = gdata.name if hasattr(gdata, 'name') else gdata.get("name", gid)
            is_subflow = gdata.is_subflow if hasattr(gdata, 'is_subflow') else bool(gdata.get("flow_ref"))
            flow_ref = gdata.flow_ref if hasattr(gdata, 'flow_ref') else gdata.get("flow_ref")
            version = (flow_ref or {}).get("version", "") if flow_ref else ""
            input_ports = gdata.input_ports if hasattr(gdata, 'input_ports') else gdata.get("input_ports", [])
            output_ports = gdata.output_ports if hasattr(gdata, 'output_ports') else gdata.get("output_ports", [])

            if _is_group_collapsed(gid):
                # Collapsed: mark members for skip, summary node added later
                collapsed_task_ids.update(member_ids)
                # Compute center position
                member_positions = {}
                for tid in member_ids:
                    if tid in st.session_state.node_positions:
                        member_positions[tid] = st.session_state.node_positions[tid]
                if member_positions:
                    avg_x = sum(p[0] for p in member_positions.values()) / len(member_positions)
                    avg_y = sum(p[1] for p in member_positions.values()) / len(member_positions)
                    center = (avg_x, avg_y)
                else:
                    center = (100, 100)

                summary_id = f"group_{gid}_summary"
                nodes.append(StreamlitFlowNode(
                    id=summary_id,
                    pos=center,
                    data={
                        "content": f"\U0001f4e6 {group_name}\n{len(member_ids)} tasks",
                        "taskType": "processGroup",
                        "collapsed": True,
                        "inputPorts": input_ports,
                        "outputPorts": output_ports,
                    },
                    node_type="default",
                    source_position="right",
                    target_position="left",
                    draggable=True,
                    selectable=True,
                    connectable=True,
                    style={
                        "background": color,
                        "color": "white",
                        "border": f"3px dashed {color}",
                        "borderRadius": "12px",
                        "padding": "10px",
                        "fontSize": "12px",
                        "opacity": "0.9",
                        "width": "160px",
                    },
                ))
            else:
                # Expanded: create group container node
                member_positions = {}
                for idx, tid in enumerate(member_ids):
                    pos = st.session_state.node_positions.get(
                        tid, (100 + idx * DEFAULT_X_SPACING, DEFAULT_Y_START + (idx % 3) * 80)
                    )
                    member_positions[tid] = pos

                bounds = compute_group_bounds(member_positions)
                group_pos = (bounds["x"], bounds["y"])

                nodes.append(StreamlitFlowNode(
                    id=f"group_{gid}",
                    pos=group_pos,
                    data={
                        "label": group_name,
                        "content": group_name,
                        "color": color,
                        "collapsed": False,
                        "isSubflow": is_subflow,
                        "version": version,
                        "taskCount": len(member_ids),
                        "inputPorts": input_ports,
                        "outputPorts": output_ports,
                    },
                    node_type="group",
                    source_position="right",
                    target_position="left",
                    draggable=True,
                    selectable=True,
                    connectable=True,
                    style={
                        "width": f"{int(bounds['width'])}px",
                        "height": f"{int(bounds['height'])}px",
                    },
                ))

        # 2. Build task nodes
        for i, (task_id, task_config) in enumerate(tasks.items()):
            # Skip tasks in collapsed groups
            if task_id in collapsed_task_ids:
                continue

            task_type = task_config.get('type', 'unknown')
            category = get_task_category(task_type)

            pos = st.session_state.node_positions.get(
                task_id,
                (100 + i * DEFAULT_X_SPACING, DEFAULT_Y_START + (i % 3) * 80)
            )
            st.session_state.node_positions[task_id] = pos

            color = get_task_color(task_type, category)

            # Check group membership
            group_id = task_to_group.get(task_id)
            group_color = None
            group_name = None
            if group_id and not _is_group_collapsed(group_id):
                gdata = groups[group_id]
                group_color = _get_group_color(gdata)
                group_name = gdata.name if hasattr(gdata, 'name') else gdata.get("name", group_id)

            node_type = "default"
            annotation = flow_dict.get("annotations", {}).get(task_id, "")
            label = f"{task_type}\n{task_id}"
            if group_name:
                label += f"\n\U0001f3f7\ufe0f {group_name}"
            if annotation:
                label += f"\n\U0001f4ac {annotation[:30]}"

            border_color = group_color or color
            border_width = "3px" if group_color else "2px"

            # Build node data with structured info for semantic zoom + badges
            hc = handle_counts.get(task_id, {}) if handle_counts else {}
            node_data = {
                "content": label,
                "taskType": task_type,
                "taskId": task_id,
                "category": category,
                "icon": CATEGORY_ICONS.get(category, ""),
                "parameters": task_config.get("parameters", {}),
                "handleCounts": {
                    "source": max(1, hc.get("source", 1)),
                    "target": max(1, hc.get("target", 1)),
                },
            }
            task_errors = validation_errors.get(task_id, [])
            if task_errors:
                node_data["warningCount"] = len(task_errors)
                node_data["warningMessages"] = [str(e)[:80] for e in task_errors[:5]]

            # Build kwargs for parentNode if task is in an expanded group
            extra_kwargs = {}
            if group_id and not _is_group_collapsed(group_id):
                extra_kwargs["parentNode"] = f"group_{group_id}"
                extra_kwargs["extent"] = "parent"
                # Convert to relative position within group
                group_node_id = f"group_{group_id}"
                group_node_pos = None
                for n in nodes:
                    if n.id == group_node_id:
                        group_node_pos = (n.position["x"], n.position["y"])
                        break
                if group_node_pos:
                    pos = absolute_to_relative(pos, group_node_pos)

            nodes.append(StreamlitFlowNode(
                id=task_id,
                pos=pos,
                data=node_data,
                node_type=node_type,
                source_position="right",
                target_position="left",
                draggable=True,
                selectable=True,
                connectable=True,
                deletable=True,
                style={
                    "background": color,
                    "color": "white",
                    "border": f"{border_width} solid {border_color}",
                    "borderRadius": "8px",
                    "padding": "10px",
                    "fontSize": "12px",
                    "width": "150px",
                },
                **extra_kwargs,
            ))

        return nodes

    def _build_edges(self, flow_dict: Dict[str, Any]) -> List[StreamlitFlowEdge]:
        """Convertir les relations du flow en edges pour le canvas.

        Handles collapsed groups: remaps edges to summary nodes.
        Assigns handle indices sorted by the y-position of the other endpoint
        to minimize edge crossings.
        """
        groups = flow_dict.get('groups', {})
        positions = getattr(st.session_state, 'node_positions', {})

        # Build collapsed group member sets and remap targets
        collapsed_remap: Dict[str, str] = {}  # task_id → summary_node_id
        for gid, gdata in groups.items():
            if _is_group_collapsed(gid):
                summary_id = f"group_{gid}_summary"
                for tid in _get_group_member_ids(gdata):
                    collapsed_remap[tid] = summary_id

        EDGE_COLORS = {
            'success': '#28a745',
            'failure': '#dc3545',
            'retry': '#ffc107',
            'original': '#6c757d',
            'matched': '#0d6efd',
            'unmatched': '#e83e8c',
        }

        # First pass: collect all valid relations after remap/dedup
        pair_count: Dict[tuple, int] = {}
        valid_relations = []

        for i, relation in enumerate(flow_dict.get('relations', [])):
            edge_type_label = relation.get('type', 'success')
            from_id = collapsed_remap.get(relation['from'], relation['from'])
            to_id = collapsed_remap.get(relation['to'], relation['to'])

            if from_id == to_id and from_id.startswith("group_"):
                continue

            pair = (from_id, to_id, edge_type_label)
            if pair in pair_count:
                pair_count[pair] += 1
                continue
            pair_count[pair] = 1

            valid_relations.append((i, from_id, to_id, edge_type_label))

        # Group by source/target for y-position-sorted handle assignment
        from collections import defaultdict
        outgoing: Dict[str, List[int]] = defaultdict(list)
        incoming: Dict[str, List[int]] = defaultdict(list)

        for idx, (i, from_id, to_id, _) in enumerate(valid_relations):
            outgoing[from_id].append(idx)
            incoming[to_id].append(idx)

        source_handle: Dict[int, int] = {}
        target_handle: Dict[int, int] = {}

        for node_id, indices in outgoing.items():
            sorted_idx = sorted(indices, key=lambda idx: positions.get(
                valid_relations[idx][2], (0, 0))[1])
            for h, vi in enumerate(sorted_idx):
                source_handle[vi] = h

        for node_id, indices in incoming.items():
            sorted_idx = sorted(indices, key=lambda idx: positions.get(
                valid_relations[idx][1], (0, 0))[1])
            for h, vi in enumerate(sorted_idx):
                target_handle[vi] = h

        # Build edge objects
        edges = []
        for idx, (i, from_id, to_id, edge_type_label) in enumerate(valid_relations):
            color = EDGE_COLORS.get(edge_type_label, '#ffc107')

            edges.append(StreamlitFlowEdge(
                id=f"e{i}_{from_id}_{to_id}_{edge_type_label}",
                source=from_id,
                target=to_id,
                animated=(edge_type_label == 'success'),
                edge_type="default",
                style={"stroke": color, "strokeWidth": 2},
                label=edge_type_label,
                sourceHandle=f"source-{source_handle.get(idx, 0)}",
                targetHandle=f"target-{target_handle.get(idx, 0)}",
            ))

        return edges

    def add_task(self, flow_dict: Dict[str, Any], task_id: str, task_type: str,
                 position: Optional[Tuple[int, int]] = None):
        """Ajouter une task au flow et au canvas."""
        if position is None:
            n = len(flow_dict.get('tasks', {}))
            position = (100 + n * DEFAULT_X_SPACING, DEFAULT_Y_START)

        flow_dict.setdefault('tasks', {})[task_id] = {
            'type': task_type,
            'parameters': {},
        }
        st.session_state.node_positions[task_id] = position
        # Force state rebuild on next render
        st.session_state.pop('_canvas_state', None)
        st.session_state.pop('_canvas_fingerprint', None)

    def remove_task(self, flow_dict: Dict[str, Any], task_id: str):
        """Supprimer une task du flow et du canvas."""
        flow_dict.get('tasks', {}).pop(task_id, None)
        st.session_state.node_positions.pop(task_id, None)
        flow_dict['relations'] = [
            r for r in flow_dict.get('relations', [])
            if r['from'] != task_id and r['to'] != task_id
        ]
        # Force state rebuild
        st.session_state.pop('_canvas_state', None)
        st.session_state.pop('_canvas_fingerprint', None)

    def add_connection(self, flow_dict: Dict[str, Any], from_id: str, to_id: str,
                       rel_type: str = 'success'):
        """Ajouter une connexion entre deux tasks."""
        flow_dict.setdefault('relations', []).append({
            'from': from_id,
            'to': to_id,
            'type': rel_type,
        })
        # Force state rebuild
        st.session_state.pop('_canvas_state', None)
        st.session_state.pop('_canvas_fingerprint', None)

    def remove_connection(self, flow_dict: Dict[str, Any], edge_id: str):
        """Supprimer une connexion par son edge_id."""
        parts = edge_id.split('_', 2)
        if len(parts) >= 3:
            from_id = parts[1]
            to_id = parts[2]
            flow_dict['relations'] = [
                r for r in flow_dict.get('relations', [])
                if not (r['from'] == from_id and r['to'] == to_id)
            ]
        # Force state rebuild
        st.session_state.pop('_canvas_state', None)
        st.session_state.pop('_canvas_fingerprint', None)
