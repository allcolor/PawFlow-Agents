"""OpenAI-compatible text-to-speech service."""

import base64
import json
import logging
import urllib.error
import urllib.request
from typing import Any, Dict

from core import ServiceFactory, ServiceError, safe_float
from core.relay_proxy_url import relay_proxy_ssl_context, resolve_relay_aware_url
from services.base_tts import BaseTTSService

logger = logging.getLogger(__name__)

_CONTENT_TYPES = {
    "mp3": "audio/mpeg",
    "opus": "audio/ogg",
    "aac": "audio/aac",
    "flac": "audio/flac",
    "wav": "audio/wav",
    "pcm": "audio/L16",
}
_VOICES = ("alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer")
_MODELS = ("gpt-4o-mini-tts", "tts-1", "tts-1-hd")


def _truthy(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _raw_config(config: dict, key: str, default=""):
    try:
        return dict.__getitem__(config, key)
    except KeyError:
        return default


class OpenAICompatibleTTSService(BaseTTSService):
    TYPE = "openaiCompatibleTTS"
    VERSION = "1.0.0"
    NAME = "OpenAI-Compatible Text-to-Speech"
    CATEGORY = "audio"
    SUPPORTS_NATIVE_TTS_VOICES = True
    DESCRIPTION = (
        "Generate speech through OpenAI or any provider exposing the "
        "OpenAI /audio/speech API."
    )

    def get_parameter_schema(self) -> dict:
        return {
            "base_url": {
                "type": "string", "required": False,
                "default": "https://api.openai.com/v1",
                "description": "OpenAI-compatible API base URL, e.g. https://api.openai.com/v1.",
            },
            "api_key": {
                "type": "string", "required": False, "sensitive": True,
                "description": "Bearer token. Leave empty for trusted local/relay endpoints.",
            },
            "allow_private_base_url": {
                "type": "boolean", "required": False, "default": False,
                "description": "Allow direct private/loopback base_url targets. Prefer relay URLs for local endpoints.",
            },
            "model": {
                "type": "string", "required": False,
                "default": "gpt-4o-mini-tts",
                "description": "Speech model sent to /audio/speech, e.g. gpt-4o-mini-tts or openai/gpt-4o-mini-tts-2025-12-15 on OpenRouter.",
            },
            "voice": {
                "type": "select", "required": False,
                "default": "coral",
                "options": list(_VOICES),
                "description": "Default OpenAI voice.",
            },
            "instructions": {
                "type": "textarea", "required": False, "default": "",
                "description": "Optional speaking style instructions supported by newer OpenAI speech models.",
            },
            "provider_options": {
                "type": "textarea", "required": False, "default": "",
                "description": "Optional JSON object passed as provider.options for OpenRouter-compatible routing, e.g. {\"openai\":{\"instructions\":\"Warm tone\"}}.",
            },
            "response_format": {
                "type": "select", "required": False, "default": "mp3",
                "options": sorted(_CONTENT_TYPES),
                "description": "Output audio format. OpenAI defaults to mp3; wav/pcm are lower-latency.",
            },
            "speed": {
                "type": "float", "required": False, "default": 1.0,
                "description": "Optional playback speed multiplier when supported by the provider.",
            },
            "timeout": {
                "type": "integer", "required": False, "default": 120,
                "description": "HTTP timeout in seconds.",
            },
        }

    def __init__(self, config):
        super().__init__(config)
        raw_base_url = str(_raw_config(self.config, "base_url", "https://api.openai.com/v1") or "https://api.openai.com/v1")
        self.allow_private_base_url = _truthy(self.config.get("allow_private_base_url", False))
        self.base_url = raw_base_url.rstrip("/")
        self._raw_base_url = self.base_url
        self.api_key = str(self.config.get("api_key") or "")
        self.model = str(self.config.get("model") or "gpt-4o-mini-tts")
        self.voice = str(self.config.get("voice") or "coral")
        self.instructions = str(self.config.get("instructions") or "")
        self.provider_options = str(self.config.get("provider_options") or "").strip()
        self.response_format = self._response_format(self.config.get("response_format") or "mp3")
        self.speed = safe_float(self.config.get("speed", 1.0), 1.0)
        self.timeout = int(self.config.get("timeout") or 120)
        self._runtime_user_id = ""
        self._runtime_conversation_id = ""
        self._runtime_agent_name = ""

    def set_runtime_context(self, user_id: str = "", conversation_id: str = "",
                            agent_name: str = "", **_: object):
        self._runtime_user_id = user_id or ""
        self._runtime_conversation_id = conversation_id or ""
        self._runtime_agent_name = agent_name or ""

    def _effective_base_url(self) -> str:
        return resolve_relay_aware_url(
            self._raw_base_url,
            user_id=self._runtime_user_id,
            conversation_id=self._runtime_conversation_id,
            agent_name=self._runtime_agent_name,
            allow_private=self.allow_private_base_url,
            service_name="OpenAI-compatible TTS",
            transform_relay=True,
        )

    def _create_connection(self):
        self.base_url = resolve_relay_aware_url(
            self._raw_base_url,
            allow_private=self.allow_private_base_url,
            service_name="OpenAI-compatible TTS",
            transform_relay=False,
        )
        return {"ready": True, "base_url": self.base_url}

    def _close_connection(self):
        pass

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "audio/*, application/json",
            "User-Agent": "PawFlow-Agent/1.0",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _speech_body(self, text: str, voice: str = "", language: str = "",
                     **kwargs) -> Dict[str, Any]:
        _ = language
        body: Dict[str, Any] = {
            "model": kwargs.pop("model", None) or self.model,
            "input": text,
            "voice": voice or self.voice,
            "response_format": self._response_format(
                kwargs.pop("response_format", None) or self.response_format),
        }
        instructions = kwargs.pop("instructions", None) or self.instructions
        if instructions:
            body["instructions"] = instructions
        provider = kwargs.pop("provider", None)
        provider_options = kwargs.pop("provider_options", None) or self.provider_options
        if provider:
            body["provider"] = provider
        elif provider_options:
            try:
                parsed_provider_options = json.loads(str(provider_options))
            except json.JSONDecodeError as exc:
                raise ServiceError("provider_options must be valid JSON") from exc
            if not isinstance(parsed_provider_options, dict):
                raise ServiceError("provider_options must be a JSON object")
            body["provider"] = {"options": parsed_provider_options}
        speed = kwargs.pop("speed", None)
        if speed in (None, ""):
            speed = self.speed
        if speed not in (None, "", 1, 1.0, "1", "1.0"):
            body["speed"] = safe_float(speed, 1.0)
        for key, value in kwargs.items():
            if key in body or key in {
                "destination", "path", "service", "audio_service", "voice_service",
                "prompt", "lyrics", "instrumental", "style", "transient",
                "transient_ttl", "ttl",
            }:
                continue
            if value is not None and value != "":
                body[key] = value
        return body

    def _post_speech(self, body: Dict[str, Any]) -> tuple[bytes, str]:
        url = self._effective_base_url() + "/audio/speech"
        payload = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers=self._headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=relay_proxy_ssl_context(url)) as resp:  # nosec B310 - configured TTS provider endpoint.
                raw = resp.read()
                content_type = resp.headers.get("Content-Type", "") or _CONTENT_TYPES.get(
                    body.get("response_format", "mp3"), "audio/mpeg")
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:1000].decode("utf-8", errors="replace")
            raise ServiceError(
                f"OpenAI-compatible TTS error POST /audio/speech ({exc.code}): {detail}") from exc
        except urllib.error.URLError as exc:
            raise ServiceError(f"OpenAI-compatible TTS unavailable at {url}: {exc}") from exc

        if "json" in (content_type or "").lower():
            try:
                data = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise ServiceError("OpenAI-compatible TTS returned invalid JSON") from exc
            b64 = data.get("audio_base64") or data.get("audio") or data.get("b64_json") or ""
            if not b64:
                raise ServiceError("OpenAI-compatible TTS JSON response did not contain audio")
            raw = base64.b64decode(str(b64).split(",", 1)[-1])
            content_type = data.get("content_type") or _CONTENT_TYPES.get(
                body.get("response_format", "mp3"), "audio/mpeg")
        if not raw:
            raise ServiceError("OpenAI-compatible TTS returned empty audio")
        return raw, content_type

    def speak(self, text: str, voice: str = "", language: str = "",
              **kwargs) -> dict:
        if not text:
            raise ServiceError("text is required")
        self.ensure_connected()
        body = self._speech_body(text=text, voice=voice, language=language, **kwargs)
        audio_bytes, content_type = self._post_speech(body)
        logger.info("[OPENAI-TTS] speech ok: %d bytes (%s)", len(audio_bytes), content_type)
        return {"audio_bytes": audio_bytes, "content_type": content_type, "source_url": ""}

    @staticmethod
    def _response_format(value: str) -> str:
        fmt = str(value or "mp3").strip().lower()
        if fmt not in _CONTENT_TYPES:
            raise ServiceError(f"unsupported response_format {fmt!r}; expected one of {sorted(_CONTENT_TYPES)}")
        return fmt


ServiceFactory.register(OpenAICompatibleTTSService)
