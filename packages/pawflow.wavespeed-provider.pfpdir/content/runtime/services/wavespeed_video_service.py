"""WaveSpeedAI video generation service — catalog-driven dispatcher."""

import logging
from typing import Any, Dict

from core import ServiceFactory, ServiceError
from services._wavespeed_base import _WaveSpeedBaseService
from services.base_video_generation import BaseVideoGenerationService


logger = logging.getLogger(__name__)


class WaveSpeedVideoService(_WaveSpeedBaseService, BaseVideoGenerationService):
    TYPE = "wavespeedVideoGeneration"
    VERSION = "1.0.0"
    NAME = "WaveSpeedAI Video Generation"
    DESCRIPTION = (
        "Generate videos via WaveSpeedAI. Supports text-to-video, "
        "image-to-video, frame-to-video, video edit, and related "
        "operations declared in wavespeed_catalog.json."
    )
    CATEGORY = "video"

    def _video_result(self, op_name: str, body: Dict[str, Any],
                      model: str) -> dict:
        r = self._invoke(op_name, body, model_id=model)
        return {"video_bytes": r["bytes"],
                "content_type": r["content_type"],
                "source_url": r["source_url"]}

    def _resolution(self, width: int, height: int) -> str:
        return "720p" if int(width or 0) >= 1280 or int(height or 0) >= 720 else "480p"

    def generate(self, prompt: str = "", negative_prompt: str = "",
                 duration: int = 5, width: int = 1280, height: int = 720,
                 model: str = "", **kwargs) -> dict:
        if not prompt:
            raise ServiceError("No prompt provided")
        op = self._op("text_to_video", model_id=model)
        body: Dict[str, Any] = {"prompt": prompt}
        self._add_supported(body, op, "negative_prompt", negative_prompt)
        self._add_supported(body, op, "duration", int(duration) if duration else None)
        if width and height:
            self._add_supported(body, op, "width", int(width))
            self._add_supported(body, op, "height", int(height))
            self._add_supported(body, op, "size", f"{int(width)}*{int(height)}")
            self._add_supported(body, op, "resolution",
                                kwargs.get("resolution", self._resolution(width, height)))
        self._add_supported(body, op, "seed", kwargs.get("seed", -1))
        self._add_supported(body, op, "enable_sync_mode", False)
        self._add_kwargs(body, kwargs)
        return self._video_result("text_to_video", body, model)

    def image_to_video(self, prompt: str = "", image_url: str = "",
                        duration: int = 5, model: str = "", **kwargs) -> dict:
        if not image_url:
            raise ServiceError("image_to_video requires `image_url`.")
        op = self._op("image_to_video", model_id=model)
        input_field = op.get("input_field", "image")
        body: Dict[str, Any] = {input_field: image_url}
        self._add_supported(body, op, "prompt", prompt)
        self._add_supported(body, op, "duration", int(duration) if duration else None)
        self._add_supported(body, op, "resolution", kwargs.get("resolution"))
        self._add_supported(body, op, "negative_prompt", kwargs.get("negative_prompt"))
        self._add_supported(body, op, "seed", kwargs.get("seed", -1))
        self._add_supported(body, op, "enable_sync_mode", False)
        self._add_kwargs(body, kwargs)
        return self._video_result("image_to_video", body, model)

    def frame_to_video(self, prompt: str = "", image_url: str = "",
                        end_image_url: str = "", duration: int = 5,
                        model: str = "", **kwargs) -> dict:
        if not image_url:
            raise ServiceError("frame_to_video requires `image_url`.")
        if not end_image_url:
            raise ServiceError("frame_to_video requires `end_image_url`.")
        op = self._op("frame_to_video", model_id=model)
        body: Dict[str, Any] = {"prompt": prompt or ""}
        body[op.get("input_field", "image")] = image_url
        last_field = "last_image" if self._accepts(op, "last_image") else "end_image"
        body[last_field] = end_image_url
        self._add_supported(body, op, "duration", int(duration) if duration else None)
        self._add_supported(body, op, "enable_sync_mode", False)
        self._add_kwargs(body, kwargs)
        return self._video_result("frame_to_video", body, model)

    def speech_to_video(self, prompt: str = "", image_url: str = "",
                         audio_url: str = "", model: str = "",
                         **kwargs) -> dict:
        if not image_url:
            raise ServiceError("speech_to_video requires `image_url`.")
        if not audio_url:
            raise ServiceError("speech_to_video requires `audio_url`.")
        op = self._op("speech_to_video", model_id=model)
        body: Dict[str, Any] = {"prompt": prompt or ""}
        body[op.get("input_field", "image")] = image_url
        body[op.get("audio_field", "audio")] = audio_url
        self._add_supported(body, op, "enable_sync_mode", False)
        self._add_kwargs(body, kwargs)
        return self._video_result("speech_to_video", body, model)

    def reference_to_video(self, prompt: str = "", image_url: str = "",
                            duration: int = 5, model: str = "",
                            **kwargs) -> dict:
        if not image_url:
            raise ServiceError("reference_to_video requires `image_url`.")
        op = self._op("reference_to_video", model_id=model)
        body: Dict[str, Any] = {"prompt": prompt or ""}
        body[op.get("input_field", "image")] = image_url
        self._add_supported(body, op, "duration", int(duration) if duration else None)
        self._add_supported(body, op, "enable_sync_mode", False)
        self._add_kwargs(body, kwargs)
        return self._video_result("reference_to_video", body, model)

    def video_edit(self, prompt: str = "", video_url: str = "",
                   duration: int = 0, model: str = "", **kwargs) -> dict:
        if not video_url:
            raise ServiceError("video_edit requires `video_url`.")
        op = self._op("video_edit", model_id=model)
        input_field = op.get("input_field", "video")
        body: Dict[str, Any] = {input_field: video_url}
        self._add_supported(body, op, "prompt", prompt)
        self._add_supported(body, op, "duration", int(duration) if duration else None)
        self._add_supported(body, op, "enable_sync_mode", False)
        self._add_kwargs(body, kwargs)
        return self._video_result("video_edit", body, model)


ServiceFactory.register(WaveSpeedVideoService)
