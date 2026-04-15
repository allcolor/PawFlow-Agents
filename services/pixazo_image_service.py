"""Pixazo image generation service — catalog-driven dispatcher.

All transport / polling / download logic lives in
`services._pixazo_base._PixazoBaseService`. This module is a thin
image-specific wrapper that declares CATEGORY="image" and exposes the
public API (`generate`, `edit_image`) every image provider implements.

Models and their operations (text_to_image, edit_image, …) are
declared in `data/repository/configs/pixazo_catalog.json`. Adding a
new image provider with one of the three supported conventions
(sync / legacy_poll / polling_url) never requires Python changes.
"""

import logging
import time
from typing import Any, Dict

from core import ServiceFactory, ServiceError
from services._pixazo_base import (  # noqa: F401
    _PixazoBaseService, _load_catalog, _CATALOG_CACHE,
)
from services.base_image_generation import BaseImageGenerationService

logger = logging.getLogger(__name__)


class PixazoImageService(_PixazoBaseService, BaseImageGenerationService):
    TYPE = "pixazoImageGeneration"
    VERSION = "4.0.0"
    NAME = "Pixazo Image Generation"
    DESCRIPTION = (
        "Generate or edit images via Pixazo API. Supports any model "
        "declared in pixazo_catalog.json under category=image."
    )
    CATEGORY = "image"

    def _download_image(self, url: str):
        """Fetch an image from a Pixazo CDN URL. (bytes, content_type)."""
        return self._download_media(url, default_mime="image/png")

    # ── Public ops ─────────────────────────────────────────────────────

    def generate(self, prompt: str = "", negative_prompt: str = "",
                 width: int = 1024, height: int = 1024, steps: int = 20,
                 **kwargs) -> dict:
        """Text-to-image — calls operation 'text_to_image' on the active model."""
        if not prompt:
            raise ServiceError("No prompt provided")
        body: Dict[str, Any] = {"prompt": prompt}
        if negative_prompt:
            body["negative_prompt"] = negative_prompt
        if width and height:
            body["width"] = max(256, int(width))
            body["height"] = max(256, int(height))
            body["size"] = f"{body['width']}x{body['height']}"
        if steps:
            body["num_steps"] = max(1, min(50, int(steps)))
            body["num_inference_steps"] = body["num_steps"]
        body["guidance_scale"] = kwargs.get("guidance_scale", 5)
        body["seed"] = kwargs.get("seed", int(time.time()) % 1_000_000)
        body["num_images"] = 1
        body["output_format"] = kwargs.get("output_format", "png")
        for k, v in kwargs.items():
            if k not in body and k not in ("destination", "path", "service"):
                body[k] = v
        r = self._invoke("text_to_image", body)
        # Alias bytes → image_bytes for callers that already use the old key
        return {"image_bytes": r["bytes"],
                "content_type": r["content_type"],
                "source_url": r["source_url"]}

    def edit_image(self, prompt: str = "", image_urls=None, **kwargs) -> dict:
        """Edit one or more source images per the prompt."""
        if not prompt:
            raise ServiceError("No prompt provided")
        if not image_urls:
            raise ServiceError(
                "edit_image requires at least one source URL in `image_urls`.")
        if isinstance(image_urls, str):
            image_urls = [image_urls]
        op = self._op("edit_image")
        input_field = op.get("input_field", "image_urls")
        body: Dict[str, Any] = {
            "prompt": prompt,
            input_field: list(image_urls),
            "num_images": int(kwargs.get("num_images", 1)),
            "output_format": kwargs.get("output_format", "png"),
        }
        for k, v in kwargs.items():
            if k not in body and k not in ("destination", "path", "service"):
                body[k] = v
        r = self._invoke("edit_image", body)
        return {"image_bytes": r["bytes"],
                "content_type": r["content_type"],
                "source_url": r["source_url"]}


ServiceFactory.register(PixazoImageService)


# Expose the URL extractor as a static method so callers and tests can
# use `PixazoImageService._extract_image_url(data, url_field="foo")`
# directly without poking at the private module.
def _extract_image_url(data, url_field: str = ""):
    from services._pixazo_base import _extract_media_url as _em
    return _em(data, url_field=url_field)


PixazoImageService._extract_image_url = staticmethod(_extract_image_url)
