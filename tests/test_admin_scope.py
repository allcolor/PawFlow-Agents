"""Tests for core.admin_scope -- admin view-all gate + owner override."""

from unittest.mock import patch

import pytest

from core import FlowFile
import core.admin_scope as admin_scope


def _ff(roles=""):
    ff = FlowFile(content=b"{}")
    if roles:
        ff.set_attribute("http.auth.roles", roles)
    return ff


# ── is_admin / wants_view_all ──────────────────────────────────────

def test_is_admin_true_false():
    assert admin_scope.is_admin(_ff("admin")) is True
    assert admin_scope.is_admin(_ff("user")) is False
    assert admin_scope.is_admin(_ff("")) is False


def test_wants_view_all_requires_admin_and_flag():
    assert admin_scope.wants_view_all({"view": "all"}, _ff("admin")) is True
    # non-admin asking for all -> silently downgraded
    assert admin_scope.wants_view_all({"view": "all"}, _ff("user")) is False
    # admin not asking -> self
    assert admin_scope.wants_view_all({"view": "self"}, _ff("admin")) is False
    assert admin_scope.wants_view_all({}, _ff("admin")) is False


# ── effective_owner ────────────────────────────────────────────────

def test_effective_owner_no_override_is_caller():
    assert admin_scope.effective_owner(
        {}, "alice", "", _ff("user"), "user") == ("alice", "")
    assert admin_scope.effective_owner(
        {}, "alice", "c1", _ff("user"), "conv") == ("alice", "c1")


def test_effective_owner_global_has_no_owner():
    assert admin_scope.effective_owner(
        {"target_user_id": "bob"}, "alice", "", _ff("admin"),
        "global") == ("", "")


def test_effective_owner_same_target_is_noop():
    # target equal to caller -> no admin needed, caller returned
    assert admin_scope.effective_owner(
        {"target_user_id": "alice"}, "alice", "", _ff("user"),
        "user") == ("alice", "")


def test_effective_owner_nonadmin_override_denied():
    with pytest.raises(PermissionError):
        admin_scope.effective_owner(
            {"target_user_id": "bob"}, "alice", "", _ff("user"), "user")


def test_effective_owner_user_scope_admin_override():
    with patch.object(admin_scope, "_user_exists", return_value=True):
        assert admin_scope.effective_owner(
            {"target_user_id": "bob"}, "alice", "", _ff("admin"),
            "user") == ("bob", "")


def test_effective_owner_unknown_user():
    with patch.object(admin_scope, "_user_exists", return_value=False):
        with pytest.raises(ValueError):
            admin_scope.effective_owner(
                {"target_user_id": "ghost"}, "alice", "", _ff("admin"),
                "user")


def test_effective_owner_conv_scope_admin_override():
    with patch.object(admin_scope, "_user_exists", return_value=True), \
            patch.object(admin_scope, "_conv_owner", return_value="bob"):
        assert admin_scope.effective_owner(
            {"target_user_id": "bob", "target_conversation_id": "c9"},
            "alice", "", _ff("admin"), "conv") == ("bob", "c9")


def test_effective_owner_conv_owner_mismatch():
    with patch.object(admin_scope, "_user_exists", return_value=True), \
            patch.object(admin_scope, "_conv_owner", return_value="carol"):
        with pytest.raises(ValueError):
            admin_scope.effective_owner(
                {"target_user_id": "bob", "target_conversation_id": "c9"},
                "alice", "", _ff("admin"), "conv")


def test_effective_owner_conv_scope_requires_conv():
    with patch.object(admin_scope, "_user_exists", return_value=True):
        with pytest.raises(ValueError):
            admin_scope.effective_owner(
                {"target_user_id": "bob"}, "alice", "", _ff("admin"), "conv")
