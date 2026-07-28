"""Tests for cross-agent read-conflict notices.

The contract: when one agent writes a file another agent has read, the reader
is told once, at its next turn, and only while its view is genuinely stale.
The single-agent case must stay completely silent.
"""
import pytest

from core.handlers import _edit_guard
from core.handlers._edit_guard import canonical_path, readers_of, track_read
from core.read_conflict import (
    BLOCK_TITLE, MAX_PATHS, clear_agent, clear_conversation, clear_path,
    note_write, pending_block, reset_for_tests, stats,
)


@pytest.fixture(autouse=True)
def _reset():
    _edit_guard.reset_for_tests()
    reset_for_tests()
    yield
    _edit_guard.reset_for_tests()
    reset_for_tests()


# -- The core case --

def test_other_agent_is_notified_after_a_write():
    track_read("u1", "c1", "qwen", "/svc.py", b"old")
    assert note_write("u1", "c1", "claude", "/svc.py", b"new") == 1

    block = pending_block("u1", "c1", "qwen")
    assert "/svc.py" in block
    assert "'claude'" in block
    assert "changed" in block


def test_writer_is_never_notified_of_its_own_write():
    track_read("u1", "c1", "claude", "/svc.py", b"old")
    note_write("u1", "c1", "claude", "/svc.py", b"new")
    assert pending_block("u1", "c1", "claude") == ""


def test_single_agent_conversation_stays_silent():
    track_read("u1", "c1", "claude", "/svc.py", b"old")
    assert note_write("u1", "c1", "claude", "/svc.py", b"new") == 0
    assert stats()["agents_with_notices"] == 0


def test_agent_that_never_read_the_file_is_not_notified():
    assert note_write("u1", "c1", "claude", "/svc.py", b"new") == 0
    assert pending_block("u1", "c1", "qwen") == ""


# -- Silence when nothing actually changed --

def test_identical_rewrite_notifies_nobody():
    track_read("u1", "c1", "qwen", "/svc.py", b"same")
    assert note_write("u1", "c1", "claude", "/svc.py", b"same") == 0
    assert pending_block("u1", "c1", "qwen") == ""


def test_unknown_content_invalidates_unconditionally():
    # The relay path cannot hand back the new bytes without a round trip.
    track_read("u1", "c1", "qwen", "/svc.py", b"same")
    assert note_write("u1", "c1", "claude", "/svc.py", None) == 1
    assert "/svc.py" in pending_block("u1", "c1", "qwen")


# -- Clearing --

def test_reread_clears_the_notice():
    track_read("u1", "c1", "qwen", "/svc.py", b"old")
    note_write("u1", "c1", "claude", "/svc.py", b"new")
    # qwen re-reads: its view is current again, nothing left to tell it.
    track_read("u1", "c1", "qwen", "/svc.py", b"new")
    assert pending_block("u1", "c1", "qwen") == ""


def test_notice_is_delivered_once():
    track_read("u1", "c1", "qwen", "/svc.py", b"old")
    note_write("u1", "c1", "claude", "/svc.py", b"new")
    assert pending_block("u1", "c1", "qwen") != ""
    assert pending_block("u1", "c1", "qwen") == ""


def test_clear_path_is_scoped_to_one_agent():
    track_read("u1", "c1", "qwen", "/svc.py", b"old")
    track_read("u1", "c1", "gemini", "/svc.py", b"old")
    note_write("u1", "c1", "claude", "/svc.py", b"new")
    clear_path("u1", "c1", "qwen", canonical_path("/svc.py"))
    assert pending_block("u1", "c1", "qwen") == ""
    assert "/svc.py" in pending_block("u1", "c1", "gemini")


def test_clear_agent_then_clear_conversation():
    track_read("u1", "c1", "qwen", "/a.py", b"old")
    track_read("u1", "c1", "gemini", "/a.py", b"old")
    note_write("u1", "c1", "claude", "/a.py", b"new")

    clear_agent("u1", "c1", "qwen")
    assert pending_block("u1", "c1", "qwen") == ""
    assert stats()["agents_with_notices"] == 1

    clear_conversation("u1", "c1")
    assert stats()["agents_with_notices"] == 0


def test_edit_guard_clear_conversation_drops_notices():
    track_read("u1", "c1", "qwen", "/a.py", b"old")
    note_write("u1", "c1", "claude", "/a.py", b"new")
    _edit_guard.clear_conversation("u1", "c1")
    assert stats()["agents_with_notices"] == 0


def test_edit_guard_clear_agent_drops_notices():
    track_read("u1", "c1", "qwen", "/a.py", b"old")
    note_write("u1", "c1", "claude", "/a.py", b"new")
    _edit_guard.clear_agent("u1", "c1", "qwen")
    assert stats()["agents_with_notices"] == 0


# -- Scoping --

def test_other_conversation_is_not_notified():
    track_read("u1", "c2", "qwen", "/svc.py", b"old")
    assert note_write("u1", "c1", "claude", "/svc.py", b"new") == 0


def test_other_user_is_not_notified():
    track_read("u2", "c1", "qwen", "/svc.py", b"old")
    assert note_write("u1", "c1", "claude", "/svc.py", b"new") == 0


