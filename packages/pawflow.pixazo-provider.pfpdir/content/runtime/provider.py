import mimetypes
from pathlib import Path

from pawflow import pfp

from services.pixazo_audio_service import PixazoAudioService
from services.pixazo_capability_services import (
    Pixazo3DService, PixazoLipsyncService, PixazoTrainerService,
    PixazoTryOnService, PixazoUpscaleService,
)
from services.pixazo_image_service import PixazoImageService
from services.pixazo_video_service import PixazoVideoService

CLASSES = {
    "image": (PixazoImageService, "image"),
    "video": (PixazoVideoService, "video"),
    "audio": (PixazoAudioService, "audio"),
    "3d": (Pixazo3DService, "model"),
    "upscale": (PixazoUpscaleService, "image"),
    "try-on": (PixazoTryOnService, "image"),
    "lipsync": (PixazoLipsyncService, "video"),
    "trainer": (PixazoTrainerService, "data"),
}

object_id = str(pfp.package.get("object_id") or "")
object_name = object_id.split(":", 1)[-1]
if object_name not in CLASSES:
    pfp.error(f"unsupported provider object: {object_id}")
    raise SystemExit(1)
service_class, artifact_kind = CLASSES[object_name]
service = service_class(dict(pfp.context.get("service_config") or {}))
callback_base = str(pfp.context.get("callback_base_url") or "")
if callback_base and hasattr(service, "set_callback_base_url"):
    service.set_callback_base_url(callback_base)
operation = str(pfp.payload.get("operation") or "")
arguments = pfp.payload.get("arguments") or {}
try:
    result = getattr(service, operation)(**arguments)
    if not isinstance(result, dict):
        raise TypeError("provider operation must return an object")
    payload = dict(result)
    media = None
    for key in ("image_bytes", "video_bytes", "audio_bytes", "bytes"):
        value = payload.pop(key, None)
        if value:
            media = bytes(value)
            break
    if media is not None:
        output_dir = Path(str(pfp.context.get("output_dir") or ""))
        if not output_dir.is_dir():
            raise ValueError("PFP output_dir is required for media operations")
        content_type = str(payload.get("content_type") or "application/octet-stream")
        ext = mimetypes.guess_extension(content_type.split(";", 1)[0].strip()) or ".bin"
        if content_type == "model/gltf-binary":
            ext = ".glb"
        filename = "result" + ext
        (output_dir / filename).write_bytes(media)
        payload.update(pfp.artifact(
            artifact_kind, filename, content_type, filename=filename))
    pfp.result(payload)
except Exception as exc:
    pfp.error(str(exc))
    raise SystemExit(1)
