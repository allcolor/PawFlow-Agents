"""AgentLoopTask actions — context ops"""

import json
import logging
import threading
import time
from typing import Dict, Any, List, Optional

from core import FlowFile
from core.llm_client import LLMMessage, LLMClient
from core.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


def _find_cc_session_jsonl(conv_id: str, agent_name: str, store,
                           user_id: str = "") -> str:
    """Find the JSONL file path for an active Claude Code session."""
    import os
    import glob as _glob

    session_key = f"claude_session:{agent_name or 'default'}"
    session_id = store.get_extra(conv_id, session_key)
    if not session_id:
        return ""

    from core.llm_providers.claude_code import _get_sessions_base
    if not conv_id or not agent_name:
        raise ValueError(f"BUG: conv_id={conv_id!r}, agent_name={agent_name!r} required for CC session")
    # Path is: <sessions_base>/{user_id}/{conv_id}/{agent}/
    uid = user_id or store.get_user_id(conv_id) or "default"
    workdir = os.path.join(_get_sessions_base(), uid, conv_id, agent_name)
    projects_dir = os.path.join(workdir, "projects", "-workspace")
    jsonl_path = os.path.join(projects_dir, f"{session_id}.jsonl")

    if not os.path.exists(jsonl_path):
        candidates = _glob.glob(os.path.join(projects_dir, "*.jsonl"))
        if candidates:
            jsonl_path = max(candidates, key=os.path.getmtime)
        else:
            return ""
    return jsonl_path


def _rewrite_cc_session(conv_id: str, agent_name: str, store,
                         remove_indices: set = None):
    """Rewrite Claude Code session JSONL without specified entries.

    Recalculates parentUuid chains so removed entries don't break --resume.
    Only user/assistant entries are indexed (matching _load_cc_session_context).
    """
    jsonl_path = _find_cc_session_jsonl(conv_id, agent_name, store)
    if not jsonl_path:
        raise RuntimeError("No active CC session found")

    # Read all lines
    with open(jsonl_path, "r", encoding="utf-8") as f:
        all_lines = f.readlines()

    # Parse entries, tracking which are user/assistant (indexed in UI)
    entries = []
    ui_index = 0
    for raw_line in all_lines:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            entry = json.loads(raw_line)
        except json.JSONDecodeError:
            entries.append({"_raw": raw_line, "_keep": True})
            continue
        etype = entry.get("type", "")
        if etype in ("user", "assistant"):
            keep = ui_index not in (remove_indices or set())
            entries.append({"_parsed": entry, "_keep": keep, "_uuid": entry.get("uuid", "")})
            ui_index += 1
        else:
            entries.append({"_parsed": entry, "_keep": True, "_uuid": entry.get("uuid", "")})

    # Build uuid → parent mapping for kept entries
    removed_uuids = {e["_uuid"] for e in entries if not e["_keep"] and e.get("_uuid")}

    # Rewrite: fix parentUuid references to skip removed entries
    uuid_to_parent = {}
    for e in entries:
        if "_parsed" in e:
            uuid_to_parent[e["_parsed"].get("uuid", "")] = e["_parsed"].get("parentUuid", "")

    def _resolve_parent(uuid):
        """Walk up the chain to find the first non-removed ancestor."""
        visited = set()
        while uuid in removed_uuids and uuid not in visited:
            visited.add(uuid)
            uuid = uuid_to_parent.get(uuid, "")
        return uuid

    # Write back
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for e in entries:
            if not e["_keep"]:
                continue
            if "_raw" in e:
                f.write(e["_raw"] + "\n")
            else:
                parsed = e["_parsed"]
                parent = parsed.get("parentUuid", "")
                if parent in removed_uuids:
                    parsed["parentUuid"] = _resolve_parent(parent)
                f.write(json.dumps(parsed, ensure_ascii=False) + "\n")

    logger.info("[cc-session] rewrote %s: removed %d entries, %d remaining",
                jsonl_path, len(remove_indices or set()),
                sum(1 for e in entries if e["_keep"]))


