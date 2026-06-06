"""OpenAI-compatible image and video generation services.

These services reuse a configured ``llmConnection`` whose provider is
``openai``. The connection supplies the API key, base URL, default model, and
timeout; the media service supplies the media-specific protocol and parsing.
"""

from __future__ import annotations

import base64
import copy
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, Optional

from core import ServiceError, ServiceFactory
from services.base_image_generation import BaseImageGenerationService
from services.base_video_generation import BaseVideoGenerationService

logger = logging.getLogger(__name__)

_IMAGE_URL_RE = re.compile(r"https?://[^\s\"')>]+\.(?:png|jpe?g|webp|gif)(?:\?[^\s\"')>]*)?", re.I)
_VIDEO_URL_RE = re.compile(r"https?://[^\s\"')>]+\.(?:mp4|webm|mov|m4v)(?:\?[^\s\"')>]*)?", re.I)
_DATA_URL_RE = re.compile(r"data:([^;]+);base64,([A-Za-z0-9+/=\s]+)")


def _truthy(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _json_object(value, *, field_name: str) -> Dict[str, Any]:
    if value in (None, ""):
        return {}
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ServiceError(f"{field_name} must be a JSON object") from exc
        if isinstance(parsed, dict):
            return parsed
    raise ServiceError(f"{field_name} must be a JSON object")


def _first_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                if isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif isinstance(item.get("content"), str):
                    parts.append(item["content"])
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    return ""


def _walk_json(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


class _OpenAICompatibleMediaMixin:
    """Shared helpers for OpenAI-compatible media services."""

    MEDIA_KIND = "media"

    def _init_common(self):
        self.llm_service = (self.config.get("llm_service", "") or "").strip()
        self.protocol = (self.config.get("protocol", "auto") or "auto").strip().lower()
        self.model = (self.config.get("model", "") or "").strip()
        self.timeout = int(self.config.get("timeout", 300) or 300)
        self.poll_interval = int(self.config.get("poll_interval", 5) or 5)
        self.max_tokens = int(self.config.get("max_tokens", 0) or 0)
        self.max_output_tokens = int(self.config.get("max_output_tokens", 0) or 0)
        self.extra_body = _json_object(self.config.get("extra_body", {}), field_name="extra_body")
        self.extra_headers = _json_object(self.config.get("extra_headers", {}), field_name="extra_headers")
        self._runtime_user_id = ""
        self._runtime_conversation_id = ""
        self._runtime_agent_name = ""

    def set_runtime_context(self, user_id: str = "", conversation_id: str = "",
                            agent_name: str = "", **_: object):
        self._runtime_user_id = user_id or ""
        self._runtime_conversation_id = conversation_id or ""
        self._runtime_agent_name = agent_name or ""

    def _create_connection(self):
        if not self.llm_service:
            raise ServiceError(f"llm_service is required for OpenAI-compatible {self.MEDIA_KIND} generation")
        return {"ready": True}

    def _close_connection(self):
        pass

    def _resolve_llm_service(self):
        from core.service_registry import ServiceRegistry
        reg = ServiceRegistry.get_instance()
        svc_def = reg.resolve_definition(
            self.llm_service,
            user_id=self._runtime_user_id,
            conv_id=self._runtime_conversation_id,
        )
        if svc_def is None:
            raise ServiceError(f"LLM service '{self.llm_service}' not found")
        if getattr(svc_def, "service_type", "") != "llmConnection":
            raise ServiceError(f"Service '{self.llm_service}' is not an llmConnection service")
        svc = reg.resolve(
            self.llm_service,
            user_id=self._runtime_user_id,
            conv_id=self._runtime_conversation_id,
        )
        if svc is None:
            raise ServiceError(f"LLM service '{self.llm_service}' could not connect")
        provider = getattr(svc, "provider", "")
        if provider != "openai":
            raise ServiceError(
                f"OpenAI-compatible {self.MEDIA_KIND} generation requires an openai llmConnection, got {provider or 'unknown'}")
        client = getattr(svc, "_client", None)
        if hasattr(svc, "get_client"):
            client = svc.get_client()
        elif client is not None and hasattr(client, "clone_for_call"):
            client = client.clone_for_call()
        if client is not None:
            svc = copy.copy(svc)
            svc._client = client
            svc._client._user_id = self._runtime_user_id
            svc._client._conversation_id = self._runtime_conversation_id
        if not getattr(svc, "api_key", ""):
            raise ServiceError(f"LLM service '{self.llm_service}' has no api_key")
        return svc

    def _model_for(self, svc) -> str:
        model = self.model or getattr(svc, "default_model", "")
        if not model:
            raise ServiceError(f"model is required for OpenAI-compatible {self.MEDIA_KIND} generation")
        return model

    @staticmethod
    def _join_url(base_url: str, path: str) -> str:
        if path.startswith(("http://", "https://")):
            return path
        base = (base_url or "").rstrip("/")
        if not path.startswith("/"):
            path = "/" + path
        return base + path

    def _headers(self, svc) -> Dict[str, str]:
        headers = {
            "Authorization": f"Bearer {svc.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "PawFlow-Agent/1.0",
        }
        headers.update({str(k): str(v) for k, v in self.extra_headers.items()})
        return headers

    def _request_json(self, svc, method: str, path: str, body: Optional[dict] = None) -> dict:
        data = json.dumps(body or {}).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            self._join_url(svc.base_url, path),
            data=data,
            headers=self._headers(svc),
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # nosec B310 - configured LLM service endpoint.
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:1200].decode("utf-8", errors="replace")
            raise ServiceError(f"OpenAI-compatible {self.MEDIA_KIND} API error {method} {path} ({exc.code}): {detail}") from exc
        except urllib.error.URLError as exc:
            raise ServiceError(f"OpenAI-compatible {self.MEDIA_KIND} API unavailable at {svc.base_url}: {exc}") from exc
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ServiceError(f"OpenAI-compatible {self.MEDIA_KIND} API returned non-JSON: {raw[:300]}") from exc
        if not isinstance(parsed, dict):
            raise ServiceError(f"OpenAI-compatible {self.MEDIA_KIND} API returned non-object JSON")
        return parsed

    def _chat_path(self, svc) -> str:
        parsed = urllib.parse.urlparse(getattr(svc, "base_url", "") or "")
        return "/chat/completions" if parsed.path.rstrip("/").endswith("/v1") else "/v1/chat/completions"

    def _tokens_key(self, svc, model: str) -> str:
        client = getattr(svc, "_client", None)
        if client is not None and hasattr(client, "_openai_tokens_key"):
            return client._openai_tokens_key(model, getattr(svc, "base_url", ""))
        if "api.openai.com" in (getattr(svc, "base_url", "") or "") and model.lower().startswith(("o1", "o3", "o4", "gpt-4o", "gpt-5", "gpt-4.1")):
            return "max_completion_tokens"
        return "max_tokens"

    def _apply_max_tokens(self, svc, model: str, body: dict):
        max_tokens = self.max_output_tokens or self.max_tokens
        if max_tokens > 0:
            body[self._tokens_key(svc, model)] = max_tokens

    @staticmethod
    def _download_url(url: str, default_content_type: str, timeout: int) -> tuple[bytes, str]:
        match = _DATA_URL_RE.match(url or "")
        if match:
            return base64.b64decode(match.group(2)), match.group(1)
        req = urllib.request.Request(url, headers={"User-Agent": "PawFlow-Agent/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 - provider-returned media URL.
            payload = resp.read()
            content_type = resp.headers.get("Content-Type", default_content_type)
        return payload, content_type


class OpenAICompatibleImageGenerationService(_OpenAICompatibleMediaMixin, BaseImageGenerationService):
    TYPE = "openaiCompatibleImageGeneration"
    VERSION = "1.0.0"
    NAME = "OpenAI-Compatible Image Generation"
    DESCRIPTION = "Generate images through an OpenAI or OpenRouter-style llmConnection"
    MEDIA_KIND = "image"

    def get_parameter_schema(self) -> dict:
        return {
            "llm_service": {
                "type": "service_ref", "service_type": "llmConnection",
                "provider": "openai", "required": True,
                "description": "OpenAI/API-compatible LLM service used for credentials and base URL.",
            },
            "protocol": {
                "type": "select", "required": False, "default": "auto",
                "options": ["auto", "openai_images", "chat_completions", "openrouter"],
                "description": "openai_images uses /images/generations; chat_completions/openrouter uses /chat/completions.",
            },
            "model": {"type": "string", "required": False, "default": "", "description": "Image model override."},
            "timeout": {"type": "integer", "required": False, "default": 300, "description": "HTTP timeout in seconds."},
            "max_tokens": {"type": "integer", "required": False, "default": 0, "description": "Max text tokens for chat-completions media responses."},
            "max_output_tokens": {"type": "integer", "required": False, "default": 0, "description": "Alias/preferred max token limit for newer OpenAI models."},
            "quality": {"type": "string", "required": False, "default": "", "description": "Optional OpenAI image quality."},
            "style": {"type": "string", "required": False, "default": "", "description": "Optional OpenAI image style."},
            "response_format": {"type": "string", "required": False, "default": "url", "description": "OpenAI images response format, usually url or b64_json."},
            "extra_body": {"type": "json", "required": False, "default": {}, "description": "Additional provider-specific JSON body fields."},
            "extra_headers": {"type": "json", "required": False, "default": {}, "description": "Additional HTTP headers such as OpenRouter attribution headers."},
        }

    def __init__(self, config):
        super().__init__(config)
        self._init_common()
        self.quality = (self.config.get("quality", "") or "").strip()
        self.style = (self.config.get("style", "") or "").strip()
        self.response_format = (self.config.get("response_format", "url") or "url").strip()

    def _select_protocol(self, svc, model: str) -> str:
        if self.protocol in ("chat_completions", "openrouter"):
            return "chat_completions"
        if self.protocol == "openai_images":
            return "openai_images"
        base = (getattr(svc, "base_url", "") or "").lower()
        if "openrouter.ai" in base or "/" in model:
            return "chat_completions"
        return "openai_images"

    @staticmethod
    def _size(width=None, height=None) -> str:
        try:
            w = int(width or 0)
            h = int(height or 0)
        except (TypeError, ValueError):
            return "1024x1024"
        if w <= 0 or h <= 0:
            return "1024x1024"
        ratio = w / h
        if ratio > 1.4:
            return "1792x1024"
        if ratio < 0.7:
            return "1024x1792"
        return "1024x1024"

    @staticmethod
    def _is_gpt_image_model(model: str) -> bool:
        return str(model or "").startswith("gpt-image-")

    @staticmethod
    def _gpt_image_size(width=None, height=None) -> str:
        try:
            w = int(width or 0)
            h = int(height or 0)
        except (TypeError, ValueError):
            return "1024x1024"
        if w <= 0 or h <= 0:
            return "1024x1024"
        ratio = w / h
        if ratio > 1.15:
            return "1536x1024"
        if ratio < 0.87:
            return "1024x1536"
        return "1024x1024"

    def generate(self, prompt="", negative_prompt="", width=1024, height=1024,
                 model: str = "", **kwargs) -> dict:
        if not prompt:
            raise ServiceError("No prompt provided")
        self.ensure_connected()
        svc = self._resolve_llm_service()
        selected_model = model or self._model_for(svc)
        protocol = self._select_protocol(svc, selected_model)
        if protocol == "openai_images":
            result = self._generate_openai_images(svc, selected_model, prompt, negative_prompt, width, height, kwargs)
        else:
            result = self._generate_chat_image(svc, selected_model, prompt, negative_prompt, width, height, kwargs)
        return result

    def _generate_openai_images(self, svc, model: str, prompt: str, negative_prompt: str,
                                width, height, kwargs: dict) -> dict:
        body = {
            "model": model,
            "prompt": prompt if not negative_prompt else f"{prompt}\nAvoid: {negative_prompt}",
            "n": 1,
            "size": kwargs.get("size") or (
                self._gpt_image_size(width, height)
                if self._is_gpt_image_model(model) else self._size(width, height)
            ),
        }
        if self._is_gpt_image_model(model):
            output_format = str(kwargs.get("output_format") or "").strip().lower()
            if output_format in {"png", "jpeg", "webp"}:
                body["output_format"] = output_format
        else:
            body["response_format"] = kwargs.get("response_format") or self.response_format
        if self.quality:
            body["quality"] = self.quality
        if self.style:
            body["style"] = self.style
        body.update(self.extra_body)
        data = self._request_json(svc, "POST", "/images/generations", body)
        payload, content_type = self._extract_image_payload(data)
        if payload is not None:
            return {"image_bytes": payload, "content_type": content_type or "image/png"}
        image_url = self._extract_image_url(data)
        if not image_url:
            raise ServiceError(f"No image URL or base64 payload in response: {json.dumps(data)[:500]}")
        image_bytes, content_type = self._download_url(image_url, "image/png", self.timeout)
        return {"image_bytes": image_bytes, "content_type": content_type}

    def _generate_chat_image(self, svc, model: str, prompt: str, negative_prompt: str,
                             width, height, kwargs: dict) -> dict:
        size_hint = kwargs.get("size") or (f"{width}x{height}" if width and height else "")
        text = prompt
        if negative_prompt:
            text += f"\nAvoid: {negative_prompt}"
        if size_hint:
            text += f"\nTarget image size/aspect: {size_hint}."
        body = {
            "model": model,
            "messages": [{"role": "user", "content": text}],
        }
        self._apply_max_tokens(svc, model, body)
        body.update(self.extra_body)
        data = self._request_json(svc, "POST", self._chat_path(svc), body)
        payload, content_type = self._extract_image_payload(data)
        if payload is not None:
            return {"image_bytes": payload, "content_type": content_type or "image/png"}
        image_url = self._extract_image_url(data)
        if not image_url:
            raise ServiceError(f"No image URL or base64 payload in response: {json.dumps(data)[:500]}")
        image_bytes, content_type = self._download_url(image_url, "image/png", self.timeout)
        return {"image_bytes": image_bytes, "content_type": content_type}

    @staticmethod
    def _extract_image_payload(data: dict) -> tuple[Optional[bytes], str]:
        for item in _walk_json(data):
            if not isinstance(item, dict):
                continue
            raw = item.get("b64_json") or item.get("image_base64") or item.get("base64")
            if isinstance(raw, str) and raw.strip():
                return base64.b64decode(raw), item.get("content_type") or item.get("mime_type") or "image/png"
        for item in _walk_json(data):
            if isinstance(item, str):
                match = _DATA_URL_RE.search(item)
                if match:
                    return base64.b64decode(match.group(2)), match.group(1)
        return None, ""

    @staticmethod
    def _extract_image_url(data: dict) -> str:
        preferred_keys = {"image_url", "url", "uri"}
        for item in _walk_json(data):
            if isinstance(item, dict):
                for key in preferred_keys:
                    value = item.get(key)
                    if isinstance(value, dict):
                        value = value.get("url")
                    if isinstance(value, str) and value.startswith(("http://", "https://", "data:")):
                        return value
            elif isinstance(item, str):
                stripped = item.strip()
                if stripped.startswith(("{", "[")):
                    try:
                        nested = json.loads(stripped)
                    except json.JSONDecodeError:
                        nested = None
                    if nested is not None:
                        found = OpenAICompatibleImageGenerationService._extract_image_url(nested)
                        if found:
                            return found
                match = _IMAGE_URL_RE.search(item)
                if match:
                    return match.group(0)
        return ""


class OpenAICompatibleVideoGenerationService(_OpenAICompatibleMediaMixin, BaseVideoGenerationService):
    TYPE = "openaiCompatibleVideoGeneration"
    VERSION = "1.0.0"
    NAME = "OpenAI-Compatible Video Generation"
    DESCRIPTION = "Generate videos through an OpenAI or OpenRouter-style llmConnection"
    MEDIA_KIND = "video"

    def get_parameter_schema(self) -> dict:
        return {
            "llm_service": {
                "type": "service_ref", "service_type": "llmConnection",
                "provider": "openai", "required": True,
                "description": "OpenAI/API-compatible LLM service used for credentials and base URL.",
            },
            "protocol": {
                "type": "select", "required": False, "default": "auto",
                "options": ["auto", "openai_video", "openrouter", "chat_completions"],
                "description": "openai_video uses configurable video endpoints; openrouter uses /videos; chat_completions is a legacy fallback.",
            },
            "model": {"type": "string", "required": False, "default": "", "description": "Video model override."},
            "timeout": {"type": "integer", "required": False, "default": 900, "description": "Total HTTP/poll timeout in seconds."},
            "poll_interval": {"type": "integer", "required": False, "default": 5, "description": "Seconds between async status polls."},
            "max_duration": {"type": "integer", "required": False, "default": 15, "description": "Maximum accepted duration seconds."},
            "max_tokens": {"type": "integer", "required": False, "default": 0, "description": "Max text tokens for chat-completions media responses."},
            "max_output_tokens": {"type": "integer", "required": False, "default": 0, "description": "Alias/preferred max token limit for newer OpenAI models."},
            "submit_path": {"type": "string", "required": False, "default": "/videos/generations", "description": "OpenAI-video submit endpoint override."},
            "status_path_template": {"type": "string", "required": False, "default": "/videos/{id}", "description": "Async status endpoint template; {id} is replaced with the generation id."},
            "openrouter_generation_path_template": {"type": "string", "required": False, "default": "/videos/{id}", "description": "OpenRouter-style async generation status endpoint."},
            "use_webhook": {
                "type": "boolean", "required": False, "default": False,
                "description": (
                    "Use the endpoint's callback_url field for openai_video "
                    "requests instead of polling. Requires PawFlow to be "
                    "reachable from the internet through public_callback_base_url "
                    "or the agent file_base_url."
                ),
            },
            "public_callback_base_url": {
                "type": "string", "required": False, "default": "",
                "description": (
                    "Public HTTPS base URL that video providers can POST "
                    "callbacks to, e.g. https://webchat.example.org. Falls "
                    "back to the agent runtime file_base_url when omitted."
                ),
            },
            "extra_body": {"type": "json", "required": False, "default": {}, "description": "Additional provider-specific JSON body fields."},
            "extra_headers": {"type": "json", "required": False, "default": {}, "description": "Additional HTTP headers such as OpenRouter attribution headers."},
        }

    def __init__(self, config):
        super().__init__(config)
        self._init_common()
        self.timeout = int(self.config.get("timeout", 900) or 900)
        self.max_duration = int(self.config.get("max_duration", 15) or 15)
        self.submit_path = (self.config.get("submit_path", "/videos/generations") or "/videos/generations").strip()
        self.status_path_template = (self.config.get("status_path_template", "/videos/{id}") or "/videos/{id}").strip()
        self.openrouter_generation_path_template = (self.config.get("openrouter_generation_path_template", "/videos/{id}") or "/videos/{id}").strip()
        self.use_webhook = _truthy(self.config.get("use_webhook", False))
        self.public_callback_base_url = (
            self.config.get("public_callback_base_url", "") or "").strip().rstrip("/")
        self._callback_base_url = ""

    def set_callback_base_url(self, base_url: str):
        self._callback_base_url = (base_url or "").strip().rstrip("/")

    def _select_protocol(self, svc, model: str) -> str:
        if self.protocol == "openrouter":
            return "openrouter_video"
        if self.protocol == "chat_completions":
            return "chat_completions"
        if self.protocol == "openai_video":
            return "openai_video"
        base = (getattr(svc, "base_url", "") or "").lower()
        if "openrouter.ai" in base or "/" in model:
            return "openrouter_video"
        return "openai_video"

    @staticmethod
    def _aspect_ratio(width=None, height=None, fallback="16:9") -> str:
        try:
            w = int(width or 0)
            h = int(height or 0)
        except (TypeError, ValueError):
            return fallback
        if w <= 0 or h <= 0:
            return fallback
        ratio = w / h
        if ratio < 0.8:
            return "9:16"
        if ratio <= 1.2:
            return "1:1"
        if ratio > 1.5:
            return "16:9"
        return "4:3"

    def generate(self, prompt="", duration=5, width=None, height=None,
                 image_url: str = "", end_image_url: str = "", model: str = "",
                 **kwargs) -> dict:
        if not prompt:
            raise ServiceError("No prompt provided")
        self.ensure_connected()
        svc = self._resolve_llm_service()
        selected_model = model or self._model_for(svc)
        protocol = self._select_protocol(svc, selected_model)
        bounded_duration = max(1, min(int(duration or 5), self.max_duration))
        webhook_ticket = None
        try:
            if self.use_webhook and protocol in {"openrouter_video", "openai_video"}:
                base_url = self.public_callback_base_url or self._callback_base_url
                from services.media_webhook_registry import MediaWebhookRegistry
                webhook_ticket = MediaWebhookRegistry.instance().register(
                    "openrouter", base_url)
                kwargs = dict(kwargs)
                kwargs["callback_url"] = webhook_ticket.url
            if protocol == "openrouter_video":
                data = self._submit_openrouter_video(
                    svc, selected_model, prompt, bounded_duration, width, height,
                    image_url, end_image_url, kwargs)
                status_template = self._extract_polling_url(data) or self.openrouter_generation_path_template
                return self._resolve_video_result(svc, data, status_template, webhook_ticket=webhook_ticket)
            if protocol == "chat_completions":
                data = self._submit_chat_video(svc, selected_model, prompt, bounded_duration, width, height, image_url, end_image_url, kwargs)
                status_template = self.openrouter_generation_path_template
                return self._resolve_video_result(svc, data, status_template)

            status_template = self.status_path_template
            data = self._submit_openai_video(svc, selected_model, prompt, bounded_duration, width, height, image_url, end_image_url, kwargs)
            return self._resolve_video_result(svc, data, status_template, webhook_ticket=webhook_ticket)
        finally:
            if webhook_ticket is not None:
                webhook_ticket.close()

    def _submit_openai_video(self, svc, model: str, prompt: str, duration: int,
                             width, height, image_url: str, end_image_url: str,
                             kwargs: dict) -> dict:
        body = {
            "model": model,
            "prompt": prompt,
            "duration": duration,
            "aspect_ratio": kwargs.get("aspect_ratio") or self._aspect_ratio(width, height),
        }
        if image_url:
            body["image_url"] = image_url
        if end_image_url:
            body["end_image_url"] = end_image_url
        for key in ("resolution", "quality", "seed", "with_audio"):
            if key in kwargs and kwargs[key] not in (None, ""):
                body[key] = kwargs[key]
        body.update(self.extra_body)
        if kwargs.get("callback_url"):
            body["callback_url"] = kwargs["callback_url"]
        return self._request_json(svc, "POST", self.submit_path, body)

    def _submit_openrouter_video(self, svc, model: str, prompt: str, duration: int,
                                 width, height, image_url: str, end_image_url: str,
                                 kwargs: dict) -> dict:
        body = {
            "model": model,
            "prompt": prompt,
            "duration": duration,
            "aspect_ratio": kwargs.get("aspect_ratio") or self._aspect_ratio(width, height),
        }
        if kwargs.get("resolution"):
            body["resolution"] = kwargs["resolution"]
        if kwargs.get("size"):
            body["size"] = kwargs["size"]
        if kwargs.get("seed") not in (None, ""):
            body["seed"] = kwargs["seed"]
        if kwargs.get("generate_audio") not in (None, ""):
            body["generate_audio"] = _truthy(kwargs["generate_audio"])
        elif kwargs.get("with_audio") not in (None, ""):
            body["generate_audio"] = _truthy(kwargs["with_audio"])
        frame_images = []
        if image_url:
            frame_images.append({
                "type": "image_url",
                "image_url": {"url": image_url},
                "frame_type": "first_frame",
            })
        if end_image_url:
            frame_images.append({
                "type": "image_url",
                "image_url": {"url": end_image_url},
                "frame_type": "last_frame",
            })
        if frame_images:
            body["frame_images"] = frame_images
        for key in ("input_references", "provider", "callback_url"):
            if key in kwargs and kwargs[key] not in (None, ""):
                body[key] = kwargs[key]
        body.update(self.extra_body)
        return self._request_json(svc, "POST", "/videos", body)

    def _submit_chat_video(self, svc, model: str, prompt: str, duration: int,
                           width, height, image_url: str, end_image_url: str,
                           kwargs: dict) -> dict:
        text = (
            f"{prompt}\n"
            f"Generate a video. Duration: {duration}s. "
            f"Aspect ratio: {kwargs.get('aspect_ratio') or self._aspect_ratio(width, height)}."
        )
        content: Any = text
        if image_url or end_image_url:
            parts = [{"type": "text", "text": text}]
            if image_url:
                parts.append({"type": "image_url", "image_url": {"url": image_url}})
            if end_image_url:
                parts.append({"type": "image_url", "image_url": {"url": end_image_url}})
            content = parts
        body = {"model": model, "messages": [{"role": "user", "content": content}]}
        for key in ("duration", "aspect_ratio", "resolution", "quality", "seed", "with_audio"):
            if key == "duration":
                body[key] = duration
            elif key == "aspect_ratio":
                body[key] = kwargs.get(key) or self._aspect_ratio(width, height)
            elif key in kwargs and kwargs[key] not in (None, ""):
                body[key] = kwargs[key]
        self._apply_max_tokens(svc, model, body)
        body.update(self.extra_body)
        return self._request_json(svc, "POST", self._chat_path(svc), body)

    def _resolve_video_result(self, svc, data: dict, status_template: str, *, webhook_ticket=None) -> dict:
        video_url = self._extract_video_url(data)
        if video_url:
            payload, content_type = self._download_url(video_url, "video/mp4", self.timeout)
            return {"video_bytes": payload, "content_type": content_type}
        if webhook_ticket is not None:
            try:
                from services.tool_relay_service import current_cancel_event
                cancel_event = current_cancel_event()
            except Exception:
                cancel_event = None
            payload = webhook_ticket.wait(
                timeout=self.timeout, cancel_event=cancel_event,
                poll_interval=self.poll_interval)
            state = self._extract_state(payload)
            if state in {"failed", "error", "cancelled", "canceled", "expired"}:
                raise ServiceError(f"OpenAI-compatible video generation {state}: {json.dumps(payload)[:500]}")
            video_url = self._extract_video_url(payload)
            if not video_url:
                raise ServiceError(f"No video URL in webhook payload: {json.dumps(payload)[:500]}")
            payload_bytes, content_type = self._download_url(video_url, "video/mp4", self.timeout)
            return {"video_bytes": payload_bytes, "content_type": content_type}
        generation_id = self._extract_generation_id(data)
        if generation_id:
            return self._poll_video(svc, generation_id, status_template)
        raise ServiceError(f"No video URL or generation id in response: {json.dumps(data)[:500]}")

    def _poll_video(self, svc, generation_id: str, status_template: str) -> dict:
        deadline = time.time() + self.timeout
        path = status_template.replace("{id}", urllib.parse.quote(generation_id, safe=""))
        last_status = {}
        while time.time() < deadline:
            time.sleep(self.poll_interval)
            last_status = self._request_json(svc, "GET", path)
            video_url = self._extract_video_url(last_status)
            if video_url:
                payload, content_type = self._download_url(video_url, "video/mp4", self.timeout)
                return {"video_bytes": payload, "content_type": content_type}
            state = self._extract_state(last_status)
            if state in {"failed", "error", "cancelled", "canceled", "expired"}:
                raise ServiceError(f"OpenAI-compatible video generation {state}: {json.dumps(last_status)[:500]}")
        raise ServiceError(f"OpenAI-compatible video generation timed out after {self.timeout}s: {json.dumps(last_status)[:500]}")

    @staticmethod
    def _extract_video_url(data: dict) -> str:
        preferred_keys = {"video_url", "output_url", "download_url", "url", "uri"}
        for item in _walk_json(data):
            if isinstance(item, dict):
                for key in preferred_keys:
                    value = item.get(key)
                    if isinstance(value, dict):
                        value = value.get("url")
                    if isinstance(value, str) and value.startswith(("http://", "https://")):
                        if _VIDEO_URL_RE.search(value) or key != "url":
                            return value
            elif isinstance(item, str):
                stripped = item.strip()
                if stripped.startswith(("{", "[")):
                    try:
                        nested = json.loads(stripped)
                    except json.JSONDecodeError:
                        nested = None
                    if nested is not None:
                        found = OpenAICompatibleVideoGenerationService._extract_video_url(nested)
                        if found:
                            return found
                match = _VIDEO_URL_RE.search(item)
                if match:
                    return match.group(0)
        return ""

    @staticmethod
    def _extract_generation_id(data: dict) -> str:
        keys = {"generation_id", "request_id", "task_id", "job_id", "id"}
        for item in _walk_json(data):
            if isinstance(item, dict):
                for key in keys:
                    value = item.get(key)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
        return ""

    @staticmethod
    def _extract_polling_url(data: dict) -> str:
        for item in _walk_json(data):
            if isinstance(item, dict):
                value = item.get("polling_url") or item.get("status_url")
                if isinstance(value, str) and value.startswith(("http://", "https://", "/")):
                    return value
        return ""

    @staticmethod
    def _extract_state(data: dict) -> str:
        for item in _walk_json(data):
            if isinstance(item, dict):
                value = item.get("status") or item.get("state")
                if isinstance(value, str):
                    return value.strip().lower()
        return ""


ServiceFactory.register(OpenAICompatibleImageGenerationService)
ServiceFactory.register(OpenAICompatibleVideoGenerationService)
