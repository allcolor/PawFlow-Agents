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
import os
import shlex
import shutil
import subprocess  # nosec B404 - managed local backend commands use explicit argv with shell=False.
import sys
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict

from core import ServiceFactory, ServiceError
from services.base_stt import BaseSTTService
from services.base_voice_clone import BaseVoiceCloneService

logger = logging.getLogger(__name__)


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


class VoiceboxService(BaseVoiceCloneService, BaseSTTService):
    TYPE = "voicebox"
    VERSION = "1.0.0"
    NAME = "Voicebox Local Voice I/O"
    CATEGORY = "audio"
    SUPPORTS_NATIVE_TTS_VOICES = True
    ACCEPTS_FILESTORE_URLS = False

    def get_parameter_schema(self) -> dict:
        return {
            "base_url": {
                "type": "string", "required": False,
                "default": "http://127.0.0.1:17493",
                "description": "Voicebox local HTTP API base URL.",
            },
            "client_id": {
                "type": "string", "required": False, "default": "pawflow",
                "description": "X-Voicebox-Client-Id header used for Voicebox bindings.",
            },
            "stt_model": {
                "type": "string", "required": False, "default": "whisper-turbo",
                "description": "Voicebox Whisper model for transcription.",
            },
            "default_profile": {
                "type": "string", "required": False, "default": "",
                "description": "Default Voicebox profile name or id for speech.",
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
            "start_command": {
                "type": "string", "required": False, "default": "",
                "description": "Optional explicit command to start the Voicebox API backend.",
            },
            "startup_timeout": {
                "type": "integer", "required": False, "default": 180,
                "description": "Seconds to wait for Voicebox to become reachable.",
            },
            "timeout": {
                "type": "integer", "required": False, "default": 180,
                "description": "HTTP timeout in seconds.",
            },
        }

    def __init__(self, config):
        super().__init__(config)
        self.base_url = str(config.get("base_url") or "http://127.0.0.1:17493").rstrip("/")
        self.client_id = str(config.get("client_id") or "pawflow")
        self.stt_model = str(config.get("stt_model") or "whisper-turbo")
        self.default_profile = str(config.get("default_profile") or "")
        self.timeout = int(config.get("timeout") or 180)
        self.auto_start = str(config.get("auto_start", True)).lower() not in {"0", "false", "no"}
        self.auto_install = str(config.get("auto_install", True)).lower() not in {"0", "false", "no"}
        self.install_dir = Path(str(config.get("install_dir") or "data/runtime/voicebox"))
        self.start_command = str(config.get("start_command") or "").strip()
        self.startup_timeout = int(config.get("startup_timeout") or 180)
        self._managed_proc = None

    def _create_connection(self):
        self._validate_endpoint()
        if self._server_ready():
            return {"base_url": self.base_url, "managed": False}
        if not self.auto_start:
            raise ServiceError(f"Voicebox API is unavailable at {self.base_url}")
        self._start_server()
        return {"base_url": self.base_url, "managed": self._managed_proc is not None}

    def _close_connection(self):
        proc = self._managed_proc
        self._managed_proc = None
        if not proc or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)

    def _validate_endpoint(self):
        parsed = urllib.parse.urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ServiceError(f"invalid Voicebox base_url: {self.base_url!r}")
        if self.auto_start and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ServiceError("auto_start requires a loopback Voicebox base_url")
        return parsed

    def _server_ready(self) -> bool:
        for path in ("/health", "/profiles"):
            req = urllib.request.Request(
                self.base_url + path, headers=self._headers(), method="GET")
            try:
                with urllib.request.urlopen(req, timeout=min(3, self.timeout)) as resp:  # nosec B310 - configured local Voicebox URL.
                    if getattr(resp, "status", 200) < 500:
                        return True
            except urllib.error.HTTPError as exc:
                if exc.code < 500 and path == "/profiles":
                    return True
            except Exception as exc:
                logger.debug("Voicebox readiness probe failed for %s: %s", path, exc)
        return False

    def _start_server(self):
        if self._try_open_desktop_app():
            return
        cmd, cwd = self._resolve_start_command()
        if not cmd and self.auto_install:
            self._ensure_checkout()
            cmd, cwd = self._resolve_start_command()
        if not cmd:
            raise ServiceError(
                "Voicebox is not installed and no start_command is configured. "
                "Set start_command or allow auto_install with install_dir.")
        logger.info("[VOICEBOX] starting managed backend: %s", " ".join(cmd))
        self._managed_proc = subprocess.Popen(  # nosec B603 - managed backend argv is resolved locally and shell=False is the default.
            cmd,
            cwd=str(cwd) if cwd else None,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        self._wait_ready(self._managed_proc)

    def _try_open_desktop_app(self) -> bool:
        if sys.platform != "darwin" or self.start_command or self.install_dir.exists():
            return False
        opener = shutil.which("open")
        if not opener:
            return False
        try:
            subprocess.Popen(  # nosec B603 - macOS opener path is resolved with shutil.which and shell=False is the default.
                [opener, "-ga", "Voicebox"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._wait_ready(None)
            return True
        except Exception:
            logger.debug("Voicebox desktop auto-open failed", exc_info=True)
            return False

    def _resolve_start_command(self):
        if self.start_command:
            return shlex.split(self.start_command), None
        parsed = urllib.parse.urlparse(self.base_url)
        host = parsed.hostname or "127.0.0.1"
        port = str(parsed.port or 17493)
        repo = self.install_dir
        candidates = [
            repo / "backend" / "venv" / "bin" / "python",
            repo / "backend" / "venv" / "Scripts" / "python.exe",
        ]
        for python in candidates:
            if python.exists():
                return [
                    str(python), "-m", "uvicorn", "backend.main:app",
                    "--host", host, "--port", port,
                ], repo
        exe = shutil.which("voicebox-server")
        if exe:
            return [exe, "--host", host, "--port", port], None
        return [], None

    def _ensure_checkout(self):
        repo = self.install_dir
        if not repo.exists():
            repo.parent.mkdir(parents=True, exist_ok=True)
            git = shutil.which("git")
            if not git:
                raise ServiceError("git is required to auto-install Voicebox")
            subprocess.check_call([  # nosec B603 - fixed git clone argv for the managed Voicebox checkout.
                git, "clone", "https://github.com/jamiepine/voicebox.git", str(repo),
            ])
        python = repo / "backend" / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if python.exists():
            return
        just = shutil.which("just")
        if just:
            subprocess.check_call([just, "setup-python"], cwd=str(repo))  # nosec B603 - local setup tool argv without shell.
            return
        backend = repo / "backend"
        if not backend.exists():
            raise ServiceError(f"Voicebox checkout is incomplete: {repo}")
        system_python = shutil.which("python3") or shutil.which("python") or sys.executable
        subprocess.check_call([system_python, "-m", "venv", str(backend / "venv")])  # nosec B603 - Python argv without shell.
        subprocess.check_call([str(python), "-m", "pip", "install", "--upgrade", "pip", "-q"])  # nosec B603 - venv pip argv without shell.
        subprocess.check_call([str(python), "-m", "pip", "install", "-r", str(backend / "requirements.txt")])  # nosec B603 - requirements path is inside managed checkout.

    def _wait_ready(self, proc):
        deadline = time.time() + self.startup_timeout
        while time.time() < deadline:
            if self._server_ready():
                return
            if proc is not None and proc.poll() not in {None, 0}:
                detail = ""
                try:
                    detail = (proc.stderr.read(1000) if proc.stderr else b"").decode(
                        "utf-8", errors="replace").strip()
                except Exception:
                    detail = ""
                raise ServiceError(f"Voicebox backend exited during startup: {detail}")
            time.sleep(0.5)
        raise ServiceError(
            f"Voicebox did not become ready at {self.base_url} within {self.startup_timeout}s")

    def _headers(self, extra: Dict[str, str] = None) -> Dict[str, str]:
        headers = {"User-Agent": "PawFlow-Agent/1.0"}
        if self.client_id:
            headers["X-Voicebox-Client-Id"] = self.client_id
        if extra:
            headers.update(extra)
        return headers

    def _request(self, method: str, path: str, data: bytes = None,
                 headers: Dict[str, str] = None, accept_json: bool = False,
                 allow_404: bool = False):
        url = self.base_url + path
        req = urllib.request.Request(
            url, data=data, headers=self._headers(headers), method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # nosec B310 - configured local Voicebox URL.
                body = resp.read()
                ctype = resp.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:500].decode("utf-8", errors="replace")
            if allow_404 and exc.code == 404:
                return {} if accept_json else b""
            raise ServiceError(f"Voicebox API error {method} {path} ({exc.code}): {detail}") from exc
        except urllib.error.URLError as exc:
            raise ServiceError(f"Voicebox unavailable at {self.base_url}: {exc}") from exc
        if accept_json:
            if not body:
                return {}
            try:
                return json.loads(body.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise ServiceError(f"Voicebox returned non-JSON for {path}: {body[:120]!r}") from exc
        return body, ctype

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
            "model": model or self.stt_model,
            "language": language,
            "prompt": prompt,
        }
        body, content_type = _multipart(fields, {
            "audio": (filename, audio_bytes, ctype),
        })
        data = self._request(
            "POST", "/transcribe", body,
            {"Content-Type": content_type}, accept_json=True)
        text = data.get("text") or data.get("transcript") or data.get("result") or ""
        return {
            "text": str(text).strip(),
            "language": data.get("language", language or ""),
            "duration": data.get("duration", 0),
            "segments": data.get("segments", []),
            "provider_result": data,
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

