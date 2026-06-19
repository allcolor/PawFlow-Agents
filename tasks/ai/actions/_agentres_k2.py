"""AgentLoopTask actions — agent resource"""

import json
import logging
from tasks.ai.actions._agentres_base import (
    _UNHANDLED,
    _has_pfp_install_records,
)

logger = logging.getLogger(__name__)


def _handle_agentres_k2(self, action, body, store, user_id, flowfile):
    """agent_resource cluster _agentres_k2. Returns result or _UNHANDLED."""
    if action == "assign_skill":
        agent_name = body.get("agent_name", "").strip()
        skill_name = body.get("skill_name", "").strip()
        if not agent_name or not skill_name:
            flowfile.set_content(json.dumps({"error": "Missing agent_name or skill_name"}).encode())
            return [flowfile]
        from core.resource_store import ResourceStore
        rs = ResourceStore.instance()
        uid = user_id
        conv_id = body.get("conversation_id", "")
        _def_name = agent_name
        if conv_id:
            try:
                from core.conv_agent_config import get_agent_config
                _def_name = get_agent_config(conv_id, agent_name).get("definition") or agent_name
            except Exception:
                _def_name = agent_name
        agent_def = rs.get_any("agent", _def_name, uid,
                               conversation_id=conv_id)
        if not agent_def:
            flowfile.set_content(json.dumps({"error": f"Agent '{agent_name}' not found"}).encode())
            return [flowfile]
        skill_def = rs.get_any("skill", skill_name, uid,
                               conversation_id=conv_id)
        if not skill_def:
            flowfile.set_content(json.dumps({"error": f"Skill '{skill_name}' not found"}).encode())
            return [flowfile]
        if skill_def.get("_invalid"):
            flowfile.set_content(json.dumps({
                "error": f"Skill '{skill_name}' is invalid: {skill_def.get('_invalid')}",
            }).encode())
            return [flowfile]
        # Re-read under the lock so a concurrent assign/unassign can't drop an entry.
        from core.skill_lifecycle import ASSIGNED_SKILLS_LOCK
        with ASSIGNED_SKILLS_LOCK:
            fresh = rs.get_any("agent", _def_name, uid,
                               conversation_id=conv_id) or agent_def
            assigned = list(fresh.get("assigned_skills", []) or [])
            from core.skill_resolver import normalize_skill_entry
            newly_assigned = not any(
                normalize_skill_entry(entry)[0] == skill_name
                for entry in assigned)
            if newly_assigned:
                assigned.append(skill_name)
            _scope = fresh.get("_scope", "user")
            _uid = uid if _scope in ("conversation", "user") else "__global__"
            _scope_kwargs = {"conversation_id": conv_id} if _scope == "conversation" and conv_id else {}
            rs.update("agent", _def_name, _uid, {"assigned_skills": assigned}, **_scope_kwargs)
        if conv_id and newly_assigned:
            try:
                from core.llm_client import stamp_message
                from core.pending_queue import PendingQueue
                from core.skill_resolver import available_skill_context_message
                content = available_skill_context_message(skill_name, skill_def)
                msg = stamp_message({
                    "role": "system",
                    "content": content,
                    "source": {"type": "context", "name": "pawflow"},
                }, conv_id)
                store.append_message(conv_id, msg, agent_name=agent_name,
                                     user_id=uid)
                PendingQueue.for_agent(conv_id, agent_name).enqueue(
                    dict(msg), source="skill_assign")
            except Exception:
                logger.debug("skill availability context injection failed",
                             exc_info=True)
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
        conv_id = body.get("conversation_id", "")
        _def_name = agent_name
        if conv_id:
            try:
                from core.conv_agent_config import get_agent_config
                _def_name = get_agent_config(conv_id, agent_name).get("definition") or agent_name
            except Exception:
                _def_name = agent_name
        agent_def = rs.get_any("agent", _def_name, uid,
                               conversation_id=conv_id)
        if not agent_def:
            flowfile.set_content(json.dumps({"error": f"Agent '{agent_name}' not found"}).encode())
            return [flowfile]
        # Re-read under the lock so a concurrent assign/unassign can't drop an entry.
        from core.skill_lifecycle import ASSIGNED_SKILLS_LOCK
        with ASSIGNED_SKILLS_LOCK:
            fresh = rs.get_any("agent", _def_name, uid,
                               conversation_id=conv_id) or agent_def
            assigned = list(fresh.get("assigned_skills", []) or [])
            from core.skill_resolver import normalize_skill_entry
            kept = []
            was_assigned = False
            for entry in assigned:
                if normalize_skill_entry(entry)[0] == skill_name:
                    was_assigned = True
                    continue
                kept.append(entry)
            assigned = kept
            _scope = fresh.get("_scope", "user")
            _uid = uid if _scope in ("conversation", "user") else "__global__"
            _scope_kwargs = {"conversation_id": conv_id} if _scope == "conversation" and conv_id else {}
            rs.update("agent", _def_name, _uid, {"assigned_skills": assigned}, **_scope_kwargs)
        if conv_id and was_assigned:
            try:
                from core.llm_client import stamp_message
                from core.pending_queue import PendingQueue
                from core.skill_resolver import removed_skill_context_message
                msg = stamp_message({
                    "role": "system",
                    "content": removed_skill_context_message(skill_name),
                    "source": {"type": "context", "name": "pawflow"},
                }, conv_id)
                store.append_message(conv_id, msg, agent_name=agent_name,
                                     user_id=uid)
                PendingQueue.for_agent(conv_id, agent_name).enqueue(
                    dict(msg), source="skill_unassign")
            except Exception:
                logger.debug("skill removal context injection failed",
                             exc_info=True)
        flowfile.set_content(json.dumps({
            "unassigned": True, "agent": agent_name, "skill": skill_name,
            "message": f"Skill '{skill_name}' removed from agent '{agent_name}'",
        }).encode())
        return [flowfile]

    if action in ("agent_msg", "resume_agent"):
        conv_id = body.get("conversation_id", "")
        agent_name = (
            body.get("target_agent", "") or body.get("agent_name", "")
        ).strip()
        message = (body.get("message", "") or "").strip()
        if action == "resume_agent" and not message:
            message = "Continue from where you stopped"
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        if agent_name:
            agent_name = self._resolve_agent_name(agent_name, conv_id)
        else:
            active = store.get_extra(conv_id, "active_resources", user_id=user_id) or {}
            agent_name = (active.get("agent", "") or "").strip()
        if not agent_name:
            flowfile.set_content(json.dumps({
                "error": "Missing target agent. Select an agent or use @agent.",
            }).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        if not message:
            flowfile.set_content(json.dumps({"error": "Missing message"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]

        from core.conv_agent_config import require_agent_member
        member_error = require_agent_member(conv_id, agent_name, user_id=user_id)
        if member_error:
            flowfile.set_content(json.dumps({"error": member_error}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]

        from core.conversation_writer import ConversationWriter
        from core.llm_client import stamp_message
        from core.pending_queue import PendingQueue
        msg = stamp_message({
            "role": "user",
            "content": message,
            "source": {
                "type": "user",
                "name": user_id,
                "target_agent": agent_name,
            },
            "channel": "web",
        }, conv_id)
        ConversationWriter.for_conversation(conv_id).enqueue_message(
            dict(msg), agent_name=agent_name, user_id=user_id)
        PendingQueue.for_agent(conv_id, agent_name).enqueue(
            dict(msg), source=action)
        try:
            from tasks.ai.agent_loop import AgentLoopTask
            AgentLoopTask.wake_agent(
                conv_id, agent_name, reason=f"[{action}] {agent_name}",
                user_id=user_id, delay=0.0)
        except Exception:
            logger.debug("agent message wake failed", exc_info=True)
        flowfile.set_content(json.dumps({
            "ok": True,
            "agent": agent_name,
            "message": "Queued for agent",
        }, ensure_ascii=False).encode())
        return [flowfile]

    if action == "run_skill":
        conv_id = body.get("conversation_id", "")
        agent_name = (
            body.get("target_agent", "") or body.get("agent_name", "")
        ).strip()
        skill_name = body.get("skill_name", "").strip().lstrip("@")
        arguments = body.get("arguments", "") or ""
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        if agent_name:
            agent_name = self._resolve_agent_name(agent_name, conv_id)
        else:
            active = store.get_extra(conv_id, "active_resources", user_id=user_id) or {}
            agent_name = (active.get("agent", "") or "").strip()
        if not agent_name:
            flowfile.set_content(json.dumps({
                "error": "Missing target agent. Select an agent or use /skill run @agent <skill> [args...]",
            }).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        if not skill_name:
            flowfile.set_content(json.dumps({"error": "Missing skill name"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]

        from core.conv_agent_config import require_agent_member
        member_error = require_agent_member(conv_id, agent_name, user_id=user_id)
        if member_error:
            flowfile.set_content(json.dumps({"error": member_error}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]

        from core.resource_store import ResourceStore
        rs = ResourceStore.instance()
        skill_def = rs.get_any(
            "skill", skill_name, user_id, conversation_id=conv_id)
        if not skill_def:
            flowfile.set_content(json.dumps({
                "error": f"Skill '{skill_name}' not found",
            }).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        if skill_def.get("_invalid"):
            flowfile.set_content(json.dumps({
                "error": f"Skill '{skill_name}' is invalid: {skill_def.get('_invalid')}",
            }).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]

        from core.skill_resolver import resolve_runnable_skill_prompt
        prompt = resolve_runnable_skill_prompt(
            skill_name, user_id, conv_id, agent_name, arguments)
        if not prompt:
            flowfile.set_content(json.dumps({
                "error": f"Skill '{skill_name}' has no runnable prompt",
            }).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]

        from core.conversation_writer import ConversationWriter
        from core.llm_client import stamp_message
        from core.pending_queue import PendingQueue
        msg = stamp_message({
            "role": "user",
            "content": prompt,
            "source": {
                "type": "user",
                "name": user_id,
                "target_agent": agent_name,
                "skill_run": {
                    "skill": skill_name,
                    "arguments": arguments,
                },
            },
            "channel": "web",
        }, conv_id)
        ConversationWriter.for_conversation(conv_id).enqueue_message(
            dict(msg), agent_name=agent_name, user_id=user_id)
        PendingQueue.for_agent(conv_id, agent_name).enqueue(
            dict(msg), source="skill_run")
        try:
            from tasks.ai.agent_loop import AgentLoopTask
            AgentLoopTask.wake_agent(
                conv_id, agent_name,
                reason=f"[skill-run] {skill_name}", user_id=user_id,
                delay=0.0)
        except Exception:
            logger.debug("skill run wake failed", exc_info=True)
        flowfile.set_content(json.dumps({
            "ok": True,
            "agent": agent_name,
            "skill": skill_name,
            "arguments": arguments,
            "message": f"Skill '{skill_name}' queued for agent '{agent_name}'",
        }, ensure_ascii=False).encode())
        return [flowfile]

    if action == "list_agent_skills":
        agent_name = body.get("agent_name", "").strip()
        if not agent_name:
            flowfile.set_content(json.dumps({"error": "Missing agent_name"}).encode())
            return [flowfile]
        from core.resource_store import ResourceStore
        from core.skill_resolver import normalize_skill_entry
        rs = ResourceStore.instance()
        uid = user_id
        conv_id = body.get("conversation_id", "")
        _def_name = agent_name
        if conv_id:
            try:
                from core.conv_agent_config import get_agent_config
                _def_name = get_agent_config(conv_id, agent_name).get("definition") or agent_name
            except Exception:
                _def_name = agent_name
        agent_def = rs.get_any("agent", _def_name, uid,
                               conversation_id=conv_id)
        if not agent_def:
            flowfile.set_content(json.dumps({"error": f"Agent '{agent_name}' not found"}).encode())
            return [flowfile]
        assigned = agent_def.get("assigned_skills", [])
        skills_detail = []
        for sn in assigned:
            name, _params, _condition = normalize_skill_entry(sn)
            sd = rs.get_any("skill", name, uid, conversation_id=conv_id)
            skills_detail.append({
                "name": name,
                "description": sd.get("description", "") if sd else "(not found)",
            })
        flowfile.set_content(json.dumps({
            "agent": agent_name, "skills": skills_detail,
        }).encode())
        return [flowfile]

    if action == "list_skills":
        from core.resource_store import ResourceStore
        from core.skill_resolver import normalize_skill_entry
        uid = user_id
        conv_id = body.get("conversation_id", "")
        rs = ResourceStore.instance()
        skills = rs.list_all("skill", uid, conversation_id=conv_id)
        assigned_by_skill = {}
        active_agent = ""
        if conv_id:
            active = store.get_extra(conv_id, "active_resources") or {}
            active_agent = (active.get("agent") or "").strip()
            try:
                from core.conv_agent_config import get_all_agent_configs
                conv_agent_cfgs = get_all_agent_configs(conv_id)
            except Exception:
                conv_agent_cfgs = {}
            all_agent_defs = rs.list_all("agent", uid, conversation_id=conv_id)
            agent_defs_by_name = {a.get("name"): a for a in all_agent_defs}
            for agent_name, acfg in conv_agent_cfgs.items():
                def_name = (acfg or {}).get("definition") or agent_name
                agent_def = agent_defs_by_name.get(def_name) or agent_defs_by_name.get(agent_name) or {}
                for raw_skill in agent_def.get("assigned_skills") or []:
                    skill_name, _params, _condition = normalize_skill_entry(raw_skill)
                    if skill_name:
                        assigned_by_skill.setdefault(skill_name, []).append(agent_name)
        flowfile.set_content(json.dumps({
            "skills": [{
                "name": s["name"],
                "description": s.get("description", ""),
                "scope": s.get("_scope", ""),
                "preview": (s.get("instructions") or s.get("prompt", ""))[:80],
                "invalid": s.get("_invalid", ""),
                "assigned_to": assigned_by_skill.get(s["name"], []),
                "active": active_agent in assigned_by_skill.get(s["name"], []),
            } for s in skills],
        }, ensure_ascii=False).encode())
        return [flowfile]

    if action == "search_skill_marketplace":
        source = body.get("source", "all") or "all"
        query = body.get("query", "") or ""
        try:
            from core.skill_marketplace import search_marketplace
            result = search_marketplace(source=source, query=query, limit=10)
            flowfile.set_content(json.dumps(result, ensure_ascii=False).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
            flowfile.set_attribute("http.response.status", "400")
        return [flowfile]

    if action == "import_skill_marketplace":
        ref = body.get("ref", "") or ""
        scope = body.get("scope", "user") or "user"
        if scope == "global" and "admin" not in (flowfile.get_attribute("http.auth.roles") or ""):
            flowfile.set_content(json.dumps({"error": "Requires admin role for global scope"}).encode())
            flowfile.set_attribute("http.response.status", "403")
            return [flowfile]
        if body.get("error"):
            flowfile.set_content(json.dumps({"error": body.get("error")}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        if not ref:
            flowfile.set_content(json.dumps({
                "error": "Missing ref. Usage: /skill import [--source src] [--review-only] [--force] <ref>",
            }).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        try:
            from core.skill_marketplace import import_marketplace_skill
            result = import_marketplace_skill(
                source=body.get("source", "") or "",
                ref=ref,
                name=body.get("name", "") or "",
                user_id=user_id,
                conversation_id=body.get("conversation_id", "") or "",
                review_only=bool(body.get("review_only", False)),
                force=bool(body.get("force", False)),
                scope=scope,
            )
            flowfile.set_content(json.dumps(result, ensure_ascii=False).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
            flowfile.set_attribute("http.response.status", "400")
        return [flowfile]

    if action == "resolve_skill_import_source":
        ref = body.get("ref", "") or ""
        if not ref:
            flowfile.set_content(json.dumps({
                "error": "Missing repository. Use owner/repo or https://github.com/owner/repo",
            }).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        try:
            from core.skill_marketplace import resolve_skill_import_source
            result = resolve_skill_import_source(
                ref=ref,
                selected_ref=body.get("selected_ref", "") or "",
                path=body.get("path", "") or "",
                limit=int(body.get("limit", 40) or 40),
            )
            flowfile.set_content(json.dumps(result, ensure_ascii=False).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
            flowfile.set_attribute("http.response.status", "400")
        return [flowfile]

    if action.startswith("pfp_"):
        try:
            pfp_action = action[4:]
            if pfp_action == "error":
                result = {"error": body.get("error", "Invalid /pfp command")}
            elif pfp_action == "list_installed" and not _has_pfp_install_records(
                    user_id,
                    body.get("conversation_id", "") or "",
                    body.get("scope") or "user"):
                _scope = body.get("scope") or "user"
                if _scope in {"conversation", "conv"} and body.get("conversation_id"):
                    _scope = "conversation"
                else:
                    _scope = "user"
                result = {"ok": True, "scope": _scope, "packages": []}
            elif pfp_action == "key_create":
                from core import pfp_package
                result = pfp_package.create_signing_key()
            elif pfp_action == "build":
                from core import pfp_package
                result = pfp_package.build_pfp(
                    body.get("source_dir") or body.get("path") or "",
                    body.get("output_path") or "",
                    private_key=body.get("private_key") or "",
                    private_key_env=body.get("private_key_env") or "",
                )
            elif pfp_action == "inspect":
                from core import pfp_package
                from core import pfp_registry
                resolved = pfp_registry.resolve_package_path(
                    body.get("path") or body.get("ref") or "",
                    user_id=user_id,
                    expected_sha256=body.get("sha256") or "",
                    confirm_download=bool(body.get("confirm_download", False)),
                )
                if resolved.get("requires_confirmation"):
                    result = resolved
                    flowfile.set_content(json.dumps(result, ensure_ascii=False).encode())
                    return [flowfile]
                result = pfp_package.inspect_pfp(
                    resolved["path"],
                    user_id=user_id,
                    conversation_id=body.get("conversation_id", "") or "",
                    scope=body.get("scope") or "user",
                )
                result["display"] = pfp_package.format_inspection_display(result)
                if resolved.get("downloaded"):
                    result["download"] = resolved
            elif pfp_action == "install":
                from core import pfp_package
                from core import pfp_registry
                agent_name = str(body.get("agent_name") or getattr(self, "_agent_name", "") or "")
                resolved = pfp_registry.resolve_package_path(
                    body.get("path") or body.get("ref") or "",
                    user_id=user_id,
                    expected_sha256=body.get("sha256") or "",
                    confirm_download=bool(body.get("confirm_download", False)),
                )
                if resolved.get("requires_confirmation"):
                    result = resolved
                    flowfile.set_content(json.dumps(result, ensure_ascii=False).encode())
                    return [flowfile]
                result = pfp_package.install_pfp(
                    resolved["path"],
                    user_id=user_id,
                    conversation_id=body.get("conversation_id", "") or "",
                    scope=body.get("scope") or "user",
                    include=body.get("include") or None,
                    exclude=body.get("exclude") or None,
                    force=bool(body.get("force", False)),
                    replace=bool(body.get("replace", False)),
                    dry_run=bool(body.get("dry_run", False)),
                    secret_bindings=body.get("secret_bindings") or {},
                    agent_name=agent_name,
                )
                if resolved.get("downloaded"):
                    result["download"] = resolved
            elif pfp_action == "update":
                from core import pfp_package
                from core import pfp_registry
                agent_name = str(body.get("agent_name") or getattr(self, "_agent_name", "") or "")
                resolved = pfp_registry.resolve_package_path(
                    body.get("path") or body.get("ref") or "",
                    user_id=user_id,
                    expected_sha256=body.get("sha256") or "",
                    confirm_download=bool(body.get("confirm_download", False)),
                )
                if resolved.get("requires_confirmation"):
                    result = resolved
                    flowfile.set_content(json.dumps(result, ensure_ascii=False).encode())
                    return [flowfile]
                result = pfp_package.update_pfp(
                    resolved["path"],
                    user_id=user_id,
                    conversation_id=body.get("conversation_id", "") or "",
                    scope=body.get("scope") or "user",
                    include=body.get("include") or None,
                    exclude=body.get("exclude") or None,
                    force=bool(body.get("force", False)),
                    dry_run=bool(body.get("dry_run", False)),
                    secret_bindings=body.get("secret_bindings") or {},
                    agent_name=agent_name,
                )
                if resolved.get("downloaded"):
                    result["download"] = resolved
            elif pfp_action == "dev_load":
                from core import pfp_package
                agent_name = str(body.get("agent_name") or getattr(self, "_agent_name", "") or "")
                result = pfp_package.dev_load_pfp(
                    body.get("source_dir") or body.get("path") or "",
                    user_id=user_id,
                    conversation_id=body.get("conversation_id", "") or "",
                    scope=body.get("scope") or "conversation",
                    include=body.get("include") or None,
                    exclude=body.get("exclude") or None,
                    force=bool(body.get("force", True)),
                    replace=bool(body.get("replace", True)),
                    dry_run=bool(body.get("dry_run", False)),
                    secret_bindings=body.get("secret_bindings") or {},
                    agent_name=agent_name,
                )
            elif pfp_action == "dev_unload":
                from core import pfp_package
                result = pfp_package.dev_unload_pfp(
                    body.get("package") or "",
                    user_id=user_id,
                    conversation_id=body.get("conversation_id", "") or "",
                    scope=body.get("scope") or "conversation",
                    force=bool(body.get("force", True)),
                )
            elif pfp_action == "uninstall":
                from core import pfp_package
                result = pfp_package.uninstall_pfp(
                    body.get("package") or "",
                    user_id=user_id,
                    conversation_id=body.get("conversation_id", "") or "",
                    scope=body.get("scope") or "user",
                    force=bool(body.get("force", False)),
                )
            elif pfp_action == "list_installed":
                from core import pfp_package
                result = pfp_package.list_installed_packages(
                    user_id=user_id,
                    conversation_id=body.get("conversation_id", "") or "",
                    scope=body.get("scope") or "user",
                )
            elif pfp_action == "reload_tasks":
                from core import pfp_package
                result = pfp_package.load_installed_package_tasks(
                    user_id=user_id,
                    conversation_id=body.get("conversation_id", "") or "",
                    scope=body.get("scope") or "user",
                )
            elif pfp_action == "export":
                from core import pfp_package
                result = pfp_package.export_pfpdir(
                    body.get("package") or "",
                    body.get("version") or "",
                    body.get("include") or [],
                    output_dir=body.get("output_dir") or body.get("path") or "",
                    user_id=user_id,
                    conversation_id=body.get("conversation_id", "") or "",
                )
            elif pfp_action == "registry_add":
                from core import pfp_registry
                result = pfp_registry.add_registry(
                    body.get("url") or body.get("path") or "",
                    user_id=user_id,
                    name=body.get("name") or "",
                    trusted=bool(body.get("trusted", False)),
                )
            elif pfp_action == "registry_remove":
                from core import pfp_registry
                result = pfp_registry.remove_registry(
                    body.get("name") or body.get("url") or body.get("path") or "",
                    user_id=user_id,
                )
            elif pfp_action == "registry_list":
                from core import pfp_registry
                result = pfp_registry.list_registries(user_id=user_id)
            elif pfp_action == "search":
                from core import pfp_registry
                result = pfp_registry.search_registries(
                    body.get("query") or "",
                    user_id=user_id,
                    limit=int(body.get("limit") or 20),
                )
            else:
                result = {"error": f"Unknown /pfp action: {pfp_action}"}
            flowfile.set_content(json.dumps(result, ensure_ascii=False).encode())
            if result.get("error"):
                flowfile.set_attribute("http.response.status", "400")
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
            flowfile.set_attribute("http.response.status", "400")
        return [flowfile]

    return _UNHANDLED
