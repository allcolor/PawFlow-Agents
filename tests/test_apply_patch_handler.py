from pathlib import Path

import pytest

from tools.fs_actions import action_apply_patch


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


def test_apply_patch_rejects_zero_hunk_patch(tmp_path: Path):
    with pytest.raises(ValueError, match="applicable hunks|applicable"):
        action_apply_patch(str(tmp_path), str(tmp_path), {
            "patch": "*** Begin Patch\n*** End Patch\n"
        })
