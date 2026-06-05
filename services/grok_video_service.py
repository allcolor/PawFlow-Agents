"""Grok Imagine video generation service (xAI direct API)."""

from __future__ import annotations

import base64
import json
import logging
import time
import urllib.error
import urllib.request

from core import ServiceFactory, ServiceError
from services.base_video_generation import BaseVideoGenerationService

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.x.ai/v1"
_DEFAULT_MODEL = "grok-imagine-video"


class GrokVideoService(BaseVideoGenerationService):
    TYPE = "grokVideoGeneration"
    VERSION = "1.0.0"
    NAME = "Grok Imagine Video Generation"
    DESCRIPTION = "Generate and edit videos via the direct xAI Grok Imagine API"
    ACCEPTS_FILESTORE_URLS = True

    def get_parameter_schema(self) -> dict:
        return {
            "api_key": {
                "type": "string", "required": True, "sensitive": True,
                "description": "xAI API key (Bearer token)",
            },
            "model": {
                "type": "string", "required": False,
                "default": _DEFAULT_MODEL,
                "description": "Model: grok-imagine-video",
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
        self.model = self.config.get("model", _DEFAULT_MODEL)
        self.timeout = int(self.config.get("timeout", 600))
        self.poll_interval = int(self.config.get("poll_interval", 5))
        self._runtime_user_id = ""
        self._runtime_conversation_id = ""

    def set_runtime_context(self, user_id: str = "", conversation_id: str = "",
                            **_: object):
        self._runtime_user_id = user_id or ""
        self._runtime_conversation_id = conversation_id or ""

    def _create_connection(self):
        if not self.api_key:
            raise ServiceError("api_key is required for Grok Imagine service")
        return {"ready": True}

    def _close_connection(self):
        pass

    def _headers(self, json_body=True) -> dict:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "User-Agent": "PawFlow-Agent/1.0",
        }
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    def _api_request(self, method: str, path: str, body=None) -> dict:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            f"{_BASE_URL}{path}", data=data, headers=self._headers(body is not None), method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:  # nosec B310 - configured xAI API endpoint.
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:1000].decode("utf-8", errors="replace")
            raise ServiceError(f"xAI video error {method} {path} ({exc.code}): {detail}") from exc

    @staticmethod
    def _aspect_ratio(width=None, height=None, aspect_ratio="") -> str:
        if aspect_ratio:
            return str(aspect_ratio)
        if width and height:
            ratio = width / height
            if ratio < 0.8:
                return "9:16"
            if ratio <= 1.2:
                return "1:1"
            if ratio > 1.5:
                return "16:9"
            return "4:3"
        return "16:9"

    @staticmethod
    def _duration(value, minimum=1, maximum=15, default=10) -> int:
        return max(minimum, min(maximum, int(value or default)))

    def _media_object(self, media_url: str, default_mime: str) -> dict:
        ref = str(media_url or "")
        if ref.startswith("data:"):
            return {"url": ref}
        if ref.startswith("fs://filestore/") or ref.startswith("/files/"):
            from core.file_store import FileStore
            if ref.startswith("fs://filestore/"):
                file_id = ref[len("fs://filestore/"):].split("/", 1)[0]
            else:
                file_id = ref[len("/files/"):].split("/", 1)[0]
            _name, data, content_type = FileStore.instance().get_required(
                file_id,
                user_id=self._runtime_user_id,
                conversation_id=self._runtime_conversation_id,
            )
            mime = content_type or default_mime
            return {"url": f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"}
        return {"url": ref}

    def _submit_and_download(self, path: str, body: dict) -> dict:
        result = self._api_request("POST", path, body)
        request_id = result.get("request_id", "")
        if not request_id:
            raise ServiceError(f"No request_id in xAI response: {json.dumps(result)[:300]}")
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            time.sleep(self.poll_interval)
            status = self._api_request("GET", f"/videos/{request_id}")
            state = (status.get("status") or "").lower()
            if state == "done":
                video_url = (status.get("video") or {}).get("url", "")
                if not video_url:
                    raise ServiceError(f"No video URL in completed response: {json.dumps(status)[:300]}")
                return self._download_video(video_url)
            if state in ("failed", "expired"):
                raise ServiceError(f"Grok video generation {state}: {status.get('error', state)}")
            logger.debug("[GROK-VIDEO] %s status: %s", request_id[:12], state)
        raise ServiceError(f"Grok video generation timed out after {self.timeout}s")

    def _download_video(self, url: str) -> dict:
        req = urllib.request.Request(url, headers={"User-Agent": "PawFlow-Agent/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp:  # nosec B310 - provider-returned video download URL.
            return {
                "video_bytes": resp.read(),
                "content_type": resp.headers.get("Content-Type", "") or "video/mp4",
                "source_url": url,
            }

    def generate(self, prompt="", duration=10, width=None, height=None,
                 aspect_ratio="", resolution="480p", model="", image_url="",
                 reference_image_urls=None, **_) -> dict:
        if not prompt:
            raise ServiceError("No prompt provided")
        self.ensure_connected()
        refs = reference_image_urls or []
        if isinstance(refs, str):
            refs = [refs]
        body = {
            "model": model or self.model,
            "prompt": prompt,
            "duration": self._duration(duration, 1, 15, 10),
            "aspect_ratio": self._aspect_ratio(width, height, aspect_ratio),
            "resolution": resolution if resolution in {"480p", "720p"} else "480p",
        }
        if image_url:
            body["image"] = self._media_object(image_url, "image/png")
        if refs:
            if len(refs) > 7:
                raise ServiceError("Grok reference-to-video supports at most 7 reference images")
            body["duration"] = self._duration(duration, 1, 10, 10)
            body["reference_images"] = [self._media_object(url, "image/png") for url in refs]
        logger.info("[GROK-VIDEO] Generating: prompt=%s..., model=%s", prompt[:80], body["model"])
        return self._submit_and_download("/videos/generations", body)

    def image_to_video(self, prompt="", image_url="", **kwargs) -> dict:
        if not image_url:
            raise ServiceError("image_url is required for Grok image-to-video")
        return self.generate(prompt=prompt, image_url=image_url, **kwargs)

    def reference_to_video(self, prompt="", image_url="", reference_image_urls=None, **kwargs) -> dict:
        refs = reference_image_urls or []
        if image_url:
            refs = [image_url] + ([refs] if isinstance(refs, str) else list(refs))
        if not refs:
            raise ServiceError("reference_image_urls is required for Grok reference-to-video")
        return self.generate(prompt=prompt, reference_image_urls=refs, **kwargs)

    def video_edit(self, prompt="", video_url="", model="", **_) -> dict:
        if not prompt:
            raise ServiceError("No prompt provided")
        if not video_url:
            raise ServiceError("video_url is required for Grok video edit")
        self.ensure_connected()
        body = {
            "model": model or self.model,
            "prompt": prompt,
            "video": self._media_object(video_url, "video/mp4"),
        }
        return self._submit_and_download("/videos/edits", body)

    def video_extend(self, prompt="", video_url="", duration=6, model="", **_) -> dict:
        if not prompt:
            raise ServiceError("No prompt provided")
        if not video_url:
            raise ServiceError("video_url is required for Grok video extension")
        self.ensure_connected()
        body = {
            "model": model or self.model,
            "prompt": prompt,
            "duration": self._duration(duration, 2, 10, 6),
            "video": self._media_object(video_url, "video/mp4"),
        }
        return self._submit_and_download("/videos/extensions", body)


ServiceFactory.register(GrokVideoService)
