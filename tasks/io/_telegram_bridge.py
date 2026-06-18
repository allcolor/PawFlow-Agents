"""Telegram agent client tasks.

These tasks make Telegram a transport for the shared agent runtime instead of
running a separate Telegram-only AgentLoopTask.
"""

from __future__ import annotations

import logging
import base64
import hashlib
import html
import mimetypes
import re
import threading
import time
from typing import Any, Dict, List, Optional

from core import FlowFile
from core.base_task import BaseTask

logger = logging.getLogger(__name__)
# Split out of telegram_agent_client.py for the <=800-line rule; re-exported
# from tasks.io.telegram_agent_client (invariant 1: import-path stability).

from tasks.io._telegram_voice import _attach_telegram_tts_audio  # noqa: E402

_TELEGRAM_LIVE_ASSISTANT_SENT_TURNS: set[str] = set()
_TELEGRAM_LIVE_SENT_ASSISTANT_MSG_IDS: set[str] = set()
# Content backstop against the bridge forwarding the SAME final assistant
# message twice. The msg_id dedup above only collapses re-publications that
# reuse the msg_id; the CCI tmux-capture path (_run_manual_capture /
# _adopt_orphan_turn) re-stamps a FRESH msg_id when it races the live
# coordinator, so the same final text reaches the bus twice with two msg_ids
# and slips past msg_id dedup. This claims (conversation, final-text) pairs
# atomically with a short TTL so the second copy is suppressed regardless of
# msg_id. TTL-bounded so a genuinely-identical message resent much later is
# still delivered.
_TELEGRAM_SENT_ASSISTANT_CONTENT: Dict[str, float] = {}
_TELEGRAM_SENT_CONTENT_LOCK = threading.Lock()
_TELEGRAM_ASSISTANT_CONTENT_TTL = 120.0