def _load_cc_session_context(conv_id: str, agent_name: str, store,
                             user_id: str = "") -> list:
    """Load Claude Code session JSONL and convert to PawFlow message format."""
    jsonl_path = _find_cc_session_jsonl(conv_id, agent_name, store, user_id=user_id)
    if not jsonl_path:
        return []

    messages = []
    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                etype = entry.get("type", "")
                if etype not in ("user", "assistant"):
                    continue
                msg = entry.get("message", {})
                role = msg.get("role", etype)
                content_blocks = msg.get("content", "")

                # Convert content blocks to text
                if isinstance(content_blocks, list):
                    parts = []
                    tool_calls = []
                    for block in content_blocks:
                        btype = block.get("type", "")
                        if btype == "text":
                            parts.append(block.get("text", ""))
                        elif btype == "thinking":
                            parts.append(f"[thinking: {block.get('thinking', '')[:200]}...]")
                        elif btype == "tool_use":
                            tool_calls.append({
                                "id": block.get("id", ""),
                                "name": block.get("name", ""),
                                "arguments": block.get("input", {}),
                            })
                        elif btype == "tool_result":
                            _tr_content = block.get("content", "")
                            if isinstance(_tr_content, list):
                                _tr_content = " ".join(
                                    b.get("text", "") for b in _tr_content
                                    if isinstance(b, dict))
                            parts.append(f"[tool_result: {str(_tr_content)[:200]}]")
                    content = "\n".join(parts) if parts else ""
                    msg_entry = {"role": role, "content": content}
                    if tool_calls:
                        msg_entry["tool_calls"] = tool_calls
                elif isinstance(content_blocks, str):
                    msg_entry = {"role": role, "content": content_blocks}
                else:
                    msg_entry = {"role": role, "content": str(content_blocks)}

                # Add metadata
                msg_entry["msg_id"] = entry.get("uuid", "")
                if msg.get("model"):
                    msg_entry["source"] = {"name": "claude-code", "model": msg["model"]}

                messages.append(msg_entry)
    except Exception as e:
        logger.error("[cc-session] Failed to read session JSONL: %s", e)
        return []

    return messages


