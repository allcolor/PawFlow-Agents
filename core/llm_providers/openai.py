"""LLM provider mixin -- OpenAI-compatible API."""

import json
import http.client
import logging
import re
import base64
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from core.token_counter import count_messages_tokens

logger = logging.getLogger(__name__)

# The only finish_reason values the chat-completions spec defines. A stream
# that ends on anything else did not end on a model decision: gateways report
# their own upstream failures in this field (opencode zen sends
# "network_error"), and an empty value means the connection simply stopped.
VALID_FINISH_REASONS = frozenset({
    "stop", "length", "tool_calls", "content_filter", "function_call",
})

# A few OpenAI-compatible gateways use their own labels for standard safety
# stops, or put an upstream transport failure in finish_reason despite the HTTP
# response itself being 200. Keep this list deliberately narrow: unknown
# non-streaming finish reasons remain untouched for compatibility.
_CONTENT_FILTER_FINISH_REASONS = frozenset({"sensitive", "safety"})
_PROVIDER_ERROR_FINISH_REASONS = frozenset({
    "network_error", "server_error", "api_error", "timeout",
})


def _finish_reason_key(value: Any) -> str:
    """Return a comparison key without changing an unknown provider value."""
    if not isinstance(value, str):
        return ""
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def _normalize_finish_reason(value: Any) -> Any:
    """Map known safety aliases to the standard OpenAI finish reason."""
    if _finish_reason_key(value) in _CONTENT_FILTER_FINISH_REASONS:
        return "content_filter"
    return value or ""


