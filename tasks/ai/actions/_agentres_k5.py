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
        runtime_kind = str(body.get("runtime_kind") or "llm").strip()
        if not conv_id or not instance_name or not definition:
            flowfile.set_content(json.dumps({
                "error": "Missing conversation_id, instance_name, or definition",
            }).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        if runtime_kind not in {"llm", "external_mcp", "external_agui"}:
            flowfile.set_content(json.dumps({
                "error": "runtime_kind must be 'llm', 'external_mcp', or 'external_agui'",
            }).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        if runtime_kind == "llm" and not llm_service:
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
             skills=(body["skills"] if "skills" in body
                     else agent.get("assigned_skills", [])),
             flash_delegate_llm_service=body.get(
                 "flash_delegate_llm_service", "").strip(),
             runtime_kind=runtime_kind,
             agui_url=str(body.get("agui_url") or "").strip(),
             agui_service=str(body.get("agui_service") or "").strip(),
             agui_timeout=max(1, int(body.get("agui_timeout") or 300)),
             agui_max_tool_rounds=max(
                 0, min(32, int(
                     body.get("agui_max_tool_rounds")
                     if body.get("agui_max_tool_rounds") not in (None, "")
                     else 8))),
             gating_service=body.get("gating_service") or "")
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
        # the UI calls `list_services` with the `llm` capability filter.
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
        from core.conv_agent_config import (
            get_all_agent_configs, set_agent_config,
        )
        configs = get_all_agent_configs(conv_id)
        if aname not in configs:
            flowfile.set_content(json.dumps({"error": f"Agent '{aname}' not in conversation"}).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        runtime_kind = str(
            cfg.get("runtime_kind")
            or configs[aname].get("runtime_kind")
            or "llm")
        if runtime_kind not in {"llm", "external_mcp", "external_agui"}:
            flowfile.set_content(json.dumps({
                "error": "runtime_kind must be 'llm', 'external_mcp', or 'external_agui'",
            }).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        effective_llm = cfg.get(
            "llm_service", configs[aname].get("llm_service", ""))
        if runtime_kind == "llm" and not effective_llm:
            flowfile.set_content(json.dumps({"error": "llm_service is required"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        # Merge only runtime fields. Skill assignment changes use the dedicated
        # assign/unassign actions so notifications and locking stay consistent.
        _allowed = {"llm_service", "model", "tools", "max_depth", "params",
                    "realtime_voice_service", "flash_delegate_llm_service",
                    "runtime_kind", "agui_url", "agui_service", "agui_timeout",
                    "agui_max_tool_rounds", "gating_service"}
        if cfg.get("gating_service"):
            from core.gating_bindings import _normalize_ref, validate_binding
            _gref = _normalize_ref(cfg.get("gating_service"))
            try:
                validate_binding(_gref.get("scope") or "", _gref.get("service_id", ""),
                                 user_id, conv_id)
            except ValueError as exc:
                flowfile.set_content(json.dumps({"error": str(exc)}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
        effective_url = cfg.get("agui_url", configs[aname].get("agui_url", ""))
        effective_service = cfg.get(
            "agui_service", configs[aname].get("agui_service", ""))
        if (runtime_kind == "external_agui" and not str(effective_url).strip()
                and not str(effective_service).strip()):
            flowfile.set_content(json.dumps({
                "error": "agui_url or agui_service is required"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
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
