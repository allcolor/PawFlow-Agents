"""Auto-extracted from core/tool_registry.py — see core/handlers/__init__.py"""

import json
import logging
import re
import threading
from typing import Dict, Any, List, Optional

from core.tool_handler import ToolHandler

logger = logging.getLogger(__name__)


def _resolve_explicit_media_service(service_id: str, user_id: str = "", conversation_id: str = ""):
    if not service_id:
        return None, ""
    try:
        from core.service_registry import ServiceRegistry
        svc = ServiceRegistry.get_instance().resolve(
            service_id, user_id=user_id, conv_id=conversation_id)
    except Exception as exc:
        return None, f"media service '{service_id}' failed to resolve: {exc}"
    if not svc:
        return None, f"media service '{service_id}' not found or not connected"
    return svc, ""


class ImageGenerationHandler(ToolHandler):
    """Generate images via a dynamically resolved image generation service.

    At execution time, calls a resolver function that discovers available
    image services and selects one based on per-agent conversation preferences.
    Handles FileStore storage and URL creation.
    """

    _base_url: str = "http://localhost:9090"
    _service_resolver = None  # () -> (service, error_msg)
    _user_id: str = ""
    _conversation_id: str = ""

    @property
    def name(self) -> str:
        return "generate_image"

    @property
    def description(self) -> str:
        return (
            "Generate an image from a text prompt. "
            "ALWAYS set width/height to match your target size (min 256). "
            "For small assets (icons, sprites), generate at 256-512 and resize with a script. "
            "PREFER writing directly to the user's filesystem: set destination to "
            "the filesystem service name and path to the target file "
            "(e.g. destination='fs_xxx', path='assets/player.png'). "
            "This avoids extra copy steps. Use 'filestore' only when you need a "
            "temporary download URL. "
            "To read a FileStore image from a script, use read_file with fs://filestore/file_id."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Detailed description of the image to generate",
                },
                "service": {
                    "type": "string",
                    "description": "Optional image service id override for this call (e.g. codex_image_service).",
                },
                "image_service": {
                    "type": "string",
                    "description": "Alias for service; optional image service id override.",
                },
                "negative_prompt": {
                    "type": "string",
                    "description": "What to avoid in the image (optional)",
                },
                "width": {
                    "type": "integer",
                    "description": "Image width in pixels (default 1024, minimum 256). For small assets like icons, generate at 256-512 then resize with a script.",
                },
                "height": {
                    "type": "integer",
                    "description": "Image height in pixels (default 1024, minimum 256).",
                },
                "destination": {
                    "type": "string",
                    "description": "Where to save: 'filestore' (default, returns a download URL) or the user's filesystem service name (writes directly to their disk). When using a filesystem, also provide 'path'. If only one filesystem service is connected, any name resolves to it.",
                },
                "path": {
                    "type": "string",
                    "description": "File path when destination is a filesystem service (e.g. 'assets/hero.png')",
                },
                "output_format": {
                    "type": "string",
                    "description": "Image format: 'png' (default, supports transparency), 'jpeg', or 'webp'",
                },
                "aspect_ratio": {
                    "type": "string",
                    "description": "Aspect ratio (e.g. '1:1', '16:9', '9:16', '4:3', '3:2'). Alternative to width/height.",
                },
                "style": {
                    "type": "string",
                    "description": "Style preset name (model-dependent, e.g. 'Recraft V3 Raw')",
                },
                "num_inference_steps": {
                    "type": "integer",
                    "description": "Number of inference steps (higher = better quality but slower, default varies by model)",
                },
                "guidance_scale": {
                    "type": "number",
                    "description": "Guidance scale / CFG (higher = more prompt adherence, default 5)",
                },
                "model": {
                    "type": "string",
                    "description": "Override the active service's default image model for this call (e.g. 'nano-banana-pro', 'flux-2-pro-text-to-image-799'). Use get_image_model_info to list available models.",
                },
            },
            "required": ["prompt"],
        }

    def set_base_url(self, base_url: str):
        self._base_url = base_url.rstrip("/")

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    def set_conversation_id(self, conversation_id: str):
        self._conversation_id = conversation_id

    def set_service_resolver(self, resolver):
        """Set a resolver function: () -> (service, error_msg)."""
        self._service_resolver = resolver

    def execute(self, arguments: Dict[str, Any]) -> str:
        import time as _time

        service_name = (arguments.get("service")
                        or arguments.get("image_service")
                        or "")
        if service_name:
            service, error = _resolve_explicit_media_service(
                service_name,
                user_id=self._user_id,
                conversation_id=getattr(self, "_conversation_id", "") or "",
            )
        else:
            if not self._service_resolver:
                return "Error: no image service resolver configured"
            service, error = self._service_resolver()
        if not service:
            return f"Error: {error or 'no image generation service available'}"

        prompt = arguments.get("prompt", "")
        if not prompt:
            return "Error: no prompt provided"

        destination = arguments.get("destination", "filestore")

        try:
            if hasattr(service, "set_runtime_context"):
                service.set_runtime_context(
                    user_id=self._user_id,
                    conversation_id=getattr(self, "_conversation_id", "") or "",
                )
            gen_args = {k: v for k, v in arguments.items()
                        if k not in ("destination", "path", "service", "image_service")}
            result = service.generate(**gen_args)

            ct = result["content_type"]
            ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(
                ct.split(";")[0].strip(), "png"
            )
            filename = arguments.get("path") or f"generated_{int(_time.time())}_{hash(prompt) & 0xFFFF:04x}.{ext}"

            from core.storage_resolver import StorageResolver
            resolver = StorageResolver(user_id=self._user_id, conversation_id=getattr(self, "_conversation_id", "") or "")
            write_result = resolver.write(destination, filename,
                                           result["image_bytes"], ct)

            if write_result.get("file_id"):
                url = f"fs://filestore/{write_result['file_id']}/{filename}"
                return f"Image generated: {url}\nfile_id: {write_result['file_id']}"
            else:
                return f"Image generated and saved to {write_result.get('destination', destination)}: {write_result.get('path', filename)}"

        except Exception as e:
            return f"Error generating image: {e}"


