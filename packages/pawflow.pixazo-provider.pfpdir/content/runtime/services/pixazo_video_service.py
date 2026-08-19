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
        "Generate videos via the Pixazo gateway (Runway, Kling, Veo, Pika, "
        "Luma, Seedance, ...). Any model declared in pixazo_catalog.json "
        "under category=video is supported."
    )
    CATEGORY = "video"

    def generate(self, prompt: str = "", negative_prompt: str = "",
                 duration: int = 8, width: int = 1280, height: int = 720,
                 model: str = "", **kwargs) -> dict:
        """Text-to-video. `model` overrides the service default for this call."""
        if not prompt:
            raise ServiceError("No prompt provided")
        body: Dict[str, Any] = {"prompt": prompt}
        if negative_prompt:
            body["negative_prompt"] = negative_prompt
        if duration:
            body["duration"] = int(duration)
            body["seconds"] = str(int(duration))
        if width and height:
            body["width"] = int(width)
            body["height"] = int(height)
            body["size"] = f"{int(width)}x{int(height)}"
        body["seed"] = kwargs.get("seed", int(time.time()) % 1_000_000)
        for k, v in kwargs.items():
            if k not in body and k not in ("destination", "path", "service", "model"):
                body[k] = v
        r = self._invoke("text_to_video", body, model_id=model)
        return {"video_bytes": r["bytes"],
                "content_type": r["content_type"],
                "source_url": r["source_url"]}

    def image_to_video(self, prompt: str = "", image_url: str = "",
                        duration: int = 8, model: str = "", **kwargs) -> dict:
        if not image_url:
            raise ServiceError("image_to_video requires `image_url`.")
        op = self._op("image_to_video", model_id=model)
        input_field = op.get("input_field", "image_url")
        body: Dict[str, Any] = {"prompt": prompt, input_field: image_url}
        if duration:
            body["duration"] = int(duration)
            body["seconds"] = str(int(duration))
        for k, v in kwargs.items():
            if k not in body and k not in ("destination", "path", "service", "model"):
                body[k] = v
        r = self._invoke("image_to_video", body, model_id=model)
        return {"video_bytes": r["bytes"],
                "content_type": r["content_type"],
                "source_url": r["source_url"]}

    def frame_to_video(self, prompt: str = "", image_url: str = "",
                        end_image_url: str = "", duration: int = 5,
                        model: str = "", **kwargs) -> dict:
        """First-frame + last-frame to video (Kling O1)."""
        if not image_url:
            raise ServiceError("frame_to_video requires `image_url` (start frame).")
        if not end_image_url:
            raise ServiceError("frame_to_video requires `end_image_url` (end frame).")
        body: Dict[str, Any] = {
            "prompt": prompt or "",
            "start_image_url": image_url,
            "end_image_url": end_image_url,
        }
        if duration:
            body["duration"] = str(int(duration))
        for k, v in kwargs.items():
            if k not in body and k not in ("destination", "path", "service",
                                            "model", "image_url", "end_image_url"):
                body[k] = v
        r = self._invoke("frame_to_video", body,
                         model_id=model or "kling-o1-first-frame-last-frame-to-video-857")
        return {"video_bytes": r["bytes"],
                "content_type": r["content_type"],
                "source_url": r["source_url"]}

    def speech_to_video(self, prompt: str = "", image_url: str = "",
                         audio_url: str = "", model: str = "",
                         **kwargs) -> dict:
        """Speech-to-video (Wan 2.2). Lip-sync video from image + audio."""
        if not image_url:
            raise ServiceError("speech_to_video requires `image_url`.")
        if not audio_url:
            raise ServiceError("speech_to_video requires `audio_url`.")
        body: Dict[str, Any] = {
            "prompt": prompt or "",
            "image_url": image_url,
            "audio_url": audio_url,
        }
        for k, v in kwargs.items():
            if k not in body and k not in ("destination", "path", "service",
                                            "model", "image_url", "audio_url"):
                body[k] = v
        r = self._invoke("speech_to_video", body, model_id=model or "wan2.2-s2v")
        return {"video_bytes": r["bytes"],
                "content_type": r["content_type"],
                "source_url": r["source_url"]}

    def reference_to_video(self, prompt: str = "", image_url: str = "",
                            duration: int = 5, model: str = "",
                            **kwargs) -> dict:
        """Reference-to-video (Seedance). Uses a content array with structured items."""
        if not image_url:
            raise ServiceError("reference_to_video requires `image_url`.")
        content = [{"type": "image_url", "image_url": {"url": image_url}}]
        if prompt:
            content.insert(0, {"type": "text", "text": prompt})
        body: Dict[str, Any] = {"content": content}
        if duration:
            body["duration"] = int(duration)
        for k, v in kwargs.items():
            if k not in body and k not in ("destination", "path", "service",
                                            "model", "image_url"):
                body[k] = v
        r = self._invoke("reference_to_video", body, model_id=model)
        return {"video_bytes": r["bytes"],
                "content_type": r["content_type"],
                "source_url": r["source_url"]}

    def video_edit(self, prompt: str = "", video_url: str = "",
                   duration: int = 0, model: str = "", **kwargs) -> dict:
        """Video-to-video editing (style transfer, re-editing)."""
        if not video_url:
            raise ServiceError("video_edit requires `video_url`.")
        op = self._op("video_edit", model_id=model)
        input_field = op.get("input_field", "video_url")
        body: Dict[str, Any] = {input_field: video_url}
        if prompt:
            body["prompt"] = prompt
        if duration:
            body["duration"] = int(duration)
            body["seconds"] = str(int(duration))
        for k, v in kwargs.items():
            if k not in body and k not in ("destination", "path", "service", "model"):
                body[k] = v
        r = self._invoke("video_edit", body, model_id=model)
        return {"video_bytes": r["bytes"],
                "content_type": r["content_type"],
                "source_url": r["source_url"]}


ServiceFactory.register(PixazoVideoService)
