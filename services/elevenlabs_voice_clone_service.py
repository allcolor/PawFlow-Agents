"""ElevenLabs voice-cloning TTS service (paradigm B: persistent voice_id).

ElevenLabs maintains a library of named voices server-side. Cloning a
voice is a two-step affair:

  1. `ensure_voice_id(ref_bytes, name)` → POST multipart
     `/v1/voices/add` with one or more audio samples. The response
     contains a stable `voice_id` that is stored once and reused for
     every subsequent synthesis call (no re-upload).

  2. `clone_speak(text, voice_id=…)` → POST
     `/v1/text-to-speech/{voice_id}` with the text + model config. The
     response streams MP3 bytes (or the format requested via
     `output_format`).

Delete semantics:
  DELETE `/v1/voices/{voice_id}` when the user removes the clone — the
  cascade-delete path in `voice_clone_cache.cascade_delete` invokes
  `service.delete_voice_id` for exactly this reason (freeing quota
  upstream, see the Creator/Starter plan limits of 10-30 voices).

API reference: https://elevenlabs.io/docs/api-reference/voices
Auth header:   `xi-api-key: <api_key>`.
"""

import http.client
import json
import logging
import ssl
import urllib.parse
import uuid
from typing import Any, Dict, Optional

from core import ServiceFactory, ServiceError
from services.base_voice_clone import BaseVoiceCloneService

logger = logging.getLogger(__name__)

_API_HOST = "api.elevenlabs.io"
_PATH_ADD_VOICE = "/v1/voices/add"
_PATH_DELETE_VOICE = "/v1/voices/{voice_id}"
_PATH_TTS = "/v1/text-to-speech/{voice_id}"

_DEFAULT_MODEL = "eleven_multilingual_v2"
_DEFAULT_FORMAT = "mp3_44100_128"  # ElevenLabs output_format id

_CT_FOR_FORMAT = {
    "mp3_44100_128": "audio/mpeg",
    "mp3_44100_192": "audio/mpeg",
    "pcm_16000":     "audio/L16",
    "pcm_22050":     "audio/L16",
    "pcm_24000":     "audio/L16",
    "pcm_44100":     "audio/L16",
    "ulaw_8000":     "audio/basic",
}


def _build_multipart(fields: Dict[str, str],
                      files: Dict[str, Dict[str, Any]]) -> tuple:
    """Encode a multipart/form-data body. Returns (content_type, body).

    `fields`  : str→str form fields.
    `files`   : name → {filename, content_type, data: bytes}.
    """
    boundary = "----pawflow" + uuid.uuid4().hex
    crlf = b"\r\n"
    buf = bytearray()
    for k, v in fields.items():
        buf += (("--" + boundary).encode() + crlf)
        buf += (f'Content-Disposition: form-data; name="{k}"'
                .encode() + crlf + crlf)
        buf += (v.encode("utf-8") + crlf)
    for k, spec in files.items():
        buf += (("--" + boundary).encode() + crlf)
        buf += (f'Content-Disposition: form-data; name="{k}"; '
                f'filename="{spec.get("filename", "sample")}"'
                .encode() + crlf)
        buf += (f'Content-Type: {spec.get("content_type", "application/octet-stream")}'
                .encode() + crlf + crlf)
        buf += spec["data"]
        buf += crlf
    buf += (("--" + boundary + "--").encode() + crlf)
    return ("multipart/form-data; boundary=" + boundary, bytes(buf))


