"""End-to-end relay dispatch tests for scoped ScratchDir operations."""

import base64
import sys
import threading
import time
import types
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parent.parent / "tools"))

from pawflow_relay import _relay_dispatch as dispatch
from pawflow_relay import scratchdir


def _ctx(workspace):
    return dispatch.DispatchCtx(
        state=types.SimpleNamespace(),
        term_mgr=None,
        send_lock=threading.Lock(),
        ws_sock_ref=[object()],
        ws_frame_send=lambda _sock, _frame: None,
        resolve=lambda value: str((workspace / value).resolve()),
        forward_to_host_helper=lambda *_args, **_kwargs: {
            "ok": True, "data": {"forwarded": True}},
        root_dir=str(workspace),
        readonly=False,
        allow_exec=True,
        allow_local=True,
        allow_local_screen=True,
        allow_automation=True,
        allow_service_tunnels=False,
    )


def _ticket(letter="a", owner="b"):
    return {
        "scratch_id": "sd_" + letter * 32,
        "scope_hash": owner * 64,
        "epoch": 1,
    }


def _ensure(ticket, workspace, **changes):
    message = {
        **ticket,
        "operation_id": "create-" + ticket["scratch_id"],
        "expires_at": time.time() + 3600,
        "quota_bytes": 1024 * 1024,
        "quota_files": 100,
    }
    message.update(changes)
    return scratchdir.ensure(message, workspace_root=str(workspace))


@pytest.fixture()
def roots(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = tmp_path / "runtime" / "scratchdirs"
    monkeypatch.setenv("PAWFLOW_SCRATCHDIR_ROOT", str(runtime))
    return workspace, runtime


def test_dispatch_write_read_and_exec_share_one_root(roots):
    workspace, runtime = roots
    ticket = _ticket()
    _ensure(ticket, workspace)
    ctx = _ctx(workspace)

    written = dispatch.execute_command(ctx, {
        "action": "write_file",
        "path": "report.txt",
        "content": base64.b64encode(b"hello").decode("ascii"),
        "base64": True,
        "scratchdir": ticket,
    })
    assert written["ok"] is True
    files = runtime / ticket["scratch_id"] / "files"
    assert (files / "report.txt").read_bytes() == b"hello"
    assert not (workspace / "report.txt").exists()

    read = dispatch.execute_command(ctx, {
        "action": "read_file",
        "path": "report.txt",
        "scratchdir": ticket,
    })
    assert read["ok"] is True

    executed = dispatch.execute_command(ctx, {
        "action": "exec",
        "path": ".",
        "command": "pwd && printf routed > /scratch/from-shell.txt",
        "scratchdir": ticket,
    })
    assert executed["ok"] is True
    assert executed["data"]["stdout"].splitlines()[0] == "/scratch"
    assert str(runtime) not in str(executed)
    assert (files / "from-shell.txt").read_text(encoding="utf-8") == "routed"


def test_dispatch_rejects_local_traversal_stale_epoch_and_symlink(roots):
    workspace, runtime = roots
    ticket = _ticket()
    _ensure(ticket, workspace)
    ctx = _ctx(workspace)

    local = dispatch.execute_command(ctx, {
        "action": "read_file",
        "path": "x",
        "local": True,
        "scratchdir": ticket,
    })
    assert local["ok"] is False
    assert "scratchdir_scope_bypass" in local["error"]

    traversal = dispatch.execute_command(ctx, {
        "action": "read_file",
        "path": "../secret",
        "scratchdir": ticket,
    })
    assert traversal["ok"] is False
    assert "scratchdir_path_escape" in traversal["error"]

    stale = dispatch.execute_command(ctx, {
        "action": "read_file",
        "path": "x",
        "scratchdir": {**ticket, "epoch": 2},
    })
    assert stale["ok"] is False
    assert "scratchdir_epoch_stale" in stale["error"]

    files = runtime / ticket["scratch_id"] / "files"
    outside = runtime / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (files / "escape").symlink_to(outside)
    escaped = dispatch.execute_command(ctx, {
        "action": "read_file",
        "path": "escape",
        "scratchdir": ticket,
    })
    assert escaped["ok"] is False
    assert (
        "scratchdir_path_escape" in escaped["error"]
        or "scratchdir_unsafe_entry" in escaped["error"]
    )


def test_dispatch_isolates_neighboring_scopes(roots):
    workspace, runtime = roots
    first = _ticket("a", "b")
    second = _ticket("c", "d")
    _ensure(first, workspace)
    _ensure(second, workspace)
    ctx = _ctx(workspace)

    for ticket, value in ((first, b"first"), (second, b"second")):
        result = dispatch.execute_command(ctx, {
            "action": "write_file",
            "path": "same.txt",
            "content": base64.b64encode(value).decode("ascii"),
            "base64": True,
            "scratchdir": ticket,
        })
        assert result["ok"] is True

    assert (
        runtime / first["scratch_id"] / "files" / "same.txt"
    ).read_bytes() == b"first"
    assert (
        runtime / second["scratch_id"] / "files" / "same.txt"
    ).read_bytes() == b"second"

    mismatched = dispatch.execute_command(ctx, {
        "action": "read_file",
        "path": "same.txt",
        "scratchdir": {**first, "scope_hash": second["scope_hash"]},
    })
    assert mismatched["ok"] is False
    assert "scratchdir_owner_mismatch" in mismatched["error"]


def test_dispatch_reads_through_a_confined_virtualenv_symlink(roots):
    workspace, runtime = roots
    ticket = _ticket()
    _ensure(ticket, workspace)
    files = runtime / ticket["scratch_id"] / "files"
    library = files / ".venv" / "lib"
    library.mkdir(parents=True)
    (library / "module.py").write_text("value", encoding="utf-8")
    (files / ".venv" / "lib64").symlink_to(
        "lib", target_is_directory=True)

    result = dispatch.execute_command(_ctx(workspace), {
        "action": "read_file",
        "path": ".venv/lib64/module.py",
        "scratchdir": ticket,
    })

    assert result["ok"] is True


def test_dispatch_supports_chunked_scratchdir_downloads(roots):
    workspace, runtime = roots
    ticket = _ticket()
    _ensure(ticket, workspace)
    files = runtime / ticket["scratch_id"] / "files"
    payload = b"abcdefgh"
    (files / "payload.bin").write_bytes(payload)
    ctx = _ctx(workspace)

    first = dispatch.execute_command(ctx, {
        "action": "read_file_chunked",
        "path": "payload.bin",
        "chunk_size": 3,
        "scratchdir": ticket,
    })
    assert first["ok"] is True
    assert base64.b64decode(first["data"]["data"]) == payload[:3]
    assert first["data"]["total_chunks"] == 3

    second = dispatch.execute_command(ctx, {
        "action": "read_chunk",
        "path": "payload.bin",
        "index": 1,
        "chunk_size": 3,
        "scratchdir": ticket,
    })
    assert second["ok"] is True
    assert base64.b64decode(second["data"]["data"]) == payload[3:6]
