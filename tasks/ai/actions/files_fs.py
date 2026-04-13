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


def _handle_files_fs(self, action, body, store, user_id, flowfile):
    """Handle files fs actions. Returns [flowfile] or None."""


    if action == "list_conv_files":
        conv_id = body.get("conversation_id", "")
        if not conv_id:
            flowfile.set_content(json.dumps({"files": []}).encode())
            return [flowfile]
        messages_data = store.load(conv_id, user_id=user_id) or []
        # Also include files from sub-conversations (task contexts)
        all_convs = store.list_conversations(user_id=user_id) if user_id else []
        # list_conversations filters ::task::, so search extras directly
        try:
            extras = store.get_extras(conv_id, user_id=user_id) or {}
            for k in extras:
                if k.startswith("task_log:"):
                    # There might be a sub-conv for this task
                    tid = k[9:]
                    sub_cid = f"{conv_id}::task::{tid}"
                    sub_msgs = store.load(sub_cid, user_id=user_id)
                    if sub_msgs:
                        messages_data.extend(sub_msgs)
        except Exception:
            pass
        if not messages_data:
            flowfile.set_content(json.dumps({"files": []}).encode())
            return [flowfile]
        import re as _re
        from core.file_store import FileStore
        fstore = FileStore.instance()
        pattern = _re.compile(r'/files/([a-f0-9]{12})')
        seen = set()
        files = []
        for msg in messages_data:
            content = msg.get("content", "")
            if not isinstance(content, str):
                continue
            for match in pattern.finditer(content):
                fid = match.group(1)
                fname = match.group(2)
                if fid in seen:
                    continue
                seen.add(fid)
                available = fstore.exists(fid)
                files.append({
                    "file_id": fid, "filename": fname,
                    "available": available,
                })
        flowfile.set_content(json.dumps({"files": files}, ensure_ascii=False).encode())
        return [flowfile]

    if action == "delete_file":
        file_id = body.get("file_id", "")
        conv_id = body.get("conversation_id", "")
        if not file_id:
            flowfile.set_content(json.dumps({"error": "Missing file_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        # Verify the file belongs to a conversation owned by this user
        if conv_id and user_id:
            conv_data = store.load(conv_id, user_id=user_id)
            if conv_data is None:
                flowfile.set_content(json.dumps({"error": "Access denied"}).encode())
                flowfile.set_attribute("http.response.status", "403")
                return [flowfile]
            # Verify file_id is referenced in this conversation
            import re as _re_del
            found = any(
                file_id in (m.get("content", "") if isinstance(m.get("content"), str) else "")
                for m in conv_data
            )
            if not found:
                flowfile.set_content(json.dumps({"error": "File not in this conversation"}).encode())
                flowfile.set_attribute("http.response.status", "403")
                return [flowfile]
        from core.file_store import FileStore
        fstore = FileStore.instance()
        if not fstore.exists(file_id):
            flowfile.set_content(json.dumps({"error": "File not found"}).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        fstore.delete(file_id)
        flowfile.set_content(json.dumps({"ok": True, "file_id": file_id}).encode())
        return [flowfile]

    if action == "flow_runtime_graph":
        instance_id = body.get("instance_id", "")
        if not instance_id:
            flowfile.set_content(json.dumps({"error": "Missing instance_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        try:
            from core.executor_registry import ExecutorRegistry
            from core.deployment_registry import DeploymentRegistry
            dep_reg = DeploymentRegistry.get_instance()
            inst = dep_reg.get(instance_id)
            flow_name = inst.flow_name if inst else instance_id

            executor = ExecutorRegistry.get_instance().get(instance_id)
            is_running = False
            nodes = {}
            edges = []

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
                for qs in executor.get_queue_stats():
                    edges.append({
                        "source": qs["source"],
                        "target": qs["target"],
                        "relationship": qs.get("relationship", qs.get("type", "success")),
                        "queue_size": qs.get("queue_size", 0),
                        "max_queue": qs.get("max_queue_size", 10000),
                        "backpressured": qs.get("backpressured", False),
                    })
            elif inst and inst.flow_path:
                # Stopped flow — load structure from file
                from pathlib import Path as _P
                try:
                    raw = json.loads(_P(inst.flow_path).read_text(encoding="utf-8"))
                    for tid, tdef in raw.get("tasks", {}).items():
                        nodes[tid] = {"type": tdef.get("type", "?"), "state": "stopped",
                                      "in": 0, "out": 0, "error_count": 0, "error": "", "in_flight": False}
                    for rel in raw.get("relations", []):
                        edges.append({"source": rel["from"], "target": rel["to"],
                                      "relationship": rel.get("type", "success"),
                                      "queue_size": 0, "max_queue": 10000, "backpressured": False})
                except Exception:
                    pass

            # Compute pending flowfiles per node (sum of incoming queue sizes)
            for e in edges:
                tgt = e["target"]
                if tgt in nodes:
                    nodes[tgt]["pending"] = nodes[tgt].get("pending", 0) + e["queue_size"]

            flowfile.set_content(json.dumps({
                "flow_name": flow_name, "instance_id": instance_id,
                "is_running": is_running, "nodes": nodes, "edges": edges,
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
                    pass
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
                raw = json.loads(
                    open(inst.flow_path, encoding="utf-8").read())
                clean = {k: v for k, v in raw.items()
                         if not k.startswith("_")}
                if inst.parameters:
                    clean.setdefault("parameters", {}).update(inst.parameters)
                flow = FlowParser.parse(clean)
                reg = ExecutorRegistry.get_instance()
                existing = reg.get(flow_id)
                if existing:
                    try:
                        existing.stop()
                    except Exception:
                        pass
                    reg.unregister(flow_id)
                executor = ContinuousFlowExecutor(
                    flow, max_workers=inst.max_workers,
                    max_retries=inst.max_retries,
                    parameters=inst.parameters or None)
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

    # â”€â”€ Per-agent context routing helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # All context actions below support agent_name param.
    # "ALL" means apply to all agents with diverged contexts.
    def _ctx_load(conv_id, agent_name=""):
        """Load context for an agent (falls back to shared â†’ messages)."""
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
            pass
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

    if action == "fs_list_services":
        from core.handlers._fs_base import find_fs_service as _find_svc, _FS_TYPES
        services = []
        try:
            from core.service_registry import ServiceRegistry
            reg = ServiceRegistry.get_instance()
            for fs_type in _FS_TYPES:
                for sdef in reg.resolve_by_type(fs_type, user_id=user_id):
                    services.append({"id": sdef.service_id, "type": sdef.service_type, "scope": sdef.scope})
        except Exception:
            pass
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
            tmp_zip = f"/tmp/pawflow_zip_{zip_name}"
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
