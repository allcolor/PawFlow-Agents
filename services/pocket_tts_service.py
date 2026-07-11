"""Managed local Kyutai Pocket TTS service.

The service owns a local Pocket TTS HTTP daemon. Creating/connecting the
PawFlow service stays lazy, and the first synthesis starts the daemon when
needed, then calls its native ``POST /tts`` multipart endpoint.
"""

import logging
import mimetypes
import os
import shutil
import subprocess  # nosec B404 - managed local daemon uses explicit argv with shell=False.
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict

from core import ServiceFactory, ServiceError
from core.relay_proxy_url import (
    CONV_RELAY_EXPR, is_relay_proxy_url, relay_proxy_ssl_context,
    resolve_relay_aware_url)
from core.service_install import (
    assert_requirements,
    python_venv_requirement,
    run_checked,
    write_install_state,
)
from services.base_audio_generation import BaseAudioGenerationService
from services.base_tts import BaseTTSService

logger = logging.getLogger(__name__)

_CONTENT_TYPE = "audio/wav"
_BOUNDARY = "----PawFlowPocketTTSBoundary"
_DEFAULT_PACKAGE = "pocket-tts[audio]>=2.1.0"


def _raw_config(config: dict, key: str, default=""):
    try:
        return dict.__getitem__(config, key)
    except KeyError:
        return default


