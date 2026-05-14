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

_FLOW_TEMPLATES_TTL = 30.0
_FLOW_TEMPLATES_CACHE: Dict[str, Dict[str, Any]] = {}
_FLOW_TEMPLATES_REFRESHING: set[str] = set()
_FLOW_TEMPLATES_LOCK = threading.Lock()


def _scan_flow_templates(user_id: str) -> List[Dict[str, Any]]:
    from core.paths import REPOSITORY_DIR

    templates = []
    roots = [("global", REPOSITORY_DIR / "flows" / "global")]
    if user_id:
        roots.append(("user", REPOSITORY_DIR / "flows" / "users" / user_id))
    for scope_label, root in roots:
        if not root.is_dir():
            continue
        for latest in root.rglob("latest.json"):
            flow_dir = latest.parent
            try:
                rel_parts = flow_dir.relative_to(root).parts
                package = ".".join(rel_parts[:-1]) if len(rel_parts) > 1 else "default"
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
                    "package": raw.get("package") or package,
                    "version": version,
                    "description": raw.get("description") or "",
                    "scope": raw.get("scope") or scope_label,
                    "tasks_count": len(raw.get("tasks", {}) or {}),
                    "services_count": len(raw.get("services", {}) or {}),
                })
            except Exception as exc:
                logger.debug("list_resources flow_templates: skip %s: %s", latest, exc)
    templates.sort(key=lambda t: (t["package"], t["name"], t["version"], t["scope"]))
    return templates


