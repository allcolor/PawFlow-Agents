"""Pixazo video generation service.

Implements BaseVideoGenerationService for the Pixazo gateway API.
Async: POST /generate -> video_id, POST /result to poll, download video.
Supports multiple models (Sora 2 Pro, etc.) via the same gateway.
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


class PixazoVideoService(BaseVideoGenerationService):
    TYPE = "pixazoVideoGeneration"
    VERSION = "1.0.0"
    NAME = "Pixazo Video Generation"
    DESCRIPTION = "Generate videos via Pixazo gateway (Sora 2 Pro and other models)"

    def get_parameter_schema(self) -> dict:
        return {
            "api_key": {
                "type": "string", "required": True, "sensitive": True,
                "description": "Pixazo API key (Ocp-Apim-Subscription-Key)",
            },
            "model": {
                "type": "string", "required": False,
                "default": "sora-2-pro",
                "description": "Video model (sora-2-pro, etc.)",
            },
            "timeout": {
                "type": "integer", "required": False, "default": 600,
                "description": "Max wait time for video generation (seconds)",
            },
            "poll_interval": {
                "type": "integer", "required": False, "default": 10,
                "description": "Seconds between status polls",
            },
        }

    def __init__(self, config):
        super().__init__(config)
        self.api_key = self.config.get("api_key", "")
        self.model = self.config.get("model", "sora-2-pro")
        self.timeout = int(self.config.get("timeout", 600))
        self.poll_interval = int(self.config.get("poll_interval", 10))

    def _create_connection(self):
        if not self.api_key:
            raise ServiceError("api_key is required for Pixazo video service")
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
        return json.loads(resp_body)

    def generate(self, prompt="", negative_prompt="", duration=8,
                 width=1280, height=720, **_) -> dict:
        if not prompt:
            raise ServiceError("No prompt provided")
        self.ensure_connected()

        # Map width/height to size string
        size = f"{int(width)}x{int(height)}" if width and height else "1280x720"
        seconds = max(4, min(12, int(duration)))

        body = {
            "prompt": prompt,
            "model": self.model,
            "size": size,
            "seconds": seconds,
        }

        logger.info("[PIXAZO-VIDEO] Generating: prompt=%s..., model=%s, size=%s, duration=%ds",
                    prompt[:80], self.model, size, seconds)

        # Submit generation
        result = self._gateway_post("/sora-video/v1/video/generate", body)
        video_id = result.get("id", "")
        if not video_id:
            raise ServiceError(f"No video_id in Pixazo response: {json.dumps(result)[:300]}")

        # Poll for completion
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            time.sleep(self.poll_interval)
            status = self._gateway_post("/sora-video/v1/video/result", {"video_id": video_id})
            state = (status.get("status") or "").lower()
            if state == "completed":
                video_url = status.get("video_url", "")
                if not video_url:
                    raise ServiceError(f"No video_url in completed response: "
                                       f"{json.dumps(status)[:300]}")
                return self._download_video(video_url)
            if state in ("failed", "error"):
                raise ServiceError(f"Pixazo video generation failed: "
                                   f"{status.get('message', state)}")
            logger.debug("[PIXAZO-VIDEO] %s status: %s", video_id[:16], state)

        raise ServiceError(f"Pixazo video generation timed out after {self.timeout}s")

    def _download_video(self, url: str) -> dict:
        req = urllib.request.Request(url, headers={"User-Agent": "PyFi2-Agent/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            video_bytes = resp.read()
            content_type = resp.headers.get("Content-Type", "video/mp4")
        return {"video_bytes": video_bytes, "content_type": content_type}


ServiceFactory.register(PixazoVideoService)
