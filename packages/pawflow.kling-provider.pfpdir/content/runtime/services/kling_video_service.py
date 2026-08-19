"""Kling AI video generation service.

Implements BaseVideoGenerationService for the Kling API.
Async: POST to generate, poll task_id for result, download video.
"""

import json
import logging
import time
import urllib.request

from core import ServiceFactory, ServiceError
from services.base_video_generation import BaseVideoGenerationService

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.klingapi.com"


class KlingVideoService(BaseVideoGenerationService):
    TYPE = "klingVideoGeneration"
    VERSION = "1.0.0"
    NAME = "Kling AI Video Generation"
    DESCRIPTION = "Generate videos via Kling API (text-to-video)"

    def get_parameter_schema(self) -> dict:
        return {
            "api_key": {
                "type": "string", "required": True, "sensitive": True,
                "description": "Kling API key (Bearer token)",
            },
            "model": {
                "type": "string", "required": False,
                "default": "kling-v2.6-std",
                "description": "Model: kling-v2.6-pro, kling-v2.6-std, kling-v2.5-turbo",
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
        self.model = self.config.get("model", "kling-v2.6-std")
        self.timeout = int(self.config.get("timeout", 300))
        self.poll_interval = int(self.config.get("poll_interval", 5))

    def _create_connection(self):
        if not self.api_key:
            raise ServiceError("api_key is required for Kling service")
        return {"ready": True}

    def _close_connection(self):
        pass

    def _api_request(self, method, path, body=None):
        """Make an authenticated request to Kling API."""
        url = f"{_BASE_URL}{path}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        data = json.dumps(body).encode("utf-8") if body else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=60) as resp:  # nosec B310 - configured Kling API endpoint.
            return json.loads(resp.read().decode("utf-8"))

    def generate(self, prompt="", negative_prompt="", duration=5,
                 width=None, height=None, **_) -> dict:
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

        body = {
            "model": self.model,
            "prompt": prompt,
            "duration": max(5, min(10, int(duration))),
            "aspect_ratio": aspect_ratio,
            "mode": "standard",
        }
        if negative_prompt:
            body["negative_prompt"] = negative_prompt

        logger.info("[KLING] Generating video: prompt=%s..., duration=%d, model=%s",
                    prompt[:80], body["duration"], self.model)

        # Submit generation request
        result = self._api_request("POST", "/v1/videos/text2video", body)
        task_id = result.get("task_id") or result.get("id", "")
        if not task_id:
            raise ServiceError(f"No task_id in Kling response: {json.dumps(result)[:300]}")

        # Poll for completion
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            time.sleep(self.poll_interval)
            status = self._api_request("GET", f"/v1/videos/{task_id}")
            state = status.get("status", "").lower()
            if state in ("completed", "done", "success"):
                video_url = (status.get("video_url")
                             or status.get("output", {}).get("video_url", ""))
                if not video_url:
                    raise ServiceError(f"No video_url in completed response: "
                                       f"{json.dumps(status)[:300]}")
                return self._download_video(video_url)
            if state in ("failed", "error"):
                raise ServiceError(f"Kling generation failed: {status.get('message', state)}")
            logger.debug("[KLING] task %s status: %s", task_id[:12], state)

        raise ServiceError(f"Kling generation timed out after {self.timeout}s")

    def _download_video(self, url: str) -> dict:
        req = urllib.request.Request(url, headers={"User-Agent": "PawFlow-Agent/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp:  # nosec B310 - provider-returned video download URL.
            video_bytes = resp.read()
            content_type = resp.headers.get("Content-Type", "video/mp4")
        return {"video_bytes": video_bytes, "content_type": content_type}


ServiceFactory.register(KlingVideoService)
