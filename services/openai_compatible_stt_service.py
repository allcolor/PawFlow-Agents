"""OpenAI-compatible speech-to-text service."""

import json
import logging
import mimetypes
import uuid
import urllib.error
import urllib.request
from typing import Dict

from core import ServiceFactory, ServiceError
from services.base_stt import BaseSTTService

logger = logging.getLogger(__name__)


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
                "description": "OpenAI-compatible API base URL, e.g. https://${convrelay}/localhost:1234/v1.",
            },
            "api_key": {
                "type": "string", "required": False, "sensitive": True,
                "description": "Bearer token. Leave empty for trusted local/relay endpoints.",
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
            "timeout": {
                "type": "integer", "required": False, "default": 120,
                "description": "HTTP timeout in seconds.",
            },
        }

    def __init__(self, config):
        super().__init__(config)
        self.base_url = str(self.config.get("base_url") or "https://api.openai.com/v1").rstrip("/")
        self.api_key = str(self.config.get("api_key") or "")
        self.model = str(self.config.get("model") or "gpt-4o-mini-transcribe")
        self.language = str(self.config.get("language") or "")
        self.response_format = str(self.config.get("response_format") or "json")
        self.timeout = int(self.config.get("timeout") or 120)

    def _create_connection(self):
        if not self.base_url.startswith(("http://", "https://")):
            raise ServiceError(f"invalid OpenAI-compatible STT base_url: {self.base_url!r}")
        return {"ready": True, "base_url": self.base_url}

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
        filename = kwargs.get("filename") or "speech.webm"
        content_type = mime_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
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
        req = urllib.request.Request(
            f"{self.base_url}/audio/transcriptions",
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # nosec B310 - configured STT provider endpoint.
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:1000].decode("utf-8", errors="replace")
            raise ServiceError(
                f"OpenAI-compatible STT error POST /audio/transcriptions ({exc.code}): {detail}") from exc
        except urllib.error.URLError as exc:
            raise ServiceError(f"OpenAI-compatible STT unavailable at {self.base_url}: {exc}") from exc

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

