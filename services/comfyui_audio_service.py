"""ComfyUI audio generation service."""

from __future__ import annotations

from core import ServiceFactory
from core.relay_proxy_url import CONV_RELAY_EXPR
from services._comfyui_client import ComfyUIClient
from services.base_audio_generation import BaseAudioGenerationService


class ComfyUIAudioService(BaseAudioGenerationService):
    """Run administrator-configured ComfyUI API workflows for audio."""

    TYPE = "comfyUIAudioGeneration"
    VERSION = "1.0.0"
    NAME = "ComfyUI Audio Generation"
    DESCRIPTION = "Generate audio with trusted ComfyUI API workflow revisions"
    CATEGORY = "audio"

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
                    "Trusted, immutable API-format audio preset revisions keyed "
                    "by generate_audio."),
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
                "description": "Maximum size of each uploaded audio input.",
            },
            "max_output_bytes": {
                "type": "integer", "required": False,
                "default": 4294967296,
                "description": "Maximum audio output size; downloads are streamed to disk.",
            },
        }

    def __init__(self, config):
        super().__init__(config)
        self.client = ComfyUIClient(self.config, media_kind="audio")
        self.client.require_operation("generate_audio")

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

    def generate(self, prompt="", negative_prompt="", duration=None,
                 seed=None, model="", **kwargs) -> dict:
        if not prompt:
            raise ValueError("prompt is required for ComfyUI audio generation")
        self.ensure_connected()
        source_audio_url = str(kwargs.pop("source_audio_url", "") or "")
        music_bed_url = str(kwargs.pop("music_bed_url", "") or "")
        values = {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "duration": duration,
            "seed": seed,
            "model": model,
            **kwargs,
        }
        if source_audio_url:
            values["source_audio"] = self.client.upload_source(
                source_audio_url, index=0)
        if music_bed_url:
            values["music_bed"] = self.client.upload_source(
                music_bed_url, index=1)
        result = self.client.run("generate_audio", values)
        return {
            "audio_path": result["path"],
            "content_type": result["content_type"],
            "_delete_media_path": True,
            "provider_prompt_id": result["prompt_id"],
        }

    def get_operations(self) -> dict:
        """Expose configured capabilities without exposing workflow bodies."""
        return {operation: {} for operation in sorted(self.client.workflows)}


ServiceFactory.register(ComfyUIAudioService)
