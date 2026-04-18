"""Tests for the rich edit-mismatch diagnostic.

The agent was burning retries on silent `old_string not found` failures.
Each test case here reproduces a real-world failure mode and asserts
the diagnostic surfaces a hint that points at the actual cause, so the
agent can fix its input on the next attempt instead of guessing.
"""
import pytest

from tools.fs_actions import _diagnose_edit_mismatch


def test_hallucinated_second_line_reports_divergence():
    # Exact scenario the agent hit: sent a 2-line old_string where the 2nd
    # line doesn't exist in the file (hallucinated anchor).
    text = (
        "  </div><div id=\"res-section-${rtype}\" style=\"display:${collapsed ? 'none' };max-height:260px;overflow-y:auto;\">`;\n"
        "}\n"
    )
    old = (
        "  </div><div id=\"res-section-${rtype}\" style=\"display:${collapsed ? 'none' };max-height:260px;overflow-y:auto;\">`;\n"
        "  </div><div id=\"res-section-${rtype}\" style=\"display:${collapsed ? 'none' : 'block'};\">`;\n"
    )
    msg = _diagnose_edit_mismatch(old, text, "resources.js")
    assert "old_string not found in resources.js" in msg
    # The real line ends with `;\n}` not another <div>, so divergence reports
    # that we matched up to the first `">`;`" and then the file has something else
    assert "Partial match starts at file line 1" in msg
    # Must tell the agent to stop retrying
    assert "Do NOT retry with the same old_string" in msg


def test_crlf_vs_lf_hint():
    text = "hello\r\nworld\r\n"
    old = "hello\nworld"
    msg = _diagnose_edit_mismatch(old, text, "f.txt")
    assert "CRLF line endings" in msg
    assert "LF" in msg


def test_trailing_whitespace_hint():
    text = "line1   \nline2\n"  # note trailing spaces on line1
    old = "line1\nline2"        # no trailing spaces
    msg = _diagnose_edit_mismatch(old, text, "f.txt")
    assert "trailing whitespace" in msg.lower()


def test_tabs_vs_spaces_hint_file_has_tabs():
    text = "def foo():\n\treturn 1\n"
    old = "def foo():\n    return 1"
    msg = _diagnose_edit_mismatch(old, text, "f.py")
    assert "tabs" in msg.lower()


def test_tabs_vs_spaces_hint_file_has_spaces():
    text = "def foo():\n    return 1\n"
    old = "def foo():\n\treturn 1"
    msg = _diagnose_edit_mismatch(old, text, "f.py")
    assert "spaces" in msg.lower()


def test_no_similar_content_falls_back_to_generic():
    text = "completely different content here\n"
    old = "something else entirely"
    msg = _diagnose_edit_mismatch(old, text, "f.txt")
    assert "No similar content" in msg


def test_diagnostic_never_matches_when_input_matches():
    # Sanity: the diagnostic is only called on mismatch; if someone calls
    # it when old_string IS present, it still produces a coherent message
    # (no crash).
    text = "hello world\n"
    old = "hello"
    # This would not be called in practice, but test robustness
    msg = _diagnose_edit_mismatch(old, text, "f.txt")
    assert "old_string not found" in msg  # header stays
