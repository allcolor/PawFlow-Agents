"""Windows launcher liveness must follow process signaling, not handle lifetime."""

import ctypes
import json
import os
import subprocess
import sys
from ctypes import wintypes
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from pawflow_relay import manager, physical_config


@pytest.fixture
def windows_api(monkeypatch):
    kernel32 = SimpleNamespace(
        OpenProcess=Mock(return_value=0x123456789ABC),
        WaitForSingleObject=Mock(return_value=0x102),
        CloseHandle=Mock(return_value=1),
    )
    monkeypatch.setattr(
        ctypes, "WinDLL", Mock(return_value=kernel32), raising=False)
    monkeypatch.setattr(
        ctypes, "get_last_error", Mock(return_value=87), raising=False)
    # Keep pathlib and the test runner on the real platform.
    monkeypatch.setattr(
        manager, "os", SimpleNamespace(**(vars(os) | {"name": "nt"})))
    return kernel32


@pytest.mark.parametrize("wait_result, expected", [
    (0, False),
    (0x102, True),
    (0xFFFFFFFF, True),
    (0x80, True),
])
def test_windows_process_state_uses_nonblocking_wait(
        windows_api, wait_result, expected):
    windows_api.WaitForSingleObject.return_value = wait_result

    assert manager._process_is_running(424242) is expected
    windows_api.OpenProcess.assert_called_once_with(0x00100000, False, 424242)
    windows_api.WaitForSingleObject.assert_called_once_with(0x123456789ABC, 0)
    windows_api.CloseHandle.assert_called_once_with(0x123456789ABC)


def test_windows_process_api_preserves_pointer_sized_handles(windows_api):
    assert manager._process_is_running(424242) is True

    assert windows_api.OpenProcess.argtypes == [
        wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    assert windows_api.OpenProcess.restype is wintypes.HANDLE
    assert windows_api.WaitForSingleObject.argtypes == [
        wintypes.HANDLE, wintypes.DWORD]
    assert windows_api.WaitForSingleObject.restype is wintypes.DWORD
    assert windows_api.CloseHandle.argtypes == [wintypes.HANDLE]
    assert windows_api.CloseHandle.restype is wintypes.BOOL


@pytest.mark.parametrize("error, expected", [(87, False), (5, True), (8, True), (0, True)])
def test_windows_open_failure_only_proves_absence_for_invalid_pid(
        windows_api, error, expected):
    windows_api.OpenProcess.return_value = None
    ctypes.get_last_error.return_value = error

    assert manager._process_is_running(424242) is expected
    windows_api.WaitForSingleObject.assert_not_called()
    windows_api.CloseHandle.assert_not_called()


def test_windows_wait_exception_still_closes_handle(windows_api):
    windows_api.WaitForSingleObject.side_effect = OSError("wait failed")

    assert manager._process_is_running(424242) is True
    windows_api.WaitForSingleObject.assert_called_once()
    windows_api.CloseHandle.assert_called_once_with(0x123456789ABC)


def test_windows_api_unavailable_keeps_runtime_reserved(windows_api):
    ctypes.WinDLL.side_effect = OSError("kernel32 unavailable")

    assert manager._process_is_running(424242) is True
    windows_api.OpenProcess.assert_not_called()


@pytest.mark.parametrize("pid", [0, -1])
def test_nonpositive_pid_does_not_open_process(windows_api, pid):
    assert manager._process_is_running(pid) is False
    windows_api.OpenProcess.assert_not_called()


@pytest.mark.parametrize("wait_result, running", [(0, False), (0x102, True), (0xFFFFFFFF, True)])
def test_windows_launcher_lock_obeys_process_state(
        windows_api, monkeypatch, tmp_path, wait_result, running):
    lock = tmp_path / "launcher.lock"
    lock.write_text(json.dumps({"pid": 424242}), encoding="utf-8")
    monkeypatch.setattr(manager, "_workspace_runtime_lock_path", lambda _: lock)
    windows_api.WaitForSingleObject.return_value = wait_result

    assert physical_config.is_running("physical-test") is running
    assert manager._remove_workspace_runtime_lock("physical-test") is not running
    assert lock.exists() is running


def test_exited_windows_launcher_does_not_receive_termination_signal(
        windows_api, monkeypatch, tmp_path):
    lock = tmp_path / "launcher.lock"
    lock.write_text(json.dumps({"pid": 424242}), encoding="utf-8")
    monkeypatch.setattr(manager, "_workspace_runtime_lock_path", lambda _: lock)
    kill = Mock(side_effect=AssertionError("Exited launcher must not be killed"))
    monkeypatch.setattr(manager.os, "kill", kill)
    windows_api.WaitForSingleObject.return_value = 0

    assert manager._terminate_workspace_runtime_lock("physical-test") is False
    kill.assert_not_called()


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows process handles")
def test_native_windows_exited_process_with_retained_handle():
    # Exit code 259 is also STILL_ACTIVE; signaling avoids that ambiguity.
    with subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read(); sys.exit(259)"],
        stdin=subprocess.PIPE,
    ) as child:
        try:
            assert manager._process_is_running(child.pid) is True
            child.communicate(timeout=10)
            assert child.returncode == 259
            assert child._handle
            assert manager._process_is_running(child.pid) is False
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)
