"""Voicebox local voice I/O service.

Voicebox exposes a local REST API for transcription and speech generation.
This service intentionally treats Voicebox as a provider bridge: PawFlow keeps
its own service selection and chat UI, while Voicebox owns local Whisper/TTS
engines and voice profiles.
"""

import json
import base64
import logging
import mimetypes
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict

from core import ServiceFactory, ServiceError
from core.relay_proxy_url import CONV_RELAY_EXPR, resolve_relay_aware_url
from services.base_stt import BaseSTTService
from services.base_voice_clone import BaseVoiceCloneService
from services._voicebox_backend import _VoiceboxBackendMixin

logger = logging.getLogger(__name__)

_VOICEBOX_DEFAULT_REPO_URL = "https://github.com/jamiepine/voicebox.git"
_VOICEBOX_DEFAULT_REPO_REF = "b35b90961d5bc83a8b4e96e8b6ccde2a03152ff9"


def _raw_config(config: dict, key: str, default=""):
    try:
        return dict.__getitem__(config, key)
    except KeyError:
        return default


def _multipart(fields: Dict[str, str], files: Dict[str, tuple[str, bytes, str]]) -> tuple[bytes, str]:
    boundary = "----PawFlowVoicebox" + uuid.uuid4().hex
    chunks = []
    for name, value in fields.items():
        if value is None or value == "":
            continue
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
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



