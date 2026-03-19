"""OpenAI DALL-E image generation service.

Implements BaseImageGenerationService for the OpenAI images API.
Supports DALL-E 3 and compatible endpoints (base_url configurable).
"""

import json
import logging
import urllib.request

from core import ServiceFactory, ServiceError
from services.base_image_generation import BaseImageGenerationService

logger = logging.getLogger(__name__)


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
                "description": "API base URL (for proxies/compatible endpoints)",
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
        self.base_url = self.config.get("base_url", "https://api.openai.com/v1").rstrip("/")
        self.model = self.config.get("model", "gpt-image-1")
        self.timeout = int(self.config.get("timeout", 120))

    def _create_connection(self):
        if not self.api_key:
            raise ServiceError("api_key is required for OpenAI Image service")
        return {"ready": True}

    def _close_connection(self):
        pass

    def generate(self, prompt="", negative_prompt="", width=1024, height=1024,
                 **_) -> dict:
        if not prompt:
            raise ServiceError("No prompt provided")
        self.ensure_connected()

        # DALL-E 3 supports: 1024x1024, 1792x1024, 1024x1792
        size = "1024x1024"
        if width and height:
            ratio = width / height
            if ratio > 1.4:
                size = "1792x1024"
            elif ratio < 0.7:
                size = "1024x1792"

        body = {
            "model": self.model,
            "prompt": prompt,
            "n": 1,
            "size": size,
            "response_format": "url",
        }

        logger.info("[OPENAI-IMAGE] Generating: prompt=%s..., model=%s, size=%s",
                    prompt[:80], self.model, size)

        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/images/generations",
            data=data,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))

        images = result.get("data", [])
        if not images:
            raise ServiceError(f"No images in response: {json.dumps(result)[:300]}")

        image_url = images[0].get("url", "")
        if not image_url:
            raise ServiceError("No image URL in response")

        # Download image
        img_req = urllib.request.Request(
            image_url, headers={"User-Agent": "PawFlow-Agent/1.0"},
        )
        with urllib.request.urlopen(img_req, timeout=60) as img_resp:
            image_bytes = img_resp.read()
            content_type = img_resp.headers.get("Content-Type", "image/png")

        return {"image_bytes": image_bytes, "content_type": content_type}


ServiceFactory.register(OpenAIImageService)
