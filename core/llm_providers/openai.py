"""LLM provider mixin -- OpenAI-compatible API."""

import json
import http.client
import logging
import ssl
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class LLMOpenaiMixin:
    """OpenAI provider methods: complete, stream, message building."""

    def _stream_openai(self, messages, model, temperature, max_tokens, tools, callback,
                        thinking_callback=None, *,
                        call_user_id: str = "",
                        call_conversation_id: str = ""):
        """OpenAI streaming: reads SSE chunks from the API."""
        from core.llm_client import LLMClientError, LLMResponse, LLMToolCall

        body = {
            "model": model,
            "messages": self._build_openai_messages(
                messages,
                user_id=call_user_id,
                conversation_id=call_conversation_id),
            "stream": True,
        }
        if temperature is not None:
            body["temperature"] = temperature
        # reasoning_effort for reasoning models (gpt-5*, o-series)
        _re = self.reasoning_effort or None
        if _re:
            body["reasoning_effort"] = _re
        if max_tokens > 0:
            tokens_key = self._openai_tokens_key(model, self.base_url)
            body[tokens_key] = max_tokens
        if tools:
            body["tools"] = [
                {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
                for t in tools
            ]
        # OpenAI-specific cache params (ignored by non-OpenAI servers)
        _pck = self.prompt_cache_key or None
        if _pck:
            body["prompt_cache_key"] = _pck
        _pcr = self.prompt_cache_retention or None
        if _pcr:
            body["prompt_cache_retention"] = _pcr
        # Request streaming usage stats (OpenAI official API only —
        # local servers may not support stream_options)
        if not self.base_url or "api.openai.com" in self.base_url:
            body["stream_options"] = {"include_usage": True}

        parsed = urlparse(self.base_url)
        host = parsed.hostname
        port = parsed.port
        full_path = (parsed.path.rstrip("/") + "/v1/chat/completions").replace("//", "/")

        if parsed.scheme == "https":
            ctx = ssl.create_default_context()
            conn = http.client.HTTPSConnection(host, port, timeout=self.timeout, context=ctx)
        else:
            conn = http.client.HTTPConnection(host, port, timeout=self.timeout)

        try:
            json_body = json.dumps(body).encode("utf-8")
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Content-Length": str(len(json_body)),
            }
            conn.request("POST", full_path, body=json_body, headers=headers)
            response = conn.getresponse()

            if response.status >= 400:
                error_body = response.read().decode("utf-8")
                raise LLMClientError(f"LLM API error {response.status}: {error_body[:500]}")

            # Parse SSE stream
            content_parts: List[str] = []
            reasoning_parts: List[str] = []
            tool_calls_map: Dict[int, Dict] = {}  # index -> {id, name, arguments_str}
            finish_reason = ""
            resp_model = model
            usage_data: Dict[str, Any] = {}

            buffer = ""
            while True:
                chunk = response.read(4096)
                if not chunk:
                    break
                buffer += chunk.decode("utf-8", errors="replace")

                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line or line.startswith(":"):
                        continue
                    if line == "data: [DONE]":
                        break
                    if line.startswith("data: "):
                        try:
                            data = json.loads(line[6:])

                            # Final usage chunk (stream_options.include_usage)
                            if data.get("usage"):
                                usage_data = data["usage"]

                            choices = data.get("choices", [])
                            if not choices:
                                continue
                            choice0 = choices[0]
                            delta = choice0.get("delta", {})
                            fr = choice0.get("finish_reason")
                            if fr:
                                finish_reason = fr
                            if data.get("model"):
                                resp_model = data["model"]

                            # Reasoning content (o1/o3/o4-mini models)
                            reasoning = delta.get("reasoning_content", "")
                            if reasoning:
                                reasoning_parts.append(reasoning)
                                # Buffered — one callback per block at end of
                                # stream for CC parity (CC's SDK fires
                                # thinking_content per whole block).

                            # Text content
                            text = delta.get("content", "")
                            if text:
                                content_parts.append(text)
                                # Buffered — one callback per block at end of
                                # stream for CC parity.

                            # Tool calls (streamed incrementally)
                            for tc_delta in delta.get("tool_calls", []):
                                idx = tc_delta.get("index", 0)
                                if idx not in tool_calls_map:
                                    tool_calls_map[idx] = {
                                        "id": tc_delta.get("id", ""),
                                        "name": tc_delta.get("function", {}).get("name", ""),
                                        "arguments_str": "",
                                    }
                                tc = tool_calls_map[idx]
                                if tc_delta.get("id"):
                                    tc["id"] = tc_delta["id"]
                                if tc_delta.get("function", {}).get("name"):
                                    tc["name"] = tc_delta["function"]["name"]
                                tc["arguments_str"] += tc_delta.get("function", {}).get("arguments", "")

                        except (json.JSONDecodeError, IndexError, KeyError):
                            pass

            # Build tool calls
            tool_calls = []
            for idx in sorted(tool_calls_map.keys()):
                tc = tool_calls_map[idx]
                try:
                    args = json.loads(tc["arguments_str"]) if tc["arguments_str"] else {}
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append(LLMToolCall(id=tc["id"], name=tc["name"], arguments=args))

            content = "".join(content_parts)
            thinking = "".join(reasoning_parts)

            # Block-level callbacks (CC parity): fire ONCE for the whole text
            # block and ONCE for the whole reasoning block. OpenAI's SSE
            # stream has no content_block_stop marker, so the boundary is
            # end-of-stream. If text and tool_calls both appear, the text
            # callback fires here and tool_calls surface via the returned
            # LLMResponse — same ordering the UI sees from CC.
            if thinking and thinking_callback:
                thinking_callback(thinking)
            if content and callback:
                callback(content)

            # Use real usage from API if available, else estimate
            tokens_in = usage_data.get("prompt_tokens", 0)
            tokens_out = usage_data.get("completion_tokens", 0)
            total_tokens = usage_data.get("total_tokens", 0)
            if not tokens_in:
                tokens_in = sum(len(m.content) if isinstance(m.content, str) else
                             sum(len(str(p)) for p in m.content) if isinstance(m.content, list) else 0
                             for m in messages) // 4
            if not tokens_out:
                tokens_out = len(content) // 4

            # Cache logging (OpenAI returns cached_tokens inside the total
            # prompt_tokens count). Split billable miss tokens from cache-hit
            # tokens so cost tracking does not charge both rates for hits.
            _ptd = usage_data.get("prompt_tokens_details") or {}
            cached_tokens = _ptd.get("cached_tokens", 0) or 0
            prompt_tokens_total = tokens_in
            tokens_in = max(0, prompt_tokens_total - cached_tokens)
            if cached_tokens > 0:
                _hit_pct = (cached_tokens / prompt_tokens_total * 100) if prompt_tokens_total else 0
                logger.info("OpenAI prompt cache: %d cached of %d prompt tokens (%.0f%% hit)",
                            cached_tokens, prompt_tokens_total, _hit_pct)
            elif prompt_tokens_total > 1024:
                logger.info("OpenAI prompt cache: MISS — %d prompt tokens, 0 cached", prompt_tokens_total)

            return LLMResponse(
                content=content,
                model=resp_model,
                finish_reason=finish_reason,
                tool_calls=tool_calls,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                total_tokens=total_tokens,
                thinking=thinking,
                cache_read_tokens=cached_tokens,
            )
        finally:
            conn.close()

    def _resolve_image_ref(self, block: dict, *,
                           user_id: str, conversation_id: str) -> dict:
        """Resolve an image_ref block to an image_url block by loading from FileStore.

        user_id + conversation_id are REQUIRED kwargs (per-call, never
        read from self.*). Concurrent calls would otherwise race on
        shared client state — see the call_* refactor in
        LLMClient.complete / complete_stream.
        """
        from core.file_store import FileStore
        import base64 as _b64
        _fid = block.get("file_id", "")
        if not _fid:
            raise ValueError("image_ref block missing file_id — producer bug")
        _fname, _data, _ct = FileStore.instance().get_required(
            _fid, user_id=user_id, conversation_id=conversation_id)
        _data_b64 = _b64.b64encode(_data).decode("ascii")
        mime = block.get("mime_type", _ct) or "image/png"
        return {
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{_data_b64}"},
        }

    def _build_openai_messages(self, messages, *,
                                user_id: str, conversation_id: str) -> List[Dict[str, Any]]:
        """Convert LLMMessage list to OpenAI API message format.

        Messages are regrouped first so the split (assistant text / assistant
        tool_calls) pair emitted by agent_core.persist is fused into the
        single assistant message OpenAI expects (content + tool_calls).

        user_id + conversation_id are required call-scoped identity
        used to resolve image_ref attachments. Passed through from
        complete / complete_stream rather than read from self.* —
        same rationale as the CC call_* refactor.
        """
        from core.llm_message_regroup import regroup_split_assistant_messages
        messages = regroup_split_assistant_messages(messages)
        # Log multipart content for debugging
        _img_count = 0
        for m in messages:
            if isinstance(m.content, list):
                for p in m.content:
                    if p.get("type") in ("image_url", "image_ref"):
                        _img_count += 1
        if _img_count:
            logger.info("build_openai_messages: %d image part(s) in context", _img_count)

        # Sanitize: collect tool_call IDs from assistant messages, drop orphan tool messages
        valid_tc_ids: set = set()
        for m in messages:
            if m.role == "assistant" and m.tool_calls:
                for tc in m.tool_calls:
                    valid_tc_ids.add(tc.id)

        api_messages = []
        for m in messages:
            if m.role == "tool":
                # Skip orphan tool messages (no matching assistant tool_call)
                if m.tool_call_id and m.tool_call_id not in valid_tc_ids:
                    continue
                api_messages.append({
                    "role": "tool",
                    "content": m.text_content if isinstance(m.content, list) else m.content,
                    "tool_call_id": m.tool_call_id or "",
                })
                # OpenAI tool messages only support string content.
                # If multimodal, inject a user message with image parts after the tool result.
                if isinstance(m.content, list):
                    img_parts = []
                    for p in m.content:
                        if p.get("type") == "image_url":
                            img_parts.append(p)
                        elif p.get("type") == "image_ref":
                            img_parts.append(self._resolve_image_ref(
                                p, user_id=user_id,
                                conversation_id=conversation_id))
                    if img_parts:
                        api_messages.append({
                            "role": "user",
                            "content": [{"type": "text", "text": "(image from tool result)"}] + img_parts,
                        })
            elif m.role == "assistant" and m.tool_calls:
                content = m.content
                if isinstance(content, list):
                    content = m.text_content or None
                msg: Dict[str, Any] = {"role": "assistant", "content": content or None}
                msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in m.tool_calls
                ]
                api_messages.append(msg)
            elif isinstance(m.content, list):
                # Multi-part content (text + images)
                # OpenAI format: [{"type": "text", "text": "..."}, {"type": "image_url", ...}]
                # Convert unsupported types (document, image_ref, file_ref) to native types
                parts = []
                for part in m.content:
                    pt = part.get("type", "")
                    if pt == "document":
                        parts.append({
                            "type": "text",
                            "text": f"[Document: {part.get('filename', 'file')}]\n{part.get('text', '')}",
                        })
                    elif pt == "image_ref":
                        parts.append(self._resolve_image_ref(
                            part, user_id=user_id,
                            conversation_id=conversation_id))
                    elif pt == "file_ref":
                        parts.append({"type": "text", "text": f"[file: {part.get('filename', '?')}]"})
                    else:
                        parts.append(part)
                api_messages.append({"role": m.role, "content": parts})
            else:
                api_messages.append({"role": m.role, "content": m.content})
        return api_messages

    @staticmethod
    def _openai_tokens_key(model: str, base_url: str) -> str:
        """Choose 'max_completion_tokens' vs 'max_tokens' for OpenAI-compatible APIs.

        Newer OpenAI models (gpt-4o, gpt-5.x, o-series) require
        max_completion_tokens; older models and local servers use max_tokens.
        """
        # Local / third-party servers: keep legacy max_tokens
        if base_url and "api.openai.com" not in base_url:
            return "max_tokens"
        # OpenAI o-series, gpt-4o+, gpt-5+ -> new param
        m = (model or "").lower()
        if m.startswith(("o1", "o3", "o4", "gpt-4o", "gpt-5", "gpt-4.1")):
            return "max_completion_tokens"
        return "max_tokens"

    def _complete_openai(self, messages, model, temperature, max_tokens, response_format, tools=None,
                          *, call_user_id: str = "", call_conversation_id: str = ""):
        """Send a non-streaming completion to an OpenAI-compatible API."""
        from core.llm_client import LLMResponse, LLMToolCall

        body = {
            "model": model,
            "messages": self._build_openai_messages(
                messages,
                user_id=call_user_id,
                conversation_id=call_conversation_id),
        }
        if temperature is not None:
            body["temperature"] = temperature
        _re = self.reasoning_effort or None
        if _re:
            body["reasoning_effort"] = _re
        if max_tokens > 0:
            tokens_key = self._openai_tokens_key(model, self.base_url)
            body[tokens_key] = max_tokens
        if response_format == "json":
            body["response_format"] = {"type": "json_object"}
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]
        # OpenAI-specific cache params (ignored by non-OpenAI servers)
        _pck = self.prompt_cache_key or None
        if _pck:
            body["prompt_cache_key"] = _pck
        _pcr = self.prompt_cache_retention or None
        if _pcr:
            body["prompt_cache_retention"] = _pcr

        data = self._http_post(
            "/v1/chat/completions",
            body,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )
        choice = data.get("choices", [{}])[0]
        usage = data.get("usage", {})
        message = choice.get("message", {})

        # Parse tool calls if present
        tool_calls = []
        for tc in message.get("tool_calls", []):
            func = tc.get("function", {})
            try:
                args = json.loads(func.get("arguments", "{}"))
            except (json.JSONDecodeError, TypeError):
                args = {}
            tool_calls.append(LLMToolCall(
                id=tc.get("id", ""),
                name=func.get("name", ""),
                arguments=args,
            ))

        # Extract reasoning content if present (o-series models)
        reasoning = message.get("reasoning_content", "") or ""

        _content = message.get("content", "") or ""
        if not _content and usage.get("completion_tokens", 0) > 10:
            import logging as _log
            _log.getLogger(__name__).warning(
                f"[openai] {usage.get('completion_tokens')} tokens produced but content empty. "
                f"message={json.dumps(message, default=str)[:500]}, "
                f"usage={json.dumps(usage, default=str)}")

        # Cache logging. OpenAI includes cached tokens in prompt_tokens, so
        # split miss/hit counts before cost tracking sees the response.
        _ptd = usage.get("prompt_tokens_details") or {}
        cached_tokens = _ptd.get("cached_tokens", 0) or 0
        _prompt_tokens = usage.get("prompt_tokens", 0)
        _input_miss_tokens = max(0, _prompt_tokens - cached_tokens)
        if cached_tokens > 0:
            _hit_pct = (cached_tokens / _prompt_tokens * 100) if _prompt_tokens else 0
            logger.info("OpenAI prompt cache: %d cached of %d prompt tokens (%.0f%% hit)",
                        cached_tokens, _prompt_tokens, _hit_pct)
        elif _prompt_tokens > 1024:
            logger.info("OpenAI prompt cache: MISS — %d prompt tokens, 0 cached", _prompt_tokens)

        return LLMResponse(
            content=_content,
            model=data.get("model", model),
            tokens_in=_input_miss_tokens,
            tokens_out=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
            finish_reason=choice.get("finish_reason", ""),
            tool_calls=tool_calls,
            thinking=reasoning,
            raw=data,
            cache_read_tokens=cached_tokens,
        )
