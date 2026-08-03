"""ComfyUI video generation service."""

from __future__ import annotations

from core import ServiceFactory
from core.relay_proxy_url import CONV_RELAY_EXPR
from services._comfyui_client import ComfyUIClient
from services.base_video_generation import BaseVideoGenerationService


class ComfyUIVideoService(BaseVideoGenerationService):
    """Run administrator-configured ComfyUI API workflows for videos."""

    TYPE = "comfyUIVideoGeneration"
    VERSION = "1.0.0"
    NAME = "ComfyUI Video Generation"
    DESCRIPTION = "Generate and transform videos with trusted ComfyUI API workflows"
    CATEGORY = "video"
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
                "description": "Allow a trusted direct private/loopback URL instead of relay://.",
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
                "description": "Header used for api_key.",
            },
            "api_key_prefix": {
                "type": "string", "required": False, "default": "Bearer",
                "description": "Prefix before api_key; leave empty for X-API-Key.",
            },
            "workflows": {
                "type": "json", "required": True,
                "description": (
                    "Trusted API-format presets keyed by generate and optional "
                    "image_to_video, frame_to_video, reference_to_video, "
                    "video_edit, or video_extend."),
            },
            "timeout": {
                "type": "integer", "required": False, "default": 3600,
                "description": "Maximum generation time in seconds.",
            },
            "request_timeout": {
                "type": "integer", "required": False, "default": 60,
                "description": "Timeout for individual ComfyUI control requests.",
            },
            "poll_interval": {
                "type": "number", "required": False, "default": 2.0,
                "description": "Seconds between /history polls.",
            },
            "max_input_bytes": {
                "type": "integer", "required": False,
                "default": 536870912,
                "description": "Maximum size of each uploaded image or video input.",
            },
            "max_output_bytes": {
                "type": "integer", "required": False,
                "default": 8589934592,
                "description": "Maximum video output size; downloads are streamed to disk.",
            },
        }

    def __init__(self, config):
        super().__init__(config)
        self.client = ComfyUIClient(self.config, media_kind="video")
        self.client.require_operation("generate")

    def set_runtime_context(self, user_id: str = "", conversation_id: str = "",
                            agent_name: str = "", **_: object):
        self.client.set_runtime_context(
            user_id=user_id, conversation_id=conversation_id,
            agent_name=agent_name)

    def _create_connection(self):
        return {"ready": True, "system_stats": self.client.ping()}

    def _close_connection(self):
        return None

    @staticmethod
    def _result(result: dict) -> dict:
        return {
            "video_path": result["path"],
            "content_type": result["content_type"],
            "_delete_media_path": True,
            "provider_prompt_id": result["prompt_id"],
        }

    @staticmethod
    def _values(prompt, negative_prompt, duration, width, height,
                seed, model, kwargs) -> dict:
        return {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "duration": duration,
            "width": width,
            "height": height,
            "seed": seed,
            "model": model,
            **kwargs,
        }

    def _run(self, operation: str, values: dict) -> dict:
        self.client.require_operation(operation)
        self.ensure_connected()
        return self._result(self.client.run(operation, values))

    def get_operations(self) -> dict:
        """Expose configured capabilities without exposing workflow bodies."""
        return {operation: {} for operation in sorted(self.client.workflows)}

    def generate(self, prompt="", negative_prompt="", duration=5,
                 width=None, height=None, seed=None, model="", **kwargs) -> dict:
        if not prompt:
            raise ValueError("prompt is required for ComfyUI video generation")
        return self._run("generate", self._values(
            prompt, negative_prompt, duration, width, height,
            seed, model, kwargs))

    def image_to_video(self, prompt="", image_url="", negative_prompt="",
                       duration=5, width=None, height=None, seed=None,
                       model="", **kwargs) -> dict:
        if not prompt:
            raise ValueError("prompt is required for ComfyUI image-to-video")
        if not image_url:
            raise ValueError("image_url is required for ComfyUI image-to-video")
        self.client.require_operation("image_to_video")
        self.ensure_connected()
        values = self._values(
            prompt, negative_prompt, duration, width, height,
            seed, model, kwargs)
        values["image"] = self.client.upload_source(image_url)
        return self._result(self.client.run("image_to_video", values))

    def frame_to_video(self, prompt="", image_url="", end_image_url="",
                       negative_prompt="", duration=5, width=None, height=None,
                       seed=None, model="", **kwargs) -> dict:
        if not prompt:
            raise ValueError("prompt is required for ComfyUI frame-to-video")
        if not image_url or not end_image_url:
            raise ValueError(
                "image_url and end_image_url are required for ComfyUI frame-to-video")
        self.client.require_operation("frame_to_video")
        self.ensure_connected()
        values = self._values(
            prompt, negative_prompt, duration, width, height,
            seed, model, kwargs)
        values["image"] = self.client.upload_source(image_url, index=0)
        values["end_image"] = self.client.upload_source(end_image_url, index=1)
        return self._result(self.client.run("frame_to_video", values))

    def reference_to_video(self, prompt="", reference_image_urls=None,
                           image_url="", negative_prompt="", duration=5,
                           width=None, height=None, seed=None, model="",
                           **kwargs) -> dict:
        if not prompt:
            raise ValueError("prompt is required for ComfyUI reference-to-video")
        sources = list(reference_image_urls or [])
        if image_url:
            sources.insert(0, image_url)
        if not sources:
            raise ValueError(
                "reference_image_urls is required for ComfyUI reference-to-video")
        self.client.require_operation("reference_to_video")
        self.ensure_connected()
        uploaded = [
            self.client.upload_source(source, index=index)
            for index, source in enumerate(sources)
        ]
        values = self._values(
            prompt, negative_prompt, duration, width, height,
            seed, model, kwargs)
        values.update({"image": uploaded[0], "images": uploaded})
        return self._result(self.client.run("reference_to_video", values))

    def video_edit(self, prompt="", video_url="", negative_prompt="",
                   duration=5, width=None, height=None, seed=None, model="",
                   **kwargs) -> dict:
        if not prompt:
            raise ValueError("prompt is required for ComfyUI video editing")
        if not video_url:
            raise ValueError("video_url is required for ComfyUI video editing")
        self.client.require_operation("video_edit")
        self.ensure_connected()
        values = self._values(
            prompt, negative_prompt, duration, width, height,
            seed, model, kwargs)
        values["video"] = self.client.upload_source(video_url)
        return self._result(self.client.run("video_edit", values))

    def video_extend(self, prompt="", video_url="", negative_prompt="",
                     duration=5, width=None, height=None, seed=None, model="",
                     **kwargs) -> dict:
        if not prompt:
            raise ValueError("prompt is required for ComfyUI video extension")
        if not video_url:
            raise ValueError("video_url is required for ComfyUI video extension")
        self.client.require_operation("video_extend")
        self.ensure_connected()
        values = self._values(
            prompt, negative_prompt, duration, width, height,
            seed, model, kwargs)
        values["video"] = self.client.upload_source(video_url)
        return self._result(self.client.run("video_extend", values))


ServiceFactory.register(ComfyUIVideoService)