class VoiceboxService(_VoiceboxBackendMixin, BaseVoiceCloneService, BaseSTTService):
    TYPE = "voicebox"
    VERSION = "1.0.0"
    NAME = "Voicebox Local Voice I/O"
    CATEGORY = "audio"
    SUPPORTS_NATIVE_TTS_VOICES = True
    ACCEPTS_FILESTORE_URLS = False
    ACCEPTS_BROWSER_STT_AUDIO = True

    def get_parameter_schema(self) -> dict:
        return {
            "base_url": {
                "type": "string", "required": False,
                "default": "http://127.0.0.1:17493",
                "description": f"Voicebox HTTP API base URL. Use relay://{CONV_RELAY_EXPR}/localhost:17493 for a relay-routed user endpoint.",
            },
            "allow_private_base_url": {
                "type": "boolean", "required": False, "default": False,
                "description": "Allow direct private/loopback base_url targets when auto_start is false. Prefer relay URLs for user-local endpoints.",
            },
            "client_id": {
                "type": "string", "required": False, "default": "pawflow",
                "description": "X-Voicebox-Client-Id header used for Voicebox bindings.",
            },
            "stt_model": {
                "type": "string", "required": False, "default": "turbo",
                "description": "Voicebox Whisper model for transcription.",
            },
            "default_profile": {
                "type": "string", "required": False, "default": "",
                "description": "Default Voicebox profile name or id for speech.",
            },
            "profile_name": {
                "type": "string", "required": False, "default": "",
                "description": "Voicebox profile name to create or update with the Save Voicebox profile action.",
            },
            "profile_engine": {
                "type": "select", "required": False, "default": "kokoro",
                "options": ["kokoro", "qwen_custom_voice"],
                "description": "Preset engine used by the Save Voicebox profile action.",
            },
            "profile_voice_id": {
                "type": "string", "required": False, "default": "",
                "description": "Preset voice id, for example ff_siwis or Ryan. Leave empty to match profile_name against the preset catalog.",
            },
            "profile_language": {
                "type": "string", "required": False, "default": "",
                "description": "Optional language code for the Voicebox profile. If empty, the preset catalog language is used.",
            },
            "profile_description": {
                "type": "textarea", "required": False, "default": "",
                "description": "Optional description for the Voicebox profile.",
            },
            "profile_personality": {
                "type": "textarea", "required": False, "default": "",
                "description": "Optional Voicebox personality prompt for this profile.",
            },
            "auto_start": {
                "type": "boolean", "required": False, "default": True,
                "description": "Start the local Voicebox backend automatically when needed.",
            },
            "auto_install": {
                "type": "boolean", "required": False, "default": True,
                "description": "Clone/setup Voicebox into install_dir if no runnable backend is found.",
            },
            "install_dir": {
                "type": "string", "required": False, "default": "data/runtime/voicebox",
                "description": "Managed Voicebox checkout directory used for auto-install/start.",
            },
            "repo_url": {
                "type": "string", "required": False,
                "default": _VOICEBOX_DEFAULT_REPO_URL,
                "description": "Git repository used when auto_install creates the managed Voicebox checkout.",
            },
            "repo_ref": {
                "type": "string", "required": False,
                "default": _VOICEBOX_DEFAULT_REPO_REF,
                "description": "Immutable git ref checked out after auto-install for reproducible setup.",
            },
            "start_command": {
                "type": "string", "required": False, "default": "",
                "description": "Optional explicit command to start the Voicebox API backend.",
            },
            "startup_timeout": {
                "type": "integer", "required": False, "default": 180,
                "description": "Seconds to wait for Voicebox to become reachable.",
            },
            "preload_stt_model": {
                "type": "boolean", "required": False, "default": True,
                "description": "Download the configured Whisper STT model during service installation.",
            },
            "preload_timeout": {
                "type": "integer", "required": False, "default": 1800,
                "description": "Seconds to wait for model preloading during service installation.",
            },
            "timeout": {
                "type": "integer", "required": False, "default": 180,
                "description": "HTTP timeout in seconds.",
            },
        }

    def get_service_actions(self) -> list:
        return [
            {
                "id": "voicebox_profiles_list",
                "label": "List Voicebox profiles",
                "server_action": "voicebox_profiles_list",
                "flow": "simple",
            },
            {
                "id": "voicebox_preset_voices_list",
                "label": "List preset voices",
                "server_action": "voicebox_preset_voices_list",
                "flow": "simple",
            },
            {
                "id": "voicebox_profile_save",
                "label": "Save Voicebox profile",
                "server_action": "voicebox_profile_save",
                "flow": "simple",
            },
            {
                "id": "voicebox_tasks_clear",
                "label": "Clear Voicebox tasks",
                "server_action": "voicebox_tasks_clear",
                "flow": "confirm",
            },
        ]

    def __init__(self, config):
        super().__init__(config)
        self.base_url = str(
            _raw_config(self.config, "base_url", "http://127.0.0.1:17493") or "http://127.0.0.1:17493").rstrip("/")
        self._raw_base_url = self.base_url
        self.allow_private_base_url = str(
            self.config.get("allow_private_base_url", False)).lower() in {"1", "true", "yes", "on"}
        self._runtime_user_id = ""
        self._runtime_conversation_id = ""
        self._runtime_agent_name = ""
        self.client_id = str(self.config.get("client_id") or "pawflow")
        self.stt_model = self._normalize_stt_model(str(self.config.get("stt_model") or "turbo"))
        self.default_profile = str(self.config.get("default_profile") or "")
        self.timeout = int(self.config.get("timeout") or 180)
        self.auto_start = str(self.config.get("auto_start", True)).lower() not in {"0", "false", "no"}
        self.auto_install = str(self.config.get("auto_install", True)).lower() not in {"0", "false", "no"}
        install_dir = Path(str(self.config.get("install_dir") or "data/runtime/voicebox")).expanduser()
        if not install_dir.is_absolute():
            install_dir = Path.cwd() / install_dir
        self.install_dir = install_dir
        self.repo_url = str(self.config.get("repo_url") or _VOICEBOX_DEFAULT_REPO_URL)
        self.repo_ref = str(self.config.get("repo_ref") or _VOICEBOX_DEFAULT_REPO_REF)
        self.start_command = str(self.config.get("start_command") or "").strip()
        self.startup_timeout = int(self.config.get("startup_timeout") or 180)
        self.preload_stt_model = str(self.config.get("preload_stt_model", True)).lower() not in {"0", "false", "no"}
        self.preload_timeout = int(self.config.get("preload_timeout") or 1800)
        self._managed_proc = None
        self._managed_log_path = self.install_dir / "backend" / "pawflow-voicebox.log"
        self._stt_warmup_done = False

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
            allow_private=self.allow_private_base_url or self.auto_start,
            service_name="Voicebox",
            transform_relay=True,
        )


    def clear_tasks(self) -> dict:
        self.ensure_connected()
        return self._request("POST", "/tasks/clear", accept_json=True)

    def transcribe(self, audio_bytes: bytes = b"", audio_path: str = "",
                   mime_type: str = "", language: str = "",
                   prompt: str = "", model: str = "", **kwargs) -> dict:
        self.ensure_connected()
        if not audio_bytes and audio_path:
            with open(audio_path, "rb") as fh:
                audio_bytes = fh.read()
        if not audio_bytes:
            raise ServiceError("audio_bytes or audio_path is required")
        filename = kwargs.get("filename") or "recording.webm"
        ctype = mime_type or mimetypes.guess_type(filename)[0] or "audio/webm"
        fields = {
            "model": self._normalize_stt_model(model or self.stt_model),
            "language": language,
            "prompt": prompt,
        }
        body, content_type = _multipart(fields, {
            "file": (filename, audio_bytes, ctype),
        })
        data = self._request(
            "POST", "/transcribe", body,
            {"Content-Type": content_type}, accept_json=True)
        detail = data.get("detail") if isinstance(data, dict) else None
        if isinstance(detail, dict) and detail.get("downloading"):
            model_name = str(detail.get("model_name") or "")
            progress = self._active_download_detail(model_name)
            raise ServiceError(str(
                (detail.get("message") or "Voicebox STT model is downloading. Please retry shortly.")
                + progress
            ))
        if isinstance(detail, str) and detail:
            raise ServiceError(detail)
        text = data.get("text") or data.get("transcript") or data.get("result") or ""
        return {
            "text": str(text).strip(),
            "language": data.get("language", language or ""),
            "duration": data.get("duration", 0),
            "segments": data.get("segments", []),
            "provider_result": data,
        }

    def warmup_stt(self, language: str = "", model: str = "", **_kwargs) -> None:
        if self._stt_warmup_done:
            return
        self.ensure_connected()
        self._stt_warmup_done = True

    def list_profiles(self) -> list[dict]:
        self.ensure_connected()
        profiles = self._request("GET", "/profiles", accept_json=True)
        return profiles if isinstance(profiles, list) else []

    def list_preset_voices(self, engine: str = "kokoro") -> list[dict]:
        self.ensure_connected()
        data = self._request(
            "GET", f"/profiles/presets/{urllib.parse.quote(str(engine or 'kokoro'))}",
            accept_json=True, allow_404=True)
        voices = data.get("voices", []) if isinstance(data, dict) else []
        return voices if isinstance(voices, list) else []

    def _preset_voice_for_profile(self, name: str, engine: str = "kokoro",
                                  voice_id: str = "") -> dict:
        wanted_name = str(name or "").strip().lower()
        wanted_id = str(voice_id or "").strip().lower()
        if not wanted_name and not wanted_id:
            return {}
        for item in self.list_preset_voices(engine):
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("voice_id") or "")
            item_name = str(item.get("name") or "")
            if wanted_id and wanted_id == item_id.lower():
                return item
            if wanted_name and wanted_name == item_name.lower():
                return item
        return {}

    def save_preset_profile(self, name: str = "", engine: str = "kokoro",
                            voice_id: str = "", language: str = "",
                            description: str = "", personality: str = "") -> dict:
        self.ensure_connected()
        profile_name = str(name or "").strip()
        preset_engine = str(engine or "kokoro").strip() or "kokoro"
        preset = self._preset_voice_for_profile(profile_name, preset_engine, voice_id)
        preset_voice_id = str(voice_id or preset.get("voice_id") or "").strip()
        if not profile_name:
            profile_name = str(preset.get("name") or preset_voice_id).strip()
        if not profile_name:
            raise ServiceError("profile_name is required")
        if not preset_voice_id:
            raise ServiceError(
                f"No preset voice matched {profile_name!r} for engine {preset_engine!r}")
        profile_language = str(language or preset.get("language") or "en").strip() or "en"
        body = {
            "name": profile_name,
            "description": description or f"{preset_engine} preset voice {preset_voice_id}",
            "language": profile_language,
            "voice_type": "preset",
            "preset_engine": preset_engine,
            "preset_voice_id": preset_voice_id,
            "default_engine": preset_engine,
        }
        if personality:
            body["personality"] = personality
        existing_id = ""
        for item in self.list_profiles():
            if not isinstance(item, dict):
                continue
            if profile_name in {str(item.get("id") or ""), str(item.get("name") or "")}:
                existing_id = str(item.get("id") or "")
                break
            if preset_voice_id and preset_voice_id == str(item.get("preset_voice_id") or ""):
                existing_id = str(item.get("id") or "")
                break
        path = f"/profiles/{urllib.parse.quote(existing_id)}" if existing_id else "/profiles"
        method = "PUT" if existing_id else "POST"
        return self._request(
            method, path, json.dumps(body).encode("utf-8"),
            {"Content-Type": "application/json"}, accept_json=True)

    def _resolve_profile_id(self, profile: str) -> str:
        """Resolve a Voicebox profile name/id to its internal profile id."""
        value = str(profile or "").strip()
        if not value:
            return ""
        for item in self.list_profiles():
            if not isinstance(item, dict):
                continue
            if value in {str(item.get("id") or ""), str(item.get("name") or "")}:
                return str(item.get("id") or "")
            if value == str(item.get("preset_voice_id") or ""):
                return str(item.get("id") or "")
        try:
            created = self.save_preset_profile(name=value)
        except Exception:
            try:
                created = self.save_preset_profile(voice_id=value)
            except Exception:
                return ""
        return str(created.get("id") or "") if isinstance(created, dict) else ""

    def _wait_for_generation_audio(self, generation_id: str) -> dict:
        deadline = time.time() + self.timeout
        last_status = ""
        while time.time() < deadline:
            gen = self._request(
                "GET", f"/history/{urllib.parse.quote(generation_id)}",
                accept_json=True, allow_404=True)
            if isinstance(gen, dict) and gen:
                last_status = str(gen.get("status") or "")
                if last_status == "failed":
                    raise ServiceError(gen.get("error") or "Voicebox generation failed")
                if last_status == "completed" and gen.get("audio_path"):
                    local_audio = self._read_local_generation_audio(gen)
                    if local_audio:
                        return local_audio
                    audio_bytes, content_type = self._request(
                        "GET", f"/audio/{urllib.parse.quote(generation_id)}",
                        accept_json=False)
                    if not audio_bytes:
                        raise ServiceError("Voicebox returned empty audio")
                    return {
                        "audio_bytes": audio_bytes,
                        "content_type": content_type or "audio/wav",
                        "source_url": "",
                    }
            time.sleep(1)
        detail = self._active_download_detail("")
        raise ServiceError(
            f"Voicebox generation {generation_id} did not finish within {self.timeout}s"
            + (f"; last status: {last_status}" if last_status else "")
            + detail)

    def _read_local_generation_audio(self, gen: dict) -> dict:
        raw = str(gen.get("audio_path") or "").strip()
        if not raw:
            return {}
        path = Path(raw)
        candidates = []
        if path.is_absolute():
            candidates.append(path)
        else:
            candidates.extend([
                Path.cwd() / path,
                Path.cwd() / "data" / path,
                self.install_dir / path,
            ])
            for prefix in ("runtime/voicebox/", "data/runtime/voicebox/"):
                if raw.startswith(prefix):
                    candidates.append(self.install_dir / raw[len(prefix):])
        for candidate in candidates:
            try:
                if candidate.exists() and candidate.is_file():
                    content_type = mimetypes.guess_type(candidate.name)[0] or "audio/wav"
                    return {
                        "audio_bytes": candidate.read_bytes(),
                        "content_type": content_type,
                        "source_url": "",
                    }
            except OSError:
                logger.debug("Could not read Voicebox audio file %s", candidate, exc_info=True)
        return {}

    def _stream_speech(self, payload: Dict[str, Any], profile_id: str) -> dict:
        stream_payload: Dict[str, Any] = {
            "profile_id": profile_id,
            "text": payload["text"],
            "language": payload.get("language") or "en",
        }
        for source_key, target_key in (
                ("engine", "engine"),
                ("model_size", "model_size"),
                ("seed", "seed"),
                ("instruct", "instruct"),
                ("max_chunk_chars", "max_chunk_chars"),
                ("crossfade_ms", "crossfade_ms"),
                ("normalize", "normalize"),
                ("effects_chain", "effects_chain")):
            value = payload.get(source_key)
            if value is not None and value != "":
                stream_payload[target_key] = value
        body = json.dumps(stream_payload).encode("utf-8")
        audio_bytes, content_type = self._request(
            "POST", "/generate/stream", body,
            {"Content-Type": "application/json", "Accept": "audio/*"},
            accept_json=False)
        if not audio_bytes:
            raise ServiceError("Voicebox returned empty audio")
        return {
            "audio_bytes": audio_bytes,
            "content_type": content_type or "audio/wav",
            "source_url": "",
        }

    def speak(self, text: str, voice: str = "", language: str = "",
              profile: str = "", personality: bool = False, **kwargs) -> dict:
        if not text:
            raise ServiceError("text is required")
        self.ensure_connected()
        payload: Dict[str, Any] = {
            "text": text,
            "profile": profile or voice or self.default_profile,
        }
        if language:
            payload["language"] = language
        if personality:
            payload["personality"] = True
        for key, value in kwargs.items():
            if key in {"destination", "path", "service", "audio_service", "voice_service"}:
                continue
            if value is not None and value != "":
                payload[key] = value
        if not personality and payload.get("profile"):
            profile_id = self._resolve_profile_id(str(payload["profile"]))
            if profile_id:
                payload["profile"] = profile_id
        active_error = self._active_download_error(str(payload.get("engine") or ""))
        if active_error:
            raise ServiceError(
                f"Voicebox {active_error}. Clear Voicebox tasks and retry the model download.")
        body = json.dumps(payload).encode("utf-8")
        audio_bytes, content_type = self._request(
            "POST", "/speak", body,
            {"Content-Type": "application/json", "Accept": "audio/*"},
            accept_json=False)
        if "json" in (content_type or "").lower():
            try:
                data = json.loads(audio_bytes.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise ServiceError("Voicebox returned invalid JSON from /speak") from exc
            b64 = data.get("audio_base64") or data.get("audio") or ""
            if b64:
                audio_bytes = base64.b64decode(str(b64).split(",", 1)[-1])
                content_type = data.get("content_type") or "audio/mpeg"
            elif data.get("url") or data.get("audio_url"):
                url = str(data.get("url") or data.get("audio_url"))
                with urllib.request.urlopen(url, timeout=self.timeout) as resp:  # nosec B310 - provider-returned local/file URL.
                    audio_bytes = resp.read()
                    content_type = resp.headers.get("Content-Type", "audio/mpeg")
            elif data.get("id") and str(data.get("status") or "") in {"generating", "loading_model", "completed"}:
                return self._wait_for_generation_audio(str(data["id"]))
            elif data.get("profile_id") and not personality:
                return self._stream_speech(payload, str(data["profile_id"]))
            else:
                raise ServiceError("Voicebox /speak returned JSON without audio")
        if not audio_bytes:
            raise ServiceError("Voicebox returned empty audio")
        return {
            "audio_bytes": audio_bytes,
            "content_type": content_type or "audio/mpeg",
            "source_url": "",
        }

    def clone_speak(self, text: str, reference_audio_url: str = "",
                    reference_text: str = "", language: str = "",
                    voice_id: str = "", profile: str = "", **kwargs) -> dict:
        return self.speak(
            text=text,
            voice=voice_id or profile or self.default_profile,
            language=language,
            **kwargs,
        )


ServiceFactory.register(VoiceboxService)