class EditImageHandler(ToolHandler):
    """Edit one or more existing images via the image generation service.

    Calls the active image service's `edit_image(prompt, image_urls, ...)`
    operation. Only models that declare an `edit_image` operation in
    `pixazo_catalog.json` (e.g. nano-banana) support this — others will
    return a clear error pointing the agent at a model that does.
    """

    _base_url: str = "http://localhost:9090"
    _service_resolver = None  # () -> (service, error_msg)
    _user_id: str = ""
    _conversation_id: str = ""

    @property
    def name(self) -> str:
        return "edit_image"

    @property
    def description(self) -> str:
        return (
            "Edit one or more existing images per a text prompt. Pass the "
            "source images as a list of URLs (HTTP or fs://filestore/<id>/<name>) "
            "via `image_urls`. Output is saved to FileStore (default) or a "
            "filesystem service (set destination + path). Use `generate_image` "
            "for text-only generation; this tool requires existing inputs."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Edit instruction (e.g. 'make the sky stormy', 'add a red hat')",
                },
                "image_urls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Source image URLs. HTTP/HTTPS and fs://filestore/<id>/<name> "
                        "are accepted. Most edit-capable models take 1 source; some "
                        "support multi-image fusion."),
                },
                "service": {
                    "type": "string",
                    "description": "Optional image service id override for this call (e.g. codex_image_service).",
                },
                "image_service": {
                    "type": "string",
                    "description": "Alias for service; optional image service id override.",
                },
                "destination": {
                    "type": "string",
                    "description": "Where to save the result: 'filestore' (default) or a filesystem service name (with `path`).",
                },
                "path": {
                    "type": "string",
                    "description": "File path when destination is a filesystem service.",
                },
                "output_format": {
                    "type": "string",
                    "description": "Output format: 'png' (default), 'jpeg', 'webp'.",
                },
                "num_images": {
                    "type": "integer",
                    "description": "Number of variants to produce (default 1).",
                },
                "model": {
                    "type": "string",
                    "description": "Override the active edit model for this call (e.g. 'nano-banana', 'qwen-image-edit-plus'). Must declare an edit_image op.",
                },
            },
            "required": ["prompt", "image_urls"],
        }

    def set_base_url(self, base_url: str):
        self._base_url = base_url.rstrip("/")

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    def set_conversation_id(self, conversation_id: str):
        self._conversation_id = conversation_id

    def set_service_resolver(self, resolver):
        """Set a resolver: () -> (service, error_msg). Same shape as ImageGenerationHandler."""
        self._service_resolver = resolver

    def _resolve_filestore_url(self, url: str, service=None) -> str:
        """Convert fs://filestore/<id>/<name> to HTTP unless service reads it locally."""
        if not url.startswith("fs://filestore/"):
            return url
        if service is not None and getattr(service, "ACCEPTS_FILESTORE_URLS", False):
            return url
        rest = url[len("fs://filestore/"):]
        fid = rest.split("/", 1)[0]
        if not fid:
            return url
        return f"{self._base_url}/files/{fid}"

    def execute(self, arguments: Dict[str, Any]) -> str:
        import time as _time

        service_name = (arguments.get("service")
                        or arguments.get("image_service")
                        or "")
        if service_name:
            service, error = _resolve_explicit_media_service(
                service_name,
                user_id=self._user_id,
                conversation_id=getattr(self, "_conversation_id", "") or "",
            )
        else:
            if not self._service_resolver:
                return "Error: no image service resolver configured"
            service, error = self._service_resolver()
        if not service:
            return f"Error: {error or 'no image generation service available'}"

        prompt = arguments.get("prompt", "")
        if not prompt:
            return "Error: no prompt provided"
        image_urls = arguments.get("image_urls") or []
        if isinstance(image_urls, str):
            image_urls = [image_urls]
        if not image_urls:
            return "Error: image_urls is required (at least one source URL)"
        # Resolve fs://filestore/... to HTTP only for providers that cannot read FileStore locally.
        image_urls = [self._resolve_filestore_url(u, service=service) for u in image_urls]

        destination = arguments.get("destination", "filestore")
        if not hasattr(service, "edit_image"):
            return ("Error: the active image service does not implement "
                    "edit_image. Switch to a model with an edit_image "
                    "operation (e.g. 'nano-banana').")

        try:
            if hasattr(service, "set_runtime_context"):
                service.set_runtime_context(
                    user_id=self._user_id,
                    conversation_id=getattr(self, "_conversation_id", "") or "",
                )
            edit_kwargs = {k: v for k, v in arguments.items()
                           if k not in ("destination", "path", "image_urls", "service", "image_service")}
            edit_kwargs["image_urls"] = image_urls
            result = service.edit_image(**edit_kwargs)

            ct = result["content_type"]
            ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(
                ct.split(";")[0].strip(), "png")
            filename = arguments.get("path") or (
                f"edited_{int(_time.time())}_{hash(prompt) & 0xFFFF:04x}.{ext}")

            from core.storage_resolver import StorageResolver
            resolver = StorageResolver(
                user_id=self._user_id,
                conversation_id=getattr(self, "_conversation_id", "") or "")
            write_result = resolver.write(destination, filename,
                                          result["image_bytes"], ct)

            if write_result.get("file_id"):
                url = f"fs://filestore/{write_result['file_id']}/{filename}"
                return f"Image edited: {url}\nfile_id: {write_result['file_id']}"
            return (f"Image edited and saved to "
                    f"{write_result.get('destination', destination)}: "
                    f"{write_result.get('path', filename)}")
        except Exception as e:
            return f"Error editing image: {e}"


