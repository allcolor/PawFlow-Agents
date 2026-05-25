"""AgentLoopTask mixin — AgentSerialization methods

Auto-extracted from tasks/ai/agent_loop.py.
All methods access self (AgentLoopTask instance).
"""
import json
import logging
import threading
import time
import uuid
from typing import Dict, Any, List, Optional


from core import FlowFile
from core.llm_client import (
    LLMClient, LLMMessage, LLMResponse, LLMToolDefinition,
    LLMToolCall, LLMToolResult, LLMClientError,
)
from core.tool_registry import ToolRegistry, create_default_registry

logger = logging.getLogger(__name__)


def _uuid() -> str:
    return uuid.uuid4().hex[:12]

_LLM_ERROR_PATTERNS = (
    "LLM call failed:", "API Error:", "Failed to authenticate",
    "LLMClientError:", "Claude Code auth failed:",
    "Budget exceeded", "LLM streaming failed",
)

def _looks_like_llm_error(text: str) -> bool:
    """Heuristic: detect LLM error messages for legacy messages without is_error flag."""
    if len(text) > 500:
        return False
    return any(p in text for p in _LLM_ERROR_PATTERNS)


class AgentSerializationMixin:
    """Methods extracted from AgentLoopTask."""


    def _serialize_messages(self, messages: List[LLMMessage],
                           channel: str = "") -> List[Dict[str, Any]]:
        """Serialize messages for storage (ephemeral messages are excluded)."""
        result = []
        tool_call_parents: Dict[str, str] = {}
        for m in messages:
            if m.source and m.source.get("type") == "ephemeral":
                continue
            entry: Dict[str, Any] = {"role": m.role, "content": m.content,
                                     "msg_id": m.msg_id, "ts": m.timestamp,
                                     "seq": m.seq}
            if m.tool_call_id:
                entry["tool_call_id"] = m.tool_call_id
            if channel and m.role in ("user", "assistant"):
                entry["channel"] = channel
            if m.source:
                entry["source"] = m.source
            if m.display_only:
                entry["display_only"] = True
            if m.is_error:
                entry["is_error"] = True
            if m.role == "assistant":
                parent_id = entry.get("msg_id", "")
                result.append(entry)
                if m.thinking:
                    trow = {
                        "role": "thinking", "content": m.thinking,
                        "msg_id": _uuid(), "ts": m.timestamp,
                        "parent_message_id": parent_id,
                    }
                    if channel:
                        trow["channel"] = channel
                    if m.source:
                        trow["source"] = m.source
                    if m.thinking_signature:
                        trow["thinking_signature"] = m.thinking_signature
                    result.append(trow)
                for tc in (m.tool_calls or []):
                    tcid = tc.id
                    crow = {
                        "role": "tool_call", "content": "",
                        "msg_id": _uuid(), "ts": tc.timestamp or m.timestamp,
                        "parent_message_id": parent_id,
                        "tool_call_id": tcid,
                        "tool_name": tc.name,
                        "name": tc.name,
                        "arguments": tc.arguments,
                    }
                    if getattr(tc, "tool_origin", ""):
                        crow["tool_origin"] = tc.tool_origin
                    if channel:
                        crow["channel"] = channel
                    if m.source:
                        crow["source"] = m.source
                    result.append(crow)
                    if tcid:
                        tool_call_parents[tcid] = crow["msg_id"]
                continue
            if m.role == "tool" and m.tool_call_id:
                parent_id = tool_call_parents.get(m.tool_call_id)
                if parent_id:
                    entry["parent_message_id"] = parent_id
            result.append(entry)
        return result


    def _deserialize_messages(self, data: List[Dict[str, Any]], *,
                              include_display_only: bool = False,
                              conversation_id: str = "") -> List[LLMMessage]:
        """Deserialize messages from storage.

        By default, display_only messages (sub_agent_trace, etc.) are
        excluded — they are transcript-only, not for LLM context.
        Pass include_display_only=True for UI replay.
        """
        messages = []
        by_msg_id: Dict[str, LLMMessage] = {}
        for entry in data:
            if not include_display_only and entry.get("display_only"):
                continue
            # ts is mandatory on disk for every non-system message — set
            # by the producer at creation. seq is the on-disk line index
            # (stamped by ConversationStore._stamp_line at write time) and
            # is read through but not required here: a freshly-built
            # message that hasn't hit disk yet has no seq. System prompts
            # are ephemeral (rebuilt from the agent definition at every
            # load) and exempt from the ts requirement, matching the
            # store's _validate_message exemption.
            _role = entry.get("role")
            _ts = entry.get("ts") or entry.get("timestamp")
            _seq = entry.get("seq")
            if _role != "system" and not _ts:
                raise ValueError(
                    f"Message missing ts on disk "
                    f"(msg_id={entry.get('msg_id')}, role={_role}) — "
                    f"producer bug, creation timestamp must be set at "
                    f"message creation.")
            if _role == "thinking":
                parent = by_msg_id.get(entry.get("parent_message_id", ""))
                if parent is not None:
                    parent.thinking = ((parent.thinking or "") +
                                       ("\n" if parent.thinking and entry.get("content") else "") +
                                       str(entry.get("content") or ""))
                    if entry.get("thinking_signature"):
                        parent.thinking_signature = entry.get("thinking_signature", "")
                continue

            if _role == "tool_call":
                parent = by_msg_id.get(entry.get("parent_message_id", ""))
                if parent is not None:
                    tcid = entry.get("tool_call_id") or entry.get("tc_id") or ""
                    parent.tool_calls = list(parent.tool_calls or [])
                    parent.tool_calls.append(LLMToolCall(
                        id=tcid,
                        name=entry.get("tool_name") or entry.get("name") or entry.get("tool") or "",
                        arguments=entry.get("arguments", {}) or {},
                        timestamp=entry.get("ts", 0),
                        tool_origin=entry.get("tool_origin", "") or "",
                    ))
                continue

            msg = LLMMessage(
                role=entry["role"],
                content=self._content_with_attachment_refs(entry),
                tool_calls=None,
                tool_call_id=entry.get("tool_call_id"),
                source=entry.get("source"),
                msg_id=entry.get("msg_id", ""),
                display_only=entry.get("display_only", False),
                thinking=entry.get("thinking", ""),
                thinking_signature=entry.get("thinking_signature", ""),
                timestamp=_ts,
                seq=_seq,
                conversation_id=(entry.get("conversation_id")
                                  or conversation_id),
            )
            messages.append(msg)
            if msg.msg_id:
                by_msg_id[msg.msg_id] = msg
        return messages


    @staticmethod
    def _content_with_attachment_refs(entry: Dict[str, Any]) -> Any:
        """Merge stored user attachments back into message content refs.

        Streaming pre-persists the user text immediately and stores uploaded
        files in a sibling ``attachments`` field. Reload/context paths render
        and resolve images from multipart ``content`` refs, so reconstruct that
        shape without touching the stored transcript format.
        """
        raw_content = entry.get("content", "")
        attachments = entry.get("attachments") or []
        if entry.get("role") != "user" or not isinstance(attachments, list):
            return raw_content

        parts = list(raw_content) if isinstance(raw_content, list) else []
        if not parts and isinstance(raw_content, str) and raw_content:
            parts.append({"type": "text", "text": raw_content})

        existing_fids = {
            p.get("file_id")
            for p in parts
            if isinstance(p, dict) and p.get("file_id")
        }
        added_ref = False
        for att in attachments:
            if not isinstance(att, dict):
                continue
            fid = att.get("file_id", "")
            if not fid or fid in existing_fids:
                continue
            mime = att.get("mime_type", "application/octet-stream")
            ref_type = "image_ref" if str(mime).startswith("image/") else "file_ref"
            parts.append({
                "type": ref_type,
                "file_id": fid,
                "filename": att.get("filename", "image" if ref_type == "image_ref" else "file"),
                "mime_type": mime,
                "size": att.get("size", 0),
            })
            existing_fids.add(fid)
            added_ref = True

        if added_ref:
            return parts
        return raw_content


    @staticmethod
    def _classify_messages_for_display(
        raw_messages: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Classify stored messages for chat UI display.

        Returns list of dicts with:
          type: "user" | "assistant" | "tool_call" | "tool_result" | "system"
          role: original role
          content: text content
          tool_name: (for tool_call/tool_result) tool name
          tool_args: (for tool_call) stringified arguments
        System messages are excluded (internal to LLM context).
        """
        result = []
        _tc_id_to_name = {}  # tool_call_id → display name (for tool_result matching)
        _tc_id_to_origin = {}  # tool_call_id → mcp/native origin
        _delegate_tc_ids = set()  # tc_ids for delegate calls (hidden, replaced by delegate blocks)
        _META_TOOLS = {"get_tool_schema"}
        for raw_idx, m in enumerate(raw_messages):
            role = m.get("role", "")
            _display_ts = m.get("timestamp") or m.get("ts")
            if role == "system":
                continue  # skip system prompts
            raw_content = AgentSerializationMixin._content_with_attachment_refs(m)
            # Normalize content to string (may be a list for multipart messages)
            if isinstance(raw_content, list):
                # Preserve image_ref/file_ref parts for user messages so
                # the frontend can render attachment thumbnails on reload.
                _has_refs = any(
                    isinstance(p, dict) and p.get("type") in ("image_ref", "file_ref")
                    for p in raw_content
                )
                if _has_refs and role == "user":
                    content = raw_content  # pass through as-is
                else:
                    text_parts = []
                    for p in raw_content:
                        if isinstance(p, dict):
                            if p.get("type") == "text":
                                text_parts.append(p.get("text", ""))
                            elif p.get("type") == "image_url":
                                text_parts.append("[Image]")
                            elif p.get("type") == "document":
                                text_parts.append(f"[Document: {p.get('filename', 'file')}]")
                        elif isinstance(p, str):
                            text_parts.append(p)
                    content = "\n".join(text_parts)
            elif isinstance(raw_content, str):
                content = raw_content
            else:
                content = str(raw_content) if raw_content else ""

            tool_call_id = m.get("tool_call_id")
            if role == "tool" and tool_call_id:
                # Skip results for hidden meta tools
                if _tc_id_to_name.get(tool_call_id) in _META_TOOLS:
                    continue
                # Skip delegate tool results — shown inside delegate blocks
                if tool_call_id in _delegate_tc_ids:
                    continue
                # Tool result message — strip outer <tool_output tool="...">
                # anti-injection envelope for display. Inner <tool_output>
                # literals that appear naturally inside grep matches, file
                # contents etc. are kept verbatim.
                display_content = content
                if display_content.startswith("<tool_output tool="):
                    first_nl = display_content.find("\n")
                    if first_nl >= 0:
                        display_content = display_content[first_nl + 1:]
                    _close_idx = display_content.rfind("</tool_output>")
                    if _close_idx >= 0:
                        display_content = display_content[:_close_idx].rstrip("\n")
                # Use longer preview for diff results
                _is_diff = any(p in display_content for p in ("replacement(s):", "Edited ", "hunks"))
                _limit = 2000 if _is_diff else 300
                preview = display_content[:_limit]
                _tr_entry = {
                    "type": "tool_result", "role": "tool",
                    "content": preview + ("..." if len(display_content) > _limit else ""),
                    "tool_call_id": tool_call_id,
                    "tc_id": tool_call_id,
                }
                if m.get("msg_id"):
                    _tr_entry["msg_id"] = m["msg_id"]
                if tool_call_id in _tc_id_to_name:
                    _tr_entry["tool_name"] = _tc_id_to_name[tool_call_id]
                if tool_call_id in _tc_id_to_origin:
                    _tr_entry["tool_origin"] = _tc_id_to_origin[tool_call_id]
                if m.get("source"):
                    _tr_entry["source"] = m["source"]
                if _display_ts:
                    _tr_entry["timestamp"] = _display_ts
                result.append(_tr_entry)
            elif role == "sub_agent_trace":
                entry = {
                    "type": "sub_agent_trace", "role": "sub_agent_trace",
                    "content": content,
                    "raw_index": raw_idx,
                    "display_only": True,
                    "trace": m.get("trace") or [],
                    "trace_id": m.get("trace_id", ""),
                }
                # msg_id is what the context editor's delete path keys
                # on. Older traces were persisted without one — fall back
                # to trace_id so they're still selectable + deletable.
                _mid = m.get("msg_id") or m.get("trace_id", "")
                if _mid:
                    entry["msg_id"] = _mid
                if m.get("source"):
                    entry["source"] = m["source"]
                if _display_ts:
                    entry["timestamp"] = _display_ts
                result.append(entry)
            elif role in ("tool_call", "tool_result", "thinking"):
                from core.llm_client import has_complete_mcp_tool_call, unwrap_mcp_tool
                if role == "tool_call":
                    raw_name = m.get("tool_name") or m.get("name") or m.get("tool") or "?"
                    raw_args = m.get("arguments", {}) or {}
                    if not has_complete_mcp_tool_call(raw_name, raw_args):
                        continue
                    tool_name, tool_args = unwrap_mcp_tool(raw_name, raw_args)
                    tcid = m.get("tool_call_id") or m.get("tc_id") or ""
                    if tcid:
                        _tc_id_to_name[tcid] = tool_name
                    tool_origin = m.get("tool_origin", "") or ""
                    if tcid and tool_origin:
                        _tc_id_to_origin[tcid] = tool_origin
                    if tool_name == "delegate":
                        if tcid:
                            _delegate_tc_ids.add(tcid)
                        continue
                    if tool_name in _META_TOOLS:
                        continue
                    tool_args_str = json.dumps(tool_args, ensure_ascii=False)[:500] if tool_args else ""
                    args_preview = ""
                    if isinstance(tool_args, dict) and tool_args:
                        parts = []
                        for k, v in tool_args.items():
                            vs = v[:60] if isinstance(v, str) else json.dumps(v, ensure_ascii=False)[:60]
                            parts.append(f"{k}={vs}")
                        args_preview = ", ".join(parts)
                        if len(args_preview) > 120:
                            args_preview = args_preview[:120] + "..."
                    src = m.get("source") or {}
                    src_agent = src.get("name", "") if isinstance(src, dict) else ""
                    src_svc = src.get("llm_service", "") if isinstance(src, dict) else ""
                    src_label = src_agent + (f" via {src_svc}" if src_svc else "")
                    display = f"🔧 [{src_label}] {tool_name}" if src_label else f"🔧 {tool_name}"
                    if args_preview:
                        display += f"({args_preview})"
                    entry = {
                        "type": "tool_call", "role": "tool_call",
                        "content": content or display,
                        "raw_index": raw_idx,
                        "display_only": True,
                        "tool_name": tool_name,
                        "tool_args": tool_args_str,
                        "arguments": tool_args,
                    }
                    if tool_origin:
                        entry["tool_origin"] = tool_origin
                    if tcid:
                        entry["tool_call_id"] = tcid
                        entry["tc_id"] = tcid
                    if m.get("msg_id"):
                        entry["msg_id"] = m["msg_id"]
                    if m.get("source"):
                        entry["source"] = m["source"]
                    if _display_ts:
                        entry["timestamp"] = _display_ts
                    result.append(entry)
                    continue
                # Skip meta tools from display
                if role in ("tool_call", "tool_result") and m.get("tool_name") in _META_TOOLS:
                    continue
                if role == "thinking" and not str(content).strip():
                    continue
                # display_only messages from claude-code turns — pass through as-is
                entry = {"type": role, "role": role, "content": content, "raw_index": raw_idx,
                         "display_only": True}
                if m.get("msg_id"):
                    entry["msg_id"] = m["msg_id"]
                if m.get("parent_message_id"):
                    entry["parent_message_id"] = m["parent_message_id"]
                if m.get("source"):
                    entry["source"] = m["source"]
                if m.get("tool_name"):
                    entry["tool_name"] = m["tool_name"]
                if m.get("tool_args"):
                    entry["tool_args"] = m["tool_args"]
                if m.get("tool_origin"):
                    entry["tool_origin"] = m["tool_origin"]
                if m.get("tool_call_id"):
                    entry["tool_call_id"] = m["tool_call_id"]
                if m.get("tc_id"):
                    entry["tc_id"] = m["tc_id"]
                elif m.get("tool_call_id"):
                    entry["tc_id"] = m["tool_call_id"]
                if m.get("display_type"):
                    entry["display_type"] = m["display_type"]
                if _display_ts:
                    entry["timestamp"] = _display_ts
                result.append(entry)
            elif role in ("user", "assistant"):
                # Skip internal system instructions injected as user messages
                if role == "user" and isinstance(content, str) and content.startswith("[System:"):
                    continue
                # Skip synthetic context messages (compaction acks, resume acks)
                _src = m.get("source") or {}
                if isinstance(_src, dict) and _src.get("type") == "context":
                    continue
                if role == "assistant" and not str(content).strip():
                    continue
                _type = role
                if role == "assistant" and (
                    m.get("is_error")
                    or (content and _looks_like_llm_error(content))
                ):
                    _type = "error"
                entry = {"type": _type, "role": role, "content": content, "raw_index": raw_idx}
                if m.get("msg_id"):
                    entry["msg_id"] = m["msg_id"]
                if m.get("display_only"):
                    entry["display_only"] = True
                    # Preserve display_type and structured data for proper rendering
                    if m.get("display_type"):
                        entry["type"] = m["display_type"]
                        entry["display_type"] = m["display_type"]
                    if m.get("tool_name"):
                        entry["tool_name"] = m["tool_name"]
                    if m.get("tool_args"):
                        entry["tool_args"] = m["tool_args"]
                if _display_ts:
                    entry["timestamp"] = _display_ts
                if m.get("channel"):
                    entry["channel"] = m["channel"]
                if m.get("source"):
                    entry["source"] = m["source"]
                elif role == "assistant":
                    # Infer source from identity prefix if present
                    import re as _re_src
                    _prefix_match = _re_src.match(r'^\[([^\]]+)\]:\s*', content)
                    if _prefix_match:
                        entry["source"] = {"type": "agent", "name": _prefix_match.group(1)}
                    else:
                        entry["source"] = {"type": "agent", "name": ""}
                result.append(entry)
        # Propagate task_id from source to top-level for frontend task block grouping
        for _item in result:
            _src = _item.get("source")
            if isinstance(_src, dict) and _src.get("task_id") and "task_id" not in _item:
                _item["task_id"] = _src["task_id"]
        return AgentSerializationMixin._replay_thinking_before_parent(result)


    @staticmethod
    def _replay_thinking_before_parent(
        messages: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Match live SSE order for persisted reasoning rows.

        Provider transcripts can persist the visible assistant row first and the
        attached thinking row immediately after it, both with the same
        timestamp. Live SSE renders the reasoning before the final assistant
        message, so reload must use the explicit parent link rather than raw
        JSONL order for those display-only rows.
        """
        parent_ids = {
            str(m.get("msg_id") or "")
            for m in messages
            if m.get("msg_id")
        }
        thinking_by_parent: Dict[str, List[Dict[str, Any]]] = {}
        anchored_thinking_ids = set()
        for item in messages:
            if item.get("type") != "thinking":
                continue
            parent_id = str(item.get("parent_message_id") or "")
            if parent_id and parent_id in parent_ids:
                thinking_by_parent.setdefault(parent_id, []).append(item)
                anchored_thinking_ids.add(id(item))
        if not thinking_by_parent:
            return messages

        replayed: List[Dict[str, Any]] = []
        for item in messages:
            if id(item) in anchored_thinking_ids:
                continue
            msg_id = str(item.get("msg_id") or "")
            if msg_id in thinking_by_parent:
                replayed.extend(thinking_by_parent.pop(msg_id))
            replayed.append(item)
        return replayed


    @staticmethod
    def _messages_to_text(messages: List[LLMMessage]) -> str:
        """Convert a list of messages to readable text for summarization."""
        lines = []
        for m in messages:
            role = m.role.upper()
            if isinstance(m.content, str):
                content = m.content
            elif isinstance(m.content, list):
                parts = []
                for p in m.content:
                    if p.get("type") == "text":
                        parts.append(p["text"])
                    elif p.get("type") == "document":
                        parts.append(f"[Document: {p.get('filename', 'file')}] {p.get('text', '')[:500]}")
                    elif p.get("type") == "image_url":
                        parts.append("[Image attached]")
                content = "\n".join(parts)
            else:
                content = str(m.content)

            if m.tool_calls:
                tc_desc = ", ".join(f"{tc.name}({json.dumps(tc.arguments)[:100]})" for tc in m.tool_calls)
                lines.append(f"{role}: {content}\n  Tool calls: {tc_desc}")
            elif m.role == "tool":
                lines.append(f"TOOL_RESULT (id={m.tool_call_id}): {content[:300]}")
            else:
                lines.append(f"{role}: {content}")
        return "\n\n".join(lines)

    # ── Attachment handling ──────────────────────────────────────────


    @staticmethod
    def _sanitize_for_llm(text: str) -> str:
        """Remove characters that break LLM API JSON parsing."""
        import re as _re
        # Strip C0/C1 control chars except \n \r \t
        text = _re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', text)
        # Remove lone surrogates (invalid in JSON)
        text = text.encode("utf-8", errors="replace").decode("utf-8", errors="replace")
        # Replace null bytes that may survive
        text = text.replace('\x00', '')
        return text
