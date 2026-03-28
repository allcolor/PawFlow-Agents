"""AgentLoopTask actions — conversation"""

import json
import logging
import time
import threading
from typing import Dict, Any, List, Optional

from core import FlowFile
from core.llm_client import LLMMessage, LLMClient
from core.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


def _handle_conversation(self, action, body, store, user_id, flowfile):
    """Handle conversation actions. Returns [flowfile] or None."""


    if action == "list_conversations":
        convs = store.list_conversations(user_id=user_id)
        result = json.dumps({"conversations": convs}, ensure_ascii=False)
        flowfile.set_content(result.encode("utf-8"))
        return [flowfile]

    if action == "load_history":
        conv_id = body.get("conversation_id", "")
        limit = int(body.get("limit", 50))
        offset = int(body.get("offset", 0))
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]

        page = store.load_page(conv_id, limit=limit, offset=offset, user_id=user_id)
        if page is None:
            flowfile.set_content(json.dumps({"error": "Conversation not found"}).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]

        raw_messages = page["messages"]
        raw_count = len(raw_messages)
        history = self._classify_messages_for_display(raw_messages)
        nicknames = store.get_extra(conv_id, "agent_nicknames", user_id=user_id) or {}
        active_res = store.get_extra(conv_id, "active_resources", user_id=user_id) or {}
        active_res = self._ensure_active_agent(conv_id, active_res, user_id or "anonymous")
        custom_css = store.get_extra(conv_id, "custom_css", user_id=user_id) or ""

        result = json.dumps({
            "conversation_id": conv_id,
            "messages": history,
            "message_count": page["total_count"],
            "has_more": page["has_more"],
            "offset": page["offset"],
            "raw_count": raw_count,
            "nicknames": nicknames,
            "active_agent": active_res.get("agent", ""),
            "custom_css": custom_css,
        }, ensure_ascii=False)
        flowfile.set_content(result.encode("utf-8"))
        return [flowfile]

    if action == "delete_conversation":
        conv_id = body.get("conversation_id", "")
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        # Collect file IDs from conversation before deleting
        history = store.load(conv_id, user_id=user_id)
        if history:
            self._cleanup_conversation_files(history)
        # Cascade cleanup: flows, dynamic tools, secrets
        self._cleanup_conversation_resources(conv_id)
        deleted = store.delete(conv_id, user_id=user_id)
        logger.info(f"[action] delete_conversation {conv_id}: deleted={deleted}, "
                    f"user_id={user_id}")
        result = json.dumps({"deleted": deleted, "conversation_id": conv_id})
        flowfile.set_content(result.encode("utf-8"))
        return [flowfile]

    if action == "resume_conversation":
        conv_id = body.get("conversation_id", "")
        _rs_agent = body.get("agent_name", "")
        max_summary_tokens = int(body.get("max_tokens", 500))
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        _rs_msgs = store.load(conv_id, user_id=user_id)
        if not _rs_msgs:
            flowfile.set_content(json.dumps({"error": "Conversation not found"}).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        # Resolve LLM client
        _summ_client, _, _ = self._get_summarizer_client(user_id)
        _rs_client = _summ_client
        if not _rs_client:
            _rs_svc = self._resolve_service_param("llm_service", user_id) or "default"
            _rs_client, _ = self._resolve_client(_rs_svc, user_id)
        if not _rs_client:
            flowfile.set_content(json.dumps({"error": "No LLM service for summarization"}).encode())
            return [flowfile]

        def _do_resume():
            deserialized = self._deserialize_messages(_rs_msgs)
            content_msgs = [m for m in deserialized if m.role != "system"]
            context_max = int(self.config.get("max_context_size", 64000))
            # Resolve agent's max_tokens
            if _rs_agent:
                _, _, _sv = self._resolve_agent_client(_rs_agent, user_id, conv_id)
                if _sv:
                    _v = int((getattr(_sv, 'config', {}) or {}).get("max_context_size", 0))
                    if _v:
                        context_max = _v
            summary = self._summarize_messages(
                content_msgs, _rs_client, context_max,
                target_tokens=max_summary_tokens,
                conversation_id=conv_id,
            )
            sys_prompt = self.config.get("system_prompt", "You are a helpful assistant.")
            from datetime import datetime
            sys_prompt += f"\n\nCurrent date and time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            new_context = [
                LLMMessage(role="system", content=sys_prompt),
                LLMMessage(role="user",
                           content=f"[Conversation summary â€” earlier messages compacted]\n\n{summary}"),
                LLMMessage(role="assistant",
                           content="Understood. I have the context from our earlier conversation. Continuing from where we left off."),
            ]
            store.save_agent_context(conv_id, _rs_agent, self._serialize_messages(new_context))
            return {"summary_length": len(summary),
                    "messages_summarized": len(_rs_msgs),
                    "agent": _rs_agent or "shared"}

        return self._run_bg_context_op(conv_id, "summary", _do_resume, flowfile)

    if action == "ping":
        # Keep-alive: session renewal happens in validateSessionAuth upstream
        flowfile.set_content(json.dumps({"status": "ok"}).encode())
        return [flowfile]

    if action == "poll":
        # Efficient delta check: client sends last known message_count,
        # server returns new messages only if count increased.
        conv_id = body.get("conversation_id", "")
        last_count = int(body.get("last_count", 0))
        if not conv_id:
            flowfile.set_content(json.dumps({"new_messages": []}).encode())
            return [flowfile]
        current_count = store.message_count(conv_id)
        if current_count <= last_count:
            flowfile.set_content(json.dumps({
                "new_messages": [], "message_count": current_count,
            }).encode())
            return [flowfile]
        # Load full history and return only the new portion
        all_messages = store.load(conv_id, user_id=user_id)
        if all_messages is None:
            flowfile.set_content(json.dumps({
                "new_messages": [], "message_count": 0,
            }).encode())
            return [flowfile]
        new_raw = all_messages[last_count:]
        new_classified = self._classify_messages_for_display(new_raw)
        flowfile.set_content(json.dumps({
            "new_messages": new_classified,
            "message_count": len(all_messages),
        }, ensure_ascii=False).encode())
        return [flowfile]

    if action == "export":
        fmt = body.get("format", "markdown")
        conv_id = body.get("conversation_id", "")
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "No conversation to export"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        msgs = store.load(conversation_id=conv_id, user_id=user_id)
        if not msgs:
            flowfile.set_content(json.dumps({"error": "Conversation not found or empty"}).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]

        if fmt == "json":
            export = json.dumps([
                {"role": m.get("role", ""), "content": m.get("content", ""),
                 "source": m.get("source", None)}
                if isinstance(m, dict) else
                {"role": m.role, "content": m.content,
                 "source": getattr(m, "source", None)}
                for m in msgs
            ], indent=2, ensure_ascii=False)
            filename = f"conversation_{conv_id[:8]}.json"
        else:
            lines = [f"# Conversation {conv_id[:8]}\n"]
            for m in msgs:
                if isinstance(m, dict):
                    role = (m.get("role") or "").upper()
                    source = m.get("source")
                    content = m.get("content", "")
                else:
                    role = (m.role or "").upper()
                    source = getattr(m, "source", None)
                    content = m.content if isinstance(m.content, str) else str(m.content)
                if source and isinstance(source, dict) and source.get("name"):
                    role = f"{role} ({source['name']})"
                lines.append(f"## {role}\n\n{content}\n")
            export = "\n".join(lines)
            filename = f"conversation_{conv_id[:8]}.md"

        # Store in FileStore for download
        from core.file_store import FileStore
        mime = "application/json" if fmt == "json" else "text/markdown"
        fid = FileStore.instance().store(filename, export.encode("utf-8"), mime,
                                           user_id=user_id)
        flowfile.set_content(json.dumps({
            "ok": True,
            "url": f"/files/{fid}/{filename}",
            "filename": filename,
            "format": fmt,
        }).encode())
        return [flowfile]

    # â”€â”€ Filesystem explorer actions â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    if action == "clear":
        conv_id = body.get("conversation_id", "")
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        _clear_msgs = store.load(conv_id, user_id=user_id)
        if not _clear_msgs:
            flowfile.set_content(json.dumps({"error": "Conversation not found"}).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]

        def _do_clear():
            deserialized = self._deserialize_messages(_clear_msgs)
            system_msgs = [m for m in deserialized if m.role == "system"]
            serialized_ctx = self._serialize_messages(system_msgs)
            # Clear shared context
            store.save_agent_context(conv_id, "", serialized_ctx)
            # Clear all agent-specific contexts
            agent_ctxs = store.list_agent_contexts(conv_id)
            count = 0
            for agent_name in agent_ctxs:
                if agent_name == "*":
                    continue
                store.save_agent_context(conv_id, agent_name, serialized_ctx)
                count += 1
            return {"cleared": True, "agents_reset": count + 1}

        return self._run_bg_context_op(conv_id, "clear", _do_clear, flowfile)

    if action == "clear_store":
        conv_id = body.get("conversation_id", "")
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        from core.file_store import FileStore
        fs = FileStore.instance()
        scope = body.get("scope", "")
        agent_name = body.get("agent_name", "")
        if agent_name:
            # Delete tool results for a specific agent
            count = fs.delete_by(category="tool_result",
                                 conversation_id=conv_id, agent_name=agent_name)
            flowfile.set_content(json.dumps({
                "deleted": count, "scope": f"agent:{agent_name}",
            }).encode())
        elif scope == "all_agents":
            # Delete all tool results for all agents in this conversation
            count = fs.delete_by(category="tool_result", conversation_id=conv_id)
            flowfile.set_content(json.dumps({
                "deleted": count, "scope": "all_agents",
            }).encode())
        else:
            # Delete ALL filestore files for this conversation
            count = fs.delete_by(conversation_id=conv_id)
            flowfile.set_content(json.dumps({
                "deleted": count, "scope": "conversation",
            }).encode())
        return [flowfile]

    if action == "loop_start":
        conv_id = body.get("conversation_id", "")
        interval = body.get("interval_seconds", 600)
        prompt = body.get("prompt", "")
        if not conv_id or not prompt:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id or prompt"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        from core.poll_scheduler import PollScheduler
        key = PollScheduler.instance().schedule_loop(
            conv_id, interval, prompt=prompt, user_id=user_id)
        flowfile.set_content(json.dumps({
            "started": True, "key": key, "interval": interval, "prompt": prompt,
        }).encode())
        return [flowfile]

    if action == "loop_stop":
        key = body.get("key", "")
        if not key:
            flowfile.set_content(json.dumps({"error": "Missing key"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        from core.poll_scheduler import PollScheduler
        ok = PollScheduler.instance().cancel(key)
        flowfile.set_content(json.dumps({"stopped": ok, "key": key}).encode())
        return [flowfile]

    if action == "loop_list":
        conv_id = body.get("conversation_id", "")
        from core.poll_scheduler import PollScheduler
        loops = PollScheduler.instance().list_loops(conv_id)
        flowfile.set_content(json.dumps({"loops": loops}).encode())
        return [flowfile]

    return None
