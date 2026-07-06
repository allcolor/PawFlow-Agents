"""Voicebox backend lifecycle: install/checkout (incl. WSL), server start/stop,
model preload, and HTTP transport for VoiceboxService.

Split out of voicebox_service.py as a leaf mixin so the service file stays
<= 800 lines. Methods rely on host state set by VoiceboxService.__init__
(self.config, self.start_command, self.install_dir, self.repo_url/ref, the
backend process handle, etc.) and host helpers (_effective_base_url).
"""

import json
import logging
import os
import shlex
import shutil
import subprocess  # nosec B404 - managed local backend commands use explicit argv with shell=False.
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict

from core import ServiceError
from core.relay_proxy_url import (
    is_relay_proxy_url, relay_proxy_ssl_context, resolve_relay_aware_url)
from core.service_install import (
    assert_requirements,
    executable_requirement,
    python_venv_requirement,
    write_install_state,
)

logger = logging.getLogger(__name__)


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


class _VoiceboxBackendMixin:
    """Backend install/lifecycle + HTTP transport for VoiceboxService."""

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
                with urllib.request.urlopen(req, timeout=min(3, self.timeout), context=relay_proxy_ssl_context(base_url)) as resp:  # nosec B310 - configured local Voicebox URL.
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
            with urllib.request.urlopen(req, timeout=self.timeout, context=relay_proxy_ssl_context(url)) as resp:  # nosec B310 - configured local Voicebox URL.
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
