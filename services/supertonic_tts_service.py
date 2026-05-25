"""Managed local Supertonic TTS service.

The service owns a local Supertonic HTTP daemon. Creating/connecting the
PawFlow service starts the daemon when needed, then calls its native
``POST /v1/tts`` endpoint and returns audio bytes to ``speak`` /
``generate_audio``.
"""

import json
import logging
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict

from core import ServiceFactory, ServiceError, safe_float
from services.base_audio_generation import BaseAudioGenerationService
from services.base_tts import BaseTTSService

logger = logging.getLogger(__name__)

_FORMATS = {"wav", "flac", "ogg"}
_CONTENT_TYPES = {
    "wav": "audio/wav",
    "flac": "audio/flac",
    "ogg": "audio/ogg",
}


class SupertonicTTSService(BaseAudioGenerationService, BaseTTSService):
    TYPE = "supertonicTTS"
    VERSION = "1.0.0"
    NAME = "Supertonic Local TTS"
    CATEGORY = "audio"
    DESCRIPTION = (
        "Generate multilingual speech through a local Supertonic 3 HTTP "
        "daemon managed automatically by PawFlow."
    )

    def get_parameter_schema(self) -> dict:
        return {
            "base_url": {
                "type": "string", "required": False,
                "default": "http://127.0.0.1:7788",
                "description": "Managed local Supertonic server base URL.",
            },
            "auto_start": {
                "type": "boolean", "required": False, "default": True,
                "description": "Start the local Supertonic daemon automatically when the service connects.",
            },
            "startup_timeout": {
                "type": "integer", "required": False, "default": 60,
                "description": "Seconds to wait for the managed daemon to become reachable.",
            },
            "voice": {
                "type": "string", "required": False, "default": "M1",
                "description": "Built-in or imported Supertonic voice name, e.g. M1-M5 or F1-F5.",
            },
            "lang": {
                "type": "string", "required": False, "default": "na",
                "description": "Language code such as en, fr, ja, ko, or na for language-agnostic fallback.",
            },
            "steps": {
                "type": "integer", "required": False, "default": 8,
                "description": "Supertonic quality steps; higher is slower.",
            },
            "speed": {
                "type": "number", "required": False, "default": 1.05,
                "description": "Speech speed, typically 0.7 to 2.0.",
            },
            "response_format": {
                "type": "select", "required": False, "default": "wav",
                "options": sorted(_FORMATS),
                "description": "Output audio format.",
            },
            "timeout": {
                "type": "integer", "required": False, "default": 120,
                "description": "HTTP timeout in seconds.",
            },
        }

    def __init__(self, config):
        super().__init__(config)
        self.base_url = str(
            self.config.get("base_url") or "http://127.0.0.1:7788").rstrip("/")
        self.voice = str(self.config.get("voice") or "M1")
        self.lang = str(self.config.get("lang") or self.config.get("language") or "na")
        self.steps = int(self.config.get("steps") or self.config.get("total_steps") or 8)
        self.speed = safe_float(self.config.get("speed"), 1.05)
        self.response_format = self._format(self.config.get("response_format") or "wav")
        self.timeout = int(self.config.get("timeout") or 120)
        self.auto_start = str(self.config.get("auto_start", True)).lower() not in {"0", "false", "no"}
        self.startup_timeout = int(self.config.get("startup_timeout") or 60)
        self._managed_proc = None
        self._connect_lock = threading.Lock()

    def connect(self):
        """Register the service without starting the heavy local daemon.

        The managed Supertonic process is started lazily by ensure_connected(),
        which is called by the first synth request. This keeps server startup
        fast while still surfacing provider errors at first use.
        """
        parsed = self._validate_endpoint()
        self._connection = {"base_url": self.base_url, "managed": False,
                            "process": None, "lazy": True, "parsed": parsed}
        self._initialized = True
        self._log_connection("Connected lazily")

    def _validate_endpoint(self):
        parsed = urllib.parse.urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ServiceError(f"invalid Supertonic base_url: {self.base_url!r}")
        if parsed.hostname not in {"127.0.0.1", "localhost", "::1"} and self.auto_start:
            raise ServiceError("auto_start requires a loopback Supertonic base_url")
        return parsed

    def _create_connection(self):
        parsed = self._validate_endpoint()
        if self._server_ready():
            return {"base_url": self.base_url, "managed": False, "process": None}
        if not self.auto_start:
            raise ServiceError(f"Supertonic server unavailable at {self.base_url}")
        proc = self._start_server(parsed)
        self._wait_ready(proc)
        self._managed_proc = proc
        return {"base_url": self.base_url, "managed": True, "process": proc}

    def ensure_connected(self):
        with self._connect_lock:
            if not self._initialized or self._connection is None:
                self.connect()
            if self._server_ready():
                return
            self._connection = self._create_connection()
            self._initialized = True

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

    def _server_ready(self) -> bool:
        probe = urllib.request.Request(
            self.base_url + "/v1/tts",
            data=json.dumps({
                "text": "ping",
                "voice": self.voice,
                "lang": self.lang,
                "steps": 1,
                "speed": self.speed,
                "response_format": self.response_format,
            }).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "audio/*"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(probe, timeout=min(5, self.timeout)) as resp:  # nosec B310 - configured local/HTTP TTS endpoint.
                return resp.status < 500
        except urllib.error.HTTPError as exc:
            return exc.code < 500
        except Exception:
            return False

    def _start_server(self, parsed) -> subprocess.Popen:
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "https" else 7788)
        cmd = [
            sys.executable,
            "-c",
            "from supertonic.cli import main; raise SystemExit(main())",
            "serve",
            "--host", host,
            "--port", str(port),
        ]
        env = os.environ.copy()
        logger.info("[SUPERTONIC] starting managed daemon: %s", " ".join(cmd))
        try:
            return subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            raise ServiceError(f"python executable not found for Supertonic: {sys.executable}") from exc

    def _wait_ready(self, proc: subprocess.Popen):
        deadline = time.time() + self.startup_timeout
        last_error = ""
        while time.time() < deadline:
            if proc.poll() is not None:
                stderr = b""
                try:
                    stderr = proc.stderr.read(1000) if proc.stderr else b""
                except Exception:
                    stderr = b""
                detail = stderr.decode("utf-8", errors="replace").strip()
                raise ServiceError(f"Supertonic daemon exited during startup: {detail}")
            if self._server_ready():
                return
            time.sleep(0.5)
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
        raise ServiceError(
            f"Supertonic daemon did not become ready at {self.base_url} within {self.startup_timeout}s{(': ' + last_error) if last_error else ''}")

    @staticmethod
    def _format(value: str) -> str:
        fmt = str(value or "wav").lower().strip()
        if fmt not in _FORMATS:
            raise ServiceError(
                f"unsupported Supertonic response_format {fmt!r}; expected one of {sorted(_FORMATS)}")
        return fmt

    def _post_tts(self, body: Dict[str, Any]) -> tuple[bytes, str]:
        url = f"{self.base_url}/v1/tts"
        payload = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "audio/*",
                "User-Agent": "PawFlow-Agent/1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # nosec B310 - configured local/HTTP TTS endpoint.
                content_type = resp.headers.get(
                    "Content-Type", _CONTENT_TYPES.get(body.get("response_format", "wav"), "audio/wav"))
                return resp.read(), content_type
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:500].decode("utf-8", errors="replace")
            raise ServiceError(
                f"Supertonic API error POST /v1/tts ({exc.code}): {detail}") from exc
        except urllib.error.URLError as exc:
            raise ServiceError(f"Supertonic server unavailable at {self.base_url}: {exc}") from exc

    def generate(self, prompt: str = "", text: str = "", voice: str = "",
                 lang: str = "", language: str = "", steps: int = 0,
                 total_steps: int = 0, speed: float = 0,
                 response_format: str = "", **kwargs) -> dict:
        return self.text_to_speech(
            text=text or prompt,
            voice=voice,
            lang=lang or language,
            steps=steps or total_steps,
            speed=speed,
            response_format=response_format,
            **kwargs,
        )

    def text_to_speech(self, text: str = "", voice: str = "", lang: str = "",
                       language: str = "", steps: int = 0,
                       total_steps: int = 0, speed: float = 0,
                       response_format: str = "", **kwargs) -> dict:
        if not text:
            raise ServiceError("No text provided")
        self.ensure_connected()

        fmt = self._format(response_format or self.response_format)
        body: Dict[str, Any] = {
            "text": text,
            "voice": voice or self.voice,
            "lang": lang or language or self.lang,
            "steps": int(steps or total_steps or self.steps),
            "speed": safe_float(speed, self.speed) if speed else self.speed,
            "response_format": fmt,
        }
        for key, value in kwargs.items():
            if key in body or key in {
                "destination", "path", "service", "audio_service", "_service",
                "model", "prompt", "lyrics", "instrumental", "style",
            }:
                continue
            if value is not None and value != "":
                body[key] = value

        audio_bytes, content_type = self._post_tts(body)
        if not audio_bytes:
            raise ServiceError("Supertonic returned empty audio")
        logger.info("[SUPERTONIC] tts ok: %d bytes (%s)", len(audio_bytes), content_type)
        return {
            "audio_bytes": audio_bytes,
            "content_type": content_type or _CONTENT_TYPES[fmt],
            "source_url": "",
        }

    def speak(self, text: str, voice: str = "", language: str = "",
              **kwargs) -> dict:
        return self.text_to_speech(
            text=text,
            voice=voice,
            language=language,
            **kwargs,
        )


ServiceFactory.register(SupertonicTTSService)
