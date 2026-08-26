"""Relay-backed FFmpeg composition service for closed Media Studio recipes."""

from __future__ import annotations

import json
import logging
import mimetypes
import re
from pathlib import PurePath
from typing import Any

from core import ServiceFactory
from core.base_service import BaseService
from core.ffmpeg_recipe import FFmpegRecipe, compile_ffmpeg_argv
from core.media_project_store import MediaProjectStore

_SAFE_SUFFIX = re.compile(r"^\.[A-Za-z0-9]{1,10}$")
logger = logging.getLogger(__name__)


class FFmpegMediaService(BaseService):
    """Probe and compose owner-authorized media on a linked PawFlow relay."""

    TYPE = "ffmpegMedia"
    VERSION = "1.0.0"
    NAME = "FFmpeg Media"
    DESCRIPTION = "Safely probe and compose media from closed typed recipes"
    CATEGORY = "media"

    def __init__(self, config):
        super().__init__(config)
        self._runtime_user_id = ""
        self._runtime_conversation_id = ""
        self._runtime_agent_name = ""
        self._runtime_relay_id = ""
        self._runtime_relay_local = None

    def get_parameter_schema(self) -> dict:
        return {
            "relay": {
                "type": "service_ref",
                "required": False,
                "service_type": "relay",
                "description": (
                    "Linked relay used for FFmpeg. When omitted, the conversation "
                    "default or sole linked relay is required."),
            },
            "local": {
                "type": "boolean",
                "required": False,
                "default": False,
                "description": "Run on the authorized relay host surface.",
            },
            "ffmpeg_binary": {
                "type": "string",
                "required": False,
                "default": "ffmpeg",
                "description": "Trusted FFmpeg executable name configured by an administrator.",
            },
            "ffprobe_binary": {
                "type": "string",
                "required": False,
                "default": "ffprobe",
                "description": "Trusted ffprobe executable name configured by an administrator.",
            },
            "timeout": {
                "type": "integer",
                "required": False,
                "default": 1800,
                "description": "Maximum seconds for one FFmpeg invocation.",
            },
            "max_input_bytes": {
                "type": "integer",
                "required": False,
                "default": 8589934592,
                "description": "Maximum bytes accepted for each input.",
            },
            "max_output_bytes": {
                "type": "integer",
                "required": False,
                "default": 8589934592,
                "description": "Maximum bytes accepted for the composed output.",
            },
        }

    def set_runtime_context(
        self,
        user_id: str = "",
        conversation_id: str = "",
        agent_name: str = "",
        relay_id: str = "",
        relay_local=None,
        **_: object,
    ) -> None:
        self._runtime_user_id = user_id or ""
        self._runtime_conversation_id = conversation_id or ""
        self._runtime_agent_name = agent_name or ""
        self._runtime_relay_id = str(relay_id or "").strip()
        self._runtime_relay_local = relay_local

    def _create_connection(self):
        return {"ready": True}

    def _close_connection(self):
        return None

    @staticmethod
    def _required(value: str, name: str) -> str:
        result = str(value or "").strip()
        if not result:
            raise ValueError(f"{name} is required")
        return result

    def _runtime(self) -> tuple[str, str]:
        return (
            self._required(self._runtime_user_id, "user_id"),
            self._required(self._runtime_conversation_id, "conversation_id"),
        )

    def _resolve_relay(self):
        from core.relay_bindings import resolve_relay

        user_id, conversation_id = self._runtime()
        return resolve_relay(
            conversation_id,
            self._runtime_relay_id or str(self.config.get("relay") or ""),
            agent=self._runtime_agent_name,
            user_id=user_id,
        )

    @staticmethod
    def _suffix(filename: str) -> str:
        suffix = PurePath(filename).suffix.lower()
        return suffix if _SAFE_SUFFIX.fullmatch(suffix) else ".bin"

    @staticmethod
    def _check_result(result: dict, operation: str) -> dict:
        if not isinstance(result, dict):
            raise RuntimeError(f"{operation} returned an invalid relay result")
        if int(result.get("returncode", 1)) != 0:
            detail = str(result.get("stderr") or result.get("stdout") or "failed")
            raise RuntimeError(f"{operation} failed: {detail[:2000]}")
        return result

    def _probe(self, relay, workdir: str, path: str, *, local: bool) -> dict:
        argv = [
            str(self.config.get("ffprobe_binary") or "ffprobe"),
            "-v", "error", "-show_format", "-show_streams", "-of", "json", path,
        ]
        result = self._check_result(
            relay.exec_argv(
                workdir, argv,
                timeout=min(int(self.config.get("timeout") or 1800), 120),
                local=local,
            ),
            "ffprobe",
        )
        try:
            value = json.loads(result.get("stdout") or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError("ffprobe returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise RuntimeError("ffprobe returned a non-object payload")
        return value

    def compose(
        self,
        *,
        recipe: dict[str, Any] | FFmpegRecipe,
        project_id: str,
        run_id: str,
        task_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Execute one idempotent closed recipe and return a FileStore artifact."""

        user_id, conversation_id = self._runtime()
        parsed = recipe if isinstance(recipe, FFmpegRecipe) else FFmpegRecipe.from_dict(recipe)
        project_id = self._required(project_id, "project_id")
        run_id = self._required(run_id, "run_id")
        task_id = self._required(task_id, "task_id")
        idempotency_key = self._required(idempotency_key, "idempotency_key")
        service_id = str(self.config.get("_service_id") or self.TYPE)
        jobs = MediaProjectStore.instance()
        job = jobs.start_provider_job(
            project_id=project_id,
            user_id=user_id,
            conversation_id=conversation_id,
            run_id=run_id,
            task_id=task_id,
            engine="ffmpeg",
            service_id=service_id,
            operation=parsed.operation,
            idempotency_key=idempotency_key,
        )
        if job["status"] == "completed":
            return dict(job["output"])
        if job["status"] in {"failed", "superseded"}:
            raise RuntimeError(job.get("error") or f"FFmpeg job is {job['status']}")
        if job["status"] == "created":
            job = jobs.record_provider_submission(
                job["job_id"],
                user_id=user_id,
                conversation_id=conversation_id,
                provider_job_id=parsed.recipe_id,
            )

        relay_id, relay = self._resolve_relay()
        if not hasattr(relay, "exec_argv"):
            raise RuntimeError(
                f"Relay '{relay_id}' does not support shell-free argv execution")
        local = (
            bool(self.config.get("local", False))
            if self._runtime_relay_local is None
            else bool(self._runtime_relay_local))
        workdir = f".pawflow-media/{job['job_id']}"
        staged_paths: list[str] = []
        output_path = f"output-{parsed.output_filename}"
        try:
            relay.mkdir(workdir, local=local)
            from core.file_store import FileStore

            file_store = FileStore.instance()
            max_input = int(self.config.get("max_input_bytes") or 8589934592)
            for index, file_id in enumerate(parsed.inputs):
                filename, content, _content_type = file_store.get_required(
                    file_id, user_id, conversation_id)
                if len(content) > max_input:
                    raise ValueError(
                        f"input {index} exceeds max_input_bytes ({max_input})")
                staged = f"input-{index:02d}{self._suffix(filename)}"
                relay.write_file(f"{workdir}/{staged}", content, local=local)
                staged_paths.append(staged)
                self._probe(relay, workdir, staged, local=local)

            if parsed.operation == "probe":
                probe = self._probe(
                    relay, workdir, staged_paths[0], local=local)
                output_bytes = json.dumps(
                    probe, sort_keys=True, separators=(",", ":")).encode("utf-8")
                content_type = "application/json"
            else:
                argv = compile_ffmpeg_argv(
                    parsed,
                    staged_paths,
                    output_path,
                    ffmpeg_binary=str(self.config.get("ffmpeg_binary") or "ffmpeg"),
                    ffprobe_binary=str(self.config.get("ffprobe_binary") or "ffprobe"),
                )
                self._check_result(
                    relay.exec_argv(
                        workdir, argv,
                        timeout=int(self.config.get("timeout") or 1800),
                        local=local,
                    ),
                    "ffmpeg",
                )
                stat = relay.stat(f"{workdir}/{output_path}", local=local)
                size = int(getattr(stat, "size", 0) or 0)
                max_output = int(self.config.get("max_output_bytes") or 8589934592)
                if size <= 0 or size > max_output:
                    raise RuntimeError(
                        f"FFmpeg output size {size} violates limit {max_output}")
                output_probe = self._probe(
                    relay, workdir, output_path, local=local)
                output_bytes = relay.read_file(
                    f"{workdir}/{output_path}", local=local)
                if len(output_bytes) != size:
                    raise RuntimeError("FFmpeg output changed while being retrieved")
                content_type = (
                    mimetypes.guess_type(parsed.output_filename)[0]
                    or "application/octet-stream"
                )
                probe = output_probe

            file_id = file_store.store(
                parsed.output_filename,
                output_bytes,
                content_type,
                conversation_id=conversation_id,
                user_id=user_id,
                agent_name=self._runtime_agent_name,
                category="media_studio",
            )
            if not file_id:
                raise RuntimeError("conversation was deleted before output storage")
            output = {
                "file_id": file_id,
                "filename": parsed.output_filename,
                "content_type": content_type,
                "size": len(output_bytes),
                "url": f"fs://filestore/{file_id}/{parsed.output_filename}",
                "relay_id": relay_id,
                "recipe": parsed.to_dict(),
                "probe": probe,
            }
            jobs.finish_provider_job(
                job["job_id"],
                user_id=user_id,
                conversation_id=conversation_id,
                status="completed",
                output=output,
            )
            return output
        except Exception as exc:
            try:
                jobs.finish_provider_job(
                    job["job_id"],
                    user_id=user_id,
                    conversation_id=conversation_id,
                    status="failed",
                    error=f"{type(exc).__name__}: {exc}",
                )
            except Exception:
                logger.exception(
                    "Failed to persist the FFmpeg provider job failure")
            raise
        finally:
            for path in [*staged_paths, output_path]:
                try:
                    relay.delete_file(f"{workdir}/{path}", local=local)
                except Exception:
                    logger.debug(
                        "Failed to remove a staged FFmpeg file",
                        exc_info=True,
                    )
            try:
                relay.exec_argv(
                    ".", ["rmdir", workdir], timeout=30, local=local)
            except Exception:
                logger.debug(
                    "Failed to remove the FFmpeg working directory",
                    exc_info=True,
                )

    def get_operations(self) -> dict:
        from core.ffmpeg_recipe import OPERATIONS

        return {operation: {} for operation in sorted(OPERATIONS)}


ServiceFactory.register(FFmpegMediaService)
