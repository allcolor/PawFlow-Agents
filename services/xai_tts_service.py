"""xAI direct text-to-speech service."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from core import ServiceFactory, ServiceError, safe_float
from services.base_tts import BaseTTSService

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.x.ai/v1"
_CONTENT_TYPES = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "pcm": "audio/L16",
    "mulaw": "audio/basic",
    "alaw": "audio/basic",
}


class XAITTSService(BaseTTSService):
    TYPE = "xaiTTS"
    VERSION = "1.0.0"
    NAME = "xAI Text-to-Speech"
    CATEGORY = "audio"
    SUPPORTS_NATIVE_TTS_VOICES = True
    DESCRIPTION = "Generate speech through the direct xAI /v1/tts API."

    def get_parameter_schema(self) -> dict:
        return {
            "api_key": {"type": "string", "required": True, "sensitive": True,
                        "description": "xAI API key."},
            "voice": {"type": "string", "required": False, "default": "eve",
                      "description": "xAI voice id, e.g. eve or ara."},
            "language": {"type": "string", "required": False, "default": "auto",
                         "description": "BCP-47 language code or auto."},
            "codec": {"type": "select", "required": False, "default": "mp3",
                      "options": sorted(_CONTENT_TYPES),
                      "description": "Output codec."},
            "sample_rate": {"type": "integer", "required": False, "default": 24000,
                            "description": "Output sample rate."},
            "speed": {"type": "float", "required": False, "default": 1.0,
                      "description": "Speech speed multiplier."},
            "timeout": {"type": "integer", "required": False, "default": 120,
                        "description": "HTTP timeout in seconds."},
        }

    def __init__(self, config):
        super().__init__(config)
        self.api_key = str(self.config.get("api_key") or "")
        self.voice = str(self.config.get("voice") or "eve")
        self.language = str(self.config.get("language") or "auto")
        self.codec = self._codec(self.config.get("codec") or "mp3")
        self.sample_rate = int(self.config.get("sample_rate") or 24000)
        self.speed = safe_float(self.config.get("speed", 1.0), 1.0)
        self.timeout = int(self.config.get("timeout") or 120)

    def _create_connection(self):
        if not self.api_key:
            raise ServiceError("api_key is required for xAI TTS")
        return {"ready": True}

    def _close_connection(self):
        pass

    @staticmethod
    def _codec(value: str) -> str:
        codec = str(value or "mp3").strip().lower()
        if codec not in _CONTENT_TYPES:
            raise ServiceError(f"unsupported codec {codec!r}; expected one of {sorted(_CONTENT_TYPES)}")
        return codec

    def speak(self, text: str, voice: str = "", language: str = "", **kwargs) -> dict:
        if not text:
            raise ServiceError("text is required")
        self.ensure_connected()
        codec = self._codec(kwargs.pop("codec", None) or self.codec)
        body = {
            "text": text,
            "voice": voice or kwargs.pop("voice", None) or self.voice,
            "language": language or kwargs.pop("language", None) or self.language,
            "codec": codec,
            "sample_rate": int(kwargs.pop("sample_rate", None) or self.sample_rate),
        }
        speed = kwargs.pop("speed", None)
        body["speed"] = safe_float(speed if speed not in (None, "") else self.speed, 1.0)
        for key in ("bit_rate", "text_normalization", "optimize_streaming_latency"):
            value = kwargs.pop(key, None)
            if value not in (None, ""):
                body[key] = value
        req = urllib.request.Request(
            f"{_BASE_URL}/tts",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "audio/*, application/json",
                "User-Agent": "PawFlow-Agent/1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # nosec B310 - configured xAI API endpoint.
                raw = resp.read()
                content_type = resp.headers.get("Content-Type", "") or _CONTENT_TYPES[codec]
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:1000].decode("utf-8", errors="replace")
            raise ServiceError(f"xAI TTS error POST /tts ({exc.code}): {detail}") from exc
        if not raw:
            raise ServiceError("xAI TTS returned empty audio")
        logger.info("[XAI-TTS] speech ok: %d bytes (%s)", len(raw), content_type)
        return {"audio_bytes": raw, "content_type": content_type, "source_url": ""}


ServiceFactory.register(XAITTSService)
