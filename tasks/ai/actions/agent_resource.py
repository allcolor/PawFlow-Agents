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
            "display": f"Agent selected: {agent_name}",
            "state_update": {"selected_agent": agent_name},
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
            # Accept expressions like ${xxx} or direct service names
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

    if action == "assign_skill":
        agent_name = body.get("agent_name", "").strip()
        skill_name = body.get("skill_name", "").strip()
        if not agent_name or not skill_name:
            flowfile.set_content(json.dumps({"error": "Missing agent_name or skill_name"}).encode())
            return [flowfile]
        from core.resource_store import ResourceStore
        rs = ResourceStore.instance()
        uid = user_id or "anonymous"
        agent_def = rs.get_any("agent", agent_name, uid)
        if not agent_def:
            flowfile.set_content(json.dumps({"error": f"Agent '{agent_name}' not found"}).encode())
            return [flowfile]
        skill_def = rs.get_any("skill", skill_name, uid)
        if not skill_def:
            flowfile.set_content(json.dumps({"error": f"Skill '{skill_name}' not found"}).encode())
            return [flowfile]
        assigned = agent_def.get("assigned_skills", [])
        if skill_name not in assigned:
            assigned.append(skill_name)
        # Update agent in the correct scope
        _scope = agent_def.get("_scope", "user")
        _uid = uid if _scope == "user" else "__global__"
        rs.update("agent", agent_name, _uid, {"assigned_skills": assigned})
        flowfile.set_content(json.dumps({
            "assigned": True, "agent": agent_name, "skill": skill_name,
            "message": f"Skill '{skill_name}' assigned to agent '{agent_name}'",
        }).encode())
        return [flowfile]

    if action == "unassign_skill":
        agent_name = body.get("agent_name", "").strip()
        skill_name = body.get("skill_name", "").strip()
        if not agent_name or not skill_name:
            flowfile.set_content(json.dumps({"error": "Missing agent_name or skill_name"}).encode())
            return [flowfile]
        from core.resource_store import ResourceStore
        rs = ResourceStore.instance()
        uid = user_id or "anonymous"
        agent_def = rs.get_any("agent", agent_name, uid)
        if not agent_def:
            flowfile.set_content(json.dumps({"error": f"Agent '{agent_name}' not found"}).encode())
            return [flowfile]
        assigned = agent_def.get("assigned_skills", [])
        if skill_name in assigned:
            assigned.remove(skill_name)
        _scope = agent_def.get("_scope", "user")
        _uid = uid if _scope == "user" else "__global__"
        rs.update("agent", agent_name, _uid, {"assigned_skills": assigned})
        flowfile.set_content(json.dumps({
            "unassigned": True, "agent": agent_name, "skill": skill_name,
            "message": f"Skill '{skill_name}' removed from agent '{agent_name}'",
        }).encode())
        return [flowfile]

    if action == "list_agent_skills":
        agent_name = body.get("agent_name", "").strip()
        if not agent_name:
            flowfile.set_content(json.dumps({"error": "Missing agent_name"}).encode())
            return [flowfile]
        from core.resource_store import ResourceStore
        rs = ResourceStore.instance()
        uid = user_id or "anonymous"
        agent_def = rs.get_any("agent", agent_name, uid)
        if not agent_def:
            flowfile.set_content(json.dumps({"error": f"Agent '{agent_name}' not found"}).encode())
            return [flowfile]
        assigned = agent_def.get("assigned_skills", [])
        skills_detail = []
        for sn in assigned:
            sd = rs.get_any("skill", sn, uid)
            skills_detail.append({
                "name": sn,
                "description": sd.get("description", "") if sd else "(not found)",
            })
        flowfile.set_content(json.dumps({
            "agent": agent_name, "skills": skills_detail,
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
        # Build agents list: only agents that are members of this conversation
        # (active_resources.agents), not all repo agents.
        conv_agent_names = active.get("agents", [])
        # Backward compat: old format had only active.agent (single agent)
        if not conv_agent_names and active.get("agent"):
            conv_agent_names = [active["agent"]]

        agents_out = []
        for aname in conv_agent_names:
            a = rs.get_any("agent", aname, uid)
            if not a:
                # Agent deleted from repo but still referenced in conv
                a = {"name": aname, "description": "", "_scope": ""}
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

        # Repo agents list (all global+user agents, with in_conversation flag)
        all_repo_agents = rs.list_all("agent", uid)
        repo_agent_count = len(all_repo_agents)
        repo_agents_out = [{
            "name": a["name"],
            "description": a.get("description", ""),
            "scope": a.get("_scope", ""),
            "in_conversation": a["name"] in set(conv_agent_names),
        } for a in all_repo_agents]
        result = {
            "agents": agents_out,
            "repo_agent_count": repo_agent_count,
            "repo_agents": repo_agents_out,
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
        # Task instances for this conversation
        if conv_id:
            all_tasks = store.get_extra(conv_id, "agent_tasks") or {}
            running = []
            all_task_instances = []
            for tid, t in all_tasks.items():
                if not isinstance(t, dict):
                    continue
                entry = {
                    "task_id": tid,
                    "agent": t.get("agent", ""),
                    "task": t.get("task", "")[:80],
                    "status": t.get("status", ""),
                    "iterations": t.get("iterations_done", 0),
                    "max_iterations": t.get("max_iterations", 50),
                    "task_def_name": t.get("task_def_name", ""),
                    "interval": t.get("interval", {}),
                    "timeout": t.get("timeout", 0),
                }
                all_task_instances.append(entry)
                # Running = active or paused only
                if t.get("status") in ("active", "paused"):
                    running.append(entry)
            result["running_tasks"] = running
            result["all_tasks"] = all_task_instances
        # Services (global + user)
        try:
            from gui.services.global_service_registry import GlobalServiceRegistry
            from gui.services.user_service_registry import UserServiceRegistry
            svcs = []
            greg = GlobalServiceRegistry.get_instance()
            for sid, sdef in greg.get_all_definitions().items():
                _enabled = getattr(sdef, "enabled", True)
                try:
                    _started = greg.is_connected(sid) if _enabled else False
                except Exception:
                    _started = False
                svcs.append({
                    "service_id": sid,
                    "service_type": getattr(sdef, "service_type", ""),
                    "enabled": _enabled,
                    "started": _started,
                    "description": getattr(sdef, "description", ""),
                    "scope": "global",
                })
            if uid and uid != "anonymous":
                ureg = UserServiceRegistry.get_instance()
                for sid, sdef in ureg.get_all_for_user(uid).items():
                    _enabled = getattr(sdef, "enabled", True)
                    try:
                        _started = ureg.is_connected(uid, sid) if _enabled else False
                    except Exception:
                        _started = False
                    _entry = {
                        "service_id": sid,
                        "service_type": getattr(sdef, "service_type", ""),
                        "enabled": _enabled,
                        "started": _started,
                        "description": getattr(sdef, "description", ""),
                        "scope": "user",
                    }
                    _svc = ureg.get_live_instance(uid, sid) if _enabled else None
                    if _svc and hasattr(_svc, '_relay_info') and _svc._relay_info:
                        _entry["relay_info"] = _svc._relay_info
                    elif sdef.config and sdef.config.get("docker_image"):
                        _entry["relay_info"] = {
                            "containerized": True,
                            "docker_image": sdef.config["docker_image"],
                        }
                    svcs.append(_entry)
            result["services"] = svcs
        except Exception:
            result["services"] = []
        # Relay bindings for this conversation (new per-agent format)
        if conv_id:
            try:
                from core.relay_bindings import get_bindings
                _rb = get_bindings(conv_id)
                # Collect all unique relay IDs across all scopes
                _all_ids = set()
                for scope_list in (_rb.get("linked") or {}).values():
                    _all_ids.update(scope_list)
                _relay_details = {}
                try:
                    from gui.services.global_service_registry import GlobalServiceRegistry
                    _greg2 = GlobalServiceRegistry.get_instance()
                    _ureg2 = None
                    try:
                        from gui.services.user_service_registry import UserServiceRegistry
                        _ureg2 = UserServiceRegistry.get_instance()
                    except Exception:
                        pass
                    for _rid in _all_ids:
                        _rsvc = None
                        _connected = False
                        # Same logic as service list: use registry.is_connected
                        try:
                            _connected = _greg2.is_connected(_rid)
                            _rsvc = _greg2.get_live_instance(_rid)
                        except Exception:
                            pass
                        if not _rsvc and _ureg2 and user_id:
                            try:
                                _connected = _ureg2.is_connected(user_id, _rid)
                                _rsvc = _ureg2.get_live_instance(user_id, _rid)
                            except Exception:
                                pass
                        _ri2 = getattr(_rsvc, '_relay_info', {}) or {} if _rsvc else {}
                        _relay_details[_rid] = {
                            "root": _ri2.get("root", ""),
                            "host_root": _ri2.get("host_root", ""),
                            "platform": _ri2.get("platform", ""),
                            "containerized": _ri2.get("containerized", False),
                            "allow_local": _ri2.get("allow_local", False),
                            "connected": _connected,
                        }
                except Exception:
                    pass
                result["relay_bindings"] = {
                    "linked": _rb.get("linked", {}),
                    "default": _rb.get("default", {}),
                    "default_local": _rb.get("default_local", {}),
                    "details": _relay_details,
                }
            except Exception:
                result["relay_bindings"] = {"linked": {}, "default": {}}
        # Deployed flows (global=readonly, user+conv visible)
        try:
            from gui.services.deployment_registry import DeploymentRegistry
            flows = []
            dr = DeploymentRegistry.get_instance()
            # sync_with_executors removed from request path — too expensive.
            # DeploymentRegistry syncs on its own schedule.
            uid = user_id or "anonymous"
            _is_admin = (flowfile.get_attribute("http.auth.roles") or "") == "admin"
            for iid, inst in dr.get_all().items():
                # Determine scope
                if not inst.owner or inst.owner == "__global__":
                    fscope = "global"
                elif inst.conversation_id:
                    fscope = "conversation"
                    # Show conv-scoped flows if they belong to this conv
                    # (including flows deployed from task sub-conversations)
                    _inst_parent = inst.conversation_id.split("::task::")[0] if "::task::" in inst.conversation_id else inst.conversation_id
                    if _inst_parent != conv_id and not _is_admin:
                        continue
                else:
                    fscope = "user"
                # Skip other users' flows (admins see all)
                if fscope != "global" and inst.owner != uid and not _is_admin:
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
        # Include user role so frontend can enable admin features
        _user_role = flowfile.get_attribute("http.auth.roles") or "viewer"
        result["user_role"] = _user_role

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
        if scope == "global" and "admin" not in (flowfile.get_attribute("http.auth.roles") or ""):
            flowfile.set_content(json.dumps({"error": "Requires admin role for global scope"}).encode())
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
        conv_id = body.get("conversation_id", "")
        if scope == "global" and "admin" not in (flowfile.get_attribute("http.auth.roles") or ""):
            flowfile.set_content(json.dumps({"error": "Requires admin role for global scope"}).encode())
            flowfile.set_attribute("http.response.status", "403")
            return [flowfile]
        if rtype == "agent" and scope == "conversation":
            flowfile.set_content(json.dumps({"error": "Agents cannot use conversation scope. Create with user or global scope, then add to conversation via add_agent_to_conv."}).encode())
            flowfile.set_attribute("http.response.status", "400")
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
            if rtype == "task_def" and scope == "conversation" and conv_id:
                from core.conversation_store import ConversationStore
                cs = ConversationStore.instance()
                conv_defs = cs.get_extra(conv_id, "conversation_task_defs") or {}
                conv_defs[rname] = data
                cs.set_extra(conv_id, "conversation_task_defs", conv_defs)
            else:
                rs.create(rtype, rname, target_uid, data)
            flowfile.set_content(json.dumps({"ok": True}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "delete_resource":
        rtype = body.get("resource_type", "")
        rname = body.get("name", "").strip()
        scope = body.get("scope", "user")
        conv_id = body.get("conversation_id", "")
        if scope == "global" and "admin" not in (flowfile.get_attribute("http.auth.roles") or ""):
            flowfile.set_content(json.dumps({"error": "Requires admin role for global scope"}).encode())
            flowfile.set_attribute("http.response.status", "403")
            return [flowfile]
        if not rtype or not rname:
            flowfile.set_content(json.dumps({"error": "Missing resource_type or name"}).encode())
            return [flowfile]
        from core.resource_store import ResourceStore
        rs = ResourceStore.instance()
        uid = user_id or "anonymous"
        target_uid = "__global__" if scope == "global" else uid
        if rtype == "task_def" and scope == "conversation" and conv_id:
            from core.conversation_store import ConversationStore
            cs = ConversationStore.instance()
            conv_defs = cs.get_extra(conv_id, "conversation_task_defs") or {}
            deleted = rname in conv_defs
            if deleted:
                del conv_defs[rname]
                cs.set_extra(conv_id, "conversation_task_defs", conv_defs)
        else:
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
        source_scope = item.get("_scope", "")
        try:
            if target_scope == "conversation" and conv_id and rtype == "task_def":
                # Copy into conversation scope
                from core.conversation_store import ConversationStore
                cs = ConversationStore.instance()
                conv_defs = cs.get_extra(conv_id, "conversation_task_defs") or {}
                conv_defs[rname] = data
                cs.set_extra(conv_id, "conversation_task_defs", conv_defs)
            else:
                rs.create(rtype, rname, target_uid, data)
            # If promoting from conversation scope, remove from there
            if source_scope == "conversation" and target_scope != "conversation" and conv_id and rtype == "task_def":
                from core.conversation_store import ConversationStore
                cs = ConversationStore.instance()
                conv_defs = cs.get_extra(conv_id, "conversation_task_defs") or {}
                conv_defs.pop(rname, None)
                cs.set_extra(conv_id, "conversation_task_defs", conv_defs)
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

    if action == "add_agent_to_conv":
        conv_id = body.get("conversation_id", "")
        aname = body.get("name", "").strip()
        if not conv_id or not aname:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id or name"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        from core.resource_store import ResourceStore
        uid = user_id or "anonymous"
        agent = ResourceStore.instance().get_any("agent", aname, uid)
        if not agent:
            flowfile.set_content(json.dumps({"error": f"Agent '{aname}' not found in repository"}).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        active = store.get_extra(conv_id, "active_resources") or {}
        agents = active.setdefault("agents", [])
        if aname not in agents:
            agents.append(aname)
        if not active.get("agent"):
            active["agent"] = aname
        store.set_extra(conv_id, "active_resources", active)
        flowfile.set_content(json.dumps({"ok": True, "agent": aname}).encode())
        return [flowfile]

    if action == "remove_agent_from_conv":
        conv_id = body.get("conversation_id", "")
        aname = body.get("name", "").strip()
        if not conv_id or not aname:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id or name"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        active = store.get_extra(conv_id, "active_resources") or {}
        agents = active.get("agents", [])
        if aname in agents:
            agents.remove(aname)
        active["agents"] = agents
        # If removed agent was the selected primary, pick next or clear
        if active.get("agent") == aname:
            active["agent"] = agents[0] if agents else ""
        store.set_extra(conv_id, "active_resources", active)
        flowfile.set_content(json.dumps({"ok": True}).encode())
        return [flowfile]

    if action == "list_repo_agents":
        conv_id = body.get("conversation_id", "")
        from core.resource_store import ResourceStore
        uid = user_id or "anonymous"
        rs = ResourceStore.instance()
        all_agents = rs.list_all("agent", uid)
        conv_agents = set()
        if conv_id:
            active = store.get_extra(conv_id, "active_resources") or {}
            conv_agents = set(active.get("agents", []))
            if not conv_agents and active.get("agent"):
                conv_agents = {active["agent"]}
        out = []
        for a in all_agents:
            out.append({
                "name": a["name"],
                "description": a.get("description", ""),
                "scope": a.get("_scope", ""),
                "in_conversation": a["name"] in conv_agents,
            })
        flowfile.set_content(json.dumps({"agents": out}, ensure_ascii=False).encode())
        return [flowfile]

    if action == "create_conversation":
        agents = body.get("agents", [])
        if not agents or not isinstance(agents, list):
            flowfile.set_content(json.dumps({"error": "'agents' list is required"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        # Validate agents exist in repo
        from core.resource_store import ResourceStore
        uid = user_id or "anonymous"
        rs = ResourceStore.instance()
        valid_agents = []
        for aname in agents:
            if rs.get_any("agent", aname, uid):
                valid_agents.append(aname)
        if not valid_agents:
            flowfile.set_content(json.dumps({"error": "None of the specified agents exist in the repository"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        new_id = store.generate_id()
        store.save(new_id, [], user_id=uid)
        active_res = {"agents": valid_agents, "agent": valid_agents[0]}
        store.set_extra(new_id, "active_resources", active_res)
        # Title
        title = body.get("title", "")
        if title:
            store.set_extra(new_id, "title", title)
        # Relay bindings
        relay_ids = body.get("relays", [])
        default_relay = body.get("default_relay", "")
        if relay_ids:
            from core.relay_bindings import link_relay, set_default_relay
            for rid in relay_ids:
                link_relay(new_id, rid)
            if default_relay and default_relay in relay_ids:
                set_default_relay(new_id, default_relay)
        flowfile.set_content(json.dumps({
            "conversation_id": new_id,
            "agents": valid_agents,
        }).encode())
        return [flowfile]

    return None
