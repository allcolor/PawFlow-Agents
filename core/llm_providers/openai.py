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
                        thinking_callback=None):
        """OpenAI streaming: reads SSE chunks from the API."""
        from core.llm_client import LLMClientError, LLMResponse, LLMToolCall

        body = {
            "model": model,
            "messages": self._build_openai_messages(messages),
            "temperature": temperature,
            "stream": True,
        }
        if max_tokens > 0:
            tokens_key = self._openai_tokens_key(model, self.base_url)
            body[tokens_key] = max_tokens
        if tools:
            body["tools"] = [
                {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
                for t in tools
            ]
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
                                if thinking_callback:
                                    thinking_callback(reasoning)

                            # Text content
                            text = delta.get("content", "")
                            if text:
                                content_parts.append(text)
                                if callback:
                                    callback(text)

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

            return LLMResponse(
                content=content,
                model=resp_model,
                finish_reason=finish_reason,
                tool_calls=tool_calls,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                total_tokens=total_tokens,
                thinking=thinking,
            )
        finally:
            conn.close()

    def _build_openai_messages(self, messages) -> List[Dict[str, Any]]:
        """Convert LLMMessage list to OpenAI API message format."""
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
                    img_parts = [p for p in m.content if p.get("type") == "image_url"]
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
                # Convert unsupported types (document) to text
                parts = []
                for part in m.content:
                    if part.get("type") == "document":
                        parts.append({
                            "type": "text",
                            "text": f"[Document: {part.get('filename', 'file')}]\n{part.get('text', '')}",
                        })
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

    def _complete_openai(self, messages, model, temperature, max_tokens, response_format, tools=None):
        """Send a non-streaming completion to an OpenAI-compatible API."""
        from core.llm_client import LLMResponse, LLMToolCall

        body = {
            "model": model,
            "messages": self._build_openai_messages(messages),
            "temperature": temperature,
        }
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

        return LLMResponse(
            content=message.get("content", "") or "",
            model=data.get("model", model),
            tokens_in=usage.get("prompt_tokens", 0),
            tokens_out=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
            finish_reason=choice.get("finish_reason", ""),
            tool_calls=tool_calls,
            thinking=reasoning,
            raw=data,
        )
