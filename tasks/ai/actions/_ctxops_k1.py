"""AgentLoopTask actions — context ops"""

import json
import logging

from tasks.ai.actions._ctxops_base import (
    _UNHANDLED,
)

logger = logging.getLogger(__name__)


def _handle_ctxops_k1(self, action, body, store, user_id, flowfile, _helpers):
    """context_ops cluster _ctxops_k1. Returns result or _UNHANDLED."""
    (_ctx_agent_name, _ctx_load, _ctx_save, _ctx_cached_usage,
     _ctx_visible_contexts, _ctx_llm_service_config, _ctx_real_context_size, _ctx_max_tokens) = _helpers
    # ── /context (improved) ──
    if action == "view_context":
        conv_id = body.get("conversation_id", "")
        _ctx_agent = body.get("agent_name", "")
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            return [flowfile]
        # Membership guard — refuse per-agent view for an agent that
        # isn't in this conv. Without this, /view_context ghost would
        # try to load a phantom agent_context (returning empty) and
        # surface confusing "no context" errors.
        from core.conv_agent_config import require_agent_member
        _vc_err = require_agent_member(conv_id, _ctx_agent,
                                         user_id=user_id)
        if _vc_err:
            flowfile.set_content(json.dumps({"error": _vc_err}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        context_data = _ctx_load(conv_id, _ctx_agent)
        source_data = context_data if context_data is not None else store.load(conv_id, user_id=user_id)
        if not source_data:
            flowfile.set_content(json.dumps({"error": "No context data"}).encode())
            return [flowfile]
        msgs = self._deserialize_messages(source_data, conversation_id=conv_id)
        max_ctx = _ctx_max_tokens(conv_id, _ctx_agent)

        # Category breakdown
        system_tokens = 0
        tool_tokens = 0
        user_tokens = 0
        assistant_tokens = 0
        for m in msgs:
            t = self._estimate_tokens([m])
            if m.role == "system":
                system_tokens += t
            elif m.role == "tool":
                tool_tokens += t
            elif m.role == "user":
                user_tokens += t
            elif m.role == "assistant":
                assistant_tokens += t

        total = system_tokens + tool_tokens + user_tokens + assistant_tokens
        free = max(0, max_ctx - total)
        def pct(v):
            return round(v / max_ctx * 100, 1) if max_ctx else 0

        # Visual bar (40 chars)
        bar_len = 40
        def bar_segment(tokens):
            return max(0, round(tokens / max_ctx * bar_len)) if max_ctx else 0
        s_len = bar_segment(system_tokens)
        t_len = bar_segment(tool_tokens)
        u_len = bar_segment(user_tokens)
        a_len = bar_segment(assistant_tokens)
        f_len = bar_len - s_len - t_len - u_len - a_len
        bar = "S" * s_len + "T" * t_len + "U" * u_len + "A" * a_len + "·" * max(0, f_len)

        lines = [
            f"## Context: {_ctx_agent or 'shared'}",
            "",
            f"[{bar}] {total:,} / {max_ctx:,} tokens ({pct(total)}%)",
            "",
            f"  **System**:    {system_tokens:>6,} tokens ({pct(system_tokens)}%) — S",
            f"  **Tools**:     {tool_tokens:>6,} tokens ({pct(tool_tokens)}%) — T",
            f"  **User**:      {user_tokens:>6,} tokens ({pct(user_tokens)}%) — U",
            f"  **Assistant**: {assistant_tokens:>6,} tokens ({pct(assistant_tokens)}%) — A",
            f"  **Free**:      {free:>6,} tokens ({pct(free)}%) — ·",
            "",
            f"  Messages: {len(msgs)} | Diverged: {'yes' if context_data is not None else 'no'}",
        ]
        # Suggestions
        if pct(total) > 80:
            lines.append(f"\n  ⚠ Context is {pct(total)}% full — consider `/compact`")
        if pct(tool_tokens) > 40:
            lines.append(f"  💡 Tool results use {pct(tool_tokens)}% — old results will be auto-cleared")
        if pct(system_tokens) > 20:
            lines.append(f"  💡 System prompt is large ({system_tokens:,} tokens)")

        flowfile.set_content(json.dumps({
            "message": "\n".join(lines),
            "total_tokens": total,
            "max_tokens": max_ctx,
            "breakdown": {
                "system": system_tokens, "tools": tool_tokens,
                "user": user_tokens, "assistant": assistant_tokens,
                "free": free,
            },
            "message_count": len(msgs),
            "pct_used": pct(total),
        }).encode())
        return [flowfile]

    # ── /rewind ──
    if action == "rewind":
        conv_id = body.get("conversation_id", "")
        checkpoint_arg = body.get("checkpoint", "").strip()
        mode = body.get("mode", "")  # "code", "conversation", "both"
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
            lines.append("\nUse `/rewind <number>` to rewind to a checkpoint.")
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
                        from core.service_registry import ServiceRegistry
                        return ServiceRegistry.get_instance().resolve(
                            svc_id, user_id=user_id, conv_id=conv_id)
                    except Exception:
                        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                # Default: try to find any filesystem service
                try:
                    return self._find_filesystem_service(user_id, conv_id)
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
                # Manual context modification → invalidate claude-code sessions
                store.invalidate_claude_sessions(conv_id)
                results["conversation"] = {
                    "messages_before": len(all_msgs) if all_msgs else 0,
                    "messages_after": target_msg_count,
                }

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

    if action == "git_prune":
        conv_id = body.get("conversation_id", "")
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        if not store.exists(conv_id):
            flowfile.set_content(json.dumps({"error": "Conversation not found"}).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]

        def _do_git_prune():
            from core.conversation_event_bus import ConversationEventBus
            bus = ConversationEventBus.instance()

            def _progress(stage, payload):
                bus.publish_event(conv_id, "compact_progress", {
                    "stage": "git_prune", "detail": stage,
                    "operation": "git_prune", **payload,
                })

            result = store.prune_git_history_now(conv_id, progress=_progress)
            return {"operation": "git_prune", "context_changed": False,
                    "agent": "shared", **result}

        return self._run_bg_context_op(
            conv_id, "git_prune", _do_git_prune, flowfile,
            agent_name="")

    return _UNHANDLED
