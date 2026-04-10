"""Editor-specific admin API actions.

Handles task type listing, schema fetching, flow save/load/validate,
and auto-layout computation. Imported by admin_actions.py.
"""

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List

from core import TaskFactory, ServiceFactory

logger = logging.getLogger(__name__)


def _admin_list_task_types(body, exec_reg, deploy_reg, gsvc_reg, tmpl_svc):
    """List all registered task types with category info."""
    from core.task_categories import TASK_CATEGORIES
    result = []
    for task_type in TaskFactory.list_types():
        cls = TaskFactory.get(task_type)
        cat = TASK_CATEGORIES.get(task_type, "Plugins")
        result.append({
            "type": cls.TYPE,
            "name": cls.NAME,
            "description": cls.DESCRIPTION,
            "icon": cls.ICON,
            "category": cat,
        })
    return result


def _admin_get_task_schema(body, exec_reg, deploy_reg, gsvc_reg, tmpl_svc):
    """Get parameter schema for a task type."""
    task_type = body.get("task_type", "")
    cls = TaskFactory.get(task_type)
    if not cls:
        return {"error": f"Unknown task type: {task_type}"}
    try:
        instance = cls.__new__(cls)
        instance.config = {}
        schema = instance.get_parameter_schema()
    except Exception:
        try:
            instance = cls({})
            schema = instance.get_parameter_schema()
        except Exception:
            schema = {}
    return {"type": task_type, "schema": schema}


def _admin_list_service_types(body, exec_reg, deploy_reg, gsvc_reg, tmpl_svc):
    """List all registered service types."""
    result = []
    for svc_type in ServiceFactory.list_types():
        cls = ServiceFactory.get(svc_type)
        result.append({
            "type": getattr(cls, 'TYPE', svc_type),
            "name": getattr(cls, 'NAME', svc_type),
            "description": getattr(cls, 'DESCRIPTION', ''),
        })
    return result


def _admin_get_service_schema(body, exec_reg, deploy_reg, gsvc_reg, tmpl_svc):
    """Get parameter schema for a service type."""
    svc_type = body.get("service_type", "")
    cls = ServiceFactory.get(svc_type)
    if not cls:
        return {"error": f"Unknown service type: {svc_type}"}
    schema = {}
    if hasattr(cls, 'get_parameter_schema'):
        try:
            instance = cls({})
            schema = instance.get_parameter_schema()
        except Exception:
            pass
    return {"type": svc_type, "schema": schema}


def _admin_save_flow_json(body, exec_reg, deploy_reg, gsvc_reg, tmpl_svc):
    """Save a flow definition to disk."""
    flow_data = body.get("flow", {})
    if not flow_data or not flow_data.get("tasks"):
        return {"error": "Invalid flow data (no tasks)"}

    flow_id = flow_data.get("id", "")
    if not flow_id:
        flow_id = "flow-" + str(int(time.time()))
        flow_data["id"] = flow_id

    flows_dir = Path("flows")
    flows_dir.mkdir(exist_ok=True)
    flow_path = flows_dir / f"{flow_id}.json"

    # Archive old version if exists
    if flow_path.exists():
        try:
            old = json.loads(flow_path.read_text(encoding="utf-8"))
            old_version = old.get("version", "0.0.0")
            versions_dir = flows_dir / "versions" / flow_id
            versions_dir.mkdir(parents=True, exist_ok=True)
            archive_path = versions_dir / f"v{old_version}.json"
            archive_path.write_text(
                json.dumps(old, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

    flow_path.write_text(
        json.dumps(flow_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"status": "saved", "flow_id": flow_id, "path": str(flow_path)}


def _admin_validate_flow(body, exec_reg, deploy_reg, gsvc_reg, tmpl_svc):
    """Validate a flow definition."""
    flow_data = body.get("flow", {})
    if not flow_data:
        return {"errors": ["Empty flow data"], "warnings": []}

    errors = []
    warnings = []

    tasks = flow_data.get("tasks", {})
    relations = flow_data.get("relations", [])

    if not tasks:
        errors.append("Flow has no tasks")

    # Check relations reference valid tasks
    task_ids = set(tasks.keys())
    for r in relations:
        if r.get("from") not in task_ids:
            errors.append(f"Relation source '{r.get('from')}' not found in tasks")
        if r.get("to") not in task_ids:
            errors.append(f"Relation target '{r.get('to')}' not found in tasks")

    # Check entries/exits
    entries = flow_data.get("entries", [])
    exits_ = flow_data.get("exits", [])
    for e in entries:
        if e not in task_ids:
            errors.append(f"Entry task '{e}' not found")
    for e in exits_:
        if e not in task_ids:
            errors.append(f"Exit task '{e}' not found")

    # Check for orphan tasks (no connections)
    connected = set()
    for r in relations:
        connected.add(r.get("from"))
        connected.add(r.get("to"))
    for tid in task_ids:
        if tid not in connected and len(task_ids) > 1:
            warnings.append(f"Task '{tid}' has no connections")

    # Check task types exist
    known_types = set(TaskFactory.list_types())
    for tid, t in tasks.items():
        if t.get("type") and t["type"] not in known_types:
            warnings.append(f"Task '{tid}' has unknown type '{t['type']}'")

    return {"errors": errors, "warnings": warnings}


def _admin_auto_layout(body, exec_reg, deploy_reg, gsvc_reg, tmpl_svc):
    """Compute auto-layout positions for flow tasks."""
    flow_data = body.get("flow", {})
    tasks = flow_data.get("tasks", {})
    relations = flow_data.get("relations", [])
    if not tasks:
        return {"positions": {}}

    ids = list(tasks.keys())
    incoming = {tid: [] for tid in ids}
    for r in relations:
        if r.get("to") in incoming:
            incoming[r["to"]].append(r.get("from", ""))

    layers = {}
    visited = set()

    def assign_layer(tid):
        if tid in visited:
            return layers.get(tid, 0)
        visited.add(tid)
        max_parent = -1
        for pid in incoming.get(tid, []):
            if pid in tasks:
                max_parent = max(max_parent, assign_layer(pid))
        layers[tid] = max_parent + 1
        return layers[tid]

    for tid in ids:
        assign_layer(tid)

    by_layer = {}
    for tid in ids:
        l = layers.get(tid, 0)
        by_layer.setdefault(l, []).append(tid)

    positions = {}
    for l in sorted(by_layer.keys()):
        nodes = by_layer[l]
        for i, tid in enumerate(nodes):
            positions[tid] = {"x": 80 + l * 220, "y": 60 + i * 80}

    return {"positions": positions}
