"""Pixazo SDXL image generation service.

Implements BaseImageGenerationService for the Pixazo gateway API.
All Pixazo-specific logic (endpoints, headers, retries) lives here.
"""

import http.client
import json
import logging
import ssl
import time
import urllib.request

from core import ServiceFactory, ServiceError
from services.base_image_generation import BaseImageGenerationService

logger = logging.getLogger(__name__)


class PixazoImageService(BaseImageGenerationService):
    TYPE = "pixazoImageGeneration"
    VERSION = "1.0.0"
    NAME = "Pixazo SDXL Image Generation"
    DESCRIPTION = "Generate images via Pixazo SDXL API"

    def get_parameter_schema(self) -> dict:
        return {
            "api_key": {
                "type": "string",
                "required": True,
                "sensitive": True,
                "description": "Pixazo API key (Ocp-Apim-Subscription-Key)",
            },
            "timeout": {
                "type": "integer",
                "required": False,
                "default": 120,
                "description": "HTTP request timeout in seconds",
            },
            "max_retries": {
                "type": "integer",
                "required": False,
                "default": 5,
                "description": "Max retries on 500 errors (cold start)",
            },
        }

    def __init__(self, config):
        super().__init__(config)
        self.api_key = self.config.get("api_key", "")
        self.timeout = int(self.config.get("timeout", 120))
        self.max_retries = int(self.config.get("max_retries", 5))

    def _create_connection(self):
        if not self.api_key:
            raise ServiceError("api_key is required for Pixazo service")
        return {"ready": True}

    def _close_connection(self):
        pass

    def generate(self, prompt="", negative_prompt="", width=1024, height=1024,
                 steps=20, **_) -> dict:
        """Generate an image via Pixazo SDXL API.

        Returns:
            {"image_bytes": bytes, "content_type": str}
        """
        if not prompt:
            raise ServiceError("No prompt provided")

        self.ensure_connected()

        width = max(256, min(1024, int(width)))
        height = max(256, min(1024, int(height)))
        steps = max(1, min(20, int(steps)))

        body = {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "width": width,
            "height": height,
            "num_steps": steps,
            "guidance_scale": 5,
            "seed": int(time.time()) % 1000000,
        }

        logger.info("[PIXAZO] Request: prompt=%s..., w=%d, h=%d, steps=%d",
                     prompt[:80], width, height, steps)
        json_body = json.dumps(body).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
            "Ocp-Apim-Subscription-Key": self.api_key,
            "Content-Length": str(len(json_body)),
        }

        ctx = ssl.create_default_context()
        resp_body = ""
        for attempt in range(self.max_retries):
            conn = http.client.HTTPSConnection(
                "gateway.pixazo.ai", timeout=self.timeout, context=ctx
            )
            conn.request("POST", "/getImage/v1/getSDXLImage",
                         body=json_body, headers=headers)
            resp = conn.getresponse()
            resp_body = resp.read().decode("utf-8", errors="replace")
            conn.close()
            if resp.status < 500:
                break
            delay = [3, 5, 8, 10][min(attempt, 3)]
            logger.warning("[PIXAZO] Attempt %d/%d got %d: %s, retrying in %ds...",
                           attempt + 1, self.max_retries, resp.status,
                           resp_body[:200], delay)
            time.sleep(delay)

        if resp.status >= 400:
            raise ServiceError(f"Pixazo API error ({resp.status}): {resp_body[:300]}")

        data = json.loads(resp_body)
        image_url = data.get("imageUrl", "") if isinstance(data, dict) else ""
        if not image_url:
            raise ServiceError(f"No imageUrl in Pixazo response: {resp_body[:300]}")

        # Download the generated image
        req = urllib.request.Request(
            image_url, headers={"User-Agent": "PyFi2-Agent/1.0"}
        )
        with urllib.request.urlopen(req, timeout=60) as img_resp:
            image_bytes = img_resp.read()
            content_type = img_resp.headers.get("Content-Type", "image/png")

        return {"image_bytes": image_bytes, "content_type": content_type}


ServiceFactory.register(PixazoImageService)
