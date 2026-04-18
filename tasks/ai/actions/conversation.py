"""AgentLoopTask actions — conversation"""

import json
import logging
import time
import threading
from pathlib import Path
from typing import Dict, Any, List, Optional

from core import FlowFile
from core.llm_client import LLMMessage, LLMClient
from core.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


def _handle_conversation(self, action, body, store, user_id, flowfile):
    """Handle conversation actions. Returns [flowfile] or None."""


    if action == "list_conversations":
        convs = store.list_conversations(user_id=user_id)
        # Override persisted status with real-time active agent state
        try:
            from tasks.ai.agent_loop import AgentLoopTask
            inst = AgentLoopTask._live_instance
            if inst:
                _active_cids = set()
                with inst._active_contexts_lock:
                    for k in inst._active_contexts:
                        _active_cids.add(k.split(":")[0])
                for c in convs:
                    c["status"] = "active" if c["conversation_id"] in _active_cids else "idle"
        except Exception:
            pass
        for c in convs:
            branch = store.git_current_branch(c["conversation_id"])
            c["branch"] = branch or ""
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
        active_res = self._ensure_active_agent(conv_id, active_res, user_id)
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

    if action == "set_conv_title":
        conv_id = body.get("conversation_id", "")
        title = body.get("title", "").strip()
        if not conv_id or not title:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id or title"}).encode())
            return [flowfile]
        store.set_extra(conv_id, "title", title, user_id=user_id)
        flowfile.set_content(json.dumps({"ok": True, "title": title}).encode())
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
            # NO datetime in system prompt — breaks KV cache
            new_context = [
                LLMMessage(role="system", content=sys_prompt),
                LLMMessage(role="user",
                           content=f"[Conversation summary â€” earlier messages compacted]\n\n{summary}"),
                LLMMessage(role="assistant",
                           content="Understood. I have the context from our earlier conversation. Continuing from where we left off.",
                           source={"type": "context"}),
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
        # Load only the new messages (max 50) — never the full conversation
        delta = current_count - last_count
        if delta > 50:
            # Too many missed — client should just update count, not render all
            delta = 50
        page = store.load_page(conv_id, limit=delta, offset=0, user_id=user_id)
        if page is None:
            flowfile.set_content(json.dumps({
                "new_messages": [], "message_count": current_count,
            }).encode())
            return [flowfile]
        new_classified = self._classify_messages_for_display(page["messages"])
        flowfile.set_content(json.dumps({
            "new_messages": new_classified,
            "message_count": current_count,
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
                                           user_id=user_id, conversation_id=conv_id)
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

    # ── Git versioning ─────────────────────────────────────────────

    if action == "conv_git_log":
        conv_id = body.get("conversation_id", "")
        limit = int(body.get("limit", 30))
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            return [flowfile]
        commits = store.git_log(conv_id, limit=limit)
        tags = store.git_list_tags(conv_id)
        tag_by_commit = {t["commit"]: t["name"] for t in tags}
        for c in commits:
            c["tag"] = tag_by_commit.get(c["hash"][:7], "")
        flowfile.set_content(json.dumps({
            "commits": commits,
            "branch": store.git_current_branch(conv_id),
        }).encode())
        return [flowfile]

    if action == "conv_fork":
        conv_id = body.get("conversation_id", "")
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            return [flowfile]
        try:
            new_cid = store.fork(conv_id, user_id)
            flowfile.set_content(json.dumps({
                "ok": True, "conversation_id": new_cid, "source": conv_id,
            }).encode())
        except (RuntimeError, ValueError) as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "conv_branch":
        conv_id = body.get("conversation_id", "")
        branch_name = body.get("branch_name", "").strip()
        if not conv_id or not branch_name:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id or branch_name"}).encode())
            return [flowfile]
        try:
            ok = store.git_branch(conv_id, branch_name)
            flowfile.set_content(json.dumps({
                "ok": ok, "branch": branch_name,
            }).encode())
        except RuntimeError as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "conv_switch_branch":
        conv_id = body.get("conversation_id", "")
        branch_name = body.get("branch_name", "").strip()
        if not conv_id or not branch_name:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id or branch_name"}).encode())
            return [flowfile]
        try:
            ok = store.git_switch(conv_id, branch_name)
            flowfile.set_content(json.dumps({"ok": ok, "branch": branch_name}).encode())
        except RuntimeError as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "conv_list_branches":
        conv_id = body.get("conversation_id", "")
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            return [flowfile]
        branches = store.git_list_branches(conv_id)
        flowfile.set_content(json.dumps({
            "branches": branches,
            "current": store.git_current_branch(conv_id),
        }).encode())
        return [flowfile]

    if action == "conv_delete_branch":
        conv_id = body.get("conversation_id", "")
        branch_name = body.get("branch_name", "").strip()
        if not conv_id or not branch_name:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id or branch_name"}).encode())
            return [flowfile]
        try:
            ok = store.git_delete_branch(conv_id, branch_name)
            flowfile.set_content(json.dumps({"ok": ok}).encode())
        except ValueError as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "conv_rollback":
        conv_id = body.get("conversation_id", "")
        commit_hash = body.get("commit_hash", "").strip()
        rewind_files = body.get("rewind_files", False)
        if not conv_id or not commit_hash:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id or commit_hash"}).encode())
            return [flowfile]
        try:
            store._require_idle(conv_id)
        except RuntimeError as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]
        ok = store.git_rollback(conv_id, commit_hash)
        result = {"ok": ok, "commit": commit_hash}
        if rewind_files:
            from core.checkpoint import CheckpointManager
            checkpoints = CheckpointManager.list_checkpoints(conv_id)
            # Find closest checkpoint by git commit timestamp
            commit_log = store.git_log(conv_id, limit=100)
            target_ts = 0
            for c in commit_log:
                if c["hash"].startswith(commit_hash):
                    target_ts = c["timestamp"]
                    break
            target_cp = None
            for cp in checkpoints:
                if cp.get("timestamp", 0) <= target_ts:
                    target_cp = cp
            if target_cp:
                def _svc_resolver(svc_id):
                    try:
                        from core.service_registry import ServiceRegistry
                        return ServiceRegistry.get_instance().get_live_instance("global", "", svc_id)
                    except Exception:
                        return self._find_filesystem_service(user_id) if hasattr(self, '_find_filesystem_service') else None
                file_result = CheckpointManager.rewind_files(
                    conv_id, target_cp["id"], service_resolver=_svc_resolver)
                result["files"] = file_result
            else:
                result["files"] = {"error": "No matching checkpoint found for file rewind"}
        flowfile.set_content(json.dumps(result).encode())
        return [flowfile]

    if action == "conv_tag":
        conv_id = body.get("conversation_id", "")
        tag_name = body.get("tag_name", "").strip()
        message = body.get("message", "")
        if not conv_id or not tag_name:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id or tag_name"}).encode())
            return [flowfile]
        ok = store.git_tag(conv_id, tag_name, message)
        flowfile.set_content(json.dumps({"ok": ok, "tag": tag_name}).encode())
        return [flowfile]

    if action == "conv_list_tags":
        conv_id = body.get("conversation_id", "")
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            return [flowfile]
        tags = store.git_list_tags(conv_id)
        flowfile.set_content(json.dumps({"tags": tags}).encode())
        return [flowfile]

    if action == "conv_delete_tag":
        conv_id = body.get("conversation_id", "")
        tag_name = body.get("tag_name", "").strip()
        if not conv_id or not tag_name:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id or tag_name"}).encode())
            return [flowfile]
        ok = store.git_delete_tag(conv_id, tag_name)
        flowfile.set_content(json.dumps({"ok": ok}).encode())
        return [flowfile]

    if action == "conv_export_pawflow":
        conv_id = body.get("conversation_id", "")
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            return [flowfile]
        import io, zipfile
        from core.file_store import FileStore
        conv_dir = store._conv_dir(conv_id)
        if not conv_dir.is_dir():
            flowfile.set_content(json.dumps({"error": "Conversation not found"}).encode())
            return [flowfile]
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(conv_dir.rglob('*')):
                if f.is_file() and '.git' not in f.parts:
                    arcname = str(f.relative_to(conv_dir))
                    zf.write(f, arcname)
        filename = f"conversation_{conv_id[:8]}.pfconv.zip"
        fid = FileStore.instance().store(filename, buf.getvalue(),
            "application/zip", user_id=user_id, conversation_id=conv_id)
        flowfile.set_content(json.dumps({
            "ok": True, "url": f"/files/{fid}/{filename}", "filename": filename,
        }).encode())
        return [flowfile]

    if action == "conv_export_claude_code":
        conv_id = body.get("conversation_id", "")
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            return [flowfile]
        from core.file_store import FileStore
        msgs = store.load(conversation_id=conv_id, user_id=user_id)
        if not msgs:
            flowfile.set_content(json.dumps({"error": "Conversation empty"}).encode())
            return [flowfile]
        lines = []
        for m in msgs:
            role = m.get("role", "") if isinstance(m, dict) else getattr(m, "role", "")
            content = m.get("content", "") if isinstance(m, dict) else getattr(m, "content", "")
            if role == "user":
                lines.append(json.dumps({"type": "human", "message": {"role": "user", "content": content}}, ensure_ascii=False))
            elif role == "assistant":
                lines.append(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": content}}, ensure_ascii=False))
            elif role == "tool":
                lines.append(json.dumps({"type": "tool_result", "message": {"role": "user", "content": content}}, ensure_ascii=False))
        export = "\n".join(lines) + "\n"
        filename = f"conversation_{conv_id[:8]}.cc.jsonl"
        fid = FileStore.instance().store(filename, export.encode("utf-8"),
            "application/jsonl", user_id=user_id, conversation_id=conv_id)
        flowfile.set_content(json.dumps({
            "ok": True, "url": f"/files/{fid}/{filename}", "filename": filename,
        }).encode())
        return [flowfile]

    if action == "conv_compare_branches":
        conv_id = body.get("conversation_id", "")
        branch_a = body.get("branch_a", "").strip()
        branch_b = body.get("branch_b", "").strip()
        if not conv_id or not branch_a or not branch_b:
            flowfile.set_content(json.dumps({"error": "Missing parameters"}).encode())
            return [flowfile]
        result = store.git_compare_branches(conv_id, branch_a, branch_b)
        flowfile.set_content(json.dumps(result).encode())
        return [flowfile]

    if action == "conv_import_cleanup":
        import tempfile, shutil
        temp_id = body.get("temp_id", "")
        if temp_id:
            temp_dir = Path(tempfile.gettempdir()) / f"pf_import_{temp_id}"
            shutil.rmtree(temp_dir, ignore_errors=True)
        flowfile.set_content(json.dumps({"ok": True}).encode())
        return [flowfile]

    if action == "conv_import_analyze":
        import tempfile, uuid
        fmt = body.get("format", "")
        file_id = body.get("file_id", "")
        if not file_id or fmt not in ("pawflow", "claude_code"):
            flowfile.set_content(json.dumps({"error": "Missing file_id or invalid format"}).encode())
            return [flowfile]
        from core.file_store import FileStore
        fs = FileStore.instance()
        result = fs.get(file_id, user_id=user_id)
        if result is None:
            flowfile.set_content(json.dumps({"error": "Upload not found or expired"}).encode())
            return [flowfile]
        _fname, raw, _ct = result
        # Delete the uploaded file from FileStore — we copy raw to temp
        fs.delete(file_id)
        temp_id = uuid.uuid4().hex[:16]
        temp_dir = Path(tempfile.gettempdir()) / f"pf_import_{temp_id}"
        temp_dir.mkdir(parents=True, exist_ok=True)
        (temp_dir / "raw").write_bytes(raw)
        agents_found = []
        message_count = 0
        if fmt == "pawflow":
            import zipfile, io
            try:
                with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                    if "transcript.jsonl" not in zf.namelist():
                        flowfile.set_content(json.dumps({"error": "Not a valid PawFlow archive (missing transcript.jsonl)"}).encode())
                        return [flowfile]
                    # Count messages
                    for line in zf.read("transcript.jsonl").decode("utf-8", errors="replace").splitlines():
                        if line.strip(): message_count += 1
                    # Extract agents from extras.json
                    if "extras.json" in zf.namelist():
                        extras = json.loads(zf.read("extras.json"))
                        conv_agents = extras.get("conv_agents", {})
                        for name, cfg in conv_agents.items():
                            agents_found.append({"name": name, "definition": cfg.get("definition", name)})
            except zipfile.BadZipFile:
                flowfile.set_content(json.dumps({"error": "Invalid zip file"}).encode())
                return [flowfile]
        elif fmt == "claude_code":
            text = raw.decode("utf-8", errors="replace")
            for line in text.splitlines():
                if line.strip(): message_count += 1
            agents_found = [{"name": "claude", "definition": "claude"}]
        flowfile.set_content(json.dumps({
            "ok": True, "temp_id": temp_id, "format": fmt,
            "agents": agents_found, "message_count": message_count,
        }).encode())
        return [flowfile]

    if action == "conv_import_execute":
        import base64, tempfile, uuid as _uuid
        temp_id = body.get("temp_id", "")
        fmt = body.get("format", "")
        agent_mapping = body.get("agent_mapping", {})  # {import_name: {definition, params, llm_service}}
        title = body.get("title", "Imported conversation")
        if not temp_id:
            flowfile.set_content(json.dumps({"error": "Missing temp_id"}).encode())
            return [flowfile]
        temp_dir = Path(tempfile.gettempdir()) / f"pf_import_{temp_id}"
        raw_file = temp_dir / "raw"
        if not raw_file.exists():
            flowfile.set_content(json.dumps({"error": "Import data expired"}).encode())
            return [flowfile]
        raw = raw_file.read_bytes()
        cid = _uuid.uuid4().hex[:16] + _uuid.uuid4().hex[:16]
        conv_dir = store._store_dir / store._safe_name(user_id) / store._safe_name(cid)
        conv_dir.mkdir(parents=True, exist_ok=True)
        if fmt == "pawflow":
            import zipfile, io
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                zf.extractall(conv_dir)
            # Update extras with new cid, user, and agent mapping
            extras_path = conv_dir / "extras.json"
            if extras_path.exists():
                extras = json.loads(extras_path.read_text(encoding="utf-8"))
            else:
                extras = {}
            extras["conversation_id"] = cid
            extras["user_id"] = user_id
            extras["title"] = title or extras.get("title", "Imported")
            # Remap agents
            if agent_mapping:
                new_conv_agents = {}
                for imp_name, mapping in agent_mapping.items():
                    new_conv_agents[imp_name] = {
                        "definition": mapping.get("definition", imp_name),
                        "params": mapping.get("params", {"name": imp_name}),
                        "llm_service": mapping.get("llm_service", ""),
                    }
                extras["conv_agents"] = new_conv_agents
                if new_conv_agents:
                    extras["selectedAgent"] = list(new_conv_agents.keys())[0]
            extras_path.write_text(json.dumps(extras, ensure_ascii=False, indent=2), encoding="utf-8")
        elif fmt == "claude_code":
            # Convert CC JSONL to PawFlow transcript
            text = raw.decode("utf-8", errors="replace")
            transcript_lines = []
            msg_id_counter = 0
            import uuid as _u2
            for line in text.splitlines():
                line = line.strip()
                if not line: continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg_type = entry.get("type", "")
                message = entry.get("message", {})
                content = message.get("content", "")
                ts = time.time()
                mid = _u2.uuid4().hex[:12]
                if msg_type == "human":
                    transcript_lines.append(json.dumps({"t": "msg", "role": "user", "content": content, "msg_id": mid, "timestamp": ts}, ensure_ascii=False))
                elif msg_type == "assistant":
                    # CC content can be array of blocks
                    if isinstance(content, list):
                        text_parts = [b.get("text", "") for b in content if b.get("type") == "text"]
                        content = "\n".join(text_parts)
                    transcript_lines.append(json.dumps({"t": "msg", "role": "assistant", "content": content, "msg_id": mid, "timestamp": ts}, ensure_ascii=False))
                elif msg_type == "tool_result":
                    transcript_lines.append(json.dumps({"t": "msg", "role": "tool", "content": str(content), "msg_id": mid, "timestamp": ts}, ensure_ascii=False))
            (conv_dir / "transcript.jsonl").write_text("\n".join(transcript_lines) + "\n", encoding="utf-8")
            # Create minimal extras
            agent_name = list(agent_mapping.keys())[0] if agent_mapping else "claude"
            agent_cfg = agent_mapping.get(agent_name, {"definition": "claude", "params": {"name": agent_name}, "llm_service": ""})
            extras = {
                "conversation_id": cid,
                "user_id": user_id,
                "title": title,
                "selectedAgent": agent_name,
                "conv_agents": {
                    agent_name: {
                        "definition": agent_cfg.get("definition", "claude"),
                        "params": agent_cfg.get("params", {"name": agent_name}),
                        "llm_service": agent_cfg.get("llm_service", ""),
                    }
                },
            }
            (conv_dir / "extras.json").write_text(json.dumps(extras, ensure_ascii=False, indent=2), encoding="utf-8")
        # Init git
        store._cid_user[cid] = user_id
        store._git_init(cid)
        # Cleanup temp
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)
        flowfile.set_content(json.dumps({
            "ok": True, "conversation_id": cid,
        }).encode())
        return [flowfile]

    return None