class TelegramConversationBridgeTask(BaseTask):
    """Relay shared conversation events to Telegram chats in compact mode."""

    TYPE = "telegramConversationBridge"
    VERSION = "1.0.0"
    NAME = "Telegram Conversation Bridge"
    DESCRIPTION = "Forward conversation events to active Telegram subscribers"
    ICON = "telegram"
    TAGS = ["telegram", "agent", "events"]

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._registered = False
        # Pending thinking accumulator, keyed "{conversation}:thinking:{agent}".
        # Thinking is delivered as many small blocks/snapshots; Telegram can
        # only post discrete messages, so we buffer the burst here and flush a
        # SINGLE consolidated block on the next non-thinking event instead of
        # spamming every fragment plus a final duplicate.
        self._thinking_buf: Dict[str, str] = {}
        self._thinking_meta: Dict[str, Dict[str, Any]] = {}

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "service_id": {
                "type": "string", "required": True,
                "description": "Fallback TelegramBotService for users without a personal bot token.",
            },
        }

    def initialize(self):
        if self._registered:
            return
        from core.conversation_event_bus import ConversationEventBus
        ConversationEventBus.instance().add_listener(self._on_event)
        self._registered = True

    def cleanup(self):
        if not self._registered:
            return
        from core.conversation_event_bus import ConversationEventBus
        ConversationEventBus.instance().remove_listener(self._on_event)
        self._registered = False

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        return [flowfile]

    def _on_event(self, conversation_id: str, event_type: str, data: Any) -> None:
        # Turn end closes any open thinking burst. A burst is normally flushed
        # by the next tool_call/tool_result/new_message; the LAST burst of a
        # turn (… -> thinking_content -> done) has no such closer, so without
        # this it stays stranded in _thinking_buf and never reaches Telegram —
        # which is why the final reasoning of each turn was missing while
        # webchat (which renders thinking_content directly) showed everything.
        if event_type in {"done", "error_event"}:
            self._flush_all_pending_thinking(conversation_id)
            return
        if event_type not in {
                "new_message", "thinking",
                "thinking_delta", "thinking_content", "tool_call",
                "tool_result"}:
            return
        if not isinstance(data, dict):
            return
        source = data.get("source") if isinstance(data.get("source"), dict) else {}
        if ((data.get("channel") == "telegram" or source.get("channel") == "telegram")
                and event_type in {"new_message", "thinking", "thinking_delta", "thinking_content", "tool_call", "tool_result"}
                and data.get("role") != "user"):
            return
        # Thinking arrives as many small blocks/snapshots. Forwarding each one
        # floods Telegram with fragments ("bouts") plus a final duplicate of the
        # whole thing. Instead, accumulate the thinking and emit ONE
        # consolidated block when the burst ends — i.e. on the next tool call,
        # tool result, or message for this agent.
        if event_type in {"thinking", "thinking_delta", "thinking_content"}:
            raw = _extract_thinking_text(event_type, data)
            if raw:
                key = f"{conversation_id}:thinking:{self._agent_key(data)}"
                self._thinking_buf[key] = _merge_thinking(
                    self._thinking_buf.get(key, ""), raw)
                self._thinking_meta[key] = data
            return
        # A non-thinking event closes the current thinking burst for this agent:
        # flush the consolidated block before handling it. The agent key must be
        # derived the SAME way as when buffering — thinking_content/tool_call
        # carry `agent_name`, but the final `new_message` carries only `source`,
        # so keying on agent_name alone stranded the pre-answer thinking of
        # no-tool-call turns (buffered under :thinking:<agent>, flush looked up
        # :thinking:'').
        self._flush_pending_thinking(conversation_id, self._agent_key(data))

        if (event_type == "new_message" and data.get("role") == "assistant"
                and _telegram_assistant_msg_id_was_sent(conversation_id, data)):
            return
        if (event_type == "new_message" and data.get("role") == "user"
                and _is_telegram_origin_event(data)):
            return
        text = self._format_event(event_type, data, conversation_id=conversation_id)
        if not text and event_type != "tool_result":
            return
        subscribers = list(self._telegram_subscribers(conversation_id, data))
        if not subscribers:
            logger.info(
                "Telegram bridge skipped event for %s: no active Telegram subscriber",
                conversation_id,
            )
            return
        # Backstop against forwarding the SAME final assistant message twice:
        # the CCI tmux-capture re-publishes it with a fresh msg_id (racing the
        # live coordinator), slipping past the msg_id dedup above. Claim the
        # (conversation, final-text) pair now that a send is certain; a
        # concurrent or later duplicate within the TTL is suppressed.
        if event_type == "new_message" and data.get("role") == "assistant":
            if _telegram_assistant_content_already_sent(
                    conversation_id, data, time.time()):
                logger.info(
                    "Telegram bridge: suppressed duplicate final assistant "
                    "message for %s (content backstop)", conversation_id[:8])
                return
        for user_id, chat_id in subscribers:
            if text:
                sent_text = self._send(user_id, chat_id, text)
                if sent_text and event_type == "new_message" and data.get("role") == "assistant":
                    _remember_forwarded_telegram_live_assistant(conversation_id)
                    _remember_sent_telegram_assistant_msg_id(conversation_id, data)
                    self._send_tts_audio(user_id, chat_id, conversation_id, data)
            if event_type == "new_message":
                self._send_message_attachments(user_id, chat_id, data)
            if event_type == "tool_result":
                self._send_tool_media(user_id, chat_id, data)

    @staticmethod
    def _agent_key(data: Dict[str, Any]) -> str:
        """Agent identity used to key the thinking buffer.

        Consistent across event types: thinking_content/tool_call set
        `agent_name`, new_message sets only `source.name` — both must map to
        the same key or a burst buffered under one is never flushed by the
        other."""
        if data.get("agent_name"):
            return str(data["agent_name"])
        source = data.get("source") if isinstance(data.get("source"), dict) else {}
        return str(source.get("name") or "")

    def _flush_all_pending_thinking(self, conversation_id: str) -> None:
        """Flush every agent's pending thinking for this conversation (turn end).

        Multiple agents may have open bursts; flush each so no final reasoning
        is stranded when the turn closes with `done`/`error_event`."""
        prefix = f"{conversation_id}:thinking:"
        agent_keys = [k[len(prefix):] for k in list(self._thinking_buf.keys())
                      if k.startswith(prefix)]
        for agent_key in agent_keys:
            self._flush_pending_thinking(conversation_id, agent_key)

    def _flush_pending_thinking(self, conversation_id: str, agent_key: str) -> None:
        """Emit the accumulated thinking for one agent as a single consolidated
        Telegram message, then clear the buffer. No-op when nothing is pending."""
        key = f"{conversation_id}:thinking:{agent_key}"
        buf = self._thinking_buf.pop(key, "")
        meta = self._thinking_meta.pop(key, None) or {}
        if not buf.strip():
            return
        text = _telegram_thinking_message(meta, buf)
        if not text:
            return
        for user_id, chat_id in self._telegram_subscribers(conversation_id, meta):
            self._send(user_id, chat_id, text)

    def _format_event(self, event_type: str, data: Dict[str, Any],
                      conversation_id: str = "") -> str:
        if event_type == "new_message":
            role = data.get("role") or ""
            if role not in {"user", "assistant"}:
                return ""
            name = _telegram_agent_badge(data, role)
            content = str(data.get("content") or "").strip()
            attachments = data.get("attachments") if isinstance(data.get("attachments"), list) else []
            attachment_text = _format_attachment_summary(attachments)
            parts = [_telegram_render_message_text(part) for part in (content, attachment_text) if part]
            if not parts:
                return ""
            body = " ".join(parts)
            if role == "assistant" and "<pre><code>" not in body:
                body = _telegram_blockquote(body)
            return f"{name}\n{body}"
        if event_type == "error_event":
            return ""
        if event_type == "thinking":
            detail = str(data.get("detail") or "").strip()
            return _telegram_thinking_message(data, detail)
        if event_type == "thinking_delta":
            text = str(data.get("text") or data.get("content") or "").strip()
            return _telegram_thinking_message(data, text)
        if event_type == "thinking_content":
            text = str(data.get("text") or data.get("content") or "").strip()
            return _telegram_thinking_message(data, text)
        if event_type == "tool_call":
            agent = _telegram_agent_badge(data)
            tool = _telegram_tool_display_name(data)
            return f"{agent}\n{_telegram_blockquote(f'calling <code>{html.escape(str(tool))}</code>')}"
        if event_type == "tool_result":
            return ""
        return ""

    @staticmethod
    def _telegram_subscribers(conversation_id: str, data: Optional[Dict[str, Any]] = None):
        from core.identity_service import IdentityService
        ids = IdentityService.instance()
        yielded = set()
        all_links = ids.list_all()

        def _yield_linked_user(user_id: str):
            links = all_links.get(user_id) or {}
            chat_id = links.get("telegram") if isinstance(links, dict) else ""
            if not chat_id:
                return
            key = (user_id, chat_id)
            if key in yielded:
                return
            yielded.add(key)
            yield key

        for user_id, links in all_links.items():
            chat_id = links.get("telegram") if isinstance(links, dict) else ""
            if not chat_id:
                continue
            if ids.get_active_conv(user_id, "telegram") == conversation_id:
                yield from _yield_linked_user(user_id)

    def _send(self, user_id: str, chat_id: str, text: str) -> bool:
        try:
            from tasks.io.telegram_send import telegram_api_chat_id
            chat_id = telegram_api_chat_id(chat_id)
            if not chat_id:
                return False
            svc = self._active_bridge_service()
            if not svc:
                return False
            from core.identity_service import IdentityService
            bot_token = IdentityService.instance().get_bot_token(user_id, "telegram")
            if bot_token:
                from services.telegram_bot_service import TelegramBotPool
                TelegramBotPool.instance().send_message(bot_token, chat_id, text, parse_mode="HTML")
                return True
            svc.send_message(chat_id, text, parse_mode="HTML")
            return True
        except Exception as exc:
            logger.warning("Telegram bridge send failed for %s: %s", chat_id, exc)
            return False

    def _active_bridge_service(self):
        svc = self.get_service(self.config.get("service_id", ""))
        if not svc or not getattr(svc, "_initialized", False):
            return None
        return svc

    def _send_message_attachments(self, user_id: str, chat_id: str,
                                  data: Dict[str, Any]) -> None:
        attachments = data.get("attachments") if isinstance(data.get("attachments"), list) else []
        refs: List[str] = []
        _collect_attachment_refs(attachments, refs)
        content = data.get("content")
        if isinstance(content, list):
            _collect_attachment_refs(content, refs)
        elif isinstance(content, str):
            for ref in _extract_filestore_refs(content):
                if ref not in refs:
                    refs.append(ref)
        media_user_id = _filestore_user_id_for_event(data, user_id)
        for file_id in refs[:4]:
            try:
                name, raw, content_type = _load_filestore_media(file_id, media_user_id)
                self._send_media(user_id, chat_id, raw, name, content_type)
            except Exception as exc:
                logger.warning(
                    "Telegram bridge attachment send failed for %s/%s owner=%s: %s",
                    chat_id, file_id, media_user_id, exc)

    def _send_tool_media(self, user_id: str, chat_id: str, data: Dict[str, Any]) -> None:
        refs = _extract_filestore_refs(str(data.get("result") or data.get("content") or ""))
        for file_id in refs[:4]:
            try:
                name, raw, content_type = _load_filestore_media(file_id, user_id)
                self._send_media(user_id, chat_id, raw, name, content_type)
            except Exception as exc:
                logger.warning("Telegram bridge media send failed for %s/%s: %s", chat_id, file_id, exc)

    def _send_tts_audio(self, user_id: str, chat_id: str,
                        conversation_id: str, data: Dict[str, Any]) -> None:
        content = str(data.get("content") or "").strip()
        if not content:
            return
        flowfile = FlowFile(content=b"")
        _attach_telegram_tts_audio(
            flowfile, content, user_id, conversation_id,
            str(data.get("agent_name") or "assistant"))
        raw = flowfile.get_attribute("telegram.tts_audio_base64") or ""
        if not raw:
            return
        try:
            audio = base64.b64decode(raw)
        except Exception:
            logger.warning("Telegram bridge TTS audio decode failed", exc_info=True)
            return
        filename = flowfile.get_attribute("telegram.tts_filename") or "telegram_reply.mp3"
        content_type = flowfile.get_attribute("telegram.tts_content_type") or "audio/mpeg"
        self._send_media(user_id, chat_id, audio, filename, content_type)

    def _send_media(self, user_id: str, chat_id: str, raw: bytes,
                    filename: str, content_type: str) -> None:
        from tasks.io.telegram_send import telegram_api_chat_id
        chat_id = telegram_api_chat_id(chat_id)
        if not chat_id:
            return
        content_type = content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        from core.identity_service import IdentityService
        bot_token = IdentityService.instance().get_bot_token(user_id, "telegram")
        svc = self._active_bridge_service()
        if not svc:
            return
        sender = None
        if bot_token:
            from services.telegram_bot_service import TelegramBotPool
            sender = TelegramBotPool.instance()
            if content_type.startswith("image/") and hasattr(sender, "send_photo"):
                sender.send_photo(bot_token, chat_id, raw, filename=filename, content_type=content_type)
            elif content_type.startswith("video/") and hasattr(sender, "send_video"):
                sender.send_video(bot_token, chat_id, raw, filename=filename, content_type=content_type)
            elif content_type.startswith("audio/") and hasattr(sender, "send_audio"):
                sender.send_audio(bot_token, chat_id, raw, filename=filename, content_type=content_type)
            else:
                sender.send_document(bot_token, chat_id, raw, filename=filename)
            return
        if content_type.startswith("image/") and hasattr(svc, "send_photo"):
            svc.send_photo(chat_id, raw, filename=filename, content_type=content_type)
        elif content_type.startswith("video/") and hasattr(svc, "send_video"):
            svc.send_video(chat_id, raw, filename=filename, content_type=content_type)
        elif content_type.startswith("audio/") and hasattr(svc, "send_audio"):
            svc.send_audio(chat_id, raw, filename=filename, content_type=content_type)
        elif hasattr(svc, "send_document"):
            svc.send_document(chat_id, raw, filename=filename)


