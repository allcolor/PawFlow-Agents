"""ComfyUI image generation and edit service."""

from __future__ import annotations

from core import ServiceFactory
from core.relay_proxy_url import CONV_RELAY_EXPR
from services._comfyui_client import ComfyUIClient
from services.base_image_generation import BaseImageGenerationService


class ComfyUIImageService(BaseImageGenerationService):
    """Run administrator-configured ComfyUI API workflows for images."""

    TYPE = "comfyUIImageGeneration"
    VERSION = "1.0.0"
    NAME = "ComfyUI Image Generation"
    DESCRIPTION = "Generate and edit images with trusted ComfyUI API workflows"
    CATEGORY = "image"
    ACCEPTS_FILESTORE_URLS = True

    def get_parameter_schema(self) -> dict:
        return {
            "base_url": {
                "type": "string", "required": False,
                "default": f"relay://{CONV_RELAY_EXPR}/localhost:8188",
                "description": (
                    "ComfyUI server URL. The default reaches a local ComfyUI "
                    "instance through the conversation relay."),
            },
            "allow_private_base_url": {
                "type": "boolean", "required": False, "default": False,
                "description": (
                    "Allow a direct private/loopback URL. Prefer relay:// for "
                    "a ComfyUI instance on a user's machine."),
            },
            "relay_local": {
                "type": "boolean", "required": False, "default": True,
                "description": (
                    "For relay:// URLs, connect from the relay host when true "
                    "or from the relay container when false."),
            },
            "api_key": {
                "type": "string", "required": False, "default": "",
                "sensitive": True,
                "description": "Optional reverse-proxy or hosted ComfyUI API key.",
            },
            "api_key_header": {
                "type": "string", "required": False,
                "default": "Authorization",
                "description": "Header used for api_key, for example Authorization or X-API-Key.",
            },
            "api_key_prefix": {
                "type": "string", "required": False, "default": "Bearer",
                "description": "Prefix placed before api_key. Leave empty for X-API-Key style authentication.",
            },
            "workflows": {
                "type": "json", "required": True,
                "description": (
                    "Trusted ComfyUI API-format workflow presets keyed by "
                    "generate and optionally edit_image. Each preset contains "
                    "workflow, bindings, and output."),
            },
            "timeout": {
                "type": "integer", "required": False, "default": 0,
                "description": (
                    "Maximum generation time in seconds (0 = unlimited)."),
            },
            "request_timeout": {
                "type": "integer", "required": False, "default": 60,
                "description": "Timeout for individual ComfyUI control requests.",
            },
            "poll_interval": {
                "type": "number", "required": False, "default": 1.0,
                "description": "Seconds between /history polls.",
            },
            "max_input_bytes": {
                "type": "integer", "required": False,
                "default": 104857600,
                "description": "Maximum size of each uploaded image input.",
            },
            "max_output_bytes": {
                "type": "integer", "required": False,
                "default": 4294967296,
                "description": "Maximum downloaded output size; downloads are streamed to disk.",
            },
        }

    def __init__(self, config):
        super().__init__(config)
        self.client = ComfyUIClient(self.config, media_kind="image")
        self.client.require_operation("generate")

    def set_runtime_context(self, user_id: str = "", conversation_id: str = "",
                            agent_name: str = "", relay_id: str = "",
                            relay_local=None,
                            **_: object):
        self.client.set_runtime_context(
            user_id=user_id, conversation_id=conversation_id,
            agent_name=agent_name, relay_id=relay_id,
            relay_local=relay_local)

    def _create_connection(self):
        return {"ready": True, "system_stats": self.client.ping()}

    def _close_connection(self):
        return None

    @staticmethod
    def _result(result: dict) -> dict:
        return {
            "image_path": result["path"],
            "content_type": result["content_type"],
            "_delete_media_path": True,
            "provider_prompt_id": result["prompt_id"],
        }

    def generate(self, prompt="", negative_prompt="", width=1024,
                 height=1024, output_format="png", aspect_ratio="",
                 style="", num_inference_steps=None, guidance_scale=None,
                 seed=None, model="", **kwargs) -> dict:
        if not prompt:
            raise ValueError("prompt is required for ComfyUI image generation")
        self.ensure_connected()
        values = {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "width": width,
            "height": height,
            "output_format": output_format,
            "aspect_ratio": aspect_ratio,
            "style": style,
            "num_inference_steps": num_inference_steps,
            "guidance_scale": guidance_scale,
            "seed": seed,
            "model": model,
            **kwargs,
        }
        return self._result(self.client.run("generate", values))

    def edit_image(self, prompt: str = "", image_urls=None,
                   negative_prompt: str = "", width=1024, height=1024,
                   output_format="png", seed=None, model="", **kwargs) -> dict:
        if not prompt:
            raise ValueError("prompt is required for ComfyUI image editing")
        if isinstance(image_urls, str):
            image_urls = [image_urls]
        image_urls = list(image_urls or [])
        if not image_urls:
            raise ValueError("image_urls is required for ComfyUI image editing")
        self.client.require_operation("edit_image")
        self.ensure_connected()
        uploaded = [
            self.client.upload_source(source, index=index)
            for index, source in enumerate(image_urls)
        ]
        values = {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "width": width,
            "height": height,
            "output_format": output_format,
            "seed": seed,
            "model": model,
            "image": uploaded[0],
            "images": uploaded,
            **kwargs,
        }
        return self._result(self.client.run("edit_image", values))

    def get_model_info(self) -> dict:
        return {
            "provider": "comfyui",
            "model": "workflow-configured",
            "available_operations": sorted(self.client.workflows),
            "supports_edit": self.client.has_operation("edit_image"),
        }

    def get_operations(self) -> dict:
        """Expose configured capabilities without exposing workflow bodies."""
        return {operation: {} for operation in sorted(self.client.workflows)}


ServiceFactory.register(ComfyUIImageService)
