"""Grok Imagine image generation service (xAI).

Implements BaseImageGenerationService for the xAI Grok Imagine API.
Uses the OpenAI-compatible /v1/images/generations endpoint.
"""

import json
import logging
import urllib.request

from core import ServiceFactory, ServiceError
from services.base_image_generation import BaseImageGenerationService

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.x.ai/v1"


class GrokImageService(BaseImageGenerationService):
    TYPE = "grokImageGeneration"
    VERSION = "1.0.0"
    NAME = "Grok Imagine Image Generation"
    DESCRIPTION = "Generate images via xAI Grok Imagine API"

    def get_parameter_schema(self) -> dict:
        return {
            "api_key": {
                "type": "string", "required": True, "sensitive": True,
                "description": "xAI API key (Bearer token)",
            },
            "model": {
                "type": "string", "required": False,
                "default": "grok-imagine-image",
                "description": "Model: grok-imagine-image",
            },
            "timeout": {
                "type": "integer", "required": False, "default": 120,
                "description": "HTTP request timeout in seconds",
            },
        }

    def __init__(self, config):
        super().__init__(config)
        self.api_key = self.config.get("api_key", "")
        self.model = self.config.get("model", "grok-imagine-image")
        self.timeout = int(self.config.get("timeout", 120))

    def _create_connection(self):
        if not self.api_key:
            raise ServiceError("api_key is required for Grok Imagine service")
        return {"ready": True}

    def _close_connection(self):
        pass

    def generate(self, prompt="", negative_prompt="", width=1024, height=1024,
                 aspect_ratio="", resolution="", **_) -> dict:
        if not prompt:
            raise ServiceError("No prompt provided")
        self.ensure_connected()

        # Use explicit aspect_ratio if provided, else derive from width/height
        if not aspect_ratio:
            aspect_ratio = "1:1"
            if width and height:
                ratio = width / height
                if ratio > 1.8:
                    aspect_ratio = "2:1"
                elif ratio > 1.4:
                    aspect_ratio = "16:9"
                elif ratio > 1.2:
                    aspect_ratio = "4:3"
                elif ratio < 0.55:
                    aspect_ratio = "1:2"
                elif ratio < 0.7:
                    aspect_ratio = "9:16"
                elif ratio < 0.85:
                    aspect_ratio = "3:4"

        body = {
            "model": self.model,
            "prompt": prompt,
            "n": 1,
            "aspect_ratio": aspect_ratio,
            "response_format": "url",
        }
        if resolution in ("1k", "2k"):
            body["resolution"] = resolution

        logger.info("[GROK-IMAGE] Generating: prompt=%s..., model=%s, aspect=%s",
                    prompt[:80], self.model, aspect_ratio)

        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            f"{_BASE_URL}/images/generations",
            data=data,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # nosec B310 - configured Grok API endpoint.
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
        with urllib.request.urlopen(img_req, timeout=60) as img_resp:  # nosec B310 - provider-returned image download URL.
            image_bytes = img_resp.read()
            content_type = img_resp.headers.get("Content-Type", "image/png")

        return {"image_bytes": image_bytes, "content_type": content_type}


ServiceFactory.register(GrokImageService)
