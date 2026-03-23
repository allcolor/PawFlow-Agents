"""Pixazo image generation service — supports all Pixazo image models.

Implements BaseImageGenerationService for the Pixazo gateway API.
Model selection via configurable endpoint or preset name.
Supports both sync (SDXL) and async (Flux, Recraft, Nano Banana, etc.) models.
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

# Known Pixazo image models with their endpoints and response patterns
# Format: {preset: {generate_endpoint, poll_endpoint, body_builder, response_parser}}
PIXAZO_MODELS = {
    "sdxl": {
        "label": "SDXL (Stability AI)",
        "endpoint": "/getImage/v1/getSDXLImage",
        "mode": "sync",  # response has imageUrl directly
        "url_field": "imageUrl",
    },
    "flux-dev": {
        "label": "Flux Dev (Black Forest Labs)",
        "endpoint": "/flux-dev/v1/dev/textToImage",
        "poll_endpoint": "/flux-dev-polling/dev/getFluxDevStatus",
        "mode": "async",
        "id_field": "requestId",
    },
    "nano-banana": {
        "label": "Nano Banana (Google)",
        "endpoint": "/nano-banana/v1/nano-banana/generateTextToImageRequest",
        "poll_endpoint": "/nano-banana-polling/nano-banana/getStatus",
        "mode": "async",
        "id_field": "request_id",
    },
    "recraft-v3": {
        "label": "Recraft V3",
        "endpoint": "/recraft/v3/generate",
        "mode": "sync",
        "url_field": "output",
    },
    "recraft-v4": {
        "label": "Recraft V4",
        "endpoint": "/recraft/v4/generate",
        "mode": "sync",
        "url_field": "output",
    },
    "ideogram": {
        "label": "Ideogram",
        "endpoint": "/ideogram/v1/ideogram/generateTextToImageRequest",
        "poll_endpoint": "/ideogram-polling/ideogram/getStatus",
        "mode": "async",
        "id_field": "request_id",
    },
    "gpt-image": {
        "label": "GPT Image (OpenAI)",
        "endpoint": "/gpt-image/v1/gpt-image/generateTextToImageRequest",
        "poll_endpoint": "/gpt-image-polling/gpt-image/getStatus",
        "mode": "async",
        "id_field": "request_id",
    },
    "stable-diffusion": {
        "label": "Stable Diffusion 3.5",
        "endpoint": "/sd35/v1/sd35/generateTextToImageRequest",
        "poll_endpoint": "/sd35-polling/sd35/getStatus",
        "mode": "async",
        "id_field": "request_id",
    },
    "dalle": {
        "label": "DALL-E (OpenAI)",
        "endpoint": "/dalle/v1/dalle/generateTextToImageRequest",
        "poll_endpoint": "/dalle-polling/dalle/getStatus",
        "mode": "async",
        "id_field": "request_id",
    },
    "seedream": {
        "label": "Seedream (BytePlus)",
        "endpoint": "/seedream/v1/seedream/generateTextToImageRequest",
        "poll_endpoint": "/seedream-polling/seedream/getStatus",
        "mode": "async",
        "id_field": "request_id",
    },
    "bria": {
        "label": "Bria",
        "endpoint": "/bria/v1/bria/generateTextToImageRequest",
        "poll_endpoint": "/bria-polling/bria/getStatus",
        "mode": "async",
        "id_field": "request_id",
    },
    "grok": {
        "label": "Grok (xAI)",
        "endpoint": "/grok/v1/grok/generateTextToImageRequest",
        "poll_endpoint": "/grok-polling/grok/getStatus",
        "mode": "async",
        "id_field": "request_id",
    },
    "hunyuan": {
        "label": "Hunyuan (Tencent)",
        "endpoint": "/hunyuan/v1/hunyuan/generateTextToImageRequest",
        "poll_endpoint": "/hunyuan-polling/hunyuan/getStatus",
        "mode": "async",
        "id_field": "request_id",
    },
    "longcat-image": {
        "label": "LongCat Image",
        "endpoint": "/longcat-image/v1/longcat-image/generateTextToImageRequest",
        "poll_endpoint": "/longcat-image-polling/longcat-image/getStatus",
        "mode": "async",
        "id_field": "request_id",
    },
    "luma-dream-machine": {
        "label": "Luma Dream Machine (Luma AI)",
        "endpoint": "/luma-dream-machine/v1/luma-dream-machine/generateTextToImageRequest",
        "poll_endpoint": "/luma-dream-machine-polling/luma-dream-machine/getStatus",
        "mode": "async",
        "id_field": "request_id",
    },
    "minimax": {
        "label": "Minimax (MiniMax)",
        "endpoint": "/minimax/v1/minimax/generateTextToImageRequest",
        "poll_endpoint": "/minimax-polling/minimax/getStatus",
        "mode": "async",
        "id_field": "request_id",
    },
    "pixelforge": {
        "label": "Pixelforge (Pixazo)",
        "endpoint": "/pixelforge/v1/pixelforge/generateTextToImageRequest",
        "poll_endpoint": "/pixelforge-polling/pixelforge/getStatus",
        "mode": "async",
        "id_field": "request_id",
    },
    "qwen-image": {
        "label": "Qwen Image (Alibaba)",
        "endpoint": "/qwen-image/v1/qwen-image/generateTextToImageRequest",
        "poll_endpoint": "/qwen-image-polling/qwen-image/getStatus",
        "mode": "async",
        "id_field": "request_id",
    },
    "reve-image": {
        "label": "Reve Image",
        "endpoint": "/reve-image/v1/reve-image/generateTextToImageRequest",
        "poll_endpoint": "/reve-image-polling/reve-image/getStatus",
        "mode": "async",
        "id_field": "request_id",
    },
    "studio-ghibli": {
        "label": "Studio Ghibli",
        "endpoint": "/studio-ghibli/v1/studio-ghibli/generateTextToImageRequest",
        "poll_endpoint": "/studio-ghibli-polling/studio-ghibli/getStatus",
        "mode": "async",
        "id_field": "request_id",
    },
    "z-image": {
        "label": "Z Image",
        "endpoint": "/z-image/v1/z-image/generateTextToImageRequest",
        "poll_endpoint": "/z-image-polling/z-image/getStatus",
        "mode": "async",
        "id_field": "request_id",
    },
    "auraflow": {
        "label": "Auraflow",
        "endpoint": "/auraflow/v1/auraflow/generateTextToImageRequest",
        "poll_endpoint": "/auraflow-polling/auraflow/getStatus",
        "mode": "async",
        "id_field": "request_id",
    },
    "p-image": {
        "label": "P Image (Pruna AI)",
        "endpoint": "/p-image/v1/p-image/generateTextToImageRequest",
        "poll_endpoint": "/p-image-polling/p-image/getStatus",
        "mode": "async",
        "id_field": "request_id",
    },
    "firered-image-edit": {
        "label": "FireRed Image Edit",
        "endpoint": "/firered-image-edit/v1/firered-image-edit/generateTextToImageRequest",
        "poll_endpoint": "/firered-image-edit-polling/firered-image-edit/getStatus",
        "mode": "async",
        "id_field": "request_id",
    },
    "wan": {
        "label": "Wan (Alibaba)",
        "endpoint": "/wan/v1/wan/generateTextToImageRequest",
        "poll_endpoint": "/wan-polling/wan/getStatus",
        "mode": "async",
        "id_field": "request_id",
    },
    "kling": {
        "label": "Kling (Kuaishou)",
        "endpoint": "/kling/v1/kling/generateTextToImageRequest",
        "poll_endpoint": "/kling-polling/kling/getStatus",
        "mode": "async",
        "id_field": "request_id",
    },
}

# Build select options for schema
_MODEL_OPTIONS = ["custom"] + sorted(PIXAZO_MODELS.keys())


class PixazoImageService(BaseImageGenerationService):
    TYPE = "pixazoImageGeneration"
    VERSION = "2.0.0"
    NAME = "Pixazo Image Generation"
    DESCRIPTION = "Generate images via Pixazo API (SDXL, Flux, Recraft, Nano Banana, GPT Image, DALL-E, and more)"

    def get_parameter_schema(self) -> dict:
        return {
            "api_key": {
                "type": "string",
                "required": True,
                "sensitive": True,
                "description": "Pixazo API key (Ocp-Apim-Subscription-Key)",
            },
            "model": {
                "type": "select",
                "required": False,
                "default": "sdxl",
                "options": _MODEL_OPTIONS,
                "description": "Pixazo model preset. Use 'custom' with custom_endpoint for unlisted models.",
            },
            "custom_endpoint": {
                "type": "string",
                "required": False,
                "default": "",
                "description": "Custom generate endpoint path (e.g. '/mymodel/v1/generate'). Only used when model='custom'.",
                "show_when": {"model": ["custom"]},
            },
            "custom_poll_endpoint": {
                "type": "string",
                "required": False,
                "default": "",
                "description": "Custom polling endpoint (empty = sync mode). Only used when model='custom'.",
                "show_when": {"model": ["custom"]},
            },
            "poll_interval": {
                "type": "integer",
                "required": False,
                "default": 5,
                "description": "Polling interval in seconds (for async models)",
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
        self.timeout = int(self.config.get("timeout", 600))
        self.poll_interval = int(self.config.get("poll_interval", 5))
        self.max_retries = int(self.config.get("max_retries", 5))

        model_key = self.config.get("model", "sdxl")
        if model_key == "custom":
            self._endpoint = self.config.get("custom_endpoint", "")
            self._poll_endpoint = self.config.get("custom_poll_endpoint", "")
            self._mode = "async" if self._poll_endpoint else "sync"
            self._id_field = "request_id"
            self._url_field = "output"
        elif model_key in PIXAZO_MODELS:
            m = PIXAZO_MODELS[model_key]
            self._endpoint = m["endpoint"]
            self._poll_endpoint = m.get("poll_endpoint", "")
            self._mode = m["mode"]
            self._id_field = m.get("id_field", "request_id")
            self._url_field = m.get("url_field", "output")
        else:
            # Fallback: treat as SDXL
            self._endpoint = PIXAZO_MODELS["sdxl"]["endpoint"]
            self._poll_endpoint = ""
            self._mode = "sync"
            self._id_field = ""
            self._url_field = "imageUrl"

    def _create_connection(self):
        if not self.api_key:
            raise ServiceError("api_key is required for Pixazo service")
        if not self._endpoint:
            raise ServiceError("No endpoint configured (set model or custom_endpoint)")
        return {"ready": True}

    def _close_connection(self):
        pass

    def _make_headers(self, body_bytes):
        return {
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
            "Ocp-Apim-Subscription-Key": self.api_key,
            "Content-Length": str(len(body_bytes)),
        }

    def _post(self, endpoint, body_dict) -> dict:
        """POST to Pixazo gateway with retry on 500."""
        json_body = json.dumps(body_dict).encode("utf-8")
        headers = self._make_headers(json_body)
        ctx = ssl.create_default_context()
        resp_body = ""

        for attempt in range(self.max_retries):
            conn = http.client.HTTPSConnection(
                "gateway.pixazo.ai", timeout=self.timeout, context=ctx
            )
            conn.request("POST", endpoint, body=json_body, headers=headers)
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

        return json.loads(resp_body) if resp_body.strip() else {}

    def _download_image(self, url) -> tuple:
        """Download image from URL. Returns (bytes, content_type)."""
        req = urllib.request.Request(url, headers={"User-Agent": "PawFlow-Agent/1.0"})
        with urllib.request.urlopen(req, timeout=60) as img_resp:
            return img_resp.read(), img_resp.headers.get("Content-Type", "image/png")

    def _extract_image_url(self, data) -> str:
        """Extract image URL from response data, trying common fields."""
        if not isinstance(data, dict):
            return ""
        # Try configured field first
        val = data.get(self._url_field, "")
        if val:
            return val[0] if isinstance(val, list) else val
        # Try common fields
        for field in ("imageUrl", "output", "image_url", "url", "image"):
            val = data.get(field, "")
            if val:
                return val[0] if isinstance(val, list) else val
        # Check nested: images[0].url
        images = data.get("images", [])
        if images and isinstance(images, list):
            first = images[0]
            if isinstance(first, dict):
                return first.get("url", "") or first.get("image_url", "")
            if isinstance(first, str):
                return first
        return ""

    def _poll_for_result(self, request_id) -> str:
        """Poll async endpoint until image URL is available. No timeout — waits forever.
        Cancel via agent interrupt (/stop) which raises AgentCancelled."""
        start = time.time()
        while True:
            time.sleep(self.poll_interval)
            data = self._post(self._poll_endpoint, {
                self._id_field: request_id,
                "request_id": request_id,
                "requestId": request_id,
            })
            status = (data.get("status", "") or "").lower()
            elapsed = int(time.time() - start)
            logger.info("[PIXAZO] Poll %s (%ds): status=%s, keys=%s",
                        self._poll_endpoint, elapsed, status,
                        list(data.keys()) if isinstance(data, dict) else type(data).__name__)
            if status in ("completed", "done", "success", "ready"):
                url = self._extract_image_url(data)
                if url:
                    return url
                raise ServiceError(f"Pixazo completed but no image URL: {json.dumps(data)[:300]}")
            if status in ("failed", "error"):
                msg = data.get("message", "") or data.get("error", "") or str(data)[:200]
                raise ServiceError(f"Pixazo generation failed: {msg}")

    def generate(self, prompt="", negative_prompt="", width=1024, height=1024,
                 steps=20, **kwargs) -> dict:
        """Generate an image via Pixazo API.

        Returns:
            {"image_bytes": bytes, "content_type": str}
        """
        if not prompt:
            raise ServiceError("No prompt provided")
        self.ensure_connected()

        # Build request body — include all common fields, models ignore what they don't need
        body = {"prompt": prompt}
        if negative_prompt:
            body["negative_prompt"] = negative_prompt
        if width and height:
            body["width"] = max(256, int(width))
            body["height"] = max(256, int(height))
            body["size"] = f"{body['width']}x{body['height']}"
        if steps:
            body["num_steps"] = max(1, min(50, int(steps)))
            body["num_inference_steps"] = body["num_steps"]
        body["guidance_scale"] = kwargs.get("guidance_scale", 5)
        body["seed"] = kwargs.get("seed", int(time.time()) % 1000000)
        body["num_images"] = 1
        # Pass through any extra kwargs (aspect_ratio, style, output_format, etc.)
        for k, v in kwargs.items():
            if k not in body and k not in ("destination", "path", "service"):
                body[k] = v

        logger.info("[PIXAZO] %s request: prompt=%s..., endpoint=%s",
                     self._mode, prompt[:80], self._endpoint)

        data = self._post(self._endpoint, body)
        logger.info("[PIXAZO] Generate response: %s", json.dumps(data)[:300])

        if self._mode == "sync":
            # Sync: response contains the image URL directly
            image_url = self._extract_image_url(data)
            if not image_url:
                raise ServiceError(f"No image URL in Pixazo response: {json.dumps(data)[:300]}")
        else:
            # Async: response contains a request ID, poll for result
            request_id = ""
            for field in (self._id_field, "request_id", "requestId", "id"):
                request_id = data.get(field, "")
                if request_id:
                    break
            if not request_id:
                # Maybe it returned the URL directly anyway
                image_url = self._extract_image_url(data)
                if image_url:
                    pass  # skip polling
                else:
                    raise ServiceError(f"No request ID in Pixazo response: {json.dumps(data)[:300]}")
            else:
                image_url = self._poll_for_result(request_id)

        image_bytes, content_type = self._download_image(image_url)
        return {"image_bytes": image_bytes, "content_type": content_type}


ServiceFactory.register(PixazoImageService)
