"""Managed realtime stack — auto-provisioned LiveKit server + agents worker.

Managed mode of docs/REALTIME_MULTIMODAL_LIVEKIT_PLAN.md: when a
realtimeVoiceConnection service uses `engine: livekit` without an external
`livekit_url`, PawFlow provisions the stack itself through the Docker
socket, exactly like ServerRelayManager provisions relay containers:

- `pawflow-livekit` — the official livekit-server image on the host
  network, with a GENERATED API key/secret pair (never devkey/secret).
- `pawflow-livekit-worker` — the agents sidecar. The image is a clean
  dependency-only image built locally once (no PawFlow code baked in);
  the `pawflow_livekit_worker` package is staged from this server install
  and bind-mounted read-only, so server upgrades need no image rebuild.

Secrets (LiveKit API secret + worker deployment secret) are generated on
first use and persisted encrypted in `data/system/realtime_stack.json`.
The worker secret is exported to this process's environment so the
existing bootstrap endpoint auth (`PAWFLOW_REALTIME_WORKER_SECRET`) works
unchanged. Provider API keys never enter the worker container: the worker
receives them per-session in the bootstrap payload.

Provisioning is asynchronous (image pull + one-time build take minutes on
first run); session start reports an actionable in-progress error until
the stack is ready.
"""

import hashlib
import json
import logging
import os
import secrets as _secrets
import shutil
import subprocess  # nosec B404
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from core.docker_utils import docker_cmd

logger = logging.getLogger(__name__)

SERVER_CONTAINER = "pawflow-livekit"
WORKER_CONTAINER = "pawflow-livekit-worker"
SERVER_IMAGE = "livekit/livekit-server:latest"
# Bump the tag when the dependency set below changes — a new tag triggers
# a rebuild on already-provisioned deployments.
WORKER_IMAGE = "pawflow-livekit-worker:deps-1"
SIGNAL_PORT = 7880

_WORKER_SECRET_ENV = "PAWFLOW_REALTIME_WORKER_SECRET"  # nosec B105

_WORKER_DEPS = (
    "livekit-agents>=1.6.0",
    "livekit-plugins-openai>=1.6.0",
    "livekit-plugins-google>=1.6.0",
    "livekit-plugins-silero>=1.6.0",
    "livekit-plugins-turn-detector>=1.6.0",
    "aiohttp>=3.9.0",
)

_WORKER_DOCKERFILE = f"""\
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir {' '.join(f'"{d}"' for d in _WORKER_DEPS)}
# Pre-download local model weights (Silero VAD + end-of-turn detector) so
# first-call latency is predictable. Best-effort: weights also download
# lazily at runtime.
RUN python -c "from livekit.plugins import silero; silero.VAD.load()" || true
CMD ["python", "-m", "pawflow_livekit_worker", "start"]
"""


def _data_dir() -> Path:
    return Path(os.environ.get("PAWFLOW_DATA_DIR") or "data").resolve()


def _host_path(path: Path) -> str:
    """Translate a server-container path under data/ to its host path."""
    from core._relay_naming import _relay_runtime_host_dir
    return _relay_runtime_host_dir(path)


def _run_docker(args: list, *, timeout: int = 600) -> subprocess.CompletedProcess:
    cmd = docker_cmd() + args
    return subprocess.run(  # nosec B603
        cmd, capture_output=True, text=True, timeout=timeout)


def _container_state(name: str) -> str:
    """Return 'running', 'exited', ... or '' when the container is absent."""
    result = _run_docker(["inspect", "--format", "{{.State.Status}}", name],
                         timeout=30)
    if result.returncode != 0:
        return ""
    return (result.stdout or "").strip()


def _image_exists(image: str) -> bool:
    return _run_docker(["image", "inspect", image], timeout=30).returncode == 0


