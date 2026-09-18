"""Unit tests for pawflow_relay.utils path translation."""

import sys
from pathlib import Path
from unittest.mock import patch

# Ensure the project root is first in sys.path so that the pawflow_relay/
# *package* is found before tools/pawflow_relay.py (standalone script).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# tools/ on the path too (appended, AFTER root so the pawflow_relay package
# still wins over tools/pawflow_relay.py): the relay tool modules (fs_exec,
# ...) bare-import their siblings (`from fs_common import ...`) the way they
# do inside the relay container, so tools/ must be importable.
sys.path.append(str(Path(__file__).resolve().parent.parent / "tools"))

from pawflow_relay.utils import translate_path
from tools.fs_common import windows_shell_cwd


class TestTranslatePathPosix:
    """On non-Windows platforms translate_path is a no-op."""

    def test_posix_passthrough(self):
        with patch("pawflow_relay.utils.os.name", "posix"):
            assert translate_path("/home/qan/Projets/PawFlow") == "/home/qan/Projets/PawFlow"
            assert translate_path(r"\\wsl$\Ubuntu-24.04\home\qan") == r"\\wsl$\Ubuntu-24.04\home\qan"
            assert translate_path(r"C:\Users\foo") == r"C:\Users\foo"


class TestTranslatePathWindows:
    """On Windows, paths are converted to what `wsl docker` expects."""

    def _translate(self, p):
        with patch("pawflow_relay.utils.os.name", "nt"):
            return translate_path(p)

    def test_drive_letter(self):
        assert self._translate(r"C:\Users\foo\bar") == "/mnt/c/Users/foo/bar"
        assert self._translate(r"D:\data") == "/mnt/d/data"

    def test_drive_letter_forward_slashes(self):
        assert self._translate("C:/Users/foo") == "/mnt/c/Users/foo"

    def test_wsl_unc_dollar(self):
        # The bug this test guards: Docker inside WSL cannot see //wsl$/...
        # so we must strip it down to the native Linux path.
        assert self._translate(r"\\wsl$\Ubuntu-24.04\home\qan\Projets\PawFlow") == "/home/qan/Projets/PawFlow"

    def test_wsl_unc_localhost(self):
        assert self._translate(r"\\wsl.localhost\Ubuntu-24.04\home\qan") == "/home/qan"

    def test_wsl_unc_case_insensitive(self):
        assert self._translate(r"\\WSL$\Ubuntu-24.04\home\qan") == "/home/qan"
        assert self._translate(r"\\Wsl.LocalHost\Ubuntu\home") == "/home"

    def test_wsl_unc_distro_root(self):
        assert self._translate(r"\\wsl$\Ubuntu-24.04") == "/"

    def test_unknown_unc_unchanged(self):
        # Non-WSL UNC (e.g. a real SMB share) has no Linux equivalent — leave it
        # alone rather than silently produce a wrong path.
        assert self._translate(r"\\server\share\path") == "//server/share/path"


def test_windows_shell_cwd_uses_pushd_for_cmd_unc_paths():
    with patch("tools.fs_common.os.name", "nt"):
        command, cwd = windows_shell_cwd(
            "python -V",
            r"\\wsl$\Ubuntu-24.04\home\qan\Projets\PawFlow",
            shell_name="cmd",
            executable=r"C:\Windows\System32\cmd.exe",
        )

    assert cwd is None
    assert command.startswith('pushd "\\\\wsl$\\Ubuntu-24.04')
    assert "python -V" in command
    assert command.endswith(" & popd")


def test_windows_shell_cwd_leaves_powershell_unc_cwd_alone():
    with patch("tools.fs_common.os.name", "nt"):
        command, cwd = windows_shell_cwd(
            "Get-Location",
            r"\\wsl$\Ubuntu-24.04\home\qan\Projets\PawFlow",
            shell_name="powershell",
            executable="powershell.exe",
        )

    assert command == "Get-Location"
    assert cwd == r"\\wsl$\Ubuntu-24.04\home\qan\Projets\PawFlow"


def test_exec_stream_exposes_the_request_env_to_the_process(tmp_path):
    """exec_stream must inject the server-side env exactly like exec does."""
    from tools.fs_exec import action_exec, action_exec_stream

    req = {
        "command": f'"{sys.executable}" -c "import os; print(os.environ.get(\'PF_CANARY\'))"',
        "env": {"PF_CANARY": "seen"},
    }
    plain = action_exec(str(tmp_path), str(tmp_path), dict(req), allow_exec=True)
    streamed = action_exec_stream(
        str(tmp_path), str(tmp_path), dict(req), allow_exec=True,
        on_output=lambda _stream, _data: None)

    assert plain["stdout"].strip() == "seen"
    assert streamed["stdout"].strip() == "seen"


def test_exec_stream_passes_the_request_env_to_docker_exec(tmp_path):
    from tools import fs_exec

    seen = {}

    class _Stop(Exception):
        pass

    def _popen(cmd, **_kwargs):
        seen["cmd"] = cmd
        raise _Stop()

    with patch.object(fs_exec.subprocess, "Popen", _popen):
        try:
            fs_exec.action_exec_stream(
                str(tmp_path), str(tmp_path),
                {"command": "true", "env": {"PF_CANARY": "seen"},
                 "_docker_container": "relay-box"},
                allow_exec=True, on_output=lambda _s, _d: None)
        except _Stop:
            pass

    assert "PF_CANARY=seen" in seen["cmd"]
    assert seen["cmd"].index("PF_CANARY=seen") < seen["cmd"].index("relay-box")


def test_exec_stream_uses_direct_argv_for_powershell():
    import inspect
    from tools.fs_exec import action_exec_stream

    src = inspect.getsource(action_exec_stream)
    assert "_powershell_command(executable, command)" in src
    assert 'popen_kwargs["shell"] = False' in src
