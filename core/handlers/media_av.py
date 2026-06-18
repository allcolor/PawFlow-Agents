"""Audio/video media tool handlers (video and audio generation).

Extracted from media.py to keep files <=800 lines. Shared helpers live in
core.handlers._media_common; re-exported from core.handlers.media.
"""

import logging
from typing import Any, Dict

from core.tool_handler import ToolHandler
from core.handlers._media_common import (
    _resolve_explicit_media_service,
    _write_media_result,
)

logger = logging.getLogger(__name__)


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
    _agent_name: str = ""

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
                "reference_image_urls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Reference image URLs for reference-to-video mode "
                        "(HTTP or fs://filestore/<id>/<name>)."),
                },
                "video_mode": {
                    "type": "string",
                    "description": "Optional video operation hint. Use 'extend' with video_url to continue an existing video.",
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
                        "'luma-dream-machine-ray-2-flash-image-to-video', "
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

    def set_agent_name(self, agent_name: str):
        self._agent_name = agent_name

    def set_service_resolver(self, resolver):
        """Set a resolver function: () -> (service, error_msg)."""
        self._service_resolver = resolver

    @staticmethod
    def _required_video_methods(arguments: Dict[str, Any]):
        if arguments.get("image_url") and arguments.get("end_image_url"):
            return ("frame_to_video",)
        if arguments.get("video_url") and arguments.get("video_mode") == "extend":
            return ("video_extend",)
        if arguments.get("video_url"):
            return ("video_edit",)
        if arguments.get("reference_image_urls"):
            return ("reference_to_video",)
        if arguments.get("image_url"):
            return ("image_to_video", "reference_to_video")
        return ("generate",)

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
            required_methods = self._required_video_methods(arguments)
            try:
                import inspect
                params = inspect.signature(self._service_resolver).parameters
                accepts_override = any(
                    param.kind in (
                        param.POSITIONAL_ONLY,
                        param.POSITIONAL_OR_KEYWORD,
                        param.VAR_POSITIONAL,
                    )
                    for param in params.values()
                )
            except (TypeError, ValueError):
                accepts_override = False
            if accepts_override:
                service, error = self._service_resolver(required_methods)
            else:
                service, error = self._service_resolver()
        if not service:
            return f"Error: {error or 'no video generation service available'}"

        prompt = arguments.get("prompt", "")
        if not prompt:
            return "Error: no prompt provided"

        # Reference inputs (image/video frames) are shared as public,
        # gateway-key URLs only for the duration of this call, then revoked
        # in the finally below — the provider fetches them over HTTP.
        from core.media_share import TemporaryPublicRefs
        share = TemporaryPublicRefs(self._base_url, self._user_id)
        image_url = share.public_url(arguments.get("image_url", "") or "", service=service)
        video_url = share.public_url(arguments.get("video_url", "") or "", service=service)
        end_image_url = share.public_url(arguments.get("end_image_url", "") or "", service=service)
        reference_image_urls = arguments.get("reference_image_urls") or []
        if isinstance(reference_image_urls, str):
            reference_image_urls = [reference_image_urls]
        reference_image_urls = [share.public_url(u, service=service) for u in reference_image_urls]
        destination = arguments.get("destination", "filestore")

        try:
            if hasattr(service, "set_runtime_context"):
                service.set_runtime_context(
                    user_id=self._user_id,
                    conversation_id=getattr(self, "_conversation_id", "") or "",
                    agent_name=getattr(self, "_agent_name", "") or "",
                )
            if hasattr(service, "set_callback_base_url"):
                service.set_callback_base_url(self._base_url)
            gen_args = {k: v for k, v in arguments.items()
                        if k not in ("destination", "path", "image_url",
                                     "video_url", "end_image_url", "reference_image_urls",
                                     "service", "video_service")}

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
                if arguments.get("video_mode") == "extend":
                    if not hasattr(service, 'video_extend'):
                        return ("Error: the active video service does not support "
                                "video extension.")
                    gen_args["video_url"] = video_url
                    result = service.video_extend(**gen_args)
                elif not hasattr(service, 'video_edit'):
                    return ("Error: the active video service does not support "
                            "video_edit. Use a model with a video_edit operation "
                            "(e.g. 'seedance-2-0-fast', 'kling-o1-edit-video-video-to-video-634').")
                else:
                    gen_args["video_url"] = video_url
                    result = service.video_edit(**gen_args)
            elif reference_image_urls:
                if not hasattr(service, 'reference_to_video'):
                    return ("Error: the active video service does not support "
                            "reference_to_video.")
                gen_args["reference_image_urls"] = reference_image_urls
                result = service.reference_to_video(**gen_args)
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
                            "'seedance-2-0-fast').")
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
            write_result = _write_media_result(
                resolver, destination, filename, result,
                "video_bytes", "video_path", ct)

            if write_result.get("file_id"):
                url = f"fs://filestore/{write_result['file_id']}/{filename}"
                return f"Video generated: {url}\nfile_id: {write_result['file_id']}"
            else:
                return f"Video generated and saved to {write_result.get('destination', destination)}: {write_result.get('path', filename)}"

        except Exception as e:
            return f"Error generating video: {e}"
        finally:
            # Revoke the temporary public access granted to reference inputs.
            share.restore()


