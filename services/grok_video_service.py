"""Grok Imagine video generation service (xAI).

Implements BaseVideoGenerationService for the xAI Grok Imagine API.
Async: POST /v1/videos/generations -> request_id, GET /v1/videos/{id} to poll.
"""

import json
import logging
import time
import urllib.request

from core import ServiceFactory, ServiceError
from services.base_video_generation import BaseVideoGenerationService

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.x.ai/v1"


class GrokVideoService(BaseVideoGenerationService):
    TYPE = "grokVideoGeneration"
    VERSION = "1.0.0"
    NAME = "Grok Imagine Video Generation"
    DESCRIPTION = "Generate videos via xAI Grok Imagine API"

    def get_parameter_schema(self) -> dict:
        return {
            "api_key": {
                "type": "string", "required": True, "sensitive": True,
                "description": "xAI API key (Bearer token)",
            },
            "model": {
                "type": "string", "required": False,
                "default": "grok-imagine-video",
                "description": "Model: grok-imagine-video",
            },
            "timeout": {
                "type": "integer", "required": False, "default": 300,
                "description": "Max wait time for video generation (seconds)",
            },
            "poll_interval": {
                "type": "integer", "required": False, "default": 5,
                "description": "Seconds between status polls",
            },
        }

    def __init__(self, config):
        super().__init__(config)
        self.api_key = self.config.get("api_key", "")
        self.model = self.config.get("model", "grok-imagine-video")
        self.timeout = int(self.config.get("timeout", 300))
        self.poll_interval = int(self.config.get("poll_interval", 5))

    def _create_connection(self):
        if not self.api_key:
            raise ServiceError("api_key is required for Grok Imagine service")
        return {"ready": True}

    def _close_connection(self):
        pass

    def _api_request(self, method, path, body=None):
        """Make an authenticated request to xAI API."""
        url = f"{_BASE_URL}{path}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        data = json.dumps(body).encode("utf-8") if body else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=60) as resp:  # nosec B310 - configured Grok API endpoint.
            return json.loads(resp.read().decode("utf-8"))

    def generate(self, prompt="", duration=10, width=None, height=None, **_) -> dict:
        if not prompt:
            raise ServiceError("No prompt provided")
        self.ensure_connected()

        # Map width/height to aspect_ratio
        aspect_ratio = "16:9"
        if width and height:
            ratio = width / height
            if ratio < 0.8:
                aspect_ratio = "9:16"
            elif 0.8 <= ratio <= 1.2:
                aspect_ratio = "1:1"
            elif ratio > 1.5:
                aspect_ratio = "16:9"
            else:
                aspect_ratio = "4:3"

        body = {
            "model": self.model,
            "prompt": prompt,
            "duration": max(1, min(15, int(duration))),
            "aspect_ratio": aspect_ratio,
            "resolution": "720p",
        }

        logger.info("[GROK-VIDEO] Generating: prompt=%s..., duration=%d, model=%s",
                    prompt[:80], body["duration"], self.model)

        # Submit generation request
        result = self._api_request("POST", "/videos/generations", body)
        request_id = result.get("request_id", "")
        if not request_id:
            raise ServiceError(f"No request_id in xAI response: {json.dumps(result)[:300]}")

        # Poll for completion
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            time.sleep(self.poll_interval)
            status = self._api_request("GET", f"/videos/{request_id}")
            state = (status.get("status") or "").lower()
            if state == "done":
                video_info = status.get("video", {})
                video_url = video_info.get("url", "")
                if not video_url:
                    raise ServiceError(f"No video URL in completed response: "
                                       f"{json.dumps(status)[:300]}")
                return self._download_video(video_url)
            if state in ("failed", "expired"):
                raise ServiceError(f"Grok video generation {state}: "
                                   f"{status.get('error', state)}")
            logger.debug("[GROK-VIDEO] %s status: %s", request_id[:12], state)

        raise ServiceError(f"Grok video generation timed out after {self.timeout}s")

    def _download_video(self, url: str) -> dict:
        req = urllib.request.Request(url, headers={"User-Agent": "PawFlow-Agent/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp:  # nosec B310 - provider-returned video download URL.
            video_bytes = resp.read()
            content_type = resp.headers.get("Content-Type", "video/mp4")
        return {"video_bytes": video_bytes, "content_type": content_type}


ServiceFactory.register(GrokVideoService)
