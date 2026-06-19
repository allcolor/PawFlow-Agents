"""AgentLoopTask actions — context ops"""

import json
import logging


logger = logging.getLogger(__name__)


# Sentinel: a cluster handler returns this when `action` is not one it owns.
_UNHANDLED = object()


def _estimate_unavailable() -> int:
    return 0


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


def _read_jsonl_tail(path: str, limit: int, offset: int,
                     convert_entry) -> dict:
    """Read a newest-first JSONL tail without parsing the whole file."""
    import os
    limit_i = max(1, int(limit or 50))
    offset_i = max(0, int(offset or 0))
    need = offset_i + limit_i + 1
    matches = []
    carry = b""
    block_size = 65536
    try:
        with open(path, "rb") as fh:
            pos = fh.seek(0, os.SEEK_END)
            while pos > 0 and len(matches) < need:
                read_size = min(block_size, pos)
                pos -= read_size
                fh.seek(pos)
                data = fh.read(read_size) + carry
                lines = data.splitlines()
                if pos > 0 and data and not data.startswith((b"\n", b"\r")):
                    carry = lines.pop(0) if lines else data
                else:
                    carry = b""
                for raw in reversed(lines):
                    if not raw.strip():
                        continue
                    try:
                        entry = json.loads(raw.decode("utf-8", errors="replace"))
                    except json.JSONDecodeError:
                        continue
                    msg = convert_entry(entry)
                    if msg:
                        matches.append(msg)
                        if len(matches) >= need:
                            break
            if pos <= 0 and carry.strip() and len(matches) < need:
                try:
                    entry = json.loads(carry.decode("utf-8", errors="replace"))
                    msg = convert_entry(entry)
                    if msg:
                        matches.append(msg)
                except json.JSONDecodeError:
                    pass
    except Exception as exc:
        logger.error("[session-tail] Failed to read JSONL tail %s: %s", path, exc)
        return {"messages": [], "total_count": 0, "has_more": False}

    has_more = len(matches) > offset_i + limit_i
    page_newest = matches[offset_i:offset_i + limit_i]
    page = list(reversed(page_newest))
    visible_total = offset_i + len(page) + (1 if has_more else 0)
    return {"messages": page, "total_count": visible_total,
            "has_more": has_more}


def _cc_session_entry_to_msg(entry: dict) -> dict:
    etype = entry.get("type", "")
    if etype not in ("user", "assistant"):
        return {}
    msg = entry.get("message", {})
    role = msg.get("role", etype)
    content_blocks = msg.get("content", "")

    if isinstance(content_blocks, list):
        parts = []
        tool_calls = []
        had_tool_result = False
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
                had_tool_result = True
                tr_content = block.get("content", "")
                if isinstance(tr_content, list):
                    tr_content = " ".join(
                        b.get("text", "") for b in tr_content
                        if isinstance(b, dict))
                parts.append(f"[tool_result: {str(tr_content)[:200]}]")
        content = "\n".join(parts) if parts else ""
        display_role = "tool" if (had_tool_result and not any(
            p and not p.startswith("[tool_result:") for p in parts
        )) else role
        msg_entry = {"role": display_role, "content": content}
        if tool_calls:
            msg_entry["tool_calls"] = tool_calls
    elif isinstance(content_blocks, str):
        msg_entry = {"role": role, "content": content_blocks}
    else:
        msg_entry = {"role": role, "content": str(content_blocks)}

    msg_entry["msg_id"] = entry.get("uuid", "")
    if msg.get("model"):
        msg_entry["source"] = {"name": "claude-code", "model": msg["model"]}
    return msg_entry


def _load_cc_session_context(conv_id: str, agent_name: str, store,
                             user_id: str = "", limit: int = 0,
                             offset: int = 0):
    """Load Claude Code session JSONL and convert to PawFlow message format."""
    jsonl_path = _find_cc_session_jsonl(conv_id, agent_name, store, user_id=user_id)
    if not jsonl_path:
        return {"messages": [], "total_count": 0, "has_more": False} if limit else []

    if limit:
        return _read_jsonl_tail(jsonl_path, limit, offset, _cc_session_entry_to_msg)

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
                msg_entry = _cc_session_entry_to_msg(entry)
                if msg_entry:
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
                                user_id: str = "", limit: int = 0,
                                offset: int = 0):
    thread_id = store.get_extra(conv_id, f"codex_app_server_thread:{agent_name or 'default'}") or ""
    if not thread_id:
        return {"messages": [], "total_count": 0, "has_more": False} if limit else []
    import os
    from core.llm_providers.codex_session import _get_sessions_base
    from core.llm_providers.codex_app_server import LLMCodexAppServerMixin
    uid = user_id or store.get_user_id(conv_id) or "default"
    workdir = os.path.join(_get_sessions_base(), uid, conv_id.replace(":", "_"), agent_name)
    jsonl_path = LLMCodexAppServerMixin._codex_app_rollout_path(workdir, thread_id)
    if not jsonl_path:
        return {"messages": [], "total_count": 0, "has_more": False} if limit else []
    def _convert(entry):
        payload = entry.get("payload") if isinstance(entry, dict) else None
        if not isinstance(payload, dict) or payload.get("type") != "message":
            return {}
        role = payload.get("role") or "assistant"
        content = _text_from_cli_content(payload.get("content"))
        if not content:
            return {}
        msg_id = entry.get("id") or entry.get("msg_id") or f"codex:{thread_id}"
        return {"role": role, "content": content, "msg_id": msg_id,
                "source": {"name": "codex-app-server"}}
    if limit:
        return _read_jsonl_tail(jsonl_path, limit, offset, _convert)
    messages = []
    try:
        with open(jsonl_path, "r", encoding="utf-8", errors="replace") as fh:
            for line_no, line in enumerate(fh):
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = _convert(entry)
                if not msg:
                    continue
                if msg.get("msg_id") == f"codex:{thread_id}":
                    msg["msg_id"] = f"codex:{thread_id}:{line_no}"
                messages.append(msg)
    except Exception as exc:
        logger.error("[codex-session] Failed to read rollout JSONL: %s", exc)
        return []
    return messages


def _load_gemini_session_context(conv_id: str, agent_name: str, store,
                                 user_id: str = "", limit: int = 0,
                                 offset: int = 0):
    session_id = store.get_extra(conv_id, f"gemini_acp_session:{agent_name or 'default'}") or ""
    if not session_id:
        return {"messages": [], "total_count": 0, "has_more": False} if limit else []
    import os
    from core.llm_providers.gemini_session import _get_sessions_base
    from core.llm_providers.gemini import LLMGeminiMixin
    uid = user_id or store.get_user_id(conv_id) or "default"
    workdir = os.path.join(_get_sessions_base(), uid, conv_id.replace(":", "_"), agent_name)
    messages = []
    paths = list(LLMGeminiMixin._gemini_acp_history_paths(workdir))
    if limit and paths:
        path = paths[-1]
        def _convert(rec):
            if rec.get("sessionId") != session_id:
                return {}
            rtype = rec.get("type") or ""
            if rtype not in ("user", "gemini"):
                return {}
            role = "assistant" if rtype == "gemini" else "user"
            content = _text_from_cli_content(rec.get("content"))
            if not content:
                return {}
            msg_id = rec.get("id") or rec.get("msg_id") or f"gemini:{os.path.basename(path)}"
            msg = {"role": role, "content": content, "msg_id": msg_id}
            if rec.get("model"):
                msg["source"] = {"name": "gemini", "model": rec.get("model")}
            return msg
        return _read_jsonl_tail(path, limit, offset, _convert)

    for path in paths:
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