class RealtimeStackManager:
    """Provisions and supervises the managed LiveKit stack (singleton)."""

    _instance: Optional["RealtimeStackManager"] = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "RealtimeStackManager":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._state_lock = threading.Lock()
        self._provision_thread: Optional[threading.Thread] = None
        self._status = "absent"       # absent|provisioning|ready|error
        self._status_detail = ""

    # -- credentials -----------------------------------------------------

    def _state_file(self) -> Path:
        return _data_dir() / "system" / "realtime_stack.json"

    def has_state(self) -> bool:
        """True when a managed stack has been provisioned on this deploy."""
        return self._state_file().exists()

    def credentials(self) -> Dict[str, str]:
        """Generated LiveKit API key/secret + worker deployment secret.

        Created on first call, persisted encrypted, stable afterwards.
        The bootstrap endpoint falls back to the managed worker secret
        when `PAWFLOW_REALTIME_WORKER_SECRET` is not set in the env.
        """
        from core.secrets import get_secrets_manager
        sm = get_secrets_manager()
        path = self._state_file()
        with self._state_lock:
            state: Dict[str, Any] = {}
            if path.exists():
                try:
                    state = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    logger.warning("realtime_stack.json unreadable — "
                                   "regenerating credentials")
            creds = state.get("credentials") or {}
            if creds.get("api_key"):
                try:
                    sm.decrypt(creds["api_secret"])
                    sm.decrypt(creds["worker_secret"])
                except Exception:
                    # Master key changed since the stack was provisioned
                    # (e.g. rotation): regenerate — the containers are
                    # recreated with the new keys on the next ensure.
                    logger.warning("realtime stack credentials cannot be "
                                   "decrypted — regenerating")
                    creds = {}
            if not creds.get("api_key"):
                creds = {
                    "api_key": "pflk" + _secrets.token_hex(8),
                    "api_secret": sm.encrypt(_secrets.token_urlsafe(32)),
                    "worker_secret": sm.encrypt(_secrets.token_hex(24)),
                }
                state["credentials"] = creds
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(state, indent=2) + "\n",
                                encoding="utf-8")
                try:
                    path.chmod(0o600)
                except OSError:
                    logger.debug("chmod on realtime_stack.json failed",
                                 exc_info=True)
            resolved = {
                "api_key": creds["api_key"],
                "api_secret": sm.decrypt(creds["api_secret"]),
                "worker_secret": sm.decrypt(creds["worker_secret"]),
            }
        return resolved

    def engine_credentials(self) -> Dict[str, str]:
        """livekit_url + API key/secret for resolve_livekit_config.

        The URL is the server-side view (worker + token minting). The
        browser never uses it: managed sessions hand the browser the
        same-origin signal proxy path instead.
        """
        creds = self.credentials()
        return {
            "livekit_url": f"ws://127.0.0.1:{SIGNAL_PORT}",
            "livekit_api_key": creds["api_key"],
            "livekit_api_secret": creds["api_secret"],
        }

    # -- status ----------------------------------------------------------

    def status(self) -> Dict[str, str]:
        """Cheap status. Docker is only queried when not provisioning."""
        with self._state_lock:
            if self._status == "provisioning":
                return {"state": "provisioning",
                        "detail": self._status_detail}
            if self._status == "error":
                return {"state": "error", "detail": self._status_detail}
        server = _container_state(SERVER_CONTAINER)
        worker = _container_state(WORKER_CONTAINER)
        if server == "running" and worker == "running":
            with self._state_lock:
                self._status = "ready"
            return {"state": "ready", "detail": ""}
        return {"state": "absent",
                "detail": f"server={server or 'absent'} "
                          f"worker={worker or 'absent'}"}

    # -- provisioning ----------------------------------------------------

    def ensure_stack(self) -> Dict[str, str]:
        """Start background provisioning if needed; return current status."""
        current = self.status()
        if current["state"] in ("ready", "provisioning"):
            return current
        with self._state_lock:
            if self._provision_thread and self._provision_thread.is_alive():
                return {"state": "provisioning",
                        "detail": self._status_detail}
            self._status = "provisioning"
            self._status_detail = "starting"
            self._provision_thread = threading.Thread(
                target=self._provision, name="realtime-stack-provision",
                daemon=True)
            self._provision_thread.start()
        return {"state": "provisioning", "detail": "starting"}

    def _set_detail(self, detail: str) -> None:
        with self._state_lock:
            self._status_detail = detail
        logger.info("[realtime-stack] %s", detail)

    def _provision(self) -> None:
        try:
            creds = self.credentials()
            self._ensure_server_container(creds)
            self._ensure_worker_image()
            code_dir = self._stage_worker_code()
            self._ensure_worker_container(creds, code_dir)
            with self._state_lock:
                self._status = "ready"
                self._status_detail = ""
            logger.info("[realtime-stack] provisioning complete")
        except Exception as e:
            logger.error("[realtime-stack] provisioning failed: %s", e,
                         exc_info=True)
            with self._state_lock:
                self._status = "error"
                self._status_detail = str(e)

    def _ensure_server_container(self, creds: Dict[str, str]) -> None:
        state = _container_state(SERVER_CONTAINER)
        if state == "running":
            return
        if state:  # exists but stopped
            self._set_detail("starting livekit-server container")
            result = _run_docker(["start", SERVER_CONTAINER], timeout=60)
            if result.returncode != 0:
                raise RuntimeError("docker start " + SERVER_CONTAINER
                                   + " failed: " + result.stderr.strip())
            return
        if not _image_exists(SERVER_IMAGE):
            self._set_detail(f"pulling {SERVER_IMAGE}")
            result = _run_docker(["pull", SERVER_IMAGE], timeout=900)
            if result.returncode != 0:
                raise RuntimeError(f"docker pull {SERVER_IMAGE} failed: "
                                   + result.stderr.strip())
        self._set_detail("creating livekit-server container")
        result = _run_docker([
            "run", "--detach", "--restart", "unless-stopped",
            "--name", SERVER_CONTAINER, "--network", "host",
            "--env",
            f"LIVEKIT_KEYS={creds['api_key']}: {creds['api_secret']}",
            # Deliberate all-interfaces bind: remote browsers send WebRTC
            # media (UDP/ICE) straight to livekit-server — only the signal
            # WS goes through the authenticated /livekit proxy. Access is
            # gated by the generated API credentials.
            SERVER_IMAGE, "--bind", "0.0.0.0",  # nosec B104
        ], timeout=120)
        if result.returncode != 0:
            raise RuntimeError("failed to start livekit-server: "
                               + result.stderr.strip())

    def _ensure_worker_image(self) -> None:
        if _image_exists(WORKER_IMAGE):
            return
        self._set_detail(f"building {WORKER_IMAGE} (one-time, few minutes)")
        build_dir = _data_dir() / "runtime" / "realtime" / ".image-build"
        build_dir.mkdir(parents=True, exist_ok=True)
        (build_dir / "Dockerfile").write_text(_WORKER_DOCKERFILE,
                                              encoding="utf-8")
        # Build context is read by the docker CLI in THIS container (it
        # tars the directory client-side), so the container path is
        # correct here — unlike volume mounts, which the daemon resolves
        # on the host.
        result = _run_docker([
            "build", "-t", WORKER_IMAGE, str(build_dir),
        ], timeout=1800)
        if result.returncode != 0:
            raise RuntimeError("worker image build failed: "
                               + (result.stderr or result.stdout).strip()[-2000:])

    def _stage_worker_code(self) -> Path:
        """Copy pawflow_livekit_worker from this install for bind-mounting."""
        root = Path(__file__).resolve().parents[1]
        pkg = root / "pawflow_livekit_worker"
        if not pkg.exists():
            raise RuntimeError(f"Missing worker source: {pkg}")
        digest = hashlib.sha256()
        for f in sorted(pkg.rglob("*.py")):
            digest.update(f.name.encode())
            digest.update(f.read_bytes())
        source_hash = digest.hexdigest()
        code_dir = _data_dir() / "runtime" / "realtime" / ".worker-code"
        marker = code_dir / ".source.json"
        if code_dir.exists():
            try:
                if json.loads(marker.read_text(
                        encoding="utf-8")).get("source_hash") == source_hash:
                    return code_dir
            except (OSError, json.JSONDecodeError):
                logger.debug("worker code marker unreadable", exc_info=True)
            shutil.rmtree(code_dir)
        staging = code_dir.with_suffix(".tmp")
        if staging.exists():
            shutil.rmtree(staging)
        shutil.copytree(pkg, staging,
                        ignore=shutil.ignore_patterns("__pycache__"))
        (staging / ".source.json").write_text(
            json.dumps({"source_hash": source_hash}) + "\n", encoding="utf-8")
        staging.rename(code_dir)
        return code_dir

    def _pawflow_url_env(self) -> list:
        """PAWFLOW_URL (+TLS flag) for the worker on the host network."""
        from services.http_listener_service import HTTPListenerService
        listeners = HTTPListenerService.all_instances()
        if not listeners:
            raise RuntimeError(
                "Cannot provision the realtime worker: no HTTPListenerService "
                "running. Start the main listener first.")
        main = next(iter(listeners.values()))
        scheme = "https" if main.is_ssl else "http"
        env = ["--env", f"PAWFLOW_URL={scheme}://127.0.0.1:{main._port}"]
        if main.is_ssl:
            # The bootstrap fetch targets loopback; the default install
            # cert is self-signed, so verification is disabled here.
            env += ["--env", "PAWFLOW_TLS_INSECURE=1"]
        return env

    def _ensure_worker_container(self, creds: Dict[str, str],
                                 code_dir: Path) -> None:
        state = _container_state(WORKER_CONTAINER)
        if state == "running":
            return
        if state:
            self._set_detail("starting worker container")
            result = _run_docker(["start", WORKER_CONTAINER], timeout=60)
            if result.returncode != 0:
                raise RuntimeError("docker start " + WORKER_CONTAINER
                                   + " failed: " + result.stderr.strip())
            return
        self._set_detail("creating worker container")
        result = _run_docker([
            "run", "--detach", "--restart", "unless-stopped",
            "--name", WORKER_CONTAINER, "--network", "host",
            "--volume",
            f"{_host_path(code_dir)}:/app/pawflow_livekit_worker:ro",
            "--env", f"LIVEKIT_URL=ws://127.0.0.1:{SIGNAL_PORT}",
            "--env", f"LIVEKIT_API_KEY={creds['api_key']}",
            "--env", f"LIVEKIT_API_SECRET={creds['api_secret']}",
            "--env", f"{_WORKER_SECRET_ENV}={creds['worker_secret']}",
            *self._pawflow_url_env(),
            WORKER_IMAGE,
            "python", "-m", "pawflow_livekit_worker", "start",
        ], timeout=120)
        if result.returncode != 0:
            raise RuntimeError("failed to start worker container: "
                               + result.stderr.strip())

    # -- teardown --------------------------------------------------------

    def stop_stack(self) -> None:
        """Stop and remove both managed containers (state file is kept)."""
        for name in (WORKER_CONTAINER, SERVER_CONTAINER):
            if _container_state(name):
                _run_docker(["rm", "--force", name], timeout=60)
        with self._state_lock:
            self._status = "absent"
            self._status_detail = ""

    def restart_stack(self) -> Dict[str, str]:
        """Force-recreate both containers (e.g. after a server upgrade)."""
        self.stop_stack()
        return self.ensure_stack()