class VideoGenerationHandler(ToolHandler):
    """Generate videos via a dynamically resolved video generation service.

    Supports three modes:
    - text-to-video: prompt only (default)
    - image-to-video: prompt + image_url (continuation / animation)
    - video-edit: prompt + video_url (style transfer / editing)
    """

    _base_url: str = "http://localhost:9090"
    _service_resolver = None  # () -> (service, error_msg)
    _user_id: str = ""
    _conversation_id: str = ""

    @property
    def name(self) -> str:
        return "generate_video"

    @property
    def description(self) -> str:
        return (
            "Generate a video from a text prompt using the active video generation\n"
            "service (provider-dependent -- e.g. Runway, Kling, etc.).\n\n"
            "Three modes:\n"
            "  1. Text-to-video (default): provide only `prompt`.\n"
            "  2. Image-to-video: provide `prompt` + `image_url` to animate a\n"
            "     still image or continue a video from its last frame.\n"
            "  3. Video edit: provide `prompt` + `video_url` to apply style\n"
            "     transfer or editing to an existing video.\n\n"
            "Returns a download URL (FileStore) or writes directly to a filesystem\n"
            "service when destination + path are provided.\n\n"
            "Be descriptive in your prompt for best results. Generation may take\n"
            "30 seconds to several minutes depending on the provider and duration."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Detailed description of the video to generate",
                },
                "service": {
                    "type": "string",
                    "description": "Optional video service id override for this call.",
                },
                "video_service": {
                    "type": "string",
                    "description": "Alias for service; optional video service id override.",
                },
                "image_url": {
                    "type": "string",
                    "description": (
                        "Source image URL for image-to-video mode (HTTP or "
                        "fs://filestore/<id>/<name>). Use this to animate a "
                        "still image or continue a video from its last frame."
                    ),
                },
                "video_url": {
                    "type": "string",
                    "description": (
                        "Source video URL for video-edit mode (HTTP or "
                        "fs://filestore/<id>/<name>). Use this for style "
                        "transfer, re-editing, or video-to-video transformation."
                    ),
                },
                "end_image_url": {
                    "type": "string",
                    "description": (
                        "End-frame image URL for frame-to-video mode "
                        "(Kling O1). Used with image_url as start frame "
                        "to create a transition video between two images."
                    ),
                },
                "negative_prompt": {
                    "type": "string",
                    "description": "What to avoid in the video (optional)",
                },
                "duration": {
                    "type": "number",
                    "description": "Video duration in seconds (optional, provider-dependent)",
                },
                "width": {
                    "type": "integer",
                    "description": "Video width in pixels (optional)",
                },
                "height": {
                    "type": "integer",
                    "description": "Video height in pixels (optional)",
                },
                "destination": {
                    "type": "string",
                    "description": "Where to save: 'filestore' (default) or relay service name. When using a relay, also provide 'path'.",
                },
                "path": {
                    "type": "string",
                    "description": "File path when destination is a filesystem service",
                },
                "model": {
                    "type": "string",
                    "description": (
                        "Override the active video model for this call. "
                        "For i2v try: 'kling-3-0-image-to-video-standard', "
                        "'sora-video', 'luma-dream-machine-ray-2-flash-image-to-video', "
                        "'wan-2-6-image-to-video-477'. "
                        "For video-edit: 'seedance-2-0-fast', 'seedance-2-0', "
                        "'kling-o1-edit-video-video-to-video-634'."
                    ),
                },
            },
            "required": ["prompt"],
        }

    def set_base_url(self, base_url: str):
        self._base_url = base_url.rstrip("/")

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    def set_conversation_id(self, conversation_id: str):
        self._conversation_id = conversation_id

    def set_service_resolver(self, resolver):
        """Set a resolver function: () -> (service, error_msg)."""
        self._service_resolver = resolver

    def _rewrite(self, url: str, service=None) -> str:
        """Convert fs://filestore/<id>/<name> to HTTP unless service reads it locally."""
        if not url or not url.startswith("fs://filestore/"):
            return url
        if service is not None and getattr(service, "ACCEPTS_FILESTORE_URLS", False):
            return url
        rest = url[len("fs://filestore/"):]
        fid = rest.split("/", 1)[0]
        if not fid:
            return url
        return f"{self._base_url}/files/{fid}"

    def execute(self, arguments: Dict[str, Any]) -> str:
        import time as _time

        service_name = (arguments.get("service")
                        or arguments.get("video_service")
                        or "")
        if service_name:
            service, error = _resolve_explicit_media_service(
                service_name,
                user_id=self._user_id,
                conversation_id=getattr(self, "_conversation_id", "") or "",
            )
        else:
            if not self._service_resolver:
                return "Error: no video service resolver configured"
            service, error = self._service_resolver()
        if not service:
            return f"Error: {error or 'no video generation service available'}"

        prompt = arguments.get("prompt", "")
        if not prompt:
            return "Error: no prompt provided"

        image_url = self._rewrite(arguments.get("image_url", "") or "", service=service)
        video_url = self._rewrite(arguments.get("video_url", "") or "", service=service)
        end_image_url = self._rewrite(arguments.get("end_image_url", "") or "", service=service)
        destination = arguments.get("destination", "filestore")

        try:
            if hasattr(service, "set_runtime_context"):
                service.set_runtime_context(
                    user_id=self._user_id,
                    conversation_id=getattr(self, "_conversation_id", "") or "",
                )
            gen_args = {k: v for k, v in arguments.items()
                        if k not in ("destination", "path", "image_url",
                                     "video_url", "end_image_url", "service", "video_service")}

            if image_url and end_image_url:
                # Frame-to-video mode (start + end frame)
                if not hasattr(service, 'frame_to_video'):
                    return ("Error: the active video service does not support "
                            "frame_to_video. Use model "
                            "'kling-o1-first-frame-last-frame-to-video-857'.")
                gen_args["image_url"] = image_url
                gen_args["end_image_url"] = end_image_url
                result = service.frame_to_video(**gen_args)
            elif video_url:
                # Video-edit mode
                if not hasattr(service, 'video_edit'):
                    return ("Error: the active video service does not support "
                            "video_edit. Use a model with a video_edit operation "
                            "(e.g. 'seedance-2-0-fast', 'kling-o1-edit-video-video-to-video-634').")
                gen_args["video_url"] = video_url
                result = service.video_edit(**gen_args)
            elif image_url:
                # Image-to-video mode — try image_to_video first,
                # fall back to reference_to_video (Seedance)
                gen_args["image_url"] = image_url
                if hasattr(service, 'image_to_video'):
                    try:
                        result = service.image_to_video(**gen_args)
                    except Exception:
                        if hasattr(service, 'reference_to_video'):
                            result = service.reference_to_video(**gen_args)
                        else:
                            raise
                elif hasattr(service, 'reference_to_video'):
                    result = service.reference_to_video(**gen_args)
                else:
                    return ("Error: the active video service does not support "
                            "image_to_video. Use a model with an image_to_video "
                            "operation (e.g. 'kling-3-0-image-to-video-standard', "
                            "'sora-video', 'seedance-2-0-fast').")
            else:
                # Text-to-video mode (default)
                result = service.generate(**gen_args)

            ct = result["content_type"]
            ext = {
                "video/mp4": "mp4", "video/webm": "webm",
                "video/quicktime": "mov", "video/x-msvideo": "avi",
            }.get(ct.split(";")[0].strip(), "mp4")
            filename = arguments.get("path") or f"generated_{int(_time.time())}_{hash(prompt) & 0xFFFF:04x}.{ext}"

            from core.storage_resolver import StorageResolver
            resolver = StorageResolver(user_id=self._user_id, conversation_id=getattr(self, "_conversation_id", "") or "")
            write_result = resolver.write(destination, filename,
                                           result["video_bytes"], ct)

            if write_result.get("file_id"):
                url = f"fs://filestore/{write_result['file_id']}/{filename}"
                return f"Video generated: {url}\nfile_id: {write_result['file_id']}"
            else:
                return f"Video generated and saved to {write_result.get('destination', destination)}: {write_result.get('path', filename)}"

        except Exception as e:
            return f"Error generating video: {e}"


