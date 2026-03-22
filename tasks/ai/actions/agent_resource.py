"""AgentLoopTask actions — agent resource"""

import json
import logging
import time
import threading
from typing import Dict, Any, List, Optional

from core import FlowFile
from core.llm_client import LLMMessage, LLMClient
from core.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


def _handle_agent_resource(self, action, body, store, user_id, flowfile):
    """Handle agent resource actions. Returns [flowfile] or None."""


    if action == "set_agent_nickname":
        conv_id = body.get("conversation_id", "")
        agent_name = body.get("agent_name", "").strip()
        nickname = body.get("nickname", "").strip()
        if agent_name and conv_id:
            agent_name = self._resolve_agent_name(agent_name, conv_id)
        if not conv_id or not agent_name or not nickname:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id, agent_name, or nickname"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        nicknames = store.get_extra(conv_id, "agent_nicknames") or {}
        nicknames[agent_name] = nickname
        store.set_extra(conv_id, "agent_nicknames", nicknames)
        flowfile.set_content(json.dumps({
            "ok": True, "agent_name": agent_name, "nickname": nickname,
        }).encode())
        return [flowfile]

    if action == "create_agent":
        conv_id = body.get("conversation_id", "")
        agent = body.get("name", "")
        prompt = body.get("prompt", "")
        scope = body.get("scope", "user")
        if not agent or not prompt:
            flowfile.set_content(json.dumps({"error": "Missing name or prompt"}).encode())
            return [flowfile]
        agent_data = {"prompt": prompt}
        if scope == "conversation" and conv_id:
            conv_agents = store.get_extra(conv_id, "conversation_agents") or {}
            conv_agents[agent] = agent_data
            store.set_extra(conv_id, "conversation_agents", conv_agents)
        else:
            from core.resource_store import ResourceStore
            ResourceStore.instance().create("agent", agent, user_id, agent_data)
        flowfile.set_content(json.dumps({
            "result": f"Agent '{agent}' created (scope: {scope})."
        }).encode())
        return [flowfile]

    if action == "list_agents":
        conv_id = body.get("conversation_id", "")
        from core.resource_store import ResourceStore
        uid = user_id or "anonymous"
        agents_list = ResourceStore.instance().list_all("agent", uid,
                                                       conversation_id=conv_id)
        agents = {a["name"]: a for a in agents_list}
        # Get selected agent from active_resources
        selected = ""
        if conv_id:
            active = store.get_extra(conv_id, "active_resources") or {}
            selected = active.get("agent", "")
        flowfile.set_content(json.dumps({
            "agents": agents, "selected": selected,
        }, ensure_ascii=False).encode())
        return [flowfile]

    if action == "agent_disable":
        conv_id = body.get("conversation_id", "")
        agent = body.get("agent_name", "")
        if not conv_id or not agent:
            flowfile.set_content(json.dumps({"error": "Missing params"}).encode())
            return [flowfile]
        disabled = store.get_extra(conv_id, "disabled_agents") or []
        if agent not in disabled:
            disabled.append(agent)
            store.set_extra(conv_id, "disabled_agents", disabled)
        flowfile.set_content(json.dumps({"result": f"Agent '{agent}' disabled in this conversation."}).encode())
        return [flowfile]

    if action == "agent_enable":
        conv_id = body.get("conversation_id", "")
        agent = body.get("agent_name", "")
        if not conv_id or not agent:
            flowfile.set_content(json.dumps({"error": "Missing params"}).encode())
            return [flowfile]
        disabled = store.get_extra(conv_id, "disabled_agents") or []
        if agent in disabled:
            disabled.remove(agent)
            store.set_extra(conv_id, "disabled_agents", disabled)
        flowfile.set_content(json.dumps({"result": f"Agent '{agent}' enabled in this conversation."}).encode())
        return [flowfile]

    if action == "agent_promote":
        conv_id = body.get("conversation_id", "")
        agent = body.get("agent_name", "")
        target_scope = body.get("target_scope", "user")
        if not agent:
            flowfile.set_content(json.dumps({"error": "Missing agent_name"}).encode())
            return [flowfile]
        from core.resource_store import ResourceStore, GLOBAL_USER_ID
        rs = ResourceStore.instance()
        item = rs.get_any("agent", agent, user_id, conversation_id=conv_id)
        if not item:
            flowfile.set_content(json.dumps({"error": f"Agent '{agent}' not found"}).encode())
            return [flowfile]
        current_scope = item.get("_scope", "user")
        promote_data = {k: v for k, v in item.items() if not k.startswith("_") and k != "name"}
        if target_scope == "user":
            rs.create("agent", agent, user_id, promote_data)
        elif target_scope == "global":
            rs.create("agent", agent, GLOBAL_USER_ID, promote_data)
        elif target_scope == "conversation" and conv_id:
            conv_agents = store.get_extra(conv_id, "conversation_agents") or {}
            conv_agents[agent] = promote_data
            store.set_extra(conv_id, "conversation_agents", conv_agents)
        flowfile.set_content(json.dumps({
            "result": f"Agent '{agent}' promoted from {current_scope} to {target_scope}."
        }).encode())
        return [flowfile]

    if action == "select_agent":
        conv_id = body.get("conversation_id", "")
        agent_name = body.get("name", "").strip()
        if agent_name:
            agent_name = self._resolve_agent_name(agent_name, conv_id)
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        if not agent_name:
            flowfile.set_content(json.dumps({"error": "Missing agent name"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        from core.resource_store import ResourceStore
        uid = user_id or "anonymous"
        if ResourceStore.instance().get_any("agent", agent_name, uid) is None:
            flowfile.set_content(json.dumps({
                "error": f"Agent '{agent_name}' not found",
            }).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        active = store.get_extra(conv_id, "active_resources") or {}
        active["agent"] = agent_name
        store.set_extra(conv_id, "active_resources", active)
        flowfile.set_content(json.dumps({
            "selected": agent_name,
        }).encode())
        return [flowfile]

    if action == "delete_agent":
        agent_name = body.get("name", "").strip()
        conv_id = body.get("conversation_id", "")
        if agent_name and conv_id:
            agent_name = self._resolve_agent_name(agent_name, conv_id)
        if not agent_name:
            flowfile.set_content(json.dumps({
                "error": "Missing name",
            }).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        from core.resource_store import ResourceStore
        uid = user_id or "anonymous"
        deleted = ResourceStore.instance().delete("agent", agent_name, uid)
        # Fall back to "assistant" if deleted agent was active
        if conv_id:
            active = store.get_extra(conv_id, "active_resources") or {}
            if active.get("agent") == agent_name:
                active["agent"] = "assistant"
                store.set_extra(conv_id, "active_resources", active)
        flowfile.set_content(json.dumps({
            "deleted": deleted, "name": agent_name,
        }).encode())
        return [flowfile]

    if action in ("create_skill", "add_skill"):
        skill_name = body.get("name", "").strip()
        skill_prompt = body.get("prompt", "").strip()
        conv_id = body.get("conversation_id", "")
        if not skill_name or not skill_prompt:
            flowfile.set_content(json.dumps({
                "error": "Missing name or prompt",
            }).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        from core.resource_store import ResourceStore
        rs = ResourceStore.instance()
        uid = user_id or "anonymous"
        try:
            data = {"prompt": skill_prompt}
            description = body.get("description", "")
            if description:
                data["description"] = description
            if rs.exists("skill", skill_name, uid):
                rs.update("skill", skill_name, uid, data)
            else:
                rs.create("skill", skill_name, uid, data)
            # Auto-activate in conversation
            if conv_id:
                active = store.get_extra(conv_id, "active_resources") or {}
                skills = active.get("skills", [])
                if skill_name not in skills:
                    skills.append(skill_name)
                active["skills"] = skills
                store.set_extra(conv_id, "active_resources", active)
            flowfile.set_content(json.dumps({
                "created": True, "name": skill_name,
            }).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "set_llm_service":
        conv_id = body.get("conversation_id", "")
        agent = body.get("agent_name", "")
        svc_value = body.get("llm_service", "")
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            return [flowfile]
        overrides = store.get_extra(conv_id, "agent_llm_overrides") or {}
        if svc_value == "restore" or svc_value == "":
            overrides.pop(agent, None)
            store.set_extra(conv_id, "agent_llm_overrides", overrides)
            flowfile.set_content(json.dumps({
                "result": f"LLM service for '{agent}' restored to default."
            }).encode())
        else:
            # Accept expressions like ${global.xxx} or direct service names
            overrides[agent] = svc_value
            store.set_extra(conv_id, "agent_llm_overrides", overrides)
            flowfile.set_content(json.dumps({
                "result": f"LLM service for '{agent}' set to '{svc_value}' in this conversation."
            }).encode())
        return [flowfile]

    if action == "delete_skill":
        skill_name = body.get("name", "").strip()
        conv_id = body.get("conversation_id", "")
        if not skill_name:
            flowfile.set_content(json.dumps({"error": "Missing name"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        from core.resource_store import ResourceStore
        uid = user_id or "anonymous"
        deleted = ResourceStore.instance().delete("skill", skill_name, uid)
        if conv_id:
            active = store.get_extra(conv_id, "active_resources") or {}
            skills = active.get("skills", [])
            if skill_name in skills:
                skills.remove(skill_name)
            active["skills"] = skills
            store.set_extra(conv_id, "active_resources", active)
        flowfile.set_content(json.dumps({
            "deleted": deleted, "name": skill_name,
        }).encode())
        return [flowfile]

    if action == "list_skills":
        from core.resource_store import ResourceStore
        uid = user_id or "anonymous"
        skills = ResourceStore.instance().list_all("skill", uid)
        conv_id = body.get("conversation_id", "")
        active_skills = []
        if conv_id:
            active = store.get_extra(conv_id, "active_resources") or {}
            active_skills = active.get("skills", [])
        flowfile.set_content(json.dumps({
            "skills": [{
                "name": s["name"],
                "description": s.get("description", ""),
                "prompt": s.get("prompt", "")[:80],
                "active": s["name"] in active_skills,
            } for s in skills],
        }, ensure_ascii=False).encode())
        return [flowfile]

    if action == "check_files":
        file_ids = body.get("file_ids", [])
        if not file_ids:
            flowfile.set_content(json.dumps({"available": []}).encode())
            return [flowfile]
        from core.file_store import FileStore
        fs = FileStore.instance()
        available = [fid for fid in file_ids if fs.exists(fid)]
        flowfile.set_content(json.dumps({"available": available}).encode())
        return [flowfile]

    if action == "list_resources":
        # List all resource types for the user
        conv_id = body.get("conversation_id", "")
        from core.resource_store import ResourceStore
        rs = ResourceStore.instance()
        uid = user_id or "anonymous"
        active = {}
        if conv_id:
            active = store.get_extra(conv_id, "active_resources") or {}
            active = self._ensure_active_agent(conv_id, active, uid)
        # Build agents list with autoconv status
        agents_out = []
        for a in rs.list_all("agent", uid, conversation_id=conv_id):
            aname = a["name"]
            entry = {
                "name": aname,
                "description": a.get("description", ""),
                "scope": a.get("_scope", ""),
                "active": active.get("agent") == aname,
            }
            if conv_id:
                ac_cfg = store.get_extra(conv_id, f"random_thought::{aname.lower()}") or {}
                if ac_cfg.get("enabled"):
                    entry["autoconv"] = ac_cfg.get("frequency", "on")
            agents_out.append(entry)
        result = {
            "agents": agents_out,
            "skills": [{
                "name": s["name"],
                "description": s.get("description", ""),
                "scope": s.get("_scope", ""),
                "active": s["name"] in active.get("skills", []),
            } for s in rs.list_all("skill", uid, conversation_id=conv_id)],
            "mcp_servers": [{
                "name": m["name"],
                "url": m.get("url", ""),
                "scope": m.get("_scope", ""),
                "active": m["name"] in active.get("mcps", []),
            } for m in rs.list_all("mcp", uid, conversation_id=conv_id)],
            "task_defs": [{
                "name": t["name"],
                "description": t.get("description", "") or t.get("prompt", "")[:60],
                "scope": t.get("_scope", ""),
                "default_interval": t.get("default_interval", "6/1m"),
            } for t in rs.list_all("task_def", uid, conversation_id=conv_id)],
        }
        # Running task instances for this conversation
        if conv_id:
            all_tasks = store.get_extra(conv_id, "agent_tasks") or {}
            running = []
            for tid, t in all_tasks.items():
                if not isinstance(t, dict):
                    continue
                running.append({
                    "task_id": tid,
                    "agent": t.get("agent", ""),
                    "task": t.get("task", "")[:80],
                    "status": t.get("status", ""),
                    "iterations": t.get("iterations_done", 0),
                    "max_iterations": t.get("max_iterations", 50),
                    "task_def_name": t.get("task_def_name", ""),
                })
            result["running_tasks"] = running
        # Services (global + user)
        try:
            from gui.services.global_service_registry import GlobalServiceRegistry
            from gui.services.user_service_registry import UserServiceRegistry
            svcs = []
            greg = GlobalServiceRegistry.get_instance()
            for sid, sdef in greg.get_all_definitions().items():
                svcs.append({
                    "service_id": sid,
                    "service_type": getattr(sdef, "service_type", ""),
                    "enabled": getattr(sdef, "enabled", True),
                    "description": getattr(sdef, "description", ""),
                    "scope": "global",
                })
            if uid and uid != "anonymous":
                ureg = UserServiceRegistry.get_instance()
                for sid, sdef in ureg.get_all_for_user(uid).items():
                    svcs.append({
                        "service_id": sid,
                        "service_type": getattr(sdef, "service_type", ""),
                        "enabled": getattr(sdef, "enabled", True),
                        "description": getattr(sdef, "description", ""),
                        "scope": "user",
                    })
            result["services"] = svcs
        except Exception:
            result["services"] = []
        # Deployed flows (global=readonly, user+conv visible)
        try:
            from gui.services.deployment_registry import DeploymentRegistry
            flows = []
            dr = DeploymentRegistry.get_instance()
            dr.sync_with_executors()
            uid = user_id or "anonymous"
            for iid, inst in dr.get_all().items():
                # Determine scope
                if not inst.owner or inst.owner == "__global__":
                    fscope = "global"
                elif inst.conversation_id:
                    fscope = "conversation"
                    # Only show conv-scoped flows in their conversation
                    if inst.conversation_id != conv_id:
                        continue
                else:
                    fscope = "user"
                # Skip other users' flows
                if fscope != "global" and inst.owner != uid:
                    continue
                flows.append({
                    "instance_id": iid,
                    "flow_name": inst.flow_name,
                    "status": inst.status,
                    "owner": inst.owner or "global",
                    "scope": fscope,
                    "template": inst.flow_id,
                })
            result["flows"] = flows
        except Exception:
            result["flows"] = []
        flowfile.set_content(json.dumps(result, ensure_ascii=False).encode())
        return [flowfile]

    if action == "get_resource_detail":
        rtype = body.get("resource_type", "")
        rname = body.get("name", "").strip()
        if not rtype or not rname:
            flowfile.set_content(json.dumps({"error": "Missing resource_type or name"}).encode())
            return [flowfile]
        from core.resource_store import ResourceStore
        rs = ResourceStore.instance()
        uid = user_id or "anonymous"
        conv_id = body.get("conversation_id", "")
        item = rs.get_any(rtype, rname, uid, conversation_id=conv_id)
        if not item:
            flowfile.set_content(json.dumps({"error": f"{rtype} '{rname}' not found"}).encode())
            return [flowfile]
        flowfile.set_content(json.dumps(item, ensure_ascii=False).encode())
        return [flowfile]

    if action == "update_resource":
        rtype = body.get("resource_type", "")
        rname = body.get("name", "").strip()
        data = body.get("data", {})
        scope = body.get("scope", "user")
        if scope == "global":
            flowfile.set_content(json.dumps({"error": "Cannot update global resources from chat. Use the admin GUI."}).encode())
            flowfile.set_attribute("http.response.status", "403")
            return [flowfile]
        if not rtype or not rname:
            flowfile.set_content(json.dumps({"error": "Missing resource_type or name"}).encode())
            return [flowfile]
        from core.resource_store import ResourceStore
        rs = ResourceStore.instance()
        uid = user_id or "anonymous"
        target_uid = "__global__" if scope == "global" else uid
        try:
            rs.update(rtype, rname, target_uid, data)
            flowfile.set_content(json.dumps({"ok": True}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "create_resource":
        rtype = body.get("resource_type", "")
        rname = body.get("name", "").strip()
        data = body.get("data", {})
        scope = body.get("scope", "user")
        if scope == "global":
            flowfile.set_content(json.dumps({"error": "Cannot create global resources from chat. Use the admin GUI."}).encode())
            flowfile.set_attribute("http.response.status", "403")
            return [flowfile]
        if not rtype or not rname:
            flowfile.set_content(json.dumps({"error": "Missing resource_type or name"}).encode())
            return [flowfile]
        from core.resource_store import ResourceStore
        rs = ResourceStore.instance()
        uid = user_id or "anonymous"
        target_uid = "__global__" if scope == "global" else uid
        if rtype == "task_def":
            data.setdefault("created_by", uid)
        try:
            rs.create(rtype, rname, target_uid, data)
            flowfile.set_content(json.dumps({"ok": True}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "delete_resource":
        rtype = body.get("resource_type", "")
        rname = body.get("name", "").strip()
        scope = body.get("scope", "user")
        if scope == "global":
            flowfile.set_content(json.dumps({"error": "Cannot delete global resources from chat. Use the admin GUI."}).encode())
            flowfile.set_attribute("http.response.status", "403")
            return [flowfile]
        if not rtype or not rname:
            flowfile.set_content(json.dumps({"error": "Missing resource_type or name"}).encode())
            return [flowfile]
        from core.resource_store import ResourceStore
        rs = ResourceStore.instance()
        uid = user_id or "anonymous"
        target_uid = "__global__" if scope == "global" else uid
        deleted = rs.delete(rtype, rname, target_uid)
        flowfile.set_content(json.dumps({"ok": True, "deleted": deleted}).encode())
        return [flowfile]

    if action == "copy_resource_scope":
        rtype = body.get("resource_type", "")
        rname = body.get("name", "").strip()
        target_scope = body.get("target_scope", "")
        if not rtype or not rname or not target_scope:
            flowfile.set_content(json.dumps({"error": "Missing resource_type, name, or target_scope"}).encode())
            return [flowfile]
        from core.resource_store import ResourceStore
        rs = ResourceStore.instance()
        uid = user_id or "anonymous"
        conv_id = body.get("conversation_id", "")
        item = rs.get_any(rtype, rname, uid, conversation_id=conv_id)
        if not item:
            flowfile.set_content(json.dumps({"error": f"{rtype} '{rname}' not found"}).encode())
            return [flowfile]
        target_uid = "__global__" if target_scope == "global" else uid
        data = {k: v for k, v in item.items() if k not in ("name", "_scope")}
        try:
            rs.create(rtype, rname, target_uid, data)
            flowfile.set_content(json.dumps({"ok": True, "copied_to": target_scope}).encode())
        except Exception as e:
            # If exists, update instead
            try:
                rs.update(rtype, rname, target_uid, data)
                flowfile.set_content(json.dumps({"ok": True, "copied_to": target_scope, "updated": True}).encode())
            except Exception as e2:
                flowfile.set_content(json.dumps({"error": str(e2)}).encode())
        return [flowfile]

    if action == "activate_resource":
        conv_id = body.get("conversation_id", "")
        rtype = body.get("resource_type", "")
        rname = body.get("name", "").strip()
        if not conv_id or not rtype or not rname:
            flowfile.set_content(json.dumps({
                "error": "Missing conversation_id, resource_type, or name",
            }).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        active = store.get_extra(conv_id, "active_resources") or {}
        if rtype == "agent":
            active["agent"] = rname
        elif rtype == "skill":
            skills = active.get("skills", [])
            if rname not in skills:
                skills.append(rname)
            active["skills"] = skills
        elif rtype == "mcp":
            mcps = active.get("mcps", [])
            if rname not in mcps:
                mcps.append(rname)
            active["mcps"] = mcps
        store.set_extra(conv_id, "active_resources", active)
        flowfile.set_content(json.dumps({
            "activated": True, "type": rtype, "name": rname,
        }).encode())
        return [flowfile]

    if action == "deactivate_resource":
        conv_id = body.get("conversation_id", "")
        rtype = body.get("resource_type", "")
        rname = body.get("name", "").strip()
        if not conv_id or not rtype or not rname:
            flowfile.set_content(json.dumps({
                "error": "Missing conversation_id, resource_type, or name",
            }).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        active = store.get_extra(conv_id, "active_resources") or {}
        if rtype == "agent":
            if active.get("agent") == rname:
                active.pop("agent", None)
        elif rtype == "skill":
            skills = active.get("skills", [])
            if rname in skills:
                skills.remove(rname)
            active["skills"] = skills
        elif rtype == "mcp":
            mcps = active.get("mcps", [])
            if rname in mcps:
                mcps.remove(rname)
            active["mcps"] = mcps
        store.set_extra(conv_id, "active_resources", active)
        flowfile.set_content(json.dumps({
            "deactivated": True, "type": rtype, "name": rname,
        }).encode())
        return [flowfile]

    if action == "share_resource":
        rtype = body.get("resource_type", "")
        rname = body.get("name", "").strip()
        target_conv = body.get("target_conversation_id", "")
        if not rtype or not rname or not target_conv:
            flowfile.set_content(json.dumps({
                "error": "Missing resource_type, name, or target_conversation_id",
            }).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        # Verify ownership of target conversation
        target_meta = store.get_metadata(target_conv)
        if not target_meta or (user_id and target_meta.get("user_id") != user_id):
            flowfile.set_content(json.dumps({
                "error": "Target conversation not found or access denied",
            }).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        # Activate in target
        active = store.get_extra(target_conv, "active_resources") or {}
        if rtype == "agent":
            active["agent"] = rname
        elif rtype == "skill":
            skills = active.get("skills", [])
            if rname not in skills:
                skills.append(rname)
            active["skills"] = skills
        elif rtype == "mcp":
            mcps = active.get("mcps", [])
            if rname not in mcps:
                mcps.append(rname)
            active["mcps"] = mcps
        store.set_extra(target_conv, "active_resources", active)
        flowfile.set_content(json.dumps({
            "shared": True, "type": rtype, "name": rname,
            "target": target_conv,
        }).encode())
        return [flowfile]

    return None
