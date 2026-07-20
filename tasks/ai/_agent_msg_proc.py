"""Token/cost estimation and message-payload processing for AgentUtilsMixin:
cpt calibration, image deflation, seen-tool-result clearing, token estimation,
and conversation-resource cleanup.

Split out of agent_utils.py as a leaf mixin so the file stays <= 800 lines.
Methods rely on AgentUtilsMixin host state/methods via the MRO.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from core.llm_client import LLMMessage

logger = logging.getLogger(__name__)


def _estimate_content_tokens(content: str, default_cpt: float = 3.5) -> int:
    """Estimate token count for a content string, aware of content type.

    JSON content (starts with { or [) uses 2 chars/token — it's denser due to
    brackets, quoted keys, and punctuation. Natural language uses the default
    ~3.5 chars/token ratio. Matches Claude Code's approach.
    """
    if not isinstance(content, str) or not content:
        return 0
    stripped = content.lstrip()
    if stripped.startswith('{') or stripped.startswith('['):
        return int(len(content) / 2.0)
    return int(len(content) / default_cpt)




class _AgentMsgProcMixin:
    """Token estimation + message-payload processing for AgentUtilsMixin."""

    def _calibrate_cpt(self, service_id: str, total_chars: int,
                       actual_tokens: int):
        """Update the calibrated chars-per-token ratio from actual API usage.

        Uses exponential moving average (alpha=0.3) so the ratio adapts
        quickly but doesn't swing wildly on a single outlier.
        """
        if not service_id or actual_tokens <= 0 or total_chars <= 0:
            return
        measured = total_chars / actual_tokens
        with self._calibrated_cpt_lock:
            old = self._calibrated_cpt.get(service_id)
            if old is None:
                self._calibrated_cpt[service_id] = measured
            else:
                alpha = 0.3
                self._calibrated_cpt[service_id] = old * (1 - alpha) + measured * alpha


    def _get_cpt(self, service_id: str, fallback: float = 0) -> float:
        """Get the best chars-per-token ratio for a service.

        Priority: calibrated (learned) → service config → default (2.0).
        """
        with self._calibrated_cpt_lock:
            cal = self._calibrated_cpt.get(service_id)
        if cal and cal > 0:
            return cal
        return fallback if fallback > 0 else 2.0


    @staticmethod
    def _strip_echo_prefix(text: str) -> str:
        """Strip identity prefix that the LLM may echo back (e.g. '[agent]: ...')."""
        if not text:
            return text
        stripped = text.lstrip()
        if stripped.startswith("["):
            import re
            return re.sub(r'^\[[^\]]+\]:\s*', '', stripped)
        return text


    @staticmethod
    def _deflate_image_messages(messages: List[LLMMessage], keep_last: bool = False,
                                 user_id: str = "", conversation_id: str = ""):
        """Replace multimodal image content with text-only references in-place.

        Called after the LLM has seen the images so base64 data doesn't
        persist in the conversation context.  The LLM can use view_image
        or show_file to re-request an image if needed.

        If keep_last=True, the last message with images is preserved
        (for pre-send compaction where the LLM hasn't seen them yet).
        """
        if keep_last:
            # Find the last message with images and skip it
            last_img_idx = -1
            for i, m in enumerate(messages):
                if isinstance(m.content, list) and any(
                    p.get("type") in ("image_url", "image_ref", "image")
                    for p in m.content
                ):
                    last_img_idx = i
        for idx, m in enumerate(messages):
            if not isinstance(m.content, list):
                continue
            has_images = any(
                p.get("type") in ("image_url", "image_ref", "image")
                for p in m.content
            )
            if not has_images:
                continue
            if keep_last and idx == last_img_idx:
                continue
            # Keep text parts, save images to FileStore and keep references
            text_parts = []
            img_refs = []
            for part in m.content:
                if part.get("type") == "text":
                    text_parts.append(part["text"])
                elif part.get("type") == "image_url":
                    url = (part.get("image_url", {}).get("url", "") or "")
                    if url.startswith("data:"):
                        # Save base64 data URI to FileStore
                        try:
                            import base64 as _b64d
                            import re as _re_d
                            _m = _re_d.match(r'data:([^;]+);base64,(.+)', url)
                            if _m:
                                mime, b64 = _m.group(1), _m.group(2)
                                ext = {"image/png": "png", "image/jpeg": "jpg",
                                       "image/webp": "webp", "image/gif": "gif"}.get(mime, "png")
                                from core.file_store import FileStore
                                import time as _t
                                fname = f"image_{int(_t.time())}_{len(img_refs)}.{ext}"
                                fid = FileStore.instance().store(
                                    fname, _b64d.b64decode(b64), mime,
                                    user_id=user_id, conversation_id=conversation_id)
                                img_refs.append(f"fs://filestore/{fid}/{fname}")
                        except Exception:
                            img_refs.append("(image)")
                    elif "/files/" in url:
                        img_refs.append(url)
                    elif url.startswith(("http://", "https://")):
                        # Keep full URL — small compared to base64, allows re-fetch
                        img_refs.append(url)
                    else:
                        img_refs.append("(image)")
                elif part.get("type") == "image_ref":
                    fid = part.get("file_id", "")
                    fname = part.get("filename", "image") or "image"
                    img_refs.append(
                        f"fs://filestore/{fid}/{fname}" if fid else "(image)")
                elif part.get("type") == "image":
                    source = part.get("source") if isinstance(part.get("source"), dict) else {}
                    data_b64 = source.get("data") or part.get("data") or ""
                    if data_b64:
                        try:
                            import base64 as _b64d
                            mime = (source.get("media_type") or part.get("mimeType")
                                    or part.get("mime_type") or "image/png")
                            ext = {"image/png": "png", "image/jpeg": "jpg",
                                   "image/webp": "webp", "image/gif": "gif"}.get(mime, "png")
                            from core.file_store import FileStore
                            import time as _t
                            fname = part.get("filename") or f"image_{int(_t.time())}_{len(img_refs)}.{ext}"
                            fid = FileStore.instance().store(
                                fname, _b64d.b64decode(data_b64), mime,
                                user_id=user_id, conversation_id=conversation_id)
                            img_refs.append(f"fs://filestore/{fid}/{fname}")
                        except Exception:
                            img_refs.append("(image)")
                    else:
                        img_refs.append("(image)")
            text = "\n".join(text_parts)
            if img_refs:
                refs_text = "\n".join(f"  - {ref}" for ref in img_refs)
                m.content = f"{text}\n[{len(img_refs)} image(s) — saved to FileStore:\n{refs_text}\n  Use show_file to view again]"
            else:
                m.content = f"{text}\n[images deflated]"

    # ── Tool result size management ──────────────────────────────────

    # TTL for tool result files in FileStore (seconds). Default 1h.
    _TOOL_RESULT_TTL = 3600
    # Threshold for clearing tool results (chars). Results over this get
    # saved to FileStore and replaced with a reference after the LLM has seen them.
    _TOOL_RESULT_CLEAR_THRESHOLD = 5000  # only store results > 5KB to FileStore


    @staticmethod
    def _detect_base64_blob(text: str) -> bool:
        """Check if text contains a large base64 blob (data URI or raw).

        Avoids false positives on minified code which also has long
        alphanumeric stretches but contains (){}[].:; characters.
        """
        if "data:" in text and ";base64," in text:
            return True
        # Raw base64: 1000+ chars of base64 alphabet WITHOUT code punctuation
        import re
        match = re.search(r'[A-Za-z0-9+/=]{1000,}', text)
        if not match:
            return False
        # Verify it's actual base64 (no code-like chars mixed in)
        blob = match.group(0)
        # Real base64 has very few + and / relative to alphanumerics
        # and NEVER has (){}[].;: inside it
        code_chars = sum(1 for c in blob if c in '(){}[].:;,!@#$%^&*<>?~`')
        return code_chars == 0


    def _clear_seen_tool_results(self, messages, keep_recent: int = 4,
                                  conversation_id: str = "",
                                  user_id: str = "",
                                  agent_name: str = ""):
        """Clear old tool results that the LLM has already seen.

        Called AFTER the LLM has responded. Saves large results to FileStore
        and replaces them with a short reference in the context.

        Only clears results NOT in the last `keep_recent` messages.
        The LLM can use read(path=url, source='filestore') to retrieve them if needed.
        """
        import re as _re_fs
        _FS_REF = _re_fs.compile(r'/files/[a-f0-9]{12}(?:/|$)')
        cleared = 0

        # Clear old tool results > threshold, skipping the last `keep_recent` messages.
        _cutoff = max(1, len(messages) - keep_recent) if keep_recent > 0 else len(messages)
        for i in range(1, _cutoff):
            m = messages[i]
            if m.role != "tool" or not isinstance(m.content, str):
                continue
            content = m.content
            content_len = len(content)

            # Skip small results
            if content_len <= self._TOOL_RESULT_CLEAR_THRESHOLD:
                continue
            # Has a FileStore ref but still has content → shrink to ref only
            _ref_match = _re_fs.search(r'(\[Result cleared[^\]]*\])', content)
            if _ref_match:
                m.content = _ref_match.group(1)
                continue

            # Strip outer <tool_output tool="..."> wrapper for storage.
            _inner = content
            if _inner.startswith("<tool_output tool="):
                _nl = _inner.find("\n")
                if _nl >= 0:
                    _inner = _inner[_nl + 1:]
                _close = _inner.rfind("</tool_output>")
                if _close >= 0:
                    _inner = _inner[:_close].rstrip()

            # Save to FileStore
            try:
                from core.file_store import FileStore
                store = FileStore.instance()
                fname = f"tool_result_{cleared}.txt"
                fid = store.store(
                    fname, _inner.encode("utf-8"),
                    conversation_id=conversation_id,
                    user_id=user_id,
                    ttl=self._TOOL_RESULT_TTL,
                    agent_name=agent_name,
                    category="tool_result",
                )
                # Keep a short summary so the LLM knows what happened
                _first_line = _inner.split("\n", 1)[0][:200]
                m.content = (
                    f"{_first_line}\n"
                    f"[Result cleared — {content_len:,} chars. "
                    f"Full output: read(path=\"{fid}\", source=\"filestore\")]"
                )
                cleared += 1
            except Exception:
                # Fallback: keep first line + truncate
                _first_line = content.split("\n", 1)[0][:200]
                m.content = f"{_first_line}\n[...{content_len - len(_first_line):,} chars cleared]"
                cleared += 1

        if cleared:
            logger.info(f"[clear_tool_results] Cleared {cleared} old tool result(s)")

    @staticmethod
    def _cleanup_tool_result_files(conversation_id: str = "",
                                    agent_name: str = ""):
        """Delete tool result files from FileStore after the agent's final response.

        Uses metadata filters (category + conversation_id + agent_name) —
        safe for multi-agent parallel execution.
        """
        try:
            from core.file_store import FileStore
            count = FileStore.instance().delete_by(
                category="tool_result",
                conversation_id=conversation_id,
                agent_name=agent_name,
            )
            if count:
                logger.info(f"[cleanup] Deleted {count} tool result file(s) "
                            f"for {agent_name or 'unknown'}@{conversation_id[:8]}")
        except Exception as e:
            logger.debug(f"[cleanup] Tool result file cleanup failed: {e}")

    @staticmethod
    def _estimate_tokens(messages: List[LLMMessage],
                         tool_defs: list = None,
                         chars_per_token: float = 0,
                         token_multiplier: float = 1.0) -> int:
        """Estimate token count for messages + tool definitions.

        Uses content-aware estimation: JSON content (starts with { or [)
        uses 2 chars/token (denser due to brackets, keys, punctuation),
        while natural language uses the default ~3.5 chars/token.

        *chars_per_token* controls the default ratio for natural language.
        Default (0) uses 3.5. The service config key ``chars_per_token``
        can override this per-LLM.

        *token_multiplier* scales the tiktoken (cl100k_base) count up to
        the real tokenizer of the target model — e.g. Opus 4.7 costs
        ~1.6x more tokens than cl100k for the same text. Compact thresh-
        old checks and the gauge both need REAL tokens, not raw.
        """
        # Precise counting via tiktoken — strip image data first
        try:
            from core.token_counter import count_messages_tokens
            from tasks.ai.context_usage_cache import _scrub_image_payloads
            _stripped = []
            for m in messages:
                c = m.content if hasattr(m, 'content') else str(m)
                if isinstance(c, list):
                    # Replace image parts with a small placeholder.
                    c = " ".join(
                        p.get("text", "") if p.get("type") == "text"
                        else "[image]" if p.get("type") in ("image_url", "image_ref", "image")
                        else p.get("text", "") if p.get("type") == "document"
                        else ""
                        for p in c
                    )
                elif isinstance(c, str):
                    c = _scrub_image_payloads(c)
                _stripped.append({"content": c})
            return count_messages_tokens(_stripped, multiplier=token_multiplier)
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        # Fallback to character estimation
        # Modern tokenizers average ~3.5 chars/token for natural language.
        # JSON is denser (brackets, keys, less natural language) — use 2 chars/token.
        cpt = chars_per_token if chars_per_token > 0 else 3.5
        total_tokens = 0
        for m in messages:
            total_tokens += int(12 / cpt)  # message overhead (role, separators)
            if isinstance(m.content, str):
                total_tokens += _estimate_content_tokens(m.content, cpt)
            elif isinstance(m.content, list):
                for part in m.content:
                    if part.get("type") == "text":
                        total_tokens += _estimate_content_tokens(
                            part.get("text", ""), cpt)
                    elif part.get("type") == "document":
                        total_tokens += _estimate_content_tokens(
                            part.get("text", ""), cpt)
                    elif part.get("type") == "image_url":
                        # Images are handled separately by the API (not counted as text tokens).
                        # Don't count them — they inflate the estimate and trigger unnecessary compaction.
                        total_tokens += 85  # ~85 tokens per image tile in OpenAI/Anthropic
            if m.tool_calls:
                for tc in m.tool_calls:
                    # Tool call arguments are JSON — use 2 chars/token
                    _tc_chars = len(tc.name) + len(json.dumps(tc.arguments))
                    total_tokens += int(_tc_chars / 2.0)
        # Tool definitions (JSON schemas) are sent with every request
        if tool_defs:
            for td in tool_defs:
                _td_chars = len(getattr(td, 'name', '') or '')
                _td_chars += len(getattr(td, 'description', '') or '')
                params = getattr(td, 'parameters', None)
                if params:
                    _td_chars += len(json.dumps(params) if isinstance(params, dict) else str(params))
                # Tool defs are JSON schemas — use 2 chars/token
                total_tokens += int(_td_chars / 2.0)
        if token_multiplier and token_multiplier != 1.0:
            total_tokens = int(total_tokens * token_multiplier)
        return total_tokens


    @staticmethod
    def _cleanup_conversation_resources(conversation_id: str):
        """Cascade-delete all resources tied to a conversation: flows, tools, secrets."""
        from core.tool_registry import FlowManagerHandler, StoreSecretHandler
        try:
            FlowManagerHandler.cleanup_conversation(conversation_id)
        except Exception as e:
            logger.warning(f"[cleanup] flow cleanup failed: {e}")
        try:
            StoreSecretHandler.cleanup_conversation(conversation_id)
        except Exception as e:
            logger.warning(f"[cleanup] secret cleanup failed: {e}")
        try:
            from core.conversation_store import ConversationStore
            from core.tool_loader import cleanup_conversation_tools
            uid = ConversationStore.instance()._cid_user.get(conversation_id, "")
            if uid:
                cleanup_conversation_tools(uid, conversation_id)
        except Exception as e:
            logger.warning(f"[cleanup] dynamic tool cleanup failed: {e}")
        # Stop and undeploy conversation-scoped flow instances
        try:
            from core.deployment_registry import DeploymentRegistry
            from core.executor_registry import ExecutorRegistry
            dr = DeploymentRegistry.get_instance()
            er = ExecutorRegistry.get_instance()
            for iid, inst in list(dr.list_all().items()):
                if getattr(inst, "conversation_id", None) == conversation_id:
                    ex = er.get(iid)
                    if ex and ex.is_running:
                        ex.stop()
                    er.unregister(iid)
                    dr.undeploy(iid)
                    logger.info(f"[cleanup] Stopped conv-scoped flow {iid}")
        except Exception as e:
            logger.warning(f"[cleanup] conv-scoped flow cleanup failed: {e}")


    @staticmethod
    def _cleanup_conversation_files(messages: List[Dict[str, Any]]):
        """Delete files referenced in conversation messages (on conv delete)."""
        import re
        from core.file_store import FileStore
        store = FileStore.instance()
        file_ids = set()
        # Scan for /files/{file_id}/ patterns in message content
        pattern = re.compile(r'/files/([a-f0-9]{12})(?:/|$)')
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                for match in pattern.finditer(content):
                    file_ids.add(match.group(1))
        for fid in file_ids:
            store.delete(fid)
        if file_ids:
            logger.info(f"[cleanup] deleted {len(file_ids)} files from conversation")