class AudioGenerationHandler(ToolHandler):
    """Generate audio/music via a dynamically resolved audio generation service."""

    _base_url: str = "http://localhost:9090"
    _service_resolver = None
    _user_id: str = ""
    _conversation_id: str = ""

    @property
    def name(self) -> str:
        return "generate_audio"

    @property
    def description(self) -> str:
        return (
            "Generate audio or music from a text prompt. "
            "Returns a download URL for the generated audio file. "
            "Supports music generation (with lyrics or instrumental) and sound effects."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Description of the audio to generate (style, mood, instruments, genre)",
                },
                "service": {
                    "type": "string",
                    "description": "Optional audio service id override for this call.",
                },
                "audio_service": {
                    "type": "string",
                    "description": "Alias for service; optional audio service id override.",
                },
                "lyrics": {
                    "type": "string",
                    "description": "Song lyrics (optional — omit for instrumental)",
                },
                "duration": {
                    "type": "number",
                    "description": "Audio duration in seconds (optional, provider-dependent)",
                },
                "instrumental": {
                    "type": "boolean",
                    "description": "Generate instrumental only, no vocals (default: false)",
                },
                "style": {
                    "type": "string",
                    "description": "Music style/genre (e.g. 'electronic', 'ambient', 'rock', 'orchestral')",
                },
                "destination": {
                    "type": "string",
                    "description": "Where to save: 'filestore' (default) or filesystem service name. When using a filesystem, also provide 'path'.",
                },
                "path": {
                    "type": "string",
                    "description": "File path when destination is a filesystem service (e.g. 'assets/music.mp3')",
                },
                "model": {
                    "type": "string",
                    "description": "Override the active audio model for this call (e.g. 'lyria-2', 'minimax-music', 'eleven-v3-alpha-954', 'chatterbox-text-to-speech').",
                },
                "callback_url": {
                    "type": "string",
                    "description": "Optional provider callback URL override for audio services that require one, such as Suno.",
                },
            },
            "required": ["prompt"],
        }

    def set_base_url(self, base_url: str):
        self._base_url = base_url.rstrip("/")

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    def set_conversation_id(self, conversation_id: str):
        self._conversation_id = conversation_id

    def set_service_resolver(self, resolver):
        """Set a resolver function: () -> (service, error_msg)."""
        self._service_resolver = resolver

    def execute(self, arguments: Dict[str, Any]) -> str:
        import time as _time

        service_name = (arguments.get("service")
                        or arguments.get("audio_service")
                        or "")
        if service_name:
            service, error = _resolve_explicit_media_service(
                service_name,
                user_id=self._user_id,
                conversation_id=getattr(self, "_conversation_id", "") or "",
            )
        else:
            if not self._service_resolver:
                return "Error: no audio service resolver configured"
            service, error = self._service_resolver()
        if not service:
            return f"Error: {error or 'no audio generation service available'}"

        prompt = arguments.get("prompt", "")
        if not prompt:
            return "Error: no prompt provided"

        destination = arguments.get("destination", "filestore")

        try:
            if hasattr(service, "set_runtime_context"):
                service.set_runtime_context(
                    user_id=self._user_id,
                    conversation_id=getattr(self, "_conversation_id", "") or "",
                )
            if hasattr(service, "set_callback_base_url"):
                service.set_callback_base_url(self._base_url)
            gen_args = {k: v for k, v in arguments.items()
                        if k not in ("destination", "path", "_service", "service", "audio_service")}
            result = service.generate(**gen_args)

            ct = result.get("content_type", "audio/mpeg")
            ext = {
                "audio/mpeg": "mp3", "audio/mp3": "mp3",
                "audio/wav": "wav", "audio/x-wav": "wav",
                "audio/ogg": "ogg", "audio/flac": "flac",
                "audio/aac": "aac", "audio/mp4": "m4a",
            }.get(ct.split(";")[0].strip(), "mp3")
            filename = arguments.get("path") or f"generated_{int(_time.time())}_{hash(prompt) & 0xFFFF:04x}.{ext}"

            from core.storage_resolver import StorageResolver
            resolver = StorageResolver(user_id=self._user_id, conversation_id=getattr(self, "_conversation_id", "") or "")
            write_result = resolver.write(destination, filename,
                                           result["audio_bytes"], ct)

            # Store all variations (Suno returns 2, others return 1)
            variations = result.get("variations", [result])
            output_lines = []
            for i, var in enumerate(variations):
                _vbytes = var.get("audio_bytes", result.get("audio_bytes"))
                _vct = var.get("content_type", ct)
                _vext = {
                    "audio/mpeg": "mp3", "audio/mp3": "mp3",
                    "audio/wav": "wav", "audio/ogg": "ogg",
                }.get(_vct.split(";")[0].strip(), ext)
                _vtitle = var.get("title", "")
                _vdur = var.get("duration", 0)
                if len(variations) > 1:
                    _vname = arguments.get("path") or f"generated_{int(_time.time())}_{hash(prompt) & 0xFFFF:04x}_v{i+1}.{_vext}"
                else:
                    _vname = filename
                _vresult = resolver.write(destination, _vname, _vbytes, _vct)
                if _vresult.get("file_id"):
                    _vurl = f"fs://filestore/{_vresult['file_id']}/{_vname}"
                    _label = f"{_vtitle} ({_vdur:.0f}s)" if _vtitle else f"variation {i+1}"
                    output_lines.append(f"{_label}: {_vurl}\nfile_id: {_vresult['file_id']}")
                else:
                    output_lines.append(f"Saved to {_vresult.get('path', _vname)}")

            return "Audio generated:\n" + "\n".join(output_lines)

        except Exception as e:
            return f"Error generating audio: {e}"


