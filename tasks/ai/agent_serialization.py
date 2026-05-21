"""AgentLoopTask mixin — AgentSerialization methods

Auto-extracted from tasks/ai/agent_loop.py.
All methods access self (AgentLoopTask instance).
"""
import json
import logging
import threading
import time
from typing import Dict, Any, List, Optional


from core import FlowFile
from core.llm_client import (
    LLMClient, LLMMessage, LLMResponse, LLMToolDefinition,
    LLMToolCall, LLMToolResult, LLMClientError,
)
from core.tool_registry import ToolRegistry, create_default_registry

logger = logging.getLogger(__name__)

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
        for m in messages:
            if m.source and m.source.get("type") == "ephemeral":
                continue
            entry: Dict[str, Any] = {"role": m.role, "content": m.content,
                                     "msg_id": m.msg_id, "ts": m.timestamp,
                                     "seq": m.seq}
            if m.tool_calls:
                entry["tool_calls"] = [
                    {"id": tc.id, "name": tc.name, "arguments": tc.arguments,
                     "ts": tc.timestamp}
                    for tc in m.tool_calls
                ]
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
            if m.thinking:
                entry["thinking"] = m.thinking
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
        for entry in data:
            if not include_display_only and entry.get("display_only"):
                continue
            tool_calls = None
            if "tool_calls" in entry:
                tool_calls = [
                    LLMToolCall(
                        id=tc["id"],
                        name=tc["name"],
                        arguments=tc.get("arguments", {}),
                        timestamp=tc.get("ts", 0),
                    )
                    for tc in (entry["tool_calls"] or [])
                ]
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
            messages.append(LLMMessage(
                role=entry["role"],
                content=self._content_with_attachment_refs(entry),
                tool_calls=tool_calls,
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
            ))
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

            tool_calls = m.get("tool_calls")
            tool_call_id = m.get("tool_call_id")

            _src_for_display = m.get("source") or {}
            if (role == "assistant"
                    and not (isinstance(_src_for_display, dict)
                             and _src_for_display.get("type") == "context")):
                _thinking = str(m.get("thinking") or "")
                if _thinking.strip():
                    _think_entry = {
                        "type": "thinking",
                        "role": "thinking",
                        "content": _thinking,
                        "raw_index": raw_idx,
                        "display_only": True,
                    }
                    if m.get("msg_id"):
                        _think_entry["msg_id"] = m["msg_id"]
                    if _display_ts:
                        _think_entry["timestamp"] = _display_ts
                    if m.get("source"):
                        _think_entry["source"] = m["source"]
                    result.append(_think_entry)

            if role == "assistant" and tool_calls:
                # Build tc_id → display name map for tool_result matching
                from core.llm_client import unwrap_mcp_tool
                for tc in tool_calls:
                    _unwrapped_name, _ = unwrap_mcp_tool(tc.get("name", "?"), tc.get("arguments", {}))
                    _tc_id_to_name[tc.get("id", "")] = _unwrapped_name
                # Assistant message that contains tool calls
                if content:
                    _tc_entry = {
                        "type": "assistant", "role": "assistant",
                        "content": content,
                    }
                    if m.get("source"):
                        _tc_entry["source"] = m["source"]
                    if _display_ts:
                        _tc_entry["timestamp"] = _display_ts
                    result.append(_tc_entry)
                _tc_source = m.get("source")
                for tc in tool_calls:
                    # Build rich display matching SSE tool_call format
                    _tc_name = tc.get("name", "?")
                    _tc_args = tc.get("arguments", {})
                    _tc_name, _tc_args = unwrap_mcp_tool(_tc_name, _tc_args)
                    if _tc_name in _META_TOOLS:
                        continue
                    # Hide delegate tool_calls — replaced by delegate blocks (sub_agent_trace)
                    if _tc_name == "delegate":
                        _delegate_tc_ids.add(tc.get("id", ""))
                        continue
                    _tc_args_str = json.dumps(_tc_args, ensure_ascii=False)[:500] if _tc_args else ""
                    # Format source label
                    _src_agent = (_tc_source or {}).get("name", "") if _tc_source else ""
                    _src_svc = (_tc_source or {}).get("llm_service", "") if _tc_source else ""
                    _src_label = _src_agent
                    if _src_svc:
                        _src_label += f" via {_src_svc}"
                    # Format args preview
                    _args_preview = ""
                    if isinstance(_tc_args, dict) and _tc_args:
                        _parts = []
                        for k, v in _tc_args.items():
                            vs = v[:60] if isinstance(v, str) else json.dumps(v, ensure_ascii=False)[:60]
                            _parts.append(f"{k}={vs}")
                        _args_preview = ", ".join(_parts)
                        if len(_args_preview) > 120:
                            _args_preview = _args_preview[:120] + "..."
                    _display = f"🔧 [{_src_label}] {_tc_name}"
                    if _args_preview:
                        _display += f"({_args_preview})"
                    _tc_entry2 = {
                        "type": "tool_call", "role": "assistant",
                        "content": _display,
                        "tool_name": _tc_name,
                        "tool_args": _tc_args_str,
                        "tc_id": tc.get("id", ""),
                        "arguments": _tc_args,
                        "source": _tc_source,
                    }
                    if _display_ts:
                        _tc_entry2["timestamp"] = _display_ts
                    result.append(_tc_entry2)
            elif role == "tool" and tool_call_id:
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
                # Skip meta tools from display
                if role in ("tool_call", "tool_result") and m.get("tool_name") in _META_TOOLS:
                    continue
                if role == "thinking" and not str(content).strip():
                    continue
                # display_only messages from claude-code turns — pass through as-is
                entry = {"type": role, "role": role, "content": content, "raw_index": raw_idx,
                         "display_only": True}
                if m.get("source"):
                    entry["source"] = m["source"]
                if m.get("tool_name"):
                    entry["tool_name"] = m["tool_name"]
                if m.get("tool_args"):
                    entry["tool_args"] = m["tool_args"]
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
        return result


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
