"""WaveSpeedAI voice-cloning TTS service."""

import logging
from typing import Any, Dict

from core import ServiceFactory, ServiceError
from services._wavespeed_base import _WaveSpeedBaseService
from services.base_voice_clone import BaseVoiceCloneService


logger = logging.getLogger(__name__)


class WaveSpeedVoiceCloneService(_WaveSpeedBaseService, BaseVoiceCloneService):
    """Zero-shot voice clone through WaveSpeedAI prediction models.

    WaveSpeed's documented voice clone models accept a reference audio
    URL with the synthesis text and return generated audio in
    ``data.outputs[]``. No provider-side persistent voice_id is required,
    so PawFlow's existing rendered-audio cache handles reuse.
    """

    TYPE = "wavespeedVoiceClone"
    VERSION = "1.0.0"
    NAME = "WaveSpeedAI Voice Clone"
    DESCRIPTION = (
        "Zero-shot voice cloning via WaveSpeedAI models such as Qwen3 TTS, "
        "OmniVoice, and MiniMax voice clone."
    )
    CATEGORY = "voice_clone"
    REQUIRES_REFERENCE_AUDIO_URL = True

    def clone_speak(self, text: str = "", reference_audio_url: str = "",
                    reference_text: str = "", language: str = "auto",
                    model: str = "", **kwargs) -> dict:
        if not text:
            raise ServiceError("clone_speak requires `text`")
        if not reference_audio_url:
            raise ServiceError("clone_speak requires `reference_audio_url`")
        op_name = self._pick_op(["voice_clone", "text_to_speech"],
                                model_id=model)
        op = self._op(op_name, model_id=model)
        ref_field = op.get("reference_audio_field") or (
            "audio" if self._accepts(op, "audio") else "reference_audio")
        body: Dict[str, Any] = {"text": text, ref_field: reference_audio_url}
        self._add_supported(body, op, "reference_text", reference_text)
        self._add_supported(body, op, "prompt", text)
        self._add_supported(body, op, "language", language)
        self._add_supported(body, op, "voice", kwargs.get("voice"))
        self._add_supported(body, op, "style_instruction",
                            kwargs.get("style_instruction"))
        self._add_supported(body, op, "enable_sync_mode", False)
        self._add_kwargs(body, kwargs)
        r = self._invoke(op_name, body, model_id=model)
        return {"audio_bytes": r["bytes"],
                "content_type": r["content_type"],
                "source_url": r["source_url"]}


ServiceFactory.register(WaveSpeedVoiceCloneService)
