"""Cross-process workspace transactions shared by Desktop and the relay CLI."""

import multiprocessing
import queue
import traceback

import pytest

from pawflow_relay import manager, physical_config


def _mutation(home, job, entered, release, attempted, done, result):
    import os
    from unittest.mock import patch

    os.environ["PAWFLOW_RELAY_HOME"] = home
    original_save = manager._save_json

    def save(filename, records):
        if release is not None and filename == manager._WORKSPACES_FILE:
            entered.set()
            if not release.wait(30):
                raise TimeoutError("The test did not release the paused writer")
        original_save(filename, records)

    class Relay:
        def __init__(self, _url, _token, _username, path, **_kwargs):
            self.path = path

        def start(self):
            pass

        def wait(self):
            pass

        def stop(self):
            pass

    try:
        with (
            patch.object(manager, "get_server", return_value={
                "name": "Server", "url": "https://fixture.invalid",
                "username": "fixture", "session_token": "fixture-session",
            }),
            patch.object(manager, "_save_json", save),
            patch("pawflow_relay.thread.RelayThread", Relay),
        ):
            attempted.set()
            operation = job["operation"]
            if operation == "save":
                value = physical_config.save_physical(
                    job["name"], "Server", "relay:test", job["workspaces"])
            elif operation == "add":
                share = job["workspaces"][0]
                value = manager.add_workspace(
                    share["name"], "Server", share["path"], docker_image="relay:test")
            elif operation == "delete":
                value = physical_config.delete_physical(job["name"])
            elif operation == "delete_workspace":
                value = manager.delete_workspace(job["name"])
            else:
                value = manager.start_workspace(job["name"]).path
            result.put(("ok", value))
    except Exception:  # noqa: BLE001 - report child failures to the parent process
        result.put(("error", traceback.format_exc()))
    finally:
        done.set()


def _overlap(home, first, second, hold_seconds=0.5):
    ctx = multiprocessing.get_context("spawn")
    entered, release = ctx.Event(), ctx.Event()
    attempted, done = ctx.Event(), ctx.Event()
    first_attempted, first_done, second_entered = ctx.Event(), ctx.Event(), ctx.Event()
    results = [ctx.Queue(), ctx.Queue()]
    workers = [
        ctx.Process(target=_mutation, args=(
            str(home), first, entered, release, first_attempted, first_done, results[0])),
        ctx.Process(target=_mutation, args=(
            str(home), second, second_entered, None, attempted, done, results[1])),
    ]
    try:
        workers[0].start()
        assert entered.wait(10), "First process did not reach its write"
        workers[1].start()
        assert attempted.wait(10), "Second process did not attempt its operation"
        # Without a transaction, the second operation completes against the old
        # snapshot while the first writer is paused. With a lock it must wait.
        early_completion = done.wait(hold_seconds)
    finally:
        release.set()
        for worker in workers:
            if worker.pid is not None:
                worker.join(10)
                if worker.is_alive():
                    worker.terminate()
                    worker.join(5)
    values = []
    for worker, result in zip(workers, results):
        assert worker.exitcode == 0
        try:
            status, value = result.get(timeout=2)
        except queue.Empty:
            pytest.fail("Child process returned no result")
        assert status == "ok", value
        values.append(value)
        result.close()
        result.join_thread()
    return early_completion, values


@pytest.fixture
def config(monkeypatch, tmp_path):
    home = tmp_path / "config"
    monkeypatch.setenv("PAWFLOW_RELAY_HOME", str(home))
    monkeypatch.setattr(manager, "get_server", lambda _name: {
        "name": "Server", "url": "https://fixture.invalid", "username": "fixture",
    })
    return home, tmp_path


def _entry(root, name):
    directory = root / name
    directory.mkdir()
    return {"name": name, "path": str(directory)}


@pytest.mark.parametrize("operation", ["save", "add", "delete", "delete_workspace"])
def test_concurrent_workspace_mutations_preserve_both_results(config, operation):
    home, root = config
    alpha, beta = _entry(root, "Alpha"), _entry(root, "Beta")
    if operation.startswith("delete"):
        physical_config.save_physical("Beta", "Server", "relay:test", [beta])
    early, _ = _overlap(
        home,
        {"operation": "save", "name": "Alpha", "workspaces": [alpha]},
        {"operation": operation, "name": "Beta", "workspaces": [beta]},
    )
    records = manager._load_json(manager._WORKSPACES_FILE)
    expected = {"Alpha"} if operation.startswith("delete") else {"Alpha", "Beta"}
    assert set(records) == expected
    assert not early, "The second mutation bypassed the workspace transaction"


def test_start_waits_for_save_and_uses_the_committed_directory(config):
    home, root = config
    before, after = _entry(root, "Before"), _entry(root, "After")
    before["name"] = after["name"] = "Code"
    physical_config.save_physical("Laptop", "Server", "relay:test", [before])
    early, values = _overlap(
        home,
        {"operation": "save", "name": "Laptop", "workspaces": [after]},
        {"operation": "start", "name": "Laptop"},
    )
    assert values[1] == after["path"]
    assert not early, "Runtime acquisition bypassed the workspace transaction"


def test_windows_transaction_waits_beyond_ten_seconds(config):
    import os

    if os.name != "nt":
        pytest.skip("Requires the Windows byte-range lock")
    home, root = config
    alpha, beta = _entry(root, "Alpha"), _entry(root, "Beta")
    early, _ = _overlap(
        home,
        {"operation": "save", "name": "Alpha", "workspaces": [alpha]},
        {"operation": "save", "name": "Beta", "workspaces": [beta]},
        hold_seconds=11,
    )
    assert not early
    assert set(manager._load_json(manager._WORKSPACES_FILE)) == {"Alpha", "Beta"}


@pytest.mark.parametrize("failure", ["contention", "invalid_fd"])
def test_windows_lock_retries_only_contention(config, monkeypatch, failure):
    import errno
    import os
    import sys
    import time
    from types import SimpleNamespace

    calls, pauses = [], []
    fake = SimpleNamespace(LK_LOCK=1, LK_NBLCK=2, LK_UNLCK=0)

    def locking(fd, mode, length):
        calls.append(mode)
        assert length == 1
        if mode == fake.LK_UNLCK:
            return
        if failure == "invalid_fd":
            raise OSError(errno.EBADF, "invalid descriptor")
        if len(calls) <= 12:
            raise OSError(errno.EACCES, "lock busy")

    fake.locking = locking
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    monkeypatch.setattr(manager, "os", SimpleNamespace(
        name="nt", environ=os.environ, open=os.open, close=os.close,
        O_CREAT=os.O_CREAT, O_RDWR=os.O_RDWR,
    ))
    monkeypatch.setattr(time, "sleep", pauses.append)
    if failure == "invalid_fd":
        with pytest.raises(OSError) as caught, manager._workspace_config_lock():
            pytest.fail("Entered transaction with invalid descriptor")
        assert caught.value.errno == errno.EBADF
        assert pauses == []
    else:
        with manager._workspace_config_lock(), manager._workspace_config_lock():
            assert len(calls) == 13
        assert calls == [fake.LK_NBLCK] * 13 + [fake.LK_UNLCK]
        assert len(pauses) == 12
