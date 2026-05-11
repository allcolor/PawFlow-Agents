"""WaveSpeedAI services for additional media capabilities."""

import logging
from typing import Any, Dict

from core import ServiceFactory, ServiceError
from services._wavespeed_base import _WaveSpeedBaseService
from services.base_capabilities import (
    BaseImage3DService, BaseImageUpscaleService, BaseTryOnService,
    BaseLipsyncService, BaseImageTrainerService,
)


logger = logging.getLogger(__name__)


class WaveSpeed3DService(_WaveSpeedBaseService, BaseImage3DService):
    TYPE = "wavespeed3DGeneration"
    VERSION = "1.0.0"
    NAME = "WaveSpeedAI 3D Generation"
    DESCRIPTION = "Generate 3D models via WaveSpeedAI catalog models."
    CATEGORY = "3d"

    def generate_3d(self, prompt: str = "", image_url: str = "",
                    model: str = "", **kwargs) -> dict:
        if not prompt and not image_url:
            raise ServiceError("generate_3d requires `prompt` or `image_url`.")
        if image_url and not prompt and not model:
            model = "wavespeed-ai/hunyuan3d-v3/image-to-3d"
        op_name = self._pick_op(["image_to_3d", "text_to_3d"], model_id=model)
        op = self._op(op_name, model_id=model)
        body: Dict[str, Any] = {}
        if image_url:
            body[op.get("input_field", "image")] = image_url
        self._add_supported(body, op, "prompt", prompt)
        self._add_supported(body, op, "enable_sync_mode", False)
        self._add_kwargs(body, kwargs)
        r = self._invoke(op_name, body, model_id=model)
        return {"bytes": r["bytes"], "content_type": r["content_type"],
                "source_url": r["source_url"]}


class WaveSpeedUpscaleService(_WaveSpeedBaseService, BaseImageUpscaleService):
    TYPE = "wavespeedUpscale"
    VERSION = "1.0.0"
    NAME = "WaveSpeedAI Upscale"
    DESCRIPTION = "Upscale images/videos or remove backgrounds via WaveSpeedAI."
    CATEGORY = "upscale"

    def upscale(self, image_url: str = "", scale: int = 2,
                model: str = "", **kwargs) -> dict:
        if not image_url:
            raise ServiceError("upscale requires `image_url`.")
        op = self._op("upscale", model_id=model)
        body: Dict[str, Any] = {op.get("input_field", "image"): image_url}
        self._add_supported(body, op, "scale", int(scale))
        self._add_supported(body, op, "enable_sync_mode", False)
        self._add_kwargs(body, kwargs)
        r = self._invoke("upscale", body, model_id=model)
        return {"bytes": r["bytes"], "content_type": r["content_type"],
                "source_url": r["source_url"]}

    def upscale_video(self, video_url: str = "", scale: int = 2,
                      model: str = "", **kwargs) -> dict:
        if not video_url:
            raise ServiceError("upscale_video requires `video_url`.")
        op = self._op("upscale", model_id=model)
        body: Dict[str, Any] = {op.get("input_field", "video"): video_url}
        self._add_supported(body, op, "scale", int(scale))
        self._add_supported(body, op, "enable_sync_mode", False)
        self._add_kwargs(body, kwargs)
        r = self._invoke("upscale", body, model_id=model)
        return {"bytes": r["bytes"], "content_type": r["content_type"],
                "source_url": r["source_url"]}

    def remove_background(self, image_url: str = "",
                           model: str = "", **kwargs) -> dict:
        if not image_url:
            raise ServiceError("remove_background requires `image_url`.")
        op = self._op("remove_background", model_id=model)
        body: Dict[str, Any] = {op.get("input_field", "image"): image_url}
        self._add_supported(body, op, "enable_sync_mode", False)
        self._add_kwargs(body, kwargs)
        r = self._invoke("remove_background", body, model_id=model)
        return {"bytes": r["bytes"], "content_type": r["content_type"],
                "source_url": r["source_url"]}


class WaveSpeedTryOnService(_WaveSpeedBaseService, BaseTryOnService):
    TYPE = "wavespeedTryOn"
    VERSION = "1.0.0"
    NAME = "WaveSpeedAI Try-On"
    DESCRIPTION = "Virtual try-on via WaveSpeedAI catalog models."
    CATEGORY = "try_on"

    def try_on(self, person_image: str = "", garment_image: str = "",
               model: str = "", **kwargs) -> dict:
        if not person_image or not garment_image:
            raise ServiceError("try_on requires person and garment images.")
        op = self._op("try_on", model_id=model)
        body: Dict[str, Any] = {
            op.get("person_field", "person_image"): person_image,
            op.get("garment_field", "garment_image"): garment_image,
        }
        self._add_supported(body, op, "enable_sync_mode", False)
        self._add_kwargs(body, kwargs)
        r = self._invoke("try_on", body, model_id=model)
        return {"bytes": r["bytes"], "content_type": r["content_type"],
                "source_url": r["source_url"]}


class WaveSpeedLipsyncService(_WaveSpeedBaseService, BaseLipsyncService):
    TYPE = "wavespeedLipsync"
    VERSION = "1.0.0"
    NAME = "WaveSpeedAI Lipsync"
    DESCRIPTION = "Lipsync and talking-head generation via WaveSpeedAI."
    CATEGORY = "lipsync"

    def lipsync(self, video_url: str = "", image_url: str = "",
                audio_url: str = "", model: str = "", **kwargs) -> dict:
        if not audio_url or not (video_url or image_url):
            raise ServiceError(
                "lipsync requires `audio_url` and either `video_url` or `image_url`.")
        op = self._op("lipsync", model_id=model)
        body: Dict[str, Any] = {op.get("audio_field", "audio"): audio_url}
        if video_url:
            body[op.get("video_field", "video")] = video_url
        if image_url:
            body[op.get("image_field", "image")] = image_url
        self._add_supported(body, op, "enable_sync_mode", False)
        self._add_kwargs(body, kwargs)
        r = self._invoke("lipsync", body, model_id=model)
        return {"bytes": r["bytes"], "content_type": r["content_type"],
                "source_url": r["source_url"]}


class WaveSpeedTrainerService(_WaveSpeedBaseService, BaseImageTrainerService):
    TYPE = "wavespeedTrainer"
    VERSION = "1.0.0"
    NAME = "WaveSpeedAI Trainer"
    DESCRIPTION = "Train/fine-tune image models via WaveSpeedAI."
    CATEGORY = "trainer"

    def train(self, dataset_url: str = "", base_model: str = "",
              model: str = "", **kwargs) -> dict:
        if not dataset_url:
            raise ServiceError("train requires `dataset_url`.")
        op = self._op("train", model_id=model)
        body: Dict[str, Any] = {op.get("input_field", "dataset_url"): dataset_url}
        self._add_supported(body, op, "base_model", base_model)
        self._add_supported(body, op, "enable_sync_mode", False)
        self._add_kwargs(body, kwargs)
        r = self._invoke("train", body, model_id=model)
        return {"lora_url": r["source_url"],
                "content_type": r["content_type"],
                "status": "done"}


for _cls in (WaveSpeed3DService, WaveSpeedUpscaleService,
             WaveSpeedTryOnService, WaveSpeedLipsyncService,
             WaveSpeedTrainerService):
    ServiceFactory.register(_cls)
