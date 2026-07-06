"""VoxCPM text-to-speech service.

PawFlow does not install or start VoxCPM. This service talks to a user-managed
VoxCPM runtime through vLLM-Omni's OpenAI-compatible endpoint or an explicitly
configured local CLI command.
"""

import base64
import json
import logging
import mimetypes
import os
import shlex
import subprocess  # nosec B404 - CLI mode uses explicit argv with shell=False.
import tempfile
import urllib.error
import urllib.request
from typing import Any, Dict

from core import ServiceFactory, ServiceError, safe_float
from core.relay_proxy_url import (
    CONV_RELAY_EXPR, relay_proxy_ssl_context, resolve_relay_aware_url)
from services.base_audio_generation import BaseAudioGenerationService
from services.base_voice_clone import BaseVoiceCloneService

logger = logging.getLogger(__name__)

_API_MODES = {"openai", "cli"}
_CONTENT_TYPES = {
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "mpeg": "audio/mpeg",
    "opus": "audio/ogg",
    "ogg": "audio/ogg",
    "flac": "audio/flac",
    "pcm": "audio/L16",
}


def _truthy(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _raw_config(config: dict, key: str, default=""):
    try:
        return dict.__getitem__(config, key)
    except KeyError:
        return default


class VoxCPMTTSService(BaseAudioGenerationService, BaseVoiceCloneService):
    TYPE = "voxcpmTTS"
    VERSION = "1.0.0"
    NAME = "VoxCPM External TTS"
    CATEGORY = "audio"
    SUPPORTS_NATIVE_TTS_VOICES = True
    DESCRIPTION = (
        "Generate speech through a user-managed VoxCPM HTTP server. PawFlow "
        "does not install, start, or stop the VoxCPM runtime."
    )

    def get_parameter_schema(self) -> dict:
        return {
            "api_mode": {
                "type": "select", "required": False, "default": "openai",
                "options": sorted(_API_MODES),
                "description": "VoxCPM integration mode: OpenAI-compatible POST /v1/audio/speech or local CLI.",
            },
            "base_url": {
                "type": "string", "required": False,
                "default": f"relay://{CONV_RELAY_EXPR}/localhost:8000",
                "description": f"VoxCPM HTTP server URL. Use relay://{CONV_RELAY_EXPR}/localhost:8000 for vLLM-Omni.",
            },
            "model": {
                "type": "string", "required": False, "default": "openbmb/VoxCPM2",
                "description": "Model name sent to OpenAI-compatible VoxCPM endpoints.",
            },
            "response_format": {
                "type": "select", "required": False, "default": "wav",
                "options": sorted(_CONTENT_TYPES),
                "description": "Requested output format for OpenAI-compatible and CLI modes.",
            },
            "api_key_env": {
                "type": "string", "required": False, "default": "",
                "description": "Optional environment variable containing a bearer token for HTTP VoxCPM endpoints.",
            },
            "allow_private_base_url": {
                "type": "boolean", "required": False, "default": False,
                "description": "Allow direct private/loopback base_url targets. Prefer relay URLs for user-local endpoints.",
            },
            "cli_command": {
                "type": "string", "required": False, "default": "voxcpm",
                "description": "Command used by api_mode=cli. It is split as argv and executed without a shell.",
            },
            "cli_workdir": {
                "type": "string", "required": False, "default": "",
                "description": "Optional working directory for api_mode=cli.",
            },
            "voice": {
                "type": "textarea", "required": False, "default": "",
                "description": "Optional VoxCPM control instruction / voice description.",
            },
            "timeout": {
                "type": "integer", "required": False, "default": 300,
                "description": "HTTP timeout in seconds.",
            },
        }

    def __init__(self, config):
        super().__init__(config)
        self.api_mode = self._api_mode(self.config.get("api_mode") or "openai")
        self.base_url = str(_raw_config(self.config, "base_url", "") or "").rstrip("/")
        self._raw_base_url = self.base_url
        self.model = str(self.config.get("model") or "openbmb/VoxCPM2")
        self.response_format = self._response_format(self.config.get("response_format") or "wav")
        self.api_key_env = str(self.config.get("api_key_env") or "").strip()
        self.cli_command = str(self.config.get("cli_command") or "voxcpm").strip()
        self.cli_workdir = str(self.config.get("cli_workdir") or "").strip()
        self.allow_private_base_url = _truthy(self.config.get("allow_private_base_url", False))
        self.voice = str(self.config.get("voice") or "")
        self.timeout = int(self.config.get("timeout") or 300)
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
            service_name="VoxCPM",
            transform_relay=True,
        )

    def _create_connection(self):
        if self.api_mode == "cli":
            if not self.cli_command:
                raise ServiceError("cli_command is required for VoxCPM CLI mode")
            return {"ready": True, "api_mode": self.api_mode, "managed": False}
        if not self._raw_base_url:
            raise ServiceError("base_url is required for VoxCPM")
        self.base_url = resolve_relay_aware_url(
            self._raw_base_url,
            allow_private=self.allow_private_base_url,
            service_name="VoxCPM",
            transform_relay=False,
        )
        return {"ready": True, "base_url": self.base_url, "api_mode": self.api_mode, "managed": False}

    def _close_connection(self):
        pass

    def _fetch_reference(self, url: str) -> tuple[bytes, str]:
        if not url:
            return b"", ""
        if not (url.startswith("http://") or url.startswith("https://")):
            raise ServiceError(f"reference_audio_url must be an absolute URL, got {url!r}")
        req = urllib.request.Request(url, headers={"User-Agent": "PawFlow-Agent/1.0"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # nosec B310 - reference URL is supplied by the handler/user.
            content_type = resp.headers.get("Content-Type") or mimetypes.guess_type(url)[0] or "audio/wav"
            return resp.read(), content_type

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "audio/wav, audio/*, application/json",
            "User-Agent": "PawFlow-Agent/1.0",
        }
        if self.api_key_env:
            token = os.environ.get(self.api_key_env, "")
            if token:
                headers["Authorization"] = f"Bearer {token}"
        return headers

    def _post_openai_speech(self, body: Dict[str, Any]) -> tuple[bytes, str]:
        url = self._effective_base_url() + "/v1/audio/speech"
        payload = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers=self._headers(),
            method="POST",
        )
        return self._post_audio_request(req, "POST /v1/audio/speech")

    def _post_audio_request(self, req, label: str) -> tuple[bytes, str]:
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=relay_proxy_ssl_context(req.full_url)) as resp:  # nosec B310 - configured VoxCPM endpoint.
                raw = resp.read()
                content_type = resp.headers.get("Content-Type", "audio/wav")
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:1000].decode("utf-8", errors="replace")
            raise ServiceError(f"VoxCPM API error {label} ({exc.code}): {detail}") from exc
        except urllib.error.URLError as exc:
            raise ServiceError(f"VoxCPM unavailable at {req.full_url}: {exc}") from exc

        if "json" in (content_type or "").lower():
            try:
                data = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise ServiceError("VoxCPM returned invalid JSON") from exc
            b64 = data.get("audio_base64") or data.get("audio") or ""
            if not b64:
                raise ServiceError("VoxCPM JSON response did not contain audio_base64")
            raw = base64.b64decode(str(b64).split(",", 1)[-1])
            content_type = data.get("content_type") or "audio/wav"
        if not raw:
            raise ServiceError("VoxCPM returned empty audio")
        return raw, content_type or "audio/wav"

    def _openai_body(self, text: str, voice: str = "", language: str = "",
                     **kwargs) -> Dict[str, Any]:
        _ = language
        body: Dict[str, Any] = {
            "model": kwargs.pop("model", None) or self.model,
            "input": text,
            "voice": voice or self.voice or "default",
            "response_format": kwargs.pop("response_format", None) or self.response_format,
        }
        speed = kwargs.pop("speed", None)
        if speed not in (None, ""):
            body["speed"] = safe_float(speed, 1.0)
        for key, value in kwargs.items():
            if key in body or key in {
                "destination", "path", "service", "audio_service", "voice_service",
                "prompt", "lyrics", "instrumental", "style",
            }:
                continue
            if value is not None and value != "":
                body[key] = value
        return body

    def _cli_speak(self, text: str, voice: str = "", language: str = "",
                   reference_audio_bytes: bytes = None,
                   reference_audio_content_type: str = "",
                   reference_text: str = "", ultimate_clone: bool = False,
                   **kwargs) -> dict:
        _ = language, reference_audio_content_type, kwargs
        fmt = self._response_format(kwargs.get("response_format") or self.response_format)
        with tempfile.TemporaryDirectory(prefix="pawflow_voxcpm_") as tmp:
            output_path = os.path.join(tmp, f"speech.{fmt}")
            argv = shlex.split(self.cli_command)
            if not argv:
                raise ServiceError("cli_command is required for VoxCPM CLI mode")
            if reference_audio_bytes:
                ref_path = os.path.join(tmp, "reference.wav")
                with open(ref_path, "wb") as fh:
                    fh.write(reference_audio_bytes)
                argv.extend(["clone", "--text", text, "--reference-audio", ref_path])
                if ultimate_clone:
                    argv.extend(["--prompt-audio", ref_path])
                if reference_text:
                    argv.extend(["--prompt-text", reference_text])
            else:
                argv.extend(["design", "--text", text])
                if voice or self.voice:
                    argv.extend(["--control", voice or self.voice])
            argv.extend(["--output", output_path])
            try:
                proc = subprocess.run(  # nosec B603 - argv is explicit and shell=False.
                    argv,
                    cwd=self.cli_workdir or None,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=self.timeout,
                    check=False,
                )
            except FileNotFoundError as exc:
                raise ServiceError(f"VoxCPM CLI command not found: {argv[0]}") from exc
            except subprocess.TimeoutExpired as exc:
                raise ServiceError(f"VoxCPM CLI timed out after {self.timeout}s") from exc
            if proc.returncode != 0:
                detail = proc.stderr[:1000].decode("utf-8", errors="replace").strip()
                raise ServiceError(f"VoxCPM CLI failed ({proc.returncode}): {detail}")
            try:
                with open(output_path, "rb") as fh:
                    audio = fh.read()
            except OSError as exc:
                raise ServiceError("VoxCPM CLI did not create output audio") from exc
            if not audio:
                raise ServiceError("VoxCPM CLI returned empty audio")
            return {"audio_bytes": audio, "content_type": _CONTENT_TYPES.get(fmt, "audio/wav"), "source_url": ""}

    def generate(self, prompt: str = "", text: str = "", voice: str = "",
                 language: str = "", **kwargs) -> dict:
        return self.speak(text=text or prompt, voice=voice, language=language, **kwargs)

    def speak(self, text: str, voice: str = "", language: str = "",
              **kwargs) -> dict:
        if not text:
            raise ServiceError("text is required")
        self.ensure_connected()
        if self.api_mode == "cli":
            return self._cli_speak(text=text, voice=voice, language=language, **kwargs)
        audio_bytes, content_type = self._post_openai_speech(
            self._openai_body(text=text, voice=voice, language=language, **kwargs))
        logger.info("[VOXCPM] openai tts ok: %d bytes (%s)", len(audio_bytes), content_type)
        return {"audio_bytes": audio_bytes, "content_type": content_type, "source_url": ""}

    def clone_speak(self, text: str = "", reference_audio_url: str = "",
                    reference_text: str = "", language: str = "",
                    reference_audio_bytes: bytes = None,
                    reference_audio_content_type: str = "",
                    ultimate_clone: bool = False, **kwargs) -> dict:
        if not text:
            raise ServiceError("clone_speak requires text")
        self.ensure_connected()
        if reference_audio_bytes is None and reference_audio_url:
            reference_audio_bytes, reference_audio_content_type = self._fetch_reference(reference_audio_url)
        if not reference_audio_bytes:
            raise ServiceError("clone_speak requires reference_audio_url or reference_audio_bytes")
        if self.api_mode == "cli":
            return self._cli_speak(
                text=text,
                language=language,
                reference_audio_bytes=reference_audio_bytes,
                reference_audio_content_type=reference_audio_content_type,
                reference_text=reference_text,
                ultimate_clone=ultimate_clone,
                **kwargs,
            )
        raise ServiceError(
            "VoxCPM voice cloning requires api_mode=cli; "
            "vLLM-Omni /v1/audio/speech supports direct speech only")

    @staticmethod
    def _api_mode(value: str) -> str:
        mode = str(value or "openai").strip().lower()
        if mode not in _API_MODES:
            raise ServiceError(f"unsupported VoxCPM api_mode {mode!r}; expected one of {sorted(_API_MODES)}")
        return mode

    @staticmethod
    def _response_format(value: str) -> str:
        fmt = str(value or "wav").strip().lower()
        if fmt not in _CONTENT_TYPES:
            raise ServiceError(f"unsupported VoxCPM response_format {fmt!r}; expected one of {sorted(_CONTENT_TYPES)}")
        return fmt


ServiceFactory.register(VoxCPMTTSService)
