"""Voicebox local voice I/O service.

Voicebox exposes a local REST API for transcription and speech generation.
This service intentionally treats Voicebox as a provider bridge: PawFlow keeps
its own service selection and chat UI, while Voicebox owns local Whisper/TTS
engines and voice profiles.
"""

import json
import base64
import io
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
import wave
from pathlib import Path
from typing import Any, Dict

from core import ServiceFactory, ServiceError
from core.relay_proxy_url import is_relay_proxy_url, resolve_relay_aware_url
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


def _silent_wav(duration: float = 0.2, sample_rate: int = 16000) -> bytes:
    frames = int(duration * sample_rate)
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"\0\0" * frames)
    return out.getvalue()


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
    ACCEPTS_BROWSER_STT_AUDIO = True

    def get_parameter_schema(self) -> dict:
        return {
            "base_url": {
                "type": "string", "required": False,
                "default": "http://127.0.0.1:17493",
                "description": "Voicebox HTTP API base URL. Use http://${conv.relay}/localhost:17493 for a relay-routed user endpoint.",
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
        self._shutdown_backend()
        if not proc or proc.poll() is not None:
            return
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)

    def _shutdown_backend(self):
        if not self.auto_start:
            return
        parsed = urllib.parse.urlparse(self.base_url)
        if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            return
        req = urllib.request.Request(
            self.base_url + "/shutdown", headers=self._headers(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=min(5, self.timeout)) as resp:  # nosec B310 - configured loopback Voicebox URL.
                resp.read()
        except Exception:
            logger.debug("Voicebox backend shutdown request failed", exc_info=True)

    def _validate_endpoint(self):
        resolved = resolve_relay_aware_url(
            self._raw_base_url,
            allow_private=self.allow_private_base_url or self.auto_start,
            service_name="Voicebox",
            transform_relay=False,
        )
        self.base_url = resolved
        if self.auto_start and is_relay_proxy_url(resolved):
            raise ServiceError("auto_start requires a loopback Voicebox base_url")
        parsed = urllib.parse.urlparse(self.base_url)
        if self.auto_start and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ServiceError("auto_start requires a loopback Voicebox base_url")
        return parsed

    def _normalize_stt_model(self, model: str) -> str:
        value = str(model or "").strip()
        if value.startswith("whisper-"):
            value = value[len("whisper-"):]
        return value or "turbo"

    def _server_ready(self) -> bool:
        try:
            base_url = self._effective_base_url()
        except Exception:
            return False
        for path in ("/health", "/profiles"):
            req = urllib.request.Request(
                base_url + path, headers=self._headers(), method="GET")
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
        self._managed_log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = self._managed_log_path.open("ab")
        self._managed_proc = subprocess.Popen(  # nosec B603 - managed backend argv is resolved locally and shell=False is the default.
            cmd,
            cwd=str(cwd) if cwd else None,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
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
            self._ensure_backend_runner(python, reporter=reporter)
            self._patch_managed_checkout()
            return
        just = shutil.which("just")
        if just:
            if reporter:
                reporter.step("installing_python_requirements", "Running Voicebox just setup-python")
            subprocess.check_call([just, "setup-python"], cwd=str(repo))  # nosec B603 - local setup tool argv without shell.
            self._ensure_backend_runner(python, reporter=reporter)
            self._patch_managed_checkout()
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
        self._ensure_backend_runner(python, reporter=reporter)
        self._patch_managed_checkout()

    def _ensure_backend_runner(self, python: Path, reporter=None):
        """Ensure the managed backend can be launched through uvicorn."""
        try:
            if subprocess.run(  # nosec B603 - Python executable path is from the managed venv.
                    [str(python), "-c", "import uvicorn, fastapi, numpy, torch; import backend.main"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False).returncode == 0:
                return
        except OSError:
            pass
        if reporter:
            reporter.step("installing_python_requirements", "Installing Voicebox backend runtime")
        backend = python.parent.parent.parent
        requirements = backend / "requirements.txt"
        if requirements.exists():
            subprocess.check_call([  # nosec B603 - requirements path is inside the managed backend checkout.
                str(python), "-m", "pip", "install", "-r", str(requirements),
            ])
        subprocess.check_call([  # nosec B603 - fixed pip argv for the managed venv.
            str(python), "-m", "pip", "install",
            "fastapi>=0.110", "numpy>=1.26", "uvicorn[standard]>=0.30",
        ])
        subprocess.check_call([  # nosec B603 - fixed pip argv for the managed venv.
            str(python), "-m", "pip", "install", "torch>=2.3",
            "--index-url", "https://download.pytorch.org/whl/cpu",
        ])

    def _patch_managed_checkout(self):
        """Apply local hardening patches to the managed Voicebox checkout."""
        progress = self.install_dir / "backend" / "utils" / "hf_progress.py"
        if progress.exists():
            text = progress.read_text(encoding="utf-8")
            updated = text.replace(
                'filtered_kwargs["disable"] = False',
                'filtered_kwargs["disable"] = True')
            updated = updated.replace(
                'kwargs["disable"] = False',
                'kwargs["disable"] = True')
            updated = updated.replace(
                'result = super().update(n)\n\n                # Report progress',
                'try:\n                    result = super().update(n)\n                except BrokenPipeError:\n                    result = None\n\n                # Report progress')
            updated = updated.replace(
                'result = tracker._hf_tqdm_original_update(tqdm_self, n)\n\n                        # Track this progress',
                'before = getattr(tqdm_self, "n", 0)\n                        try:\n                            result = tracker._hf_tqdm_original_update(tqdm_self, n)\n                        except BrokenPipeError:\n                            try:\n                                tqdm_self.n = before + n\n                            except Exception:\n                                pass\n                            result = None\n\n                        # Track this progress')
            if updated != text:
                progress.write_text(updated, encoding="utf-8")

    def _patch_managed_checkout_wsl(self, target: tuple[str, str]):
        distro, linux_repo = target
        script = "\n".join([
            "set -e",
            f"cd {shlex.quote(linux_repo)}",
            "python3 - <<'PY'",
            "from pathlib import Path",
            "p = Path('backend/utils/hf_progress.py')",
            "if p.exists():",
            "    text = p.read_text(encoding='utf-8')",
            "    updated = text.replace('filtered_kwargs[\"disable\"] = False', 'filtered_kwargs[\"disable\"] = True')",
            "    updated = updated.replace('kwargs[\"disable\"] = False', 'kwargs[\"disable\"] = True')",
            "    updated = updated.replace('result = super().update(n)\\n\\n                # Report progress', 'try:\\n                    result = super().update(n)\\n                except BrokenPipeError:\\n                    result = None\\n\\n                # Report progress')",
            "    updated = updated.replace('result = tracker._hf_tqdm_original_update(tqdm_self, n)\\n\\n                        # Track this progress', 'before = getattr(tqdm_self, \"n\", 0)\\n                        try:\\n                            result = tracker._hf_tqdm_original_update(tqdm_self, n)\\n                        except BrokenPipeError:\\n                            try:\\n                                tqdm_self.n = before + n\\n                            except Exception:\\n                                pass\\n                            result = None\\n\\n                        # Track this progress')",
            "    if updated != text:",
            "        p.write_text(updated, encoding='utf-8')",
            "PY",
        ])
        subprocess.check_call(self._wsl_argv(distro, script))  # nosec B603 - fixed wsl argv; script applies deterministic local patch.

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
            logger.debug("Voicebox WSL command failed", exc_info=True)
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
            subprocess.check_call(self._wsl_argv(  # nosec B603 - fixed WSL argv; Python path is shell-quoted.
                distro,
                f"{shlex.quote(linux_python)} -c 'import uvicorn, fastapi, numpy, torch; import backend.main' >/dev/null 2>&1 || "
                f"({shlex.quote(linux_python)} -m pip install -r {shlex.quote(linux_repo.rstrip('/') + '/backend/requirements.txt')} && "
                f"{shlex.quote(linux_python)} -m pip install 'fastapi>=0.110' 'numpy>=1.26' 'uvicorn[standard]>=0.30' && "
                f"{shlex.quote(linux_python)} -m pip install 'torch>=2.3' --index-url https://download.pytorch.org/whl/cpu)",
            ))
            self._patch_managed_checkout_wsl(target)
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
                "backend/venv/bin/python -c 'import uvicorn, fastapi, numpy, torch; import backend.main' >/dev/null 2>&1 || (backend/venv/bin/python -m pip install 'fastapi>=0.110' 'numpy>=1.26' 'uvicorn[standard]>=0.30' && backend/venv/bin/python -m pip install 'torch>=2.3' --index-url https://download.pytorch.org/whl/cpu)",
            ]),
        ))
        self._patch_managed_checkout_wsl(target)

    def _wait_ready(self, proc):
        deadline = time.time() + self.startup_timeout
        while time.time() < deadline:
            if self._server_ready():
                return
            if proc is not None and proc.poll() not in {None, 0}:
                detail = ""
                try:
                    if self._managed_log_path.exists():
                        detail = self._managed_log_path.read_text(
                            encoding="utf-8", errors="replace")[-4000:].strip()
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
        url = self._effective_base_url() + path
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

    def _active_download_error(self, model_name: str = "") -> str:
        try:
            tasks = self._request("GET", "/tasks/active", accept_json=True)
        except Exception:
            return ""
        downloads = tasks.get("downloads", []) if isinstance(tasks, dict) else []
        wanted = str(model_name or "")
        for item in downloads:
            if not isinstance(item, dict) or (wanted and item.get("model_name") != wanted):
                continue
            error = item.get("error")
            if error:
                return f"{item.get('model_name') or 'model'} download failed: {error}"
        return ""

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
        self.transcribe(
            audio_bytes=_silent_wav(),
            mime_type="audio/wav",
            filename="warmup.wav",
            language=language,
            model=model or self.stt_model,
        )
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

