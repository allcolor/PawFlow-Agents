"""Codex CLI-backed image generation service.

This service runs isolated Codex CLI jobs through PawFlow's server-side
CodexPool. It does not use a filesystem relay and does not expose a Codex
binary path. Authentication and provider settings come from a configured
llmConnection service whose provider is codex-app-server.
"""

from __future__ import annotations

import logging
import mimetypes
import os
import shutil
import time
import urllib.request
import uuid
from pathlib import Path

from core import ServiceError, ServiceFactory
from services.base_image_generation import BaseImageGenerationService

logger = logging.getLogger(__name__)

_IMAGE_EXTS = ("png", "jpg", "jpeg", "webp")
_CONTENT_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
}


class CodexImageService(BaseImageGenerationService):
    """Generate/edit images by running Codex CLI with the `$imagegen` skill."""

    TYPE = "codexImageGeneration"
    VERSION = "1.0.0"
    NAME = "Codex CLI Image Generation"
    DESCRIPTION = "Generate and edit images through a Codex app-server LLM service"
    ACCEPTS_FILESTORE_URLS = True

    def get_parameter_schema(self) -> dict:
        return {
            "llm_service": {
                "type": "service_ref",
                "service_type": "llmConnection",
                "provider": "codex-app-server",
                "required": True,
                "description": (
                    "Codex app-server LLM service to use for credentials, "
                    "model defaults, and Codex CLI runtime configuration."),
            },
            "timeout": {
                "type": "integer", "required": False, "default": 900,
                "description": "Codex image job timeout in seconds.",
            },
            "cleanup": {
                "type": "boolean", "required": False, "default": True,
                "description": "Delete temporary Codex image job files after the image is read.",
            },
        }

    def __init__(self, config):
        super().__init__(config)
        self.llm_service = (self.config.get("llm_service", "") or "").strip()
        self.timeout = int(self.config.get("timeout", 900) or 900)
        self.cleanup = bool(self.config.get("cleanup", True))
        self._runtime_user_id = ""
        self._runtime_conversation_id = ""
        self._runtime_agent_name = ""

    def set_runtime_context(self, user_id: str = "", conversation_id: str = "",
                            agent_name: str = "", **_: object):
        self._runtime_user_id = user_id or ""
        self._runtime_conversation_id = conversation_id or ""
        self._runtime_agent_name = agent_name or ""

    def _create_connection(self):
        if not self.llm_service:
            raise ServiceError("llm_service is required for Codex image generation")
        return {"ready": True}

    def _close_connection(self):
        pass

    @staticmethod
    def _normalize_format(output_format: str = "") -> str:
        fmt = (output_format or "png").lower().lstrip(".")
        if fmt == "jpeg":
            return "jpg"
        if fmt not in _IMAGE_EXTS:
            return "png"
        return fmt

    @staticmethod
    def _safe_path_part(value: str, fallback: str) -> str:
        safe = (value or fallback).replace(":", "_").replace("/", "_").replace("\\", "_")
        safe = safe.strip(" .")
        return safe or fallback

    @staticmethod
    def _size_text(width=None, height=None, aspect_ratio: str = "") -> str:
        parts = []
        try:
            w = int(width or 0)
            h = int(height or 0)
            if w > 0 and h > 0:
                parts.append(f"Target size: {w}x{h}px.")
        except (TypeError, ValueError):
            pass
        if aspect_ratio:
            parts.append(f"Target aspect ratio: {aspect_ratio}.")
        return " ".join(parts)

    @staticmethod
    def _filename_from_url(url: str, index: int) -> tuple[str, str]:
        clean = (url or "").split("?", 1)[0].rstrip("/")
        name = clean.rsplit("/", 1)[-1] if clean else ""
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else "png"
        if ext not in _IMAGE_EXTS:
            ext = "png"
        return f"reference_{index}.{ext}", ext

    def _resolve_codex_client(self):
        from core.service_registry import ServiceRegistry
        reg = ServiceRegistry.get_instance()
        svc_def = reg.resolve_definition(
            self.llm_service,
            user_id=self._runtime_user_id,
            conv_id=self._runtime_conversation_id,
        )
        if svc_def is None:
            raise ServiceError(f"LLM service '{self.llm_service}' not found")
        if getattr(svc_def, "service_type", "") != "llmConnection":
            raise ServiceError(
                f"Service '{self.llm_service}' is not an llmConnection service")
        provider = ((getattr(svc_def, "config", {}) or {}).get("provider", "") or "").strip()
        if provider != "codex-app-server":
            raise ServiceError(
                f"Codex image generation requires a codex-app-server llm_service, got {provider or 'unknown'}")

        svc = reg.resolve(
            self.llm_service,
            user_id=self._runtime_user_id,
            conv_id=self._runtime_conversation_id,
        )
        if svc is None:
            raise ServiceError(f"LLM service '{self.llm_service}' could not connect")
        if getattr(svc, "provider", "") != "codex-app-server":
            raise ServiceError(
                f"Codex image generation requires provider codex-app-server, got {getattr(svc, 'provider', '') or 'unknown'}")
        client = getattr(svc, "_client", None)
        if client is None:
            raise ServiceError(f"LLM service '{self.llm_service}' has no Codex client")
        setattr(client, "_agent_service", self.llm_service)
        return svc, client

    def _prepare_workdir(self, job_id: str) -> tuple[Path, str]:
        import core.paths as _paths
        user_slot = self._safe_path_part(self._runtime_user_id, "_image_service")
        conv_slot = "_image_generation"
        job_slot = self._safe_path_part(job_id, "job")
        host_dir = _paths.CODEX_SESSIONS_DIR / user_slot / conv_slot / job_slot
        host_dir.mkdir(parents=True, exist_ok=True)
        session_dir = f"/cc_sessions/{user_slot}/{conv_slot}/{job_slot}"
        return host_dir, session_dir

    def _load_image_reference(self, url: str, index: int) -> tuple[str, bytes]:
        if not url:
            raise ServiceError("Empty image reference URL")
        name, _ext = self._filename_from_url(url, index)
        if url.startswith("fs://filestore/"):
            from core.file_store import FileStore
            rest = url[len("fs://filestore/"):]
            fid = rest.split("/", 1)[0]
            try:
                fname, data, _ct = FileStore.instance().get_required(
                    fid, self._runtime_user_id, self._runtime_conversation_id)
            except Exception as exc:
                raise ServiceError(f"FileStore image '{fid}' not found or not accessible: {exc}") from exc
            if fname and "." in fname:
                ext = fname.rsplit(".", 1)[-1].lower()
                if ext in _IMAGE_EXTS:
                    name = f"reference_{index}.{ext}"
            return name, data
        if url.startswith("http://") or url.startswith("https://"):
            req = urllib.request.Request(url, headers={"User-Agent": "PawFlow-Agent/1.0"})
            with urllib.request.urlopen(req, timeout=60) as resp:  # nosec B310 - HTTP(S) image reference already checked above.
                return name, resp.read()
        path = Path(url)
        if path.is_file():
            return name, path.read_bytes()
        raise ServiceError(
            "Codex image references must be HTTP(S), fs://filestore, or a server-local file path")

    def _write_reference_images(self, job_dir: Path, image_urls) -> list[str]:
        paths = []
        for idx, url in enumerate(image_urls or []):
            name, data = self._load_image_reference(str(url), idx)
            (job_dir / name).write_bytes(data)
            paths.append(name)
        return paths

    def _build_prompt(self, prompt: str, output_name: str, *, edit: bool,
                      width=None, height=None, aspect_ratio: str = "",
                      negative_prompt: str = "", reference_paths=None) -> str:
        if "$imagegen" not in prompt:
            prompt = f"{prompt.rstrip()} $imagegen"
        size_text = self._size_text(width, height, aspect_ratio)
        refs = ""
        if edit and reference_paths:
            refs = ("Use the attached reference image(s) as edit inputs: "
                    + ", ".join(reference_paths) + ".")
        avoid = f" Avoid: {negative_prompt}." if negative_prompt else ""
        return (
            f"{prompt}\n\n"
            f"{refs}\n"
            f"{size_text}{avoid}\n"
            f"Generate exactly one final image. If possible, save it as "
            f"./{output_name} in the current working directory. If the "
            f"Codex image tool writes into $CODEX_HOME/generated_images "
            f"instead, leave the generated image there."
        ).strip()

    @staticmethod
    def _codex_image_args(model: str = "") -> list[str]:
        args = [
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
            "-o", "last_message.txt",
        ]
        # Keep image_generation enabled. Disable local OS/browser tools; this
        # service reads generated files itself after Codex exits.
        for builtin in (
            "shell_tool",
            "shell_snapshot",
            "unified_exec",
            "apps",
            "browser_use",
            "in_app_browser",
            "computer_use",
        ):
            args.extend(["--disable", builtin])
        args.extend(["-c", "tools.web_search=false"])
        model = (model or "").strip()
        if model:
            args.extend(["--model", model])
        return args

    def _codex_env(self, client) -> dict:
        env = {}
        if hasattr(client, "_codex_env"):
            for key, value in (client._codex_env("") or {}).items():
                if key in (
                    "CODEX_API_KEY",
                    "OPENAI_API_KEY",
                    "OPENAI_BASE_URL",
                    "NODE_TLS_REJECT_UNAUTHORIZED",
                ):
                    env[key] = value
        return env

    @staticmethod
    def _find_output_image(job_dir: Path, output_name: str, start_time: float) -> Path | None:
        direct = job_dir / output_name
        if direct.is_file() and direct.stat().st_size > 0:
            return direct
        candidates = []
        for root in (job_dir / ".codex" / "generated_images", job_dir / "generated_images"):
            if not root.is_dir():
                continue
            for ext in _IMAGE_EXTS:
                candidates.extend(root.rglob(f"*.{ext}"))
        candidates = [p for p in candidates if p.is_file() and p.stat().st_size > 0]
        newer = [p for p in candidates if p.stat().st_mtime >= start_time - 1]
        pool = newer or candidates
        if not pool:
            return None
        return max(pool, key=lambda p: p.stat().st_mtime)

    def _run_job(self, prompt: str, *, image_urls=None, width=None, height=None,
                 aspect_ratio: str = "", output_format: str = "png",
                 negative_prompt: str = "", edit: bool = False,
                 model: str = "") -> dict:
        if not prompt:
            raise ServiceError("No prompt provided")
        self._get_connection()
        fmt = self._normalize_format(output_format)
        job_id = uuid.uuid4().hex[:12]
        output_name = f"output.{fmt}"
        job_dir, session_dir = self._prepare_workdir(job_id)
        container = ""
        pool = None
        client = None
        start_time = time.time()
        try:
            _svc, client = self._resolve_codex_client()
            if not model:
                model = (getattr(_svc, "default_model", "")
                         or getattr(client, "default_model", "")
                         or "")
            if hasattr(client, "_codex_setup_credentials"):
                client._codex_setup_credentials(str(job_dir))
            reference_paths = self._write_reference_images(job_dir, image_urls or [])
            final_prompt = self._build_prompt(
                prompt, output_name, edit=edit, width=width, height=height,
                aspect_ratio=aspect_ratio, negative_prompt=negative_prompt,
                reference_paths=reference_paths)
            codex_args = self._codex_image_args(model=model)
            for ref in reference_paths:
                codex_args.extend(["-i", ref])
            codex_args.append("-")

            from core.codex_pool import CodexPool
            pool = CodexPool.instance()
            container = pool.acquire()
            logger.info("[CODEX-IMAGE] job=%s service=%s edit=%s prompt=%s...",
                        job_id, self.llm_service, edit, prompt[:80])
            proc = pool.exec_codex(
                container,
                session_dir,
                codex_args,
                extra_env=self._codex_env(client),
                stdin=__import__("subprocess").PIPE,  # nosec B404
                stdout=__import__("subprocess").PIPE,  # nosec B404
                stderr=__import__("subprocess").PIPE,  # nosec B404
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            try:
                stdout, stderr = proc.communicate(final_prompt, timeout=self.timeout)
            except __import__("subprocess").TimeoutExpired as exc:  # nosec B404
                raise ServiceError(f"Codex image job timed out after {self.timeout}s") from exc
            rc = int(getattr(proc, "returncode", 0) or 0)
            if hasattr(client, "_codex_recover_tokens"):
                client._codex_recover_tokens(str(job_dir))
            if rc != 0:
                raise ServiceError(
                    f"Codex image job failed (exit {rc}): stdout={stdout[:500]!r} stderr={stderr[:500]!r}")
            output = self._find_output_image(job_dir, output_name, start_time)
            if output is None:
                raise ServiceError(
                    "Codex image job completed but produced no image in the job directory or CODEX_HOME/generated_images")
            image_bytes = output.read_bytes()
            out_ext = output.suffix.lower().lstrip(".") or fmt
            content_type = (_CONTENT_TYPES.get(out_ext)
                            or mimetypes.guess_type(output.name)[0]
                            or "image/png")
            return {"image_bytes": image_bytes, "content_type": content_type}
        finally:
            if pool is not None and container:
                try:
                    pool.release(container)
                except Exception:
                    logger.debug("[CODEX-IMAGE] pool release failed", exc_info=True)
            if self.cleanup:
                try:
                    shutil.rmtree(job_dir, ignore_errors=True)
                except Exception:
                    logger.debug("[CODEX-IMAGE] cleanup failed for %s", job_dir, exc_info=True)

    def generate(self, prompt="", negative_prompt="", width=1024, height=1024,
                 output_format="png", aspect_ratio="", model="", **_) -> dict:
        return self._run_job(
            prompt, width=width, height=height, aspect_ratio=aspect_ratio,
            output_format=output_format, negative_prompt=negative_prompt,
            edit=False, model=model)

    def edit_image(self, prompt: str = "", image_urls=None, negative_prompt: str = "",
                   width=1024, height=1024, output_format="png",
                   aspect_ratio: str = "", model="", **_) -> dict:
        image_urls = image_urls or []
        if isinstance(image_urls, str):
            image_urls = [image_urls]
        if not image_urls:
            raise ServiceError("image_urls is required for Codex image editing")
        return self._run_job(
            prompt, image_urls=image_urls, width=width, height=height,
            aspect_ratio=aspect_ratio, output_format=output_format,
            negative_prompt=negative_prompt, edit=True, model=model)


ServiceFactory.register(CodexImageService)
