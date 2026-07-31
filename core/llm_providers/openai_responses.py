"""LLM provider mixin -- OpenAI Responses API.

The Responses API is OpenAI's successor to chat/completions, and it is a
different wire format at every level, not a flag on the old one:

  - ``messages[]`` becomes ``input[]`` of typed items; a tool call is its own
    ``function_call`` item and its result a ``function_call_output`` item,
    rather than an assistant field and a ``role: tool`` message;
  - the system prompt moves out of the list into ``instructions``;
  - tools lose the ``{"function": {...}}`` envelope and are declared flat;
  - the stream is SEMANTIC: typed events carrying their own deltas, and it
    ends on ``response.completed`` / ``.incomplete`` / ``.failed`` -- there is
    no ``data: [DONE]``, so a chat/completions reader waits forever;
  - usage is ``input_tokens`` / ``output_tokens``, with the cache hit and the
    reasoning tokens as details rather than ``prompt_tokens`` details.

It is implemented once, for any endpoint that speaks the format -- the same
position PawFlow already takes on chat/completions. OpenAI is the reason the
format matters (it is where reasoning items and the built-in tools live), and
DeepSeek exposes it too, at the same base URL as their chat/completions.

Unsupported top-level parameters are specified to be IGNORED rather than
rejected, so one payload shape is safe to send everywhere; nothing here
branches on the vendor.
"""

import json
import http.client
import logging
import re
from typing import Any, Dict, List, Tuple
from urllib.parse import urlparse

from core.token_counter import count_messages_tokens

logger = logging.getLogger(__name__)

#: Events that end the stream. `.failed` carries an `error` object, and the
#: absence of a [DONE] sentinel is why they have to be recognised explicitly.
_TERMINAL_EVENTS = ("response.completed", "response.incomplete", "response.failed")


def responses_endpoint(base_url: str) -> str:
    """The ``/responses`` suffix to append to ``base_url``'s path.

    Same rule as chat/completions: a base that already carries its own version
    segment does not get another ``/v1`` on top, and a base that already names
    the endpoint is left alone.
    """
    path = urlparse(base_url or "").path.rstrip("/")
    if path.endswith("/responses"):
        return ""
    if re.search(r"/v\d+[a-z]*$", path):
        return "/responses"
    return "/v1/responses"


