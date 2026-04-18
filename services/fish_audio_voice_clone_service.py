"""Fish Audio voice-cloning TTS service.

Fish Audio offers **zero-shot** voice cloning: the reference audio is
sent at every synthesis call (no persistent `voice_id`). 10-30 s of
clean speech is the recommended sample length. The optional
`reference_text` (transcription of the sample) noticeably improves
quality.

API: https://docs.fish.audio/text-to-speech/text-to-speech

Auth: `Authorization: Bearer <api_key>`

Endpoint used here: ``POST /v1/tts`` with a JSON body
    {
      "text":      "<text to speak>",
      "references":[{"audio": "<base64>", "text": "<reference_text>"}],
      "latency":   "normal",
      "format":    "mp3"
    }
The response streams the audio bytes back. We buffer everything and
return it as a single `{"audio_bytes": ..., "content_type": ...}` dict.
"""

import base64
import http.client
import json
import logging
import ssl
import urllib.request
from typing import Any, Dict

from core import ServiceFactory, ServiceError
from services.base_voice_clone import BaseVoiceCloneService

logger = logging.getLogger(__name__)

_API_HOST = "api.fish.audio"
_API_PATH = "/v1/tts"

# Supported output formats on Fish Audio.
_FORMATS = ("mp3", "wav", "pcm", "opus")
_CT = {
    "mp3":  "audio/mpeg",
    "wav":  "audio/wav",
    "pcm":  "audio/L16",
    "opus": "audio/ogg",
}


class FishAudioVoiceCloneService(BaseVoiceCloneService):
    TYPE = "fishAudioVoiceClone"
    VERSION = "1.0.0"
    NAME = "Fish Audio Voice Clone"
    DESCRIPTION = (
        "Zero-shot voice cloning via Fish Audio. Sends the reference "
        "audio (10-30 s recommended) with every synthesis call; no "
        "persistent voice_id is created provider-side. Works well with "
        "most languages; supply `reference_text` (the sample's "
        "transcription) for best quality."
    )

    def get_parameter_schema(self) -> dict:
        return {
            "api_key": {
                "type": "string", "required": True, "sensitive": True,
                "description": "Fish Audio API key (from fish.audio dashboard).",
            },
            "model": {
                "type": "string", "required": False,
                "default": "speech-1.6",
                "description": "Fish Audio TTS model id (e.g. speech-1.6, s1).",
            },
            "format": {
                "type": "select", "required": False,
                "default": "mp3",
                "options": list(_FORMATS),
                "description": "Output audio format.",
            },
            "latency": {
                "type": "select", "required": False,
                "default": "normal",
                "options": ["normal", "balanced"],
                "description": "Latency profile — 'normal' is higher quality.",
            },
            "timeout": {
                "type": "integer", "required": False, "default": 120,
                "description": "HTTP timeout in seconds.",
            },
        }

    def __init__(self, config):
        super().__init__(config)
        self.api_key = self.config.get("api_key", "")
        self.model = self.config.get("model", "speech-1.6")
        self.format = (self.config.get("format") or "mp3").lower()
        if self.format not in _FORMATS:
            self.format = "mp3"
        self.latency = self.config.get("latency", "normal")
        self.timeout = int(self.config.get("timeout", 120))

    # ── lifecycle ────────────────────────────────────────────────

    def _create_connection(self):
        if not self.api_key:
            raise ServiceError("api_key is required for Fish Audio service")
        return {"ready": True}

    def _close_connection(self):
        pass

    # ── helpers ──────────────────────────────────────────────────

    def _fetch_reference(self, url: str) -> bytes:
        """Download the reference audio bytes from an HTTP(S) URL.

        fs://filestore/<id>/<name> URLs must be rewritten to an
        absolute HTTP URL by the caller (the handler does this via
        `_resolve_filestore_url`) before reaching this method.
        """
        if not url:
            raise ServiceError("reference_audio_url is required")
        if not (url.startswith("http://") or url.startswith("https://")):
            raise ServiceError(
                f"reference_audio_url must be an absolute URL, got {url!r}")
        req = urllib.request.Request(
            url, headers={"User-Agent": "PawFlow-Agent/1.0"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return resp.read()

    # ── API ──────────────────────────────────────────────────────

    def clone_speak(self,
                    text: str = "",
                    reference_audio_url: str = "",
                    reference_text: str = "",
                    language: str = "",
                    reference_audio_bytes: bytes = None,
                    **kwargs) -> dict:
        """Synthesize `text` in the voice of the reference sample.

        `reference_audio_bytes` is an optimisation used by the handler
        so we don't re-download the reference on every call when the
        bytes are already in hand.
        """
        if not text:
            raise ServiceError("clone_speak requires `text`")
        if not reference_audio_url and not reference_audio_bytes:
            raise ServiceError(
                "clone_speak requires `reference_audio_url` or "
                "`reference_audio_bytes`")
        self.ensure_connected()

        if reference_audio_bytes is None:
            reference_audio_bytes = self._fetch_reference(reference_audio_url)

        ref_b64 = base64.b64encode(reference_audio_bytes).decode("ascii")
        references = [{"audio": ref_b64, "text": reference_text or ""}]

        body: Dict[str, Any] = {
            "text": text,
            "references": references,
            "latency": self.latency,
            "format": self.format,
        }
        if self.model:
            body["model"] = self.model
        if language:
            body["language"] = language
        for k, v in kwargs.items():
            if k in body or k in ("destination", "path", "_service",
                                   "service", "model"):
                continue
            body[k] = v

        ctx = ssl.create_default_context()
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        json_body = json.dumps(body).encode("utf-8")
        headers["Content-Length"] = str(len(json_body))

        conn = http.client.HTTPSConnection(
            _API_HOST, timeout=self.timeout, context=ctx)
        try:
            conn.request("POST", _API_PATH, body=json_body, headers=headers)
            resp = conn.getresponse()
            content_type = resp.headers.get(
                "Content-Type", _CT.get(self.format, "audio/mpeg"))
            audio_bytes = resp.read()
            if resp.status >= 400:
                preview = audio_bytes[:300].decode("utf-8", errors="replace")
                raise ServiceError(
                    f"Fish Audio API error ({resp.status}): {preview}")
        finally:
            conn.close()

        if not audio_bytes:
            raise ServiceError("Fish Audio returned empty audio")

        logger.info("[FISH] clone_speak ok: %d bytes (%s)",
                    len(audio_bytes), content_type)
        return {
            "audio_bytes": audio_bytes,
            "content_type": content_type,
            "source_url": "",
        }


ServiceFactory.register(FishAudioVoiceCloneService)
