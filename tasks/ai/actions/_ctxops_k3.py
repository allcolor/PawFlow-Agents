"""AgentLoopTask actions — context ops"""

import json
import logging

from core.task_lifecycle import cleanup_agent_task_context
from tasks.ai.actions._ctxops_base import (
    _UNHANDLED,
    _estimate_unavailable,
    _load_cc_session_context,
    _load_codex_session_context,
    _load_gemini_session_context,
)

logger = logging.getLogger(__name__)


def _handle_ctxops_k3(self, action, body, store, user_id, flowfile, _helpers):
    """context_ops cluster _ctxops_k3. Returns result or _UNHANDLED."""
    (_ctx_agent_name, _ctx_load, _ctx_save, _ctx_cached_usage,
     _ctx_visible_contexts, _ctx_llm_service_config, _ctx_real_context_size, _ctx_max_tokens) = _helpers
    if action == "get_context":
        conv_id = body.get("conversation_id", "")
        _ctx_agent = body.get("agent_name", "")
        _limit = int(body.get("limit", 50))
        _offset = int(body.get("offset", 0))
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        # Load paginated context (tail-first like load_page)
        if _ctx_agent == "transcript":
            page = store.load_page(conv_id, limit=_limit, offset=_offset, user_id=user_id)
            if page is None:
                flowfile.set_content(json.dumps({"error": "Conversation not found"}).encode())
                return [flowfile]
            context_data = page["messages"]
            total_count = page["total_count"]
            has_more = page["has_more"]
            diverged = False
        elif not _ctx_agent or _ctx_agent == "shared":
            # Shared context — page shared.jsonl directly instead of loading
            # thousands of rows just to render the first 50.
            page = store.load_agent_context_page(
                conv_id, "", limit=_limit, offset=_offset)
            if page is None:
                page = store.load_shared_page(
                    conv_id, user_id=user_id, limit=_limit, offset=_offset) or {}
            context_data = page.get("messages") or []
            total_count = page.get("total_count", len(context_data))
            has_more = page.get("has_more", False)
            diverged = True
        elif _ctx_agent.startswith("cc_session:"):
            _cc_agent = _ctx_agent[len("cc_session:"):]
            page = _load_cc_session_context(
                conv_id, _cc_agent, store, user_id=user_id,
                limit=_limit, offset=_offset)
            context_data = page.get("messages") or []
            total_count = page.get("total_count", len(context_data))
            has_more = page.get("has_more", False)
            diverged = True
        elif _ctx_agent.startswith("codex_session:"):
            _codex_agent = _ctx_agent[len("codex_session:"):]
            page = _load_codex_session_context(
                conv_id, _codex_agent, store, user_id=user_id,
                limit=_limit, offset=_offset)
            context_data = page.get("messages") or []
            total_count = page.get("total_count", len(context_data))
            has_more = page.get("has_more", False)
            diverged = True
        elif _ctx_agent.startswith("gemini_session:"):
            _gemini_agent = _ctx_agent[len("gemini_session:"):]
            page = _load_gemini_session_context(
                conv_id, _gemini_agent, store, user_id=user_id,
                limit=_limit, offset=_offset)
            context_data = page.get("messages") or []
            total_count = page.get("total_count", len(context_data))
            has_more = page.get("has_more", False)
            diverged = True
        elif _ctx_agent.startswith("task:"):
            _sub_tid = _ctx_agent.split("(")[0].replace("task:", "").strip()
            _sub_cid = f"{conv_id}::task::{_sub_tid}"
            page = store.load_page(_sub_cid, limit=_limit, offset=_offset)
            if page is None:
                context_data = []
                total_count = 0
                has_more = False
            else:
                context_data = page["messages"]
                total_count = page["total_count"]
                has_more = page["has_more"]
            diverged = True
        else:
            page = store.load_agent_context_page(
                conv_id, _ctx_agent, limit=_limit, offset=_offset)
            diverged = page is not None
            if page is None:
                page = store.load_transcript_page_for_agent(
                    conv_id, _ctx_agent, limit=_limit, offset=_offset) or {}
                context_data = page.get("messages") or []
                total_count = page.get("total_count", len(context_data))
                has_more = page.get("has_more", False)
            else:
                context_data = page.get("messages") or []
                total_count = page.get("total_count", len(context_data))
                has_more = page.get("has_more", False)

        # CC session context is read from CC's own jsonl files — we don't
        # own those entries, they don't have PawFlow's (ts, seq) invariant.
        # Skip the strict deserialize and estimate tokens directly from
        # content length. For every other source (shared, agent ctx, task
        # sub-conv, transcript page) we go through _deserialize_messages
        # which enforces the invariant.
        if _ctx_agent.startswith(("cc_session:", "codex_session:", "gemini_session:")):
            _total_chars = 0
            for _m in context_data:
                _c = _m.get("content", "")
                if isinstance(_c, str):
                    _total_chars += len(_c)
                elif isinstance(_c, list):
                    for _p in _c:
                        _t = _p.get("text") if isinstance(_p, dict) else None
                        if _t:
                            _total_chars += len(_t)
            estimated = _total_chars // 4  # rough
        else:
            deserialized = self._deserialize_messages(context_data, conversation_id=conv_id)
            estimated = self._estimate_tokens(deserialized)
        _context_usage = _ctx_cached_usage(conv_id, _ctx_agent)
        # The token estimate above only covers the loaded page, not the
        # whole context. When a gauge is available it is the authoritative
        # whole-context size — use it so the panel header matches the gauge
        # line instead of showing a much smaller page-only count.
        if (isinstance(_context_usage, dict)
                and int(_context_usage.get("used", 0) or 0) > 0):
            estimated = int(_context_usage.get("used", 0) or 0)
        # Classify messages for display
        display_msgs = []
        _is_shared_view = (not _ctx_agent or _ctx_agent == "shared")
        for m in context_data:
            role = m.get("role", "unknown")
            source = m.get("source") or {}
            display_role = role
            if _is_shared_view and isinstance(source, dict):
                stype = source.get("type", "")
                if stype == "agent":
                    display_role = "assistant"
                elif stype == "user":
                    display_role = "user"
            content = m.get("content", "")
            if isinstance(content, list):
                text_parts = [p.get("text", "") for p in content if p.get("type") == "text"]
                content = "\n".join(text_parts) if text_parts else str(content)
            has_tool_calls = bool(m.get("tool_calls"))
            # sub_agent_trace messages persisted before the msg_id fix
            # have no msg_id — fall back to trace_id so the context
            # editor can still select + delete them.
            _row_mid = m.get("msg_id", "") or (
                m.get("trace_id", "") if role == "sub_agent_trace" else "")
            display_msgs.append({
                "role": display_role,
                "raw_role": role,
                "content": content if isinstance(content, str) else str(content),
                "has_tool_calls": has_tool_calls,
                "tool_calls": m.get("tool_calls") or [],
                "source": m.get("source"),
                "msg_id": _row_mid,
            })
        # Include agent context status map (only on first page)
        _agent_ctx_map = {}
        if _offset == 0:
            _agent_ctx_map = _ctx_visible_contexts(
                conv_id, store.list_agent_contexts(conv_id), _ctx_agent)
            _extras = store.get_extras(conv_id, user_id=user_id) or {}
            for ek, ev in _extras.items():
                if ek.startswith("claude_session:") and ev:
                    _cc_agent = ek[len("claude_session:"):]
                    _agent_ctx_map[f"cc_session:{_cc_agent}"] = "cc-active"
                elif ek.startswith("codex_app_server_thread:") and ev:
                    _codex_agent = ek[len("codex_app_server_thread:"):]
                    _agent_ctx_map[f"codex_session:{_codex_agent}"] = "codex-active"
                elif ek.startswith("gemini_acp_session:") and ev:
                    _gemini_agent = ek[len("gemini_acp_session:"):]
                    _agent_ctx_map[f"gemini_session:{_gemini_agent}"] = "gemini-active"
            _tasks_data = _extras.get("agent_tasks", {})
            if isinstance(_tasks_data, dict):
                for _tid, _t_entry in _tasks_data.items():
                    _sub_cid = f"{conv_id}::task::{_tid}"
                    if store.exists(_sub_cid):
                        _t_agent = _t_entry.get("agent", "?")
                        _agent_ctx_map[f"task:{_tid} ({_t_agent})"] = "sub-conv"
        flowfile.set_content(json.dumps({
            "context": display_msgs,
            "message_count": total_count,
            "token_estimate": estimated,
            "context_usage": _context_usage,
            "diverged": diverged,
            "agent_name": _ctx_agent or "",
            "agent_contexts": _agent_ctx_map,
            "has_more": has_more,
            "offset": _offset,
            "limit": _limit,
        }, ensure_ascii=False).encode())
        return [flowfile]

    if action == "get_context_full":
        flowfile.set_content(json.dumps({
            "error": "Full context loading is disabled; use paginated get_context.",
        }).encode())
        flowfile.set_attribute("http.response.status", "400")
        return [flowfile]

    if action == "edit_context":
        conv_id = body.get("conversation_id", "")
        _ctx_agent = body.get("agent_name", "")
        msg_id = body.get("msg_id", "")
        new_content = body.get("content", "")
        new_role = body.get("role")
        if not conv_id or not msg_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id or msg_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        if _ctx_agent.startswith(("cc_session:", "codex_session:", "gemini_session:")):
            flowfile.set_content(json.dumps({"error": "Runtime session context is read-only"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        if _ctx_agent in ("transcript",):
            flowfile.set_content(json.dumps({
                "error": "Use edit_message for transcript rows.",
            }).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        patched = store.patch_agent_context_message(
            conv_id, _ctx_agent_name(_ctx_agent), msg_id,
            {"content": new_content, **({"role": new_role} if new_role else {})})
        if not patched:
            flowfile.set_content(json.dumps({
                "error": f"Message {msg_id} not found in context — please refresh.",
            }).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        page = store.load_agent_context_page(
            conv_id, _ctx_agent_name(_ctx_agent), limit=1, offset=0) or {}
        flowfile.set_content(json.dumps({
            "ok": True,
            "message_count": page.get("total_count", 0),
            "token_estimate": _estimate_unavailable(),
        }).encode())
        return [flowfile]

    if action == "delete_agent_context":
        conv_id = body.get("conversation_id", "")
        agent_name = body.get("agent_name", "")
        # Runtime CLI sessions: invalidate instead of editing/deleting JSONL rows.
        if agent_name.startswith("cc_session:"):
            _cc_agent = agent_name[len("cc_session:"):]
            try:
                self._clear_claude_session(conv_id, _cc_agent)
                flowfile.set_content(json.dumps({"ok": True}).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]
        if agent_name.startswith(("codex_session:", "gemini_session:")):
            _agent = agent_name.split(":", 1)[1]
            try:
                store.invalidate_claude_session_for_agent(conv_id, _agent)
                flowfile.set_content(json.dumps({"ok": True}).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]
        # Task sub-conversation: delete context and provider sessions.
        if agent_name.startswith("task:"):
            _tid = agent_name.split("(")[0].replace("task:", "").strip()
            try:
                cleanup_agent_task_context(
                    conv_id, _tid, "", store, clear_runtime=True,
                    reason="manual_task_context_delete")
                flowfile.set_content(json.dumps({"ok": True}).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]
        if not conv_id or not agent_name:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id or agent_name"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        try:
            store.delete_agent_context(conv_id, agent_name)
            store.invalidate_claude_session_for_agent(conv_id, agent_name)
            flowfile.set_content(json.dumps({"ok": True}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
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
            cleanup_agent_task_context(
                conv_id, _sub_tid, "", store, clear_runtime=True,
                reason="manual_task_context_delete")
            # Also clean up sync counter and task log
            store.set_extra(conv_id, f"_sub_sync:{_sub_cid}", None)
            store.set_extra(conv_id, f"task_log:{_sub_tid}", None)
            flowfile.set_content(json.dumps({"ok": True, "deleted": _sub_cid}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "delete_context_messages":
        conv_id = body.get("conversation_id", "")
        _ctx_agent = body.get("agent_name", "")
        msg_ids = body.get("msg_ids", [])
        if _ctx_agent.startswith(("cc_session:", "codex_session:", "gemini_session:")):
            flowfile.set_content(json.dumps({"error": "Runtime session context is read-only"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        if not conv_id or not msg_ids:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id or msg_ids"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        if _ctx_agent == "transcript":
            flowfile.set_content(json.dumps({
                "error": "Use delete_message for transcript rows.",
            }).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        try:
            deleted = store.delete_agent_context_messages(
                conv_id, _ctx_agent_name(_ctx_agent), msg_ids)
            page = store.load_agent_context_page(
                conv_id, _ctx_agent_name(_ctx_agent), limit=1, offset=0) or {}
            flowfile.set_content(json.dumps({
                "ok": True,
                "deleted": deleted,
                "message_count": page.get("total_count", 0),
                "token_estimate": _estimate_unavailable(),
            }).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "delete_context_message":
        conv_id = body.get("conversation_id", "")
        _ctx_agent = body.get("agent_name", "")
        msg_id = body.get("msg_id", "")
        if not conv_id or not msg_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id or msg_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        if _ctx_agent.startswith(("cc_session:", "codex_session:", "gemini_session:")):
            flowfile.set_content(json.dumps({"error": "Runtime session context is read-only"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        deleted = store.delete_agent_context_messages(
            conv_id, _ctx_agent_name(_ctx_agent), [msg_id])
        if not deleted:
            flowfile.set_content(json.dumps({
                "error": f"Message {msg_id} not found in context — please refresh.",
            }).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        page = store.load_agent_context_page(
            conv_id, _ctx_agent_name(_ctx_agent), limit=1, offset=0) or {}
        flowfile.set_content(json.dumps({
            "ok": True,
            "message_count": page.get("total_count", 0),
            "token_estimate": _estimate_unavailable(),
        }).encode())
        return [flowfile]

    if action == "replace_context":
        flowfile.set_content(json.dumps({
            "error": "Full context replacement is disabled; use paginated row edits.",
        }).encode())
        flowfile.set_attribute("http.response.status", "400")
        return [flowfile]

    if action == "add_context_message":
        conv_id = body.get("conversation_id", "")
        _ctx_agent = body.get("agent_name", "")
        role = body.get("role", "user")
        content = body.get("content", "")
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        ok = store.append_agent_context_message(
            conv_id, _ctx_agent_name(_ctx_agent), {"role": role, "content": content})
        if not ok:
            flowfile.set_content(json.dumps({"error": "Context not found"}).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        page = store.load_agent_context_page(
            conv_id, _ctx_agent_name(_ctx_agent), limit=1, offset=0) or {}
        flowfile.set_content(json.dumps({
            "ok": True,
            "message_count": page.get("total_count", 0),
            "token_estimate": _estimate_unavailable(),
        }).encode())
        return [flowfile]

    return _UNHANDLED
