"""Auto-extracted from core/tool_registry.py — see core/handlers/__init__.py"""

import json
import logging
import re
import threading
from typing import Dict, Any, List, Optional

from core.tool_handler import ToolHandler

logger = logging.getLogger(__name__)



class ImageGenerationHandler(ToolHandler):
    """Generate images via a dynamically resolved image generation service.

    At execution time, calls a resolver function that discovers available
    image services and selects one based on per-agent conversation preferences.
    Handles FileStore storage and URL creation.
    """

    _base_url: str = "http://localhost:9090"
    _service_resolver = None  # () -> (service, error_msg)
    _user_id: str = ""

    @property
    def name(self) -> str:
        return "generate_image"

    @property
    def description(self) -> str:
        return (
            "Generate an image from a text prompt. "
            "Returns a download URL for the generated image. "
            "Be descriptive in your prompt for best results. "
            "You can also provide a negative_prompt to exclude unwanted elements."
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
                "negative_prompt": {
                    "type": "string",
                    "description": "What to avoid in the image (optional)",
                },
                "width": {
                    "type": "integer",
                    "description": "Image width in pixels (optional)",
                },
                "height": {
                    "type": "integer",
                    "description": "Image height in pixels (optional)",
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
            },
            "required": ["prompt"],
        }

    def set_base_url(self, base_url: str):
        self._base_url = base_url.rstrip("/")

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    def set_service_resolver(self, resolver):
        """Set a resolver function: () -> (service, error_msg)."""
        self._service_resolver = resolver

    def execute(self, arguments: Dict[str, Any]) -> str:
        import time as _time

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
            gen_args = {k: v for k, v in arguments.items()
                        if k not in ("destination", "path")}
            result = service.generate(**gen_args)

            ct = result["content_type"]
            ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(
                ct.split(";")[0].strip(), "png"
            )
            filename = arguments.get("path") or f"generated_{int(_time.time())}_{hash(prompt) & 0xFFFF:04x}.{ext}"

            from core.storage_resolver import StorageResolver
            resolver = StorageResolver(user_id=self._user_id)
            write_result = resolver.write(destination, filename,
                                           result["image_bytes"], ct)

            if write_result.get("file_id"):
                url = f"{self._base_url}/files/{write_result['file_id']}/{filename}"
                return f"Image generated: {url}\nfile_id: {write_result['file_id']}"
            else:
                return f"Image generated and saved to {write_result.get('destination', destination)}: {write_result.get('path', filename)}"

        except Exception as e:
            return f"Error generating image: {e}"


class VideoGenerationHandler(ToolHandler):
    """Generate videos via a dynamically resolved video generation service.

    At execution time, calls a resolver function that discovers available
    video services and selects one based on per-agent conversation preferences.
    Handles FileStore storage and URL creation.
    """

    _base_url: str = "http://localhost:9090"
    _service_resolver = None  # () -> (service, error_msg)
    _user_id: str = ""

    @property
    def name(self) -> str:
        return "generate_video"

    @property
    def description(self) -> str:
        return (
            "Generate a video from a text prompt. "
            "Returns a download URL for the generated video. "
            "Be descriptive in your prompt for best results."
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
                    "description": "Where to save: 'filestore' (default) or filesystem service (e.g. 'fs:workspace'). When using a filesystem, also provide 'path'.",
                },
                "path": {
                    "type": "string",
                    "description": "File path when destination is a filesystem service",
                },
            },
            "required": ["prompt"],
        }

    def set_base_url(self, base_url: str):
        self._base_url = base_url.rstrip("/")

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    def set_service_resolver(self, resolver):
        """Set a resolver function: () -> (service, error_msg)."""
        self._service_resolver = resolver

    def execute(self, arguments: Dict[str, Any]) -> str:
        import time as _time

        if not self._service_resolver:
            return "Error: no video service resolver configured"
        service, error = self._service_resolver()
        if not service:
            return f"Error: {error or 'no video generation service available'}"

        prompt = arguments.get("prompt", "")
        if not prompt:
            return "Error: no prompt provided"

        destination = arguments.get("destination", "filestore")

        try:
            gen_args = {k: v for k, v in arguments.items()
                        if k not in ("destination", "path")}
            result = service.generate(**gen_args)

            ct = result["content_type"]
            ext = {
                "video/mp4": "mp4", "video/webm": "webm",
                "video/quicktime": "mov", "video/x-msvideo": "avi",
            }.get(ct.split(";")[0].strip(), "mp4")
            filename = arguments.get("path") or f"generated_{int(_time.time())}_{hash(prompt) & 0xFFFF:04x}.{ext}"

            from core.storage_resolver import StorageResolver
            resolver = StorageResolver(user_id=self._user_id)
            write_result = resolver.write(destination, filename,
                                           result["video_bytes"], ct)

            if write_result.get("file_id"):
                url = f"{self._base_url}/files/{write_result['file_id']}/{filename}"
                return f"Video generated: {url}\nfile_id: {write_result['file_id']}"
            else:
                return f"Video generated and saved to {write_result.get('destination', destination)}: {write_result.get('path', filename)}"

        except Exception as e:
            return f"Error generating video: {e}"


class ImageModelInfoHandler(ToolHandler):
    """Return info about the active image generation model and its parameters."""

    _service_resolver = None

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
        return {"type": "object", "properties": {}}

    def set_service_resolver(self, resolver):
        self._service_resolver = resolver

    def execute(self, arguments: Dict[str, Any]) -> str:
        if not self._service_resolver:
            return "Error: no image service resolver configured"
        service, error = self._service_resolver()
        if not service:
            return f"Error: {error or 'no image generation service available'}"
        if hasattr(service, 'get_model_info'):
            info = service.get_model_info()
            return json.dumps(info, indent=2)
        return json.dumps({"model": "unknown", "model_params": {}})
