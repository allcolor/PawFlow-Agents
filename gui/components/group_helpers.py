"""Helpers for Process Group visualization on the canvas.

Handles position conversion (absolute ↔ relative), group bounds computation,
and edge remapping for collapsed groups.
"""

from typing import Dict, Any, List, Optional, Set, Tuple

GROUP_HEADER_HEIGHT = 40
GROUP_PADDING = 20


def compute_group_bounds(
    member_positions: Dict[str, Tuple[float, float]],
    node_width: float = 150,
    node_height: float = 60,
) -> Dict[str, float]:
    """Compute bounding box for a group given its member positions.

    Returns: {"x": min_x, "y": min_y, "width": w, "height": h}
    """
    if not member_positions:
        return {"x": 0, "y": 0, "width": 250, "height": 160}

    xs = [p[0] for p in member_positions.values()]
    ys = [p[1] for p in member_positions.values()]

    min_x = min(xs) - GROUP_PADDING
    min_y = min(ys) - GROUP_PADDING - GROUP_HEADER_HEIGHT
    max_x = max(xs) + node_width + GROUP_PADDING
    max_y = max(ys) + node_height + GROUP_PADDING

    return {
        "x": min_x,
        "y": min_y,
        "width": max_x - min_x,
        "height": max_y - min_y,
    }


def absolute_to_relative(
    abs_pos: Tuple[float, float],
    group_pos: Tuple[float, float],
) -> Tuple[float, float]:
    """Convert absolute canvas position to relative-to-group position."""
    return (
        abs_pos[0] - group_pos[0],
        abs_pos[1] - group_pos[1] - GROUP_HEADER_HEIGHT,
    )


def relative_to_absolute(
    rel_pos: Tuple[float, float],
    group_pos: Tuple[float, float],
) -> Tuple[float, float]:
    """Convert relative-to-group position to absolute canvas position."""
    return (
        rel_pos[0] + group_pos[0],
        rel_pos[1] + group_pos[1] + GROUP_HEADER_HEIGHT,
    )


def remap_edges_for_collapse(
    edges: List[Dict[str, Any]],
    group_id: str,
    member_ids: Set[str],
    summary_node_id: str,
) -> List[Dict[str, Any]]:
    """Remap edges when a group is collapsed.

    - Internal edges (both source and target in group) → removed
    - External→member edges → redirected to summary_node_id
    - Member→external edges → redirected from summary_node_id
    - Deduplicates: if multiple edges map to the same (src, tgt), keep one
      with label showing count.
    """
    new_edges = []
    seen_pairs: Dict[Tuple[str, str], int] = {}

    for edge in edges:
        src = edge.get("source", edge.get("from", ""))
        tgt = edge.get("target", edge.get("to", ""))
        src_in = src in member_ids
        tgt_in = tgt in member_ids

        # Internal edge — skip
        if src_in and tgt_in:
            continue

        # Remap
        new_src = summary_node_id if src_in else src
        new_tgt = summary_node_id if tgt_in else tgt
        pair = (new_src, new_tgt)

        if pair in seen_pairs:
            seen_pairs[pair] += 1
            continue

        seen_pairs[pair] = 1
        new_edge = dict(edge)
        new_edge["source"] = new_src
        new_edge["target"] = new_tgt
        new_edge["id"] = f"collapsed_{group_id}_{new_src}_{new_tgt}"
        new_edges.append(new_edge)

    # Update labels for deduplicated edges
    for edge in new_edges:
        pair = (edge["source"], edge["target"])
        count = seen_pairs.get(pair, 1)
        if count > 1:
            edge["label"] = f"{count} connections"

    return new_edges


def collapse_nodes_edges(
    nodes: list,
    edges: list,
    group_id: str,
    group_name: str,
    member_ids: Set[str],
    group_center: Tuple[float, float],
    group_color: str = "#4285f4",
    input_ports: Optional[List[str]] = None,
    output_ports: Optional[List[str]] = None,
) -> Tuple[list, list]:
    """Replace a group's member nodes with a single summary node.

    Returns (new_nodes, new_edges).
    """
    from streamlit_flow.elements import StreamlitFlowNode

    summary_id = f"group_{group_id}_summary"

    # Filter out member nodes and the group container node
    new_nodes = [n for n in nodes if n.id not in member_ids and n.id != f"group_{group_id}"]

    # Create summary node
    summary = StreamlitFlowNode(
        id=summary_id,
        pos=group_center,
        data={
            "content": f"\U0001f4e6 {group_name}\n{len(member_ids)} tasks",
            "taskType": "processGroup",
            "collapsed": True,
            "inputPorts": input_ports or [],
            "outputPorts": output_ports or [],
        },
        node_type="default",
        source_position="right",
        target_position="left",
        draggable=True,
        selectable=True,
        connectable=True,
        style={
            "background": group_color,
            "color": "white",
            "border": f"3px dashed {group_color}",
            "borderRadius": "12px",
            "padding": "10px",
            "fontSize": "12px",
            "opacity": "0.9",
            "width": "160px",
        },
    )
    new_nodes.append(summary)

    # Remap edges
    # Convert StreamlitFlowEdge objects to dicts for remapping
    edge_dicts = []
    for e in edges:
        if hasattr(e, 'asdict'):
            edge_dicts.append(e.asdict())
        elif hasattr(e, 'source'):
            edge_dicts.append({
                "id": e.id, "source": e.source, "target": e.target,
                "label": getattr(e, 'label', ''),
                "style": getattr(e, 'style', {}),
                "animated": getattr(e, 'animated', False),
                "type": getattr(e, 'type', 'smoothstep'),
            })
        else:
            edge_dicts.append(e)

    new_edge_dicts = remap_edges_for_collapse(
        edge_dicts, group_id, member_ids, summary_id
    )

    # Convert back to StreamlitFlowEdge
    from streamlit_flow.elements import StreamlitFlowEdge
    new_edges = []
    for ed in new_edge_dicts:
        new_edges.append(StreamlitFlowEdge(
            id=ed["id"],
            source=ed["source"],
            target=ed["target"],
            animated=ed.get("animated", False),
            edge_type=ed.get("type", "smoothstep"),
            style=ed.get("style", {}),
            label=ed.get("label", ""),
        ))

    return new_nodes, new_edges
