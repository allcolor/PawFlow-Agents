"""Filesystem and execution routing contracts for ScratchDir."""

import json
from typing import ClassVar

import pytest

from core import paths
from core.handlers._fs_base import BaseFsHandler
from core.handlers.bash import BashHandler
from core.handlers.batch_edit import BatchEditHandler
from core.handlers.copy import CopyHandler
from core.handlers.devops import RunTestsHandler, SecurityScanHandler
from core.handlers.write import WriteHandler
from core.scratchdir_models import ScratchDirError
from core.scratchdir_store import ScratchDirStore
from services.scratchdir_service import (
    ScratchDirService,
    normalize_scratchdir_path,
)


class _Relay:
    TYPE = "relay"
    _service_id = "relay-1"
    config: ClassVar[dict] = {}

    def __init__(self):
        self.calls = []
        self.receipts = {}

    def supports_capability(self, name):
        return name == "scratchdir_v1"

    def scratchdir_ensure(self, **kwargs):
        receipt = {
            **kwargs,
            "locator": kwargs["scratch_id"],
            "observed_bytes": 0,
            "observed_files": 0,
        }
        self.receipts[kwargs["scratch_id"]] = receipt
        return receipt

    def scratchdir_status(self, **kwargs):
        return {
            **self.receipts[kwargs["scratch_id"]],
            "observed_bytes": 0,
            "observed_files": 0,
        }

    def _request(self, action, path=".", **kwargs):
        self.calls.append((action, path, kwargs))
        if action == "exec":
            command = kwargs.get("command", "")
            if "bandit" in command:
                return {"stdout": json.dumps({"results": []}), "stderr": "",
                        "returncode": 0}
            return {"stdout": "/scratch\n", "stderr": "", "returncode": 0}
        if action == "copy_file":
            return {"size": 3}
        if action == "batch_edit":
            return {
                "files_modified": [edit["path"] for edit in kwargs["edits"]],
                "files_modified_count": len(kwargs["edits"]),
                "edits_applied": len(kwargs["edits"]),
                "total_replacements": len(kwargs["edits"]),
            }
        return {}

    def _request_stream(self, action, path=".", on_output=None, **kwargs):
        self.calls.append((action, path, kwargs))
        return {"stdout": "", "stderr": "", "returncode": 0}


class _Handler(BaseFsHandler):
    name = "test_scratchdir_fs"
    description = "ScratchDir test handler"
    parameters_schema: ClassVar[dict] = {
        "type": "object", "properties": {}}

    def execute(self, arguments):
        return str(arguments)


@pytest.fixture()
def relay(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "SCRATCHDIRS_DIR", tmp_path / "metadata")
    ScratchDirStore._instance = None
    value = _Relay()
    yield value
    ScratchDirStore._instance = None


def _wire(handler, relay):
    handler.set_user_id("user")
    handler.set_conversation_id("conversation")
    handler.set_agent_name("assistant")
    handler.set_fs_service(relay)
    return handler


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (".", "."),
        ("report.txt", "report.txt"),
        ("a/b.txt", "a/b.txt"),
        ("fs://scratchdir/a/b.txt", "a/b.txt"),
        ("/scratch/a/b.txt", "a/b.txt"),
        ("/scratch", "."),
    ],
)
def test_logical_path_normalization(value, expected):
    assert normalize_scratchdir_path(value) == expected


@pytest.mark.parametrize(
    "value",
    ["/tmp/x", "../x", "a/../x", "a//x", "a/./x", r"a\x",
     "fs://other/x"],
)
def test_logical_path_bypasses_are_denied(value):
    with pytest.raises(ScratchDirError) as exc:
        normalize_scratchdir_path(value)
    assert exc.value.code == "scratchdir_path_escape"


def test_reserved_service_is_resolved_before_named_relays(relay):
    handler = _wire(_Handler(), relay)
    service, workdir = handler._resolve("scratchdir")
    assert isinstance(service, ScratchDirService)
    assert workdir is None
    assert service._service_id == "relay-1"


def test_reserved_service_requires_full_authenticated_context(relay):
    handler = _Handler()
    handler.set_fs_service(relay)
    with pytest.raises(ScratchDirError) as exc:
        handler._resolve("scratchdir")
    assert exc.value.code == "scratchdir_context_missing"


