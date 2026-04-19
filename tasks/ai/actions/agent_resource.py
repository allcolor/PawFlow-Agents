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
        llm_service = body.get("llm_service", "")
        if not agent or not prompt:
            flowfile.set_content(json.dumps({"error": "Missing name or prompt"}).encode())
            return [flowfile]
        agent_data = {"prompt": prompt}
        if body.get("description"):
            agent_data["description"] = body["description"]
        from core.resource_store import ResourceStore
        rs = ResourceStore.instance()
        if scope == "conversation" and conv_id:
            rs.create("agent", agent, user_id, agent_data,
                      conversation_id=conv_id)
            from core.conv_agent_config import add_agent_to_conv
            add_agent_to_conv(conv_id, agent,
                             llm_service=llm_service, definition=agent)
        else:
            rs.create("agent", agent, user_id, agent_data)
            if conv_id:
                from core.conv_agent_config import add_agent_to_conv
                add_agent_to_conv(conv_id, agent,
                                 llm_service=llm_service, definition=agent)
        flowfile.set_content(json.dumps({
            "result": f"Agent '{agent}' created (scope: {scope})."
        }).encode())
        return [flowfile]

    if action == "list_agents":
        conv_id = body.get("conversation_id", "")
        from core.resource_store import ResourceStore
        uid = user_id
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
        # Check agent is an instance in conv_agents (not just in the repo)
        from core.conv_agent_config import get_all_agent_configs, get_agent_config
        _conv_cfgs = get_all_agent_configs(conv_id)
        _found = agent_name in _conv_cfgs or any(
            isinstance(_k, str) and _k.lower() == agent_name.lower()
            for _k in _conv_cfgs
        )
        if not _found:
            flowfile.set_content(json.dumps({
                "error": f"Agent '{agent_name}' is not in this conversation.",
            }).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        _acfg = get_agent_config(conv_id, agent_name)
        if not _acfg.get("llm_service"):
            flowfile.set_content(json.dumps({
                "error": f"Agent '{agent_name}' has no llm_service in this "
                         f"conversation. Add it via add_agent_to_conv with "
                         f"an explicit llm_service first.",
            }).encode())
            flowfile.set_attribute("http.response.status", "400")
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
        uid = user_id
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
        uid = user_id
        try:
            data = {"prompt": skill_prompt}
            description = body.get("description", "")
            if description:
                data["description"] = description
            if rs.exists("skill", skill_name, uid):
                rs.update("skill", skill_name, uid, data)
            else:
                rs.create("skill", skill_name, uid, data)
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
        uid = user_id
        deleted = ResourceStore.instance().delete("skill", skill_name, uid)
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
        uid = user_id
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
        # Invalidate this agent's Claude Code session so the new skill is injected
        conv_id = body.get("conversation_id", "")
        if conv_id:
            store.set_extra(conv_id, f"claude_session:{agent_name}", "")
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
        uid = user_id
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
        # Invalidate this agent's Claude Code session so the removed skill takes effect
        conv_id = body.get("conversation_id", "")
        if conv_id:
            store.set_extra(conv_id, f"claude_session:{agent_name}", "")
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
        uid = user_id
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
        uid = user_id
        skills = ResourceStore.instance().list_all("skill", uid)
        flowfile.set_content(json.dumps({
            "skills": [{
                "name": s["name"],
                "description": s.get("description", ""),
                "prompt": s.get("prompt", "")[:80],
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

    if action == "reload_disk":
        # Force reload services and deployments from disk (after manual file edits)
        reloaded = []
        try:
            from core.service_registry import ServiceRegistry
            reg = ServiceRegistry.get_instance()
            reg.reload_scope("global")
            if user_id and user_id != "anonymous":
                reg.reload_scope("user", user_id)
            reloaded.append("services")
        except Exception:
            pass
        try:
            from core.deployment_registry import DeploymentRegistry
            DeploymentRegistry.get_instance().reload()
            reloaded.append("deployments")
        except Exception:
            pass
        flowfile.set_content(json.dumps(
            {"ok": True, "reloaded": reloaded}).encode())
        return [flowfile]

    if action == "list_resources":
        # List all resource types for the user
        conv_id = body.get("conversation_id", "")
        from core.resource_store import ResourceStore
        rs = ResourceStore.instance()
        uid = user_id
        active = {}
        if conv_id:
            active = store.get_extra(conv_id, "active_resources") or {}
            active = self._ensure_active_agent(conv_id, active, uid)
        # conv_agents is the source of truth for agent membership
        from core.conv_agent_config import get_all_agent_configs, get_agent_config
        conv_agent_cfgs = get_all_agent_configs(conv_id) if conv_id else {}
        conv_agent_names = list(conv_agent_cfgs.keys())

        # Per-agent context-window usage (persisted by agent_core on each
        # final message_meta) — keyed by agent instance name.
        context_usage_map = store.get_extra(conv_id, "context_usage") or {} if conv_id else {}

        agents_out = []
        for aname in conv_agent_names:
            a = rs.get_any("agent", aname, uid)
            if not a:
                a = {"name": aname, "description": "", "_scope": ""}
            acfg = conv_agent_cfgs.get(aname, {})
            entry = {
                "name": aname,
                "description": a.get("description", ""),
                "scope": a.get("_scope", ""),
                "active": active.get("agent") == aname,
                "llm_service": acfg.get("llm_service", ""),
                "assigned_skills": acfg.get("skills") or [],
            }
            _cu = context_usage_map.get(aname)
            if _cu:
                entry["context_usage"] = _cu
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

        # Skills: show all from repo, mark assigned_to
        all_skills = rs.list_all("skill", uid, conversation_id=conv_id)
        skills_out = []
        for s in all_skills:
            sname = s["name"]
            assigned_to = [aname for aname, acfg in conv_agent_cfgs.items()
                           if sname in (acfg.get("skills") or [])]
            skills_out.append({
                "name": sname,
                "description": s.get("description", ""),
                "scope": s.get("_scope", ""),
                "assigned_to": assigned_to,
            })

        # MCPs: all in-scope (global + user + conv) are auto-active.
        # No "linked" flag — presence in the repo == available in the conv.
        all_mcps = rs.list_all("mcp", uid, conversation_id=conv_id)
        mcps_out = [{
            "name": m["name"],
            "url": m.get("url", ""),
            "scope": m.get("_scope", ""),
        } for m in all_mcps]

        # Tasks: show all from repo
        all_task_defs = rs.list_all("task_def", uid, conversation_id=conv_id)
        tasks_out = [{
            "name": t["name"],
            "description": t.get("description", "") or t.get("prompt", "")[:60],
            "scope": t.get("_scope", ""),
            "default_interval": t.get("default_interval", "6/1m"),
        } for t in all_task_defs]

        # Prompts: all from repo (no link/unlink — click to use)
        all_prompts = rs.list_all("prompt", uid, conversation_id=conv_id)
        prompts_out = [{
            "name": p["name"],
            "title": p.get("title", ""),
            "category": p.get("category", ""),
            "description": p.get("description", ""),
            "scope": p.get("_scope", ""),
            "has_parameters": bool(p.get("parameters")),
        } for p in all_prompts]

        # Voice clones: user-scope registered voices (voice_clone_cache)
        voices_out = []
        try:
            from core import voice_clone_cache as _vcache
            for v in _vcache.list_for_user(uid):
                voices_out.append({
                    "name": v.get("name", ""),
                    "provider": v.get("provider", ""),
                    "paradigm": "voice_id" if v.get("voice_id") else "zero-shot",
                    "language": v.get("language", ""),
                    "ref_audio_fid": v.get("ref_audio_fid", ""),
                    "ref_audio_filename": v.get("ref_audio_filename", ""),
                    "ref_audio_content_type":
                        v.get("ref_audio_content_type", ""),
                    "created_at": v.get("created_at", 0),
                    "last_used_at": v.get("last_used_at", 0),
                })
        except Exception as e:
            logger.debug("list_resources: voice_clones failed: %s", e)

        result = {
            "agents": agents_out,
            "repo_agent_count": repo_agent_count,
            "repo_agents": repo_agents_out,
            "skills": skills_out,
            "mcp_servers": mcps_out,
            "task_defs": tasks_out,
            "prompts": prompts_out,
            "voices": voices_out,
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
                    "iterations": t.get("reschedule_count", 0),
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
        # Services are NOT embedded here — UI calls `list_services` directly.
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
                    from core.service_registry import ServiceRegistry
                    _greg2 = ServiceRegistry.get_instance()
                    _ureg2 = None
                    try:
                        from core.service_registry import ServiceRegistry
                        _ureg2 = ServiceRegistry.get_instance()
                    except Exception:
                        pass
                    for _rid in _all_ids:
                        _rsvc = None
                        _connected = False
                        # Same logic as service list: use registry.is_connected
                        try:
                            _connected = _greg2.is_connected("global", "", _rid)
                            _rsvc = _greg2.get_live_instance("global", "", _rid)
                        except Exception:
                            pass
                        if not _rsvc and _ureg2 and user_id:
                            try:
                                _connected = _ureg2.is_connected("user", user_id, _rid)
                                _rsvc = _ureg2.get_live_instance("user", user_id, _rid)
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
            from core.deployment_registry import DeploymentRegistry
            flows = []
            dr = DeploymentRegistry.get_instance()
            # sync_with_executors removed from request path — too expensive.
            # DeploymentRegistry syncs on its own schedule.
            uid = user_id
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
        # Flow templates (Flows Repository on disk under data/repository/flows/).
        # Walks global + caller's user scope; each flow lives at
        # <scope>/<package>/<flow_name>/{latest.json, versions/X.Y.Z.json}.
        try:
            from core.paths import REPOSITORY_DIR
            templates = []
            roots = [("global", REPOSITORY_DIR / "flows" / "global")]
            if user_id:
                roots.append(("user",
                              REPOSITORY_DIR / "flows" / "users" / user_id))
            for scope_label, root in roots:
                if not root.is_dir():
                    continue
                for latest in root.rglob("latest.json"):
                    flow_dir = latest.parent
                    try:
                        ptr = json.loads(latest.read_text(encoding="utf-8"))
                        version = (ptr.get("version") or "").strip()
                        if not version:
                            continue
                        vfile = flow_dir / "versions" / f"{version}.json"
                        if not vfile.is_file():
                            continue
                        raw = json.loads(vfile.read_text(encoding="utf-8"))
                        templates.append({
                            "id": raw.get("id") or flow_dir.name,
                            "name": raw.get("name") or flow_dir.name,
                            "version": version,
                            "description": raw.get("description") or "",
                            "scope": raw.get("scope") or scope_label,
                            "tasks_count": len(raw.get("tasks", {}) or {}),
                            "services_count": len(raw.get("services", {}) or {}),
                        })
                    except Exception as _e:
                        logger.debug("list_resources flow_templates: skip %s: %s",
                                     latest, _e)
            templates.sort(key=lambda t: (t["scope"], t["name"]))
            result["flow_templates"] = templates
        except Exception as e:
            logger.debug("list_resources flow_templates failed: %s", e)
            result["flow_templates"] = []
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
        uid = user_id
        conv_id = body.get("conversation_id", "")
        item = rs.get_any(rtype, rname, uid, conversation_id=conv_id)
        if not item:
            flowfile.set_content(json.dumps({"error": f"{rtype} '{rname}' not found"}).encode())
            return [flowfile]
        flowfile.set_content(json.dumps(item, ensure_ascii=False).encode())
        return [flowfile]

    if action == "delete_voice_clone":
        # Cascade-delete a registered voice clone from the user scope:
        # purges provider voice_id (paradigm A), ref audio, cached TTS
        # files, and the repository entry itself.
        vname = body.get("name", "").strip()
        if not vname:
            flowfile.set_content(json.dumps({"error": "Missing name"}).encode())
            return [flowfile]
        from core import voice_clone_cache as _vcache
        entry = _vcache.get_by_name(user_id, vname)
        if entry is None:
            flowfile.set_content(json.dumps({"error": f"voice clone {vname!r} not found"}).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        # Try to resolve a service of the same provider TYPE so we can
        # free the upstream voice_id quota (paradigm A). If the provider
        # service is not deployed, cascade_delete still removes local
        # state with service=None.
        svc = None
        provider = entry.get("provider") or ""
        if provider:
            try:
                from core.service_registry import ServiceRegistry
                reg = ServiceRegistry.get_instance()
                for sdef in reg.resolve_by_type(provider, user_id=user_id):
                    svc = reg.resolve(sdef.service_id, user_id=user_id)
                    if svc is not None:
                        break
            except Exception as e:
                logger.debug("delete_voice_clone: service resolve: %s", e)
        outcome = _vcache.cascade_delete(user_id, vname, svc)
        flowfile.set_content(json.dumps({
            "ok": bool(outcome.get("entry")),
            "name": vname,
            "provider": provider,
            "voice_id_deleted": bool(outcome.get("voice_id")),
            "ref_audio_deleted": bool(outcome.get("ref_audio")),
            "tts_cached_purged": int(outcome.get("tts_cached", 0)),
        }).encode())
        return [flowfile]

    if action == "rename_voice_clone":
        # Rename a voice clone entry in place. provider voice_id,
        # ref_audio_fid and cache tags are unchanged — only the
        # user-visible identifier changes.
        old = body.get("name", "").strip()
        new = body.get("new_name", "").strip()
        if not old or not new:
            flowfile.set_content(json.dumps({
                "error": "Missing name or new_name",
            }).encode())
            return [flowfile]
        from core import voice_clone_cache as _vcache
        new_safe = _vcache.safe_name(new)
        entry = _vcache.get_by_name(user_id, old)
        if entry is None:
            flowfile.set_content(json.dumps({
                "error": f"voice clone {old!r} not found",
            }).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        if new_safe == entry.get("name"):
            flowfile.set_content(json.dumps({
                "ok": True, "name": new_safe, "unchanged": True,
            }).encode())
            return [flowfile]
        if _vcache.get_by_name(user_id, new_safe) is not None:
            flowfile.set_content(json.dumps({
                "error": f"voice clone {new_safe!r} already exists",
            }).encode())
            flowfile.set_attribute("http.response.status", "409")
            return [flowfile]
        renamed = dict(entry)
        renamed["name"] = new_safe
        renamed.pop("created_at", None)
        _vcache.save(user_id, renamed)
        _vcache.delete(user_id, old)
        flowfile.set_content(json.dumps({
            "ok": True, "name": new_safe, "previous_name": old,
        }).encode())
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
        # Agent definitions in the repository hold only prompt + description.
        # Runtime params (llm_service, model, tools, skills, max_depth, timeout)
        # live in conv_agents — edited via update_agent_conv_config.
        if rtype == "agent":
            data = {k: v for k, v in data.items() if k in ("prompt", "description")}
        from core.resource_store import ResourceStore
        rs = ResourceStore.instance()
        uid = user_id
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
        if rtype == "agent":
            data = {k: v for k, v in data.items() if k in ("prompt", "description")}
        from core.resource_store import ResourceStore
        rs = ResourceStore.instance()
        uid = user_id
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
        uid = user_id
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
        uid = user_id
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
        elif rtype == "mcp":
            mcps = active.get("mcps", [])
            if rname not in mcps:
                mcps.append(rname)
            active["mcps"] = mcps
        else:
            flowfile.set_content(json.dumps({
                "error": f"Cannot activate resource type '{rtype}' on a conversation",
            }).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
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
        elif rtype == "mcp":
            mcps = active.get("mcps", [])
            if rname in mcps:
                mcps.remove(rname)
            active["mcps"] = mcps
        else:
            flowfile.set_content(json.dumps({
                "error": f"Cannot deactivate resource type '{rtype}' from a conversation",
            }).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
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
        elif rtype == "mcp":
            mcps = active.get("mcps", [])
            if rname not in mcps:
                mcps.append(rname)
            active["mcps"] = mcps
        else:
            flowfile.set_content(json.dumps({
                "error": f"Cannot share resource type '{rtype}' to a conversation",
            }).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        store.set_extra(target_conv, "active_resources", active)
        flowfile.set_content(json.dumps({
            "shared": True, "type": rtype, "name": rname,
            "target": target_conv,
        }).encode())
        return [flowfile]

    if action == "add_agent_to_conv":
        conv_id = body.get("conversation_id", "")
        # instance_name = the name in the conv (user-chosen)
        # definition = the repo template name
        # If only "name" is given (legacy), it's both instance_name and definition
        instance_name = (body.get("instance_name") or body.get("name", "")).strip()
        definition = (body.get("definition") or instance_name).strip()
        inst_params = body.get("params") or {}
        llm_service = body.get("llm_service", "").strip()
        if not conv_id or not instance_name:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id or instance_name"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        if not llm_service:
            flowfile.set_content(json.dumps({
                "error": "llm_service is required when adding an agent to a conversation",
            }).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        from core.resource_store import ResourceStore
        uid = user_id
        agent = ResourceStore.instance().get_any("agent", definition, uid)
        if not agent:
            flowfile.set_content(json.dumps({"error": f"Definition '{definition}' not found in repository"}).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        from core.conv_agent_config import add_agent_to_conv as _add
        _add(conv_id, instance_name,
             llm_service=llm_service,
             definition=definition,
             params=inst_params,
             model=body.get("model", ""),
             tools=body.get("tools", []),
             max_depth=int(body.get("max_depth", 1000) or 1000),
             skills=body.get("skills", []))
        active = store.get_extra(conv_id, "active_resources") or {}
        if not active.get("agent"):
            active["agent"] = instance_name
        store.set_extra(conv_id, "active_resources", active)
        flowfile.set_content(json.dumps({
            "ok": True, "agent": instance_name, "definition": definition,
        }).encode())
        return [flowfile]

    if action == "get_agent_conv_config":
        conv_id = body.get("conversation_id", "")
        aname = body.get("name", "").strip()
        if not conv_id or not aname:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id or name"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        from core.conv_agent_config import get_all_agent_configs
        cfgs = get_all_agent_configs(conv_id)
        if aname not in cfgs:
            flowfile.set_content(json.dumps({"error": f"Agent '{aname}' not in conversation"}).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        # Include definition's parameters schema. For the LLM service dropdown,
        # the UI calls `list_services` with service_type='llmConnection' directly.
        _cfg = cfgs[aname]
        _def_params_schema = {}
        _def_name = _cfg.get("definition", "")
        if _def_name:
            from core.resource_store import ResourceStore as _RS
            _adef = _RS.instance().get_any("agent", _def_name, user_id)
            if _adef and _adef.get("parameters"):
                _def_params_schema = _adef["parameters"]
        flowfile.set_content(json.dumps({
            "name": aname,
            "config": _cfg,
            "parameters_schema": _def_params_schema,
        }, ensure_ascii=False).encode())
        return [flowfile]

    if action == "update_agent_conv_config":
        conv_id = body.get("conversation_id", "")
        aname = body.get("name", "").strip()
        cfg = body.get("config", {}) or {}
        if not conv_id or not aname:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id or name"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        if not cfg.get("llm_service"):
            flowfile.set_content(json.dumps({"error": "llm_service is required"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        from core.conv_agent_config import (
            get_all_agent_configs, set_agent_config,
        )
        configs = get_all_agent_configs(conv_id)
        if aname not in configs:
            flowfile.set_content(json.dumps({"error": f"Agent '{aname}' not in conversation"}).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        # Merge — only update known fields
        _allowed = {"llm_service", "model", "tools", "max_depth", "skills", "params"}
        merged = dict(configs[aname])
        for k, v in cfg.items():
            if k in _allowed:
                merged[k] = v
        set_agent_config(conv_id, aname, merged)
        flowfile.set_content(json.dumps({"ok": True}).encode())
        return [flowfile]

    if action == "remove_agent_from_conv":
        conv_id = body.get("conversation_id", "")
        aname = body.get("name", "").strip()
        if not conv_id or not aname:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id or name"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        from core.conv_agent_config import (
            get_all_agent_configs, CONV_AGENTS_KEY,
        )
        configs = get_all_agent_configs(conv_id)
        configs.pop(aname, None)
        store.set_extra(conv_id, CONV_AGENTS_KEY, configs)
        remaining = list(configs.keys())
        active = store.get_extra(conv_id, "active_resources") or {}
        # If removed agent was the selected primary, pick next or clear
        if active.get("agent") == aname:
            active["agent"] = remaining[0] if remaining else ""
        store.set_extra(conv_id, "active_resources", active)
        flowfile.set_content(json.dumps({"ok": True}).encode())
        return [flowfile]

    if action == "list_repo_agents":
        conv_id = body.get("conversation_id", "")
        from core.resource_store import ResourceStore
        from core.conv_agent_config import get_all_agent_configs
        uid = user_id
        rs = ResourceStore.instance()
        all_agents = rs.list_all("agent", uid)
        conv_cfgs = get_all_agent_configs(conv_id) if conv_id else {}
        # Build set of definitions currently in the conv
        _conv_defs = set()
        for _iname, _icfg in conv_cfgs.items():
            _conv_defs.add(_icfg.get("definition", _iname))
            _conv_defs.add(_iname)  # legacy compat
        out = []
        for a in all_agents:
            entry = {
                "name": a["name"],
                "description": a.get("description", ""),
                "scope": a.get("_scope", ""),
                "in_conversation": a["name"] in _conv_defs,
            }
            # Include parameters schema if present in the definition
            if a.get("parameters"):
                entry["parameters"] = a["parameters"]
            out.append(entry)
        flowfile.set_content(json.dumps({
            "agents": out,
        }, ensure_ascii=False).encode())
        return [flowfile]

    if action == "create_conversation":
        agents = body.get("agents", [])
        if not agents or not isinstance(agents, list):
            flowfile.set_content(json.dumps({"error": "'agents' list is required"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        from core.resource_store import ResourceStore
        uid = user_id
        rs = ResourceStore.instance()
        from core.conv_agent_config import add_agent_to_conv
        _agent_entries = []
        for item in agents:
            if not isinstance(item, dict):
                continue
            iname = item.get("instance_name") or item.get("name", "")
            _agent_entries.append({
                "instance_name": iname,
                "definition": item.get("definition", iname),
                "params": item.get("params") or {},
                "llm_service": item.get("llm_service", ""),
                "model": item.get("model", ""),
                "tools": item.get("tools"),
                "max_depth": int(item.get("max_depth", 1000)),
                "skills": item.get("skills"),
            })
        # Validate definitions exist in repo
        valid_entries = []
        for entry in _agent_entries:
            if rs.get_any("agent", entry["definition"], uid):
                valid_entries.append(entry)
        if not valid_entries:
            flowfile.set_content(json.dumps({"error": "None of the specified agent definitions exist in the repository"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        new_id = store.generate_id()
        store.save(new_id, [], user_id=uid)
        _instance_names = [e["instance_name"] for e in valid_entries]
        active_res = {"agents": _instance_names, "agent": _instance_names[0]}
        store.set_extra(new_id, "active_resources", active_res)
        for entry in valid_entries:
            add_agent_to_conv(
                new_id, entry["instance_name"],
                llm_service=entry["llm_service"],
                definition=entry["definition"],
                params=entry["params"],
                model=entry["model"],
                tools=entry["tools"],
                max_depth=entry["max_depth"],
                skills=entry["skills"],
            )
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
            "agents": _instance_names,
        }).encode())
        return [flowfile]

    return None
