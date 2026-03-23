"""Pixazo video generation service — supports all Pixazo video models.

Implements BaseVideoGenerationService for the Pixazo gateway API.
Async: POST generate -> id, POST poll -> status/url, download video.
Supports multiple models via configurable endpoint or preset.
"""

import json
import logging
import time
import http.client
import ssl
import urllib.request

from core import ServiceFactory, ServiceError
from services.base_video_generation import BaseVideoGenerationService

logger = logging.getLogger(__name__)

_GATEWAY = "gateway.pixazo.ai"

# Known Pixazo video models with their endpoints
PIXAZO_VIDEO_MODELS = {
    "sora": {
        "label": "Sora (OpenAI)",
        "generate_endpoint": "/sora-video/v1/video/generate",
        "poll_endpoint": "/sora-video/v1/video/result",
        "id_field": "id",
        "poll_id_field": "video_id",
    },
    "p-video": {
        "label": "P Video (Pruna AI)",
        "generate_endpoint": "/p-video/v1/p-video/generateTextToVideoRequest",
        "poll_endpoint": "/p-video-polling/p-video/getStatus",
        "id_field": "request_id",
        "poll_id_field": "request_id",
    },
    "seedance": {
        "label": "Seedance (BytePlus)",
        "generate_endpoint": "/seedance/v1/seedance/generateTextToVideoRequest",
        "poll_endpoint": "/seedance-polling/seedance/getStatus",
        "id_field": "request_id",
        "poll_id_field": "request_id",
    },
    "veo": {
        "label": "Veo (Google)",
        "generate_endpoint": "/veo/v1/veo/generateTextToVideoRequest",
        "poll_endpoint": "/veo-polling/veo/getStatus",
        "id_field": "request_id",
        "poll_id_field": "request_id",
    },
    "runway": {
        "label": "Runway",
        "generate_endpoint": "/runway/v1/runway/generateTextToVideoRequest",
        "poll_endpoint": "/runway-polling/runway/getStatus",
        "id_field": "request_id",
        "poll_id_field": "request_id",
    },
    "kling": {
        "label": "Kling (Kuaishou)",
        "generate_endpoint": "/kling/v1/kling/generateTextToVideoRequest",
        "poll_endpoint": "/kling-polling/kling/getStatus",
        "id_field": "request_id",
        "poll_id_field": "request_id",
    },
    "pika": {
        "label": "Pika (Pika Labs)",
        "generate_endpoint": "/pika/v1/pika/generateTextToVideoRequest",
        "poll_endpoint": "/pika-polling/pika/getStatus",
        "id_field": "request_id",
        "poll_id_field": "request_id",
    },
    "luma-dream-machine": {
        "label": "Luma Dream Machine (Luma AI)",
        "generate_endpoint": "/luma-dream-machine/v1/luma-dream-machine/generateTextToVideoRequest",
        "poll_endpoint": "/luma-dream-machine-polling/luma-dream-machine/getStatus",
        "id_field": "request_id",
        "poll_id_field": "request_id",
    },
    "hailuo": {
        "label": "Hailuo (MiniMax)",
        "generate_endpoint": "/hailuo/v1/hailuo/generateTextToVideoRequest",
        "poll_endpoint": "/hailuo-polling/hailuo/getStatus",
        "id_field": "request_id",
        "poll_id_field": "request_id",
    },
    "mochi": {
        "label": "Mochi",
        "generate_endpoint": "/mochi/v1/mochi/generateTextToVideoRequest",
        "poll_endpoint": "/mochi-polling/mochi/getStatus",
        "id_field": "request_id",
        "poll_id_field": "request_id",
    },
    "vidu": {
        "label": "Vidu",
        "generate_endpoint": "/vidu/v1/vidu/generateTextToVideoRequest",
        "poll_endpoint": "/vidu-polling/vidu/getStatus",
        "id_field": "request_id",
        "poll_id_field": "request_id",
    },
    "wan": {
        "label": "Wan (Alibaba)",
        "generate_endpoint": "/wan/v1/wan/generateTextToVideoRequest",
        "poll_endpoint": "/wan-polling/wan/getStatus",
        "id_field": "request_id",
        "poll_id_field": "request_id",
    },
    "ltx": {
        "label": "LTX (Lightricks)",
        "generate_endpoint": "/ltx/v1/ltx/generateTextToVideoRequest",
        "poll_endpoint": "/ltx-polling/ltx/getStatus",
        "id_field": "request_id",
        "poll_id_field": "request_id",
    },
    "veed": {
        "label": "Veed",
        "generate_endpoint": "/veed/v1/veed/generateTextToVideoRequest",
        "poll_endpoint": "/veed-polling/veed/getStatus",
        "id_field": "request_id",
        "poll_id_field": "request_id",
    },
    "pixverse": {
        "label": "Pixverse",
        "generate_endpoint": "/pixverse/v1/pixverse/generateTextToVideoRequest",
        "poll_endpoint": "/pixverse-polling/pixverse/getStatus",
        "id_field": "request_id",
        "poll_id_field": "request_id",
    },
    "lucy-edit": {
        "label": "Lucy Edit (Decart)",
        "generate_endpoint": "/lucy-edit/v1/lucy-edit/generateTextToVideoRequest",
        "poll_endpoint": "/lucy-edit-polling/lucy-edit/getStatus",
        "id_field": "request_id",
        "poll_id_field": "request_id",
    },
    "higgsfield": {
        "label": "Higgsfield",
        "generate_endpoint": "/higgsfield/v1/higgsfield/generateTextToVideoRequest",
        "poll_endpoint": "/higgsfield-polling/higgsfield/getStatus",
        "id_field": "request_id",
        "poll_id_field": "request_id",
    },
    "genflare": {
        "label": "GenFlare (Baidu)",
        "generate_endpoint": "/genflare/v1/genflare/generateTextToVideoRequest",
        "poll_endpoint": "/genflare-polling/genflare/getStatus",
        "id_field": "request_id",
        "poll_id_field": "request_id",
    },
    "kandinsky": {
        "label": "Kandinsky",
        "generate_endpoint": "/kandinsky/v1/kandinsky/generateTextToVideoRequest",
        "poll_endpoint": "/kandinsky-polling/kandinsky/getStatus",
        "id_field": "request_id",
        "poll_id_field": "request_id",
    },
    "stable-diffusion": {
        "label": "Stable Diffusion (Stability AI)",
        "generate_endpoint": "/sd-video/v1/sd-video/generateTextToVideoRequest",
        "poll_endpoint": "/sd-video-polling/sd-video/getStatus",
        "id_field": "request_id",
        "poll_id_field": "request_id",
    },
    "grok": {
        "label": "Grok (xAI)",
        "generate_endpoint": "/grok/v1/grok/generateTextToVideoRequest",
        "poll_endpoint": "/grok-polling/grok/getVideoStatus",
        "id_field": "request_id",
        "poll_id_field": "request_id",
    },
}

