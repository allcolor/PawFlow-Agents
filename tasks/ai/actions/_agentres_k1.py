"""AgentLoopTask actions — agent resource"""

import json
import logging
from tasks.ai.actions._agentres_base import (
    _UNHANDLED,
    _decode_skill_package_files,
)

logger = logging.getLogger(__name__)


def _handle_agentres_k1(self, action, body, store, user_id, flowfile):
    """agent_resource cluster _agentres_k1. Returns result or _UNHANDLED."""
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
        if scope == "global" and "admin" not in (flowfile.get_attribute("http.auth.roles") or ""):
            flowfile.set_content(json.dumps({"error": "Requires admin role for global scope"}).encode())
            flowfile.set_attribute("http.response.status", "403")
            return [flowfile]
        if scope == "conversation" and not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id for conversation scope"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        llm_service = body.get("llm_service", "")
        if not agent or not prompt:
            flowfile.set_content(json.dumps({"error": "Missing name or prompt"}).encode())
            return [flowfile]
        agent_data = {"prompt": prompt}
        if body.get("description"):
            agent_data["description"] = body["description"]
        from core.resource_store import ResourceStore
        rs = ResourceStore.instance()
        # Admin may create on behalf of another owner. Default = caller.
        from core import admin_scope
        try:
            _owner_user, _owner_conv = admin_scope.effective_owner(
                body, user_id, conv_id, flowfile, scope)
        except PermissionError as _pe:
            flowfile.set_content(json.dumps({"error": str(_pe)}).encode())
            flowfile.set_attribute("http.response.status", "403")
            return [flowfile]
        except ValueError as _ve:
            flowfile.set_content(json.dumps({"error": str(_ve)}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        owner_uid = _owner_user or user_id
        if scope == "conversation":
            conv_id = _owner_conv or conv_id
        if scope == "conversation" and conv_id:
            rs.create("agent", agent, owner_uid, agent_data,
                      conversation_id=conv_id)
            from core.conv_agent_config import add_agent_to_conv
            add_agent_to_conv(conv_id, agent,
                             llm_service=llm_service, definition=agent)
        else:
            from core.resource_store import GLOBAL_USER_ID
            target_uid = GLOBAL_USER_ID if scope == "global" else owner_uid
            rs.create("agent", agent, target_uid, agent_data)
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
        # The user/conv side of a promote/demote belongs to an owner. When an
        # admin acts from the global view, that owner is given via
        # target_user_id / target_conversation_id ("which user to demote to").
        # Default = caller. Used both to LOCATE the source and to WRITE the
        # destination so the resource lands on the right user.
        from core import admin_scope
        _owner_scope = "conv" if target_scope == "conversation" else "user"
        try:
            owner_user, owner_conv = admin_scope.effective_owner(
                body, user_id, conv_id, flowfile, _owner_scope)
        except PermissionError as _pe:
            flowfile.set_content(json.dumps({"error": str(_pe)}).encode())
            flowfile.set_attribute("http.response.status", "403")
            return [flowfile]
        except ValueError as _ve:
            flowfile.set_content(json.dumps({"error": str(_ve)}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        owner_user = owner_user or user_id
        _read_conv = owner_conv if target_scope == "conversation" else conv_id
        item = rs.get_any("agent", agent, owner_user, conversation_id=_read_conv)
        if not item:
            flowfile.set_content(json.dumps({"error": f"Agent '{agent}' not found"}).encode())
            return [flowfile]
        current_scope = item.get("_scope", "user")
        if (target_scope == "global" or current_scope == "global") and "admin" not in (flowfile.get_attribute("http.auth.roles") or ""):
            flowfile.set_content(json.dumps({"error": "Requires admin role for global scope"}).encode())
            flowfile.set_attribute("http.response.status", "403")
            return [flowfile]
        promote_data = {k: v for k, v in item.items() if not k.startswith("_") and k != "name"}
        if target_scope == "user":
            rs.create("agent", agent, owner_user, promote_data)
        elif target_scope == "global":
            rs.create("agent", agent, GLOBAL_USER_ID, promote_data)
        elif target_scope == "conversation" and owner_conv:
            conv_agents = store.get_extra(owner_conv, "conversation_agents") or {}
            conv_agents[agent] = promote_data
            store.set_extra(owner_conv, "conversation_agents", conv_agents)
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
        # Route the delete to the scope the definition actually lives in
        # (same pattern as delete_skill): conv-scoped agents need the
        # conversation_id, global ones need admin + __global__.
        _rs = ResourceStore.instance()
        _adef = _rs.get_any("agent", agent_name, uid, conversation_id=conv_id)
        _scope = (_adef or {}).get("_scope", "user")
        if _scope == "global" and "admin" not in (flowfile.get_attribute("http.auth.roles") or ""):
            flowfile.set_content(json.dumps({"error": "Requires admin role for global scope"}).encode())
            flowfile.set_attribute("http.response.status", "403")
            return [flowfile]
        _del_kwargs = {"conversation_id": conv_id} if _scope == "conversation" and conv_id else {}
        _del_uid = uid if _scope in ("conversation", "user") else "__global__"
        deleted = _rs.delete("agent", agent_name, _del_uid, **_del_kwargs)
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

    if action in ("create_skill", "add_skill", "update_skill", "modify_skill"):
        skill_name = body.get("name", "").strip()
        skill_instructions = body.get("instructions", "").strip()
        description = body.get("description", "").strip()
        conv_id = body.get("conversation_id", "")
        is_update = action in ("update_skill", "modify_skill")
        if not skill_name or (not is_update and (not skill_instructions or not description)):
            flowfile.set_content(json.dumps({
                "error": "Missing name, description, or instructions",
            }).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        if is_update and not skill_instructions and not description:
            flowfile.set_content(json.dumps({
                "error": "Missing description or instructions",
            }).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        scope = body.get("scope", "user")
        if scope == "global" and "admin" not in (flowfile.get_attribute("http.auth.roles") or ""):
            flowfile.set_content(json.dumps({"error": "Requires admin role for global scope"}).encode())
            flowfile.set_attribute("http.response.status", "403")
            return [flowfile]
        if scope == "conversation" and not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id for conversation scope"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        from core.resource_store import GLOBAL_USER_ID, ResourceStore
        rs = ResourceStore.instance()
        # Admin may create/edit on behalf of another owner. Default = caller.
        from core import admin_scope
        try:
            _owner_user, _owner_conv = admin_scope.effective_owner(
                body, user_id, conv_id, flowfile, scope)
        except PermissionError as _pe:
            flowfile.set_content(json.dumps({"error": str(_pe)}).encode())
            flowfile.set_attribute("http.response.status", "403")
            return [flowfile]
        except ValueError as _ve:
            flowfile.set_content(json.dumps({"error": str(_ve)}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        uid = _owner_user or user_id
        if scope == "conversation":
            conv_id = _owner_conv or conv_id
        target_uid = GLOBAL_USER_ID if scope == "global" else uid
        try:
            data = {}
            if skill_instructions:
                data["instructions"] = skill_instructions
            if description:
                data["description"] = description
            # Optional Agent Skills frontmatter fields, when supplied.
            for _opt in ("allowed-tools", "license", "metadata"):
                if body.get(_opt) not in (None, "", [], {}):
                    data[_opt] = body.get(_opt)
            # Bundled assets (scripts/, references/, assets/...) arrive
            # base64-encoded so binary files survive the JSON transport.
            _pkg_files = _decode_skill_package_files(body.get("package_files"))
            if _pkg_files:
                data["package_files"] = _pkg_files
            from core.review_bindings import attach_review_metadata, review_for_write
            from core.package_review import ReviewBlocked
            _review_subject = {k: v for k, v in data.items()
                               if k != "package_files"}
            try:
                review_meta = review_for_write(
                    _review_subject,
                    operation="update" if is_update else "create",
                    user_id=target_uid,
                    conversation_id=conv_id if scope == "conversation" else "",
                    package_files=_pkg_files,
                    force=bool(body.get("force", False)),
                )
            except ReviewBlocked as _rb:
                # The user has the final word: surface the findings and let
                # the UI offer a rerun with force.
                flowfile.set_content(json.dumps({
                    "requires_confirmation": True,
                    "name": skill_name,
                    "review": _rb.review,
                    "message": str(_rb),
                }, ensure_ascii=False).encode())
                return [flowfile]
            if review_meta:
                data = attach_review_metadata(data, review_meta)
            scope_kwargs = {"conversation_id": conv_id} if scope == "conversation" and conv_id else {}
            exists = rs.get("skill", skill_name, target_uid, **scope_kwargs)
            if is_update:
                if not exists:
                    flowfile.set_content(json.dumps({
                        "error": f"Skill '{skill_name}' not found in {scope} scope",
                    }).encode())
                    flowfile.set_attribute("http.response.status", "404")
                    return [flowfile]
                rs.update("skill", skill_name, target_uid, data, **scope_kwargs)
                if conv_id:
                    from core.skill_lifecycle import notify_skill_updated
                    updated_def = rs.get_any(
                        "skill", skill_name, uid, conversation_id=conv_id) or data
                    notify_skill_updated(
                        skill_name, updated_def, uid, conv_id,
                        resource_store=rs, conversation_store=store)
            else:
                if exists:
                    flowfile.set_content(json.dumps({
                        "error": f"Skill '{skill_name}' already exists in {scope} scope. Use /skill update to modify it.",
                    }).encode())
                    flowfile.set_attribute("http.response.status", "409")
                    return [flowfile]
                rs.create("skill", skill_name, target_uid, data, **scope_kwargs)
            flowfile.set_content(json.dumps({
                "created": not is_update, "updated": is_update,
                "name": skill_name, "scope": scope,
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
        rs = ResourceStore.instance()
        skill_def = rs.get_any(
            "skill", skill_name, uid, conversation_id=conv_id)
        if not skill_def:
            flowfile.set_content(json.dumps({
                "deleted": False, "name": skill_name,
                "error": f"Skill '{skill_name}' not found",
            }).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        scope = skill_def.get("_scope", "user")
        if scope == "global" and "admin" not in (flowfile.get_attribute("http.auth.roles") or ""):
            flowfile.set_content(json.dumps({"error": "Requires admin role for global scope"}).encode())
            flowfile.set_attribute("http.response.status", "403")
            return [flowfile]
        delete_kwargs = {"conversation_id": conv_id} if scope == "conversation" and conv_id else {}
        delete_uid = uid if scope in ("conversation", "user") else "__global__"
        deleted = rs.delete("skill", skill_name, delete_uid, **delete_kwargs)
        cleaned_agents = []
        if deleted:
            from core.skill_lifecycle import remove_skill_assignments
            cleaned_agents = remove_skill_assignments(
                skill_name, uid, conv_id, resource_store=rs,
                conversation_store=store, source="skill_delete")
        flowfile.set_content(json.dumps({
            "deleted": deleted, "name": skill_name,
            "cleaned_agents": cleaned_agents,
        }).encode())
        return [flowfile]

    return _UNHANDLED
