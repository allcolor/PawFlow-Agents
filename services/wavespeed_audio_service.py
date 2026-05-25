"""WaveSpeedAI audio generation service — catalog-driven dispatcher."""

import logging
from typing import Any, Dict

from core import ServiceFactory, ServiceError
from services._wavespeed_base import _WaveSpeedBaseService
from services.base_audio_generation import BaseAudioGenerationService
from services.base_tts import BaseTTSService


logger = logging.getLogger(__name__)


class WaveSpeedAudioService(_WaveSpeedBaseService, BaseAudioGenerationService, BaseTTSService):
    TYPE = "wavespeedAudioGeneration"
    VERSION = "1.0.0"
    NAME = "WaveSpeedAI Audio Generation"
    DESCRIPTION = (
        "Generate music, speech, and audio via WaveSpeedAI. Supports any "
        "audio model declared in wavespeed_catalog.json."
    )
    CATEGORY = "audio"

    def _audio_result(self, op_name: str, body: Dict[str, Any],
                      model: str) -> dict:
        r = self._invoke(op_name, body, model_id=model)
        return {"audio_bytes": r["bytes"],
                "content_type": r["content_type"],
                "source_url": r["source_url"]}

    def generate(self, prompt: str = "", lyrics: str = "",
                 duration: int = 30, instrumental: bool = False,
                 style: str = "", model: str = "", **kwargs) -> dict:
        if not prompt:
            raise ServiceError("No prompt provided")
        op_name = self._pick_op(
            ["music_generation", "text_to_audio", "audio_edit"],
            model_id=model)
        op = self._op(op_name, model_id=model)
        body: Dict[str, Any] = {"prompt": prompt}
        self._add_supported(body, op, "lyrics", lyrics)
        self._add_supported(body, op, "duration", int(duration) if duration else None)
        self._add_supported(body, op, "instrumental", True if instrumental else None)
        self._add_supported(body, op, "style", style)
        self._add_supported(body, op, "enable_sync_mode", False)
        self._add_kwargs(body, kwargs)
        return self._audio_result(op_name, body, model)

    def text_to_speech(self, text: str = "", voice: str = "",
                        model: str = "", language: str = "auto",
                        **kwargs) -> dict:
        if not text:
            raise ServiceError("No text provided")
        op = self._op("text_to_speech", model_id=model)
        input_field = op.get("input_field", "text")
        body: Dict[str, Any] = {input_field: text}
        self._add_supported(body, op, "voice", voice or kwargs.get("voice"))
        self._add_supported(body, op, "language", language)
        self._add_supported(body, op, "style_instruction",
                            kwargs.get("style_instruction"))
        self._add_supported(body, op, "enable_sync_mode", False)
        self._add_kwargs(body, kwargs)
        return self._audio_result("text_to_speech", body, model)

    def speak(self, text: str, voice: str = "", language: str = "",
              model: str = "", **kwargs) -> dict:
        return self.text_to_speech(
            text=text,
            voice=voice,
            model=model,
            language=language or kwargs.pop("lang", "auto"),
            **kwargs,
        )


ServiceFactory.register(WaveSpeedAudioService)