class LLMOpenaiMixin:
    """OpenAI provider methods: complete, stream, message building."""

    @staticmethod
    def _chat_completions_endpoint(base_url: str) -> str:
        """Return the chat-completions suffix to append to base_url's path.

        Most OpenAI-compatible bases already carry their own API version
        segment (.../v1, .../v4, .../compatible-mode/v1). Only fall back to
        the default /v1 when the base has no version of its own, so e.g.
        z.ai's https://api.z.ai/api/paas/v4 -> /api/paas/v4/chat/completions
        instead of /api/paas/v4/v1/chat/completions.
        """
        path = urlparse(base_url or "").path.rstrip("/")
        if path.endswith("/chat/completions"):
            return ""  # caller already supplied the full endpoint path
        # Match a trailing version segment, including suffixed ones like
        # /v1beta or /v2alpha (Gemini-compatible gateways), so we never
        # re-append /v1 on top of an existing version.
        if re.search(r"/v\d+[a-z]*$", path):
            return "/chat/completions"
        return "/v1/chat/completions"

    def _openai_endpoint_path(self, base_url: str, model: str) -> str:
        """Chat-completions path for this provider's dialect."""
        from core.llm_providers.openai_dialects import DIALECTS, endpoint_path
        provider = getattr(self, "provider", "openai")
        if provider in DIALECTS:
            return endpoint_path(provider, base_url, model, self._cfg)
        return self._chat_completions_endpoint(base_url)

    def _openai_auth_headers(self) -> Dict[str, str]:
        """Authentication headers for this provider's dialect."""
        from core.llm_providers.openai_dialects import DIALECTS, auth_headers
        provider = getattr(self, "provider", "openai")
        # The credential's SOURCE (static key, OAuth pool, or nothing) is
        # resolved once in bearer_credential; the header SHAPE stays each
        # dialect's business.
        credential = self.bearer_credential()
        if provider == "omniroute":
            from core.llm_providers.omniroute import auth_headers
            return auth_headers(
                credential, self._cfg("omniroute_auth_mode", ""))
        if provider in DIALECTS:
            return auth_headers(provider, credential)
        if not credential:
            # auth_mode=none: send no Authorization header at all rather than
            # an empty bearer, which some gateways reject outright.
            return {}
        return {"Authorization": f"Bearer {credential}"}

    def _openai_provider_headers(self, conversation_id: str = "") -> Dict[str, str]:
        """Identity, operator ``extra_headers`` and gateway-dialect headers."""
        headers = self.request_headers(conversation_id)
        if getattr(self, "provider", "openai") != "omniroute":
            return headers
        from core.llm_providers.omniroute import request_headers
        headers.update(request_headers(self._config_ref))
        return headers

    def _openai_provider_metadata(self, response_headers=(), sse_comments=()):
        if getattr(self, "provider", "openai") != "omniroute":
            return {}
        from core.llm_providers.omniroute import parse_response_metadata
        return parse_response_metadata(response_headers, sse_comments)

    @staticmethod
    def _openai_cache_token_counts(usage: Dict[str, Any]) -> tuple[int, int, int]:
        """Return input misses, cache hits, and the total prompt token count.

        DeepSeek exposes an explicit top-level hit/miss split. OpenAI exposes
        only cached tokens nested under prompt_tokens_details, so misses must
        be derived from the total. Prefer the explicit split when both shapes
        are present to avoid billing the same prompt tokens twice.
        """
        prompt_total = max(0, int(usage.get("prompt_tokens", 0) or 0))
        has_hit = "prompt_cache_hit_tokens" in usage
        has_miss = "prompt_cache_miss_tokens" in usage
        if has_hit or has_miss:
            cache_hits = max(0, int(usage.get("prompt_cache_hit_tokens", 0) or 0))
            input_misses = max(0, int(usage.get("prompt_cache_miss_tokens", 0) or 0))
            if not has_hit:
                cache_hits = max(0, prompt_total - input_misses)
            if not has_miss:
                input_misses = max(0, prompt_total - cache_hits)
            return input_misses, cache_hits, prompt_total or input_misses + cache_hits

        details = usage.get("prompt_tokens_details") or {}
        cache_hits = max(0, int(details.get("cached_tokens", 0) or 0))
        return max(0, prompt_total - cache_hits), cache_hits, prompt_total

    def _stream_openai(self, messages, model, temperature, max_tokens, tools, callback,
                        thinking_callback=None, *,
                        call_user_id: str = "",
                        call_conversation_id: str = ""):
        """OpenAI streaming: reads SSE chunks from the API."""
        from core.llm_client import LLMClientError, LLMResponse, LLMToolCall
        from tasks.ai.agent_exceptions import AgentCancelled

        if getattr(self, "_abort", None) and self._abort.is_set():
            raise AgentCancelled()

        api_messages = self._build_openai_messages(
            messages,
            user_id=call_user_id,
            conversation_id=call_conversation_id)
        body = {
            "model": model,
            "messages": api_messages,
            "stream": True,
        }
        if temperature is not None:
            body["temperature"] = temperature
        # reasoning_effort for reasoning models (gpt-5*, o-series)
        _re = self.reasoning_effort or None
        if _re:
            body["reasoning_effort"] = _re
        base_url = self.base_url
        if max_tokens > 0:
            tokens_key = self._openai_tokens_key(model, base_url)
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
        if not base_url or "api.openai.com" in base_url:
            body["stream_options"] = {"include_usage": True}
        extra_body = self.extra_body
        if extra_body:
            body.update(extra_body)

        parsed = urlparse(base_url)
        host = parsed.hostname
        port = parsed.port
        from core.llm_providers.cli_shared import request_path
        full_path = request_path(
            base_url, self._openai_endpoint_path(base_url, model))
        safe_base_url = re.sub(r"(/relay-proxy/[^/]+/)[^/]+/", r"\1<token>/", base_url or "")

        if parsed.scheme == "https":
            from core.relay_proxy_url import relay_proxy_ssl_context
            ctx = relay_proxy_ssl_context(base_url)
            conn = http.client.HTTPSConnection(host, port, timeout=self.timeout, context=ctx)
        else:
            conn = http.client.HTTPConnection(host, port, timeout=self.timeout)

        try:
            self._active_http_conn = conn
            json_body = json.dumps(body).encode("utf-8")
            headers = {
                "Content-Type": "application/json",
                "Content-Length": str(len(json_body)),
            }
            headers.update(self._openai_auth_headers())
            headers.update(self._openai_provider_headers(call_conversation_id))
            logger.info(
                "OpenAI stream request model=%s host=%s port=%s path=%s base_url=%s body_bytes=%d",
                model, host, port, full_path, safe_base_url, len(json_body),
            )
            conn.request("POST", full_path, body=json_body, headers=headers)
            response = conn.getresponse()
            logger.info(
                "OpenAI stream response status=%s reason=%s model=%s base_url=%s",
                response.status, getattr(response, "reason", ""), model, safe_base_url,
            )

            if response.status >= 400:
                try:
                    error_body = response.read().decode("utf-8")
                except Exception as exc:
                    raise LLMClientError(
                        f"LLM API error {response.status}: failed to read error body: {type(exc).__name__}: {exc}") from exc
                if self._is_vision_rejected_error(error_body):
                    logger.warning(
                        "OpenAI-compatible endpoint rejected image input; retrying without native vision blocks")
                    conn.close()
                    if parsed.scheme == "https":
                        conn = http.client.HTTPSConnection(host, port, timeout=self.timeout, context=ctx)
                    else:
                        conn = http.client.HTTPConnection(host, port, timeout=self.timeout)
                    self._active_http_conn = conn
                    body["messages"] = self._build_openai_messages(
                        messages,
                        user_id=call_user_id,
                        conversation_id=call_conversation_id,
                        allow_vision=False)
                    json_body = json.dumps(body).encode("utf-8")
                    headers["Content-Length"] = str(len(json_body))
                    conn.request("POST", full_path, body=json_body, headers=headers)
                    response = conn.getresponse()
                    if response.status < 400:
                        pass
                    else:
                        error_body = response.read().decode("utf-8")
                        from core.llm_failure_classifier import classify_http_error
                        raise classify_http_error(
                            response.status, headers=dict(response.getheaders()),
                            body=error_body, provider=getattr(self, "provider", ""),
                            model=model)
                else:
                    from core.llm_failure_classifier import classify_http_error
                    raise classify_http_error(
                        response.status, headers=dict(response.getheaders()),
                        body=error_body, provider=getattr(self, "provider", ""),
                        model=model)

            # Parse SSE stream
            content_parts: List[str] = []
            reasoning_parts: List[str] = []
            tool_calls_map: Dict[int, Dict] = {}  # index -> {id, name, arguments_str}
            finish_reason = ""
            resp_model = model
            usage_data: Dict[str, Any] = {}
            provider_comments: List[str] = []
            getheaders = getattr(response, "getheaders", None)
            response_headers = tuple(getheaders()) if callable(getheaders) else ()

            buffer = ""
            saw_done = False
            while True:
                if getattr(self, "_abort", None) and self._abort.is_set():
                    raise AgentCancelled()
                try:
                    chunk = response.read(4096)
                except Exception:
                    # abort() closes the socket mid-read; the resulting
                    # connection error must surface as a clean interruption
                    # (AgentCancelled), not as a raw AttributeError/OSError
                    # that the agent loop turns into an error turn.
                    if getattr(self, "_abort", None) and self._abort.is_set():
                        raise AgentCancelled()
                    raise
                if getattr(self, "_abort", None) and self._abort.is_set():
                    raise AgentCancelled()
                if not chunk:
                    break
                buffer += chunk.decode("utf-8", errors="replace")

                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith(":"):
                        provider_comments.append(line)
                        continue
                    if line == "data: [DONE]":
                        saw_done = True
                        break
                    if line.startswith("data: "):
                        if getattr(self, "_abort", None) and self._abort.is_set():
                            raise AgentCancelled()
                        try:
                            data = json.loads(line[6:])

                            # Final usage chunk (stream_options.include_usage)
                            if data.get("usage"):
                                usage_data = data["usage"]

                            choices = data.get("choices", [])
                            if not choices:
                                continue
                            choice0 = choices[0] or {}
                            # Some gateways (OpenCode Go serving GLM) send
                            # explicit JSON nulls for absent fields; treat
                            # them exactly like missing keys.
                            delta = choice0.get("delta") or {}
                            fr = choice0.get("finish_reason")
                            if fr:
                                finish_reason = _normalize_finish_reason(fr)
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
                            for tc_delta in delta.get("tool_calls") or []:
                                if not isinstance(tc_delta, dict):
                                    continue
                                idx = tc_delta.get("index") or 0
                                fn = tc_delta.get("function") or {}
                                if idx not in tool_calls_map:
                                    tool_calls_map[idx] = {
                                        "id": tc_delta.get("id") or "",
                                        "name": fn.get("name") or "",
                                        "arguments_str": "",
                                    }
                                tc = tool_calls_map[idx]
                                if tc_delta.get("id"):
                                    tc["id"] = tc_delta["id"]
                                if fn.get("name"):
                                    tc["name"] = fn["name"]
                                tc["arguments_str"] += fn.get("arguments") or ""

                        except (json.JSONDecodeError, IndexError, KeyError):
                            pass

                # [DONE] is the protocol terminator. Do not wait for the peer
                # to close an already-complete HTTP response: some relays keep
                # that connection alive for reuse.
                if saw_done:
                    break

            # Build tool calls
            tool_calls = []
            from core.tool_json import parse_tool_arguments
            for idx in sorted(tool_calls_map.keys()):
                tc = tool_calls_map[idx]
                if not tc.get("arguments_str"):
                    logger.warning(
                        "[openai] streamed tool call for %s had empty arguments",
                        tc.get("name") or "<unknown>",
                    )
                args = parse_tool_arguments(
                    tc["arguments_str"],
                    tool_name=tc["name"],
                    provider="openai",
                    log=logger,
                )
                tool_calls.append(LLMToolCall(id=tc["id"], name=tc["name"], arguments=args))

            content = "".join(content_parts)
            thinking = "".join(reasoning_parts)

            logger.info(
                "OpenAI stream completed model=%s finish_reason=%s text_chars=%d thinking_chars=%d tool_calls=%d base_url=%s",
                resp_model, finish_reason, len(content), len(thinking), len(tool_calls), safe_base_url,
            )

            # Two ways an OpenAI-compatible stream fails behind a 200, both of
            # which used to be accepted as a finished answer.
            #
            # 1. Silence. A clean EOF is indistinguishable from a normal end:
            #    read() just returns b"" and the loop exits with no exception.
            #    SSE has two legitimate end-of-stream signals -- a finish_reason
            #    on choice 0 and the `data: [DONE]` sentinel -- and a gateway
            #    that drops the connection mid-answer sends neither. The turn
            #    was then released on a response the user never saw.
            # 2. An in-band error. opencode's zen gateway reports its own
            #    upstream death as finish_reason="network_error" on an
            #    otherwise well-formed stream. A finish_reason the spec does
            #    not define is a provider failure, not a model decision.
            #
            # A stream that sent [DONE] with no finish_reason is NEITHER: it is
            # well formed and the provider simply had nothing to say. That is an
            # empty answer, not a transport failure, and it is left alone here.
            #
            # Both real cases are retryable: there is no status code to key off
            # because the response was a 200 that stopped early.
            truncated = not finish_reason and not saw_done
            bad_finish = bool(finish_reason) and finish_reason not in VALID_FINISH_REASONS
            if truncated or bad_finish:
                if truncated:
                    category = "stream_truncated"
                    detail = "stream ended with neither finish_reason nor [DONE]"
                else:
                    category = "provider_stream_error"
                    detail = f"provider ended the stream with finish_reason={finish_reason!r}"
                logger.warning(
                    "OpenAI stream did not terminate cleanly model=%s %s "
                    "(text_chars=%d thinking_chars=%d tool_calls=%d) base_url=%s",
                    resp_model, detail, len(content), len(thinking),
                    len(tool_calls), safe_base_url,
                )
                from core._llm_types import LLMCallError
                raise LLMCallError(
                    f"Truncated LLM stream: {detail}",
                    category=category, origin="provider", provider_status=0,
                    retryable=True, retry_after_seconds=0,
                    provider=getattr(self, "provider", ""), model=resp_model,
                )

            # Use real usage from API if available, else estimate.
            input_usage_native = any(key in usage_data for key in (
                "prompt_tokens",
                "prompt_cache_hit_tokens",
                "prompt_cache_miss_tokens",
            ))
            tokens_in, cached_tokens, prompt_tokens_total = (
                self._openai_cache_token_counts(usage_data))
            tokens_out = usage_data.get("completion_tokens", 0)
            total_tokens = usage_data.get("total_tokens", 0)
            if not tokens_in and not cached_tokens:
                tokens_in = count_messages_tokens(messages)
                prompt_tokens_total = tokens_in
            if not tokens_out:
                tokens_out = len(content) // 4

            # Cache logging. The shared parser has already split billable miss
            # tokens from cache hits for both OpenAI and DeepSeek response shapes.
            if cached_tokens > 0:
                _hit_pct = (cached_tokens / prompt_tokens_total * 100) if prompt_tokens_total else 0
                logger.info("OpenAI-compatible prompt cache: %d cached of %d prompt tokens (%.0f%% hit)",
                            cached_tokens, prompt_tokens_total, _hit_pct)
            elif prompt_tokens_total > 1024:
                logger.info("OpenAI-compatible prompt cache: MISS — %d prompt tokens, 0 cached", prompt_tokens_total)

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
                input_usage_native=input_usage_native,
                provider_metadata=self._openai_provider_metadata(
                    response_headers, provider_comments),
            )
        finally:
            self._active_http_conn = None
            conn.close()

    @staticmethod
    def _filestore_link_text(part: dict) -> Optional[dict]:
        file_id = str(part.get("file_id") or "").strip()
        if not file_id:
            return None
        filename = str(part.get("filename") or "image").strip() or "image"
        return {
            "type": "text",
            "text": f"Attached image: fs://filestore/{file_id}/{filename}",
        }

    @staticmethod
    def _image_text_link(part: dict) -> Optional[dict]:
        fs_link = LLMOpenaiMixin._filestore_link_text(part)
        if fs_link:
            return fs_link
        if part.get("type") == "image_url":
            image_url = part.get("image_url") or {}
            url = image_url.get("url", "") if isinstance(image_url, dict) else str(image_url or "")
            if url and not url.startswith("data:"):
                return {"type": "text", "text": f"Attached image: {url}"}
        return None

    @staticmethod
    def _vision_image_part(part: dict, *, user_id: str = "",
                           conversation_id: str = "") -> Optional[dict]:
        """Return a native vision part for the current turn.

        Base64 belongs in the provider vision payload for images the model must
        inspect now. It must not be persisted into text context for old turns.
        """
        if part.get("type") == "image_ref":
            file_id = str(part.get("file_id") or "").strip()
            if not file_id:
                return None
            from core.file_store import FileStore
            _fname, data, content_type = FileStore.instance().get_required(
                file_id, user_id=user_id, conversation_id=conversation_id)
            mime = part.get("mime_type", content_type) or "image/png"
            return {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
                },
            }
        if part.get("type") == "image_url":
            image_url = part.get("image_url") or {}
            url = image_url.get("url", "") if isinstance(image_url, dict) else str(image_url or "")
            if url:
                return part
        return None

    @staticmethod
    def _is_vision_rejected_error(error_text: str) -> bool:
        text = (error_text or "").lower()
        markers = (
            "support image input",
            "image input",
            "image inputs",
            "image_url",
            "vision",
            "multimodal",
        )
        return any(marker in text for marker in markers)

    def _build_openai_messages(self, messages, *,
                                user_id: str, conversation_id: str,
                                allow_vision: bool = True,
                                carry_reasoning: bool = False) -> List[Dict[str, Any]]:
        """Convert LLMMessage list to OpenAI API message format.

        Messages are regrouped first so the split (assistant text / assistant
        tool_calls) pair emitted by agent_core.persist is fused into the
        single assistant message OpenAI expects (content + tool_calls).

        Images are native vision only for the latest user message with image
        attachments, and tool-result image messages, when vision is enabled.
        Older images, or all images when vision is disabled/rejected, become
        links in text context. Raw image payloads are only emitted on those
        current native-vision paths, never as historical text context.
        """
        from core.llm_message_regroup import regroup_split_assistant_messages
        from core.llm_tool_sequence import repair_tool_sequence
        messages = regroup_split_assistant_messages(messages)
        # Enforce the strict provider protocol: every assistant tool_calls
        # block must be immediately followed by its tool results, in call
        # order. A cancel/compact/rewind can leave results before their
        # assistant message, duplicated, orphaned, interleaved with unrelated
        # messages, or missing — any of which the provider rejects with a 400
        # ("insufficient tool messages following tool_calls message"). Rebuild
        # the list so the sequence is valid by construction; unanswered calls
        # (preempted turn) get a synthetic "[Result unavailable...]" result.
        messages, _tool_seq_changed = repair_tool_sequence(
            messages, conversation_id)
        if _tool_seq_changed:
            logger.warning(
                "build_openai_messages: repaired tool-call ordering for "
                "strict provider sequence (conv=%s)", conversation_id)
        # Log multipart content for debugging
        _img_count = 0
        for m in messages:
            if isinstance(m.content, list):
                for p in m.content:
                    if p.get("type") in ("image_url", "image_ref"):
                        _img_count += 1
        if _img_count:
            logger.info("build_openai_messages: %d image part(s) in context", _img_count)

        vision_enabled = bool(self.supports_vision and allow_vision)
        last_user_idx = -1
        for idx, msg in enumerate(messages):
            if msg.role == "user":
                last_user_idx = idx

        # Sanitize: collect tool_call IDs from assistant messages, drop orphan tool messages
        valid_tc_ids: set = set()
        for m in messages:
            if m.role == "assistant" and m.tool_calls:
                for tc in m.tool_calls:
                    valid_tc_ids.add(tc.id)

        api_messages = []
        # Images attached to tool results cannot live inside the OpenAI tool
        # message (string content only). They become user messages, but they
        # MUST be emitted after the whole tool block, never between tool
        # results: strict providers reject an assistant tool_calls block whose
        # tool messages are interrupted by a user message (HTTP 400
        # "insufficient tool messages following tool_calls message").
        _pending_image_users = []
        for idx, m in enumerate(messages):
            if _pending_image_users and m.role != "tool":
                api_messages.extend(_pending_image_users)
                _pending_image_users = []
            is_current_image_user = vision_enabled and idx == last_user_idx
            if m.role == "tool":
                # Skip orphan tool messages (no matching assistant tool_call)
                if m.tool_call_id and m.tool_call_id not in valid_tc_ids:
                    continue
                api_messages.append({
                    "role": "tool",
                    "content": m.text_content if isinstance(m.content, list) else m.content,
                    "tool_call_id": m.tool_call_id or "",
                })
                # OpenAI tool messages only support string content. Image
                # parts become deferred user messages (flushed after the tool
                # block) so assistant(tool_calls) -> tool* stays contiguous.
                if isinstance(m.content, list):
                    img_parts = []
                    for p in m.content:
                        if p.get("type") in ("image_url", "image_ref"):
                            if vision_enabled:
                                vision_part = self._vision_image_part(
                                    p, user_id=user_id,
                                    conversation_id=conversation_id)
                                if vision_part:
                                    link_part = self._image_text_link(p)
                                    if link_part:
                                        img_parts.append(link_part)
                                    img_parts.append(vision_part)
                                    continue
                            link_part = self._image_text_link(p)
                            if link_part:
                                img_parts.append(link_part)
                    if img_parts:
                        _pending_image_users.append({
                            "role": "user",
                            "content": [{"type": "text", "text": "(image from tool result)"}] + img_parts,
                        })
            elif m.role == "assistant" and m.tool_calls:
                content = m.content
                if isinstance(content, list):
                    content = m.text_content or None
                msg: Dict[str, Any] = {"role": "assistant", "content": content or None}
                # Only for the Responses builder, which reads it off the
                # normalised message rather than re-deriving the turn from
                # LLMMessage a second time. chat/completions must never see it:
                # an unknown key inside a message is a 400, not an ignored
                # field, so the caller has to ask for it explicitly.
                if carry_reasoning and getattr(m, "reasoning_item", ""):
                    msg["reasoning_item"] = m.reasoning_item
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
                        if is_current_image_user:
                            vision_part = self._vision_image_part(
                                part, user_id=user_id,
                                conversation_id=conversation_id)
                            if vision_part:
                                link_part = self._image_text_link(part)
                                if link_part:
                                    parts.append(link_part)
                                parts.append(vision_part)
                                continue
                        link_part = self._image_text_link(part)
                        if link_part:
                            parts.append(link_part)
                    elif pt == "image_url":
                        if is_current_image_user:
                            vision_part = self._vision_image_part(
                                part, user_id=user_id,
                                conversation_id=conversation_id)
                            if vision_part:
                                parts.append(vision_part)
                                continue
                        link_part = self._image_text_link(part)
                        if link_part:
                            parts.append(link_part)
                    elif pt == "file_ref":
                        parts.append({"type": "text", "text": f"[file: {part.get('filename', '?')}]"})
                    else:
                        parts.append(part)
                api_messages.append({"role": m.role, "content": parts if parts else m.text_content})
            else:
                plain: Dict[str, Any] = {"role": m.role, "content": m.content}
                # An assistant turn that reasoned and then simply answered also
                # owns its reasoning items; only the tool-call branch above was
                # carrying them, which broke continuity on every turn that
                # called nothing.
                if (carry_reasoning and m.role == "assistant"
                        and getattr(m, "reasoning_item", "")):
                    plain["reasoning_item"] = m.reasoning_item
                api_messages.append(plain)
        if _pending_image_users:
            api_messages.extend(_pending_image_users)
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
        from tasks.ai.agent_exceptions import AgentCancelled

        if getattr(self, "_abort", None) and self._abort.is_set():
            raise AgentCancelled()

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
        base_url = self.base_url
        if max_tokens > 0:
            tokens_key = self._openai_tokens_key(model, base_url)
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
        extra_body = self.extra_body
        if extra_body:
            body.update(extra_body)

        try:
            data = self._http_post(
                self._openai_endpoint_path(base_url, model),
                body,
                headers={**self._openai_auth_headers(),
                         **self._openai_provider_headers(call_conversation_id),
                         "Content-Type": "application/json"},
                base_url=base_url,
            )
        except Exception as exc:
            if not self._is_vision_rejected_error(str(exc)):
                raise
            logger.warning(
                "OpenAI-compatible endpoint rejected image input; retrying without native vision blocks")
            body["messages"] = self._build_openai_messages(
                messages,
                user_id=call_user_id,
                conversation_id=call_conversation_id,
                allow_vision=False)
            data = self._http_post(
                self._openai_endpoint_path(base_url, model),
                body,
                headers={**self._openai_auth_headers(),
                         **self._openai_provider_headers(call_conversation_id),
                         "Content-Type": "application/json"},
                base_url=base_url,
            )
        # Explicit JSON nulls (OpenCode Go serving GLM) read as absent fields.
        choice = (data.get("choices") or [{}])[0] or {}
        usage = data.get("usage") or {}
        message = choice.get("message") or {}
        finish_reason = _normalize_finish_reason(choice.get("finish_reason") or "")

        # A successful HTTP envelope can still carry an upstream failure. This
        # is the non-streaming equivalent of the in-band stream error handled
        # above. Reject only known transport labels so compatible providers'
        # custom successful reasons continue to behave exactly as before.
        if _finish_reason_key(finish_reason) in _PROVIDER_ERROR_FINISH_REASONS:
            from core._llm_types import LLMCallError
            raise LLMCallError(
                "Provider returned an unsuccessful completion: "
                f"finish_reason={finish_reason!r}",
                category="provider_stream_error", origin="provider",
                provider_status=0, retryable=True, retry_after_seconds=0,
                provider=getattr(self, "provider", ""),
                model=data.get("model", model),
            )

        # Parse tool calls if present
        tool_calls = []
        from core.tool_json import parse_tool_arguments
        for tc in message.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            func = tc.get("function") or {}
            raw_args = func.get("arguments")
            if raw_args is None:
                logger.warning(
                    "[openai] tool call for %s omitted arguments field",
                    func.get("name") or "<unknown>",
                )
            elif raw_args == "":
                logger.warning(
                    "[openai] tool call for %s had empty arguments",
                    func.get("name") or "<unknown>",
                )
            args = parse_tool_arguments(
                raw_args,
                tool_name=func.get("name", ""),
                provider="openai",
                log=logger,
            )
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

        # Split billable misses from cache hits before cost tracking sees the
        # response. DeepSeek's explicit counters take precedence over the
        # nested OpenAI-compatible cached-token detail when both are present.
        _input_miss_tokens, cached_tokens, _prompt_tokens = (
            self._openai_cache_token_counts(usage))
        if cached_tokens > 0:
            _hit_pct = (cached_tokens / _prompt_tokens * 100) if _prompt_tokens else 0
            logger.info("OpenAI-compatible prompt cache: %d cached of %d prompt tokens (%.0f%% hit)",
                        cached_tokens, _prompt_tokens, _hit_pct)
        elif _prompt_tokens > 1024:
            logger.info("OpenAI-compatible prompt cache: MISS — %d prompt tokens, 0 cached", _prompt_tokens)

        return LLMResponse(
            content=_content,
            model=data.get("model", model),
            tokens_in=_input_miss_tokens,
            tokens_out=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
            finish_reason=finish_reason,
            tool_calls=tool_calls,
            thinking=reasoning,
            raw=data,
            cache_read_tokens=cached_tokens,
            input_usage_native=any(key in usage for key in (
                "prompt_tokens",
                "prompt_cache_hit_tokens",
                "prompt_cache_miss_tokens",
            )),
            provider_metadata=self._openai_provider_metadata(
                getattr(self, "_last_http_response_headers", ())),
        )
