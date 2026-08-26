"""Append-only Media Studio project and provider-job persistence."""

import pytest

from core.media_project_store import MediaProjectConflict, MediaProjectStore


@pytest.fixture
def store(tmp_path):
    return MediaProjectStore(tmp_path / "media.sqlite3")


def create_project(store, key="project-create"):
    return store.create_project(
        user_id="alice",
        conversation_id="conv-1",
        title="Launch teaser",
        idempotency_key=key,
    )


def append(store, project, *, key="revision-1", **changes):
    values = {
        "project_id": project["project_id"],
        "user_id": "alice",
        "conversation_id": "conv-1",
        "expected_state_revision": project["state_revision"],
        "idempotency_key": key,
        "run_id": "run-1",
        "root_turn_id": "turn-1",
        "user_request": "Create a five second teaser.",
        "intent": {"kind": "video", "operation": "generate"},
        "brief": {"media_kind": "video", "prompt_original": "A teaser"},
        "selection": {"capability_id": "comfy-video"},
        "artifacts": [{"file_id": "file-video", "filename": "teaser.mp4"}],
        "qa_report": {"valid": True},
    }
    values.update(changes)
    return store.append_revision(**values)


def test_project_creation_is_scoped_and_idempotent(store):
    project = create_project(store)
    replay = create_project(store)

    assert replay["project_id"] == project["project_id"]
    assert project["state_revision"] == 1
    assert store.list_projects(
        user_id="alice", conversation_id="conv-1"
    )[0]["title"] == "Launch teaser"
    with pytest.raises(KeyError):
        store.get_project(
            project["project_id"], user_id="bob", conversation_id="conv-1")
    with pytest.raises(MediaProjectConflict, match="different input"):
        store.create_project(
            user_id="alice",
            conversation_id="conv-1",
            title="Different title",
            idempotency_key="project-create",
        )


def test_revision_is_append_only_parented_and_updates_project_revision(store):
    project = create_project(store)
    first = append(store, project)
    current = store.get_project(
        project["project_id"], user_id="alice", conversation_id="conv-1")
    second = append(
        store,
        current,
        key="revision-2",
        run_id="run-2",
        root_turn_id="turn-2",
        user_request="Make it darker.",
        artifacts=[{"url": "fs://filestore/file-dark/teaser.mp4"}],
    )

    assert first["parent_revision_id"] is None
    assert second["parent_revision_id"] == first["revision_id"]
    assert current["state_revision"] == 2
    assert store.get_project(
        project["project_id"], user_id="alice", conversation_id="conv-1"
    )["state_revision"] == 3
    assert [row["revision_id"] for row in store.list_revisions(
        project["project_id"], user_id="alice", conversation_id="conv-1"
    )] == [first["revision_id"], second["revision_id"]]


def test_revision_replay_is_idempotent_before_stale_revision_check(store):
    project = create_project(store)
    revision = append(store, project)
    replay = append(store, project)

    assert replay["revision_id"] == revision["revision_id"]
    with pytest.raises(MediaProjectConflict, match="different input"):
        append(store, project, user_request="Different request")


def test_concurrent_project_revision_fails_closed(store):
    project = create_project(store)
    append(store, project)
    with pytest.raises(MediaProjectConflict, match="state revision"):
        append(store, project, key="revision-2")


def test_parent_revision_must_belong_to_same_project(store):
    first_project = create_project(store, "project-1")
    first_revision = append(store, first_project)
    second_project = create_project(store, "project-2")

    with pytest.raises(MediaProjectConflict, match="does not belong"):
        append(
            store,
            second_project,
            key="revision-other",
            parent_revision_id=first_revision["revision_id"],
        )


def test_provider_job_identity_is_reserved_before_wait_and_immutable(store):
    project = create_project(store)
    job = store.start_provider_job(
        project_id=project["project_id"],
        user_id="alice",
        conversation_id="conv-1",
        run_id="run-1",
        task_id="generate",
        engine="comfyui",
        service_id="comfy-video",
        operation="image_to_video",
        idempotency_key="run-1:generate",
    )
    replay = store.start_provider_job(
        project_id=project["project_id"],
        user_id="alice",
        conversation_id="conv-1",
        run_id="run-1",
        task_id="generate",
        engine="comfyui",
        service_id="comfy-video",
        operation="image_to_video",
        idempotency_key="run-1:generate",
    )
    submitted = store.record_provider_submission(
        job["job_id"],
        user_id="alice",
        conversation_id="conv-1",
        provider_job_id="prompt-123",
    )
    completed = store.finish_provider_job(
        job["job_id"],
        user_id="alice",
        conversation_id="conv-1",
        status="completed",
        output={"file_id": "file-video"},
    )

    assert replay["job_id"] == job["job_id"]
    assert submitted["status"] == "submitted"
    assert completed["status"] == "completed"
    with pytest.raises(MediaProjectConflict, match="cannot be replaced"):
        store.record_provider_submission(
            job["job_id"],
            user_id="alice",
            conversation_id="conv-1",
            provider_job_id="prompt-456",
        )


def test_failed_job_requires_error_and_conversation_cleanup_cascades(store):
    project = create_project(store)
    job = store.start_provider_job(
        project_id=project["project_id"],
        user_id="alice",
        conversation_id="conv-1",
        run_id="run-1",
        task_id="generate",
        engine="provider",
        service_id="video-api",
        operation="generate",
        idempotency_key="job-failure",
    )
    store.record_provider_submission(
        job["job_id"],
        user_id="alice",
        conversation_id="conv-1",
        provider_job_id="vendor-1",
    )
    with pytest.raises(ValueError, match="error"):
        store.finish_provider_job(
            job["job_id"],
            user_id="alice",
            conversation_id="conv-1",
            status="failed",
        )

    assert store.delete_conversation(
        user_id="alice", conversation_id="conv-1") == 1
    with pytest.raises(KeyError):
        store.get_provider_job(
            job["job_id"], user_id="alice", conversation_id="conv-1")


def test_artifacts_must_be_filestore_or_explicit_relay_references(store):
    project = create_project(store)
    with pytest.raises(ValueError, match="FileStore or relay"):
        append(store, project, artifacts=[{"path": "relative.mp4"}])
