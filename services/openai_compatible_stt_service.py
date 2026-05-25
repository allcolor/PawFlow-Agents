"""OpenAI-compatible speech-to-text service."""

import json
import logging
import mimetypes
import ipaddress
import socket
import uuid
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict

from core import ServiceFactory, ServiceError
from services.base_stt import BaseSTTService

logger = logging.getLogger(__name__)


_CONVRELAY_HOSTS = {"${convrelay}", "convrelay"}


def _truthy(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _is_private_address(value: str) -> bool:
    try:
        addr = ipaddress.ip_address(value)
    except ValueError:
        return False
    return (
        addr.is_loopback
        or addr.is_private
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def _validate_provider_base_url(base_url: str, *, allow_private: bool = False) -> str:
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ServiceError(f"invalid OpenAI-compatible STT base_url: {base_url!r}")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise ServiceError(f"invalid OpenAI-compatible STT base_url: {base_url!r}")
    if host in _CONVRELAY_HOSTS:
        return base_url.rstrip("/")
    if allow_private:
        return base_url.rstrip("/")
    if host == "localhost" or host.endswith(".localhost") or _is_private_address(host):
        raise ServiceError(
            "OpenAI-compatible STT base_url targets a private/local network address. "
            "Use a relay URL such as https://${convrelay}/localhost:1234/v1, or set "
            "allow_private_base_url=true only for a trusted endpoint."
        )
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ServiceError(f"OpenAI-compatible STT base_url host could not be resolved: {host}") from exc
    for info in infos:
        address = info[4][0]
        if _is_private_address(address):
            raise ServiceError(
                "OpenAI-compatible STT base_url resolves to a private/local network address. "
                "Use https://${convrelay}/... for relay-routed local services, or set "
                "allow_private_base_url=true only for a trusted endpoint."
            )
    return base_url.rstrip("/")


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
            "allow_private_base_url": {
                "type": "boolean", "required": False, "default": False,
                "description": "Allow direct private/loopback base_url targets. Prefer https://${convrelay}/... for local relay endpoints.",
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
        raw_base_url = str(self.config.get("base_url") or "https://api.openai.com/v1")
        self.allow_private_base_url = _truthy(self.config.get("allow_private_base_url", False))
        self.base_url = raw_base_url.rstrip("/")
        self.api_key = str(self.config.get("api_key") or "")
        self.model = str(self.config.get("model") or "gpt-4o-mini-transcribe")
        self.language = str(self.config.get("language") or "")
        self.response_format = str(self.config.get("response_format") or "json")
        self.timeout = int(self.config.get("timeout") or 120)

    def _create_connection(self):
        self.base_url = _validate_provider_base_url(
            self.base_url, allow_private=self.allow_private_base_url)
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

