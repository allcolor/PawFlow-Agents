"""AgentLoopTask actions — context ops"""

import json
import logging
import threading
import time
import threading
from typing import Dict, Any, List, Optional

from core import FlowFile
from core.llm_client import LLMMessage, LLMClient
from core.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


def _handle_context_ops(self, action, body, store, user_id, flowfile):
    """Handle context ops actions. Returns [flowfile] or None."""

    def _ctx_load(conv_id, agent_name=""):
        """Load context for an agent (falls back to shared → messages)."""
        if agent_name and agent_name != "ALL":
            return store.load_agent_context(conv_id, agent_name)
        return store.load_context(conv_id, user_id=user_id)

    def _ctx_save(conv_id, data, agent_name=""):
        """Save context for an agent (or shared if no agent)."""
        if agent_name and agent_name != "ALL":
            store.save_agent_context(conv_id, agent_name, data)
        else:
            store.save_context(conv_id, data)

    def _resolve_agent_max_tokens(agent_name):
        """Get max_tokens from an agent's LLM service config."""
        _, _, svc = self._resolve_agent_client(agent_name, user_id)
        if svc:
            v = int((getattr(svc, 'config', {}) or {}).get("max_context_size", 0))
            if v:
                return v
        return 0

    def _ctx_max_tokens(agent_name=""):
        """Get max_context_size for an agent or shared context."""
        flow_default = int(self.config.get("max_context_size", 64000))
        if agent_name and agent_name not in ("", "ALL"):
            return _resolve_agent_max_tokens(agent_name) or flow_default
        try:
            from core.resource_store import ResourceStore
            all_agents = ResourceStore.instance().list_all("agent", user_id)
            max_val = 0
            for a in all_agents:
                v = _resolve_agent_max_tokens(a["name"])
                if v > max_val:
                    max_val = v
            default_svc = self.config.get("llm_service", "default")
            if default_svc and "${" not in default_svc:
                _, svc = self._resolve_llm_service(default_svc, user_id)
                if svc:
                    v = int((getattr(svc, 'config', {}) or {}).get("max_context_size", 0))
                    if v > max_val:
                        max_val = v
            return max_val or flow_default
        except Exception:
            return flow_default

    # ── /rewind ──
    if action == "rewind":
        conv_id = body.get("conversation_id", "")
        checkpoint_arg = body.get("checkpoint", "").strip()
        mode = body.get("mode", "")  # "code", "conversation", "both", "summarize"
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            return [flowfile]

        from core.checkpoint import CheckpointManager
        checkpoints = CheckpointManager.list_checkpoints(conv_id)

        if not checkpoint_arg:
            # List available checkpoints
            if not checkpoints:
                flowfile.set_content(json.dumps({
                    "message": "No checkpoints available. Checkpoints are created automatically on each user message.",
                    "checkpoints": [],
                }).encode())
                return [flowfile]
            # Include message preview for each checkpoint
            cp_list = []
            for i, cp in enumerate(checkpoints):
                from datetime import datetime
                ts = datetime.fromtimestamp(cp.get("timestamp", 0)).strftime("%H:%M:%S")
                cp_list.append({
                    "index": i + 1,
                    "id": cp["id"],
                    "timestamp": ts,
                    "message_count": cp.get("message_count", 0),
                })
            lines = ["## Checkpoints\n"]
            for c in cp_list:
                lines.append(f"  {c['index']}. `{c['id']}` ({c['timestamp']}) — {c['message_count']} messages")
            lines.append(f"\nUse `/rewind <number>` to rewind to a checkpoint.")
            flowfile.set_content(json.dumps({
                "message": "\n".join(lines),
                "checkpoints": cp_list,
            }).encode())
            return [flowfile]

        # Resolve checkpoint: by index or by ID
        target_cp = None
        if checkpoint_arg.isdigit():
            idx = int(checkpoint_arg) - 1
            if 0 <= idx < len(checkpoints):
                target_cp = checkpoints[idx]
        else:
            for cp in checkpoints:
                if cp["id"] == checkpoint_arg or cp["id"].startswith(checkpoint_arg):
                    target_cp = cp
                    break

        if not target_cp:
            flowfile.set_content(json.dumps({
                "error": f"Checkpoint '{checkpoint_arg}' not found. Use /rewind to list.",
            }).encode())
            return [flowfile]

        results = {"checkpoint": target_cp["id"]}

        # Default mode: both code and conversation
        if not mode:
            mode = "both"

        # Rewind files
        if mode in ("code", "both"):
            def _svc_resolver(svc_id):
                if svc_id:
                    try:
                        from gui.services.global_service_registry import GlobalServiceRegistry
                        return GlobalServiceRegistry.get_instance().get_live_instance(svc_id)
                    except Exception:
                        pass
                # Default: try to find any filesystem service
                try:
                    return self._find_filesystem_service(user_id)
                except Exception:
                    return None

            file_result = CheckpointManager.rewind_files(
                conv_id, target_cp["id"], service_resolver=_svc_resolver)
            results["files"] = file_result

        # Rewind conversation
        if mode in ("conversation", "both"):
            target_msg_count = target_cp.get("message_count", 0)
            if target_msg_count > 0:
                # Truncate transcript to checkpoint point
                all_msgs = store.load(conv_id, user_id=user_id)
                if all_msgs and len(all_msgs) > target_msg_count:
                    store.save(conv_id, all_msgs[:target_msg_count], user_id=user_id)
                # Clear agent contexts (they'll be rebuilt on next message)
                extras = store.get_extras(conv_id, user_id=user_id) or {}
                for k in list(extras.keys()):
                    if k.startswith("agent_context:") or k == "agent_context":
                        store.set_extra(conv_id, k, None, user_id=user_id)
                results["conversation"] = {
                    "messages_before": len(all_msgs) if all_msgs else 0,
                    "messages_after": target_msg_count,
                }

        # Summarize mode (compact from checkpoint point)
        if mode == "summarize":
            # TODO: implement summarize-from-here
            results["summarize"] = "Not implemented yet"

        # Build response message
        lines = [f"## Rewound to checkpoint {target_cp['id']}"]
        if "files" in results:
            fr = results["files"]
            lines.append(f"Files: {fr.get('restored', 0)} restored, "
                         f"{fr.get('deleted', 0)} deleted")
            if fr.get("errors"):
                for e in fr["errors"][:5]:
                    lines.append(f"  ⚠ {e}")
        if "conversation" in results:
            cr = results["conversation"]
            lines.append(f"Conversation: {cr['messages_before']} → "
                         f"{cr['messages_after']} messages")
        results["message"] = "\n".join(lines)
        flowfile.set_content(json.dumps(results).encode())
        return [flowfile]

    if action == "compact":
        conv_id = body.get("conversation_id", "")
        _ctx_agent = body.get("agent_name", "")
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        # Load source data
        context_data = _ctx_load(conv_id, _ctx_agent)
        source_data = context_data if context_data is not None else store.load(conv_id, user_id=user_id)
        # Filter out display-only sub-agent traces
        if source_data:
            source_data = [m for m in source_data if not (isinstance(m, dict) and m.get("display_only"))]
        if not source_data or len(source_data) < 4:
            flowfile.set_content(json.dumps({"error": "Not enough messages to compact"}).encode())
            return [flowfile]
        # Resolve client
        _summ_client, _ = self._get_summarizer_client(user_id)
        if _summ_client:
            _compact_client = _summ_client
        else:
            svc_id = self.config.get("llm_service", "")
            if not svc_id or "${" in svc_id:
                svc_id = "default"
            _compact_client, _ = self._resolve_client(
                svc_id, user_id, resolve_expressions=False,
            )
        if not _compact_client:
            flowfile.set_content(json.dumps({"error": "LLM service not found"}).encode())
            return [flowfile]
        _compact_max = _ctx_max_tokens(_ctx_agent)
        _compact_source = source_data
        _compact_conv = conv_id
        _compact_agent_name = _ctx_agent
        _compact_keep = int(self.config.get("context_keep_recent", 6))

        _compact_instructions = body.get("instructions", "")

        def _do_compact():
            msgs = self._deserialize_messages(_compact_source)
            # Inject focus instructions if provided (like Claude Code's /compact <focus>)
            if _compact_instructions:
                from core.llm_client import LLMMessage as _LM
                msgs.insert(1 if msgs and msgs[0].role == "system" else 0,
                    _LM(role="user", content=(
                        f"[Compaction focus: {_compact_instructions}. "
                        f"Prioritize retaining information about this topic.]")))
            before = len(msgs)
            estimated = self._estimate_tokens(msgs)
            compacted = self._compact_if_needed(
                msgs, _compact_client, _compact_max, 0.5,
                _compact_keep, conversation_id=_compact_conv,
                agent_name=_compact_agent_name,
            )
            after_tokens = self._estimate_tokens(compacted)
            return {"before": before, "after": len(compacted),
                    "tokens_before": estimated, "tokens_after": after_tokens,
                    "agent": _compact_agent_name or "shared",
                    "focus": _compact_instructions or None}

        return self._run_bg_context_op(conv_id, "compact", _do_compact, flowfile)

    if action == "rebuild":
        conv_id = body.get("conversation_id", "")
        _rb_agent = body.get("agent_name", "")
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        _rb_msgs = store.load(conv_id, user_id=user_id)
        # Filter out display-only sub-agent traces
        if _rb_msgs:
            _rb_msgs = [m for m in _rb_msgs if not (isinstance(m, dict) and m.get("display_only"))]
        if not _rb_msgs:
            flowfile.set_content(json.dumps({"error": "Conversation not found"}).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        # Resolve client for potential compaction
        _summ_client, _ = self._get_summarizer_client(user_id)
        _rb_client = _summ_client
        if not _rb_client:
            _rb_client, _ = self._resolve_client(
                self.config.get("llm_service", "default"),
                user_id, resolve_expressions=False,
            )
        _rb_max = _ctx_max_tokens(_rb_agent)

        def _do_rebuild():
            deserialized = self._deserialize_messages(_rb_msgs)
            estimated = self._estimate_tokens(deserialized)
            limit = int(_rb_max * 0.8)
            if estimated <= limit:
                _ctx_save(conv_id, _rb_msgs, _rb_agent)
                return {"action": "full_restore", "before": len(_rb_msgs),
                        "after": len(_rb_msgs), "tokens_after": estimated,
                        "agent": _rb_agent or "shared"}
            if not _rb_client:
                raise ValueError("No LLM service for compaction")
            compacted = self._compact_if_needed(
                deserialized, _rb_client, _rb_max, 0.8,
                int(self.config.get("context_keep_recent", 6)),
                conversation_id=conv_id, agent_name=_rb_agent,
            )
            return {"action": "compacted", "before": len(_rb_msgs),
                    "after": len(compacted),
                    "tokens_after": self._estimate_tokens(compacted),
                    "agent": _rb_agent or "shared"}

        return self._run_bg_context_op(conv_id, "rebuild", _do_rebuild, flowfile)

    if action in ("rebuild_clean", "rebuild_full"):
        conv_id = body.get("conversation_id", "")
        _rf_agent = body.get("agent_name", "")
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        _rf_msgs = store.load(conv_id, user_id=user_id)
        # Filter out display-only sub-agent traces
        if _rf_msgs:
            _rf_msgs = [m for m in _rf_msgs if not (isinstance(m, dict) and m.get("display_only"))]
        if not _rf_msgs:
            flowfile.set_content(json.dumps({"error": "Conversation not found"}).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]

        def _do_rebuild_full():
            deserialized = self._deserialize_messages(_rf_msgs)
            estimated = self._estimate_tokens(deserialized)
            if _rf_agent == "ALL":
                agent_map = store.list_agent_contexts(conv_id)
                for name in agent_map:
                    if name == "*":
                        store.save_context(conv_id, list(_rf_msgs))
                    else:
                        store.save_agent_context(conv_id, name, list(_rf_msgs))
            else:
                _ctx_save(conv_id, list(_rf_msgs), _rf_agent)
            return {"action": "full_restore", "before": len(_rf_msgs),
                    "after": len(_rf_msgs), "tokens_after": estimated,
                    "agent": _rf_agent or "shared"}

        return self._run_bg_context_op(conv_id, "rebuild_full", _do_rebuild_full, flowfile)
        return [flowfile]

    if action == "get_context":
        conv_id = body.get("conversation_id", "")
        _ctx_agent = body.get("agent_name", "")
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        # Sub-conversation context: load from sub-conv directly
        if _ctx_agent.startswith("task:"):
            _sub_tid = _ctx_agent.split("(")[0].replace("task:", "").strip()
            _sub_cid = f"{conv_id}::task::{_sub_tid}"
            context_data = store.load(_sub_cid) or []
            diverged = True
        else:
            context_data = _ctx_load(conv_id, _ctx_agent)
            diverged = context_data is not None
            if context_data is None:
                context_data = store.load(conv_id, user_id=user_id) or []
        deserialized = self._deserialize_messages(context_data)
        estimated = self._estimate_tokens(deserialized)
        # Classify messages for display
        display_msgs = []
        for m in context_data:
            role = m.get("role", "unknown")
            content = m.get("content", "")
            if isinstance(content, list):
                text_parts = [p.get("text", "") for p in content if p.get("type") == "text"]
                content = "\n".join(text_parts) if text_parts else str(content)
            has_tool_calls = bool(m.get("tool_calls"))
            display_msgs.append({
                "role": role,
                "content": content[:300] if isinstance(content, str) else str(content)[:300],
                "has_tool_calls": has_tool_calls,
                "source": m.get("source"),
            })
        # Include agent context status map
        _agent_ctx_map = store.list_agent_contexts(conv_id)
        # Include active sub-conversations (task contexts)
        _extras = store.get_extras(conv_id, user_id=user_id) or {}
        for ek in _extras:
            if ek.startswith("task_log:"):
                _tid = ek[9:]
                _sub_cid = f"{conv_id}::task::{_tid}"
                _sub_msgs = store.load(_sub_cid)
                if _sub_msgs:
                    # Find agent name from task data
                    _tasks_data = _extras.get("agent_tasks", {})
                    _t_entry = _tasks_data.get(_tid, {}) if isinstance(_tasks_data, dict) else {}
                    _t_agent = _t_entry.get("agent", "?")
                    _agent_ctx_map[f"task:{_tid} ({_t_agent})"] = "sub-conv"
        flowfile.set_content(json.dumps({
            "context": display_msgs,
            "message_count": len(context_data),
            "token_estimate": estimated,
            "diverged": diverged,
            "agent_name": _ctx_agent or "",
            "agent_contexts": _agent_ctx_map,
        }, ensure_ascii=False).encode())
        return [flowfile]

    if action == "get_context_full":
        conv_id = body.get("conversation_id", "")
        _ctx_agent = body.get("agent_name", "")
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        context_data = _ctx_load(conv_id, _ctx_agent)
        diverged = context_data is not None
        if context_data is None:
            context_data = store.load(conv_id, user_id=user_id) or []
        flowfile.set_content(json.dumps({
            "context": context_data,
            "message_count": len(context_data),
            "diverged": diverged,
        }, ensure_ascii=False).encode())
        return [flowfile]

    if action == "edit_context":
        conv_id = body.get("conversation_id", "")
        _ctx_agent = body.get("agent_name", "")
        index = body.get("index")
        new_content = body.get("content", "")
        new_role = body.get("role")
        if not conv_id or index is None:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id or index"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        context_data = _ctx_load(conv_id, _ctx_agent)
        _using_context = context_data is not None
        if context_data is None:
            context_data = store.load(conv_id, user_id=user_id) or []
        if index < 0 or index >= len(context_data):
            flowfile.set_content(json.dumps({
                "error": f"Index {index} out of range (0-{len(context_data)-1}). "
                         "The context may have changed â€” please refresh.",
            }).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        context_data[index]["content"] = new_content
        if new_role:
            context_data[index]["role"] = new_role
        _ctx_save(conv_id, context_data)
        deserialized = self._deserialize_messages(context_data)
        estimated = self._estimate_tokens(deserialized)
        flowfile.set_content(json.dumps({
            "ok": True,
            "message_count": len(context_data),
            "token_estimate": estimated,
        }).encode())
        return [flowfile]

    if action == "delete_sub_context":
        conv_id = body.get("conversation_id", "")
        sub_name = body.get("agent_name", "")  # "task:t_xxx (AgentName)"
        if not conv_id or not sub_name or not sub_name.startswith("task:"):
            flowfile.set_content(json.dumps({"error": "Missing conversation_id or task agent_name"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        _sub_tid = sub_name.split("(")[0].replace("task:", "").strip()
        _sub_cid = f"{conv_id}::task::{_sub_tid}"
        try:
            store.delete(_sub_cid)
            # Also clean up sync counter and task log
            store.set_extra(conv_id, f"_sub_sync:{_sub_cid}", None)
            store.set_extra(conv_id, f"task_log:{_sub_tid}", None)
            flowfile.set_content(json.dumps({"ok": True, "deleted": _sub_cid}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "delete_context_message":
        conv_id = body.get("conversation_id", "")
        _ctx_agent = body.get("agent_name", "")
        index = body.get("index")
        if not conv_id or index is None:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id or index"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        context_data = _ctx_load(conv_id, _ctx_agent)
        if context_data is None:
            context_data = store.load(conv_id, user_id=user_id) or []
        if index < 0 or index >= len(context_data):
            # Index from overlay may target messages if context was compacted;
            # fall back to messages list
            msgs = store.load(conv_id, user_id=user_id) or []
            if 0 <= index < len(msgs):
                msgs.pop(index)
                store.save(conv_id, msgs, user_id=user_id)
                deserialized = self._deserialize_messages(msgs)
                estimated = self._estimate_tokens(deserialized)
                flowfile.set_content(json.dumps({
                    "ok": True,
                    "message_count": len(msgs),
                    "token_estimate": estimated,
                }).encode())
                return [flowfile]
            flowfile.set_content(json.dumps({
                "error": f"Index {index} out of range (0-{len(context_data)-1}). "
                         "The context may have changed â€” please refresh.",
            }).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        context_data.pop(index)
        _ctx_save(conv_id, context_data)
        deserialized = self._deserialize_messages(context_data)
        estimated = self._estimate_tokens(deserialized)
        flowfile.set_content(json.dumps({
            "ok": True,
            "message_count": len(context_data),
            "token_estimate": estimated,
        }).encode())
        return [flowfile]

    if action == "replace_context":
        conv_id = body.get("conversation_id", "")
        _ctx_agent = body.get("agent_name", "")
        new_context = body.get("context", [])
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        for msg in new_context:
            if "role" not in msg or "content" not in msg:
                flowfile.set_content(json.dumps({"error": "Each message must have 'role' and 'content'"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
        _ctx_save(conv_id, new_context)
        deserialized = self._deserialize_messages(new_context)
        estimated = self._estimate_tokens(deserialized)
        flowfile.set_content(json.dumps({
            "ok": True,
            "message_count": len(new_context),
            "token_estimate": estimated,
        }).encode())
        return [flowfile]

    if action == "add_context_message":
        conv_id = body.get("conversation_id", "")
        _ctx_agent = body.get("agent_name", "")
        role = body.get("role", "user")
        content = body.get("content", "")
        index = body.get("index")
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        context_data = _ctx_load(conv_id, _ctx_agent)
        if context_data is None:
            context_data = store.load(conv_id, user_id=user_id) or []
        msg = {"role": role, "content": content}
        if index is not None:
            context_data.insert(index, msg)
        else:
            context_data.append(msg)
        _ctx_save(conv_id, context_data)
        deserialized = self._deserialize_messages(context_data)
        estimated = self._estimate_tokens(deserialized)
        flowfile.set_content(json.dumps({
            "ok": True,
            "message_count": len(context_data),
            "token_estimate": estimated,
        }).encode())
        return [flowfile]

    if action == "restart_from":
        conv_id = body.get("conversation_id", "")
        _rf_agent = body.get("agent_name", "")
        keep_last = int(body.get("keep_last", 5))
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        _rf_msgs = store.load(conv_id, user_id=user_id)
        # Filter out display-only sub-agent traces
        if _rf_msgs:
            _rf_msgs = [m for m in _rf_msgs if not (isinstance(m, dict) and m.get("display_only"))]
        if not _rf_msgs:
            flowfile.set_content(json.dumps({"error": "Conversation not found"}).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]

        def _do_restart():
            deserialized = self._deserialize_messages(_rf_msgs)
            system_msgs = [m for m in deserialized if m.role == "system"]
            non_system = [m for m in deserialized if m.role != "system"]
            if keep_last == 0:
                new_context = system_msgs
            else:
                kept = non_system[-keep_last:] if len(non_system) > keep_last else non_system
                new_context = system_msgs + kept
            serialized_ctx = self._serialize_messages(new_context)
            store.save_agent_context(conv_id, _rf_agent, serialized_ctx)
            return {"kept_messages": len(new_context) - len(system_msgs),
                    "agent": _rf_agent or "shared"}

        return self._run_bg_context_op(conv_id, "restart_from", _do_restart, flowfile)

    if action == "delete_message":
        conv_id = body.get("conversation_id", "")
        msg_index = body.get("index")
        if not conv_id or msg_index is None:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id or index"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        deleted = store.delete_message(conv_id, int(msg_index), user_id=user_id)
        flowfile.set_content(json.dumps({
            "deleted": deleted, "conversation_id": conv_id,
            "message_count": store.message_count(conv_id),
        }).encode())
        return [flowfile]

    return None