def _format_attachment_summary(attachments: List[Dict[str, Any]]) -> str:
    if not attachments:
        return ""
    image_count = 0
    file_count = 0
    for att in attachments:
        if not isinstance(att, dict):
            continue
        mime_type = str(att.get("mime_type") or att.get("content_type") or "")
        filename = str(att.get("filename") or att.get("name") or "")
        if mime_type.startswith("image/") or filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
            image_count += 1
        else:
            file_count += 1
    parts = []
    if image_count:
        parts.append(f"{image_count} image attachment{'s' if image_count != 1 else ''}")
    if file_count:
        parts.append(f"{file_count} file attachment{'s' if file_count != 1 else ''}")
    return "[attachments: " + ", ".join(parts) + "]" if parts else "[attachments]"


def _collect_attachment_refs(items: List[Any], refs: List[str]) -> None:
    for item in items:
        if not isinstance(item, dict):
            continue
        file_id = str(item.get("file_id") or "").strip()
        if file_id and file_id not in refs:
            refs.append(file_id)
            continue
        ref_text = " ".join(str(item.get(k) or "") for k in ("url", "href", "path"))
        for ref in _extract_filestore_refs(ref_text):
            if ref not in refs:
                refs.append(ref)


def _filestore_user_id_for_event(data: Dict[str, Any], fallback: str) -> str:
    source = data.get("source") if isinstance(data.get("source"), dict) else {}
    if str(source.get("type") or "") == "user":
        name = str(source.get("name") or "").strip()
        if name:
            return name
    return fallback


