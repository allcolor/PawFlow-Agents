"""xAI direct speech-to-text service."""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import urllib.error
import urllib.request

from core import ServiceFactory, ServiceError
from services.base_stt import BaseSTTService

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.x.ai/v1"


class XAISTTService(BaseSTTService):
    TYPE = "xaiSTT"
    VERSION = "1.0.0"
    NAME = "xAI Speech-to-Text"
    CATEGORY = "audio"
    DESCRIPTION = "Transcribe audio through the direct xAI /v1/stt API."

    def get_parameter_schema(self) -> dict:
        return {
            "api_key": {"type": "string", "required": True, "sensitive": True,
                        "description": "xAI API key."},
            "model": {"type": "string", "required": False, "default": "grok-transcribe",
                      "description": "xAI transcription model."},
            "language": {"type": "string", "required": False, "default": "",
                         "description": "Optional language hint."},
            "timeout": {"type": "integer", "required": False, "default": 120,
                        "description": "HTTP timeout in seconds."},
        }

    def __init__(self, config):
        super().__init__(config)
        self.api_key = str(self.config.get("api_key") or "")
        self.model = str(self.config.get("model") or "grok-transcribe")
        self.language = str(self.config.get("language") or "")
        self.timeout = int(self.config.get("timeout") or 120)

    def _create_connection(self):
        if not self.api_key:
            raise ServiceError("api_key is required for xAI STT")
        return {"ready": True}

    def _close_connection(self):
        pass

    def transcribe(self, audio_bytes: bytes = b"", audio_path: str = "",
                   mime_type: str = "", language: str = "",
                   prompt: str = "", model: str = "", **kwargs) -> dict:
        self.ensure_connected()
        if not audio_bytes and audio_path:
            with open(audio_path, "rb") as fh:
                audio_bytes = fh.read()
        if not audio_bytes:
            raise ServiceError("audio_bytes or audio_path is required")
        filename = kwargs.pop("filename", None) or "speech.webm"
        content_type = mime_type or mimetypes.guess_type(filename)[0] or "audio/webm"
        body = {
            "file": f"data:{content_type};base64,{base64.b64encode(audio_bytes).decode('ascii')}",
            "model": model or self.model,
        }
        if language or self.language:
            body["language"] = language or self.language
        if prompt:
            body["prompt"] = prompt
        for key in ("diarize", "filler_words", "multichannel", "channels", "keyterm"):
            value = kwargs.pop(key, None)
            if value not in (None, ""):
                body[key] = value
        req = urllib.request.Request(
            f"{_BASE_URL}/stt",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "PawFlow-Agent/1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # nosec B310 - configured xAI API endpoint.
                result = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:1000].decode("utf-8", errors="replace")
            raise ServiceError(f"xAI STT error POST /stt ({exc.code}): {detail}") from exc
        transcript = result.get("text") or result.get("transcript") or ""
        logger.info("[XAI-STT] transcription ok: %d chars", len(str(transcript)))
        return {
            "text": str(transcript).strip(),
            "language": result.get("language", language or self.language),
            "duration": result.get("duration", 0),
            "segments": result.get("segments") or result.get("words") or [],
            "provider_result": result,
        }


ServiceFactory.register(XAISTTService)
