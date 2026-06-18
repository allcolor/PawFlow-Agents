"""Image media tool handlers (generate, edit, model info).

Extracted from media.py to keep files <=800 lines. Shared helpers live in
core.handlers._media_common; re-exported from core.handlers.media.
"""

import json
import logging
from typing import Any, Dict

from core.tool_handler import ToolHandler
from core.handlers._media_common import (
    _resolve_explicit_media_service,
    _write_media_result,
)

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
    _conversation_id: str = ""
    _agent_name: str = ""

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
                    agent_name=getattr(self, "_agent_name", "") or "",
                )
            if hasattr(service, "set_callback_base_url"):
                service.set_callback_base_url(self._base_url)
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
            write_result = _write_media_result(
                resolver, destination, filename, result,
                "image_bytes", "image_path", ct)

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
    _agent_name: str = ""

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

    def set_agent_name(self, agent_name: str):
        self._agent_name = agent_name

    def set_service_resolver(self, resolver):
        """Set a resolver: () -> (service, error_msg). Same shape as ImageGenerationHandler."""
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
        image_urls = arguments.get("image_urls") or []
        if isinstance(image_urls, str):
            image_urls = [image_urls]
        if not image_urls:
            return "Error: image_urls is required (at least one source URL)"
        destination = arguments.get("destination", "filestore")
        if not hasattr(service, "edit_image"):
            return ("Error: the active image service does not implement "
                    "edit_image. Switch to a model with an edit_image "
                    "operation (e.g. 'nano-banana').")
        # Share source images as public, gateway-key URLs for the duration
        # of this call (revoked in the finally below) for providers that
        # cannot read FileStore locally.
        from core.media_share import TemporaryPublicRefs
        share = TemporaryPublicRefs(self._base_url, self._user_id)
        image_urls = [share.public_url(u, service=service) for u in image_urls]

        try:
            if hasattr(service, "set_runtime_context"):
                service.set_runtime_context(
                    user_id=self._user_id,
                    conversation_id=getattr(self, "_conversation_id", "") or "",
                    agent_name=getattr(self, "_agent_name", "") or "",
                )
            if hasattr(service, "set_callback_base_url"):
                service.set_callback_base_url(self._base_url)
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
            write_result = _write_media_result(
                resolver, destination, filename, result,
                "image_bytes", "image_path", ct)

            if write_result.get("file_id"):
                url = f"fs://filestore/{write_result['file_id']}/{filename}"
                return f"Image edited: {url}\nfile_id: {write_result['file_id']}"
            return (f"Image edited and saved to "
                    f"{write_result.get('destination', destination)}: "
                    f"{write_result.get('path', filename)}")
        except Exception as e:
            return f"Error editing image: {e}"
        finally:
            # Revoke the temporary public access granted to source images.
            share.restore()



class ImageModelInfoHandler(ToolHandler):
    """Return info about the active image generation model and its parameters."""

    _service_resolver = None
    _user_id: str = ""
    _conversation_id: str = ""
    _agent_name: str = ""

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

    def set_agent_name(self, agent_name: str):
        self._agent_name = agent_name

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
                agent_name=getattr(self, "_agent_name", "") or "",
            )
        if hasattr(service, 'get_model_info'):
            info = service.get_model_info()
            return json.dumps(info, indent=2)
        return json.dumps({"model": "unknown", "model_params": {}})