def _handle_context_ops(self, action, body, store, user_id, flowfile):
    """Handle context ops actions. Returns [flowfile] or None."""

    def _ctx_load(conv_id, agent_name=""):
        """Load context for compaction/view.

        Rule: compaction always starts from the shared timeline, never
        from the per-agent context (which may already contain leftover
        summaries from previous compactions — feeding that back into a
        new summarization just layers stale topics on top of each
        other).  For a specific agent, personalize the shared view so
        the agent's own messages read as assistant and the others as
        user (via load_shared_for_agent).
        """
        if agent_name and agent_name not in ("", "ALL"):
            full = store.load_transcript_for_agent(conv_id, agent_name)
            if full:
                return full
            # Fresh conversation: no transcript yet → fall back to agent ctx
            return store.load_agent_context(conv_id, agent_name)
        return store.load_context(conv_id, user_id=user_id)

    def _ctx_save(conv_id, data, agent_name=""):
        """Save context for an agent (or shared if no agent)."""
        # "shared" or "" both mean the shared context (agent="")
        _name = "" if (not agent_name or agent_name == "shared") else agent_name
        store.save_agent_context(conv_id, _name, data)
        if _name:
            store.set_extra(conv_id, f"claude_session:{_name}", "")
        else:
            store.invalidate_claude_sessions(conv_id)

    def _ctx_max_tokens(agent_name=""):
        """Get max_context_size from the summarizer service.

        Compaction is always driven by the summarizer_service (not the
        agent's main LLM), so the context limit we size against is the
        summarizer's — not whichever model happens to be answering the
        user.
        """
        flow_default = int(self.config.get("max_context_size", 64000))
        try:
            _sc, _sc_max, _ = self._get_summarizer_client(user_id)
            if _sc_max:
                return int(_sc_max)
            if _sc:
                v = int((getattr(_sc, 'config', {}) or {}).get("max_context_size", 0))
                if v:
                    return v
        except Exception:
            pass
        return flow_default

    # ── /context (improved) ──
    if action == "view_context":
        conv_id = body.get("conversation_id", "")
        _ctx_agent = body.get("agent_name", "")
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            return [flowfile]
        context_data = _ctx_load(conv_id, _ctx_agent)
        source_data = context_data if context_data is not None else store.load(conv_id, user_id=user_id)
        if not source_data:
            flowfile.set_content(json.dumps({"error": "No context data"}).encode())
            return [flowfile]
        msgs = self._deserialize_messages(source_data)
        max_ctx = _ctx_max_tokens(_ctx_agent)

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
        pct = lambda v: round(v / max_ctx * 100, 1) if max_ctx else 0

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
            f"",
            f"[{bar}] {total:,} / {max_ctx:,} tokens ({pct(total)}%)",
            f"",
            f"  **System**:    {system_tokens:>6,} tokens ({pct(system_tokens)}%) — S",
            f"  **Tools**:     {tool_tokens:>6,} tokens ({pct(tool_tokens)}%) — T",
            f"  **User**:      {user_tokens:>6,} tokens ({pct(user_tokens)}%) — U",
            f"  **Assistant**: {assistant_tokens:>6,} tokens ({pct(assistant_tokens)}%) — A",
            f"  **Free**:      {free:>6,} tokens ({pct(free)}%) — ·",
            f"",
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
                        from core.service_registry import ServiceRegistry
                        return ServiceRegistry.get_instance().get_live_instance("global", "", svc_id)
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
                # Manual context modification → invalidate claude-code sessions
                store.invalidate_claude_sessions(conv_id)
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
        # Resolve client — compaction is driven by summarizer_service,
        # full stop. No fallback to the agent's llm_service (that would
        # compact with a model the user didn't choose for summarization)
        # and no "default" that doesn't actually exist as a service.
        _compact_client, _, _compact_svc_id = self._get_summarizer_client(user_id)
        if not _compact_client:
            flowfile.set_content(json.dumps({
                "error": "No summarizer_service configured — compaction needs one.",
            }).encode())
            return [flowfile]
        _compact_max = _ctx_max_tokens(_ctx_agent)
        _compact_source = source_data
        _compact_conv = conv_id
        _compact_agent_name = _ctx_agent
        _compact_keep = int(self.config.get("context_keep_recent", 6))

        _compact_instructions = body.get("instructions", "")
        _compact_force = body.get("force", False)

        # Check for precompact snapshot (skip with --force)
        _snap_key = f"{conv_id}:{_ctx_agent}"
        _precompact_snap = None if _compact_force else self._precompact_snapshots.pop(_snap_key, None)

        def _do_compact():
            msgs = self._deserialize_messages(_compact_source)
            before = len(msgs)
            estimated = self._estimate_tokens(msgs)

            if _precompact_snap and not _compact_instructions:
                # Use existing precompact snapshot — merge with messages after snapshot
                _snap_last_id = _precompact_snap.get("last_msg_id", "")
                _split = len(msgs)
                for _si in range(len(msgs)):
                    _mid = getattr(msgs[_si], 'msg_id', None) or (msgs[_si].get('msg_id') if isinstance(msgs[_si], dict) else None)
                    if _mid == _snap_last_id:
                        _split = _si + 1
                        break
                _after = msgs[_split:]
                compacted = list(_precompact_snap["messages"]) + _after
                # If still over, compact further
                _merged_est = self._estimate_tokens(compacted)
                if _merged_est > _compact_max * 0.9:
                    compacted = self._compact(
                        compacted, _compact_client, _compact_max,
                        conversation_id=_compact_conv,
                        agent_name=_compact_agent_name,
                        force=True,
                        user_id=user_id,
                    )
                else:
                    self._persist_context(compacted, _compact_conv, _compact_agent_name)
                logger.info("[compact] used precompact snapshot: %d + %d after = %d msgs",
                            len(_precompact_snap["messages"]), len(_after), len(compacted))
            else:
                compacted = self._compact(
                    msgs, _compact_client, _compact_max,
                    conversation_id=_compact_conv,
                    agent_name=_compact_agent_name,
                    compact_instructions=_compact_instructions,
                    force=True,
                    user_id=user_id,
                )
            after_tokens = self._estimate_tokens(compacted)
            # Invalidate the compacted agent's CC session
            if _compact_agent_name:
                store.set_extra(_compact_conv, f"claude_session:{_compact_agent_name}", "")
            else:
                store.invalidate_claude_sessions(_compact_conv)
            return {"before": before, "after": len(compacted),
                    "tokens_before": estimated, "tokens_after": after_tokens,
                    "agent": _compact_agent_name or "shared",
                    "focus": _compact_instructions or None,
                    "used_snapshot": bool(_precompact_snap and not _compact_instructions)}

        return self._run_bg_context_op(conv_id, "compact", _do_compact, flowfile)

    if action == "rebuild":
        conv_id = body.get("conversation_id", "")
        _rb_agent = body.get("agent_name", "")
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        _rb_msgs = store.load(conv_id, user_id=user_id)
        if _rb_msgs:
            # Filter: no display_only, no tool_calls, no tool results
            # (context = conversation messages only, not tool plumbing)
            _rb_msgs = [m for m in _rb_msgs
                        if isinstance(m, dict)
                        and not m.get("display_only")
                        and not m.get("tool_calls")
                        and m.get("role") != "tool"]
        if not _rb_msgs:
            flowfile.set_content(json.dumps({"error": "Conversation not found"}).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]

        def _do_rebuild():
            # /rebuild = context = full conversation transcript. No compaction.
            deserialized = self._deserialize_messages(_rb_msgs)
            estimated = self._estimate_tokens(deserialized)
            if _rb_agent == "ALL":
                # Rebuild ALL agent contexts + shared
                agent_map = store.list_agent_contexts(conv_id)
                for name in agent_map:
                    if name == "*":
                        store.save_context(conv_id, list(_rb_msgs))
                    else:
                        store.save_agent_context(conv_id, name, list(_rb_msgs))
            else:
                _ctx_save(conv_id, _rb_msgs, _rb_agent)
                # If agent context == shared context after rebuild, remove the
                # agent context (it'll fall back to shared automatically)
                if _rb_agent and _rb_agent != "shared":
                    shared_ctx = store.load_context(conv_id)
                    if shared_ctx is not None and shared_ctx == _rb_msgs:
                        store.save_agent_context(conv_id, _rb_agent, shared_ctx)
                        logger.info(f"[rebuild] Agent '{_rb_agent}' context == shared, merged back")
            # Invalidate the rebuilt agent's CC session
            if _rb_agent and _rb_agent != "shared":
                store.set_extra(conv_id, f"claude_session:{_rb_agent}", "")
            else:
                store.invalidate_claude_sessions(conv_id)
            return {"before": len(_rb_msgs), "after": len(_rb_msgs),
                    "tokens_after": estimated,
                    "agent": _rb_agent or "shared"}

        return self._run_bg_context_op(conv_id, "rebuild", _do_rebuild, flowfile)

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
            # Shared context — load from shared.jsonl (not transcript)
            context_data = _ctx_load(conv_id, "")
            if context_data is None:
                context_data = []
            total_count = len(context_data)
            end = len(context_data) - _offset
            start = max(0, end - _limit)
            context_data = context_data[start:end]
            has_more = start > 0
            diverged = True
        elif _ctx_agent.startswith("cc_session:"):
            _cc_agent = _ctx_agent[len("cc_session:"):]
            context_data = _load_cc_session_context(conv_id, _cc_agent, store, user_id=user_id)
            total_count = len(context_data)
            has_more = False
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
            context_data = _ctx_load(conv_id, _ctx_agent)
            diverged = context_data is not None
            if context_data is None:
                page = store.load_page(conv_id, limit=_limit, offset=_offset, user_id=user_id)
                context_data = page["messages"] if page else []
                total_count = page["total_count"] if page else 0
                has_more = page["has_more"] if page else False
            else:
                total_count = len(context_data)
                # Paginate in-memory
                end = len(context_data) - _offset
                start = max(0, end - _limit)
                context_data = context_data[start:end]
                has_more = start > 0

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
            # sub_agent_trace messages persisted before the msg_id fix
            # have no msg_id — fall back to trace_id so the context
            # editor can still select + delete them.
            _row_mid = m.get("msg_id", "") or (
                m.get("trace_id", "") if role == "sub_agent_trace" else "")
            display_msgs.append({
                "role": role,
                "content": content[:300] if isinstance(content, str) else str(content)[:300],
                "has_tool_calls": has_tool_calls,
                "source": m.get("source"),
                "msg_id": _row_mid,
            })
        # Include agent context status map (only on first page)
        _agent_ctx_map = {}
        if _offset == 0:
            _agent_ctx_map = store.list_agent_contexts(conv_id)
            _extras = store.get_extras(conv_id, user_id=user_id) or {}
            for ek, ev in _extras.items():
                if ek.startswith("claude_session:") and ev:
                    _cc_agent = ek[len("claude_session:"):]
                    _agent_ctx_map[f"cc_session:{_cc_agent}"] = "cc-active"
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
            "diverged": diverged,
            "agent_name": _ctx_agent or "",
            "agent_contexts": _agent_ctx_map,
            "has_more": has_more,
            "offset": _offset,
            "limit": _limit,
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

    if action == "delete_agent_context":
        conv_id = body.get("conversation_id", "")
        agent_name = body.get("agent_name", "")
        # CC session: invalidate instead of deleting context
        if agent_name.startswith("cc_session:"):
            _cc_agent = agent_name[len("cc_session:"):]
            try:
                self._clear_claude_session(conv_id, _cc_agent)
                flowfile.set_content(json.dumps({"ok": True}).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]
        # Task sub-conversation: delete the sub-conversation
        if agent_name.startswith("task:"):
            _tid = agent_name.split("(")[0].replace("task:", "").strip()
            _sub_cid = f"{conv_id}::task::{_tid}"
            try:
                store.delete(_sub_cid)
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
            store.set_extra(conv_id, f"claude_session:{agent_name}", "")
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
            store.delete(_sub_cid)
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
        indices = body.get("indices", [])
        # CC session: rewrite JSONL without selected entries
        if _ctx_agent.startswith("cc_session:"):
            _cc_agent = _ctx_agent[len("cc_session:"):]
            try:
                _rewrite_cc_session(conv_id, _cc_agent, store, remove_indices=set(indices))
                flowfile.set_content(json.dumps({"ok": True, "deleted": len(indices)}).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]
        if not conv_id or not indices:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id or indices"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        try:
            context_data = _ctx_load(conv_id, _ctx_agent)
            if context_data is None:
                context_data = store.load(conv_id, user_id=user_id) or []
            # Remove indices in reverse order to preserve positions
            for idx in sorted(indices, reverse=True):
                if 0 <= idx < len(context_data):
                    context_data.pop(idx)
            _ctx_save(conv_id, context_data, _ctx_agent)
            deserialized = self._deserialize_messages(context_data)
            estimated = self._estimate_tokens(deserialized)
            flowfile.set_content(json.dumps({
                "ok": True,
                "deleted": len(indices),
                "message_count": len(context_data),
                "token_estimate": estimated,
            }).encode())
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
        _ctx_save(conv_id, context_data, _ctx_agent)
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
        _ctx_save(conv_id, new_context, _ctx_agent)
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
        _ctx_save(conv_id, context_data, _ctx_agent)
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
        if _rf_msgs:
            _rf_msgs = [m for m in _rf_msgs
                        if isinstance(m, dict)
                        and not m.get("display_only")
                        and not m.get("tool_calls")
                        and m.get("role") != "tool"]
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
            "message_count": store.message_count(conv_id),
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

    return None
