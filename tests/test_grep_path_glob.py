from core.handlers.grep_handler import GrepHandler
from tools.fs_actions import action_grep


def test_grep_handler_accepts_glob_in_path(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    target = pkg / "mod.py"
    target.write_text("needle = 'gemini'\n", encoding="utf-8")
    (pkg / "skip.txt").write_text("needle = 'gemini'\n", encoding="utf-8")

    handler = GrepHandler()
    handler.set_workdir(str(tmp_path))
    handler.set_is_claude_code(True)

    result = handler.execute({
        "pattern": "gemini",
        "path": str(tmp_path / "**" / "*.py"),
        "output_mode": "files_with_matches",
    })

    assert "pkg/mod.py" in result
    assert "skip.txt" not in result


def test_relay_action_grep_accepts_glob_in_path(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    target = pkg / "mod.py"
    target.write_text("needle = 'gemini'\n", encoding="utf-8")
    (pkg / "skip.txt").write_text("needle = 'gemini'\n", encoding="utf-8")

    results = action_grep(str(tmp_path), str(tmp_path / "**" / "*.py"), {
        "regex": "gemini",
        "recursive": True,
    })

    assert [row["path"] for row in results] == ["pkg/mod.py"]


def test_relay_action_grep_include_alias_is_recursive(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    target = pkg / "mod.py"
    target.write_text("needle = 'gemini'\n", encoding="utf-8")
    (pkg / "skip.txt").write_text("needle = 'gemini'\n", encoding="utf-8")

    results = action_grep(str(tmp_path), str(tmp_path), {
        "regex": "gemini",
        "include": "*.py",
        "recursive": True,
    })

    assert [row["path"] for row in results] == ["pkg/mod.py"]


def test_grep_handler_accepts_include_alias(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "mod.py").write_text("needle = 'gemini'\n", encoding="utf-8")
    (pkg / "skip.txt").write_text("needle = 'gemini'\n", encoding="utf-8")

    handler = GrepHandler()
    handler.set_workdir(str(tmp_path))
    handler.set_is_claude_code(True)

    result = handler.execute({
        "pattern": "gemini",
        "path": str(tmp_path),
        "include": "*.py",
        "output_mode": "files_with_matches",
    })

    assert "pkg/mod.py" in result
    assert "skip.txt" not in result
