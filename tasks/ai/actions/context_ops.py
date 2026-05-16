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

    from core.llm_providers.claude_code import (
        _get_sessions_base, LLMClaudeCodeMixin)
    if not conv_id or not agent_name:
        raise ValueError(f"BUG: conv_id={conv_id!r}, agent_name={agent_name!r} required for CC session")
    # Path is: <sessions_base>/{user_id}/{conv_id}/{agent}/
    uid = user_id or store.get_user_id(conv_id) or "default"
    workdir = os.path.join(_get_sessions_base(), uid, conv_id, agent_name)
    # CC derives the project bucket from its containerized cwd
    # (/cc_sessions/<conv>/<agent>) by replacing every non-alphanum char
    # with '-'. _cc_project_key reproduces that so we land on the exact
    # on-disk bucket name.
    proj_key = LLMClaudeCodeMixin._cc_project_key(workdir)
    projects_dir = os.path.join(workdir, "projects", proj_key)
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
                    _had_tool_result = False
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
                            _had_tool_result = True
                            _tr_content = block.get("content", "")
                            if isinstance(_tr_content, list):
                                _tr_content = " ".join(
                                    b.get("text", "") for b in _tr_content
                                    if isinstance(b, dict))
                            parts.append(f"[tool_result: {str(_tr_content)[:200]}]")
                    content = "\n".join(parts) if parts else ""
                    _display_role = "tool" if (_had_tool_result and not any(
                        p and not p.startswith("[tool_result:") for p in parts
                    )) else role
                    msg_entry = {"role": _display_role, "content": content}
                    if tool_calls:
                        msg_entry["tool_calls"] = tool_calls
                elif isinstance(content_blocks, str):
                    msg_entry = {"role": role, "content": content_blocks}
                else:
                    msg_entry = {"role": role, "content": str(content_blocks)}

                msg_entry["msg_id"] = entry.get("uuid", "")
                if msg.get("model"):
                    msg_entry["source"] = {"name": "claude-code", "model": msg["model"]}

                messages.append(msg_entry)
    except Exception as e:
        logger.error("[cc-session] Failed to read session JSONL: %s", e)
        return []

    return messages


def _text_from_cli_content(content) -> str:
    if isinstance(content, str):
        return content
    parts = []
    if isinstance(content, list):
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                text = part.get("text") or part.get("input_text") or part.get("output_text") or ""
                if text:
                    parts.append(str(text))
    return "".join(parts)


def _load_codex_session_context(conv_id: str, agent_name: str, store,
                                user_id: str = "") -> list:
    thread_id = store.get_extra(conv_id, f"codex_app_server_thread:{agent_name or 'default'}") or ""
    if not thread_id:
        return []
    import os
    from core.llm_providers.codex_session import _get_sessions_base
    from core.llm_providers.codex_app_server import LLMCodexAppServerMixin
    uid = user_id or store.get_user_id(conv_id) or "default"
    workdir = os.path.join(_get_sessions_base(), uid, conv_id.replace(":", "_"), agent_name)
    jsonl_path = LLMCodexAppServerMixin._codex_app_rollout_path(workdir, thread_id)
    if not jsonl_path:
        return []
    messages = []
    try:
        with open(jsonl_path, "r", encoding="utf-8", errors="replace") as fh:
            for line_no, line in enumerate(fh):
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                payload = entry.get("payload") if isinstance(entry, dict) else None
                if not isinstance(payload, dict) or payload.get("type") != "message":
                    continue
                role = payload.get("role") or "assistant"
                content = _text_from_cli_content(payload.get("content"))
                if not content:
                    continue
                msg_id = entry.get("id") or entry.get("msg_id") or f"codex:{thread_id}:{line_no}"
                msg = {"role": role, "content": content, "msg_id": msg_id}
                msg["source"] = {"name": "codex-app-server"}
                messages.append(msg)
    except Exception as exc:
        logger.error("[codex-session] Failed to read rollout JSONL: %s", exc)
        return []
    return messages