def _telegram_agent_badge(data: Dict[str, Any], fallback: str = "assistant") -> str:
    source = data.get("source") if isinstance(data.get("source"), dict) else {}
    name = str(source.get("name") or data.get("agent_name") or data.get("channel") or fallback or "assistant")
    service = str(source.get("llm_service") or data.get("llm_service") or "")
    color = _telegram_badge_color(name) if fallback == "assistant" or data.get("agent_name") or source.get("llm_service") else "⬜"
    name_html = html.escape(name)
    if service and name != service:
        return f"{color} <b>{name_html}</b> <code>{html.escape(service)}</code>"
    return f"{color} <b>{name_html}</b>"


def _extract_thinking_text(event_type: str, data: Dict[str, Any]) -> str:
    """Pull the raw thinking text out of a thinking/-delta/-content event."""
    if event_type == "thinking":
        return str(data.get("detail") or "").strip()
    return str(data.get("text") or data.get("content") or "").strip()


def _merge_thinking(old: str, new: str) -> str:
    """Accumulate consecutive thinking blocks into one consolidated text.

    Handles both segmented providers (disjoint blocks — append) and providers
    that re-send a growing snapshot (cumulative superset — replace), and drops
    exact/substring duplicates so the consolidated block has no repetition.
    """
    new = (new or "").strip()
    if not new:
        return old or ""
    old = old or ""
    if not old:
        return new
    if new in old:
        return old
    if old in new:
        return new
    return f"{old}\n\n{new}"


