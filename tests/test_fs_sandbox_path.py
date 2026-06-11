"""Regression tests for BaseFsHandler._sandbox_path containment.

The workdir handlers resolve every path through this helper. It must keep
relative and absolute inputs inside the workdir base and refuse symlink
escapes, so a CC agent cannot read or write server files outside its
workspace by planting a symlink or passing an arbitrary absolute path.
"""

import os

import pytest

from core.handlers._fs_base import BaseFsHandler

_sandbox = BaseFsHandler._sandbox_path


def test_relative_path_resolves_inside_base(tmp_path):
    base = str(tmp_path)
    out = _sandbox("sub/file.txt", base)
    assert out == os.path.realpath(os.path.join(base, "sub/file.txt"))


def test_dotdot_escape_is_blocked(tmp_path):
    base = tmp_path / "work"
    base.mkdir()
    with pytest.raises(ValueError, match="escapes sandbox"):
        _sandbox("../secret.txt", str(base))


def test_absolute_path_inside_base_is_allowed(tmp_path):
    base = str(tmp_path)
    abs_in = os.path.join(base, "keep.txt")
    assert _sandbox(abs_in, base) == os.path.realpath(abs_in)


def test_absolute_path_outside_base_is_blocked(tmp_path):
    base = tmp_path / "work"
    base.mkdir()
    with pytest.raises(ValueError, match="escapes sandbox"):
        _sandbox("/etc/passwd", str(base))


def test_symlink_escape_is_blocked(tmp_path):
    base = tmp_path / "work"
    base.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("top secret", encoding="utf-8")
    # A symlink under the workdir pointing outside it must not be followable.
    (base / "escape").symlink_to(outside)
    with pytest.raises(ValueError, match="escapes sandbox"):
        _sandbox("escape/secret.txt", str(base))


def test_new_file_under_base_resolves(tmp_path):
    # Writing a not-yet-existing file must resolve (parents resolved, final
    # component kept) and stay inside the base.
    base = str(tmp_path)
    out = _sandbox("newdir/new.txt", base)
    assert out.startswith(os.path.realpath(base) + os.sep)


def test_sibling_outside_base_is_blocked(tmp_path):
    # Even when the workdir itself sits under the system temp dir, a `..`
    # escape into a sibling must be refused (no broad temp allowance that
    # would undermine containment for temp-rooted CC session workdirs).
    base = tmp_path / "work"
    base.mkdir()
    sibling = tmp_path / "sibling"
    sibling.mkdir()
    with pytest.raises(ValueError, match="escapes sandbox"):
        _sandbox("../sibling/x.txt", str(base))


def test_empty_base_preserves_passthrough():
    # No sandbox configured: absolute passes through, relative is normalised.
    assert _sandbox("/abs/path", "") == "/abs/path"
    assert _sandbox("a/b", "") == os.path.normpath("a/b")
