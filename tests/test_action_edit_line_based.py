"""Unit tests for fs_actions.action_edit — covers both modes.

Regression guard: the relay handler used to accept only the string-based
mode (old_string + new_string). When EditHandler routed a line-based
request (start_line + end_line + new_string) the relay rejected it with
"Missing 'old_string' parameter", even though the tool schema advertises
both modes.
"""

import pytest

from tools.fs_actions import action_edit


@pytest.fixture
def src(tmp_path):
    p = tmp_path / "src.js"
    p.write_text(
        "line 1\n"
        "line 2\n"
        "line 3\n"
        "line 4\n"
        "line 5\n",
        encoding="utf-8",
    )
    return p


# ── Line-based mode ───────────────────────────────────────────────────

def test_line_based_replaces_range(src, tmp_path):
    result = action_edit(str(tmp_path), str(src), {
        "start_line": 2, "end_line": 3, "new_string": "NEW A\nNEW B",
    })
    assert result["lines_replaced"] == "2-3"
    assert result["lines_removed"] == 2
    assert result["lines_inserted"] == 2
    assert src.read_text(encoding="utf-8") == (
        "line 1\nNEW A\nNEW B\nline 4\nline 5\n"
    )


def test_line_based_single_line(src, tmp_path):
    result = action_edit(str(tmp_path), str(src), {
        "start_line": 3, "end_line": 3, "new_string": "REPLACED",
    })
    assert result["lines_removed"] == 1
    assert result["lines_inserted"] == 1
    assert src.read_text(encoding="utf-8") == (
        "line 1\nline 2\nREPLACED\nline 4\nline 5\n"
    )


def test_line_based_expand(src, tmp_path):
    # Replace 1 line with 3.
    result = action_edit(str(tmp_path), str(src), {
        "start_line": 2, "end_line": 2, "new_string": "A\nB\nC",
    })
    assert result["lines_removed"] == 1
    assert result["lines_inserted"] == 3


def test_line_based_delete_range(src, tmp_path):
    # Replace with empty string -> deletes the range (one empty line
    # remains because "".split("\n") == [""]).
    result = action_edit(str(tmp_path), str(src), {
        "start_line": 2, "end_line": 4, "new_string": "",
    })
    assert result["lines_removed"] == 3
    assert result["lines_inserted"] == 1


def test_line_based_out_of_range_rejected(src, tmp_path):
    with pytest.raises(ValueError, match="Invalid line range"):
        action_edit(str(tmp_path), str(src), {
            "start_line": 99, "end_line": 100, "new_string": "x",
        })


def test_line_based_inverted_range_rejected(src, tmp_path):
    with pytest.raises(ValueError, match="Invalid line range"):
        action_edit(str(tmp_path), str(src), {
            "start_line": 4, "end_line": 2, "new_string": "x",
        })


def test_line_based_no_old_string_needed(src, tmp_path):
    # The bug this test guards: before the fix, a line-based request
    # without old_string was rejected with "Missing 'old_string' parameter".
    result = action_edit(str(tmp_path), str(src), {
        "start_line": 1, "end_line": 1, "new_string": "first!",
    })
    assert result["lines_replaced"] == "1-1"


# ── String-based mode (existing behavior, guard against regression) ──

def test_string_based_unique_match(src, tmp_path):
    result = action_edit(str(tmp_path), str(src), {
        "old_string": "line 3", "new_string": "LINE 3",
    })
    assert result["replacements"] == 1
    assert "LINE 3" in src.read_text(encoding="utf-8")


def test_string_based_missing_old_string_rejects(src, tmp_path):
    # No old_string AND no line range -> hard error.
    with pytest.raises(ValueError, match="Missing 'old_string' parameter"):
        action_edit(str(tmp_path), str(src), {"new_string": "anything"})


def test_string_based_not_found_surfaces_diagnostic(src, tmp_path):
    # Regression guard: diagnostic must still fire, not be swallowed by
    # the line-based branch when start_line/end_line are absent.
    with pytest.raises(ValueError, match="old_string not found"):
        action_edit(str(tmp_path), str(src), {
            "old_string": "nope", "new_string": "x",
        })


def test_string_based_multiple_without_replace_all(src, tmp_path):
    src.write_text("same\nsame\nsame\n", encoding="utf-8")
    with pytest.raises(ValueError, match="found 3 times"):
        action_edit(str(tmp_path), str(src), {
            "old_string": "same", "new_string": "x",
        })


def test_string_based_multiple_with_replace_all(src, tmp_path):
    src.write_text("same\nsame\nsame\n", encoding="utf-8")
    result = action_edit(str(tmp_path), str(src), {
        "old_string": "same", "new_string": "x", "replace_all": True,
    })
    assert result["replacements"] == 3
    assert src.read_text(encoding="utf-8") == "x\nx\nx\n"
