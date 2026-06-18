"""AgentLoopTask files-fs action: flow-graph + flow-definition loader helpers.

Split out of files_fs.py for the <=800-line rule; imported by the _handle_files_fs
dispatcher.
"""

import json
import logging
from typing import Dict, Any, List


logger = logging.getLogger(__name__)


def _load_deployed_flow_definition(inst) -> Dict[str, Any]:
    """Load a deployed flow from repository FQN, then legacy file path."""
    if getattr(inst, "flow_fqn", ""):
        from core.repository import ScopedRepository
        repo = ScopedRepository.instance()
        scopes = []
        flow_scope = str(getattr(inst, "flow_scope", "") or "")
        if flow_scope:
            scopes.append(flow_scope)
        if getattr(inst, "conversation_id", ""):
            scopes.append("conversation")
        if getattr(inst, "owner", ""):
            scopes.append("user")
        scopes.append("global")
        seen = set()
        for scope in scopes:
            repo_scope = "conv" if scope == "conversation" else scope
            if repo_scope in seen:
                continue
            seen.add(repo_scope)
            raw = repo.get_flow(
                inst.flow_fqn, repo_scope,
                user_id=getattr(inst, "owner", "") or "",
                conv_id=getattr(inst, "conversation_id", "") or "",
            )
            if raw is not None:
                return raw
    flow_path = getattr(inst, "flow_path", "") or ""
    if flow_path:
        with open(flow_path, encoding="utf-8") as handle:
            return json.loads(handle.read())
    raise FileNotFoundError(
        f"Flow not found: fqn={getattr(inst, 'flow_fqn', '') or '-'} path={flow_path or '-'}")


def _with_deployed_parameters(raw: Dict[str, Any], inst) -> Dict[str, Any]:
    """Return flow definition with deployment parameter overrides applied."""
    parameters = dict(raw.get("parameters") or {})
    parameters.update(getattr(inst, "parameters", None) or {})
    if parameters == (raw.get("parameters") or {}):
        return raw
    merged = dict(raw)
    merged["parameters"] = parameters
    return merged


def _executor_flow_metadata(executor) -> Dict[str, Any]:
    """Return graph-only metadata preserved on a live Flow object."""
    flow = getattr(executor, "_flow", None)
    if flow is None:
        return {}
    return {
        "parameters": getattr(flow, "parameters", {}) or {},
        "ports": getattr(flow, "ports", {}) or {},
        "runtime_links": getattr(flow, "runtime_links", []) or [],
    }


def _merge_graph_metadata(raw: Dict[str, Any], fallback: Dict[str, Any]) -> Dict[str, Any]:
    if not fallback:
        return raw
    if not raw:
        return fallback
    merged = dict(raw)
    parameters = dict(fallback.get("parameters") or {})
    parameters.update(raw.get("parameters") or {})
    if parameters:
        merged["parameters"] = parameters
    for key in ("ports", "runtime_links"):
        if not merged.get(key) and fallback.get(key):
            merged[key] = fallback[key]
    return merged


def _static_flow_graph(raw: Dict[str, Any]):
    nodes = {}
    edges = []
    for tid, tdef in (raw.get("tasks") or {}).items():
        nodes[tid] = {
            "type": tdef.get("type", "?"),
            "state": "stopped",
            "in": 0,
            "out": 0,
            "error_count": 0,
            "error": "",
            "in_flight": False,
        }
    for gid, gdef in (raw.get("groups") or {}).items():
        if not isinstance(gdef, dict):
            continue
        flow_ref = gdef.get("flow_ref") or {}
        nodes[gid] = {
            "type": "subflow" if flow_ref else "processGroup",
            "state": "stopped",
            "in": 0,
            "out": 0,
            "error_count": 0,
            "error": "",
            "in_flight": False,
            "subflow_ref": flow_ref,
            "group_name": gdef.get("name", gid),
        }
    for rel in raw.get("relations", []) or []:
        source = rel.get("from") or rel.get("source")
        target = rel.get("to") or rel.get("target")
        if not source or not target:
            continue
        edges.append({
            "source": source,
            "target": target,
            "relationship": rel.get("type", "success"),
            "queue_size": 0,
            "max_queue": 10000,
            "backpressured": False,
        })
    _add_declared_ports_to_graph(raw, nodes, edges)
    _add_runtime_links_to_graph(raw, nodes, edges)
    return nodes, edges


