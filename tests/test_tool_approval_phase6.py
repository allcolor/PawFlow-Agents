"""Phase 6 tests: read_only allowlist + fail-closed approval.

Locks down the contract that:
  - read_only is an allowlist, not a blocklist (any new tool is denied
    by default until explicitly classified safe);
  - approval defaults to fail-closed when the SSE dialog cannot be
    shown, opt-in fail-open via PAWFLOW_APPROVAL_FAIL_OPEN.
"""

import os
import unittest.mock as mock

from core.tool_approval import ToolApprovalGate


# ---------------------------------------------------------------------------
# read_only allowlist
# ---------------------------------------------------------------------------


def test_read_only_allows_listed_read_tools():
    for t in ("read", "list_dir", "stat", "exists", "glob", "grep",
              "search", "recall", "semantic_recall", "read_history",
              "pawflow_help", "get_tool_schema", "web_search",
              "notify_user", "ask_user"):
        assert ToolApprovalGate.is_read_only_allowed(t), (
            f"{t} should be allowed in read_only mode")


def test_read_only_denies_write_tools():
    for t in ("write", "edit", "batch_edit", "apply_patch", "find_replace",
              "delete", "mkdir", "bash", "notebook_edit",
              "execute_script", "remote_exec", "store_secret",
              "create_tool", "manage_resource"):
        assert not ToolApprovalGate.is_read_only_allowed(t), (
            f"{t} must NOT be allowed in read_only mode")


def test_read_only_denies_unknown_new_tool():
    """Allowlist contract: a brand-new tool no one classified is denied.
    This is the whole point of fail-closed."""
    assert not ToolApprovalGate.is_read_only_allowed(
        "some_brand_new_media_generation_tool")
    assert not ToolApprovalGate.is_read_only_allowed(
        "video_synthesize")
    assert not ToolApprovalGate.is_read_only_allowed(
        "browser_action")
    assert not ToolApprovalGate.is_read_only_allowed(
        "screen")


def test_read_only_denies_empty_tool_name():
    assert not ToolApprovalGate.is_read_only_allowed("")
    assert not ToolApprovalGate.is_read_only_allowed(None)


def test_advisor_read_only_is_silent_and_fail_closed():
    assert ToolApprovalGate.is_advisor_read_only_allowed("read")
    assert not ToolApprovalGate.is_advisor_read_only_allowed("notify_user")
    assert not ToolApprovalGate.is_advisor_read_only_allowed("ask_user")
    assert not ToolApprovalGate.is_advisor_read_only_allowed("new_tool")


def test_read_only_filesystem_dispatch():
    """filesystem.<action> must defer to _FS_EXEMPT, not the top-level
    allowlist (filesystem itself isn't in READ_ONLY_ALLOWED)."""
    for action in ("list_dir", "read_file", "stat", "exists", "search",
                   "grep", "git_status", "git_log", "git_diff"):
        assert ToolApprovalGate.is_read_only_allowed(
            "filesystem", {"action": action}), (
            f"filesystem.{action} should be allowed in read_only")
    for action in ("write_file", "edit", "delete_file", "exec",
                   "git_push", "git_checkout", "mkdir"):
        assert not ToolApprovalGate.is_read_only_allowed(
            "filesystem", {"action": action}), (
            f"filesystem.{action} must NOT be allowed in read_only")


def test_read_only_filesystem_without_args_denies():
    """No `arguments` dict → we can't tell which sub-action; deny."""
    assert not ToolApprovalGate.is_read_only_allowed("filesystem", None)
    assert not ToolApprovalGate.is_read_only_allowed("filesystem", {})


def test_read_only_see_screenshot_path_denied():
    """`see` is read for plain paths, screenshot for screen/screenshot."""
    assert ToolApprovalGate.is_read_only_allowed(
        "see", {"path": "/some/file.txt"})
    assert not ToolApprovalGate.is_read_only_allowed(
        "see", {"path": "screen"})
    assert not ToolApprovalGate.is_read_only_allowed(
        "see", {"path": "screenshot"})
    # case + whitespace insensitive (matches check() normalization)
    assert not ToolApprovalGate.is_read_only_allowed(
        "see", {"path": "  SCREEN  "})