def test_write_url_routes_with_ticket_and_rejects_local(relay):
    handler = _wire(WriteHandler(), relay)
    result = handler.execute({
        "path": "fs://scratchdir/reports/result.txt",
        "destination": "some-other-relay",
        "content": "ok",
    })
    assert "Written" in result
    action, path, kwargs = relay.calls[-1]
    assert (action, path) == ("write_file", "reports/result.txt")
    assert set(kwargs["scratchdir"]) == {"scratch_id", "scope_hash", "epoch"}
    assert not kwargs.get("local")

    denied = handler.execute({
        "path": "fs://scratchdir/nope.txt",
        "content": "x",
        "local": True,
    })
    assert "scratchdir_scope_bypass" in denied


def test_copy_urls_use_one_scoped_root(relay):
    handler = _wire(CopyHandler(), relay)
    result = handler.execute({
        "source_path": "fs://scratchdir/a.txt",
        "dest_path": "fs://scratchdir/sub/b.txt",
        "source_service": "wrong",
        "dest_service": "also-wrong",
    })
    assert "a.txt" in result
    action, path, kwargs = relay.calls[-1]
    assert (action, path, kwargs["dest_path"]) == (
        "copy_file", "a.txt", "sub/b.txt")
    assert "scratchdir" in kwargs


def test_batch_edit_urls_select_one_scoped_root(relay):
    handler = _wire(BatchEditHandler(), relay)
    result = handler.execute({
        "edits": [
            {
                "path": "fs://scratchdir/a.txt",
                "old_string": "old",
                "new_string": "new",
            },
            {
                "path": "sub/b.txt",
                "old_string": "before",
                "new_string": "after",
            },
        ],
        "filesystem": "wrong",
    })
    assert "Batch edited 2 file(s)" in result
    action, path, kwargs = relay.calls[-1]
    assert (action, path) == ("batch_edit", ".")
    assert [edit["path"] for edit in kwargs["edits"]] == ["a.txt", "sub/b.txt"]
    assert "scratchdir" in kwargs


def test_batch_edit_rejects_mixed_filesystem_urls(relay):
    handler = _wire(BatchEditHandler(), relay)
    result = handler.execute({
        "edits": [
            {"path": "fs://scratchdir/a", "old_string": "a", "new_string": "b"},
            {"path": "fs://other/b", "old_string": "a", "new_string": "b"},
        ],
    })
    assert "must use one filesystem service" in result
    assert not relay.calls


def test_bash_and_run_tests_accept_scratchdir_working_directory(relay):
    bash = _wire(BashHandler(), relay)
    output = bash.execute({
        "command": "pwd",
        "path": "fs://scratchdir/build",
        "relay": "wrong",
    })
    assert output.strip() == "/scratch"
    action, path, kwargs = relay.calls[-1]
    assert (action, path) == ("exec", "build")
    assert "scratchdir" in kwargs

    tests = _wire(RunTestsHandler(), relay)
    output = tests.execute({
        "test_files": ["tests/test_example.py"],
        "path": "fs://scratchdir/project",
        "maxfail": 0,
    })
    assert "Tests PASSED" in output
    action, path, kwargs = relay.calls[-1]
    assert (action, path) == ("exec", "project")
    assert "scratchdir" in kwargs


def test_security_scan_accepts_scratchdir_url(relay):
    handler = _wire(SecurityScanHandler(), relay)
    result = handler.execute({
        "path": "fs://scratchdir/source",
        "tool": "bandit",
    })
    assert result == "No security issues found."
    action, path, kwargs = relay.calls[-1]
    assert (action, path) == ("exec", ".")
    assert "source" in kwargs["command"]
    assert "scratchdir" in kwargs


def test_checkpoints_and_project_refresh_skip_scratchdir(relay, monkeypatch):
    handler = _wire(_Handler(), relay)
    handler.set_checkpoint_id("checkpoint")
    service, _workdir = handler._resolve("scratchdir")

    class Checkpoints:
        @staticmethod
        def capture_before_write(*_args, **_kwargs):
            raise AssertionError("checkpoint must not run")

    monkeypatch.setattr("core.checkpoint.CheckpointManager", Checkpoints)
    handler._checkpoint_before(service, "x.txt")

    def fail_refresh(**_kwargs):
        raise AssertionError("project refresh must not run")

    monkeypatch.setattr(
        "core.project_maintenance.schedule_project_maintenance", fail_refresh)
    handler._schedule_project_refresh(service, "x.txt")