class ImageModelInfoHandler(ToolHandler):
    """Return info about the active image generation model and its parameters."""

    _service_resolver = None
    _user_id: str = ""
    _conversation_id: str = ""

    @property
    def name(self) -> str:
        return "get_image_model_info"

    @property
    def description(self) -> str:
        return (
            "Get information about the active image generation model: "
            "model name, supported parameters, available models. "
            "Call this before generate_image if you need to know what "
            "parameters the current model supports (aspect_ratio, style, etc.)."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "description": "Optional image service id override for this call.",
                },
                "image_service": {
                    "type": "string",
                    "description": "Alias for service; optional image service id override.",
                },
            },
        }

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    def set_conversation_id(self, conversation_id: str):
        self._conversation_id = conversation_id

    def set_service_resolver(self, resolver):
        self._service_resolver = resolver

    def execute(self, arguments: Dict[str, Any]) -> str:
        service_name = (arguments.get("service")
                        or arguments.get("image_service")
                        or "")
        if service_name:
            service, error = _resolve_explicit_media_service(
                service_name,
                user_id=self._user_id,
                conversation_id=getattr(self, "_conversation_id", "") or "",
            )
        else:
            if not self._service_resolver:
                return "Error: no image service resolver configured"
            service, error = self._service_resolver()
        if not service:
            return f"Error: {error or 'no image generation service available'}"
        if hasattr(service, "set_runtime_context"):
            service.set_runtime_context(
                user_id=self._user_id,
                conversation_id=getattr(self, "_conversation_id", "") or "",
            )
        if hasattr(service, 'get_model_info'):
            info = service.get_model_info()
            return json.dumps(info, indent=2)
        return json.dumps({"model": "unknown", "model_params": {}})
