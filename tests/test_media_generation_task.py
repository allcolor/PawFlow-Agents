"""WP6 exact-service Media Studio generation and recovery tests."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from core import FlowFile
from core._service_defs import ServiceDef
from core.file_store import FileStore
from core.media_project_store import MediaProjectStore
from core.service_definition_revision import compute_service_definition_revision
from tasks.ai.workflow.media_execution_tasks import (
    ComposeMediaTask,
    JoinMediaGenerationTask,
    SplitMediaGenerationTask,
    SubmitMediaGenerationTask,
    _arguments,
)


class ImageService:
    TYPE = "testImage"
    VERSION = "1.0.0"

    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("provider unavailable")
        return {"image_bytes": b"image-data", "content_type": "image/png"}


class Registry:
    def __init__(self, definition, service):
        self.definition = definition
        self.service = service
        self.resolve_calls = []

    def resolve_definition(self, service_id, *, user_id="", conv_id=""):
        assert user_id == "alice"
        assert conv_id == "conv-1"
        return self.definition if service_id == self.definition.service_id else None

    def resolve(self, service_id, *, user_id="", conv_id=""):
        self.resolve_calls.append(service_id)
        return self.service if service_id == self.definition.service_id else None


@pytest.fixture
def stores(tmp_path, monkeypatch):
    project_store = MediaProjectStore(tmp_path / "media.sqlite3")
    file_store = FileStore(base_dir=str(tmp_path / "files"))
    monkeypatch.setattr(MediaProjectStore, "_instance", project_store)
    monkeypatch.setattr(FileStore, "_instance", file_store)
    yield project_store, file_store
    monkeypatch.setattr(MediaProjectStore, "_instance", None)
    monkeypatch.setattr(FileStore, "_instance", None)


def _context():
    return SimpleNamespace(
        user_id="alice",
        conversation_id="conv-1",
        agent_name="Media Studio",
        run_id="run-1",
        root_turn_id="web:turn-1",
        service_snapshot={},
        limits=SimpleNamespace(max_fanout=4),
    )


def _project(store):
    return store.create_project(
        user_id="alice",
        conversation_id="conv-1",
        title="Image",
        idempotency_key="project-1",
    )


def _definition(model="v1"):
    return ServiceDef(
        service_id="image-service",
        service_type="testImage",
        scope="user",
        scope_id="alice",
        created_at=100,
        config={"model": model},
    )


def _state(store, definition=None):
    definition = definition or _definition()
    revision = compute_service_definition_revision(definition)
    selected = {
        "capability_id": "user:alice:image-service:generate:-",
        "engine": "test",
        "service_id": "image-service",
        "service_revision": revision,
        "scope": "user",
        "media_kinds": ["image"],
        "operations": ["generate"],
        "accepted_reference_roles": [],
        "output_content_types": ["image/png"],
        "tags": ["local", "private"],
        "preset_id": "",
        "model": "v1",
        "estimated_cost_usd": 0.0,
        "max_duration_seconds": None,
        "max_width": 2048,
        "max_height": 2048,
        "available": True,
        "unavailable_reason": "",
    }
    return {
        "request": {
            "schema_version": 1,
            "request": {
                "message": "Create a cinematic image.",
                "attachments": [],
            },
            "conversation": {"id": "conv-1", "agent": "Media Studio"},
            "turn": {
                "root_turn_id": "web:turn-1",
                "request_message_ids": ["web:turn-1"],
            },
            "parameters": {},
        },
        "project": _project(store),
        "brief": {
            "media_kind": "image",
            "operation": "generate",
            "prompt_refined": "Cinematic landscape",
            "negative_prompt": "",
            "style": "cinematic",
            "duration_seconds": None,
            "width": 1024,
            "height": 1024,
            "aspect_ratio": "1:1",
            "references": [],
            "audio": {},
            "output": {"content_type": "image/png"},
        },
        "capability_snapshot": {
            "digest": "snapshot-digest",
            "capabilities": [selected],
        },
        "selection": {
            "outcome": "selected",
            "selected": selected,
            "snapshot_digest": "snapshot-digest",
        },
        "provider_jobs": [],
        "artifacts": [],
    }


def _task():
    task = SubmitMediaGenerationTask({"task_id": "generate-image"})
    task.set_workflow_run_context(_context())
    return task


def _patch_registry(monkeypatch, registry):
    class Holder:
        @classmethod
        def get_instance(cls):
            return registry

    monkeypatch.setattr("core.service_registry.ServiceRegistry", Holder)


def test_exact_service_submission_persists_artifact_and_replays_without_call(
        stores, monkeypatch):
    project_store, file_store = stores
    definition = _definition()
    service = ImageService()
    registry = Registry(definition, service)
    _patch_registry(monkeypatch, registry)
    flowfile = FlowFile(content=json.dumps(
        _state(project_store, definition)).encode("utf-8"))
    task = _task()

    task.execute(flowfile)
    first = json.loads(flowfile.get_content().decode("utf-8"))
    assert service.calls[0]["prompt"] == "Cinematic landscape"
    assert registry.resolve_calls == ["image-service"]
    assert first["provider_jobs"][0]["status"] == "completed"
    assert first["artifacts"][0]["url"].startswith("fs://filestore/")
    assert file_store.get_metadata(first["artifacts"][0]["file_id"])

    task.execute(flowfile)
    replay = json.loads(flowfile.get_content().decode("utf-8"))
    assert len(service.calls) == 1
    assert replay["artifacts"] == first["artifacts"]


def test_split_and_join_media_jobs_preserve_order_and_combine_outputs():
    state = {
        "brief": {"duration_seconds": 10, "prompt_refined": "base"},
        "proposal": {"shots": [
            {"id": "opening", "duration_seconds": 2, "prompt": "first"},
            {"id": "closing", "duration_seconds": 3, "prompt": "last"},
        ]},
        "provider_jobs": [], "artifacts": [],
    }
    flowfile = FlowFile(content=json.dumps(state).encode("utf-8"))
    split = SplitMediaGenerationTask({})
    split.set_workflow_run_context(_context())

    fragments = split.execute(flowfile)

    assert [json.loads(item.get_content())["execution_job"]["job_id"]
            for item in fragments] == ["opening", "closing"]
    assert [json.loads(item.get_content())["brief"]["duration_seconds"]
            for item in fragments] == [2, 3]
    for index, fragment in enumerate(fragments):
        fragment_state = json.loads(fragment.get_content())
        fragment_state["provider_jobs"] = [{"job_id": f"job-{index}"}]
        fragment_state["artifacts"] = [{
            "file_id": f"file-{index}", "filename": f"{index}.png",
            "content_type": "image/png",
        }]
        fragment.set_content(json.dumps(fragment_state).encode("utf-8"))
    join = JoinMediaGenerationTask({
        "expected_count_attribute": "fragment.count", "min_entries": 1})
    assert join.execute(fragments[1]) == []
    merged = join.execute(fragments[0])

    assert len(merged) == 1
    merged_state = json.loads(merged[0].get_content())
    assert [item["job_id"] for item in merged_state["provider_jobs"]] == [
        "job-0", "job-1"]
    assert [item["file_id"] for item in merged_state["artifacts"]] == [
        "file-0", "file-1"]
    assert "execution_job" not in merged_state


def test_split_media_jobs_enforces_workflow_fanout_limit():
    state = {
        "brief": {},
        "proposal": {"shots": [
            {"id": f"shot-{index}", "duration_seconds": 1}
            for index in range(5)
        ]},
    }
    task = SplitMediaGenerationTask({})
    task.set_workflow_run_context(_context())

    with pytest.raises(ValueError, match="max_fanout is 4"):
        task.execute(FlowFile(content=json.dumps(state).encode("utf-8")))


def test_changed_service_definition_fails_before_provider_submission(
        stores, monkeypatch):
    project_store, _ = stores
    selected_definition = _definition("v1")
    current_definition = _definition("v2")
    service = ImageService()
    _patch_registry(monkeypatch, Registry(current_definition, service))
    flowfile = FlowFile(content=json.dumps(
        _state(project_store, selected_definition)).encode("utf-8"))

    with pytest.raises(ValueError, match="definition revision changed"):
        _task().execute(flowfile)
    assert service.calls == []


def test_missing_exact_service_never_falls_back(stores, monkeypatch):
    project_store, _ = stores
    definition = _definition()
    registry = Registry(definition, None)
    _patch_registry(monkeypatch, registry)
    flowfile = FlowFile(content=json.dumps(
        _state(project_store, definition)).encode("utf-8"))

    with pytest.raises(ValueError, match="exact selected service is unavailable"):
        _task().execute(flowfile)
    assert registry.resolve_calls == ["image-service"]


def test_comfyui_submission_requires_and_injects_frozen_relay(
        stores, monkeypatch):
    project_store, _ = stores
    definition = _definition()
    service = ImageService()
    service.context_calls = []

    def set_runtime_context(**kwargs):
        service.context_calls.append(kwargs)

    service.set_runtime_context = set_runtime_context
    _patch_registry(monkeypatch, Registry(definition, service))
    state = _state(project_store, definition)
    state["selection"]["selected"]["engine"] = "comfyui"
    state["relay"] = {"relay_id": "Relay-B", "local": False}
    flowfile = FlowFile(content=json.dumps(state).encode("utf-8"))

    _task().execute(flowfile)

    assert service.context_calls[0]["relay_id"] == "Relay-B"


def test_comfyui_authorization_target_contains_frozen_relay(stores):
    project_store, _ = stores
    state = _state(project_store)
    state["selection"]["selected"]["engine"] = "comfyui"
    state["relay"] = {"relay_id": "Relay-B", "local": False}
    flowfile = FlowFile(content=json.dumps(state).encode("utf-8"))

    target = _task().workflow_authorization_target(flowfile)

    assert target["relay_id"] == "Relay-B"


def test_audio_submission_arguments_preserve_reference_roles(stores):
    project_store, _ = stores
    state = _state(project_store)
    selected = state["selection"]["selected"]
    selected.update({
        "media_kinds": ["audio"],
        "operations": ["generate_audio"],
        "preset_id": "ace-step-music",
    })
    state["brief"].update({
        "media_kind": "audio",
        "operation": "generate_audio",
        "duration_seconds": 15,
        "references": [
            {
                "role": "source_audio",
                "file_id": "source-id",
                "filename": "source.wav",
            },
            {
                "role": "music_bed",
                "file_id": "music-id",
                "filename": "bed.wav",
            },
        ],
    })

    arguments = _arguments(state, selected)

    assert arguments["source_audio_url"] == (
        "fs://filestore/source-id/source.wav")
    assert arguments["music_bed_url"] == "fs://filestore/music-id/bed.wav"


def test_provider_failure_is_recorded_and_not_retried_implicitly(
        stores, monkeypatch):
    project_store, _ = stores
    definition = _definition()
    service = ImageService(fail=True)
    _patch_registry(monkeypatch, Registry(definition, service))
    state = _state(project_store, definition)
    flowfile = FlowFile(content=json.dumps(state).encode("utf-8"))
    task = _task()

    with pytest.raises(RuntimeError, match="provider unavailable"):
        task.execute(flowfile)
    assert len(service.calls) == 1

    with pytest.raises(RuntimeError, match="previous submission failed"):
        task.execute(flowfile)
    assert len(service.calls) == 1


def test_orphaned_submitted_job_fails_closed_instead_of_resubmitting(
        stores, monkeypatch):
    project_store, _ = stores
    definition = _definition()
    service = ImageService()
    _patch_registry(monkeypatch, Registry(definition, service))
    state = _state(project_store, definition)
    project = state["project"]
    job = project_store.start_provider_job(
        project_id=project["project_id"],
        user_id="alice",
        conversation_id="conv-1",
        run_id="run-1",
        task_id="generate-image",
        engine="test",
        service_id="image-service",
        operation="generate",
        idempotency_key="run-1:generate-image",
    )
    project_store.record_provider_submission(
        job["job_id"],
        user_id="alice",
        conversation_id="conv-1",
        provider_job_id=f"sync:{job['job_id']}",
    )
    flowfile = FlowFile(content=json.dumps(state).encode("utf-8"))

    with pytest.raises(RuntimeError, match="cannot be safely replayed"):
        _task().execute(flowfile)
    assert service.calls == []


def test_compose_media_uses_exact_ffmpeg_service_and_closed_recipe(
        stores, monkeypatch):
    project_store, _ = stores
    definition = _definition()

    class FFmpegService:
        def __init__(self):
            self.calls = []
            self.context = None

        def set_runtime_context(self, **kwargs):
            self.context = kwargs

        def compose(self, **kwargs):
            self.calls.append(kwargs)
            return {
                "file_id": "0123456789ab",
                "filename": "clip.mp4",
                "content_type": "video/mp4",
                "url": "fs://filestore/0123456789ab/clip.mp4",
            }

    service = FFmpegService()
    registry = Registry(definition, service)
    _patch_registry(monkeypatch, registry)
    state = _state(project_store, definition)
    state["selection"]["selected"].update({
        "media_kinds": ["compose"], "operations": ["compose"],
    })
    state["ffmpeg_recipe"] = {
        "schema_version": "1.0",
        "recipe_id": "ffmpeg_recipe_0123456789abcdef",
        "created_at": "2026-08-25T00:00:00+00:00",
        "operation": "trim",
        "inputs": ["a1b2c3d4e5f6"],
        "output_filename": "clip.mp4",
        "parameters": {"start": 0, "duration": 2.5},
    }
    flowfile = FlowFile(content=json.dumps(state).encode("utf-8"))
    task = ComposeMediaTask({"task_id": "compose-media"})
    task.set_workflow_run_context(_context())

    task.execute(flowfile)

    updated = json.loads(flowfile.get_content().decode("utf-8"))
    assert registry.resolve_calls == ["image-service"]
    assert service.context["user_id"] == "alice"
    assert service.calls[0]["project_id"] == state["project"]["project_id"]
    assert service.calls[0]["recipe"]["operation"] == "trim"
    assert updated["artifacts"][0]["file_id"] == "0123456789ab"
    assert updated["result"]["status"] == "completed"
