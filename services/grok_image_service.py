"""Grok Imagine image generation service (xAI direct API)."""

from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.request

from core import ServiceFactory, ServiceError
from services.base_image_generation import BaseImageGenerationService

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.x.ai/v1"
_DEFAULT_MODEL = "grok-imagine-image-quality"


class GrokImageService(BaseImageGenerationService):
    TYPE = "grokImageGeneration"
    VERSION = "1.0.0"
    NAME = "Grok Imagine Image Generation"
    DESCRIPTION = "Generate and edit images via the direct xAI Grok Imagine API"
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
                "description": "Model: grok-imagine-image-quality",
            },
            "timeout": {
                "type": "integer", "required": False, "default": 120,
                "description": "HTTP request timeout in seconds",
            },
        }

    def __init__(self, config):
        super().__init__(config)
        self.api_key = self.config.get("api_key", "")
        self.model = self.config.get("model", _DEFAULT_MODEL)
        self.timeout = int(self.config.get("timeout", 120))
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

    @staticmethod
    def _aspect_ratio(width=1024, height=1024, aspect_ratio="") -> str:
        if aspect_ratio:
            return str(aspect_ratio)
        if not width or not height:
            return "1:1"
        ratio = width / height
        if ratio > 1.8:
            return "2:1"
        if ratio > 1.4:
            return "16:9"
        if ratio > 1.2:
            return "4:3"
        if ratio < 0.55:
            return "1:2"
        if ratio < 0.7:
            return "9:16"
        if ratio < 0.85:
            return "3:4"
        return "1:1"

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "PawFlow-Agent/1.0",
        }

    def _post_json(self, path: str, body: dict) -> dict:
        req = urllib.request.Request(
            f"{_BASE_URL}{path}",
            data=json.dumps(body).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # nosec B310 - configured xAI API endpoint.
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:1000].decode("utf-8", errors="replace")
            raise ServiceError(f"xAI image error POST {path} ({exc.code}): {detail}") from exc

    def _image_object(self, image_url: str) -> dict:
        ref = str(image_url or "")
        if ref.startswith("data:image/"):
            return {"url": ref, "type": "image_url"}
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
            mime = content_type or "image/png"
            b64 = base64.b64encode(data).decode("ascii")
            return {"url": f"data:{mime};base64,{b64}", "type": "image_url"}
        return {"url": ref, "type": "image_url"}

    @staticmethod
    def _content_type_for_format(output_format: str) -> str:
        return {
            "jpeg": "image/jpeg",
            "jpg": "image/jpeg",
            "webp": "image/webp",
            "png": "image/png",
        }.get(str(output_format or "png").lower(), "image/png")

    def _first_image(self, result: dict, output_format="png") -> dict:
        images = result.get("data", [])
        if not images:
            raise ServiceError(f"No images in response: {json.dumps(result)[:300]}")
        image = images[0]
        b64 = image.get("b64_json") or ""
        if b64:
            return {
                "image_bytes": base64.b64decode(str(b64).split(",", 1)[-1]),
                "content_type": self._content_type_for_format(output_format),
            }
        image_url = image.get("url", "")
        if not image_url:
            raise ServiceError("No image URL or base64 payload in response")
        req = urllib.request.Request(
            image_url, headers={"User-Agent": "PawFlow-Agent/1.0"},
        )
        with urllib.request.urlopen(req, timeout=60) as img_resp:  # nosec B310 - provider-returned image URL.
            return {
                "image_bytes": img_resp.read(),
                "content_type": img_resp.headers.get("Content-Type", "") or image.get("mime_type") or "image/jpeg",
                "source_url": image_url,
            }

    def generate(self, prompt="", negative_prompt="", width=1024, height=1024,
                 aspect_ratio="", resolution="", model="", n=1,
                 response_format="url", output_format="png", **_) -> dict:
        if not prompt:
            raise ServiceError("No prompt provided")
        self.ensure_connected()
        body = {
            "model": model or self.model,
            "prompt": prompt,
            "n": max(1, min(10, int(n or 1))),
            "aspect_ratio": self._aspect_ratio(width, height, aspect_ratio),
            "response_format": response_format if response_format in {"url", "b64_json"} else "url",
        }
        if resolution in ("1k", "2k"):
            body["resolution"] = resolution
        logger.info("[GROK-IMAGE] Generating: prompt=%s..., model=%s", prompt[:80], body["model"])
        return self._first_image(self._post_json("/images/generations", body), output_format)

    def edit_image(self, prompt: str = "", image_urls=None, width=1024, height=1024,
                   aspect_ratio="", resolution="", model="", n=1,
                   response_format="url", output_format="png", **_) -> dict:
        if not prompt:
            raise ServiceError("No prompt provided")
        if isinstance(image_urls, str):
            image_urls = [image_urls]
        image_urls = image_urls or []
        if not image_urls:
            raise ServiceError("image_urls is required for Grok image edit")
        if len(image_urls) > 3:
            raise ServiceError("Grok image edit supports at most 3 source images")
        self.ensure_connected()
        body = {
            "model": model or self.model,
            "prompt": prompt,
            "n": max(1, min(10, int(n or 1))),
            "response_format": response_format if response_format in {"url", "b64_json"} else "url",
        }
        if len(image_urls) == 1:
            body["image"] = self._image_object(image_urls[0])
        else:
            body["images"] = [self._image_object(url) for url in image_urls]
            body["aspect_ratio"] = self._aspect_ratio(width, height, aspect_ratio)
        if resolution in ("1k", "2k"):
            body["resolution"] = resolution
        logger.info("[GROK-IMAGE] Editing %d image(s): prompt=%s..., model=%s",
                    len(image_urls), prompt[:80], body["model"])
        return self._first_image(self._post_json("/images/edits", body), output_format)


ServiceFactory.register(GrokImageService)