def _telegram_thinking_message(data: Dict[str, Any], text: str) -> str:
    text = str(text or "").strip()
    if not text:
        return ""
    source = data.get("source") if isinstance(data.get("source"), dict) else {}
    name = str(source.get("name") or data.get("agent_name") or "assistant")
    return f"💭 <i>{html.escape(name)} thinking</i>\n{_telegram_blockquote(html.escape(text))}"


def _telegram_blockquote(text: str) -> str:
    text = str(text or "").strip()
    if not text:
        return ""
    return f"<blockquote>{text}</blockquote>"


def _telegram_render_message_text(text: str) -> str:
    text = str(text or "")
    if "```" not in text:
        return html.escape(text)
    parts: List[str] = []
    pos = 0
    pattern = re.compile(r"```([^\n`]*)\n?(.*?)```", re.DOTALL)
    for match in pattern.finditer(text):
        parts.append(html.escape(text[pos:match.start()]))
        code = match.group(2)
        if code.startswith("\n"):
            code = code[1:]
        if code.endswith("\n"):
            code = code[:-1]
        parts.append(f"<pre><code>{html.escape(code)}</code></pre>")
        pos = match.end()
    parts.append(html.escape(text[pos:]))
    return "".join(parts)


def _telegram_badge_color(name: str) -> str:
    if (name or "").strip().lower() == "assistant":
        return "🟩"
    colors = ["🟦", "🟪", "🟧", "🟥", "🟨", "🟫"]
    idx = sum(ord(ch) for ch in (name or "agent")) % len(colors)
    return colors[idx]


