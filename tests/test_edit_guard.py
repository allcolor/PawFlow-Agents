"""Tests for Read-before-Edit + duplicate-retry guardrails.

State lives only for the duration of one agent loop (cleared on `done`)
and is scoped to (user, conv, agent) — reads by another agent don't
count. These tests verify both invariants.
"""
import pytest

from core.handlers._edit_guard import (
    track_read, track_write,
    check_read_before_edit, check_duplicate_failure,
    record_edit_failure,
    clear_agent, clear_conversation,
    stats, reset_for_tests,
)


@pytest.fixture(autouse=True)
def _reset():
    reset_for_tests()
    yield
    reset_for_tests()


def test_no_read_blocks_edit():
    msg = check_read_before_edit("u1", "c1", "claude", "/foo.py")
    assert msg is not None
    assert "have not read" in msg


def test_read_then_edit_ok():
    track_read("u1", "c1", "claude", "/foo.py", b"content")
    assert check_read_before_edit("u1", "c1", "claude", "/foo.py") is None


def test_different_agent_does_not_count():
    # claude reads, qwen tries to edit — qwen must read first
    track_read("u1", "c1", "claude", "/foo.py", b"content")
    msg = check_read_before_edit("u1", "c1", "qwen", "/foo.py")
    assert msg is not None
    assert "have not read" in msg


def test_different_conversation_does_not_count():
    track_read("u1", "c1", "claude", "/foo.py", b"content")
    msg = check_read_before_edit("u1", "c2", "claude", "/foo.py")
    assert msg is not None


def test_duplicate_failure_refuses_same_old_string():
    track_read("u1", "c1", "claude", "/foo.py", b"content")
    # First failure recorded
    record_edit_failure("u1", "c1", "claude", "/foo.py", "SAME_INPUT")
    # Second attempt with same old_string: refused
    msg = check_duplicate_failure(
        "u1", "c1", "claude", "/foo.py", "SAME_INPUT")
    assert msg is not None
    assert "already failed" in msg


def test_different_old_string_not_refused():
    record_edit_failure("u1", "c1", "claude", "/foo.py", "WRONG_1")
    # Different input — allowed
    assert check_duplicate_failure(
        "u1", "c1", "claude", "/foo.py", "WRONG_2") is None


def test_re_read_clears_failure_streak():
    record_edit_failure("u1", "c1", "claude", "/foo.py", "BAD")
    # Before re-read: refused
    assert check_duplicate_failure(
        "u1", "c1", "claude", "/foo.py", "BAD") is not None
    # After re-read: streak cleared, retry allowed
    track_read("u1", "c1", "claude", "/foo.py", b"new content")
    assert check_duplicate_failure(
        "u1", "c1", "claude", "/foo.py", "BAD") is None


def test_track_write_allows_subsequent_edit():
    # Agent reads, edits, then edits again without re-reading their own output
    track_read("u1", "c1", "claude", "/foo.py", b"before")
    track_write("u1", "c1", "claude", "/foo.py", b"after")
    # Still can edit — track_write refreshed the hash
    assert check_read_before_edit(
        "u1", "c1", "claude", "/foo.py") is None


def test_clear_agent_drops_only_that_agent():
    track_read("u1", "c1", "claude", "/foo.py", b"x")
    track_read("u1", "c1", "qwen", "/foo.py", b"x")
    clear_agent("u1", "c1", "claude")
    assert check_read_before_edit("u1", "c1", "claude", "/foo.py") is not None
    assert check_read_before_edit("u1", "c1", "qwen", "/foo.py") is None


def test_clear_conversation_drops_all_agents_in_that_conv():
    track_read("u1", "c1", "claude", "/foo.py", b"x")
    track_read("u1", "c1", "qwen", "/foo.py", b"x")
    track_read("u1", "c2", "claude", "/other.py", b"x")
    clear_conversation("u1", "c1")
    assert check_read_before_edit("u1", "c1", "claude", "/foo.py") is not None
    assert check_read_before_edit("u1", "c1", "qwen", "/foo.py") is not None
    # Other conv untouched
    assert check_read_before_edit("u1", "c2", "claude", "/other.py") is None


def test_missing_identity_fails_open():
    # No user_id / conv_id / agent_name: guard disabled to avoid
    # breaking code paths that don't provide identity (legacy callers).
    assert check_read_before_edit("", "c1", "a", "/x") is None
    assert check_read_before_edit("u1", "", "a", "/x") is None
    assert check_read_before_edit("u1", "c1", "", "/x") is None
    assert check_read_before_edit("u1", "c1", "a", "") is None


def test_stats_reflects_state():
    assert stats()["read_hashes"] == 0
    track_read("u1", "c1", "claude", "/a.py", b"x")
    track_read("u1", "c1", "claude", "/b.py", b"y")
    assert stats()["read_hashes"] == 2
    clear_conversation("u1", "c1")
    assert stats()["read_hashes"] == 0
