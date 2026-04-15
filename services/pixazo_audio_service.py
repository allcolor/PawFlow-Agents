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

logger = logging.getLogger(__name__)


class PixazoAudioService(_PixazoBaseService, BaseAudioGenerationService):
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
                 style: str = "", **kwargs) -> dict:
        """Music generation — calls operation 'music_generation' by default.

        If the active model declares `text_to_speech` instead, callers
        should use `text_to_speech()` which targets that op explicitly.
        """
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
            if k not in body and k not in ("destination", "path", "service", "_service"):
                body[k] = v
        # Pick the first matching audio op the model supports so the
        # caller doesn't need to care whether it's "music_generation"
        # or "text_to_audio".
        op_name = self._pick_op(
            "music_generation", "text_to_audio", "text_to_music")
        r = self._invoke(op_name, body)
        return {"audio_bytes": r["bytes"],
                "content_type": r["content_type"],
                "source_url": r["source_url"]}

    def text_to_speech(self, text: str = "", voice: str = "",
                        **kwargs) -> dict:
        """Text-to-speech — calls operation 'text_to_speech'."""
        if not text:
            raise ServiceError("No text provided")
        op = self._op("text_to_speech")
        input_field = op.get("input_field", "text")
        body: Dict[str, Any] = {input_field: text}
        if voice:
            body["voice"] = voice
        for k, v in kwargs.items():
            if k not in body and k not in ("destination", "path", "service"):
                body[k] = v
        r = self._invoke("text_to_speech", body)
        return {"audio_bytes": r["bytes"],
                "content_type": r["content_type"],
                "source_url": r["source_url"]}

    def _pick_op(self, *candidates: str) -> str:
        """Return the first op in `candidates` declared by the active model."""
        ops = self._model().get("operations") or {}
        for c in candidates:
            if c in ops:
                return c
        raise ServiceError(
            f"Model '{self._model_id}' does not declare any of "
            f"{candidates}. Supported: {sorted(ops.keys())}.")


ServiceFactory.register(PixazoAudioService)
