"""AgentLoopTask actions — context ops"""

import json
import logging

from tasks.ai.actions._ctxops_base import (
    _UNHANDLED,
)

logger = logging.getLogger(__name__)


def _handle_ctxops_k4(self, action, body, store, user_id, flowfile, _helpers):
    """context_ops cluster _ctxops_k4. Returns result or _UNHANDLED."""
    (_ctx_agent_name, _ctx_load, _ctx_save, _ctx_cached_usage,
     _ctx_visible_contexts, _ctx_llm_service_config, _ctx_real_context_size, _ctx_max_tokens) = _helpers
    if action == "restart_from":
        conv_id = body.get("conversation_id", "")
        _rf_target = str(
            body.get("msg_id")
            or body.get("restart_msg_id")
            or body.get("restart_from")
            or ""
        ).strip()
        _rf_index_raw = body.get("restart_index", None)
        if _rf_index_raw is None and "keep_last" in body:
            _rf_index_raw = body.get("keep_last")
        if _rf_index_raw is None and "count" in body:
            _rf_index_raw = body.get("count")
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        _meta = store.get_metadata(conv_id)
        if not _meta:
            flowfile.set_content(json.dumps({"error": "Conversation not found"}).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        if user_id and _meta.get("user_id") and _meta.get("user_id") != user_id:
            flowfile.set_content(json.dumps({"error": "Access denied"}).encode())
            flowfile.set_attribute("http.response.status", "403")
            return [flowfile]
        restart_prompt_text = ""
        restart_original_msg_id = ""
        _rf_msgs = None
        truncate_boundary_msg_id = ""
        if _rf_target:
            boundary = store.find_restart_boundary(conv_id, _rf_target)
            if not boundary.get("found"):
                flowfile.set_content(json.dumps({
                    "error": f"msg_id {_rf_target} not found",
                }).encode())
                flowfile.set_attribute("http.response.status", "404")
                return [flowfile]
            _target_msg = boundary.get("target") or {}
            if isinstance(_target_msg, dict) and _target_msg.get("role") == "user":
                restart_original_msg_id = _rf_target
                _content = _target_msg.get("content", "")
                if isinstance(_content, str):
                    restart_prompt_text = _content
                elif isinstance(_content, list):
                    restart_prompt_text = "\n".join(
                        str(part.get("text", ""))
                        for part in _content
                        if isinstance(part, dict) and part.get("type") == "text"
                    ).strip()
                else:
                    restart_prompt_text = str(_content or "")
            truncate_boundary_msg_id = boundary.get("boundary_msg_id") or ""
            if not truncate_boundary_msg_id:
                keep_count = 0
            else:
                keep_count = -1
        else:
            _rf_msgs = store.load(conv_id, user_id=user_id)
            if not _rf_msgs:
                flowfile.set_content(json.dumps({"error": "Conversation not found"}).encode())
                flowfile.set_attribute("http.response.status", "404")
                return [flowfile]
            if _rf_index_raw is None:
                flowfile.set_content(json.dumps({
                    "error": "Missing restart_index or msg_id",
                }).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            try:
                keep_count = int(_rf_index_raw)
            except (TypeError, ValueError):
                flowfile.set_content(json.dumps({
                    "error": "restart_index must be an integer or use msg_id",
                }).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            if keep_count < 0:
                flowfile.set_content(json.dumps({
                    "error": "restart_index must be >= 0",
                }).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]

        kept_msgs = list(_rf_msgs[:keep_count]) if _rf_msgs is not None and keep_count >= 0 else []
        drop_ids = {
            str(m.get("msg_id") or "")
            for m in (_rf_msgs or [])[keep_count:]
            if isinstance(m, dict) and m.get("msg_id")
        } if _rf_msgs is not None and keep_count >= 0 else set()

        def _do_restart():
            import time as _time

            deleted_contexts = 0
            kept_message_count = len(kept_msgs)
            if _rf_target and truncate_boundary_msg_id:
                result = store.truncate_after_msg_id(conv_id, truncate_boundary_msg_id)
                if not result.get("found"):
                    raise ValueError(
                        f"msg_id {truncate_boundary_msg_id} not found")
                kept_message_count = int(result.get("kept_messages") or 0)
            elif keep_count == 0:
                lock = store._get_conv_lock(conv_id)
                with lock:
                    rows = list(store._transcript_log(conv_id).iter_rows())
                    meta = next((dict(r) for r in rows if r.get("t") == "meta"), None)
                    if meta is None:
                        meta = {
                            "t": "meta", "user_id": user_id, "status": "idle",
                            "created_at": _time.time(), "expires_at": 0,
                        }
                    meta["status"] = "idle"
                    meta["ts"] = _time.time()
                    store._transcript_log(conv_id).replace_dicts([
                        store._stamp_line(conv_id, meta),
                    ])
                with store._cache_lock:
                    store._cache.pop(conv_id, None)
                store._invalidate_ctx_cache(conv_id)
                store._reload_cache(conv_id)
                store.save_agent_context(conv_id, "", [])
                agent_names = {
                    a for a in store.list_agent_contexts(conv_id)
                    if a and a != "*"
                }
                conv_dir = store._conv_dir(conv_id)
                if conv_dir.is_dir():
                    for entry in conv_dir.iterdir():
                        if (entry.is_dir()
                                and store._jsonl_exists(entry / "context.jsonl")):
                            agent_names.add(entry.name)
                for agent_name in sorted(agent_names):
                    if store.delete_agent_context(conv_id, agent_name):
                        deleted_contexts += 1
                store.invalidate_claude_sessions(conv_id)
            elif drop_ids:
                store._remove_msg_ids_from_files(conv_id, drop_ids)
            store.set_extra(conv_id, "_restart_from_context", {
                "msg_id": _rf_target or "",
                "boundary_msg_id": truncate_boundary_msg_id or "",
                "restart_original_msg_id": restart_original_msg_id or "",
                "restart_index": kept_message_count,
            })
            return {
                "operation": "restart_from",
                "kept_messages": kept_message_count,
                "deleted_contexts": deleted_contexts,
                "msg_id": _rf_target or None,
                "restart_original_msg_id": restart_original_msg_id or None,
                "restart_prompt_text": restart_prompt_text,
                "restart_index": kept_message_count,
                "agent": "shared",
            }

        return self._run_bg_context_op(
            conv_id, "restart_from", _do_restart, flowfile,
            agent_name="")

    if action == "edit_message":
        conv_id = body.get("conversation_id", "")
        msg_id = body.get("msg_id", "")
        new_content = body.get("content", "")
        new_role = body.get("role") or ""
        if not conv_id or not msg_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id or msg_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        try:
            updated = store.edit_message(
                conv_id, msg_id=msg_id, content=new_content,
                role=new_role, user_id=user_id)
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]
        if not updated:
            flowfile.set_content(json.dumps({"error": f"Message {msg_id} not found"}).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        flowfile.set_content(json.dumps({
            "ok": True, "updated": updated, "conversation_id": conv_id,
            "message_count": int(store.get_extra_snapshot(
                conv_id, "_meta_msg_count", 0) or 0),
        }).encode())
        return [flowfile]

    if action == "delete_message":
        conv_id = body.get("conversation_id", "")
        msg_id = body.get("msg_id", "")
        msg_ids = body.get("msg_ids", [])
        if msg_id and not msg_ids:
            msg_ids = [msg_id]
        if not conv_id or not msg_ids:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id or msg_id(s)"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        try:
            if len(msg_ids) == 1:
                deleted = 1 if store.delete_message(conv_id, msg_id=msg_ids[0], user_id=user_id) else 0
            else:
                deleted = store.delete_messages(conv_id, msg_ids, user_id=user_id)
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]
        flowfile.set_content(json.dumps({
            "deleted": deleted, "conversation_id": conv_id,
            "message_count": int(store.get_extra_snapshot(
                conv_id, "_meta_msg_count", 0) or 0),
        }).encode())
        return [flowfile]

    if action == "set_permission_mode":
        conv_id = body.get("conversation_id", "")
        mode = body.get("mode", "default")
        if mode not in ("default", "approve_edits", "read_only", "auto"):
            flowfile.set_content(json.dumps({"error": f"Invalid permission mode: {mode}"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        store.set_extra(conv_id, "permission_mode", mode)
        flowfile.set_content(json.dumps({"status": "ok", "permission_mode": mode}).encode())
        return [flowfile]

    if action == "get_permission_mode":
        conv_id = body.get("conversation_id", "")
        mode = store.get_extra(conv_id, "permission_mode") or "default" if conv_id else "default"
        flowfile.set_content(json.dumps({"permission_mode": mode}).encode())
        return [flowfile]

    if action == "set_tool_permission":
        conv_id = body.get("conversation_id", "")
        tool_name = body.get("tool_name", "")
        permission = body.get("permission", "")  # allow | deny | confirm | "" (reset)
        if not conv_id or not tool_name:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id or tool_name"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        if permission not in ("allow", "deny", "confirm", ""):
            flowfile.set_content(json.dumps({"error": f"Invalid permission: '{permission}' (use allow|deny|confirm or empty to reset)"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        perms = store.get_extra(conv_id, "tool_permissions") or {}
        if permission:
            perms[tool_name] = permission
        else:
            perms.pop(tool_name, None)
        store.set_extra(conv_id, "tool_permissions", perms)
        flowfile.set_content(json.dumps({"status": "ok", "tool_name": tool_name,
                                         "permission": permission or "(reset)"}).encode())
        return [flowfile]

    if action == "get_tool_permissions":
        conv_id = body.get("conversation_id", "")
        perms = store.get_extra(conv_id, "tool_permissions") or {} if conv_id else {}
        if perms:
            lines = ["## Per-tool permissions\n"]
            for tname, tp in sorted(perms.items()):
                icon = {"allow": "\u2705", "deny": "\u274c", "confirm": "\u2753"}.get(tp, "?")
                lines.append(f"  {icon} `{tname}` — {tp}")
            msg = "\n".join(lines)
        else:
            msg = "No per-tool permission overrides (using global mode)."
        flowfile.set_content(json.dumps({"tool_permissions": perms, "message": msg}).encode())
        return [flowfile]

    return _UNHANDLED
