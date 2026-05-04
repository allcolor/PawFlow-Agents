"""AgentLoopTask actions — memory prompts"""

import json
import logging
import time
from typing import Dict, Any, List, Optional

from core import FlowFile

logger = logging.getLogger(__name__)


def _handle_memory_prompts(self, action, body, store, user_id, flowfile):
    """Handle memory prompts actions. Returns [flowfile] or None."""

    if action == "list_memories":
        try:
            from core.memory_store import MemoryStore
            ms = MemoryStore.instance()
            agent_filter = body.get("agent_name")  # None = all
            if agent_filter is not None:
                entries = ms.list_by_agent(user_id, agent_filter)
            else:
                entries = ms.list_all(user_id)
            include_ended = bool(body.get("include_ended"))
            if not include_ended:
                entries = [e for e in entries if not e.ended]
            # Newest first — users open this panel to see what was just
            # captured, not to scroll through 30-day-old auto-extracts.
            entries = sorted(
                entries,
                key=lambda e: (e.updated_at or e.created_at or 0),
                reverse=True,
            )
            result = [{
                "id": e.id, "text": e.text, "tags": e.tags,
                "created_at": e.created_at, "updated_at": e.updated_at,
                "source": e.source, "agent": e.agent,
                "conversation_id": e.conversation_id,
                "category": e.category, "ended": e.ended,
                "expires_at": e.expires_at,
            } for e in entries]
            flowfile.set_content(json.dumps({
                "memories": result, "count": len(result),
            }, ensure_ascii=False).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "delete_memory":
        memory_id = body.get("memory_id", "")
        if not memory_id:
            flowfile.set_content(json.dumps({"error": "Missing memory_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        try:
            from core.memory_store import MemoryStore
            deleted = MemoryStore.instance().forget(user_id, memory_id)
            flowfile.set_content(json.dumps({"deleted": deleted}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "edit_memory":
        memory_id = body.get("memory_id", "")
        if not memory_id:
            flowfile.set_content(json.dumps({"error": "Missing memory_id"}).encode())
            return [flowfile]
        from core.memory_store import MemoryStore
        ms = MemoryStore.instance()
        updated = False
        if "text" in body:
            updated = ms.update_text(user_id, memory_id, body["text"]) or updated
        if "tags" in body:
            updated = ms.update_tags(user_id, memory_id, body["tags"]) or updated
        if "agent" in body:
            updated = ms.update_agent(user_id, memory_id, body["agent"]) or updated
        flowfile.set_content(json.dumps({"updated": updated}).encode())
        return [flowfile]

    if action == "add_memory":
        text = body.get("text", "")
        if not text:
            flowfile.set_content(json.dumps({"error": "Missing text"}).encode())
            return [flowfile]
        tags = body.get("tags", [])
        agent = body.get("agent", "")
        conv_id = body.get("conversation_id", "")
        scope = body.get("scope", "agent")  # global/agent/conversation/private
        if scope == "global" and "admin" not in (flowfile.get_attribute("http.auth.roles") or ""):
            flowfile.set_content(json.dumps({"error": "Requires admin role for global scope"}).encode())
            flowfile.set_attribute("http.response.status", "403")
            return [flowfile]
        # Resolve scope
        if scope == "global":
            agent, conv_id = "", ""
        elif scope == "conversation":
            agent = ""
        elif scope == "private":
            pass  # keep both
        else:  # agent
            conv_id = ""
        from core.memory_store import MemoryStore
        entry = MemoryStore.instance().remember(
            user_id, text, tags, source="user",
            agent=agent, conversation_id=conv_id,
        )
        flowfile.set_content(json.dumps({
            "id": entry.id, "text": entry.text,
            "tags": entry.tags, "agent": entry.agent,
            "conversation_id": entry.conversation_id,
        }, ensure_ascii=False).encode())
        return [flowfile]

    if action == "list_skills":
        from core.resource_store import ResourceStore
        rs = ResourceStore.instance()
        skills = rs.list_all("skill", user_id)
        items = [
            {
                "name": s["name"],
                "description": s.get("description", ""),
                "preview": s.get("prompt", "")[:100],
                "extends": s.get("extends", ""),
            }
            for s in skills
        ]
        flowfile.set_content(json.dumps({"skills": items}, ensure_ascii=False).encode())
        return [flowfile]

    if action == "get_skill":
        skill_name = body.get("name", "")
        if not skill_name:
            flowfile.set_content(json.dumps({"error": "Missing name"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        from core.resource_store import ResourceStore
        rs = ResourceStore.instance()
        skill_def = rs.get_any("skill", skill_name, user_id)
        if not skill_def:
            flowfile.set_content(json.dumps({"error": "Skill not found"}).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        flowfile.set_content(json.dumps({
            "name": skill_name,
            "prompt": skill_def.get("prompt", ""),
            "description": skill_def.get("description", ""),
            "extends": skill_def.get("extends", ""),
            "parameters": skill_def.get("parameters", {}),
        }, ensure_ascii=False).encode())
        return [flowfile]

    if action == "list_prompts":
        from core.resource_store import ResourceStore
        rs = ResourceStore.instance()
        prompts = rs.list_all("prompt", user_id)
        items = [
            {
                "name": p["name"],
                "title": p.get("title", ""),
                "category": p.get("category", ""),
                "description": p.get("description", ""),
                "has_parameters": bool(p.get("parameters")),
                "preview": p.get("prompt", "")[:100],
            }
            for p in prompts
        ]
        flowfile.set_content(json.dumps({"prompts": items}, ensure_ascii=False).encode())
        return [flowfile]

    if action == "get_prompt":
        name = body.get("name", "")
        if not name:
            flowfile.set_content(json.dumps({"error": "Missing name"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        from core.resource_store import ResourceStore
        rs = ResourceStore.instance()
        prompt_def = rs.get_any("prompt", name, user_id)
        if not prompt_def:
            flowfile.set_content(json.dumps({"error": "Prompt not found"}).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        flowfile.set_content(json.dumps({
            "name": name,
            "prompt": prompt_def.get("prompt", ""),
            "title": prompt_def.get("title", ""),
            "category": prompt_def.get("category", ""),
            "description": prompt_def.get("description", ""),
            "parameters": prompt_def.get("parameters", {}),
        }, ensure_ascii=False).encode())
        return [flowfile]

    if action == "use_prompt":
        name = body.get("name", "")
        params = body.get("params", {})
        if not name:
            flowfile.set_content(json.dumps({"error": "Missing name"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        from core.resource_store import ResourceStore
        rs = ResourceStore.instance()
        prompt_def = rs.get_any("prompt", name, user_id)
        if not prompt_def:
            flowfile.set_content(json.dumps({"error": "Prompt not found"}).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        text = prompt_def.get("prompt", "")
        if params:
            import re as _re
            def _replace(m):
                key = m.group(1)
                return str(params.get(key, m.group(0)))
            text = _re.sub(r'\$\{(\w+)}', _replace, text)
        flowfile.set_content(json.dumps({
            "resolved": text,
        }, ensure_ascii=False).encode())
        return [flowfile]

    return None
