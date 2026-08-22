"""Unit tests for user_bin_path: the relay puts ~/bin and ~/.local/bin on PATH.

The shells the relay spawns are non-login and non-interactive, so no profile
file ever runs. Without this, a tool the user installed for themselves (gh in
~/bin, anything pipx put in ~/.local/bin) is invisible to every exec.
"""

import os
import sys
from pathlib import Path

# Ensure the project root is first in sys.path so that the pawflow_relay/
# *package* is found before tools/pawflow_relay.py (standalone script).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# tools/ on the path too (appended, AFTER root so the pawflow_relay package
# still wins over tools/pawflow_relay.py): the relay tool modules (fs_exec,
# ...) bare-import their siblings (`from fs_common import ...`) the way they
# do inside the relay container, so tools/ must be importable.
sys.path.append(str(Path(__file__).resolve().parent.parent / "tools"))

from tools.fs_common import USER_BIN_DIRS, user_bin_path


def _home_with(tmp_path: Path, *names: str) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    for name in names:
        (home / name).mkdir(parents=True)
    return home


class TestUserBinPath:
    """Prepending is conditional on the directory actually existing."""

    def test_both_dirs_prepended_in_order(self, tmp_path):
        home = _home_with(tmp_path, "bin", ".local/bin")
        env = {"HOME": str(home), "PATH": "/usr/bin:/bin"}
        assert user_bin_path(env).split(os.pathsep) == [
            str(home / "bin"),
            str(home / ".local" / "bin"),
            "/usr/bin",
            "/bin",
        ]

    def test_missing_dir_is_not_prepended(self, tmp_path):
        home = _home_with(tmp_path, ".local/bin")
        env = {"HOME": str(home), "PATH": "/usr/bin"}
        assert user_bin_path(env).split(os.pathsep) == [
            str(home / ".local" / "bin"),
            "/usr/bin",
        ]

    def test_no_user_dirs_leaves_path_untouched(self, tmp_path):
        home = _home_with(tmp_path)
        env = {"HOME": str(home), "PATH": "/usr/bin:/bin"}
        assert user_bin_path(env) == "/usr/bin:/bin"

    def test_already_present_is_not_duplicated(self, tmp_path):
        """An env that already exports ~/bin keeps its own ordering."""
        home = _home_with(tmp_path, "bin", ".local/bin")
        env = {"HOME": str(home), "PATH": f"/usr/bin{os.pathsep}{home / 'bin'}"}
        result = user_bin_path(env).split(os.pathsep)
        assert result == [str(home / ".local" / "bin"), "/usr/bin", str(home / "bin")]
        assert result.count(str(home / "bin")) == 1

    def test_empty_path_yields_only_user_dirs(self, tmp_path):
        home = _home_with(tmp_path, "bin")
        assert user_bin_path({"HOME": str(home), "PATH": ""}) == str(home / "bin")

    def test_no_home_returns_path_unchanged(self, monkeypatch):
        """expanduser is the last resort; if even that is empty, do nothing."""
        monkeypatch.setattr(os.path, "expanduser", lambda _p: "")
        assert user_bin_path({"PATH": "/usr/bin"}) == "/usr/bin"

    def test_falls_back_to_userprofile(self, tmp_path):
        """Windows relays export USERPROFILE rather than HOME."""
        home = _home_with(tmp_path, "bin")
        env = {"USERPROFILE": str(home), "PATH": "/usr/bin"}
        assert user_bin_path(env).split(os.pathsep)[0] == str(home / "bin")

    def test_dirs_are_relative_to_home(self):
        assert USER_BIN_DIRS == ("bin", os.path.join(".local", "bin"))


class TestExecUsesUserBinPath:
    """The exec paths must actually hand the augmented PATH to the child."""

    def test_action_exec_child_sees_user_bin(self, tmp_path, monkeypatch):
        from tools.fs_exec import action_exec

        home = _home_with(tmp_path, "bin")
        monkeypatch.setenv("HOME", str(home))
        workspace = tmp_path / "ws"
        workspace.mkdir()

        result = action_exec(
            str(workspace), str(workspace),
            {"command": "printf %s \"$PATH\""},
            allow_exec=True,
        )
        assert result["returncode"] == 0
        assert result["stdout"].split(os.pathsep)[0] == str(home / "bin")

    def test_explicit_env_still_wins(self, tmp_path, monkeypatch):
        """A caller-supplied PATH is an instruction, not a suggestion."""
        from tools.fs_exec import action_exec

        home = _home_with(tmp_path, "bin")
        monkeypatch.setenv("HOME", str(home))
        workspace = tmp_path / "ws"
        workspace.mkdir()

        result = action_exec(
            str(workspace), str(workspace),
            {"command": "printf %s \"$PATH\"", "env": {"PATH": "/only/this"}},
            allow_exec=True,
        )
        assert result["stdout"] == "/only/this"