def _load_gemini_session_context(conv_id: str, agent_name: str, store,
                                 user_id: str = "") -> list:
    session_id = store.get_extra(conv_id, f"gemini_acp_session:{agent_name or 'default'}") or ""
    if not session_id:
        return []
    import os
    from core.llm_providers.gemini_session import _get_sessions_base
    from core.llm_providers.gemini import LLMGeminiMixin
    uid = user_id or store.get_user_id(conv_id) or "default"
    workdir = os.path.join(_get_sessions_base(), uid, conv_id.replace(":", "_"), agent_name)
    messages = []
    for path in LLMGeminiMixin._gemini_acp_history_paths(workdir):
        try:
            found_session = False
            current = []
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for line_no, line in enumerate(fh):
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("sessionId") == session_id:
                        found_session = True
                    rtype = rec.get("type") or ""
                    if rtype not in ("user", "gemini"):
                        continue
                    role = "assistant" if rtype == "gemini" else "user"
                    content = _text_from_cli_content(rec.get("content"))
                    if not content:
                        continue
                    msg_id = rec.get("id") or rec.get("msg_id") or f"gemini:{os.path.basename(path)}:{line_no}"
                    msg = {"role": role, "content": content, "msg_id": msg_id}
                    if rec.get("model"):
                        msg["source"] = {"name": "gemini", "model": rec.get("model")}
                    current.append(msg)
            if found_session:
                messages.extend(current)
        except Exception as exc:
            logger.error("[gemini-session] Failed to read history JSONL: %s", exc)
    return messages