def _telegram_tool_display_name(data: Dict[str, Any]) -> str:
    tool = str(data.get("tool") or data.get("tool_name") or data.get("name") or "tool")
    raw_tool = str(data.get("raw_tool") or data.get("raw_name") or "")
    if tool not in {"use_tool", "mcp_pawflow_use_tool", "mcp__pawflow__use_tool"}:
        return tool
    args = data.get("arguments") if isinstance(data.get("arguments"), dict) else {}
    inner = str(args.get("tool_name") or args.get("name") or args.get("tool") or "")
    if inner:
        return inner
    result = str(data.get("result") or data.get("content") or "")
    match = re.search(r"tool_name['\"]?\s*[:=]\s*['\"]([^'\"}\s,]+)", result)
    if match:
        return match.group(1)
    return raw_tool or tool


def _extract_filestore_refs(text: str) -> List[str]:
    if not text:
        return []
    refs: List[str] = []
    for match in re.finditer(r"fs://filestore/([A-Za-z0-9_-]+)(?:/[^\s)\]}>\"']+)?", text):
        fid = match.group(1)
        if fid and fid not in refs:
            refs.append(fid)
    for match in re.finditer(r"/files/([A-Za-z0-9_-]+)(?:/[^\s)\]}>\"']+)?", text):
        fid = match.group(1)
        if fid and fid not in refs:
            refs.append(fid)
    return refs


def _load_filestore_media(file_id: str, user_id: str):
    if not user_id:
        raise FileNotFoundError(file_id)
    from core.file_store import FileStore
    store = FileStore.instance()
    result = store.get(file_id, user_id=user_id)
    if result is None:
        raise FileNotFoundError(file_id)
    return result


def _remember_forwarded_telegram_live_assistant(conversation_id: str) -> None:
    if conversation_id:
        _TELEGRAM_LIVE_ASSISTANT_SENT_TURNS.add(conversation_id)


def _telegram_assistant_msg_key(conversation_id: str, data: Dict[str, Any]) -> str:
    msg_id = str(data.get("msg_id") or data.get("message_id") or "").strip()
    return f"{conversation_id}\x1f{msg_id}" if conversation_id and msg_id else ""


def _telegram_assistant_content_text(data: Dict[str, Any]) -> str:
    """Normalized plain text of an assistant message, for content dedup."""
    content = data.get("content")
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, dict):
                piece = block.get("text") or block.get("content")
                if isinstance(piece, str):
                    parts.append(piece)
            elif isinstance(block, str):
                parts.append(block)
        content = " ".join(parts)
    return re.sub(r"\s+", " ", str(content or "")).strip()