def test_different_paths_do_not_cross():
    track_read("u1", "c1", "qwen", "/a.py", b"old")
    assert note_write("u1", "c1", "claude", "/b.py", b"new") == 0


def test_relative_and_absolute_paths_are_the_same_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    track_read("u1", "c1", "qwen", str(tmp_path / "svc.py"), b"old")
    assert note_write("u1", "c1", "claude", "svc.py", b"new") == 1


# -- Aggregation and bounds --

def test_repeated_writes_aggregate_into_one_line():
    track_read("u1", "c1", "qwen", "/svc.py", b"v0")
    note_write("u1", "c1", "claude", "/svc.py", b"v1")
    note_write("u1", "c1", "gemini", "/svc.py", b"v2")

    block = pending_block("u1", "c1", "qwen")
    assert block.count("/svc.py") == 1
    assert "'claude'" in block and "'gemini'" in block
    assert "2 times" in block


def test_pending_paths_are_bounded():
    for i in range(MAX_PATHS + 5):
        track_read("u1", "c1", "qwen", f"/f{i}.py", b"old")
        note_write("u1", "c1", "claude", f"/f{i}.py", b"new")
    assert stats()["paths"] == MAX_PATHS
    block = pending_block("u1", "c1", "qwen")
    # Oldest evicted, newest kept.
    assert "/f0.py" not in block
    assert f"/f{MAX_PATHS + 4}.py" in block


def test_block_tells_the_agent_what_to_do():
    track_read("u1", "c1", "qwen", "/svc.py", b"old")
    note_write("u1", "c1", "claude", "/svc.py", b"new")
    assert "Re-read" in pending_block("u1", "c1", "qwen")
    assert BLOCK_TITLE


# -- readers_of, the accessor the notice store is built on --

def test_readers_of_excludes_writer_and_matching_content():
    track_read("u1", "c1", "qwen", "/svc.py", b"old")
    track_read("u1", "c1", "gemini", "/svc.py", b"new")
    track_read("u1", "c1", "claude", "/svc.py", b"old")

    stale = readers_of("u1", "c1", "/svc.py",
                       exclude_agent="claude", content=b"new")
    assert stale == ["qwen"]


def test_readers_of_without_content_returns_every_other_reader():
    track_read("u1", "c1", "qwen", "/svc.py", b"old")
    track_read("u1", "c1", "gemini", "/svc.py", b"new")
    stale = readers_of("u1", "c1", "/svc.py", exclude_agent="claude")
    assert sorted(stale) == ["gemini", "qwen"]


# -- Wiring: the notice must actually fire from the real handlers --

def _workdir_handler(cls, tmp_path, agent: str):
    handler = cls()
    handler.set_user_id("u1")
    handler.set_conversation_id("c1")
    handler.set_agent_name(agent)
    handler.set_workdir(str(tmp_path))
    handler.set_is_claude_code(True)
    return handler


def test_write_handler_notifies_a_stale_reader(tmp_path):
    from core.handlers.read import ReadHandler
    from core.handlers.write import WriteHandler

    (tmp_path / "svc.py").write_text("old")

    reader = _workdir_handler(ReadHandler, tmp_path, "qwen")
    reader.execute({"path": "svc.py"})

    writer = _workdir_handler(WriteHandler, tmp_path, "claude")
    writer.execute({"path": "svc.py", "content": "new"})

    block = pending_block("u1", "c1", "qwen")
    assert "svc.py" in block
    assert "'claude'" in block


def test_edit_handler_notifies_a_stale_reader(tmp_path):
    from core.handlers.edit_handler import EditHandler
    from core.handlers.read import ReadHandler

    (tmp_path / "svc.py").write_text("alpha\n")

    reader = _workdir_handler(ReadHandler, tmp_path, "qwen")
    reader.execute({"path": "svc.py"})

    editor = _workdir_handler(EditHandler, tmp_path, "claude")
    result = editor.execute({"path": "svc.py",
                             "old_string": "alpha", "new_string": "beta"})
    assert not result.startswith("Error")

    assert "svc.py" in pending_block("u1", "c1", "qwen")


def test_editing_agent_gets_no_notice_from_its_own_edit(tmp_path):
    from core.handlers.edit_handler import EditHandler
    from core.handlers.read import ReadHandler

    (tmp_path / "svc.py").write_text("alpha\n")

    agent = _workdir_handler(ReadHandler, tmp_path, "claude")
    agent.execute({"path": "svc.py"})

    editor = _workdir_handler(EditHandler, tmp_path, "claude")
    editor.execute({"path": "svc.py",
                    "old_string": "alpha", "new_string": "beta"})

    assert pending_block("u1", "c1", "claude") == ""


# -- Robustness --

def test_missing_identity_is_a_no_op():
    assert note_write("", "c1", "claude", "/svc.py", b"new") == 0
    assert note_write("u1", "", "claude", "/svc.py", b"new") == 0
    assert note_write("u1", "c1", "claude", "", b"new") == 0
    assert pending_block("", "", "") == ""