def _text_of(content) -> str:
    """Flatten a chat-completions content field to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content
                       if isinstance(p, dict) and p.get("type") == "text")
    return ""


def _content_parts(content, role: str) -> List[Dict[str, Any]]:
    """Content parts for one message item.

    The part type is decided by WHO wrote it: what the model produced is
    ``output_text``, everything else is ``input_text``. Sending a user turn as
    output_text is rejected outright, which makes this the single most
    load-bearing line of the conversion.
    """
    kind = "output_text" if role == "assistant" else "input_text"
    if isinstance(content, str):
        return [{"type": kind, "text": content}] if content else []
    parts: List[Dict[str, Any]] = []
    for p in content or []:
        if not isinstance(p, dict):
            continue
        if p.get("type") == "text":
            if p.get("text"):
                parts.append({"type": kind, "text": p["text"]})
        elif p.get("type") == "image_url":
            url = p.get("image_url") or {}
            url = url.get("url", "") if isinstance(url, dict) else str(url or "")
            if url:
                # Endpoints without vision replace this with placeholder text
                # instead of failing, so it is safe to send unconditionally.
                parts.append({"type": "input_image", "image_url": url})
    return parts


def build_responses_input(api_messages: List[Dict[str, Any]]) -> Tuple[str, List[Dict[str, Any]]]:
    """Convert chat-completions messages into ``(instructions, input items)``.

    Deliberately fed from the ALREADY-normalised OpenAI messages rather than
    from LLMMessage: regrouping the split assistant pair, the vision policy and
    the orphan-tool-result sanitising are decided there, once, and duplicating
    that here is how the two paths would drift apart.

    Leading system/developer messages become ``instructions``. A system message
    that appears later is left in place as an item -- moving it to the front
    would reorder the conversation.
    """
    instructions: List[str] = []
    items: List[Dict[str, Any]] = []
    seen_non_system = False

    for m in api_messages:
        role = m.get("role", "")
        if role in ("system", "developer") and not seen_non_system:
            text = _text_of(m.get("content"))
            if text:
                instructions.append(text)
            continue
        if role == "tool":
            # The result stops being a message and becomes its own item,
            # addressed by the call id rather than by position.
            items.append({
                "type": "function_call_output",
                "call_id": m.get("tool_call_id", ""),
                "output": _text_of(m.get("content")) or "",
            })
            seen_non_system = True
            continue

        seen_non_system = True
        parts = _content_parts(m.get("content"), role)
        if parts:
            items.append({"type": "message", "role": role, "content": parts})
        # An assistant turn's tool calls are siblings of its text, not a field
        # on it, so they are emitted after the message they accompanied.
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function") or {}
            items.append({
                "type": "function_call",
                "call_id": tc.get("id", ""),
                "name": fn.get("name", ""),
                "arguments": fn.get("arguments", "") or "{}",
            })

    return "\n\n".join(instructions), items


class _StreamState:
    """What a Responses stream builds up, event by event."""

    def __init__(self):
        self.text: List[str] = []
        self.reasoning: List[str] = []
        self.calls: Dict[str, Dict[str, Any]] = {}
        self.order: List[str] = []
        self.usage: Dict[str, Any] = {}
        self.model = ""
        self.status = ""
        self.error = ""
        self.terminal_event = ""

    def _slot(self, key: str) -> Dict[str, Any]:
        if key not in self.calls:
            self.calls[key] = {"call_id": "", "name": "", "arguments": ""}
            self.order.append(key)
        return self.calls[key]

    def feed(self, ev: Dict[str, Any]) -> bool:
        """Apply one decoded SSE event.

        Keyed on ``item_id`` and not on ``output_index``: parallel tool calling
        is always on in the Responses API, so several function calls stream
        interleaved and an index is not a stable identity across events.
        """
        etype = ev.get("type", "")
        if etype == "response.output_text.delta":
            if ev.get("delta"):
                self.text.append(ev["delta"])
        elif etype in ("response.reasoning_text.delta",
                       "response.reasoning_summary_text.delta"):
            if ev.get("delta"):
                self.reasoning.append(ev["delta"])
        elif etype == "response.function_call_arguments.delta":
            key = ev.get("item_id") or str(ev.get("output_index", 0))
            self._slot(key)["arguments"] += ev.get("delta", "") or ""
        elif etype in ("response.output_item.added", "response.output_item.done"):
            item = ev.get("item") or {}
            if item.get("type") != "function_call":
                return False
            key = item.get("id") or ev.get("item_id") or str(ev.get("output_index", 0))
            slot = self._slot(key)
            slot["call_id"] = item.get("call_id") or slot["call_id"]
            slot["name"] = item.get("name") or slot["name"]
            # `done` carries the complete arguments; it is authoritative over
            # whatever the deltas accumulated.
            if etype == "response.output_item.done" and item.get("arguments"):
                slot["arguments"] = item["arguments"]
        elif etype in _TERMINAL_EVENTS:
            self.terminal_event = etype
            resp = ev.get("response") or {}
            self.usage = resp.get("usage") or {}
            self.model = resp.get("model") or self.model
            self.status = resp.get("status") or etype.rsplit(".", 1)[-1]
            err = resp.get("error") or {}
            if isinstance(err, dict) and err.get("message"):
                self.error = str(err["message"])
        return bool(self.terminal_event)


class LLMOpenaiResponsesMixin:
    """Responses-API provider methods, for any endpoint speaking the format."""

    def _responses_endpoint_path(self, base_url: str) -> str:
        return responses_endpoint(base_url)

    def _build_responses_body(self, messages, model, temperature, max_tokens,
                              tools, *, call_user_id: str = "",
                              call_conversation_id: str = "",
                              allow_vision: bool = True) -> Dict[str, Any]:
        api_messages = self._build_openai_messages(
            messages, user_id=call_user_id,
            conversation_id=call_conversation_id, allow_vision=allow_vision)
        instructions, items = build_responses_input(api_messages)

        body: Dict[str, Any] = {
            "model": model,
            "input": items,
            "stream": True,
        }
        if instructions:
            body["instructions"] = instructions
        if temperature is not None:
            body["temperature"] = temperature
        if max_tokens > 0:
            # One name here, unlike chat/completions where it depends on the
            # model generation.
            body["max_output_tokens"] = max_tokens
        _re = self.reasoning_effort or None
        if _re:
            body["reasoning"] = {"effort": _re}
        if tools:
            body["tools"] = [
                {"type": "function", "name": t.name,
                 "description": t.description, "parameters": t.parameters}
                for t in tools
            ]
        extra_body = self.extra_body
        if extra_body:
            body.update(extra_body)
        return body

    def _stream_openai_responses(self, messages, model, temperature, max_tokens,
                                 tools, callback, thinking_callback=None, *,
                                 call_user_id: str = "",
                                 call_conversation_id: str = ""):
        """Stream one turn from a Responses-API endpoint."""
        from core.llm_client import LLMClientError, LLMResponse, LLMToolCall
        from tasks.ai.agent_exceptions import AgentCancelled

        if getattr(self, "_abort", None) and self._abort.is_set():
            raise AgentCancelled()

        base_url = self.base_url
        body = self._build_responses_body(
            messages, model, temperature, max_tokens, tools,
            call_user_id=call_user_id,
            call_conversation_id=call_conversation_id)

        parsed = urlparse(base_url)
        full_path = _request_path(base_url, self._responses_endpoint_path(base_url))
        safe_base_url = re.sub(r"(/relay-proxy/[^/]+/)[^/]+/", r"\1<token>/", base_url or "")

        if parsed.scheme == "https":
            from core.relay_proxy_url import relay_proxy_ssl_context
            ctx = relay_proxy_ssl_context(base_url)
            conn = http.client.HTTPSConnection(
                parsed.hostname, parsed.port, timeout=self.timeout, context=ctx)
        else:
            conn = http.client.HTTPConnection(
                parsed.hostname, parsed.port, timeout=self.timeout)

        try:
            self._active_http_conn = conn
            json_body = json.dumps(body).encode("utf-8")
            headers = {
                "Content-Type": "application/json",
                "Content-Length": str(len(json_body)),
                "Authorization": f"Bearer {self.api_key}",
            }
            logger.info(
                "Responses stream request model=%s host=%s path=%s base_url=%s "
                "items=%d body_bytes=%d",
                model, parsed.hostname, full_path, safe_base_url,
                len(body.get("input") or []), len(json_body))
            conn.request("POST", full_path, body=json_body, headers=headers)
            response = conn.getresponse()
            logger.info("Responses stream response status=%s model=%s base_url=%s",
                        response.status, model, safe_base_url)

            if response.status >= 400:
                error_body = response.read().decode("utf-8", errors="replace")
                raise LLMClientError(
                    f"LLM API error {response.status}: {error_body[:500]}")

            state = _StreamState()
            state.model = model
            buffer = ""
            terminal = False
            while True:
                if getattr(self, "_abort", None) and self._abort.is_set():
                    raise AgentCancelled()
                chunk = response.read(4096)
                if not chunk:
                    break
                buffer += chunk.decode("utf-8", errors="replace")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    # `event:` restates the `type` already inside the payload,
                    # so the data line alone is enough and stays correct if a
                    # server omits the event name.
                    if not line.startswith("data: "):
                        continue
                    try:
                        terminal = state.feed(json.loads(line[6:]))
                    except json.JSONDecodeError:
                        logger.debug("undecodable Responses event", exc_info=True)
                    if terminal:
                        break
                if terminal:
                    break

            if not state.terminal_event:
                raise LLMClientError(
                    "Responses API stream ended before a terminal event")

            if state.status == "failed":
                raise LLMClientError(
                    f"Responses API failed: {state.error or 'no reason given'}")

            from core.tool_json import parse_tool_arguments
            tool_calls = []
            for key in state.order:
                slot = state.calls[key]
                if not slot.get("name"):
                    continue
                tool_calls.append(LLMToolCall(
                    id=slot.get("call_id") or key,
                    name=slot["name"],
                    arguments=parse_tool_arguments(
                        slot.get("arguments") or "",
                        tool_name=slot["name"],
                        provider="openai-responses", log=logger)))

            content = "".join(state.text)
            thinking = "".join(state.reasoning)
            # Block-level callbacks fire once each at end of stream, the same
            # contract the chat/completions path gives the UI.
            if thinking and thinking_callback:
                thinking_callback(thinking)
            if content and callback:
                callback(content)

            usage = state.usage or {}
            tokens_in = usage.get("input_tokens", 0) or 0
            tokens_out = usage.get("output_tokens", 0) or 0
            cached = ((usage.get("input_tokens_details") or {})
                      .get("cached_tokens", 0) or 0)
            if not tokens_in:
                tokens_in = count_messages_tokens(messages)
            if not tokens_out:
                tokens_out = len(content) // 4
            total_tokens = usage.get("total_tokens", 0) or 0
            # Cache hits are inside input_tokens, as in chat/completions, so
            # the billable miss is the difference -- charging both rates for a
            # hit is the bug this split exists to avoid.
            billable_in = max(0, tokens_in - cached)

            logger.info(
                "Responses stream completed model=%s status=%s text_chars=%d "
                "thinking_chars=%d tool_calls=%d reasoning_tokens=%s base_url=%s",
                state.model, state.status or "completed", len(content),
                len(thinking), len(tool_calls),
                (usage.get("output_tokens_details") or {}).get("reasoning_tokens", 0),
                safe_base_url)

            return LLMResponse(
                content=content,
                model=state.model or model,
                # `incomplete` is the API's max_output_tokens truncation, which
                # the agent loop reads as a length stop.
                finish_reason=("length" if state.status == "incomplete"
                               else ("tool_calls" if tool_calls else "stop")),
                tool_calls=tool_calls,
                tokens_in=billable_in,
                tokens_out=tokens_out,
                total_tokens=total_tokens,
                thinking=thinking,
                cache_read_tokens=cached,
            )
        finally:
            self._active_http_conn = None
            conn.close()


def _request_path(base_url: str, endpoint_path: str) -> str:
    from core.llm_providers.cli_shared import request_path
    return request_path(base_url, endpoint_path)
