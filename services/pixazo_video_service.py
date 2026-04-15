"""Pixazo video generation service — catalog-driven dispatcher.

Thin subclass of `_PixazoBaseService` with CATEGORY="video". All
transport and polling logic lives in the base. Models are declared in
`pixazo_catalog.json` with `category: "video"` and one or more of
`text_to_video`, `image_to_video`, `video_edit`, etc. Adding a new
video provider is a pure-JSON change when the API fits one of the
three supported conventions.
"""

import logging
import time
from typing import Any, Dict

from core import ServiceFactory, ServiceError
from services._pixazo_base import _PixazoBaseService
from services.base_video_generation import BaseVideoGenerationService

logger = logging.getLogger(__name__)


class PixazoVideoService(_PixazoBaseService, BaseVideoGenerationService):
    TYPE = "pixazoVideoGeneration"
    VERSION = "3.0.0"
    NAME = "Pixazo Video Generation"
    DESCRIPTION = (
        "Generate videos via the Pixazo gateway (Sora, Runway, Kling, "
        "Veo, Pika, Luma, Seedance, …). Any model declared in "
        "pixazo_catalog.json under category=video is supported."
    )
    CATEGORY = "video"

    def generate(self, prompt: str = "", negative_prompt: str = "",
                 duration: int = 8, width: int = 1280, height: int = 720,
                 **kwargs) -> dict:
        """Text-to-video. Calls operation 'text_to_video' on the active model."""
        if not prompt:
            raise ServiceError("No prompt provided")
        body: Dict[str, Any] = {"prompt": prompt}
        if negative_prompt:
            body["negative_prompt"] = negative_prompt
        if duration:
            body["duration"] = int(duration)
            body["seconds"] = int(duration)
        if width and height:
            body["width"] = int(width)
            body["height"] = int(height)
            body["size"] = f"{int(width)}x{int(height)}"
        body["seed"] = kwargs.get("seed", int(time.time()) % 1_000_000)
        for k, v in kwargs.items():
            if k not in body and k not in ("destination", "path", "service"):
                body[k] = v
        r = self._invoke("text_to_video", body)
        return {"video_bytes": r["bytes"],
                "content_type": r["content_type"],
                "source_url": r["source_url"]}

    def image_to_video(self, prompt: str = "", image_url: str = "",
                        duration: int = 8, **kwargs) -> dict:
        """Image-to-video. Calls operation 'image_to_video'."""
        if not image_url:
            raise ServiceError(
                "image_to_video requires `image_url`.")
        op = self._op("image_to_video")
        input_field = op.get("input_field", "image_url")
        body: Dict[str, Any] = {
            "prompt": prompt,
            input_field: image_url,
        }
        if duration:
            body["duration"] = int(duration)
            body["seconds"] = int(duration)
        for k, v in kwargs.items():
            if k not in body and k not in ("destination", "path", "service"):
                body[k] = v
        r = self._invoke("image_to_video", body)
        return {"video_bytes": r["bytes"],
                "content_type": r["content_type"],
                "source_url": r["source_url"]}


ServiceFactory.register(PixazoVideoService)
