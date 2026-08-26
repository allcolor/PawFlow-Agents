"""WP4 deterministic Media Studio Workflow Agent task contracts."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import FlowFile, TaskFactory
from core.agent_contracts import CapabilityEffect, IdempotencyClass
from core.file_store import FileStore
from core.media_project_store import MediaProjectStore
from core.media_studio import canonical_digest
from core.workflow_agent_contracts import AgentWorkflowResult
from tasks.ai.workflow.media_tasks import (
    ApplyMediaRelayTask,
    PrepareMediaRelayTask,
    ApplyMediaCapabilityChoiceTask,
    ApplyMediaQuestionAnswersTask,
    ApplyMediaScenarioDecisionTask,
    ApplyMediaVoiceConsentTask,
    AppendMediaRevisionTask,
    FormatMediaStudioResultTask,
    LoadMediaProjectTask,
    PrepareMediaBriefTask,
    PrepareMediaExecutionTask,
    PrepareMediaIntentTask,
    PrepareMediaQuestionsTask,
    PrepareMediaVoiceConsentTask,
    ResolveMediaReferencesTask,
    RouteMediaIntentTask,
    SelectMediaCapabilityTask,
    SnapshotMediaCapabilitiesTask,
    ValidateMediaCompositionRecipeTask,
)
from tasks import register_all_tasks


def _request(message="Create a cinematic image.", attachments=()):
    return {
        "schema_version": 1,
        "request": {"message": message, "attachments": list(attachments)},
        "conversation": {"id": "conv-1", "agent": "Media Studio"},
        "turn": {
            "root_turn_id": "web:turn-1",
            "request_message_ids": ["web:turn-1"],
        },
        "parameters": {},
    }


def _flowfile(message="Create a cinematic image.", attachments=()):
    return FlowFile(content=json.dumps(
        _request(message, attachments)).encode("utf-8"))


def _context(*, service_snapshot=None):
    return SimpleNamespace(
        user_id="alice",
        conversation_id="conv-1",
        agent_name="Media Studio",
        run_id="run-1",
        root_turn_id="web:turn-1",
        service_snapshot=dict(service_snapshot or {}),
    )


def _inject(task):
    task.set_workflow_run_context(_context())
    return task


def _intent_payload(**changes):
    payload = {
        "kind": "image",
        "operation": "generate",
        "confidence": 0.96,
        "explanation": "The user requested an image.",
        "requires_references": False,
        "requires_scenario": False,
        "missing_fields": [],
        "requested_project_id": "",
        "revision_selector": "",
        "relay_references": [],
        "response": "",
    }
    payload.update(changes)
    return payload


def _brief_state(**changes):
    state = {
        "request": _request(),
        "media_intent": {
            "intent_id": "intent_11111111-1111-4111-8111-111111111111",
            "created_at": "2026-08-25T17:00:00+00:00",
            **{k: v for k, v in _intent_payload().items() if k != "response"},
        },
        "references": [],
    }
    state.update(changes)
    return state


def test_intent_gate_preserves_request_and_stops_unsupported_before_access():
    flowfile = _flowfile("Fix the CSS navigation.")
    PrepareMediaIntentTask({}).execute(flowfile)
    assert "Fix the CSS navigation." in flowfile.get_attribute(
        "media.intent_prompt")

    flowfile.set_attribute("media.intent", json.dumps(_intent_payload(
        kind="unsupported",
        operation="unsupported",
        confidence=0.99,
        explanation="This is a coding request.",
        response="Please use a general-purpose agent.",
    )))
    RouteMediaIntentTask({}).execute(flowfile)

    state = json.loads(flowfile.get_content().decode("utf-8"))
    assert flowfile.get_attribute("route.relationship") == "unsupported"
    assert state["result"]["response"] == "Please use a general-purpose agent."
    assert "project" not in state
    assert "capability_snapshot" not in state


def test_reference_resolution_normalizes_browser_filestore_attachments(
        tmp_path, monkeypatch):
    store = FileStore(base_dir=str(tmp_path / "files"))
    monkeypatch.setattr(
        FileStore, "instance", classmethod(lambda cls: store))
    file_id = store.store(
        "portrait.png", b"image", "image/png",
        conversation_id="conv-1", user_id="alice")
    flowfile = _flowfile(attachments=({
        "file_id": file_id,
        "filename": "untrusted-name.jpg",
        "mime_type": "application/octet-stream",
        "source_message_id": "forged-message",
        "source_relay_id": "forged-relay",
        "source_path": "/forged/path",
    },))
    flowfile.set_attribute("media.intent", json.dumps(_intent_payload(
        kind="video", operation="image_to_video", requires_references=True)))
    RouteMediaIntentTask({}).execute(flowfile)
    _inject(ResolveMediaReferencesTask({})).execute(flowfile)
    state = json.loads(flowfile.get_content().decode("utf-8"))
    assert state["references"][0]["role"] == "source_image"
    assert state["references"][0]["file_id"] == file_id
    assert state["references"][0]["filename"] == "portrait.png"
    assert state["references"][0]["content_type"] == "image/png"
    assert state["references"][0]["source_message_id"] == "web:turn-1"

    invalid = _flowfile(attachments=({
        "path": "/workspace/private.png",
        "filename": "private.png",
        "content_type": "image/png",
        "role": "source_image",
        "source_message_id": "web:turn-1",
    },))
    with pytest.raises(ValueError, match="file_id"):
        _inject(ResolveMediaReferencesTask({})).execute(invalid)


def test_relay_path_reference_overrides_frozen_default_and_imports_once(
        tmp_path, monkeypatch):
    store = FileStore(base_dir=str(tmp_path / "files"))
    monkeypatch.setattr(
        FileStore, "instance", classmethod(lambda cls: store))
    copied = []

    class _Relay:
        _service_id = "MyWorkspace"

        def copy_file_to_local(self, source, destination, local=False):
            copied.append((source, local))
            Path(destination).write_bytes(b"relay-image")
            return {"written": 11}

    registry = SimpleNamespace(resolve=lambda *args, **kwargs: _Relay())
    from core.service_registry import ServiceRegistry
    monkeypatch.setattr(
        ServiceRegistry, "get_instance", classmethod(lambda cls: registry))
    context = _context(service_snapshot={"relay": {
        "selected_id": "OtherRelay",
        "candidates": ["OtherRelay", "MyWorkspace"],
        "source": "agent_default",
        "local": False,
    }})
    payload = _intent_payload(
        kind="video", operation="image_to_video", requires_references=True,
        relay_references=[{
            "relay_id": "MyWorkspace",
            "path": "/workspace/assets/portrait.png",
            "role": "source_image",
        }],
    )

    def _original_flowfile():
        flowfile = _flowfile("Animate the relay portrait.")
        flowfile.set_attribute("media.intent", json.dumps(payload))
        RouteMediaIntentTask({}).execute(flowfile)
        task = PrepareMediaRelayTask({})
        task.set_workflow_run_context(context)
        task.execute(flowfile)
        return flowfile

    first = _original_flowfile()
    task = ResolveMediaReferencesTask({})
    task.set_workflow_run_context(context)
    task.execute(first)
    first_reference = json.loads(first.get_content().decode("utf-8"))[
        "references"][0]

    replay = _original_flowfile()
    replay_task = ResolveMediaReferencesTask({})
    replay_task.set_workflow_run_context(context)
    replay_task.execute(replay)
    replay_reference = json.loads(replay.get_content().decode("utf-8"))[
        "references"][0]

    assert copied == [("/workspace/assets/portrait.png", False)]
    assert replay_reference["file_id"] == first_reference["file_id"]
    assert first_reference["source_relay_id"] == "MyWorkspace"
    assert first_reference["source_path"] == "/workspace/assets/portrait.png"
    assert first_reference["role"] == "source_image"


def test_prepare_brief_and_grouped_questions_are_deterministic():
    state = _brief_state()
    state["media_intent"]["missing_fields"] = ["duration_seconds", "aspect_ratio"]
    flowfile = FlowFile(content=json.dumps(state).encode("utf-8"))
    PrepareMediaBriefTask({}).execute(flowfile)
    assert state["request"]["request"]["message"] in flowfile.get_attribute(
        "media.brief_prompt")

    PrepareMediaQuestionsTask({}).execute(flowfile)
    result = json.loads(flowfile.get_content().decode("utf-8"))
    question = result["question"]
    assert flowfile.get_attribute("route.relationship") == "ask"
    assert question["kind"] == "form"
    assert [field["name"] for field in question["response_schema"]["fields"]] == [
        "duration_seconds", "aspect_ratio",
    ]
    assert json.loads(flowfile.get_attribute("media.question")) == question


def test_reference_question_uses_multiple_file_field_and_authorized_answers(
        tmp_path, monkeypatch):
    store = FileStore(base_dir=str(tmp_path / "files"))
    monkeypatch.setattr(
        FileStore, "instance", classmethod(lambda cls: store))
    first = store.store(
        "first.png", b"one", "image/png",
        conversation_id="conv-1", user_id="alice")
    second = store.store(
        "last.png", b"two", "image/png",
        conversation_id="conv-1", user_id="alice")
    state = _brief_state()
    state["brief"] = {
        "media_kind": "video", "operation": "frame_to_video",
        "references": [], "audio": {},
    }
    state["media_intent"].update({
        "operation": "frame_to_video", "requires_references": True})
    flowfile = FlowFile(content=json.dumps(state).encode("utf-8"))

    PrepareMediaQuestionsTask({}).execute(flowfile)
    question = json.loads(flowfile.get_attribute("media.question"))
    field = question["response_schema"]["fields"][0]
    assert field == {
        "name": "references", "label": "Reference files",
        "type": "file", "required": True, "multiple": True,
    }

    flowfile.set_attribute("durable.wait.value", json.dumps({
        "status": "answered",
        "answer": {"references": [
            {"file_id": first, "name": "spoofed.png"},
            {"file_id": second, "name": "spoofed-again.png"},
        ]},
    }))
    _inject(ApplyMediaQuestionAnswersTask({})).execute(flowfile)
    references = json.loads(flowfile.get_content().decode("utf-8"))["references"]
    assert [item["role"] for item in references] == ["start_frame", "end_frame"]
    assert [item["filename"] for item in references] == ["first.png", "last.png"]
    assert {item["source_message_id"] for item in references} == {"web:turn-1"}


def test_answered_media_questions_feed_the_next_brief_and_are_not_reasked():
    state = _brief_state()
    state["media_intent"]["missing_fields"] = ["duration_seconds", "aspect_ratio"]
    flowfile = FlowFile(
        content=json.dumps(state).encode("utf-8"),
        attributes={"durable.wait.value": json.dumps({
            "status": "answered",
            "answer": {"duration_seconds": 4.5, "aspect_ratio": "16:9"},
        })},
    )

    PrepareMediaBriefTask({}).execute(flowfile)

    prompt = flowfile.get_attribute("media.brief_prompt")
    assert '"duration_seconds": 4.5' in prompt
    assert '"aspect_ratio": "16:9"' in prompt
    PrepareMediaQuestionsTask({}).execute(flowfile)
    updated = json.loads(flowfile.get_content().decode("utf-8"))
    assert flowfile.get_attribute("route.relationship") == "ready"
    assert updated["user_answers"] == {
        "aspect_ratio": "16:9", "duration_seconds": 4.5,
    }


def test_media_question_answers_update_validated_brief_without_llm_loop():
    state = _brief_state()
    state["brief"] = {
        "media_kind": "video", "operation": "generate",
        "duration_seconds": None, "aspect_ratio": "",
        "audio": {},
    }
    state["media_intent"]["missing_fields"] = [
        "duration_seconds", "aspect_ratio", "budget",
    ]
    flowfile = FlowFile(
        content=json.dumps(state).encode("utf-8"),
        attributes={
            "durable.wait.status": "signaled",
            "durable.wait.value": json.dumps({
                "status": "answered",
                "answer": {
                    "duration_seconds": 6.0,
                    "aspect_ratio": "16:9",
                    "budget": 12.5,
                },
            }),
        },
    )

    ApplyMediaQuestionAnswersTask({}).execute(flowfile)

    updated = json.loads(flowfile.get_content().decode("utf-8"))
    assert updated["brief"]["duration_seconds"] == 6.0
    assert updated["brief"]["aspect_ratio"] == "16:9"
    assert updated["selection_preferences"]["max_cost_usd"] == 12.5
    assert updated["media_intent"]["missing_fields"] == []
    assert flowfile.get_attribute("durable.wait.status") is None
    assert flowfile.get_attribute("durable.wait.value") is None


@pytest.mark.parametrize("answer, relationship", [
    ("produce", "approved"),
    ("revise", "revise"),
    ("cancel", "cancelled"),
])
def test_media_scenario_decision_is_explicit(answer, relationship):
    state = _brief_state()
    state["proposal"] = {"proposal_id": "proposal-1", "digest": "a" * 64}
    flowfile = FlowFile(
        content=json.dumps(state).encode("utf-8"),
        attributes={"durable.wait.value": json.dumps({
            "status": "answered", "answer": answer,
        })},
    )

    ApplyMediaScenarioDecisionTask({}).execute(flowfile)

    updated = json.loads(flowfile.get_content().decode("utf-8"))
    assert flowfile.get_attribute("route.relationship") == relationship
    assert updated.get("proposal_approved") is (answer == "produce")
    if answer != "produce":
        assert updated["result"]["status"] in {"revise", "cancelled"}


def test_voice_clone_requires_explicit_durable_consent():
    state = _brief_state()
    state["brief"] = {"media_kind": "voice_clone", "operation": "clone_voice"}
    flowfile = FlowFile(content=json.dumps(state).encode("utf-8"))
    PrepareMediaVoiceConsentTask({}).execute(flowfile)
    assert flowfile.get_attribute("route.relationship") == "ask"

    flowfile.set_attribute("durable.wait.value", json.dumps({
        "status": "answered", "answer": "yes",
    }))
    ApplyMediaVoiceConsentTask({}).execute(flowfile)
    assert flowfile.get_attribute("route.relationship") == "approved"
    assert json.loads(flowfile.get_content().decode("utf-8"))[
        "voice_clone_authorized"] is True

    denied = FlowFile(
        content=json.dumps(state).encode("utf-8"),
        attributes={"durable.wait.value": json.dumps({
            "status": "answered", "answer": "no",
        })},
    )
    ApplyMediaVoiceConsentTask({}).execute(denied)
    denied_state = json.loads(denied.get_content().decode("utf-8"))
    assert denied.get_attribute("route.relationship") == "cancelled"
    assert denied_state["result"]["status"] == "cancelled"


def test_media_capability_choice_must_match_frozen_alternative():
    state = _brief_state()
    state["selection"] = {
        "outcome": "user_choice",
        "selected": {"capability_id": "cap-a", "service_id": "svc-a"},
        "alternatives": [{"capability_id": "cap-b", "service_id": "svc-b"}],
        "snapshot_digest": "b" * 64,
    }
    flowfile = FlowFile(
        content=json.dumps(state).encode("utf-8"),
        attributes={"durable.wait.value": json.dumps({
            "status": "answered", "answer": "cap-b",
        })},
    )

    ApplyMediaCapabilityChoiceTask({}).execute(flowfile)

    updated = json.loads(flowfile.get_content().decode("utf-8"))
    assert updated["selection"]["outcome"] == "selected"
    assert updated["selection"]["selected"]["service_id"] == "svc-b"
    assert flowfile.get_attribute("route.relationship") == "selected"

    flowfile.set_content(json.dumps(state).encode("utf-8"))
    flowfile.set_attribute("durable.wait.value", json.dumps({
        "status": "answered", "answer": "stale-capability",
    }))
    with pytest.raises(ValueError, match="frozen capability alternatives"):
        ApplyMediaCapabilityChoiceTask({}).execute(flowfile)


def test_media_execution_routes_and_validates_closed_ffmpeg_recipe():
    state = _brief_state()
    state["brief"] = {
        "media_kind": "compose", "operation": "compose",
        "objective": "Trim the source clip.", "references": [],
    }
    state["selection"] = {
        "outcome": "selected",
        "selected": {"media_kinds": ["compose"], "operations": ["compose"]},
    }
    flowfile = FlowFile(content=json.dumps(state).encode("utf-8"))

    PrepareMediaExecutionTask({}).execute(flowfile)

    assert flowfile.get_attribute("route.relationship") == "compose"
    assert "closed FFmpeg recipe" in flowfile.get_attribute(
        "media.ffmpeg_recipe_prompt")
    flowfile.set_attribute("media.ffmpeg_recipe", json.dumps({
        "operation": "trim",
        "inputs": ["a1b2c3d4e5f6"],
        "output_filename": "clip.mp4",
        "parameters": {"start": 0, "duration": 2.5},
    }))
    ValidateMediaCompositionRecipeTask({}).execute(flowfile)
    updated = json.loads(flowfile.get_content().decode("utf-8"))
    assert updated["ffmpeg_recipe"]["operation"] == "trim"
    assert updated["ffmpeg_recipe"]["recipe_id"].startswith("ffmpeg_recipe_")

    flowfile.set_attribute("media.ffmpeg_recipe", json.dumps({
        "operation": "trim", "inputs": ["a1b2c3d4e5f6"],
        "output_filename": "clip.mp4", "command": "touch owned",
    }))
    with pytest.raises(ValueError, match="unsupported recipe fields"):
        ValidateMediaCompositionRecipeTask({}).execute(flowfile)


def test_media_relay_preflight_auto_freezes_snapshot_selection():
    flowfile = FlowFile(content=json.dumps(_brief_state()).encode("utf-8"))
    task = PrepareMediaRelayTask({})
    task.set_workflow_run_context(_context(service_snapshot={"relay": {
        "selected_id": "Relay-A", "candidates": ["Relay-A"],
        "source": "unique", "local": False,
    }}))

    task.execute(flowfile)

    state = json.loads(flowfile.get_content().decode("utf-8"))
    assert flowfile.get_attribute("route.relationship") == "ready"
    assert state["relay"] == {
        "relay_id": "Relay-A", "local": False, "source": "unique"}


def test_media_relay_preflight_asks_and_applies_only_frozen_candidate(monkeypatch):
    flowfile = FlowFile(content=json.dumps(_brief_state()).encode("utf-8"))
    context = _context(service_snapshot={"relay": {
        "selected_id": "", "candidates": ["Relay-A", "Relay-B"],
        "selection_required": True,
    }})
    prepare = PrepareMediaRelayTask({})
    prepare.set_workflow_run_context(context)
    prepare.execute(flowfile)
    question = json.loads(flowfile.get_attribute("media.relay_question"))
    assert flowfile.get_attribute("route.relationship") == "ask"
    assert question["response_schema"]["fields"][0]["options"] == [
        "Relay-A", "Relay-B"]

    monkeypatch.setattr(
        "core.relay_bindings.get_default_local",
        lambda cid, relay_id="", agent="": True)
    flowfile.set_attribute("durable.wait.value", json.dumps({
        "status": "answered", "answer": {"relay": "relay-b"},
    }))
    apply = ApplyMediaRelayTask({})
    apply.set_workflow_run_context(context)
    apply.execute(flowfile)
    state = json.loads(flowfile.get_content().decode("utf-8"))
    assert state["relay"] == {
        "relay_id": "Relay-B", "local": True, "source": "durable_choice"}


def test_snapshot_and_selection_use_the_frozen_capability_identity(monkeypatch):
    capability = {
        "capability_id": "user:alice:image-service:generate:-",
        "engine": "provider",
        "service_id": "image-service",
        "service_revision": "rev-1",
        "scope": "user",
        "media_kinds": ["image"],
        "operations": ["generate"],
        "accepted_reference_roles": [],
        "output_content_types": ["image/png"],
        "tags": ["local", "private"],
        "preset_id": "",
        "model": "",
        "estimated_cost_usd": 0.0,
        "max_duration_seconds": None,
        "max_width": 2048,
        "max_height": 2048,
        "available": True,
        "unavailable_reason": "",
    }
    snapshot = {
        "snapshot_id": "media_snapshot_11111111-1111-4111-8111-111111111111",
        "created_at": "2026-08-25T17:00:00+00:00",
        "user_id": "alice",
        "conversation_id": "conv-1",
        "capabilities": [capability],
    }
    snapshot["digest"] = canonical_digest(snapshot)

    class FrozenSnapshot:
        def to_dict(self):
            return snapshot

    monkeypatch.setattr(
        "core.media_capability_discovery.snapshot_media_capabilities",
        lambda user_id, conversation_id: FrozenSnapshot(),
    )
    flowfile = FlowFile(content=json.dumps(_brief_state(
        brief={
            "media_kind": "image", "operation": "generate",
            "duration_seconds": None, "width": 1024, "height": 1024,
            "references": [], "output": {"content_type": "image/png"},
        },
    )).encode("utf-8"))

    _inject(SnapshotMediaCapabilitiesTask({})).execute(flowfile)
    SelectMediaCapabilityTask({"question_mode": "automatic"}).execute(flowfile)

    state = json.loads(flowfile.get_content().decode("utf-8"))
    assert state["selection"]["outcome"] == "selected"
    assert state["selection"]["selected"]["service_id"] == "image-service"
    assert state["selection"]["snapshot_digest"] == snapshot["digest"]


def test_project_creation_and_revision_append_are_scoped_and_idempotent(
        tmp_path, monkeypatch):
    store = MediaProjectStore(tmp_path / "media.sqlite3")
    monkeypatch.setattr(
        "core.media_project_store.MediaProjectStore.instance",
        classmethod(lambda cls: store),
    )
    flowfile = FlowFile(content=json.dumps(_brief_state(
        brief={
            "brief_id": "brief_11111111-1111-4111-8111-111111111111",
            "created_at": "2026-08-25T17:00:00+00:00",
            "media_kind": "image", "operation": "generate",
            "objective": "Create an image.",
            "prompt_original": "Create a cinematic image.",
            "prompt_refined": "Create a cinematic image.",
            "references": [], "assumptions": [], "exact_prompt": True,
            "audio": {}, "output": {},
        },
        selection={"outcome": "selected", "selected": {
            "capability_id": "cap-1", "service_id": "image-service",
        }},
        artifacts=[{
            "file_id": "file-output", "filename": "output.png",
            "content_type": "image/png",
            "url": "fs://filestore/file-output/output.png",
        }],
        qa_report={"valid": True},
    )).encode("utf-8"))

    task = _inject(LoadMediaProjectTask({}))
    task.execute(flowfile)
    first = json.loads(flowfile.get_content().decode("utf-8"))["project"]
    task.execute(flowfile)
    replay = json.loads(flowfile.get_content().decode("utf-8"))["project"]
    assert replay["project_id"] == first["project_id"]

    append = _inject(AppendMediaRevisionTask({}))
    append.execute(flowfile)
    state = json.loads(flowfile.get_content().decode("utf-8"))
    revision_id = state["revision"]["revision_id"]
    append.execute(flowfile)
    assert json.loads(flowfile.get_content().decode("utf-8"))[
        "revision"]["revision_id"] == revision_id
    assert len(store.list_revisions(
        first["project_id"], user_id="alice", conversation_id="conv-1")) == 1


def test_terminal_result_reports_exact_filestore_artifacts_and_metadata():
    state = _brief_state(
        project={"project_id": "media-project-1"},
        revision={"revision_id": "media-revision-1"},
        artifacts=[{
            "file_id": "file-output",
            "filename": "result.png",
            "content_type": "image/png",
            "url": "fs://filestore/file-output/result.png",
        }],
        result={"status": "completed"},
    )
    flowfile = FlowFile(content=json.dumps(state).encode("utf-8"))
    _inject(FormatMediaStudioResultTask({})).execute(flowfile)

    result = AgentWorkflowResult.from_dict(json.loads(
        flowfile.get_content().decode("utf-8")))
    assert result.answered_turn_ids == ("web:turn-1",)
    assert result.artifacts[0].id == "file-output"
    assert "media-project-1" in result.response
    assert "media-revision-1" in result.response


def test_media_tasks_are_registered_with_explicit_workflow_metadata():
    register_all_tasks()
    expected = {
        "prepareMediaIntent": IdempotencyClass.PURE,
        "routeMediaIntent": IdempotencyClass.PURE,
        "loadMediaProject": IdempotencyClass.KEYED_EFFECT,
        "resolveMediaReferences": IdempotencyClass.KEYED_EFFECT,
        "snapshotMediaCapabilities": IdempotencyClass.RUN_CACHED,
        "prepareMediaBrief": IdempotencyClass.PURE,
        "prepareMediaQuestions": IdempotencyClass.PURE,
        "selectMediaCapability": IdempotencyClass.PURE,
        "appendMediaRevision": IdempotencyClass.KEYED_EFFECT,
        "formatMediaStudioResult": IdempotencyClass.PURE,
    }
    for task_type, idempotency in expected.items():
        task = TaskFactory.get(task_type)
        assert task.AGENT_WORKFLOW_SAFE is True
        assert task.IDEMPOTENCY == idempotency
        assert task.EFFECTS
    assert CapabilityEffect.RESOURCE_WRITE in TaskFactory.get(
        "appendMediaRevision").EFFECTS
    assert CapabilityEffect.FILESYSTEM_READ in TaskFactory.get(
        "resolveMediaReferences").EFFECTS
