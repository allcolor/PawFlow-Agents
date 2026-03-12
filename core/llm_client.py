"""Shared LLM HTTP client — zero dependencies (stdlib only).

Used by:
- services/llm_connection.py (LLMConnectionService)
- engine/nifi_script_converter.py (Groovy→Python conversion)
- tasks/ai/agent_loop.py (Agent LLM loop with tool_use)
- Any future PyFi2 feature needing LLM calls
"""

import json
import http.client
import logging
import ssl
import time
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Union
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


@dataclass
class LLMToolDefinition:
    """A tool definition sent to the LLM."""
    name: str
    description: str
    parameters: Dict[str, Any]  # JSON Schema for the tool's input


@dataclass
class LLMToolCall:
    """A tool call requested by the LLM."""
    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass
class LLMToolResult:
    """Result of executing a tool call, sent back to the LLM."""
    tool_call_id: str
    content: str


@dataclass
class LLMMessage:
    """A single message in a conversation.

    For tool_calls from the assistant: role="assistant", content may be empty,
    tool_calls contains the list of tool calls.
    For tool results: role="tool", content is the result text,
    tool_call_id identifies which call this responds to.

    Content can be:
    - str: plain text message
    - List[dict]: multi-part content (text + images), e.g.:
        [{"type": "text", "text": "Describe this image"},
         {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}]
    """
    role: str  # "system", "user", "assistant", "tool"
    content: Union[str, List[Dict[str, Any]]] = ""
    tool_calls: Optional[List[LLMToolCall]] = None
    tool_call_id: Optional[str] = None

    @property
    def text_content(self) -> str:
        """Extract text content regardless of content format."""
        if isinstance(self.content, str):
            return self.content
        if isinstance(self.content, list):
            return " ".join(
                p.get("text", "") for p in self.content if p.get("type") == "text"
            )
        return ""


@dataclass
class LLMResponse:
    """Response from an LLM API call."""
    content: str = ""
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    total_tokens: int = 0
    finish_reason: str = ""
    duration_ms: float = 0.0
    tool_calls: List[LLMToolCall] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)


