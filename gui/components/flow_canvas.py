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
from streamlit_flow.layouts import ManualLayout, LayeredLayout, TreeLayout, ForceLayout
from typing import Dict, Any, List, Optional, Tuple


# Couleurs par catégorie de task
TASK_COLORS = {
    # System - gris
    'log': '#6c757d',
    'replace_text': '#6c757d',
    'wait': '#6c757d',
    'updateAttribute': '#6c757d',
    'generateFlowFile': '#6c757d',
    'hashContent': '#6c757d',
    'listFiles': '#6c757d',
    'executeScript': '#6c757d',
    'fail': '#dc3545',
    # IO - bleu
    'getFile': '#0d6efd',
    'putFile': '#0d6efd',
    'fetchHTTP': '#0d6efd',
    # Data - vert
    'transformJSON': '#198754',
    'evaluateJSONPath': '#198754',
    'extractText': '#198754',
    'compressContent': '#198754',
    'validateJSON': '#198754',
    'convertCharset': '#198754',
    'filterContent': '#198754',
    'base64Encode': '#198754',
    'countText': '#198754',
    'convertCSVToJSON': '#198754',
    'convertJSONToCSV': '#198754',
    'executeSQL': '#20c997',
    'putSQL': '#20c997',
    'putCache': '#e83e8c',
    'getCache': '#e83e8c',
    # Control - orange
    'routeOnAttribute': '#fd7e14',
    'splitContent': '#fd7e14',
    'mergeContent': '#fd7e14',
    'duplicateContent': '#fd7e14',
    'funnel': '#fd7e14',
    'executeFlow': '#9b59b6',
    'inputPort': '#17a2b8',
    'outputPort': '#17a2b8',
    # Sync - violet
    'waitForSignal': '#6f42c1',
    'notify': '#6f42c1',
    # Distributed cache - teal
    'fetchDistributedMapCache': '#20c997',
    'putDistributedMapCache': '#20c997',
    # Reporting - indigo
    'reporting': '#6610f2',
    # Dedup/Attributes - teal
    'detectDuplicate': '#17a2b8',
    'attributesToJSON': '#198754',
    'splitJSON': '#198754',
    # Control rate - orange
    'controlRate': '#fd7e14',
    # Listen HTTP - blue
    'listenHTTP': '#0d6efd',
}

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
                  "funnel", "executeFlow", "inputPort", "outputPort", "controlRate"],
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
        "tasks": ["inferLLM"],
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
                    "color": TASK_COLORS.get(task_type, "#adb5bd"),
                    "category": cat_name,
                })
                known.add(task_type)

    # Plugin/unknown tasks
    for task_type in sorted(available_types - known):
        result.append({
            "type": task_type,
            "color": TASK_COLORS.get(task_type, "#adb5bd"),
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
    groups = sorted((k, tuple(v.get('tasks', [])), v.get('color', ''))
                    for k, v in flow_dict.get('groups', {}).items())
    return f"{tasks}|{rels}|{annots}|{groups}"


class FlowCanvas:
    """Composant canvas pour l'édition visuelle de flux."""

    def __init__(self):
        if 'node_positions' not in st.session_state:
            st.session_state.node_positions = {}

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
            nodes = self._build_nodes(flow_dict)
            edges = self._build_edges(flow_dict)
            state = StreamlitFlowState(nodes=nodes, edges=edges)
            st.session_state._canvas_state = state
            st.session_state._canvas_fingerprint = fingerprint
        else:
            state = st.session_state._canvas_state

        show_minimap = st.session_state.get("canvas_show_minimap", False)
        show_controls = st.session_state.get("canvas_show_controls", True)

        layout_name = st.session_state.get("canvas_layout", "manual")
        layout_map = {
            "manual": ManualLayout(),
            "layered": LayeredLayout(direction="right"),
            "tree": TreeLayout(direction="right"),
            "force": ForceLayout(),
        }
        layout = layout_map.get(layout_name, ManualLayout())

        # Build task types for the drag palette
        task_types = []
        if enable_drag_palette and available_task_types:
            task_types = _build_task_types_for_palette(available_task_types)

        result_state = streamlit_flow(
            "pyfi2_canvas",
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
        # (selected, dragged, added edge). Avoid overwriting on every
        # render — that creates a new timestamp → infinite rerun loop.
        if result_state:
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

    def _build_nodes(self, flow_dict: Dict[str, Any]) -> List[StreamlitFlowNode]:
        """Convertir les tasks du flow en nodes pour le canvas."""
        nodes = []
        tasks = flow_dict.get('tasks', {})

        for i, (task_id, task_config) in enumerate(tasks.items()):
            task_type = task_config.get('type', 'unknown')

            pos = st.session_state.node_positions.get(
                task_id,
                (100 + i * DEFAULT_X_SPACING, DEFAULT_Y_START + (i % 3) * 80)
            )
            st.session_state.node_positions[task_id] = pos

            color = TASK_COLORS.get(task_type, '#adb5bd')

            # Check if task belongs to a group — use group color for border
            group_color = None
            group_name = None
            for gname, gdata in flow_dict.get("groups", {}).items():
                if task_id in gdata.get("tasks", []):
                    group_color = gdata.get("color", "#4285f4")
                    group_name = gname
                    break

            # All nodes are "default" so they have both source (right) and target (left) handles
            node_type = "default"
            annotation = flow_dict.get("annotations", {}).get(task_id, "")
            label = f"{task_type}\n{task_id}"
            if group_name:
                label += f"\n\U0001f3f7\ufe0f {group_name}"
            if annotation:
                label += f"\n\U0001f4ac {annotation[:30]}"

            border_color = group_color or color
            border_width = "3px" if group_color else "2px"

            nodes.append(StreamlitFlowNode(
                id=task_id,
                pos=pos,
                data={"content": label},
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
            ))

        return nodes

    def _build_edges(self, flow_dict: Dict[str, Any]) -> List[StreamlitFlowEdge]:
        """Convertir les relations du flow en edges pour le canvas."""
        edges = []

        # Track pairs to offset multiple edges between same nodes
        pair_count: Dict[tuple, int] = {}

        EDGE_COLORS = {
            'success': '#28a745',
            'failure': '#dc3545',
            'retry': '#ffc107',
            'original': '#6c757d',
            'matched': '#0d6efd',
            'unmatched': '#e83e8c',
        }

        for i, relation in enumerate(flow_dict.get('relations', [])):
            edge_type_label = relation.get('type', 'success')
            pair = (relation['from'], relation['to'])
            pair_count[pair] = pair_count.get(pair, 0) + 1

            color = EDGE_COLORS.get(edge_type_label, '#ffc107')

            edges.append(StreamlitFlowEdge(
                id=f"e{i}_{relation['from']}_{relation['to']}_{edge_type_label}",
                source=relation['from'],
                target=relation['to'],
                animated=(edge_type_label == 'success'),
                edge_type="smoothstep",
                style={"stroke": color, "strokeWidth": 2},
                label=edge_type_label,
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
