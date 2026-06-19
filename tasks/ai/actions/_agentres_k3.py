"""AgentLoopTask actions — agent resource"""

import json
import logging
from tasks.ai.actions._agentres_base import (
    _UNHANDLED,
    _overlay_admin_view_all,
    _get_flow_templates_cached,
)

logger = logging.getLogger(__name__)


def _handle_agentres_k3(self, action, body, store, user_id, flowfile):
    """agent_resource cluster _agentres_k3. Returns result or _UNHANDLED."""
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
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        try:
            from core.deployment_registry import DeploymentRegistry
            DeploymentRegistry.get_instance().reload()
            reloaded.append("deployments")
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        flowfile.set_content(json.dumps(
            {"ok": True, "reloaded": reloaded}).encode())
        return [flowfile]

    if action == "list_resources":
        # List all resource types for the user
        conv_id = body.get("conversation_id", "")
        from core.resource_store import ResourceStore
        rs = ResourceStore.instance()
        from core import admin_scope
        # Admin cross-user view-all. Rather than early-returning a sparse
        # catalog (which blanked every non-catalog section — deployed flows,
        # relays, remote FS, summarizer, tasks, flow templates), build the
        # full self-view below and overlay the repo-backed catalogs with
        # cross-user rows at the end. The admin's own runtime/personal
        # sections stay populated; sensitive ones (secrets/variables) are
        # never enumerated cross-user.
        _view_all = admin_scope.wants_view_all(body, flowfile)
        uid = user_id
        if conv_id and hasattr(store, "get_extras_snapshot"):
            extras_snapshot = store.get_extras_snapshot(conv_id)
        else:
            extras_snapshot = {}
        active = {}
        if conv_id:
            active = extras_snapshot.get("active_resources") or {}
            active = self._ensure_active_agent(conv_id, active, uid)
        # conv_agents is the source of truth for agent membership
        from core.conv_agent_config import get_all_agent_configs
        conv_agent_cfgs = get_all_agent_configs(conv_id) if conv_id else {}
        conv_agent_names = list(conv_agent_cfgs.keys())

        # list_resources is a resource catalog/status endpoint. It must not
        # publish or hydrate the context gauge: the gauge has one live source
        # of truth (`message_meta` / compact events / explicit `/context`).

        all_agent_defs = rs.list_all("agent", uid, conversation_id=conv_id)
        agent_defs_by_name = {a.get("name"): a for a in all_agent_defs}
        assigned_by_skill = {}

        agents_out = []
        for aname in conv_agent_names:
            acfg = conv_agent_cfgs.get(aname, {})
            _def_name = acfg.get("definition") or aname
            a = agent_defs_by_name.get(_def_name) or agent_defs_by_name.get(aname)
            if not a:
                a = {"name": aname, "description": "", "_scope": ""}
            from core.skill_resolver import normalize_skill_entry
            assigned_names = []
            for raw_skill in a.get("assigned_skills") or []:
                skill_name, _params, _condition = normalize_skill_entry(raw_skill)
                if skill_name:
                    assigned_names.append(skill_name)
            entry = {
                "name": aname,
                "description": a.get("description", ""),
                "scope": a.get("_scope", ""),
                "active": active.get("agent") == aname,
                "llm_service": acfg.get("llm_service", ""),
                "assigned_skills": assigned_names,
            }
            for skill_name in assigned_names:
                assigned_by_skill.setdefault(skill_name, []).append(aname)
            if conv_id:
                ac_cfg = extras_snapshot.get(f"random_thought::{aname.lower()}") or {}
                if ac_cfg.get("enabled"):
                    entry["autoconv"] = ac_cfg.get("frequency", "on")
            agents_out.append(entry)

        # Repo agents list (all global+user agents, with in_conversation flag)
        all_repo_agents = rs.list_all("agent", uid, conversation_id=conv_id)
        repo_agent_count = len(all_repo_agents)
        repo_agents_out = [{
            "name": a["name"],
            "description": a.get("description", ""),
            "scope": a.get("_scope", ""),
            "in_conversation": a["name"] in set(conv_agent_names),
        } for a in all_repo_agents]

        # Skills: show all from repo, mark assigned_to from the precomputed
        # agent definitions map. No per-skill repository reads in UI refresh.
        all_skills = rs.list_all("skill", uid, conversation_id=conv_id)
        skills_out = []
        for s in all_skills:
            sname = s["name"]
            assigned_to = assigned_by_skill.get(sname, [])
            skills_out.append({
                "name": sname,
                "description": s.get("description", ""),
                "scope": s.get("_scope", ""),
                "assigned_to": assigned_to,
                "invalid": s.get("_invalid", ""),
            })

        try:
            from core.tool_mcp_filters import enabled_mcp_names as _enabled_mcp_names
            _enabled_mcps = _enabled_mcp_names(conv_id) if conv_id else set()
        except Exception:
            _enabled_mcps = set()

        # MCPs: all in-scope are visible; availability is controlled by
        # conversation/agent tool_mcp_filters.
        all_mcps = rs.list_all("mcp", uid, conversation_id=conv_id)
        mcps_out = [{
            "name": m["name"],
            "url": m.get("url", ""),
            "scope": m.get("_scope", ""),
            "enabled": m.get("name", "") in _enabled_mcps,
            "transport": m.get("transport", "http"),
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

        # Agent hooks: installed repo hooks, activated per conversation by
        # conversation_hooks bindings.
        try:
            from core.agent_hooks import VALID_AGENT_HOOK_EVENTS
            all_agent_hooks = rs.list_all("agent_hook", uid, conversation_id=conv_id)
            _active_hooks = extras_snapshot.get("conversation_hooks") or [] if conv_id else []
            _active_names = set()
            if isinstance(_active_hooks, dict):
                _active_hooks = _active_hooks.get("hooks", list(_active_hooks.values()))
            if isinstance(_active_hooks, list):
                for _bh in _active_hooks:
                    if isinstance(_bh, str):
                        _active_names.add(_bh)
                    elif isinstance(_bh, dict):
                        _active_names.add(str(_bh.get("name") or _bh.get("ref") or ""))
            agent_hooks_out = [{
                "name": h["name"],
                "description": h.get("description", ""),
                "scope": h.get("_scope", ""),
                "events": h.get("events") or [],
                "tools": h.get("tools") or [],
                "fail_policy": h.get("fail_policy", "open"),
                "active": h.get("name", "") in _active_names,
                "valid_events": sorted(VALID_AGENT_HOOK_EVENTS),
            } for h in all_agent_hooks]
        except Exception:
            logger.debug("list_resources: agent_hooks failed", exc_info=True)
            agent_hooks_out = []

        try:
            from core.chat_themes import list_themes as _list_themes
            themes_out = _list_themes(uid, conv_id, include_css=False)
        except Exception as e:
            logger.debug("list_resources: themes failed: %s", e)
            themes_out = []

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
            "agent_hooks": agent_hooks_out,
            "themes": themes_out,
            "voices": voices_out,
        }
        # Task instances for this conversation
        if conv_id:
            all_tasks = extras_snapshot.get("agent_tasks") or {}
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
        # Summarizer binding/effective service for this conversation.
        if conv_id:
            try:
                from core.summarizer_bindings import summary as _summarizer_summary
                result["summarizer"] = _summarizer_summary(user_id, conv_id)
            except Exception:
                result["summarizer"] = {"binding": {}, "available": [], "effective": None}
            try:
                from core.remote_fs_bindings import summary as _remote_fs_summary
                result["remote_filesystems"] = _remote_fs_summary(user_id, conv_id)
            except Exception:
                result["remote_filesystems"] = {"linked": [], "available": []}
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
                    # Resolve each linked relay's definition across the full
                    # scope chain (conv > user > global, parents included) and
                    # query the connection status against the definition's OWN
                    # scope — the same path as the relay link dialog
                    # (core.relay_bindings.list_available_relays), which is the
                    # reference for the red/green dot. resolve_all() also
                    # ensure-loads each scope, so a conv-scoped relay is seen
                    # even when nothing else touched the conv registry yet.
                    _all_defs = _greg2.resolve_all(user_id=user_id, conv_id=conv_id)
                    for _rid in _all_ids:
                        _rsvc = None
                        _connected = False
                        _sdef = _all_defs.get(_rid)
                        if _sdef is not None:
                            try:
                                _connected = _greg2.is_connected(
                                    _sdef.scope, _sdef.scope_id, _rid)
                                _rsvc = _greg2.get_live_instance_cached(
                                    _sdef.scope, _sdef.scope_id, _rid)
                            except Exception:
                                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                        # Tri-state, identical rule to the services list
                        # (_service_started_for_listing): an enabled relay that
                        # is not yet connected is "connecting" (managed
                        # container spawned, waiting for the dial-back; or lazy
                        # connect in flight) rather than down. The services
                        # panel already renders this window yellow, so the
                        # relays panel must too or the two dots disagree.
                        _enabled = getattr(_sdef, "enabled", True) if _sdef is not None else False
                        _connecting = bool(_enabled and not _connected)
                        if not _connected:
                            logging.getLogger(__name__).info(
                                "[relay-panel] linked relay '%s' reports %s "
                                "(def=%s, live=%s, conv=%s)",
                                _rid,
                                "connecting" if _connecting else "not connected",
                                f"{_sdef.scope}/{_sdef.scope_id[:8]}" if _sdef is not None else "missing",
                                "present" if _rsvc is not None else "absent",
                                conv_id[:8])
                        _ri2 = getattr(_rsvc, '_relay_info', {}) or {} if _rsvc else {}
                        _relay_details[_rid] = {
                            "root": _ri2.get("root", ""),
                            "host_root": _ri2.get("host_root", ""),
                            "platform": _ri2.get("platform", ""),
                            "containerized": _ri2.get("containerized", False),
                            "allow_local": _ri2.get("allow_local", False),
                            "connected": _connected,
                            "connecting": _connecting,
                        }
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                result["relay_bindings"] = {
                    "linked": _rb.get("linked", {}),
                    "default": _rb.get("default", {}),
                    "default_local": _rb.get("default_local", {}),
                    "details": _relay_details,
                }
            except Exception:
                result["relay_bindings"] = {"linked": {}, "default": {}}
        # Deployed flows (global=readonly, user+conv visible).
        # Ownership is STRICT: user/conversation-scoped deployments are visible
        # only to their owner / within their own conversation. The admin role
        # grants NO cross-user visibility here — the resource panel is a
        # per-user view, and another account's user-scoped deployment must
        # never leak into an admin's panel. Cross-user management lives in the
        # dedicated admin endpoints, not in list_resources.
        try:
            from core.deployment_registry import DeploymentRegistry
            flows = []
            dr = DeploymentRegistry.get_instance()
            # sync_with_executors removed from request path — too expensive.
            # DeploymentRegistry syncs on its own schedule.
            uid = user_id
            for iid, inst in dr.get_all().items():
                # Determine scope
                if not inst.owner or inst.owner == "__global__":
                    fscope = "global"
                elif inst.conversation_id:
                    fscope = "conversation"
                    # Show conv-scoped flows only if they belong to this conv
                    # (including flows deployed from task sub-conversations)
                    from core.service_registry import _parent_conversation_id
                    _inst_parent = (_parent_conversation_id(inst.conversation_id)
                                    or inst.conversation_id)
                    if _inst_parent != conv_id:
                        continue
                else:
                    fscope = "user"
                # Skip other users' flows — owner-only, no admin override.
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
        # Flow template discovery can walk many files. Keep /api/ui fast by
        # returning cache immediately and refreshing it off the request path.
        result["flow_templates"] = _get_flow_templates_cached(user_id)
        # Admin cross-user view: overlay the repo-backed catalogs (agents,
        # skills, mcp, task defs, prompts, hooks, flow templates) with rows
        # from every owner, owner-labelled. All other sections keep the
        # admin's self-view (built above) so nothing blanks out.
        if _view_all:
            _overlay_admin_view_all(result, rs)
            result["view"] = "all"
        # Include user role so frontend can enable admin features
        _user_role = flowfile.get_attribute("http.auth.roles") or "user"
        result["user_role"] = _user_role

        flowfile.set_content(json.dumps(result, ensure_ascii=False).encode())
        return [flowfile]

    if action == "list_chat_themes":
        conv_id = body.get("conversation_id", "")
        try:
            from core.chat_themes import list_themes
            selected = store.get_extra(conv_id, "theme_ref", user_id=user_id) if conv_id else ""
            flowfile.set_content(json.dumps({
                "themes": list_themes(user_id, conv_id, include_css=False),
                "conversation_theme_ref": selected or "",
            }, ensure_ascii=False).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "apply_chat_theme":
        conv_id = body.get("conversation_id", "")
        ref = body.get("theme_ref", "") or "global:pawflow_dark"
        conv_override = bool(body.get("conversation_override"))
        if not conv_id and ref.startswith("conversation:"):
            flowfile.set_content(json.dumps({"error": "Missing conversation_id for conversation theme"}).encode())
            return [flowfile]
        try:
            from core.chat_themes import resolve_theme
            theme = resolve_theme(ref, user_id, conv_id)
            if not theme:
                flowfile.set_content(json.dumps({"error": f"Theme '{ref}' not found"}).encode())
                return [flowfile]
            css = theme.get("css", "") or ""
            if conv_id:
                if conv_override:
                    store.set_extra(conv_id, "theme_ref", theme.get("ref", ref), user_id=user_id)
                    store.set_extra(conv_id, "custom_css", css, user_id=user_id)
                else:
                    store.set_extra(conv_id, "theme_ref", None, user_id=user_id)
                    store.set_extra(conv_id, "custom_css", "", user_id=user_id)
                try:
                    from core.conversation_event_bus import ConversationEventBus
                    ConversationEventBus.instance().publish_event(conv_id, "theme", {
                        "css": css,
                        "theme_ref": theme.get("ref", ref),
                    })
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            flowfile.set_content(json.dumps({
                "ok": True,
                "theme_ref": theme.get("ref", ref),
                "css": css,
                "conversation_override": conv_override,
            }, ensure_ascii=False).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "create_chat_theme":
        conv_id = body.get("conversation_id", "")
        scope = body.get("scope", "user")
        if scope == "global" and "admin" not in (flowfile.get_attribute("http.auth.roles") or ""):
            flowfile.set_content(json.dumps({"error": "Requires admin role for global scope"}).encode())
            flowfile.set_attribute("http.response.status", "403")
            return [flowfile]
        if scope == "conversation" and not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            return [flowfile]
        try:
            from core.chat_themes import create_theme
            name = body.get("name", "").strip()
            if not name:
                flowfile.set_content(json.dumps({"error": "Missing theme name"}).encode())
                return [flowfile]
            created = create_theme(
                name=name,
                scope=scope,
                user_id=user_id,
                conversation_id=conv_id,
                title=body.get("title", ""),
                description=body.get("description", ""),
                css=body.get("css", ""),
                upload=body.get("upload") or {},
            )
            flowfile.set_content(json.dumps({
                "ok": True,
                "theme": created,
                "scope": scope,
            }, ensure_ascii=False).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "delete_chat_theme":
        conv_id = body.get("conversation_id", "")
        ref = body.get("theme_ref", "")
        guard_ref = "global:" + ref.split(":", 1)[1] if ref.startswith("builtin:") else ref
        if guard_ref.startswith("global:") and "admin" not in (flowfile.get_attribute("http.auth.roles") or ""):
            flowfile.set_content(json.dumps({"error": "Requires admin role for global scope"}).encode())
            flowfile.set_attribute("http.response.status", "403")
            return [flowfile]
        try:
            from core.chat_themes import delete_theme
            deleted = delete_theme(ref, user_id, conv_id)
            if store.get_extra(conv_id, "theme_ref", user_id=user_id) == ref:
                store.set_extra(conv_id, "theme_ref", None, user_id=user_id)
                store.set_extra(conv_id, "custom_css", "", user_id=user_id)
                try:
                    from core.conversation_event_bus import ConversationEventBus
                    ConversationEventBus.instance().publish_event(conv_id, "theme", {
                        "css": "",
                        "theme_ref": "global:pawflow_dark",
                    })
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            flowfile.set_content(json.dumps({"ok": True, "deleted": deleted}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "get_tool_mcp_filters":
        conv_id = body.get("conversation_id", "")
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            return [flowfile]
        try:
            from core.tool_mcp_filters import get_filters
            from core.tool_registry import create_default_registry
            from core.resource_store import ResourceStore
            from core.conv_agent_config import get_all_agent_configs
            rs = ResourceStore.instance()
            registry = create_default_registry()
            builtin_tools = [{
                "name": h.name,
                "description": h.description,
                "source": "builtin",
            } for h in registry.list_tools()]
            dynamic_tools = [{
                "name": t.get("name", ""),
                "description": t.get("description", ""),
                "source": "dynamic",
                "scope": t.get("_scope", ""),
            } for t in rs.list_all("tool", user_id, conversation_id=conv_id)]
            mcps = [{
                "name": m.get("name", ""),
                "description": m.get("description", ""),
                "scope": m.get("_scope", ""),
                "transport": m.get("transport", "http"),
                "url": m.get("url", ""),
            } for m in rs.list_all("mcp", user_id, conversation_id=conv_id)]
            agents = list(get_all_agent_configs(conv_id).keys())
            flowfile.set_content(json.dumps({
                "filters": get_filters(conv_id),
                "tools": builtin_tools + dynamic_tools,
                "mcps": mcps,
                "agents": agents,
            }, ensure_ascii=False).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "update_tool_mcp_filters":
        conv_id = body.get("conversation_id", "")
        filters = body.get("filters", {})
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            return [flowfile]
        try:
            from core.tool_mcp_filters import set_filters
            saved = set_filters(conv_id, filters if isinstance(filters, dict) else {})
            flowfile.set_content(json.dumps({"ok": True, "filters": saved}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
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
                for sdef in reg.resolve_by_type(provider, user_id=user_id,
                                                conv_id=conv_id):
                    svc = reg.resolve(sdef.service_id, user_id=user_id,
                                      conv_id=conv_id)
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

    return _UNHANDLED
