"""AgentLoopTask actions — agent resource"""

import json
import logging
from tasks.ai.actions._agentres_base import (
    _UNHANDLED,
)

logger = logging.getLogger(__name__)


def _handle_agentres_k5(self, action, body, store, user_id, flowfile):
    """agent_resource cluster _agentres_k5. Returns result or _UNHANDLED."""
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
        instance_name = body.get("instance_name", "").strip()
        definition = body.get("definition", "").strip()
        inst_params = body.get("params") or {}
        llm_service = body.get("llm_service", "").strip()
        if not conv_id or not instance_name or not definition:
            flowfile.set_content(json.dumps({
                "error": "Missing conversation_id, instance_name, or definition",
            }).encode())
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
        agent = ResourceStore.instance().get_any(
            "agent", definition, uid, conversation_id=conv_id)
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
            _adef = _RS.instance().get_any(
                "agent", _def_name, user_id, conversation_id=conv_id)
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
        # Merge — only update known runtime fields. Skills live on the agent
        # definition (`assigned_skills`), not in conv_agent_config.
        _allowed = {"llm_service", "model", "tools", "max_depth", "params",
                    "realtime_voice_service"}
        merged = dict(configs[aname])
        for k, v in cfg.items():
            if k in _allowed:
                merged[k] = v
        set_agent_config(conv_id, aname, merged)
        if "skills" in cfg:
            from core.resource_store import ResourceStore
            from core.skill_resolver import normalize_skill_entry
            _def_name = merged.get("definition", aname)
            _skills = cfg.get("skills") or []
            _agent_def = ResourceStore.instance().get_any(
                "agent", _def_name, user_id, conversation_id=conv_id)
            _old_skills = [normalize_skill_entry(s)[0]
                           for s in ((_agent_def or {}).get("assigned_skills") or [])]
            _new_skills = [normalize_skill_entry(s)[0] for s in _skills]
            if _agent_def is not None:
                _scope = _agent_def.get("_scope", "user")
                _uid = user_id if _scope == "user" else "__global__"
                ResourceStore.instance().update(
                    "agent", _def_name, _uid, {"assigned_skills": list(_skills)})
            try:
                from core.llm_client import stamp_message
                from core.pending_queue import PendingQueue
                from core.skill_resolver import (
                    available_skill_context_message,
                    removed_skill_context_message,
                )
                for _skill in [s for s in _new_skills if s and s not in _old_skills]:
                    _skill_def = ResourceStore.instance().get_any(
                        "skill", _skill, user_id,
                        conversation_id=conv_id) or {}
                    _msg = stamp_message({
                        "role": "system",
                        "content": available_skill_context_message(_skill, _skill_def),
                        "source": {"type": "context", "name": "pawflow"},
                    }, conv_id)
                    store.append_message(conv_id, _msg, agent_name=aname,
                                         user_id=user_id)
                    PendingQueue.for_agent(conv_id, aname).enqueue(
                        dict(_msg), source="skill_config")
                for _skill in [s for s in _old_skills if s and s not in _new_skills]:
                    _msg = stamp_message({
                        "role": "system",
                        "content": removed_skill_context_message(_skill),
                        "source": {"type": "context", "name": "pawflow"},
                    }, conv_id)
                    store.append_message(conv_id, _msg, agent_name=aname,
                                         user_id=user_id)
                    PendingQueue.for_agent(conv_id, aname).enqueue(
                        dict(_msg), source="skill_config")
            except Exception:
                logger.debug("skill config context injection failed",
                             exc_info=True)
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
            _conv_defs.add(_icfg["definition"])
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
        try:
            from core.conversation_creation import create_conversation
            result = create_conversation(user_id, body)
        except ValueError as exc:
            flowfile.set_content(json.dumps({"error": str(exc)}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        flowfile.set_content(json.dumps(result).encode())
        return [flowfile]

    return _UNHANDLED
