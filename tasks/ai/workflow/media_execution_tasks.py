"""Exact-service media submission for the Media Studio Workflow Agent."""

from __future__ import annotations

import json
import re
from typing import Any

from core import FlowFile, TaskFactory
from core.agent_contracts import CapabilityEffect, IdempotencyClass
from core.service_definition_revision import compute_service_definition_revision
from tasks.ai.workflow.turn_tasks import _WorkflowContextTask
from tasks.control.merge_content import MergeContentTask


_FILESTORE_URL_RE = re.compile(
    r"fs://filestore/([A-Za-z0-9._-]+)/([^\s]+)")
_SUPPORTED_OPERATIONS = frozenset({
    "generate", "edit_image",
    "image_to_video", "frame_to_video", "reference_to_video",
    "video_edit", "video_extend",
    "generate_audio", "speak", "clone_voice",
})


def _state(flowfile: FlowFile) -> dict[str, Any]:
    try:
        value = json.loads(flowfile.get_content().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Media Studio workflow state must be a JSON object") from exc
    if not isinstance(value, dict):
        raise ValueError("Media Studio workflow state must be a JSON object")
    return value


def _put(flowfile: FlowFile, state: dict[str, Any]) -> None:
    flowfile.set_content(json.dumps(
        state, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8"))


def _selected(state: dict[str, Any]) -> dict[str, Any]:
    selection = state.get("selection") or {}
    if selection.get("outcome") != "selected":
        raise ValueError("media generation requires one selected capability")
    selected = selection.get("selected")
    if not isinstance(selected, dict):
        raise ValueError("selected media capability is required")
    snapshot = state.get("capability_snapshot") or {}
    if selection.get("snapshot_digest") != snapshot.get("digest"):
        raise ValueError("selected capability does not match the frozen snapshot")
    capability_id = str(selected.get("capability_id") or "")
    matching = [
        item for item in snapshot.get("capabilities") or []
        if isinstance(item, dict)
        and str(item.get("capability_id") or "") == capability_id
    ]
    if len(matching) != 1 or matching[0] != selected:
        raise ValueError("selected capability is not the frozen snapshot entry")
    return selected


def _reference_urls(brief: dict[str, Any]) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    for reference in brief.get("references") or []:
        if not isinstance(reference, dict):
            raise ValueError("brief references must be objects")
        role = str(reference.get("role") or "")
        file_id = str(reference.get("file_id") or "")
        filename = str(reference.get("filename") or "")
        if not role or not file_id or not filename:
            raise ValueError("brief references require role, file_id and filename")
        values.setdefault(role, []).append(
            f"fs://filestore/{file_id}/{filename}")
    return values


def _first(references: dict[str, list[str]], *roles: str) -> str:
    for role in roles:
        values = references.get(role) or []
        if values:
            return values[0]
    return ""


def _handler(operation: str, media_kind: str):
    if media_kind == "image" and operation == "generate":
        from core.handlers.media_image import ImageGenerationHandler
        return ImageGenerationHandler()
    if media_kind == "image" and operation == "edit_image":
        from core.handlers.media_image import EditImageHandler
        return EditImageHandler()
    if media_kind == "video" and operation in {
        "generate", "image_to_video", "frame_to_video",
        "reference_to_video", "video_edit", "video_extend",
    }:
        from core.handlers.media_av import VideoGenerationHandler
        return VideoGenerationHandler()
    if media_kind == "audio" and operation == "generate_audio":
        from core.handlers.media_av import AudioGenerationHandler
        return AudioGenerationHandler()
    if media_kind == "speech" and operation == "speak":
        from core.handlers.capabilities import SpeakHandler
        return SpeakHandler()
    if media_kind == "voice_clone" and operation == "clone_voice":
        from core.handlers.capabilities import CloneVoiceHandler
        return CloneVoiceHandler()
    raise ValueError(
        f"unsupported media generation operation: {media_kind}/{operation}")


def _arguments(
    state: dict[str, Any], selected: dict[str, Any],
) -> dict[str, Any]:
    brief = state.get("brief") or {}
    media_kind = str(brief.get("media_kind") or "")
    operation = str(brief.get("operation") or "")
    prompt = str(
        brief.get("prompt_refined") or brief.get("prompt_original") or "")
    references = _reference_urls(brief)
    arguments: dict[str, Any] = {
        "destination": "filestore",
        "prompt": prompt,
    }
    for name in (
        "negative_prompt", "style", "width", "height", "aspect_ratio",
    ):
        value = brief.get(name)
        if value not in (None, ""):
            arguments[name] = value
    if brief.get("duration_seconds") is not None:
        arguments["duration"] = brief["duration_seconds"]
    model = str(selected.get("model") or "")
    preset_id = str(selected.get("preset_id") or "")
    if model:
        arguments["model"] = model
    if preset_id:
        arguments["preset_id"] = preset_id

    if media_kind == "image" and operation == "edit_image":
        arguments["image_urls"] = [
            url for role in (
                "source_image", "subject_reference", "style_reference",
                "composition_reference",
            )
            for url in references.get(role, [])
        ]
    elif media_kind == "video":
        if operation == "frame_to_video":
            arguments["image_url"] = _first(
                references, "start_frame", "source_image")
            arguments["end_image_url"] = _first(references, "end_frame")
        elif operation == "image_to_video":
            arguments["image_url"] = _first(
                references, "source_image", "subject_reference")
        elif operation == "reference_to_video":
            arguments["reference_image_urls"] = [
                url for role in (
                    "source_image", "subject_reference", "style_reference",
                    "composition_reference",
                )
                for url in references.get(role, [])
            ]
        elif operation in {"video_edit", "video_extend"}:
            arguments["video_url"] = _first(references, "source_video")
            if operation == "video_extend":
                arguments["video_mode"] = "extend"
    elif media_kind == "audio":
        source_audio = _first(references, "source_audio")
        music_bed = _first(references, "music_bed")
        if source_audio:
            arguments["source_audio_url"] = source_audio
        if music_bed:
            arguments["music_bed_url"] = music_bed
    elif media_kind == "speech":
        audio = brief.get("audio") or {}
        arguments = {
            "destination": "filestore",
            "text": str(audio.get("text") or prompt),
            "voice": str(audio.get("voice") or ""),
            "language": str(audio.get("language") or ""),
        }
        if model:
            arguments["model"] = model
    elif media_kind == "voice_clone":
        audio = brief.get("audio") or {}
        arguments = {
            "name": str(audio.get("voice_name") or ""),
            "reference_audio_url": _first(
                references, "voice_reference"),
            "reference_text": str(audio.get("reference_text") or ""),
            "language": str(audio.get("language") or ""),
        }
    return arguments


def _artifacts(message: str) -> list[dict[str, Any]]:
    from core.file_store import FileStore
    results = []
    seen = set()
    for file_id, filename in _FILESTORE_URL_RE.findall(message):
        key = (file_id, filename)
        if key in seen:
            continue
        seen.add(key)
        metadata = FileStore.instance().get_metadata(file_id) or {}
        results.append({
            "file_id": file_id,
            "filename": str(metadata.get("filename") or filename),
            "content_type": str(
                metadata.get("content_type") or "application/octet-stream"),
            "url": f"fs://filestore/{file_id}/{filename}",
        })
    return results


def _merge_output(state: dict[str, Any], output: dict[str, Any]) -> None:
    artifacts = list(state.get("artifacts") or [])
    known = {str(item.get("file_id") or "") for item in artifacts}
    for artifact in output.get("artifacts") or []:
        if artifact["file_id"] not in known:
            artifacts.append(artifact)
            known.add(artifact["file_id"])
    state["artifacts"] = artifacts
    if output.get("message"):
        state["generation_message"] = output["message"]


class SplitMediaGenerationTask(_WorkflowContextTask):
    """Create correlated per-shot FlowFiles within the run fan-out limit."""

    TYPE = "splitMediaGeneration"
    NAME = "Split Media Generation"
    DESCRIPTION = "Fan out approved independent shots with stable job identity."
    EFFECTS = (CapabilityEffect.RESOURCE_READ,)
    IDEMPOTENCY = IdempotencyClass.PURE
    RELATIONSHIPS = ["jobs", "failure"]

    def execute(self, flowfile: FlowFile) -> list[FlowFile]:
        state = _state(flowfile)
        proposal = state.get("proposal") or {}
        shots = list(proposal.get("shots") or ())
        if not shots:
            shots = [{"id": "primary", "duration_seconds": (
                state.get("brief") or {}).get("duration_seconds")}]
        limit = int(getattr(
            getattr(self._context(), "limits", None), "max_fanout", 16))
        if len(shots) > limit:
            raise ValueError(
                f"media scenario has {len(shots)} jobs but max_fanout is {limit}")
        correlation_id = f"{self._context().run_id}:media-generation"
        results = []
        for index, shot in enumerate(shots):
            if not isinstance(shot, dict):
                raise ValueError("media scenario shots must be objects")
            job_id = str(shot.get("id") or "").strip()
            if not job_id:
                raise ValueError("media scenario shot id is required")
            fragment = flowfile.clone()
            fragment_state = dict(state)
            brief = dict(state.get("brief") or {})
            if shot.get("duration_seconds") is not None:
                brief["duration_seconds"] = shot["duration_seconds"]
            if str(shot.get("prompt") or "").strip():
                brief["prompt_refined"] = str(shot["prompt"]).strip()
            fragment_state["brief"] = brief
            fragment_state["execution_job"] = {
                "job_id": job_id, "index": index,
                "count": len(shots), "shot": dict(shot),
            }
            fragment_state["provider_jobs"] = []
            fragment_state["artifacts"] = []
            _put(fragment, fragment_state)
            fragment.set_attribute("fragment.identifier", correlation_id)
            fragment.set_attribute("fragment.index", str(index))
            fragment.set_attribute("fragment.count", str(len(shots)))
            fragment.set_attribute("route.relationship", "jobs")
            results.append(fragment)
        return results


class JoinMediaGenerationTask(MergeContentTask):
    """Checkpointable correlation join for per-shot Media Studio states."""

    TYPE = "joinMediaGeneration"
    NAME = "Join Media Generation"
    DESCRIPTION = "Join correlated provider jobs and artifacts before quality checks."
    AGENT_WORKFLOW_SAFE = True
    EFFECTS = (CapabilityEffect.RESOURCE_READ,)
    IDEMPOTENCY = IdempotencyClass.NATURAL
    AUTHORIZATION_TARGET_KIND = "workflow.runtime"
    RELATIONSHIPS = ["completed", "failure"]

    def _flush_bin(self, key: str) -> list[FlowFile]:
        buf = self._ordered(self._bins.pop(key, []))
        self._bin_created.pop(key, None)
        self._bin_expected.pop(key, None)
        self._bin_bytes.pop(key, None)
        if not buf:
            return []
        states = [_state(item) for item in buf]
        merged = dict(states[0])
        artifacts = []
        artifact_ids = set()
        jobs = []
        job_ids = set()
        messages = []
        for state in states:
            for artifact in state.get("artifacts") or ():
                file_id = str(artifact.get("file_id") or "")
                if file_id and file_id not in artifact_ids:
                    artifacts.append(dict(artifact))
                    artifact_ids.add(file_id)
            for job in state.get("provider_jobs") or ():
                job_id = str(job.get("job_id") or "")
                if job_id and job_id not in job_ids:
                    jobs.append(dict(job))
                    job_ids.add(job_id)
            message = str(state.get("generation_message") or "").strip()
            if message:
                messages.append(message)
        merged["artifacts"] = artifacts
        merged["provider_jobs"] = jobs
        merged["generation_messages"] = messages
        merged.pop("execution_job", None)
        merged["result"] = {"status": "completed"}
        result = buf[0].clone()
        _put(result, merged)
        result.set_attribute("merge.count", str(len(buf)))
        result.set_attribute("merge.correlation", key)
        result.set_attribute("route.relationship", "completed")
        return [result]


class SubmitMediaGenerationTask(_WorkflowContextTask):
    """Submit one media operation to the exact frozen service definition."""

    TYPE = "submitMediaGeneration"
    NAME = "Submit Media Generation"
    DESCRIPTION = (
        "Submit one allowlisted operation to the exact selected service and "
        "persist durable job correlation before provider execution.")
    EFFECTS = (
        CapabilityEffect.RESOURCE_READ,
        CapabilityEffect.RESOURCE_WRITE,
        CapabilityEffect.NETWORK_WRITE,
        CapabilityEffect.FILESYSTEM_WRITE,
        CapabilityEffect.EXTERNAL_SIDE_EFFECT,
    )
    IDEMPOTENCY = IdempotencyClass.KEYED_EFFECT
    AUTHORIZATION_TARGET_KIND = "media_service"
    RELATIONSHIPS = ["completed", "failure"]

    def get_parameter_schema(self) -> dict[str, Any]:
        return {
            "task_id": {
                "type": "string", "required": True,
                "description": "Stable flow task identifier used for job correlation.",
            },
            "base_url": {
                "type": "string", "required": False,
                "default": "http://localhost:9090",
                "description": "Gateway base URL used for temporary provider shares.",
            },
        }

    def workflow_authorization_target(self, flowfile: FlowFile) -> dict[str, Any]:
        context = self._context()
        state = _state(flowfile)
        selected = _selected(state)
        target = {
            "user_id": context.user_id,
            "conversation_id": context.conversation_id,
            "service_id": selected["service_id"],
            "service_revision": selected["service_revision"],
            "capability_id": selected["capability_id"],
            "scope": selected["scope"],
        }
        if str(selected.get("engine") or "") in {"comfyui", "ffmpeg"}:
            target["relay_id"] = str(
                (state.get("relay") or {}).get("relay_id") or "")
        return target

    def _validate_definition(self, selected: dict[str, Any]):
        context = self._context()
        from core.service_registry import ServiceRegistry
        registry = ServiceRegistry.get_instance()
        definition = registry.resolve_definition(
            str(selected.get("service_id") or ""),
            user_id=context.user_id,
            conv_id=context.conversation_id,
        )
        if definition is None:
            raise ValueError("exact selected service definition is unavailable")
        if str(getattr(definition, "scope", "") or "") != selected.get("scope"):
            raise ValueError("selected service scope changed")
        revision = compute_service_definition_revision(definition)
        if revision != selected.get("service_revision"):
            raise ValueError("selected service definition revision changed")
        return registry

    def _resolve_exact_service(self, registry, selected: dict[str, Any]):
        context = self._context()
        service = registry.resolve(
            selected["service_id"],
            user_id=context.user_id,
            conv_id=context.conversation_id,
        )
        if service is None:
            raise ValueError("exact selected service is unavailable")
        return service

    def execute(self, flowfile: FlowFile) -> list[FlowFile]:
        state = _state(flowfile)
        selected = _selected(state)
        operation = str((selected.get("operations") or [""])[0])
        media_kind = str((selected.get("media_kinds") or [""])[0])
        if operation not in _SUPPORTED_OPERATIONS:
            raise ValueError("selected operation is not allowed for submission")
        if (
            operation == "clone_voice"
            and state.get("voice_clone_authorized") is not True
        ):
            raise ValueError("voice cloning requires explicit durable authorization")

        registry = self._validate_definition(selected)
        context = self._context()
        project = state.get("project") or {}
        task_id = str(self.config.get("task_id") or "").strip()
        if not task_id:
            raise ValueError("submitMediaGeneration task_id is required")
        from core.media_project_store import MediaProjectStore
        store = MediaProjectStore.instance()
        execution_job_id = str(
            (state.get("execution_job") or {}).get("job_id") or "")
        durable_task_id = (
            f"{task_id}:{execution_job_id}" if execution_job_id else task_id)
        job = store.start_provider_job(
            project_id=str(project.get("project_id") or ""),
            user_id=context.user_id,
            conversation_id=context.conversation_id,
            run_id=context.run_id,
            task_id=durable_task_id,
            engine=str(selected.get("engine") or ""),
            service_id=str(selected.get("service_id") or ""),
            operation=operation,
            idempotency_key=f"{context.run_id}:{durable_task_id}",
        )

        if job["status"] == "completed":
            _merge_output(state, job["output"])
            self._record_state_job(state, job)
            _put(flowfile, state)
            flowfile.set_attribute("route.relationship", "completed")
            return [flowfile]
        if job["status"] == "failed":
            raise RuntimeError(
                "previous submission failed: " + str(job.get("error") or "unknown"))
        if job["status"] == "submitted":
            raise RuntimeError(
                "submitted provider job cannot be safely replayed without a "
                "provider recovery result")

        service = self._resolve_exact_service(registry, selected)
        relay_id = ""
        if str(selected.get("engine") or "") == "comfyui":
            relay_id = str((state.get("relay") or {}).get("relay_id") or "")
            if not relay_id:
                raise ValueError("ComfyUI generation requires a frozen media relay")
            service.set_runtime_context(
                user_id=context.user_id,
                conversation_id=context.conversation_id,
                agent_name=context.agent_name,
                relay_id=relay_id,
            )
        job = store.record_provider_submission(
            job["job_id"],
            user_id=context.user_id,
            conversation_id=context.conversation_id,
            provider_job_id=f"sync:{job['job_id']}",
        )
        handler = _handler(operation, media_kind)
        handler.set_service_resolver(lambda: (service, ""))
        handler.set_user_id(context.user_id)
        handler.set_conversation_id(context.conversation_id)
        handler.set_agent_name(context.agent_name)
        if callable(getattr(handler, "set_relay_id", None)):
            handler.set_relay_id(
                relay_id, (state.get("relay") or {}).get("local"))
        if hasattr(handler, "set_base_url"):
            handler.set_base_url(str(
                self.config.get("base_url") or "http://localhost:9090"))

        try:
            message = str(handler.execute(
                _arguments(state, selected)) or "").strip()
            if not message or message.startswith("Error"):
                raise RuntimeError(message or "media handler returned no result")
            output = {
                "message": message,
                "artifacts": _artifacts(message),
            }
            if operation != "clone_voice" and not output["artifacts"]:
                raise RuntimeError("media handler returned no FileStore artifact")
            job = store.finish_provider_job(
                job["job_id"],
                user_id=context.user_id,
                conversation_id=context.conversation_id,
                status="completed",
                output=output,
            )
        except Exception as exc:
            store.finish_provider_job(
                job["job_id"],
                user_id=context.user_id,
                conversation_id=context.conversation_id,
                status="failed",
                error=str(exc),
            )
            raise RuntimeError(str(exc)) from exc

        _merge_output(state, job["output"])
        self._record_state_job(state, job)
        state["result"] = {"status": "completed"}
        _put(flowfile, state)
        flowfile.set_attribute("route.relationship", "completed")
        return [flowfile]

    @staticmethod
    def _record_state_job(
        state: dict[str, Any], job: dict[str, Any],
    ) -> None:
        jobs = list(state.get("provider_jobs") or [])
        jobs = [
            item for item in jobs
            if str(item.get("job_id") or "") != job["job_id"]
        ]
        jobs.append(job)
        state["provider_jobs"] = jobs


class ComposeMediaTask(SubmitMediaGenerationTask):
    """Execute one validated recipe through the exact selected FFmpeg service."""

    TYPE = "composeMedia"
    NAME = "Compose Media"
    DESCRIPTION = "Execute a closed recipe through the pinned FFmpeg service."
    EFFECTS = (
        CapabilityEffect.RESOURCE_READ,
        CapabilityEffect.RESOURCE_WRITE,
        CapabilityEffect.PROCESS_EXECUTE,
        CapabilityEffect.FILESYSTEM_WRITE,
        CapabilityEffect.EXTERNAL_SIDE_EFFECT,
    )

    def execute(self, flowfile: FlowFile) -> list[FlowFile]:
        state = _state(flowfile)
        selected = _selected(state)
        operation = str((selected.get("operations") or [""])[0])
        media_kind = str((selected.get("media_kinds") or [""])[0])
        if (media_kind, operation) != ("compose", "compose"):
            raise ValueError("composeMedia requires a selected compose capability")
        from core.ffmpeg_recipe import FFmpegRecipe

        recipe = FFmpegRecipe.from_dict(state.get("ffmpeg_recipe") or {})
        registry = self._validate_definition(selected)
        service = self._resolve_exact_service(registry, selected)
        if not callable(getattr(service, "compose", None)):
            raise ValueError("exact selected service does not support composition")
        context = self._context()
        project_id = str((state.get("project") or {}).get("project_id") or "")
        task_id = str(self.config.get("task_id") or "").strip()
        if not task_id:
            raise ValueError("composeMedia task_id is required")
        service.set_runtime_context(
            user_id=context.user_id,
            conversation_id=context.conversation_id,
            agent_name=context.agent_name,
            relay_id=str((state.get("relay") or {}).get("relay_id") or ""),
            relay_local=(state.get("relay") or {}).get("local"),
        )
        output = service.compose(
            recipe=recipe.to_dict(),
            project_id=project_id,
            run_id=context.run_id,
            task_id=task_id,
            idempotency_key=f"{context.run_id}:{task_id}",
        )
        if not isinstance(output, dict) or not output.get("file_id"):
            raise RuntimeError("FFmpeg service returned no FileStore artifact")
        _merge_output(state, {"artifacts": [dict(output)]})
        state["result"] = {"status": "completed"}
        _put(flowfile, state)
        flowfile.set_attribute("route.relationship", "completed")
        return [flowfile]


TaskFactory.register(SubmitMediaGenerationTask)
TaskFactory.register(ComposeMediaTask)
TaskFactory.register(SplitMediaGenerationTask)
TaskFactory.register(JoinMediaGenerationTask)


__all__ = [
    "ComposeMediaTask", "JoinMediaGenerationTask",
    "SplitMediaGenerationTask", "SubmitMediaGenerationTask",
]
