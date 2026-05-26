"""OpenAI Sora video generation service.

Implements BaseVideoGenerationService for the OpenAI video generation API.
Uses the OpenAI-compatible REST API (no SDK dependency).
"""

import json
import logging
import time
import urllib.request

from core import ServiceFactory, ServiceError
from core.relay_proxy_url import resolve_relay_aware_url
from services.base_video_generation import BaseVideoGenerationService

logger = logging.getLogger(__name__)


def _truthy(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _raw_config(config: dict, key: str, default=""):
    try:
        return dict.__getitem__(config, key)
    except KeyError:
        return default


class SoraVideoService(BaseVideoGenerationService):
    TYPE = "soraVideoGeneration"
    VERSION = "1.0.0"
    NAME = "OpenAI Sora Video Generation"
    DESCRIPTION = "Generate videos via OpenAI Sora API"

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
                "default": "sora-2",
                "description": "Model: sora-2, sora-2-2025-12-08",
            },
            "timeout": {
                "type": "integer", "required": False, "default": 600,
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
        self.base_url = str(_raw_config(self.config, "base_url", "https://api.openai.com/v1") or "https://api.openai.com/v1").rstrip("/")
        self._raw_base_url = self.base_url
        self.allow_private_base_url = _truthy(self.config.get("allow_private_base_url", False))
        self._runtime_user_id = ""
        self._runtime_conversation_id = ""
        self._runtime_agent_name = ""
        self.model = self.config.get("model", "sora-2")
        self.timeout = int(self.config.get("timeout", 600))
        self.poll_interval = int(self.config.get("poll_interval", 5))

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
            service_name="Sora",
            transform_relay=True,
        )

    def _create_connection(self):
        if not self.api_key:
            raise ServiceError("api_key is required for Sora service")
        self.base_url = resolve_relay_aware_url(
            self._raw_base_url,
            allow_private=self.allow_private_base_url,
            service_name="Sora",
            transform_relay=False,
        )
        return {"ready": True, "base_url": self.base_url}

    def _close_connection(self):
        pass

    def _api_request(self, method, path, body=None):
        """Make an authenticated request to OpenAI API."""
        url = f"{self._effective_base_url()}{path}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        data = json.dumps(body).encode("utf-8") if body else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=60) as resp:  # nosec B310 - configured Sora API endpoint.
            return json.loads(resp.read().decode("utf-8"))

    def generate(self, prompt="", duration=10, width=1280, height=720, **_) -> dict:
        if not prompt:
            raise ServiceError("No prompt provided")
        self.ensure_connected()

        # Sora supports specific sizes
        size = f"{int(width)}x{int(height)}" if width and height else "1280x720"

        body = {
            "model": self.model,
            "input": [
                {"type": "text", "text": prompt},
            ],
            "size": size,
            "duration": max(1, min(20, int(duration))),
        }

        logger.info("[SORA] Generating: prompt=%s..., model=%s, size=%s, duration=%ds",
                    prompt[:80], self.model, size, body["duration"])

        # Submit generation request
        result = self._api_request("POST", "/video/generations", body)
        gen_id = result.get("id", "")
        if not gen_id:
            # Some API versions return the video directly (synchronous)
            if result.get("data"):
                return self._handle_sync_response(result)
            raise ServiceError(f"No generation id in Sora response: "
                               f"{json.dumps(result)[:300]}")

        # Poll for completion
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            time.sleep(self.poll_interval)
            status = self._api_request("GET", f"/video/generations/{gen_id}")
            state = (status.get("status") or "").lower()
            if state in ("completed", "succeeded"):
                return self._extract_video(status)
            if state in ("failed", "error"):
                raise ServiceError(f"Sora generation failed: "
                                   f"{status.get('error', state)}")
            logger.debug("[SORA] %s status: %s", gen_id[:12], state)

        raise ServiceError(f"Sora generation timed out after {self.timeout}s")

    def _handle_sync_response(self, result: dict) -> dict:
        """Handle synchronous response with data array."""
        data = result.get("data", [])
        if not data:
            raise ServiceError("Empty data in Sora response")
        video_url = data[0].get("url", "")
        if not video_url:
            raise ServiceError(f"No video URL in Sora response: "
                               f"{json.dumps(result)[:300]}")
        return self._download_video(video_url)

    def _extract_video(self, status: dict) -> dict:
        """Extract video from completed generation status."""
        # Try various response formats
        video_url = ""
        if status.get("output", {}).get("video"):
            video_url = status["output"]["video"]
        elif status.get("data"):
            items = status["data"]
            if items and isinstance(items, list):
                video_url = items[0].get("url", "")
        elif status.get("video_url"):
            video_url = status["video_url"]

        if not video_url:
            raise ServiceError(f"Cannot find video URL in response: "
                               f"{json.dumps(status)[:300]}")
        return self._download_video(video_url)

    def _download_video(self, url: str) -> dict:
        req = urllib.request.Request(url, headers={"User-Agent": "PawFlow-Agent/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp:  # nosec B310 - provider-returned video download URL.
            video_bytes = resp.read()
            content_type = resp.headers.get("Content-Type", "video/mp4")
        return {"video_bytes": video_bytes, "content_type": content_type}


ServiceFactory.register(SoraVideoService)
