from core.handlers.grep_handler import GrepHandler
from core.handlers.glob_handler import GlobHandler
from core.handlers.search import SearchHandler
from tools.fs_actions import action_grep, action_search


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


def test_relay_action_grep_accepts_space_separated_paths(tmp_path):
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    (left / "a.py").write_text("needle = 'left'\n", encoding="utf-8")
    (right / "b.py").write_text("needle = 'right'\n", encoding="utf-8")

    results = action_grep(str(tmp_path), f"{left} {right}", {
        "regex": "needle",
        "include": "*.py",
        "recursive": True,
    })

    assert [row["path"] for row in results] == [
        f"{left}/a.py",
        f"{right}/b.py",
    ]


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


def test_relay_action_grep_accepts_comma_separated_include(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "mod.py").write_text("needle = 'gemini'\n", encoding="utf-8")
    (pkg / "view.js").write_text("needle = 'gemini'\n", encoding="utf-8")
    (pkg / "skip.txt").write_text("needle = 'gemini'\n", encoding="utf-8")

    results = action_grep(str(tmp_path), str(tmp_path), {
        "regex": "gemini",
        "include": "*.py,*.js",
        "recursive": True,
    })

    assert [row["path"] for row in results] == ["pkg/mod.py", "pkg/view.js"]


def test_relay_action_grep_accepts_brace_include(tmp_path):
    (tmp_path / "core").mkdir()
    (tmp_path / "services").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "core" / "a.py").write_text("needle = 'core'\n", encoding="utf-8")
    (tmp_path / "services" / "b.py").write_text("needle = 'services'\n", encoding="utf-8")
    (tmp_path / "docs" / "c.py").write_text("needle = 'docs'\n", encoding="utf-8")

    results = action_grep(str(tmp_path), str(tmp_path), {
        "regex": "needle",
        "include": "{core,services}/**/*.py",
        "recursive": True,
    })

    assert [row["path"] for row in results] == ["core/a.py", "services/b.py"]


def test_relay_action_grep_honors_limit_and_streams_context(tmp_path):
    f = tmp_path / "large.txt"
    f.write_text(
        "before one\nneedle one\nafter one\n"
        "before two\nneedle two\nafter two\n"
        "before three\nneedle three\nafter three\n",
        encoding="utf-8",
    )

    results = action_grep(str(tmp_path), str(f), {
        "regex": "needle",
        "recursive": True,
        "limit": 2,
        "context_before": 1,
        "context_after": 1,
    })

    assert [row["line"] for row in results] == ["needle one", "needle two"]
    assert results[0]["before"] == [{"line_number": 1, "line": "before one"}]
    assert results[0]["after"] == [{"line_number": 3, "line": "after one"}]
    assert results[1]["before"] == [{"line_number": 4, "line": "before two"}]
    assert results[1]["after"] == [{"line_number": 6, "line": "after two"}]


def test_search_handler_uses_relay_context_without_reading_full_files():
    class FakeRelay:
        def grep(self, path, pattern, recursive, **kwargs):
            assert kwargs["context_before"] == 1
            assert kwargs["context_after"] == 1
            return [{
                "path": "huge.log",
                "line_number": 2,
                "line": "needle hit",
                "before": [{"line_number": 1, "line": "before"}],
                "after": [{"line_number": 3, "line": "after"}],
            }]

        def read_file(self, path, **kwargs):
            raise AssertionError("search should not re-read files with relay context")

    handler = SearchHandler()
    handler.set_fs_service(FakeRelay())

    result = handler.execute({
        "pattern": "needle",
        "path": "/workspace/data/runtime",
        "context": 1,
        "limit": 10,
    })

    assert "  1: before" in result
    assert "> 2: needle hit" in result
    assert "  3: after" in result


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


def test_glob_handler_accepts_limit(tmp_path):
    for idx in range(5):
        (tmp_path / f"file_{idx}.py").write_text("x", encoding="utf-8")

    handler = GlobHandler()
    handler.set_workdir(str(tmp_path))
    handler.set_is_claude_code(True)

    schema = handler.parameters_schema["properties"]
    assert "limit" in schema

    result = handler.execute({"pattern": "*.py", "path": str(tmp_path), "limit": 2})

    lines = [line for line in result.splitlines() if line.strip()]
    assert len(lines) == 2


def test_glob_handler_accepts_brace_pattern(tmp_path):
    (tmp_path / "core").mkdir()
    (tmp_path / "services").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "core" / "a.py").write_text("x", encoding="utf-8")
    (tmp_path / "services" / "b.py").write_text("x", encoding="utf-8")
    (tmp_path / "docs" / "c.py").write_text("x", encoding="utf-8")

    handler = GlobHandler()
    handler.set_workdir(str(tmp_path))
    handler.set_is_claude_code(True)

    result = handler.execute({
        "pattern": "{core,services}/**/*.py",
        "path": str(tmp_path),
    })

    assert "core/a.py" in result
    assert "services/b.py" in result
    assert "docs/c.py" not in result


def test_search_handler_accepts_brace_glob(tmp_path):
    (tmp_path / "core").mkdir()
    (tmp_path / "services").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "core" / "a.py").write_text("needle = 'core'\n", encoding="utf-8")
    (tmp_path / "services" / "b.py").write_text("needle = 'services'\n", encoding="utf-8")
    (tmp_path / "docs" / "c.py").write_text("needle = 'docs'\n", encoding="utf-8")

    handler = SearchHandler()
    handler.set_workdir(str(tmp_path))
    handler.set_is_claude_code(True)

    result = handler.execute({
        "pattern": "needle",
        "path": str(tmp_path),
        "glob": "{core,services}/**/*.py",
        "context": 0,
    })

    assert "Search results for 'needle': 2 match(es) in 2 file(s)" in result
    assert "## core/a.py" in result
    assert "## services/b.py" in result
    assert "docs/c.py" not in result


def test_relay_action_search_accepts_limit(tmp_path):
    for idx in range(5):
        (tmp_path / f"file_{idx}.py").write_text("x", encoding="utf-8")

    results = action_search(str(tmp_path), str(tmp_path), {
        "pattern": "*.py",
        "recursive": True,
        "limit": 3,
    })

    assert len(results) == 3


def test_relay_action_search_accepts_brace_pattern(tmp_path):
    (tmp_path / "core").mkdir()
    (tmp_path / "services").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "core" / "a.py").write_text("x", encoding="utf-8")
    (tmp_path / "services" / "b.py").write_text("x", encoding="utf-8")
    (tmp_path / "docs" / "c.py").write_text("x", encoding="utf-8")

    results = action_search(str(tmp_path), str(tmp_path), {
        "pattern": "{core,services}/**/*.py",
        "recursive": True,
    })

    assert results == ["core/a.py", "services/b.py"]
