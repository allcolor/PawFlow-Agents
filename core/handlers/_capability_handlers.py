"""Image/3D capability tool handlers (first group).

Extracted from capabilities.py to keep files <=800 lines. All handlers extend
_CapabilityHandlerBase from _capability_base; re-exported from
core.handlers.capabilities.
"""

import logging
import time
from typing import Any, Dict

from core.handlers._capability_base import (
    _SERVICE_ARG_NAMES,
    _CapabilityHandlerBase,
)

logger = logging.getLogger(__name__)


class Generate3DHandler(_CapabilityHandlerBase):
    @property
    def name(self) -> str:
        return "generate_3d"

    @property
    def description(self) -> str:
        return (
            "Generate a 3D model (GLB / GLTF / OBJ / USDZ) from an "
            "input image or a text prompt. Uses the active 3D service "
            "(Hunyuan3D, Rodin, Trellis, Tripo3D, …). Provide `image_url` "
            "for image-to-3D (preferred, higher quality) or `prompt` for "
            "text-to-3D. Saves the result to FileStore (default) or a "
            "filesystem service when `destination` + `path` are given."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Optional text prompt for text-to-3D"},
                "image_url": {"type": "string", "description": "Source image URL for image-to-3D (HTTP or fs://filestore/<id>/<name>)"},
                "destination": {"type": "string", "description": "'filestore' (default) or relay service name"},
                "path": {"type": "string", "description": "Filename when saving to a filesystem service"},
                "model": {"type": "string", "description": "Override the active 3D model (e.g. 'hyper3d-rodin-259', 'tripo3d-v2-5-413', 'hunyuan3d-3-0-api-294')."},
            },
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        svc, err = self._get_service(arguments)
        if not svc:
            return f"Error: {err or 'no 3D generation service available'}"
        prompt = arguments.get("prompt", "") or ""
        image_url = self._rewrite(arguments.get("image_url", "") or "", service=svc)
        if not prompt and not image_url:
            return "Error: provide `prompt` or `image_url`"
        try:
            kwargs = {k: v for k, v in arguments.items()
                      if k not in ("destination", "path", "prompt", "image_url", *_SERVICE_ARG_NAMES)}
            r = svc.generate_3d(prompt=prompt, image_url=image_url, **kwargs)
            ext = {"model/gltf-binary": "glb", "model/gltf+json": "gltf",
                   "model/obj": "obj", "model/vnd.usdz+zip": "usdz"}.get(
                       r.get("content_type", "").split(";")[0].strip(), "glb")
            filename = arguments.get("path") or (
                f"model_{int(time.time())}.{ext}")
            destination = arguments.get("destination", "filestore")
            return self._persist(destination, filename, r, "3D model generated")
        except Exception as e:
            return f"Error generating 3D model: {e}"


# ── Upscale ────────────────────────────────────────────────────────────


