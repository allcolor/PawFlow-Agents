"""LLM provider mixin -- Anthropic API."""

import json
import http.client
import logging
import ssl
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from core.cache_diagnostics import CacheBreakDetector

logger = logging.getLogger(__name__)


class LLMAnthropicMixin:
    """Anthropic provider methods: complete, stream, message building."""

    # Shared cache break detector (one per LLMClient instance via mixin)
    _cache_detector: Optional[CacheBreakDetector] = None

    def _get_cache_detector(self) -> CacheBreakDetector:
        """Lazily create and return the cache break detector."""
        if self._cache_detector is None:
            self._cache_detector = CacheBreakDetector()
        return self._cache_detector

    @staticmethod
    def _log_anthropic_cache_usage(tokens_in: int, cache_creation_tokens: int,
                                   cache_read_tokens: int) -> None:
        """Log Anthropic cache usage without deriving impossible negatives.

        Anthropic reports cache creation/read tokens separately from
        `input_tokens` on cached requests, so subtracting cache tokens from
        `input_tokens` can produce negative values. Treat `input_tokens` as
        the non-cached input portion reported by the provider.
        """
        _cache_total = cache_creation_tokens + cache_read_tokens
        if _cache_total > 0:
            _hit_pct = (cache_read_tokens / _cache_total * 100) if _cache_total else 0
            logger.info(
                "Anthropic KV cache: %d created, %d read (%.0f%% hit), %d input tokens",
                cache_creation_tokens, cache_read_tokens, _hit_pct, tokens_in)
        elif tokens_in > 0:
            logger.info("Anthropic KV cache: MISS — %d input tokens, 0 cached", tokens_in)

    def _stream_anthropic(self, messages, model, temperature, max_tokens, tools, callback, thinking_budget: int = 0, thinking_callback=None,
                           *, call_user_id: str = "", call_conversation_id: str = ""):
        """Anthropic streaming: reads SSE events from the API."""
        from core.llm_client import LLMClientError, LLMResponse, LLMToolCall
        from tasks.ai.agent_exceptions import AgentCancelled

        if getattr(self, "_abort", None) and self._abort.is_set():
            raise AgentCancelled()

        system_text, api_messages = self._build_anthropic_messages(
            messages,
            user_id=call_user_id,
            conversation_id=call_conversation_id)

        # Add cache_control breakpoints for KV cache optimization
        self._apply_anthropic_cache_control(api_messages)

        # Record pre-call state for cache break detection
        detector = self._get_cache_detector()
        tool_defs = [{"name": t.name, "description": t.description, "parameters": t.parameters} for t in tools] if tools else []
        detector.record_pre_call(system_text, tool_defs, model)

        body = {
            "model": model,
            "messages": api_messages,
            "max_tokens": max_tokens if max_tokens > 0 else 64000,
            "temperature": temperature,
            "stream": True,
        }
        if thinking_budget > 0:
            body["thinking"] = {"type": "enabled", "budget_tokens": thinking_budget}
            body["temperature"] = 1  # Required by Anthropic when thinking is enabled
        _cache_ttl = int(self._cfg("anthropic_cache_ttl", 0))
        _cc = {"type": "ephemeral"}
        if _cache_ttl > 0:
            _cc["ttl"] = _cache_ttl
        if system_text:
            body["system"] = [{"type": "text", "text": system_text, "cache_control": _cc}]
        if tools:
            _tool_list = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters}
                for t in tools
            ]
            if _tool_list:
                _tool_list[-1]["cache_control"] = _cc
            body["tools"] = _tool_list

        from core.llm_client import LLMClientError
        _base = self.base_url or "https://api.anthropic.com"
        parsed = urlparse(_base)
        host = parsed.hostname
        if not host:
            raise LLMClientError(
                f"Invalid base_url for anthropic provider: {_base!r} — "
                f"no hostname could be parsed. Check the llm_service config."
            )
        port = parsed.port
        full_path = (parsed.path.rstrip("/") + "/v1/messages").replace("//", "/")

        if parsed.scheme == "https":
            ctx = ssl.create_default_context()
            conn = http.client.HTTPSConnection(host, port, timeout=self.timeout, context=ctx)
        else:
            conn = http.client.HTTPConnection(host, port, timeout=self.timeout)

        try:
            self._active_http_conn = conn
            json_body = json.dumps(body).encode("utf-8")
            headers = {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
                "Content-Length": str(len(json_body)),
            }
            conn.request("POST", full_path, body=json_body, headers=headers)
            response = conn.getresponse()

            if response.status >= 400:
                error_body = response.read().decode("utf-8")
                raise LLMClientError(f"LLM API error {response.status}: {error_body[:500]}")

            content_parts: List[str] = []
            tool_calls: list = []
            current_tool: Optional[Dict] = None
            tool_input_str = ""
            finish_reason = ""
            resp_model = model
            tokens_in = 0
            tokens_out = 0
            cache_creation_tokens = 0
            cache_read_tokens = 0
            thinking_text = ""
            thinking_signature = ""
            current_block_type = None

            def _append_text_piece(text: str) -> None:
                nonlocal _text_block_buf
                if text:
                    content_parts.append(text)
                    _text_block_buf += text

            def _append_thinking_piece(text: str) -> None:
                nonlocal thinking_text, _thinking_block_buf
                if text:
                    thinking_text += text
                    _thinking_block_buf += text

            # Per-block buffers: callbacks fire ONCE per block (CC parity).
            # CC's SDK delivers whole blocks, so its `token`/`thinking_content`
            # SSE events arrive block-granular. Anthropic streams per-delta —
            # we accumulate here and invoke the callbacks on content_block_stop
            # so the UI sees the same cadence on every provider.
            _text_block_buf = ""
            _thinking_block_buf = ""

            buffer = ""
            while True:
                if getattr(self, "_abort", None) and self._abort.is_set():
                    raise AgentCancelled()
                chunk = response.read(4096)
                if getattr(self, "_abort", None) and self._abort.is_set():
                    raise AgentCancelled()
                if not chunk:
                    break
                buffer += chunk.decode("utf-8", errors="replace")

                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line or line.startswith(":"):
                        continue
                    if line.startswith("data: "):
                        if getattr(self, "_abort", None) and self._abort.is_set():
                            raise AgentCancelled()
                        try:
                            data = json.loads(line[6:])
                            evt_type = data.get("type", "")

                            if evt_type == "message_start":
                                msg = data.get("message", {})
                                resp_model = msg.get("model", model)
                                usage = msg.get("usage", {})
                                tokens_in = usage.get("input_tokens", 0)
                                cache_creation_tokens = usage.get("cache_creation_input_tokens", 0) or 0
                                cache_read_tokens = usage.get("cache_read_input_tokens", 0) or 0

                            elif evt_type == "content_block_start":
                                block = data.get("content_block", {})
                                block_type = block.get("type")
                                if block_type == "thinking":
                                    current_block_type = "thinking"
                                    _append_thinking_piece(
                                        block.get("thinking", "")
                                        or block.get("text", "")
                                        or block.get("reasoning_content", ""))
                                elif block_type == "tool_use":
                                    current_block_type = "tool_use"
                                    current_tool = {
                                        "id": block.get("id", ""),
                                        "name": block.get("name", ""),
                                    }
                                    tool_input = block.get("input")
                                    tool_input_str = (
                                        json.dumps(tool_input, ensure_ascii=False)
                                        if isinstance(tool_input, dict) and tool_input else "")
                                else:
                                    current_block_type = block_type
                                    if block_type == "text":
                                        _append_text_piece(block.get("text", ""))

                            elif evt_type == "content_block_delta":
                                delta = data.get("delta", {})
                                delta_type = delta.get("type", "")
                                if delta_type == "signature_delta":
                                    thinking_signature = delta.get("signature", "") or thinking_signature
                                elif delta_type == "input_json_delta":
                                    tool_input_str += delta.get("partial_json", "")
                                else:
                                    t_text = (
                                        delta.get("thinking", "")
                                        or delta.get("reasoning_content", "")
                                        or delta.get("reasoning", ""))
                                    if delta_type == "thinking_delta" or (
                                            current_block_type == "thinking" and t_text):
                                        _append_thinking_piece(t_text)
                                    else:
                                        _append_text_piece(delta.get("text", ""))

                            elif evt_type == "content_block_stop":
                                if current_tool:
                                    from core.tool_json import parse_tool_arguments
                                    args = parse_tool_arguments(
                                        tool_input_str,
                                        tool_name=current_tool["name"],
                                        provider="anthropic",
                                        log=logger,
                                    )
                                    tool_calls.append(LLMToolCall(
                                        id=current_tool["id"],
                                        name=current_tool["name"],
                                        arguments=args,
                                    ))
                                    current_tool = None
                                    tool_input_str = ""
                                # Fire block-level callbacks ONCE per closed block.
                                if _text_block_buf and callback:
                                    callback(_text_block_buf)
                                    _text_block_buf = ""
                                if _thinking_block_buf and thinking_callback:
                                    thinking_callback(_thinking_block_buf)
                                    _thinking_block_buf = ""
                                current_block_type = None

                            elif evt_type == "message_delta":
                                delta = data.get("delta", {})
                                finish_reason = delta.get("stop_reason", finish_reason)
                                usage = data.get("usage", {})
                                tokens_out = usage.get("output_tokens", tokens_out)
                                if usage.get("cache_creation_input_tokens"):
                                    cache_creation_tokens = usage["cache_creation_input_tokens"]
                                if usage.get("cache_read_input_tokens"):
                                    cache_read_tokens = usage["cache_read_input_tokens"]

                            elif evt_type == "message_stop":
                                pass

                        except (json.JSONDecodeError, KeyError):
                            pass

            self._log_anthropic_cache_usage(
                tokens_in, cache_creation_tokens, cache_read_tokens)

            # Check for cache break
            _diag = detector.check_post_call(cache_read_tokens, cache_creation_tokens)
            if _diag:
                logger.warning("Anthropic cache diagnostics: %s", _diag)

            return LLMResponse(
                content="".join(content_parts),
                model=resp_model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                total_tokens=tokens_in + tokens_out,
                finish_reason=finish_reason,
                tool_calls=tool_calls,
                cache_creation_tokens=cache_creation_tokens,
                cache_read_tokens=cache_read_tokens,
                thinking=thinking_text,
                thinking_signature=thinking_signature,
            )
        finally:
            self._active_http_conn = None
            conn.close()

    def _build_anthropic_messages(self, messages, *,
                                   user_id: str, conversation_id: str) -> tuple:
        """Convert LLMMessage list to Anthropic API format.

        Returns (system_text, api_messages).

        Messages are regrouped first so the split (assistant text / assistant
        tool_calls) pair emitted by agent_core.persist is fused into the
        single assistant message Anthropic expects.

        user_id + conversation_id are required call-scoped identity
        used to resolve image_ref attachments. Passed through from
        complete / complete_stream rather than read from self.* —
        same rationale as the CC call_* refactor.
        """
        from core.llm_message_regroup import regroup_split_assistant_messages
        messages = regroup_split_assistant_messages(messages)
        system_text = ""
        api_messages: List[Dict[str, Any]] = []

        def _image_link_block(part: dict) -> Optional[Dict[str, Any]]:
            fid = str(part.get("file_id") or "").strip()
            if fid:
                name = str(part.get("filename") or "image").strip() or "image"
                return {"type": "text", "text": f"Attached image: fs://filestore/{fid}/{name}"}
            if part.get("type") == "image_url":
                url = part.get("image_url", {}).get("url", "")
                if url and not url.startswith("data:"):
                    return {"type": "text", "text": f"Attached image: {url}"}
            return None

        def _tool_result_content(m) -> Any:
            tool_content: Any = m.content
            if isinstance(m.content, list):
                # Multimodal tool result: build content blocks (text + images)
                blocks: List[Dict[str, Any]] = []
                for part in m.content:
                    if part.get("type") == "text":
                        blocks.append({"type": "text", "text": part["text"]})
                    elif part.get("type") == "image_url":
                        if not self.supports_vision:
                            link = _image_link_block(part)
                            if link:
                                blocks.append(link)
                            continue
                        url = part.get("image_url", {}).get("url", "")
                        if url.startswith("data:"):
                            header, _, b64data = url.partition(",")
                            media_type = header.split(":")[1].split(";")[0] if ":" in header else "image/png"
                            blocks.append({
                                "type": "image",
                                "source": {"type": "base64", "media_type": media_type, "data": b64data},
                            })
                        elif url:
                            blocks.append({"type": "image", "source": {"type": "url", "url": url}})
                    elif part.get("type") == "image_ref":
                        if not self.supports_vision:
                            link = _image_link_block(part)
                            if link:
                                blocks.append(link)
                            continue
                        from core.file_store import FileStore
                        import base64 as _b64
                        _fid = part.get("file_id", "")
                        if not _fid:
                            raise ValueError(
                                "image_ref block missing file_id — producer bug")
                        _fname, _data, _ct = FileStore.instance().get_required(
                            _fid,
                            user_id=user_id,
                            conversation_id=conversation_id)
                        logger.info(
                            "Loaded tool-result image from FileStore for Anthropic vision: %s (%d bytes)",
                            _fid, len(_data),
                        )
                        _data_b64 = _b64.b64encode(_data).decode("ascii")
                        mime = part.get("mime_type", _ct) or "image/png"
                        blocks.append({
                            "type": "text",
                            "text": f"Attached image: fs://filestore/{_fid}/{_fname}",
                        })
                        blocks.append({
                            "type": "image",
                            "source": {"type": "base64", "media_type": mime, "data": _data_b64},
                        })
                tool_content = blocks if blocks else m.text_content
            return tool_content

        last_user_idx = -1
        for idx, msg in enumerate(messages):
            if msg.role == "user":
                last_user_idx = idx

        i = 0
        while i < len(messages):
            m = messages[i]
            if m.role == "system":
                system_text = m.text_content if isinstance(m.content, list) else m.content
            elif m.role == "tool":
                # Anthropic requires all results for one assistant tool_use
                # turn to be in the immediately-following user message. PawFlow
                # stores one role=tool message per result, so group adjacent
                # tool messages when building the provider payload.
                content_blocks: List[Dict[str, Any]] = []
                while i < len(messages) and messages[i].role == "tool":
                    tm = messages[i]
                    content_blocks.append({
                        "type": "tool_result",
                        "tool_use_id": tm.tool_call_id or "",
                        "content": _tool_result_content(tm),
                    })
                    i += 1
                api_messages.append({"role": "user", "content": content_blocks})
                continue
            elif m.role == "assistant" and m.tool_calls:
                # Assistant message with tool_use content blocks
                content_blocks: List[Dict[str, Any]] = []
                if getattr(m, "thinking", ""):
                    thinking_block = {"type": "thinking", "thinking": m.thinking}
                    signature = getattr(m, "thinking_signature", "")
                    if signature:
                        thinking_block["signature"] = signature
                    content_blocks.append(thinking_block)
                text = m.text_content if isinstance(m.content, list) else m.content
                if text:
                    content_blocks.append({"type": "text", "text": text})
                for tc in m.tool_calls:
                    content_blocks.append({
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.arguments,
                    })
                api_messages.append({"role": "assistant", "content": content_blocks})
            elif isinstance(m.content, list):
                # Multi-part content (text + images)
                # Convert from OpenAI format to Anthropic format
                content_blocks = []
                for part in m.content:
                    if part.get("type") == "text":
                        content_blocks.append({"type": "text", "text": part["text"]})
                    elif part.get("type") == "image_url":
                        if not (self.supports_vision and i == last_user_idx):
                            link = _image_link_block(part)
                            if link:
                                content_blocks.append(link)
                            continue
                        url = part.get("image_url", {}).get("url", "")
                        if url.startswith("data:"):
                            # Parse data URI: data:image/png;base64,XXXX
                            header, _, b64data = url.partition(",")
                            media_type = header.split(":")[1].split(";")[0] if ":" in header else "image/png"
                            content_blocks.append({
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": b64data,
                                },
                            })
                        else:
                            # URL-based image
                            content_blocks.append({
                                "type": "image",
                                "source": {"type": "url", "url": url},
                            })
                    elif part.get("type") == "image_ref":
                        if not (self.supports_vision and i == last_user_idx):
                            link = _image_link_block(part)
                            if link:
                                content_blocks.append(link)
                            continue
                        from core.file_store import FileStore
                        import base64 as _b64
                        _fid = part.get("file_id", "")
                        if not _fid:
                            raise ValueError(
                                "image_ref block missing file_id — producer bug")
                        _fname, _data, _ct = FileStore.instance().get_required(
                            _fid,
                            user_id=user_id,
                            conversation_id=conversation_id)
                        logger.info(
                            "Loaded image from FileStore for Anthropic vision: %s (%d bytes)",
                            _fid, len(_data),
                        )
                        _data_b64 = _b64.b64encode(_data).decode("ascii")
                        mime = part.get("mime_type", _ct) or "image/png"
                        content_blocks.append({
                            "type": "text",
                            "text": f"Attached image: fs://filestore/{_fid}/{_fname}",
                        })
                        content_blocks.append({
                            "type": "image",
                            "source": {"type": "base64", "media_type": mime, "data": _data_b64},
                        })
                    elif part.get("type") == "file_ref":
                        content_blocks.append({"type": "text", "text": f"[file: {part.get('filename', '?')}]"})
                    elif part.get("type") == "document":
                        # Document content -- inject as text block
                        content_blocks.append({
                            "type": "text",
                            "text": f"[Document: {part.get('filename', 'file')}]\n{part.get('text', '')}",
                        })
                api_messages.append({"role": m.role, "content": content_blocks})
            else:
                # Assistant message with thinking but no tool_calls
                if m.role == "assistant" and getattr(m, "thinking", ""):
                    _thinking_block = {"type": "thinking", "thinking": m.thinking}
                    _signature = getattr(m, "thinking_signature", "")
                    if _signature:
                        _thinking_block["signature"] = _signature
                    _blocks = [_thinking_block]
                    if m.content:
                        _blocks.append({"type": "text", "text": m.content if isinstance(m.content, str) else m.text_content})
                    api_messages.append({"role": "assistant", "content": _blocks})
                else:
                    api_messages.append({"role": m.role, "content": m.content or ""})
            i += 1
        image_blocks_sent = self._count_anthropic_image_blocks(api_messages)
        if image_blocks_sent:
            logger.info(
                "Anthropic payload includes image blocks: count=%d provider=anthropic",
                image_blocks_sent,
            )
        return system_text, api_messages

    @staticmethod
    def _count_anthropic_image_blocks(api_messages: List[Dict[str, Any]]) -> int:
        """Count Anthropic image blocks without inspecting their base64 payload."""
        count = 0
        stack: List[Any] = [api_messages]
        while stack:
            value = stack.pop()
            if isinstance(value, list):
                stack.extend(value)
            elif isinstance(value, dict):
                if value.get("type") == "image":
                    count += 1
                stack.extend(value.values())
        return count

    def _apply_anthropic_cache_control(self, api_messages: List[Dict[str, Any]]) -> None:
        """Add cache_control breakpoints to maximize KV cache hits.

        Anthropic caches the KV computation for all tokens up to a
        cache_control breakpoint. On subsequent requests with the same
        prefix, cached tokens are 10x cheaper and faster.

        Strategy (up to 2 breakpoints in messages, 4 total with system+tools):
        - Breakpoint A: the message just before the last user message
          ("turn boundary") — caches the entire conversation history.
        - Breakpoint B: for longer conversations (>10 messages), a second
          breakpoint deeper in the history for partial cache hits even
          after compaction changes recent messages.
        """
        if not api_messages:
            return

        _cache_ttl = int(self._cfg("anthropic_cache_ttl", 0))
        _cc: Dict[str, Any] = {"type": "ephemeral"}
        if _cache_ttl > 0:
            _cc["ttl"] = _cache_ttl

        def _set_cache(msg: Dict[str, Any]) -> None:
            content = msg["content"]
            if isinstance(content, str):
                msg["content"] = [
                    {"type": "text", "text": content, "cache_control": _cc}
                ]
            elif isinstance(content, list) and content:
                content[-1]["cache_control"] = _cc

        # Find the last user message index (the new turn being sent)
        last_user_idx = -1
        for i in range(len(api_messages) - 1, -1, -1):
            if api_messages[i].get("role") == "user":
                last_user_idx = i
                break

        # Breakpoint A: message just before the last user message
        # This caches the entire prefix (all history up to the current turn)
        if last_user_idx > 0:
            _set_cache(api_messages[last_user_idx - 1])
        elif last_user_idx == 0:
            # Only one user message — cache it (same as before)
            _set_cache(api_messages[0])
            return

        # Breakpoint B: deeper in history for partial cache survival
        # Place at ~40% of the conversation (rounded to a message boundary)
        if len(api_messages) > 10 and last_user_idx > 4:
            deep_idx = max(1, last_user_idx * 2 // 5)
            # Don't place on the same message as breakpoint A
            if deep_idx < last_user_idx - 1:
                _set_cache(api_messages[deep_idx])

    def _complete_anthropic(self, messages, model, temperature, max_tokens, tools=None, thinking_budget: int = 0,
                             *, call_user_id: str = "", call_conversation_id: str = ""):
        """Send a non-streaming completion to the Anthropic API."""
        from core.llm_client import LLMResponse, LLMToolCall

        system_text, api_messages = self._build_anthropic_messages(
            messages,
            user_id=call_user_id,
            conversation_id=call_conversation_id)

        # Add cache_control breakpoints for KV cache optimization
        self._apply_anthropic_cache_control(api_messages)

        # Record pre-call state for cache break detection
        detector = self._get_cache_detector()
        tool_defs = [{"name": t.name, "description": t.description, "parameters": t.parameters} for t in tools] if tools else []
        detector.record_pre_call(system_text, tool_defs, model)

        body: Dict[str, Any] = {"model": model, "messages": api_messages, "max_tokens": max_tokens if max_tokens > 0 else 64000, "temperature": temperature}
        if thinking_budget > 0:
            body["thinking"] = {"type": "enabled", "budget_tokens": thinking_budget}
            body["temperature"] = 1  # Required by Anthropic when thinking is enabled
        _cache_ttl = int(self._cfg("anthropic_cache_ttl", 0))
        _cc = {"type": "ephemeral"}
        if _cache_ttl > 0:
            _cc["ttl"] = _cache_ttl
        if system_text:
            body["system"] = [{"type": "text", "text": system_text, "cache_control": _cc}]
        if tools:
            _tool_list = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.parameters,
                }
                for t in tools
            ]
            if _tool_list:
                _tool_list[-1]["cache_control"] = _cc
            body["tools"] = _tool_list

        data = self._http_post(
            "/v1/messages",
            body,
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
        )
        content_blocks = data.get("content", [])
        text = "".join(
            b.get("text", "")
            for b in content_blocks
            if isinstance(b, dict) and b.get("type") == "text")

        # Parse thinking blocks. Anthropic-compatible providers sometimes use
        # text/reasoning_content instead of Anthropic's `thinking` field.
        thinking_text = ""
        thinking_signature = ""
        for block in content_blocks:
            if isinstance(block, dict) and block.get("type") == "thinking":
                thinking_text += (
                    block.get("thinking", "")
                    or block.get("text", "")
                    or block.get("reasoning_content", ""))
                thinking_signature = block.get("signature", "") or thinking_signature

        # Parse tool_use blocks
        tool_calls = []
        for block in content_blocks:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                from core.tool_json import parse_tool_arguments
                tool_calls.append(LLMToolCall(
                    id=block.get("id", ""),
                    name=block.get("name", ""),
                    arguments=parse_tool_arguments(
                        block.get("input", {}),
                        tool_name=block.get("name", ""),
                        provider="anthropic",
                        log=logger,
                    ),
                ))

        usage = data.get("usage", {})
        tokens_in = usage.get("input_tokens", 0)
        cache_creation_tokens = usage.get("cache_creation_input_tokens", 0) or 0
        cache_read_tokens = usage.get("cache_read_input_tokens", 0) or 0
        self._log_anthropic_cache_usage(
            tokens_in, cache_creation_tokens, cache_read_tokens)

        # Check for cache break
        _diag = detector.check_post_call(cache_read_tokens, cache_creation_tokens)
        if _diag:
            logger.warning("Anthropic cache diagnostics: %s", _diag)

        tokens_out = usage.get("output_tokens", 0)
        return LLMResponse(
            content=text,
            model=data.get("model", model),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            total_tokens=tokens_in + tokens_out,
            finish_reason=data.get("stop_reason", ""),
            tool_calls=tool_calls,
            raw=data,
            cache_creation_tokens=cache_creation_tokens,
            cache_read_tokens=cache_read_tokens,
            thinking=thinking_text,
            thinking_signature=thinking_signature,
        )