class AudioGenerationHandler(ToolHandler):
    """Generate audio/music via a dynamically resolved audio generation service."""

    _base_url: str = "http://localhost:9090"
    _service_resolver = None
    _user_id: str = ""
    _conversation_id: str = ""
    _agent_name: str = ""

    @property
    def name(self) -> str:
        return "generate_audio"

    @property
    def description(self) -> str:
        return (
            "Generate audio, music, or text-to-speech from a text prompt. "
            "Returns a download URL for the generated audio file. "
            "Supports music generation, sound effects, and TTS services such "
            "as local Supertonic."
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
                "voice": {
                    "type": "string",
                    "description": "Voice name for TTS services, e.g. Supertonic M1-M5/F1-F5.",
                },
                "lang": {
                    "type": "string",
                    "description": "Language code for TTS services, e.g. fr, en, ja, ko, or na.",
                },
                "steps": {
                    "type": "integer",
                    "description": "Quality steps for TTS services that expose denoising/inference steps.",
                },
                "speed": {
                    "type": "number",
                    "description": "Speech speed for TTS services.",
                },
                "response_format": {
                    "type": "string",
                    "description": "Requested audio format for TTS services, e.g. wav, flac, or ogg.",
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

    def set_agent_name(self, agent_name: str):
        self._agent_name = agent_name

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
                    agent_name=getattr(self, "_agent_name", "") or "",
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

            def _audio_variation_name(base_name: str, idx: int, vext: str) -> str:
                stem, sep, tail = base_name.rpartition(".")
                if sep and "/" not in tail and "\\" not in tail:
                    return f"{stem}_v{idx}.{tail}"
                return f"{base_name}_v{idx}.{vext}"

            # Store all variations (Suno returns 2, others return 1)
            variations = result.get("variations", [result])
            output_lines = []
            for i, var in enumerate(variations):
                _vbytes = var.get("audio_bytes", result.get("audio_bytes"))
                _vpath = var.get("audio_path", result.get("audio_path"))
                _vct = var.get("content_type", ct)
                _vext = {
                    "audio/mpeg": "mp3", "audio/mp3": "mp3",
                    "audio/wav": "wav", "audio/ogg": "ogg",
                }.get(_vct.split(";")[0].strip(), ext)
                _vtitle = var.get("title", "")
                _vdur = var.get("duration", 0)
                _variation_idx = int(var.get("variation_index") or 0)
                if len(variations) > 1 or _variation_idx:
                    _vname = _audio_variation_name(filename, _variation_idx or i + 1, _vext)
                else:
                    _vname = filename
                _payload = {
                    "audio_bytes": _vbytes,
                    "audio_path": _vpath,
                    "_delete_media_path": var.get(
                        "_delete_media_path", result.get("_delete_media_path")),
                }
                _vresult = _write_media_result(
                    resolver, destination, _vname, _payload,
                    "audio_bytes", "audio_path", _vct)
                if _vresult.get("file_id"):
                    _vurl = f"fs://filestore/{_vresult['file_id']}/{_vname}"
                    _label = f"{_vtitle} ({_vdur:.0f}s)" if _vtitle else f"variation {i+1}"
                    output_lines.append(f"{_label}: {_vurl}\nfile_id: {_vresult['file_id']}")
                else:
                    output_lines.append(f"Saved to {_vresult.get('path', _vname)}")

            return "Audio generated:\n" + "\n".join(output_lines)

        except Exception as e:
            return f"Error generating audio: {e}"