_VIDEO_MODEL_OPTIONS = ["custom"] + sorted(PIXAZO_VIDEO_MODELS.keys())


class PixazoVideoService(BaseVideoGenerationService):
    TYPE = "pixazoVideoGeneration"
    VERSION = "2.0.0"
    NAME = "Pixazo Video Generation"
    DESCRIPTION = "Generate videos via Pixazo API (Sora, Runway, Kling, Pika, Veo, and more)"

    def get_parameter_schema(self) -> dict:
        return {
            "api_key": {
                "type": "string", "required": True, "sensitive": True,
                "description": "Pixazo API key (Ocp-Apim-Subscription-Key)",
            },
            "model": {
                "type": "select", "required": False,
                "default": "sora",
                "options": _VIDEO_MODEL_OPTIONS,
                "description": "Pixazo video model. Use 'custom' with custom endpoints for unlisted models.",
            },
            "custom_generate_endpoint": {
                "type": "string", "required": False, "default": "",
                "description": "Custom generate endpoint (only when model='custom')",
                "show_when": {"model": ["custom"]},
            },
            "custom_poll_endpoint": {
                "type": "string", "required": False, "default": "",
                "description": "Custom poll endpoint (only when model='custom')",
                "show_when": {"model": ["custom"]},
            },
            "poll_interval": {
                "type": "integer", "required": False, "default": 10,
                "description": "Seconds between status polls",
            },
        }

    def __init__(self, config):
        super().__init__(config)
        self.api_key = self.config.get("api_key", "")
        self.timeout = int(self.config.get("timeout", 600))
        self.poll_interval = int(self.config.get("poll_interval", 10))

        model_key = self.config.get("model", "sora")
        if model_key == "custom":
            self._gen_endpoint = self.config.get("custom_generate_endpoint", "")
            self._poll_endpoint = self.config.get("custom_poll_endpoint", "")
            self._id_field = "request_id"
            self._poll_id_field = "request_id"
        elif model_key in PIXAZO_VIDEO_MODELS:
            m = PIXAZO_VIDEO_MODELS[model_key]
            self._gen_endpoint = m["generate_endpoint"]
            self._poll_endpoint = m["poll_endpoint"]
            self._id_field = m["id_field"]
            self._poll_id_field = m["poll_id_field"]
        else:
            # Fallback to Sora
            m = PIXAZO_VIDEO_MODELS["sora"]
            self._gen_endpoint = m["generate_endpoint"]
            self._poll_endpoint = m["poll_endpoint"]
            self._id_field = m["id_field"]
            self._poll_id_field = m["poll_id_field"]

    def _create_connection(self):
        if not self.api_key:
            raise ServiceError("api_key is required for Pixazo video service")
        if not self._gen_endpoint:
            raise ServiceError("No endpoint configured (set model or custom endpoints)")
        return {"ready": True}

    def _close_connection(self):
        pass

    def _gateway_post(self, path: str, body: dict) -> dict:
        """POST to Pixazo gateway."""
        ctx = ssl.create_default_context()
        json_body = json.dumps(body).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
            "Ocp-Apim-Subscription-Key": self.api_key,
            "Content-Length": str(len(json_body)),
        }
        conn = http.client.HTTPSConnection(_GATEWAY, timeout=60, context=ctx)
        conn.request("POST", path, body=json_body, headers=headers)
        resp = conn.getresponse()
        resp_body = resp.read().decode("utf-8", errors="replace")
        conn.close()
        if resp.status >= 400:
            raise ServiceError(f"Pixazo API error ({resp.status}): {resp_body[:300]}")
        return json.loads(resp_body) if resp_body.strip() else {}

    def generate(self, prompt="", negative_prompt="", duration=8,
                 width=1280, height=720, **kwargs) -> dict:
        if not prompt:
            raise ServiceError("No prompt provided")
        self.ensure_connected()

        size = f"{int(width)}x{int(height)}" if width and height else "1280x720"
        seconds = max(4, min(30, int(duration)))

        body = {
            "prompt": prompt,
            "size": size,
            "seconds": seconds,
        }
        if negative_prompt:
            body["negative_prompt"] = negative_prompt
        # Pass through extra kwargs (model sub-variant, aspect_ratio, etc.)
        for k, v in kwargs.items():
            if k not in body and k not in ("destination", "path", "service"):
                body[k] = v

        logger.info("[PIXAZO-VIDEO] Generating: prompt=%s..., model=%s, endpoint=%s, size=%s, duration=%ds",
                    prompt[:80], self.config.get("model", "sora"),
                    self._gen_endpoint, size, seconds)

        # Submit generation
        result = self._gateway_post(self._gen_endpoint, body)

        # Extract request/video ID
        req_id = ""
        for field in (self._id_field, "request_id", "requestId", "id", "video_id"):
            req_id = result.get(field, "")
            if req_id:
                break
        if not req_id:
            raise ServiceError(f"No request ID in Pixazo response: {json.dumps(result)[:300]}")

        # Poll for completion — no timeout, waits forever.
        # Cancel via agent interrupt (/stop) which raises AgentCancelled.
        start = time.time()
        while True:
            time.sleep(self.poll_interval)
            poll_body = {self._poll_id_field: req_id, "request_id": req_id, "video_id": req_id}
            status = self._gateway_post(self._poll_endpoint, poll_body)
            state = (status.get("status") or "").lower()
            elapsed = int(time.time() - start)
            logger.info("[PIXAZO-VIDEO] Poll %s (%ds): status=%s",
                        req_id[:16], elapsed, state)

            if state in ("completed", "done", "success", "ready"):
                video_url = (status.get("video_url", "")
                             or status.get("output", "")
                             or status.get("url", ""))
                if isinstance(video_url, list):
                    video_url = video_url[0] if video_url else ""
                if not video_url:
                    raise ServiceError(f"No video URL in completed response: "
                                       f"{json.dumps(status)[:300]}")
                return self._download_video(video_url)

            if state in ("failed", "error"):
                raise ServiceError(f"Pixazo video generation failed: "
                                   f"{status.get('message', state)}")

    def _download_video(self, url: str) -> dict:
        req = urllib.request.Request(url, headers={"User-Agent": "PawFlow-Agent/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            video_bytes = resp.read()
            content_type = resp.headers.get("Content-Type", "video/mp4")
        return {"video_bytes": video_bytes, "content_type": content_type}


ServiceFactory.register(PixazoVideoService)