# ---------------------------------------------------------------------------
# Approval fail-closed when dialog cannot be shown
# ---------------------------------------------------------------------------


def _force_publish_failure(monkeypatch):
    """Make ConversationEventBus.publish_event always raise so check()
    falls into the fail-open / fail-closed branch."""
    import core.conversation_event_bus as bus_mod

    class _BoomBus:
        @classmethod
        def instance(cls):
            return cls()

        def subscriber_count(self, *_a, **_kw):
            return 1

        def publish_event(self, *a, **kw):
            raise RuntimeError("no SSE subscriber")

    monkeypatch.setattr(
        bus_mod, "ConversationEventBus", _BoomBus, raising=True)


def test_approval_denies_immediately_without_live_subscriber(monkeypatch):
    import core.conversation_event_bus as bus_mod

    class _NoSubscriberBus:
        @classmethod
        def instance(cls):
            return cls()

        def subscriber_count(self, *_a, **_kw):
            return 0

        def publish_event(self, *a, **kw):
            raise AssertionError("publish_event must not run without subscribers")

    monkeypatch.setattr(
        bus_mod, "ConversationEventBus", _NoSubscriberBus, raising=True)
    result = ToolApprovalGate.check(
        "execute_script", "execute_script(1)",
        conversation_id="convNoSub", user_id="alice",
        arguments={"code": "1"})
    assert result == "denied"


def test_approval_fail_closed_by_default(monkeypatch):
    monkeypatch.delenv("PAWFLOW_APPROVAL_FAIL_OPEN", raising=False)
    _force_publish_failure(monkeypatch)
    # `edit` is NOT in ALWAYS_ASK — historically would have been auto
    # approved on dialog failure. With fail-closed default, denied.
    result = ToolApprovalGate.check(
        "edit", "edit(/x)",
        conversation_id="convA", user_id="alice",
        arguments={"path": "/x", "content": "y"})
    assert result == "denied"


def test_approval_fail_open_with_env(monkeypatch):
    monkeypatch.setenv("PAWFLOW_APPROVAL_FAIL_OPEN", "true")
    _force_publish_failure(monkeypatch)
    # With env opt-in, non-ALWAYS_ASK tools auto-approve as before.
    result = ToolApprovalGate.check(
        "edit", "edit(/x)",
        conversation_id="convA", user_id="alice",
        arguments={"path": "/x", "content": "y"})
    assert result == "approved"


def test_approval_always_ask_denied_even_with_fail_open(monkeypatch):
    monkeypatch.setenv("PAWFLOW_APPROVAL_FAIL_OPEN", "true")
    _force_publish_failure(monkeypatch)
    # bash is in ALWAYS_ASK — must be denied even when fail-open is on.
    result = ToolApprovalGate.check(
        "bash", "bash(ls)",
        conversation_id="convA", user_id="alice",
        arguments={"command": "ls"})
    assert result == "denied"


def test_approval_fail_open_env_values(monkeypatch):
    """Only specific opt-in values enable fail-open; anything else stays
    fail-closed."""
    _force_publish_failure(monkeypatch)
    for val in ("1", "true", "TRUE", "yes", "YES", "True"):
        monkeypatch.setenv("PAWFLOW_APPROVAL_FAIL_OPEN", val)
        assert ToolApprovalGate.check(
            "edit", "edit(/x)",
            conversation_id="convB", user_id="alice",
            arguments={"path": "/x", "content": "y"}) == "approved", (
            f"FAIL_OPEN={val!r} should enable approval")
    for val in ("", "0", "false", "no", "random", "FALSE"):
        monkeypatch.setenv("PAWFLOW_APPROVAL_FAIL_OPEN", val)
        assert ToolApprovalGate.check(
            "edit", "edit(/x)",
            conversation_id="convC", user_id="alice",
            arguments={"path": "/x", "content": "y"}) == "denied", (
            f"FAIL_OPEN={val!r} must keep fail-closed")
