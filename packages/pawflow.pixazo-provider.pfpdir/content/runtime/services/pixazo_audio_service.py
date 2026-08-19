"""Pixazo audio generation service — catalog-driven dispatcher.

Thin subclass of `_PixazoBaseService` with CATEGORY="audio". Models
are declared in `pixazo_catalog.json` under `category: "audio"` with
operations like `music_generation`, `text_to_speech`, etc.
"""

import logging
from typing import Any, Dict

from core import ServiceFactory, ServiceError
from services._pixazo_base import _PixazoBaseService
from services.base_audio_generation import BaseAudioGenerationService
from services.base_tts import BaseTTSService

logger = logging.getLogger(__name__)


class PixazoAudioService(_PixazoBaseService, BaseAudioGenerationService, BaseTTSService):
    TYPE = "pixazoAudioGeneration"
    VERSION = "3.0.0"
    NAME = "Pixazo Audio Generation"
    DESCRIPTION = (
        "Generate audio (music, speech) via the Pixazo gateway. Any "
        "model declared in pixazo_catalog.json under category=audio is "
        "supported (MiniMax, Ace Step, Lyria, ElevenLabs, …)."
    )
    CATEGORY = "audio"

    def generate(self, prompt: str = "", lyrics: str = "",
                 duration: int = 30, instrumental: bool = False,
                 style: str = "", model: str = "", **kwargs) -> dict:
        """Music generation. `model` overrides the service default."""
        if not prompt:
            raise ServiceError("No prompt provided")
        body: Dict[str, Any] = {"prompt": prompt}
        if lyrics:
            body["lyrics"] = lyrics
        if duration:
            body["duration"] = int(duration)
        if instrumental:
            body["instrumental"] = True
        if style:
            body["style"] = style
        for k, v in kwargs.items():
            if k not in body and k not in ("destination", "path", "service", "_service", "model"):
                body[k] = v
        op_name = self._pick_op(
            ["music_generation", "text_to_audio", "text_to_music"],
            model_id=model)
        r = self._invoke(op_name, body, model_id=model)
        return {"audio_bytes": r["bytes"],
                "content_type": r["content_type"],
                "source_url": r["source_url"]}

    def text_to_speech(self, text: str = "", voice: str = "",
                        model: str = "", **kwargs) -> dict:
        if not text:
            raise ServiceError("No text provided")
        op = self._op("text_to_speech", model_id=model)
        input_field = op.get("input_field", "text")
        body: Dict[str, Any] = {input_field: text}
        if voice:
            body["voice"] = voice
        for k, v in kwargs.items():
            if k not in body and k not in ("destination", "path", "service", "model"):
                body[k] = v
        r = self._invoke("text_to_speech", body, model_id=model)
        return {"audio_bytes": r["bytes"],
                "content_type": r["content_type"],
                "source_url": r["source_url"]}

    def speak(self, text: str, voice: str = "", language: str = "",
              model: str = "", **kwargs) -> dict:
        if language and "language" not in kwargs:
            kwargs["language"] = language
        return self.text_to_speech(
            text=text,
            voice=voice,
            model=model,
            **kwargs,
        )

    def _pick_op(self, candidates, *, model_id: str = "") -> str:
        """Return the first op in `candidates` declared by the active model."""
        ops = self._model(model_id).get("operations") or {}
        for c in candidates:
            if c in ops:
                return c
        raise ServiceError(
            f"Model '{model_id or self._model_id}' does not declare any of "
            f"{list(candidates)}. Supported: {sorted(ops.keys())}.")


ServiceFactory.register(PixazoAudioService)