def _get_flow_templates_cached(user_id: str) -> List[Dict[str, Any]]:
    key = user_id or ""
    now = time.monotonic()
    with _FLOW_TEMPLATES_LOCK:
        entry = _FLOW_TEMPLATES_CACHE.get(key) or {}
        cached = list(entry.get("data") or [])
        if entry.get("expires", 0.0) > now:
            return cached
        if key in _FLOW_TEMPLATES_REFRESHING:
            return cached
        _FLOW_TEMPLATES_REFRESHING.add(key)

    if not cached:
        try:
            data = _scan_flow_templates(key)
            with _FLOW_TEMPLATES_LOCK:
                _FLOW_TEMPLATES_CACHE[key] = {
                    "data": data,
                    "expires": time.monotonic() + _FLOW_TEMPLATES_TTL,
                }
                _FLOW_TEMPLATES_REFRESHING.discard(key)
            return data
        except Exception as exc:
            logger.debug("list_resources flow_templates cold scan failed: %s", exc)
            with _FLOW_TEMPLATES_LOCK:
                _FLOW_TEMPLATES_REFRESHING.discard(key)
            return cached

    def _refresh() -> None:
        try:
            data = _scan_flow_templates(key)
            with _FLOW_TEMPLATES_LOCK:
                _FLOW_TEMPLATES_CACHE[key] = {
                    "data": data,
                    "expires": time.monotonic() + _FLOW_TEMPLATES_TTL,
                }
        except Exception as exc:
            logger.debug("list_resources flow_templates failed: %s", exc)
        finally:
            with _FLOW_TEMPLATES_LOCK:
                _FLOW_TEMPLATES_REFRESHING.discard(key)

    threading.Thread(
        target=_refresh, name=f"flow-template-cache-{key or 'global'}",
        daemon=True).start()
    return cached


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
        scope = "conversation" if conv_id else body.get("scope", "user")
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
        scope = "conversation" if conv_id else body.get("scope", "user")
        from core.resource_store import ResourceStore
        rs = ResourceStore.instance()
        uid = user_id
        try:
            data = {"prompt": skill_prompt}
            description = body.get("description", "")
            if description:
                data["description"] = description
            from core.review_bindings import attach_review_metadata, review_for_write
            review_meta = review_for_write(
                data,
                operation="create",
                user_id=uid,
                conversation_id=conv_id,
            )
            if review_meta:
                data = attach_review_metadata(data, review_meta)
            scope_kwargs = {"conversation_id": conv_id} if scope == "conversation" and conv_id else {}
            if rs.get("skill", skill_name, uid, **scope_kwargs):
                rs.update("skill", skill_name, uid, data, **scope_kwargs)
            else:
                rs.create("skill", skill_name, uid, data, **scope_kwargs)
            flowfile.set_content(json.dumps({
                "created": True, "name": skill_name, "scope": scope,
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
        conv_id = body.get("conversation_id", "")
        _def_name = agent_name
        if conv_id:
            try:
                from core.conv_agent_config import get_agent_config
                _def_name = get_agent_config(conv_id, agent_name).get("definition") or agent_name
            except Exception:
                _def_name = agent_name
        agent_def = rs.get_any("agent", _def_name, uid)
        if not agent_def:
            flowfile.set_content(json.dumps({"error": f"Agent '{agent_name}' not found"}).encode())
            return [flowfile]
        skill_def = rs.get_any("skill", skill_name, uid)
        if not skill_def:
            flowfile.set_content(json.dumps({"error": f"Skill '{skill_name}' not found"}).encode())
            return [flowfile]
        assigned = list(agent_def.get("assigned_skills", []) or [])
        newly_assigned = skill_name not in assigned
        if newly_assigned:
            assigned.append(skill_name)
        # Update agent in the correct scope
        _scope = agent_def.get("_scope", "user")
        _uid = uid if _scope == "user" else "__global__"
        rs.update("agent", _def_name, _uid, {"assigned_skills": assigned})
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
        agent_def = rs.get_any("agent", _def_name, uid)
        if not agent_def:
            flowfile.set_content(json.dumps({"error": f"Agent '{agent_name}' not found"}).encode())
            return [flowfile]
        assigned = list(agent_def.get("assigned_skills", []) or [])
        was_assigned = skill_name in assigned
        if was_assigned:
            assigned.remove(skill_name)
        _scope = agent_def.get("_scope", "user")
        _uid = uid if _scope == "user" else "__global__"
        rs.update("agent", _def_name, _uid, {"assigned_skills": assigned})
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
        agent_def = rs.get_any("agent", _def_name, uid)
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
                scope=body.get("scope", "user") or "user",
            )
            flowfile.set_content(json.dumps(result, ensure_ascii=False).encode())
            if result.get("blocked"):
                flowfile.set_attribute("http.response.status", "400")
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
            flowfile.set_attribute("http.response.status", "400")
        return [flowfile]

    if action.startswith("pfp_"):
        try:
            from core import pfp_package
            pfp_action = action[4:]
            if pfp_action == "error":
                result = {"error": body.get("error", "Invalid /pfp command")}
            elif pfp_action == "key_create":
                result = pfp_package.create_signing_key()
            elif pfp_action == "build":
                result = pfp_package.build_pfp(
                    body.get("source_dir") or body.get("path") or "",
                    body.get("output_path") or "",
                    private_key=body.get("private_key") or "",
                    private_key_env=body.get("private_key_env") or "",
                )
            elif pfp_action == "inspect":
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
                result = pfp_package.dev_unload_pfp(
                    body.get("package") or "",
                    user_id=user_id,
                    conversation_id=body.get("conversation_id", "") or "",
                    scope=body.get("scope") or "conversation",
                    force=bool(body.get("force", True)),
                )
            elif pfp_action == "uninstall":
                result = pfp_package.uninstall_pfp(
                    body.get("package") or "",
                    user_id=user_id,
                    conversation_id=body.get("conversation_id", "") or "",
                    scope=body.get("scope") or "user",
                    force=bool(body.get("force", False)),
                )
            elif pfp_action == "list_installed":
                result = pfp_package.list_installed_packages(
                    user_id=user_id,
                    conversation_id=body.get("conversation_id", "") or "",
                    scope=body.get("scope") or "user",
                )
            elif pfp_action == "reload_tasks":
                result = pfp_package.load_installed_package_tasks(
                    user_id=user_id,
                    conversation_id=body.get("conversation_id", "") or "",
                    scope=body.get("scope") or "user",
                )
            elif pfp_action == "export":
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
            entry = {
                "name": aname,
                "description": a.get("description", ""),
                "scope": a.get("_scope", ""),
                "active": active.get("agent") == aname,
                "llm_service": acfg.get("llm_service", ""),
                "assigned_skills": a.get("assigned_skills") or [],
            }
            for skill_name in entry["assigned_skills"]:
                assigned_by_skill.setdefault(skill_name, []).append(aname)
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

        try:
            from core.chat_themes import list_themes as _list_themes
            themes_out = _list_themes(uid, conv_id)
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
            "themes": themes_out,
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
        # Flow template discovery can walk many files. Keep /api/ui fast by
        # returning cache immediately and refreshing it off the request path.
        result["flow_templates"] = _get_flow_templates_cached(user_id)
        # Include user role so frontend can enable admin features
        _user_role = flowfile.get_attribute("http.auth.roles") or "viewer"
        result["user_role"] = _user_role

        flowfile.set_content(json.dumps(result, ensure_ascii=False).encode())
        return [flowfile]

    if action == "list_chat_themes":
        conv_id = body.get("conversation_id", "")
        try:
            from core.chat_themes import list_themes
            selected = store.get_extra(conv_id, "theme_ref", user_id=user_id) if conv_id else ""
            flowfile.set_content(json.dumps({
                "themes": list_themes(user_id, conv_id),
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
                    pass
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
                    pass
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
            if rtype == "skill":
                existing = rs.get("skill", rname, target_uid) or {}
                merged = {k: v for k, v in existing.items()
                          if not str(k).startswith("_")}
                merged.update(data if isinstance(data, dict) else {})
                from core.review_bindings import attach_review_metadata, review_for_write
                review_meta = review_for_write(
                    merged,
                    operation="update",
                    user_id=target_uid,
                    conversation_id="",
                )
                if review_meta:
                    data = attach_review_metadata(data, review_meta)
            rs.update(rtype, rname, target_uid, data)
            flowfile.set_content(json.dumps({"ok": True}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "create_resource":
        rtype = body.get("resource_type", "")
        rname = body.get("name", "").strip()
        data = body.get("data", {})
        conv_id = body.get("conversation_id", "")
        scope = "conversation" if conv_id else body.get("scope", "user")
        if scope == "global" and "admin" not in (flowfile.get_attribute("http.auth.roles") or ""):
            flowfile.set_content(json.dumps({"error": "Requires admin role for global scope"}).encode())
            flowfile.set_attribute("http.response.status", "403")
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
            if rtype == "skill":
                from core.review_bindings import attach_review_metadata, review_for_write
                review_meta = review_for_write(
                    data,
                    operation="create",
                    user_id=target_uid,
                    conversation_id=conv_id if scope == "conversation" else "",
                )
                if review_meta:
                    data = attach_review_metadata(data, review_meta)
            if rtype == "task_def" and scope == "conversation" and conv_id:
                from core.conversation_store import ConversationStore
                cs = ConversationStore.instance()
                conv_defs = cs.get_extra(conv_id, "conversation_task_defs") or {}
                conv_defs[rname] = data
                cs.set_extra(conv_id, "conversation_task_defs", conv_defs)
            else:
                scope_kwargs = {"conversation_id": conv_id} if scope == "conversation" and conv_id else {}
                rs.create(rtype, rname, target_uid, data, **scope_kwargs)
                if rtype == "agent" and scope == "conversation" and conv_id:
                    from core.conv_agent_config import add_agent_to_conv
                    add_agent_to_conv(conv_id, rname, definition=rname)
            flowfile.set_content(json.dumps({"ok": True, "scope": scope}).encode())
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
        # Merge — only update known runtime fields. Skills live on the agent
        # definition (`assigned_skills`), not in conv_agent_config.
        _allowed = {"llm_service", "model", "tools", "max_depth", "params"}
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
            _agent_def = ResourceStore.instance().get_any("agent", _def_name, user_id)
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
                        "skill", _skill, user_id) or {}
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
            if entry.get("skills") is not None:
                _agent_def = rs.get_any("agent", entry["definition"], uid)
                if _agent_def is not None:
                    _scope = _agent_def.get("_scope", "user")
                    _uid = uid if _scope == "user" else "__global__"
                    rs.update("agent", entry["definition"], _uid, {
                        "assigned_skills": list(entry.get("skills") or [])
                    })
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