def _add_declared_ports_to_graph(raw: Dict[str, Any], nodes: Dict[str, Any],
                                 edges: List[Dict[str, Any]]) -> None:
    for port_id, port in (raw.get("ports") or {}).items():
        if not isinstance(port, dict):
            continue
        direction = str(port.get("direction") or "input")
        task_id = str(port.get("task") or "")
        node_id = f"port:{port_id}"
        nodes[node_id] = {
            "type": port.get("type") or "port",
            "state": "stopped",
            "in": 0,
            "out": 0,
            "error_count": 0,
            "error": "",
            "in_flight": False,
            "runtime_port": True,
            "port_direction": direction,
            "runtime_target": port_id,
            "group_name": port_id,
            "description": port.get("description", ""),
        }
        if task_id not in nodes:
            continue
        source, target = (node_id, task_id) if direction == "input" else (task_id, node_id)
        edges.append({
            "source": source,
            "target": target,
            "relationship": port.get("type") or direction,
            "queue_size": 0,
            "max_queue": 10000,
            "backpressured": False,
            "runtime_port": True,
        })


def _add_runtime_links_to_graph(raw: Dict[str, Any], nodes: Dict[str, Any],
                                edges: List[Dict[str, Any]]) -> None:
    parameters = raw.get("parameters") or {}
    for link in raw.get("runtime_links", []) or []:
        if not isinstance(link, dict):
            continue
        source = link.get("from") or link.get("source")
        target = _resolve_template_parameter(link.get("to") or link.get("target"), parameters)
        if not source or not target or source not in nodes:
            continue
        node_id = f"runtime:{target}"
        nodes[node_id] = {
            "type": link.get("type") or "runtimePort",
            "state": "stopped",
            "in": 0,
            "out": 0,
            "error_count": 0,
            "error": "",
            "in_flight": False,
            "runtime_link": True,
            "runtime_target": target,
            "group_name": target,
            "description": link.get("description", ""),
        }
        edges.append({
            "source": source,
            "target": node_id,
            "relationship": link.get("type") or "runtime",
            "queue_size": 0,
            "max_queue": 10000,
            "backpressured": False,
            "runtime_link": True,
        })


def _resolve_template_parameter(value: Any, parameters: Dict[str, Any]) -> str:
    text = str(value or "")
    if text.startswith("${") and text.endswith("}") and text.count("${") == 1:
        key = text[2:-1]
        resolved = parameters.get(key)
        if isinstance(resolved, dict):
            resolved = resolved.get("default", "")
        if resolved not in (None, ""):
            return str(resolved)
    return text


def _load_flow_ref_definition(flow_ref: Dict[str, Any]) -> Dict[str, Any]:
    """Load a graph sub-flow from a ProcessGroup flow_ref."""
    from pathlib import Path as _P

    ref_path = str((flow_ref or {}).get("path") or "")
    if not ref_path:
        raise FileNotFoundError("Missing subflow flow_ref.path")
    path = _P(ref_path)
    if not path.is_absolute():
        path = _P.cwd() / path
    root = _P.cwd().resolve()
    resolved = path.resolve()
    if root not in resolved.parents and resolved != root:
        raise PermissionError(f"Subflow path is outside the workspace: {ref_path}")
    if resolved.suffix != ".json":
        raise ValueError(f"Subflow path must point to a JSON flow: {ref_path}")
    raw = json.loads(resolved.read_text(encoding="utf-8"))
    expected_version = str((flow_ref or {}).get("version") or "")
    if expected_version and str(raw.get("version") or "") != expected_version:
        raise ValueError(
            f"Subflow version mismatch for {ref_path}: expected "
            f"{expected_version}, got {raw.get('version') or '(none)'}")
    return raw


def _load_flow_template_definition(template_id: str, user_id: str,
                                   conversation_id: str = "") -> Dict[str, Any]:
    """Load a flow template from the versioned repository or legacy path."""
    from pathlib import Path as _P
    from tasks.ai.actions.service_flow import _resolve_flow_template_path

    tpath = _resolve_flow_template_path(template_id, user_id, conversation_id)
    if tpath:
        return json.loads(_P(tpath).read_text(encoding="utf-8"))

    # Repository-backed flows can be addressed by FQN, by package/name without
    # a version, or by the raw id/name embedded in the flow definition.
    from core.repository import ScopedRepository
    repo = ScopedRepository.instance()
    scope_candidates = []
    if user_id and conversation_id:
        scope_candidates.append(("conv", user_id, conversation_id))
    if user_id:
        scope_candidates.append(("user", user_id, ""))
    scope_candidates.append(("global", "", ""))
    id_candidates = [template_id]
    if ":" not in template_id and "." not in template_id:
        id_candidates.append(f"default.{template_id}")

    for scope, uid, cid in scope_candidates:
        for flow_id in id_candidates:
            try:
                raw = repo.get_flow(flow_id, scope, user_id=uid, conv_id=cid)
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                raw = None
            if raw is not None:
                return raw

    raise FileNotFoundError(f"Flow template not found: {template_id}")
