"""Unit tests for pawflow_relay.utils path translation."""

from unittest.mock import patch

from pawflow_relay.utils import translate_path


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
