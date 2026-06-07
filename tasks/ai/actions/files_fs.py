"""AgentLoopTask actions — files fs"""

import json
import logging
import time
import threading
from typing import Dict, Any, List, Optional

from core import FlowFile
from core.llm_client import LLMMessage, LLMClient
from core.tool_registry import ToolRegistry

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


def _static_flow_graph(raw: Dict[str, Any]):
    nodes = {}
    edges = []
    parameters = raw.get("parameters") or {}
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


def _handle_files_fs(self, action, body, store, user_id, flowfile):
    """Handle files fs actions. Returns [flowfile] or None."""


    if action == "list_conv_files":
        conv_id = body.get("conversation_id", "")
        if not conv_id:
            flowfile.set_content(json.dumps({"files": []}).encode())
            return [flowfile]
        # Query the FileStore directly. The previous implementation scanned
        # message content for fs://filestore/<id> / /files/<id> URLs and
        # missed any file the agent stored without surfacing a URL
        # (uploaded attachments not yet sent, tool-internal files, etc.).
        # The FileStore is conv-scoped on disk, so this is the right
        # source of truth.
        from core.file_store import FileStore
        fstore = FileStore.instance()
        try:
            rows = fstore.list_files(
                user_id=user_id, conversation_id=conv_id,
                include_internal=False)
        except Exception:
            logger.exception("list_conv_files: FileStore.list_files failed")
            rows = []
        files = []
        for r in rows:
            fid = r.get("file_id", "")
            if not fid:
                continue
            files.append({
                "file_id": fid,
                "filename": r.get("filename", fid),
                "content_type": r.get("content_type", ""),
                "size": int(r.get("size", 0) or 0),
                "created_at": float(r.get("created_at", 0) or 0),
                "available": fstore.exists(fid),
            })
        # Newest first; the UI sorts again on the client side but
        # pre-sorting here keeps non-UI consumers consistent.
        files.sort(key=lambda f: f["created_at"], reverse=True)
        flowfile.set_content(json.dumps({"files": files}, ensure_ascii=False).encode())
        return [flowfile]

    if action == "delete_file":
        file_id = body.get("file_id", "")
        conv_id = body.get("conversation_id", "")
        if not file_id:
            flowfile.set_content(json.dumps({"error": "Missing file_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        from core.file_store import FileStore
        fstore = FileStore.instance()
        meta = fstore.get_metadata(file_id)
        if not meta or not fstore.exists(file_id):
            flowfile.set_content(json.dumps({"error": "File not found"}).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        if user_id and not fstore.check_access(file_id, user_id=user_id):
            flowfile.set_content(json.dumps({"error": "Access denied"}).encode())
            flowfile.set_attribute("http.response.status", "403")
            return [flowfile]
        if conv_id and meta.get("conversation_id") != conv_id:
            flowfile.set_content(json.dumps({"error": "File not in this conversation"}).encode())
            flowfile.set_attribute("http.response.status", "403")
            return [flowfile]
        if not fstore.delete(file_id, user_id=user_id):
            flowfile.set_content(json.dumps({"error": "Access denied"}).encode())
            flowfile.set_attribute("http.response.status", "403")
            return [flowfile]
        flowfile.set_content(json.dumps({"ok": True, "file_id": file_id}).encode())
        return [flowfile]

    if action == "delete_files":
        file_ids = body.get("file_ids") or []
        conv_id = body.get("conversation_id", "")
        if not isinstance(file_ids, list) or not file_ids:
            flowfile.set_content(json.dumps({"error": "Missing file_ids"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        from core.file_store import FileStore
        fstore = FileStore.instance()
        deleted = []
        skipped = []
        for raw_id in file_ids:
            file_id = str(raw_id or "").strip()
            if not file_id:
                continue
            meta = fstore.get_metadata(file_id)
            if not meta or not fstore.exists(file_id):
                skipped.append({"file_id": file_id, "error": "File not found"})
                continue
            if user_id and not fstore.check_access(file_id, user_id=user_id):
                skipped.append({"file_id": file_id, "error": "Access denied"})
                continue
            if conv_id and meta.get("conversation_id") != conv_id:
                skipped.append({"file_id": file_id, "error": "File not in this conversation"})
                continue
            if fstore.delete(file_id, user_id=user_id):
                deleted.append(file_id)
            else:
                skipped.append({"file_id": file_id, "error": "Access denied"})
        flowfile.set_content(json.dumps({
            "ok": True,
            "deleted": len(deleted),
            "file_ids": deleted,
            "skipped": skipped,
        }).encode())
        return [flowfile]

    if action == "flow_runtime_graph":
        instance_id = body.get("instance_id", "")
        template_id = body.get("template_id", "")
        flow_ref = body.get("flow_ref") if isinstance(body.get("flow_ref"), dict) else None
        if not instance_id and not template_id and not flow_ref:
            flowfile.set_content(json.dumps({"error": "instance_id, template_id, or flow_ref required"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        try:
            from core.executor_registry import ExecutorRegistry
            from core.deployment_registry import DeploymentRegistry
            is_running = False
            nodes = {}
            edges = []
            flow_name = instance_id or template_id

            if flow_ref:
                raw = _load_flow_ref_definition(flow_ref)
                flow_name = raw.get("name") or raw.get("id") or flow_ref.get("path", "subflow")
                nodes, edges = _static_flow_graph(raw)
            elif template_id:
                raw = _load_flow_template_definition(
                    template_id, user_id, body.get("conversation_id", ""))
                flow_name = raw.get("name") or raw.get("id") or template_id
                nodes, edges = _static_flow_graph(raw)
            else:
                dep_reg = DeploymentRegistry.get_instance()
                inst = dep_reg.get(instance_id)
                flow_name = inst.flow_name if inst else instance_id

                executor = ExecutorRegistry.get_instance().get(instance_id)
                if executor:
                    is_running = executor.is_running
                    for tid, st in executor.get_all_task_states().items():
                        nodes[tid] = {
                            "type": st.get("task_type", "?"),
                            "state": st.get("state", "stopped"),
                            "in": st.get("flowfiles_in", 0),
                            "out": st.get("flowfiles_out", 0),
                            "error_count": st.get("error_count", 0),
                            "error": (st.get("error_message") or st.get("error", ""))[:80],
                            "in_flight": st.get("in_flight", False),
                        }
                    for gid, group in getattr(executor._flow, "groups", {}).items():
                        if gid not in nodes:
                            continue
                        flow_ref = getattr(group, "flow_ref", None) or {}
                        nodes[gid]["type"] = "subflow" if flow_ref else nodes[gid].get("type", "processGroup")
                        nodes[gid]["subflow_ref"] = flow_ref
                        nodes[gid]["group_name"] = getattr(group, "name", gid)
                    for qs in executor.get_queue_stats():
                        edges.append({
                            "source": qs["source"],
                            "target": qs["target"],
                            "relationship": qs.get("relationship", qs.get("type", "success")),
                            "queue_size": qs.get("queue_size", 0),
                            "max_queue": qs.get("max_queue_size", 10000),
                            "backpressured": qs.get("backpressured", False),
                        })
                    if inst:
                        try:
                            raw = _load_deployed_flow_definition(inst)
                            _add_declared_ports_to_graph(raw, nodes, edges)
                            _add_runtime_links_to_graph(raw, nodes, edges)
                        except Exception:
                            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                elif inst:
                    try:
                        raw = _load_deployed_flow_definition(inst)
                        nodes, edges = _static_flow_graph(raw)
                    except Exception:
                        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

            for e in edges:
                target = e["target"]
                if target in nodes:
                    nodes[target]["pending"] = nodes[target].get("pending", 0) + e.get("queue_size", 0)

            flowfile.set_content(json.dumps({
                "flow_name": flow_name, "instance_id": instance_id,
                "template_id": template_id, "is_running": is_running,
                "nodes": nodes, "edges": edges,
            }).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "list_conv_flows":
        # Show all flows belonging to this user (not conversation-scoped)
        try:
            from core.deployment_registry import DeploymentRegistry
            dep_reg = DeploymentRegistry.get_instance()
            dep_reg.sync_with_executors()
            uid = user_id or None
            instances = dep_reg.get_by_owner(uid) if uid else []
            flows_list = []
            for inst in instances:
                tasks_count = 0
                try:
                    from pathlib import Path as _Path
                    raw = json.loads(_Path(inst.flow_path).read_text(encoding="utf-8"))
                    tasks_count = len(raw.get("tasks", {}))
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                flows_list.append({
                    "id": inst.instance_id,
                    "name": inst.flow_name,
                    "status": inst.status,
                    "template": inst.flow_id if inst.flow_id != inst.instance_id else "",
                    "tasks_count": tasks_count,
                })
        except Exception:
            flows_list = []
        flowfile.set_content(
            json.dumps({"flows": flows_list}, ensure_ascii=False).encode())
        return [flowfile]

    if action == "manage_conv_flow":
        flow_id = body.get("flow_id", "")
        flow_action = body.get("flow_action", "")
        if not flow_id or not flow_action:
            flowfile.set_content(json.dumps(
                {"error": "flow_id and flow_action required"}).encode())
            return [flowfile]

        from core.deployment_registry import DeploymentRegistry
        dep_reg = DeploymentRegistry.get_instance()
        inst = dep_reg.get(flow_id)
        if not inst:
            flowfile.set_content(json.dumps(
                {"error": f"Flow '{flow_id}' not found"}).encode())
            return [flowfile]
        # Ownership check
        if user_id and inst.owner != user_id:
            flowfile.set_content(json.dumps(
                {"error": "Permission denied"}).encode())
            return [flowfile]

        if flow_action == "start":
            try:
                from core.executor_registry import ExecutorRegistry
                from engine.parser import FlowParser
                from engine.continuous_executor import ContinuousFlowExecutor
                from tasks import register_all_tasks
                register_all_tasks()
                raw = _load_deployed_flow_definition(inst)
                selected_trigger_ids = body.get("entry_task_ids")
                if selected_trigger_ids is None:
                    selected_trigger_ids = body.get("one_shot_trigger_ids")
                if selected_trigger_ids is not None:
                    if not isinstance(selected_trigger_ids, list):
                        flowfile.set_content(json.dumps(
                            {"error": "entry_task_ids must be a list"}).encode())
                        return [flowfile]
                    selected_trigger_ids = [str(tid) for tid in selected_trigger_ids if str(tid)]
                    from tasks.ai.actions.service_flow import _flow_one_shot_trigger_payload
                    one_shot_meta = _flow_one_shot_trigger_payload(raw or {})
                    valid_trigger_ids = {
                        item.get("task_id")
                        for item in one_shot_meta.get("one_shot_triggers", [])
                    }
                    if not one_shot_meta.get("is_one_shot_flow"):
                        flowfile.set_content(json.dumps(
                            {"error": "Flow has no selectable one-shot triggers"}).encode())
                        return [flowfile]
                    if not selected_trigger_ids and valid_trigger_ids:
                        flowfile.set_content(json.dumps(
                            {"error": "Select at least one one-shot trigger"}).encode())
                        return [flowfile]
                    invalid = [tid for tid in selected_trigger_ids
                               if tid not in valid_trigger_ids]
                    if invalid:
                        flowfile.set_content(json.dumps(
                            {"error": f"Unknown one-shot trigger(s): {invalid}"}).encode())
                        return [flowfile]
                clean = {k: v for k, v in raw.items()
                         if not k.startswith("_")}
                if inst.parameters:
                    clean.setdefault("parameters", {}).update(inst.parameters)
                flow = FlowParser.parse(clean)
                from core.executor_registry import _apply_service_bindings
                _apply_service_bindings(
                    flow, inst.service_overrides, inst.service_configs)
                reg = ExecutorRegistry.get_instance()
                existing = reg.get(flow_id)
                if existing:
                    try:
                        existing.stop()
                    except Exception:
                        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                    reg.unregister(flow_id)
                executor = ContinuousFlowExecutor(
                    flow, max_workers=inst.max_workers,
                    max_retries=inst.max_retries,
                    parameters=inst.parameters or None,
                    runtime_context={
                        "user_id": inst.owner or "",
                        "conversation_id": inst.conversation_id or "",
                        "scope": "conversation" if inst.conversation_id else "user" if inst.owner else "",
                        "agent_name": getattr(inst, "agent_name", "") or "",
                    },
                    enabled_one_shot_root_task_ids=selected_trigger_ids)
                executor.start()
                reg.register(flow_id, executor)
                flowfile.set_content(json.dumps(
                    {"message": f"Flow '{flow_id}' started"}).encode())
            except Exception as e:
                dep_reg.update_status(flow_id, "error", str(e))
                flowfile.set_content(json.dumps(
                    {"error": f"Start failed: {e}"}).encode())

        elif flow_action == "stop":
            try:
                from core.executor_registry import ExecutorRegistry
                reg = ExecutorRegistry.get_instance()
                ex = reg.get(flow_id)
                if ex:
                    ex.stop()
                    reg.unregister(flow_id)
                flowfile.set_content(json.dumps(
                    {"message": f"Flow '{flow_id}' stopped"}).encode())
            except Exception as e:
                flowfile.set_content(json.dumps(
                    {"error": f"Stop failed: {e}"}).encode())

        elif flow_action == "delete":
            try:
                from core.executor_registry import ExecutorRegistry
                reg = ExecutorRegistry.get_instance()
                ex = reg.get(flow_id)
                if ex:
                    ex.stop()
                    reg.unregister(flow_id)
                dep_reg.undeploy(flow_id)
                flowfile.set_content(json.dumps(
                    {"message": f"Flow '{flow_id}' deleted"}).encode())
            except Exception as e:
                flowfile.set_content(json.dumps(
                    {"error": f"Delete failed: {e}"}).encode())
        else:
            flowfile.set_content(json.dumps(
                {"error": f"Unknown action: {flow_action}"}).encode())
        return [flowfile]

    # Per-agent context routing helpers
    # All context actions below support agent_name param.
    # "ALL" means apply to all agents with diverged contexts.
    def _ctx_load(conv_id, agent_name=""):
        """Load context for an agent (falls back to shared -> messages)."""
        if agent_name and agent_name != "ALL":
            return store.load_agent_context(conv_id, agent_name)
        return store.load_context(conv_id, user_id=user_id)

    def _ctx_save(conv_id, data, agent_name=""):
        """Save context for an agent (or shared if no agent)."""
        if agent_name and agent_name != "ALL":
            store.save_agent_context(conv_id, agent_name, data)
        else:
            store.save_context(conv_id, data)

    def _resolve_agent_max_tokens(agent_name):
        """Get max_tokens from an agent's LLM service config."""
        try:
            from core.resource_store import ResourceStore
            adef = ResourceStore.instance().get_any("agent", agent_name, user_id)
            if adef and adef.get("llm_service"):
                from core.expression import resolve_value
                svc_id = resolve_value(adef["llm_service"], owner=user_id) or ""
                if svc_id:
                    _, svc = self._resolve_llm_service(svc_id, user_id)
                    if svc:
                        v = int((getattr(svc, 'config', {}) or {}).get("max_context_size", 0))
                        if v:
                            return v
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        return 0

    def _ctx_max_tokens(agent_name=""):
        """Get max_context_size for an agent or shared context.

        For a specific agent: use that agent's LLM service max_tokens.
        For shared ("" or "ALL"): use the LARGEST max_tokens among all
        agents (the shared context must fit the biggest consumer).
        """
        flow_default = int(self.config.get("max_context_size", 64000))
        if agent_name and agent_name not in ("", "ALL"):
            return _resolve_agent_max_tokens(agent_name) or flow_default
        # Shared: max of all agent LLM services
        try:
            from core.resource_store import ResourceStore
            all_agents = ResourceStore.instance().list_all("agent", user_id)
            max_val = 0
            for a in all_agents:
                v = _resolve_agent_max_tokens(a["name"])
                if v > max_val:
                    max_val = v
            # Also check the default LLM service
            default_svc = self._resolve_service_param("llm_service", user_id) or "default"
            if default_svc:
                _, svc = self._resolve_llm_service(default_svc, user_id)
                if svc:
                    v = int((getattr(svc, 'config', {}) or {}).get("max_context_size", 0))
                    if v > max_val:
                        max_val = v
            return max_val or flow_default
        except Exception:
            return flow_default

    from core.handlers._fs_base import find_fs_service as _find_svc, _FS_TYPES

    if action == "fs_list_services":
        services = []
        conv_id = body.get("conversation_id", "")
        agent_name = body.get("agent_name", "")
        try:
            from core.service_registry import ServiceRegistry
            reg = ServiceRegistry.get_instance()
            seen = set()
            if conv_id:
                from core.relay_bindings import get_linked
                for sid in get_linked(conv_id, agent_name):
                    if sid in seen:
                        continue
                    sdef = reg.resolve_definition(sid, user_id=user_id, conv_id=conv_id)
                    if not sdef or sdef.service_type not in ("relay", "filesystem"):
                        continue
                    seen.add(sid)
                    services.append({"id": sdef.service_id, "type": sdef.service_type, "scope": sdef.scope})
                try:
                    from core.remote_fs_bindings import list_tool_filesystems
                    for item in list_tool_filesystems(user_id, conv_id):
                        sid = item.get("id", "")
                        if not sid or sid in seen:
                            continue
                        seen.add(sid)
                        services.append({
                            "id": sid,
                            "type": item.get("type", "filesystem"),
                            "scope": item.get("scope", ""),
                        })
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        flowfile.set_content(json.dumps({"services": services}).encode())
        return [flowfile]

    if action == "fs_list_dir":
        from core.handlers._fs_base import find_fs_service as _find_svc, _FS_TYPES
        _fs_svc = _find_svc(user_id,body.get("service", ""))
        if not _fs_svc:
            flowfile.set_content(json.dumps({"error": "Filesystem service not found"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        try:
            entries = _fs_svc.list_dir(body.get("path", "."))
            result = [{"name": e.name, "kind": e.kind, "size": e.size, "modified": e.modified} for e in entries]
            flowfile.set_content(json.dumps({"entries": result}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "fs_read_file":
        import base64 as _b64r
        _fs_svc = _find_svc(user_id, body.get("service", ""))
        if not _fs_svc:
            flowfile.set_content(json.dumps({"error": "Filesystem service not found"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        try:
            data = _fs_svc.read_file(body.get("path", ""))
            # Try UTF-8, fallback to base64
            try:
                text = data.decode("utf-8")
                flowfile.set_content(json.dumps({"content": text, "encoding": "utf-8", "size": len(data)}).encode())
            except UnicodeDecodeError:
                flowfile.set_content(json.dumps({"content": _b64r.b64encode(data).decode("ascii"), "encoding": "base64", "size": len(data)}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "fs_write_file":
        import base64 as _b64w
        _fs_svc = _find_svc(user_id, body.get("service", ""))
        if not _fs_svc:
            flowfile.set_content(json.dumps({"error": "Filesystem service not found"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        try:
            content = body.get("content", "")
            encoding = body.get("encoding", "utf-8")
            if encoding == "base64":
                raw = _b64w.b64decode(content)
            else:
                raw = content.encode("utf-8")
            _fs_svc.write_file(body.get("path", ""), raw)
            flowfile.set_content(json.dumps({"ok": True, "size": len(raw)}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "fs_delete":
        from core.handlers._fs_base import find_fs_service as _find_svc, _FS_TYPES
        _fs_svc = _find_svc(user_id,body.get("service", ""))
        if not _fs_svc:
            flowfile.set_content(json.dumps({"error": "Filesystem service not found"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        try:
            _fs_svc.delete_file(body.get("path", ""))
            flowfile.set_content(json.dumps({"ok": True}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "fs_mkdir":
        from core.handlers._fs_base import find_fs_service as _find_svc, _FS_TYPES
        _fs_svc = _find_svc(user_id,body.get("service", ""))
        if not _fs_svc:
            flowfile.set_content(json.dumps({"error": "Filesystem service not found"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        try:
            _fs_svc.mkdir(body.get("path", ""))
            flowfile.set_content(json.dumps({"ok": True}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "fs_rename":
        from core.handlers._fs_base import find_fs_service as _find_svc, _FS_TYPES
        _fs_svc = _find_svc(user_id,body.get("service", ""))
        if not _fs_svc:
            flowfile.set_content(json.dumps({"error": "Filesystem service not found"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        try:
            old_path = body.get("old_path", "")
            new_path = body.get("new_path", "")
            if not old_path or not new_path:
                raise ValueError("Missing old_path or new_path")
            data = _fs_svc.read_file(old_path)
            _fs_svc.write_file(new_path, data)
            _fs_svc.delete_file(old_path)
            flowfile.set_content(json.dumps({"ok": True}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "fs_search":
        from core.handlers._fs_base import find_fs_service as _find_svc, _FS_TYPES
        _fs_svc = _find_svc(user_id,body.get("service", ""))
        if not _fs_svc:
            flowfile.set_content(json.dumps({"error": "Filesystem service not found"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        try:
            results = _fs_svc.search(body.get("path", "."), body.get("pattern", "*"))
            flowfile.set_content(json.dumps({"results": results[:200]}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "fs_copy":
        from core.handlers._fs_base import find_fs_service as _find_svc, _FS_TYPES
        src_svc = _find_svc(user_id,body.get("source_service", ""))
        dst_svc = _find_svc(user_id,body.get("dest_service", ""))
        if not src_svc or not dst_svc:
            flowfile.set_content(json.dumps({"error": "Source or dest service not found"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        try:
            data = src_svc.read_file(body.get("source_path", ""))
            dst_svc.write_file(body.get("dest_path", ""), data)
            flowfile.set_content(json.dumps({"ok": True, "size": len(data)}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "fs_copy_to_store":
        import mimetypes as _mt_fcs
        from core.handlers._fs_base import find_fs_service as _find_svc
        _conv_id = body.get("conversation_id", "")
        if not _conv_id:
            flowfile.set_content(json.dumps({"error": "conversation_id is required"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        _fs_svc = _find_svc(user_id, body.get("service", ""))
        if not _fs_svc:
            flowfile.set_content(json.dumps({"error": "Filesystem service not found"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        try:
            fpath = body.get("path", "")
            data = _fs_svc.read_file(fpath)
            fname = fpath.rsplit("/", 1)[-1] if "/" in fpath else fpath
            mime = _mt_fcs.guess_type(fname)[0] or "application/octet-stream"
            from core.file_store import FileStore
            fid = FileStore.instance().store(fname, data, mime,
                                              user_id=user_id, conversation_id=_conv_id)
            flowfile.set_content(json.dumps({"ok": True, "file_id": fid, "url": f"/files/{fid}/{fname}", "filename": fname, "size": len(data)}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "fs_exec":
        from core.handlers._fs_base import find_fs_service as _find_svc, _FS_TYPES
        _fs_svc = _find_svc(user_id,body.get("service", ""))
        if not _fs_svc:
            flowfile.set_content(json.dumps({"error": "Filesystem service not found"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        try:
            result = _fs_svc.exec(".", body.get("command", ""), int(body.get("timeout", 30)))
            flowfile.set_content(json.dumps(result).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "fs_zip_dir":
        """Zip a directory on a relay filesystem and return a FileStore download URL."""
        import mimetypes as _mt_zip
        from core.handlers._fs_base import find_fs_service as _find_svc
        _conv_id = body.get("conversation_id", "")
        if not _conv_id:
            flowfile.set_content(json.dumps({"error": "conversation_id is required"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        _fs_svc = _find_svc(user_id, body.get("service", ""))
        if not _fs_svc:
            flowfile.set_content(json.dumps({"error": "Filesystem service not found"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        try:
            dir_path = body.get("path", ".")
            # Sanitize dir_path for use as a filename
            _safe_name = dir_path.strip("/").replace("/", "_").replace("..", "") or "workspace"
            zip_name = f"{_safe_name}.zip"
            tmp_zip = f"/tmp/pawflow_zip_{zip_name}"  # nosec B108 - relay-local zip scratch path.
            # Build zip inside the relay via exec
            zip_cmd = f"cd '{dir_path}' && zip -r '{tmp_zip}' . && cat '{tmp_zip}' | base64"
            result = _fs_svc.exec(".", zip_cmd, 120)
            if result.get("returncode", 1) != 0:
                raise RuntimeError(result.get("stderr", "zip failed"))
            import base64 as _b64
            zip_bytes = _b64.b64decode(result["stdout"].strip())
            from core.file_store import FileStore
            fid = FileStore.instance().store(zip_name, zip_bytes, "application/zip",
                                              user_id=user_id, conversation_id=_conv_id)
            flowfile.set_content(json.dumps({
                "ok": True,
                "file_id": fid,
                "url": f"/files/{fid}/{zip_name}",
                "filename": zip_name,
                "size": len(zip_bytes),
            }).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    return None
