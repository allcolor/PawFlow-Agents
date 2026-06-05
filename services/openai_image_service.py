"""OpenAI DALL-E image generation service.

Implements BaseImageGenerationService for the OpenAI images API.
Supports DALL-E 3 and compatible endpoints (base_url configurable).
"""

import json
import logging
import base64
import urllib.request

from core import ServiceFactory, ServiceError
from core.relay_proxy_url import resolve_relay_aware_url
from services.base_image_generation import BaseImageGenerationService

logger = logging.getLogger(__name__)


def _truthy(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _raw_config(config: dict, key: str, default=""):
    try:
        return dict.__getitem__(config, key)
    except KeyError:
        return default


class OpenAIImageService(BaseImageGenerationService):
    TYPE = "openaiImageGeneration"
    VERSION = "1.0.0"
    NAME = "OpenAI Image Generation"
    DESCRIPTION = "Generate images via OpenAI API (ChatGPT Image, DALL-E)"

    def get_parameter_schema(self) -> dict:
        return {
            "api_key": {
                "type": "string", "required": True, "sensitive": True,
                "description": "OpenAI API key",
            },
            "base_url": {
                "type": "string", "required": False,
                "default": "https://api.openai.com/v1",
                "description": "API base URL. Use http://${conv.relay}/host:port/v1 for relay-routed compatible endpoints.",
            },
            "allow_private_base_url": {
                "type": "boolean", "required": False, "default": False,
                "description": "Allow direct private/loopback base_url targets. Prefer relay URLs for local endpoints.",
            },
            "model": {
                "type": "string", "required": False,
                "default": "gpt-image-1",
                "description": "Model: gpt-image-1 (ChatGPT Image), dall-e-3, dall-e-2",
            },
            "timeout": {
                "type": "integer", "required": False, "default": 120,
                "description": "HTTP request timeout in seconds",
            },
        }

    def __init__(self, config):
        super().__init__(config)
        self.api_key = self.config.get("api_key", "")
        self.base_url = str(_raw_config(self.config, "base_url", "https://api.openai.com/v1") or "https://api.openai.com/v1").rstrip("/")
        self._raw_base_url = self.base_url
        self.allow_private_base_url = _truthy(self.config.get("allow_private_base_url", False))
        self._runtime_user_id = ""
        self._runtime_conversation_id = ""
        self._runtime_agent_name = ""
        self.model = self.config.get("model", "gpt-image-1")
        self.timeout = int(self.config.get("timeout", 120))

    def set_runtime_context(self, user_id: str = "", conversation_id: str = "",
                            agent_name: str = "", **_: object):
        self._runtime_user_id = user_id or ""
        self._runtime_conversation_id = conversation_id or ""
        self._runtime_agent_name = agent_name or ""

    def _effective_base_url(self) -> str:
        return resolve_relay_aware_url(
            self._raw_base_url,
            user_id=self._runtime_user_id,
            conversation_id=self._runtime_conversation_id,
            agent_name=self._runtime_agent_name,
            allow_private=self.allow_private_base_url,
            service_name="OpenAI image",
            transform_relay=True,
        )

    def _create_connection(self):
        if not self.api_key:
            raise ServiceError("api_key is required for OpenAI Image service")
        self.base_url = resolve_relay_aware_url(
            self._raw_base_url,
            allow_private=self.allow_private_base_url,
            service_name="OpenAI image",
            transform_relay=False,
        )
        return {"ready": True, "base_url": self.base_url}

    def _close_connection(self):
        pass

    def generate(self, prompt="", negative_prompt="", width=1024, height=1024,
                 output_format="png", quality="", **_) -> dict:
        if not prompt:
            raise ServiceError("No prompt provided")
        self.ensure_connected()

        is_gpt_image = str(self.model or "").startswith("gpt-image-")
        size = "1024x1024"
        if width and height:
            ratio = width / height
            if is_gpt_image:
                if ratio > 1.15:
                    size = "1536x1024"
                elif ratio < 0.87:
                    size = "1024x1536"
            else:
                if ratio > 1.4:
                    size = "1792x1024"
                elif ratio < 0.7:
                    size = "1024x1792"

        body = {
            "model": self.model,
            "prompt": prompt,
            "n": 1,
            "size": size,
        }
        if is_gpt_image:
            fmt = str(output_format or "").strip().lower()
            if fmt in {"png", "jpeg", "webp"}:
                body["output_format"] = fmt
            if quality:
                body["quality"] = quality
        else:
            body["response_format"] = "url"

        logger.info("[OPENAI-IMAGE] Generating: prompt=%s..., model=%s, size=%s",
                    prompt[:80], self.model, size)

        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            f"{self._effective_base_url()}/images/generations",
            data=data,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # nosec B310 - configured OpenAI image API endpoint.
            result = json.loads(resp.read().decode("utf-8"))

        images = result.get("data", [])
        if not images:
            raise ServiceError(f"No images in response: {json.dumps(result)[:300]}")

        image = images[0]
        image_b64 = image.get("b64_json", "")
        if image_b64:
            content_type = {
                "jpeg": "image/jpeg",
                "webp": "image/webp",
                "png": "image/png",
            }.get(str(output_format or "png").lower(), "image/png")
            return {"image_bytes": base64.b64decode(image_b64), "content_type": content_type}

        image_url = image.get("url", "")
        if not image_url:
            raise ServiceError("No image URL or base64 payload in response")

        # Download image
        img_req = urllib.request.Request(
            image_url, headers={"User-Agent": "PawFlow-Agent/1.0"},
        )
        with urllib.request.urlopen(img_req, timeout=60) as img_resp:  # nosec B310 - provider-returned image download URL.
            image_bytes = img_resp.read()
            content_type = img_resp.headers.get("Content-Type", "image/png")

        return {"image_bytes": image_bytes, "content_type": content_type}


ServiceFactory.register(OpenAIImageService)
