"""WaveSpeedAI image generation service — catalog-driven dispatcher."""

import logging
from typing import Any, Dict

from core import ServiceFactory, ServiceError
from services._wavespeed_base import _WaveSpeedBaseService
from services.base_image_generation import BaseImageGenerationService


logger = logging.getLogger(__name__)


class WaveSpeedImageService(_WaveSpeedBaseService, BaseImageGenerationService):
    TYPE = "wavespeedImageGeneration"
    VERSION = "1.0.0"
    NAME = "WaveSpeedAI Image Generation"
    DESCRIPTION = (
        "Generate or edit images via WaveSpeedAI. Supports any image "
        "model declared in wavespeed_catalog.json."
    )
    CATEGORY = "image"

    def generate(self, prompt: str = "", negative_prompt: str = "",
                 width: int = 1024, height: int = 1024, steps: int = 28,
                 model: str = "", **kwargs) -> dict:
        if not prompt:
            raise ServiceError("No prompt provided")
        op = self._op("text_to_image", model_id=model)
        body: Dict[str, Any] = {"prompt": prompt}
        self._add_supported(body, op, "negative_prompt", negative_prompt)
        if width and height:
            w, h = max(256, int(width)), max(256, int(height))
            if self._accepts(op, "size"):
                body["size"] = f"{w}*{h}"
            self._add_supported(body, op, "width", w)
            self._add_supported(body, op, "height", h)
        if steps:
            self._add_supported(body, op, "num_inference_steps", int(steps))
            self._add_supported(body, op, "steps", int(steps))
        self._add_supported(body, op, "guidance_scale",
                            kwargs.get("guidance_scale"))
        self._add_supported(body, op, "seed", kwargs.get("seed", -1))
        self._add_supported(body, op, "num_images", kwargs.get("num_images", 1))
        self._add_supported(body, op, "output_format",
                            kwargs.get("output_format", "png"))
        self._add_supported(body, op, "enable_sync_mode", False)
        self._add_kwargs(body, kwargs)
        r = self._invoke("text_to_image", body, model_id=model)
        return {"image_bytes": r["bytes"],
                "content_type": r["content_type"],
                "source_url": r["source_url"]}

    def edit_image(self, prompt: str = "", image_urls=None,
                   model: str = "", **kwargs) -> dict:
        if not prompt:
            raise ServiceError("No prompt provided")
        if not image_urls:
            raise ServiceError("edit_image requires `image_urls`.")
        if isinstance(image_urls, str):
            image_urls = [image_urls]
        op = self._op("edit_image", model_id=model)
        input_field = op.get("input_field") or (
            "images" if self._accepts(op, "images") else "image")
        body: Dict[str, Any] = {"prompt": prompt}
        body[input_field] = list(image_urls) if input_field.endswith("s") else image_urls[0]
        self._add_supported(body, op, "num_images", kwargs.get("num_images", 1))
        self._add_supported(body, op, "output_format",
                            kwargs.get("output_format", "png"))
        self._add_supported(body, op, "enable_sync_mode", False)
        self._add_kwargs(body, kwargs)
        r = self._invoke("edit_image", body, model_id=model)
        return {"image_bytes": r["bytes"],
                "content_type": r["content_type"],
                "source_url": r["source_url"]}


ServiceFactory.register(WaveSpeedImageService)
