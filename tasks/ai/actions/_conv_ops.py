"""AgentLoopTask actions — conversation"""

import json
import logging

from tasks.ai.actions._conv_base import (
    _UNHANDLED,
)

logger = logging.getLogger(__name__)


def _handle_conv_ops(self, action, body, store, user_id, flowfile):
    """Conversation actions cluster: _conv_ops. Returns result or _UNHANDLED."""
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

    # Filesystem explorer actions

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
            deserialized = self._deserialize_messages(_clear_msgs, conversation_id=conv_id)
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
                        return ServiceRegistry.get_instance().resolve(
                            svc_id, user_id=user_id, conv_id=conv_id)
                    except Exception:
                        return self._find_filesystem_service(user_id, conv_id) if hasattr(self, '_find_filesystem_service') else None
                file_result = CheckpointManager.rewind_files(
                    conv_id, target_cp["id"], service_resolver=_svc_resolver)
                result["files"] = file_result
            else:
                result["files"] = {"error": "No matching checkpoint found for file rewind"}
        flowfile.set_content(json.dumps(result).encode())
        return [flowfile]

    return _UNHANDLED
