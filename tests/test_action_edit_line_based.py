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


def test_string_based_accepts_old_new_aliases(src, tmp_path):
    result = action_edit(str(tmp_path), str(src), {
        "old": "line 3", "new": "LINE 3",
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


# ── Reported diff ─────────────────────────────────────────────────────
#
# The diff is what the caller believes happened. It used to be built from
# old_string/new_string BEFORE writing, which made correct edits look
# mangled and sent agents re-reading files to check writes that were fine.
# It is now derived from the before/after texts.

def _rows(result, kind):
    return [(d["line"], d["text"]) for d in result["diff"] if d["type"] == kind]


def test_diff_of_a_partial_line_edit_shows_whole_lines(src, tmp_path):
    """A mid-line match must not report the untouched remainder as deleted."""
    src.write_text("alpha beta gamma\n", encoding="utf-8")
    result = action_edit(str(tmp_path), str(src), {
        "old_string": "alpha", "new_string": "ALPHA",
    })
    assert src.read_text(encoding="utf-8") == "ALPHA beta gamma\n"
    # The added row carries the COMPLETE resulting line. Reporting just the
    # replaced fragment is what made " beta gamma" look lost.
    assert _rows(result, "add") == [(1, "ALPHA beta gamma")]
    assert _rows(result, "remove") == [(1, "alpha beta gamma")]


def test_diff_places_additions_at_the_change_not_at_the_end(src, tmp_path):
    """Added rows sit in their region, before trailing context."""
    result = action_edit(str(tmp_path), str(src), {
        "old_string": "line 3", "new_string": "THREE",
    })
    types = [d["type"] for d in result["diff"]]
    add_at = types.index("add")
    # Something unchanged must follow the addition -- the old renderer
    # appended every "+" after the whole window, so nothing ever did.
    assert "context" in types[add_at:]


def test_diff_line_numbers_follow_the_written_file(src, tmp_path):
    """One line replaced by three: numbering must not assume equal length."""
    result = action_edit(str(tmp_path), str(src), {
        "old_string": "line 2", "new_string": "A\nB\nC",
    })
    assert src.read_text(encoding="utf-8") == (
        "line 1\nA\nB\nC\nline 3\nline 4\nline 5\n")
    assert _rows(result, "add") == [(2, "A"), (3, "B"), (4, "C")]
    # Trailing context is numbered in the NEW file: "line 3" moved to 5.
    assert (5, "line 3") in _rows(result, "context")


def test_diff_reports_every_replaced_region_not_only_the_first(src, tmp_path):
    """replace_all announced N replacements while showing one."""
    src.write_text("\n".join(["target"] + [f"f{i}" for i in range(20)]
                             + ["target"]) + "\n", encoding="utf-8")
    result = action_edit(str(tmp_path), str(src), {
        "old_string": "target", "new_string": "HIT", "replace_all": True,
    })
    assert result["replacements"] == 2
    assert _rows(result, "add") == [(1, "HIT"), (22, "HIT")]


def test_diff_truncation_is_announced(src, tmp_path):
    """A bounded view must never read as a complete one."""
    from tools._fs_edit import _DIFF_MAX_ROWS
    src.write_text("\n".join(f"x{i}" for i in range(_DIFF_MAX_ROWS * 3)) + "\n",
                   encoding="utf-8")
    result = action_edit(str(tmp_path), str(src), {
        "old_string": "x", "new_string": "y", "replace_all": True,
    })
    assert len(result["diff"]) <= _DIFF_MAX_ROWS + 1
    assert "truncated" in result["diff"][-1]["text"]
