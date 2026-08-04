import shutil
from pathlib import Path

import pytest

from tools.fs_actions import action_apply_patch

TWENTY_LINES = "".join(f"line {i}\n" for i in range(1, 21))

# Correct context and correct edits, but hand-counted @@ numbers. git refuses
# to parse those, which is exactly what drops a patch into the manual fallback.
BAD_COUNTS = (
    "--- a/f.txt\n"
    "+++ b/f.txt\n"
    "@@ -2,99 +2,99 @@\n"
    " line 2\n"
    "+ADDED A\n"
    "+ADDED B\n"
    " line 3\n"
    "@@ -15,99 +17,99 @@\n"
    " line 15\n"
    "+ADDED C\n"
    " line 16\n"
)


def test_openai_apply_patch_updates_file(tmp_path: Path):
    target = tmp_path / "pkg" / "mod.py"
    target.parent.mkdir()
    target.write_text("one\nold\nthree\n", encoding="utf-8")

    result = action_apply_patch(str(tmp_path), str(tmp_path), {
        "patch": """*** Begin Patch
*** Update File: pkg/mod.py
@@
 one
-old
+new
 three
*** End Patch
"""
    })

    assert result["applied"] is True
    assert result["method"] == "openai_apply_patch"
    assert result["files_modified"] == ["pkg/mod.py"]
    assert result["hunks_applied"] == 1
    assert target.read_text(encoding="utf-8") == "one\nnew\nthree\n"


def test_openai_apply_patch_adds_file(tmp_path: Path):
    result = action_apply_patch(str(tmp_path), str(tmp_path), {
        "patch": """*** Begin Patch
*** Add File: added.txt
+hello
+world
*** End Patch
"""
    })

    assert result["files_modified"] == ["added.txt"]
    assert (tmp_path / "added.txt").read_text(encoding="utf-8") == "hello\nworld\n"


def test_openai_apply_patch_is_atomic_across_files(tmp_path: Path):
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("old first\n", encoding="utf-8")
    second.write_text("old second\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Patch context not found"):
        action_apply_patch(str(tmp_path), str(tmp_path), {
            "patch": """*** Begin Patch
*** Update File: first.txt
@@
-old first
+new first
*** Update File: second.txt
@@
-not present
+new second
*** End Patch
"""
        })

    assert first.read_text(encoding="utf-8") == "old first\n"
    assert second.read_text(encoding="utf-8") == "old second\n"


def test_apply_patch_rejects_zero_hunk_patch(tmp_path: Path):
    with pytest.raises(ValueError, match="applicable hunks|applicable"):
        action_apply_patch(str(tmp_path), str(tmp_path), {
            "patch": "*** Begin Patch\n*** End Patch\n"
        })


def test_later_hunks_are_not_shifted_by_earlier_ones(tmp_path: Path):
    # The fallback indexed the old-side @@ number straight into a buffer the
    # preceding hunks had already grown, so every hunk after the first landed
    # off by that net delta -- here, two lines early, silently.
    target = tmp_path / "f.txt"
    target.write_text(TWENTY_LINES, encoding="utf-8")

    result = action_apply_patch(str(tmp_path), str(tmp_path), {"patch": BAD_COUNTS})

    assert result["method"] == "manual_unified"
    lines = target.read_text(encoding="utf-8").splitlines()
    assert lines[1:5] == ["line 2", "ADDED A", "ADDED B", "line 3"]
    assert lines[16:19] == ["line 15", "ADDED C", "line 16"]


def test_a_hunk_whose_context_is_absent_is_refused_not_guessed(tmp_path: Path):
    # It used to pop whatever sat at the stated offset without ever comparing
    # it, so a hunk matching nothing still reported success.
    target = tmp_path / "f.txt"
    target.write_text(TWENTY_LINES, encoding="utf-8")

    with pytest.raises(ValueError, match="context is nowhere"):
        action_apply_patch(str(tmp_path), str(tmp_path), {"patch": (
            "--- a/f.txt\n"
            "+++ b/f.txt\n"
            "@@ -5,3 +5,3 @@\n"
            " THIS CONTEXT DOES NOT EXIST\n"
            "-NOR DOES THIS\n"
            "+REPLACEMENT\n"
        )})

    assert target.read_text(encoding="utf-8") == TWENTY_LINES


def test_a_failing_late_hunk_leaves_the_file_untouched(tmp_path: Path):
    # The fallback wrote each file as it went, so a bad hunk at the end left
    # the earlier ones already rewritten.
    target = tmp_path / "f.txt"
    target.write_text(TWENTY_LINES, encoding="utf-8")

    with pytest.raises(ValueError):
        action_apply_patch(str(tmp_path), str(tmp_path), {"patch": (
            "--- a/f.txt\n"
            "+++ b/f.txt\n"
            "@@ -2,99 +2,99 @@\n"
            " line 2\n"
            "+ADDED A\n"
            " line 3\n"
            "@@ -9,99 +9,99 @@\n"
            " NOT PRESENT AT ALL\n"
            "+NOPE\n"
        )})

    assert target.read_text(encoding="utf-8") == TWENTY_LINES


