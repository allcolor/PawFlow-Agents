"""Pixazo services for non-image/video/audio capabilities.

All thin subclasses of `_PixazoBaseService` — the catalog drives the
actual API calls. Each service just declares CATEGORY and a public
method whose name matches the capability handler's contract.

Adding a new Pixazo model for any of these capabilities is a pure
JSON change in `pixazo_catalog.json`.
"""

import logging
from typing import Any, Dict

from core import ServiceFactory, ServiceError
from services._pixazo_base import _PixazoBaseService
from services.base_capabilities import (
    BaseImage3DService, BaseImageUpscaleService,
    BaseTryOnService, BaseLipsyncService, BaseImageTrainerService,
)

logger = logging.getLogger(__name__)


class Pixazo3DService(_PixazoBaseService, BaseImage3DService):
    """Generate a 3D model from an image or prompt (Hunyuan3D, Rodin, …)."""
    TYPE = "pixazo3DGeneration"
    VERSION = "1.0.0"
    NAME = "Pixazo 3D Generation"
    DESCRIPTION = ("Generate a 3D model via the Pixazo gateway. Any "
                   "catalog model under category=3d is supported "
                   "(Hunyuan3D, Hyper3D Rodin, Trellis, Tripo3D).")
    CATEGORY = "3d"

    def generate_3d(self, prompt: str = "", image_url: str = "",
                    **kwargs) -> dict:
        if not prompt and not image_url:
            raise ServiceError(
                "generate_3d requires `image_url` or `prompt`.")
        op_name = self._pick_op("image_to_3d", "text_to_3d")
        op = self._op(op_name)
        input_field = op.get("input_field", "image_url")
        body: Dict[str, Any] = {}
        if image_url:
            body[input_field] = image_url
        if prompt:
            body["prompt"] = prompt
        for k, v in kwargs.items():
            if k not in body and k not in ("destination", "path", "service"):
                body[k] = v
        r = self._invoke(op_name, body)
        return {"bytes": r["bytes"],
                "content_type": r["content_type"],
                "source_url": r["source_url"]}

    def _pick_op(self, *names: str) -> str:
        ops = self._model().get("operations") or {}
        for n in names:
            if n in ops:
                return n
        raise ServiceError(
            f"Model '{self._model_id}' has no op among {names}. "
            f"Supported: {sorted(ops.keys())}.")


class PixazoUpscaleService(_PixazoBaseService, BaseImageUpscaleService):
    """Upscale images via Pixazo (SeedVR, Crystal, Topaz, …)."""
    TYPE = "pixazoUpscale"
    VERSION = "1.0.0"
    NAME = "Pixazo Upscale"
    DESCRIPTION = ("Upscale images via the Pixazo gateway. Catalog "
                   "models under category=upscale (SeedVR, Crystal "
                   "Upscaler, Topaz, Bria RMBG, …).")
    CATEGORY = "upscale"

    def upscale(self, image_url: str = "", scale: int = 2,
                **kwargs) -> dict:
        if not image_url:
            raise ServiceError("upscale requires `image_url`.")
        op = self._op("upscale")
        input_field = op.get("input_field", "image_url")
        body: Dict[str, Any] = {
            input_field: image_url,
            "scale": int(scale),
        }
        for k, v in kwargs.items():
            if k not in body and k not in ("destination", "path", "service"):
                body[k] = v
        r = self._invoke("upscale", body)
        return {"bytes": r["bytes"],
                "content_type": r["content_type"],
                "source_url": r["source_url"]}


class PixazoTryOnService(_PixazoBaseService, BaseTryOnService):
    """Virtual try-on (Kling VTON, Fashn, IDM-VTON, Glass)."""
    TYPE = "pixazoTryOn"
    VERSION = "1.0.0"
    NAME = "Pixazo Try-On"
    DESCRIPTION = ("Virtual try-on via the Pixazo gateway. Catalog "
                   "models under category=try_on.")
    CATEGORY = "try_on"

    def try_on(self, person_image: str = "", garment_image: str = "",
               **kwargs) -> dict:
        if not person_image or not garment_image:
            raise ServiceError(
                "try_on requires both `person_image` and `garment_image`.")
        op = self._op("try_on")
        person_field = op.get("person_field", "person_image")
        garment_field = op.get("garment_field", "garment_image")
        body: Dict[str, Any] = {
            person_field: person_image,
            garment_field: garment_image,
        }
        for k, v in kwargs.items():
            if k not in body and k not in ("destination", "path", "service"):
                body[k] = v
        r = self._invoke("try_on", body)
        return {"bytes": r["bytes"],
                "content_type": r["content_type"],
                "source_url": r["source_url"]}


class PixazoLipsyncService(_PixazoBaseService, BaseLipsyncService):
    """Lipsync / talking-head (OmniHuman, Kling Avatar, Sync Lipsync)."""
    TYPE = "pixazoLipsync"
    VERSION = "1.0.0"
    NAME = "Pixazo Lipsync"
    DESCRIPTION = ("Lipsync / talking-head generation via the Pixazo "
                   "gateway. Catalog models under category=lipsync.")
    CATEGORY = "lipsync"

    def lipsync(self, video_url: str = "", image_url: str = "",
                audio_url: str = "", **kwargs) -> dict:
        if not audio_url or not (video_url or image_url):
            raise ServiceError(
                "lipsync requires `audio_url` and either `video_url` or "
                "`image_url`.")
        op = self._op("lipsync")
        video_field = op.get("video_field", "video_url")
        image_field = op.get("image_field", "image_url")
        audio_field = op.get("audio_field", "audio_url")
        body: Dict[str, Any] = {audio_field: audio_url}
        if video_url:
            body[video_field] = video_url
        if image_url:
            body[image_field] = image_url
        for k, v in kwargs.items():
            if k not in body and k not in ("destination", "path", "service"):
                body[k] = v
        r = self._invoke("lipsync", body)
        return {"bytes": r["bytes"],
                "content_type": r["content_type"],
                "source_url": r["source_url"]}


class PixazoTrainerService(_PixazoBaseService, BaseImageTrainerService):
    """Image-model trainer (Flux LoRA Fast, Flux 2 Trainer, …)."""
    TYPE = "pixazoTrainer"
    VERSION = "1.0.0"
    NAME = "Pixazo Trainer"
    DESCRIPTION = ("Fine-tune image models via the Pixazo gateway. "
                   "Catalog models under category=trainer.")
    CATEGORY = "trainer"

    def train(self, dataset_url: str = "", base_model: str = "",
              **kwargs) -> dict:
        if not dataset_url:
            raise ServiceError("train requires `dataset_url`.")
        op = self._op("train")
        input_field = op.get("input_field", "image_data_url")
        body: Dict[str, Any] = {input_field: dataset_url}
        if base_model:
            body["base_model"] = base_model
        for k, v in kwargs.items():
            if k not in body and k not in ("destination", "path", "service"):
                body[k] = v
        # Trainers return a LoRA / checkpoint URL — alias into our std shape.
        r = self._invoke("train", body)
        return {"lora_url": r["source_url"],
                "content_type": r["content_type"],
                "status": "done"}


for _cls in (Pixazo3DService, PixazoUpscaleService, PixazoTryOnService,
              PixazoLipsyncService, PixazoTrainerService):
    ServiceFactory.register(_cls)
