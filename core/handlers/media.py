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

        # Resolve service dynamically
        if not self._service_resolver:
            return "Error: no image service resolver configured"
        service, error = self._service_resolver()
        if not service:
            return f"Error: {error or 'no image generation service available'}"

        prompt = arguments.get("prompt", "")
        if not prompt:
            return "Error: no prompt provided"

        try:
            result = service.generate(**arguments)
            # result = {"image_bytes": bytes, "content_type": str}

            from core.file_store import FileStore
            ct = result["content_type"]
            ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(
                ct.split(";")[0].strip(), "png"
            )
            filename = f"generated_{int(_time.time())}_{hash(prompt) & 0xFFFF:04x}.{ext}"
            file_id = FileStore.instance().store(
                filename, result["image_bytes"], content_type=ct,
                user_id=self._user_id
            )
            download_url = f"{self._base_url}/files/{file_id}/{filename}"
            return (
                f"Image generated: {download_url}\n"
                f"file_id: {file_id}\n"
                f"To save to filesystem: use filesystem(action=write_file, path=<target>, file_id={file_id}, service=<fs>)"
            )

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

        # Resolve service dynamically
        if not self._service_resolver:
            return "Error: no video service resolver configured"
        service, error = self._service_resolver()
        if not service:
            return f"Error: {error or 'no video generation service available'}"

        prompt = arguments.get("prompt", "")
        if not prompt:
            return "Error: no prompt provided"

        try:
            result = service.generate(**arguments)
            # result = {"video_bytes": bytes, "content_type": str}

            from core.file_store import FileStore
            ct = result["content_type"]
            ext = {
                "video/mp4": "mp4", "video/webm": "webm",
                "video/quicktime": "mov", "video/x-msvideo": "avi",
            }.get(ct.split(";")[0].strip(), "mp4")
            filename = f"generated_{int(_time.time())}_{hash(prompt) & 0xFFFF:04x}.{ext}"
            file_id = FileStore.instance().store(
                filename, result["video_bytes"], content_type=ct,
                user_id=self._user_id
            )
            download_url = f"{self._base_url}/files/{file_id}/{filename}"
            return (
                f"Video generated: {download_url}\n"
                f"file_id: {file_id}"
            )

        except Exception as e:
            return f"Error generating video: {e}"
