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
from core.service_install import (
    assert_requirements,
    executable_requirement,
    python_venv_requirement,
    write_install_state,
)
from services.base_stt import BaseSTTService
from services.base_voice_clone import BaseVoiceCloneService

logger = logging.getLogger(__name__)

_VOICEBOX_DEFAULT_REPO_URL = "https://github.com/jamiepine/voicebox.git"
_VOICEBOX_DEFAULT_REPO_REF = "b35b90961d5bc83a8b4e96e8b6ccde2a03152ff9"


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


def _wsl_unc_to_linux_path(path: Path) -> tuple[str, str] | None:
    text = str(path).replace("\\", "/")
    for prefix in ("//wsl$/", "//wsl.localhost/"):
        if not text.lower().startswith(prefix):
            continue
        rest = text[len(prefix):]
        distro, _, linux_path = rest.partition("/")
        if not distro or not linux_path:
            return None
        return distro, "/" + linux_path
    return None


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
                "type": "string", "required": False, "default": "turbo",
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

    def __init__(self, config):
        super().__init__(config)
        self.base_url = str(config.get("base_url") or "http://127.0.0.1:17493").rstrip("/")
        self.client_id = str(config.get("client_id") or "pawflow")
        self.stt_model = self._normalize_stt_model(str(config.get("stt_model") or "turbo"))
        self.default_profile = str(config.get("default_profile") or "")
        self.timeout = int(config.get("timeout") or 180)
        self.auto_start = str(config.get("auto_start", True)).lower() not in {"0", "false", "no"}
        self.auto_install = str(config.get("auto_install", True)).lower() not in {"0", "false", "no"}
        install_dir = Path(str(config.get("install_dir") or "data/runtime/voicebox")).expanduser()
        if not install_dir.is_absolute():
            install_dir = Path.cwd() / install_dir
        self.install_dir = install_dir
        self.repo_url = str(config.get("repo_url") or _VOICEBOX_DEFAULT_REPO_URL)
        self.repo_ref = str(config.get("repo_ref") or _VOICEBOX_DEFAULT_REPO_REF)
        self.start_command = str(config.get("start_command") or "").strip()
        self.startup_timeout = int(config.get("startup_timeout") or 180)
        self.preload_stt_model = str(config.get("preload_stt_model", True)).lower() not in {"0", "false", "no"}
        self.preload_timeout = int(config.get("preload_timeout") or 1800)
        self._managed_proc = None

    def get_install_requirements(self):
        if self.start_command:
            return []
        wsl_target = _wsl_unc_to_linux_path(self.install_dir)
        if wsl_target:
            distro, _linux_repo = wsl_target
            reqs = [executable_requirement("wsl.exe", install_name="wsl.exe")]
            if reqs[0]["ok"] and not self._wsl_command_succeeds(
                    distro, "command -v git >/dev/null && command -v python3 >/dev/null && python3 -m venv --help >/dev/null"):
                reqs.append({
                    "name": "WSL git/python3-venv",
                    "type": "wsl_packages",
                    "required": True,
                    "ok": False,
                    "detail": "install git, python3, python3-venv inside the WSL distro",
                    "install": {"ubuntu": "sudo apt install -y git python3 python3-venv"},
                })
            return reqs
        return [
            executable_requirement("git"),
            python_venv_requirement(),
            executable_requirement("ffmpeg", required=False),
        ]

    def prepare_install(self, reporter=None):
        if reporter:
            reporter.step("checking_requirements", "Checking Voicebox requirements")
        reqs = self.get_install_requirements()
        assert_requirements(reqs)
        if reporter:
            reporter.step("preparing_runtime", "Preparing Voicebox checkout and Python environment")
        if not self.start_command:
            self._ensure_checkout(reporter=reporter)
        if self.preload_stt_model and self.auto_start:
            try:
                if reporter:
                    reporter.step("starting", "Starting Voicebox for model preload")
                if not self._server_ready():
                    self._start_server()
                self._preload_stt_model(reporter)
            finally:
                self._close_connection()
        write_install_state(self.install_dir / ".pawflow_install.json", {
            "service_type": self.TYPE,
            "repo_url": self.repo_url,
            "repo_ref": self.repo_ref,
            "stt_model": self.stt_model,
            "preload_stt_model": self.preload_stt_model,
        })
        return {"prepared": True, "requirements": reqs, "install_dir": str(self.install_dir)}

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

    def _normalize_stt_model(self, model: str) -> str:
        value = str(model or "").strip()
        if value.startswith("whisper-"):
            value = value[len("whisper-"):]
        return value or "turbo"

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
        if not cmd:
            raise ServiceError(
                "Voicebox runtime is not prepared and no start_command is configured. "
                "Run /service install for this service again, or set start_command to an existing backend.")
        logger.info("[VOICEBOX] starting managed backend: %s", " ".join(cmd))
        env = os.environ.copy()
        env.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
        self._managed_proc = subprocess.Popen(  # nosec B603 - managed backend argv is resolved locally and shell=False is the default.
            cmd,
            cwd=str(cwd) if cwd else None,
            env=env,
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
        wsl_target = _wsl_unc_to_linux_path(repo)
        if wsl_target:
            distro, linux_repo = wsl_target
            linux_python = linux_repo.rstrip("/") + "/backend/venv/bin/python"
            if self._wsl_command_succeeds(
                    distro,
                    f"test -x {shlex.quote(linux_python)}"):
                script = (
                    f"cd {shlex.quote(linux_repo)} && "
                    "export HF_HUB_DISABLE_PROGRESS_BARS=1 && "
                    f"exec {shlex.quote(linux_python)} -m uvicorn backend.main:app "
                    f"--host {shlex.quote(host)} --port {shlex.quote(port)}"
                )
                return self._wsl_argv(distro, script), None
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

    def _ensure_checkout(self, reporter=None):
        repo = self.install_dir
        wsl_target = _wsl_unc_to_linux_path(repo)
        if wsl_target:
            self._ensure_checkout_wsl(wsl_target, reporter=reporter)
            return
        git = shutil.which("git")
        if not repo.exists():
            if reporter:
                reporter.step("cloning", "Cloning Voicebox repository")
            repo.parent.mkdir(parents=True, exist_ok=True)
            if not git:
                raise ServiceError("git is required to auto-install Voicebox")
            subprocess.check_call([  # nosec B603 - fixed git clone argv for the managed Voicebox checkout.
                git, "clone", "--no-checkout", self.repo_url, str(repo),
            ])
        backend = repo / "backend"
        if not backend.exists():
            if not (repo / ".git").exists():
                raise ServiceError(f"Voicebox checkout is incomplete: {repo}")
            if not git:
                raise ServiceError("git is required to finish the Voicebox checkout")
            checkout_ref = self.repo_ref or "HEAD"
            if reporter:
                reporter.step("checkout", f"Checking out Voicebox ref {checkout_ref}")
            subprocess.check_call([  # nosec B603 - managed checkout argv without shell; safe.directory is scoped to this git process.
                git, "-c", "safe.directory=*", "-C", str(repo),
                "checkout", "--detach", checkout_ref,
            ])
        python = repo / "backend" / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if python.exists():
            return
        just = shutil.which("just")
        if just:
            if reporter:
                reporter.step("installing_python_requirements", "Running Voicebox just setup-python")
            subprocess.check_call([just, "setup-python"], cwd=str(repo))  # nosec B603 - local setup tool argv without shell.
            return
        backend = repo / "backend"
        if not backend.exists():
            raise ServiceError(f"Voicebox checkout is incomplete: {repo}")
        system_python = sys.executable or shutil.which("python3") or shutil.which("python")
        if reporter:
            reporter.step("creating_venv", "Creating Voicebox Python virtual environment")
        subprocess.check_call([system_python, "-m", "venv", str(backend / "venv")])  # nosec B603 - Python argv without shell.
        if reporter:
            reporter.step("installing_python_requirements", "Installing Voicebox Python requirements")
        subprocess.check_call([str(python), "-m", "pip", "install", "--upgrade", "pip", "-q"])  # nosec B603 - venv pip argv without shell.
        subprocess.check_call([str(python), "-m", "pip", "install", "-r", str(backend / "requirements.txt")])  # nosec B603 - requirements path is inside managed checkout.

    def _wsl_argv(self, distro: str, script: str) -> list[str]:
        wsl = shutil.which("wsl.exe") or shutil.which("wsl")
        if not wsl:
            raise ServiceError("wsl.exe is required to manage Voicebox from a WSL UNC checkout")
        return [wsl, "-d", distro, "--", "bash", "-lc", script]

    def _wsl_command_succeeds(self, distro: str, script: str) -> bool:
        try:
            return subprocess.run(  # nosec B603 - wsl.exe argv is fixed; script arguments are shell-quoted by callers.
                self._wsl_argv(distro, script),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            ).returncode == 0
        except Exception:
            return False

    def _ensure_checkout_wsl(self, target: tuple[str, str], reporter=None):
        repo = self.install_dir
        distro, linux_repo = target
        linux_parent = linux_repo.rstrip("/").rsplit("/", 1)[0] or "/"
        if not repo.exists():
            if reporter:
                reporter.step("cloning", "Cloning Voicebox repository inside WSL")
            subprocess.check_call(self._wsl_argv(  # nosec B603 - wsl.exe argv is fixed; script arguments are shell-quoted.
                distro,
                " && ".join([
                    f"mkdir -p {shlex.quote(linux_parent)}",
                    "git clone --no-checkout "
                    f"{shlex.quote(self.repo_url)} {shlex.quote(linux_repo)}",
                ]),
            ))
        backend = repo / "backend"
        if not backend.exists():
            if not (repo / ".git").exists():
                raise ServiceError(f"Voicebox checkout is incomplete: {repo}")
            checkout_ref = self.repo_ref or "HEAD"
            if reporter:
                reporter.step("checkout", f"Checking out Voicebox ref {checkout_ref} inside WSL")
            subprocess.check_call(self._wsl_argv(  # nosec B603 - wsl.exe argv is fixed; script arguments are shell-quoted.
                distro,
                f"git -C {shlex.quote(linux_repo)} checkout --detach {shlex.quote(checkout_ref)}",
            ))
        linux_python = linux_repo.rstrip("/") + "/backend/venv/bin/python"
        if self._wsl_command_succeeds(
                distro,
                f"test -x {shlex.quote(linux_python)} && "
                f"{shlex.quote(linux_python)} -m pip --version >/dev/null"):
            return
        if not backend.exists():
            raise ServiceError(f"Voicebox checkout is incomplete: {repo}")
        if reporter:
            reporter.step("installing_python_requirements", "Installing Voicebox Python requirements inside WSL")
        subprocess.check_call(self._wsl_argv(  # nosec B603 - wsl.exe argv is fixed; script arguments are shell-quoted.
            distro,
            "\n".join([
                "set -e",
                f"cd {shlex.quote(linux_repo)}",
                "if command -v just >/dev/null 2>&1; then",
                "  just setup-python",
                "else",
                "  python3 -m venv backend/venv",
                "  backend/venv/bin/python -m pip install --upgrade pip -q",
                "  backend/venv/bin/python -m pip install -r backend/requirements.txt",
                "fi",
            ]),
        ))

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

    def _voicebox_model_name(self) -> str:
        model = self._normalize_stt_model(self.stt_model)
        return model if model.startswith("whisper-") else f"whisper-{model}"

    def _model_downloaded(self, model_name: str) -> bool:
        try:
            data = self._request("GET", "/models/status", accept_json=True)
        except Exception:
            return False
        for item in data.get("models", []) if isinstance(data, dict) else []:
            if item.get("model_name") == model_name:
                return bool(item.get("downloaded")) and not bool(item.get("downloading"))
        return False

    def _preload_stt_model(self, reporter=None):
        model_name = self._voicebox_model_name()
        if self._model_downloaded(model_name):
            if reporter:
                reporter.step("downloading_models", f"Voicebox model {model_name} is already cached", progress=1.0)
            return
        if reporter:
            reporter.step("downloading_models", f"Downloading Voicebox model {model_name}", progress=0.0)
        body = json.dumps({"model_name": model_name}).encode("utf-8")
        self._request(
            "POST", "/models/download", body,
            {"Content-Type": "application/json"}, accept_json=True)
        deadline = time.time() + self.preload_timeout
        while time.time() < deadline:
            if self._model_downloaded(model_name):
                if reporter:
                    reporter.step("downloading_models", f"Voicebox model {model_name} downloaded", progress=1.0)
                return
            detail = self._active_download_detail(model_name)
            if reporter:
                reporter.step("downloading_models", f"Downloading Voicebox model {model_name}{detail}")
            time.sleep(2)
        raise ServiceError(
            f"Voicebox model {model_name} did not finish downloading within {self.preload_timeout}s")

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

    def _active_download_detail(self, model_name: str) -> str:
        try:
            tasks = self._request("GET", "/tasks/active", accept_json=True)
        except Exception:
            return ""
        downloads = tasks.get("downloads", []) if isinstance(tasks, dict) else []
        wanted = str(model_name or "")
        for item in downloads:
            if not isinstance(item, dict) or (wanted and item.get("model_name") != wanted):
                continue
            parts = []
            error = item.get("error")
            if error:
                return f" ({item.get('status') or 'error'}: {error})"
            progress = item.get("progress")
            if isinstance(progress, (int, float)):
                parts.append(f"{progress:.1f}%")
            current = item.get("current")
            total = item.get("total")
            if isinstance(current, int) and isinstance(total, int) and total > 0:
                parts.append(f"{current / 1024 / 1024:.1f}/{total / 1024 / 1024:.1f} MB")
            filename = item.get("filename")
            if filename:
                parts.append(str(filename))
            status = item.get("status")
            if status and not parts:
                parts.append(str(status))
            return " (" + ", ".join(parts) + ")" if parts else ""
        return ""

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