def test_a_header_without_line_numbers_is_located_by_context(tmp_path: Path):
    # A bare @@ carries no address at all, so context is the only thing that
    # can place it. It used to be skipped, then reported as an empty patch.
    target = tmp_path / "f.txt"
    target.write_text(TWENTY_LINES, encoding="utf-8")

    action_apply_patch(str(tmp_path), str(tmp_path), {"patch": (
        "--- a/f.txt\n"
        "+++ b/f.txt\n"
        "@@\n"
        " line 2\n"
        "+ADDED A\n"
        " line 3\n"
    )})

    assert target.read_text(encoding="utf-8").splitlines()[1:4] == [
        "line 2", "ADDED A", "line 3"]


# Exactly what `diff -U0` emits for inserting two lines after line 2 and one
# more after line 15. Every hunk is context-free.
ZERO_CONTEXT = (
    "--- a/f.txt\n"
    "+++ b/f.txt\n"
    "@@ -2,0 +3,2 @@\n"
    "+ADDED A\n"
    "+ADDED B\n"
    "@@ -15,0 +18 @@\n"
    "+ADDED C\n"
)


def test_a_zero_context_diff_is_applied_rather_than_silently_skipped(tmp_path: Path):
    # git demands one line of context and otherwise skips the hunk *and exits
    # 0*, which this tool reported as a successful patch over an untouched
    # file. --unidiff-zero is what makes it actually apply.
    target = tmp_path / "f.txt"
    target.write_text(TWENTY_LINES, encoding="utf-8")

    action_apply_patch(str(tmp_path), str(tmp_path), {"patch": ZERO_CONTEXT})

    lines = target.read_text(encoding="utf-8").splitlines()
    assert lines[1:5] == ["line 2", "ADDED A", "ADDED B", "line 3"]
    assert lines[16:19] == ["line 15", "ADDED C", "line 16"]


def test_a_contextless_insertion_is_placed_by_the_headers_arithmetic(
        tmp_path: Path, monkeypatch):
    # Same patch, with git taken away so the fallback has to place it. There
    # is nothing to match, but the header is redundant -- the counts restate
    # the body and the new-side start restates the old-side start plus the
    # shift so far -- so a sound header is itself the corroboration. Two
    # hunks, so the accumulated shift has to be right for the second.
    def _no_git(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr("tools._fs_edit.subprocess.run", _no_git)

    target = tmp_path / "f.txt"
    target.write_text(TWENTY_LINES, encoding="utf-8")

    result = action_apply_patch(str(tmp_path), str(tmp_path),
                                {"patch": ZERO_CONTEXT})

    assert result["method"] == "manual_unified"
    lines = target.read_text(encoding="utf-8").splitlines()
    assert lines[1:5] == ["line 2", "ADDED A", "ADDED B", "line 3"]
    assert lines[16:19] == ["line 15", "ADDED C", "line 16"]


def test_a_contextless_insertion_with_bad_numbers_is_refused(tmp_path: Path):
    # Same hunk, counts falsified. With no context there is nothing else to
    # go on, so guessing a position is exactly what must not happen.
    target = tmp_path / "f.txt"
    target.write_text(TWENTY_LINES, encoding="utf-8")

    with pytest.raises(ValueError, match="do not check out"):
        action_apply_patch(str(tmp_path), str(tmp_path), {"patch": (
            "--- a/f.txt\n"
            "+++ b/f.txt\n"
            "@@ -2,0 +3,99 @@\n"
            "+ADDED A\n"
            "+ADDED B\n"
        )})

    assert target.read_text(encoding="utf-8") == TWENTY_LINES


def test_a_contextless_insertion_under_a_bare_header_is_refused(tmp_path: Path):
    # A bare @@ carries no arithmetic at all, and a pure insertion carries no
    # context: nothing corroborates anything, so it cannot be placed.
    target = tmp_path / "f.txt"
    target.write_text(TWENTY_LINES, encoding="utf-8")

    with pytest.raises(ValueError, match="do not check out"):
        action_apply_patch(str(tmp_path), str(tmp_path), {"patch": (
            "--- a/f.txt\n"
            "+++ b/f.txt\n"
            "@@\n"
            "+ADDED A\n"
        )})

    assert target.read_text(encoding="utf-8") == TWENTY_LINES


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_gits_refusal_is_reported_rather_than_discarded(tmp_path: Path):
    # git names the offending line; that diagnostic used to be dropped on the
    # floor, leaving the caller with no idea why the fallback had run at all.
    target = tmp_path / "f.txt"
    target.write_text(TWENTY_LINES, encoding="utf-8")

    with pytest.raises(ValueError, match="git apply said"):
        action_apply_patch(str(tmp_path), str(tmp_path), {"patch": (
            "--- a/f.txt\n"
            "+++ b/f.txt\n"
            "@@ -5,3 +5,3 @@\n"
            " NOTHING LIKE THIS IN THE FILE\n"
            "+REPLACEMENT\n"
        )})
