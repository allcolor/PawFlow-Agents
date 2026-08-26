from types import SimpleNamespace

import pytest

from core.ffmpeg_recipe import FFmpegRecipe
from services.ffmpeg_media_service import FFmpegMediaService


FID = "a1b2c3d4e5f6"


class FakeJobs:
    def __init__(self):
        self.job = {
            "job_id": "media_job_1",
            "status": "created",
            "output": {},
            "error": "",
        }
        self.finish_calls = []

    def start_provider_job(self, **kwargs):
        return dict(self.job)

    def record_provider_submission(self, job_id, **kwargs):
        self.job["status"] = "submitted"
        self.job["provider_job_id"] = kwargs["provider_job_id"]
        return dict(self.job)

    def finish_provider_job(self, job_id, **kwargs):
        self.finish_calls.append(kwargs)
        self.job["status"] = kwargs["status"]
        self.job["output"] = kwargs.get("output", {})
        self.job["error"] = kwargs.get("error", "")
        return dict(self.job)


class FakeFiles:
    def __init__(self):
        self.stored = []

    def get_required(self, file_id, user_id, conversation_id):
        assert (file_id, user_id, conversation_id) == (FID, "user-1", "conv-1")
        return "source.mp4", b"source-bytes", "video/mp4"

    def store(self, filename, content, content_type, **kwargs):
        self.stored.append((filename, content, content_type, kwargs))
        return "123456789abc"


class FakeRelay:
    def __init__(self, fail_ffmpeg=False):
        self.calls = []
        self.files = {}
        self.fail_ffmpeg = fail_ffmpeg

    def mkdir(self, path, local=False):
        self.calls.append(("mkdir", path, local))

    def write_file(self, path, content, local=False):
        self.calls.append(("write", path, local))
        self.files[path] = content

    def exec_argv(self, path, argv, timeout=None, local=False):
        self.calls.append(("exec_argv", path, list(argv), local))
        if argv[0] == "ffprobe":
            return {
                "returncode": 0,
                "stdout": '{"format":{"duration":"1.0"},"streams":[]}',
                "stderr": "",
            }
        if self.fail_ffmpeg:
            return {"returncode": 1, "stdout": "", "stderr": "bad media"}
        self.files[f"{path}/{argv[-1]}"] = b"rendered"
        return {"returncode": 0, "stdout": "", "stderr": ""}

    def stat(self, path, local=False):
        return SimpleNamespace(size=len(self.files[path]))

    def read_file(self, path, local=False):
        return self.files[path]

    def delete_file(self, path, local=False):
        self.calls.append(("delete", path, local))
        self.files.pop(path, None)


@pytest.fixture
def environment(monkeypatch):
    jobs = FakeJobs()
    files = FakeFiles()
    relay = FakeRelay()
    monkeypatch.setattr(
        "services.ffmpeg_media_service.MediaProjectStore.instance",
        lambda: jobs,
    )
    monkeypatch.setattr("core.file_store.FileStore.instance", lambda: files)
    monkeypatch.setattr(
        "core.relay_bindings.resolve_relay",
        lambda *args, **kwargs: ("relay-1", relay),
    )
    service = FFmpegMediaService({
        "_service_id": "ffmpeg-main",
        "timeout": 60,
        "max_input_bytes": 1000,
        "max_output_bytes": 1000,
    })
    service.set_runtime_context(
        user_id="user-1", conversation_id="conv-1", agent_name="media-studio")
    return service, jobs, files, relay


def _recipe():
    return FFmpegRecipe(
        operation="trim",
        inputs=(FID,),
        output_filename="result.mp4",
        parameters={"start": 0, "duration": 1},
    )


def test_service_probes_inputs_and_output_executes_argv_and_stores(environment):
    service, jobs, files, relay = environment
    result = service.compose(
        recipe=_recipe(),
        project_id="project-1",
        run_id="run-1",
        task_id="compose",
        idempotency_key="compose-1",
    )

    commands = [call[2] for call in relay.calls if call[0] == "exec_argv"]
    assert [command[0] for command in commands[:3]] == [
        "ffprobe", "ffmpeg", "ffprobe"]
    assert result["file_id"] == "123456789abc"
    assert result["url"] == "fs://filestore/123456789abc/result.mp4"
    assert files.stored[0][1] == b"rendered"
    assert jobs.job["status"] == "completed"
    assert any(call[0] == "delete" for call in relay.calls)
    assert commands[-1][0] == "rmdir"


def test_completed_job_returns_durable_output_without_reexecuting(environment):
    service, jobs, _files, relay = environment
    jobs.job.update({
        "status": "completed",
        "output": {"file_id": "123456789abc", "url": "cached"},
    })
    result = service.compose(
        recipe=_recipe(),
        project_id="project-1",
        run_id="run-1",
        task_id="compose",
        idempotency_key="compose-1",
    )
    assert result["url"] == "cached"
    assert relay.calls == []


def test_failure_is_persisted_and_staged_files_are_cleaned(environment):
    service, jobs, _files, relay = environment
    relay.fail_ffmpeg = True
    with pytest.raises(RuntimeError, match="bad media"):
        service.compose(
            recipe=_recipe(),
            project_id="project-1",
            run_id="run-1",
            task_id="compose",
            idempotency_key="compose-1",
        )
    assert jobs.job["status"] == "failed"
    assert jobs.finish_calls[-1]["error"].startswith("RuntimeError:")
    assert any(call[0] == "delete" for call in relay.calls)


@pytest.mark.parametrize("missing", ["project_id", "run_id", "task_id", "idempotency_key"])
def test_service_requires_durable_correlation(environment, missing):
    service, _jobs, _files, _relay = environment
    values = {
        "recipe": _recipe(),
        "project_id": "project-1",
        "run_id": "run-1",
        "task_id": "compose",
        "idempotency_key": "compose-1",
    }
    values[missing] = ""
    with pytest.raises(ValueError, match=f"{missing} is required"):
        service.compose(**values)


def test_service_requires_runtime_identity():
    service = FFmpegMediaService({})
    with pytest.raises(ValueError, match="user_id is required"):
        service.compose(
            recipe=_recipe(),
            project_id="project-1",
            run_id="run-1",
            task_id="compose",
            idempotency_key="compose-1",
        )