class UpscaleImageHandler(_CapabilityHandlerBase):
    @property
    def name(self) -> str:
        return "upscale_image"

    @property
    def description(self) -> str:
        return (
            "Upscale an image (2x / 4x / 8x) using an AI upscaler "
            "(SeedVR, Crystal Upscaler, Topaz, …). Pass the source via "
            "`image_url` (HTTP or fs://filestore/<id>/<name>). Saves the "
            "result to FileStore (default) or a filesystem service."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "image_url": {"type": "string", "description": "Source image URL"},
                "scale": {"type": "integer", "description": "Upscale factor (2, 4 — default 2)"},
                "destination": {"type": "string"},
                "path": {"type": "string"},
                "model": {"type": "string", "description": "Override the upscale model (e.g. 'seedvr-upscale', 'crystal-upscaler', 'topaz-upscale-video-753', 'bria-rmbg-2-0-682')."},
            },
            "required": ["image_url"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        svc, err = self._get_service(arguments)
        if not svc:
            return f"Error: {err or 'no upscale service available'}"
        image_url = self._rewrite(arguments.get("image_url", "") or "", service=svc)
        if not image_url:
            return "Error: `image_url` is required"
        scale = int(arguments.get("scale", 2))
        try:
            kwargs = {k: v for k, v in arguments.items()
                      if k not in ("destination", "path", "image_url", "scale", *_SERVICE_ARG_NAMES)}
            r = svc.upscale(image_url=image_url, scale=scale, **kwargs)
            ct = r.get("content_type", "image/png").split(";")[0].strip()
            ext = {"image/png": "png", "image/jpeg": "jpg",
                   "image/webp": "webp"}.get(ct, "png")
            filename = arguments.get("path") or (
                f"upscaled_{int(time.time())}.{ext}")
            destination = arguments.get("destination", "filestore")
            return self._persist(destination, filename, r, "Upscaled image")
        except Exception as e:
            return f"Error upscaling image: {e}"


# ── Upscale Video ─────────────────────────────────────────────────────


class UpscaleVideoHandler(_CapabilityHandlerBase):
    @property
    def name(self) -> str:
        return "upscale_video"

    @property
    def description(self) -> str:
        return (
            "Upscale a video (2x / 4x) using an AI upscaler "
            "(SeedVR Video, Topaz, ...). Pass the source via "
            "`video_url` (HTTP or fs://filestore/<id>/<name>). Saves the "
            "result to FileStore (default) or a filesystem service."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "video_url": {"type": "string", "description": "Source video URL"},
                "scale": {"type": "integer", "description": "Upscale factor (2, 4 — default 2)"},
                "destination": {"type": "string"},
                "path": {"type": "string"},
                "model": {"type": "string", "description": "Override the upscale model (e.g. 'seedvr-upscale-video', 'topaz-upscale-video-753')."},
            },
            "required": ["video_url"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        svc, err = self._get_service(arguments)
        if not svc:
            return f"Error: {err or 'no upscale service available'}"
        video_url = self._rewrite(arguments.get("video_url", "") or "", service=svc)
        if not video_url:
            return "Error: `video_url` is required"
        if not hasattr(svc, 'upscale_video'):
            return "Error: the active upscale service does not support video upscaling"
        scale = int(arguments.get("scale", 2))
        try:
            kwargs = {k: v for k, v in arguments.items()
                      if k not in ("destination", "path", "video_url", "scale", *_SERVICE_ARG_NAMES)}
            r = svc.upscale_video(video_url=video_url, scale=scale, **kwargs)
            filename = arguments.get("path") or (
                f"upscaled_{int(time.time())}.mp4")
            destination = arguments.get("destination", "filestore")
            return self._persist(destination, filename, r, "Upscaled video")
        except Exception as e:
            return f"Error upscaling video: {e}"


# ── Describe Image ───────────────────────────────────────────────────


class DescribeImageHandler(_CapabilityHandlerBase):
    @property
    def name(self) -> str:
        return "describe_image"

    @property
    def description(self) -> str:
        return (
            "Describe the content of an image using an AI model "
            "(Ideogram v2 / Turbo). Returns a text description of the "
            "image content, style, and composition. Pass the source via "
            "`image_url` (HTTP or fs://filestore/<id>/<name>)."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "image_url": {"type": "string", "description": "Source image URL to describe"},
                "model": {"type": "string", "description": "Override the model (e.g. 'ideogram', 'ideogram-turbo')."},
            },
            "required": ["image_url"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        svc, err = self._get_service(arguments)
        if not svc:
            return f"Error: {err or 'no image service available'}"
        image_url = self._rewrite(arguments.get("image_url", "") or "", service=svc)
        if not image_url:
            return "Error: `image_url` is required"
        if not hasattr(svc, 'describe_image'):
            return "Error: the active image service does not support describe_image"
        try:
            kwargs = {k: v for k, v in arguments.items()
                      if k not in ("image_url", *_SERVICE_ARG_NAMES)}
            r = svc.describe_image(image_url=image_url, **kwargs)
            return f"Image description: {r.get('description', '(no description)')}"
        except Exception as e:
            return f"Error describing image: {e}"


# ── Remix Image ─────────────────────────────────────────────────────


class RemixImageHandler(_CapabilityHandlerBase):
    @property
    def name(self) -> str:
        return "remix_image"

    @property
    def description(self) -> str:
        return (
            "Remix an image: generate a new image inspired by a source image "
            "and a text prompt. The output blends the style/content of the "
            "source with the prompt direction. Uses Ideogram v2 / Turbo remix. "
            "Pass the source via `image_url` (HTTP or fs://filestore/<id>/<name>)."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Text prompt describing the desired remix"},
                "image_url": {"type": "string", "description": "Source image URL to remix"},
                "destination": {"type": "string"},
                "path": {"type": "string"},
                "model": {"type": "string", "description": "Override the model (e.g. 'ideogram', 'ideogram-turbo')."},
            },
            "required": ["prompt", "image_url"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        svc, err = self._get_service(arguments)
        if not svc:
            return f"Error: {err or 'no image service available'}"
        image_url = self._rewrite(arguments.get("image_url", "") or "", service=svc)
        prompt = arguments.get("prompt", "")
        if not image_url or not prompt:
            return "Error: `prompt` and `image_url` are required"
        if not hasattr(svc, 'remix_image'):
            return "Error: the active image service does not support remix_image"
        try:
            kwargs = {k: v for k, v in arguments.items()
                      if k not in ("destination", "path", "image_url", "prompt", *_SERVICE_ARG_NAMES)}
            r = svc.remix_image(prompt=prompt, image_url=image_url, **kwargs)
            ct = r.get("content_type", "image/png").split(";")[0].strip()
            ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(ct, "png")
            filename = arguments.get("path") or f"remix_{int(time.time())}.{ext}"
            destination = arguments.get("destination", "filestore")
            return self._persist(destination, filename, r, "Remixed image")
        except Exception as e:
            return f"Error remixing image: {e}"


# ── Remove Background ─────────────────────────────────────────────────


class RemoveBackgroundHandler(_CapabilityHandlerBase):
    @property
    def name(self) -> str:
        return "remove_background"

    @property
    def description(self) -> str:
        return (
            "Remove the background from an image using an AI model "
            "(Bria RMBG 2.0, VEED, ...). Pass the source via "
            "`image_url` (HTTP or fs://filestore/<id>/<name>). Returns "
            "a PNG with transparent background."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "image_url": {"type": "string", "description": "Source image URL"},
                "destination": {"type": "string"},
                "path": {"type": "string"},
                "model": {"type": "string", "description": "Override the model (e.g. 'bria-rmbg-2-0-682')."},
            },
            "required": ["image_url"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        svc, err = self._get_service(arguments)
        if not svc:
            return f"Error: {err or 'no upscale/background service available'}"
        image_url = self._rewrite(arguments.get("image_url", "") or "", service=svc)
        if not image_url:
            return "Error: `image_url` is required"
        if not hasattr(svc, 'remove_background'):
            return "Error: the active service does not support remove_background"
        try:
            kwargs = {k: v for k, v in arguments.items()
                      if k not in ("destination", "path", "image_url", *_SERVICE_ARG_NAMES)}
            r = svc.remove_background(image_url=image_url, **kwargs)
            filename = arguments.get("path") or (
                f"nobg_{int(time.time())}.png")
            destination = arguments.get("destination", "filestore")
            return self._persist(destination, filename, r, "Background removed")
        except Exception as e:
            return f"Error removing background: {e}"


# ── Try-on ─────────────────────────────────────────────────────────────


class TryOnHandler(_CapabilityHandlerBase):
    @property
    def name(self) -> str:
        return "try_on"

    @property
    def description(self) -> str:
        return (
            "Virtual try-on: dress a person image with a garment image. "
            "Produces a single output image where the person wears the "
            "garment. Uses the active try-on service (Kling VTON, Fashn, "
            "IDM-VTON, …). Both inputs are URLs (HTTP or fs://filestore/...)."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "person_image": {"type": "string", "description": "URL of the person image"},
                "garment_image": {"type": "string", "description": "URL of the garment image"},
                "destination": {"type": "string"},
                "path": {"type": "string"},
                "model": {"type": "string", "description": "Override the try-on model (e.g. 'fashn-virtual-try-on', 'idm-vton-api', 'kling-ai-vton', 'glass-virtual-try-on')."},
            },
            "required": ["person_image", "garment_image"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        svc, err = self._get_service(arguments)
        if not svc:
            return f"Error: {err or 'no try-on service available'}"
        person = self._rewrite(arguments.get("person_image", "") or "", service=svc)
        garment = self._rewrite(arguments.get("garment_image", "") or "", service=svc)
        if not person or not garment:
            return "Error: `person_image` and `garment_image` are required"
        try:
            kwargs = {k: v for k, v in arguments.items()
                      if k not in ("destination", "path",
                                   "person_image", "garment_image", *_SERVICE_ARG_NAMES)}
            r = svc.try_on(person_image=person, garment_image=garment,
                           **kwargs)
            ct = r.get("content_type", "image/png").split(";")[0].strip()
            ext = {"image/png": "png", "image/jpeg": "jpg"}.get(ct, "png")
            filename = arguments.get("path") or f"tryon_{int(time.time())}.{ext}"
            destination = arguments.get("destination", "filestore")
            return self._persist(destination, filename, r, "Try-on result")
        except Exception as e:
            return f"Error running try-on: {e}"


# ── Lipsync ────────────────────────────────────────────────────────────


class LipsyncHandler(_CapabilityHandlerBase):
    @property
    def name(self) -> str:
        return "lipsync"

    @property
    def description(self) -> str:
        return (
            "Generate a lipsync video: drive a face (video or still image) "
            "with an audio track. Produces an MP4 where the face speaks the "
            "audio. Uses the active lipsync service (OmniHuman, Kling "
            "Avatar, Sync Lipsync, …). Provide `audio_url` and either "
            "`video_url` (preferred) or `image_url`."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "video_url": {"type": "string", "description": "Source face video (URL)"},
                "image_url": {"type": "string", "description": "Source face image (URL) — used if `video_url` is absent"},
                "audio_url": {"type": "string", "description": "Audio track (URL) to drive the face"},
                "destination": {"type": "string"},
                "path": {"type": "string"},
                "model": {"type": "string", "description": "Override the lipsync model (e.g. 'omnihuman', 'bytedance-omnihuman-v1-5-290', 'kling-ai-avatar-v2-pro-789')."},
            },
            "required": ["audio_url"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        svc, err = self._get_service(arguments)
        if not svc:
            return f"Error: {err or 'no lipsync service available'}"
        video = self._rewrite(arguments.get("video_url", "") or "", service=svc)
        image = self._rewrite(arguments.get("image_url", "") or "", service=svc)
        audio = self._rewrite(arguments.get("audio_url", "") or "", service=svc)
        if not audio or not (video or image):
            return ("Error: `audio_url` is required plus either `video_url` "
                    "or `image_url`")
        try:
            kwargs = {k: v for k, v in arguments.items()
                      if k not in ("destination", "path",
                                   "video_url", "image_url", "audio_url", *_SERVICE_ARG_NAMES)}
            r = svc.lipsync(video_url=video, image_url=image,
                            audio_url=audio, **kwargs)
            filename = arguments.get("path") or f"lipsync_{int(time.time())}.mp4"
            destination = arguments.get("destination", "filestore")
            return self._persist(destination, filename, r, "Lipsync video")
        except Exception as e:
            return f"Error running lipsync: {e}"


# ── Trainer ────────────────────────────────────────────────────────────


class TrainImageModelHandler(_CapabilityHandlerBase):
    @property
    def name(self) -> str:
        return "train_image_model"

    @property
    def description(self) -> str:
        return (
            "Fine-tune an image model on a dataset (LoRA training on Flux "
            "variants, or full training on supported bases). Returns a "
            "URL to the trained LoRA / checkpoint. This is an async job — "
            "the service polls internally and the call waits for completion "
            "(no timeout, cancel via /stop). Pass `dataset_url` (zip archive "
            "of training images) and an optional `base_model` name."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "dataset_url": {"type": "string", "description": "URL (or fs://) to the training dataset (zip)"},
                "base_model": {"type": "string", "description": "Base model to fine-tune (optional)"},
                "steps": {"type": "integer", "description": "Training steps (provider-dependent default)"},
                "learning_rate": {"type": "number"},
                "trigger_word": {"type": "string"},
                "model": {"type": "string", "description": "Override the trainer model (e.g. 'flux-2-pro-text-to-image-trainer-712', 'flux-2-pro-image-to-image-trainer-831', 'qwen-image-edit-plus-trainer')."},
            },
            "required": ["dataset_url"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        svc, err = self._get_service(arguments)
        if not svc:
            return f"Error: {err or 'no trainer service available'}"
        dataset = self._rewrite(arguments.get("dataset_url", "") or "", service=svc)
        base_model = arguments.get("base_model", "") or ""
        if not dataset:
            return "Error: `dataset_url` is required"
        try:
            kwargs = {k: v for k, v in arguments.items()
                      if k not in ("dataset_url", "base_model", *_SERVICE_ARG_NAMES)}
            r = svc.train(dataset_url=dataset, base_model=base_model,
                          **kwargs)
            lora = r.get("lora_url") or r.get("source_url") or ""
            if not lora:
                return "Error: training completed but no LoRA URL returned"
            return f"Training complete. LoRA: {lora}"
        except Exception as e:
            return f"Error training image model: {e}"


# ── Speech to Video ─────────────────────────────────────────────────