class ElevenLabsVoiceCloneService(BaseVoiceCloneService):
    TYPE = "elevenLabsVoiceClone"
    VERSION = "1.0.0"
    NAME = "ElevenLabs Voice Clone"
    DESCRIPTION = (
        "Persistent voice cloning via ElevenLabs. First call uploads the "
        "reference sample once; the returned `voice_id` is cached and "
        "reused for every synthesis. Supports 29 languages with the "
        "`eleven_multilingual_v2` model. Quota-bounded by your plan "
        "(Starter: 10 voices, Creator: 30, Pro: 160)."
    )

    def get_parameter_schema(self) -> dict:
        return {
            "api_key": {
                "type": "string", "required": True, "sensitive": True,
                "description": "ElevenLabs API key (xi-api-key).",
            },
            "model_id": {
                "type": "string", "required": False,
                "default": _DEFAULT_MODEL,
                "description": "TTS model id (eleven_multilingual_v2, "
                               "eleven_turbo_v2_5, eleven_flash_v2_5, …).",
            },
            "output_format": {
                "type": "select", "required": False,
                "default": _DEFAULT_FORMAT,
                "options": list(_CT_FOR_FORMAT.keys()),
                "description": "Output codec+sample rate (e.g. mp3_44100_128).",
            },
            "stability": {
                "type": "number", "required": False, "default": 0.5,
                "description": "Voice stability (0.0-1.0). Higher = steadier.",
            },
            "similarity_boost": {
                "type": "number", "required": False, "default": 0.75,
                "description": "Similarity to reference (0.0-1.0).",
            },
            "timeout": {
                "type": "integer", "required": False, "default": 120,
                "description": "HTTP timeout in seconds.",
            },
        }

    def __init__(self, config):
        super().__init__(config)
        self.api_key = self.config.get("api_key", "")
        self.model_id = self.config.get("model_id") or _DEFAULT_MODEL
        self.output_format = self.config.get("output_format") or _DEFAULT_FORMAT
        if self.output_format not in _CT_FOR_FORMAT:
            self.output_format = _DEFAULT_FORMAT
        self.stability = float(self.config.get("stability", 0.5))
        self.similarity_boost = float(self.config.get("similarity_boost", 0.75))
        self.timeout = int(self.config.get("timeout", 120))

    # ── lifecycle ────────────────────────────────

    def _create_connection(self):
        if not self.api_key:
            raise ServiceError("api_key is required for ElevenLabs service")
        return {"ready": True}

    def _close_connection(self):
        pass

    # ── HTTP helper ───────────────────────────────

    def _request(self, method: str, path: str,
                 body: Optional[bytes] = None,
                 content_type: str = "",
                 accept: str = "application/json") -> tuple:
        self.ensure_connected()
        ctx = ssl.create_default_context()
        conn = http.client.HTTPSConnection(
            _API_HOST, timeout=self.timeout, context=ctx)
        try:
            headers = {
                "xi-api-key": self.api_key,
                "Accept": accept,
            }
            if content_type:
                headers["Content-Type"] = content_type
            if body is not None:
                headers["Content-Length"] = str(len(body))
            conn.request(method, path, body=body, headers=headers)
            resp = conn.getresponse()
            resp_ct = resp.headers.get("Content-Type", "")
            data = resp.read()
            if resp.status >= 400:
                preview = data[:400].decode("utf-8", errors="replace")
                raise ServiceError(
                    f"ElevenLabs API error {method} {path} "
                    f"({resp.status}): {preview}")
            return (data, resp_ct)
        finally:
            conn.close()

    # ── API ────────────────────────────────────────

    def ensure_voice_id(self, reference_audio_url: str = "",
                        reference_text: str = "",
                        name: str = "",
                        reference_audio_bytes: Optional[bytes] = None,
                        **kwargs) -> str:
        """Create a voice on ElevenLabs and return the stable voice_id.

        The caller passes `reference_audio_bytes` — the handler fetched
        them to compute the cache key already. `name` becomes the voice
        name on the provider; falls back to a uuid-based label.
        """
        if not reference_audio_bytes:
            raise ServiceError(
                "ensure_voice_id requires reference_audio_bytes")
        voice_name = (name or f"pawflow-{uuid.uuid4().hex[:8]}")[:80]
        ct, body = _build_multipart(
            fields={"name": voice_name,
                    "description": (reference_text or "")[:500],
                    "labels": "{}"},
            files={"files": {
                "filename": "reference.mp3",
                "content_type": "audio/mpeg",
                "data": reference_audio_bytes,
            }},
        )
        data, _ = self._request(
            "POST", _PATH_ADD_VOICE, body=body, content_type=ct)
        try:
            payload = json.loads(data)
        except Exception as e:
            raise ServiceError(
                f"ElevenLabs /voices/add returned non-JSON: {e}") from e
        vid = payload.get("voice_id") or payload.get("id") or ""
        if not vid:
            raise ServiceError(
                f"ElevenLabs /voices/add returned no voice_id: {payload}")
        logger.info("[EL] voice_id created: name=%s id=%s", voice_name, vid)
        return vid

    def delete_voice_id(self, voice_id: str) -> bool:
        """Delete a voice on ElevenLabs, freeing the quota slot."""
        if not voice_id:
            return True
        try:
            self._request("DELETE",
                          _PATH_DELETE_VOICE.format(
                              voice_id=urllib.parse.quote(voice_id)))
            logger.info("[EL] voice_id deleted: %s", voice_id)
            return True
        except ServiceError as e:
            logger.warning("[EL] delete_voice_id(%s) failed: %s",
                            voice_id, e)
            return False

    def clone_speak(self,
                    text: str = "",
                    reference_audio_url: str = "",
                    reference_text: str = "",
                    language: str = "",
                    reference_audio_bytes: Optional[bytes] = None,
                    voice_id: str = "",
                    **kwargs) -> dict:
        """Synthesize `text` using an ElevenLabs voice_id.

        ElevenLabs requires a persistent voice_id — the handler passes
        the cached one via `voice_id=`. If absent we raise: the caller
        forgot to run `ensure_voice_id` first.
        """
        if not text:
            raise ServiceError("clone_speak requires `text`")
        if not voice_id:
            raise ServiceError(
                "ElevenLabs clone_speak requires `voice_id` — register "
                "the voice via `clone_voice` first (ensure_voice_id).")

        body_dict: Dict[str, Any] = {
            "text": text,
            "model_id": kwargs.get("model_id") or self.model_id,
            "voice_settings": {
                "stability": kwargs.get("stability", self.stability),
                "similarity_boost": kwargs.get(
                    "similarity_boost", self.similarity_boost),
            },
        }
        if language:
            body_dict["language_code"] = language

        out_fmt = kwargs.get("output_format") or self.output_format
        path = (_PATH_TTS.format(voice_id=urllib.parse.quote(voice_id))
                + "?output_format=" + urllib.parse.quote(out_fmt))
        audio_bytes, resp_ct = self._request(
            "POST", path,
            body=json.dumps(body_dict).encode("utf-8"),
            content_type="application/json",
            accept="audio/*",
        )
        if not audio_bytes:
            raise ServiceError("ElevenLabs returned empty audio")
        ct = resp_ct or _CT_FOR_FORMAT.get(out_fmt, "audio/mpeg")
        logger.info("[EL] clone_speak ok: %d bytes (%s)",
                     len(audio_bytes), ct)
        return {
            "audio_bytes": audio_bytes,
            "content_type": ct,
            "source_url": "",
            "voice_id": voice_id,
        }


ServiceFactory.register(ElevenLabsVoiceCloneService)