def _handle_context_ops(self, action, body, store, user_id, flowfile):
    """Handle context ops actions. Returns [flowfile] or None."""

    def _ctx_agent_name(agent_name=""):
        """Normalize UI context selectors to store agent names."""
        return "" if agent_name in ("", "ALL", "shared") else agent_name

    def _ctx_load(conv_id, agent_name=""):
        """Load the context the agent actually sees right now.

        Same precedence as agent_loop.py at runtime: agent_context
        first (compacted/diverged view), fallback to the personalized
        transcript only if no per-agent context exists yet (fresh
        conversation). This is the view the Context Editor must show.

        Compaction doesn't use this function — it needs the full
        transcript as source and calls load_transcript_for_agent
        directly.
        """
        if agent_name == "transcript":
            return store.load(conv_id, user_id=user_id) or []
        _name = _ctx_agent_name(agent_name)
        if _name:
            ctx = store.load_agent_context(conv_id, _name)
            if ctx is not None:
                return ctx
            return store.load_transcript_for_agent(conv_id, _name) or []
        shared = store.load_context(conv_id, user_id=user_id)
        return shared if shared is not None else (store.load(conv_id, user_id=user_id) or [])

    def _ctx_save(conv_id, data, agent_name=""):
        """Save context for an agent (or shared if no agent)."""
        if agent_name == "transcript":
            raise ValueError(
                "Transcript is read-only here; delete transcript messages "
                "with delete_message or switch to Shared/an agent context.")
        # "shared" or "" both mean the shared context (agent="")
        _name = _ctx_agent_name(agent_name)
        store.save_agent_context(conv_id, _name, data)
        if _name:
            store.invalidate_claude_session_for_agent(conv_id, _name)
        else:
            store.invalidate_claude_sessions(conv_id)

    def _ctx_cached_usage(conv_id, agent_name=""):
        """Read persisted context gauge without recomputing the full context."""
        _name = _ctx_agent_name(agent_name)
        if not _name:
            return None
        usage_map = store.get_extra(conv_id, "context_usage", user_id=user_id) or {}
        usage = usage_map.get(_name) if isinstance(usage_map, dict) else None
        if not isinstance(usage, dict) or int(usage.get("max", 0) or 0) <= 0:
            return None
        return {
            "used": int(usage.get("used", 0) or 0),
            "max": int(usage.get("max", 0) or 0),
            "pct": float(usage.get("pct", 0.0) or 0.0),
            "source": usage.get("source", "context_usage_cache"),
            "message_count": usage.get("message_count", 0),
            "cache_mode": usage.get("cache_mode", ""),
            "updated_at": usage.get("updated_at", 0),
            "computed_from": "persisted_context_usage",
        }

    def _ctx_visible_contexts(conv_id, raw_map, selected_agent=""):
        """Return context selector entries that represent real agents/sessions."""
        if not isinstance(raw_map, dict):
            raw_map = {}
        try:
            from core.conv_agent_config import get_all_agent_configs
            active_agents = set((get_all_agent_configs(conv_id) or {}).keys())
        except Exception:
            active_agents = set()
        hidden = {"background", "notification", "system"}
        if user_id:
            hidden.add(user_id)
        try:
            owner = (store._load_cache(conv_id) or {}).get("user_id", "")
            if owner:
                hidden.add(owner)
        except Exception:
            pass
        visible = {"*": raw_map.get("*", "messages")}
        for name, status in raw_map.items():
            if not name or name == "*" or name in hidden:
                continue
            if active_agents and name not in active_agents and name != selected_agent:
                continue
            visible[name] = status
        return visible

    def _ctx_llm_service_config(conv_id, agent_name=""):
        """Return the llm_service config associated with the selected agent."""
        _name = _ctx_agent_name(agent_name)
        if not _name:
            return {}
        try:
            from core.conv_agent_config import get_agent_config
            llm_service = (get_agent_config(conv_id, _name).get("llm_service")
                           or "")
            if not llm_service:
                return {}
            from core.service_registry import ServiceRegistry
            reg = ServiceRegistry.get_instance()
            svc = reg.resolve(llm_service, user_id=user_id, conv_id=conv_id)
            if svc:
                return dict(getattr(svc, "config", {}) or {})
            sdef = reg.resolve_definition(
                llm_service, user_id=user_id, conv_id=conv_id)
            return dict(getattr(sdef, "config", {}) or {}) if sdef else {}
        except Exception:
            logger.exception(
                "Failed to resolve llm_service config for compact agent %s",
                _name)
            return {}

    def _ctx_real_context_size(conv_id, agent_name=""):
        """Return provider/CLI real context window when the client exposes it."""
        _name = _ctx_agent_name(agent_name)
        if not _name:
            return 0
        try:
            from core.conv_agent_config import get_agent_config
            llm_service = (get_agent_config(conv_id, _name).get("llm_service")
                           or "")
            if not llm_service:
                return 0
            from core.service_registry import ServiceRegistry
            svc = ServiceRegistry.get_instance().resolve(
                llm_service, user_id=user_id, conv_id=conv_id)
            client = svc.get_client() if svc and hasattr(svc, "get_client") else None
            if not client:
                return 0
            return int(
                getattr(client, "_real_context_size", 0)
                or getattr(client, "_context_window", 0)
                or 0)
        except Exception:
            return 0

    def _ctx_max_tokens(conv_id, agent_name=""):
        """Get effective max from agent llm_service config capped by provider real window."""
        flow_default = int(self.config.get("max_context_size", 64000))
        cfg = _ctx_llm_service_config(conv_id, agent_name)
        try:
            configured = int((cfg or {}).get("max_context_size", 0) or 0)
        except Exception:
            configured = 0
        from core.context_window import effective_context_window
        return effective_context_window(
            configured, _ctx_real_context_size(conv_id, agent_name),
            fallback=flow_default)

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
        # Membership guard — `/compact ghost` used to silently create a
        # per-agent dir (data/.../ghost/) and write a compacted ctx for
        # an agent that was never added to this conv, producing orphan
        # state. require_agent_member auto-registers from a global
        # definition when possible (matches the user's mental model
        # "I have qwen configured globally"); otherwise fails loud.
        from core.conv_agent_config import require_agent_member
        _cp_err = require_agent_member(conv_id, _ctx_agent,
                                         user_id=user_id)
        if _cp_err:
            flowfile.set_content(json.dumps({"error": _cp_err}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        # Compaction always starts from the full transcript (personalized
        # for the agent). Feeding back an already-compacted agent_context
        # would layer stale summaries on top of each other.
        if _ctx_agent and _ctx_agent not in ("", "ALL"):
            source_data = store.load_transcript_for_agent(conv_id, _ctx_agent)
        else:
            source_data = store.load_context(conv_id, user_id=user_id)
        if source_data is None:
            source_data = store.load(conv_id, user_id=user_id)
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
        _compact_client, _, _compact_svc_id = self._get_summarizer_client(user_id, conversation_id=conv_id)
        if not _compact_client:
            flowfile.set_content(json.dumps({
                "error": "No summarizer service configured — compaction needs one.",
            }).encode())
            return [flowfile]
        _compact_budget_config = _ctx_llm_service_config(conv_id, _ctx_agent)
        _compact_max = _ctx_max_tokens(conv_id, _ctx_agent)
        _compact_source = source_data
        _compact_conv = conv_id
        _compact_agent_name = _ctx_agent
        _compact_keep = int(self.config.get("context_keep_recent", 6))

        _compact_instructions = body.get("instructions", "")

        def _do_compact():
            msgs = self._deserialize_messages(_compact_source, conversation_id=conv_id)
            before = len(msgs)
            estimated = self._estimate_tokens(msgs)
            # Single path: _compact now reads the BucketStore and only
            # summarizes messages since the last bucket. No separate
            # snapshot mechanism needed — buckets persist across restarts
            # and are the canonical pre-digested input.
            compacted = self._compact(
                msgs, _compact_client, _compact_max,
                conversation_id=_compact_conv,
                agent_name=_compact_agent_name,
                compact_instructions=_compact_instructions,
                force=True,
                user_id=user_id,
                budget_config=_compact_budget_config,
            )
            after_tokens = self._estimate_tokens(compacted)
            # CC session invalidation (extra clear + jsonl+companion purge on disk)
            # is handled by `_run_bg_context_op` via `_clear_claude_session` after
            # _do_compact returns. Do NOT clear the extra here — that would
            # make the subsequent purge a no-op (helper bails early on empty sid).
            return {"before": before, "after": len(compacted),
                    "tokens_before": estimated, "tokens_after": after_tokens,
                    "agent": _compact_agent_name or "shared",
                    "focus": _compact_instructions or None}

        # Scope the compact lock to the target agent: /compact claude
        # must NOT block other agents on the same conv. Only a
        # whole-conv /compact (agent_name=="" or "ALL"/"shared") uses
        # the sentinel that blocks everyone.
        _compact_lock_agent = (
            "" if _ctx_agent in ("", "ALL", "shared") else _ctx_agent)
        return self._run_bg_context_op(
            conv_id, "compact", _do_compact, flowfile,
            agent_name=_compact_lock_agent)

    if action == "rebuild":
        conv_id = body.get("conversation_id", "")
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        transcript = store.load(conv_id, user_id=user_id)
        if transcript is None:
            flowfile.set_content(json.dumps({"error": "Conversation not found"}).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        _compact_client, _, _compact_svc_id = self._get_summarizer_client(user_id, conversation_id=conv_id)
        if not _compact_client:
            flowfile.set_content(json.dumps({
                "error": "No summarizer service configured — rebuild needs compaction.",
            }).encode())
            return [flowfile]
        from core.conv_agent_config import get_all_agent_configs
        agent_names = sorted((get_all_agent_configs(conv_id) or {}).keys())

        def _do_rebuild():
            from core.bucket_store import BucketStore
            from core.bg_bucket_builder import BgBucketBuilder
            from core.conversation_event_bus import ConversationEventBus
            from tasks.ai.context_usage import (
                compute_context_usage, persist_context_usage,
                usage_event_payload)

            shared_candidates = store.filter_for_shared(transcript)
            shared_msgs = [store._transform_for_shared(m) for m in shared_candidates]
            store.save_agent_context(conv_id, "", shared_msgs)

            bucket_store = BucketStore.get(store._conv_dir(conv_id))
            buckets_before = bucket_store.object_count
            bucket_store.wipe()
            bucket_result = BgBucketBuilder.instance().build_now_sync(
                conv_id, user_id, allow_partial=True)

            for existing_name in store.list_agent_contexts(conv_id):
                if existing_name != "*" and existing_name not in agent_names:
                    store.delete_agent_context(conv_id, existing_name)

            compacted_agents = {}
            total_before = 0
            total_after = 0
            for name in agent_names:
                source_data = store.load_transcript_for_agent(conv_id, name) or []
                source_data = [m for m in source_data
                               if isinstance(m, dict)
                               and not m.get("display_only")]
                if len(source_data) < 4:
                    store.save_agent_context(conv_id, name, list(source_data))
                    compacted_agents[name] = {
                        "before": len(source_data), "after": len(source_data),
                        "skipped": "not_enough_messages",
                    }
                else:
                    msgs = self._deserialize_messages(
                        source_data, conversation_id=conv_id)
                    compacted = self._compact(
                        msgs, _compact_client, _ctx_max_tokens(conv_id, name),
                        conversation_id=conv_id,
                        agent_name=name,
                        compact_instructions="",
                        force=True,
                        user_id=user_id,
                        budget_config=_ctx_llm_service_config(conv_id, name),
                    )
                    serialized = self._serialize_messages(compacted)
                    store.save_agent_context(conv_id, name, serialized)
                    compacted_agents[name] = {
                        "before": len(source_data), "after": len(serialized),
                    }
                total_before += int(compacted_agents[name]["before"])
                total_after += int(compacted_agents[name]["after"])
                usage = compute_context_usage(
                    conv_id, name, user_id=user_id, store=store,
                    owner=self, source="rebuild_compact")
                persist_context_usage(conv_id, name, usage, store=store)
                ConversationEventBus.instance().publish_event(
                    conv_id, "message_meta", usage_event_payload(usage))

            store.invalidate_claude_sessions(conv_id)
            return {
                "agent": "ALL",
                "shared_messages": len(shared_msgs),
                "buckets_before": buckets_before,
                "buckets_built": bucket_result.get("buckets_built", 0),
                "rollups_fired": bucket_result.get("rollups_fired", 0),
                "agents": compacted_agents,
                "before": total_before,
                "after": total_after,
                "summarizer_service": _compact_svc_id,
            }

        return self._run_bg_context_op(
            conv_id, "rebuild", _do_rebuild, flowfile, agent_name="")

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
                page = store.load_page(
                    conv_id, limit=_limit, offset=_offset, user_id=user_id) or {}
            context_data = page.get("messages") or []
            total_count = page.get("total_count", len(context_data))
            has_more = page.get("has_more", False)
            diverged = True
        elif _ctx_agent.startswith("cc_session:"):
            _cc_agent = _ctx_agent[len("cc_session:"):]
            context_data = _load_cc_session_context(conv_id, _cc_agent, store, user_id=user_id)
            total_count = len(context_data)
            has_more = False
            diverged = True
        elif _ctx_agent.startswith("codex_session:"):
            _codex_agent = _ctx_agent[len("codex_session:"):]
            context_data = _load_codex_session_context(conv_id, _codex_agent, store, user_id=user_id)
            total_count = len(context_data)
            has_more = False
            diverged = True
        elif _ctx_agent.startswith("gemini_session:"):
            _gemini_agent = _ctx_agent[len("gemini_session:"):]
            context_data = _load_gemini_session_context(conv_id, _gemini_agent, store, user_id=user_id)
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
            page = store.load_agent_context_page(
                conv_id, _ctx_agent, limit=_limit, offset=_offset)
            diverged = page is not None
            if page is None:
                context_data = _ctx_load(conv_id, _ctx_agent)
                if context_data is None:
                    context_data = []
                total_count = len(context_data)
                end = len(context_data) - _offset
                start = max(0, end - _limit)
                context_data = context_data[start:end]
                has_more = start > 0
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
        conv_id = body.get("conversation_id", "")
        _ctx_agent = body.get("agent_name", "")
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        if _ctx_agent == "transcript":
            context_data = store.load(conv_id, user_id=user_id) or []
            diverged = False
        elif not _ctx_agent or _ctx_agent == "shared":
            context_data = _ctx_load(conv_id, "")
            diverged = True
        elif _ctx_agent.startswith("cc_session:"):
            _cc_agent = _ctx_agent[len("cc_session:"):]
            context_data = _load_cc_session_context(conv_id, _cc_agent, store, user_id=user_id)
            diverged = True
        elif _ctx_agent.startswith("codex_session:"):
            _codex_agent = _ctx_agent[len("codex_session:"):]
            context_data = _load_codex_session_context(conv_id, _codex_agent, store, user_id=user_id)
            diverged = True
        elif _ctx_agent.startswith("gemini_session:"):
            _gemini_agent = _ctx_agent[len("gemini_session:"):]
            context_data = _load_gemini_session_context(conv_id, _gemini_agent, store, user_id=user_id)
            diverged = True
        else:
            private_ctx = store.load_agent_context(conv_id, _ctx_agent)
            diverged = private_ctx is not None
            context_data = private_ctx if private_ctx is not None else _ctx_load(conv_id, _ctx_agent)
        flowfile.set_content(json.dumps({
            "context": context_data,
            "message_count": len(context_data),
            "diverged": diverged,
        }, ensure_ascii=False).encode())
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
        context_data = _ctx_load(conv_id, _ctx_agent)
        if context_data is None:
            context_data = store.load(conv_id, user_id=user_id) or []
        _idx = next(
            (i for i, m in enumerate(context_data)
             if m.get("msg_id") == msg_id or m.get("trace_id") == msg_id),
            -1,
        )
        if _idx < 0:
            flowfile.set_content(json.dumps({
                "error": f"Message {msg_id} not found in context — please refresh.",
            }).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        context_data[_idx]["content"] = new_content
        if new_role:
            context_data[_idx]["role"] = new_role
        _ctx_save(conv_id, context_data, _ctx_agent)
        deserialized = self._deserialize_messages(context_data, conversation_id=conv_id)
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
        msg_ids = body.get("msg_ids", [])
        # CC session: rewrite JSONL without selected entries
        if _ctx_agent.startswith("cc_session:"):
            _cc_agent = _ctx_agent[len("cc_session:"):]
            try:
                _rewrite_cc_session(conv_id, _cc_agent, store, remove_indices=set(indices))
                flowfile.set_content(json.dumps({"ok": True, "deleted": len(indices)}).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]
        if not conv_id or (not indices and not msg_ids):
            flowfile.set_content(json.dumps({"error": "Missing conversation_id or indices/msg_ids"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        if _ctx_agent == "transcript":
            flowfile.set_content(json.dumps({
                "error": "Use delete_message for transcript rows.",
            }).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        try:
            context_data = _ctx_load(conv_id, _ctx_agent)
            if context_data is None:
                context_data = store.load(conv_id, user_id=user_id) or []
            if msg_ids:
                msg_id_set = set(msg_ids)
                before = len(context_data)
                context_data = [
                    m for m in context_data
                    if (m.get("msg_id") not in msg_id_set
                        and m.get("trace_id") not in msg_id_set)
                ]
                deleted = before - len(context_data)
            else:
                # Remove indices in reverse order to preserve positions
                deleted = 0
                for idx in sorted(indices, reverse=True):
                    if 0 <= idx < len(context_data):
                        context_data.pop(idx)
                        deleted += 1
            _ctx_save(conv_id, context_data, _ctx_agent)
            deserialized = self._deserialize_messages(context_data, conversation_id=conv_id)
            estimated = self._estimate_tokens(deserialized)
            flowfile.set_content(json.dumps({
                "ok": True,
                "deleted": deleted,
                "message_count": len(context_data),
                "token_estimate": estimated,
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
        context_data = _ctx_load(conv_id, _ctx_agent)
        if context_data is None:
            context_data = store.load(conv_id, user_id=user_id) or []
        _before = len(context_data)
        context_data = [
            m for m in context_data
            if m.get("msg_id") != msg_id and m.get("trace_id") != msg_id
        ]
        if len(context_data) == _before:
            flowfile.set_content(json.dumps({
                "error": f"Message {msg_id} not found in context — please refresh.",
            }).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        _ctx_save(conv_id, context_data, _ctx_agent)
        deserialized = self._deserialize_messages(context_data, conversation_id=conv_id)
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
        deserialized = self._deserialize_messages(new_context, conversation_id=conv_id)
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
        before_msg_id = body.get("before_msg_id", "")
        after_msg_id = body.get("after_msg_id", "")
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        context_data = _ctx_load(conv_id, _ctx_agent)
        if context_data is None:
            context_data = store.load(conv_id, user_id=user_id) or []
        from core.llm_client import stamp_message
        msg = stamp_message({"role": role, "content": content}, conv_id)
        if before_msg_id:
            _idx = next((i for i, m in enumerate(context_data)
                          if m.get("msg_id") == before_msg_id), -1)
            if _idx < 0:
                flowfile.set_content(json.dumps({
                    "error": f"before_msg_id {before_msg_id} not found — please refresh.",
                }).encode())
                flowfile.set_attribute("http.response.status", "404")
                return [flowfile]
            context_data.insert(_idx, msg)
        elif after_msg_id:
            _idx = next((i for i, m in enumerate(context_data)
                          if m.get("msg_id") == after_msg_id), -1)
            if _idx < 0:
                flowfile.set_content(json.dumps({
                    "error": f"after_msg_id {after_msg_id} not found — please refresh.",
                }).encode())
                flowfile.set_attribute("http.response.status", "404")
                return [flowfile]
            context_data.insert(_idx + 1, msg)
        else:
            context_data.append(msg)
        _ctx_save(conv_id, context_data, _ctx_agent)
        deserialized = self._deserialize_messages(context_data, conversation_id=conv_id)
        estimated = self._estimate_tokens(deserialized)
        flowfile.set_content(json.dumps({
            "ok": True,
            "message_count": len(context_data),
            "token_estimate": estimated,
        }).encode())
        return [flowfile]

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
        _rf_msgs = store.load(conv_id, user_id=user_id)
        if not _rf_msgs:
            flowfile.set_content(json.dumps({"error": "Conversation not found"}).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]

        if _rf_target:
            _rf_idx = next(
                (i for i, m in enumerate(_rf_msgs)
                 if isinstance(m, dict) and m.get("msg_id") == _rf_target),
                -1,
            )
            if _rf_idx < 0:
                flowfile.set_content(json.dumps({
                    "error": f"msg_id {_rf_target} not found",
                }).encode())
                flowfile.set_attribute("http.response.status", "404")
                return [flowfile]
            keep_count = _rf_idx + 1
        else:
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

        kept_msgs = list(_rf_msgs[:keep_count])
        drop_ids = {
            str(m.get("msg_id") or "")
            for m in _rf_msgs[keep_count:]
            if isinstance(m, dict) and m.get("msg_id")
        }

        def _do_restart():
            import time as _time

            deleted_contexts = 0
            if keep_count == 0:
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
            elif drop_ids:
                store._remove_msg_ids_from_files(conv_id, drop_ids)
            try:
                from core.bucket_store import BucketStore
                BucketStore.get(store._conv_dir(conv_id)).wipe()
            except Exception:
                logger.debug("restart_from bucket wipe failed", exc_info=True)
            return {
                "operation": "restart_from",
                "kept_messages": len(kept_msgs),
                "deleted_contexts": deleted_contexts,
                "msg_id": _rf_target or None,
                "restart_index": keep_count,
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
            "message_count": store.message_count(conv_id),
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
