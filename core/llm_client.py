"""Shared LLM HTTP client — zero dependencies (stdlib only).

Used by:
- services/llm_connection.py (LLMConnectionService)
- engine/nifi_script_converter.py (Groovy→Python conversion)
- tasks/ai/agent_loop.py (Agent LLM loop with tool_use)
- Any future PawFlow feature needing LLM calls
"""

import json
import http.client
import logging
import os
import re
import ssl
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Union, Tuple
from urllib.parse import urlparse
from uuid import uuid4

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
    source: Optional[Dict[str, str]] = None  # {"type": "user"|"agent", "name": "...", "llm_service": "..."}

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

    PROVIDERS = ("openai", "anthropic", "claude-code", "gemini-cli")

    DEFAULT_URLS = {
        "openai": "https://api.openai.com",
        "anthropic": "https://api.anthropic.com",
    }

    DEFAULT_MODELS = {
        "openai": "gpt-4o-mini",
        "anthropic": "claude-sonnet-4-20250514",
        "claude-code": "sonnet",
        "gemini-cli": "gemini-2.5-flash",
    }

    # Regex for parsing <tool_call>...</tool_call> tags from claude-code responses
    TOOL_CALL_RE = re.compile(r'<tool_call>\s*(\{.*?\})\s*</tool_call>', re.DOTALL)

    def __init__(
        self,
        provider: str = "openai",
        api_key: str = "",
        base_url: str = "",
        default_model: str = "",
        timeout: int = 60,
        max_retries: int = 2,
        claude_binary: str = "claude",
        gemini_binary: str = "gemini",
        refresh_token: str = "",
        token_expires_at: float = 0.0,
        token_url: str = "",
    ):
        self.provider = provider
        self.api_key = api_key
        self.base_url = base_url or self.DEFAULT_URLS.get(provider, "")
        self.default_model = default_model or self.DEFAULT_MODELS.get(provider, "")
        self.timeout = timeout
        self.max_retries = max_retries
        self.claude_binary = claude_binary
        self.gemini_binary = gemini_binary
        self.refresh_token = refresh_token
        self.token_expires_at = token_expires_at
        self.token_url = token_url or "https://console.anthropic.com/v1/oauth/token"
        self._token_lock = threading.Lock() if refresh_token else None
        # Token tracking callback — set by LLMConnectionService
        self._on_tokens = None  # callable(tokens_in, tokens_out, model)

    def _report_tokens(self, response, messages):
        """Report token usage via callback if set. Estimates if not returned by provider."""
        if not self._on_tokens:
            return
        tokens_in = response.tokens_in
        tokens_out = response.tokens_out
        # Estimate if provider didn't return counts
        if not tokens_in and messages:
            total_chars = sum(
                len(m.content) if isinstance(m.content, str)
                else sum(len(str(p)) for p in m.content) if isinstance(m.content, list)
                else 0 for m in messages
            )
            tokens_in = total_chars // 4
        if not tokens_out and response.content:
            tokens_out = len(response.content) // 4
        try:
            self._on_tokens(tokens_in, tokens_out, response.model or self.default_model)
        except Exception:
            pass

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
            claude_binary=config.get("claude_binary", "claude"),
            gemini_binary=config.get("gemini_binary", "gemini"),
            refresh_token=config.get("refresh_token", ""),
            token_expires_at=float(config.get("token_expires_at", 0)),
            token_url=config.get("token_url", ""),
        )

    def complete(
        self,
        messages: List[LLMMessage],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 0,
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
        if not self.api_key and self.provider not in ("claude-code", "gemini-cli"):
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
                elif self.provider == "claude-code":
                    result = self._complete_claude_code(messages, model, temperature, max_tokens, tools)
                elif self.provider == "gemini-cli":
                    result = self._complete_gemini_cli(messages, model, temperature, max_tokens, tools)
                else:
                    result = self._complete_anthropic(messages, model, temperature, max_tokens, tools)
                result.duration_ms = (time.time() - start) * 1000
                # Estimate tokens if provider didn't return counts
                if not result.tokens_in and messages:
                    result.tokens_in = sum(
                        len(m.content) if isinstance(m.content, str) else
                        sum(len(str(p)) for p in m.content) if isinstance(m.content, list)
                        else 0 for m in messages) // 4
                if not result.tokens_out and result.content:
                    result.tokens_out = len(result.content) // 4
                self._report_tokens(result, messages)
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
        max_tokens: int = 0,
        tools: Optional[List[LLMToolDefinition]] = None,
        callback=None,
    ) -> LLMResponse:
        """Streaming completion — calls callback(token: str) for each token.

        Also returns the full LLMResponse at the end.  If callback is None,
        behaves like complete() but uses the streaming API under the hood.

        Supports both OpenAI and Anthropic streaming.
        """
        if not self.api_key and self.provider not in ("claude-code", "gemini-cli"):
            raise LLMClientError("api_key is required")

        model = model or self.default_model
        start = time.time()

        if self.provider == "openai":
            result = self._stream_openai(messages, model, temperature, max_tokens, tools, callback)
        elif self.provider == "claude-code":
            result = self._stream_claude_code(messages, model, temperature, max_tokens, tools, callback)
        elif self.provider == "gemini-cli":
            result = self._stream_gemini_cli(messages, model, temperature, max_tokens, tools, callback)
        elif self.provider == "anthropic":
            result = self._stream_anthropic(messages, model, temperature, max_tokens, tools, callback)
        else:
            raise LLMClientError(f"Unknown provider '{self.provider}'")

        result.duration_ms = (time.time() - start) * 1000
        # Estimate tokens only if provider didn't return them
        if not result.tokens_in and messages:
            result.tokens_in = sum(
                len(m.content) if isinstance(m.content, str) else
                sum(len(str(p)) for p in m.content) if isinstance(m.content, list)
                else 0 for m in messages) // 4
        if not result.tokens_out and result.content:
            result.tokens_out = len(result.content) // 4
        self._report_tokens(result, messages)
        return result

    def _stream_openai(self, messages, model, temperature, max_tokens, tools, callback) -> LLMResponse:
        """OpenAI streaming: reads SSE chunks from the API."""
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

            content = "".join(content_parts)
            # Estimate tokens if not returned by provider
            est_in = sum(len(m.content) if isinstance(m.content, str) else
                         sum(len(str(p)) for p in m.content) if isinstance(m.content, list) else 0
                         for m in messages) // 4
            est_out = len(content) // 4
            return LLMResponse(
                content=content,
                model=resp_model,
                finish_reason=finish_reason,
                tool_calls=tool_calls,
                tokens_in=est_in,
                tokens_out=est_out,
            )
        finally:
            conn.close()

    def _stream_anthropic(self, messages, model, temperature, max_tokens, tools, callback) -> LLMResponse:
        """Anthropic streaming: reads SSE events from the API."""
        system_text, api_messages = self._build_anthropic_messages(messages)
        body = {
            "model": model,
            "messages": api_messages,
            "max_tokens": max_tokens if max_tokens > 0 else 64000,
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

    def embed(
        self,
        texts: List[str],
        model: Optional[str] = None,
    ) -> List[List[float]]:
        """Call OpenAI /v1/embeddings API. Batches max 2048 texts per call.

        Only supported for OpenAI provider (Anthropic has no embeddings API).

        Args:
            texts: List of texts to embed.
            model: Model name (default: text-embedding-3-small).

        Returns:
            List of embedding vectors (one per input text).
        """
        if not self.api_key:
            raise LLMClientError("api_key is required")
        if self.provider != "openai":
            raise LLMClientError("Embeddings are only supported with OpenAI provider")

        model = model or "text-embedding-3-small"
        all_embeddings: List[List[float]] = []
        batch_size = 2048

        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            body = {"model": model, "input": batch}
            data = self._http_post(
                "/v1/embeddings",
                body,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
            # Sort by index to ensure order matches input
            emb_data = sorted(data.get("data", []), key=lambda x: x.get("index", 0))
            for item in emb_data:
                all_embeddings.append(item.get("embedding", []))

        return all_embeddings

    def _build_openai_messages(self, messages: List[LLMMessage]) -> List[Dict[str, Any]]:
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

        body = {"model": model, "messages": api_messages, "max_tokens": max_tokens if max_tokens > 0 else 64000, "temperature": temperature}
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


    # ── claude-code provider (subprocess-based) ──────────────────────

    def _build_tool_prompt(self, tools: List[LLMToolDefinition]) -> str:
        """Render tool definitions as text for the system prompt."""
        if not tools:
            return ""
        lines = ["<available_tools>"]
        for t in tools:
            lines.append(f"## {t.name}")
            lines.append(t.description)
            lines.append(f"Parameters: {json.dumps(t.parameters)}")
            lines.append("")
        lines.append("</available_tools>")
        lines.append("")
        lines.append("<tool_use_instructions>")
        lines.append("To use a tool, you MUST output this EXACT XML format (multiple calls allowed):")
        lines.append('<tool_call>{"name": "tool_name", "arguments": {"param": "value"}}</tool_call>')
        lines.append("")
        lines.append("CRITICAL RULES:")
        lines.append("- NEVER describe what you would do. EMIT the <tool_call> tag directly.")
        lines.append("- Do NOT wrap tool calls in markdown code blocks.")
        lines.append("- After emitting <tool_call> tags, the system executes them and returns results in the next turn.")
        lines.append("- You may include text before or after <tool_call> tags.")
        lines.append("- If you need more information or another turn to complete your work, output: [NEED_MORE]")
        lines.append("- When no tool is needed and your answer is complete, respond with plain text (no tags).")
        lines.append("")
        lines.append("EXAMPLE — correct:")
        lines.append('Let me check that for you.')
        lines.append('<tool_call>{"name": "fetch_http", "arguments": {"url": "https://api.example.com/data"}}</tool_call>')
        lines.append("")
        lines.append("EXAMPLE — WRONG (never do this):")
        lines.append('I would use the fetch_http tool to retrieve the data from the API.')
        lines.append("</tool_use_instructions>")
        return "\n".join(lines)

    def _serialize_messages_for_cli(
        self, messages: List[LLMMessage], tools: Optional[List[LLMToolDefinition]],
    ) -> Tuple[str, str]:
        """Convert messages to (system_prompt, user_text) for the CLI.

        System messages + tool definitions → system_prompt.
        Conversation history → structured XML in user_text so the model
        understands it's a multi-turn conversation to continue.
        """
        system_parts = []
        history_lines = []
        last_user_text = ""
        has_history = False

        for m in messages:
            text = m.text_content if isinstance(m.content, list) else (m.content or "")
            if m.role == "system":
                system_parts.append(text)
            elif m.role == "user":
                last_user_text = text
                history_lines.append(f"<message role=\"user\">\n{text}\n</message>")
            elif m.role == "assistant":
                # Include source/agent identity if available
                source = getattr(m, "source", None) or {}
                agent_name = source.get("name", "") if isinstance(source, dict) else ""
                svc = source.get("llm_service", "") if isinstance(source, dict) else ""
                attr = ' role="assistant"'
                if agent_name:
                    attr += f' agent="{agent_name}"'
                if svc:
                    attr += f' service="{svc}"'

                assistant_text = text
                if m.tool_calls:
                    tc_strs = []
                    for tc in m.tool_calls:
                        tc_strs.append(
                            f'<tool_call>{json.dumps({"name": tc.name, "arguments": tc.arguments})}</tool_call>'
                        )
                    assistant_text = (assistant_text + "\n" + "\n".join(tc_strs)).strip()
                history_lines.append(f"<message{attr}>\n{assistant_text}\n</message>")
                has_history = True
            elif m.role == "tool":
                name = m.tool_call_id or "unknown"
                history_lines.append(
                    f"<message role=\"tool\" name=\"{name}\">\n{text}\n</message>"
                )

        # Build system prompt
        tool_prompt = self._build_tool_prompt(tools) if tools else ""
        if tool_prompt:
            system_parts.append(tool_prompt)
        system_prompt = "\n\n".join(system_parts)

        # Build user text
        if has_history:
            # Multi-turn: wrap in conversation tags with clear instruction
            user_text = (
                "<conversation_history>\n"
                + "\n".join(history_lines)
                + "\n</conversation_history>\n\n"
                "Continue the conversation. Reply to the latest user message. "
                "You are a participant in this conversation — read the full "
                "history above and respond naturally, referencing previous "
                "messages from any participant (user or other agents) as needed."
            )
        else:
            user_text = last_user_text

        return system_prompt, user_text

    def _extract_tool_calls(self, text: str) -> Tuple[str, List[LLMToolCall]]:
        """Extract <tool_call> tags from response text.

        Returns (clean_text, tool_calls) where clean_text has tags removed.
        """
        tool_calls = []
        for match in self.TOOL_CALL_RE.finditer(text):
            try:
                data = json.loads(match.group(1))
                tool_calls.append(LLMToolCall(
                    id=f"cc_{uuid4().hex[:12]}",
                    name=data.get("name", ""),
                    arguments=data.get("arguments", {}),
                ))
            except (json.JSONDecodeError, KeyError, TypeError):
                logger.warning("Failed to parse tool_call: %s", match.group(1)[:200])
        clean = self.TOOL_CALL_RE.sub("", text).strip()
        return clean, tool_calls

    def _refresh_oauth_token(self) -> None:
        """Refresh the OAuth access token using the refresh_token.

        Thread-safe: only one refresh happens at a time. Updates
        self.api_key and self.token_expires_at in place.
        """
        if not self.refresh_token or not self._token_lock:
            return
        with self._token_lock:
            # Double-check after acquiring lock (another thread may have refreshed)
            if self.token_expires_at and time.time() * 1000 < self.token_expires_at - 60_000:
                return  # still valid

            logger.info("Refreshing OAuth token via %s", self.token_url)
            parsed = urlparse(self.token_url)
            body = json.dumps({
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
            }).encode("utf-8")

            try:
                if parsed.scheme == "https":
                    ctx = ssl.create_default_context()
                    conn = http.client.HTTPSConnection(
                        parsed.hostname, parsed.port, timeout=30, context=ctx,
                    )
                else:
                    conn = http.client.HTTPConnection(
                        parsed.hostname, parsed.port, timeout=30,
                    )
                conn.request("POST", parsed.path or "/v1/oauth/token", body=body, headers={
                    "Content-Type": "application/json",
                    "Content-Length": str(len(body)),
                })
                resp = conn.getresponse()
                resp_body = resp.read().decode("utf-8")
                conn.close()

                if resp.status >= 400:
                    logger.error("OAuth refresh failed (%d): %s", resp.status, resp_body[:300])
                    return

                data = json.loads(resp_body)
                new_token = data.get("access_token", data.get("accessToken", ""))
                new_refresh = data.get("refresh_token", data.get("refreshToken", ""))
                expires_at = data.get("expires_at", data.get("expiresAt", 0))
                # Some endpoints return expires_in (seconds) instead of expires_at
                if not expires_at and data.get("expires_in"):
                    expires_at = time.time() * 1000 + data["expires_in"] * 1000

                if new_token:
                    self.api_key = new_token
                    self.token_expires_at = float(expires_at)
                    logger.info("OAuth token refreshed, expires at %s",
                                time.strftime("%Y-%m-%d %H:%M:%S",
                                              time.localtime(expires_at / 1000)) if expires_at else "unknown")
                    # Update refresh token if rotated
                    if new_refresh:
                        self.refresh_token = new_refresh
                    # Persist updated tokens back to service config
                    self._persist_refreshed_tokens()
                else:
                    logger.error("OAuth refresh returned no access_token: %s", resp_body[:300])
            except Exception as e:
                logger.error("OAuth token refresh error: %s", e)

    def _persist_refreshed_tokens(self) -> None:
        """Persist refreshed tokens back to secrets/params/config.

        Detects ${secrets.global.*} and ${global.*} expressions in the
        service config and updates the underlying store accordingly.
        Best-effort — if it fails, tokens are still valid in memory.
        """
        try:
            self._update_secret_or_config("api_key", self.api_key)
            self._update_secret_or_config("refresh_token", self.refresh_token)
            self._update_secret_or_config("token_expires_at", str(int(self.token_expires_at)))
        except Exception as e:
            logger.debug("Failed to persist refreshed tokens: %s", e)

    def _update_secret_or_config(self, field: str, value: str) -> None:
        """Update a value behind an expression reference, or direct config.

        Detects expression patterns in the service config and writes
        to the appropriate backing store:
        - ${secrets.global.KEY}       → config/global_secrets.json (encrypted)
        - ${secrets.user.KEY}         → config/users/<user>/secrets.json (encrypted)
        - ${global.KEY} / ${user.KEY} → config/global_parameters.json or user params (plaintext)
        - raw value                   → service config directly
        """
        if not value:
            return
        try:
            from gui.services.global_service_registry import GlobalServiceRegistry
            registry = GlobalServiceRegistry.get_instance()
            for svc_id, svc in registry._services.items():
                if not (hasattr(svc, '_client') and svc._client is self):
                    continue
                raw_val = str(svc.config.get(field, ""))
                if "${" in raw_val:
                    self._write_expression_value(raw_val, value)
                    logger.debug("Updated expression '%s' for field '%s'", raw_val, field)
                else:
                    svc.config[field] = value
                    registry.save()
                    logger.debug("Updated config field '%s' for service '%s'", field, svc_id)
                return
        except Exception as e:
            logger.debug("Failed to update %s: %s", field, e)

    @staticmethod
    def _write_expression_value(expression: str, value: str) -> None:
        """Write a value to the backing store referenced by an expression.

        Supports: ${secrets.global.KEY}, ${secrets.user.KEY},
                  ${global.KEY}, ${user.KEY}
        """
        from pathlib import Path

        m = re.search(r'\$\{([^}]+)\}', expression)
        if not m:
            return
        ref = m.group(1)  # e.g. "secrets.global.claude_api_key"
        parts = ref.split(".")

        if parts[0] == "secrets" and len(parts) >= 3:
            # ${secrets.global.KEY} or ${secrets.user.KEY}
            scope = parts[1]  # "global" or username
            key = ".".join(parts[2:])
            if scope == "global":
                path = Path("config/global_secrets.json")
            else:
                path = Path("config/users") / scope / "secrets.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            existing = {}
            if path.exists():
                existing = json.loads(path.read_text(encoding="utf-8"))
            from core.secrets import get_secrets_manager
            existing[key] = get_secrets_manager().encrypt(value)
            path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")

        elif parts[0] == "global" and len(parts) >= 2:
            # ${global.KEY}
            key = ".".join(parts[1:])
            path = Path("config/global_parameters.json")
            existing = {}
            if path.exists():
                existing = json.loads(path.read_text(encoding="utf-8"))
            existing[key] = value
            path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")

        elif parts[0] == "user" and len(parts) >= 2:
            # ${user.KEY} — needs owner context, best-effort
            key = ".".join(parts[1:])
            # Try to find the user from config/users/*/parameters.json
            # For now, log a warning — user-scoped write needs owner info
            logger.warning("Cannot auto-update user-scoped expression '%s' — "
                           "user context not available during token refresh", expression)
        else:
            logger.debug("Unknown expression pattern '%s', skipping", expression)

    def _claude_code_env(self) -> dict:
        """Build environment for claude subprocess.

        Claude CLI uses its own auth (claude login). Just inherit env.
        """
        return os.environ.copy()

    @staticmethod
    def _build_stdin_with_system(system_prompt: str, user_text: str) -> str:
        """Combine system prompt and user text into a single stdin payload.

        Always passes everything via stdin to avoid Windows command-line
        length limits (CreateProcess: 32,767 chars).
        """
        if not system_prompt:
            return user_text
        return (
            "<system_instructions>\n"
            + system_prompt
            + "\n</system_instructions>\n\n"
            + user_text
        )

    def _complete_claude_code(
        self, messages, model, temperature, max_tokens, tools=None,
    ) -> LLMResponse:
        """Run claude CLI in pipe mode and parse the response."""
        system_prompt, user_text = self._serialize_messages_for_cli(messages, tools)
        stdin_text = self._build_stdin_with_system(system_prompt, user_text)

        cmd = [
            self.claude_binary, "-p",
            "--output-format", "json",
            "--model", model or "sonnet",
            "--max-turns", "1",
            # Disable all native Claude Code tools — the model must only
            # respond with text (and optionally <tool_call> tags that we
            # parse ourselves).  Without this, Claude Code tries to execute
            # tools interactively (Read, Write, Bash...) which triggers
            # permission prompts and causes timeouts.
            "--allowedTools", "",
        ]
        # Note: Claude CLI has no --max-tokens flag (only --max-budget-usd)

        logger.debug("claude-code cmd: %s", " ".join(cmd[:6]) + "...")
        logger.debug("claude-code input length: %d chars", len(stdin_text))

        try:
            result = subprocess.run(
                cmd,
                input=stdin_text,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=self._claude_code_env(),
                encoding="utf-8",
            )
        except FileNotFoundError:
            raise LLMClientError(
                f"Claude CLI binary '{self.claude_binary}' not found. "
                f"Install with: npm install -g @anthropic-ai/claude-code"
            )
        except subprocess.TimeoutExpired:
            raise LLMClientError(
                f"Claude CLI timed out after {self.timeout}s"
            )

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            raise LLMClientError(
                f"Claude CLI exited with code {result.returncode}: {stderr[:500]}"
            )

        # Parse JSON output
        stdout = result.stdout.strip()
        if not stdout:
            raise LLMClientError("Claude CLI returned empty output")

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            # Sometimes output is plain text, not JSON
            content = stdout
            clean, tc = self._extract_tool_calls(content)
            return LLMResponse(
                content=clean, model=model, tool_calls=tc,
                finish_reason="stop" if not tc else "tool_use",
            )

        content = data.get("result", data.get("content", ""))
        if isinstance(content, list):
            # Handle content blocks format
            content = " ".join(
                b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
            )

        clean, tc = self._extract_tool_calls(content)

        return LLMResponse(
            content=clean,
            model=data.get("model", model),
            tokens_in=data.get("usage", {}).get("input_tokens", 0),
            tokens_out=data.get("usage", {}).get("output_tokens", 0),
            total_tokens=(
                data.get("usage", {}).get("input_tokens", 0)
                + data.get("usage", {}).get("output_tokens", 0)
            ),
            finish_reason="stop" if not tc else "tool_use",
            tool_calls=tc,
            raw=data,
        )

    def _stream_claude_code(
        self, messages, model, temperature, max_tokens, tools, callback,
    ) -> LLMResponse:
        """Stream from claude CLI using stream-json output format."""
        system_prompt, user_text = self._serialize_messages_for_cli(messages, tools)
        stdin_text = self._build_stdin_with_system(system_prompt, user_text)

        cmd = [
            self.claude_binary, "-p",
            "--output-format", "stream-json",
            "--verbose",
            "--model", model or "sonnet",
            "--max-turns", "1",
        ]
        # Note: Claude CLI has no --max-tokens flag (only --max-budget-usd)

        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=self._claude_code_env(),
                encoding="utf-8",
            )
        except FileNotFoundError:
            raise LLMClientError(
                f"Claude CLI binary '{self.claude_binary}' not found. "
                f"Install with: npm install -g @anthropic-ai/claude-code"
            )

        # Send input and close stdin
        try:
            proc.stdin.write(stdin_text)
            proc.stdin.close()
        except BrokenPipeError:
            # CLI process died before reading input — capture stderr
            stderr = ""
            try:
                stderr = proc.stderr.read().strip()
            except Exception:
                pass
            proc.wait()
            raise LLMClientError(
                f"Claude CLI pipe broken (exit {proc.returncode}): {stderr[:500]}"
            )

        # Read streaming output line by line
        content_parts = []
        last_data = {}
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                etype = event.get("type", "")
                if etype == "assistant":
                    # Content message
                    msg = event.get("message", {})
                    for block in msg.get("content", []):
                        if block.get("type") == "text":
                            text = block.get("text", "")
                            if text:
                                content_parts.append(text)
                                if callback:
                                    callback(text)
                    last_data = msg
                elif etype == "content_block_delta":
                    delta = event.get("delta", {})
                    text = delta.get("text", "")
                    if text:
                        content_parts.append(text)
                        if callback:
                            callback(text)
                elif etype == "result":
                    # Final result
                    result_text = event.get("result", "")
                    if result_text and not content_parts:
                        content_parts.append(result_text)
                        if callback:
                            callback(result_text)
                    last_data = event
        finally:
            proc.stdout.close()
            proc.stderr.close()
            proc.wait(timeout=5)

        if proc.returncode and proc.returncode != 0:
            raise LLMClientError(f"Claude CLI stream exited with code {proc.returncode}")

        full_content = "".join(content_parts)
        clean, tc = self._extract_tool_calls(full_content)

        usage = last_data.get("usage", {})
        return LLMResponse(
            content=clean,
            model=last_data.get("model", model),
            tokens_in=usage.get("input_tokens", 0),
            tokens_out=usage.get("output_tokens", 0),
            total_tokens=usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
            finish_reason="stop" if not tc else "tool_use",
            tool_calls=tc,
            raw=last_data,
        )


    # ── gemini-cli provider (subprocess-based) ───────────────────────

    def _gemini_cli_env(self) -> dict:
        """Build environment for gemini subprocess."""
        env = os.environ.copy()
        if self.api_key:
            env["GEMINI_API_KEY"] = self.api_key
        return env

    def _complete_gemini_cli(
        self, messages, model, temperature, max_tokens, tools=None,
    ) -> LLMResponse:
        """Run gemini CLI in prompt mode and parse the response."""
        system_prompt, user_text = self._serialize_messages_for_cli(messages, tools)
        env = self._gemini_cli_env()

        cmd = [
            self.gemini_binary, "-p",
            "--output-format", "json",
            "-m", model or "gemini-2.5-flash",
        ]

        # System prompt via temp file (gemini uses GEMINI_SYSTEM_MD env var)
        sys_file = None
        try:
            if system_prompt:
                sys_file = tempfile.NamedTemporaryFile(
                    mode="w", suffix=".md", delete=False, encoding="utf-8",
                )
                sys_file.write(system_prompt)
                sys_file.close()
                env["GEMINI_SYSTEM_MD"] = sys_file.name

            logger.debug("gemini-cli cmd: %s", " ".join(cmd[:6]) + "...")

            try:
                result = subprocess.run(
                    cmd, input=user_text, capture_output=True,
                    text=True, timeout=self.timeout, env=env,
                    encoding="utf-8",
                )
            except FileNotFoundError:
                raise LLMClientError(
                    f"Gemini CLI binary '{self.gemini_binary}' not found. "
                    f"Install with: npm install -g @google/gemini-cli"
                )
            except subprocess.TimeoutExpired:
                raise LLMClientError(f"Gemini CLI timed out after {self.timeout}s")
        finally:
            if sys_file:
                try:
                    os.unlink(sys_file.name)
                except OSError:
                    pass

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            raise LLMClientError(
                f"Gemini CLI exited with code {result.returncode}: {stderr[:500]}"
            )

        stdout = result.stdout.strip()
        if not stdout:
            raise LLMClientError("Gemini CLI returned empty output")

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            clean, tc = self._extract_tool_calls(stdout)
            return LLMResponse(
                content=clean, model=model,
                finish_reason="stop" if not tc else "tool_use", tool_calls=tc,
            )

        content = data.get("response", data.get("result", ""))
        if isinstance(content, list):
            content = " ".join(
                b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
            )

        clean, tc = self._extract_tool_calls(content)

        # Gemini stats format: {"stats": {"models": {"model_name": {"inputTokens": N, ...}}}}
        stats = data.get("stats", {})
        model_stats = {}
        for _mname, mdata in stats.get("models", {}).items():
            model_stats = mdata
            break
        tokens_in = model_stats.get("inputTokens", 0)
        tokens_out = model_stats.get("outputTokens", 0)

        return LLMResponse(
            content=clean,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            total_tokens=tokens_in + tokens_out,
            finish_reason="stop" if not tc else "tool_use",
            tool_calls=tc,
            raw=data,
        )

    def _stream_gemini_cli(
        self, messages, model, temperature, max_tokens, tools, callback,
    ) -> LLMResponse:
        """Stream from gemini CLI using stream-json output format."""
        system_prompt, user_text = self._serialize_messages_for_cli(messages, tools)
        env = self._gemini_cli_env()

        cmd = [
            self.gemini_binary, "-p",
            "--output-format", "stream-json",
            "-m", model or "gemini-2.5-flash",
        ]

        sys_file = None
        try:
            if system_prompt:
                sys_file = tempfile.NamedTemporaryFile(
                    mode="w", suffix=".md", delete=False, encoding="utf-8",
                )
                sys_file.write(system_prompt)
                sys_file.close()
                env["GEMINI_SYSTEM_MD"] = sys_file.name

            try:
                proc = subprocess.Popen(
                    cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True, env=env,
                    encoding="utf-8",
                )
            except FileNotFoundError:
                raise LLMClientError(
                    f"Gemini CLI binary '{self.gemini_binary}' not found. "
                    f"Install with: npm install -g @google/gemini-cli"
                )

            proc.stdin.write(user_text)
            proc.stdin.close()

            content_parts = []
            last_data = {}
            try:
                for line in proc.stdout:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    etype = event.get("type", "")
                    if etype in ("message", "assistant"):
                        msg = event.get("message", event)
                        for block in msg.get("content", []):
                            if block.get("type") == "text":
                                text = block.get("text", "")
                                if text:
                                    content_parts.append(text)
                                    if callback:
                                        callback(text)
                        last_data = event
                    elif etype == "content_block_delta":
                        delta = event.get("delta", {})
                        text = delta.get("text", "")
                        if text:
                            content_parts.append(text)
                            if callback:
                                callback(text)
                    elif etype == "result":
                        result_text = event.get("response", event.get("result", ""))
                        if result_text and not content_parts:
                            content_parts.append(result_text)
                            if callback:
                                callback(result_text)
                        last_data = event
            finally:
                proc.stdout.close()
                proc.stderr.close()
                proc.wait(timeout=5)

            if proc.returncode and proc.returncode != 0:
                raise LLMClientError(f"Gemini CLI stream exited with code {proc.returncode}")
        finally:
            if sys_file:
                try:
                    os.unlink(sys_file.name)
                except OSError:
                    pass

        full_content = "".join(content_parts)
        clean, tc = self._extract_tool_calls(full_content)

        stats = last_data.get("stats", {})
        model_stats = {}
        for _mname, mdata in stats.get("models", {}).items():
            model_stats = mdata
            break
        tokens_in = model_stats.get("inputTokens", 0)
        tokens_out = model_stats.get("outputTokens", 0)

        return LLMResponse(
            content=clean,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            total_tokens=tokens_in + tokens_out,
            finish_reason="stop" if not tc else "tool_use",
            tool_calls=tc,
            raw=last_data,
        )


class LLMClientError(Exception):
    """Error from LLM client."""
    pass
