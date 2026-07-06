"""OpenAI-compatible speech-to-text service."""

import json
import logging
import mimetypes
import base64
import uuid
import urllib.error
import urllib.request
from typing import Dict

from core import ServiceFactory, ServiceError
from core.relay_proxy_url import (
    CONV_RELAY_EXPR, relay_proxy_ssl_context, resolve_relay_aware_url)
from services.base_stt import BaseSTTService

logger = logging.getLogger(__name__)


def _truthy(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _raw_config(config: dict, key: str, default=""):
    try:
        return dict.__getitem__(config, key)
    except KeyError:
        return default


def _multipart(fields: Dict[str, str], files: Dict[str, tuple[str, bytes, str]]) -> tuple[bytes, str]:
    boundary = "----PawFlowSTT" + uuid.uuid4().hex
    chunks = []
    for name, value in fields.items():
        if value is None or value == "":
            continue
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")
    for name, (filename, payload, content_type) in files.items():
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode())
        chunks.append(f"Content-Type: {content_type or 'application/octet-stream'}\r\n\r\n".encode())
        chunks.append(payload)
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


class OpenAICompatibleSTTService(BaseSTTService):
    TYPE = "openaiCompatibleSTT"
    VERSION = "1.0.0"
    NAME = "OpenAI-Compatible Speech-to-Text"
    CATEGORY = "audio"
    DESCRIPTION = (
        "Transcribe audio through OpenAI, Groq, local whisper.cpp, or any "
        "provider exposing the OpenAI /audio/transcriptions API."
    )

    def get_parameter_schema(self) -> dict:
        return {
            "base_url": {
                "type": "string", "required": False,
                "default": "https://api.openai.com/v1",
                "description": f"OpenAI-compatible API base URL, e.g. relay://{CONV_RELAY_EXPR}/localhost:1234/v1.",
            },
            "model": {
                "type": "string",
                "default": "whisper-1",
                "description": "Transcription model name",
            },
            "protocol": {
                "type": "string",
                "default": "auto",
                "description": "Request protocol. auto uses OpenRouter JSON when base_url points to openrouter.ai; otherwise OpenAI multipart.",
            },
            "allow_private_base_url": {
                "type": "boolean",
                "default": False,
                "description": f"Allow direct private/loopback base_url targets. Prefer relay://{CONV_RELAY_EXPR}/host:port for local relay endpoints.",
            },
            "model": {
                "type": "string", "required": False,
                "default": "gpt-4o-mini-transcribe",
                "description": "Transcription model, e.g. gpt-4o-transcribe, whisper-1, whisper-large-v3-turbo.",
            },
            "language": {
                "type": "string", "required": False, "default": "",
                "description": "Optional language hint such as en or fr.",
            },
            "response_format": {
                "type": "string", "required": False, "default": "json",
                "description": "Provider response format. json is recommended.",
            },
            "provider_options": {
                "type": "textarea", "required": False, "default": "",
                "description": "Optional JSON object passed as provider.options for OpenRouter STT routing.",
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
        self._runtime_user_id = ""
        self._runtime_conversation_id = ""
        self._runtime_agent_name = ""
        self.api_key = str(self.config.get("api_key") or "")
        self.protocol = str(self.config.get("protocol") or "auto").strip().lower()
        self.model = str(self.config.get("model") or "gpt-4o-mini-transcribe")
        self.language = str(self.config.get("language") or "")
        self.response_format = str(self.config.get("response_format") or "json")
        self.provider_options = str(self.config.get("provider_options") or "").strip()
        self.timeout = int(self.config.get("timeout") or 120)

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
            service_name="OpenAI-compatible STT",
            transform_relay=True,
        )

    def _create_connection(self):
        self.base_url = resolve_relay_aware_url(
            self._raw_base_url,
            allow_private=self.allow_private_base_url,
            service_name="OpenAI-compatible STT",
            transform_relay=False,
        )
        return {"ready": True, "base_url": self.base_url}

    def _close_connection(self):
        pass

    def _select_protocol(self) -> str:
        if self.protocol in {"openai_multipart", "openrouter_json"}:
            return self.protocol
        if "openrouter.ai" in (self._raw_base_url or "").lower():
            return "openrouter_json"
        return "openai_multipart"

    @staticmethod
    def _audio_format(filename: str, mime_type: str) -> str:
        marker = f"{filename} {mime_type}".lower()
        for fmt in ("wav", "mp3", "flac", "m4a", "ogg", "webm", "aac"):
            if fmt in marker:
                return fmt
        return "wav"

    def _provider_body(self, override=None) -> dict:
        provider = override
        provider_options = ""
        if provider is None:
            provider_options = self.provider_options
        if provider:
            if not isinstance(provider, dict):
                raise ServiceError("provider must be a JSON object")
            return provider
        if not provider_options:
            return {}
        try:
            parsed = json.loads(provider_options)
        except json.JSONDecodeError as exc:
            raise ServiceError("provider_options must be valid JSON") from exc
        if not isinstance(parsed, dict):
            raise ServiceError("provider_options must be a JSON object")
        return {"options": parsed}

    def _transcribe_openrouter_json(self, audio_bytes: bytes, filename: str,
                                    mime_type: str, language: str,
                                    prompt: str, model: str,
                                    **kwargs) -> dict:
        body = {
            "model": model or self.model,
            "input_audio": {
                "data": base64.b64encode(audio_bytes).decode("ascii"),
                "format": kwargs.pop("format", None) or self._audio_format(filename, mime_type),
            },
        }
        if language or self.language:
            body["language"] = language or self.language
        temperature = kwargs.pop("temperature", None)
        if temperature not in (None, ""):
            body["temperature"] = float(temperature)
        provider = self._provider_body(kwargs.pop("provider", None))
        if provider:
            body["provider"] = provider
        if prompt:
            body.setdefault("provider", {"options": {}})
            body["provider"].setdefault("options", {}).setdefault("openai", {})["prompt"] = prompt
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "PawFlow-Agent/1.0",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        base_url = self._effective_base_url()
        req = urllib.request.Request(
            f"{base_url}/audio/transcriptions",
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=relay_proxy_ssl_context(base_url)) as resp:  # nosec B310 - configured STT provider endpoint.
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:1000].decode("utf-8", errors="replace")
            raise ServiceError(
                f"OpenAI-compatible STT error POST /audio/transcriptions ({exc.code}): {detail}") from exc
        except urllib.error.URLError as exc:
            raise ServiceError(f"OpenAI-compatible STT unavailable at {base_url}: {exc}") from exc
        try:
            return json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            raise ServiceError("OpenAI-compatible STT returned non-JSON") from exc

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
        content_type = mime_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        if self._select_protocol() == "openrouter_json":
            result = self._transcribe_openrouter_json(
                audio_bytes, filename, content_type, language, prompt, model, **kwargs)
            transcript = result.get("text") or result.get("transcript") or ""
            usage = result.get("usage") or {}
            return {
                "text": str(transcript).strip(),
                "language": result.get("language", language or self.language),
                "duration": result.get("duration", usage.get("seconds", 0)),
                "segments": result.get("segments", []),
                "provider_result": result,
            }
        fields = {
            "model": model or self.model,
            "language": language or self.language,
            "prompt": prompt,
            "response_format": self.response_format,
        }
        body, multipart_type = _multipart(fields, {
            "file": (filename, audio_bytes, content_type),
        })
        headers = {
            "Content-Type": multipart_type,
            "Accept": "application/json",
            "User-Agent": "PawFlow-Agent/1.0",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        base_url = self._effective_base_url()
        req = urllib.request.Request(
            f"{base_url}/audio/transcriptions",
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=relay_proxy_ssl_context(base_url)) as resp:  # nosec B310 - configured STT provider endpoint.
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:1000].decode("utf-8", errors="replace")
            raise ServiceError(
                f"OpenAI-compatible STT error POST /audio/transcriptions ({exc.code}): {detail}") from exc
        except urllib.error.URLError as exc:
            raise ServiceError(f"OpenAI-compatible STT unavailable at {base_url}: {exc}") from exc

        text = raw.decode("utf-8", errors="replace")
        result = {"text": text}
        if self.response_format == "json":
            try:
                result = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ServiceError(f"OpenAI-compatible STT returned non-JSON: {text[:200]}") from exc
        transcript = result.get("text") or result.get("transcript") or ""
        return {
            "text": str(transcript).strip(),
            "language": result.get("language", language or self.language),
            "duration": result.get("duration", 0),
            "segments": result.get("segments", []),
            "provider_result": result,
        }


ServiceFactory.register(OpenAICompatibleSTTService)