class PocketTTSService(BaseAudioGenerationService, BaseTTSService):
    TYPE = "pocketTTS"
    VERSION = "1.0.0"
    NAME = "Pocket TTS Local"
    CATEGORY = "audio"
    DESCRIPTION = (
        "Generate CPU-friendly local speech through Kyutai Pocket TTS, "
        "with built-in voices and per-call reference audio cloning."
    )

    def get_parameter_schema(self) -> dict:
        return {
            "base_url": {
                "type": "string", "required": False,
                "default": "http://127.0.0.1:8000",
                "description": f"Pocket TTS server base URL. Use relay://{CONV_RELAY_EXPR}/localhost:8000 for a relay-routed user endpoint.",
            },
            "allow_private_base_url": {
                "type": "boolean", "required": False, "default": False,
                "description": "Allow direct private/loopback base_url targets when auto_start is false. Prefer relay URLs for user-local endpoints.",
            },
            "allow_remote_voice_urls": {
                "type": "boolean", "required": False, "default": False,
                "description": "Allow Pocket TTS to fetch HTTP(S) voice_url values. Disabled by default to avoid local-daemon SSRF.",
            },
            "auto_start": {
                "type": "boolean", "required": False, "default": True,
                "description": "Start the local Pocket TTS daemon automatically when first used.",
            },
            "auto_install": {
                "type": "boolean", "required": False, "default": True,
                "description": "Prepare a managed Pocket TTS runtime during service installation.",
            },
            "install_dir": {
                "type": "string", "required": False, "default": "data/runtime/pocket-tts",
                "description": "Managed Pocket TTS runtime directory.",
            },
            "package_spec": {
                "type": "string", "required": False, "default": _DEFAULT_PACKAGE,
                "description": "pip package spec installed into the managed runtime.",
            },
            "start_command": {
                "type": "string", "required": False, "default": "",
                "description": "Optional explicit command to start an existing Pocket TTS daemon.",
            },
            "startup_timeout": {
                "type": "integer", "required": False, "default": 120,
                "description": "Seconds to wait for the managed daemon to become reachable.",
            },
            "language": {
                "type": "select", "required": False, "default": "english",
                "options": ["english", "english_2026-01", "english_2026-04", "french_24l", "german_24l", "portuguese_24l", "italian_24l", "spanish_24l"],
                "description": "Pocket TTS model language loaded by the daemon.",
            },
            "voice": {
                "type": "string", "required": False, "default": "alba",
                "description": "Built-in voice name, hf:// voice URL, http(s) voice URL, or local voice file path.",
            },
            "quantize": {
                "type": "boolean", "required": False, "default": False,
                "description": "Start Pocket TTS with int8 quantization enabled.",
            },
            "timeout": {
                "type": "integer", "required": False, "default": 180,
                "description": "HTTP timeout in seconds.",
            },
        }

    def __init__(self, config):
        super().__init__(config)
        self.base_url = str(
            _raw_config(self.config, "base_url", "http://127.0.0.1:8000") or "http://127.0.0.1:8000").rstrip("/")
        self._raw_base_url = self.base_url
        self.allow_private_base_url = str(
            self.config.get("allow_private_base_url", False)).lower() in {"1", "true", "yes", "on"}
        self.allow_remote_voice_urls = str(
            self.config.get("allow_remote_voice_urls", False)).lower() in {"1", "true", "yes", "on"}
        self._runtime_user_id = ""
        self._runtime_conversation_id = ""
        self._runtime_agent_name = ""
        self.voice = str(self.config.get("voice") or "alba")
        self.language = str(self.config.get("language") or self.config.get("lang") or "english")
        self.quantize = str(self.config.get("quantize", False)).lower() in {"1", "true", "yes", "on"}
        self.timeout = int(self.config.get("timeout") or 180)
        self.auto_start = str(self.config.get("auto_start", True)).lower() not in {"0", "false", "no"}
        self.auto_install = str(self.config.get("auto_install", True)).lower() not in {"0", "false", "no"}
        install_dir = Path(str(self.config.get("install_dir") or "data/runtime/pocket-tts")).expanduser()
        if not install_dir.is_absolute():
            install_dir = Path.cwd() / install_dir
        self.install_dir = install_dir
        self.package_spec = str(self.config.get("package_spec") or _DEFAULT_PACKAGE)
        self.start_command = str(self.config.get("start_command") or "").strip()
        self.startup_timeout = int(self.config.get("startup_timeout") or 120)
        self._managed_proc = None
        self._managed_log_path = self.install_dir / "pocket-tts-pawflow.log"
        self._connect_lock = threading.Lock()
        self._warmup_done = False

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
            service_name="Pocket TTS",
            transform_relay=True,
        )

    def get_install_requirements(self):
        if self.start_command or not self.auto_install:
            return []
        return [python_venv_requirement()]

    def prepare_install(self, reporter=None):
        if reporter:
            reporter.step("checking_requirements", "Checking Pocket TTS requirements")
        reqs = self.get_install_requirements()
        assert_requirements(reqs)
        if self.auto_install and not self.start_command:
            self._ensure_runtime(reporter)
        write_install_state(self.install_dir / ".pawflow_install.json", {
            "service_type": self.TYPE,
            "package_spec": self.package_spec,
            "language": self.language,
            "quantize": self.quantize,
        })
        return {"prepared": True, "requirements": reqs, "install_dir": str(self.install_dir)}

    def connect(self):
        parsed = self._validate_endpoint()
        self._connection = {"base_url": self.base_url, "managed": False,
                            "process": None, "lazy": True, "parsed": parsed}
        self._initialized = True
        self._log_connection("Connected lazily")

    def _validate_endpoint(self):
        resolved = resolve_relay_aware_url(
            self._raw_base_url,
            allow_private=self.allow_private_base_url or self.auto_start,
            service_name="Pocket TTS",
            transform_relay=False,
        )
        self.base_url = resolved
        if self.auto_start and is_relay_proxy_url(resolved):
            raise ServiceError("auto_start requires a loopback Pocket TTS base_url")
        parsed = urllib.parse.urlparse(self.base_url)
        if parsed.hostname not in {"127.0.0.1", "localhost", "::1"} and self.auto_start:
            raise ServiceError("auto_start requires a loopback Pocket TTS base_url")
        return parsed

    def _create_connection(self):
        parsed = self._validate_endpoint()
        if self._server_ready():
            return {"base_url": self.base_url, "managed": False, "process": None}
        if not self.auto_start:
            raise ServiceError(f"Pocket TTS server unavailable at {self.base_url}")
        proc = self._start_server(parsed)
        self._wait_ready(proc)
        self._managed_proc = proc
        return {"base_url": self.base_url, "managed": True, "process": proc}

    def _venv_python(self) -> Path:
        return self.install_dir / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    def _resolve_start_command(self):
        if self.start_command:
            import shlex
            return shlex.split(self.start_command)
        venv_python = self._venv_python()
        if not venv_python.exists():
            raise ServiceError(
                "Pocket TTS runtime is not prepared. Install the service again "
                "or configure start_command for an existing Pocket TTS daemon.")
        return [
            str(venv_python),
            "-c",
            "from pocket_tts.main import cli_app; cli_app()",
            "serve",
        ]

    def _ensure_runtime(self, reporter=None):
        python = self._venv_python()
        if python.exists():
            self._ensure_serve_runtime(python, reporter=reporter)
            if reporter:
                reporter.step("validating", "Pocket TTS managed runtime already exists")
            return
        self.install_dir.mkdir(parents=True, exist_ok=True)
        if reporter:
            reporter.step("creating_venv", "Creating Pocket TTS Python virtual environment")
        system_python = sys.executable or shutil.which("python3") or shutil.which("python")
        run_checked([system_python, "-m", "venv", str(self.install_dir / "venv")], reporter=reporter, phase="creating_venv")
        run_checked([str(python), "-m", "pip", "install", "--upgrade", "pip", "-q"], reporter=reporter, phase="installing_python_requirements")
        run_checked([str(python), "-m", "pip", "install", self.package_spec], reporter=reporter, phase="installing_python_requirements")
        self._ensure_serve_runtime(python, reporter=reporter)

    def _ensure_serve_runtime(self, python: Path, reporter=None):
        probe = "import fastapi, uvicorn; from pocket_tts.main import cli_app"
        try:
            if subprocess.run(  # nosec B603 - Python executable path is from the managed venv.
                    [str(python), "-c", probe],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False).returncode == 0:
                return
        except OSError:
            pass
        if reporter:
            reporter.step("installing_python_requirements", "Installing Pocket TTS server runtime")
        run_checked([str(python), "-m", "pip", "install", self.package_spec], reporter=reporter, phase="installing_python_requirements")

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
        try:
            url = self._effective_base_url() + "/health"
        except Exception:
            return False
        req = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=min(5, self.timeout), context=relay_proxy_ssl_context(url)) as resp:  # nosec B310 - configured local/HTTP TTS endpoint.
                return resp.status < 500
        except urllib.error.HTTPError as exc:
            return exc.code < 500
        except Exception:
            return False

    def _start_server(self, parsed) -> subprocess.Popen:
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "https" else 8000)
        if not self.start_command and self.auto_start and self.auto_install:
            self._ensure_runtime()
        cmd = self._resolve_start_command() + ["--host", host, "--port", str(port), "--language", self.language]
        if self.quantize:
            cmd.append("--quantize")
        env = os.environ.copy()
        logger.info("[POCKET_TTS] starting managed daemon: %s", " ".join(cmd))
        self._managed_log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = self._managed_log_path.open("ab")
        try:
            return subprocess.Popen(  # nosec B603 - argv is constructed locally and shell=False is the default.
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                env=env,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            raise ServiceError(f"python executable not found for Pocket TTS: {sys.executable}") from exc

    def _wait_ready(self, proc: subprocess.Popen):
        deadline = time.time() + self.startup_timeout
        while time.time() < deadline:
            if proc.poll() is not None:
                detail = ""
                try:
                    if self._managed_log_path.exists():
                        detail = self._managed_log_path.read_text(
                            encoding="utf-8", errors="replace")[-4000:].strip()
                except Exception:
                    detail = ""
                raise ServiceError(f"Pocket TTS daemon exited during startup: {detail}")
            if self._server_ready():
                return
            time.sleep(0.5)
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
        raise ServiceError(
            f"Pocket TTS daemon did not become ready at {self.base_url} within {self.startup_timeout}s")

    @staticmethod
    def _multipart(fields: Dict[str, str], files: Dict[str, tuple[str, bytes, str]] = None) -> tuple[bytes, str]:
        chunks: list[bytes] = []
        boundary = _BOUNDARY
        for name, value in fields.items():
            chunks.extend([
                f"--{boundary}\r\n".encode("ascii"),
                f"Content-Disposition: form-data; name=\"{name}\"\r\n\r\n".encode("ascii"),
                str(value).encode("utf-8"),
                b"\r\n",
            ])
        for name, (filename, data, content_type) in (files or {}).items():
            chunks.extend([
                f"--{boundary}\r\n".encode("ascii"),
                f"Content-Disposition: form-data; name=\"{name}\"; filename=\"{filename}\"\r\n".encode("utf-8"),
                f"Content-Type: {content_type or 'application/octet-stream'}\r\n\r\n".encode("ascii"),
                data,
                b"\r\n",
            ])
        chunks.append(f"--{boundary}--\r\n".encode("ascii"))
        return b"".join(chunks), f"multipart/form-data; boundary={boundary}"

    def _reference_file(self, reference_audio_url: str = "",
                        reference_audio_bytes: bytes = None):
        if reference_audio_bytes:
            return "reference.wav", reference_audio_bytes, "audio/wav", None
        if not reference_audio_url:
            return None
        if reference_audio_url.startswith(("http://", "https://", "hf://")):
            return None
        path = Path(reference_audio_url).expanduser()
        data = path.read_bytes()
        return path.name or "reference.wav", data, mimetypes.guess_type(str(path))[0] or "audio/wav", None

    def _validate_voice_url(self, voice_url: str) -> str:
        voice_url = str(voice_url or "").strip()
        if voice_url.startswith(("http://", "https://")) and not self.allow_remote_voice_urls:
            raise ServiceError(
                "Pocket TTS remote HTTP(S) voice_url values require allow_remote_voice_urls=true")
        return voice_url

    def _post_tts(self, text: str, voice: str = "",
                  reference_audio_url: str = "", reference_audio_bytes: bytes = None) -> tuple[bytes, str]:
        url = f"{self._effective_base_url()}/tts"
        fields = {"text": text}
        files = {}
        ref_file = self._reference_file(reference_audio_url, reference_audio_bytes)
        if ref_file:
            filename, data, content_type, _tmp_path = ref_file
            files["voice_wav"] = (filename, data, content_type)
        else:
            fields["voice_url"] = self._validate_voice_url(reference_audio_url or voice or self.voice)
        body, content_type = self._multipart(fields, files)
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": content_type,
                "Accept": "audio/wav",
                "User-Agent": "PawFlow-Agent/1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=relay_proxy_ssl_context(url)) as resp:  # nosec B310 - configured local/HTTP TTS endpoint.
                return resp.read(), resp.headers.get("Content-Type", _CONTENT_TYPE)
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:500].decode("utf-8", errors="replace")
            raise ServiceError(
                f"Pocket TTS API error POST /tts ({exc.code}): {detail}") from exc
        except urllib.error.URLError as exc:
            raise ServiceError(f"Pocket TTS server unavailable at {url}: {exc}") from exc

    def text_to_speech(self, text: str = "", voice: str = "",
                       language: str = "", reference_audio_url: str = "",
                       reference_audio_bytes: bytes = None, **kwargs) -> dict:
        del language, kwargs
        if not text:
            raise ServiceError("No text provided")
        self.ensure_connected()
        audio_bytes, content_type = self._post_tts(
            text, voice=voice, reference_audio_url=reference_audio_url,
            reference_audio_bytes=reference_audio_bytes)
        if not audio_bytes:
            raise ServiceError("Pocket TTS returned empty audio")
        logger.info("[POCKET_TTS] tts ok: %d bytes (%s)", len(audio_bytes), content_type)
        return {"audio_bytes": audio_bytes, "content_type": content_type or _CONTENT_TYPE, "source_url": ""}

    def generate(self, prompt: str = "", text: str = "", voice: str = "",
                 **kwargs) -> dict:
        return self.text_to_speech(text=text or prompt, voice=voice, **kwargs)

    def warmup(self, voice: str = "", language: str = "", **_kwargs) -> None:
        if self._warmup_done:
            return
        self.text_to_speech(text="OK", voice=voice, language=language)
        self._warmup_done = True

    def speak(self, text: str, voice: str = "", language: str = "",
              **kwargs) -> dict:
        return self.text_to_speech(text=text, voice=voice, language=language, **kwargs)


ServiceFactory.register(PocketTTSService)