class LLMClient:
    """Standalone LLM HTTP client (no BaseService dependency).

    Supports OpenAI-compatible and Anthropic APIs via stdlib HTTP.

    Args:
        provider: "openai" or "anthropic"
        api_key: API key
        base_url: API base URL (optional, uses provider default)
        default_model: Default model name (optional)
        timeout: Request timeout in seconds
        max_retries: Number of retries on transient errors
    """

    PROVIDERS = ("openai", "anthropic")

    DEFAULT_URLS = {
        "openai": "https://api.openai.com",
        "anthropic": "https://api.anthropic.com",
    }

    DEFAULT_MODELS = {
        "openai": "gpt-4o-mini",
        "anthropic": "claude-sonnet-4-20250514",
    }

    def __init__(
        self,
        provider: str = "openai",
        api_key: str = "",
        base_url: str = "",
        default_model: str = "",
        timeout: int = 60,
        max_retries: int = 2,
    ):
        self.provider = provider
        self.api_key = api_key
        self.base_url = base_url or self.DEFAULT_URLS.get(provider, "")
        self.default_model = default_model or self.DEFAULT_MODELS.get(provider, "")
        self.timeout = timeout
        self.max_retries = max_retries

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> "LLMClient":
        """Create from a flat config dict."""
        return cls(
            provider=config.get("provider", "openai"),
            api_key=config.get("api_key", ""),
            base_url=config.get("base_url", ""),
            default_model=config.get("default_model", ""),
            timeout=int(config.get("timeout", 60)),
            max_retries=int(config.get("max_retries", 2)),
        )

    def complete(
        self,
        messages: List[LLMMessage],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        response_format: Optional[str] = None,
        tools: Optional[List[LLMToolDefinition]] = None,
    ) -> LLMResponse:
        """Send a completion request to the LLM.

        Args:
            messages: Conversation messages (supports tool_calls and tool results).
            model: Model name override.
            temperature: Sampling temperature.
            max_tokens: Max response tokens.
            response_format: "json" for JSON mode (OpenAI only).
            tools: Tool definitions for function calling / tool_use.

        Returns:
            LLMResponse with content and/or tool_calls populated.
        """
        if not self.api_key:
            raise LLMClientError("api_key is required")
        if self.provider not in self.PROVIDERS:
            raise LLMClientError(
                f"Unknown provider '{self.provider}'. Supported: {', '.join(self.PROVIDERS)}"
            )

        model = model or self.default_model

        for attempt in range(1, self.max_retries + 1):
            try:
                start = time.time()
                if self.provider == "openai":
                    result = self._complete_openai(messages, model, temperature, max_tokens, response_format, tools)
                else:
                    result = self._complete_anthropic(messages, model, temperature, max_tokens, tools)
                result.duration_ms = (time.time() - start) * 1000
                return result
            except LLMClientError:
                raise
            except Exception as e:
                if attempt < self.max_retries:
                    logger.warning(f"LLM request attempt {attempt} failed: {e}, retrying...")
                    time.sleep(attempt * 0.5)
                else:
                    raise LLMClientError(f"LLM request failed after {self.max_retries} attempts: {e}")

    def complete_stream(
        self,
        messages: List[LLMMessage],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        tools: Optional[List[LLMToolDefinition]] = None,
        callback=None,
    ) -> LLMResponse:
        """Streaming completion — calls callback(token: str) for each token.

        Also returns the full LLMResponse at the end.  If callback is None,
        behaves like complete() but uses the streaming API under the hood.

        Supports both OpenAI and Anthropic streaming.
        """
        if not self.api_key:
            raise LLMClientError("api_key is required")

        model = model or self.default_model
        start = time.time()

        if self.provider == "openai":
            result = self._stream_openai(messages, model, temperature, max_tokens, tools, callback)
        elif self.provider == "anthropic":
            result = self._stream_anthropic(messages, model, temperature, max_tokens, tools, callback)
        else:
            raise LLMClientError(f"Unknown provider '{self.provider}'")

        result.duration_ms = (time.time() - start) * 1000
        return result

    def _stream_openai(self, messages, model, temperature, max_tokens, tools, callback) -> LLMResponse:
        """OpenAI streaming: reads SSE chunks from the API."""
        tokens_key = self._openai_tokens_key(model, self.base_url)
        body = {
            "model": model,
            "messages": self._build_openai_messages(messages),
            "temperature": temperature,
            tokens_key: max_tokens,
            "stream": True,
        }
        if tools:
            body["tools"] = [
                {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
                for t in tools
            ]

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
            content_parts = []
            tool_calls_map: Dict[int, Dict] = {}  # index -> {id, name, arguments_str}
            finish_reason = ""
            resp_model = model

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
                            delta = data.get("choices", [{}])[0].get("delta", {})
                            fr = data.get("choices", [{}])[0].get("finish_reason")
                            if fr:
                                finish_reason = fr
                            if data.get("model"):
                                resp_model = data["model"]

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

            return LLMResponse(
                content="".join(content_parts),
                model=resp_model,
                finish_reason=finish_reason,
                tool_calls=tool_calls,
            )
        finally:
            conn.close()

    def _stream_anthropic(self, messages, model, temperature, max_tokens, tools, callback) -> LLMResponse:
        """Anthropic streaming: reads SSE events from the API."""
        system_text, api_messages = self._build_anthropic_messages(messages)
        body = {
            "model": model,
            "messages": api_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        if system_text:
            body["system"] = system_text
        if tools:
            body["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters}
                for t in tools
            ]

        parsed = urlparse(self.base_url)
        host = parsed.hostname
        port = parsed.port
        full_path = (parsed.path.rstrip("/") + "/v1/messages").replace("//", "/")

        if parsed.scheme == "https":
            ctx = ssl.create_default_context()
            conn = http.client.HTTPSConnection(host, port, timeout=self.timeout, context=ctx)
        else:
            conn = http.client.HTTPConnection(host, port, timeout=self.timeout)

        try:
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

            content_parts = []
            tool_calls = []
            current_tool: Optional[Dict] = None
            tool_input_str = ""
            finish_reason = ""
            resp_model = model
            tokens_in = 0
            tokens_out = 0

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
                    if line.startswith("data: "):
                        try:
                            data = json.loads(line[6:])
                            evt_type = data.get("type", "")

                            if evt_type == "message_start":
                                msg = data.get("message", {})
                                resp_model = msg.get("model", model)
                                usage = msg.get("usage", {})
                                tokens_in = usage.get("input_tokens", 0)

                            elif evt_type == "content_block_start":
                                block = data.get("content_block", {})
                                if block.get("type") == "tool_use":
                                    current_tool = {
                                        "id": block.get("id", ""),
                                        "name": block.get("name", ""),
                                    }
                                    tool_input_str = ""

                            elif evt_type == "content_block_delta":
                                delta = data.get("delta", {})
                                if delta.get("type") == "text_delta":
                                    text = delta.get("text", "")
                                    if text:
                                        content_parts.append(text)
                                        if callback:
                                            callback(text)
                                elif delta.get("type") == "input_json_delta":
                                    tool_input_str += delta.get("partial_json", "")

                            elif evt_type == "content_block_stop":
                                if current_tool:
                                    try:
                                        args = json.loads(tool_input_str) if tool_input_str else {}
                                    except json.JSONDecodeError:
                                        args = {}
                                    tool_calls.append(LLMToolCall(
                                        id=current_tool["id"],
                                        name=current_tool["name"],
                                        arguments=args,
                                    ))
                                    current_tool = None
                                    tool_input_str = ""

                            elif evt_type == "message_delta":
                                delta = data.get("delta", {})
                                finish_reason = delta.get("stop_reason", finish_reason)
                                usage = data.get("usage", {})
                                tokens_out = usage.get("output_tokens", tokens_out)

                            elif evt_type == "message_stop":
                                pass

                        except (json.JSONDecodeError, KeyError):
                            pass

            return LLMResponse(
                content="".join(content_parts),
                model=resp_model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                total_tokens=tokens_in + tokens_out,
                finish_reason=finish_reason,
                tool_calls=tool_calls,
            )
        finally:
            conn.close()

    def _build_openai_messages(self, messages: List[LLMMessage]) -> List[Dict[str, Any]]:
        """Convert LLMMessage list to OpenAI API message format."""
        api_messages = []
        for m in messages:
            if m.role == "tool":
                api_messages.append({
                    "role": "tool",
                    "content": m.text_content if isinstance(m.content, list) else m.content,
                    "tool_call_id": m.tool_call_id or "",
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
        # OpenAI o-series, gpt-4o+, gpt-5+ → new param
        m = (model or "").lower()
        if m.startswith(("o1", "o3", "o4", "gpt-4o", "gpt-5", "gpt-4.1")):
            return "max_completion_tokens"
        return "max_tokens"

    def _complete_openai(self, messages, model, temperature, max_tokens, response_format, tools=None) -> LLMResponse:
        tokens_key = self._openai_tokens_key(model, self.base_url)
        body = {
            "model": model,
            "messages": self._build_openai_messages(messages),
            "temperature": temperature,
            tokens_key: max_tokens,
        }
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

        return LLMResponse(
            content=message.get("content", "") or "",
            model=data.get("model", model),
            tokens_in=usage.get("prompt_tokens", 0),
            tokens_out=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
            finish_reason=choice.get("finish_reason", ""),
            tool_calls=tool_calls,
            raw=data,
        )

    def _build_anthropic_messages(self, messages: List[LLMMessage]) -> tuple:
        """Convert LLMMessage list to Anthropic API format.

        Returns (system_text, api_messages).
        """
        system_text = ""
        api_messages = []
        for m in messages:
            if m.role == "system":
                system_text = m.text_content if isinstance(m.content, list) else m.content
            elif m.role == "tool":
                # Anthropic: tool results are sent as user messages with tool_result content blocks
                api_messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": m.tool_call_id or "",
                            "content": m.text_content if isinstance(m.content, list) else m.content,
                        }
                    ],
                })
            elif m.role == "assistant" and m.tool_calls:
                # Assistant message with tool_use content blocks
                content_blocks = []
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
                    elif part.get("type") == "document":
                        # Document content — inject as text block
                        content_blocks.append({
                            "type": "text",
                            "text": f"[Document: {part.get('filename', 'file')}]\n{part.get('text', '')}",
                        })
                api_messages.append({"role": m.role, "content": content_blocks})
            else:
                api_messages.append({"role": m.role, "content": m.content})
        return system_text, api_messages

    def _complete_anthropic(self, messages, model, temperature, max_tokens, tools=None) -> LLMResponse:
        system_text, api_messages = self._build_anthropic_messages(messages)

        body = {"model": model, "messages": api_messages, "max_tokens": max_tokens, "temperature": temperature}
        if system_text:
            body["system"] = system_text
        if tools:
            body["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.parameters,
                }
                for t in tools
            ]

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
        text = "".join(b.get("text", "") for b in content_blocks if b.get("type") == "text")

        # Parse tool_use blocks
        tool_calls = []
        for block in content_blocks:
            if block.get("type") == "tool_use":
                tool_calls.append(LLMToolCall(
                    id=block.get("id", ""),
                    name=block.get("name", ""),
                    arguments=block.get("input", {}),
                ))

        usage = data.get("usage", {})
        return LLMResponse(
            content=text,
            model=data.get("model", model),
            tokens_in=usage.get("input_tokens", 0),
            tokens_out=usage.get("output_tokens", 0),
            total_tokens=usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
            finish_reason=data.get("stop_reason", ""),
            tool_calls=tool_calls,
            raw=data,
        )

    @staticmethod
    def _clean_control_chars(text: str) -> str:
        """Remove control characters that break JSON parsing on some APIs."""
        import re
        return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', text)

    def _http_post(self, path: str, body: dict, headers: dict) -> dict:
        """Send POST and return parsed JSON."""
        parsed = urlparse(self.base_url)
        host = parsed.hostname
        port = parsed.port
        scheme = parsed.scheme

        if scheme == "https":
            ctx = ssl.create_default_context()
            conn = http.client.HTTPSConnection(host, port, timeout=self.timeout, context=ctx)
        else:
            conn = http.client.HTTPConnection(host, port, timeout=self.timeout)

        try:
            raw_json = json.dumps(body)
            # Strip control characters that some LLM APIs can't parse
            json_body = self._clean_control_chars(raw_json).encode("utf-8")
            headers["Content-Length"] = str(len(json_body))
            full_path = (parsed.path.rstrip("/") + "/" + path.lstrip("/")).replace("//", "/")
            conn.request("POST", full_path, body=json_body, headers=headers)
            response = conn.getresponse()
            response_body = response.read().decode("utf-8")
            if response.status >= 400:
                raise LLMClientError(f"LLM API error {response.status}: {response_body[:500]}")
            return json.loads(response_body)
        finally:
            conn.close()


class LLMClientError(Exception):
    """Error from LLM client."""
    pass