def _telegram_assistant_content_already_sent(conversation_id: str,
                                             data: Dict[str, Any],
                                             now: float) -> bool:
    """Atomically claim a (conversation, final-text) pair. Returns True when
    the same assistant text was already forwarded within the TTL.

    Race-safe: the live coordinator and the CCI tmux-capture run on separate
    threads and can publish the same final message concurrently, so the
    check-and-claim happens under one lock. Claims before the send (rather
    than after, like the msg_id dedup) precisely so a concurrent duplicate
    can't slip between check and record.
    """
    text = _telegram_assistant_content_text(data)
    if not conversation_id or not text:
        return False
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]  # nosec B303,B324 - dedup key, not security
    key = f"{conversation_id}\x1f{digest}"
    with _TELEGRAM_SENT_CONTENT_LOCK:
        for stale_key, ts in list(_TELEGRAM_SENT_ASSISTANT_CONTENT.items()):
            if now - ts > _TELEGRAM_ASSISTANT_CONTENT_TTL:
                _TELEGRAM_SENT_ASSISTANT_CONTENT.pop(stale_key, None)
        if key in _TELEGRAM_SENT_ASSISTANT_CONTENT:
            return True
        _TELEGRAM_SENT_ASSISTANT_CONTENT[key] = now
        return False


def _remember_sent_telegram_assistant_msg_id(conversation_id: str,
                                             data: Dict[str, Any]) -> None:
    key = _telegram_assistant_msg_key(conversation_id, data)
    if key:
        _TELEGRAM_LIVE_SENT_ASSISTANT_MSG_IDS.add(key)


def _telegram_assistant_msg_id_was_sent(conversation_id: str,
                                        data: Dict[str, Any]) -> bool:
    key = _telegram_assistant_msg_key(conversation_id, data)
    return bool(key and key in _TELEGRAM_LIVE_SENT_ASSISTANT_MSG_IDS)


def _telegram_live_assistant_was_forwarded(conversation_id: str,
                                           final_data: Optional[Dict[str, Any]] = None) -> bool:
    if not conversation_id:
        return False
    final_msg_id = ""
    if isinstance(final_data, dict):
        final_msg_id = str(final_data.get("msg_id") or "").strip()
        if not final_msg_id:
            all_ids = final_data.get("all_msg_ids")
            if isinstance(all_ids, list) and all_ids:
                final_msg_id = str(all_ids[-1] or "").strip()
    if final_msg_id:
        key = f"{conversation_id}\x1f{final_msg_id}"
        seen_final = key in _TELEGRAM_LIVE_SENT_ASSISTANT_MSG_IDS
        _TELEGRAM_LIVE_ASSISTANT_SENT_TURNS.discard(conversation_id)
        prefix = conversation_id + "\x1f"
        for sent_key in list(_TELEGRAM_LIVE_SENT_ASSISTANT_MSG_IDS):
            if sent_key.startswith(prefix):
                _TELEGRAM_LIVE_SENT_ASSISTANT_MSG_IDS.discard(sent_key)
        return seen_final
    seen = conversation_id in _TELEGRAM_LIVE_ASSISTANT_SENT_TURNS
    _TELEGRAM_LIVE_ASSISTANT_SENT_TURNS.discard(conversation_id)
    if seen:
        prefix = conversation_id + "\x1f"
        for key in list(_TELEGRAM_LIVE_SENT_ASSISTANT_MSG_IDS):
            if key.startswith(prefix):
                _TELEGRAM_LIVE_SENT_ASSISTANT_MSG_IDS.discard(key)
    return seen


def _is_telegram_origin_event(data: Dict[str, Any]) -> bool:
    source = data.get("source") if isinstance(data.get("source"), dict) else {}
    msg_id = str(data.get("msg_id") or data.get("turn_id") or data.get("request_msg_id") or "")
    return (
        data.get("channel") == "telegram"
        or source.get("channel") == "telegram"
        or msg_id.startswith("telegram:")
        or bool(data.get("telegram.chat_id") or data.get("telegram.user_id"))
    )


def _compact_live_text(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"
